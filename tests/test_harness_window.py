"""Boards > Board harness... (orchestrator/gui/harness_window.py): `llmcf bundle harness` by mouse. Offscreen, against
the fake board of tests/test_harness_run.py (llmcf's real gate, the llmcf-install test build): the dry run and the run
after their questions (default "No"), the installer's output live in the pane as plain text while the GUI thread
stays free, an answer sent beside the running step, the report; Detach leaves the installer running and Re-attach
reads on; every text in English and German with the same placeholders. Every name here is a test value."""

import re
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from test_boards_window import Boxes, framework, wait  # noqa: E402
from test_bundle_ui import _stored_board  # noqa: E402
from test_harness_run import harness_board  # noqa: E402,F401 - the fake board with a harness bundle applied
from test_llmcf_install import helper_binary, sudo_on_path  # noqa: E402,F401


def test_every_text_has_both_languages_with_the_same_placeholders():
    from orchestrator.utils.l10n_harness import TEXTS
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key, text in TEXTS["en"].items():
        assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", TEXTS["de"][key])), key


def test_the_installers_output_never_steers_the_window_or_the_terminal():
    from orchestrator.runtime.bundle.harness_run import plain_output
    text = plain_output(b"\x1b[31mred\x1b[0m\r\n\x1b]52;c;aGk=\x07title\x1b]0;x\x1b\\ ok\x07\x1b(B\x00end")
    assert text == "red\ntitle ok end" or text == "red\ntitle okend"
    assert "\x1b" not in text and "\x07" not in text


@pytest.fixture
def window(qapp, harness_board, tmp_path, monkeypatch):
    from orchestrator.gui.harness_window import HarnessWindow
    from orchestrator.runtime.device import person_steps, remote
    shell, built, state = harness_board
    _stored_board(state)
    monkeypatch.setattr(person_steps, "connect_stored", lambda *a, **kw: (shell, "key"))
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    fw = framework(tmp_path)
    assert Path(fw.config.user_state_dir) == state
    dialog = HarnessWindow(fw, lang="en")
    dialog.reload()
    yield dialog, built
    if dialog.running:
        dialog._on_cancel()
        wait(qapp, dialog)
    dialog.deleteLater()
    qapp.processEvents()


def _until(qapp, dialog, words: str, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if words in dialog.output.toPlainText():
            return
        time.sleep(0.02)
    raise AssertionError(f"the pane never showed {words!r}: {dialog.output.toPlainText()[-400:]}")


def test_dry_run_run_with_a_live_answer_and_the_report(qapp, window, monkeypatch):
    dialog, built = window
    boxes = Boxes(monkeypatch, "yes")
    assert dialog.cmb_bundle.count() == 1 and dialog.cmb_bundle.currentData() == (built.bundle_id, "harness")
    dialog.buttons["btn_harness_run"].click()
    _until(qapp, dialog, "would install the demo agent")
    _until(qapp, dialog, "Name of the agent account?")
    assert dialog.running and len(boxes.asked) == 2 and all(b.default_is_no for b in boxes.asked)
    assert "dry run" in boxes.asked[0].text and "responsibility" in boxes.asked[1].text
    dialog.edt_answer.setText("agent-account")
    dialog.btn_send.click()
    assert dialog.edt_answer.text() == ""
    _until(qapp, dialog, "set up for agent-account")
    wait(qapp, dialog, timeout=60)
    assert "finished with findings" in dialog.txt_report.toPlainText()
    assert dialog.lst_runs.count() == 2


def test_a_no_starts_nothing_and_detach_leaves_the_run_for_re_attach(qapp, window, monkeypatch):
    from orchestrator.runtime.bundle import harness_run as hr
    dialog, built = window
    Boxes(monkeypatch, "no")
    dialog.buttons["btn_harness_dry_run"].click()
    qapp.processEvents()
    assert not dialog.running and hr.list_runs(dialog.state_dir) == []
    Boxes(monkeypatch, "yes")
    dialog.chk_skip_dry.setChecked(True)
    dialog.buttons["btn_harness_run"].click()
    _until(qapp, dialog, "Name of the agent account?")
    dialog._on_cancel()                       # Detach: the step's session closes, the installer goes on
    wait(qapp, dialog, timeout=30)
    assert "Detached" in dialog.output.toPlainText()
    dialog.lst_runs.item(0).setSelected(True)
    run = hr.list_runs(dialog.state_dir)[0]
    assert run.status == "running"
    dialog.buttons["btn_harness_attach"].click()
    time.sleep(0.5)
    qapp.processEvents()
    dialog.edt_answer.setText("second")
    dialog.btn_send.click()
    _until(qapp, dialog, "set up for second")
    wait(qapp, dialog, timeout=60)
    assert dialog.output.toPlainText().count("Name of the agent account?") == 1
