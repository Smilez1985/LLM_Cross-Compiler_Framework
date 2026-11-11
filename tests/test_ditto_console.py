"""The Ditto console beside the chat (DESIGN "GUI and the person's path", the row "Beside the chat sits the Ditto
console").

- the reader (Core/ditto_console.py): what Ditto and AI apps type and do, from the signed chat feed and the audit log,
  in order, with the source marked; the person's own calls left out; secrets, key material and the home folder never
  shown; an archived audit log read again from its start; nothing ever written to either record;
- the console (gui/chat_panel/console.py): batches, a capped ring buffer, the filters, Pause Scrolling, Clear View
  (the view only) and Copy;
- the real main window: a dock of its own beside the chat dock, hidden at the first start, View > Ditto Console
  (Shift+F9) and the toolbar's Console button, live rows from a tool call, and visibility remembered across restarts;
- `llm-cli ditto console`: the same lines at a terminal, filtered, as JSON, and followed.
"""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from orchestrator.Core.activity import ActivityProducer
from orchestrator.Core.chat_feed import FeedSender, FeedStore, FeedWriter
from orchestrator.Core.ditto_console import (MAX_LINES, ConsoleLine, ConsoleReader, clock, hide_secret_values,
                                             line_of_audit, matches, scrub, secret_name)
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.output import ToolOutput
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolRegistry, ToolSpec

KEY = b"c" * 32                       # stands in for the audit key from the keyring
SECRET = "hf_" + "Zq9" * 12           # the shape of a Hugging Face token, made up
REQ = "req_0a1b2c3d"


class In(BaseModel):
    text: str = "x"
    api_key: str = ""
    max_tokens: int = 0


class Out(ToolOutput):
    request_id: str = ""


def setup(tmp_path):
    """A registry with one tool that changes something and one that reads, its audit log, and the chat feed with the
    activity producer writing to it (as the framework attaches them)."""
    registry = ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "audit.db"))
    registry.register(ToolSpec("start_build", "Start a build", "Starts", ToolTier.LOCAL_WRITE, In, Out,
                               lambda i: Out(request_id=REQ)))
    registry.register(ToolSpec("list_targets", "List", "Lists", ToolTier.READ, In, Out, lambda i: Out()))
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=["start_build", "list_targets"],
                                             overrides={"start_build": Decision.ALLOW,
                                                        "list_targets": Decision.ALLOW}))
    feed = FeedStore(tmp_path / "state", key=KEY)
    producer = ActivityProducer(feed, program="mcp").attach(registry)
    return registry, feed, producer


def line(key="audit:1:t", ts="2026-11-11T10:00:00+00:00", source="ditto", caller="ditto", who="Ditto", kind="call",
         words="list_targets no arguments -> done"):
    return ConsoleLine(key, ts, source, caller, who, kind, words)


# --- the reader --------------------------------------------------------------------------------------------------

def test_key_material_in_arguments_is_never_shown():
    assert secret_name("api_key") and secret_name("password") and secret_name("hf-token")
    assert secret_name("key") and secret_name("ssh_key") and secret_name("client_secret")
    assert not secret_name("max_tokens") and not secret_name("key_storage_kind") and not secret_name("model")
    shown = hide_secret_values('{"model": "smol", "password": "pa\\"ss", "token": 123, "api_key": "abc')
    assert shown == '{"model": "smol", "password": "[hidden]", "token": "[hidden]", "api_key": "[hidden]"'
    home = str(Path.home())
    assert scrub(f"{home}/models and {SECRET}") == "~/models and hf_REDACTED"
    assert scrub("a\nb").startswith("a\\u000a") and len(scrub("x" * 5000)) == 1000, "one line, cut"


def test_the_reader_shows_what_ditto_and_apps_do_and_leaves_the_person_out(tmp_path):
    registry, feed, producer = setup(tmp_path)
    reader = ConsoleReader(feed, registry.audit)
    assert reader.first_look() == [], "nothing yet"
    registry.call("start_build", {"text": str(Path.home() / "model.gguf"), "api_key": SECRET},
                  ToolContext("mcp", client="Claude Code 2.1"))
    registry.call("list_targets", {}, ToolContext("ditto"))
    registry.call("list_targets", {}, ToolContext("gui"))
    registry.call("list_targets", {}, ToolContext("cli"))
    writer = FeedWriter(feed, "gui")
    writer.write("ditto_answer", FeedSender(role="ditto", caller="ditto"), ai_text="Two targets:\nrk3588 and x86",
                 ai_text_kind="answer")
    writer.write("person_message", FeedSender(role="person"), person_text="what do you know?")
    assert producer.flush()
    before = (registry.audit.count(), len(feed.after(0, view="all").entries))
    lines = reader.poll()
    shown = [entry.shown() for entry in lines]
    text = "\n".join(shown)
    assert len(lines) == 5, text
    assert "what do you know" not in text, "the person's message is not shown"
    assert sum("list_targets" in s for s in shown) == 1, "the person's own calls are left out"
    home = str(Path.home())
    assert SECRET not in text and "[hidden]" in text and "~" in text and "model.gguf" in text
    assert home not in text and json.dumps(home)[1:-1] not in text, "the home folder, also as JSON writes it"
    calls = [entry for entry in lines if entry.kind == "call"]
    assert [(c.caller, c.text.rsplit("-> ", 1)[1]) for c in calls] == [
        ("mcp", "started"), ("mcp", "done"), ("ditto", "done")]
    assert "calls itself 'Claude Code 2.1'" in calls[0].who, "an app's own name only as its claim"
    did = next(entry for entry in lines if entry.kind == "did")
    assert did.source == "app" and "Started a build" in did.text
    says = next(entry for entry in lines if entry.kind == "says")
    assert says.who == "Ditto" and "Two targets:\\u000ark3588" in says.text, "AI text stays on one line"
    assert [entry.kind for entry in lines][-2:] == ["did", "says"] or lines.index(did) > lines.index(calls[1]), \
        "within one second a call comes before what came of it"
    assert reader.poll() == [], "read once"
    assert (registry.audit.count(), len(feed.after(0, view="all").entries)) == before, "the reader writes nothing"
    reader.close()


def test_refusals_questions_and_answers_read_as_words(tmp_path):
    entry = {"id": 7, "ts": "2026-11-11T10:00:00.000+00:00", "actor": "mcp:laptop", "tool": "start_build",
             "args_preview": "{}", "outcome": "not_confirmed", "answer": "declined", "reason_code": "permission_denied",
             "client": "Some App"}
    shown = line_of_audit(entry)
    assert shown.text == "start_build no arguments -> not confirmed, answer: no, reason: permission_denied"
    assert shown.level == "warning" and shown.source == "app" and "laptop" in shown.who
    assert line_of_audit(dict(entry, actor="gui")) is None and line_of_audit(dict(entry, actor="api")) is None
    german = line_of_audit(dict(entry, outcome="confirmation_requested", answer="", reason_code=""), lang="de")
    assert german.text.endswith("bittet um dein Ja") and german.shown("de").split("  ")[2].startswith("ruft auf")


def test_an_archived_audit_log_is_read_again_from_its_start(tmp_path):
    registry, feed, _producer = setup(tmp_path)
    reader = ConsoleReader(feed, registry.audit)
    for _ in range(3):
        registry.call("list_targets", {}, ToolContext("ditto"))
    assert len(reader.poll()) == 3
    assert registry.audit.archive() is not None
    registry.call("list_targets", {}, ToolContext("ditto"))
    again = reader.poll()
    assert len(again) == 1 and again[0].key.startswith("audit:1:"), "the new log's first entry"
    reader.close()


def test_the_filters():
    ditto, app = line(), line(key="feed:x", source="app", caller="mcp:laptop", who="laptop", kind="did",
                              words="Started a build.")
    assert matches(ditto, "ditto") and not matches(app, "ditto")
    assert matches(app, "apps") and not matches(ditto, "apps")
    assert matches(app, "mcp:laptop") and not matches(app, "mcp")
    assert matches(app, "all", "STARTED") and not matches(ditto, "all", "started")
    assert clock("not a time") == "--:--:--"


# --- the console -------------------------------------------------------------------------------------------------

def make_console(qapp, tmp_path, framework=None):
    from orchestrator.gui.chat_panel.console import DittoConsole
    registry, feed, producer = setup(tmp_path)
    reader = ConsoleReader(feed, registry.audit)
    console = DittoConsole(framework, reader=reader, start=False)
    return console, registry, feed, producer


def test_a_burst_is_one_batch_and_the_buffer_is_capped(qapp, tmp_path):
    console, registry, _feed, _producer = make_console(qapp, tmp_path)
    try:
        burst = [line(key=f"audit:{i}:t", words=f"call {i}") for i in range(MAX_LINES + 500)]
        console.follower.push(burst)
        added = console.take()
        assert added == MAX_LINES, "the queue keeps the newest lines"
        assert len(console.buffer) == MAX_LINES and console.view.document().blockCount() == MAX_LINES
        assert console.shown_text().splitlines()[-1].endswith(f"call {MAX_LINES + 499}")
        console.follower.push(burst[-3:])
        assert console.take() == 0, "a line that arrives twice is shown once"
    finally:
        console.stop()


def test_filters_pause_clear_and_copy(qapp, tmp_path):
    from PySide6.QtGui import QGuiApplication
    console, registry, feed, producer = make_console(qapp, tmp_path)
    try:
        registry.call("start_build", {}, ToolContext("mcp", client="App One"))
        registry.call("list_targets", {}, ToolContext("ditto"))
        producer.flush()
        console.follower.push(console.follower.reader.poll())
        console.take()
        assert len(console.shown_text().splitlines()) == 4
        sources = [console.cb_source.itemData(i) for i in range(console.cb_source.count())]
        assert sources == ["all", "ditto", "apps", "mcp"], "an app is offered once it appeared"
        console.cb_source.setCurrentIndex(1)
        assert console.shown_text().splitlines() and all("Ditto" in s for s in console.shown_text().splitlines())
        console.cb_source.setCurrentIndex(0)
        console.txt_search.setText("start_build")
        assert len(console.shown_text().splitlines()) == 2
        console.txt_search.clear()
        console.btn_pause.setChecked(True)
        bar = console.view.verticalScrollBar()
        bar.setValue(0)
        console.follower.push([line(key=f"audit:x{i}:t", words=f"more {i}") for i in range(200)])
        console.take()
        assert bar.value() == 0, "paused: the view stays where the person is"
        assert console.copy_lines() == 204 and "more 199" in QGuiApplication.clipboard().text()
        before = (registry.audit.count(), len(feed.after(0, view="all").entries))
        console.btn_clear.click()
        assert console.shown_text() == "" and not console.buffer
        assert (registry.audit.count(), len(feed.after(0, view="all").entries)) == before, "only the view is cleared"
        assert "&" not in "".join(b.text() for b in (console.btn_pause, console.btn_clear, console.btn_copy))
    finally:
        console.stop()


def test_this_processs_activity_rows_come_at_once(qapp, tmp_path):
    from orchestrator.gui.chat_panel.console import DittoConsole
    registry, feed, producer = setup(tmp_path)
    framework = SimpleNamespace(chat_feed=feed, tool_registry=registry, activity=producer)
    console = DittoConsole(framework, start=False)
    try:
        registry.call("start_build", {}, ToolContext("mcp"))
        assert producer.flush()
        assert console.take() == 1 and "Started a build" in console.shown_text(), "the listener, before any look"
    finally:
        console.stop()
    assert not producer._listeners, "the listener ends with the console"


# --- the main window ---------------------------------------------------------------------------------------------

@pytest.fixture
def main_window(qapp, tmp_path, monkeypatch, memory_keyring):
    from conftest import REPO_ROOT
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window as module
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir(exist_ok=True)

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(module, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(module.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    windows = []

    def factory():
        win = module.MainOrchestrator(REPO_ROOT)
        windows.append(win)
        return win
    yield factory
    for win in windows:
        win.update_timer.stop()
        if win.isVisible() or win.framework is not None:
            win.close()


def wait_for(qapp, condition, seconds=10.0):
    import time
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    return condition()


def test_the_console_sits_beside_the_chat_and_is_remembered(main_window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QDockWidget
    win = main_window()
    win.show()
    qapp.processEvents()
    win.chat_window.notice.btn_ok.click()   # the notice on Ditto is read (until then a restart shows the chat)
    dock = win.findChild(QDockWidget, "consoleDock")
    assert dock is win.console_dock and dock.isHidden(), "hidden at the first start"
    assert win.dockWidgetArea(dock) == Qt.RightDockWidgetArea == win.dockWidgetArea(win.chat_dock)
    view = win.view_menu.actions()
    assert view[0] is win.chat_dock.toggleViewAction() and view[2] is dock.toggleViewAction()
    assert view[2].shortcut() == QKeySequence("Shift+F9") and view[2].objectName() == "act_view_console"
    assert win.btn_console.text() == "Console" and "&" not in win.btn_console.text()
    win.btn_console.click()
    qapp.processEvents()
    assert not dock.isHidden() and not win.chat_dock.isHidden(), "both areas side by side"
    assert dock.x() > win.chat_dock.x() and dock.width() >= 280
    # Ditto's call through the in-process path shows up live.
    win.framework.tool_registry.call("get_status", {}, ToolContext("ditto"))
    win.ditto_console.follower.look_now()
    assert wait_for(qapp, lambda: "get_status" in win.ditto_console.shown_text()), win.ditto_console.shown_text()
    assert "Ditto" in win.ditto_console.shown_text()
    win.chat_dock.hide()
    assert not dock.isHidden(), "each area on its own"
    win.close()
    second = main_window()
    second.show()
    qapp.processEvents()
    assert not second.console_dock.isHidden() and second.chat_dock.isHidden(), "remembered across a restart"
    assert wait_for(qapp, lambda: "get_status" in second.ditto_console.shown_text()), "the records are read again"
    second.console_dock.toggleViewAction().trigger()
    assert second.console_dock.isHidden()
    second.close()
    third = main_window()
    qapp.processEvents()
    assert third.console_dock.isHidden()


def test_gui_state_keeps_the_consoles_visibility(tmp_path):
    from orchestrator.gui.gui_state import FILE_NAME, GuiState
    state = GuiState.load(tmp_path)
    assert (state.console_visible, state.console_sized) == (False, False)
    state.console_visible = state.console_sized = True
    assert state.save()
    assert (GuiState.load(tmp_path).console_visible, GuiState.load(tmp_path).console_sized) == (True, True)
    (tmp_path / FILE_NAME).write_text(json.dumps({"version": 1, "console_visible": "yes"}))
    assert GuiState.load(tmp_path).console_visible is False, "a wrong value gets the default"


# --- the command line --------------------------------------------------------------------------------------------

@pytest.fixture
def cli_framework(tmp_path, monkeypatch):
    from orchestrator import cli
    registry, feed, producer = setup(tmp_path)
    framework = SimpleNamespace(chat_feed=feed, tool_registry=registry, activity=producer)

    def require(self):
        self.framework_manager, self._initialized = framework, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    return framework


def invoke(*args):
    from click.testing import CliRunner
    from orchestrator import cli
    return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args))


def test_the_command_line_prints_the_same_lines(cli_framework):
    registry = cli_framework.tool_registry
    registry.call("start_build", {"api_key": SECRET}, ToolContext("mcp", client="App One"))
    registry.call("list_targets", {}, ToolContext("ditto"))
    registry.call("list_targets", {}, ToolContext("cli"))
    cli_framework.activity.flush()
    before = registry.audit.count()
    result = invoke("ditto", "console")
    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 4 and SECRET not in result.output and "[hidden]" in result.output
    rows = [json.loads(row) for row in invoke("ditto", "console", "--json", "--source", "ditto").stdout.splitlines()]
    assert [(r["caller"], r["kind"]) for r in rows] == [("ditto", "call")]
    assert len(invoke("ditto", "console", "--search", "start_build").stdout.splitlines()) == 2
    assert len(invoke("ditto", "console", "--limit", "1").stdout.splitlines()) == 1
    assert invoke("ditto", "console", "--source", "someone else").exit_code == 2
    assert registry.audit.count() == before, "the command line writes nothing to the records"


def test_follow_prints_new_lines_until_stopped(cli_framework, monkeypatch):
    from orchestrator.Core import ditto_console
    registry = cli_framework.tool_registry
    stop = threading.Event()
    real = ditto_console.follow

    def once(reader, emit, **kwargs):
        registry.call("list_targets", {}, ToolContext("ditto"))
        stop.set()
        real(reader, emit, stop=stop, sleep=lambda s: None)
        for entry in reader.poll():
            emit(entry)
    monkeypatch.setattr(ditto_console, "follow", once)
    result = invoke("ditto", "console", "--follow")
    assert result.exit_code == 0 and "list_targets" in result.output, result.output
