#!/usr/bin/env python3
"""Provider presets (configs/llm_providers.yml) as typed data."""

import ipaddress
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.llm.types import Effort

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "configs" / "llm_providers.yml"
CATALOG_FILENAME = "llm_providers.yml"

AuthMethod = Literal["api_key", "oauth", "none"]


class ProviderPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    adapter: Literal["anthropic", "openai_responses", "openai_compat", "claude_code"]
    base_url: Optional[str] = None
    base_url_configurable: bool = False  # ai_base_url applies; cloud presets keep their fixed endpoint
    runtime_endpoint: bool = False       # endpoint is supplied by a running local server
    location: Literal["cloud", "local", "auto"] = "cloud"
    auth: List[AuthMethod] = Field(default_factory=lambda: ["api_key"])
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
