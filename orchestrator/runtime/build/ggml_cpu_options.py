"""Every CPU option of the pinned llama.cpp (ggml's CPU backend) and what the framework does with it.

The one-planner rule for the CPU: every CPU feature that the pinned llama.cpp can use is detected by the hardware
probe (scripts/hardware_probe.py, .sh, .ps1) and planned by one planner, orchestrator/runtime/build/recipe.py; the
targets' config_module.sh mirrors that plan and is tested against it. A board's acceleration must never be lost
silently: what the board has and the build cannot use is named in the plan with the reason.

This file is the inventory of the pin: one row per CPU option (ggml/CMakeLists.txt, "instruction set specific"), per
-march extension tag of GGML_CPU_ARM_ARCH (ggml/src/ggml-cpu/CMakeLists.txt, ARCH_TAGS and check_arm_feature), per
CPU option ggml-cpu reads without declaring it, and per feature of ggml's CPU variant lists (ggml/src/CMakeLists.txt,
ggml_add_cpu_backend_variant). Each row says which probe key decides it, which compiler flag or -march tag it gives,
which feature it builds on and whether the planner uses it ("planned") or not, and why not ("not usable").

tests/test_cpu_feature_coverage.py parses the committed excerpts of the pin's CMake files
(tests/fixtures/llama_cpp/<tag>/) and fails when the pin has an option or tag this file does not list, when a row
names a probe key the schema does not know, or when a "planned" row is not planned by recipe.py. A pin bump without
new excerpts fails too (the excerpts name the commit). Re-checked at every pin bump (CONTRIBUTING.md, "Bumping the
llama.cpp pin").
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# The pin this inventory was read from (configs/llama_runtime.yml, llama_cpp.source).
PIN_COMMIT = "d81235049384534c167caea52b85a694f6103d14"
PIN_TAG = "b11429"

PLANNED = "planned"
NOT_USABLE = "not usable"

# kinds of rows
CMAKE_OPTION = "cmake-option"        # a CMake option or cache string of ggml
ARM_ARCH_TAG = "arm-arch-tag"        # an extension tag of -march (GGML_CPU_ARM_ARCH)
POWER_CPU_TYPE = "power-cpu-type"    # a value of GGML_CPU_POWERPC_CPUTYPE (-mcpu=powerN)
VARIANT_FEATURE = "variant-feature"  # a feature only ggml's CPU variant lists name (GGML_CPU_ALL_VARIANTS)

# Why a feature of the other architectures is not planned (recipe.py refuses the architecture with the features the
# probe found; the row still says which option each would set, so a later target pack plans the same).
_NO_ARCH = "recipe.py has no plan for {arch} yet (no target builds for it): the build is refused and names this feature"


@dataclass(frozen=True)
class CpuOption:
    """One CPU option or tag of the pin. `name`: the CMake option (GGML_AVX2), the -march tag (+dotprod) or the
    -mcpu value (power9); `arch`: the ARCH of the probe it belongs to (any: every architecture); `probe_key`: the key
    that decides it (None: no probe key decides it); `flag`: the compiler flag or -march tag it gives; `builds_on`:
    the probe key of the feature it needs; `state`: planned or not usable, with `reason`; `variant`: its word in
    ggml's CPU variant lists."""
    name: str
    arch: str
    kind: str
    probe_key: Optional[str]
    flag: str
    builds_on: Optional[str]
    state: str
    reason: str = ""
    variant: str = ""


def _x86(name: str, key: str, flag: str, builds_on: Optional[str] = None) -> CpuOption:
    return CpuOption(name, "x86_64", CMAKE_OPTION, key, flag, builds_on, PLANNED,
                     "explicit for another machine, -march=native for this one", variant=name[len("GGML_"):])


def _other(name: str, arch: str, key: Optional[str], flag: str, builds_on: Optional[str] = None,
           kind: str = CMAKE_OPTION, reason: str = "", variant: str = "") -> CpuOption:
    return CpuOption(name, arch, kind, key, flag, builds_on, NOT_USABLE, reason or _NO_ARCH.format(arch=arch),
                     variant)


OPTIONS: Tuple[CpuOption, ...] = (
    # --- every architecture -------------------------------------------------------------------------------------
    CpuOption("GGML_NATIVE", "any", CMAKE_OPTION, None, "-march=native / -mcpu=native", None, PLANNED,
              "ON for a build on the machine that runs the binaries, OFF for every other build"),
    CpuOption("GGML_CPU_ALL_VARIANTS", "any", CMAKE_OPTION, None, "every CPU variant, chosen at run time", None,
              PLANNED, "when the probe could not tell the CPU features (with GGML_BACKEND_DL)"),
    CpuOption("GGML_CPU_REPACK", "any", CMAKE_OPTION, None, "runtime weight conversion of Q4_0", None, PLANNED,
              "left at upstream's ON: no CPU feature, every build has it"),
    CpuOption("GGML_CPU_HBM", "any", CMAKE_OPTION, None, "links memkind", None, NOT_USABLE,
              "high-bandwidth memory through the memkind library, not a CPU feature; kept at upstream's OFF"),

    # --- x86: the 14 options of the probe's x86 keys (ggml/CMakeLists.txt, ggml-cpu/CMakeLists.txt:347-410) -----
    _x86("GGML_SSE42", "SUPPORTS_SSE42", "-msse4.2"),
    _x86("GGML_AVX", "SUPPORTS_AVX", "-mavx"),
    _x86("GGML_AVX2", "SUPPORTS_AVX2", "-mavx2", "SUPPORTS_AVX"),
    _x86("GGML_BMI2", "SUPPORTS_BMI2", "-mbmi2"),
    _x86("GGML_F16C", "SUPPORTS_F16C", "-mf16c", "SUPPORTS_AVX"),
    _x86("GGML_FMA", "SUPPORTS_FMA", "-mfma", "SUPPORTS_AVX"),
    _x86("GGML_AVX512", "SUPPORTS_AVX512", "-mavx512f -mavx512cd -mavx512vl -mavx512dq -mavx512bw", "SUPPORTS_AVX2"),
    _x86("GGML_AVX512_VBMI", "SUPPORTS_AVX512_VBMI", "-mavx512vbmi", "SUPPORTS_AVX512"),
    _x86("GGML_AVX512_VNNI", "SUPPORTS_AVX512_VNNI", "-mavx512vnni", "SUPPORTS_AVX512"),
    _x86("GGML_AVX512_BF16", "SUPPORTS_AVX512_BF16", "-mavx512bf16", "SUPPORTS_AVX512"),
    _x86("GGML_AVX_VNNI", "SUPPORTS_AVX_VNNI", "-mavxvnni", "SUPPORTS_AVX2"),
    _x86("GGML_AMX_TILE", "SUPPORTS_AMX_TILE", "-mamx-tile"),
    _x86("GGML_AMX_INT8", "SUPPORTS_AMX_INT8", "-mamx-int8", "SUPPORTS_AMX_TILE"),
    _x86("GGML_AMX_BF16", "SUPPORTS_AMX_BF16", "-mamx-bf16", "SUPPORTS_AMX_TILE"),

    # --- ARM: GGML_CPU_ARM_ARCH = armv8.2-a plus the tags in ggml's ARCH_TAGS order ------------------------------
    CpuOption("GGML_CPU_ARM_ARCH", "arm64", CMAKE_OPTION, None, "-march=<value>", None, PLANNED,
              "armv8.2-a with the tags below that the probe confirms, armv8-a without any"),
    CpuOption("+dotprod", "arm64", ARM_ARCH_TAG, "SUPPORTS_DOTPROD", "+dotprod", None, PLANNED, variant="DOTPROD"),
    CpuOption("+fp16", "arm64", ARM_ARCH_TAG, "SUPPORTS_FP16_ARITH", "+fp16", None, PLANNED,
              variant="FP16_VECTOR_ARITHMETIC"),
    CpuOption("+sve", "arm64", ARM_ARCH_TAG, "SUPPORTS_SVE", "+sve", None, PLANNED, variant="SVE"),
    CpuOption("+i8mm", "arm64", ARM_ARCH_TAG, "SUPPORTS_I8MM", "+i8mm", None, PLANNED, variant="MATMUL_INT8"),
    CpuOption("+sve2", "arm64", ARM_ARCH_TAG, "SUPPORTS_SVE2", "+sve2", None, PLANNED, variant="SVE2"),
    CpuOption("+sme", "arm64", ARM_ARCH_TAG, "SUPPORTS_SME", "+sme", None, PLANNED,
              "only with a compiler that knows it (recipe.SME_COMPILER_MIN), else the plan names it as unusable",
              variant="SME"),
    CpuOption("+nosve", "arm64", ARM_ARCH_TAG, None, "+nosve", None, NOT_USABLE,
              "switches SVE off; only upstream's Apple M4 variant uses it, a plan names only what the board has",
              variant="NOSVE"),
    CpuOption("+bf16", "arm64", ARM_ARCH_TAG, "SUPPORTS_BF16_ARM", "+bf16", None, NOT_USABLE,
              "the pin has no BF16 kernel for ARM: its ggml-cpu build files neither check nor tag BF16 on ARM"),
    CpuOption("+fma", "arm64", ARM_ARCH_TAG, None, "", None, NOT_USABLE,
              "FMA is mandatory on ARMv8-A and needs no flag (ggml probes it only for MSVC)"),
    CpuOption("GGML_CPU_KLEIDIAI", "arm64", CMAKE_OPTION, None, "KleidiAI kernels", "SUPPORTS_DOTPROD", NOT_USABLE,
              "downloads the KleidiAI source at configure time: kept OFF (recipe DOWNLOAD_OPTIONS_OFF); a module may "
              "use it only with the pre-fetched copy (recipe DEPENDENCY_OF_OPTION)"),

    # --- RISC-V (ggml-cpu/CMakeLists.txt: -march=rv64gc + v + _ext ..., -mabi=lp64d) -----------------------------
    _other("GGML_RVV", "riscv64", "SUPPORTS_RVV", "v (-march=rv64gcv)", variant="RVV"),
    _other("GGML_RV_ZFH", "riscv64", "SUPPORTS_RV_ZFH", "_zfh"),
    _other("GGML_RV_ZVFH", "riscv64", "SUPPORTS_RV_ZVFH", "_zvfh", "SUPPORTS_RVV"),
    _other("GGML_RV_ZICBOP", "riscv64", "SUPPORTS_RV_ZICBOP", "_zicbop"),
    _other("GGML_RV_ZIHINTPAUSE", "riscv64", "SUPPORTS_RV_ZIHINTPAUSE", "_zihintpause"),
    _other("GGML_RV_ZVFBFWMA", "riscv64", "SUPPORTS_RV_ZVFBFWMA", "_zvfbfwma", "SUPPORTS_RVV",
           reason="OFF upstream; and " + _NO_ARCH.format(arch="riscv64")),
    _other("GGML_RV_ZBA", "riscv64", "SUPPORTS_RV_ZBA", "_zba",
           reason="read by ggml-cpu without an option declaration; and " + _NO_ARCH.format(arch="riscv64")),
    _other("GGML_XTHEADVECTOR", "riscv64", "SUPPORTS_XTHEADVECTOR", "_xtheadvector",
           reason="T-Head cores only (instead of the V extension); and " + _NO_ARCH.format(arch="riscv64")),
    _other("GGML_CPU_RISCV64_SPACEMIT", "riscv64", None, "_xsmtvdotii (GCC 15)",
           reason="SpacemiT's vendor kernels (FindSMTIME.cmake), no ISA feature a probe reads; and "
                  + _NO_ARCH.format(arch="riscv64")),

    # --- LoongArch (-march=loongarch64 -mlasx -mlsx) ---------------------------------------------------------------
    _other("GGML_LSX", "loongarch64", "SUPPORTS_LSX", "-mlsx"),
    _other("GGML_LASX", "loongarch64", "SUPPORTS_LASX", "-mlasx", "SUPPORTS_LSX"),

    # --- POWER (GGML_CPU_POWERPC_CPUTYPE -> -mcpu=, the variants' POWERn and VSX) ----------------------------------
    _other("GGML_CPU_POWERPC_CPUTYPE", "ppc64le", "POWER_CPU_TYPE", "-mcpu=<value>"),
    _other("power7", "ppc64le", "POWER_CPU_TYPE", "-mcpu=power7", kind=POWER_CPU_TYPE, variant="POWER7"),
    _other("power8", "ppc64le", "POWER_CPU_TYPE", "-mcpu=power8", kind=POWER_CPU_TYPE, variant="POWER8"),
    _other("power9", "ppc64le", "POWER_CPU_TYPE", "-mcpu=power9", kind=POWER_CPU_TYPE, variant="POWER9"),
    _other("power10", "ppc64le", "POWER_CPU_TYPE", "-mcpu=power10", kind=POWER_CPU_TYPE, variant="POWER10"),
    _other("power11", "ppc64le", "POWER_CPU_TYPE", "-mcpu=power11", kind=POWER_CPU_TYPE, variant="POWER11"),
    _other("VSX", "ppc64le", "SUPPORTS_VSX", "-mvsx", kind=VARIANT_FEATURE, variant="VSX"),

    # --- s390x (GGML_VXE -> -mvx -mzvector; the variants' machine levels and NNPA) -------------------------------
    _other("GGML_VXE", "s390x", "SUPPORTS_VXE2", "-mvx -mzvector", "SUPPORTS_VXE", variant="VXE2"),
    _other("NNPA", "s390x", "SUPPORTS_NNPA", "GGML_USE_NNPA", kind=VARIANT_FEATURE, variant="NNPA"),
    _other("z15", "s390x", None, "-march=z15", kind=VARIANT_FEATURE, variant="Z15",
           reason="a machine level of upstream's s390x variants (the native build reads it from /proc/cpuinfo); and "
                  + _NO_ARCH.format(arch="s390x")),
    _other("z16", "s390x", None, "-march=z16", kind=VARIANT_FEATURE, variant="Z16",
           reason="a machine level of upstream's s390x variants (the native build reads it from /proc/cpuinfo); and "
                  + _NO_ARCH.format(arch="s390x")),
    _other("z17", "s390x", None, "-march=arch15", kind=VARIANT_FEATURE, variant="Z17",
           reason="a machine level of upstream's s390x variants (the native build reads it from /proc/cpuinfo); and "
                  + _NO_ARCH.format(arch="s390x")),
)

# The architectures recipe.py builds for; the others are refused with the features the probe found.
PLANNED_ARCHES = ("x86_64", "arm64")


def rows(arch: Optional[str] = None, kind: Optional[str] = None) -> Tuple[CpuOption, ...]:
    """The rows of one architecture and/or kind, in table order."""
    return tuple(r for r in OPTIONS if (arch is None or r.arch == arch) and (kind is None or r.kind == kind))


def by_probe_key() -> Dict[str, CpuOption]:
    """The first row each probe key decides."""
    out: Dict[str, CpuOption] = {}
    for row in OPTIONS:
        if row.probe_key and row.probe_key not in out:
            out[row.probe_key] = row
    return out


def feature_label(probe_key: str) -> str:
    """A probe key's feature as plans name it: SUPPORTS_DOTPROD -> DOTPROD, POWER_CPU_TYPE -> POWER_CPU_TYPE."""
    return probe_key[len("SUPPORTS_"):] if probe_key.startswith("SUPPORTS_") else probe_key
