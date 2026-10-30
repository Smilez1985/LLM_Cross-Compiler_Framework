"""Ditto > Evaluation... for the token measurement (ctx-measure): the tokens and openai_paid kinds, the pause, the
paid test's confirmation (OpenAI only, the most it costs, No by default) and the CLI's tokens table. The run itself
is the stand-in of tests/test_gui_ditto_models.py (FakeRun): nothing calls a model."""

import pytest

pytest.importorskip("PySide6")

from test_gui_ditto_models import eval_ui, tick, wait_until  # noqa: E402,F401  (eval_ui is a fixture)


def _choose_provider(win, provider):
    win.cb_provider.setCurrentIndex(win.cb_provider.findData(provider))


def test_the_window_offers_the_new_kinds_and_the_pause(eval_ui):  # noqa: F811 - fixtures
    win = eval_ui.win
    assert {"tokens", "openai_paid"} <= set(win.chk_kinds)
    assert win.sp_pause.value() == 0 and win.choice()["pause_minutes"] is None
    win.sp_pause.setValue(2.5)
    assert win.choice()["pause_minutes"] == 2.5


def test_a_tokens_run_passes_the_pause_to_the_one_runner(eval_ui):  # noqa: F811 - fixtures
    win = eval_ui.win
    win.chk_kinds["tokens"].setChecked(True)
    win.sp_pause.setValue(5)
    eval_ui.answers.append(True)
    assert win.start()
    wait_until(eval_ui.qapp, lambda: not win.busy)
    call = eval_ui.run.calls[-1]
    assert call.kw["pause_minutes"] == 5 and call.kw["i_pay"] is False
    assert all(case_id.startswith("tokens-") for case_id in call.cases)


def test_the_paid_test_needs_the_provider_openai(eval_ui):  # noqa: F811 - fixtures
    win = eval_ui.win
    win.chk_kinds["openai_paid"].setChecked(True)
    assert win.start() is False
    assert not eval_ui.asked and not eval_ui.run.calls
    assert "provider OpenAI" in eval_ui.shown[-1][1]


def test_the_paid_test_asks_with_its_cost_and_no_is_the_default(eval_ui, monkeypatch):  # noqa: F811 - fixtures
    from orchestrator.evals import eval_run
    for name in eval_run.CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    win = eval_ui.win
    win.chk_kinds["openai_paid"].setChecked(True)
    _choose_provider(win, "openai")
    assert win.start() is False          # the question was answered with the default (No)
    text = eval_ui.asked[-1][0]
    cost = eval_run.paid_estimate([c for c in eval_run.all_cases() if c.kind == "openai_paid"])
    assert "paid OpenAI test" in text and f"at most {cost['calls']} requests" in text
    assert "openai_cache_check.json" in text and not eval_ui.run.calls
    eval_ui.answers.append(True)
    assert win.start()
    wait_until(eval_ui.qapp, lambda: not win.busy)
    assert eval_ui.run.calls[-1].kw["i_pay"] is True


def test_the_paid_test_never_starts_in_ci(eval_ui, monkeypatch):  # noqa: F811 - fixtures
    monkeypatch.setenv("CI", "true")
    win = eval_ui.win
    win.chk_kinds["openai_paid"].setChecked(True)
    _choose_provider(win, "openai")
    assert win.start() is False and not eval_ui.asked and not eval_ui.run.calls
    assert "never runs in CI" in eval_ui.shown[-1][1]


def test_an_unexpected_pass_is_told_loudly(eval_ui):  # noqa: F811 - fixtures
    from orchestrator.evals.ditto_eval import Attempt, CaseResult, load_cases
    from orchestrator.gui.ditto_eval_window import _RelayEvents
    win = eval_ui.win
    case = next(c for c in load_cases() if c.id == "tokens-followup-after-compaction")
    token = object()
    win._events, win._job = token, object()
    events = _RelayEvents(token)
    events.result(CaseResult(case, [Attempt(True, 1.0)]))
    wait_until(eval_ui.qapp, lambda: "UNEXPECTED PASS" in win.log.toPlainText())
    events.result(CaseResult(case, [Attempt(False, 1.0, problems=["mentions none"])]))
    wait_until(eval_ui.qapp, lambda: "expected to fail" in win.log.toPlainText())
    win._events = win._job = None


def test_the_tokens_table_is_the_clis(eval_ui):  # noqa: F811 - fixtures
    from orchestrator.evals import ditto_eval
    win = eval_ui.win
    case = next(c for c in ditto_eval.load_cases() if c.id == "tokens-build-talk")
    attempt = ditto_eval.Attempt(True, 1.0, conversation={"turns": 3, "input_tokens": 10, "output_tokens": 5,
                                                          "cache_read_tokens": 30, "cache_write_tokens": 0,
                                                          "calls": 3, "cache_read_share": 0.75})
    report = ditto_eval.EvalReport([ditto_eval.CaseResult(case, [attempt])], setup={"label": "x", "model": "m"})
    win.kinds_shown = ["tokens"]
    win.show_results([report])
    assert not win.tbl_tokens.isHidden()
    assert win.tbl_tokens.rowCount() == 1 and win.tbl_tokens.item(0, 7).text() == "75%"
