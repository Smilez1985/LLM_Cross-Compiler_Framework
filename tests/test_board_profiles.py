"""The optimization profiles and benchmark rows of target.yml are usable data (DECISIONS round 23 item 10).

- Every board's profile agrees with the build recipe's plan for its SoC (the recipe stays the one planner): no -mfpu
  on aarch64, GGML_NATIVE OFF for a cross build, no download option, the same -march.
- Every benchmark row names its source; an unmeasured value is shown as unmeasured, never as a measurement; a board
  test record becomes a measured row.
- The target side (recommended quantizations, optimization level) feeds the choices of a build.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from conftest import REPO_ROOT
from orchestrator.Core import board_profiles as bp
from orchestrator.schemas.targets import (BenchmarkEntry, BenchmarkSource, OptimizationProfile, TargetCapabilities)

TARGETS = REPO_ROOT / "targets"


def meta_of(folder: str) -> dict:
    return yaml.safe_load((TARGETS / folder / "target.yml").read_text(encoding="utf-8"))


def profile(**changes) -> OptimizationProfile:
    values = dict(arch="arm64", cpu="cortex-a55", cpu_features=["neon", "dotprod", "fp16"],
                  cmake_options={"GGML_NATIVE": "OFF", "GGML_CPU_ARM_ARCH": "armv8.2-a+dotprod+fp16"},
                  cpu_flags="-march=armv8.2-a+dotprod+fp16", memory_profile="low_memory", optimization="size",
                  recommended_quantization=["Q4_0", "Q4_K_M"])
    values.update(changes)
    return OptimizationProfile(**values)


# --- the source side agrees with the recipe ----------------------------------------------------------------------

def test_every_rockchip_board_has_a_profile_that_agrees_with_the_recipe_plan():
    meta = meta_of("Rockchip")
    caps = TargetCapabilities.model_validate(meta["capabilities"])
    data = bp.board_data(meta)
    assert set(data.optimization_profiles) == set(caps.boards)
    for board, prof in data.optimization_profiles.items():
        assert bp.plan_problems(prof) == [], board
    assert bp.check_board_data(meta, caps) == []


@pytest.mark.parametrize("board,arch", [("rk3566", "armv8.2-a+dotprod+fp16"), ("rk3568", "armv8.2-a+dotprod+fp16"),
                                        ("rk3576", "armv8-a"), ("rk3588", "armv8.2-a+dotprod+fp16")])
def test_rockchip_profiles_carry_the_planned_arm_architecture(board, arch):
    prof = bp.board_data(meta_of("Rockchip")).optimization_profiles[board]
    assert prof.cmake_options == {"GGML_NATIVE": "OFF", "GGML_CPU_ARM_ARCH": arch}
    assert prof.cpu_flags == f"-march={arch}"
    assert "-mfpu" not in prof.cpu_flags


def test_the_template_profiles_are_valid_and_the_old_comment_is_gone():
    for folder in ("Rockchip", "_template"):
        text = (TARGETS / folder / "target.yml").read_text(encoding="utf-8")
        assert "optimization profiles: descriptive, nothing reads them" not in text
        bp.board_data(yaml.safe_load(text))   # no ValidationError


def test_aarch32_options_and_native_builds_are_refused():
    with pytest.raises(ValidationError, match="AArch32"):
        profile(cpu_flags="-mcpu=cortex-a55 -mfpu=neon-fp-armv8")
    with pytest.raises(ValidationError, match="GGML_NATIVE must be OFF"):
        profile(cmake_options={"GGML_NATIVE": "ON"})
    with pytest.raises(ValidationError, match="native"):
        profile(cpu_flags="-march=native")


def test_a_profile_that_differs_from_the_plan_is_named():
    problems = bp.plan_problems(profile(cpu_features=["neon"]))   # the plan then is armv8-a
    assert any("GGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16" in p and "plans GGML_CPU_ARM_ARCH=armv8-a" in p
               for p in problems), problems
    assert any("cpu_flags" in p for p in problems)


def test_a_download_option_in_a_profile_is_refused():
    meta = meta_of("Rockchip")
    meta["optimization_profiles"]["rk3566"]["cmake_options"]["LLAMA_BUILD_TESTS"] = "ON"
    caps = TargetCapabilities.model_validate(meta["capabilities"])
    problems = bp.check_board_data(meta, caps)
    assert any("LLAMA_BUILD_TESTS" in m and "download" in m for _, m in problems), problems


def test_an_unknown_board_or_quantization_is_named():
    meta = meta_of("Rockchip")
    meta["optimization_profiles"]["rk9999"] = dict(meta["optimization_profiles"]["rk3566"])
    meta["optimization_profiles"]["rk3576"]["recommended_quantization"] = ["Q4_K_M", "XYZ"]
    caps = TargetCapabilities.model_validate(meta["capabilities"])
    fields = dict(bp.check_board_data(meta, caps))
    assert "optimization_profiles.rk9999" in fields
    assert "XYZ" in fields["optimization_profiles.rk3576.recommended_quantization"]


def test_targets_validate_reports_profile_problems(tmp_path):
    from orchestrator.Core.target_manager import TargetManager
    shutil.copytree(TARGETS / "Rockchip", tmp_path / "Rockchip", ignore=shutil.ignore_patterns("*.sh", "scripts"))
    path = tmp_path / "Rockchip" / "target.yml"
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta["optimization_profiles"]["rk3588"]["cpu_flags"] = "-mcpu=cortex-a76 -mfpu=neon-fp-armv8"
    path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")

    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(tmp_path)), resolve_path=Path))
    check = tm.validate("Rockchip", reread=True)
    assert not check.valid
    assert any(field.startswith("optimization_profiles") and "AArch32" in message for field, message in check.errors)


# --- the target side feeds the choices --------------------------------------------------------------------------

def test_recommended_quantizations_come_first_and_only_offered_ones():
    prof = profile(recommended_quantization=["Q4_0", "IQ4_NL", "NOPE"])
    assert bp.ordered_quantizations(["Q4_K_M", "q4_0", "IQ4_NL", "Q8_0"], prof) == ["q4_0", "IQ4_NL", "Q4_K_M",
                                                                                       "Q8_0"]
    assert bp.ordered_quantizations(["Q4_K_M"], None) == ["Q4_K_M"]


def test_the_optimization_level_comes_from_the_profile_unless_one_is_given():
    prof = profile(optimization="size")
    assert bp.optimization_for(prof) == "size"
    assert bp.optimization_for(prof, "speed") == "speed"
    assert bp.optimization_for(None) == "balanced"
    assert bp.optimization_for(prof, "nonsense") == "size"


# --- benchmarks: every number has a source -------------------------------------------------------------------

def test_every_shipped_benchmark_row_names_its_source_and_unmeasured_ones_say_so():
    rows = bp.board_data(meta_of("Rockchip")).performance_benchmarks
    assert rows
    for row in rows:
        text = bp.row_text(row)
        if row.source.kind == "unmeasured":
            assert "unmeasured" in text and "measured by" not in text
        assert row.source.kind in ("measured", "published", "unmeasured")


def test_board_lines_mark_every_row_and_show_the_profile():
    meta = meta_of("Rockchip")
    caps = TargetCapabilities.model_validate(meta["capabilities"])
    lines = bp.board_lines(caps, bp.board_data(meta), ["rk3566"])
    text = "\n".join(lines)
    assert text.startswith("rk3566: RK3566")
    assert "recommended quantization: Q4_0, IQ4_NL, Q4_K_M" in text
    assert "GGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16" in text
    assert "generation 12 tokens/s" in text and "[unmeasured" in text


def test_published_and_measured_values_need_their_source():
    with pytest.raises(ValidationError, match="url"):
        BenchmarkSource(kind="published", date="2026-10-29")
    with pytest.raises(ValidationError, match="label"):
        BenchmarkSource(kind="measured", date="2026-10-29")
    with pytest.raises(ValidationError, match="unmeasured"):
        BenchmarkSource(kind="unmeasured", url="https://example.com/bench")
    src = BenchmarkSource(kind="published", url="https://example.com/bench", date="2026-10-29")
    assert src.label() == "published: https://example.com/bench (read 2026-10-29)"


def test_a_board_test_record_becomes_a_measured_row():
    entry = {"label": "desk board", "at": "2026-10-28T10:00:00Z", "gen_ts": 9.5, "prompt_ts": 41.25,
             "device_id": "a" * 16}
    row = bp.measured_row(entry, model="ibm-granite/granite-4.0-h-350m", board="rk3566", quantization="Q4_0")
    assert isinstance(row, BenchmarkEntry) and row.measured
    assert bp.row_text(row) == ("ibm-granite/granite-4.0-h-350m Q4_0: generation 9.5 tokens/s, prompt 41.2 tokens/s "
                                "[measured by llmcf (board desk board, 2026-10-28)]")
    assert bp.measured_row({"label": "x", "at": "2026-10-28T10:00:00Z"}, model="m", board="b",
                           quantization="q") is None   # nothing measured: no row


@pytest.mark.parametrize("with_probe", [False, True])
def test_the_module_generator_writes_a_profile_per_board_that_agrees_with_the_plan(tmp_path, with_probe):
    from orchestrator.Core.module_generator import ModuleGenerator
    from orchestrator.runtime.profile import read_profile
    probe = read_profile(REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt") \
        if with_probe else None
    (tmp_path / "targets").mkdir()
    folder = ModuleGenerator(tmp_path / "targets").generate_module(
        dict(module_name="Zero Board", architecture="aarch64", supported_boards=["Zero A", "Zero B"]), profile=probe)
    meta = yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8"))
    caps = TargetCapabilities.model_validate(meta["capabilities"])
    assert set(meta["optimization_profiles"]) == set(caps.boards) == {"zero_a", "zero_b"}
    assert bp.check_board_data(meta, caps) == []
    prof = bp.board_data(meta).optimization_profiles["zero_a"]
    expected = "armv8.2-a+dotprod+fp16" if with_probe else "armv8-a"   # the probe's features, else plain ARMv8-A
    assert prof.cmake_options["GGML_CPU_ARM_ARCH"] == expected
    assert prof.memory_profile == ("low_memory" if with_probe else "medium_memory")


# --- wired: the build service, the CLI, the MCP tools, the GUI ------------------------------------------------

def shipped_service():
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.target_manager import TargetManager
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(TARGETS)), resolve_path=Path))
    framework = SimpleNamespace(target_manager=tm, config=SimpleNamespace(get=lambda k, d=None: d),
                                resolve_path=lambda p: p)
    return BuildService(framework)


def test_the_optimization_level_of_a_build_follows_the_board_profile():
    from orchestrator.schemas.build import BuildRequestIn, StartBuildIn
    service = shipped_service()
    model = "HuggingFaceTB/SmolLM2-135M-Instruct"
    assert service.optimization_for(BuildRequestIn(model=model, target="Rockchip", format="gguf", board="rk3566")) == "size"
    assert service.optimization_for(BuildRequestIn(model=model, target="Rockchip", format="gguf", board="rk3588")) == "balanced"
    # no board named: the board the scripts fall back to (default_board rk3588)
    assert service.optimization_for(BuildRequestIn(model=model, target="Rockchip", format="gguf")) == "balanced"
    assert service.optimization_for(BuildRequestIn(model=model, target="Rockchip", format="gguf", board="rk3566"), "speed") == "speed"
    assert StartBuildIn(model=model, target="Rockchip", format="gguf").optimization is None   # an AI caller may leave it out


def test_list_targets_carries_the_profiles_and_marked_benchmarks():
    target = next(t for t in shipped_service().list_targets().targets if t.id == "Rockchip")
    assert target.board_profiles["rk3566"].recommended_quantization[0] == "Q4_0"
    assert target.benchmarks and all(r.source_label.startswith("unmeasured") for r in target.benchmarks)

