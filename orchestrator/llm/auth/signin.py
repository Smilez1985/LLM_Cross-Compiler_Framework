#!/usr/bin/env python3
"""
Sign-in operations behind `llm-cli ditto login|logout|auth` and the GUI's SignInPanel:

- which methods a provider offers, which of them this version can sign in with, and which one is recommended
  (the first of those in the catalog's order that is not opt-in);
- the state of a provider's sign-in (AuthStatus): method, where the credential comes from, account, the last four
  characters of a key at most, never a secret;
- checking a key before it is stored (a typed key never reaches the vault when the check fails), storing it,
  choosing a method that needs no secret (none, an `ant` profile), and signing out locally first;
- choosing how the vault is protected when no keyring is usable, and rotating its key.

Every call may block on the vault, the keyring or the network: the GUI runs them in worker threads. The method a
sign-in uses is remembered as ai_auth_<provider> (R8: nothing is selected silently).
"""

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from orchestrator.llm.auth.errors import EXIT_AUTH, EXIT_NETWORK, EXIT_STORAGE, AuthError, auth_error
from orchestrator.llm.auth.resolver import ResolvedCredential
from orchestrator.llm.auth.spec import KEY_TYPES, OAUTH_TYPES, AuthMethodSpec
from orchestrator.llm.auth.store import display_origin, key_malformed_error, last4, malformed_key
from orchestrator.llm.auth.tokens import sign_out as sign_out_tokens
from orchestrator.llm.auth.validate import ValidationResult, validate_credential
from orchestrator.llm.errors import (AuthFailed, BadRequest, LLMError, NotConfigured, PermissionDenied,
                                     ProviderTimeout, ProviderUnavailable, rejected_key_error)
from orchestrator.security.redaction import SecretStr
from orchestrator.utils.logging import get_logger

# Types this version can sign in with; OAuth flows, cloud credential chains and short-lived tokens come later.
BUILT_TYPES = ("none", "bearer_optional", "api_key")

SIGNED_IN = "signed_in"
NOT_SIGNED_IN = "not_signed_in"
SIGNED_OUT = "signed_out"            # expired or revoked: sign in again
NO_SIGN_IN = "no_sign_in_needed"
STORAGE = "storage_problem"          # the vault cannot be used, so a stored key cannot be read
NOT_RUNNING = "not_running"          # the managed local server is not running
UNAVAILABLE = "not_available"        # the method has no sign-in in this version

STATUS_TEXT = {
    "signed_in_as": "Signed in as {account}",
    "signed_in_key": "Signed in (key ...{last4})",
    "signed_in": "Signed in",
    "env": "Using key from environment variable {name}",
    "profile": "Using the ant CLI profile '{name}'",
    "profile_active": "Using the active ant CLI profile",
    "managed": "The key of the managed local server is set automatically",
    "no_sign_in": "No sign-in needed",
    "not_signed_in": "Not signed in",
    "expired": "Expired: sign in again",
}
PROFILE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
ANT_LOGOUT_NOTE = ("`ant auth logout` would also sign out the other tools that use this profile; run it yourself "
                   "if you want that.")

Translate = Callable[[str, str], str]


def exit_code(error: BaseException) -> int:
    """The CLI's exit code for an error: 3 authentication, 4 network, 5 storage, 6 not allowed or not built."""
    if isinstance(error, AuthError):
        return error.exit_code
    if (getattr(error, "code", None) or "").startswith("storage."):
        return EXIT_STORAGE
    if isinstance(error, (ProviderUnavailable, ProviderTimeout)):
        return EXIT_NETWORK
    if isinstance(error, (AuthFailed, PermissionDenied, BadRequest, NotConfigured)):
        return EXIT_AUTH
    return EXIT_AUTH


def check_base_url(url: str) -> str:
    """A server address typed by the user: http(s), a host, no user info, nothing a parser could read two ways.
    The address is never part of the error (it may hold a password)."""
    text = (url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc or not display_origin(text):
        raise ValueError("The server address must be a plain http:// or https:// URL with a host (no user name or "
                         "password in it).")
    return text


def plain_http_to_remote(url: Optional[str]) -> bool:
    """A key would travel unencrypted: http:// to a host that is not this computer."""
    from orchestrator.llm.client import is_loopback_url
    return bool(url) and urlsplit(url).scheme == "http" and not is_loopback_url(url)


def valid_profile_name(name: str) -> bool:
    return 0 < len(name) <= 64 and set(name) <= PROFILE_NAME_CHARS


@dataclass
class MethodInfo:
    spec: AuthMethodSpec
    built: bool         # this version can sign in with it
    recommended: bool   # the first built method in the catalog's order that is not opt-in


def is_built(preset: Any, spec: AuthMethodSpec) -> bool:
    """This version can sign in with `spec` (the `ant` profile is the only CLI session so far)."""
    if spec.type in BUILT_TYPES:
        return True
    return spec.type == "cli_session" and preset.adapter == "anthropic"


def recommended_method(preset: Any) -> Optional[AuthMethodSpec]:
    return next((m for m in preset.methods if not m.opt_in and is_built(preset, m)), None)


def method_infos(preset: Any) -> List[MethodInfo]:
    """The provider's methods in preferred order (the catalog only: no vault, no network)."""
    recommended = recommended_method(preset)
    return [MethodInfo(spec, is_built(preset, spec), spec is recommended) for spec in preset.methods]


@dataclass(repr=False)
class AuthStatus:
    """A provider's sign-in state for status lines, `auth status` and Ditto's header. Holds no secret."""
    provider: str
    label: str
    state: str
    primary: bool = False
    method: Optional[str] = None
    method_label: Optional[str] = None
    type: Optional[str] = None
    chosen: bool = False             # the method is named by ai_auth_<provider>
    source: Optional[str] = None     # vault | env:<NAME> | profile:<name> | runtime | none
    account: Optional[str] = None
    last4: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)
    origin: Optional[str] = None
    validated_at: Optional[str] = None
    expires_at: Optional[str] = None
    profile: Optional[str] = None
    error: Optional[LLMError] = None
    check: Optional[ValidationResult] = None

    def __repr__(self) -> str:
        return f"AuthStatus(provider={self.provider!r}, state={self.state!r}, method={self.method!r})"

    @property
    def env_variable(self) -> Optional[str]:
        return self.source[4:] if self.source and self.source.startswith("env:") else None

    @property
    def message(self) -> str:
        return status_line(self)

    def to_dict(self) -> Dict[str, Any]:
        error = self.error.to_dict() if isinstance(self.error, AuthError) else (
            {"code": getattr(self.error, "code", None), "message": str(self.error)} if self.error else None)
        out = {"provider": self.provider, "label": self.label, "primary": self.primary, "state": self.state,
               "message": self.message, "method": self.method, "method_label": self.method_label, "type": self.type,
               "chosen": self.chosen, "source": self.source, "account": self.account, "last4": self.last4,
               "fields": dict(self.fields), "origin": self.origin, "validated_at": self.validated_at,
               "expires_at": self.expires_at, "profile": self.profile, "error": error}
        if self.check is not None:
            out["check"] = self.check.to_dict()
        return out


def status_line(status: AuthStatus, translate: Optional[Translate] = None) -> str:
    """"Signed in as ...", "Not signed in", "Using key from environment variable X", ... (`translate(key,
    default)` gives the GUI's language; the templates use {account}, {last4} and {name})."""
    def text(key: str, **values: Any) -> str:
        template = translate(f"signin.status.{key}", STATUS_TEXT[key]) if translate else STATUS_TEXT[key]
        return template.format(**values)

    if status.state in (STORAGE, NOT_RUNNING, UNAVAILABLE) and status.error is not None:
        return str(status.error)
    if status.state == SIGNED_OUT:
        return text("expired")
    if status.state == NOT_SIGNED_IN:
        return text("not_signed_in")
    if status.state == NO_SIGN_IN:
        return text("no_sign_in")
    source = status.source or ""
    if source == "runtime":
        return text("managed")
    if status.env_variable:
        return text("env", name=status.env_variable)
    if source.startswith("profile:"):
        return text("profile", name=status.profile) if status.profile else text("profile_active")
    if status.account:
        return text("signed_in_as", account=status.account)
    if status.last4:
        return text("signed_in_key", last4=status.last4)
    return text("signed_in")


def access_text(info: Mapping[str, Any], translate: Optional[Translate] = None) -> str:
    """Ditto's header: the access a chat uses, e.g. "Anthropic Claude (API) · API key · wrkspc_..." or
    "Local · Ollama" (LLMClient.describe(); no secrets)."""
    from orchestrator.llm.auth.errors import engine_name

    def t(key: str, default: str) -> str:
        return translate(key, default) if translate else default

    label = str(info.get("label") or info.get("provider") or "")
    source = str(info.get("auth_source") or "none")
    if info.get("local") and info.get("auth_type") in ("none", "bearer_optional", None):
        return f"{t('chat.access.local', 'Local')} · {engine_name(SimpleNamespace(label=label))}"
    parts = [label]
    if source.startswith("env:"):
        parts.append(t("chat.access.env", "key from {name}").format(name=source[4:]))
    elif source.startswith("profile:"):
        profile = info.get("profile")
        parts.append(t("chat.access.profile", "ant profile {name}").format(name=profile) if profile
                     else t("chat.access.profile_active", "active ant profile"))
    elif source == "none" and info.get("auth_type") == "api_key":
        parts.append(t("chat.access.not_signed_in", "not signed in"))
    elif source != "none":
        parts.append(str(info.get("auth_label") or info.get("auth") or ""))
    if info.get("workspace_id"):
        parts.append(str(info["workspace_id"]))
    return " · ".join(p for p in parts if p)


class EnvKeyError(ValueError):
    """`login --from-env` cannot take a key: the method reads no variable (`no_variable`), none of its variables is
    set (`not_set`), or the value is not a key (`malformed`: a space, a line break, ...). Holds the names, never a
    value; the CLI and the GUI word it themselves."""

    def __init__(self, code: str, variables: List[str], name: Optional[str] = None):
        self.code, self.variables, self.name = code, list(variables), name
        super().__init__(f"{code}: {name or ', '.join(variables)}")


@dataclass
class Saved:
    metadata: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


@dataclass
class SignOutResult:
    provider: str
    method: str
    type: str
    deleted: bool = False
    revoke_url: Optional[str] = None     # where the user revokes the credential itself
    env_variable: Optional[str] = None   # still set: its key stays in use until it is unset
    note: Optional[str] = None


class SignIn:
    """`config`: the settings (ConfigManager or anything with get/set); `secrets`: the SecretsManager (may have
    failed to open: `storage_error`). `http_client` reaches the provider for checks (tests: a mock transport)."""

    def __init__(self, config: Any, secrets: Any, *, client: Any = None, http_client: Any = None,
                 catalog: Any = None, timeout: float = 30.0):
        from orchestrator.llm.client import LLMClient
        self.config = config
        self.secrets = secrets
        self.client = client if client is not None else LLMClient(config, secrets, http_client=http_client,
                                                                  catalog=catalog)
        self.timeout = timeout
        self.logger = get_logger("SignIn")

    @property
    def store(self):
        return self.client.credentials

    @property
    def resolver(self):
        return self.client.resolver

    @property
    def catalog(self):
        return self.client.catalog

    # --- settings -------------------------------------------------------------------------------------------
    def setting_source(self, key: str) -> Optional[str]:
        """default | file | environment | override, None when the setting has no value."""
        values = getattr(self.config, "config_values", None)
        if isinstance(values, dict):
            entry = values.get(key)
            if entry is None or entry.value in (None, ""):
                return None
            return getattr(getattr(entry, "source", None), "source_type", None)
        try:
            return "override" if self.config.get(key, None) not in (None, "") else None
        except Exception:
            return None

    def _set(self, key: str, value: Any) -> None:
        """Changes a setting and saves the user config ("" removes a sign-in setting from it)."""
        setter = getattr(self.config, "set", None)
        if not callable(setter):
            return
        setter(key, value)
        save = getattr(self.config, "save_user_config", None)
        if callable(save) and save() is False:
            self.logger.warning(f"The setting {key} could not be saved in the user config.")

    def _user_set(self, key: str) -> bool:
        return self.setting_source(key) in ("file", "override")

    # --- providers and methods ------------------------------------------------------------------------------
    def method(self, preset: Any, method_id: str) -> Optional[AuthMethodSpec]:
        """A method by id; `ant-profile` is accepted for `ant_profile`."""
        method_id = (method_id or "").strip()
        return preset.method(method_id) or preset.method(method_id.replace("-", "_"))

    built = staticmethod(is_built)
    methods = staticmethod(method_infos)
    recommended = staticmethod(recommended_method)

    def endpoint(self, preset: Any, base_url: Optional[str] = None) -> Optional[str]:
        """The server address: `base_url` for presets whose address the user sets, else the configured one.
        Raises NotConfigured for a managed local server that is not running and ValueError for a bad address."""
        if base_url:
            if preset.runtime_endpoint or not preset.base_url_configurable:
                raise ValueError(f"{preset.label} has a fixed address; a server address does not apply.")
            return check_base_url(base_url)
        return self.client.endpoint(preset)

    def chosen(self, preset: Any) -> Optional[AuthMethodSpec]:
        name, _ = self.client.chosen_method(preset)
        return preset.method(name) if name else None

    def profile(self, preset: Any, spec: AuthMethodSpec) -> Optional[str]:
        """The `ant` profile name in use (setting ai_oauth_profile, else the one stored at sign-in)."""
        name = self.resolver.setting("ai_oauth_profile") or self.store.metadata(preset.id, spec.id).get("profile")
        return str(name).strip() if name else None

    def _origin(self, preset: Any, spec: AuthMethodSpec, url: Optional[str]) -> Optional[str]:
        return (display_origin(url) or None) if spec.binds_origin(preset) else None

    def storage_problem(self, provider: str = "") -> Optional[AuthError]:
        if self.secrets is None:
            return auth_error("storage.unavailable", provider=provider,
                              message="The credential vault is not available in this process.")
        return self.store.storage_problem(provider)

    def storage_message(self) -> Optional[str]:
        """The vault's own full explanation of why it cannot be used (None while it works)."""
        error = getattr(self.secrets, "storage_error", None)
        if error is not None:
            return getattr(error, "message", None) or str(error)
        unreadable = getattr(self.secrets, "load_error", None)
        return unreadable if isinstance(unreadable, str) and unreadable else None

    def _require_vault(self, provider: str = "") -> None:
        problem = self.storage_problem(provider)
        if problem is not None:
            raise problem

    # --- state ----------------------------------------------------------------------------------------------
    def status(self, preset: Any, base_url: Optional[str] = None) -> AuthStatus:
        """The provider's sign-in state. Only looks: nothing is moved, bound or written."""
        st = AuthStatus(provider=preset.id, label=preset.label, state=NOT_SIGNED_IN,
                        primary=self.client.is_primary(preset))
        try:
            url = self.endpoint(preset, base_url)
        except NotConfigured as e:
            st.state, st.error = NOT_RUNNING, e
            self._describe(st, preset, next(iter(preset.methods), None))
            return st
        st.origin = display_origin(url) or None
        chosen, explicit = self.client.chosen_method(preset)
        try:
            spec = self.resolver.select(preset, chosen=chosen, explicit=explicit, base_url=url, adopt=False)
        except NotConfigured as e:
            problem = self.storage_problem(preset.id)
            spec = (preset.method(chosen) if chosen else None) or self.recommended(preset)
            signed_out = spec is not None and self.store.is_signed_out(preset.id, spec.id)
            st.state = STORAGE if problem else (SIGNED_OUT if signed_out else NOT_SIGNED_IN)
            st.error = problem or e
            self._describe(st, preset, spec)
            return st
        self._describe(st, preset, spec)
        if spec.type == "none":
            st.state, st.source = NO_SIGN_IN, "none"
        elif spec.type in KEY_TYPES:
            self._key_status(st, preset, spec, url)
        elif spec.type == "cli_session" and preset.adapter == "anthropic":
            st.profile = self.profile(preset, spec)
            st.state, st.source = SIGNED_IN, f"profile:{st.profile or 'active'}"
        elif spec.type in OAUTH_TYPES:
            meta = self.store.metadata(preset.id, spec.id)
            if self.store.is_signed_out(preset.id, spec.id):
                st.state = SIGNED_OUT
            elif self.store.get_record(preset.id, spec.id):
                st.state, st.source = SIGNED_IN, "vault"
                st.account, st.expires_at = meta.get("account_label"), meta.get("expires_at")
        else:
            st.state = UNAVAILABLE
            st.error = auth_error("auth.not_built", provider=preset.id, provider_label=preset.label, method=spec.id)
        return st

    def _describe(self, st: AuthStatus, preset: Any, spec: Optional[AuthMethodSpec]) -> None:
        if spec is None:
            return
        chosen = self.chosen(preset)
        st.method, st.method_label, st.type = spec.id, spec.label, spec.type
        st.chosen = chosen is not None and chosen.id == spec.id

    def _key_status(self, st: AuthStatus, preset: Any, spec: AuthMethodSpec, url: Optional[str]) -> None:
        if preset.runtime_endpoint:
            st.state, st.source = SIGNED_IN, "runtime"
            return
        found = self.store.find_secret(preset, spec, url, adopt=False)
        if found is None:
            problem = self.storage_problem(preset.id)
            if problem is not None:
                st.state, st.error = STORAGE, problem
            else:
                st.state = NO_SIGN_IN if spec.type == "bearer_optional" else NOT_SIGNED_IN
            return
        st.state, st.source = SIGNED_IN, found.source
        st.last4 = last4(found.value.get_secret_value().strip())
        origin = self._origin(preset, spec, url)
        if found.source == "vault":
            meta = self.store.metadata(preset.id, spec.id, origin)
            st.account, st.validated_at = meta.get("account_label"), meta.get("validated_at")
        try:
            st.fields = self.resolver.field_values(preset, spec, origin)
        except AuthError as e:
            st.error = e

    def status_all(self, *, check: bool = False) -> List[AuthStatus]:
        """Every provider of the catalog; `check` runs the zero-cost check where there is a credential (and for
        the primary provider)."""
        out = []
        for preset in self.catalog.providers.values():
            st = self.status(preset)
            if check and st.method and (st.state == SIGNED_IN or (st.primary and st.state == NO_SIGN_IN)):
                st.check = self.check(preset, preset.method(st.method))
            out.append(st)
        return out

    # --- check ----------------------------------------------------------------------------------------------
    def check(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None, *,
              key: Optional[str] = None, fields: Optional[Mapping[str, Any]] = None,
              profile: Optional[str] = None) -> ValidationResult:
        """The zero-cost check of a typed key (with typed fields), of a profile, or of the stored credential.
        Never raises for a provider's answer or a problem found before the request: the result says it."""
        result = ValidationResult(ok=False, provider=preset.id, method=spec.id)
        try:
            url = self.endpoint(preset, base_url)
            stored = False
            if spec.type in KEY_TYPES and not preset.runtime_endpoint and (key is not None or fields):
                credential = self._typed_credential(preset, spec, url, key, fields or {})
            elif spec.type == "cli_session" and preset.adapter == "anthropic":
                name = profile if profile is not None else self.profile(preset, spec)
                credential = ResolvedCredential(kind="anthropic_profile", method=spec.id, type=spec.type,
                                                source=f"profile:{name or 'active'}", profile=name or None)
            elif not self.built(preset, spec) and spec.type not in OAUTH_TYPES:
                raise auth_error("auth.not_built", provider=preset.id, provider_label=preset.label, method=spec.id)
            else:
                credential = self.resolver.resolve(preset, spec, url)
                stored = credential.source == "vault"
        except LLMError as e:
            result.error = e
            return result
        result = validate_credential(preset, spec, credential, url, http_client=self.client.http_client,
                                     timeout=self.timeout,
                                     list_models=lambda: self.client.adapter_for(preset, url, credential).list_models())
        if result.ok and stored and spec.type in KEY_TYPES:
            self.store.update_metadata(preset.id, spec.id, {"validated_at": self.store.now(),
                                                            "account_label": result.account_label},
                                       self._origin(preset, spec, url))
        return result

    def _merged_fields(self, preset: Any, spec: AuthMethodSpec, origin: Optional[str],
                       typed: Mapping[str, Optional[str]]) -> Dict[str, str]:
        """Typed values win; the others come from the setting or the stored metadata, as at use."""
        meta = self.store.metadata(preset.id, spec.id, origin)
        out: Dict[str, str] = {}
        for item in spec.fields:
            if item.id in typed:
                value = typed[item.id] or ""
            else:
                raw = self.resolver.setting(item.setting) if item.setting else None
                raw = raw if raw not in (None, "") else meta.get(item.id)
                value = str(raw).strip() if raw not in (None, "") else ""
            if not value:
                if item.required:
                    raise auth_error("auth.field_invalid", provider=preset.id, provider_label=preset.label,
                                     message=f"{preset.label}: the {item.label} is required.")
                continue
            if not item.matches(value):
                raise self.store.field_error(preset, item)
            out[item.id] = value
        return out

    def _typed_credential(self, preset: Any, spec: AuthMethodSpec, url: Optional[str], key: Optional[str],
                          fields: Mapping[str, Any]) -> ResolvedCredential:
        """`key` None: the stored key with the typed fields; "" (an optional key left empty): no key at all."""
        origin = self._origin(preset, spec, url)
        value, source = (key or "").strip(), "input"
        if value and spec.rejected(value):
            raise rejected_key_error(preset)  # before any request
        if malformed_key(value):
            raise key_malformed_error(preset, spec.key_page)
        if key is not None and not value and spec.type == "api_key":
            raise auth_error("auth.not_signed_in", provider=preset.id, message="No key was entered.")
        typed = self.store.check_fields(preset, spec, fields)
        values = self._merged_fields(preset, spec, origin, typed)
        if key is None:
            # As when it is used: a key stored without an address is bound to this one first, so it never goes to
            # one address unbound and is then bound to (and sent to) another.
            found = self.store.find_secret(preset, spec, url)
            if found is not None:
                value, source = found.value.get_secret_value().strip(), found.source
            elif spec.type == "api_key":
                raise self.resolver.not_signed_in(preset, spec)
        headers = {item.header: values[item.id] for item in spec.fields if item.header and item.id in values}
        return ResolvedCredential(kind="api_key" if value else "none", method=spec.id, type=spec.type,
                                  source=source if value else "none", api_key=SecretStr.wrap(value),
                                  headers=headers, workspace_id=values.get("workspace_id"),
                                  project=values.get("project"), origin=origin)

    # --- the key from an environment variable (login --from-env) --------------------------------------------
    def _environ(self) -> Mapping[str, str]:
        environ = getattr(self.store, "environ", None)
        return os.environ if environ is None else environ

    def env_variable(self, spec: AuthMethodSpec) -> Optional[str]:
        """The first of the method's environment variables that is set (its name only), else None."""
        environ = self._environ()
        return next((name for name in spec.env if (environ.get(name) or "").strip()), None)

    def env_key(self, preset: Any, spec: AuthMethodSpec) -> "tuple[str, str]":
        """The key in the method's first environment variable that is set, and that variable's name, for storing it
        in the vault (`llmcf ditto login PROVIDER --from-env`; Security > Sign-in Overview... > Store the key from
        the environment variable). Raises EnvKeyError; never reads the vault or the network."""
        if not spec.env:
            raise EnvKeyError("no_variable", [])
        name = self.env_variable(spec)
        if name is None:
            raise EnvKeyError("not_set", list(spec.env))
        value = (self._environ().get(name) or "").strip()
        if malformed_key(value):
            raise EnvKeyError("malformed", list(spec.env), name)
        return value, name

    # --- storing ----------------------------------------------------------------------------------------------
    def save_key(self, preset: Any, spec: AuthMethodSpec, key: str, base_url: Optional[str] = None, *,
                 fields: Optional[Mapping[str, Any]] = None, source: str = "manual",
                 remember_url: bool = False, checked: Optional[ValidationResult] = None) -> Saved:
        """Stores a typed key with its fields (stored values the user did not give are kept) and remembers the
        method; `checked` (the check of this key just before) gives the account for "Signed in as ...". Raises an
        AuthError when the key, a field or the vault refuses."""
        if spec.type not in KEY_TYPES or preset.runtime_endpoint:
            raise ValueError(f"{preset.label}: '{spec.id}' does not take a key")
        self._require_vault(preset.id)
        url = self.endpoint(preset, base_url)
        origin = self._origin(preset, spec, url)
        given = dict(fields or {})
        old = self.store.metadata(preset.id, spec.id, origin)
        kept = {f.id: old[f.id] for f in spec.fields if old.get(f.id) and f.id not in given}
        metadata = self.store.save_api_key(preset, spec, key, base_url=url, fields={**kept, **given}, source=source)
        if checked is not None and checked.ok and checked.key_checked:
            self.store.update_metadata(preset.id, spec.id, {"validated_at": self.store.now(),
                                                            "account_label": checked.account_label}, origin)
            metadata = self.store.metadata(preset.id, spec.id, origin)
        saved = Saved(metadata, self._sync_field_settings(preset, spec, given))
        self._remember(preset, spec, base_url if remember_url else None)
        return saved

    def save_fields(self, preset: Any, spec: AuthMethodSpec, fields: Mapping[str, Any],
                    base_url: Optional[str] = None) -> Saved:
        """Changes only the fields of a stored key (the GUI's Save with an empty key field)."""
        self._require_vault(preset.id)
        url = self.endpoint(preset, base_url)
        if self.store.find_secret(preset, spec, url, adopt=False) is None:
            raise self.resolver.not_signed_in(preset, spec)
        values = self.store.check_fields(preset, spec, fields)
        origin = self._origin(preset, spec, url)
        self.store.update_metadata(preset.id, spec.id, values, origin)
        saved = Saved(self.store.metadata(preset.id, spec.id, origin), self._sync_field_settings(preset, spec, values))
        self._remember(preset, spec)
        return saved

    def _sync_field_settings(self, preset: Any, spec: AuthMethodSpec, given: Mapping[str, Any]) -> List[str]:
        """A field whose setting is set in the user config (ai_anthropic_workspace_id) gets the new value there
        too, so the setting never overrides what was just stored; one set in the environment is reported."""
        warnings = []
        for item in spec.fields:
            if not item.setting or item.id not in given:
                continue
            value = str(given[item.id] or "").strip()
            source = self.setting_source(item.setting)
            if source == "environment":
                current = str(self.resolver.setting(item.setting) or "")
                if current != value:
                    warnings.append(f"The environment sets {item.setting} ({'LLM_FRAMEWORK_' + item.setting.upper()}"
                                    f"), which wins over the {item.label} stored with the key.")
            elif source in ("file", "override"):
                self._set(item.setting, value)
        return warnings

    def _remember(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None) -> None:
        self._set(f"ai_auth_{preset.id}", spec.id)
        if base_url:
            self._set(f"ai_base_url_{preset.id}", base_url)

    def use_method(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None, *,
                   profile: Optional[str] = None, remember_url: bool = False) -> None:
        """Signs in with a method that stores no secret: none, a key that stays optional, an `ant` profile (only
        its name is kept)."""
        if not self.built(preset, spec):
            raise auth_error("auth.not_built", provider=preset.id, provider_label=preset.label, method=spec.id)
        if base_url:
            base_url = self.endpoint(preset, base_url)
        if spec.type == "cli_session":
            profile = (profile or "").strip() or None
            if profile is not None and not valid_profile_name(profile):
                raise ValueError("A profile name has letters, digits, '.', '_' or '-' (at most 64).")
            self.store.update_metadata(preset.id, spec.id, {"profile": profile, "created_at": self.store.now(),
                                                            "source": "cli"}, replace=True)
            if self._user_set("ai_oauth_profile"):
                self._set("ai_oauth_profile", profile or "")
        self._remember(preset, spec, base_url if remember_url else None)

    def sign_out(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None) -> SignOutResult:
        """Signs out locally (the stored key, the remembered profile or address) and says what it cannot revoke."""
        result = SignOutResult(preset.id, spec.id, spec.type)
        try:
            url = self.endpoint(preset, base_url)
        except NotConfigured:
            url = None
        if spec.type in KEY_TYPES and not preset.runtime_endpoint:
            if spec.binds_origin(preset) and not display_origin(url or preset.base_url):
                raise ValueError(f"{preset.label}: a key belongs to one server address; give the address whose key "
                                 "is deleted.")
            self._require_vault(preset.id)
            result.deleted = self.store.delete_key(preset, spec, url)
            result.revoke_url = spec.key_page
            left = self.store.find_secret(preset, spec, url, adopt=False)
            if left is not None and left.source.startswith("env:"):
                result.env_variable = left.source[4:]
        elif spec.type in KEY_TYPES:
            result.note = "The managed local server gets a new key at every start; nothing is stored."
        elif spec.type == "cli_session":
            result.deleted = bool(self.store.metadata(preset.id, spec.id)) or self.chosen(preset) is spec
            self.store.remove_metadata(preset.id, spec.id)
            if self._user_set("ai_oauth_profile"):
                self._set("ai_oauth_profile", "")
            result.note = ANT_LOGOUT_NOTE
        elif spec.type == "none":
            if self._user_set(f"ai_base_url_{preset.id}"):
                self._set(f"ai_base_url_{preset.id}", "")
                result.deleted = True
        elif spec.type in OAUTH_TYPES:
            self._require_vault(preset.id)
            result.deleted = self.store.get_record(preset.id, spec.id) is not None
            sign_out_tokens(self.store, preset.id, spec.id)
            result.revoke_url = spec.key_page
            result.note = "The provider was not asked to revoke the sign-in; revoke it in your account settings."
        name, explicit = self.client.chosen_method(preset)
        if explicit and preset.method(name) is spec:
            self._set(f"ai_auth_{preset.id}", "")
            result.deleted = True if spec.type in ("none", "cli_session") else result.deleted
        return result

    # --- storage ------------------------------------------------------------------------------------------------
    def storage(self) -> Dict[str, Any]:
        """How the vault is protected (no secrets): mode, whether it can be used now, the problem, the file."""
        from orchestrator.Core.vault_key import STORAGE_CHOICES
        problem = self.storage_problem()
        mode = getattr(self.secrets, "storage_mode", None) or str(self.config.get("secret_storage", "keyring")
                                                                   or "keyring")
        path = getattr(self.secrets, "secrets_file", None)
        return {"mode": mode, "usable": problem is None, "file": str(path) if path else None,
                "problem": problem.to_dict() if problem is not None else None,
                "choices": {name: text for name, text in STORAGE_CHOICES.items()}}

    def choose_storage(self, mode: str, helper: Optional[str] = None) -> None:
        """Protects the vault the chosen way: moves a working vault (every entry re-encrypted), or opens it with
        the new protection when it could not be opened. Raises StorageError; the setting stays as it was then."""
        from orchestrator.Core.vault_key import STORAGE_MODES, StorageError
        mode = (mode or "").strip().lower()
        if mode not in STORAGE_MODES:
            raise StorageError("storage.invalid_mode", f"Unknown storage '{mode}'. Choose one of: "
                                                       f"{', '.join(m for m in STORAGE_MODES if m != 'ephemeral')}.")
        if mode == "ephemeral":
            raise StorageError("storage.invalid_mode", "ephemeral is chosen per run (LLM_SECRET_STORAGE=ephemeral) "
                                                       "and protects no vault.")
        if os.environ.get("LLM_SECRET_STORAGE"):
            raise StorageError("storage.invalid_mode", "LLM_SECRET_STORAGE decides the storage for this run; "
                                                       "unset it to choose one.")
        sm = self.secrets
        if sm is None:
            raise StorageError("storage.unavailable", "The credential vault is not available in this process.")
        helper = (helper or "").strip() or None
        if getattr(sm, "storage_error", None) is None and getattr(sm, "storage_mode", None):
            if not sm.change_storage(mode, helper=helper):
                raise sm.storage_error or StorageError(
                    "storage.write_failed", "The vault could not be moved to the new protection; it was not "
                                            "changed (the log says why).")
            return
        # Saved only once the vault opens with it: a failed choice leaves the user config as it was (no default or
        # environment value is written into it).
        keys = ("secret_storage", "secret_helper")
        values = getattr(self.config, "config_values", None)
        previous = {key: (values.get(key) if isinstance(values, dict) else self.config.get(key, None)) for key in keys}
        self.config.set("secret_storage", mode)
        if helper:
            self.config.set("secret_helper", helper)
        if sm.initialize():
            save = getattr(self.config, "save_user_config", None)
            if callable(save) and save() is False:
                self.logger.warning("The storage choice could not be saved in the user config.")
            return
        error = sm.storage_error
        for key, value in previous.items():
            if isinstance(values, dict):
                if value is None:
                    values.pop(key, None)
                else:
                    values[key] = value
            else:
                self.config.set(key, value if value is not None else "")
        raise error or StorageError("storage.unavailable", f"The {mode} storage cannot be used.")

    def rotate_key(self) -> None:
        """A new vault key; every entry is re-encrypted and the old key removed. Raises an AuthError."""
        self._require_vault()
        if not self.secrets.rotate_key():
            raise self.storage_problem() or auth_error(
                "storage.write_failed", message="The vault key could not be rotated; the vault was not changed "
                                                "(the log says why).")
