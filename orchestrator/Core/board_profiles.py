#!/usr/bin/env python3
"""
A board's optimization profile and its benchmark rows: the `optimization_profiles:` and `performance_benchmarks:`
blocks of a target's target.yml (schemas in orchestrator/schemas/targets.py), made usable (DECISIONS round 23 item 10).

- The profile's source side (llama.cpp's CMake options, the compiler flags) is what the build recipe
  (orchestrator/runtime/build/recipe.py, the one place that decides them) plans for a cross build for the board's SoC.
  plan_problems() plans that build from a reference probe of the SoC (the profile's arch and CPU features) and names
  every value that differs, so the profile can never become a second planner: a test runs it for every shipped board,
  and `llm-cli targets validate` (TargetManager.validate) for any target.
- The profile's target side is the framework's recommendation for the board: the quantizations offered first
  (ordered_quantizations), the memory profile and the optimization level a build starts from when the person or the
  AI names none (optimization_for). The Builder, `llm-cli build start` and start_build use them.
- A benchmark row says where its numbers come from: measured by llmcf on a board (a board test record of
  runtime/device/tested.py, measured_row), published (address and date) or unmeasured. board_lines() shows the profile
  and the rows with that mark, labels only (never an address or a path), for `llm-cli targets boards`, Build >
  Targets... and the Builder's board line; list_targets and get_target_capabilities carry the same data.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from orchestrator.schemas.targets import (BENCHMARK_VALUES, MEASURED_VALUES, OPTIMIZATION_LEVELS, BenchmarkEntry,
                                          BenchmarkSource, BoardData, OptimizationProfile, TargetCapabilities)

DEFAULT_OPTIMIZATION = "balanced"
# The reference probe of a SoC: the facts the recipe needs for a cross build of a Linux board (decision 8: the plan
# follows the probe), with the profile's CPU features ON and every other one OFF. Its C library version only picks the
# builder base, which a profile does not name.
_REFERENCE_LIBC = ("glibc", "2.36")
_ARM_KEYS = {"neon": "SUPPORTS_NEON", "dotprod": "SUPPORTS_DOTPROD", "fp16": "SUPPORTS_FP16_ARITH",
             "i8mm": "SUPPORTS_I8MM", "sve": "SUPPORTS_SVE"}
_ARM_OFF = ("SUPPORTS_SVE2", "SUPPORTS_SME", "SUPPORTS_BF16_ARM")
_VALUE_LABELS = {"gen_ts": "generation {} tokens/s", "prompt_ts": "prompt {} tokens/s",
                 "first_token_ms": "first token {} ms", "memory_usage_mb": "memory {} MB",
                 "cpu_usage_percent": "CPU {} %"}


# The keys of a profile in the format every target.yml had before 3.2.0 (the template, Rockchip, the targets an
# earlier module wizard made, community packs built from them): descriptive only, nothing read them.
_OLD_PROFILE_KEYS = frozenset({"cpu_flags", "cmake_flags", "memory_profile", "recommended_quantization"})


def _old_profile(entry: Any) -> bool:
    """A profile in the format before 3.2.0: its `cmake_flags` line, none of the new keys (arch, cmake_options)."""
    return isinstance(entry, Mapping) and "cmake_flags" in entry and set(entry) <= _OLD_PROFILE_KEYS


def old_format_entries(meta: Mapping[str, Any]) -> List[str]:
    """The fields of a target.yml in the format before 3.2.0 (a profile with `cmake_flags`, the benchmarks as a
    mapping by model and board instead of a list of rows with their source): never read, never refused, so an older
    target or community pack still passes `llm-cli targets validate`; the Module Developer Guide shows the new format."""
    meta = meta if isinstance(meta, Mapping) else {}
    profiles = meta.get("optimization_profiles")
    found = [f"optimization_profiles.{name}" for name, entry in profiles.items() if _old_profile(entry)] \
        if isinstance(profiles, Mapping) else []
    if isinstance(meta.get("performance_benchmarks"), Mapping):
        found.append("performance_benchmarks")
    return found


def board_data(meta: Mapping[str, Any]) -> BoardData:
    """The two blocks of a target.yml's data, validated (ValueError / ValidationError when they are not valid; a
    target without them has none). Entries in the format before 3.2.0 (old_format_entries) are left out: they never
    became a profile or a benchmark row, so an old value (GGML_NATIVE=ON, -mfpu, a number without a source) is never
    used or shown."""
    meta = meta if isinstance(meta, Mapping) else {}
    profiles = meta.get("optimization_profiles")
    rows = meta.get("performance_benchmarks")
    if isinstance(profiles, Mapping):
        profiles = {name: entry for name, entry in profiles.items() if not _old_profile(entry)}
    if isinstance(rows, Mapping):
        rows = None
    return BoardData.model_validate({"optimization_profiles": profiles if profiles is not None else {},
                                     "performance_benchmarks": rows if rows is not None else []})


def check_board_data(meta: Mapping[str, Any], caps: Optional[TargetCapabilities]) -> List[Tuple[str, str]]:
    """(field, message) for everything wrong in the two blocks: the schema, a board or quantization the capabilities
    do not have, a download option, and a profile that does not agree with the recipe's plan. Empty: valid."""
    from pydantic import ValidationError
    try:
        data = board_data(meta)
    except ValidationError as e:
        return [(".".join(str(p) for p in err.get("loc", ())) or "optimization_profiles", str(err.get("msg", "")))
                for err in e.errors()]
    found = data.problems(caps)
    from orchestrator.Core.module_generator import download_flag_problems
    for board, profile in data.optimization_profiles.items():
        line = " ".join(f"-D{k}={v}" for k, v in profile.cmake_options.items())
        found += [(f"optimization_profiles.{board}.cmake_options", p) for p in download_flag_problems(line)]
        found += [(f"optimization_profiles.{board}", p) for p in plan_problems(profile)]
    return found


# --- the source side: the recipe's plan --------------------------------------------------------------------------

def reference_probe(profile: OptimizationProfile):
    """A hardware profile of the SoC the optimization profile describes: a Linux board of its architecture with its
    CPU features ON and the others OFF (the recipe treats UNKNOWN as OFF, so OFF plans the same)."""
    from orchestrator.runtime.build.recipe import X86_FEATURES
    from orchestrator.runtime.profile import HardwareProfile, load_schema
    values: Dict[str, str] = {"OS": "linux", "ARCH": profile.arch, "LIBC": _REFERENCE_LIBC[0],
                              "LIBC_VERSION": _REFERENCE_LIBC[1]}
    features = set(profile.cpu_features)
    if profile.arch == "arm64":
        for name, key in _ARM_KEYS.items():
            values[key] = "ON" if name in features or name == "neon" else "OFF"
        values.update({key: "OFF" for key in _ARM_OFF})
    else:
        for key in X86_FEATURES:
            values[key] = "ON" if key[len("SUPPORTS_"):].lower() in features else "OFF"
    return HardwareProfile(values, schema=load_schema())


def recipe_plan(profile: OptimizationProfile, catalog: Any = None):
    """The recipe's plan for the board's device runtime, a cross build from the framework's Linux PC (the plan
    `llm-cli runtime device build` makes from the board's own probe)."""
    from orchestrator.runtime.build.recipe import ToolchainInfo, plan_build
    from orchestrator.runtime.catalog import load_catalog
    return plan_build(reference_probe(profile), purpose="device-runtime", placement="cross", backend="cpu",
                      catalog=catalog or load_catalog(), toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64"))


def plan_problems(profile: OptimizationProfile, catalog: Any = None) -> List[str]:
    """Every value of the profile's source side that the recipe's plan for its SoC does not have (empty: they agree).
    A profile names a subset of the plan's options; its cpu_flags must be exactly the plan's compiler flags."""
    from orchestrator.runtime.build.recipe import PlanError
    try:
        plan = recipe_plan(profile, catalog)
    except PlanError as e:
        return [f"the build recipe has no plan for this SoC: {e}"]
    options = plan.options()
    found = [f"cmake_options {name}={value}, but the build recipe plans "
             f"{name}={options[name] if name in options else '(not set)'}"
             for name, value in profile.cmake_options.items() if options.get(name) != str(value)]
    planned = list(plan.verify.get("compile_check_flags") or [])
    if profile.cpu_flags.split() != planned:
        found.append(f"cpu_flags {profile.cpu_flags or '(none)'!r}, but the build recipe plans "
                     f"{' '.join(planned) or '(none)'!r}")
    return found


def planned_profile_values(profile: OptimizationProfile, catalog: Any = None) -> Dict[str, Any]:
    """The source side the recipe plans for the profile's SoC (GGML_NATIVE and the CPU options, the compiler flags):
    what the module generator writes into a new target's profiles."""
    plan = recipe_plan(profile, catalog)
    options = plan.options()
    keep = {k: v for k, v in options.items() if k == "GGML_NATIVE" or k == "GGML_CPU_ARM_ARCH"
            or (profile.arch == "x86_64" and k.startswith(("GGML_AVX", "GGML_SSE", "GGML_FMA", "GGML_F16C",
                                                             "GGML_BMI2", "GGML_AMX")))}
    return {"cmake_options": keep, "cpu_flags": " ".join(plan.verify.get("compile_check_flags") or [])}


_MODULE_ARCH = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64"}


def generated_profile(architecture: str, probe: Any = None, quantizations: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    """The optimization profile the module generator writes for each board of a new target: the CPU features of the
    board's probe (without one: the plain defaults of the architecture, generic ARMv8-A with NEON or x86-64), the
    source side planned by the build recipe for them, the memory profile from the probe's RAM, and the first of the
    target's quantizations. None for an architecture the recipe does not plan (armv7, riscv64)."""
    import re
    from orchestrator.runtime.build.recipe import X86_FEATURES, PlanError
    arch = _MODULE_ARCH.get(str(getattr(probe, "arch", None) or architecture or ""))
    if arch is None:
        return None
    flag = getattr(probe, "flag", None)
    if callable(flag):
        if arch == "arm64":
            features = ["neon"] + [name for name, key in _ARM_KEYS.items() if name != "neon" and flag(key) == "ON"]
        else:
            features = [key[len("SUPPORTS_"):].lower() for key in X86_FEATURES if flag(key) == "ON"]
        model = re.sub(r"[^a-z0-9.+-]+", "-", str(probe.get("CPU_MODEL") or "").lower()).strip("-")
        cpu = model if re.fullmatch(r"[a-z0-9][a-z0-9.+-]{1,63}", model) else "generic"
        ram = probe.int_value("RAM_TOTAL_MB")
    else:
        features, cpu, ram = (["neon"] if arch == "arm64" else []), "generic", None
    memory = ("low_memory" if ram < 4096 else "medium_memory" if ram < 8192 else "high_memory") if ram else \
        "medium_memory"
    base = OptimizationProfile(arch=arch, cpu=cpu, cpu_features=features, memory_profile=memory,
                               optimization="size" if memory == "low_memory" else DEFAULT_OPTIMIZATION,
                               recommended_quantization=list(quantizations)[:3] or ["Q4_K_M"])
    try:
        planned = planned_profile_values(base)
    except PlanError:
        return None
    return OptimizationProfile(**{**base.model_dump(exclude_none=True), **planned}).model_dump(exclude_none=True)


# --- the target side: the recommendation -----------------------------------------------------------------------------

def profile_for(data: Optional[BoardData], board: Optional[str]) -> Optional[OptimizationProfile]:
    if data is None or not board:
        return None
    wanted = str(board).casefold()
    return next((p for b, p in data.optimization_profiles.items() if b.casefold() == wanted), None)


def optimization_for(profile: Optional[OptimizationProfile], given: Optional[str] = None) -> str:
    """The optimization level of a build: the one given, else the board profile's, else balanced."""
    if given:
        level = str(given).strip().lower()
        if level in OPTIMIZATION_LEVELS:
            return level
    return profile.optimization if profile is not None else DEFAULT_OPTIMIZATION


def ordered_quantizations(offered: Sequence[str], profile: Optional[OptimizationProfile]) -> List[str]:
    """`offered` (what the board's format accepts) with the profile's recommended quantizations first, in the
    profile's order; a recommendation the format does not offer is left out."""
    offered = list(offered)
    if profile is None:
        return offered
    by_fold = {q.casefold(): q for q in offered}
    first = [by_fold[q.casefold()] for q in profile.recommended_quantization if q.casefold() in by_fold]
    return list(dict.fromkeys(first + offered))


# --- benchmarks ---------------------------------------------------------------------------------------------------

def rows_for(data: Optional[BoardData], board: Optional[str] = None) -> List[BenchmarkEntry]:
    if data is None:
        return []
    wanted = str(board).casefold() if board else None
    return [r for r in data.performance_benchmarks if wanted is None or r.board.casefold() == wanted]


def measured_row(entry: Mapping[str, Any], *, model: str, board: str, quantization: str) -> Optional[BenchmarkEntry]:
    """A benchmark row from a board test record (runtime/device/tested.py make_entry: gen_ts, prompt_ts, the board's
    label and the time): the values a measurement fills (MEASURED_VALUES), marked measured by llmcf on that board.
    None when the record has no measured value or no label."""
    values = {k: entry.get(k) for k in MEASURED_VALUES if isinstance(entry.get(k), (int, float))}
    label, at = entry.get("label"), str(entry.get("at") or "")[:10]
    if not values or not label or len(at) != 10:
        return None
    return BenchmarkEntry(model=model, board=board, quantization=quantization, **values,
                          source=BenchmarkSource(kind="measured", board_label=str(label)[:64], date=at))


def newest_measured(records: Iterable[Tuple[Mapping[str, Any], Mapping[str, Any]]],
                    target_id: str) -> List[BenchmarkEntry]:
    """The measured rows of board tests: `records` are (a build's manifest, one of its board test records:
    runtime/device/tested.py) pairs; of target `target_id`'s builds, the newest passed test per board, model and
    quantization, as measured_row makes it (the board's label and the date only, never a path, an address or a
    device id), newest first. A build without board, model or quantization or a model id a benchmark row cannot
    carry gives none."""
    newest: Dict[Tuple[str, str, str], Tuple[str, BenchmarkEntry]] = {}
    wanted = str(target_id or "").casefold()
    for info, entry in records:
        board, model, quant = info.get("board"), info.get("model_source"), info.get("quantization")
        if str(info.get("target") or "").casefold() != wanted or not (board and model and quant):
            continue
        if not (entry.get("smoke_passed") and entry.get("checks_passed")):
            continue
        try:
            row = measured_row(entry, model=str(model), board=str(board), quantization=str(quant))
        except ValueError:   # a model id or label a benchmark row cannot carry
            continue
        at = str(entry.get("at") or "")
        key = (row.board.casefold(), row.model, row.quantization.casefold()) if row else None
        if key is not None and (key not in newest or at > newest[key][0]):
            newest[key] = (at, row)
    return [row for _, row in sorted(newest.values(), key=lambda pair: pair[0], reverse=True)]


def old_format_note(names: Sequence[str]) -> str:
    """The plain note for the entries in the format before 3.2.0 that were ignored (old_format_entries): "" for
    none."""
    names = list(names)
    return f"{len(names)} entries in the old format were ignored: {', '.join(names)}" if names else ""


def row_text(row: BenchmarkEntry) -> str:
    """One benchmark row as plain text, its source mark at the end (an unmeasured value never reads as measured)."""
    values = ", ".join(_VALUE_LABELS[k].format(_number(v)) for k, v in row.values().items())
    return f"{row.model} {row.quantization}: {values or 'no values'} [{row.source.label()}]"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def profile_text(profile: OptimizationProfile) -> List[str]:
    options = " ".join(f"{k}={v}" for k, v in profile.cmake_options.items()) or "-"
    lines = [f"recommended quantization: {', '.join(profile.recommended_quantization)}",
             f"memory profile: {profile.memory_profile.replace('_', ' ')}; optimization level: {profile.optimization}",
             f"llama.cpp options (from the build recipe): {options}; compiler flags: {profile.cpu_flags or '-'}",
             f"CPU: {profile.cpu} ({', '.join(profile.cpu_features) or 'no optional features'})"]
    if profile.note:
        lines.append(f"note: {profile.note}")
    return lines


def board_lines(caps: Optional[TargetCapabilities], data: Optional[BoardData],
                boards: Optional[Iterable[str]] = None) -> List[str]:
    """Every board of a target (or only `boards`) with its label, its profile and its benchmark rows, each row marked
    measured, published or unmeasured. Plain text from target.yml: labels, never an address or a path."""
    names = list(boards) if boards is not None else (list(caps.boards) if caps is not None else
                                                     list((data.optimization_profiles if data else {}).keys()))
    lines: List[str] = []
    for board in names:
        info = caps.boards.get(board) if caps is not None else None
        lines.append(f"{board}: {info.label if info is not None else board}")
        profile = profile_for(data, board)
        lines += [f"  {line}" for line in (profile_text(profile) if profile else ["no optimization profile"])]
        rows = rows_for(data, board)
        lines += [f"  benchmark: {row_text(row)}" for row in rows] or ["  benchmark: none recorded"]
    return lines


def benchmark_fields_text() -> str:
    """Which values a benchmark row has and which ones a measurement on the board fills (for docs and help texts)."""
    return (f"values: {', '.join(BENCHMARK_VALUES)}; a board test fills {', '.join(MEASURED_VALUES)} "
            "(llama-bench on the board, device_tests.json)")


__all__ = ["DEFAULT_OPTIMIZATION", "benchmark_fields_text", "board_data", "board_lines", "check_board_data",
           "measured_row", "newest_measured", "old_format_entries", "old_format_note", "optimization_for", "ordered_quantizations", "plan_problems", "planned_profile_values",
           "profile_for", "profile_text", "recipe_plan", "reference_probe", "row_text", "rows_for"]
