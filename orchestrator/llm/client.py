#!/usr/bin/env python3
"""
LLMClient: picks provider and model per task from the settings, builds the adapter, keeps personal data away
from cloud providers, retries what is worth retrying and falls back along ai_fallback_chain.

Settings (all optional except ai_provider):
  ai_provider            preset id from configs/llm_providers.yml (old values such as "OpenAI" still work)
  ai_model               model for every task; ai_model_<task> (chat, tools, json, healing) wins over it
  ai_base_url            endpoint for presets that allow one (Ollama, custom); ai_base_url_<provider> per provider
  ai_auth / ai_auth_<provider>   api_key | oauth | none (default: the first usable method of the preset)
  ai_oauth_profile       `ant auth login` profile for Claude (default: the active profile)
  ai_effort / ai_effort_<task>   low | medium | high | xhigh | max
  ai_fallback_chain      e.g. ["local"] or ["ollama:qwen3.5:4b"]; tried on refusals and outages
  ai_security_level      STRICT | BALANCED | OFF for cloud providers (local ones are never filtered)
  ai_timeout             seconds per request (default 600)
  ai_max_retry_wait      longest Retry-After the client waits for (default 30 s); longer waits are raised
  ai_claude_code_path    path of the `claude` binary (default: from PATH)
  offline_mode           only local providers are used
"""

import hashlib
import os
import random
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.llm.catalog import Catalog, ProviderPreset, load_catalog
from orchestrator.llm.errors import (LLMError, NotConfigured, ProviderUnavailable, RateLimited, Refused)
from orchestrator.llm.privacy import PrivacyFilter, normalize_level, restore, restore_obj
from orchestrator.llm.providers import adapter_class
from orchestrator.llm.types import EFFORTS, TASKS, ChatRequest, ChatResult, Message, ModelInfo
from orchestrator.utils.logging import get_logger

MAX_RETRIES = 2  # the SDKs' own retries are switched off (max_retries=0), so attempts never multiply


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
                 sleep: Callable[[float], None] = time.sleep, adapter_options: Optional[Dict[str, Dict]] = None):
        self.logger = get_logger("LLMClient")
        self.config = config_manager
        self.secrets = secrets_manager
        self.catalog = catalog or load_catalog(user_dir=getattr(config_manager, "config_dir", None))
        self._state_dir = Path(state_dir) if state_dir else None
        self.http_client = http_client          # tests and proxies: an httpx2.Client for both SDKs
        self.sleep = sleep
        self.adapter_options = adapter_options or {}  # extra constructor arguments per adapter family
        self._runtime: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
        self._adapters: Dict[tuple, Any] = {}
        self._state_cache: Dict[str, str] = {}
        self._lock = threading.Lock()

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
            raise NotConfigured(f"Unknown AI provider '{name}'. Choose one of: {', '.join(self.catalog.ids())}.")
        return preset

    def set_runtime_endpoint(self, provider: str, base_url: Optional[str], api_key: Optional[str] = None,
                             model: Optional[str] = None) -> None:
        """Endpoint (and loaded model) of a running local server, e.g. the managed llama-server; base_url None
        removes it. Kept in memory only."""
        pid = self.catalog.resolve_id(provider) or provider
        with self._lock:
            if base_url:
                self._runtime[pid] = (base_url, api_key, model)
            else:
                self._runtime.pop(pid, None)

    def _base_url(self, preset: ProviderPreset, primary: bool) -> Optional[str]:
        if preset.runtime_endpoint:
            endpoint = self._runtime.get(preset.id)
            if not endpoint:
                raise NotConfigured(f"{preset.label}: the local model server is not running.", provider=preset.id)
            return endpoint[0]
        if preset.base_url_configurable:
            return (self._setting(f"ai_base_url_{preset.id}") or (self._setting("ai_base_url") if primary else None)
                    or preset.base_url)
        return preset.base_url  # cloud endpoints are fixed: a leftover custom URL must never receive their key

    def _api_key(self, preset: ProviderPreset) -> Optional[str]:
        if preset.runtime_endpoint:
            return self._runtime.get(preset.id, (None, None, None))[1]
        if not preset.secret or self.secrets is None:
            return None
        try:
            return self.secrets.get_secret(preset.secret) or None
        except Exception as e:
            self.logger.warning(f"Could not read the stored key for {preset.id}: {e.__class__.__name__}")
            return None

    def _oauth_available(self, preset: ProviderPreset) -> bool:
        if preset.adapter == "claude_code":
            return True  # Claude Code handles its own sign-in; a missing login is reported when it runs
        if preset.adapter == "anthropic":
            try:
                from anthropic.lib.credentials import CredentialsFile
                return CredentialsFile(self._setting("ai_oauth_profile")).config_path.is_file()
            except Exception:
                return False
        return False

    def _auth(self, preset: ProviderPreset, primary: bool) -> str:
        chosen = self._setting(f"ai_auth_{preset.id}") or (self._setting("ai_auth") if primary else None)
        if chosen:
            if chosen not in preset.auth:
                raise NotConfigured(f"{preset.label} does not support sign-in method '{chosen}'.", provider=preset.id)
            return chosen
        for method in preset.auth:
            if method == "none" or (method == "oauth" and self._oauth_available(preset)) or \
                    (method == "api_key" and self._api_key(preset)):
                return method
        if preset.adapter == "anthropic":
            raise NotConfigured("Claude API: no API key stored and no `ant auth login` profile found. Add an API key "
                                "in the AI settings (Max/Team plans include monthly API credits for a linked Console "
                                "organization), or choose the Claude Code provider to use your subscription.",
                                provider=preset.id)
        raise NotConfigured(f"{preset.label}: no API key stored. Add one in the AI settings.", provider=preset.id)

    def _model(self, preset: ProviderPreset, task: str, primary: bool, explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        if preset.runtime_endpoint:
            loaded = self._runtime.get(preset.id, (None, None, None))[2]
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
            raise NotConfigured(f"Unknown AI provider '{provider}'.")
        primary = preset.id == primary_preset.id
        base_url = self._base_url(preset, primary)
        local = preset.is_local(base_url)
        if self._flag("offline_mode") and not local:
            raise NotConfigured(f"Offline mode is on: {preset.label} is a cloud provider. Choose a local model.",
                                provider=preset.id)
        return Target(preset=preset, model=self._model(preset, task, primary, model), base_url=base_url,
                      auth=self._auth(preset, primary), local=local)

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
    def adapter(self, target: Target):
        api_key = self._api_key(target.preset) if target.auth == "api_key" or target.preset.runtime_endpoint else None
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else ""
        options = dict(self.adapter_options.get(target.preset.adapter, {}))
        if target.preset.adapter == "anthropic":
            options.update(auth=target.auth, oauth_profile=self._setting("ai_oauth_profile"))
        elif target.preset.adapter == "openai_responses":
            options.update(prompt_cache_key=self.install_id())
        elif target.preset.adapter == "claude_code":
            options.setdefault("binary", self._setting("ai_claude_code_path"))
        key = (target.preset.id, target.base_url, target.auth, fingerprint, repr(sorted(options.items(), key=str)))
        with self._lock:
            adapter = self._adapters.get(key)
            if adapter is None:
                cls = adapter_class(target.preset.adapter)
                adapter = cls(target.preset, base_url=target.base_url, api_key=api_key, http_client=self.http_client,
                              timeout=float(self._setting("ai_timeout", 600)), **options)
                self._adapters[key] = adapter
        return adapter

    def list_models(self, provider: Optional[str] = None) -> List[ModelInfo]:
        """Live model list of a provider (the preset's suggestions where there is no list endpoint)."""
        return self.adapter(self.resolve("chat", provider=provider)).list_models()

    # --- per-install state ------------------------------------------------------------------------------------
    def _state_path(self, name: str) -> Path:
        base = self._state_dir or Path(getattr(self.config, "user_state_dir", None) or Path.home() / ".llm-framework")
        return Path(base) / name

    def _state_secret(self, name: str) -> str:
        """A random value created on first use and kept in the per-user state directory (mode 0600)."""
        cached = self._state_cache.get(name)
        if cached:
            return cached
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
        self._state_cache[name] = value
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
        """What a call for `task` would use, for the settings dialog (no secrets)."""
        target = self.resolve(task)
        return {"provider": target.provider, "label": target.preset.label, "model": target.model,
                "base_url": target.base_url, "auth": target.auth, "local": target.local,
                "privacy": "off (local)" if target.local else self.privacy_level()}
