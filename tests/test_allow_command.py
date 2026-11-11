"""The allow-to-run question of a command (Features "Allow a command (human in the loop)"; DESIGN "GUI and the
person's path"), offscreen through the real main window.

- [Run...] appears only for a step the framework may run, in a next-step row and under a chat record;
- the question reads "Allow `llmcf ...`? What. Why." with No as the default; No (and Escape) runs nothing;
- Yes runs exactly the proposal on a worker thread through run_proposal, and its output appears in the question and
  in the Ditto console; the end says the exit status; each decision is in the activity record;
- a secret goes only through stdin and never appears in the output, the console, the audit log or the log;
- Cancel stops the command; a changed proposal is refused; a deleting step goes to its own deletion list instead.
"""

import dataclasses
import logging
import sys
import threading
import time

import pytest

from test_ditto_console import main_window  # noqa: F401 - the real main window, offscreen

from orchestrator.utils import next_steps as ns
from orchestrator.utils.next_steps import NextStep

SECRET = "s3cr3t-" + "Kq7" * 6     # made up; must never appear anywhere but the child's stdin


def wait_for(qapp, condition, seconds=120.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    return condition()


def fake_child(monkeypatch, code: str):
    """run_proposal starts `python -c code` instead of the CLI (its check of the proposal still runs first)."""
    monkeypatch.setattr(ns, "child_argv", lambda prop, config_path=None, python=None: [sys.executable, "-c", code])


def decisions(win):
    return [e for e in win.framework.tool_registry.audit.entries(50) if e["tool"] == "command_proposal"]


def ready_window(main_window, qapp):  # noqa: F811
    win = main_window()
    win.show()
    qapp.processEvents()
    win.console_dock.show()
    return win


def run_button(row):
    from PySide6.QtWidgets import QPushButton
    return [b for b in row.findChildren(QPushButton) if b.objectName().startswith("btn_step_run_")]


def test_run_appears_only_for_a_step_the_framework_may_run(qapp, main_window):  # noqa: F811
    from orchestrator.gui.next_step_widgets import StepsRow
    win = ready_window(main_window, qapp)
    row = StepsRow(["trash.show", "trash.empty", "permissions.edit", "board.ssh_enable"], window=win, lang="en")
    buttons = run_button(row)
    assert [b.objectName() for b in buttons] == ["btn_step_run_trash_show"], "never for a deletion, a place or a " \
                                                                             "board's command"
    assert buttons[0].text() == "Run..." and "&" not in buttons[0].text()


def test_yes_runs_a_catalogue_step_and_its_output_appears(qapp, main_window, monkeypatch):  # noqa: F811
    from orchestrator.gui.next_step_widgets import StepsRow
    win = ready_window(main_window, qapp)
    row = StepsRow(["trash.show"], window=win, lang="en")
    run_button(row)[0].click()
    dialog = row.dialog
    assert dialog is not None and dialog.lbl_question.text().startswith("Allow `llmcf trash list`?")
    assert dialog.btn_no.isDefault() and not dialog.btn_yes.isDefault(), "No is the default"
    gui_thread = threading.get_ident()
    seen_threads = []
    real = dialog.runner

    def runner(*args, **kwargs):
        seen_threads.append(threading.get_ident())
        return real(*args, **kwargs)
    dialog.runner = runner
    monkeypatch.setenv("LLM_SECRET_STORAGE", "ephemeral")   # the child has no keyring of the test's own process
    dialog.btn_yes.click()
    assert wait_for(qapp, lambda: dialog.result is not None), dialog.lbl_status.text()
    assert seen_threads and seen_threads[0] != gui_thread, "never on the GUI thread"
    assert dialog.result.state == "done", (dialog.result, dialog.output.toPlainText())
    assert dialog.output.toPlainText().strip(), "the command's output is shown in the question"
    assert "Exit status: 0" in dialog.lbl_status.text() and not dialog.btn_close.isHidden()
    console = win.ditto_console
    assert wait_for(qapp, lambda: "llmcf trash list: Done" in console.shown_text(), 10), console.shown_text()
    text = console.shown_text()
    assert "You allowed  runs: allowed: llmcf trash list" in text and "output: llmcf trash list:" in text
    assert [(e["outcome"], e["actor"]) for e in decisions(win)] == [("ok", "gui")]
    dialog.btn_close.click()


def test_no_runs_nothing_and_escape_is_a_no(qapp, main_window, monkeypatch):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from orchestrator.gui.allow_command import ask_to_run
    win = ready_window(main_window, qapp)
    monkeypatch.setattr(ns, "child_argv", lambda *a, **k: pytest.fail("a No ran the command"))
    dialog = ask_to_run(win, "trash.show", lang="en")
    dialog.btn_no.click()
    assert dialog.decided is False and dialog.thread is None and not dialog.isVisible()
    again = ask_to_run(win, "trash.show", lang="en")
    QTest.keyClick(again, Qt.Key_Escape)
    QTest.keyClick(again, Qt.Key_Return)   # Enter on the question is the default button: No
    assert again.decided is False and again.thread is None
    assert [e["outcome"] for e in decisions(win)] == ["not_confirmed", "not_confirmed"]
    assert wait_for(qapp, lambda: win.ditto_console.shown_text().count("declined: llmcf trash list") == 2, 5)


def test_a_secret_goes_through_stdin_and_appears_nowhere(qapp, main_window, monkeypatch, caplog):  # noqa: F811
    from orchestrator.gui.allow_command import ask_to_run
    caplog.set_level(logging.DEBUG)
    win = ready_window(main_window, qapp)
    fake_child(monkeypatch, "import sys\nvalue = sys.stdin.readline().strip()\nprint('got', len(value))\n"
                            "print('echo', value)")
    dialog = ask_to_run(win, NextStep("secrets.set", {"name": "hf_token"}), lang="en")
    field = dialog.secret_fields["value"]
    assert not dialog.btn_yes.isEnabled(), "Yes waits for the hidden value"
    assert field.echoMode() == field.EchoMode.Password
    field.setText(SECRET)
    dialog.btn_yes.click()
    assert field.text() == "", "the field is emptied at once"
    assert wait_for(qapp, lambda: dialog.result is not None)
    output = dialog.output.toPlainText()
    assert f"got {len(SECRET)}" in output and "echo [hidden]" in output and SECRET not in output
    assert wait_for(qapp, lambda: "got" in win.ditto_console.shown_text(), 10)
    assert SECRET not in win.ditto_console.shown_text()
    assert all(SECRET not in str(e) for e in win.framework.tool_registry.audit.entries(100))
    assert SECRET not in caplog.text
    assert SECRET not in "\n".join(dialog.result.lines)
    dialog.btn_close.click()


def test_cancel_stops_the_command(qapp, main_window, monkeypatch):  # noqa: F811
    from orchestrator.gui.allow_command import ask_to_run
    win = ready_window(main_window, qapp)
    fake_child(monkeypatch, "import time\nprint('start', flush=True)\ntime.sleep(60)")
    dialog = ask_to_run(win, "trash.show", lang="en")
    dialog.btn_yes.click()
    assert wait_for(qapp, lambda: "start" in dialog.output.toPlainText(), 30)
    dialog.btn_cancel.click()
    assert wait_for(qapp, lambda: dialog.result is not None, 30)
    assert dialog.result.state == "cancelled" and "Cancelled" in dialog.lbl_status.text()
    assert [e["outcome"] for e in decisions(win)] == ["cancelled"]


def test_a_changed_proposal_is_refused(qapp, main_window, monkeypatch):  # noqa: F811
    from orchestrator.gui.allow_command import AllowCommandDialog
    win = ready_window(main_window, qapp)
    monkeypatch.setattr(ns, "child_argv", lambda *a, **k: pytest.fail("a changed proposal ran"))
    tampered = dataclasses.replace(ns.proposal("trash.show", lang="en"), argv=("trash", "empty", "--all"))
    dialog = AllowCommandDialog(tampered, win)
    dialog.btn_yes.click()
    assert wait_for(qapp, lambda: not dialog.btn_close.isHidden(), 30)
    assert dialog.lbl_status.text().startswith("Not run:") and dialog.result is None


def test_a_deleting_step_goes_to_its_own_list_and_a_place_opens(qapp, main_window, monkeypatch):  # noqa: F811
    from orchestrator.gui import allow_command
    from orchestrator.gui import next_step_widgets
    win = ready_window(main_window, qapp)
    opened = []
    monkeypatch.setattr(next_step_widgets, "open_object", lambda window, name: opened.append(name) or True)
    assert allow_command.ask_to_run(win, "trash.empty", lang="en") is None
    assert allow_command.ask_to_run(win, "permissions.edit", lang="en") is None
    assert opened == ["btn_trash_delete", "act_tools_permissions"]


def test_a_chat_record_offers_run_for_its_steps(qapp):
    from orchestrator.Core.chat_feed import FeedEntry, FeedRefs, FeedSender, StoredEntry, new_entry_id
    from orchestrator.gui.chat_panel.bubbles import make_bubble
    entry = FeedEntry(entry_id=new_entry_id(), ts="2026-11-11T10:00:00+00:00", kind="blocked",
                      sender=FeedSender(role="client", caller="mcp"), text_key="activity.blocked",
                      params={"tool": "list_trash"}, refs=FeedRefs(steps=["trash.show"]), level="warning",
                      program="mcp")
    bubble = make_bubble(StoredEntry(1, entry), lang="en")
    assert bubble.steps_row is not None and run_button(bubble.steps_row), "the record's step can be run after a yes"
