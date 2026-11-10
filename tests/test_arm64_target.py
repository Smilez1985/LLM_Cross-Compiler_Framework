"""The generic ARM64 CPU target (targets/ARM64; DESIGN, "Targets, hardware and templates", the row of the target).

- Its target.yml validates; its board profiles (bcm2711: Raspberry Pi 4, bcm2712: Raspberry Pi 5) pass the existing
  profile rules and are the build recipe's plan for each SoC; the target side is ordered for each board.
- A build for each board plans the expected compiler and CMake options in the stage modules, with or without the
  board's probe, and makes the board package (llama-cli and llama-server cross-built); what the target cannot build is
  refused with the reason.
- A probe of a Raspberry Pi 4 and of a Raspberry Pi 5 maps to the right board; the recipe plans the device runtime of
  either probe as the board's profile says.
- A build request passes the pre-flight check without SDK findings; the target shows in the CLI, the Builder and the
  AI tools.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import REPO_ROOT
from test_build_stages import Image, needs_bash, plan_of, stage_lines
from test_review_capabilities_paths import (
    gui_messages,  # noqa: F401 - the Builder tab's recorded message boxes
)

from orchestrator.Core import board_profiles as bp
from orchestrator.schemas.targets import TargetCapabilities

ARM64 = REPO_ROOT / "targets" / "ARM64"
PROBES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"
PI4_PROBE = PROBES / "probe_rpi4_raspios.txt"
PI5_PROBE = PROBES / "probe_rpi5_raspios.txt"
PI4_FLAGS = "armv8-a"
PI5_FLAGS = "armv8.2-a+dotprod+fp16"
BOARDS = {"bcm2711": ("Cortex-A72", PI4_FLAGS), "bcm2712": ("Cortex-A76", PI5_FLAGS)}
PAD = {"STUB_PAD": 1 << 20}   # the package's input check of 1.x wants a model of at least 1 MiB


def meta() -> dict:
    return yaml.safe_load((ARM64 / "target.yml").read_text(encoding="utf-8"))


def caps() -> TargetCapabilities:
    return TargetCapabilities.model_validate(meta()["capabilities"])


def targets_manager(folder: Path = REPO_ROOT / "targets"):
    from orchestrator.Core.target_manager import TargetManager
    return TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(folder)), resolve_path=Path))


# --- the target's data ---------------------------------------------------------------------------------------------

def test_the_target_validates_and_needs_no_sdk():
    check = targets_manager().validate("arm64")
    assert check.valid and check.target_id == "ARM64" and check.boards == 2 and check.formats == ("gguf",)
    c = caps()
    assert list(c.boards) == ["bcm2711", "bcm2712"] and c.default_board == "bcm2712"
    assert c.boards["bcm2711"].ram_max_gb == 8 and c.boards["bcm2712"].ram_max_gb == 16
    gguf = c.formats["gguf"]
    assert gguf.sdk is None, "a CPU target asks nothing of the board before a build"
    assert gguf.default_for("bcm2711") == gguf.default_for("bcm2712") == "Q4_K_M"
    assert c.normalize("gguf", None, None) == ("gguf", "bcm2712", "Q4_K_M")
    assert c.check_request("gguf", "bcm2711", "IQ4_XS", imatrix=True) == []
    assert c.check_request("rkllm", "bcm2712", None)[0].startswith("Format 'rkllm' is not available")


def test_the_gguf_types_are_the_config_stage_s():
    """The capabilities list every type of the config stage (GGUF_CONVERT_TYPES and GGUF_QUANT_TYPES), like
    Rockchip's: the same llama.cpp pin."""
    import re
    text = (ARM64 / "modules" / "config_module.sh").read_text(encoding="utf-8")
    quant = re.search(r'GGUF_QUANT_TYPES=" ([^"]*)"', text, re.DOTALL).group(1).replace("\\\n", " ").split()
    listed = set(caps().formats["gguf"].quantizations)
    assert set(quant) | {"F16", "BF16", "F32"} == listed
    rockchip = yaml.safe_load((REPO_ROOT / "targets" / "Rockchip" / "target.yml").read_text(encoding="utf-8"))
    assert set(rockchip["capabilities"]["formats"]["gguf"]["quantizations"]) == listed


def test_the_config_stage_s_board_defaults_are_the_board_profiles():
    """BOARD_DEFAULTS of config_module.sh (the plan without a probe) is data: one row per board, the same cores and
    features as the board's optimization profile, so a new board is a row and a profile."""
    import re
    text = (ARM64 / "modules" / "config_module.sh").read_text(encoding="utf-8")
    rows = re.search(r"^BOARD_DEFAULTS=\(\n(.*?)\n\)", text, re.S | re.M).group(1)
    table = {}
    for line in rows.splitlines():
        board, cpu, cores, features = line.strip().strip('"').split()
        table[board] = (cpu.lower(), int(cores), [] if features == "-" else features.split(","))
    profiles = bp.board_data(meta()).optimization_profiles
    assert set(table) == set(profiles) == set(caps().boards)
    for board, (cpu, cores, features) in table.items():
        prof = profiles[board]
        assert (cpu, ["neon", *features]) == (prof.cpu, prof.cpu_features), board
        assert f"{cores}x" in caps().boards[board].cpu, board


def test_every_board_profile_passes_the_profile_rules_and_is_the_recipe_s_plan():
    data = bp.board_data(meta())
    assert set(data.optimization_profiles) == set(caps().boards)
    assert bp.check_board_data(meta(), caps()) == []
    for board, prof in data.optimization_profiles.items():
        assert bp.plan_problems(prof) == [], board
    assert data.performance_benchmarks == [], "no estimate is shipped as a number"


@pytest.mark.parametrize("board, cpu, features, arch", [
    ("bcm2711", "cortex-a72", ["neon"], PI4_FLAGS),
    ("bcm2712", "cortex-a76", ["neon", "dotprod", "fp16"], PI5_FLAGS)])
def test_each_board_has_its_cores_options_and_flags(board, cpu, features, arch):
    prof = bp.board_data(meta()).optimization_profiles[board]
    assert (prof.arch, prof.cpu, prof.cpu_features) == ("arm64", cpu, features)
    assert prof.cmake_options == {"GGML_NATIVE": "OFF", "GGML_CPU_ARM_ARCH": arch}
    assert prof.cpu_flags == f"-march={arch}" and "-mfpu" not in prof.cpu_flags and "-mcpu" not in prof.cpu_flags
    plan = bp.recipe_plan(prof)
    assert plan.options()["GGML_NATIVE"] == "OFF" and plan.options()["GGML_CPU_ARM_ARCH"] == arch
    assert "i8mm" not in arch and "sve" not in arch, "neither core has int8 matrix multiply or SVE"


@pytest.mark.parametrize("board, memory, order", [
    ("bcm2711", "medium_memory", ["Q4_K_M", "Q4_0", "IQ4_XS", "Q5_K_M"]),
    ("bcm2712", "high_memory", ["Q4_K_M", "Q4_0", "IQ4_NL", "Q5_K_M", "Q6_K", "Q8_0"])])
def test_the_target_side_is_ordered_for_each_board(board, memory, order):
    prof = bp.board_data(meta()).optimization_profiles[board]
    assert prof.memory_profile == memory and prof.recommended_quantization == order
    assert bp.optimization_for(prof) == "balanced" and bp.optimization_for(prof, "size") == "size"
    offered = bp.ordered_quantizations(caps().formats["gguf"].quantizations_for(board), prof)
    assert offered[:len(order)] == order and set(offered) == set(caps().formats["gguf"].quantizations)
    assert "Threads: the four cores" in prof.note


def test_the_pi5_s_recommendations_are_the_types_llama_cpp_repacks_with_the_dot_product():
    """At the pin (ggml/src/ggml-cpu/repack.cpp, ggml_repack_get_optimal_repack_type) NEON with the dot product
    repacks Q4_0, IQ4_NL and Q8_0 (4x4) and Q4_K, Q5_K and Q6_K (8x4); Q4_K_M and Q5_K_M are made of those K types."""
    repacked = {"Q4_0", "IQ4_NL", "Q8_0", "Q4_K_M", "Q5_K_M", "Q6_K"}
    prof = bp.board_data(meta()).optimization_profiles["bcm2712"]
    assert set(prof.recommended_quantization) <= repacked


def test_the_boards_lines_show_both_profiles():
    lines = "\n".join(bp.board_lines(caps(), bp.board_data(meta()), list(caps().boards)))
    assert "bcm2711: Raspberry Pi 4" in lines and "bcm2712: Raspberry Pi 5" in lines
    assert f"GGML_CPU_ARM_ARCH={PI5_FLAGS}" in lines and f"GGML_CPU_ARM_ARCH={PI4_FLAGS}" in lines
    assert "recommended quantization: Q4_K_M, Q4_0, IQ4_NL, Q5_K_M, Q6_K, Q8_0" in lines


# --- the build plan of the stage modules (the image's stubs of tests/test_build_stages.py) --------------------------

@pytest.fixture
def image(tmp_path):
    return Image(tmp_path, ARM64)


def arm64_build(image, **values):
    return image.run(**{"TARGET_ARCH": "arm64", "LLMCF_TARGET_LABEL": "ARM64", "RKLLM_PYTHON": None,
                        "RKNN_PYTHON": None, **PAD, **values})


def manifest(image) -> dict:
    (path,) = [p for p in (image.output / "packages").glob("*/MANIFEST.json") if not p.parent.is_symlink()]
    return json.loads(path.read_text(encoding="utf-8"))


@needs_bash
@pytest.mark.parametrize("board", list(BOARDS))
def test_a_build_without_a_probe_plans_the_board_defaults_and_makes_the_package(image, board):
    cpu, arch = BOARDS[board]
    result = arm64_build(image, TARGET_BOARD=board)

    assert result.returncode == 0, result.stdout + result.stderr
    plan = plan_of(image)
    assert (plan["BOARD_PACKAGE"], plan["PROFILE_ARCH"], plan["TARGET_CPU"]) == ("1", "aarch64", cpu)
    assert plan["CFLAGS"].split()[-1] == f"-march={arch}" and "-mcpu" not in plan["CFLAGS"]
    assert "-mfpu" not in plan["CFLAGS"]
    assert plan["CMAKE_FLAGS"].split() == ["-DGGML_NATIVE=OFF", f"-DGGML_CPU_ARM_ARCH={arch}"]
    assert f"the board defaults of {board} ({cpu}" in result.stdout
    assert image.build_info()["board"] == board
    package = manifest(image)
    assert package["hardware"]["source"] == "defaults"
    assert package["hardware"]["cpu_features"]["used"] == ([] if board == "bcm2711" else ["DOTPROD", "FP16_ARITH"])
    names = {p.name for p in (image.output / "packages").glob("*/*")}
    assert {"llama-cli", "llama-server", "MANIFEST.json", "SHA256SUMS", "deploy.sh", "test_model.sh"} <= names


@needs_bash
@pytest.mark.parametrize("board, probe", [("bcm2711", PI4_PROBE), ("bcm2712", PI5_PROBE)])
def test_a_build_with_the_board_s_probe_plans_from_the_probe_the_same_options(image, board, probe):
    cpu, arch = BOARDS[board]
    result = arm64_build(image, TARGET_BOARD=board, HARDWARE_CONFIG_FILE=probe)

    assert result.returncode == 0, result.stdout + result.stderr
    plan = plan_of(image)
    assert (plan["TARGET_CPU"], plan["CFLAGS"].split()[-1]) == (cpu, f"-march={arch}")
    assert manifest(image)["hardware"]["source"] == "profile"


@needs_bash
def test_a_32_bit_system_on_the_board_is_refused_with_the_way_to_the_64_bit_one(image):
    profile = image.root / "pi32.txt"
    profile.write_text("PROBE_SCHEMA=2\nARCH=armv7\nCPU_MODEL=Cortex-A72\nSOC_MODEL=bcm2711\n")
    result = arm64_build(image, TARGET_BOARD="bcm2711", HARDWARE_CONFIG_FILE=profile)

    assert result.returncode == 1
    assert "the boards of this target have 64-bit ARM CPUs" in result.stderr and "64-bit Raspberry Pi OS" in result.stderr
    assert ("2/4", "config", "failed") in stage_lines(result)


@needs_bash
def test_an_npu_format_is_refused_by_the_config_stage(image):
    result = arm64_build(image, TARGET_BOARD="bcm2712", TARGET_FORMAT="rkllm")
    assert result.returncode == 1
    assert "Format 'rkllm' is not supported by the generic ARM64 target" in result.stderr
    assert image.calls() == []


@needs_bash
def test_the_imatrix_job_plans_no_board(image):
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    image.output.rmdir()
    result = arm64_build(image, TARGET_BOARD="bcm2712", JOB_TYPE="imatrix", DATASET_PATH=dataset)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "board defaults" not in result.stdout and "Compiler test" not in result.stdout
    assert [call.split()[0] for call in image.calls()] == ["convert", "imatrix"]


def test_the_image_builds_the_tools_like_rockchip_and_keeps_what_the_stages_run():
    """The builder stage fetches and builds llama.cpp as every target does (tests/test_target_dockerfiles.py compares
    the steps); the image gets the conversion venv, the source tree with its tools and the aarch64 compilers."""
    from test_target_dockerfiles import defaults, stages
    text = (ARM64 / "Dockerfile").read_text(encoding="utf-8")
    (builder_from, _builder), (image_from, image) = stages(text)
    assert builder_from == "debian:bookworm-slim AS builder" and image_from == "debian:bookworm-slim"
    copies = {rest for word, rest in image if word == "COPY" and rest.startswith("--from=")}
    assert copies == {f"--from=builder {p} {p}" for p in ("/usr/src/llama.cpp", "/opt/venv")}
    env = defaults("\n".join(f"{word} {rest}" for word, rest in image if word == "ENV"))
    assert env["VIRTUAL_ENV"] == "/opt/venv" and env["LLAMA_CPP_PATH"] == "/usr/src/llama.cpp"
    assert "RKLLM_PYTHON" not in env and "RKNN_PYTHON" not in env
    assert image[-3:] == [("USER", "llmbuilder"), ("ENTRYPOINT", '["/entrypoint.sh"]'), ("CMD", '["interactive"]')]


def test_the_image_has_no_vendor_sdk():
    text = (ARM64 / "Dockerfile").read_text(encoding="utf-8")
    for word in ("venv-rkllm", "venv-rknn", "RKLLM", "RKNN", "libgl1"):
        assert word not in text, word
    assert "gcc-aarch64-linux-gnu g++-aarch64-linux-gnu" in text
    for name in ("rkllm_module.sh", "rknn_module.sh"):
        assert not (ARM64 / "modules" / name).exists()
    for name in ("export_rkllm.py", "rknn_converter.py"):
        assert not (ARM64 / "scripts" / name).exists()


# --- the hardware probe of a Raspberry Pi ---------------------------------------------------------------------------

@pytest.mark.skipif(__import__("os").name != "posix", reason="the probe's sysroots are POSIX")
@pytest.mark.parametrize("kind, board", [("rpi4", "bcm2711"), ("rpi5", "bcm2712")])
def test_a_probe_of_a_raspberry_pi_maps_to_its_board(tmp_path, kind, board):
    from test_hardware_probe import make_sysroot, probe, py_probe

    from orchestrator.runtime.probe_service import match_target
    from orchestrator.runtime.profile import parse_profile
    profile = parse_profile(probe.render_text(py_probe(make_sysroot(tmp_path, kind))))
    tm = targets_manager()
    tm.initialize()
    found = match_target(profile, tm)
    assert (found.target, found.board) == ("ARM64", board), found


@pytest.mark.parametrize("path, board, arch", [(PI4_PROBE, "bcm2711", PI4_FLAGS), (PI5_PROBE, "bcm2712", PI5_FLAGS)])
def test_the_stored_probes_map_to_their_board_and_plan_as_its_profile(path, board, arch):
    """The recipe plans the board's device runtime (`llm-cli device runtime build`) from its own probe exactly as the
    board's profile says, and the build service takes the probe for that board only."""
    from orchestrator.runtime.build.recipe import ToolchainInfo, plan_build
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.probe_service import match_target, soc_fits
    from orchestrator.runtime.profile import read_profile
    profile = read_profile(path)
    tm = targets_manager()
    tm.initialize()
    found = match_target(profile, tm)
    assert (found.target, found.board) == ("ARM64", board)
    assert caps().canonical_board(profile.get("SOC_MODEL")) == board
    other = "bcm2712" if board == "bcm2711" else "bcm2711"
    assert soc_fits(profile.get("SOC_MODEL"), board) and not soc_fits(profile.get("SOC_MODEL"), other)
    plan = plan_build(profile, purpose="device-runtime", placement="cross", backend="cpu", catalog=load_catalog(),
                      toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64"))
    assert plan.options()["GGML_CPU_ARM_ARCH"] == arch and plan.options()["GGML_NATIVE"] == "OFF"
    prof = bp.board_data(meta()).optimization_profiles[board]
    assert plan.options()["GGML_CPU_ARM_ARCH"] == prof.cmake_options["GGML_CPU_ARM_ARCH"]


def test_a_rockchip_probe_does_not_map_to_the_generic_target():
    from orchestrator.runtime.probe_service import match_target
    from orchestrator.runtime.profile import read_profile
    tm = targets_manager()
    tm.initialize()
    found = match_target(read_profile(PROBES / "schema2_rk3588_devuan_sysvinit.txt"), tm)
    assert (found.target, found.board) == ("Rockchip", "rk3588")


# --- the pre-flight check and the listings (CLI, Builder, AI tools) -------------------------------------------------

def store(state: Path, name: str, path: Path) -> str:
    from orchestrator.runtime.profile import ProfileStore, read_profile
    device_id = f"{abs(hash(name)) % 16 ** 16:016x}"
    ProfileStore(state).save_device(device_id, read_profile(path), label=name)
    return device_id


@pytest.fixture
def service(tmp_path):
    import asyncio

    from test_build_tools import Cfg, Orchestrator, Service

    from orchestrator.Core.target_manager import TargetManager
    framework = SimpleNamespace(config=Cfg(tmp_path), resolve_path=lambda p: Path(p), orchestrator=Orchestrator())
    framework.run_coroutine = lambda coro, timeout=None: asyncio.run(coro)
    framework.target_manager = TargetManager(framework)
    svc = Service(framework)
    state = Path(framework.config.user_state_dir)
    svc.pi4 = store(state, "pi4", PI4_PROBE)
    svc.pi5 = store(state, "pi5", PI5_PROBE)
    return svc


@pytest.mark.parametrize("board", list(BOARDS))
def test_a_build_request_passes_the_pre_flight_check_without_sdk_findings(service, board):
    from orchestrator.Core.build_service import BuildService
    probe = service.pi4 if board == "bcm2711" else service.pi5
    result = service.preflight_for("ARM64", "gguf", board, probe)
    assert result.findings == [] and not result.stopped and result.unchecked == []
    request = BuildService.request_in(model="Qwen/Qwen2.5-0.5B-Instruct", target="ARM64", format="gguf",
                                      board=board, probe=probe)
    service.start_build(request, local_paths=True)
    (sent,) = service.framework.orchestrator.requests
    assert sent.preflight_notes == [] and sent.targets == ["ARM64"] and sent.target_board == board
    assert sent.quantization_options == ["Q4_K_M"] and sent.optimization_level.value == "balanced"


def test_a_probe_of_the_other_pi_is_refused_for_a_board(service):
    from orchestrator.Core.build_service import BuildService, BuildServiceError
    request = BuildService.request_in(model="Qwen/Qwen2.5-0.5B-Instruct", target="ARM64", format="gguf",
                                      board="bcm2711", probe=service.pi5)
    with pytest.raises(BuildServiceError, match="SoC bcm2712, not of bcm2711"):
        service.start_build(request, local_paths=True)


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(booted_framework, tmp_path, monkeypatch)


def test_the_cli_lists_validates_shows_and_builds_the_target(cli):
    result = cli.invoke("targets", "list", "-f", "json")
    data = {t["id"]: t for t in json.loads(result.out[result.out.index("["):])}
    assert data["ARM64"]["name"] == "Generic ARM64 (CPU)" and sorted(data["ARM64"]["boards"]) == ["bcm2711", "bcm2712"]
    assert [f["id"] for f in data["ARM64"]["formats"]] == ["gguf"]
    assert cli.invoke("targets", "validate", "arm64").code == 0
    boards = cli.invoke("targets", "boards", "ARM64")
    assert boards.code == 0 and "bcm2712: Raspberry Pi 5" in " ".join(boards.out.split())
    result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "arm64", "-b", "bcm2711")
    assert result.code == 0, result.out
    sent = cli.submitted[-1]
    assert (sent.targets, sent.target_board, sent.quantization_options) == (["ARM64"], "bcm2711", ["Q4_K_M"])


def test_the_ai_tools_list_the_target_and_its_capabilities(booted_framework):
    from orchestrator.tools.policy import ToolPolicy
    from orchestrator.tools.registry import ToolContext
    registry = booted_framework.tool_registry
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))

    def call(name, arguments):
        return registry.call(name, arguments, ToolContext("ditto", session="s"))

    listed = {t.id: t for t in call("list_targets", {}).targets}
    assert listed["ARM64"].trust == "shipped"
    details = call("get_target_capabilities", {"target": "ARM64"})
    assert set(details.boards) == {"bcm2711", "bcm2712"} and list(details.formats) == ["gguf"]
    assert details.formats["gguf"].sdk is None


@pytest.mark.usefixtures("qapp", "gui_messages")
def test_the_builder_offers_the_target_its_boards_and_the_board_s_order(booted_framework):
    from test_review_capabilities_paths import make_tab, select, submit
    tab = make_tab(booted_framework, target="ARM64")
    try:
        assert tab.target_combo.findData("ARM64") >= 0
        boards = [tab.board_combo.itemData(i) for i in range(tab.board_combo.count())]
        assert {"bcm2711", "bcm2712"} <= set(boards)
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "bcm2712")
        offered = [tab.quant_combo.itemText(i) for i in range(tab.quant_combo.count())]
        assert offered[:6] == ["Q4_K_M", "Q4_0", "IQ4_NL", "Q5_K_M", "Q6_K", "Q8_0"]
        req = submit(tab)
        assert req is not None and (req.targets, req.target_board) == (["ARM64"], "bcm2712")
    finally:
        tab._build_timer.stop()
