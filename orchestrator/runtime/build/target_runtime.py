#!/usr/bin/env python3
"""
The `runtime:` block of a target module: the recipe's plan for the target's device runtime (design 4.3, decision D14).

The hardware probe runs on the TARGET, the board or PC that will run the models. From that probe the build recipe
(orchestrator/runtime/build/recipe.py) derives how the device runtime (llama-server, llama-cli, llama-bench,
llama-perplexity) is built from the pinned commit: a cross build in the builder container for Linux devices, on a base
whose C library is not newer than the device's, with explicit CPU options from the probe, and the backends worth
building. The module generator writes this summary into every new target.yml, so a module says what its device
runtime is and that it is built from source (decision D21: the framework shares recipes, not binaries).

The block is a record, never an input: a device runtime is planned again from the device's own probe when it is
built, so a block in a community pack cannot change any build. Compiler and CMake flags typed into the module wizard
or proposed by an AI are kept under `advisory` for the reader and never applied (decision D20).
"""

from typing import Any, Dict, List, Mapping, Optional

from orchestrator.runtime.build.recipe import (X86_FEATURES, BuildPlan, PlanError, ToolchainInfo, plan_variants)
from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog
from orchestrator.runtime.profile import HardwareProfile

RUNTIME_SCHEMA_VERSION = 1
RECIPE_PATH = "orchestrator/runtime/build/recipe.py"
# The module generator's architecture names as the probe writes them (ARCH in configs/hardware_probe_schema.yml).
ARCH_OF_MODULE = {"aarch64": "arm64", "x86_64": "x86_64", "armv7l": "armv7", "riscv64": "riscv64"}
ADVISORY_NOTE = ("proposed in the module wizard or by an AI; never applied: llama.cpp's options come from the build "
                 "recipe and the device's probe")
NO_PROBE_NOTE = ("no hardware probe of the device was given: its runtime is planned from its probe when it is built "
                 "(llm-cli scan --device <address> stores one)")
_MAX_NOTES = 12


def _toolchain(profile: HardwareProfile) -> Optional[ToolchainInfo]:
    """The build host a device runtime is planned with: the framework's Linux PC for Linux devices (builder container),
    the machine itself for Windows and macOS (their binaries are built there)."""
    if profile.os == "linux":
        return ToolchainInfo(host_os="linux", host_arch="x86_64")
    return ToolchainInfo.from_profile(profile)


def _cpu_summary(plan: BuildPlan) -> Dict[str, Any]:
    options = plan.options()
    if options.get("GGML_CPU_ALL_VARIANTS") == "ON":
        return {"cpu": "all-variants"}
    if options.get("GGML_NATIVE") == "ON":
        return {"cpu": "native"}
    out: Dict[str, Any] = {"cpu": "explicit"}
    if "GGML_CPU_ARM_ARCH" in options:
        out["cpu_arm_arch"] = options["GGML_CPU_ARM_ARCH"]
    elif plan.target["arch"] == "x86_64":
        out["cpu_features"] = [name for name in X86_FEATURES.values() if options.get(name) == "ON"]
    return out


def _advisory(flags: Mapping[str, str]) -> Optional[Dict[str, str]]:
    kept = {key: " ".join(str(flags.get(key) or "").split()) for key in ("cpu_flags", "cmake_flags")}
    kept = {key: value for key, value in kept.items() if value}
    if not kept:
        return None
    kept["note"] = ADVISORY_NOTE
    return kept


def runtime_block(profile: Optional[HardwareProfile], *, architecture: str,
                  catalog: Optional[RuntimeCatalog] = None,
                  advisory: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """The runtime: block for a new target module. `profile` is the probe of the TARGET (None when none was given),
    `architecture` the module's (aarch64, x86_64, ...), `advisory` the wizard's or AI's cpu_flags/cmake_flags."""
    block: Dict[str, Any] = {"schema_version": RUNTIME_SCHEMA_VERSION, "built_from": "source", "recipe": RECIPE_PATH,
                             "purpose": "device-runtime"}
    notes: List[str] = []
    if profile is None:
        block.update({"arch": ARCH_OF_MODULE.get(architecture, architecture), "backends": ["cpu"]})
        notes.append(NO_PROBE_NOTE)
    else:
        block["arch"] = profile.arch or ARCH_OF_MODULE.get(architecture, architecture)
        if profile.os:
            block["os"] = profile.os
        block["profile_perf_key"] = profile.perf_key()
        try:
            toolchain = _toolchain(profile)
            plans = plan_variants(profile, purpose="device-runtime",
                                  placement="cross" if profile.os == "linux" else "native",
                                  catalog=catalog or load_catalog(), toolchain=toolchain)
        except PlanError as e:
            block["backends"] = []
            notes.append(f"the recipe has no device runtime plan for this probe yet: {e}")
        else:
            cpu = plans[0]
            libc, version = cpu.target.get("libc"), cpu.target.get("libc_version")
            if libc:
                block["libc"] = libc
            if libc in ("glibc", "musl") and version:
                block["libc_min"] = f"{libc}-{version}"
            block["placement"] = cpu.placement
            if cpu.builder.get("base"):
                block["builder_base"] = cpu.builder["base"]
            block.update(_cpu_summary(cpu))
            block["backends"] = [p.backend for p in plans]
            block["targets"] = list(cpu.targets)
            notes += [n for p in plans for n in p.notes if n != "backend cpu: CPU build"]
        if profile.needs_reprobe:
            notes.append("the probe is converted or incomplete: probe the device again before its runtime is built")
        if ARCH_OF_MODULE.get(architecture, architecture) != block["arch"]:
            notes.append(f"the module says {architecture}, the probe {block['arch']}: the probe decides")
    if notes:
        block["notes"] = list(dict.fromkeys(n[:300] for n in notes))[:_MAX_NOTES]
    extra = _advisory(advisory or {})
    if extra:
        block["advisory"] = extra
    return block
