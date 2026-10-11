#!/usr/bin/env python3
"""
Sign-in methods of a provider preset (configs/llm_providers.yml, schema v2) as typed data.

A preset lists its methods in preferred order under `auth`. An entry is a mapping (schema v2) or a plain method
name (the v1 form `auth: [api_key, none]`, which expands to the default spec of that name):

  - id: api_key                    unique in the preset; names the vault entry cred/<provider>/<id>
    type: api_key                  see AUTH_TYPES
    label: "API key"
    key_page: https://...          where the user creates (and revokes) the key
    env: [ANTHROPIC_API_KEY]       documented variables read as a source when nothing is stored (shown as such)
    reject_prefixes: [...]         key kinds refused before any request (admin keys)
    fields: [...]                  non-secret extra values (workspace ID, organization, ...); see FieldSpec
    validate: {path: /v1/models}   the zero-cost GET that checks the credential; see ValidateSpec
    opt_in: true                   never picked automatically, only when ai_auth_<provider> names it
"""

import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

AuthType = Literal["none", "bearer_optional", "api_key", "cli_session", "oauth_pkce_loopback", "oauth_other",
                   "oauth_device_code", "cloud_credential_chain", "short_lived_token"]
AUTH_TYPES: tuple = AuthType.__args__
OAUTH_TYPES = ("oauth_pkce_loopback", "oauth_other", "oauth_device_code")
KEY_TYPES = ("api_key", "bearer_optional")  # a static key from the vault (or the environment)

LEGACY_AUTH = {"oauth": "ant_profile"}  # earlier name of the `ant auth login` method (catalogs and settings)
ALWAYS_OPT_IN = ("ant_profile",)        # never picked automatically, whatever a (user) catalog says
CLAUDE_API_HOST = "api.anthropic.com"
CLAUDE_ADMIN_PREFIX = "sk-ant-admin01-"  # Admin API keys never work for chat (R11)

# Metadata keys the store writes itself; a field must not use one of them as its id.
RESERVED_METADATA = frozenset({
    "origin", "account_label", "last4", "scopes", "created_at", "validated_at", "expires_at", "issued_client_id",
    "source", "state", "signed_out_at", "signed_out_reason", "refreshed_at", "method", "type", "kind"})

# Headers that carry the credential itself: a non-secret field (stored in plain text) must never replace the key.
CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key",
                                "cookie"})

_METHOD_ID = r"^[a-z0-9][a-z0-9_.-]{0,63}$"  # one segment of cred/<provider>/<method>
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_DOTTED = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


def _https_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    parts = urlparse(str(value))
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        raise ValueError(f"expected an https URL without user info: {value!r}")
    return str(value)


class FieldSpec(BaseModel):
    """A non-secret value that belongs to a credential, e.g. the Claude workspace ID or the OpenAI project."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str
    pattern: Optional[str] = None       # full match after trimming
    header: Optional[str] = None        # request header that carries the value
    required: bool = False
    setting: Optional[str] = None       # a setting that holds the value too and wins while it is set
    invalid_code: Optional[str] = None  # error code when the value does not match (default auth.field_invalid)

    @field_validator("id")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value in RESERVED_METADATA:
            raise ValueError(f"field id '{value}' is reserved for the store's own metadata")
        return value

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            re.compile(value)
        return value

    @field_validator("header")
    @classmethod
    def _header_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _HEADER_NAME.match(value):
            raise ValueError(f"not a header name: {value!r}")
        if value is not None and value.lower() in CREDENTIAL_HEADERS:
            raise ValueError(f"{value!r} is a credential header: a non-secret field must not replace the key")
        return value

    def matches(self, value: str) -> bool:
        """A header-safe value (printable ASCII, no spaces) that fits the pattern, if there is one."""
        if not value or not value.isascii() or not value.isprintable() or " " in value:
            return False
        return self.pattern is None or re.fullmatch(self.pattern, value) is not None


class ValidateSpec(BaseModel):
    """The check behind "Check": a GET that costs nothing (R7), relative to the preset's base_url or absolute."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["GET"] = "GET"
    path: Optional[str] = None
    url: Optional[str] = None
    account_field: Optional[str] = None   # dotted path into the JSON answer that names the account (data.label)
    account_header: Optional[str] = None  # response header that names it (anthropic-workspace-id)
    models_field: Optional[str] = "data"  # dotted path to the list that is counted as models
    public: bool = False                  # answers without a key: it proves the endpoint, not the key

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (not value.startswith("/") or value.startswith("//") or ".." in value
                                  or any(c in value for c in "?#\\ ") or not value.isprintable()):
            raise ValueError(f"validate.path must be a plain path starting with '/': {value!r}")
        return value

    @field_validator("url")
    @classmethod
    def _absolute_url(cls, value: Optional[str]) -> Optional[str]:
        return _https_url(value)

    @field_validator("account_field", "models_field")
    @classmethod
    def _dotted(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _DOTTED.match(value):
            raise ValueError(f"expected a dotted path such as data.label: {value!r}")
        return value

    @field_validator("account_header")
    @classmethod
    def _header(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _HEADER_NAME.match(value):
            raise ValueError(f"not a header name: {value!r}")
        return value

    @model_validator(mode="after")
    def _one_target(self):
        if (self.path is None) == (self.url is None):
            raise ValueError("validate needs exactly one of path and url")
        return self


class CliSpec(BaseModel):
    """An official vendor CLI that owns the session (`ant auth login`); we only start it, visibly."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    install: Optional[str] = None
    login: List[str] = Field(default_factory=list)
    logout: List[str] = Field(default_factory=list)

    @field_validator("install")
    @classmethod
    def _install_url(cls, value: Optional[str]) -> Optional[str]:
        return _https_url(value)


class OAuthSpec(BaseModel):
    """Endpoints and parameters of a browser or device sign-in (flows come in later packages)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorize_url: Optional[str] = None
    exchange_url: Optional[str] = None
    device_url: Optional[str] = None
    revoke_url: Optional[str] = None
    userinfo_url: Optional[str] = None
    client_id: Optional[str] = None     # a public client id we own (never another tool's); None: no registration
    scopes: List[str] = Field(default_factory=list)
    redirect_path: str = "/callback"
    result: Literal["api_key", "tokens"] = "tokens"
    headless: Optional[Literal["paste_code", "device_code"]] = None

    @field_validator("authorize_url", "exchange_url", "device_url", "revoke_url", "userinfo_url")
    @classmethod
    def _urls(cls, value: Optional[str]) -> Optional[str]:
        return _https_url(value)


TYPE_LABELS = {
    "none": "No sign-in", "bearer_optional": "API key (optional)", "api_key": "API key",
    "cli_session": "Command-line sign-in", "oauth_pkce_loopback": "Sign in with the browser",
    "oauth_other": "Sign in with a code", "oauth_device_code": "Sign in on another device",
    "cloud_credential_chain": "Cloud credentials", "short_lived_token": "Short-lived token",
}


class AuthMethodSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str = Field(pattern=_METHOD_ID)
    type: AuthType
    label: str = Field(default="", validate_default=True)
    key_page: Optional[str] = None
    where: Optional[str] = None          # local engines: where the key is found in the engine's own settings
    env: List[str] = Field(default_factory=list)
    reject_prefixes: List[str] = Field(default_factory=list)
    fields: List[FieldSpec] = Field(default_factory=list)
    validation: Optional[ValidateSpec] = Field(default=None, alias="validate")
    opt_in: bool = False
    cli: Optional[CliSpec] = None
    oauth: Optional[OAuthSpec] = None
    sources: List[str] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _default_label(cls, value: str, info: ValidationInfo) -> str:
        return value or TYPE_LABELS.get(info.data.get("type"), "")

    @field_validator("key_page")
    @classmethod
    def _key_page(cls, value: Optional[str]) -> Optional[str]:
        return _https_url(value)

    @field_validator("env")
    @classmethod
    def _env_names(cls, value: List[str]) -> List[str]:
        bad = [name for name in value if not _ENV_NAME.match(name)]
        if bad:
            raise ValueError(f"not an environment variable name: {', '.join(map(repr, bad))}")
        return value

    @model_validator(mode="after")
    def _consistent(self):
        ids = [f.id for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError(f"method '{self.id}': duplicate field ids")
        if self.type in OAUTH_TYPES and self.oauth is None:
            raise ValueError(f"method '{self.id}' of type {self.type} needs an oauth block")
        if (self.env or self.reject_prefixes) and self.type not in KEY_TYPES:
            raise ValueError(f"method '{self.id}': env and reject_prefixes belong to key methods")
        return self

    @property
    def needs_key(self) -> bool:
        """A stored or environment key is required (bearer_optional works without one)."""
        return self.type == "api_key"

    def binds_origin(self, preset: Any) -> bool:
        """Whether the key belongs to one endpoint (R6): keys of presets whose base URL the user can change and
        keys of local engines. Changing the base URL then never carries the key over."""
        return self.type in KEY_TYPES and (self.type == "bearer_optional" or bool(preset.base_url_configurable))

    def rejected(self, key: Optional[str]) -> bool:
        """True for a key of a kind this method refuses (an Admin API key), also with surrounding whitespace."""
        return bool(key) and any(key.strip().startswith(p) for p in self.reject_prefixes if p)

    def field(self, field_id: str) -> Optional[FieldSpec]:
        return next((f for f in self.fields if f.id == field_id), None)


WORKSPACE_FIELD = FieldSpec(id="workspace_id", label="Workspace ID (only for keys valid in all workspaces)",
                            pattern=r"^wrkspc_[A-Za-z0-9]+$", header="anthropic-workspace-id",
                            setting="ai_anthropic_workspace_id", invalid_code="anthropic.workspace_invalid")


def default_spec(name: str, *, adapter: Optional[str] = None, base_url: Optional[str] = None,
                 reject_prefixes: Optional[List[str]] = None) -> AuthMethodSpec:
    """The spec behind a plain method name of the v1 form."""
    name = LEGACY_AUTH.get(name, name)
    if name == "none":
        return AuthMethodSpec(id="none", type="none")
    if name == "ant_profile":
        return AuthMethodSpec(id="ant_profile", type="cli_session", label="Anthropic CLI profile (advanced)",
                              opt_in=True, cli=CliSpec(login=["ant", "auth", "login"]))
    if name == "api_key":
        claude_api = adapter == "anthropic" and (urlparse(base_url or "").hostname or "").lower() == CLAUDE_API_HOST
        refused = list(reject_prefixes or ([CLAUDE_ADMIN_PREFIX] if claude_api else []))
        return AuthMethodSpec(id="api_key", type="api_key", reject_prefixes=refused,
                              fields=[WORKSPACE_FIELD] if claude_api else [])
    raise ValueError(f"unknown sign-in method '{name}' (schema v1 knows api_key, ant_profile and none; "
                     "use the mapping form for other types)")


def parse_methods(data: Dict[str, Any]) -> List[AuthMethodSpec]:
    """`auth` of a raw preset (both schema versions), with the v1 fields opt_in_auth and reject_key_prefixes
    folded into the specs. Raises ValueError for unknown or duplicate methods."""
    raw = data.get("auth")
    if raw is None:
        raw = ["api_key"]
    if not isinstance(raw, list):
        raise ValueError("auth must be a list of methods")
    opt_in = [LEGACY_AUTH.get(m, m) for m in (data.get("opt_in_auth") or [])]
    prefixes = list(data.get("reject_key_prefixes") or [])
    methods: List[AuthMethodSpec] = []
    for entry in raw:
        if isinstance(entry, str):
            spec = default_spec(entry, adapter=data.get("adapter"), base_url=data.get("base_url"),
                                reject_prefixes=prefixes)
        elif isinstance(entry, AuthMethodSpec):
            spec = entry
        elif isinstance(entry, dict):
            spec = AuthMethodSpec.model_validate(entry)
        else:
            raise ValueError(f"auth entries are method names or mappings, not {type(entry).__name__}")
        if spec.type == "api_key" and prefixes and not spec.reject_prefixes:
            spec = spec.model_copy(update={"reject_prefixes": prefixes})
        if (spec.id in opt_in or spec.id in ALWAYS_OPT_IN) and not spec.opt_in:
            spec = spec.model_copy(update={"opt_in": True})
        methods.append(spec)
    ids = [m.id for m in methods]
    if len(ids) != len(set(ids)):
        raise ValueError(f"auth lists a method twice: {', '.join(ids)}")
    unknown = [m for m in opt_in if m not in ids]
    if unknown:
        raise ValueError(f"opt_in_auth lists methods that are not in auth: {', '.join(unknown)}")
    return methods
