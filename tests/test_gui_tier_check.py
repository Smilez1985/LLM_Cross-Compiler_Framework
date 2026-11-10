"""The 2-bit gate and the measurement on this computer in the windows: Ditto > Local Models... names a candidate,
"Evaluate first..." opens Ditto > Evaluation... with the tier and the measurement chosen, and the evaluation window
runs the measurement (the CLI's eval_run.run_measurement), asks before a reference is downloaded and says a failed
tier plainly. Offscreen, with the runtime of tests/test_tier_check.py and stand-in programs."""

import time
from types import SimpleNamespace

import pytest

from test_tier_check import FakeBench, FakePerplexity, record_for, runtime, stage_rt  # noqa: F401 - the fixture


def wait_until(qapp, condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def boxes(monkeypatch):
    from orchestrator.gui import ditto_eval_window, ditto_models_window
    shown = []
    for module in (ditto_eval_window, ditto_models_window):
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
    return shown


def test_local_models_names_the_candidate_and_evaluate_first_presets_the_evaluation(qapp, tmp_path, boxes):
    from orchestrator.gui.ditto_models_window import LocalModelsWindow
    rt = runtime(tmp_path, ram_gib=2.9)
    win = LocalModelsWindow(SimpleNamespace(), runtime_factory=lambda: rt, lang="en")
    wait_until(qapp, lambda: win.view is not None)
    assert "No local model is recommended yet: small-q2 is a candidate to evaluate first" in \
        win.lbl_recommended.text()
    notes = {win.table.item(r, 0).text(): win.table.item(r, 5).text() for r in range(win.table.rowCount())}
    assert notes["small-q2"].startswith("candidate to evaluate") and notes["default-q2"].startswith(
        "not evaluated here")
    assert win.btn_evaluate.isEnabled() and win.evaluation_tier() == "small-q2"
    evaluation = win.evaluate_first()
    assert evaluation is not None and evaluation.tiers() == ["small-q2"] and evaluation.chk_measure.isChecked()
    evaluation.close()
    win.close()


def test_a_passed_tier_is_recommended_in_the_window(qapp, tmp_path, boxes):
    from orchestrator.gui.ditto_models_window import LocalModelsWindow
    rt = runtime(tmp_path, ram_gib=2.9)
    record_for(rt, "small-q2", tokens_s=12.0, kld=0.03)
    win = LocalModelsWindow(SimpleNamespace(), runtime_factory=lambda: rt, lang="de")
    wait_until(qapp, lambda: win.view is not None)
    assert win.lbl_recommended.text().startswith("Empfohlen für diesen Computer: small-q2")
    notes = {win.table.item(r, 0).text(): win.table.item(r, 5).text() for r in range(win.table.rowCount())}
    assert notes["small-q2"].startswith("empfohlen")
    win.close()


def test_the_evaluation_window_measures_asks_for_the_reference_and_says_a_failure(qapp, tmp_path, stage_rt,
                                                                                    monkeypatch, boxes):
    from orchestrator.evals import tier_check
    from orchestrator.gui import ditto_eval_window as module
    rt = stage_rt
    (rt.models_dir / "tiny-f16.gguf").unlink()
    original = tier_check.measure
    monkeypatch.setattr(tier_check, "measure", lambda rt_, stage, **kw: original(
        rt_, stage, popen=FakePerplexity(kld=0.3), bench=FakeBench(gen_ts=5.0), **kw))

    def fetch(entry, progress=None, stop=None):
        (rt.models_dir / entry.file).write_bytes(b"tiny-f16.gguf" * 10)
    rt.download_entry = fetch
    asked = []
    monkeypatch.setattr(module, "ask", lambda parent, title, text: asked.append(text) or True)
    framework = SimpleNamespace(config=None, ditto_manager=None, resolve_path=lambda p: tmp_path / str(p))
    win = module.EvaluationWindow(framework, runtime_factory=lambda: rt, lang="en")
    win.preset_measure("tiny-q2")
    win.chk_measure_only.setChecked(True)
    win.sp_chunks.setValue(8)
    assert win.start() is True
    wait_until(qapp, lambda: not win.busy, timeout=20)
    assert asked[0].startswith("First tiny-q2 is measured on this computer")
    assert "needs the reference tiny-f16.gguf" in asked[1], "the download is asked before it starts"
    assert win.measured and win.measured[0]["verdict"] == "failed"
    log = win.log.toPlainText()
    assert "tiny-q2 FAILED on this computer" in log and "5.0 tokens/s, at least 8 needed" in log
    assert win.tbl_measure.isVisible() or win.tbl_measure.rowCount() == 1
    assert win.tbl_measure.item(0, 1).text() == "failed"
    assert rt.tier_checks().get("tiny-q2")["verdict"] == "failed"
    win.close()


def test_measuring_needs_a_tier(qapp, tmp_path, boxes):
    from orchestrator.gui.ditto_eval_window import EvaluationWindow
    rt = runtime(tmp_path)
    win = EvaluationWindow(SimpleNamespace(config=None, ditto_manager=None), runtime_factory=lambda: rt, lang="en")
    win.chk_measure.setChecked(True)
    assert win.start() is False and "Tick the tiers to measure" in boxes[-1][1]
    win.close()
