#!/usr/bin/env python3
"""Provider presets (configs/llm_providers.yml) as typed data."""

import ipaddress
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.llm.types import Effort

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "configs" / "llm_providers.yml"
CATALOG_FILENAME = "llm_providers.yml"

AuthMethod = Literal["api_key", "ant_profile", "none"]

# Providers that no longer exist. Selecting one, also by an old alias, raises NotConfigured with this text.
CLAUDE_CODE_REMOVED = (
    "The Claude Code connection was removed: Anthropic does not allow other apps to use Claude subscription "
    "limits without approval. Choose one of these instead: (1) a Claude API key (Max and Team plans include "
    "monthly API credits for a linked Console organization), (2) Claude on Bedrock, Vertex or Foundry, (3) use "
    "the framework from Claude Code itself: `llm-cli mcp config`.")
REMOVED_PROVIDERS = {"claude_code": CLAUDE_CODE_REMOVED, "claudecode": CLAUDE_CODE_REMOVED}


class ProviderPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    adapter: Literal["anthropic", "openai_responses", "openai_compat"]
    base_url: Optional[str] = None
    base_url_configurable: bool = False  # ai_base_url applies; cloud presets keep their fixed endpoint
    runtime_endpoint: bool = False       # endpoint is supplied by a running local server
    location: Literal["cloud", "local", "auto"] = "cloud"
    auth: List[AuthMethod] = Field(default_factory=lambda: ["api_key"])
    opt_in_auth: List[AuthMethod] = Field(default_factory=list)  # used only when ai_auth_<id> names it
    reject_key_prefixes: List[str] = Field(default_factory=list)  # admin keys, refused before any request
    secret: Optional[str] = None
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

    @model_validator(mode="after")
    def _opt_in_methods_are_offered(self):
        unknown = [m for m in self.opt_in_auth if m not in self.auth]
        if unknown:
            raise ValueError(f"opt_in_auth lists methods that are not in auth: {', '.join(unknown)}")
        return self

    def rejected_key(self, key: Optional[str]) -> bool:
        """True for a key of a kind this provider refuses for chat (e.g. an Admin API key)."""
        return bool(key) and any(key.startswith(p) for p in self.reject_key_prefixes if p)

    def model_for(self, task: str) -> Optional[str]:
        return self.models.get(task) or self.models.get("default")

    def is_local(self, base_url: Optional[str] = None) -> bool:
        if self.location != "auto":
            return self.location == "local"
        return is_local_url(base_url or self.base_url)

    def map_effort(self, effort: Optional[str]) -> Optional[str]:
        if effort is None:
            return None
        return self.effort_map.get(effort, effort)


class Catalog(BaseModel):
    providers: Dict[str, ProviderPreset]
    aliases: Dict[str, str] = Field(default_factory=dict)

    def resolve_id(self, name: Optional[str]) -> Optional[str]:
        """Accepts ids, labels' first word and the old ai_provider values ("OpenAI", "LocalAI (...)")."""
        if not name:
            return None
        raw = str(name).strip()
        candidates = [raw, raw.split()[0] if raw.split() else raw]
        for cand in candidates:
            if cand in self.providers:
                return cand
            lowered = cand.lower()
            if lowered in self.providers:
                return lowered
            for alias, target in self.aliases.items():
                if alias.lower() == lowered:
                    return target
        return None

    def removed_message(self, name: Optional[str]) -> Optional[str]:
        """What to use instead when `name` is a provider that was removed (None otherwise)."""
        pid = self.resolve_id(name)
        if not name or pid in self.providers:
            return None
        raw = str(name).strip()
        for cand in (pid, raw, raw.split()[0] if raw.split() else ""):
            if cand and cand.lower() in REMOVED_PROVIDERS:
                return REMOVED_PROVIDERS[cand.lower()]
        return None

    def get(self, name: Optional[str]) -> Optional[ProviderPreset]:
        pid = self.resolve_id(name)
        return self.providers.get(pid) if pid else None

    def ids(self) -> List[str]:
        return list(self.providers)


def is_local_url(url: Optional[str]) -> bool:
    """Loopback, private network or *.local hosts count as local; anything else (or nothing) as cloud."""
    if not url:
        return False
    host = (urlparse(url if "//" in url else f"http://{url}").hostname or "").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _read(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return data


def load_catalog(path: Optional[Path] = None, user_dir: Optional[Path] = None) -> Catalog:
    """Loads the bundled presets; a llm_providers.yml in `user_dir` adds or replaces presets by id."""
    data = _read(Path(path) if path else DEFAULT_CATALOG)
    providers = dict(data.get("providers") or {})
    aliases = dict(data.get("aliases") or {})
    if user_dir is not None:
        override = Path(user_dir) / CATALOG_FILENAME
        if override.is_file() and override.resolve() != (Path(path) if path else DEFAULT_CATALOG).resolve():
            extra = _read(override)
            providers.update(extra.get("providers") or {})
            aliases.update(extra.get("aliases") or {})
    presets = {pid: ProviderPreset(id=pid, **(spec or {})) for pid, spec in providers.items()}
    return Catalog(providers=presets, aliases=aliases)
