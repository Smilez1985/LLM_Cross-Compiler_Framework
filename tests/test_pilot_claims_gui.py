"""
The question before a value of an unconfirmed archive entry is used, in the module wizard and the terminal (DESIGN,
Ditto as the pilot in the module wizard, step 3a): the wizard asks once per pending value before the form shows the
proposal; "use" fills the field (its note says it rests on an unconfirmed claim used on the person's decision),
"confirm" confirms the entry in Ditto's archive and fills it, "skip" (the default, also Escape) leaves it empty with
why; a confirmation that fails is shown and asked again. `module propose-values` asks the same in a terminal.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.format_proposal import FormatValuesAnswer, build_proposal  # noqa: E402
from test_format_form_gui import DOC, PROBE, answer_data, tab_of  # noqa: E402
from test_gui_build import MODULE, wizard  # noqa: E402,F401
from test_pilot_claims import entry  # noqa: E402


def pending_proposal(claim=None):
    data = answer_data()
    values = [v for v in data["formats"][0]["values"] if v["path"] != "quantizations"]
    values.append({"path": "quantizations", "value": ["int8", "int16"], "reason": "the archive says so",
                   "source": "A1", "confidence": 0.6})
    data["formats"][0]["values"] = values
    return build_proposal(FormatValuesAnswer.model_validate(data), [DOC], PROBE, claims=[claim or entry()],
                          needed=5)


def answers(monkeypatch, *decisions):
    from orchestrator.gui import claim_question
    asked = []
    queue = list(decisions)

    def ask(parent, pending, lang=None, can_confirm=True):
        asked.append((pending.path, can_confirm))
        return queue.pop(0)
    monkeypatch.setattr(claim_question, "ask_claim", ask)
    return asked


def test_use_fills_the_field_with_its_note(wizard, monkeypatch):  # noqa: F811
    asked = answers(monkeypatch, "use")
    wizard.on_formats_proposed(pending_proposal())
    assert asked == [("quantizations", False)], "no archive in this wizard: nothing to confirm in"
    row = tab_of(wizard.formats_form).rows["quantizations"]
    assert row.value() == ["int8", "int16"] and "unconfirmed, used on your decision" in row.note.text()


def test_confirm_confirms_the_entry_and_a_failure_asks_again(wizard, monkeypatch):  # noqa: F811
    from orchestrator.gui import plain
    shown = []
    monkeypatch.setattr(plain, "plain_box", lambda parent, kind, title, text, **kw: shown.append(text))
    calls = []

    def confirm(entry_id):
        calls.append(entry_id)
        if len(calls) == 1:
            raise RuntimeError("the knowledge base is busy")
    wizard.framework_manager = SimpleNamespace(ditto_archive=SimpleNamespace(confirm=confirm))
    asked = answers(monkeypatch, "confirm", "confirm")
    wizard.on_formats_proposed(pending_proposal())
    assert len(asked) == 2 and asked[0][1] is True and len(calls) == 2
    assert shown and "could not be confirmed" in shown[0] and "busy" in shown[0]
    assert "confirmed by you" in tab_of(wizard.formats_form).rows["quantizations"].note.text()


def test_skip_leaves_the_field_empty_with_why(wizard, monkeypatch):  # noqa: F811
    answers(monkeypatch, "skip")
    wizard.on_formats_proposed(pending_proposal())
    row = tab_of(wizard.formats_form).rows["quantizations"]
    assert row.value() in (None, [], "") and "did not use the unconfirmed archive entry" in row.note.text()


def test_the_dialog_says_what_and_why_and_defaults_to_no(qapp):
    from orchestrator.gui.claim_question import ClaimQuestionDialog
    claim = entry(sources=2)
    (pending,) = pending_proposal(claim).pending()
    dialog = ClaimQuestionDialog(pending, lang="en", can_confirm=False)
    try:
        text = dialog.lbl_claim.text()
        assert "quantizations" in text and "int8 int16" in text and "2 of 5 independent domains" in text
        assert claim.text in text and "https://vendor.example.com/docs/quantization" in text
        assert dialog.btn_skip.isDefault() and not dialog.btn_confirm.isEnabled()
        dialog.reject()
        assert dialog.decision == "skip"
        dialog.btn_use.click()
        assert dialog.decision == "use"
    finally:
        dialog.close()
    german = ClaimQuestionDialog(pending, lang="de")
    try:
        assert "unabhängigen Domains" in german.lbl_claim.text() and german.btn_confirm.isEnabled()
    finally:
        german.close()


def test_the_terminal_asks_the_same(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.Core import format_proposal
    confirmed = []
    framework = SimpleNamespace(ditto_archive=SimpleNamespace(confirm=confirmed.append))

    def require(self):
        self.framework_manager, self._initialized = framework, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    monkeypatch.setattr(cli, "_run_ditto", lambda ctx, provider, model: object())
    monkeypatch.setattr(format_proposal, "propose", lambda ditto, path: pending_proposal())
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)
    out = CliRunner().invoke(cli.cli, ["module", "propose-values", str(probe)], input="confirm\n")
    assert out.exit_code == 0, out.output
    assert "An unconfirmed claim of Ditto's archive" in out.output and "1 of 5 independent domains" in out.output
    assert confirmed == [entry().id] and "confirmed by you" in out.output
