#!/usr/bin/env python3
"""
One change of the AI settings, for every channel that changes them (consolidation c3-ai-settings).

The rules lived in the AI settings dialog (gui/dialogs.py AIConfigurationDialog._save_config); the sign-in and the CLI's
`ditto login` repeated two of them, and MCP update_settings had its direction rule in a tool module. AISettingsChange
wraps those existing rules in one Qt-free place:

    change = AISettingsChange(preset, catalog, model=..., api_key=..., base_url=..., fallback_chain=[...])
    problems = change.problems(config, secrets)       # every refusal, in the dialog's order, with its title
    url = change.plain_http_url()                     # a key would travel unencrypted: the channel asks the person
    applied = change.apply(config, secrets, framework)  # the key stored for its address, the settings written and
                                                      # saved, the knowledge base started, Ditto reloaded

Each channel keeps only its own prompts: the dialog's message boxes and its plain-http question, the CLI's confirm,
and MCP's refusal of anything that loosens what an AI may do (direction_problem). AISettingsChange.current(config,
catalog) and with_setting(key, value) serve a change of one setting; problems_of_settings(config, secrets, values)
runs the same rules for it (MCP update_settings for offline_mode and enable_rag_knowledge, the coming `llmcf config
set`, Q6).

Moved here from where they were first written, their old import paths kept: check_base_url and plain_http_to_remote
(llm/auth/signin.py), direction_problem with TIGHTEN_ONLY and memory_bytes (tools/tools_status.py), and the dialog's
SettingsOverlay and fallback parsing (gui/dialogs.py). Only the standard library is imported here at load time.
"""

import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse, urlsplit

# --- the settings an AI may only tighten (MCP update_settings, the dialog's GUI-tool fields) --------------------------

TIGHTEN_ONLY: Dict[str, Any] = {
    "offline_mode": True, "enable_telemetry": False, "enable_security_scan": True, "build_sandbox": True,
    "enable_rag_knowledge": False, "crawler_max_pages": "lower", "crawler_max_depth": "lower",
    "crawler_rate_limit": "higher", "build_memory_limit": "lower", "build_pids_limit": "lower",
    "crawler_download_pdfs": False}
MIN_MEMORY_LIMIT = 6 * 1024 ** 2  # Docker refuses a smaller container memory limit


def memory_bytes(value: Any) -> Optional[int]:
    """A Docker memory limit ('24g', '512m', '1024') in bytes; None for no limit ('')."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([bkmg]?)b?", text)
    if not match:
        raise ValueError(f"'{value}' is not a memory size (e.g. 16g or 512m)")
    factor = {"": 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[match.group(2)]
    value = int(float(match.group(1)) * factor)
    if value == 0:
        return None  # Docker reads a memory limit of 0 as no limit at all
    if value < MIN_MEMORY_LIMIT:
        raise ValueError(f"'{value}' bytes is below the smallest memory limit Docker accepts (6m)")
    return value


def direction_problem(key: str, old: Any, new: Any) -> Optional[str]:
    """Why a change of a tighten-only setting would widen what an AI may do (None when it tightens or keeps it)."""
    if key not in TIGHTEN_ONLY:
        return None
    rule = TIGHTEN_ONLY[key]
    if isinstance(rule, bool):
        return None if new is rule else (f"{key} may only be set to {str(rule).lower()} over MCP; the other "
                                         "direction widens what an AI may do and stays with the person")
    if key == "build_memory_limit":
        try:
            old_b = memory_bytes(old)
        except ValueError:
            old_b = None  # an unreadable current value limits nothing a build can rely on
        new_b = memory_bytes(new)
        if old_b is None or (new_b is not None and new_b <= old_b):
            return None
        return "build_memory_limit may only be lowered over MCP (a larger limit or none widens what builds may use)"
    try:
        old_n = float(old) if old is not None else None
    except (TypeError, ValueError):
        old_n = None
    if old_n is None:
        return None
    if rule == "lower" and float(new) > old_n:
        return f"{key} may only be lowered over MCP (now {old}); raising it stays with the person"
    if rule == "higher" and float(new) < old_n:
        return f"{key} may only be raised over MCP (now {old}); lowering it stays with the person"
    return None


# --- server addresses -------------------------------------------------------------------------------------------

def check_base_url(url: str) -> str:
    """A server address typed by the user: http(s), a host, no user info, nothing a parser could read two ways.
    The address is never part of the error (it may hold a password)."""
    from orchestrator.llm.auth.store import display_origin
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


# --- reading the person's settings ------------------------------------------------------------------------------

class SettingsOverlay:
    """The saved settings with unsaved choices on top (the dialog's live model list reads through it). Like LLMClient,
    it counts only values the user set: schema defaults belong to the default provider."""

    config_values = None  # makes LLMClient read every key through get()

    def __init__(self, base: Any, overrides: Dict[str, Any]):
        self._base = base
        self._overrides = overrides

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        values = getattr(self._base, "config_values", None)
        if isinstance(values, dict):
            entry = values.get(key)
            if entry is None or getattr(getattr(entry, "source", None), "source_type", "") == "default":
                return default
            return entry.value
        return self._base.get(key, default) if self._base is not None and hasattr(self._base, "get") else default

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._base, name)


def user_value(config: Any, key: str, default: Any = None) -> Any:
    """A value the user set; schema defaults do not count (the default model belongs to OpenAI)."""
    return SettingsOverlay(config, {}).get(key, default)


def parse_fallback(text: str) -> List[str]:
    """A fallback chain as typed: provider or provider:model, separated by commas."""
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def removed_message(catalog: Any, entries: Sequence[str]) -> Optional[str]:
    """The migration text for the first removed provider among `entries` (provider or provider:model)."""
    for entry in entries:
        removed = catalog.removed_message(str(entry).partition(":")[0]) if catalog is not None else None
        if removed:
            return removed
    return None


def key_method(preset: Any):
    """The provider's method that takes a key (api_key or bearer_optional), None without one."""
    from orchestrator.llm.auth.spec import KEY_TYPES
    return next((m for m in preset.methods if m.type in KEY_TYPES), None) if preset is not None else None


def takes_workspace(preset: Any) -> bool:
    from orchestrator.llm.providers.anthropic import takes_workspace_id
    return preset is not None and takes_workspace_id(preset)


# --- the change ------------------------------------------------------------------------------------------------

# The AI settings a change writes, by setting name (a change of one of them goes through AISettingsChange).
AI_SETTINGS = {"ai_provider": "provider", "ai_model": "model", "ai_base_url": "base_url",
               "ai_anthropic_workspace_id": "workspace_id", "ai_effort": "effort",
               "ai_security_level": "security_level", "ai_fallback_chain": "fallback_chain",
               "offline_mode": "offline_mode", "enable_rag_knowledge": "enable_rag"}
BASE_URL_SCHEME = "The Base URL must start with http:// or https://."


@dataclass(frozen=True)
class SettingsProblem:
    """A refusal: its code, the title a window shows it under and the text."""
    code: str
    title: str
    message: str


class SettingsChangeError(ValueError):
    """apply() could not store the key; nothing was written to the settings."""

    def __init__(self, message: str, code: str = "key_store", title: str = "Error"):
        super().__init__(message)
        self.code, self.title = code, title


@dataclass
class AppliedChange:
    """What apply() did: the settings as the dialog reports them (never a key), and whether config.yml was written
    (None when the settings cannot be saved at all)."""
    result: Dict[str, Any]
    saved: Optional[bool]


@dataclass
class AISettingsChange:
    """A change of the AI settings: the provider (a catalog preset) and the values a window or command chose.
    `base_url` counts only for a provider whose address is configurable, `workspace_id` only for the Claude API, so a
    leftover value never reaches another provider (it would receive that provider's key)."""
    preset: Any
    catalog: Any = None
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    workspace_id: str = ""
    effort: str = ""
    security_level: str = "STRICT"
    fallback_chain: Sequence[str] = field(default_factory=list)
    offline_mode: bool = False
    enable_rag: bool = False

    # --- values as they are used ---
    @property
    def address(self) -> str:
        return str(self.base_url or "").strip() if getattr(self.preset, "base_url_configurable", False) else ""

    @property
    def workspace(self) -> str:
        return str(self.workspace_id or "").strip() if takes_workspace(self.preset) else ""

    @property
    def key(self) -> str:
        return str(self.api_key or "").strip()

    @property
    def key_url(self) -> Optional[str]:
        """The address the key is stored for and sent to."""
        return self.address or self.preset.base_url

    def key_required(self, config: Any) -> bool:
        """Presets that also work without a key (local servers) do not need one, nor does a provider whose other
        sign-in method the user chose (ai_auth_<provider>); opt-in methods never stand in for the key on their own."""
        preset = self.preset
        if not preset.secret:
            return False
        chosen = user_value(config, f"ai_auth_{preset.id}")
        if chosen and chosen != "api_key":
            return False
        return all(method == "api_key" or method in preset.opt_in_auth for method in preset.auth)

    def stored_key(self, secrets: Any) -> Optional[str]:
        """Where the provider's key comes from ("vault", "env:<VARIABLE>"), None without one. A key bound to a
        server address counts only for this change's address."""
        from orchestrator.llm.auth.store import CredentialStore
        preset, spec = self.preset, key_method(self.preset)
        if preset is None or not preset.secret or not secrets or spec is None:
            return None
        return CredentialStore(secrets).key_source(preset, spec, self.key_url)

    def removed_message(self, entries: Sequence[str]) -> Optional[str]:
        return removed_message(self.catalog, entries)

    # --- the rules ---
    def problems(self, config: Any, secrets: Any) -> List[SettingsProblem]:
        """Every refusal of this change, in the order the dialog checks them (it shows the first)."""
        from orchestrator.llm.errors import ANTHROPIC_WORKSPACE_INVALID, rejected_key_error
        from orchestrator.llm.providers.anthropic import WORKSPACE_ID
        preset, found = self.preset, []
        chain = list(self.fallback_chain or [])
        unknown = [e for e in chain if self.catalog is not None and self.catalog.get(e.partition(":")[0]) is None]
        if unknown:
            removed = self.removed_message(unknown)
            found.append(SettingsProblem(
                "fallback_unknown", "Invalid Fallback Chain",
                f"Unknown provider in the fallback chain: {', '.join(unknown)}.\n"
                f"Known providers: {', '.join(self.catalog.ids())}." + (f"\n\n{removed}" if removed else "")))
        # The LLM layer's own rule (client.resolve): a provider whose location depends on its address (a custom
        # server) counts as local only on this computer or the local network (no address at all: base_url_missing).
        if self.offline_mode and preset.location == "cloud":
            found.append(SettingsProblem(
                "offline_cloud", "Offline Mode",
                f"{preset.label} is a cloud provider. In offline mode choose a local provider (Ollama, the managed "
                "local model, or a custom server on your network)."))
        elif self.offline_mode and (self.address or preset.base_url) and not preset.is_local(self.address or None):
            found.append(SettingsProblem(
                "offline_cloud", "Offline Mode",
                f"The server address of {preset.label} is not on this computer or your local network. In offline "
                "mode choose a local provider (Ollama, the managed local model, or a custom server on your "
                "network)."))
        if self.key_required(config) and not self.key and not self.stored_key(secrets):
            found.append(SettingsProblem("key_missing", "Missing API Key",
                                         "Please provide an API key for this provider."))
        if preset.rejected_key(self.key):
            found.append(SettingsProblem("key_rejected", "Unsuitable API Key", str(rejected_key_error(preset))))
        if self.workspace and not WORKSPACE_ID.match(self.workspace):
            found.append(SettingsProblem("workspace_invalid", "Invalid Workspace ID", ANTHROPIC_WORKSPACE_INVALID))
        address = self.address
        if preset.base_url_configurable and not preset.base_url and not address:
            found.append(SettingsProblem("base_url_missing", "Missing Base URL",
                                         "A custom endpoint requires a Base URL."))
        if address:
            parsed = urlparse(address)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                found.append(SettingsProblem("base_url_invalid", "Invalid Base URL", BASE_URL_SCHEME))
            else:
                try:
                    check_base_url(address)  # the sign-in's rule: no user name or password in the address
                except ValueError as e:
                    found.append(SettingsProblem("base_url_invalid", "Invalid Base URL", str(e)))
        if not str(self.model or "").strip() and not preset.runtime_endpoint:
            found.append(SettingsProblem("model_missing", "Missing Model", "Please select or enter a model."))
        return found

    def plain_http_url(self) -> Optional[str]:
        """The address a typed key would travel to unencrypted (plain http:// to another computer); the channel asks
        the person before apply() (the dialog's question, the CLI's confirm). None when nothing needs asking."""
        url = self.key_url
        return url if self.key and self.preset.secret and plain_http_to_remote(url) else None

    # --- applying it ---
    def apply(self, config: Any, secrets: Any, framework: Any = None) -> AppliedChange:
        """Stores a typed key for its address, writes the settings and saves them, starts the knowledge base when it
        is switched on and reloads Ditto. Check problems() and ask about plain_http_url() first. Raises
        SettingsChangeError when the key cannot be stored (then nothing else is written)."""
        from orchestrator.llm.auth.store import CredentialStore
        from orchestrator.llm.errors import LLMError
        preset, spec, key, address = self.preset, key_method(self.preset), self.key, self.address
        if key and preset.secret and spec is not None:
            # A key of a server whose address can be changed is stored for that address only.
            try:
                CredentialStore(secrets).save_api_key(preset, spec, key, base_url=address or preset.base_url)
            except (LLMError, ValueError) as e:
                raise SettingsChangeError(f"Failed to store the API key: {e}") from None
        elif key and preset.secret:  # a user preset with a key name but no key method
            if not secrets or not secrets.set_secret(preset.secret, key):
                raise SettingsChangeError("Failed to store the API key in the encrypted credential vault.")
        effort, security = self.effort or "", self.security_level or "STRICT"
        fallback = list(self.fallback_chain or [])
        saved = None
        if config is not None and hasattr(config, "set"):
            config.set("ai_provider", preset.id)
            config.set("ai_model", self.model)
            config.set("ai_base_url", address)
            if user_value(config, f"ai_base_url_{preset.id}"):
                config.set(f"ai_base_url_{preset.id}", address)  # set by `llm-cli ditto login`; it wins over ai_base_url
            chosen = user_value(config, f"ai_auth_{preset.id}")
            if key and spec is not None and chosen and getattr(preset.method(chosen), "id", None) != spec.id:
                # A key typed here is meant to be used: the method remembered by a sign-in (an `ant` profile, no
                # key) would otherwise keep it unused without a word.
                config.set(f"ai_auth_{preset.id}", spec.id)
            if takes_workspace(preset):
                config.set("ai_anthropic_workspace_id", self.workspace)
            config.set("ai_effort", effort)
            config.set("ai_security_level", security)
            config.set("ai_fallback_chain", fallback)
            config.set("offline_mode", bool(self.offline_mode))
            config.set("enable_rag_knowledge", bool(self.enable_rag))
            if hasattr(config, "save_user_config"):
                saved = bool(config.save_user_config())
        if self.enable_rag and hasattr(framework, "ensure_rag_manager"):
            framework.ensure_rag_manager()
        ditto = getattr(framework, "ditto_manager", None)
        if ditto is not None and hasattr(ditto, "reload"):
            ditto.reload()  # a new key or catalog takes effect on the next question
        return AppliedChange(result={
            "provider": preset.id, "provider_label": preset.label, "model": self.model, "base_url": address or None,
            "effort": effort or None, "security_level": security, "fallback_chain": fallback,
            "offline_mode": bool(self.offline_mode), "enable_rag": bool(self.enable_rag)}, saved=saved)

    # --- a change of one setting (the coming `llmcf config set`) ---
    @classmethod
    def current(cls, config: Any, catalog: Any, provider: Optional[str] = None) -> "AISettingsChange":
        """The saved AI settings as a change (what the dialog shows when it opens), for `provider` (a preset id) when
        given: its own address and workspace, the saved model only when it is the saved provider's."""
        get = getattr(config, "get", None)
        saved_id = catalog.resolve_id(user_value(config, "ai_provider"))
        pid = provider or catalog.resolve_id((get("ai_provider", "OpenAI") if callable(get) else None) or "OpenAI") \
            or "openai"
        preset = catalog.providers.get(pid)
        if preset is None:
            raise ValueError(f"Unknown AI provider '{pid}'.")
        saved = saved_id == preset.id
        own = user_value(config, f"ai_base_url_{preset.id}") if preset.base_url_configurable else None
        chain = user_value(config, "ai_fallback_chain") or []
        security = str((get("ai_security_level", "STRICT") if callable(get) else None) or "STRICT").upper()
        return cls(preset, catalog,
                   model=(user_value(config, "ai_model") if saved else None) or preset.model_for("chat") or "",
                   base_url=own or ((user_value(config, "ai_base_url") or "") if saved and preset.base_url_configurable
                                    else ""),
                   workspace_id=(user_value(config, "ai_anthropic_workspace_id") or "")
                   if takes_workspace(preset) else "",
                   effort=user_value(config, "ai_effort") or "", security_level=security,
                   fallback_chain=list(chain) if isinstance(chain, (list, tuple)) else parse_fallback(chain),
                   offline_mode=bool(get("offline_mode", False)) if callable(get) else False,
                   enable_rag=bool(get("enable_rag_knowledge", False)) if callable(get) else False)

    @classmethod
    def problems_of_settings(cls, config: Any, secrets: Any, values: Dict[str, Any],
                             catalog: Any = None) -> List[SettingsProblem]:
        """The refusals (problems(), the dialog's rules) that setting `values` ({setting name: new value}) one by one
        would bring: the saved AI settings (current) with each AI setting among `values` replaced (with_setting);
        names that are not AI settings are ignored. A problem the saved settings already have is not counted, so a
        change that does not touch it is not refused for it (the person fixes it in the AI settings dialog). For the
        channels that change single settings: MCP update_settings and the coming `llmcf config set`. ValueError for a
        value an AI setting cannot take."""
        names = [key for key in values if key in AI_SETTINGS]
        if not names:
            return []
        if catalog is None:
            from orchestrator.llm.catalog import load_catalog
            catalog = load_catalog(user_dir=getattr(config, "config_dir", None))
        saved = cls.current(config, catalog)
        changed = saved
        for key in names:
            changed = changed.with_setting(key, values[key], config)
        known = {problem.code for problem in saved.problems(config, secrets)}
        return [problem for problem in changed.problems(config, secrets) if problem.code not in known]

    def with_setting(self, key: str, value: Any, config: Any = None) -> "AISettingsChange":
        """This change with one AI setting replaced (a name of AI_SETTINGS). A new provider brings its own address,
        workspace and model from `config` (never the old provider's); KeyError for a setting that is not an AI
        setting, ValueError for a value it cannot take."""
        name = AI_SETTINGS.get(key)
        if name is None:
            raise KeyError(f"{key} is not an AI setting (AI settings: {', '.join(AI_SETTINGS)}).")
        if name == "provider":
            pid = self.catalog.resolve_id(str(value or "")) if self.catalog is not None else None
            if pid is None:
                removed = self.removed_message([str(value or "")])
                raise ValueError(removed or f"Unknown AI provider '{value}' (known: {', '.join(self.catalog.ids())}).")
            fresh = AISettingsChange.current(config, self.catalog, provider=pid)
            return replace(fresh, effort=self.effort, security_level=self.security_level,
                           fallback_chain=list(self.fallback_chain or []), offline_mode=self.offline_mode,
                           enable_rag=self.enable_rag)
        if name == "fallback_chain":
            value = list(value) if isinstance(value, (list, tuple)) else parse_fallback(value)
        elif name in ("offline_mode", "enable_rag"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} is true or false, not '{value}'.")
        elif name == "security_level":
            from orchestrator.llm.privacy import LEVELS
            value = str(value or "").strip().upper()
            if value not in LEVELS:
                raise ValueError(f"{key} is one of {', '.join(LEVELS)}, not '{value}'.")
        elif name == "effort":
            from orchestrator.llm.types import EFFORTS
            value = str(value or "").strip().lower()
            if value and value not in EFFORTS:
                raise ValueError(f"{key} is one of {', '.join(EFFORTS)} (or empty), not '{value}'.")
        else:
            value = str(value or "").strip()
        return replace(self, **{name: value})


__all__ = ["AI_SETTINGS", "AISettingsChange", "AppliedChange", "MIN_MEMORY_LIMIT", "SettingsChangeError",
           "SettingsOverlay", "SettingsProblem", "TIGHTEN_ONLY", "check_base_url", "direction_problem", "key_method",
           "memory_bytes", "parse_fallback", "plain_http_to_remote", "takes_workspace", "user_value"]
