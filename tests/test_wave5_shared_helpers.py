"""Helpers the two halves of the GUI parity wave each built (wave 5 integration): one implementation, used from every
place.

- Finding and placing menus: every area module of orchestrator/gui/menus/ goes through orchestrator/gui/menus/
  place.py, which finds a menu by its object name before its title, so a translated title (wave 6 translates the
  Security, Tools and Help titles) never makes a module add a second menu.
- A yes/no question from a worker thread: the Boards window, the AI settings' runtime box and Ditto > Local
  Runtime... ask through worker_relay.ask_from_worker / WorkerQuestion ("No" is the default, the worker waits)."""

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

MENUS = REPO_ROOT / "orchestrator" / "gui" / "menus"
RAW_MENU_CALLS = {"actions", "addMenu", "insertMenu", "insertAction"}


def _calls(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node.func.attr


def test_every_area_module_finds_and_places_its_menus_through_the_one_helper():
    modules = sorted(p for p in MENUS.glob("*.py") if p.name not in ("__init__.py", "place.py"))
    assert len(modules) >= 9, [p.name for p in modules]
    for path in modules:
        raw = RAW_MENU_CALLS & set(_calls(path))
        assert not raw, f"{path.name} looks up or places menus itself ({sorted(raw)}); use menus/place.py"


def _translated_bar():
    """A main window whose menus carry the object names of MainOrchestrator._setup_menu and German titles."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMainWindow
    from orchestrator.gui.menus import place
    win = QMainWindow()
    win.framework = SimpleNamespace()
    german = {"menu.file": "Datei", "menu.exit": "Beenden", "menu.open_hub": "Community Hub öffnen"}
    win.i18n = SimpleNamespace(t=lambda key, default=None: german.get(key, default))
    bar = win.menuBar()
    menus = {}
    for name, title in ((place.MENU_FILE, "Datei"), (place.MENU_SECURITY, "Sicherheit"), (place.MENU_VIEW, "Ansicht"),
                        (place.MENU_TOOLS, "Werkzeuge"), (place.MENU_HELP, "Hilfe")):
        menus[name] = bar.addMenu(title)
        menus[name].setObjectName(name)
    win.act_output_folder = QAction("Output Folder...", win)
    menus[place.MENU_FILE].addAction(win.act_output_folder)
    menus[place.MENU_FILE].addAction(QAction("Beenden", win))
    menus[place.MENU_SECURITY].addAction(QAction("Set OpenAI API Key", win))
    tools = menus[place.MENU_TOOLS]
    for text in ("Create Hardware Module...", "Community Hub öffnen", "Tool Permissions (Ditto / MCP)...",
                 "Folders the AI May Use...", "Pending Approvals..."):
        tools.addAction(QAction(text, win))
    win.act_snapshots = QAction("Knowledge Snapshots...", win)
    win.act_images = QAction("Framework Build Images...", win)
    win.act_framework_docs = QAction("Index the Framework's Documentation...", win)
    for action in (win.act_snapshots, win.act_images, win.act_framework_docs):
        tools.addAction(action)
    return win, bar, menus


def test_the_menus_are_found_by_object_name_whatever_their_title(qapp):
    """With translated titles every area module still finds File, Security and Tools by their object names: no
    module adds a second Security or Tools menu, Build and Boards follow File, Ditto comes before Security."""
    from orchestrator.gui.menus import (boards, build, ditto_models, ditto_runtime, knowledge, mojo, place, signin,
                                        tools_mcp)
    win, bar, menus = _translated_bar()
    try:
        for module in (build, boards, ditto_runtime, ditto_models, signin, tools_mcp, knowledge, mojo):  # main window
            module.install(win, bar)
        names = [m.objectName() for m in place.menus(bar)]
        assert names == [place.MENU_FILE, place.MENU_BUILD, place.MENU_BOARDS, place.MENU_DITTO, place.MENU_SECURITY,
                         place.MENU_VIEW, place.MENU_TOOLS, place.MENU_HELP], names
        security = [a.objectName() for a in menus[place.MENU_SECURITY].actions() if not a.isSeparator()]
        assert security == ["act_signin_openai_key", "act_signin_secrets", "act_signin_overview",
                            "act_signin_key_storage", "act_tools_mcp_audit", "act_tools_mcp_connections"]
        tools = [a.objectName() for a in menus[place.MENU_TOOLS].actions()]
        assert tools == ["act_tools_module_wizard", "act_knowledge_hub", "act_tools_permissions", "act_tools_folders",
                         "act_tools_mcp_crawl_notice", "act_tools_approvals", "act_knowledge_snapshots",
                         "act_knowledge_sources", "act_framework_images", "act_mojo_health",
                         "act_knowledge_index_docs"]
        file_entries = [a.objectName() or a.text() for a in menus[place.MENU_FILE].actions()]
        assert file_entries == ["act_file_output_folder", "act_file_settings", "Beenden"]
    finally:
        win.deleteLater()


def test_place_finds_by_name_before_title_and_names_a_menu_found_by_title(qapp):
    from PySide6.QtWidgets import QMainWindow
    from orchestrator.gui.menus import place
    win = QMainWindow()
    bar = win.menuBar()
    try:
        decoy = bar.addMenu("Tools")
        real = bar.addMenu("Werkzeuge")
        real.setObjectName(place.MENU_TOOLS)
        assert place.find_menu(bar, place.MENU_TOOLS, ("Tools",)) is real
        assert place.find_menu(bar, place.MENU_HELP, ("&Help",)) is None
        helpm = bar.addMenu("&Help")
        assert place.find_menu(bar, place.MENU_HELP, ("Help",)) is helpm and helpm.objectName() == place.MENU_HELP
        made = place.menu_or_new(bar, "menu_extra", "Extra")
        assert place.menus(bar)[-1] is made and made.objectName() == "menu_extra"
        assert place.menu_or_new(bar, "menu_extra", "Extra") is made, "found again, not made twice"
        first = place.new_action(win, "act_one", "One", lambda: None)
        last = place.new_action(win, "act_two", "Two", lambda: None)
        decoy.addAction(first)
        place.insert_after(decoy, first, last)
        middle = place.new_action(win, "act_mid", "Mid", lambda: None)
        place.insert_after(decoy, first, middle)
        place.insert_before(decoy, None, place.new_action(win, "act_end", "End", lambda: None))
        assert [a.objectName() for a in decoy.actions()] == ["act_one", "act_mid", "act_two", "act_end"]
        assert place.name_entry(first, "act_other").objectName() == "act_one", "a name it has already stays"
    finally:
        win.deleteLater()


def test_one_worker_question_for_every_window():
    from orchestrator.gui import boards_window, ditto_runtime_window, runtime_mode_box, worker_relay
    assert boards_window.ask_from_worker is worker_relay.ask_from_worker
    assert runtime_mode_box.ask_from_worker is worker_relay.ask_from_worker
    assert ditto_runtime_window.relay_question is worker_relay.ask_from_worker
    for module in (boards_window, runtime_mode_box, ditto_runtime_window):
        assert module.WorkerQuestion is worker_relay.WorkerQuestion
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "answered.wait" not in source and "event.wait" not in source, module.__name__


def test_a_worker_question_is_answered_in_the_gui_thread_and_wakes_the_worker(qapp):
    from PySide6.QtCore import QObject, Qt
    from orchestrator.gui.worker_relay import WorkerQuestion, ask_from_worker, worker_relay

    token, asked, results = object(), [], []

    class Receiver(QObject):
        def take(self, got_token, question):
            if got_token is token and isinstance(question, WorkerQuestion):
                asked.append((threading.current_thread() is threading.main_thread(), question.title, question.text))
                question.reply(lambda: question.text == "yes please")

    receiver = Receiver()
    worker_relay().done.connect(receiver.take, Qt.QueuedConnection)
    try:
        for text in ("yes please", "no thanks"):
            worker = threading.Thread(target=lambda t=text: results.append(ask_from_worker(token, "Title", t)))
            worker.start()
            while worker.is_alive():
                qapp.processEvents()
                worker.join(0.01)
        assert results == [True, False]
        assert asked == [(True, "Title", "yes please"), (True, "Title", "no thanks")], "asked in the GUI thread"
        # a stop hook that gives up (its window was closed) ends the wait with "No", without an answer
        assert ask_from_worker(object(), "Title", "nobody answers", stop=lambda: True, interval=0.01) is False
        # a failing question still wakes the worker
        question = WorkerQuestion("Title", "text")
        with pytest.raises(RuntimeError):
            question.reply(lambda: (_ for _ in ()).throw(RuntimeError("gone")))
        assert question.answered.is_set() and question.yes is False
    finally:
        worker_relay().done.disconnect(receiver.take)
        receiver.deleteLater()
