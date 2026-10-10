#!/usr/bin/env python3
"""Pinned llama.cpp build and Ditto models (configs/llama_runtime.yml), validated with pydantic."""

import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATALOG_PATH = Path(__file__).resolve().parents[2] / "configs" / "llama_runtime.yml"

OsName = Literal["linux", "windows", "macos"]
ArchName = Literal["x86_64", "arm64"]
Backend = Literal["cpu", "vulkan", "cuda", "rocm", "metal"]
GPU_BACKENDS = ("vulkan", "cuda", "rocm", "metal")

_SHA256 = r"^[0-9a-f]{64}$"
# Plain file names (no paths, no traversal) of the archive types the installer unpacks, and of GGUF models.
_ARCHIVE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(zip|tar\.gz|tgz)$"
_MODEL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*\.gguf$"


def version_tuple(value: Optional[str]) -> Optional[Tuple[int, ...]]:
    """'570.124.06' -> (570, 124, 6). Versions compare as tuples: as floats 570.124 would be older than 570.26."""
    m = re.match(r"\s*(\d+(?:\.\d+)*)", str(value or ""))
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _https_dir(value: str) -> str:
    if not value.startswith("https://") or not value.endswith("/"):
        raise ValueError("must be an https:// URL ending with '/'")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssetFile(_Strict):
    name: str = Field(pattern=_ARCHIVE_NAME, max_length=200)
    # Both are optional in the schema so that an unpinned entry can be listed; installing it is refused.
    size: Optional[int] = Field(default=None, gt=0)
    sha256: Optional[str] = Field(default=None, pattern=_SHA256)
    libs: bool = False  # shared libraries (CUDA runtime) that are moved next to llama-server after unpacking

    @property
    def pinned(self) -> bool:
        return bool(self.sha256 and self.size)


class RuntimeAsset(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    os: OsName
    arch: ArchName
    backend: Backend
    cuda: Optional[str] = Field(default=None, pattern=r"^\d+\.\d+$")
    # Oldest driver for GPUs the build carries native code for (CUDA minor version compatibility).
    min_driver: Optional[str] = None
    # Oldest driver for GPUs that only get PTX, which the driver JIT-compiles: minor version compatibility
    # does not cover PTX, so these need the driver of the toolkit the build was made with.
    jit_min_driver: Optional[str] = None
    native_compute_caps: List[float] = Field(default_factory=list)
    min_compute_cap: Optional[float] = None
    gpu_targets: List[str] = Field(default_factory=list)  # ROCm: the gfx targets the build was compiled for
    opt_in: bool = False
    files: List[AssetFile] = Field(min_length=1)

    @field_validator("min_driver", "jit_min_driver", mode="before")
    @classmethod
    def _driver_version(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, (bool, float)):
            # YAML reads 570.60 as the float 570.6; a quoted string keeps the version exact.
            raise ValueError("write driver versions as quoted strings, e.g. '570.26'")
        if not re.fullmatch(r"\d+(\.\d+){0,3}", str(v)):
            raise ValueError(f"not a driver version: {v!r}")
        return str(v)

    @property
    def variant(self) -> str:
        """Folder suffix: cpu, vulkan, cuda-13.4, ..."""
        return f"{self.backend}-{self.cuda}" if self.cuda else self.backend

    @property
    def pinned(self) -> bool:
        return all(f.pinned for f in self.files)

    @property
    def download_size(self) -> int:
        return sum(f.size or 0 for f in self.files)

    def has_native_code(self, compute_cap: Optional[float]) -> bool:
        return compute_cap is not None and any(abs(c - compute_cap) < 0.01 for c in self.native_compute_caps)


class LlamaCppBuild(_Strict):
    build: str = Field(pattern=r"^b\d+$")
    version: Optional[str] = None
    base_url: str
    features: Dict[str, bool] = Field(default_factory=dict)
    assets: List[RuntimeAsset]

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        return _https_dir(v)

    def supports(self, feature: str) -> bool:
        return bool(self.features.get(feature, False))


class ServerDefaults(_Strict):
    ctx_cap: int = Field(default=32768, ge=512, le=1_048_576)
    ctx_min: int = Field(default=4096, ge=256)
    sleep_idle_seconds: int = Field(default=0, ge=0)
    health_timeout_seconds: int = Field(default=300, ge=5)
    log_max_mib: int = Field(default=5, ge=1)
    log_backups: int = Field(default=3, ge=0, le=20)


class ModelEntry(_Strict):
    tier: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    repo: str = Field(pattern=r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
    # A commit, never a branch or tag: the pinned sha256 belongs to exactly this revision.
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    file: str = Field(pattern=_MODEL_NAME, max_length=200)
    size: Optional[int] = Field(default=None, gt=0)
    sha256: Optional[str] = Field(default=None, pattern=_SHA256)
    license: str = "unknown"
    native_context: int = Field(default=8192, gt=0)
    kv_bytes_per_token: int = Field(default=131072, gt=0)
    on_request: bool = False
    description: str = ""

    @property
    def pinned(self) -> bool:
        return bool(self.sha256 and self.size)

    @property
    def alias(self) -> str:
        """The model name llama-server reports and expects in requests."""
        return Path(self.file).stem

    def url(self, base_url: str) -> str:
        return f"{base_url}{self.repo}/resolve/{self.revision}/{self.file}"


class TierRule(_Strict):
    tier: str
    min_vram_gib: Optional[float] = None
    min_ram_gib: Optional[float] = None
    max_ram_gib: Optional[float] = None

    @property
    def unconditional(self) -> bool:
        return self.min_vram_gib is None and self.min_ram_gib is None and self.max_ram_gib is None


class RuntimeCatalog(_Strict):
    schema_version: Literal[1] = 1
    llama_cpp: LlamaCppBuild
    model_base_url: str = "https://huggingface.co/"
    server: ServerDefaults = ServerDefaults()
    models: List[ModelEntry]
    tier_rules: List[TierRule] = Field(min_length=1)

    @field_validator("model_base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        return _https_dir(v)

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
        if not self.tier_rules[-1].unconditional:
            raise ValueError("the last tier rule must have no conditions, so that every machine gets a model")
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
