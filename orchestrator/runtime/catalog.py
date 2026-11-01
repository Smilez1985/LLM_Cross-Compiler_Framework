#!/usr/bin/env python3
"""Pinned llama.cpp (source commit and prebuilt archives), CUDA toolkits, builder bases and Ditto models
(configs/llama_runtime.yml), validated with pydantic."""

import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple

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
    # Linux archives: the glibc they were built against (the Ubuntu release of upstream's release job). A machine with
    # an older glibc, or with musl, cannot start them (hardware.select_backend refuses them there).
    glibc_min: Optional[str] = Field(default=None, pattern=r"^\d{1,2}\.\d{1,3}$")
    opt_in: bool = False  # never chosen automatically, only when the backend is asked for
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


_COMMIT = r"^[0-9a-f]{40}$"
# An https git URL without credentials, query or fragment.
_GIT_URL = r"^https://[A-Za-z0-9][A-Za-z0-9.-]*(?::\d{1,5})?(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+$"
_VERSION = r"^\d{1,5}(?:\.\d{1,6}){0,3}$"
# image:tag@sha256:<digest>; registry host and path components without credentials.
_IMAGE_BY_DIGEST = r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9._-]{1,128})?@sha256:[0-9a-f]{64}$"


class LlamaCppSource(_Strict):
    """The one llama.cpp pin every self-built llama.cpp comes from: a commit, never a moving tag."""
    repo: str = Field(pattern=_GIT_URL, max_length=200)
    commit: str = Field(pattern=_COMMIT)
    tag: str = Field(pattern=r"^b\d{1,7}$")
    build_number: int = Field(gt=0)

    # the project the self-build path builds from this pin (runtime/build/projects.py)
    project: ClassVar[str] = "llama.cpp"

    @model_validator(mode="after")
    def _label_matches_number(self) -> "LlamaCppSource":
        if self.tag != f"b{self.build_number}":
            raise ValueError(f"source.tag {self.tag} and source.build_number {self.build_number} disagree")
        return self

    @property
    def short_commit(self) -> str:
        """The 8 hex characters llama.cpp's --version prints."""
        return self.commit[:8]


class WhisperCppSource(_Strict):
    """The one whisper.cpp pin every self-built whisper.cpp (the board bundle's speech recognition) comes from: a
    commit, never a moving tag. The tag is the label: the pin-bump check compares the tag's commit with this one."""
    repo: str = Field(pattern=_GIT_URL, max_length=200)
    commit: str = Field(pattern=_COMMIT)
    tag: str = Field(pattern=r"^v\d{1,3}\.\d{1,3}\.\d{1,3}$")

    # the project the self-build path builds from this pin (runtime/build/projects.py)
    project: ClassVar[str] = "whisper.cpp"

    @property
    def short_commit(self) -> str:
        return self.commit[:8]


class WhisperCppBuild(_Strict):
    """whisper.cpp for the board bundle: only self-built from `source` (there is no prebuilt archive)."""
    source: WhisperCppSource


class LlamaCppBuild(_Strict):
    build: str = Field(pattern=r"^b\d+$")
    version: Optional[str] = None
    # Required for self-built llama.cpp (orchestrator/runtime/build/); the prebuilt download does not need it.
    source: Optional[LlamaCppSource] = None
    base_url: str
    features: Dict[str, bool] = Field(default_factory=dict)
    assets: List[RuntimeAsset]

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        return _https_dir(v)

    @model_validator(mode="after")
    def _one_label(self) -> "LlamaCppBuild":
        if self.source is not None and self.source.tag != self.build:
            raise ValueError(f"llama_cpp.build ({self.build}) and llama_cpp.source.tag ({self.source.tag}) must be "
                             "the same label")
        return self

    def supports(self, feature: str) -> bool:
        return bool(self.features.get(feature, False))


class PinnedFile(_Strict):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$", max_length=200)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256)


class VersionRange(_Strict):
    min: str = Field(pattern=_VERSION)
    max: str = Field(pattern=_VERSION)

    @model_validator(mode="after")
    def _ordered(self) -> "VersionRange":
        if version_tuple(self.min) > version_tuple(self.max):
            raise ValueError(f"range {self.min}..{self.max} is empty")
        return self


class HostCompilers(_Strict):
    """Host compiler versions nvcc accepts, inclusive. nvcc checks the major version (Linux) and the MSVC version
    (Windows), so a range like ["6", "14"] covers GCC 6.1 to 14.x."""
    gcc: Optional[Tuple[str, str]] = None
    clang: Optional[Tuple[str, str]] = None
    msvc: Optional[Tuple[str, str]] = None

    @field_validator("gcc", "clang", "msvc")
    @classmethod
    def _versions(cls, v: Optional[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
        if v is not None:
            if not all(re.fullmatch(_VERSION, part) for part in v) or version_tuple(v[0]) > version_tuple(v[1]):
                raise ValueError(f"not a version range: {v!r}")
        return v


class CudaToolkit(_Strict):
    version: str = Field(pattern=r"^\d{1,2}\.\d{1,2}\.\d{1,3}$")
    manifest: PinnedFile
    components: List[str] = Field(min_length=1)
    platforms: List[Literal["linux-x86_64", "linux-sbsa", "linux-aarch64", "windows-x86_64", "windows-arm64"]]
    compute_caps: VersionRange
    host_compilers: HostCompilers

    @field_validator("components")
    @classmethod
    def _component_names(cls, v: List[str]) -> List[str]:
        for name in v:
            if not re.fullmatch(r"^[a-z][a-z0-9_]{1,40}$", name):
                raise ValueError(f"not a redist component name: {name!r}")
        return v

    @model_validator(mode="after")
    def _manifest_matches(self) -> "CudaToolkit":
        if self.manifest.name != f"redistrib_{self.version}.json":
            raise ValueError(f"toolkit {self.version}: manifest must be redistrib_{self.version}.json")
        return self

    @property
    def major_minor(self) -> str:
        return ".".join(self.version.split(".")[:2])


class CudaImage(_Strict):
    """A prebuilt NVIDIA development image: an explicit opt-in only (decision D6), never chosen automatically."""
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    cuda: str = Field(pattern=r"^\d{1,2}\.\d{1,2}\.\d{1,3}$")
    image: str = Field(pattern=_IMAGE_BY_DIGEST, max_length=300)
    opt_in: Literal[True]


class CudaCatalog(_Strict):
    redist_base_url: str
    cmake_min: str = Field(default="3.18", pattern=_VERSION)
    toolkits: List[CudaToolkit] = Field(default_factory=list)
    prebuilt_images: List[CudaImage] = Field(default_factory=list)

    @field_validator("redist_base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        return _https_dir(v)

    @model_validator(mode="after")
    def _unique(self) -> "CudaCatalog":
        versions = [t.version for t in self.toolkits]
        if len(versions) != len(set(versions)):
            raise ValueError("CUDA toolkit versions must be unique")
        ids = [i.id for i in self.prebuilt_images]
        if len(ids) != len(set(ids)):
            raise ValueError("prebuilt CUDA image ids must be unique")
        return self


class BuildBase(_Strict):
    """A builder container base for container and cross builds, chosen by the C library of the target machine."""
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    libc: Literal["glibc", "musl"]
    libc_version: str = Field(pattern=r"^\d{1,3}(\.\d{1,5}){1,3}$")
    gcc: str = Field(pattern=_VERSION)
    image: str = Field(pattern=_IMAGE_BY_DIGEST, max_length=300)


class BuildJobs(_Strict):
    """Parallel compile jobs of a self-built llama.cpp: -j = min(logical CPUs, available RAM / memory_mib of the
    backend), at least 1. The values are estimates of the peak memory per job (MiB)."""
    memory_mib: Dict[Backend, int] = Field(default_factory=lambda: {"cpu": 1024, "vulkan": 1536, "cuda": 3072,
                                                                    "rocm": 2048, "metal": 1024})

    @field_validator("memory_mib")
    @classmethod
    def _positive(cls, v: Dict[str, int]) -> Dict[str, int]:
        if any(m < 128 or m > 65536 for m in v.values()):
            raise ValueError("memory_mib values must be between 128 and 65536")
        return v

    def per_job_mib(self, backend: str) -> int:
        return self.memory_mib.get(backend) or self.memory_mib.get("cpu") or 1024


class SmokeModel(_Strict):
    """The tiny pinned GGUF of the build check (decision D17): loaded once and asked for a few tokens after every
    build. A catalog model pinned by revision, size and sha256, so it runs natively (it is no untrusted model)."""
    repo: str = Field(pattern=r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    file: str = Field(pattern=_MODEL_NAME, max_length=200)
    size: int = Field(gt=0, le=512 * 1024 ** 2)
    sha256: str = Field(pattern=_SHA256)
    license: str

    def url(self, base_url: str) -> str:
        return f"{base_url}{self.repo}/resolve/{self.revision}/{self.file}"


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
    # the board bundle's speech recognition, built by the same self-build path as llama.cpp
    whisper_cpp: Optional[WhisperCppBuild] = None
    cuda: Optional[CudaCatalog] = None
    build_bases: List[BuildBase] = Field(default_factory=list)
    build_jobs: BuildJobs = BuildJobs()
    smoke_model: Optional[SmokeModel] = None
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
        bases = [b.id for b in self.build_bases]
        if len(bases) != len(set(bases)):
            raise ValueError("build base ids must be unique")
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
