"""Chips served by another chip's profile, and a board profile's guidance on model sizes (DESIGN, "Targets, hardware
and templates", the row of the chip variants).

- A target declares chips without a board of their own as data (`capabilities.variants`), checked by the schema: the
  board must be one of the target's, a chip is never a board. The Rockchip target declares the RK3582, RK3588S and
  RK3588J, each pointing to rk3588 with the reason and its source.
- The board lists show them: `llm-cli targets boards` (an entry and a note line), the Builder (an entry whose value is
  the board, its tooltip the reason), `list_targets` and `get_target_capabilities` (a field). Choosing a chip chooses
  its board: a build request for rk3582 runs and is recorded as rk3588, and the CLI, the Builder's log and the AI's
  start question say so. A probe whose SoC is the chip finds the board.
- A board profile's `model_sizes` is guidance only: written for the Raspberry Pi Zero 2 W, shown in the board list,
  as the board's tooltip and in the start question, and a 135M-class model builds for the Zero 2 W without a memory
  warning.
"""

import json
from types import SimpleNamespace

import pytest
import yaml
from conftest import REPO_ROOT
from test_arm64_target import ZERO2W_PROBE, probe_of, targets_manager
from test_build_tools import env, ask  # noqa: F401 - the build tools' environment (no network, no probe)
from test_review_capabilities_paths import (
    gui_messages,  # noqa: F401 - the Builder tab's recorded message boxes
)

from orchestrator.Core import board_profiles as bp
from orchestrator.schemas.targets import TargetCapabilities

CHIPS = ("rk3582", "rk3588s", "rk3588j")


def target_meta(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "targets" / name / "target.yml").read_text(encoding="utf-8"))


def rockchip() -> TargetCapabilities:
    return TargetCapabilities.model_validate(target_meta("Rockchip")["capabilities"])


# --- the data and its schema ---------------------------------------------------------------------------------------

def test_the_rockchip_target_names_its_chips_with_the_board_the_reason_and_the_source():
    caps = rockchip()
    assert list(caps.variants) == list(CHIPS) and caps.variants_of("rk3588") == list(CHIPS)
    for chip in CHIPS:
        entry = caps.variants[chip]
        assert entry.board == "rk3588" and chip not in caps.boards
        assert entry.reason and entry.source and chip.upper() in entry.label
    assert "rk3588 platform for RKNN and RKLLM" in caps.variants["rk3582"].reason
    assert "docs.radxa.com" in caps.variants["rk3582"].source
    assert "industrial-grade" in caps.variants["rk3588j"].reason
    assert "smaller package" in caps.variants["rk3588s"].reason


def test_a_chip_must_point_to_a_board_and_must_not_be_one():
    data = target_meta("Rockchip")["capabilities"]
    wrong = {**data, "variants": {"rk3582": {"board": "rk9999", "label": "x", "reason": "y"}}}
    with pytest.raises(ValueError, match="unknown board 'rk9999'"):
        TargetCapabilities.model_validate(wrong)
    twice = {**data, "variants": {"RK3576": {"board": "rk3588", "label": "x", "reason": "y"}}}
    with pytest.raises(ValueError, match="is one of the boards"):
        TargetCapabilities.model_validate(twice)
    # a community target without chips stays valid, and a target declares its own
    assert TargetCapabilities.model_validate({**data, "variants": {}}).variants == {}


def test_a_request_for_a_chip_is_the_request_for_its_board():
    caps = rockchip()
    assert caps.normalize("gguf", "RK3582", None) == ("gguf", "rk3588", "Q4_K_M")
    assert caps.normalize("rkllm", "rk3588j", None) == ("rkllm", "rk3588", "w8a8")
    assert caps.check_request("rkllm", "rk3582", None) == []
    assert caps.canonical_board("rk3399") is None
    note = caps.variant_note("rk3582")
    assert note.startswith("rk3582 has no board of its own and is built as rk3588: ") and "source:" in note
    assert caps.variant_note("rk3588") is None


def test_the_model_sizes_are_written_for_the_zero_2_w_only():
    data = bp.board_data(target_meta("ARM64"))
    sizes = data.optimization_profiles["bcm2837"].model_sizes
    for words in ("135M to 360M", "MQTT", "not chat or writing code", "1B", "memory warning", "you decide"):
        assert words in sizes
    assert [b for b, p in data.optimization_profiles.items() if p.model_sizes] == ["bcm2837"]
    assert bp.board_tooltip(data, "bcm2837") == sizes and bp.board_tooltip(data, "bcm2712") == ""


def test_the_board_lines_show_the_chips_after_their_board_and_the_model_sizes():
    rk = bp.board_lines(rockchip(), bp.board_data(target_meta("Rockchip")))
    at = rk.index(next(line for line in rk if line.startswith("rk3588:")))
    chips = [line for line in rk[at:] if line.startswith(CHIPS)]
    assert [line.split(":")[0] for line in chips] == list(CHIPS)
    note = rk[rk.index(chips[0]) + 1]
    assert note.startswith("  note: no profile of its own; choosing rk3582 chooses rk3588.") and "source:" in note
    arm = "\n".join(bp.board_lines(TargetCapabilities.model_validate(target_meta("ARM64")["capabilities"]),
                                   bp.board_data(target_meta("ARM64"))))
    assert "  model sizes: Zero 2 W (512 MB): models of about 135M to 360M" in arm
    assert arm.count("model sizes:") == 1


# --- the probe -----------------------------------------------------------------------------------------------------

def test_a_probe_of_a_chip_finds_its_board(tmp_path):
    from orchestrator.runtime.board_recommendation import recommend_board
    from orchestrator.runtime.probe_service import HardwareProbes, match_target
    from orchestrator.runtime.profile import ProfileStore
    tm = targets_manager()
    tm.initialize()
    profile = probe_of("rk3582", "Some RK3582 board", "rockchip")
    found = match_target(profile, tm)
    assert (found.target, found.board) == ("Rockchip", "rk3588")
    rec = recommend_board(profile, tm, "Rockchip")
    assert rec.sure and rec.board == "rk3588" and "no board of its own" in rec.reason
    ProfileStore(tmp_path).save_device("00000000000000c1", profile, label="e54c")
    probes = HardwareProbes(tmp_path)
    assert probes.for_board("rk3588") is None, "without the chips, an rk3582 probe is no rk3588's"
    assert probes.for_board("rk3588", rockchip().variants_of("rk3588")).probe_id == "00000000000000c1"


# --- the CLI, the AI tools, the Builder ----------------------------------------------------------------------------

@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(booted_framework, tmp_path, monkeypatch)


def test_the_cli_lists_the_chips_and_builds_a_chip_as_its_board(cli):
    boards = cli.invoke("targets", "boards", "Rockchip")
    text = " ".join(boards.out.split())
    assert boards.code == 0 and "rk3582: RK3582" in text and "note: no profile of its own; choosing rk3582" in text
    arm = cli.invoke("targets", "boards", "ARM64")
    assert "model sizes: Zero 2 W (512 MB)" in " ".join(arm.out.split())
    listed = cli.invoke("targets", "list", "-f", "json")
    data = {t["id"]: t for t in json.loads(listed.out[listed.out.index("["):])}
    assert data["Rockchip"]["variants"]["rk3582"]["board"] == "rk3588"
    result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "Rockchip", "-f", "gguf",
                        "-b", "rk3582")
    assert result.code == 0, result.out
    assert cli.submitted[-1].target_board == "rk3588"
    assert "Note: rk3582 has no board of its own and is built as rk3588" in " ".join(result.out.split())


def test_the_ai_tools_carry_the_chips_and_the_model_sizes(booted_framework):
    from orchestrator.tools.policy import ToolPolicy
    from orchestrator.tools.registry import ToolContext
    registry = booted_framework.tool_registry
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))

    def call(name, arguments):
        return registry.call(name, arguments, ToolContext("ditto", session="s"))

    listed = {t.id: t for t in call("list_targets", {}).targets}
    assert set(listed["Rockchip"].variants) == set(CHIPS)
    assert listed["Rockchip"].variants["rk3582"].board == "rk3588"
    assert "135M to 360M" in listed["ARM64"].board_profiles["bcm2837"].model_sizes
    details = call("get_target_capabilities", {"target": "Rockchip"})
    assert details.variants["rk3588j"].board == "rk3588" and details.variants["rk3588j"].reason


@pytest.mark.usefixtures("qapp", "gui_messages")
def test_the_builder_lists_the_chips_with_their_reason_and_builds_them_as_their_board(booted_framework):
    from PySide6.QtCore import Qt
    from test_review_capabilities_paths import make_tab, select, submit
    tab = make_tab(booted_framework, target="Rockchip")
    try:
        select(tab.format_combo, "gguf")
        combo = tab.board_combo
        chips = {combo.itemData(i, tab.CHIP_ROLE): i for i in range(combo.count()) if combo.itemData(i, tab.CHIP_ROLE)}
        assert set(chips) == set(CHIPS)
        index = chips["rk3582"]
        assert combo.itemData(index) == "rk3588"
        tip = combo.itemData(index, Qt.ToolTipRole)
        assert "rk3582" in tip and "rk3588" in tip and "Radxa" in tip and "docs.radxa.com" in tip
        combo.setCurrentIndex(index)
        assert tab._selected_board() == "rk3588" and tab._selected_chip() == "rk3582"
        req = submit(tab)
        assert req is not None and req.target_board == "rk3588"
        assert "rk3582" in tab.log_area.toPlainText() and "rk3588" in tab.log_area.toPlainText()
        select(tab.target_combo, "ARM64")
        select(tab.format_combo, "gguf")
        zero = tab.board_combo.findData("bcm2837")
        assert "135M to 360M" in tab.board_combo.itemData(zero, Qt.ToolTipRole)
        assert not tab.board_combo.itemData(tab.board_combo.findData("bcm2712"), Qt.ToolTipRole)
    finally:
        tab._build_timer.stop()


# --- the AI's start question ---------------------------------------------------------------------------------------

def test_the_start_question_names_the_chip_s_board(env):  # noqa: F811
    from test_build_tools import BUILD
    _, shown = ask(env, {**BUILD, "board": "rk3582"})
    assert env.orch.requests[-1].target_board == "rk3588"
    assert "rk3582 has no board of its own and is built as rk3588" in shown[0]


def small_model_facts(parameters: int = 135 * 10 ** 6):
    """The Hub's facts of a 135M-class model in 16 bits (example values: about 270 MB of safetensors)."""
    from orchestrator.Core.hf_lookup import HubFile, ModelFacts
    files = [HubFile("config.json", 700), HubFile("model.safetensors", parameters * 2, "f" * 64)]
    return ModelFacts(repo="example/small-135m", commit="c" * 40, files=files, staged=files, weights="safetensors",
                      gated="false", license="apache-2.0", license_class="permissive", token_access="not_needed",
                      parameters=parameters)


def test_a_135m_class_model_builds_for_the_zero_2_w_without_a_memory_warning(env, tmp_path):  # noqa: F811
    """The guidance the profile gives is what the memory rules say: a 135M-class model at the board's default type
    gets no memory warning, neither in the start question nor after the build (with the Zero 2 W's probe or the most
    memory the board comes with), and the question shows the guidance."""
    from orchestrator.Core import disk_plan
    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    from orchestrator.runtime.profile import ProfileStore, read_profile
    env.service.hub = small_model_facts()
    out, shown = ask(env, {"model": "example/small-135m", "target": "ARM64", "format": "gguf", "board": "bcm2837"})
    sent = env.orch.requests[-1]
    assert out.request_id and (sent.target_board, sent.quantization_options) == ("bcm2837", ["IQ4_XS"])
    text = shown[0]
    assert "Model sizes for this board" in text and "135M to 360M" in text
    assert "will run only with swap" not in text and "little room is left" not in text
    assert "On bcm2837: does not fit" not in text and "On bcm2837: tight" not in text
    # the build's output, estimated as the disk rule does (the real file of IQ4_XS is smaller), against the board
    output = int(270 * 10 ** 6 * disk_plan.output_share("IQ4_XS"))
    state = tmp_path / "probes"
    ProfileStore(state).save_device("00000000000000e3", read_profile(ZERO2W_PROBE), label="zero")
    engine = BuildEngine.__new__(BuildEngine)
    engine.config = SimpleNamespace(user_state_dir=str(state))
    engine.target_manager = targets_manager()
    engine.target_manager.initialize()
    manifest = {"primary": "m.gguf", "artifacts": [{"file": "m.gguf", "bytes": output}]}
    for probe in ("00000000000000e3", None):
        progress = BuildProgress("b", BuildStatus.COMPLETED, "done")
        config = SimpleNamespace(hardware_probe=probe, target_board="bcm2837", target_arch="ARM64")
        engine._board_memory_warning(config, manifest, progress)
        assert progress.warnings == []
    # and a model of about 1B is accepted with the warning, never refused
    assert bp.memory_warning(int(2 * 10 ** 9 * disk_plan.output_share("IQ4_XS")), 416, "bcm2837", "the board's probe")


def test_every_shipped_target_still_validates():
    for name in ("Rockchip", "ARM64"):
        assert bp.check_board_data(target_meta(name),
                                   TargetCapabilities.model_validate(target_meta(name)["capabilities"])) == []

