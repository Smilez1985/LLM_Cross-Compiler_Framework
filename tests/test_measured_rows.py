"""Measured benchmark rows from the board tests (device_tests.json) in list_targets and the target details, and the plain
note about entries in the old format (chain followups-3).

- The newest measured row per board, model and quantization of a target's builds (Core/board_profiles.newest_measured
  over build_service.measured_records)
  joins the target.yml rows in list_targets / get_target_capabilities and `llm-cli targets boards`, marked measured by
  llmcf on the board's label: labels only, never a path, an address or a device id.
- `llm-cli targets validate`, `llm-cli targets boards` and the GUI's target check name the entries in the format before
  3.2.0 they ignored: "N entries in the old format were ignored: <names>".
"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from conftest import REPO_ROOT
from orchestrator.Core import board_profiles as bp

TARGETS = REPO_ROOT / "targets"
MODEL = "acme/tiny-model"


def _build(root: Path, name: str, *, board="rk3566", quant="Q4_K_M", target="Rockchip", tests=()):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "build_info.json").write_text(json.dumps({
        "build_id": name, "target": target, "board": board, "model_source": MODEL, "quantization": quant,
        "created": "2026-10-29T10:00:00", "artifacts": [{"file": "m.gguf", "sha256": "a" * 64, "bytes": 1}]}),
        encoding="utf-8")
    (folder / "device_tests.json").write_text(json.dumps({"schema": "llm-framework.device-test/1",
                                                         "tests": list(tests)}), encoding="utf-8")
    return folder


def _test(label, at, gen, *, passed=True):
    return {"schema": "llm-framework.device-test/1", "at": at, "device_id": "0123456789abcdef", "label": label,
            "board": "Radxa ROCK 3C", "gen_ts": gen, "prompt_ts": 40.0, "smoke_passed": passed,
            "checks_passed": passed, "model_sha256": "a" * 64}


def test_the_newest_measured_row_per_board_model_and_quantization(tmp_path):
    out = tmp_path / "output"
    _build(out, "b1", tests=[_test("desk board", "2026-10-20T09:00:00Z", 7.0),
                             _test("desk board", "2026-10-28T09:00:00Z", 9.5)])
    _build(out, "b2", tests=[_test("desk board", "2026-10-25T09:00:00Z", 8.0)])
    _build(out, "b3", quant="Q8_0", tests=[_test("shelf board", "2026-10-26T09:00:00Z", 5.0)])
    _build(out, "b4", tests=[_test("desk board", "2026-10-29T09:00:00Z", 99.0, passed=False)])   # a failed run
    _build(out, "b5", target="OtherTarget", tests=[_test("desk board", "2026-10-29T09:00:00Z", 1.0)])
    from orchestrator.Core.build_service import measured_records
    rows = bp.newest_measured(measured_records([out]), "Rockchip")
    by_quant = {r.quantization: r for r in rows}
    assert sorted(by_quant) == ["Q4_K_M", "Q8_0"]
    assert by_quant["Q4_K_M"].gen_ts == 9.5 and by_quant["Q4_K_M"].source.date == "2026-10-28"
    assert by_quant["Q8_0"].source.board_label == "shelf board"
    text = json.dumps([r.model_dump() for r in rows])
    assert str(tmp_path) not in text and "0123456789abcdef" not in text, "labels only, never paths or device ids"
    assert all(r.board == "rk3566" and r.model == MODEL and r.measured for r in rows)


def test_list_targets_and_target_details_carry_the_measured_rows(tmp_path):
    """Regression: the board tests' numbers stayed in device_tests.json; list_targets showed only target.yml's rows."""
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.target_manager import TargetManager
    out = tmp_path / "output"
    _build(out, "b1", tests=[_test("desk board", "2026-10-28T09:00:00Z", 9.5)])
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(TARGETS)), resolve_path=Path))
    values = {"output_dir": str(out)}
    framework = SimpleNamespace(target_manager=tm, config=SimpleNamespace(get=lambda k, d=None: values.get(k, d)),
                                resolve_path=lambda p: Path(p))
    service = BuildService(framework)
    target = next(t for t in service.list_targets().targets if t.id == "Rockchip")
    measured = [r for r in target.benchmarks if r.source.kind == "measured"]
    assert len(measured) == 1 and measured[0].gen_ts == 9.5
    assert measured[0].source_label == "measured by llmcf (board desk board, 2026-10-28)"
    assert any(r.source.kind == "unmeasured" for r in target.benchmarks), "target.yml's rows stay"
    _profiles, rows = service.board_data_out("Rockchip")
    assert [r.gen_ts for r in rows if r.source.kind == "measured"] == [9.5]


def _old_target(tmp_path):
    from orchestrator.Core.target_manager import TargetManager
    shutil.copytree(TARGETS / "Rockchip", tmp_path / "Rockchip", ignore=shutil.ignore_patterns("*.sh", "scripts"))
    path = tmp_path / "Rockchip" / "target.yml"
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta["optimization_profiles"]["rk3588_performance"] = {"cmake_flags": "-DGGML_NATIVE=ON",
                                                           "memory_profile": "high_memory"}
    meta["performance_benchmarks"] = {"granite": {"rk3588": {"q4_k_m": {"tokens_per_second": 25}}}}
    path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    return TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(tmp_path)), resolve_path=Path))


def test_the_check_names_the_old_format_entries_it_ignored(tmp_path):
    """Regression: an old entry was ignored without a word, so the person never learned it had no effect."""
    tm = _old_target(tmp_path)
    check = tm.validate("Rockchip", reread=True)
    assert check.valid and check.old_format == ("optimization_profiles.rk3588_performance", "performance_benchmarks")
    assert bp.old_format_note(check.old_format) == ("2 entries in the old format were ignored: "
                                                    "optimization_profiles.rk3588_performance, performance_benchmarks")
    assert bp.old_format_note(()) == ""


def test_targets_validate_and_boards_print_the_note(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    tm = _old_target(tmp_path)
    fake = SimpleNamespace(get_component=lambda name: tm if name == "target_manager" else None)
    monkeypatch.setattr(cli.FrameworkContext, "require_framework",
                        lambda self: setattr(self, "framework_manager", fake))
    for args in (["targets", "validate", "Rockchip"], ["targets", "boards", "Rockchip"]):
        result = CliRunner().invoke(cli.cli, args)
        assert result.exit_code == 0, result.output
        assert "2 entries in the old format were ignored: optimization_profiles.rk3588_performance, " \
               "performance_benchmarks" in " ".join(result.output.split()), (args, result.output)


def test_the_gui_check_text_names_them_too():
    from orchestrator.Core.target_manager import TargetCheck
    import pytest
    pytest.importorskip("PySide6")
    from orchestrator.gui.targets_window import target_check_text
    check = TargetCheck("Rockchip", "Rockchip", declared=True, boards=4, formats=("gguf",),
                        old_format=("performance_benchmarks",))
    for lang, words in (("en", "1 entries in the old format were ignored"), ("de", "im alten Format")):
        text = target_check_text(check, lang)
        assert words in text and "performance_benchmarks" in text
