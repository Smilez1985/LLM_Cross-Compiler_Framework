#!/usr/bin/env python3
"""
CredentialResolver: which sign-in method a call uses, and the credential the adapter gets for it.

Selection (select()): the method the user chose (ai_auth_<provider>; ai_auth for the primary provider, which can
never pick an opt-in method), else the first method in the catalog's preferred order that is usable now:
  none, bearer_optional     always (bearer_optional sends a key when one is stored for the origin)
  api_key                   a key is stored, or set in one of the method's environment variables
  oauth_* (stored record)   a record exists, is not signed out, and is an API key or has a refresher
  cli_session, cloud_*, short_lived_token   only when chosen (and a handler is registered for the type)
Opt-in methods (an `ant` profile) are never picked automatically.

resolve() returns a ResolvedCredential: frozen, its repr shows no value. Later packages add types with
register_type() and token refreshers with register_refresher().
"""

import hashlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Tuple, Union

from orchestrator.llm.auth.errors import auth_error
from orchestrator.llm.auth.spec import KEY_TYPES, OAUTH_TYPES, AuthMethodSpec
from orchestrator.llm.auth.store import CredentialStore, display_origin
from orchestrator.llm.auth.tokens import Refresher, TokenProvider
from orchestrator.llm.errors import NotConfigured, rejected_key_error
from orchestrator.security.redaction import SecretStr, reveal

CredentialKind = Literal["none", "api_key", "token_callable", "anthropic_profile", "aws", "google", "azure"]


@dataclass(frozen=True, repr=False, eq=False)
class ResolvedCredential:
    """What an adapter needs to build its SDK client. `headers` are non-secret (workspace ID, project)."""
    kind: CredentialKind
    method: str
    type: str
    source: str = "none"           # none | vault | env:<NAME> | runtime | profile:<name> | vault (oauth)
    api_key: Optional[SecretStr] = None
    token_provider: Optional[Callable[[], str]] = None
    headers: Mapping[str, str] = field(default_factory=dict)
    profile: Optional[str] = None
    region: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    endpoint: Optional[str] = None
    workspace_id: Optional[str] = None
    origin: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "api_key", SecretStr.wrap(self.api_key))
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    def __repr__(self) -> str:
        key = "<redacted>" if self.api_key else None
        token = "<callable>" if self.token_provider else None
        return (f"ResolvedCredential(kind={self.kind!r}, method={self.method!r}, source={self.source!r}, "
                f"api_key={key}, token_provider={token}, headers={sorted(self.headers)!r})")

    __str__ = __repr__

    def __reduce_ex__(self, protocol):
        raise TypeError("a ResolvedCredential is not pickled: it holds a credential")

    @property
    def fingerprint(self) -> str:
        """Tells credentials apart (adapter cache) without keeping the value: a short hash."""
        parts = [self.kind, self.method, reveal(self.api_key) or "", repr(sorted(self.headers.items())),
                 self.profile or "", "token" if self.token_provider else ""]
        return hashlib.sha256("\x00".join(parts).encode("utf-8", "surrogatepass")).hexdigest()[:16]

    def describe(self) -> Dict[str, Any]:
        """For status lines and `auth status` (no secrets): method, type, source, origin, header names."""
        out: Dict[str, Any] = {"method": self.method, "type": self.type, "kind": self.kind, "source": self.source}
        for name in ("origin", "workspace_id", "profile", "region", "project", "location", "endpoint"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        if self.headers:
            out["headers"] = sorted(self.headers)
        return out


# type -> (resolve(resolver, preset, spec, base_url) -> ResolvedCredential, available(...) -> bool)
TypeHandler = Tuple[Callable[..., ResolvedCredential], Callable[..., bool]]
TYPE_HANDLERS: Dict[str, TypeHandler] = {}
REFRESHERS: Dict[Tuple[str, str], Refresher] = {}  # (provider, method) -> refresher of its OAuth tokens


def register_type(auth_type: str, resolve: Callable[..., ResolvedCredential],
                  available: Optional[Callable[..., bool]] = None) -> None:
    """Adds a sign-in type (cloud chains, short-lived tokens, other CLI sessions) for every resolver."""
    TYPE_HANDLERS[auth_type] = (resolve, available or (lambda *a: False))


def register_refresher(provider: str, method: str, refresher: Refresher) -> None:
    """Adds the token refresh of one OAuth method (its vendor module)."""
    REFRESHERS[(provider, method)] = refresher


RuntimeKey = Callable[[Any], Union[str, SecretStr, None]]
_READ = object()   # resolve(): read the running local server's key through the runtime_key callback


class CredentialResolver:
    def __init__(self, store: CredentialStore, *, setting: Callable[[str], Any] = lambda key: None,
                 runtime_key: RuntimeKey = lambda preset: None):
        self.store = store
        self.setting = setting          # a value the user set (file, environment, GUI), None otherwise
        self.runtime_key = runtime_key  # the per-start key of a running local server (runtime_endpoint presets)
        self.refreshers: Dict[Tuple[str, str], Refresher] = dict(REFRESHERS)
        self.handlers: Dict[str, TypeHandler] = dict(TYPE_HANDLERS)

    # --- selection ------------------------------------------------------------------------------------------
    def available(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None, *,
                  adopt: bool = True) -> bool:
        """Whether `spec` can be used now without the user choosing it (opt-in aside). `adopt=False` only looks
        (`auth status`): a key stored without an origin is not bound to `base_url`."""
        if spec.type in ("none", "bearer_optional"):
            return True
        if spec.type == "api_key":
            if preset.runtime_endpoint:
                return bool(reveal(self.runtime_key(preset)))
            return self.store.find_secret(preset, spec, base_url, adopt=adopt) is not None
        if spec.type in OAUTH_TYPES:
            if self.store.is_signed_out(preset.id, spec.id):
                return False
            record = self.store.get_record(preset.id, spec.id)
            return bool(record) and (record.get("kind") == "api_key" or (preset.id, spec.id) in self.refreshers)
        handler = self.handlers.get(spec.type)
        return bool(handler and handler[1](self, preset, spec, base_url))

    def select(self, preset: Any, *, chosen: Optional[str] = None, explicit: bool = False,
               base_url: Optional[str] = None, adopt: bool = True) -> AuthMethodSpec:
        """The method to use. `chosen` comes from ai_auth_<provider> (`explicit`) or ai_auth (primary only)."""
        if chosen:
            chosen = str(chosen).strip()
            spec = preset.method(chosen)  # also the earlier names ("oauth" for ant_profile)
            if spec is None:
                raise NotConfigured(f"{preset.label} does not support sign-in method '{chosen}'.", provider=preset.id)
            if spec.opt_in and not explicit:
                raise NotConfigured(f"{preset.label}: the sign-in method '{spec.id}' is used only when "
                                    f"ai_auth_{preset.id} is set to it.", provider=preset.id)
            return spec
        for spec in preset.methods:
            if not spec.opt_in and self.available(preset, spec, base_url, adopt=adopt):
                return spec
        raise self.not_signed_in(preset)

    def not_signed_in(self, preset: Any, spec: Optional[AuthMethodSpec] = None) -> NotConfigured:
        """No usable credential. When the vault cannot be read, that is the answer (a key may be stored there)."""
        problem = self.store.storage_problem(preset.id)
        if problem is not None:
            return problem
        spec = spec or next((m for m in preset.methods if m.type == "api_key"), None)
        env = f" or set {' or '.join(spec.env)}" if spec is not None and spec.env else ""
        key_page = spec.key_page if spec is not None else None
        login = f"llm-cli ditto login {preset.id}"
        if preset.adapter == "anthropic":
            message = (f"Claude API: no API key stored. Add one in the AI settings or with `{login}`{env} (Max and "
                       "Team plans include monthly API credits for a linked Console organization). To use an `ant "
                       f"auth login` profile instead: `{login} --method ant_profile` (sets ai_auth_anthropic).")
        else:
            message = f"{preset.label}: no API key stored. Add one in the AI settings or with `{login}`{env}."
        return auth_error("auth.not_signed_in", message=message, provider=preset.id, key_page=key_page)

    # --- credentials ----------------------------------------------------------------------------------------
    def resolve(self, preset: Any, method: Union[str, AuthMethodSpec], base_url: Optional[str] = None, *,
                runtime_key: Any = _READ) -> ResolvedCredential:
        """The credential for `method`. For a running local server (runtime_endpoint presets) `runtime_key` is the
        key read together with its URL (LLMClient.adapter: a restarted server has a new port and a new key, and the
        two must come from one read); without it the key is read through the runtime_key callback."""
        spec = method if isinstance(method, AuthMethodSpec) else preset.method(method)
        if spec is None:
            raise NotConfigured(f"{preset.label} does not support sign-in method '{method}'.", provider=preset.id)
        if preset.runtime_endpoint and spec.type in ("none", *KEY_TYPES):
            key = reveal(self.runtime_key(preset) if runtime_key is _READ else runtime_key)
            return ResolvedCredential(kind="api_key" if key else "none", method=spec.id, type=spec.type,
                                      source="runtime" if key else "none", api_key=key,
                                      origin=display_origin(base_url) or None)
        if spec.type == "none":
            return ResolvedCredential(kind="none", method=spec.id, type=spec.type)
        if spec.type in KEY_TYPES:
            return self._key(preset, spec, base_url)
        if spec.type == "cli_session" and preset.adapter == "anthropic":
            profile = self.setting("ai_oauth_profile") or self.store.metadata(preset.id, spec.id).get("profile")
            profile = str(profile).strip() if profile else None
            return ResolvedCredential(kind="anthropic_profile", method=spec.id, type=spec.type,
                                      source=f"profile:{profile or 'active'}", profile=profile)
        if spec.type in OAUTH_TYPES:
            return self._oauth(preset, spec)
        handler = self.handlers.get(spec.type)
        if handler is not None:
            return handler[0](self, preset, spec, base_url)
        raise auth_error("auth.not_built", provider=preset.id, provider_label=preset.label, method=spec.id)

    def field_values(self, preset: Any, spec: AuthMethodSpec, origin: Optional[str] = None) -> Dict[str, str]:
        """The fields of `spec`: a setting named by the field wins while it is set (the AI dialog still writes
        ai_anthropic_workspace_id), else the metadata stored with the key (for its origin where keys are bound).
        Malformed values raise before any request."""
        meta = self.store.metadata(preset.id, spec.id, origin)
        values: Dict[str, str] = {}
        for item in spec.fields:
            raw = self.setting(item.setting) if item.setting else None
            if raw in (None, ""):
                raw = meta.get(item.id)
            value = str(raw).strip() if raw not in (None, "") else ""
            if not value:
                if item.required:
                    raise auth_error("auth.field_invalid", provider=preset.id, provider_label=preset.label,
                                     message=f"{preset.label}: the {item.label} is required.")
                continue
            if not item.matches(value):
                raise self.store.field_error(preset, item)
            values[item.id] = value
        return values

    def _key(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str]) -> ResolvedCredential:
        origin = (display_origin(base_url) or None) if spec.binds_origin(preset) else None
        found = self.store.find_secret(preset, spec, base_url)
        if found is None:
            if spec.type == "bearer_optional":
                return ResolvedCredential(kind="none", method=spec.id, type=spec.type, origin=origin)
            raise self.not_signed_in(preset, spec)
        key = found.value.get_secret_value().strip()
        if spec.rejected(key):
            raise rejected_key_error(preset)  # before any request
        values = self.field_values(preset, spec, origin)
        headers = {item.header: values[item.id] for item in spec.fields if item.header and item.id in values}
        return ResolvedCredential(kind="api_key", method=spec.id, type=spec.type, source=found.source,
                                  api_key=SecretStr(key), headers=headers, workspace_id=values.get("workspace_id"),
                                  project=values.get("project"), region=values.get("region"),
                                  endpoint=values.get("endpoint"), origin=origin)

    def _oauth(self, preset: Any, spec: AuthMethodSpec) -> ResolvedCredential:
        if self.store.is_signed_out(preset.id, spec.id):
            raise auth_error("auth.signed_out", provider=preset.id, provider_label=preset.label)
        record = self.store.get_record(preset.id, spec.id)
        if not record:
            raise self.store.storage_problem(preset.id) or auth_error(
                "auth.not_signed_in", provider=preset.id, provider_label=preset.label, key_page=spec.key_page)
        if record.get("kind") == "api_key" and isinstance(record.get("secret"), str):
            # A flow whose result is an API key (OpenRouter): used like a stored key, nothing to refresh.
            return ResolvedCredential(kind="api_key", method=spec.id, type=spec.type, source="vault",
                                      api_key=SecretStr(record["secret"].strip()))
        refresher = self.refreshers.get((preset.id, spec.id))
        if refresher is None:
            raise auth_error("auth.not_built", provider=preset.id, provider_label=preset.label, method=spec.id)
        provider = TokenProvider(self.store, preset.id, spec.id, refresher, label=preset.label)
        return ResolvedCredential(kind="token_callable", method=spec.id, type=spec.type, source="vault",
                                  token_provider=provider)
