#!/usr/bin/env python3
"""
Machine-readable target capabilities (the `capabilities:` block of a target's target.yml), and the `runtime:`
block: what the build recipe planned for the target's device runtime (a record, never an input to a build).

One place says which boards a target has, which formats it can produce for which board, which
quantizations each format accepts and what calibration data it needs. GUI, CLI, the build service,
Ditto and the MCP server derive their choices and their request validation from it.
"""

import re
from enum import Enum
from typing import Annotated, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.tools.output import UntrustedText, untrusted_text

# Labels, notes and board descriptions are free text from target.yml, which a community target pack installs: they
# are marked as external text (UntrustedText), so tool results clean, cap and mark them like a pack's other texts.

class SupportLevel(str, Enum):
    OFFICIAL = "official"          # maintained and tested by the project
    COMMUNITY = "community"        # contributed; works for its maintainers, use at your own risk
    EXPERIMENTAL = "experimental"  # incomplete or unverified


class BoardInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: untrusted_text(200)
    cpu: Optional[untrusted_text(200)] = None
    ram_max_gb: Optional[float] = Field(default=None, gt=0)
    npu: Optional[untrusted_text(200)] = None
    gpu: Optional[untrusted_text(200)] = None
    examples: List[untrusted_text(200)] = Field(default_factory=list)


class FormatCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: untrusted_text(200)
    input: List[Literal["hf", "onnx", "gguf"]] = Field(default_factory=lambda: ["hf"])
    quantizations: List[str] = Field(min_length=1)
    default_quantization: str
    # Boards that can run this format (None: every board of the target).
    boards: Optional[List[str]] = None
    # Per-board subsets of `quantizations` where a SoC supports fewer types.
    board_quantizations: Dict[str, List[str]] = Field(default_factory=dict)
    board_default_quantization: Dict[str, str] = Field(default_factory=dict)
    imatrix: bool = False
    needs_imatrix: List[str] = Field(default_factory=list)
    dataset: Literal["none", "optional", "required"] = "none"
    dataset_kind: Literal["text", "sample_list"] = "text"
    dataset_required_for: List[str] = Field(default_factory=list)
    # Other spellings the module scripts accept, e.g. {INT8: Q8_0}; matched case-insensitively.
    aliases: Dict[str, str] = Field(default_factory=dict)
    notes: Optional[UntrustedText] = None

    @model_validator(mode="after")
    def _consistent(self):
        known = set(self.quantizations)
        problems = []
        if self.default_quantization not in known:
            problems.append(f"default_quantization {self.default_quantization!r} is not in quantizations")
        for board, quants in self.board_quantizations.items():
            extra = set(quants) - known
            if extra:
                problems.append(f"board_quantizations[{board}] lists unknown types {sorted(extra)}")
        for board, quant in self.board_default_quantization.items():
            if quant not in self.board_quantizations.get(board, self.quantizations):
                problems.append(f"board_default_quantization[{board}] {quant!r} is not allowed on that board")
        for name, values in (("needs_imatrix", self.needs_imatrix), ("dataset_required_for", self.dataset_required_for)):
            extra = set(values) - known
            if extra:
                problems.append(f"{name} lists unknown types {sorted(extra)}")
        if self.needs_imatrix and not self.imatrix:
            problems.append("needs_imatrix is set but imatrix is false")
        bad_aliases = {a: q for a, q in self.aliases.items() if q not in known}
        if bad_aliases:
            problems.append(f"aliases point to unknown types {bad_aliases}")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def canonical(self, quantization: Optional[str]) -> Optional[str]:
        """The listed spelling of a quantization type or of one of its aliases (case-insensitive), or None."""
        if not quantization:
            return None
        wanted = quantization.strip().casefold()
        found = next((q for q in self.quantizations if q.casefold() == wanted), None)
        if found is None:
            found = next((q for a, q in self.aliases.items() if a.casefold() == wanted), None)
        return found

    def quantizations_for(self, board: Optional[str]) -> List[str]:
        if board and board in self.board_quantizations:
            return list(self.board_quantizations[board])
        return list(self.quantizations)

    def default_for(self, board: Optional[str]) -> str:
        if board and board in self.board_default_quantization:
            return self.board_default_quantization[board]
        allowed = self.quantizations_for(board)
        return self.default_quantization if self.default_quantization in allowed else allowed[0]


class TargetCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    support_level: SupportLevel = SupportLevel.OFFICIAL
    boards: Dict[str, BoardInfo] = Field(min_length=1)
    formats: Dict[str, FormatCapability] = Field(min_length=1)
    # The board the build scripts use when a request names none (must match the config stage, config_module.sh).
    default_board: Optional[str] = None

    @model_validator(mode="after")
    def _boards_exist(self):
        problems = []
        if self.default_board is not None and self.default_board not in self.boards:
            problems.append(f"default_board {self.default_board!r} is not one of the boards")
        for fmt, cap in self.formats.items():
            referenced = set(cap.boards or []) | set(cap.board_quantizations) | set(cap.board_default_quantization)
            unknown = referenced - set(self.boards)
            if unknown:
                problems.append(f"format {fmt} references unknown boards {sorted(unknown)}")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def boards_for(self, fmt: str) -> List[str]:
        cap = self.formats.get(fmt)
        if not cap:
            return []
        return list(cap.boards) if cap.boards is not None else list(self.boards)

    def canonical_format(self, fmt: Optional[str]) -> Optional[str]:
        if not fmt:
            return None
        wanted = fmt.strip().casefold()
        return next((f for f in self.formats if f.casefold() == wanted), None)

    def canonical_board(self, board: Optional[str]) -> Optional[str]:
        """The declared spelling of a board id (case-insensitive), or None if unknown."""
        if not board:
            return None
        wanted = board.strip().casefold()
        return next((b for b in self.boards if b.casefold() == wanted), None)

    def normalize(self, fmt: str, board: Optional[str], quantization: Optional[str]
                  ) -> Tuple[str, Optional[str], Optional[str]]:
        """The values a build really runs with: canonical spellings, the board the scripts fall back to when
        none is given, and the board's default quantization. Unknown values are passed through unchanged
        (check_request reports them)."""
        fmt = self.canonical_format(fmt) or fmt
        cap = self.formats.get(fmt)
        if board:
            board = self.canonical_board(board) or board
        elif cap is not None:
            boards = self.boards_for(fmt)
            if len(boards) == 1:
                board = boards[0]
            elif self.default_board in boards:
                board = self.default_board
        if cap is not None:
            quantization = cap.canonical(quantization) or quantization or cap.default_for(board)
        return fmt, board, quantization

    def check_request(self, fmt: str, board: Optional[str], quantization: Optional[str],
                      dataset: bool = False, imatrix: bool = False) -> List[str]:
        """Problems with a build request, as user-facing sentences (empty list: the request is valid).
        The values are normalized first (see normalize()); validate what will actually be built."""
        fmt, board, quantization = self.normalize(fmt, board, quantization)
        cap = self.formats.get(fmt)
        if cap is None:
            return [f"Format '{fmt}' is not available for this target (available: {', '.join(self.formats)})."]
        problems = []
        if board is None and cap.board_quantizations and len(self.boards_for(fmt)) > 1:
            return [f"Choose a board for {fmt} ({', '.join(self.boards_for(fmt))}): the supported quantizations differ."]
        if board is not None and board not in self.boards:
            problems.append(f"Unknown board '{board}' (known: {', '.join(self.boards)}).")
        elif board is not None and board not in self.boards_for(fmt):
            problems.append(f"Format '{fmt}' does not run on {board} (supported: {', '.join(self.boards_for(fmt))}).")
        quant = cap.canonical(quantization) or quantization or cap.default_for(board)
        allowed = cap.quantizations_for(board)
        if quant not in allowed:
            where = f" on {board}" if board else ""
            problems.append(f"Quantization '{quant}' is not supported for {fmt}{where} (allowed: {', '.join(allowed)}).")
        if imatrix and not cap.imatrix:
            problems.append(f"An importance matrix (IMatrix) is not used by the {fmt} format.")
        if quant in cap.needs_imatrix and not imatrix:
            # Without a dataset of its own, the framework uses its shipped calibration text.
            problems.append(f"{quant} needs an importance matrix: enable IMatrix.")
        if (cap.dataset == "required" or quant in cap.dataset_required_for) and not dataset:
            problems.append(f"{fmt} {quant} needs a calibration dataset ({cap.dataset_kind.replace('_', ' ')}).")
        if dataset and cap.dataset == "none" and not cap.imatrix:
            problems.append(f"The {fmt} format does not use a calibration dataset.")
        return problems


# --- the runtime: block (orchestrator/runtime/build/target_runtime.py writes it) ---------------------------------

_FLAG_TOKEN = re.compile(r"^-[A-Za-z][\w.=+,:/-]*$")
_NoteText = Annotated[str, Field(max_length=300)]


class RuntimeAdvisory(BaseModel):
    """Compiler and CMake flags from the module wizard or an AI: shown to the reader, never applied (decision D20)."""
    model_config = ConfigDict(extra="forbid")

    cpu_flags: str = Field(default="", max_length=500)
    cmake_flags: str = Field(default="", max_length=500)
    note: Optional[_NoteText] = None

    @model_validator(mode="after")
    def _plain_flags(self):
        bad = [t for t in (self.cpu_flags + " " + self.cmake_flags).split() if not _FLAG_TOKEN.fullmatch(t)]
        if bad:
            raise ValueError(f"advisory flags must be plain options like -mcpu=cortex-a76: {', '.join(bad[:3])}")
        return self


class TargetRuntime(BaseModel):
    """The `runtime:` block of a target.yml: what the build recipe planned for the target's device runtime from the
    target's probe. A record for the reader; a device runtime is always planned again from the device's own probe, so
    the block never feeds a build."""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    built_from: Literal["source"] = "source"     # llama.cpp from the pinned commit; never a prebuilt image or binary
    recipe: Literal["orchestrator/runtime/build/recipe.py"] = "orchestrator/runtime/build/recipe.py"
    purpose: Literal["device-runtime"] = "device-runtime"
    # The probe's values (configs/hardware_probe_schema.yml) and the module generator's architecture names.
    os: Optional[Literal["linux", "windows", "macos", "freebsd", "android"]] = None
    arch: Literal["x86_64", "x86", "arm64", "armv7", "armv7l", "armv6", "riscv64", "ppc64le", "s390x", "loongarch64",
                  "other"]
    libc: Optional[Literal["glibc", "musl", "uclibc", "bionic", "ucrt", "libsystem", "bsdlibc"]] = None
    libc_min: Optional[str] = Field(default=None, pattern=r"^(glibc|musl)-\d{1,3}(\.\d{1,5}){1,3}$")
    placement: Optional[Literal["native", "cross", "on-device"]] = None
    builder_base: Optional[str] = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    cpu: Optional[Literal["native", "explicit", "all-variants"]] = None
    cpu_arm_arch: Optional[str] = Field(default=None, pattern=r"^armv8(\.[1-9])?-a(\+[a-z0-9]{2,12}){0,8}$")
    cpu_features: List[Annotated[str, Field(pattern=r"^GGML_[A-Z0-9_]{2,24}$")]] = Field(default_factory=list,
                                                                                         max_length=20)
    backends: List[Literal["cpu", "cuda", "vulkan", "rocm", "metal"]] = Field(default_factory=list, max_length=5)
    targets: List[Literal["llama-server", "llama-cli", "llama-bench", "llama-perplexity"]] = Field(
        default_factory=list, max_length=4)
    profile_perf_key: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: List[_NoteText] = Field(default_factory=list, max_length=12)
    advisory: Optional[RuntimeAdvisory] = None


# --- the optimization_profiles: and performance_benchmarks: blocks (orchestrator/Core/board_profiles.py reads them) ---

# The optimization levels of a build (start_build, `llm-cli build start --optimization`, the Builder); the build
# scripts get the level as OPTIMIZATION_LEVEL. schemas/build.py imports them from here.
OPTIMIZATION_LEVELS = ("fast", "balanced", "size", "speed", "aggressive")
# What a board's memory allows (the target side of a profile): low_memory boards (1-4 GB) take models up to about 1.5B
# parameters at 4 bit, medium_memory (4-8 GB) up to about 4B, high_memory (8 GB and more) up to about 8B.
MEMORY_PROFILES = ("low_memory", "medium_memory", "high_memory")
# CPU features a profile names, as the hardware probe reports them (SUPPORTS_<NAME>): the ARM ones the build recipe
# turns into GGML_CPU_ARM_ARCH (fp16 is SUPPORTS_FP16_ARITH), and the x86 ones it turns into GGML_<NAME> options.
PROFILE_CPU_FEATURES = ("neon", "dotprod", "fp16", "i8mm", "sve", "sse42", "avx", "avx2", "bmi2", "f16c", "fma",
                        "avx512", "avx512_vbmi", "avx512_vnni", "avx512_bf16", "avx_vnni", "amx_tile", "amx_int8",
                        "amx_bf16")
_OPTION_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$", re.ASCII)
_OPTION_VALUE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$", re.ASCII)


class OptimizationProfile(BaseModel):
    """The optimization profile of one board (`optimization_profiles.<board>` in target.yml). Its source side is what
    the build recipe (orchestrator/runtime/build/recipe.py, the one place that decides llama.cpp's options and the
    compiler flags) plans for a cross build for this SoC; a test and the target check (TargetManager.validate) see that the two
    agree (Core/board_profiles.plan_problems), so the profile is never a second planner. Its target side is the
    framework's recommendation for the board: the quantizations offered first, the memory profile and the
    optimization level the Builder, the CLI and AI callers start from."""
    model_config = ConfigDict(extra="forbid")

    arch: Literal["arm64", "x86_64"]
    # The cores as GCC's -mcpu names them (cortex-a76.cortex-a55 for big.LITTLE): for the reader and the tuning note;
    # the plan sets the architecture (-march) from cpu_features, so a board of the same SoC never gets more.
    cpu: str = Field(pattern=r"^[a-z0-9][a-z0-9.+-]{1,63}$")
    cpu_features: List[Literal[PROFILE_CPU_FEATURES]] = Field(default_factory=list, max_length=20)
    # Source side: llama.cpp's CMake options and the compiler flags of the recipe's cross-build plan for this SoC.
    cmake_options: Dict[str, str] = Field(default_factory=dict, max_length=32)
    cpu_flags: str = Field(default="", max_length=200)
    # Target side: the recommendation for the board.
    memory_profile: Literal[MEMORY_PROFILES]
    optimization: Literal[OPTIMIZATION_LEVELS] = "balanced"
    recommended_quantization: List[str] = Field(min_length=1, max_length=12)
    note: Optional[untrusted_text(300)] = None

    @model_validator(mode="after")
    def _plain(self):
        problems = []
        for name, value in self.cmake_options.items():
            if not _OPTION_NAME.fullmatch(name) or not _OPTION_VALUE.fullmatch(str(value)):
                problems.append(f"cmake_options: {name!r} is not a plain CMake option")
        if self.cmake_options.get("GGML_NATIVE", "OFF") != "OFF":
            problems.append("cmake_options: GGML_NATIVE must be OFF (the build for a board is a cross build; ON would "
                            "take the build computer's CPU)")
        flags = self.cpu_flags.split()
        bad = [t for t in flags if not _FLAG_TOKEN.fullmatch(t)]
        if bad:
            problems.append(f"cpu_flags must be plain options like -march=armv8.2-a: {', '.join(bad[:3])}")
        if any(t in ("-march=native", "-mcpu=native", "-mtune=native") for t in flags):
            problems.append("cpu_flags: native is the build computer's CPU, never the board's")
        if self.arch == "arm64" and any(t.startswith("-mfpu") or t.startswith("-mfloat-abi") for t in flags):
            problems.append("cpu_flags: -mfpu and -mfloat-abi are AArch32 options; an arm64 compiler refuses them "
                            "(NEON is part of every ARMv8-A)")
        if self.arch == "arm64" and any(f not in ("neon", "dotprod", "fp16", "i8mm", "sve") for f in self.cpu_features):
            problems.append("cpu_features: an arm64 board has ARM features only (neon, dotprod, fp16, i8mm, sve)")
        if problems:
            raise ValueError("; ".join(problems))
        return self


# The values of a benchmark row. gen_ts and prompt_ts are the ones a measurement on the board fills (llama-bench -n 128
# and -p 512 in the board test, runtime/device/tested.py: device_tests.json gen_ts and prompt_ts).
BENCHMARK_VALUES = ("gen_ts", "prompt_ts", "first_token_ms", "memory_usage_mb", "cpu_usage_percent")
MEASURED_VALUES = ("gen_ts", "prompt_ts")
_DATE = r"^\d{4}-\d{2}-\d{2}$"
_PUBLISHED_URL = (r"^https://(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?::\d{1,5})?"
                  r"(?:[/?#][A-Za-z0-9._~%/?#=&+:,;!*()'-]*)?$")


class BenchmarkSource(BaseModel):
    """Where the numbers of a benchmark row come from: measured on a board by the framework (a board test record), published
    (an https address and the date it was read) or unmeasured (an estimate without a source, shown as such)."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["measured", "published", "unmeasured"]
    # a plain https address (host name with a dot, optional port, path/query/fragment of URL characters): no
    # credential, no markup, no prose; it is shown to the person and to AI callers (list_targets)
    url: Optional[str] = Field(default=None, max_length=500, pattern=_PUBLISHED_URL)
    date: Optional[str] = Field(default=None, pattern=_DATE)
    board_label: Optional[untrusted_text(64)] = None   # measured: the label the person gave the board, never an address
    note: Optional[untrusted_text(300)] = None

    @model_validator(mode="after")
    def _complete(self):
        if self.kind == "published" and not (self.url and self.date):
            raise ValueError("a published value needs its url and the date it was read")
        if self.kind == "measured" and not (self.board_label and self.date):
            raise ValueError("a measured value needs the board's label and the date of the measurement")
        if self.kind == "unmeasured" and (self.url or self.board_label):
            raise ValueError("an unmeasured value has no url and no board: give kind published or measured")
        return self

    def label(self) -> str:
        """How the row's source is shown (plain text): never a measurement unless it is one."""
        if self.kind == "measured":
            return f"measured by llmcf (board {self.board_label}, {self.date})"
        if self.kind == "published":
            return f"published: {self.url} (read {self.date})"
        return "unmeasured (an estimate, no measurement or publication behind it)"


class BenchmarkEntry(BaseModel):
    """One row of `performance_benchmarks:` in target.yml: a model at a quantization on a board, and its source."""
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,95}$")
    board: str = Field(min_length=1, max_length=64)
    quantization: str = Field(min_length=1, max_length=32)
    gen_ts: Optional[float] = Field(default=None, ge=0)
    prompt_ts: Optional[float] = Field(default=None, ge=0)
    first_token_ms: Optional[float] = Field(default=None, ge=0)
    memory_usage_mb: Optional[float] = Field(default=None, ge=0)
    cpu_usage_percent: Optional[float] = Field(default=None, ge=0, le=100 * 256)
    source: BenchmarkSource

    @property
    def measured(self) -> bool:
        return self.source.kind == "measured"

    def values(self) -> Dict[str, float]:
        return {name: getattr(self, name) for name in BENCHMARK_VALUES if getattr(self, name) is not None}


class BoardData(BaseModel):
    """The optimization_profiles: and performance_benchmarks: blocks of a target.yml, checked against its
    capabilities (every board known, every recommended quantization one the board's formats accept)."""
    model_config = ConfigDict(extra="forbid")

    optimization_profiles: Dict[str, OptimizationProfile] = Field(default_factory=dict)
    performance_benchmarks: List[BenchmarkEntry] = Field(default_factory=list, max_length=500)

    def problems(self, caps: Optional[TargetCapabilities]) -> List[Tuple[str, str]]:
        """(field, message) for every board or quantization the capabilities do not have (empty: none)."""
        if caps is None:
            return []
        found: List[Tuple[str, str]] = []
        for board, profile in self.optimization_profiles.items():
            if caps.canonical_board(board) != board:
                found.append((f"optimization_profiles.{board}", f"{board!r} is not one of the boards "
                                                                f"({', '.join(caps.boards)})"))
                continue
            allowed = {q.casefold() for fid, fmt in caps.formats.items() if board in caps.boards_for(fid)
                       for q in fmt.quantizations_for(board)}
            unknown = [q for q in profile.recommended_quantization if q.casefold() not in allowed]
            if unknown:
                found.append((f"optimization_profiles.{board}.recommended_quantization",
                              f"not offered for {board}: {', '.join(unknown)}"))
        for number, row in enumerate(self.performance_benchmarks):
            if caps.canonical_board(row.board) != row.board:
                found.append((f"performance_benchmarks.{number}.board", f"{row.board!r} is not one of the boards"))
        return found


# --- the input of the tools that name one target ----------------------------------------------------------------------
# First written in tools/framework_tools.py, which keeps the name; it lives here so the target tool module
# (tools/tools_targets.py, whose TargetCheckIn builds on it) does not import the tool hub. Its JSON schema, which an AI
# reads, is unchanged; a class docstring would become part of it, so the comment stays a comment. The capabilities
# result (TargetCapabilitiesOut) lives with the target tools: this bottom layer defines no ToolOutput.

class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=64, description="Target id, e.g. Rockchip")
