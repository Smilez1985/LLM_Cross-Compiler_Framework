#!/usr/bin/env python3
"""Provider presets (configs/llm_providers.yml, schema v2; v1 files still load) as typed data. The sign-in methods
of a preset are AuthMethodSpecs (orchestrator/llm/auth/spec.py)."""

import re
from pathlib import Path
from typing import Any, Dict, List, Literal, NamedTuple, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.llm.auth.spec import ALWAYS_OPT_IN, LEGACY_AUTH, AuthMethodSpec, parse_methods
from orchestrator.llm.types import Effort
from orchestrator.utils.logging import get_logger

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "configs" / "llm_providers.yml"
CATALOG_FILENAME = "llm_providers.yml"

SCHEMA_VERSIONS = (1, 2)
__all__ = ["ALWAYS_OPT_IN", "LEGACY_AUTH", "Catalog", "OfflineRefusal", "ProviderPreset", "is_local_url",
           "load_catalog"]

# Providers that no longer exist. Selecting one, also by an old alias or spelling ("Claude Code"), raises
# NotConfigured with this text. Keys are normalized (see _removed_key).
CLAUDE_CODE_REMOVED = (
    "The Claude Code connection was removed: Anthropic does not allow other apps to use Claude subscription "
    "limits without approval. Choose one of these instead: (1) a Claude API key (Max and Team plans include "
    "monthly API credits for a linked Console organization), (2) Claude on Bedrock, Vertex or Foundry, (3) use "
    "the framework from Claude Code itself: `llm-cli mcp config`.")
REMOVED_PROVIDERS = {"claudecode": CLAUDE_CODE_REMOVED}
# Adapters that no longer exist: a user catalog preset that still uses one is skipped, not fatal.
REMOVED_ADAPTERS = {"claude_code": CLAUDE_CODE_REMOVED}


class OfflineRefusal(NamedTuple):
    """Why a provider cannot be used in offline mode: the plain text, the next steps (the person's opt-out of the
    locality rule when it would help) and the locality verdict behind it (None for a cloud provider)."""
    text: str
    steps: Tuple[str, ...] = ()
    locality: Any = None


def _removed_key(name: Optional[str]) -> str:
    return "".join(re.findall(r"[a-z0-9]+", str(name or "").lower()))


def _removed_keys(name: Optional[str]) -> List[str]:
    """The normalized name and its leading word groups: "Claude Code (Pro/Max)" -> claudecodepromax, claudecodepro,
    claudecode. Single leading words are left out ("Claude" alone is the Claude API)."""
    words = re.findall(r"[a-z0-9]+", str(name or "").lower())
    return ["".join(words[:k]) for k in range(len(words), 1, -1)] or ["".join(words)]


class ProviderPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    label: str
    adapter: Literal["anthropic", "openai_responses", "openai_compat"]
    group: Optional[str] = None          # GUI grouping (the vendor)
    category: Literal["cloud_api", "cloud_platform", "aggregator", "local_engine", "mcp_reverse"] = "cloud_api"
    base_url: Optional[str] = None
    base_url_configurable: bool = False  # ai_base_url applies; cloud presets keep their fixed endpoint
    runtime_endpoint: bool = False       # endpoint is supplied by a running local server
    location: Literal["cloud", "local", "auto"] = "cloud"
    # Sign-in methods in preferred order (`auth` in the file; the v1 name list expands to default specs).
    methods: List[AuthMethodSpec] = Field(default_factory=list, alias="auth")
    secret: Optional[str] = None         # earlier name of the API key in the vault (kept as cred/<id>/api_key)
    models: Dict[str, str] = Field(default_factory=dict)
    suggested_models: List[str] = Field(default_factory=list)
    efforts: Dict[str, Effort] = Field(default_factory=dict)
    effort_map: Dict[str, str] = Field(default_factory=dict)
    reasoning: Literal["none", "reasoning_effort", "reasoning_object"] = "none"
    server_fallback_models: List[str] = Field(default_factory=list)
    no_effort_models: List[str] = Field(default_factory=list)
    no_tools_with_response_format: bool = False
    headers: Dict[str, str] = Field(default_factory=dict)
    live_models: bool = True
    docs_url: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _methods(cls, data):
        # Both schema versions: v1 lists method names (with opt_in_auth and reject_key_prefixes next to them),
        # v2 lists method specs; "oauth" in a v1 list is the earlier name of the `ant auth login` profile.
        if isinstance(data, dict):
            data = dict(data)
            key = "auth" if "auth" in data or "methods" not in data else "methods"
            methods = parse_methods({**data, "auth": data.get(key)})
            for name in ("auth", "methods", "opt_in_auth", "reject_key_prefixes"):
                data.pop(name, None)
            data["auth"] = methods
            if "category" not in data and data.get("location") == "local":
                data["category"] = "local_engine"
        return data

    @model_validator(mode="after")
    def _key_name(self):
        # The AI settings store a key under `secret`; a schema-2 preset may leave it out.
        if self.secret is None and not self.runtime_endpoint:
            first = next((m for m in self.methods if m.type == "api_key"), None)
            if first is not None and re.match(r"^[a-z0-9][a-z0-9_.-]{0,63}$", self.id):
                self.secret = f"cred/{self.id}/{first.id}"
        return self

    @property
    def auth(self) -> List[str]:
        """Method ids in preferred order."""
        return [m.id for m in self.methods]

    @property
    def opt_in_auth(self) -> List[str]:
        """Methods used only when ai_auth_<id> names them."""
        return [m.id for m in self.methods if m.opt_in]

    @property
    def reject_key_prefixes(self) -> List[str]:
        return [p for m in self.methods for p in m.reject_prefixes]

    @property
    def env_names(self) -> List[str]:
        """Every environment variable a method of this preset reads."""
        return [name for m in self.methods for name in m.env]

    def method(self, method_id: Optional[str]) -> Optional[AuthMethodSpec]:
        """The method with this id; an earlier name ("oauth" for ant_profile) only when no method has it."""
        found = next((m for m in self.methods if m.id == method_id), None)
        if found is None and method_id in LEGACY_AUTH:
            found = next((m for m in self.methods if m.id == LEGACY_AUTH[method_id]), None)
        return found

    def rejected_key(self, key: Optional[str]) -> bool:
        """True for a key of a kind this provider refuses for chat (e.g. an Admin API key), also when it was
        stored with surrounding whitespace (a pasted key)."""
        return any(m.rejected(key) for m in self.methods)

    def model_for(self, task: str) -> Optional[str]:
        return self.models.get(task) or self.models.get("default")

    def is_local(self, base_url: Optional[str] = None) -> bool:
        """Where the server stands, the preset's location label (the privacy filter follows it): its `location`, or for
        "auto" (a server whose address the person sets) the network-locality rule for the address WITHOUT the person's
        opt-outs (utils/locality.check_location, DECISIONS round 26 Q3): a public host the person counts as local for
        offline mode keeps the label "cloud" and the privacy filter, online and offline. Whether offline mode may reach
        the server is offline_refusal's question."""
        if self.location != "auto":
            return self.location == "local"
        return is_local_url(base_url or self.base_url)

    def located_by_address(self, base_url: Optional[str] = None) -> bool:
        """True when the "local" label comes from the server's address (location "auto" and the address counts as
        local by check_location): a connection to it then goes only to the checked local addresses (LLMClient), so
        the label holds for the connection too."""
        return self.location == "auto" and self.is_local(base_url)

    def offline_refusal(self, base_url: Optional[str] = None, config: Any = None) -> Optional[OfflineRefusal]:
        """Why this preset cannot be used in offline mode with `base_url` (None when it can): a cloud provider, or a
        server address that is not on this computer or in the person's own network by the locality rule (every
        address the name resolves to; a Tailscale address only with the own Headscale; the person's opt-outs in
        `config`). Ollama and a custom server are judged by their address, the managed local model by its loopback
        address; a server without an address is reached nowhere."""
        url = base_url or self.base_url
        if self.location == "cloud" or (self.location == "auto" and not url):
            return OfflineRefusal(f"Offline mode is on: {self.label} is a cloud provider. Choose a local model.")
        if not url:
            return None
        from orchestrator.utils.locality import check_reach
        verdict = check_reach(url, config)   # may offline mode reach it: the person's opt-outs count (round 26 Q3)
        if verdict.local:
            return None
        return OfflineRefusal(verdict.refusal(f"the server address of {self.label}"), verdict.steps, verdict)

    def map_effort(self, effort: Optional[str]) -> Optional[str]:
        if effort is None:
            return None
        return self.effort_map.get(effort, effort)


class Catalog(BaseModel):
    providers: Dict[str, ProviderPreset]
    aliases: Dict[str, str] = Field(default_factory=dict)
    # Removed providers (normalized name -> what to use instead), plus user presets skipped while loading.
    removed: Dict[str, str] = Field(default_factory=lambda: dict(REMOVED_PROVIDERS))

    def _lookup(self, cand: str) -> Optional[str]:
        if cand in self.providers:
            return cand
        lowered = cand.lower()
        if lowered in self.providers:
            return lowered
        for alias, target in self.aliases.items():
            if alias.lower() == lowered:
                return target
        return None

    def _removed(self, name: Optional[str]) -> Optional[str]:
        for key in _removed_keys(name):
            if key in self.removed:
                return self.removed[key]
        return None

    def resolve_id(self, name: Optional[str]) -> Optional[str]:
        """Accepts ids, labels' first word and the old ai_provider values ("OpenAI", "LocalAI (...)")."""
        if not name:
            return None
        raw = str(name).strip()
        found = self._lookup(raw)
        if found or self._removed(raw):
            return found  # a removed provider such as "Claude Code" must not turn into its first word "Claude"
        return self._lookup(raw.split()[0]) if raw.split() else None

    def removed_message(self, name: Optional[str]) -> Optional[str]:
        """What to use instead when `name` is a provider that was removed (None otherwise)."""
        pid = self.resolve_id(name)
        if not name or pid in self.providers:
            return None
        raw = str(name).strip()
        for cand in (pid, raw, raw.split()[0] if raw.split() else ""):
            message = self._removed(cand) if cand else None
            if message:
                return message
        return None

    def get(self, name: Optional[str]) -> Optional[ProviderPreset]:
        pid = self.resolve_id(name)
        return self.providers.get(pid) if pid else None

    def ids(self) -> List[str]:
        return list(self.providers)


def is_local_url(url: Optional[str]) -> bool:
    """Where a server stands: whether its address counts as local by the one network-locality rule WITHOUT the
    person's opt-outs (utils/locality.check_location; DECISIONS round 24 L-NET, round 26 Q3): a name by the addresses
    it resolves to (a .local or .lan name only when it has local ones; a name with local and public addresses by its
    local ones, which are the only ones a connection then uses), loopback, private and link-local addresses, a
    Tailscale address only with the person's own Headscale; anything else (or nothing) counts as cloud. The opt-outs
    count only for offline mode's reach (ProviderPreset.offline_refusal)."""
    if not url:
        return False
    from orchestrator.utils.locality import check_location
    return check_location(url).local


def _read(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return data


def _check_version(data: dict, path: Any) -> dict:
    if data.get("version", 1) not in SCHEMA_VERSIONS:
        raise ValueError(f"{path}: schema version {data.get('version')!r} is not supported "
                         f"(known: {', '.join(map(str, SCHEMA_VERSIONS))})")
    return data


def load_catalog(path: Optional[Path] = None, user_dir: Optional[Path] = None) -> Catalog:
    """Loads the bundled presets; a llm_providers.yml in `user_dir` adds or replaces presets by id."""
    data = _check_version(_read(Path(path) if path else DEFAULT_CATALOG), path or DEFAULT_CATALOG)
    providers = dict(data.get("providers") or {})
    aliases = dict(data.get("aliases") or {})
    if user_dir is not None:
        override = Path(user_dir) / CATALOG_FILENAME
        if override.is_file() and override.resolve() != (Path(path) if path else DEFAULT_CATALOG).resolve():
            extra = _check_version(_read(override), override)  # a newer framework's file is not guessed at
            providers.update(extra.get("providers") or {})
            aliases.update(extra.get("aliases") or {})
    presets: Dict[str, ProviderPreset] = {}
    removed = dict(REMOVED_PROVIDERS)
    for pid, spec in providers.items():
        adapter = spec.get("adapter") if isinstance(spec, dict) else None
        if isinstance(adapter, str) and adapter in REMOVED_ADAPTERS:
            # Left over in a user catalog from an earlier version: skipped, so the other presets still work.
            get_logger("LLMCatalog").warning(f"Provider preset '{pid}' skipped: the adapter '{adapter}' was removed.")
            removed[_removed_key(pid)] = REMOVED_ADAPTERS[adapter]
            continue
        presets[pid] = ProviderPreset(id=pid, **(spec or {}))
    return Catalog(providers=presets, aliases=aliases, removed=removed)
