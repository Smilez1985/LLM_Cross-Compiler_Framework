#!/usr/bin/env python3
"""
LLMClient: picks provider and model per task from the settings, builds the adapter, keeps personal data away
from cloud providers, retries what is worth retrying and falls back along ai_fallback_chain.

Settings (all optional except ai_provider):
  ai_provider            preset id from configs/llm_providers.yml (old values such as "OpenAI" still work)
  ai_model               model for every task; ai_model_<task> (chat, tools, json, healing) wins over it
  ai_base_url            endpoint for presets that allow one (Ollama, custom); ai_base_url_<provider> per provider
  ai_auth / ai_auth_<provider>   a sign-in method id of the preset (api_key, ant_profile, none, ...; default:
                         the first usable method in the catalog's order; opt-in methods such as ant_profile only
                         through ai_auth_<provider>). Keys come from the vault, else from the environment variables
                         the catalog lists for the method (shown as the source); see orchestrator/llm/auth/.
  ai_oauth_profile       `ant auth login` profile for Claude when ai_auth_anthropic is ant_profile (default: the
                         CLI's active profile)
  ai_anthropic_workspace_id   wrkspc_... for Claude API keys that are valid in every workspace of the organization
                         (sent with the API key to the Claude API only; an `ant` profile keeps its own workspace).
                         While set it wins over the workspace ID stored with the key.
  ai_effort / ai_effort_<task>   low | medium | high | xhigh | max
  ai_fallback_chain      e.g. ["local"] or ["ollama:qwen3.5:4b"]; tried on refusals and outages
  ai_security_level      STRICT | BALANCED | OFF for cloud providers (local ones are never filtered)
  ai_timeout             seconds per request (default 600)
  ai_max_retry_wait      longest Retry-After the client waits for (default 30 s); longer waits are raised
  offline_mode           only local providers are used

The "local" provider talks to the managed llama-server (orchestrator.runtime): base_url, the per-start API key
and the loaded model are read from the running server's state for every call, so a server started or
restarted by the CLI or the GUI is picked up without configuration. set_runtime_endpoint() overrides that.
"""

import ipaddress
import os
import random
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from orchestrator.llm.auth.resolver import CredentialResolver, ResolvedCredential
from orchestrator.llm.auth.store import CredentialStore
from orchestrator.llm.catalog import Catalog, ProviderPreset, load_catalog
from orchestrator.llm.errors import LLMError, NotConfigured, ProviderUnavailable, RateLimited, Refused
from orchestrator.llm.privacy import PrivacyFilter, normalize_level, restore, restore_obj
from orchestrator.llm.providers import adapter_class
from orchestrator.llm.types import EFFORTS, TASKS, ChatRequest, ChatResult, Message, ModelInfo
from orchestrator.security.redaction import SecretStr, reveal
from orchestrator.utils.locality import offline_mode
from orchestrator.utils.logging import get_logger

MAX_RETRIES = 2  # the SDKs' own retries are switched off (max_retries=0), so attempts never multiply
MANAGED_RUNTIME_PROVIDER = "local"  # preset served by the managed llama-server

# (base_url, api_key, model) of a running local server
RuntimeEndpoint = Tuple[str, Union[str, SecretStr, None], Optional[str]]


def is_loopback_url(url: Optional[str]) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def offline_http_client(config: Any, *, offline: bool = True):
    """The pinned SDK client (openai_compat servers: Ollama, the managed local model, a custom server): no proxy from
    the environment, and every connection goes only to the local addresses the network-locality rule checked for its
    host (utils/locality.connect_addresses, from the cache, so a DNS answer that changes after the check cannot reach a
    public host, also not through a redirect; a name with local and public addresses only to its local ones, round 26
    Q4), tried in turn as the system's own connection would (a name with an IPv6 and an IPv4 address in the own
    network, a server that listens on one of them only); when every one failed, the lookup is dropped
    (locality.forget), so the next try asks the resolver again. A localhost name goes to the loopback addresses: a
    system that asks a DNS server for *.localhost never decides where it goes. A host that is not local is refused
    when the connection is opened (LocalityRefused, which map_openai_error turns into the plain refusal).

    In offline mode (`offline` True) `config` holds the person's opt-outs (offline mode's reach); online, a server
    labelled local by its address (ProviderPreset.located_by_address) gets this client with `config` None and
    `offline` False: the label without the opt-outs (check_location) then holds for the connection too, so prompts
    sent without the privacy filter never reach a public address."""
    import httpcore2
    import httpx2
    import openai
    from orchestrator.utils.locality import connect_addresses, forget

    class PinnedBackend(httpcore2.NetworkBackend):
        def __init__(self, inner):
            self._inner = inner

        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            failed = None
            for address in connect_addresses(host, config, "the AI provider's server", offline=offline):
                try:
                    return self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                                   socket_options=socket_options)
                except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as e:   # the next checked address
                    failed = e
            forget(host)   # every checked address failed: the next connection asks the resolver again
            raise failed

        def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover - never used
            raise PermissionError("offline mode connects to checked network addresses only")

        def sleep(self, seconds):
            self._inner.sleep(seconds)

    transport = httpx2.HTTPTransport(trust_env=False)
    transport._pool._network_backend = PinnedBackend(transport._pool._network_backend)
    return openai.DefaultHttpxClient(trust_env=False, transport=transport)


@dataclass(frozen=True)
class Target:
    """A resolved provider + model + endpoint for one call (never contains the key)."""
    preset: ProviderPreset
    model: str
    base_url: Optional[str]
    auth: str
    local: bool

    @property
    def provider(self) -> str:
        return self.preset.id


class LLMClient:
    def __init__(self, config_manager: Any, secrets_manager: Any = None, *, catalog: Optional[Catalog] = None,
                 state_dir: Optional[Path] = None, http_client: Any = None,
                 sleep: Callable[[float], None] = time.sleep, adapter_options: Optional[Dict[str, Dict]] = None,
                 runtime_resolver: Optional[Callable[[str], Optional[RuntimeEndpoint]]] = None):
        self.logger = get_logger("LLMClient")
        self.config = config_manager
        self.secrets = secrets_manager
        self.catalog = catalog or load_catalog(user_dir=getattr(config_manager, "config_dir", None))
        self._state_dir = Path(state_dir) if state_dir else None
        self.http_client = http_client          # tests and proxies: an httpx2.Client for both SDKs
        self.sleep = sleep
        self.adapter_options = adapter_options or {}  # extra constructor arguments per adapter family
        self._runtime: Dict[str, RuntimeEndpoint] = {}
        # Looks up the endpoint of a running local server per call; default: the managed llama-server.
        self.runtime_resolver = runtime_resolver or self._managed_runtime_endpoint
        self._loopback_http = None
        self._offline_http = None
        self._located_http = None   # online: the pinned client of a server labelled local by its address
        self._adapters: Dict[tuple, Any] = {}
        self._state_cache: Dict[str, SecretStr] = {}
        self._lock = threading.Lock()
        # Credentials: vault records and their metadata (next to the vault), resolved per call.
        self.credentials = CredentialStore(secrets_manager, state_dir=lambda: self._state_path(""))
        self.resolver = CredentialResolver(self.credentials, setting=self._setting, runtime_key=self._runtime_key)

    # --- settings ---------------------------------------------------------------------------------------------
    def _setting(self, key: str, default: Any = None) -> Any:
        """A value the user set (file, environment, GUI). Schema defaults do not count: the default ai_model
        belongs to the default provider and would be wrong for any other."""
        values = getattr(self.config, "config_values", None)
        if isinstance(values, dict):
            entry = values.get(key)
            if entry is None or getattr(getattr(entry, "source", None), "source_type", "") == "default":
                return default
            value = entry.value
        else:
            try:
                value = self.config.get(key, None)
            except Exception:
                value = None
        return default if value in (None, "", []) else value

    def _flag(self, key: str) -> bool:
        try:
            return bool(self.config.get(key, False))
        except Exception:
            return False

    def primary_provider(self) -> ProviderPreset:
        name = self._setting("ai_provider")
        if name is None:
            try:
                name = self.config.get("ai_provider", None)
            except Exception:
                name = None
        preset = self.catalog.get(name)
        if preset is None:
            raise self._unknown_provider(name)
        return preset

    def is_primary(self, preset: ProviderPreset) -> bool:
        """Whether `preset` is the provider in ai_provider (False while that setting names no provider)."""
        try:
            return self.primary_provider().id == preset.id
        except LLMError:
            return False

    def _unknown_provider(self, name: Any) -> NotConfigured:
        removed = self.catalog.removed_message(name)
        if removed:
            return NotConfigured(removed, code="provider.removed")
        return NotConfigured(f"Unknown AI provider '{name}'. Choose one of: {', '.join(self.catalog.ids())}.")

    def set_runtime_endpoint(self, provider: str, base_url: Optional[str], api_key: Optional[str] = None,
                             model: Optional[str] = None) -> None:
        """Endpoint (and loaded model) of a running local server, e.g. the managed llama-server; base_url None
        removes it. Kept in memory only, the key as a SecretStr."""
        pid = self.catalog.resolve_id(provider) or provider
        with self._lock:
            if base_url:
                self._runtime[pid] = (base_url, SecretStr.wrap(api_key), model)
            else:
                self._runtime.pop(pid, None)

    def _managed_runtime_endpoint(self, provider: str) -> Optional[RuntimeEndpoint]:
        """The managed llama-server of this user (started by `llm-cli ditto runtime start` or the GUI)."""
        if provider != MANAGED_RUNTIME_PROVIDER:
            return None
        from orchestrator.runtime.llama_server import managed_endpoint
        endpoint = managed_endpoint(self._state_path(""))
        return (endpoint.base_url, endpoint.api_key, endpoint.model) if endpoint else None

    def _runtime_endpoint(self, preset: ProviderPreset) -> Optional[RuntimeEndpoint]:
        with self._lock:
            endpoint = self._runtime.get(preset.id)
        if endpoint:
            return endpoint
        try:
            return self.runtime_resolver(preset.id)
        except Exception as e:  # a broken state file must not break the other providers
            self.logger.warning(f"Could not read the local server state for {preset.id}: {e.__class__.__name__}")
            return None

    def _base_url(self, preset: ProviderPreset, primary: bool) -> Optional[str]:
        if preset.runtime_endpoint:
            endpoint = self._runtime_endpoint(preset)
            if not endpoint:
                hint = (" Start it in the GUI with Ditto > Local Runtime... > Start, or with: llm-cli ditto runtime "
                        "start" if preset.id == MANAGED_RUNTIME_PROVIDER else "")
                raise NotConfigured(f"{preset.label}: the local model server is not running.{hint}",
                                    provider=preset.id)
            return endpoint[0]
        if preset.base_url_configurable:
            return (self._setting(f"ai_base_url_{preset.id}") or (self._setting("ai_base_url") if primary else None)
                    or preset.base_url)
        return preset.base_url  # cloud endpoints are fixed: a leftover custom URL must never receive their key

    def endpoint(self, preset: ProviderPreset) -> Optional[str]:
        """The base URL a call to `preset` uses; raises NotConfigured for a local server that is not running."""
        return self._base_url(preset, self.is_primary(preset))

    def _runtime_key(self, preset: ProviderPreset) -> Optional[str]:
        """The per-start key of the running local server of a runtime_endpoint preset."""
        if not preset.runtime_endpoint:
            return None
        return reveal((self._runtime_endpoint(preset) or (None, None, None))[1])

    def chosen_method(self, preset: ProviderPreset, primary: Optional[bool] = None) -> Tuple[Optional[str], bool]:
        """The sign-in method the user chose (ai_auth_<provider>, or ai_auth for the primary provider) and whether
        it names this provider (only then may it be an opt-in method such as an `ant` profile)."""
        own = self._setting(f"ai_auth_{preset.id}")
        if own:
            return str(own).strip(), True
        if primary is None:
            primary = self.is_primary(preset)
        shared = self._setting("ai_auth") if primary else None
        return (str(shared).strip() if shared else None), False

    def _auth(self, preset: ProviderPreset, primary: bool, base_url: Optional[str]) -> str:
        """The sign-in method: the one the user chose, else the first usable one in the catalog's order. Opt-in
        methods (an `ant` profile) are never picked automatically, so a profile on disk cannot switch the billed
        account."""
        chosen, explicit = self.chosen_method(preset, primary)
        return self.resolver.select(preset, chosen=chosen, explicit=explicit, base_url=base_url).id

    def _model(self, preset: ProviderPreset, task: str, primary: bool, explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        if preset.runtime_endpoint:
            loaded = (self._runtime_endpoint(preset) or (None, None, None))[2]
            if loaded:
                return loaded  # the model the local server has loaded is the only one it serves
        if primary:
            for key in (f"ai_model_{task}", "ai_model"):
                value = self._setting(key)
                if value and not self._foreign_model(preset, str(value)):
                    return str(value)
        model = preset.model_for(task)
        if not model:
            raise NotConfigured(f"{preset.label}: choose a model in the AI settings.", provider=preset.id)
        return model

    def _foreign_model(self, preset: ProviderPreset, model: str) -> bool:
        """A model id that is a known default of another fixed-catalog provider (left over after switching)."""
        if preset.adapter == "openai_compat" and preset.id not in ("google",):
            return False  # routers and local servers take any id
        if model in preset.suggested_models or model in preset.models.values():
            return False
        for other in self.catalog.providers.values():
            if other.id != preset.id and (model in other.suggested_models or model in other.models.values()):
                self.logger.warning(f"Model '{model}' belongs to {other.label}; using the {preset.label} default.")
                return True
        return False

    def resolve(self, task: str = "chat", provider: Optional[str] = None, model: Optional[str] = None) -> Target:
        """Provider, model, endpoint and sign-in method for a task (or for an explicit provider[:model])."""
        primary_preset = self.primary_provider()
        preset = self.catalog.get(provider) if provider else primary_preset
        if preset is None:
            raise self._unknown_provider(provider)
        primary = preset.id == primary_preset.id
        base_url = self._base_url(preset, primary)
        local = preset.is_local(base_url)   # where the server stands: the privacy filter, never the opt-outs (Q3)
        if offline_mode(self.config):
            # the one network-locality rule (utils/locality.py): a cloud provider never, Ollama and a custom server
            # only at an address on this computer or in the person's own network (or one the person counts as local)
            refusal = preset.offline_refusal(base_url, self.config)
            if refusal is not None:
                raise NotConfigured(refusal.text, provider=preset.id).with_steps(*refusal.steps)
        return Target(preset=preset, model=self._model(preset, task, primary, model), base_url=base_url,
                      auth=self._auth(preset, primary, base_url), local=local)

    def fallback_targets(self, task: str) -> List[Tuple[str, Optional[str]]]:
        chain = self._setting("ai_fallback_chain") or []
        if isinstance(chain, str):
            chain = [c.strip() for c in chain.split(",") if c.strip()]
        out = []
        for entry in chain:
            provider, _, model = str(entry).partition(":")
            out.append((provider, model or None))
        return out

    # --- adapters ---------------------------------------------------------------------------------------------
    def credential(self, target: Target) -> ResolvedCredential:
        """The credential for the target's sign-in method (key, profile, token provider); repr shows no value."""
        return self.resolver.resolve(target.preset, target.auth, target.base_url)

    def adapter(self, target: Target):
        """The adapter for a resolved target. For a running local server (runtime_endpoint presets) URL and key are
        read again, together: a server restarted since resolve() has a new port and a new key, and the new key must
        never go to the old port (another program may hold it now), so a changed URL is refused (ProviderUnavailable,
        which chat() treats like an outage)."""
        if target.preset.runtime_endpoint:
            endpoint = self._runtime_endpoint(target.preset)
            if not endpoint or endpoint[0] != target.base_url:
                raise ProviderUnavailable(f"{target.preset.label}: the local model server was stopped or restarted; "
                                          "try again.", provider=target.provider)
            credential = self.resolver.resolve(target.preset, target.auth, target.base_url, runtime_key=endpoint[1])
        else:
            credential = self.credential(target)  # raises before any request: no key, admin key, bad workspace ID
        return self.adapter_for(target.preset, target.base_url, credential)

    def adapter_for(self, preset: ProviderPreset, base_url: Optional[str], credential: ResolvedCredential):
        """The adapter for an explicit credential (also one that is not stored yet: the sign-in check)."""
        options = dict(self.adapter_options.get(preset.adapter, {}))
        if preset.adapter == "anthropic":
            options.update(auth="ant_profile" if credential.kind == "anthropic_profile" else "api_key",
                           oauth_profile=credential.profile, workspace_id=credential.workspace_id)
        elif preset.adapter == "openai_responses":
            options.update(prompt_cache_key=self.install_id())
        offline = offline_mode(self.config)   # an adapter made online is never reused offline (its client differs)
        target = Target(preset=preset, model="", base_url=base_url, auth=credential.method,
                        local=preset.is_local(base_url))
        # nor one made while the address counted as local, once it does not (its client differs too)
        key = (preset.id, base_url, credential.method, credential.fingerprint, repr(sorted(options.items(), key=str)),
               offline, target.local)
        with self._lock:
            adapter = self._adapters.get(key)
            if adapter is None:
                cls = adapter_class(preset.adapter)
                adapter = cls(preset, base_url=base_url, api_key=credential.api_key, credential=credential,
                              http_client=self._http_client_for(target),
                              timeout=float(self._setting("ai_timeout", 600)), **options)
                self._adapters[key] = adapter
        return adapter

    def _http_client_for(self, target: Target):
        """Loopback servers are reached directly: an HTTP(S)_PROXY from the environment would otherwise receive
        the prompts and the local server's key (the SDKs' default clients honour those variables). In offline mode
        every server is reached directly and only at the addresses the locality rule checked, with the person's
        opt-outs (offline_http_client: a DNS answer that changes between the check and the connection cannot reach a
        public host). Online, a server labelled local by its address (a custom server; its prompts go without the
        privacy filter) is reached the same way without the opt-outs, so the label holds for the connection: a name
        with local and public addresses only at its local ones (round 26 Q3, Q4)."""
        if self.http_client is not None or target.preset.adapter != "openai_compat":
            return self.http_client
        if offline_mode(self.config) and target.base_url:
            if self._offline_http is None:
                self._offline_http = offline_http_client(self.config)
            return self._offline_http
        if target.local and target.base_url and not is_loopback_url(target.base_url) \
                and target.preset.located_by_address(target.base_url):
            if self._located_http is None:
                self._located_http = offline_http_client(None, offline=False)
            return self._located_http
        if not is_loopback_url(target.base_url):
            return self.http_client
        if self._loopback_http is None:
            import openai
            self._loopback_http = openai.DefaultHttpxClient(trust_env=False)
        return self._loopback_http

    def list_models(self, provider: Optional[str] = None) -> List[ModelInfo]:
        """Live model list of a provider (the preset's suggestions where there is no list endpoint)."""
        return self.adapter(self.resolve("chat", provider=provider)).list_models()

    # --- per-install state ------------------------------------------------------------------------------------
    def _state_path(self, name: str) -> Path:
        base = self._state_dir or getattr(self.config, "user_state_dir", None)
        if base is None:
            base = os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework"
        return Path(base) / name

    def _state_secret(self, name: str) -> str:
        """A random value created on first use and kept in the per-user state directory (mode 0600)."""
        cached = self._state_cache.get(name)
        if cached:
            return cached.get_secret_value()
        path = self._state_path(name)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if len(value) < 64:
            value = secrets.token_hex(32)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{name}.")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
            os.replace(tmp, path)
        self._state_cache[name] = SecretStr(value)  # the privacy key is an HMAC key
        return value

    def install_id(self) -> str:
        """Random per-install id for prompt_cache_key; says nothing about the user."""
        return self._state_secret("llm_install_id")[:32]

    def _privacy_key(self) -> bytes:
        return bytes.fromhex(self._state_secret("llm_privacy_key"))

    # --- privacy ----------------------------------------------------------------------------------------------
    def privacy_level(self) -> str:
        try:
            return normalize_level(self.config.get("ai_security_level", "STRICT"))
        except Exception:
            return "STRICT"

    def _sanitize(self, request: ChatRequest) -> Tuple[ChatRequest, Dict[str, str]]:
        privacy = PrivacyFilter(self.privacy_level(), self._privacy_key())
        mapping: Dict[str, str] = {}

        def clean(text: str) -> str:
            out, found = privacy.sanitize(text)
            mapping.update(found)
            return out

        def clean_obj(value):
            if isinstance(value, str):
                return clean(value)
            if isinstance(value, list):
                return [clean_obj(v) for v in value]
            if isinstance(value, dict):
                return {k: clean_obj(v) for k, v in value.items()}
            return value

        messages = []
        for msg in request.messages:
            update: Dict[str, Any] = {}
            if isinstance(msg.content, str):
                update["content"] = clean(msg.content)
            else:
                update["content"] = [{**b, "text": clean(b.get("text", ""))} if b["type"] == "text" else b
                                     for b in msg.content]
            if msg.tool_calls:
                update["tool_calls"] = [c.model_copy(update={"arguments": clean_obj(c.arguments)})
                                        for c in msg.tool_calls]
            messages.append(msg.model_copy(update=update))  # `native` stays untouched
        return request.model_copy(update={"system": clean(request.system), "messages": messages}), mapping

    @staticmethod
    def _restore(result: ChatResult, mapping: Dict[str, str]) -> ChatResult:
        if not mapping:
            return result

        def fix(msg: Message) -> Message:
            if msg.role != "assistant":
                return msg
            content = restore(msg.content, mapping) if isinstance(msg.content, str) else msg.content
            calls = [c.model_copy(update={"arguments": restore_obj(c.arguments, mapping)}) for c in msg.tool_calls]
            return msg.model_copy(update={"content": content, "tool_calls": calls})

        new_messages = [fix(m) for m in result.new_messages]
        parsed = result.parsed
        if parsed is not None:
            parsed = type(parsed).model_validate(restore_obj(parsed.model_dump(mode="json"), mapping))
        return result.model_copy(update={
            "text": restore(result.text, mapping),
            "tool_calls": [c.model_copy(update={"arguments": restore_obj(c.arguments, mapping)})
                           for c in result.tool_calls],
            "parsed": parsed, "new_messages": new_messages,
            "message": new_messages[-1] if new_messages else result.message,
        })

    # --- calls ------------------------------------------------------------------------------------------------
    def _effort(self, request: ChatRequest) -> Optional[str]:
        if request.effort:
            return request.effort
        for key in (f"ai_effort_{request.task}", "ai_effort"):
            value = self._setting(key)
            if value in EFFORTS:
                return value
            if value:
                self.logger.warning(f"Ignoring {key}={value!r}; expected one of {', '.join(EFFORTS)}.")
        return None

    def _backoff(self, attempt: int) -> float:
        return min(0.5 * (2 ** attempt), 8.0) * (1 - 0.25 * random.random())  # noqa: S311 - jitter only

    def _call(self, target: Target, request: ChatRequest) -> ChatResult:
        adapter = self.adapter(target)
        mapping: Dict[str, str] = {}
        if not target.local:
            request, mapping = self._sanitize(request)
        max_wait = float(self._setting("ai_max_retry_wait", 30))
        attempt = 0
        while True:
            try:
                result = adapter.chat(request, target.model)
                return self._restore(result, mapping)
            except Refused as e:
                if e.result is not None:
                    e.result = self._restore(e.result, mapping)
                raise
            except (RateLimited, ProviderUnavailable) as e:
                wait = e.retry_after if e.retry_after is not None else self._backoff(attempt)
                if attempt >= MAX_RETRIES or wait > max_wait:
                    raise
                attempt += 1
                self.logger.info(f"{target.provider}: {type(e).__name__}, retry {attempt}/{MAX_RETRIES} in {wait:.1f}s")
                self.sleep(wait)

    def chat(self, request: ChatRequest) -> ChatResult:
        """One model turn. Refusals and outages move on along ai_fallback_chain; other errors are raised."""
        if request.task not in TASKS:
            raise NotConfigured(f"Unknown task profile '{request.task}'.")
        effort = self._effort(request)
        if effort and effort != request.effort:
            request = request.model_copy(update={"effort": effort})
        primary = self.resolve(request.task, model=request.model)
        targets: List[Any] = [primary, *self.fallback_targets(request.task)]
        last_error: Optional[LLMError] = None
        for index, target in enumerate(targets):
            if not isinstance(target, Target):
                provider, model = target
                try:
                    target = self.resolve(request.task, provider=provider, model=model)
                except NotConfigured as e:
                    self.logger.info(f"Fallback {provider} skipped: {e}")
                    continue
            if index > 0:
                self.logger.warning(f"Falling back to {target.provider} ({target.model}) after "
                                    f"{type(last_error).__name__}.")
            try:
                return self._call(target, request)
            except (Refused, ProviderUnavailable) as e:
                last_error = e
        assert last_error is not None
        raise last_error

    def describe(self, task: str = "chat") -> Dict[str, Any]:
        """What a call for `task` would use, for the settings dialog and Ditto's header (no secrets). `source`
        says where the credential comes from: vault, env:<VARIABLE>, runtime, profile:<name> or none."""
        target = self.resolve(task)
        spec = target.preset.method(target.auth)
        try:
            credential = self.credential(target)
        except LLMError:
            credential = None
        return {"provider": target.provider, "label": target.preset.label, "model": target.model,
                "base_url": target.base_url, "auth": target.auth, "auth_label": spec.label if spec else target.auth,
                "auth_type": spec.type if spec else None, "auth_source": credential.source if credential else "none",
                "workspace_id": credential.workspace_id if credential else None,
                "profile": credential.profile if credential else None, "local": target.local,
                "privacy": "off (local)" if target.local else self.privacy_level()}
