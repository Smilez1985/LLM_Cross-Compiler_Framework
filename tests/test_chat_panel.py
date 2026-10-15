"""The chat panel and its dock (delegation design 5.1, 5.2, 5.6, 5.9; work package WP6).

The former AI Chat tab lives in a dock on the right of the main window. Everything it shows is plain text: a model's
answer, an app's self-reported name or a file name never becomes rich text, a link or a line of the framework's own
text. Ids in AI text are shown as speaking names, with the original words one click away. What it shows comes from
the signed chat feed; questions that wait come from the pending-approvals store. "Clear chat" lists every file with
full path, file name, size and what it is, with "No" as the default, and keeps the records of what AIs did (hidden,
not deleted).
"""

import ast
import json
import os
import re
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.chat_feed import FeedEntry, FeedRefs, FeedSender, FeedStore, FeedWriteError, FeedWriter

pytestmark = pytest.mark.usefixtures("qapp")

KEY = b"k" * 32          # stands in for the audit key from the keyring (tests build no real credential)
REPO_ROOT = Path(__file__).resolve().parent.parent
GUI = REPO_ROOT / "orchestrator" / "gui"
JOB = "job_k3j2h1g0f9e8d7c6"
RLO, ZWSP, LRI, LSEP = chr(0x202E), chr(0x200B), chr(0x2066), chr(0x2028)


# --- helpers -------------------------------------------------------------------------------------------------------

class RecordingAudit:
    """Stands in for the audit log: records what was written."""

    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **fields):
        self.entries.append((actor, tool, outcome, fields))
        return len(self.entries)


class FakeDitto:
    """Ditto set up with a local model (the header shows it; nothing is asked)."""

    def describe(self, task="chat"):
        return {"label": "Ollama (local)", "local": True, "auth_type": "none", "auth_source": "none"}


class FakeWorker:
    started = []

    def __init__(self, ditto, question, history, confirm):
        FakeWorker.started.append(question)
        for name in ("answered", "failed", "tool_used"):
            setattr(self, name, SimpleNamespace(connect=lambda f: None))

    def start(self):
        pass

    def isRunning(self):
        return False


@pytest.fixture
def feed(tmp_path):
    store = FeedStore(tmp_path / "feed-state", key=KEY)
    yield store
    store.close()


@pytest.fixture
def make_panel(tmp_path, monkeypatch):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui import chat_window
    monkeypatch.setattr(chat_window, "ChatWorker", FakeWorker)
    FakeWorker.started = []
    made = []

    def factory(feed=None, audit=None, ditto=None, jobs=True, registry=None, **extra):
        if registry is None and audit is not None:
            registry = SimpleNamespace(audit=audit)
        framework = SimpleNamespace(
            config=ConfigManager(tmp_path / "configs"), ditto_manager=ditto if ditto is not None else FakeDitto(),
            chat_feed=feed, tool_registry=registry,
            job_service=SimpleNamespace(get=lambda value: SimpleNamespace(kind="hf_download")) if jobs else None,
            **extra)
        panel = chat_window.ChatWindow(framework)
        made.append(panel)
        return panel

    yield factory
    for panel in made:
        panel.close()
        panel.deleteLater()


def client(name="laptop", app="Claude Code 2"):
    return FeedWriter.sender("client", f"mcp:{name}", client=app)


def framework_sender():
    return FeedSender(role="framework")


def decide(feed, caller="laptop", answer="yes", reason="The disk is tight but enough, and the person asked for it.",
           **content):
    content.setdefault("params", {"tool": "Start a build"})
    return FeedWriter(feed, "mcp").delegated_answer(client(caller), answer, reason, **content)[0]


def at(entry, moment):
    return entry.model_copy(update={"ts": moment.isoformat(timespec="seconds")})


def labels(widget):
    from PySide6.QtWidgets import QLabel
    return widget.findChildren(QLabel)


def approval_record(title="Start a build", caller="mcp:laptop", client_name="Claude Code 2", minutes=30,
                    approval_id="ap_0123456789abcdef"):
    from orchestrator.tools.approvals import ApprovalRecord
    now = datetime.now(timezone.utc)
    return ApprovalRecord(id=approval_id, caller=caller, client=client_name, tool="start_build", title=title,
                          args_sha256="0" * 64, question="Start a build of a model", question_sha256="1" * 64,
                          created=now.isoformat(timespec="seconds"),
                          expires=(now + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
                          state="pending", pid=os.getpid())


def approvals_store(tmp_path, *records):
    from orchestrator.tools.approvals import ApprovalStore
    store = ApprovalStore(tmp_path / "approvals", key=KEY)
    for record in records:
        store.add(record)
    return store


def fill_feed(feed):
    """One row of every kind a person sees today, with markup, ids and hidden characters in what others wrote."""
    gui, mcp = FeedWriter(feed, "gui"), FeedWriter(feed, "mcp")
    gui.person_message("Build <b>Qwen</b> for my board & check it")
    FeedWriter(feed, "ditto").write("ditto_answer", FeedWriter.sender("ditto", "ditto"),
                                    ai_text=f'<a href="https://evil.example">click</a> started {JOB}\n'
                                            "```sh\nls -la\n```", ai_text_kind="answer")
    mcp.write("client_reply", client(app="Ditto <img src=x>"), text_key="chat.kind.client_reply",
              ai_text="<script>alert(1)</script> I did it", ai_text_kind="reply")
    decide(feed, warnings_decided=[{"code": "disk_tight", "kind": "situation", "numbers": {}}])
    gui.write("notice", framework_sender(), text_key="chat.notice.proposal",
              params={"who": "<b>evil</b> & co", "build": "model"}, external=["who", "build"], level="warning")
    gui.write("job_result", framework_sender(), text_key="chat.kind.job_result", level="warning")
    gui.write("blocked", framework_sender(), text_key="chat.kind.blocked")


# --- R1: plain text only -------------------------------------------------------------------------------------------

def test_every_text_in_the_panel_is_plain_text(make_panel, feed, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractButton, QTextEdit, QWidget

    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    from orchestrator.gui.plain import TOOLTIP_PREFIX
    fill_feed(feed)
    panel = make_panel(feed)
    panel.link_approvals(approvals_store(tmp_path, approval_record(title="<b>Start</b> & build")))
    dialog = ClearChatDialog(panel, None, feed.clear_plan(), "en")
    for root in (panel, dialog):
        found = labels(root)
        assert found, "the panel shows its texts in labels"
        for label in found:
            assert label.textFormat() == Qt.PlainText, label.text()
            assert not label.openExternalLinks() and not label.textInteractionFlags() & Qt.LinksAccessibleByMouse
        assert root.findChildren(QTextEdit) == [], "no rich-text editor anywhere"
        for button in root.findChildren(QAbstractButton):
            assert not re.search(r"(?<!&)&(?!&)", button.text().replace("&&", "")), button.text()
        for widget in root.findChildren(QWidget):
            assert not widget.toolTip() or widget.toolTip().startswith(TOOLTIP_PREFIX), widget.toolTip()
    shown = panel.feed_view.plain_text()
    assert "<b>Qwen</b>" in shown and '<a href="https://evil.example">' in shown and "<script>" in shown


def _sources(*names):
    files = sorted((GUI / "chat_panel").glob("*.py"))
    return files + [GUI / name for name in names]


# The new and touched windows (design 5.2 R1, SEC-M2); a package that touches another window adds it here.
PLAIN_FILES = _sources("plain.py", "gui_state.py", "chat_window.py", "main_window.py")
# Rich-text APIs are refused in these as well (they have none today).
RICH_API_FILES = PLAIN_FILES + [GUI / name for name in ("approvals_window.py", "dialogs.py", "delegation_window.py",
                                                         "trash_window.py", "deletion_dialog.py",
                                                         "connect_app_wizard.py") if (GUI / name).exists()]
RICH_API = {"QTextBrowser", "setHtml", "setMarkdown", "insertHtml", "RichText", "AutoText"}


def rich_api_uses(source: str):
    """Names of rich-text APIs used in code (docstrings and style sheets do not count)."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in RICH_API:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in RICH_API:
            found.append(node.id)
        elif isinstance(node, ast.ImportFrom):
            found.extend(alias.name for alias in node.names if alias.name in RICH_API)
    return found


def test_no_rich_text_api_in_the_chat_panel_sources():
    offenders = {str(p.relative_to(REPO_ROOT)): rich_api_uses(p.read_text(encoding="utf-8")) for p in RICH_API_FILES}
    assert {k: v for k, v in offenders.items() if v} == {}
    # Every text widget of the panel package is made by plain_label (gui/plain.py), never a bare QLabel.
    for path in sorted((GUI / "chat_panel").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "QLabel"]
        assert bare == [], f"{path.name} builds a QLabel without plain_label at lines {bare}"
    # The scan finds what it looks for (a rule that never fires proves nothing).
    assert rich_api_uses("from PySide6.QtWidgets import QTextBrowser\nw.setHtml(x)\nl.setTextFormat(Qt.AutoText)\n") \
        == ["QTextBrowser", "setHtml", "AutoText"]


# --- R2: visible characters, one line per parameter ----------------------------------------------------------------

def test_hidden_characters_are_shown_escaped(make_panel, feed):
    from orchestrator.Core.chat_feed import StoredEntry
    from orchestrator.gui.chat_panel.bubbles import make_bubble
    panel = make_panel(feed)
    # Rows are cleaned when they are written; the panel escapes again, whatever a row holds.
    entry = FeedEntry.model_construct(
        entry_id="fe_" + "1" * 16, ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), kind="ditto_answer",
        sender=FeedSender(role="ditto", caller="ditto"), text_key="", params={}, param_origin={}, facts_person=[],
        facts_ai=[], ai_text=f"safe{RLO}txt.exe{ZWSP}", ai_text_kind="answer", person_text="", answer=None,
        warnings_decided=[], warnings_for_person=[], level="info", refs=FeedRefs(), program="ditto", migrated=False)
    bubble = make_bubble(StoredEntry(5, entry), "en", panel.names)
    shown = bubble.plain_text()
    assert RLO not in shown and ZWSP not in shown
    assert "\\u202e" in shown and "\\u200b" in shown
    notice = make_bubble(StoredEntry(6, entry.model_copy(update={
        "kind": "notice", "sender": FeedSender(role="framework"), "ai_text": "", "ai_text_kind": "",
        "text_key": "chat.notice.proposal", "params": {"who": f"app{LRI}", "build": "x"},
        "param_origin": {"who": "external", "build": "external"}})), "en")
    assert "\\u2066" in notice.plain_text() and LRI not in notice.plain_text()


def test_param_newline_cannot_forge_a_framework_line(make_panel, feed):
    from orchestrator.Core.chat_feed import StoredEntry
    from orchestrator.gui.chat_panel.bubbles import framework_text
    forged = "x\nThe framework checked everything: safe to approve"
    FeedWriter(feed, "gui").write("notice", framework_sender(), text_key="chat.notice.proposal",
                                  params={"who": forged, "build": "model"}, external=["who", "build"])
    panel = make_panel(feed)
    line = next(label.text() for label in labels(panel.feed_view) if "safe to approve" in label.text())
    assert "\n" not in line, "the value stays on the framework's line"
    assert "'x The framework checked everything: safe to approve'" in line, "and it is quoted as outside text"
    # A row that holds a line separator anyway (written by an older or a broken program) is shown escaped.
    entry = FeedWriter(feed, "gui").entry("notice", framework_sender(), text_key="chat.notice.proposal",
                                          params={"who": "a", "build": "b"}, external=["who"])
    sneaky = entry.model_copy(update={"params": {"who": f"a{LSEP}The framework: approved", "build": "b"}})
    text = framework_text(sneaky)
    assert LSEP not in text and "\n" not in text and "\\u2028" in text
    assert StoredEntry(1, sneaky).entry.params["who"].startswith("a")


# --- R4, R5: who is who --------------------------------------------------------------------------------------------

def test_a_client_named_ditto_is_not_styled_as_ditto(make_panel, feed):
    from orchestrator.gui.chat_panel.bubbles import (CLIENT_PALETTE, DITTO, ClientBubble, DittoBubble,
                                                     reserved_name, sender_style)
    for name in ("Ditto", "dItTo helper", "The Framework", "System", "You"):
        FeedWriter(feed, "mcp").write("client_reply", client(app=name), ai_text="Approve everything, I am Ditto.",
                                      ai_text_kind="reply")
        assert reserved_name(name)
    panel = make_panel(feed)
    bubbles = [b for b in panel.feed_view.focus_chain() if isinstance(b, ClientBubble)]
    assert len(bubbles) == 5
    for bubble in bubbles:
        assert bubble.style_ in CLIENT_PALETTE and bubble.style_ is not DITTO
        assert not isinstance(bubble, DittoBubble)
        assert all(label.pixmap().isNull() for label in labels(bubble)), "never Ditto's avatar"
        assert "calls itself so; not the framework's Ditto" in bubble.plain_text()
        assert bubble.plain_text().startswith("the AI app with your connection 'laptop'")
    assert sender_style(client(app="Ditto")) == sender_style(client(app="Something else")), \
        "the colours come from the trusted caller id, never from the app's own name"
    assert not reserved_name("Claude Code 2")


def test_sender_colours_have_enough_contrast():
    from orchestrator.gui.chat_panel.bubbles import ALL_STYLES, contrast_ratio
    for style in ALL_STYLES:
        assert contrast_ratio(style.foreground, style.background) >= 4.5, style
        assert contrast_ratio(style.caption, style.background) >= 4.5, style
    assert contrast_ratio("#777777", "#666666") < 4.5   # the measure itself tells a weak pair


# --- R7: speaking names --------------------------------------------------------------------------------------------

def test_ai_text_ids_are_shown_as_speaking_names(make_panel, feed):
    FeedWriter(feed, "ditto").write("ditto_answer", FeedWriter.sender("ditto", "ditto"),
                                    ai_text=f"I started {JOB} for you.\n```\ntail {JOB}.log\n```",
                                    ai_text_kind="answer")
    panel = make_panel(feed)
    bubble = panel.feed_view.focus_chain()[-1]
    shown = bubble.plain_text()
    assert "I started «Hugging Face download» for you." in shown
    assert f"tail {JOB}.log" in shown, "ids inside code keep their form"
    assert f"{JOB} = Hugging Face download" in shown, "and get a legend line"
    assert feed.after(0).entries[-1].entry.ai_text.startswith(f"I started {JOB}"), "the stored text is unchanged"


def test_only_exact_framework_ids_are_replaced(make_panel, feed):
    near = [f"{JOB}x", JOB.upper(), f"x{JOB}", JOB[:-1], f"{JOB}_2"]
    FeedWriter(feed, "ditto").write("ditto_answer", FeedWriter.sender("ditto", "ditto"), ai_text=" | ".join(near),
                                    ai_text_kind="answer")
    panel = make_panel(feed)
    shown = panel.feed_view.focus_chain()[-1].plain_text()
    assert "«" not in shown and all(value in shown for value in near)


def test_original_words_toggle_shows_unchanged_text(make_panel, feed):
    original = f"Download {JOB} is done; see {JOB} again."
    FeedWriter(feed, "ditto").write("ditto_answer", FeedWriter.sender("ditto", "ditto"), ai_text=original,
                                    ai_text_kind="answer")
    panel = make_panel(feed)
    body = panel.feed_view.focus_chain()[-1].body
    assert JOB not in body.plain_text() and body.btn_original is not None
    body.btn_original.setChecked(True)
    assert body.shown_text() == original and original in body.plain_text()
    body.btn_original.setChecked(False)
    assert "Download «Hugging Face download» is done" in body.plain_text()


# --- the remembered state ------------------------------------------------------------------------------------------

def test_panel_state_is_remembered_and_a_broken_file_gives_defaults(tmp_path):
    from orchestrator.gui.gui_state import FILE_NAME, GuiState
    state = GuiState.load(tmp_path)
    assert (state.chat_panel_visible, state.last_seen_entry, state.filter, state.dock_state) == (True, 0, "all", "")
    state.chat_panel_visible, state.last_seen_entry, state.filter = False, 42, "decisions"
    state.dock_state = GuiState.encode(b"\x00\x01dock")
    assert state.save()
    path = tmp_path / FILE_NAME
    assert path.stat().st_mode & 0o777 == 0o600
    again = GuiState.load(tmp_path)
    assert (again.chat_panel_visible, again.last_seen_entry, again.filter) == (False, 42, "decisions")
    assert GuiState.decode(again.dock_state) == b"\x00\x01dock"
    for broken in ("{not json", "[]", json.dumps({"version": 99, "chat_panel_visible": False}), "\xff\xfe"):
        path.write_text(broken, encoding="utf-8", errors="surrogateescape") if broken != "\xff\xfe" \
            else path.write_bytes(b"\xff\xfe\x00")
        assert GuiState.load(tmp_path).chat_panel_visible is True, broken
    path.write_text(json.dumps({"version": 1, "chat_panel_visible": "no", "last_seen_entry": -3, "filter": "<b>",
                                "dock_state": "not base64!", "geometry": 7}))
    odd = GuiState.load(tmp_path)
    assert (odd.chat_panel_visible, odd.last_seen_entry, odd.filter, odd.dock_state, odd.geometry) == \
        (True, 0, "all", "", ""), "a field with a wrong value gets its default"
    path.unlink()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"version": 1, "chat_panel_visible": False}))
    path.symlink_to(target)
    assert GuiState.load(tmp_path).chat_panel_visible is True, "a link is never followed"


@pytest.fixture
def main_window(tmp_path, monkeypatch, memory_keyring):
    from conftest import REPO_ROOT as ROOT
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import chat_window, main_window as module
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir(exist_ok=True)

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(module, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(module.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    monkeypatch.setattr(chat_window, "ChatWorker", FakeWorker)
    windows = []

    def factory():
        win = module.MainOrchestrator(ROOT)
        windows.append(win)
        return win

    yield factory
    for win in windows:
        win.update_timer.stop()
        if win.isVisible() or win.framework is not None:
            win.close()


def test_the_chat_tab_is_gone_and_the_panel_has_the_chat(main_window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QDockWidget, QMenu

    from orchestrator.gui.chat_window import ChatWindow
    win = main_window()
    win.show()
    qapp.processEvents()
    tabs = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert not any("Chat" in t for t in tabs), tabs
    dock = win.findChild(QDockWidget, "chatDock")
    assert dock is win.chat_dock and isinstance(dock.widget(), ChatWindow) and dock.widget() is win.chat_window
    assert win.dockWidgetArea(dock) == Qt.RightDockWidgetArea and dock.widget().minimumWidth() >= 300
    menus = {m.title(): m for m in win.findChildren(QMenu) if m.title()}
    view = [a for a in menus["View"].actions()]
    assert view[0] is dock.toggleViewAction() and view[0].shortcut() == QKeySequence("F9")
    assert view[1].shortcut() == QKeySequence("Ctrl+Shift+C")
    # Show and hide: F9 (the toggle), the toolbar button, a click on Ditto.
    view[0].trigger()
    assert dock.isHidden()
    win.btn_chat.click()
    assert not dock.isHidden()
    dock.hide()
    win._show_chat()
    assert not dock.isHidden()
    # The chat still talks to Ditto.
    win.chat_window.txt_input.setText("Which boards do you know?")
    win.chat_window.send_message()
    assert FakeWorker.started == ["Which boards do you know?"]
    assert "Which boards do you know?" in win.chat_window.feed_view.plain_text()
    assert win.framework.chat_feed.after(0).entries[-1].entry.person_text == "Which boards do you know?"


def test_the_dock_state_survives_a_restart_and_a_question_shows_it_without_focus(main_window, qapp):
    win = main_window()
    qapp.processEvents()
    win.chat_dock.hide()
    win.chat_window.header.set_filter("decisions")
    win.close()
    second = main_window()
    qapp.processEvents()
    assert second.chat_dock.isHidden(), "the hidden panel stays hidden after a restart"
    assert second.chat_window.header.current_filter() == "decisions"
    # A new question shows the hidden panel without taking the keyboard focus.
    store = second.framework.tool_registry.approvals.store
    store.add(approval_record())
    second.approval_notifier.check()
    qapp.processEvents()
    assert not second.chat_dock.isHidden()
    assert second.chat_window.pending_count() == 1 and "AI Chat (" in second.btn_chat.text()
    focus = qapp.focusWidget()
    assert focus is None or not second.chat_dock.isAncestorOf(focus)
    # The person can turn that off; then the status bar and the desktop notice stay.
    second.chat_window.header.act_auto_show.setChecked(False)
    assert second.framework.config.get("chat_panel_auto_show") is False
    second.chat_dock.hide()
    store.add(approval_record(approval_id="ap_fedcba9876543210", title="Download a file"))
    second.approval_notifier.check()
    qapp.processEvents()
    assert second.chat_dock.isHidden()


# --- the feed ------------------------------------------------------------------------------------------------------

def test_ditto_transcript_is_imported_once(make_panel, feed, tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.ditto_manager import ChatHistory, TranscriptEntry
    # Ditto's transcript from before the panel existed (the former AI Chat tab kept it in chat_history.json).
    history = ChatHistory(Path(ConfigManager(tmp_path / "configs").user_state_dir) / ChatHistory.FILE_NAME)
    history.transcript = [TranscriptEntry(role="user", text="hello Ditto"),
                          TranscriptEntry(role="tool", text="Used tool: List targets"),
                          TranscriptEntry(role="assistant", text="Hello! I know 3 boards.")]
    assert history.save()
    first = make_panel(feed)
    second = make_panel(feed)
    rows = feed.after(0).entries
    assert len(rows) == 3 and all(r.entry.migrated for r in rows), "imported once, by whichever window is first"
    for panel in (first, second):
        shown = panel.feed_view.plain_text()
        assert "hello Ditto" in shown and "Hello! I know 3 boards." in shown and "Used tool: List targets" in shown
    # The file stays where it was: Ditto's API conversation goes on from it.
    assert ChatHistory.load(second.history_file).transcript[0].text == "hello Ditto"


def test_rows_from_another_process_appear_within_a_poll(make_panel, feed):
    panel = make_panel(feed)
    counts = []
    panel.counts_changed.connect(lambda unread, pending: counts.append(unread))
    panel.apply_state(SimpleNamespace(last_seen_entry=0, filter="all"))
    FeedWriter(feed, "mcp").write("client_reply", client(), ai_text="Done with the build.", ai_text_kind="reply")
    assert panel.watcher.check() == 1
    assert "Done with the build." in panel.feed_view.plain_text()
    assert panel.unread_count() == 1 and counts[-1] == 1
    assert panel.watcher.check() == 0, "a row is shown once"
    panel.mark_seen()
    assert panel.unread_count() == 0


def test_a_message_that_cannot_be_saved_is_shown_and_marked(make_panel, feed, monkeypatch):
    panel = make_panel(feed)

    def full(*args, **kwargs):
        raise FeedWriteError("disk_full", "The chat could not be saved: the disk is full.")
    monkeypatch.setattr(feed, "append", full)
    panel.txt_input.setText("Please build it")
    panel.send_message()
    shown = panel.feed_view.plain_text()
    assert "Please build it" in shown and "Only shown in this window" in shown
    assert not panel.header.lbl_warning.isHidden() and "disk is full" in panel.header.lbl_warning.text()
    assert FakeWorker.started == ["Please build it"], "Ditto still answers"


def test_routine_decisions_are_grouped(make_panel, feed):
    from orchestrator.gui.chat_panel.bubbles import DecisionBubble, GroupBubble
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    writer = FeedWriter(feed, "mcp")

    def row(minute, caller="laptop", answer="yes", **content):
        entry = writer.entry("delegated_decision", client(caller), answer=answer, ai_text="Routine, as asked.",
                             ai_text_kind="reason", params={"tool": "Start a build"}, **content)
        feed.append(at(entry, start + timedelta(minutes=minute)))

    for minute in (0, 1, 2, 3):
        row(minute)
    row(4, answer="ask_person")                       # left for the person: never folded away
    for minute in (5, 6):
        row(minute, caller="desk")                    # another app: its own line
    row(20)                                           # more than ten minutes later
    row(21)
    panel = make_panel(feed)
    chain = panel.feed_view.focus_chain()
    groups = [b for b in chain if isinstance(b, GroupBubble)]
    assert len(groups) == 1 and len(groups[0].members) == 4
    assert groups[0].plain_text() == "the AI app with your connection 'laptop' did 4 things"
    assert [type(b) for b in chain[1:]] == [DecisionBubble] * 5
    groups[0].set_expanded(True)
    assert len(panel.feed_view.focus_chain()) == 10
    # A routine decision that arrives later joins the group while it is the last row and within ten minutes.
    fresh = datetime.now(timezone.utc)
    for minute in (0, 1, 2):
        entry = writer.entry("delegated_decision", client("desk"), answer="yes", ai_text="ok, as asked.",
                             ai_text_kind="reason")
        feed.append(at(entry, fresh + timedelta(seconds=minute)))
    panel.watcher.check()
    assert isinstance(panel.feed_view.focus_chain()[-1], GroupBubble) or any(
        isinstance(b, GroupBubble) and len(b.members) == 3 for b in panel.feed_view.focus_chain())


def test_away_summary_counts_from_feed_rows(make_panel, feed, tmp_path):
    from orchestrator.gui.chat_panel.feed_view import away_summary
    gui, ditto = FeedWriter(feed, "gui"), FeedWriter(feed, "ditto")
    gui.person_message("before")
    seen = feed.max_id()
    for _ in range(2):   # the AI's own words claim far more; only rows count
        ditto.write("ditto_answer", FeedWriter.sender("ditto", "ditto"), ai_text="I made 100 decisions and 9 builds.",
                    ai_text_kind="answer")
    for _ in range(3):
        decide(feed)
    gui.write("job_result", framework_sender(), text_key="chat.kind.job_result")
    gui.write("job_result", framework_sender(), text_key="chat.kind.job_result", level="warning")
    gui.write("blocked", framework_sender(), text_key="chat.kind.blocked")
    gui.person_message("my own message is not news to me")
    rows = [s.entry for s in feed.after(seen).entries]
    summary = away_summary(rows, pending=1)
    assert summary.endswith(": 2 answers from Ditto, 3 decisions by AI apps, 2 results (1 failed), 1 piece of work "
                            "an AI could not do; 1 thing needs you.")
    assert summary.startswith("Since ")
    assert away_summary([], 0) .endswith(": nothing new.")
    assert "2 Antworten von Ditto, 3 Entscheidungen von KI-Apps" in away_summary(rows, 0, "de")
    panel = make_panel(feed)
    panel.link_approvals(approvals_store(tmp_path, approval_record()))
    panel.apply_state(SimpleNamespace(last_seen_entry=seen, filter="all"))
    shown = panel.feed_view.plain_text()
    assert "New since you last looked" in shown and "1 thing needs you." in shown
    assert shown.index("New since you last looked") < shown.index("I made 100 decisions")
    assert shown.index("before") < shown.index("New since you last looked")


def test_pending_questions_come_from_the_approvals_store_not_from_feed_rows(make_panel, feed, tmp_path):
    from orchestrator.gui.chat_panel.bubbles import ApprovalBubble
    # A feed row says a question was asked, but the store has none pending (decided, expired): no question bubble.
    FeedWriter(feed, "mcp").write("approval", client(), text_key="chat.kind.approval",
                                  refs={"approval_id": "ap_0123456789abcdef"})
    panel = make_panel(feed)
    panel.link_approvals(approvals_store(tmp_path))
    assert not [b for b in panel.feed_view.focus_chain() if isinstance(b, ApprovalBubble)]
    assert panel.pending_count() == 0
    # A question in the store shows, whether or not a feed row was written for it.
    store = approvals_store(tmp_path / "other", approval_record(title="Download a file"))
    opened = []
    panel.open_approvals_requested.connect(opened.append)
    panel.link_approvals(store)
    pending = [b for b in panel.feed_view.focus_chain() if isinstance(b, ApprovalBubble)]
    assert len(pending) == 1 and "'Download a file' waits for your decision." in pending[0].plain_text()
    assert "calls itself 'Claude Code 2'" in pending[0].plain_text()
    assert panel.header.cb_filter.itemText(1) == "Needs you (1)"
    buttons = pending[0].findChildren(type(panel.btn_send))
    assert [b.text() for b in buttons] == ["Open in the approvals window…"], "no Yes/No in this package"
    buttons[0].click()
    assert opened == ["ap_0123456789abcdef"]
    panel.header.set_filter("needs_you")
    assert panel.feed_view.focus_chain() == pending


# --- "Clear chat" --------------------------------------------------------------------------------------------------

def _chat_with_history(make_panel, feed, audit=None):
    panel = make_panel(feed, audit=audit)
    panel.txt_input.setText("Hello Ditto")
    panel.send_message()
    from orchestrator.Core.ditto_manager import ChatReply
    from orchestrator.llm.types import Message
    panel._on_ai_response(ChatReply(question="Hello Ditto", text="Hello!",
                                    new_messages=[Message.user("Hello Ditto"), Message(role="assistant",
                                                                                       content="Hello!")]))
    decide(feed)
    panel.watcher.check()
    return panel


def test_clear_chat_lists_full_path_file_name_size_and_what_with_no_as_default(make_panel, feed, monkeypatch):
    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    panel = _chat_with_history(make_panel, feed, audit=RecordingAudit())
    before_rows, before_file = len(feed.after(0, view="all").entries), panel.history_file.read_bytes()
    seen = []

    def answer_no(dialog):
        seen.append(dialog)
        return 0
    monkeypatch.setattr(ClearChatDialog, "exec", answer_no)
    assert panel._confirm_clear_history() is False
    dialog = seen[0]
    shown = dialog.plain_text()
    history_path = str(panel.history_file.resolve())
    assert f"Full path: {history_path}" in shown and "File name: chat_history.json" in shown
    assert f"({len(before_file):,} bytes)" in shown or f"{len(before_file)} bytes" in shown
    assert "what Ditto remembers of your chat" in shown
    assert f"Full path: {feed.path.resolve()}" in shown and "File name: chat_feed.sqlite" in shown
    assert "Chat messages (2)" in shown and "1 record of what AI apps did is not deleted" in shown
    assert dialog.btn_no.isDefault() and not dialog.btn_yes.isDefault()
    assert dialog.btn_no.text() == "No, keep everything"
    assert len(feed.after(0, view="all").entries) == before_rows and panel.history_file.read_bytes() == before_file


def test_clear_chat_deletes_the_conversation_and_keeps_decision_records_hidden(make_panel, feed, monkeypatch):
    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    audit = RecordingAudit()
    panel = _chat_with_history(make_panel, feed, audit=audit)
    monkeypatch.setattr(ClearChatDialog, "exec", lambda dialog: 1)
    assert panel._confirm_clear_history() is True
    kinds = [s.entry.kind for s in feed.after(0, view="all").entries]
    assert kinds == ["delegated_decision", "feed_cleared"], "the record of what the AI did stays"
    assert [s.entry.kind for s in feed.after(0).entries] == ["feed_cleared"], "hidden from the panel"
    reloaded = json.loads(panel.history_file.read_text())
    assert reloaded["messages"] == [] and reloaded["transcript"] == []
    assert [(a, t, o) for a, t, o, _ in audit.entries] == [("gui", "chat_feed", "feed_cleared"),
                                                           ("gui", "chat_history", "feed_cleared")]
    assert all(fields["extra"]["deletion_sha256"] for *_, fields in audit.entries)
    shown = panel.feed_view.plain_text()
    assert "You cleared the chat" in shown and "Hello Ditto" not in shown and "Hello!" not in shown


def test_clear_chat_deletes_nothing_when_it_cannot_be_recorded(make_panel, feed, monkeypatch):
    from orchestrator.gui import plain
    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    panel = _chat_with_history(make_panel, feed, audit=None)
    told = []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)
    monkeypatch.setattr(ClearChatDialog, "exec", lambda dialog: 1)
    rows, history = len(feed.after(0, view="all").entries), panel.history_file.read_bytes()
    assert panel._confirm_clear_history() is False
    assert told and told[0].startswith("Nothing was deleted")
    assert len(feed.after(0, view="all").entries) == rows and panel.history_file.read_bytes() == history


def test_clear_chat_with_nothing_in_it_asks_nothing(make_panel, feed, monkeypatch):
    from orchestrator.gui import plain
    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    panel = make_panel(feed, audit=RecordingAudit())
    told = []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)
    monkeypatch.setattr(ClearChatDialog, "exec", lambda dialog: pytest.fail("nothing to delete, nothing to ask"))
    assert panel._confirm_clear_history() is False
    assert told == ["The chat is empty; there is nothing to delete."]


# --- R3: links -----------------------------------------------------------------------------------------------------

def test_links_open_only_after_the_whole_address_was_confirmed(monkeypatch):
    from orchestrator.gui import plain
    from orchestrator.gui.chat_panel import bubbles
    opened, asked = [], []
    monkeypatch.setattr(bubbles.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    url = "https://example.org/a-very/long/path?with=query&and=more"
    monkeypatch.setattr(plain, "_exec", lambda box: asked.append((box.text(), box.defaultButton())) or
                        plain.QMessageBox.No)
    assert bubbles.confirm_open_link(None, url) is False and opened == []
    assert url in asked[0][0] and asked[0][1] is not None
    monkeypatch.setattr(plain, "_exec", lambda box: plain.QMessageBox.Yes)
    assert bubbles.confirm_open_link(None, url) is True and opened == [url]
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x"):
        assert bubbles.confirm_open_link(None, bad) is False
    assert opened == [url]


# --- texts ---------------------------------------------------------------------------------------------------------

def test_chat_keys_exist_in_en_and_de_with_the_same_placeholders():
    from orchestrator.Core.chat_feed import TEXTS as FEED_TEXTS
    from orchestrator.utils.l10n_chat import TEXTS
    from orchestrator.utils.localization import LocalizationManager
    fields = string.Formatter()

    def placeholders(value):
        return {name for _, name, _, _ in fields.parse(value) if name}

    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key in TEXTS["en"]:
        assert key.startswith("chat."), key
        assert placeholders(TEXTS["en"][key]) == placeholders(TEXTS["de"][key]), key
        assert LocalizationManager.TRANSLATIONS["de"][key] == TEXTS["de"][key], "merged by LocalizationManager"
    # Every chat key the panel and the main window use has a text.
    known = set(TEXTS["en"]) | set(FEED_TEXTS["en"]) | set(LocalizationManager.TRANSLATIONS["en"])
    used = set()
    for path in PLAIN_FILES:
        used |= set(re.findall(r"[\"'](chat\.[a-z_]+(?:\.[a-z_]+)+)[\"']", path.read_text(encoding="utf-8")))
    assert used and sorted(used - known) == []


# --- keyboard and screen readers -----------------------------------------------------------------------------------

def test_keyboard_moves_between_bubbles_and_back_to_the_composer(make_panel, feed, qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    for n in range(4):
        FeedWriter(feed, "gui").person_message(f"message {n}")
    panel = make_panel(feed)
    panel.link_approvals(approvals_store(tmp_path, approval_record()))
    panel.resize(400, 600)
    panel.show()
    panel.activateWindow()
    qapp.processEvents()
    chain = panel.feed_view.focus_chain()
    assert len(chain) == 5

    def focused():
        qapp.processEvents()
        return qapp.focusWidget()

    chain[0].setFocus()
    assert focused() is chain[0]
    QTest.keyClick(chain[0], Qt.Key_Down)
    assert focused() is chain[1]
    QTest.keyClick(chain[1], Qt.Key_End)
    assert focused() is chain[-1], "the question that waits is the last stop"
    opened = []
    panel.open_approvals_requested.connect(opened.append)
    for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        QTest.keyClick(chain[-1], key)
    assert opened == [] and focused() is chain[-1], "no key on a bubble opens, approves or clicks anything"
    QTest.keyClick(chain[-1], Qt.Key_Home)
    assert focused() is chain[0]
    QTest.keyClick(chain[0], Qt.Key_Up)
    assert focused() is chain[0]
    QTest.keyClick(chain[0], Qt.Key_Escape)
    assert focused() is panel.txt_input
    QTest.keyClick(panel.txt_input, Qt.Key_Up)
    assert focused() is chain[-1], "Up in an empty composer goes to the newest bubble"
    panel.focus_composer()
    QTest.keyClicks(panel.txt_input, "line one")
    QTest.keyClick(panel.txt_input, Qt.Key_Return, Qt.ShiftModifier)
    QTest.keyClicks(panel.txt_input, "line two")
    assert panel.txt_input.text() == "line one\nline two"
    QTest.keyClick(panel.txt_input, Qt.Key_Return)
    assert FakeWorker.started == ["line one\nline two"] and panel.txt_input.text() == ""
    assert chain[0].accessibleName().startswith("You, ")


def test_new_messages_are_announced_to_screen_readers(make_panel, feed, tmp_path, monkeypatch):
    from PySide6.QtGui import QAccessible

    from orchestrator.gui.chat_panel import feed_view
    events = []
    monkeypatch.setattr(feed_view, "_post_announcement",
                        lambda event: events.append((event.message(), event.politeness())))
    panel = make_panel(feed)
    assert events == [], "what was there before is not announced"
    FeedWriter(feed, "mcp").write("client_reply", client(), ai_text="Build finished.", ai_text_kind="reply")
    FeedWriter(feed, "gui").write("blocked", framework_sender(), text_key="chat.kind.blocked")
    panel.watcher.check()
    panel.link_approvals(approvals_store(tmp_path, approval_record(title="Start a build")))
    polite, assertive = QAccessible.AnnouncementPoliteness.Polite, QAccessible.AnnouncementPoliteness.Assertive
    assert events[0][0].startswith("the AI app with your connection 'laptop', ") and events[0][1] == polite
    assert events[1][1] == assertive and "Work an AI could not do." in events[1][0]
    assert events[2] == ("A question waits for you: Start a build", assertive)
    assert "Build finished." not in " ".join(message for message, _ in events), \
        "announcements are the framework's words; the text itself is in the bubble"


def test_the_panel_follows_the_language(make_panel, feed):
    from orchestrator.utils.localization import get_instance
    FeedWriter(feed, "gui").write("notice", framework_sender(), text_key="chat.notice.new_conversation")
    panel = make_panel(feed)
    i18n = get_instance()
    before = i18n.current_lang
    try:
        i18n.set_language("de")
        assert panel.header.title.text() == "Ditto und deine KI-Apps" and panel.btn_send.text() == "Senden"
        assert "Das Gespräch wurde lang" in panel.feed_view.plain_text(), "framework text is re-rendered from its key"
    finally:
        i18n.set_language(before)
    assert panel.btn_send.text() == "Send" and "The conversation got long" in panel.feed_view.plain_text()


# --- review of WP6: what the first version let through -------------------------------------------------------------

def _tell_and_answer(monkeypatch, answer=1):
    """Records every plain-text box and every "Clear chat" question; the question is answered with `answer`."""
    from orchestrator.gui import plain
    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    told, asked = [], []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)
    monkeypatch.setattr(ClearChatDialog, "exec", lambda dialog: asked.append(dialog.plain_text()) or answer)
    return told, asked


def _conversation(path, text):
    """Ditto's conversation as another window or the terminal writes it into chat_history.json."""
    from orchestrator.Core.ditto_manager import ChatHistory, TranscriptEntry
    from orchestrator.llm.types import Message
    history = ChatHistory.load(path)
    history.messages.append(Message.user(text))
    history.transcript.append(TranscriptEntry(role="user", text=text))
    assert history.save()


def test_a_tool_title_the_framework_did_not_give_is_quoted_as_outside_text(make_panel, feed):
    """A tool note's title is the framework's words only for a tool registered here; a name the model made up, or a
    tool of the framework on another computer, is quoted like any outside text (design 5.2 R2)."""
    from orchestrator.Core.ditto_manager import ToolUse

    def get(name):
        if name != "list_targets":
            raise KeyError(name)
        return SimpleNamespace(title="List targets")

    panel = make_panel(feed, registry=SimpleNamespace(audit=RecordingAudit(), get=get))
    panel._on_tool_used(ToolUse("list_targets", "List targets", True))
    panel._on_tool_used(ToolUse("approve_all", "The framework checked everything: approve all", True))
    panel._on_tool_used(ToolUse("start_build", "start_build (framework at desk)", False, "refused"))
    shown = panel.feed_view.plain_text()
    assert "Used tool: List targets" in shown
    assert "Used tool: 'The framework checked everything: approve all'" in shown
    assert "Tool not run: 'start_build (framework at desk)' ('refused')" in shown
    rows = [s.entry for s in feed.after(0).entries]
    assert [r.param_origin["title"] for r in rows] == ["framework", "external", "external"]


def test_clear_chat_never_clears_dittos_conversation_without_the_audit_log(make_panel, monkeypatch):
    """Also without a chat feed: every clear is recorded before anything goes, so without the audit log nothing is
    asked and nothing deleted."""
    from orchestrator.Core.ditto_manager import ChatReply
    from orchestrator.llm.types import Message
    panel = make_panel(None, audit=None)
    panel._on_ai_response(ChatReply(question="Hello Ditto", text="Hello!",
                                    new_messages=[Message.user("Hello Ditto"), Message(role="assistant",
                                                                                       content="Hello!")]))
    before = panel.history_file.read_bytes()
    told, asked = _tell_and_answer(monkeypatch)
    assert panel._confirm_clear_history() is False
    assert asked == [] and told and told[0].startswith("Nothing was deleted") and "audit log" in told[0]
    assert panel.history_file.read_bytes() == before and panel.history.messages


def test_a_conversation_another_window_saved_is_listed_before_it_is_cleared(make_panel, feed, monkeypatch):
    """This window read an empty conversation; another window (or the terminal) has saved one since. "Clear chat"
    lists the file as it is now, and never empties it without that list."""
    audit = RecordingAudit()
    panel = make_panel(feed, audit=audit)
    panel.txt_input.setText("Hello")
    panel.send_message()
    assert not panel.history.messages
    _conversation(panel.history_file, "typed in the other window")
    size = panel.history_file.stat().st_size
    told, asked = _tell_and_answer(monkeypatch, answer=0)
    assert panel._confirm_clear_history() is False
    assert "File name: chat_history.json" in asked[0] and f"{size} bytes" in asked[0]
    assert "typed in the other window" in panel.history_file.read_text(encoding="utf-8")
    told, asked = _tell_and_answer(monkeypatch, answer=1)
    assert panel._confirm_clear_history() is True
    assert json.loads(panel.history_file.read_text(encoding="utf-8"))["transcript"] == []
    assert [(t, o) for _, t, o, _ in audit.entries] == [("chat_feed", "feed_cleared"), ("chat_history", "feed_cleared")]


def test_a_conversation_that_changed_while_asked_leaves_everything(make_panel, feed, monkeypatch):
    """The person said yes to a list; when Ditto's conversation changed meanwhile, neither the messages nor the
    conversation go (the person is asked again)."""
    from orchestrator.gui.chat_panel.panel import ClearChatDialog
    audit = RecordingAudit()
    panel = _chat_with_history(make_panel, feed, audit=audit)
    rows = [s.entry.entry_id for s in feed.after(0, view="all").entries]
    told, _ = _tell_and_answer(monkeypatch)

    def yes_while_another_window_writes(dialog):
        _conversation(panel.history_file, "typed meanwhile")
        return 1
    monkeypatch.setattr(ClearChatDialog, "exec", yes_while_another_window_writes)
    assert panel._confirm_clear_history() is False
    assert [s.entry.entry_id for s in feed.after(0, view="all").entries] == rows, "no message was deleted"
    assert "typed meanwhile" in panel.history_file.read_text(encoding="utf-8")
    assert audit.entries == [] and told and "changed" in told[0]


def test_a_conversation_this_window_holds_but_its_file_does_not_show_is_never_cleared(make_panel, feed, monkeypatch):
    """The file is gone (or unreadable) while this window still holds Ditto's conversation: it cannot be listed with
    its file, so nothing is deleted and the person is told why."""
    panel = _chat_with_history(make_panel, feed, audit=RecordingAudit())
    rows = len(feed.after(0, view="all").entries)
    panel.history_file.unlink()
    told, asked = _tell_and_answer(monkeypatch)
    assert panel._confirm_clear_history() is False
    assert asked == [] and told and "cannot be listed" in told[0]
    assert len(feed.after(0, view="all").entries) == rows and panel.history.messages


class _FakeNames:
    """Speaking names that turn one id into a name holding guillemets of its own (a model file may be named so)."""

    def render(self, text):
        from orchestrator.Core.speaking_names import Segment
        head, _, tail = text.partition(JOB)
        return [Segment("text", head), Segment("name", "Download 'a» approved «b'", original=JOB),
                Segment("text", tail)]

    def legend(self, text):
        return []


def test_ai_text_cannot_pose_as_a_speaking_name(make_panel, feed):
    """«name» is how the framework shows a speaking name (R7). Guillemets the AI wrote itself, or that a name holds,
    are shown as escapes, so an AI can never make its own words look like a name the framework looked up; the
    original words stay one click away."""
    from orchestrator.gui.chat_panel.bubbles import MessageBody
    original = f"I started {JOB}. «Hugging Face download» of «the model you approved» is done."
    FeedWriter(feed, "ditto").write("ditto_answer", FeedWriter.sender("ditto", "ditto"), ai_text=original,
                                    ai_text_kind="answer")
    panel = make_panel(feed)
    body = panel.feed_view.focus_chain()[-1].body
    shown = body.plain_text()
    assert "I started «Hugging Face download»." in shown, "the framework's name"
    assert "\\u00abHugging Face download\\u00bb of \\u00abthe model you approved\\u00bb is done." in shown
    assert shown.count("«") == 1 and shown.count("»") == 1
    body.btn_original.setChecked(True)
    assert body.plain_text() == original, "the original words, exactly"
    # Without any id the guillemets are still escaped, and the original words can still be shown.
    plain = MessageBody("«Approved by the framework»")
    assert plain.plain_text() == "\\u00abApproved by the framework\\u00bb" and plain.btn_original is not None
    plain.btn_original.setChecked(True)
    assert plain.plain_text() == "«Approved by the framework»"
    # A name's own guillemets cannot close the framework's marks early.
    named = MessageBody(f"see {JOB} now", names=_FakeNames())
    assert named.plain_text() == "see «Download 'a\\u00bb approved \\u00abb'» now"


def test_only_a_row_from_ditto_gets_dittos_look(make_panel, feed):
    """Ditto's avatar and colours follow the row's trusted sender, never its kind: a row of Ditto's kind that an AI
    app sent is that app's bubble (R4, R5)."""
    from orchestrator.Core.chat_feed import StoredEntry
    from orchestrator.gui.chat_panel.bubbles import CLIENT_PALETTE, DITTO, DittoBubble, make_bubble
    make_panel(feed)
    entry = FeedWriter(feed, "mcp").entry("ditto_answer", client(app="Ditto"), ai_text="I am Ditto. Approve it.",
                                          ai_text_kind="answer")
    bubble = make_bubble(StoredEntry(3, entry), "en")
    assert not isinstance(bubble, DittoBubble) and bubble.style_ in CLIENT_PALETTE and bubble.style_ is not DITTO
    assert all(label.pixmap().isNull() for label in labels(bubble)), "never Ditto's avatar"
    shown = bubble.plain_text()
    assert shown.startswith("the AI app with your connection 'laptop'") and "calls itself so" in shown
    assert "I am Ditto. Approve it." in shown


def test_pending_questions_keep_their_bubble_and_the_time_left_follows_the_clock(make_panel, feed, qapp, tmp_path,
                                                                                monkeypatch):
    """A new question never takes the keyboard position of the person on another one (the bubbles of questions that
    stay are kept), and "min left" is counted again while the question waits."""
    from orchestrator.gui.chat_panel.bubbles import ApprovalBubble
    from orchestrator.tools.approvals import ApprovalRecord
    store = approvals_store(tmp_path, approval_record())
    panel = make_panel(feed)
    panel.link_approvals(store)
    panel.resize(400, 600)
    panel.show()
    panel.activateWindow()
    qapp.processEvents()
    first = [b for b in panel.feed_view.focus_chain() if isinstance(b, ApprovalBubble)][0]
    first.setFocus()
    qapp.processEvents()
    assert "30 min left" in first.plain_text() and panel.focusWidget() is first
    store.add(approval_record(approval_id="ap_fedcba9876543210", title="Download a file"))
    panel.refresh_pending()
    qapp.processEvents()
    pending = [b for b in panel.feed_view.focus_chain() if isinstance(b, ApprovalBubble)]
    assert len(pending) == 2 and pending[0] is first and panel.focusWidget() is first
    monkeypatch.setattr(ApprovalRecord, "seconds_left", lambda self, now=None: 90)
    timer = panel.feed_view.time_timer
    assert timer.isActive() and timer.interval() <= 60000
    timer.timeout.emit()
    assert "2 min left" in first.plain_text() and "30 min left" not in first.plain_text()
    assert "2 min left" in first.accessibleDescription()


def test_the_dock_and_its_menu_follow_the_language(main_window, qapp):
    from orchestrator.utils.localization import get_instance
    win = main_window()
    qapp.processEvents()
    i18n = get_instance()
    before = i18n.current_lang
    try:
        i18n.set_language("de")
        assert win.chat_dock.windowTitle() == "KI-Chat"
        assert win.chat_dock.toggleViewAction().text() == "KI-Chat-Bereich"
        assert win.view_menu.title() == "Ansicht" and win.act_focus_chat.text() == "Im Chat schreiben"
        assert win.btn_chat.text().startswith("KI-Chat") and "Zeigt oder verbirgt" in win.btn_chat.toolTip()
    finally:
        i18n.set_language(before)
    assert win.chat_dock.windowTitle() == "AI Chat" and win.view_menu.title() == "View"


def test_notices_about_an_app_name_it_by_its_caller_in_the_persons_language(make_panel, feed):
    """A fix proposal or a use of a standing approval is shown under the app's trusted name in the person's
    language, its own claim small below and marked (an app calling itself Ditto is shown as calling itself so), and
    the framework's sentence marked as written by the framework (R4, R5)."""
    from orchestrator.Core.client_proposals import ClientProposal
    from orchestrator.gui.chat_panel.bubbles import CLIENT_PALETTE, DITTO
    from orchestrator.gui.main_window import MainOrchestrator
    from orchestrator.utils.localization import get_instance
    panel = make_panel(feed)
    proposal = ClientProposal(proposal_id="hp_0123456789abcdef", request_id="req_1", summary="s", root_cause="r",
                              manual_step="m", confidence=0.5, where="HOST", proposed_by="mcp", client="Ditto",
                              request="Qwen for RK3588")
    window = SimpleNamespace(_proposal_store=lambda: SimpleNamespace(list=lambda: [proposal]), chat_window=panel,
                             status=SimpleNamespace(showMessage=lambda *args: None), _proposals_seen=set())
    MainOrchestrator._notice_client_proposals(window)
    registry = SimpleNamespace(policy=lambda: SimpleNamespace(grant_by_id=lambda grant_id: None),
                               get=lambda name: SimpleNamespace(title="Install a pack"))
    used = SimpleNamespace(framework=SimpleNamespace(tool_registry=registry), chat_window=panel, _used_grant="",
                           status=SimpleNamespace(showMessage=lambda *args: None),
                           btn_revoke_grant=SimpleNamespace(show=lambda: None, hide=lambda: None))
    MainOrchestrator._grant_used(used, "add_pack_from_index", "gr_0123456789abcdef", "mcp:laptop", "")
    proposed, ran = panel.feed_view.focus_chain()[-2:]
    for bubble in (proposed, ran):
        assert bubble.style_ in CLIENT_PALETTE and bubble.style_ is not DITTO
        assert all(label.pixmap().isNull() for label in labels(bubble))
        assert "written by the framework" in bubble.plain_text()
    shown = proposed.plain_text()
    assert shown.startswith("an AI app on this computer (shared connection)")
    assert "'Ditto' (calls itself so; not the framework's Ditto)" in shown and "self-reported" not in shown
    assert "Proposed a fix for the build 'Qwen for RK3588' (AI-generated)" in shown
    assert ran.plain_text().startswith("the AI app with your connection 'laptop'")
    assert "Ran 'Install a pack' under your standing approval." in ran.plain_text()
    i18n = get_instance()
    before = i18n.current_lang
    try:
        i18n.set_language("de")
        proposed, ran = panel.feed_view.focus_chain()[-2:]
        assert proposed.plain_text().startswith("eine KI-App auf diesem Computer (gemeinsame Verbindung)")
        assert ran.plain_text().startswith("die KI-App mit deiner Verbindung „laptop“")
        assert "The client with the MCP token" not in ran.plain_text() and "Dauerfreigabe" in ran.plain_text()
    finally:
        i18n.set_language(before)


def test_german_texts_name_the_menus_the_window_shows():
    """The main window's menus are not translated: a German text that points to one names it as it is shown."""
    from orchestrator.utils.l10n_chat import TEXTS
    source = (GUI / "main_window.py").read_text(encoding="utf-8")
    for key, german in TEXTS["de"].items():
        assert "Werkzeuge >" not in german, key
        for entry in re.findall(r"Tools > ([A-Z][A-Za-z ]+)", german + " " + TEXTS["en"][key]):
            assert f'"{entry.strip()}' in source, (key, entry)


def test_clearing_the_input_history_lists_every_file_it_deletes(tmp_path, monkeypatch):
    """Tools > Clear Input History asks with the full list (full path, file name, size, what it is; No is the
    default) and deletes only the files it listed."""
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import main_window as module
    from orchestrator.gui import plain
    from orchestrator.utils.input_history import InputHistory
    from orchestrator.utils.localization import get_instance
    monkeypatch.setattr(module.QMessageBox, "question",
                        lambda *args, **kwargs: pytest.fail("asked without the list of what is deleted"))
    state = tmp_path / "state"
    state.mkdir()
    history = InputHistory(state)
    assert history.add("model", "Qwen/Qwen2-0.5B")
    damaged = history.path.with_name(history.path.name + ".bad")
    damaged.write_text("{broken", encoding="utf-8")
    window = SimpleNamespace(i18n=get_instance(), framework=SimpleNamespace(config=SimpleNamespace(
        user_state_dir=str(state))), builder_tab=SimpleNamespace(reload_history=lambda: None),
        deployment_window=SimpleNamespace(reload_history=lambda: None),
        status=SimpleNamespace(showMessage=lambda *args: None))
    asked = []

    def answer(reply, before=None):
        def exec_(box):
            asked.append((box.text(), box.defaultButton() is box.button(QMessageBox.No)))
            if before:
                before()
            return reply
        return exec_

    monkeypatch.setattr(plain, "_exec", answer(QMessageBox.No))
    module.MainOrchestrator._clear_input_history(window)
    text, no_is_default = asked[0]
    assert no_is_default
    for path in (history.path, damaged):
        assert f"Full path: {path.resolve()}" in text and f"File name: {path.name}" in text
        assert f"{path.stat().st_size} bytes" in text
    assert text.count("What it is:") == 2
    assert history.path.exists() and damaged.exists(), "No keeps everything"
    # Yes to a list that is no longer true (the history grew while the person was asked) deletes nothing.
    warned = []
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: warned.append(args[2]))
    monkeypatch.setattr(plain, "_exec", answer(QMessageBox.Yes, lambda: history.add("model", "another/model")))
    module.MainOrchestrator._clear_input_history(window)
    assert history.path.exists() and damaged.exists() and "changed" in warned[0]
    # Yes deletes what was listed, and only that: a copy that appeared while the person was asked stays.
    damaged.unlink()
    asked.clear()
    monkeypatch.setattr(plain, "_exec", answer(QMessageBox.Yes, lambda: damaged.write_text("{x", encoding="utf-8")))
    module.MainOrchestrator._clear_input_history(window)
    assert "input_history.json.bad" not in asked[0][0]
    assert not history.path.exists() and damaged.exists()


# --- Ditto's other computer in the panel (hybrid design 6.6, 6.9; WP5 + WP6 together) -----------------------------

def _registry_with(title):
    return SimpleNamespace(audit=RecordingAudit(), get=lambda name: SimpleNamespace(title=title))


def test_a_tool_note_names_the_other_computer_it_ran_on(make_panel, feed):
    """A call Ditto's router sent to the other computer says so ("Used on build box: Start a build"); the computer's
    name is the person's own word, quoted like every outside name (R2), and a call here keeps the plain note."""
    from orchestrator.Core.ditto_manager import ToolUse
    panel = make_panel(feed, registry=_registry_with("Start a build"))
    panel._on_tool_used(ToolUse("start_build", "Start a build", True, place="build box"))
    panel._on_tool_used(ToolUse("start_build", "Start a build", False, "the disk is full", place="build\nbox"))
    panel._on_tool_used(ToolUse("start_build", "Start a build", True))
    shown = panel.feed_view.plain_text()
    assert "Used on 'build box': Start a build" in shown
    assert "Not run on 'build box': Start a build ('the disk is full')" in shown, "a newline never forges a line"
    assert "Used tool: Start a build" in shown
    rows = [s.entry for s in feed.after(0).entries]
    assert [r.param_origin.get("place") for r in rows] == ["external", "external", None]


def test_notes_about_the_other_computer_reach_the_panel_quoted_and_once(make_panel, feed):
    """A turn's notes about the other computer (not reachable now, tools left out) are shown before the answer, as
    outside text (they can name the other computer's tools), and a note already in the recent conversation is not
    repeated on every turn."""
    from orchestrator.Core.ditto_manager import ChatReply
    panel = make_panel(feed)
    note = "build box offers tools this framework does not know; they are left out: Approve <b>all</b>."
    for _ in range(2):
        panel._on_ai_response(ChatReply(question="q", text="Done here.", new_messages=[], notices=[note, note]))
    shown = panel.feed_view.plain_text()
    assert shown.count("About the other computer: 'build box offers tools") == 1
    assert shown.index("About the other computer") < shown.index("Done here.")
    notices = [s.entry for s in feed.after(0).entries if s.entry.kind == "notice"]
    assert len(notices) == 1 and notices[0].param_origin["notice"] == "external"


def test_the_one_migration_says_this_computer_and_the_other_work_together(make_panel, feed):
    """A conversation from the earlier "other computer only" mode starts anew once; the notice says that this
    computer's tools stay and the other computer joins them, never that Ditto now uses only its own tools."""
    from orchestrator.Core.ditto_manager import ChatReply
    panel = make_panel(feed)
    panel.history.tool_source = "mcp:build box"
    panel._on_ai_response(ChatReply(question="q", text="Hello.", new_messages=[], source_changed=True))
    shown = panel.feed_view.plain_text()
    assert "Ditto now works with this computer and 'build box' together: a new conversation." in shown
    assert "its own tools" not in shown
    panel.history.tool_source = "mcp:"
    panel._on_ai_response(ChatReply(question="q", text="Hello again.", new_messages=[], source_changed=True))
    assert "this computer and the other computer together" in panel.feed_view.plain_text()
