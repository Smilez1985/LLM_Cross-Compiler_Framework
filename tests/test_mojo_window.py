"""Mojo in the GUI (W1' of 2026-10-16: every action of `llmcf mojo` also by mouse, never only by a typed command).

Tools > Mojo Health Check... opens the Mojo window: Mojo Oracle runs off the GUI thread and every finding shows as the
CLI prints it; Run Mojo Miracle... asks first (No the default) and runs Mojo Miracle with the GUI as the person's
channel (the plans shown bind the repairs, the build images open in the deletion dialog with the full list); a board
a recorded fact points to gets a "Check <board> now" button, "Check a board..." checks any trusted board, and the
Deployment tab's "Check this board" does the same for the chosen board. A default run contacts no board (W1).
"""

import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.mojo import Finding, MojoReport, Severity
from orchestrator.Core.mojo.runner import MiracleEntry, MiracleReport
from test_mojo_boards import board, framework_at, record
from test_mojo_cli import open_state  # noqa: F401 - the fixture of the end-to-end test

pytest.importorskip("PySide6")

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")


def wait_for(qapp, condition, timeout=10.0):
    """Lets the GUI thread deliver the workers' results until `condition()` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("the worker did not finish")
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def tree_rows(tree):
    """Each top-level row's texts with its children's texts."""
    rows = []
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        rows.append(([item.text(c) for c in range(tree.columnCount())],
                     [[item.child(j).text(c) for c in range(tree.columnCount())] for j in range(item.childCount())]))
    return rows


@pytest.fixture
def answers(monkeypatch):
    """Every plain message box (questions included) answered as the test says, recorded with its default button."""
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import plain
    state = SimpleNamespace(answer=QMessageBox.No, shown=[])

    def answer(box):
        default = box.defaultButton()
        state.shown.append((box.text(), default is not None and default is box.button(QMessageBox.No)))
        return state.answer
    monkeypatch.setattr(plain, "_exec", answer)
    return state


def gui_framework(tmp_path, client=None):
    """A framework with a per-user folder, a Docker client (or none) and an audit log."""
    from test_doctor_fixes import FakeAudit
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state),
                           get_component=lambda name: client if name == "docker_client" else None,
                           tool_registry=SimpleNamespace(audit=FakeAudit(client.events if client else [])))


def problems():
    """A repairable problem with its plan, one Mojo Miracle opens the image list for, one only the person can do,
    an information with a hint and a fine one."""
    from orchestrator.Core.mojo.offers import REASONS, Offer
    step = SimpleNamespace(target_label="the state folder", full_path="/srv/llm/state", before="0755", after="0700",
                           undoable=True)
    repairable = Finding("security", "state.permissions", Severity.WARN, "The state folder is open to other users.",
                         "Make it private.", where="/srv/llm/state", why="Other users could read your keys.")
    disk = Finding("disk", "disk.cache", Severity.ERROR, "Only 1.0 GB free for the cache folder.", "Free disk space.",
                   where="/srv/llm/cache", why="a full disk stops a build", cannot="deletion_list")
    audit = Finding("ai", "tools.audit", Severity.ERROR, "The audit log was changed.", "Archive it.",
                    why="the record cannot be trusted", cannot="person")
    info = Finding("python", "python.extra", Severity.INFO, "An optional package is missing.", "Install it later.")
    fine = Finding("python", "python.version", Severity.OK, "Python 3.13")
    plan = SimpleNamespace(summary="only you can open the state folder then", steps=[step], sha256=lambda: "0" * 64)
    offered = [Offer(repairable, "miracle", plan, ""), Offer(disk, "deletion_list", None, REASONS["deletion_list"]),
               Offer(audit, "person", None, REASONS["person"])]
    return MojoReport([fine, info, repairable, disk, audit]), offered


@pytest.fixture
def engine(monkeypatch):
    """Mojo as the window calls it (orchestrator.Core.mojo.Mojo and offers), recording each call and its thread."""
    import orchestrator.Core.mojo as mojo_package
    state = SimpleNamespace(report=None, offered=[], after_offered=[], runs=[], miracles=[], last=None, freed=[])

    class FakeMojo:
        def __init__(self, framework, checks=None, contact=()):
            state.runs.append(("init", framework, tuple(contact)))
            self.framework = framework

        def run(self):
            state.runs.append(("run", threading.current_thread() is threading.main_thread()))
            return state.report

        def miracle(self, **kwargs):
            state.miracles.append(dict(kwargs, gui_thread=threading.current_thread() is threading.main_thread()))
            freed = kwargs["free_space"]() if kwargs.get("free_space") is not None else (0, "")
            state.freed.append(freed)
            state.last = miracle_result(state.offered, freed)
            return state.last

    monkeypatch.setattr(mojo_package, "Mojo", FakeMojo)
    monkeypatch.setattr(mojo_package, "offers",
                        lambda framework, report: state.offered if report is state.report else state.after_offered)
    return state


def opened(qapp, framework, engine=None, lang="en"):
    from orchestrator.gui.mojo_window import MojoWindow
    window = MojoWindow(framework, lang=lang)
    window.show()
    wait_for(qapp, lambda: window._token is None)
    return window


# --- the menu entry --------------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
def test_tools_mojo_health_check_opens_the_window_next_to_the_image_list(qapp, tmp_path, monkeypatch, memory_keyring,
                                                                         engine):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QPushButton

    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window
    from orchestrator.gui.menus import mojo as mojo_menu
    from orchestrator.gui.mojo_window import MojoWindow
    engine.report, engine.offered = problems()
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    win = main_window.MainOrchestrator(REPO_ROOT)
    qapp.processEvents()
    try:
        tools = next(m.menu() for m in win.menuBar().actions() if m.text() == "Tools")
        texts = [a.text() for a in tools.actions()]
        assert texts.index("Mojo Health Check...") == texts.index("Framework Build Images...") + 1
        action = win.findChild(QAction, "act_mojo_health")
        assert action is win.act_mojo_health and action.text() == "Mojo Health Check..."
        assert win.findChild(QAction, "act_framework_images") is win.act_images, "the covered entry is named"
        assert win.findChild(QPushButton, "btn_check_board") is win.deployment_window.btn_check_board
        assert engine.runs == [], "nothing runs before the person opens the window"
        action.trigger()
        window = win.mojo_window
        assert isinstance(window, MojoWindow) and window.isVisible() and window.parent() is win
        wait_for(qapp, lambda: window._token is None)
        assert ("run", False) in engine.runs, "Mojo Oracle ran off the GUI thread"
        assert engine.runs[0] == ("init", win.framework, ()), "the default run names no board to contact"
        action.trigger()   # a second click brings the open window to the front
        assert win.mojo_window is window and len(win.findChildren(MojoWindow)) == 1
        for path, name in mojo_menu.CLI_PATHS.items():
            holder = window if mojo_menu.WINDOW_OBJECTS.get(name) == "act_mojo_health" else win
            assert holder.findChild((QAction, QPushButton)[name.startswith("btn")], name) is not None, (path, name)
        assert {"mojo", "mojo --oracle", "mojo --miracle", "mojo --board"} <= set(mojo_menu.CLI_PATHS)
        window.close()
    finally:
        win.close()


# --- Mojo Oracle -----------------------------------------------------------------------------------------------------

def test_every_finding_shows_as_the_cli_prints_it(qapp, tmp_path, engine):
    from orchestrator.Core.mojo.views import oracle_summary
    engine.report, engine.offered = problems()
    window = opened(qapp, gui_framework(tmp_path))
    try:
        rows = tree_rows(window.tree)
        assert [r[0][:2] for r in rows] == [["✔ OK", "python"], ["ℹ Information", "python"],
                                            ["! Warning", "security"], ["✘ Error", "disk"], ["✘ Error", "ai"]]
        assert not window.tree.topLevelItem(4).icon(0).isNull(), "an icon next to the severity's text"
        assert rows[0][1] == [] and rows[1][1] == [["", "Next step", "Install it later."]]
        assert rows[2][1] == [["", "Where", "/srv/llm/state"], ["", "Why it matters", "Other users could read your keys."],
                              ["", "Next step", "Make it private."],
                              ["", "Mojo Miracle", "Mojo Miracle can repair it: only you can open the state folder "
                                                   "then."],
                              ["", "Plan", "the state folder (/srv/llm/state): 0755 → 0700, can be undone"]]
        assert rows[3][1][-1][2].startswith("Mojo Miracle shows you the framework's build images to choose from")
        assert rows[4][1][-1][2] == "Mojo Miracle cannot repair it: " + engine.offered[2].reason + "."
        assert window.summary.text() == oracle_summary(engine.offered, person=True)
        assert window.overall.text() == "Overall: errors (exit code 2)."
        assert window.btn_miracle.isEnabled() and window.btn_check.isEnabled()
        assert window.boards_note.text() == "No recorded fact points to a problem with a board, so Mojo contacted none."
        engine.report = MojoReport([Finding("python", "python.version", Severity.OK, "Python 3.13")])
        engine.offered = []
        window.btn_check.click()
        wait_for(qapp, lambda: window._token is None)
        assert window.overall.text() == "Overall: no problems (exit code 0)." and not window.btn_miracle.isEnabled()
        assert window.summary.text() == "Mojo Oracle found no problems."
    finally:
        window.close()


# --- Mojo Miracle ----------------------------------------------------------------------------------------------------

def test_mojo_miracle_asks_first_and_no_is_the_default(qapp, tmp_path, engine, answers):
    engine.report, engine.offered = problems()
    window = opened(qapp, gui_framework(tmp_path))
    try:
        window.btn_miracle.click()
        text, no_default = answers.shown[-1]
        assert text.startswith("Run Mojo Miracle now?") and no_default, "the CLI's question, No preselected"
        assert engine.miracles == [] and window.status.text() == "Mojo Miracle did not run; nothing was changed."
    finally:
        window.close()


def test_mojo_miracle_waits_for_something_it_can_act_on(qapp, tmp_path, engine, answers):
    report, offered = problems()
    engine.report, engine.offered = report, [offered[2]]   # only a problem that needs the person
    window = opened(qapp, gui_framework(tmp_path))
    try:
        assert not window.btn_miracle.isEnabled() and window.run_miracle() is False
        assert answers.shown == [] and engine.miracles == []
    finally:
        window.close()


def miracle_result(offered, freed):
    """What Mojo Miracle reports: the repair, the image list (as the person answered it) and the person's problem."""
    repaired, disk, audit = offered
    images = (MiracleEntry(disk.finding, "deletion_list", "repaired", f"you deleted {freed[0]} item(s) from the list")
              if freed[0] else MiracleEntry(disk.finding, "deletion_list", "declined",
                                            reason="you chose nothing from the list, so nothing was deleted"))
    after = MojoReport([disk.finding, audit.finding] if not freed[0] else [audit.finding])
    return MiracleReport([MiracleEntry(repaired.finding, "miracle", "repaired", repaired.plan.summary), images,
                          MiracleEntry(audit.finding, "person", "not_done", reason=audit.reason)], after)


def test_mojo_miracle_runs_with_the_gui_as_the_persons_channel(qapp, tmp_path, engine, answers, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from test_doctor_fixes import FakeClient, deletions, images

    from orchestrator.Core.mojo.docker_objects import framework_image_plan
    from orchestrator.gui import mojo_window
    client = FakeClient(images())
    framework = gui_framework(tmp_path, client)
    engine.report, engine.offered = problems()
    asked = []

    def ask_deletion(parent, plan, **kwargs):
        asked.append((parent, plan, kwargs, threading.current_thread() is threading.main_thread()))
        return None   # the person ticks nothing: "No, keep everything"
    monkeypatch.setattr(mojo_window, "ask_deletion", ask_deletion)
    window = opened(qapp, framework)
    try:
        answers.answer = QMessageBox.Yes
        window.btn_miracle.click()
        wait_for(qapp, lambda: window._token is None)
        (call,) = engine.miracles
        assert call["actor"] == "gui" and call["at_terminal"] is True and call["decision"] == "person"
        assert call["answered_by"] == "person" and call["confirm_channel"] == "framework-gui"
        assert call["before"] is engine.report and call["offered"] is engine.offered, "the plans shown bind it"
        assert call["gui_thread"] is False, "Mojo Miracle runs off the GUI thread"
        ((parent, plan, kwargs, gui_thread),) = asked
        assert parent is window and gui_thread, "the deletion dialog opens on the GUI thread"
        assert kwargs["multiple"] is True and kwargs["title"] == "Framework Build Images"
        assert [i.file_name for i in plan.items] == [i.file_name for i in framework_image_plan(framework).items], \
            "the full list"
        assert engine.freed == [(0, "")] and deletions(client) == [], "nothing ticked: nothing deleted"
    finally:
        window.close()


def test_mojo_miracles_image_list_is_the_real_deletion_dialog_with_no_as_the_default(qapp, tmp_path, engine, answers,
                                                                                     monkeypatch):
    """The list Mojo Miracle opens is the framework's deletion dialog itself (not a copy): every framework build image
    with its full id, nothing ticked, "No" the default button; the person's No deletes nothing."""
    from PySide6.QtWidgets import QMessageBox
    from test_doctor_fixes import FakeClient, deletions, images

    from orchestrator.Core.mojo.docker_objects import framework_image_plan
    from orchestrator.gui import deletion_dialog
    client = FakeClient(images())
    framework = gui_framework(tmp_path, client)
    engine.report, engine.offered = problems()
    shown = []

    def exec_no(dialog):
        shown.append((dialog.btn_no.isDefault(), dialog.ticked(), dialog.multiple, dialog.plain_text()))
        return 0   # the person's "No, keep everything"
    monkeypatch.setattr(deletion_dialog.DeletionConfirmDialog, "exec", exec_no)
    window = opened(qapp, framework)
    try:
        answers.answer = QMessageBox.Yes
        window.btn_miracle.click()
        wait_for(qapp, lambda: window._token is None)
        ((no_default, ticked, multiple, text),) = shown
        assert no_default and ticked == [] and multiple, "No is the default and nothing is ticked"
        for item in framework_image_plan(framework).items:
            assert item.file_name in text and item.speaking_name in text, "the full list with each full id"
        assert engine.freed == [(0, "")] and deletions(client) == []
    finally:
        window.close()


def test_mojo_miracle_deletes_exactly_what_the_person_ticked_and_shows_what_became_of_each_problem(
        qapp, tmp_path, engine, answers, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from test_doctor_fixes import FakeClient, deletions, images

    from orchestrator.Core.deletion import accept, choose
    from orchestrator.Core.mojo.views import miracle_summary
    from orchestrator.gui import mojo_window
    client = FakeClient(images())
    framework = gui_framework(tmp_path, client)
    engine.report, engine.offered = problems()
    monkeypatch.setattr(mojo_window, "ask_deletion",
                        lambda parent, plan, **kwargs: accept(choose(plan, [0]), "gui", "permanent"))
    window = opened(qapp, framework)
    try:
        answers.answer = QMessageBox.Yes
        window.btn_miracle.click()
        wait_for(qapp, lambda: window._token is None)
        assert engine.freed == [(1, "")] and len(deletions(client)) == 1, "exactly the ticked image"
        assert framework.tool_registry.audit.entries[0]["actor"] == "gui"
        rows = tree_rows(window.results)
        assert rows[0][0][1] == "Your list of the framework's build images" and rows[0][0][2].startswith("1 images")
        assert rows[0][1][0][2].startswith("Deleted for good: Framework build image")
        assert rows[1][0] == ["✔ Repaired", "security (state.permissions)",
                              "only you can open the state folder then. Checked again: the problem is gone."]
        assert rows[2][0][0] == "✔ Repaired" and "you deleted 1 item(s)" in rows[2][0][2]
        assert rows[3][0][0] == "✘ Not repaired" and rows[3][1] == [["", "Next step", "Archive it."]]
        assert window.results_summary.text() == miracle_summary(engine.last) and window.results.isVisible()
        assert window.overall.text() == "Overall: errors (exit code 2).", "the window shows the check afterwards"
        assert [r[0][1] for r in tree_rows(window.tree)] == ["ai"]
    finally:
        window.close()


def test_a_closed_window_answers_the_waiting_list_with_nothing(qapp, tmp_path, engine, answers, monkeypatch):
    """The worker waits for the person's list; when the window closes first, nothing is deleted."""
    from PySide6.QtWidgets import QMessageBox
    from test_doctor_fixes import FakeClient, deletions, images

    from orchestrator.gui import mojo_window
    client = FakeClient(images())
    engine.report, engine.offered = problems()
    monkeypatch.setattr(mojo_window, "ask_deletion", lambda *a, **k: pytest.fail("the window was closed"))
    window = opened(qapp, gui_framework(tmp_path, client))
    answers.answer = QMessageBox.Yes
    window.btn_miracle.click()
    window.close()   # before the GUI thread delivered the list
    deadline = time.monotonic() + 10
    while not engine.freed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert engine.freed == [(0, "")] and deletions(client) == []


@POSIX
def test_the_window_repairs_with_the_real_engine_as_the_cli_does(qapp, booted_framework, open_state, answers,
                                                                 monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import mojo_window
    monkeypatch.setattr(mojo_window, "ask_deletion", lambda *a, **k: pytest.fail("Docker is not available"))
    window = opened(qapp, booted_framework)
    try:
        rows = {r[0][2]: r[1] for r in tree_rows(window.tree)}
        plan = [c[2] for children in rows.values() for c in children if c[1] == "Plan"]
        assert plan == [f"the state folder ({open_state}): 0755 → 0700, can be undone"]
        assert (os.stat(open_state).st_mode & 0o777) == 0o755, "Mojo Oracle only looks"
        answers.answer = QMessageBox.Yes
        window.btn_miracle.click()
        wait_for(qapp, lambda: window._token is None, timeout=30)
        assert (os.stat(open_state).st_mode & 0o777) == 0o700
        results = {r[0][1]: r for r in tree_rows(window.results)}
        assert results["security (state.permissions)"][0][0] == "✔ Repaired"
        assert results["disk (disk.cache)"][0][2].startswith("nothing was deleted: Docker is not available"), \
            "the list could not be shown: said so, never counted as the person's no"
        assert window.results_summary.text() == "Mojo Miracle repaired 1 of 5 problems; 4 were not repaired (why: " \
                                                "above)."
    finally:
        window.close()


# --- boards (W1: only on the person's click) ------------------------------------------------------------------------

def counted(monkeypatch, answer=None):
    from orchestrator.runtime.device import reasons
    asked = []
    monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=reasons.CONNECT_TIMEOUT: asked.append(
        (host, port, threading.current_thread() is threading.main_thread())) or answer)
    return asked


def test_a_board_a_recorded_fact_points_to_gets_its_check_button(qapp, tmp_path, monkeypatch):
    from orchestrator.Core.mojo import runner
    from orchestrator.runtime.device import reasons
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [runner.check_devices])
    state = tmp_path / "state"
    state.mkdir()
    garage = board(state, "192.0.2.11", label="Garage board")
    board(state, "192.0.2.12", label="Kitchen")
    record(state, garage, "verify", reasons.PORT_CLOSED)
    asked = counted(monkeypatch)
    window = opened(qapp, framework_at(state))
    try:
        assert asked == [], "Mojo Oracle contacts no board"
        button = window.board_buttons[garage.device_id]
        assert button.text() == "Check Garage board now" and button.objectName() == f"btn_mojo_check_{garage.device_id}"
        assert list(window.board_buttons) == [garage.device_id], "only the board a recorded fact points to"
        rows = {r[0][2]: r[1] for r in tree_rows(window.tree)}
        assert any(["", "Board", "Mojo did not contact the board Garage board."] in children
                   for children in rows.values())
        button.click()
        assert window.board_result.text().startswith("Checking Garage board (one connection to its SSH port")
        wait_for(qapp, lambda: "checked now" in window.board_result.text())
        assert asked == [("192.0.2.11", 22, False)], "one connection, to that board, off the GUI thread"
        assert window.board_result.text().startswith("✔ OK: Board Garage board") and button.isEnabled()
        assert "radxa" not in window.board_result.text(), "the device account never shows"
    finally:
        window.close()


def test_check_a_board_lets_the_person_pick_any_trusted_board(qapp, tmp_path, monkeypatch, answers, engine):
    from PySide6.QtWidgets import QInputDialog

    from orchestrator.runtime.device import reasons
    engine.report, engine.offered = MojoReport([]), []
    state = tmp_path / "state"
    state.mkdir()
    empty = opened(qapp, framework_at(state))
    try:
        assert empty.pick_board() is None and "No board is trusted yet" in answers.shown[-1][0]
    finally:
        empty.close()
    board(state, "192.0.2.11", label="Garage board")
    desk = board(state, "192.0.2.12", port=2222, label="Desk")
    asked = counted(monkeypatch, answer=reasons.PORT_CLOSED)
    offered = []

    def get_item(parent, title, text, items, current=0, editable=True):
        offered.append((title, text, list(items), editable))
        return items[0], True
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    window = opened(qapp, framework_at(state))
    try:
        assert window.btn_pick_board.objectName() == "btn_mojo_check_board"
        window.btn_pick_board.click()
        title, text, items, editable = offered[0]
        assert items == ["Desk (192.0.2.12:2222)", "Garage board (192.0.2.11)"] and editable is False
        assert "one connection to its SSH port, no login" in text
        wait_for(qapp, lambda: "checked now" in window.board_result.text())
        assert asked == [("192.0.2.12", 2222, False)]
        lines = window.board_result.text().splitlines()
        assert lines[0].startswith("! Warning: Board Desk, checked now") and reasons.PORT_CLOSED in lines[0]
        assert lines[1].startswith("Next step: ")
        assert desk.device_id not in window.board_buttons, "no recorded fact: no button of its own"
    finally:
        window.close()


def test_check_a_board_lets_the_person_pick_each_of_two_boards_that_read_the_same(qapp, tmp_path, monkeypatch,
                                                                                  engine):
    """A board set up again keeps its address and often its name but has a new host key, so a second trusted record
    (a new device ID) reads the same as the first. `llmcf mojo --board` then lists both with their device IDs; the GUI
    names the device ID too, so the person can pick each one by mouse (the list is not the first one twice)."""
    from PySide6.QtWidgets import QInputDialog

    from orchestrator.runtime.device.trusted import TrustedDevices
    engine.report, engine.offered = MojoReport([]), []
    state = tmp_path / "state"
    state.mkdir()
    board(state, "192.0.2.11", label="Garage board")
    board(state, "192.0.2.11", label="Garage board")   # set up again: a new host key, the same name and address
    board(state, "192.0.2.12", label="Desk")
    first, second = [d for d in TrustedDevices(state).list() if d.label == "Garage board"]
    offered = []

    def get_item(parent, title, text, items, current=0, editable=True):
        offered.append(list(items))
        return items[pick], True
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    window = opened(qapp, framework_at(state))
    started = []
    monkeypatch.setattr(window, "check_board", lambda device_id, label: started.append((device_id, label)) or True)
    try:
        for pick, device in ((1, first), (2, second)):
            assert window.pick_board() == device.device_id
        items = offered[0]
        assert len(set(items)) == len(items) == 3, "each trusted board can be told apart"
        assert items[0] == "Desk (192.0.2.12)", "a board that reads like no other keeps its label and address"
        assert items[1] == f"Garage board (192.0.2.11, device ID {first.device_id})"
        assert started == [(first.device_id, "Garage board"), (second.device_id, "Garage board")]
    finally:
        window.close()


def test_the_deployment_tabs_check_this_board(qapp, window_framework, answers, monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.gui.deployment_window import DeploymentWindow
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    state = Path(window_framework.config.user_state_dir)
    win = DeploymentWindow(window_framework)
    assert not win.btn_check_board.isEnabled(), "no board to check"
    key = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, "myboard.local", 22, key)
    device = TrustedDevices(state).record("myboard.local", 22, "radxa", key, via="gui", paired=True,
                                          label="Radxa on the shelf")
    win._refresh_boards(select=device.device_id)
    assert win.btn_check_board.isEnabled() and win.btn_check_board.text() == "Check this board"
    asked = counted(monkeypatch, answer=reasons.PORT_CLOSED)
    win.btn_check_board.click()
    assert win.lbl_bench.text().startswith("Checking Radxa on the shelf") and not win.btn_check_board.isEnabled()
    wait_for(qapp, lambda: "checked now" in win.lbl_bench.text())
    assert asked == [("myboard.local", 22, False)], "the chosen board, off the GUI thread, once"
    assert reasons.PORT_CLOSED in win.lbl_bench.text() and "Next step: " in win.lbl_bench.text()
    assert answers.shown and reasons.PORT_CLOSED in answers.shown[-1][0], "a problem is also said in a message"
    assert win.btn_check_board.isEnabled() and "radxa" not in win.lbl_bench.text()
    win.close()


@pytest.fixture
def window_framework(make_framework, tmp_path):
    from test_input_history import framework as make_window_framework
    return make_window_framework.__wrapped__(make_framework, tmp_path)


# --- texts -----------------------------------------------------------------------------------------------------------

def test_mojo_texts_exist_in_en_and_de_with_the_same_placeholders():
    from orchestrator.utils.l10n_mojo import TEXTS
    from orchestrator.utils.localization import ui_text
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key in TEXTS["en"]:
        fields = [set(re.findall(r"{(\w+)}", TEXTS[lang][key])) for lang in ("en", "de")]
        assert fields[0] == fields[1], key
    assert ui_text("mojo.menu", "en") == "Mojo Health Check..." and ui_text("mojo.menu", "de") != ui_text("mojo.menu",
                                                                                                         "en")
    from orchestrator.Core.mojo.views import MIRACLE_LINES
    for kind, words in MIRACLE_LINES.items():
        assert TEXTS["en"]["mojo.can." + kind] == words, "the GUI's English words are the CLI's"


def test_the_window_speaks_german(qapp, tmp_path, engine):
    engine.report, engine.offered = problems()
    window = opened(qapp, gui_framework(tmp_path), lang="de")
    try:
        assert window.windowTitle() == "Mojo-Gesundheitscheck"
        assert window.btn_miracle.text() == "Mojo Miracle ausführen..." and window.btn_check.text() == "Erneut prüfen"
        assert window.overall.text() == "Gesamt: Fehler (Exit-Code 2)."
        rows = tree_rows(window.tree)
        assert rows[3][0][0] == "✘ Fehler" and ["", "Wo", "/srv/llm/cache"] in rows[3][1]
        assert any(c[2].startswith("Mojo Miracle kann es reparieren") for c in rows[2][1])
    finally:
        window.close()


def test_no_rich_text_in_the_mojo_windows():
    for path in (REPO_ROOT / "orchestrator" / "gui" / "mojo_window.py",
                 REPO_ROOT / "orchestrator" / "gui" / "menus" / "mojo.py"):
        source = path.read_text(encoding="utf-8")
        for api in ("setHtml", "RichText", "QMessageBox.information(", "QMessageBox.warning(", "QMessageBox.question("):
            assert api not in source, (path.name, api)
        assert all("plain_tooltip(" in line for line in source.splitlines() if "setToolTip(" in line)
