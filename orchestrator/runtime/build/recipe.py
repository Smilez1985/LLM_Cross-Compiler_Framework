#!/usr/bin/env python3
"""
The llama.cpp build recipe: a hardware profile becomes a build plan.

The hardware probe runs on the machine that will run the binaries (a PC or a board) and reports facts; this module
derives the CMake options from them. plan_build() is pure: no I/O, no clock, no environment. What needs I/O (compiler
versions, what -march=native expands to, the CUDA toolkit found or provisioned) is gathered beforehand into a
ToolchainInfo, and the executor that runs a plan decides nothing itself.

Every plan
- builds the pinned commit (configs/llama_runtime.yml, llama_cpp.source) with build number and commit set explicitly,
  so --version is right without .git;
- sets every download-capable option of llama.cpp at the pin to OFF or leaves it unset (DOWNLOAD_OPTIONS_OFF,
  NEVER_SET) and sets FETCHCONTENT_FULLY_DISCONNECTED=ON, so configure and build need no network;
- builds without subprocess support (LLAMA_SUBPROCESS=OFF, decision D22): no shell tool, no stdio MCP servers, no
  router child processes in llama-server;
- follows the OS, architecture and C library of the machine that runs the binaries (decision 8): a build on that
  machine uses GGML_NATIVE, a build for another machine uses the probe's CPU features (or all CPU variants when the
  probe could not tell), a container or cross build gets a builder base whose C library (glibc, or musl on Alpine)
  is not newer than the machine's LIBC_VERSION, and none without that value;
- has a build ID, the sha256 of everything that changes the binaries. Volatile probe values cannot enter it, because
  only derived options do.

Probe values are untrusted: profiles come from devices over SSH and from files carried by hand. HardwareProfile has
validated them against configs/hardware_probe_schema.yml; this module checks every value again before it becomes part
of an option, and free text (CPU and GPU names) never reaches an option, a file name or a command.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from orchestrator.runtime.build import ggml_cpu_options as _cpu_options
from orchestrator.runtime.catalog import BuildBase, CudaToolkit, RuntimeCatalog, version_tuple
from orchestrator.runtime.profile import HardwareProfile

RECIPE_VERSION = 4  # bump when the same inputs give different options: every build ID changes with it
# 2: Linux Clang builds use OpenMP only with a usable libomp; ARM CPU variants need a compiler that knows +sme;
#    the CPU variant modules of device builds link the C++ runtime statically too
# 3: conversion tools (host-tools, built in the target's image) get the build host's CPU features as explicit
#    options instead of GGML_NATIVE
# 4: GGML_AVX512 only when the probe's CPU flags name AVX-512 F, CD, VL, DQ and BW (AVX512_SET), else the AVX2 plan

PURPOSES = ("host-runtime", "host-tools", "device-runtime")
PLACEMENTS = ("native", "container", "cross", "on-device")
BACKENDS = ("auto", "cpu", "cuda", "vulkan", "rocm", "metal")
GPU_BACKENDS = ("cuda", "vulkan", "rocm", "metal")

# What each purpose builds. llama-cli exists only when the server is built (tools/CMakeLists.txt:24-28 at the pin).
TARGETS: Mapping[str, Tuple[str, ...]] = {
    "host-runtime": ("llama-server", "llama-cli", "llama-bench", "llama-perplexity"),
    "host-tools": ("llama-quantize", "llama-imatrix", "llama-perplexity", "llama-completion", "llama-bench"),
    "device-runtime": ("llama-server", "llama-cli", "llama-bench", "llama-perplexity"),
}

# --- download-capable options of llama.cpp at the pin (paths in the llama.cpp tree) ---------------------------------
# Re-checked at every pin bump (CONTRIBUTING.md, "Bumping the llama.cpp pin").

# Set to OFF on every build.
DOWNLOAD_OPTIONS_OFF: Mapping[str, str] = {
    "LLAMA_BUILD_TESTS": "tests/CMakeLists.txt:267 downloads test models (cmake/download-models.cmake)",
    "LLAMA_BUILD_EXAMPLES": "examples/eval-callback/CMakeLists.txt:21 downloads a model",
    "LLAMA_BUILD_APP": "app/CMakeLists.txt: the unified binary with a model downloader (app/download.cpp)",
    "LLAMA_USE_PREBUILT_UI": "scripts/ui-assets.cmake:431-575 downloads the web UI from a Hugging Face bucket",
    "LLAMA_BUILD_UI": "scripts/ui-assets.cmake:332-409 runs npm ci",
    "LLAMA_LLGUIDANCE": "common/CMakeLists.txt:154-170 clones llguidance from git and runs cargo",
    "GGML_CPU_KLEIDIAI": "ggml/src/ggml-cpu/CMakeLists.txt:625-675 fetches the KleidiAI source archive",
    "GGML_OPENMP_FETCH": "ggml/src/CMakeLists.txt:227-338 downloads an LLVM OpenMP installer and licence",
}

# Never set (each one downloads at configure time); a plan that contains one is refused.
NEVER_SET: Mapping[str, str] = {
    "LLAMA_BUILD_BORINGSSL": "vendor/cpp-httplib/CMakeLists.txt:42-84 fetches BoringSSL from git",
    "LLAMA_BUILD_LIBRESSL": "vendor/cpp-httplib/CMakeLists.txt:85-127 fetches a LibreSSL archive",
    "GGML_CUDA_CCCL_VERSION": "ggml/src/ggml-cuda/CMakeLists.txt:74-84 fetches CCCL from git",
    "GGML_ZENDNN": "ggml/src/ggml-zendnn/CMakeLists.txt:16-30 clones ZenDNN from git when ZENDNN_ROOT is unset",
    "GGML_VIRTGPU": "ggml/src/ggml-virtgpu/CMakeLists.txt:9-18 downloads a header file",
    "LLAMA_BUILD_WEBUI": "tools/ui/CMakeLists.txt:7-10, deprecated alias of LLAMA_BUILD_UI",
    "LLAMA_USE_PREBUILT_WEBUI": "tools/ui/CMakeLists.txt:11-14, deprecated alias of LLAMA_USE_PREBUILT_UI",
}

# Ignored at the pin, but a download option in older versions: a target module's CMake flags may not switch it on
# (refused_option), and the board package's cross build keeps it OFF (targets/*/modules/target_module.sh).
DEPRECATED_DOWNLOAD_OPTIONS: Mapping[str, str] = {
    "LLAMA_CURL": "CMakeLists.txt:195 deprecated and ignored (llama_option_depr); older versions link libcurl to "
                  "download models",
}

# CMake variables that make CMake run a script file at project() (cmake-variables(7): CMAKE_PROJECT_INCLUDE,
# CMAKE_PROJECT_INCLUDE_BEFORE, CMAKE_PROJECT_<name>_INCLUDE[_BEFORE], CMAKE_PROJECT_TOP_LEVEL_INCLUDES). The script runs
# after the command line's options and can switch any of them back on, like an initial cache (-C): never set.
SCRIPT_VARIABLE_RE = re.compile(r"^CMAKE_PROJECT_(?:[^\s=:]+_)?INCLUDE(?:_BEFORE)?$|^CMAKE_PROJECT_TOP_LEVEL_INCLUDES$")

# Options that do not change the binaries and stay out of the build ID.
_NOT_IN_BUILD_ID = frozenset({"GGML_CCACHE"})

# The 14 x86 features of the probe and the ggml option each one maps to (ggml/CMakeLists.txt:154-170).
X86_FEATURES: Mapping[str, str] = {
    "SUPPORTS_SSE42": "GGML_SSE42", "SUPPORTS_AVX": "GGML_AVX", "SUPPORTS_AVX2": "GGML_AVX2",
    "SUPPORTS_BMI2": "GGML_BMI2", "SUPPORTS_F16C": "GGML_F16C", "SUPPORTS_FMA": "GGML_FMA",
    "SUPPORTS_AVX512": "GGML_AVX512", "SUPPORTS_AVX512_VBMI": "GGML_AVX512_VBMI",
    "SUPPORTS_AVX512_VNNI": "GGML_AVX512_VNNI", "SUPPORTS_AVX512_BF16": "GGML_AVX512_BF16",
    "SUPPORTS_AVX_VNNI": "GGML_AVX_VNNI", "SUPPORTS_AMX_TILE": "GGML_AMX_TILE",
    "SUPPORTS_AMX_INT8": "GGML_AMX_INT8", "SUPPORTS_AMX_BF16": "GGML_AMX_BF16",
}
# A feature is only used together with the one it builds on (an inconsistent probe file cannot add -mavx512vnni
# without AVX-512 F, for example).
_X86_NEEDS: Mapping[str, str] = {
    "SUPPORTS_AVX2": "SUPPORTS_AVX", "SUPPORTS_FMA": "SUPPORTS_AVX", "SUPPORTS_F16C": "SUPPORTS_AVX",
    "SUPPORTS_AVX512": "SUPPORTS_AVX2", "SUPPORTS_AVX512_VBMI": "SUPPORTS_AVX512",
    "SUPPORTS_AVX512_VNNI": "SUPPORTS_AVX512", "SUPPORTS_AVX512_BF16": "SUPPORTS_AVX512",
    "SUPPORTS_AVX_VNNI": "SUPPORTS_AVX2", "SUPPORTS_AMX_INT8": "SUPPORTS_AMX_TILE",
    "SUPPORTS_AMX_BF16": "SUPPORTS_AMX_TILE",
}
# The CPU flags (/proc/cpuinfo names) GGML_AVX512 compiles for: -mavx512f -mavx512cd -mavx512vl -mavx512dq
# -mavx512bw (_X86_FLAGS). GGML_AVX512 is used only when the probe's CPU flags name all of them (avx512_missing);
# SUPPORTS_AVX512 alone is AVX-512 F (configs/hardware_probe_schema.yml).
AVX512_SET: Tuple[str, ...] = ("avx512f", "avx512cd", "avx512vl", "avx512dq", "avx512bw")
# The compiler flags ggml adds per option without GGML_NATIVE (ggml/src/ggml-cpu/CMakeLists.txt:347-410).
_X86_FLAGS: Mapping[str, Tuple[str, ...]] = {
    "GGML_SSE42": ("-msse4.2",), "GGML_F16C": ("-mf16c",), "GGML_FMA": ("-mfma",), "GGML_BMI2": ("-mbmi2",),
    "GGML_AVX": ("-mavx",), "GGML_AVX2": ("-mavx2",), "GGML_AVX_VNNI": ("-mavxvnni",),
    "GGML_AVX512": ("-mavx512f", "-mavx512cd", "-mavx512vl", "-mavx512dq", "-mavx512bw"),
    "GGML_AVX512_VBMI": ("-mavx512vbmi",), "GGML_AVX512_VNNI": ("-mavx512vnni",),
    "GGML_AVX512_BF16": ("-mavx512bf16",), "GGML_AMX_TILE": ("-mamx-tile",), "GGML_AMX_INT8": ("-mamx-int8",),
    "GGML_AMX_BF16": ("-mamx-bf16",),
}
# The four ARM features whose UNKNOWN makes a build for another machine take all CPU variants (and that a native
# build's identity records): a probe that cannot tell these cannot tell the board's base level.
ARM_FEATURES: Mapping[str, str] = {
    "SUPPORTS_DOTPROD": "dotprod", "SUPPORTS_FP16_ARITH": "fp16", "SUPPORTS_I8MM": "i8mm", "SUPPORTS_SVE": "sve",
}
# Every -march tag GGML_CPU_ARM_ARCH is built from, in ggml's ARCH_TAGS order (ggml-cpu/CMakeLists.txt at the pin:
# +dotprod +fp16 +sve +i8mm +sve2 +sme), from the inventory of the pin (ggml_cpu_options.py, the planned ARM tags).
ARM_ARCH_TAGS: Tuple[Tuple[str, str], ...] = tuple(
    (row.probe_key, row.flag.lstrip("+")) for row in _cpu_options.rows("arm64", _cpu_options.ARM_ARCH_TAG)
    if row.state == _cpu_options.PLANNED and row.probe_key)
# ARM features the board may have that no tag of the pin uses: probe key -> why (the plan names them as unusable).
ARM_UNUSABLE: Mapping[str, str] = {
    row.probe_key: row.reason for row in _cpu_options.rows("arm64", _cpu_options.ARM_ARCH_TAG)
    if row.state == _cpu_options.NOT_USABLE and row.probe_key}
# The tag each ARM tag builds on (probe keys, from the inventory): a tag is planned only when its base is planned, as
# _X86_NEEDS for x86 (GCC and Clang make +sve imply +fp16 and +sve2 imply +sve, GCC 14 makes +sme imply +sve2, and
# +i8mm without +dotprod or +fp16 would raise an armv8.0 board to armv8.2-a).
ARM_NEEDS: Mapping[str, str] = {
    row.probe_key: row.builds_on for row in _cpu_options.rows("arm64", _cpu_options.ARM_ARCH_TAG)
    if row.state == _cpu_options.PLANNED and row.probe_key and row.builds_on}

_SUPPORTED_ARCH = ("x86_64", "arm64")
_SUPPORTED_OS = ("linux", "windows", "macos")
_LIBC_OF_OS = {"linux": ("glibc", "musl"), "windows": ("ucrt",), "macos": ("libsystem",)}

# Validation of the probe values that enter options (design 3.10), independent of the schema file.
_RE_LIBC_VERSION = re.compile(r"^\d{1,3}(\.\d{1,5}){1,3}$", re.ASCII)
_RE_COMPUTE_CAP = re.compile(r"^\d{1,2}\.\d$", re.ASCII)
_RE_GFX = re.compile(r"^gfx[0-9a-f]{3,5}$", re.ASCII)
_RE_CUDA_MAX = re.compile(r"^\d{1,2}\.\d{1,3}$", re.ASCII)
_RE_VERSION = re.compile(r"^\d{1,5}(\.\d{1,6}){0,3}$", re.ASCII)
_RE_TOOL = re.compile(r"^(gcc|clang|appleclang|msvc|icx)-(\d{1,4}(?:\.\d{1,6}){0,3})$", re.ASCII)
_RE_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]{1,128})?(@sha256:[0-9a-f]{64})?$", re.ASCII)
_RE_NATIVE = re.compile(r"^[A-Za-z0-9 ._=+,-]{1,512}$", re.ASCII)
# Every option of a plan: computed from validated values only; this is the last guard before a command line.
_RE_OPTION_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$", re.ASCII)
_RE_OPTION_VALUE = re.compile(r"^[A-Za-z0-9 ._;:+=/$-]{0,256}$", re.ASCII)
# A CPU feature as plans name it (DOTPROD, AVX512_VNNI, POWER_CPU_TYPE): the probe key without SUPPORTS_.
_RE_FEATURE = re.compile(r"^[A-Z][A-Z0-9_]{0,47}$", re.ASCII)

# Cross toolchains per target architecture and C library (Linux).
_GNU_TRIPLE = {"x86_64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}
_MUSL_TRIPLE = {"x86_64": "x86_64-alpine-linux-musl", "arm64": "aarch64-alpine-linux-musl"}
_CMAKE_PROCESSOR = {"x86_64": "x86_64", "arm64": "aarch64"}
_SYSROOT = "/opt/sysroot"  # where the builder image keeps the target's root file system for clang cross builds

# Upstream's ARM CPU variants (GGML_CPU_ALL_VARIANTS on Linux) include armv9.2-a+sme, which GCC 13 rejects (upstream's
# release workflow installs GCC 14 for it, .github/workflows/release.yml at the pin). No option shortens the list
# (ggml/src/CMakeLists.txt:533-544), so an older compiler gets no variant build. Clang: an estimate, from the SME
# keyword attribute ggml's feature check uses (__arm_locally_streaming).
SME_COMPILER_MIN: Mapping[str, Tuple[int, ...]] = {"gcc": (14,), "clang": (18,)}

_STATIC_RUNTIME = "-static-libstdc++ -static-libgcc"
_DEVICE_RPATH = "$ORIGIN;$ORIGIN/../lib"  # binaries in bin/, libgomp.so.1 in lib/ of the device tarball


class PlanError(ValueError):
    """No plan can be made: the message names the missing fact or the unsupported combination and the way out."""


@dataclass(frozen=True)
class ToolchainInfo:
    """What the build host offers, gathered by I/O before planning (toolchain detection). Versions only, no paths.

    compiler is the C/C++ compiler of a native build as the probe writes it (gcc-13.3.0, clang-19.1.5,
    appleclang-16.0.0); container and cross builds use their builder base's compiler instead. cuda names the CUDA
    toolkit found or provisioned; None lets the recipe choose one from the catalog. cuda_image opts in to a prebuilt
    NVIDIA image (decision D6). base_image is the image a host-tools build runs in (the target's toolchain image),
    base_libc its C library. native_expansion is what -march=native / -mcpu=native expands to on the build machine.
    openmp says whether the compiler of a native build compiles and links OpenMP code (Clang needs libomp for it);
    None means not checked, which a Linux Clang build treats as no.
    """
    host_os: str
    host_arch: str
    compiler: Optional[str] = None
    cmake: Optional[str] = None
    msvc: Optional[str] = None
    cuda: Optional[str] = None
    cuda_image: Optional[str] = None
    glslc: Optional[str] = None
    rocm: Optional[str] = None
    base_image: Optional[str] = None
    base_libc: str = "glibc"
    native_expansion: Optional[str] = None
    ccache: bool = False
    openmp: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.host_os not in _SUPPORTED_OS:
            raise PlanError(f"build host OS {self.host_os!r} is not supported (linux, windows, macos)")
        if self.host_arch not in _SUPPORTED_ARCH:
            raise PlanError(f"build host architecture {self.host_arch!r} is not supported (x86_64, arm64)")
        if self.compiler is not None and not _RE_TOOL.fullmatch(self.compiler):
            raise PlanError("compiler must look like gcc-13.3.0 or clang-19.1.5")
        for name in ("cmake", "msvc", "cuda", "glslc", "rocm"):
            value = getattr(self, name)
            if value is not None and not _RE_VERSION.fullmatch(value):
                raise PlanError(f"{name} must be a dotted version number")
        if self.base_image is not None and not _RE_IMAGE.fullmatch(self.base_image):
            raise PlanError("base_image is not an image reference")
        if self.cuda_image is not None and not re.fullmatch(r"^[a-z0-9][a-z0-9._-]{0,63}$", self.cuda_image):
            raise PlanError("cuda_image is not an image id of the catalog")
        if self.base_libc not in ("glibc", "musl"):
            raise PlanError("base_libc must be glibc or musl")
        if self.native_expansion is not None and not _RE_NATIVE.fullmatch(self.native_expansion):
            raise PlanError("native_expansion contains unexpected characters")
        if self.openmp not in (None, True, False):
            raise PlanError("openmp must be true, false or unset")

    @classmethod
    def from_profile(cls, profile: HardwareProfile, **overrides: Any) -> "ToolchainInfo":
        """The toolchain a machine's own probe reports (TOOL_* keys), for a build on that machine."""
        compiler = profile.get("TOOL_CXX") or profile.get("TOOL_CC")
        clang = profile.get("TOOL_CLANG")
        if clang and (compiler is None or profile.os == "windows"):
            compiler = f"clang-{clang}"  # Windows builds the CPU part with Clang, MSVC is only nvcc's host compiler
        values: Dict[str, Any] = {
            "host_os": profile.os, "host_arch": profile.arch, "compiler": compiler,
            "cmake": profile.get("TOOL_CMAKE"), "msvc": profile.get("TOOL_MSVC"), "glslc": profile.get("TOOL_GLSLC"),
            "rocm": profile.get("ROCM_VERSION"), "ccache": profile.get("TOOL_CCACHE") is not None,
        }
        values.update(overrides)
        return cls(**values)

    def compiler_id(self) -> Optional[Tuple[str, str]]:
        m = _RE_TOOL.fullmatch(self.compiler or "")
        return (m.group(1), m.group(2)) if m else None


@dataclass(frozen=True)
class BuildStep:
    """One configure-and-build run. Windows CUDA builds have two (Clang CPU part, MSVC CUDA backend)."""
    name: str
    compiler: str
    generator: str
    options: Tuple[Tuple[str, str], ...]
    targets: Tuple[str, ...]
    toolchain_file: Optional[str] = None

    def option(self, name: str) -> Optional[str]:
        return dict(self.options).get(name)

    def cmake_args(self) -> List[str]:
        """-D arguments of the configure call, sorted (an argument list, never a shell string)."""
        return [f"-D{name}={value}" for name, value in self.options]

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "compiler": self.compiler, "generator": self.generator,
                "options": dict(self.options), "targets": list(self.targets), "toolchain_file": self.toolchain_file}


@dataclass(frozen=True)
class BuildPlan:
    """What to build, where, with which options; everything an executor needs, nothing it has to decide."""
    recipe_version: int
    purpose: str
    placement: str
    backend: str
    source: Mapping[str, Any]
    target: Mapping[str, Any]
    builder: Mapping[str, Any]
    steps: Tuple[BuildStep, ...]
    toolchain: Mapping[str, Any]
    verify: Mapping[str, Any]
    notes: Tuple[str, ...]
    build_id: str = ""
    # The CPU features of the probe this plan uses, and those the machine has that the build cannot use (with the
    # reason): {"used": [label, ...], "unusable": [{"feature": label, "reason": text}, ...]}. A summary of the
    # options, so it stays out of the build ID.
    cpu_features: Mapping[str, Any] = field(default_factory=lambda: {"used": [], "unusable": []})

    @property
    def targets(self) -> Tuple[str, ...]:
        return self.steps[0].targets

    def options(self, step: int = 0) -> Dict[str, str]:
        return dict(self.steps[step].options)

    def id_inputs(self) -> Dict[str, Any]:
        """Everything that changes the binaries: the input of the build ID (notes and checks stay out)."""
        steps = []
        for step in self.steps:
            d = step.as_dict()
            d["options"] = {k: v for k, v in step.options if k not in _NOT_IN_BUILD_ID}
            steps.append(d)
        return {"recipe": self.recipe_version, "source": {"commit": self.source["commit"], "tag": self.source["tag"]},
                "target": {k: self.target[k] for k in ("os", "arch", "libc")}, "builder": dict(self.builder),
                "steps": steps, "toolchain": dict(self.toolchain)}

    def as_dict(self) -> Dict[str, Any]:
        return {"build_id": self.build_id, "recipe_version": self.recipe_version, "purpose": self.purpose,
                "placement": self.placement, "backend": self.backend, "source": dict(self.source),
                "target": dict(self.target), "builder": dict(self.builder),
                "steps": [s.as_dict() for s in self.steps], "toolchain": dict(self.toolchain),
                "verify": dict(self.verify), "notes": list(self.notes),
                "cpu_features": {"used": list(self.cpu_features.get("used") or []),
                                 "unusable": [dict(u) for u in self.cpu_features.get("unusable") or []]}}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BuildPlan":
        """The plan of as_dict() back, from a file that may come from elsewhere (an imported build.json): every
        field is type-checked and the options go through the same character check as a computed plan. Raises
        PlanError. The build ID is taken as written; build_id_of(plan.id_inputs()) tells whether it fits."""
        def mapping(value: Any, what: str) -> Mapping[str, Any]:
            if not isinstance(value, Mapping):
                raise PlanError(f"{what} is not a mapping")
            return value

        def text(value: Any, what: str) -> str:
            if not isinstance(value, str):
                raise PlanError(f"{what} is not text")
            return value

        if not isinstance(data, Mapping):
            raise PlanError("the plan is not a mapping")
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 4:
            raise PlanError("the plan has no build steps")
        steps = []
        for raw in raw_steps:
            raw = mapping(raw, "a build step")
            options = mapping(raw.get("options"), "the options of a build step")
            pairs = tuple(sorted((text(k, "an option name"), text(v, "an option value")) for k, v in options.items()))
            for name, value in pairs:
                if not _RE_OPTION_NAME.fullmatch(name) or not _RE_OPTION_VALUE.fullmatch(value):
                    raise PlanError(f"option {name[:64]!r} has unexpected characters")
            targets = raw.get("targets")
            if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                raise PlanError("the targets of a build step are not a list of names")
            toolchain_file = raw.get("toolchain_file")
            if toolchain_file is not None:
                text(toolchain_file, "the toolchain file")
            steps.append(BuildStep(text(raw.get("name"), "a step name"), text(raw.get("compiler"), "a compiler"),
                                   text(raw.get("generator"), "a generator"), pairs, tuple(targets), toolchain_file))
        source = mapping(data.get("source"), "source")
        target = mapping(data.get("target"), "target")
        for key in ("commit", "tag"):
            text(source.get(key), f"source.{key}")
        for key in ("os", "arch"):
            text(target.get(key), f"target.{key}")
        recipe_version = data.get("recipe_version")
        if not isinstance(recipe_version, int) or isinstance(recipe_version, bool):
            raise PlanError("recipe_version is not a number")
        notes = data.get("notes") or []
        if not isinstance(notes, list):
            raise PlanError("notes is not a list")
        features = mapping(data.get("cpu_features") or {}, "cpu_features")  # absent in plans before 3.3.0
        used = features.get("used") or []
        unusable = features.get("unusable") or []
        if not isinstance(used, list) or not all(isinstance(u, str) and _RE_FEATURE.fullmatch(u) for u in used):
            raise PlanError("cpu_features.used is not a list of feature names")
        if not isinstance(unusable, list) or not all(
                isinstance(u, Mapping) and isinstance(u.get("feature"), str) and _RE_FEATURE.fullmatch(u["feature"])
                and isinstance(u.get("reason"), str) for u in unusable):
            raise PlanError("cpu_features.unusable is not a list of features with their reason")
        return cls(recipe_version=recipe_version, purpose=text(data.get("purpose"), "purpose"),
                   placement=text(data.get("placement"), "placement"), backend=text(data.get("backend"), "backend"),
                   source=dict(source), target={"libc": None, **target},
                   builder=dict(mapping(data.get("builder"), "builder")), steps=tuple(steps),
                   toolchain=dict(mapping(data.get("toolchain"), "toolchain")),
                   verify=dict(mapping(data.get("verify") or {}, "verify")), notes=tuple(str(n) for n in notes),
                   build_id=str(data.get("build_id") or ""),
                   cpu_features={"used": list(used), "unusable": [{"feature": u["feature"], "reason": u["reason"]}
                                                                 for u in unusable]})


def build_id_of(inputs: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the build inputs."""
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def refused_option(name: str, value: Optional[str]) -> Optional[str]:
    """Why the CMake option NAME=VALUE has no place in a llama.cpp build of the framework, or None: it downloads at
    configure time whenever it is set (NEVER_SET), it makes CMake run a script (SCRIPT_VARIABLE_RE), or it switches on what
    every build keeps off (DOWNLOAD_OPTIONS_OFF
    and DEPRECATED_DOWNLOAD_OPTIONS other than OFF, FETCHCONTENT_FULLY_DISCONNECTED other than ON) or a server with
    subprocesses (LLAMA_SUBPROCESS other than OFF, decision D22). The one rule: check_download_options applies it to
    a plan, the module generator to the CMake flags of a target module (module_generator.download_flag_problems)."""
    if name in NEVER_SET:
        return f"{name} must never be set: {NEVER_SET[name]}"
    if SCRIPT_VARIABLE_RE.match(name):
        return f"{name} makes CMake run a CMake script, which can set any option"
    if name in DOWNLOAD_OPTIONS_OFF and value != "OFF":
        return f"{name} must be OFF on every build: {DOWNLOAD_OPTIONS_OFF[name]}"
    if name in DEPRECATED_DOWNLOAD_OPTIONS and value != "OFF":
        return f"{name} must be OFF on every build: {DEPRECATED_DOWNLOAD_OPTIONS[name]}"
    if name == "FETCHCONTENT_FULLY_DISCONNECTED" and value != "ON":
        return "FETCHCONTENT_FULLY_DISCONNECTED must be ON on every build"
    if name == "LLAMA_SUBPROCESS" and value != "OFF":
        return "LLAMA_SUBPROCESS must be OFF on every build (decision D22)"
    return None


def check_download_options(options: Mapping[str, str], prefetched: Sequence[str] = ()) -> None:
    """Refuses options that would let configure or build download anything (and a server with subprocesses): every
    option of NEVER_SET that is set, and every option a build must set to its value (refused_option). An option whose
    dependency the framework fetched before the build and mounts read-only (`prefetched`: the dependency names of
    orchestrator/runtime/build/deps.py, DEPENDENCY_OF_OPTION) is no download: the build reads the mounted copy."""
    for name in NEVER_SET:
        if name in options and DEPENDENCY_OF_OPTION.get(name) not in prefetched:
            raise PlanError(refused_option(name, options[name]))
    for name in (*DOWNLOAD_OPTIONS_OFF, "FETCHCONTENT_FULLY_DISCONNECTED", "LLAMA_SUBPROCESS"):
        if DEPENDENCY_OF_OPTION.get(name) in prefetched and name in options:
            continue
        problem = refused_option(name, options.get(name))
        if problem:
            raise PlanError(problem)


# --- the CMake flags of a target module (DECISIONS round 25 Q6) ---------------------------------------------------
# One verdict for every place that judges a module's CMake flags: the module generator (validate_module_data), the
# folder check of a target others build (module_generator.validate_target_folder: config_module.sh and a Dockerfile's
# LLAMA_CMAKE_ARGS), the build plan and the AI's facts (Core/build_plan.py, Core/module_flags.py). The shell's
# module_flag_verdicts of targets/*/modules/target_module.sh is the last check inside the build container and gives
# the same verdicts (tests/test_module_flags.py runs both over one list, tests/data/cmake_flag_vectors.json).
#
# Allowed: -DNAME=VALUE (also -D NAME=VALUE and -DNAME:TYPE=VALUE) and CMake's own -W words. Never: what makes CMake
# load code or another toolchain (a project include, a toolchain file, CMAKE_MODULE_PATH, a rules override, -C, -P,
# a preset), the source and build folders (-S, -B: the plan's), what only llmcf's dependency cache sets
# (FETCHCONTENT_*), and CMAKE_PREFIX_PATH outside the build container. A valid CMake option that is not on the
# allowlist is no refusal: it is the person's decision (verdict "decision", with what it does), made per module
# version and flag set (Core/module_flags.py) - an AI-started build stops with needs_person.

# Each dependency of the pinned llama.cpp that llmcf can fetch before the build, by the option that needs it
# (orchestrator/runtime/build/deps.py DEPENDENCIES holds the pins; tests/test_build_deps.py keeps the two equal).
DEPENDENCY_OF_OPTION: Mapping[str, str] = {
    "LLAMA_BUILD_BORINGSSL": "BORINGSSL",
    "LLAMA_BUILD_LIBRESSL": "LIBRESSL",
    "GGML_CPU_KLEIDIAI": "KLEIDIAI",
}

# Variables that make CMake load code or pick another toolchain, beside SCRIPT_VARIABLE_RE (cmake-variables(7)).
CODE_VARIABLES: Mapping[str, str] = {
    "CMAKE_TOOLCHAIN_FILE": "names a module toolchain file; the build plan's toolchain file for the board is the one "
                            "the build uses",
    "CMAKE_MODULE_PATH": "makes CMake load CMake modules from other folders, which can set any option",
    "CMAKE_USER_MAKE_RULES_OVERRIDE": "makes CMake run a CMake file, which can set any option",
}
_RULES_OVERRIDE_RE = re.compile(r"^CMAKE_USER_MAKE_RULES_OVERRIDE(?:_[A-Za-z]+)?$")
# Variables that make CMake or the build run another program: a compiler or linker launcher, a code checker run with
# every compile, the emulator of a cross build (try_run, custom commands) and the build tool configure runs for its
# checks (cmake-variables(7)). Like a script, such a program can change what is built.
PROGRAM_VARIABLE_RE = re.compile(r"^CMAKE_(?:[A-Za-z0-9]+_)?(?:COMPILER_LAUNCHER|LINKER_LAUNCHER|CLANG_TIDY|CPPCHECK|"
                                 r"CPPLINT|INCLUDE_WHAT_YOU_USE|ICSTAT)$|^CMAKE_LINK_WHAT_YOU_USE_CHECK$|"
                                 r"^CMAKE_CROSSCOMPILING_EMULATOR$|^CMAKE_MAKE_PROGRAM$")
# CMake's own warning switches (cmake(1) -W...): the only -W words; a compiler warning belongs in the C flags.
CMAKE_WARNING_RE = re.compile(r"^-W(?:no-)?(?:error=)?(?:dev|deprecated)$")
# Switches that take the next word as their value when it is not attached (-G Ninja, -GNinja).
_PAIRED = ("-D", "-U", "-G", "-T", "-A", "-C", "-P", "-S", "-B")
# Valid CMake switches outside the allowlist: what each does, in the person's words (the warning explains it).
DECISION_SWITCHES: Mapping[str, str] = {
    "-G": "chooses another build tool (generator) instead of the plan's; the programs are the same, only the way "
          "they are built changes, and the image must have that tool",
    "-T": "chooses a toolset of the generator (Visual Studio, Xcode); on Linux builds it has no use",
    "-A": "chooses a platform of the generator (Visual Studio); on Linux builds it has no use",
    "-U": "removes entries from the CMake cache; the build starts from a fresh cache anyway",
    "--fresh": "starts from a fresh CMake cache; the build does that anyway",
    "--log-level": "changes how much CMake prints while it configures; the programs stay the same",
    "--log-context": "adds context to CMake's messages; the programs stay the same",
    "--debug-output": "makes CMake print debug output; the programs stay the same",
    "--debug-find": "makes CMake print how it looks for packages; the programs stay the same",
    "--trace": "makes CMake print every line it runs (a very long log); the programs stay the same",
    "--trace-expand": "makes CMake print every line it runs with its variables expanded (a very long log)",
    "--warn-uninitialized": "warns about variables used before they are set; the programs stay the same",
    "--no-warn-unused-cli": "hides the warning about command-line variables nobody reads",
    "--check-system-vars": "also warns about problems in the system's CMake files",
    "--compile-no-warning-as-error": "ignores COMPILE_WARNING_AS_ERROR of the project",
    "--install-prefix": "changes the install folder; the package takes the programs from the build folder, so it has "
                        "no effect on the package",
}
# Valid CMake switches that are never allowed in a module's flags.
REFUSED_SWITCHES: Mapping[str, str] = {
    "-C": "CMake runs a script with -C or -P, and a script can set any option",
    "-P": "CMake runs a script with -C or -P, and a script can set any option",
    "-S": "-S and -B choose the source and build folders, which the build plan sets",
    "-B": "-S and -B choose the source and build folders, which the build plan sets",
    "--preset": "a preset file can set any option and a toolchain",
    "--toolchain": "names a module toolchain file; the build plan's toolchain file for the board is the one the build "
                   "uses",
    "--build": "runs another CMake mode, not a configure option",
    "--install": "runs another CMake mode, not a configure option",
    "--open": "runs another CMake mode, not a configure option",
    "-E": "runs another CMake mode, not a configure option",
    "-N": "configures nothing (view mode)",
    "--graphviz": "writes files outside the build folder",
    "--trace-redirect": "writes files outside the build folder",
    "--profiling-output": "writes files outside the build folder",
    "--profiling-format": "writes files outside the build folder",
    "--debugger": "waits for a debugger",
    "--debugger-pipe": "waits for a debugger",
}
FLAG_CONTEXTS = ("module", "package", "call")
_RE_CONTAINER_PATHS = re.compile(r"^/[A-Za-z0-9._+/-]{0,255}$", re.ASCII)


@dataclass(frozen=True)
class FlagVerdict:
    """One CMake option of a module's flags as cmake reads it. `word`: the option as it is shown (-D NAME=V joined to
    -DNAME=V, -G Ninja with its value), `words`: the words it came from, `verdict`: allowed, refused or decision
    (valid, outside the allowlist: the person decides), `reason`: why (refused) or what it does (decision), `kind`:
    download (it would let llama.cpp download, or run a script that can), rule (another refusal), or ""."""
    word: str
    words: Tuple[str, ...]
    verdict: str
    reason: str = ""
    kind: str = ""
    name: str = ""


def _pairs(words: Sequence[str]) -> List[Tuple[str, ...]]:
    out: List[Tuple[str, ...]] = []
    items = [str(w) for w in words]
    i = 0
    while i < len(items):
        word = items[i]
        if word in _PAIRED and i + 1 < len(items):
            out.append((word, items[i + 1]))
            i += 2
            continue
        out.append((word,))
        i += 1
    return out


def _d_verdict(words: Tuple[str, ...], context: str, prefetched: Sequence[str]) -> FlagVerdict:
    word = "-D" + words[1] if len(words) == 2 else words[0]
    body = word[2:]
    if "=" not in body:
        return FlagVerdict(word, words, "refused", "not -DNAME=VALUE", "rule")
    name_type, _, value = body.partition("=")
    name = name_type.split(":", 1)[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", name):
        return FlagVerdict(word, words, "refused", "not -DNAME=VALUE", "rule", name)
    dependency = DEPENDENCY_OF_OPTION.get(name)
    if name in NEVER_SET and not (dependency and dependency in prefetched):
        return FlagVerdict(word, words, "refused", f"{name} downloads at configure time and is never set",
                           "download", name)
    if SCRIPT_VARIABLE_RE.match(name):
        return FlagVerdict(word, words, "refused", f"{name} makes CMake run a CMake script, which can set any option",
                           "download", name)
    if context == "call":
        problem = None if (dependency and dependency in prefetched) else refused_option(name, value)
        if problem:
            return FlagVerdict(word, words, "refused", problem, "download", name)
        return FlagVerdict(word, words, "allowed", name=name)
    if name in CODE_VARIABLES or _RULES_OVERRIDE_RE.match(name):
        reason = CODE_VARIABLES.get(name) or CODE_VARIABLES["CMAKE_USER_MAKE_RULES_OVERRIDE"]
        return FlagVerdict(word, words, "refused", f"{name} {reason}", "rule", name)
    if PROGRAM_VARIABLE_RE.match(name):
        return FlagVerdict(word, words, "refused", f"{name} makes CMake or the build run another program, which can "
                           "change what is built", "rule", name)
    if name.startswith("FETCHCONTENT_") and name != "FETCHCONTENT_FULLY_DISCONNECTED":
        return FlagVerdict(word, words, "refused", f"{name} is set only by llmcf's dependency cache", "rule", name)
    if name == "CMAKE_PREFIX_PATH":
        parts = [p for p in value.split(";") if p]   # as the shell's last check reads it: a quote is no path
        if not parts or not all(_RE_CONTAINER_PATHS.fullmatch(p) and ".." not in p.split("/") for p in parts):
            return FlagVerdict(word, words, "refused", "CMAKE_PREFIX_PATH names only folders inside the build "
                               "container (absolute paths)", "rule", name)
        return FlagVerdict(word, words, "allowed", name=name)
    if dependency and dependency in prefetched:
        return FlagVerdict(word, words, "allowed", name=name)
    problem = refused_option(name, value)
    if problem and context == "module":
        return FlagVerdict(word, words, "refused", problem, "download", name)
    return FlagVerdict(word, words, "allowed", name=name)


def cmake_flag_verdicts(words: Sequence[str], *, context: str = "module",
                        prefetched: Sequence[str] = ()) -> List[FlagVerdict]:
    """The verdict of each CMake option in `words` (a module's CMake flags, split as the shell splits them).
    `context`: "module" (the flags as the generator, the folder check, the build plan and the AI's facts judge them:
    an option every build keeps OFF must be OFF there), "package" (the same flags in the package's cross build, where
    the options of every build come after them and win), "call" (a cmake call of a module's own scripts, build.sh's
    cmake: only what lets llama.cpp download or runs a script is refused, as before). `prefetched`: the dependencies
    the framework fetched for this build and mounts read-only (deps.py); their options are allowed."""
    if context not in FLAG_CONTEXTS:
        raise ValueError(f"unknown context {context!r}")
    out: List[FlagVerdict] = []
    for pair in _pairs(words):
        first = pair[0]
        if first.startswith("-D"):
            out.append(_d_verdict(pair, context, prefetched))
            continue
        if first.startswith(("-C", "-P")):
            out.append(FlagVerdict(first, pair, "refused", REFUSED_SWITCHES["-C"], "download"))
            continue
        if context == "call":
            out.append(FlagVerdict(" ".join(pair), pair, "allowed"))
            continue
        shown = " ".join(pair)
        if first.startswith("-W"):
            if CMAKE_WARNING_RE.fullmatch(first) and len(pair) == 1:
                out.append(FlagVerdict(first, pair, "allowed"))
            else:
                out.append(FlagVerdict(shown, pair, "refused", "not a CMake warning option (a compiler warning "
                                       "belongs in the C flags)", "rule"))
            continue
        key = first.split("=", 1)[0]
        short = first[:2] if len(first) > 2 and first[:2] in _PAIRED and not first.startswith("--") else None
        if key in REFUSED_SWITCHES or (short and short in REFUSED_SWITCHES):
            out.append(FlagVerdict(shown, pair, "refused", REFUSED_SWITCHES.get(key) or REFUSED_SWITCHES[short],
                                   "rule"))
        elif key in DECISION_SWITCHES or (short and short in DECISION_SWITCHES):
            out.append(FlagVerdict(shown, pair, "decision", DECISION_SWITCHES.get(key) or DECISION_SWITCHES[short]))
        else:
            out.append(FlagVerdict(shown, pair, "refused", "not a CMake option the framework knows", "rule"))
    return out


def module_flag_refusals(words: Sequence[str], *, context: str = "module", prefetched: Sequence[str] = ()) -> List[str]:
    """`word (reason)` for each refused option of `words` (cmake_flag_verdicts), empty when none is refused."""
    return [f"{v.word} ({v.reason})" for v in cmake_flag_verdicts(words, context=context, prefetched=prefetched)
            if v.verdict == "refused"]


def module_flag_decisions(words: Sequence[str], *, prefetched: Sequence[str] = ()) -> List[FlagVerdict]:
    """The options of `words` that are valid but outside the allowlist: the person's decision."""
    return [v for v in cmake_flag_verdicts(words, prefetched=prefetched) if v.verdict == "decision"]


# --- profile facts ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Target:
    os: str
    arch: str
    libc: Optional[str]
    libc_version: Optional[str]


def _flag(profile: HardwareProfile, key: str) -> str:
    value = profile.flag(key)
    return value if value in ("ON", "OFF", "UNKNOWN") else "UNKNOWN"


def unplanned_features(profile: HardwareProfile) -> str:
    """For a machine of an architecture the recipe does not build (riscv64, loongarch64, ppc64le, s390x): the CPU
    features its probe found and the ggml option each would set (ggml_cpu_options.py), so the refusal loses none of
    them; empty when the probe found none."""
    found = []
    for row in _cpu_options.rows(profile.arch or ""):
        if row.probe_key is None or row.kind == _cpu_options.POWER_CPU_TYPE:
            continue
        value = profile.get(row.probe_key)
        if row.probe_key == "POWER_CPU_TYPE":
            if value:
                found.append(f"{value} ({row.name}={value})")
        elif _flag(profile, row.probe_key) == "ON":
            found.append(f"{_cpu_options.feature_label(row.probe_key)} ({row.name}: {row.flag})")
    return f"; the probe found these CPU features: {', '.join(found)}" if found else ""


def _target_of(profile: HardwareProfile, purpose: str, placement: str, toolchain: ToolchainInfo) -> _Target:
    os_name, arch = profile.os, profile.arch
    if os_name not in _SUPPORTED_OS:
        raise PlanError(f"the recipe has no plan for OS {os_name!r} yet (supported: linux, windows, macos); "
                        "a build on the device itself is not offered for it either")
    if arch not in _SUPPORTED_ARCH:
        raise PlanError(f"the recipe has no plan for architecture {arch!r} yet (supported: x86_64, arm64)"
                        + unplanned_features(profile))
    if purpose == "host-tools" and placement == "container":
        # The tools run inside the target's toolchain image (a Linux container) on this machine's CPU.
        return _Target("linux", arch, toolchain.base_libc, None)
    libc = profile.libc
    libc_version = profile.libc_version
    if libc is not None and libc not in _LIBC_OF_OS[os_name]:
        raise PlanError(f"the recipe has no plan for C library {libc!r} on {os_name} yet "
                        f"(supported: {', '.join(_LIBC_OF_OS[os_name])})")
    if libc_version is not None and not _RE_LIBC_VERSION.fullmatch(libc_version):
        raise PlanError("LIBC_VERSION is not a version number")
    return _Target(os_name, arch, libc, libc_version)


def _check_placement(target: _Target, purpose: str, placement: str, toolchain: ToolchainInfo) -> None:
    host = f"{toolchain.host_os}/{toolchain.host_arch}"
    machine = f"{target.os}/{target.arch}"
    if placement in ("native", "on-device"):
        if (toolchain.host_os, toolchain.host_arch) != (target.os, target.arch):
            raise PlanError(f"a {placement} build runs on the machine itself, but the build host is {host} and the "
                            f"profile describes {machine}")
        return
    if placement == "container":
        if target.os != "linux":
            raise PlanError("container builds produce Linux binaries; this profile describes " + machine)
        if target.arch != toolchain.host_arch:
            raise PlanError(f"a container build runs on the build host ({host}); for {machine} use a cross build")
        if purpose != "host-tools" and toolchain.host_os != "linux":
            raise PlanError("the host runtime from a container build runs on Linux only; on "
                            f"{toolchain.host_os} build it natively")
        if purpose == "device-runtime":
            raise PlanError("a device runtime is built natively on the device or as a cross build")
        return
    # cross: built on the build host for another machine
    if purpose != "device-runtime":
        raise PlanError("a cross build is for another machine: purpose device-runtime")
    if target.os == "linux":
        return  # in the builder container, on any host that runs Linux containers
    if target.os == "windows":
        if toolchain.host_os != "windows" or toolchain.host_arch != target.arch:
            raise PlanError("Windows binaries are built on Windows: build on that PC (native) or on another "
                            f"Windows PC with the same architecture ({target.arch}) and import the build")
        return
    raise PlanError(f"{target.os} binaries are only built on the machine itself (placement native)")


def _leaves_userland(target: _Target, purpose: str, placement: str) -> bool:
    """True when the binaries run outside the userland they were built in (cross, or a container build whose
    binaries run on the host): they then need a compatible C library and carry their C++ runtime."""
    return target.os == "linux" and (placement == "cross" or (placement == "container" and purpose != "host-tools"))


# --- backend choice ---------------------------------------------------------------------------------------------

def _nvidia_caps(profile: HardwareProfile) -> List[str]:
    caps = []
    for gpu in profile.gpus():
        if gpu.get("VENDOR") != "nvidia":
            continue
        cap = gpu.get("COMPUTE_CAP")
        if cap is not None:
            if not _RE_COMPUTE_CAP.fullmatch(cap):
                raise PlanError("a GPU<n>_COMPUTE_CAP value is not major.minor")
            caps.append(cap)
    return sorted(set(caps), key=version_tuple)


def _missing_caps(profile: HardwareProfile) -> List[str]:
    """GPU<n>_COMPUTE_CAP keys an NVIDIA GPU lacks. A -real build has no kernel for a GPU it was not built for, so a
    CUDA build needs the capability of every NVIDIA GPU, not just of some."""
    return [f"GPU{n}_COMPUTE_CAP" for n, gpu in enumerate(profile.gpus())
            if gpu.get("VENDOR") == "nvidia" and gpu.get("COMPUTE_CAP") is None]


def _missing_caps_message(missing: Sequence[str]) -> str:
    return (f"CUDA needs the compute capability of every NVIDIA GPU, and {', '.join(missing)} is missing: probe the "
            "machine again with the NVIDIA driver loaded")


def _vulkan_worthy(gpu: Mapping[str, str]) -> bool:
    """GPUs on which llama.cpp's Vulkan build beats the CPU (as hardware.vulkan_worthy): dedicated GPUs, AMD Radeon
    APUs, Intel Arc. Mali on boards only after measurement (decision D8). The name is used for this decision only."""
    vendor = gpu.get("VENDOR")
    if vendor == "arm":
        return False
    if gpu.get("DISCRETE") == "ON" or vendor == "amd":
        return True
    return vendor == "intel" and "arc" in (gpu.get("NAME") or "").lower()


def choose_backend(profile: HardwareProfile, *, purpose: str, placement: str, catalog: RuntimeCatalog,
                   toolchain: ToolchainInfo) -> Tuple[str, str]:
    """(backend, reason) for backend="auto": Metal on Apple Silicon, CUDA when a pinned toolkit fits every NVIDIA GPU,
    Vulkan for GPUs where it beats the CPU, else the CPU. ROCm only when asked for; Vulkan on boards only after
    llama-bench showed it beats the CPU build (D8); conversion tools are CPU builds."""
    if purpose == "host-tools":
        return "cpu", "conversion tools are CPU builds"
    target = _target_of(profile, purpose, placement, toolchain)
    if target.os == "macos" and target.arch == "arm64" and profile.flag("METAL") != "OFF":
        return "metal", "Apple Silicon: Metal"
    notes = []
    if any(g.get("VENDOR") == "nvidia" for g in profile.gpus()):
        missing = _missing_caps(profile)
        if placement == "cross":
            notes.append("CUDA cross builds are not offered")
        elif missing:
            notes.append(_missing_caps_message(missing))
        else:
            try:
                base = _base_for(target, purpose, placement, catalog, toolchain)
                toolkit, why = choose_cuda_toolkit(profile, catalog, toolchain, placement=placement,
                                                   target_os=target.os, base=base)
            except PlanError as e:
                toolkit, why = None, [str(e)]
            if toolkit is not None:
                return "cuda", f"NVIDIA GPU(s), compute {', '.join(_nvidia_caps(profile))}: CUDA {toolkit.version}"
            notes.append("no pinned CUDA toolkit fits: " + "; ".join(why))
    if profile.flag("VULKAN") == "ON" and any(_vulkan_worthy(g) for g in profile.gpus()):
        return "vulkan", "; ".join(notes + ["a GPU on which the Vulkan build beats the CPU"])
    if any(g.get("VENDOR") == "arm" for g in profile.gpus()):
        notes.append("Vulkan on Mali only after llama-bench shows it beats the CPU build (decision D8)")
    return "cpu", "; ".join(notes + ["CPU build"])


# --- CUDA toolkit ------------------------------------------------------------------------------------------------

def _cuda_platform(target_os: str, arch: str) -> str:
    return {("linux", "x86_64"): "linux-x86_64", ("linux", "arm64"): "linux-sbsa",
            ("windows", "x86_64"): "windows-x86_64", ("windows", "arm64"): "windows-arm64"}.get((target_os, arch), "")


def _in_range(version: str, bounds: Tuple[str, str], parts: int) -> bool:
    v = (version_tuple(version) or (0,))[:parts]
    lo = (version_tuple(bounds[0]) or (0,))[:parts]
    hi = (version_tuple(bounds[1]) or (0,))[:parts]
    return lo <= v <= hi


def cuda_toolkit_problems(toolkit: CudaToolkit, profile: HardwareProfile, catalog: RuntimeCatalog, *,
                          target_os: str, host_compiler: Optional[Tuple[str, str]], cmake: Optional[str],
                          msvc: Optional[str]) -> List[str]:
    """Why the toolkit does not fit (empty: it fits). The four constraints of design 3.5: the driver's CUDA version
    (the clamp of decision D6), every NVIDIA GPU's compute capability, the host compiler, CMake."""
    problems = []
    arch = profile.arch or ""
    if _cuda_platform(target_os, arch) not in toolkit.platforms:
        problems.append(f"CUDA {toolkit.version} has no {target_os}/{arch} archives")
    if target_os == "linux" and profile.libc not in (None, "glibc"):
        other = "musl" if profile.libc == "musl" else "this machine's C library"  # no raw probe text in messages
        problems.append(f"NVIDIA's CUDA toolkit is built for glibc, not {other} (decision 8: the build follows the "
                        "machine's C library)")
    missing = _missing_caps(profile)
    if missing:
        problems.append(_missing_caps_message(missing))
    cuda_max = profile.get("NVIDIA_DRIVER_CUDA_MAX")
    if cuda_max is None:
        problems.append("the driver's CUDA version is unknown (NVIDIA_DRIVER_CUDA_MAX): probe again with the NVIDIA "
                        "driver loaded")
    elif not _RE_CUDA_MAX.fullmatch(cuda_max):
        problems.append("NVIDIA_DRIVER_CUDA_MAX is not a version")
    elif version_tuple(toolkit.major_minor) > version_tuple(cuda_max):
        problems.append(f"CUDA {toolkit.version} is newer than the driver supports (CUDA {cuda_max}); a newer "
                        "driver allows it")
    for cap in _nvidia_caps(profile):
        if not version_tuple(toolkit.compute_caps.min) <= version_tuple(cap) <= version_tuple(toolkit.compute_caps.max):
            problems.append(f"CUDA {toolkit.version} supports compute capability {toolkit.compute_caps.min} to "
                            f"{toolkit.compute_caps.max}, not {cap}")
    if target_os == "windows":
        bounds = toolkit.host_compilers.msvc
        if msvc and bounds and not _in_range(msvc, bounds, 2):
            problems.append(f"CUDA {toolkit.version} accepts MSVC {bounds[0]} to {bounds[1]}, not {msvc}")
    elif host_compiler is not None:
        family, version = host_compiler
        bounds = getattr(toolkit.host_compilers, family, None) if family in ("gcc", "clang") else None
        if bounds and not _in_range(version, bounds, 1):
            problems.append(f"CUDA {toolkit.version} accepts {family} {bounds[0]} to {bounds[1]}, not {version}")
    if cmake and catalog.cuda and version_tuple(cmake) < version_tuple(catalog.cuda.cmake_min):
        problems.append(f"CUDA builds need CMake {catalog.cuda.cmake_min} or newer, not {cmake}")
    return problems


def _host_compiler(toolchain: ToolchainInfo, base: Optional[BuildBase]) -> Optional[Tuple[str, str]]:
    if base is not None:
        return ("gcc", base.gcc)
    return toolchain.compiler_id()


def choose_cuda_toolkit(profile: HardwareProfile, catalog: RuntimeCatalog, toolchain: ToolchainInfo, *,
                        placement: str = "native", target_os: Optional[str] = None,
                        base: Optional[BuildBase] = None) -> Tuple[Optional[CudaToolkit], List[str]]:
    """The newest pinned toolkit that meets all four constraints, and the reasons the newer ones were skipped. A
    toolkit named by the toolchain (found or provisioned) must be a pinned one and must fit."""
    if catalog.cuda is None or not catalog.cuda.toolkits:
        return None, ["the catalog lists no CUDA toolkit"]
    target_os = target_os or profile.os or ""
    compiler = _host_compiler(toolchain, base)
    wanted = toolchain.cuda
    if toolchain.cuda_image:
        image = next((i for i in catalog.cuda.prebuilt_images if i.id == toolchain.cuda_image), None)
        if image is None:
            return None, [f"prebuilt CUDA image {toolchain.cuda_image!r} is not in the catalog"]
        wanted = image.cuda
    candidates = sorted(catalog.cuda.toolkits, key=lambda t: version_tuple(t.version), reverse=True)
    if wanted is not None:
        candidates = [t for t in candidates if t.version == wanted]
        if not candidates:
            return None, [f"CUDA {wanted} is not a pinned toolkit of the catalog"]
    skipped: List[str] = []
    for toolkit in candidates:
        problems = cuda_toolkit_problems(toolkit, profile, catalog, target_os=target_os, host_compiler=compiler,
                                         cmake=toolchain.cmake, msvc=toolchain.msvc)
        if not problems:
            return toolkit, skipped
        skipped.append(problems[0])
    return None, skipped


# --- builder base ------------------------------------------------------------------------------------------------

def choose_build_base(catalog: RuntimeCatalog, libc: Optional[str], libc_version: Optional[str]) -> BuildBase:
    """The builder base for a Linux target: the newest base whose C library (glibc or musl) is not newer than the
    target's. A target without LIBC_VERSION, or with an older C library than every base, is refused."""
    if libc is None:
        raise PlanError("the profile has no LIBC: probe the machine again before a build for it")
    bases = [b for b in catalog.build_bases if b.libc == libc]
    if not bases:
        raise PlanError(f"the catalog has no builder base for {libc}")
    bases.sort(key=lambda b: version_tuple(b.libc_version), reverse=True)
    if libc_version is None:
        # Required for glibc and musl alike (design 2.4, 3.10): a binary linked against a newer musl can need symbols
        # an older one lacks, so the newest base is no safe guess.
        raise PlanError(f"the profile has no LIBC_VERSION; the builder base must not have a newer {libc} than the "
                        "machine: probe it again before a build for it")
    fitting = [b for b in bases if version_tuple(b.libc_version) <= version_tuple(libc_version)]
    if not fitting:
        oldest = bases[-1]
        raise PlanError(f"{libc} {libc_version} on the machine is older than every builder base (oldest: "
                        f"{oldest.id} with {oldest.libc_version}); upgrade its OS or build on the device itself")
    return fitting[0]


def _base_for(target: _Target, purpose: str, placement: str, catalog: RuntimeCatalog,
              toolchain: ToolchainInfo) -> Optional[BuildBase]:
    """The builder base of a container or cross build for a Linux machine (None: built natively or in the target's
    toolchain image, or in an opted-in prebuilt CUDA image)."""
    if placement in ("native", "on-device") or target.os != "linux" or purpose == "host-tools" or toolchain.cuda_image:
        return None
    return choose_build_base(catalog, target.libc, target.libc_version)


# --- options ----------------------------------------------------------------------------------------------------

def sme_problem(compiler: Optional[Tuple[str, Optional[str]]]) -> Optional[str]:
    """Why the compiler (family, version) cannot build +sme, None when it can (SME_COMPILER_MIN: GCC 14, Clang 18)."""
    family, version = compiler if compiler is not None else ("", None)
    minimum = SME_COMPILER_MIN.get(family)
    need = (f"GCC {'.'.join(map(str, SME_COMPILER_MIN['gcc']))} or Clang "
            f"{'.'.join(map(str, SME_COMPILER_MIN['clang']))}")
    if minimum is None or version is None:
        return f"compiler too old or not known: +sme needs {need}, and the build's compiler version is not known"
    if (version_tuple(version) or (0,))[:len(minimum)] < minimum:
        name = "GCC" if family == "gcc" else "Clang"
        return f"compiler too old: +sme needs {need}, and the build compiles with {name} {version}"
    return None


def arm_plan(profile: HardwareProfile, compiler: Optional[Tuple[str, Optional[str]]] = None
             ) -> Tuple[str, List[str], List[Dict[str, str]]]:
    """(GGML_CPU_ARM_ARCH, the features it uses, the features the board has that the build cannot use, each with the
    reason) from the probe. The one ARM planner; the targets' config_module.sh mirrors it (tests compare them).

    armv8.2-a plus the tags of ARM_ARCH_TAGS in ggml's ARCH_TAGS order (+dotprod +fp16 +sve +i8mm +sve2 +sme) for
    the features that are ON, armv8-a when none is (RK3566/RK3568/RK3588: armv8.2-a+dotprod+fp16). UNKNOWN counts as
    OFF, so the plan never asks for more than the probe confirmed. +sme only with a compiler of SME_COMPILER_MIN
    (`compiler`: (family, version); None when not known): else SME is named as unusable and the rest is built. BF16
    has no kernel for ARM at the pin (ARM_UNUSABLE). A tag whose base (ARM_NEEDS) is not planned is named as unusable
    too, so an inconsistent probe file cannot add +sve2 without +sve or +i8mm on an armv8.0 board."""
    tags: List[str] = []
    used: List[str] = []
    planned: List[str] = []
    unusable: List[Dict[str, str]] = []
    for key, tag in ARM_ARCH_TAGS:
        if _flag(profile, key) != "ON":
            continue
        base = ARM_NEEDS.get(key)
        if base and base not in planned:
            unusable.append({"feature": _cpu_options.feature_label(key),
                             "reason": f"builds on {_cpu_options.feature_label(base)}, which the build does not use"})
            continue
        problem = sme_problem(compiler) if tag == "sme" else None
        if problem:
            unusable.append({"feature": _cpu_options.feature_label(key), "reason": problem})
            continue
        tags.append(tag)
        planned.append(key)
        used.append(_cpu_options.feature_label(key))
    for key, reason in ARM_UNUSABLE.items():
        if _flag(profile, key) == "ON":
            unusable.append({"feature": _cpu_options.feature_label(key), "reason": reason})
    march = "armv8.2-a" + "".join(f"+{t}" for t in tags) if tags else "armv8-a"
    return march, used, unusable


def arm_arch(profile: HardwareProfile, compiler: Optional[Tuple[str, Optional[str]]] = None) -> str:
    """GGML_CPU_ARM_ARCH from the probe (arm_plan); without a compiler that knows +sme, SME is left out."""
    return arm_plan(profile, compiler)[0]


def _variants_supported(target: _Target) -> bool:
    """GGML_CPU_ALL_VARIANTS at the pin: x86 on every OS, ARM on Linux (and Android, Apple) only
    (ggml/src/CMakeLists.txt:487-584)."""
    return target.arch == "x86_64" or (target.arch == "arm64" and target.os in ("linux", "macos"))


def _cpu_mode(profile: HardwareProfile, target: _Target, placement: str, backend_dl: bool,
              purpose: str = "host-runtime") -> str:
    """native: GGML_NATIVE on the machine that runs the binaries (a container build runs on the build host's CPU).
    explicit: the probe's features. variants: all CPU variants, chosen at run time, when the probe could not tell.

    Conversion tools are compiled inside the image build of the target's toolchain image (design 4.3). They run on
    the build host, but get its CPU features as explicit options: the options are then part of the image build's
    arguments, so the layer cache and the plan say what the tools were built for, and nothing depends on what
    -march=native gives inside the image build."""
    on_machine = placement in ("native", "on-device") or (placement == "container" and purpose != "host-tools")
    if on_machine and not (backend_dl and target.arch == "x86_64"):
        return "native"  # GGML_NATIVE is not compatible with GGML_BACKEND_DL on x86 (ggml-cpu/CMakeLists.txt:413)
    features = X86_FEATURES if target.arch == "x86_64" else ARM_FEATURES
    unknown = [k for k in features if _flag(profile, k) == "UNKNOWN"]
    if unknown and _variants_supported(target):
        return "variants"
    return "explicit"


def _variants_compiler(target: _Target, toolchain: ToolchainInfo,
                       base: Optional[BuildBase]) -> Optional[Tuple[str, Optional[str]]]:
    """(family, version) of the compiler that builds the CPU variants; the version is None when it is not known."""
    if base is not None:
        if target.libc == "musl" and target.arch != toolchain.host_arch:
            return ("clang", None)  # the Alpine builder's clang with the target's sysroot (_toolchain_file)
        return ("gcc", base.gcc)
    cid = toolchain.compiler_id()
    return (cid[0], cid[1]) if cid else None


def _check_arm_variants(compiler: Optional[Tuple[str, Optional[str]]], base: Optional[BuildBase],
                        notes: List[str]) -> None:
    """Upstream's ARM CPU variants include armv9.2-a+sme: a compiler known to be too old for it is refused."""
    family, version = compiler if compiler is not None else ("", None)
    minimum = SME_COMPILER_MIN.get(family)
    need = (f"GCC {'.'.join(map(str, SME_COMPILER_MIN['gcc']))} or Clang "
            f"{'.'.join(map(str, SME_COMPILER_MIN['clang']))}")
    if minimum is None or version is None:
        notes.append(f"upstream's ARM CPU variants include armv9.2-a+sme, which needs {need}; the compiler version is "
                     "not known (unverified)")
        return
    if (version_tuple(version) or (0,))[:len(minimum)] < minimum:
        where = f"the builder base {base.id} has" if base is not None else "the build machine has"
        name = "GCC" if family == "gcc" else "Clang"
        raise PlanError(f"the probe could not tell this machine's CPU features, so all of upstream's ARM CPU variants "
                        f"would be built, and they include armv9.2-a+sme, which needs {need}; {where} {name} "
                        f"{version}. Probe the machine again with hardware_probe.py or hardware_probe.sh (both read "
                        "its CPU features), and the build uses exactly those")


def avx512_missing(cpu_flags: Sequence[str]) -> List[str]:
    """The parts of AVX512_SET (what GGML_AVX512 compiles for) that the CPU flags do not name, in that order. Empty:
    GGML_AVX512 may be used. Every AVX-512 CPU since Skylake-X has them all; a Xeon Phi (Knights Landing, Knights
    Mill) has AVX-512 F and CD without BW, VL and DQ, and a probe without CPU flags (Windows) cannot tell."""
    have = {str(flag).strip().lower() for flag in cpu_flags}
    return [part for part in AVX512_SET if part not in have]


def _x86_options(profile: HardwareProfile, notes: List[str],
                 unusable: Optional[List[Dict[str, str]]] = None) -> Tuple[Dict[str, str], List[str]]:
    """The 14 x86 options and their compiler flags for a build for another machine. A feature the probe confirms that
    the build cannot use goes into `unusable` with the reason (and a note)."""
    dropped: List[Dict[str, str]] = unusable if unusable is not None else []
    state = {key: _flag(profile, key) == "ON" for key in X86_FEATURES}
    missing = avx512_missing(profile.items_of("CPU_FLAGS_ALL")) if state["SUPPORTS_AVX512"] else []
    if missing:
        state["SUPPORTS_AVX512"] = False
        notes.append(f"SUPPORTS_AVX512 is ON, but GGML_AVX512 also compiles for {', '.join(missing)}, which the "
                     "probe's CPU flags do not name (a Xeon Phi has AVX-512 F without BW, VL and DQ; a probe without "
                     "CPU flags cannot tell): the AVX2 plan, no AVX-512")
        dropped.append({"feature": "AVX512", "reason": f"GGML_AVX512 also compiles for {', '.join(missing)}, which "
                                                       "the probe's CPU flags do not name"})
    for key in X86_FEATURES:  # dependents follow their base (in table order, so chains resolve)
        base = _X86_NEEDS.get(key)
        if base and state[key] and not state[base]:
            state[key] = False
            notes.append(f"{key} is ON without {base} in the probe; not used")
            dropped.append({"feature": _cpu_options.feature_label(key),
                            "reason": f"builds on {_cpu_options.feature_label(base)}, which the build does not use"})
    options = {X86_FEATURES[k]: ("ON" if on else "OFF") for k, on in state.items()}
    flags = [f for opt, opt_flags in _X86_FLAGS.items() if options[opt] == "ON" for f in opt_flags]  # ggml order
    if state["SUPPORTS_AVX512"]:
        notes.append("GGML_AVX512 also enables AVX-512 CD, VL, DQ and BW (every AVX-512 CPU since Skylake-X)")
    return options, flags


def cpu_features_of(profile: HardwareProfile, arch: str, options: Mapping[str, str],
                    unusable: Sequence[Mapping[str, str]] = ()) -> Dict[str, Any]:
    """The plan's cpu_features: the probe's features the options use, and `unusable` (the features the machine has
    that the build cannot use, with the reason). For GGML_NATIVE and the CPU variants every confirmed feature of the
    architecture counts as used (the compiler or the run time picks them), except those no code of the pin uses."""
    skip = {u["feature"] for u in unusable}
    if arch == "x86_64":
        keys = list(X86_FEATURES)
        if options.get("GGML_NATIVE") == "ON" or options.get("GGML_CPU_ALL_VARIANTS") == "ON":
            used = [k for k in keys if _flag(profile, k) == "ON"]
        else:
            used = [k for k, opt in X86_FEATURES.items() if options.get(opt) == "ON"]
    elif arch == "arm64":
        keys = [k for k, _tag in ARM_ARCH_TAGS]
        if "GGML_CPU_ARM_ARCH" in options:
            tags = options["GGML_CPU_ARM_ARCH"].split("+")[1:]
            used = [k for k, tag in ARM_ARCH_TAGS if tag in tags]
        else:
            used = [k for k in keys if _flag(profile, k) == "ON"]
    else:
        used = []
    labels = [_cpu_options.feature_label(k) for k in used]
    return {"used": [f for f in labels if f not in skip], "unusable": [dict(u) for u in unusable]}


def cpu_feature_lines(cpu_features: Mapping[str, Any]) -> List[str]:
    """The plan's cpu_features as the plan views show them (CLI, GUI, MCP; labels and reasons only)."""
    used = list(cpu_features.get("used") or [])
    lines = [f"CPU features used: {', '.join(used) if used else 'none beyond the base level'}"]
    lines += [f"The machine has {u['feature']}, the build cannot use it: {u['reason']}"
              for u in cpu_features.get("unusable") or []]
    return lines


def _every_build(source: Any, *, shared: bool, native: bool, ccache: bool) -> Dict[str, str]:
    options = {name: "OFF" for name in DOWNLOAD_OPTIONS_OFF}
    options.update({
        "CMAKE_BUILD_TYPE": "Release",
        "FETCHCONTENT_FULLY_DISCONNECTED": "ON",
        "LLAMA_BUILD_COMMON": "ON",
        "LLAMA_BUILD_TOOLS": "ON",
        "LLAMA_OPENSSL": "OFF",       # loopback server with a key file; also no server TLS (P9)
        "LLAMA_SUBPROCESS": "OFF",    # D22
        "LLAMA_BUILD_NUMBER": str(source.build_number),
        "LLAMA_BUILD_COMMIT": source.short_commit,
        "BUILD_SHARED_LIBS": "ON" if shared else "OFF",
        "GGML_NATIVE": "ON" if native else "OFF",
        "GGML_CCACHE": "ON" if ccache else "OFF",
    })
    return options


def _toolchain_file(target: _Target, toolchain: ToolchainInfo) -> Tuple[Optional[str], str, Optional[str]]:
    """(toolchain file text, compiler, triple) of a Linux build in the builder container."""
    if target.libc == "musl":
        triple = _MUSL_TRIPLE[target.arch]
        if target.arch == toolchain.host_arch:
            return None, "gcc", triple  # the Alpine base compiles for its own architecture
        lines = [
            "set(CMAKE_SYSTEM_NAME Linux)",
            f"set(CMAKE_SYSTEM_PROCESSOR {_CMAKE_PROCESSOR[target.arch]})",
            f"set(CMAKE_SYSROOT {_SYSROOT})",
            "set(CMAKE_C_COMPILER clang)",
            "set(CMAKE_CXX_COMPILER clang++)",
            f"set(CMAKE_C_COMPILER_TARGET {triple})",
            f"set(CMAKE_CXX_COMPILER_TARGET {triple})",
            "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)",
        ]
        return _file_text(lines), "clang", triple
    triple = _GNU_TRIPLE[target.arch]
    if target.arch == toolchain.host_arch:
        return None, "gcc", triple
    lines = [
        "set(CMAKE_SYSTEM_NAME Linux)",
        f"set(CMAKE_SYSTEM_PROCESSOR {_CMAKE_PROCESSOR[target.arch]})",
        f"set(CMAKE_C_COMPILER {triple}-gcc)",
        f"set(CMAKE_CXX_COMPILER {triple}-g++)",
        f"set(CMAKE_FIND_ROOT_PATH /usr/{triple})",
        "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)",
        "set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)",
        "set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)",
        "set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)",
    ]
    return _file_text(lines), "gcc", triple


def _file_text(lines: Sequence[str]) -> str:
    return "\n".join(["# Generated by the llama.cpp build recipe (orchestrator/runtime/build/recipe.py)."] +
                     list(lines)) + "\n"


def _cuda_architectures(caps: Sequence[str]) -> str:
    """CMAKE_CUDA_ARCHITECTURES: real code for exactly the GPUs of the machine (RTX 2080 Ti 75-real, RTX 3090
    86-real). 12.x is written as 12xa, as ggml rewrites it anyway (ggml/src/ggml-cuda/CMakeLists.txt:87-104)."""
    archs = []
    for cap in caps:
        major, minor = cap.split(".")
        suffix = "a" if major == "12" else ""
        archs.append(f"{int(major)}{int(minor)}{suffix}-real")
    return ";".join(archs)


def _gfx_targets(profile: HardwareProfile) -> List[str]:
    targets = []
    for n, gpu in enumerate(profile.gpus()):
        if gpu.get("VENDOR") != "amd":
            continue
        gfx = gpu.get("GFX_TARGET")
        if gfx is None:
            raise PlanError(f"GPU{n}_GFX_TARGET is missing: the HIP build needs the gfx target of every AMD GPU")
        if not _RE_GFX.fullmatch(gfx):
            raise PlanError(f"GPU{n}_GFX_TARGET is not a gfx target")
        targets.append(gfx)
    return sorted(set(targets))


def _cpu_identity(profile: HardwareProfile, target: _Target) -> Dict[str, Any]:
    """What GGML_NATIVE depends on, for the build ID of a native build (the native expansion, when known, too)."""
    features = X86_FEATURES if target.arch == "x86_64" else ARM_FEATURES
    keys = ("ARCH", "CPU_VENDOR", "CPU_MODEL", "CPU_CLUSTERS")
    out: Dict[str, Any] = {k: profile.get(k) for k in keys if profile.get(k) is not None}
    out["features"] = {k: _flag(profile, k) for k in features}
    return out


# --- the plan ----------------------------------------------------------------------------------------------------

def plan_build(profile: HardwareProfile, *, purpose: str, placement: str, backend: str = "auto",
               catalog: RuntimeCatalog, toolchain: ToolchainInfo) -> BuildPlan:
    """The build plan for the machine `profile` describes (the machine that runs the binaries). Raises PlanError."""
    if purpose not in PURPOSES:
        raise PlanError(f"purpose must be one of {', '.join(PURPOSES)}")
    if placement not in PLACEMENTS:
        raise PlanError(f"placement must be one of {', '.join(PLACEMENTS)}")
    if backend not in BACKENDS:
        raise PlanError(f"backend must be one of {', '.join(BACKENDS)}")
    if not isinstance(profile, HardwareProfile):
        raise PlanError("expected a parsed HardwareProfile")
    source = catalog.llama_cpp.source
    if source is None:
        raise PlanError("configs/llama_runtime.yml has no llama_cpp.source: there is no pinned commit to build")

    notes: List[str] = []
    target = _target_of(profile, purpose, placement, toolchain)
    _check_placement(target, purpose, placement, toolchain)
    if toolchain.cuda_image and placement in ("native", "on-device"):
        raise PlanError("a prebuilt NVIDIA image is only used for container builds")
    if profile.needs_reprobe and placement == "cross":
        notes.append("the profile is converted or incomplete; probe the machine again for a tuned build")

    if backend == "auto":
        backend, reason = choose_backend(profile, purpose=purpose, placement=placement, catalog=catalog,
                                         toolchain=toolchain)
        notes.append(f"backend {backend}: {reason}")
    if purpose == "host-tools" and backend != "cpu":
        raise PlanError("conversion tools are CPU builds here (GPU host tools are a later step)")

    # The builder: on the machine itself, in a builder container chosen by the target's C library, in the target's
    # toolchain image (conversion tools), or in a prebuilt NVIDIA image (opt-in, D6).
    base: Optional[BuildBase] = None
    builder: Dict[str, Any]
    if placement in ("native", "on-device") or target.os == "windows":
        builder = {"kind": "native"}
        if placement == "cross":
            builder["host"] = "another Windows PC"
        cid = toolchain.compiler_id()
        if target.os == "windows" and cid is not None and cid[0] != "clang":
            raise PlanError(f"Windows builds compile the CPU part with Clang (decision D9; MSVC only hosts nvcc), but "
                            f"the build machine reports {cid[0]} {cid[1]}: install the Visual Studio component "
                            "'C++ Clang tools for Windows' and probe again")
    elif purpose == "host-tools":
        if not toolchain.base_image:
            raise PlanError("conversion tools are built in the target's toolchain image: ToolchainInfo.base_image "
                            "must name it")
        builder = {"kind": "container", "base": "target-image", "image": toolchain.base_image,
                   "libc": toolchain.base_libc}
    elif backend == "cuda" and toolchain.cuda_image:
        image = next((i for i in (catalog.cuda.prebuilt_images if catalog.cuda else ())
                      if i.id == toolchain.cuda_image), None)
        if image is None:
            raise PlanError(f"prebuilt CUDA image {toolchain.cuda_image!r} is not in the catalog")
        if target.libc != "glibc":
            raise PlanError("the prebuilt CUDA images are glibc based")
        builder = {"kind": "container", "base": image.id, "image": image.image, "libc": "glibc", "prebuilt": True}
        notes.append("prebuilt NVIDIA image (explicit opt-in, decision D6), not a toolkit provisioned from the "
                     "pinned manifest")
    else:
        base = choose_build_base(catalog, target.libc, target.libc_version)
        builder = {"kind": "container", "base": base.id, "image": base.image, "libc": base.libc,
                   "libc_version": base.libc_version, "gcc": base.gcc}
        if version_tuple(base.gcc) < (11,):
            notes.append(f"{base.id} has GCC {base.gcc}, below upstream's oldest tested compiler (GCC 11); whether "
                         "the pin builds with it is unverified")

    leaves = _leaves_userland(target, purpose, placement)
    windows_cuda = target.os == "windows" and backend == "cuda"
    mode = _cpu_mode(profile, target, placement, backend_dl=windows_cuda, purpose=purpose)
    if profile.needs_reprobe and mode == "explicit":
        notes.append("CPU features the probe could not tell are left out (treated as OFF)")

    cpu_opts: Dict[str, str] = {}
    compile_flags: List[str] = []
    expect_sdot = False
    unusable: List[Dict[str, str]] = []
    if mode == "explicit":
        if target.arch == "x86_64":
            cpu_opts, compile_flags = _x86_options(profile, notes, unusable)
        else:
            march, _used, arm_unusable = arm_plan(profile, _variants_compiler(target, toolchain, base))
            cpu_opts["GGML_CPU_ARM_ARCH"] = march
            compile_flags = [f"-march={march}"]
            expect_sdot = "+dotprod" in march
            unusable.extend(arm_unusable)
    elif target.arch == "arm64":  # GGML_NATIVE or the CPU variants: no code of the pin uses these
        cid = toolchain.compiler_id()
        if mode == "native" and cid is not None and cid[1] is not None and _flag(profile, "SUPPORTS_SME") == "ON":
            problem = sme_problem((cid[0], cid[1]))  # -mcpu=native of a compiler without +sme leaves SME out
            if problem:
                unusable.append({"feature": _cpu_options.feature_label("SUPPORTS_SME"), "reason": problem})
        unusable.extend({"feature": _cpu_options.feature_label(k), "reason": reason}
                        for k, reason in ARM_UNUSABLE.items() if _flag(profile, k) == "ON")
    if mode == "variants":
        if target.arch == "arm64":
            _check_arm_variants(_variants_compiler(target, toolchain, base), base, notes)
        cpu_opts.update({"GGML_BACKEND_DL": "ON", "GGML_CPU_ALL_VARIANTS": "ON"})
        notes.append("CPU features the probe could not tell: all CPU variants are built and the best one is chosen "
                     "at run time")
    shared = mode == "variants" or windows_cuda

    options = _every_build(source, shared=shared, native=(mode == "native"), ccache=toolchain.ccache)
    options["LLAMA_BUILD_SERVER"] = "OFF" if purpose == "host-tools" else "ON"
    options.update(cpu_opts)
    for name in ("GGML_CUDA", "GGML_VULKAN", "GGML_HIP", "GGML_METAL"):
        options[name] = "OFF"

    # OpenMP: GCC's libgomp on Linux; Apple Clang has none; on Windows LLVM's libomp would be downloaded at configure
    # time (GGML_OPENMP_FETCH), so Windows builds use ggml's own thread pool (as Windows on ARM must, D9).
    compiler = "cc"
    toolchain_text: Optional[str] = None
    triple: Optional[str] = None
    openmp = True
    if target.os == "windows":
        compiler = "clang"
        options.update({"CMAKE_C_COMPILER": "clang", "CMAKE_CXX_COMPILER": "clang++"})
        openmp = False
    elif target.os == "macos":
        compiler = "appleclang"
        openmp = False
    elif builder["kind"] == "container" and purpose != "host-tools":
        toolchain_text, compiler, triple = _toolchain_file(target, toolchain)
        if compiler == "clang":
            openmp = False  # clang against the musl sysroot: no libgomp; ggml's thread pool
            notes.append("musl cross build with clang and the target's sysroot (unverified)")
    else:
        cid = toolchain.compiler_id()
        compiler = cid[0] if cid else "cc"
        if target.os == "linux" and cid is not None and cid[0] == "clang" and toolchain.openmp is not True:
            # CMake's FindOpenMP fails quietly without libomp and the build loses OpenMP: switch it off openly, so
            # the build ID tells the two builds apart.
            openmp = False
            notes.append("Clang without a usable OpenMP runtime (libomp): ggml's own thread pool (GGML_OPENMP=OFF); "
                         "install libomp (for example libomp-dev) and detect the toolchain again for an OpenMP build")
        elif toolchain.openmp is False:
            openmp = False
            notes.append("the compiler cannot build OpenMP code: ggml's own thread pool (GGML_OPENMP=OFF)")
    options["GGML_OPENMP"] = "ON" if openmp else "OFF"

    runtime_libs: List[str] = []
    if leaves:
        options["CMAKE_EXE_LINKER_FLAGS"] = _STATIC_RUNTIME
        if shared:
            options["CMAKE_SHARED_LINKER_FLAGS"] = _STATIC_RUNTIME
            # GGML_BACKEND_DL builds the backends (the CPU variants) as MODULE libraries
            # (ggml/src/CMakeLists.txt:381-383), which CMake links with the module flags, not the shared ones.
            options["CMAKE_MODULE_LINKER_FLAGS"] = _STATIC_RUNTIME
        if openmp:
            runtime_libs.append("libgomp.so.1")
        options["CMAKE_INSTALL_RPATH"] = _DEVICE_RPATH if openmp else "$ORIGIN"
        options["CMAKE_BUILD_WITH_INSTALL_RPATH"] = "ON"
    elif shared and target.os == "linux":
        options["CMAKE_INSTALL_RPATH"] = "$ORIGIN"
        options["CMAKE_BUILD_WITH_INSTALL_RPATH"] = "ON"

    # GPU backends.
    devices: List[str] = []
    tool_versions: Dict[str, Any] = {}
    cuda_toolkit: Optional[CudaToolkit] = None
    if backend == "cuda":
        caps = _nvidia_caps(profile)
        missing = _missing_caps(profile)
        if missing:
            raise PlanError(_missing_caps_message(missing))
        if not caps:
            raise PlanError("CUDA needs the compute capability of every NVIDIA GPU (GPU<n>_COMPUTE_CAP)")
        if placement == "cross":
            raise PlanError("CUDA cross builds are not offered; build on that machine")
        if target.arch != "x86_64":
            raise PlanError("CUDA builds are offered for x86_64 machines only")
        cuda_toolkit, why = choose_cuda_toolkit(profile, catalog, toolchain, placement=placement,
                                                target_os=target.os, base=base)
        if cuda_toolkit is None:
            raise PlanError("no pinned CUDA toolkit fits: " + "; ".join(why))
        options["GGML_CUDA"] = "ON"
        options["CMAKE_CUDA_ARCHITECTURES"] = _cuda_architectures(caps)
        options["GGML_CUDA_NCCL"] = "OFF"  # found on some machines only; the build stays the same everywhere
        count = sum(1 for g in profile.gpus() if g.get("VENDOR") == "nvidia")
        devices = [f"CUDA{n}" for n in range(count)]
        tool_versions["cuda"] = cuda_toolkit.version
        notes.append(f"real code for compute {', '.join(caps)} only: another GPU needs a rebuild")
    elif backend == "vulkan":
        options["GGML_VULKAN"] = "ON"
        devices = ["Vulkan0"]
        if toolchain.glslc:
            tool_versions["glslc"] = toolchain.glslc
        if profile.flag("VULKAN") != "ON":
            notes.append("the probe found no Vulkan loader on the machine")
        if any(g.get("VENDOR") == "arm" for g in profile.gpus()):
            notes.append("Vulkan on Mali: keep it only if llama-bench beats the CPU build (decision D8)")
    elif backend == "rocm":
        if target.os != "linux":
            raise PlanError("self-built HIP (ROCm) is offered on Linux only; on Windows use Vulkan")
        if placement not in ("native", "on-device"):
            raise PlanError("HIP builds run on the machine itself, with the ROCm installation of its user")
        gfx = _gfx_targets(profile)
        if not gfx:
            raise PlanError("ROCm needs an AMD GPU with a gfx target")
        rocm = toolchain.rocm or profile.get("ROCM_VERSION")
        if rocm is not None and not _RE_VERSION.fullmatch(rocm):
            raise PlanError("ROCM_VERSION is not a version number")
        if rocm is not None and version_tuple(rocm) < (6, 1):
            raise PlanError(f"llama.cpp's HIP backend needs ROCm 6.1 or newer, not {rocm}")
        options["GGML_HIP"] = "ON"
        options["GPU_TARGETS"] = ";".join(gfx)
        devices = ["ROCm0"]
        if rocm:
            tool_versions["rocm"] = rocm
        else:
            notes.append("ROCm version unknown; llama.cpp's HIP backend needs 6.1 or newer")
    elif backend == "metal":
        if target.os != "macos":
            raise PlanError("Metal exists on macOS only")
        options["GGML_METAL"] = "ON"
        options["GGML_METAL_EMBED_LIBRARY"] = "ON"
        devices = ["MTL0"]
        notes.append("macOS builds are untested (no Mac in the maintainer's hardware)")

    targets = TARGETS[purpose]
    steps: List[BuildStep] = []
    if windows_cuda:
        # Upstream's split (release.yml windows-cuda): the CPU part with Clang, the CUDA backend as a dynamic backend
        # with MSVC as nvcc's host compiler (D9). Whether both work from one Build Tools install is unverified.
        cpu_part = {k: v for k, v in options.items()
                    if k not in ("GGML_CUDA", "CMAKE_CUDA_ARCHITECTURES", "GGML_CUDA_NCCL")}
        cpu_part["GGML_CUDA"] = "OFF"
        cpu_part["GGML_BACKEND_DL"] = "ON"
        cuda_part = dict(options)
        cuda_part.pop("GGML_CPU_ALL_VARIANTS", None)
        for k in X86_FEATURES.values():
            cuda_part.pop(k, None)
        cuda_part.update({"CMAKE_C_COMPILER": "cl", "CMAKE_CXX_COMPILER": "cl", "GGML_CPU": "OFF",
                          "GGML_BACKEND_DL": "ON", "GGML_NATIVE": "OFF"})
        steps.append(BuildStep("cpu", "clang", "Ninja", _sorted(cpu_part), targets))
        steps.append(BuildStep("cuda", "msvc", "Ninja", _sorted(cuda_part), ("ggml-cuda",)))
        notes.append("the CUDA backend uses the toolkit's own CCCL (upstream's Windows release fetches CCCL "
                     "v3.4.3 at configure time; whether the bundled one builds is unverified)")
        if toolchain.msvc:
            tool_versions["msvc"] = toolchain.msvc
    else:
        steps.append(BuildStep("main", compiler, "Ninja", _sorted(options), targets, toolchain_text))

    for step in steps:
        _check_options(step.options)

    toolchain_ids: Dict[str, Any] = dict(tool_versions)
    if builder["kind"] == "native" or purpose == "host-tools":
        if toolchain.compiler:
            toolchain_ids["compiler"] = toolchain.compiler
    if toolchain.cmake:
        toolchain_ids["cmake"] = toolchain.cmake
    if any(step.option("GGML_NATIVE") == "ON" for step in steps):
        toolchain_ids["native"] = _cpu_identity(profile, target)
        if toolchain.native_expansion:
            toolchain_ids["native"]["expansion"] = toolchain.native_expansion
        else:
            notes.append("what -march/-mcpu=native expands to is recorded by the executor")

    verify: Dict[str, Any] = {
        "version": {"build_number": source.build_number, "commit": source.short_commit},
        "binaries": list(targets),
        "devices": devices,
    }
    if leaves and target.libc == "glibc":
        verify["glibc_max"] = target.libc_version
    if compile_flags and placement in ("cross",):
        verify["compile_check_flags"] = compile_flags
    if expect_sdot:
        verify["expect_sdot"] = True
    if runtime_libs:
        verify["runtime_libs"] = runtime_libs
    if leaves:
        verify["static_libstdcxx"] = True

    plan = BuildPlan(
        recipe_version=RECIPE_VERSION, purpose=purpose, placement=placement, backend=backend,
        source={"repo": source.repo, "commit": source.commit, "tag": source.tag, "build_number": source.build_number},
        target={"os": target.os, "arch": target.arch, "libc": target.libc, "libc_version": target.libc_version,
                "triple": triple},
        builder=builder, steps=tuple(steps), toolchain=toolchain_ids, verify=verify, notes=tuple(notes),
        cpu_features=cpu_features_of(profile, target.arch, dict(steps[0].options), unusable))
    return replace(plan, build_id=build_id_of(plan.id_inputs()))


def plan_variants(profile: HardwareProfile, *, purpose: str, placement: str, catalog: RuntimeCatalog,
                  toolchain: ToolchainInfo) -> List[BuildPlan]:
    """The builds a machine gets: the CPU build first (the fallback, built first), then at most one GPU build."""
    plans = [plan_build(profile, purpose=purpose, placement=placement, backend="cpu", catalog=catalog,
                        toolchain=toolchain)]
    backend, _reason = choose_backend(profile, purpose=purpose, placement=placement, catalog=catalog,
                                      toolchain=toolchain)
    if backend != "cpu":
        plans.append(plan_build(profile, purpose=purpose, placement=placement, backend=backend, catalog=catalog,
                                toolchain=toolchain))
    return plans


def _sorted(options: Mapping[str, str]) -> Tuple[Tuple[str, str], ...]:
    return tuple(sorted(options.items()))


def _check_options(options: Sequence[Tuple[str, str]]) -> None:
    as_dict = dict(options)
    for name, value in options:
        if not _RE_OPTION_NAME.fullmatch(name) or not _RE_OPTION_VALUE.fullmatch(value):
            raise PlanError(f"refusing option {name!r}: unexpected characters")  # pragma: no cover - computed values
    check_download_options(as_dict)

