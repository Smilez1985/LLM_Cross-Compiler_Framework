"""The generic ARM64 CPU target (targets/ARM64; DESIGN, "Targets, hardware and templates", the row of the target).

- Its target.yml validates; its board profiles (bcm2711: Raspberry Pi 4, bcm2712: Raspberry Pi 5, bcm2837: Raspberry
  Pi Zero 2 W, sm1: Amlogic S905X3, sun50i-h616 and sun50i-h618: Allwinner H616 and H618) pass the existing profile
  rules and are the build recipe's plan for each SoC; the target side is ordered for each board.
- A build for each board plans the expected compiler and CMake options in the stage modules, with or without the
  board's probe, and makes the board package (llama-cli and llama-server cross-built); what the target cannot build is
  refused with the reason.
- A probe of a Raspberry Pi 4, 5 and Zero 2 W maps to the right board; the recipe plans the device runtime of each
  probe as the board's profile says. A probe of an Orange Pi or Banana Pi board maps to the board of its SoC, in this
  target or in Rockchip's, also when its kernel names an RK3588S or only the board's name is known.
- The Zero 2 W's 512 MB: the existing memory rules (the board measurement) refuse a model that does not fit and give a
  small model its context.
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
ZERO2W_PROBE = PROBES / "probe_rpizero2w_raspios.txt"
PI4_FLAGS = "armv8-a"
PI5_FLAGS = "armv8.2-a+dotprod+fp16"
BOARDS = {"bcm2711": ("Cortex-A72", PI4_FLAGS), "bcm2712": ("Cortex-A76", PI5_FLAGS),
          "bcm2837": ("Cortex-A53", PI4_FLAGS), "sm1": ("Cortex-A55", PI5_FLAGS),
          "sun50i-h616": ("Cortex-A53", PI4_FLAGS), "sun50i-h618": ("Cortex-A53", PI4_FLAGS)}
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
    assert check.valid and check.target_id == "ARM64" and check.boards == 6 and check.formats == ("gguf",)
    c = caps()
    assert list(c.boards) == list(BOARDS) and c.default_board == "bcm2712"
    assert c.boards["bcm2711"].ram_max_gb == 8 and c.boards["bcm2712"].ram_max_gb == 16
    assert c.boards["bcm2837"].ram_max_gb == 1 and c.boards["sm1"].ram_max_gb == 4
    assert c.boards["bcm2837"].label.startswith("Raspberry Pi Zero 2 W")
    gguf = c.formats["gguf"]
    assert gguf.sdk is None, "a CPU target asks nothing of the board before a build"
    assert gguf.default_for("bcm2711") == gguf.default_for("bcm2712") == "Q4_K_M"
    assert gguf.default_for("sun50i-h616") == gguf.default_for("sun50i-h618") == "Q4_K_M"
    assert gguf.default_for("bcm2837") == "IQ4_XS" and gguf.default_for("sm1") == "Q4_0"
    assert c.normalize("gguf", "BCM2837", None) == ("gguf", "bcm2837", "IQ4_XS")
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
    ("bcm2712", "cortex-a76", ["neon", "dotprod", "fp16"], PI5_FLAGS),
    ("bcm2837", "cortex-a53", ["neon"], PI4_FLAGS),
    ("sm1", "cortex-a55", ["neon", "dotprod", "fp16"], PI5_FLAGS),
    ("sun50i-h616", "cortex-a53", ["neon"], PI4_FLAGS),
    ("sun50i-h618", "cortex-a53", ["neon"], PI4_FLAGS)])
def test_each_board_has_its_cores_options_and_flags(board, cpu, features, arch):
    prof = bp.board_data(meta()).optimization_profiles[board]
    assert (prof.arch, prof.cpu, prof.cpu_features) == ("arm64", cpu, features)
    assert prof.cmake_options == {"GGML_NATIVE": "OFF", "GGML_CPU_ARM_ARCH": arch}
    assert prof.cpu_flags == f"-march={arch}" and "-mfpu" not in prof.cpu_flags and "-mcpu" not in prof.cpu_flags
    plan = bp.recipe_plan(prof)
    assert plan.options()["GGML_NATIVE"] == "OFF" and plan.options()["GGML_CPU_ARM_ARCH"] == arch
    assert "i8mm" not in arch and "sve" not in arch, "no core of these boards has int8 matrix multiply or SVE"


@pytest.mark.parametrize("board, memory, level, order", [
    ("bcm2711", "medium_memory", "balanced", ["Q4_K_M", "Q4_0", "IQ4_XS", "Q5_K_M"]),
    ("bcm2712", "high_memory", "balanced", ["Q4_K_M", "Q4_0", "IQ4_NL", "Q5_K_M", "Q6_K", "Q8_0"]),
    ("bcm2837", "low_memory", "size", ["IQ4_XS", "IQ3_M", "IQ2_M", "Q4_0"]),
    ("sm1", "low_memory", "size", ["Q4_0", "IQ4_NL", "Q4_K_M"]),
    ("sun50i-h616", "low_memory", "size", ["Q4_K_M", "IQ4_XS", "Q4_0"]),
    ("sun50i-h618", "low_memory", "size", ["Q4_K_M", "IQ4_XS", "Q4_0"])])
def test_the_target_side_is_ordered_for_each_board(board, memory, level, order):
    prof = bp.board_data(meta()).optimization_profiles[board]
    assert prof.memory_profile == memory and prof.recommended_quantization == order
    assert bp.optimization_for(prof) == level and bp.optimization_for(prof, "fast") == "fast"
    assert caps().formats["gguf"].default_for(board) == order[0], "the board's default is its first recommendation"
    offered = bp.ordered_quantizations(caps().formats["gguf"].quantizations_for(board), prof)
    assert offered[:len(order)] == order and set(offered) == set(caps().formats["gguf"].quantizations)
    assert "Threads: the four cores" in prof.note


def test_the_pi5_s_recommendations_are_the_types_llama_cpp_repacks_with_the_dot_product():
    """At the pin (ggml/src/ggml-cpu/repack.cpp, ggml_repack_get_optimal_repack_type) NEON with the dot product
    repacks Q4_0, IQ4_NL and Q8_0 (4x4) and Q4_K, Q5_K and Q6_K (8x4); Q4_K_M and Q5_K_M are made of those K types."""
    repacked = {"Q4_0", "IQ4_NL", "Q8_0", "Q4_K_M", "Q5_K_M", "Q6_K"}
    for board in ("bcm2712", "sm1"):
        prof = bp.board_data(meta()).optimization_profiles[board]
        assert "dotprod" in prof.cpu_features and set(prof.recommended_quantization) <= repacked, board


def test_the_low_memory_rule_of_the_module_generator_is_the_small_boards_level():
    """A board with little memory starts from the level size, the rule the module generator writes for a probe with
    less than 4 GB (board_profiles.generated_profile); the Zero 2 W's probe gets the same memory profile."""
    from orchestrator.runtime.profile import read_profile
    made = bp.generated_profile("aarch64", read_profile(ZERO2W_PROBE), ["IQ4_XS"])
    prof = bp.board_data(meta()).optimization_profiles["bcm2837"]
    assert (made["memory_profile"], made["optimization"]) == (prof.memory_profile, prof.optimization)
    assert made["cpu_flags"] == prof.cpu_flags and made["cmake_options"] == prof.cmake_options


def test_the_boards_lines_show_both_profiles():
    lines = "\n".join(bp.board_lines(caps(), bp.board_data(meta()), list(caps().boards)))
    assert "bcm2711: Raspberry Pi 4" in lines and "bcm2712: Raspberry Pi 5" in lines
    assert "bcm2837: Raspberry Pi Zero 2 W" in lines and "sun50i-h618: Allwinner H618" in lines
    assert f"GGML_CPU_ARM_ARCH={PI5_FLAGS}" in lines and f"GGML_CPU_ARM_ARCH={PI4_FLAGS}" in lines
    assert "recommended quantization: Q4_K_M, Q4_0, IQ4_NL, Q5_K_M, Q6_K, Q8_0" in lines
    assert "recommended quantization: IQ4_XS, IQ3_M, IQ2_M, Q4_0" in lines


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
    assert package["hardware"]["cpu_features"]["used"] == ([] if arch == PI4_FLAGS else ["DOTPROD", "FP16_ARITH"])
    names = {p.name for p in (image.output / "packages").glob("*/*")}
    assert {"llama-cli", "llama-server", "MANIFEST.json", "SHA256SUMS", "deploy.sh", "test_model.sh"} <= names


@needs_bash
@pytest.mark.parametrize("board, probe", [("bcm2711", PI4_PROBE), ("bcm2712", PI5_PROBE), ("bcm2837", ZERO2W_PROBE)])
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
@pytest.mark.parametrize("kind, board", [("rpi4", "bcm2711"), ("rpi5", "bcm2712"), ("rpizero2w", "bcm2837")])
def test_a_probe_of_a_raspberry_pi_maps_to_its_board(tmp_path, kind, board):
    from test_hardware_probe import make_sysroot, probe, py_probe

    from orchestrator.runtime.probe_service import match_target
    from orchestrator.runtime.profile import parse_profile
    profile = parse_profile(probe.render_text(py_probe(make_sysroot(tmp_path, kind))))
    tm = targets_manager()
    tm.initialize()
    found = match_target(profile, tm)
    assert (found.target, found.board) == ("ARM64", board), found


@pytest.mark.parametrize("path, board, arch", [(PI4_PROBE, "bcm2711", PI4_FLAGS), (PI5_PROBE, "bcm2712", PI5_FLAGS),
                                               (ZERO2W_PROBE, "bcm2837", PI4_FLAGS)])
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
    assert soc_fits(profile.get("SOC_MODEL"), board)
    assert not any(soc_fits(profile.get("SOC_MODEL"), other) for other in BOARDS if other != board)
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


def probe_of(soc: str, board_model: str, vendor: str = ""):
    """A probe of an ARM64 board: the Raspberry Pi 4 fixture with another SoC and board name (example values only)."""
    from orchestrator.runtime.profile import parse_profile
    text = PI4_PROBE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines()
             if not line.startswith(("SOC_MODEL=", "SOC_VENDOR=", "BOARD_MODEL="))]
    lines += [f"SOC_MODEL={soc}", f"BOARD_MODEL={board_model}"] + ([f"SOC_VENDOR={vendor}"] if vendor else [])
    return parse_profile("\n".join(lines) + "\n")


# The device trees' last compatible entry and model: mainline Linux (sun50i-h618-orangepi-zero3.dts,
# sun50i-h618-orangepi-zero2w.dts, sun50i-h616-orangepi-zero2.dts, meson-sm1-bananapi-m5.dts,
# meson-sm1-bananapi-m2-pro.dts, rk3588s-orangepi-5.dts, rk3588-orangepi-5-plus.dts, rk3566-orangepi-3b-v2.1.dts,
# rk3568-bpi-r2-pro.dts) and Orange Pi's own kernel (linux-orangepi: the H618 boards name the sun50i-h616, the RK3588S
# boards rockchip,rk3588).
@pytest.mark.parametrize("vendor, soc, model, target, board", [
    ("allwinner", "sun50i-h618", "OrangePi Zero3", "ARM64", "sun50i-h618"),
    ("allwinner", "sun50i-h618", "OrangePi Zero 2W", "ARM64", "sun50i-h618"),
    ("allwinner", "sun50i-h616", "OrangePi Zero2", "ARM64", "sun50i-h616"),
    ("allwinner", "sun50i-h616", "OrangePi Zero3", "ARM64", "sun50i-h616"),
    ("amlogic", "sm1", "Banana Pi BPI-M5", "ARM64", "sm1"),
    ("amlogic", "sm1", "Banana Pi BPI-M2-PRO", "ARM64", "sm1"),
    ("rockchip", "rk3588s", "Xunlong Orange Pi 5", "Rockchip", "rk3588"),
    ("rockchip", "rk3588", "Orange Pi 5", "Rockchip", "rk3588"),
    ("rockchip", "rk3588", "Xunlong Orange Pi 5 Plus", "Rockchip", "rk3588"),
    ("rockchip", "rk3588s", "Xunlong Orange Pi CM5 Base", "Rockchip", "rk3588"),
    ("rockchip", "rk3566", "Xunlong Orange Pi 3B v2.1", "Rockchip", "rk3566"),
    ("rockchip", "rk3568", "Bananapi-R2 Pro (RK3568) DDR4 Board", "Rockchip", "rk3568")])
def test_a_probe_of_an_orange_pi_or_banana_pi_board_maps_to_the_board_of_its_soc(vendor, soc, model, target, board):
    from orchestrator.runtime.board_recommendation import recommend_board
    from orchestrator.runtime.probe_service import match_target
    profile = probe_of(soc, model, vendor)
    tm = targets_manager()
    tm.initialize()
    found = match_target(profile, tm)
    assert (found.target, found.board) == (target, board), found
    rec = recommend_board(profile, tm, target)
    assert rec.sure and rec.board == board and rec.source == "probe"


def test_a_kernel_that_names_no_known_soc_finds_the_board_by_its_name_only_when_it_is_known():
    """A maker's own kernel may name its SoC otherwise: the board's name in a board's list of known boards finds the
    target and the board; an unknown name with an unknown SoC finds nothing rather than a guess."""
    from orchestrator.runtime.probe_service import match_target
    tm = targets_manager()
    tm.initialize()
    by_name = match_target(probe_of("g12a", "Banana Pi BPI-M5", "amlogic"), tm)
    assert (by_name.target, by_name.board) == ("ARM64", "sm1") and "known board" in by_name.reason
    assert match_target(probe_of("sun55iw3p1", "sun55iw3", "arm"), tm) is None, "the Orange Pi 4A is left out"


def test_a_stored_probe_of_an_rk3588s_board_is_taken_for_an_rk3588_build(tmp_path):
    from orchestrator.runtime.probe_service import HardwareProbes
    from orchestrator.runtime.profile import ProfileStore
    ProfileStore(tmp_path).save_device("00000000000000d5", probe_of("rk3588s", "Xunlong Orange Pi 5", "rockchip"),
                                       label="opi5")
    probes = HardwareProbes(tmp_path)
    assert probes.for_board("rk3588").probe_id == "00000000000000d5"
    assert probes.for_board("rk3566") is None


# --- the Raspberry Pi Zero 2 W's 512 MB: the existing memory rules decide ---------------------------------------

ZERO2W_IDLE = """mem MemTotal 426000
mem MemAvailable 326000
mem SwapTotal 102396
mem SwapFree 102396
cpus 4
freqmax 1000000
freq 1000000
"""


def zero2w_measurement(rss_kib: int, model_bytes: int):
    """A board measurement of a Zero 2 W (example values): the server's resident memory at 512 and 1536 tokens, the
    KV cache of a small model (about 11 KiB per token in f16) on top."""
    from orchestrator.runtime.device import measure
    load = (f"step ctx=512 up=1 rss_kib={rss_kib} hwm_kib={rss_kib} avail_kib=40000 freq_khz=1000000 train=32768\n"
            f"step ctx=1536 up=1 rss_kib={rss_kib + 11264} hwm_kib={rss_kib + 11264} avail_kib=29000 "
            "freq_khz=1000000 train=32768\n")
    idle = measure.parse_idle(ZERO2W_IDLE + f"model {model_bytes}\n")
    return measure.Measurement(measured_at="2026-11-10T09:00:00Z", build_id="b" * 64, model_sha256="a" * 64,
                               model_bytes=model_bytes, kv_measured="f16", idle_before=idle, idle_after=idle,
                               steps=measure.parse_load(load))


def test_a_model_of_about_1b_is_refused_on_the_zero_2_w_by_its_measurement():
    """The weights of a 1B model at IQ2_M (2.7 bits per weight) take about 340 MB: with the system and the reserve of
    the measurement, nothing is left for a context, so the board refuses it plainly (no guess, no start)."""
    from orchestrator.runtime.device import measure
    limits = measure.derive(zero2w_measurement(rss_kib=370 * 1024, model_bytes=340 * 1000 * 1000))
    assert limits.total_mib == 416 and limits.reserve_mib == measure.RESERVE_MIN_MIB
    assert not limits.fits and "try a smaller model or quantization" in limits.reason
    assert "ctx=0\n" in limits.board_file(), "the start script starts no server from it"


def test_a_small_model_gets_its_context_and_the_small_kv_cache_on_the_zero_2_w():
    from orchestrator.runtime.device import measure
    limits = measure.derive(zero2w_measurement(rss_kib=150 * 1024, model_bytes=120 * 1000 * 1000))
    assert limits.fits and limits.ctx >= measure.CTX_MIN and limits.ctx % 256 == 0
    assert limits.kv_cache == "q8_0", "little memory is left: the boards' rule takes the smaller KV cache"
    assert limits.need_mib + limits.reserve_mib <= limits.available_mib


def test_the_zero_2_w_s_probe_gives_the_small_kv_cache_without_a_measurement():
    """Without a measurement the deploy settings take the KV-cache type from the probe's RAM (hardware.kv_cache_default):
    on 416 MiB it is q8_0, and the start script still starts no server without a measurement or a context."""
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.runtime.profile import read_profile
    choice = DeploySettings().kv_choice(read_profile(ZERO2W_PROBE), model_bytes=120 * 1000 * 1000)
    assert choice.kind == "q8_0" and choice.why == "little"


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
    svc.zero2w = store(state, "zero2w", ZERO2W_PROBE)
    return svc


@pytest.mark.parametrize("board, quantization, level", [("bcm2711", "Q4_K_M", "balanced"),
                                                        ("bcm2712", "Q4_K_M", "balanced"),
                                                        ("bcm2837", "IQ4_XS", "size")])
def test_a_build_request_passes_the_pre_flight_check_without_sdk_findings(service, board, quantization, level):
    from orchestrator.Core.build_service import BuildService
    probe = {"bcm2711": service.pi4, "bcm2712": service.pi5, "bcm2837": service.zero2w}[board]
    result = service.preflight_for("ARM64", "gguf", board, probe)
    assert result.findings == [] and not result.stopped and result.unchecked == []
    request = BuildService.request_in(model="Qwen/Qwen2.5-0.5B-Instruct", target="ARM64", format="gguf",
                                      board=board, probe=probe)
    service.start_build(request, local_paths=True)
    (sent,) = service.framework.orchestrator.requests
    assert sent.preflight_notes == [] and sent.targets == ["ARM64"] and sent.target_board == board
    assert sent.quantization_options == [quantization] and sent.optimization_level.value == level


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
    assert data["ARM64"]["name"] == "Generic ARM64 (CPU)" and sorted(data["ARM64"]["boards"]) == sorted(BOARDS)
    assert [f["id"] for f in data["ARM64"]["formats"]] == ["gguf"]
    assert cli.invoke("targets", "validate", "arm64").code == 0
    boards = cli.invoke("targets", "boards", "ARM64")
    assert boards.code == 0 and "bcm2712: Raspberry Pi 5" in " ".join(boards.out.split())
    result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "arm64", "-b", "bcm2711")
    assert result.code == 0, result.out
    sent = cli.submitted[-1]
    assert (sent.targets, sent.target_board, sent.quantization_options) == (["ARM64"], "bcm2711", ["Q4_K_M"])
    zero = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "arm64", "-b", "bcm2837")
    assert zero.code == 0, zero.out
    sent = cli.submitted[-1]
    assert (sent.target_board, sent.quantization_options, sent.optimization_level.value) == ("bcm2837", ["IQ4_XS"],
                                                                                           "size")


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
    assert set(details.boards) == set(BOARDS) and list(details.formats) == ["gguf"]
    assert details.formats["gguf"].sdk is None


@pytest.mark.usefixtures("qapp", "gui_messages")
def test_the_builder_offers_the_target_its_boards_and_the_board_s_order(booted_framework):
    from test_review_capabilities_paths import make_tab, select, submit
    tab = make_tab(booted_framework, target="ARM64")
    try:
        assert tab.target_combo.findData("ARM64") >= 0
        boards = [tab.board_combo.itemData(i) for i in range(tab.board_combo.count())]
        assert set(BOARDS) <= set(boards)
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "bcm2837")
        offered = [tab.quant_combo.itemText(i) for i in range(tab.quant_combo.count())]
        assert offered[:4] == ["IQ4_XS", "IQ3_M", "IQ2_M", "Q4_0"]
        select(tab.board_combo, "bcm2712")
        offered = [tab.quant_combo.itemText(i) for i in range(tab.quant_combo.count())]
        assert offered[:6] == ["Q4_K_M", "Q4_0", "IQ4_NL", "Q5_K_M", "Q6_K", "Q8_0"]
        req = submit(tab)
        assert req is not None and (req.targets, req.target_board) == (["ARM64"], "bcm2712")
    finally:
        tab._build_timer.stop()
