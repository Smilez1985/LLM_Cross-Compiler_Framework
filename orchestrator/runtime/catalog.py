#!/usr/bin/env python3
"""Pinned llama.cpp build and Ditto models (configs/llama_runtime.yml), validated with pydantic."""

from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATALOG_PATH = Path(__file__).resolve().parents[2] / "configs" / "llama_runtime.yml"

OsName = Literal["linux", "windows", "macos"]
ArchName = Literal["x86_64", "arm64"]
Backend = Literal["cpu", "vulkan", "cuda", "rocm", "metal"]
GPU_BACKENDS = ("vulkan", "cuda", "rocm", "metal")

_SHA256 = r"^[0-9a-f]{64}$"
_FILE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"  # a plain file name: no paths, no traversal


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssetFile(_Strict):
    name: str = Field(pattern=_FILE_NAME, max_length=200)
    size: Optional[int] = Field(default=None, gt=0)
    # Optional in the schema so that an unpinned entry can be listed; installing it is refused.
    sha256: Optional[str] = Field(default=None, pattern=_SHA256)


class RuntimeAsset(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    os: OsName
    arch: ArchName
    backend: Backend
    cuda: Optional[str] = Field(default=None, pattern=r"^\d+\.\d+$")
    min_driver: Optional[float] = None
    min_compute_cap: Optional[float] = None
    opt_in: bool = False
    files: List[AssetFile] = Field(min_length=1)

    @property
    def variant(self) -> str:
        """Folder suffix: cpu, vulkan, cuda-13.4, ..."""
        return f"{self.backend}-{self.cuda}" if self.cuda else self.backend

    @property
    def pinned(self) -> bool:
        return all(f.sha256 for f in self.files)

    @property
    def download_size(self) -> int:
        return sum(f.size or 0 for f in self.files)


class LlamaCppBuild(_Strict):
    build: str = Field(pattern=r"^b\d+$")
    version: Optional[str] = None
    base_url: str
    features: Dict[str, bool] = Field(default_factory=dict)
    assets: List[RuntimeAsset]

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://") or not v.endswith("/"):
            raise ValueError("base_url must be an https:// URL ending with '/'")
        return v

    def supports(self, feature: str) -> bool:
        return bool(self.features.get(feature, False))


class ServerDefaults(_Strict):
    ctx_cap: int = Field(default=32768, ge=512, le=1_048_576)
    ctx_min: int = Field(default=4096, ge=256)
    sleep_idle_seconds: int = Field(default=0, ge=0)
    health_timeout_seconds: int = Field(default=300, ge=5)


class ModelEntry(_Strict):
    tier: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    repo: str = Field(pattern=r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
    # A commit, never a branch or tag: the pinned sha256 belongs to exactly this revision.
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    file: str = Field(pattern=_FILE_NAME, max_length=200)
    size: Optional[int] = Field(default=None, gt=0)
    sha256: Optional[str] = Field(default=None, pattern=_SHA256)
    license: str = "unknown"
    native_context: int = Field(default=8192, gt=0)
    kv_bytes_per_token: int = Field(default=131072, gt=0)
    on_request: bool = False
    description: str = ""


class TierRule(_Strict):
    tier: str
    min_vram_gib: Optional[float] = None
    min_ram_gib: Optional[float] = None
    max_ram_gib: Optional[float] = None


class RuntimeCatalog(_Strict):
    schema_version: Literal[1] = 1
    llama_cpp: LlamaCppBuild
    server: ServerDefaults = ServerDefaults()
    models: List[ModelEntry]
    tier_rules: List[TierRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "RuntimeCatalog":
        tiers = [m.tier for m in self.models]
        if len(tiers) != len(set(tiers)):
            raise ValueError("model tiers must be unique")
        ids = [a.id for a in self.llama_cpp.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset ids must be unique")
        for rule in self.tier_rules:
            if rule.tier not in tiers:
                raise ValueError(f"tier rule refers to unknown tier '{rule.tier}'")
        return self

    def model(self, tier: str) -> ModelEntry:
        for m in self.models:
            if m.tier == tier:
                return m
        raise KeyError(f"Unknown model tier '{tier}'. Known: {', '.join(m.tier for m in self.models)}")

    def assets_for(self, os_name: str, arch: str) -> List[RuntimeAsset]:
        return [a for a in self.llama_cpp.assets if a.os == os_name and a.arch == arch]


def load_catalog(path: Optional[Path] = None) -> RuntimeCatalog:
    with open(path or CATALOG_PATH, "r", encoding="utf-8") as f:
        return RuntimeCatalog.model_validate(yaml.safe_load(f))
