"""
Wave 5 (GUI parity, chain tools-mcp): every `llmcf tools` and `llmcf mcp` action of this area by mouse. Offscreen:
each entry is reachable from the menu bar or its window, each handler calls the CLI's core function with the right
arguments, every question defaults to No, the integrity check, the archive, the summary and the HTTP server's output
run off the GUI thread, a token is never shown except once, and every text exists in English and German.

No network: the HTTP server is a stand-in child process where it would listen.
"""

import json
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from conftest import REPO_ROOT

from orchestrator.tools.audit import AuditLog
from orchestrator.tools.grants import Grant
from orchestrator.tools.mcp_tokens import TokenStore, read_only_policy
from orchestrator.tools.policy import CallerPolicy, Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolRegistry, ToolSpec

pytest.importorskip("PySide6")   # after pydantic's models are loaded (shiboken's import hook inspects them)

pytestmark = pytest.mark.usefixtures("qapp")


class In(BaseModel):
    text: str = "x"


class Out(BaseModel):
    echoed: str = ""


def grant(gid: str, tool: str = "write") -> Grant:
    return Grant(id="g_" + gid * 16, tool=tool, scope={"tool": tool}, summary="every call", rule_version=1,
                 created="2026-10-11T00:00:00+00:00", created_via="gui")


@pytest.fixture
def registry(tmp_path):
    tokens, audit = TokenStore(tmp_path), AuditLog(tmp_path / "audit.db")
    reg = ToolRegistry(PolicyStore(tmp_path, audit=audit), audit, tokens=tokens)
    reg.register(ToolSpec("echo", "Echo", "Echo text", ToolTier.READ, In, Out, lambda i: Out(echoed=i.text)))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out, lambda i: Out(echoed="w")))
    return reg


def save(registry, **fields):
    fields.setdefault("reviewed", ["echo", "write"])
    registry.policy_store.replace(ToolPolicy(configured=True, **fields))


class Boxes:
    """Answers plain message boxes (plain._exec) and keeps what each one asked, with its default button. `before`
    runs when a question is asked (e.g. a change made elsewhere meanwhile)."""

    def __init__(self, monkeypatch, answer="no", before=None):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import plain
        self.asked, self.answer, self.before = [], answer, before
        buttons = {"yes": QMessageBox.Yes, "no": QMessageBox.No}

        def fake(box):
            default = box.defaultButton()
            question = box.button(QMessageBox.Yes) is not None
            self.asked.append(SimpleNamespace(text=box.text(), title=box.windowTitle(), question=question,
                                              default_is_no=default is not None and default is box.button(
                                                  QMessageBox.No)))
            if not question:
                return int(QMessageBox.Ok)
            if self.before is not None:
                self.before()
            return int(buttons[self.answer])

        monkeypatch.setattr(plain, "_exec", fake)

    def questions(self):
        return [a for a in self.asked if a.question]

    def texts(self):
        return " ".join(" ".join(a.text.split()) for a in self.asked)


def wait_until(qapp, condition, timeout=15.0):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert condition(), "the condition was not reached in time"


def close(qapp, *dialogs):
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


# --- the menu bar ----------------------------------------------------------------------------------------------------

def test_every_entry_is_reachable_from_the_menu_bar_and_named(qapp, tmp_path, monkeypatch, memory_keyring):
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QAction

    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window
    from orchestrator.gui.approvals_window import ApprovalsDialog
    from orchestrator.gui.dialogs import AIFoldersDialog, ToolPermissionsDialog
    from orchestrator.gui.menus import tools_mcp

    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    win = main_window.MainOrchestrator(REPO_ROOT)
    qapp.processEvents()
    opened = []
    try:
        menus = {a.text(): [x.text() for x in a.menu().actions()] for a in win.menuBar().actions() if a.menu()}
        assert menus["Security"][-2:] == ["Audit Log...", "AI App Connections (MCP)..."]
        tools = menus["Tools"]
        assert tools[tools.index("Folders the AI May Use...") + 1] == "Deep Ingest Notice..."
        in_window = set(tools_mcp.WINDOW_OBJECTS)
        for path, name in tools_mcp.CLI_PATHS.items():
            target = tools_mcp.WINDOW_OBJECTS.get(name, name)
            assert isinstance(win.findChild(QAction, target), QAction), (path, target)
        # Each window holds the objects the map names for it.
        registry = win.framework.tool_registry
        win.findChild(QAction, tools_mcp.ACT_AUDIT).trigger()
        win.findChild(QAction, tools_mcp.ACT_CONNECTIONS).trigger()
        opened += [win.audit_window, win.mcp_window]
        windows = {tools_mcp.ACT_AUDIT: win.audit_window, tools_mcp.ACT_CONNECTIONS: win.mcp_window,
                   tools_mcp.ACT_PERMISSIONS: ToolPermissionsDialog(registry),
                   tools_mcp.ACT_FOLDERS: AIFoldersDialog(win.framework, registry),
                   tools_mcp.ACT_APPROVALS: ApprovalsDialog(registry)}
        opened += list(windows.values())[2:]
        for name in in_window:
            window = windows[tools_mcp.WINDOW_OBJECTS[name]]
            assert window.findChild(QObject, name) is not None, name
        assert win.audit_window.isVisible() and win.mcp_window.isVisible()
        assert set(tools_mcp.MACHINE_ONLY) == {"mcp serve"}

        # Closing while the HTTP server still serves asks first; "No" (the default) keeps the window open.
        stopped = []
        win.mcp_http_server = SimpleNamespace(running=True, url="http://127.0.0.1:8765/mcp",
                                              stop_and_wait=lambda: stopped.append(1))
        boxes = Boxes(monkeypatch, answer="no")
        assert win.close() is False and stopped == []
        assert boxes.questions()[0].default_is_no and "http://127.0.0.1:8765/mcp" in boxes.questions()[0].text
        boxes.answer = "yes"
        win.mcp_http_server.running = True
        assert win.close() is True and stopped == [1]
    finally:
        close(qapp, *opened)
        win.mcp_http_server = None
        win.close()


# --- Tool Permissions: effective decision, callers, resets, tokens -----------------------------------------------

class TestToolPermissions:

    @pytest.fixture
    def dialog_of(self, qapp, registry, monkeypatch):
        from orchestrator.gui import dialogs
        made = []
        monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *a, **k: None)

        def make(**kwargs):
            dialog = dialogs.ToolPermissionsDialog(registry, **kwargs)
            dialog.shown = []
            monkeypatch.setattr(dialog, "_show_token", lambda name, token: dialog.shown.append((name, token)))
            made.append(dialog)
            return dialog
        yield make
        close(qapp, *made)

    def test_each_tool_shows_what_is_decided_now_and_why(self, registry, dialog_of):
        save(registry, overrides={"write": Decision.DENY})
        dialog = dialog_of(lang="en")
        rows = {row["tool"]: row for row in registry.permissions("")}
        assert dialog.effective("write") == rows["write"]["decision"] == "deny"
        assert dialog.effective("echo") == "allow"
        item = dialog.table.item([s.name for s in registry.list()].index("write"), 6)
        assert item.text() == "Deny" and "your setting for this tool" in item.toolTip()
        assert dialog.table.horizontalHeaderItem(6).text() == "Effective now"
        german = dialog_of(lang="de")
        assert german.table.item([s.name for s in registry.list()].index("write"), 6).text() == "Verweigern"

    def test_callers_with_their_own_permissions_are_named(self, registry, dialog_of):
        save(registry, callers={"mcp": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.DENY})})
        dialog = dialog_of(lang="en")
        assert not dialog.callers_note.isHidden() and "Local MCP clients (stdio)" in dialog.callers_note.text()

    def test_a_callers_own_section_is_shown_and_saved(self, registry, dialog_of):
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}, framework_only=["write"])
        dialog = dialog_of(lang="en")
        assert dialog.set_caller("mcp")
        assert "uses the shared permissions" in dialog.caller_note.text()
        assert dialog.tier_combos[ToolTier.LOCAL_WRITE].currentData() == "allow", "starts from the shared ones"
        assert dialog.effective("write") == "allow"
        combo = dialog.tier_combos[ToolTier.LOCAL_WRITE]
        combo.setCurrentIndex(combo.findData("deny"))
        dialog.save()
        policy = registry.policy()
        assert policy.callers["mcp"].tiers[ToolTier.LOCAL_WRITE] == Decision.DENY
        assert policy.callers["mcp"].framework_only == ["write"]
        assert policy.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW, "the shared permissions are unchanged"
        again = dialog_of(lang="en", caller="mcp")
        assert "has permissions of its own" in again.caller_note.text() and again.effective("write") == "deny"
        assert again.btn_reset_caller.isEnabled() and again.btn_reset.isHidden()

    def test_switching_with_unsaved_changes_asks_and_no_keeps_them(self, registry, dialog_of, monkeypatch):
        save(registry)
        dialog = dialog_of(lang="en")
        combo = dialog.tier_combos[ToolTier.LOCAL_WRITE]
        combo.setCurrentIndex(combo.findData("deny"))
        boxes = Boxes(monkeypatch, answer="no")
        dialog.caller_combo.setCurrentIndex(dialog.caller_combo.findData("ditto"))
        assert boxes.questions()[0].default_is_no
        assert dialog.caller is None and dialog.caller_combo.currentData() is None
        assert combo.currentData() == "deny", "the unsaved change stays"

    def test_reset_to_the_recommended_defaults_lists_what_changes_and_asks(self, registry, dialog_of, monkeypatch):
        save(registry, callers={"ditto": CallerPolicy()}, grants={"mcp": [grant("1")]})
        dialog = dialog_of(lang="en")
        boxes = Boxes(monkeypatch, answer="no")
        assert dialog.reset_shared() is False
        question = boxes.questions()[0]
        assert question.default_is_no
        assert "ditto: own permissions removed" in question.text and "standing approval g_1111" in question.text
        assert registry.policy().callers, "No changes nothing"
        boxes.answer = "yes"
        assert dialog.reset_shared()
        policy = registry.policy()
        assert not policy.configured and policy.callers == {} and policy.grants == {}
        assert policy.tiers == ToolPolicy().tiers
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"]) == ("gui", "tool_policy")
        assert dialog.grant_table.rowCount() == 0, "the window shows the new state"
        boxes.asked.clear()
        assert dialog.reset_shared() is False and not boxes.questions(), "nothing to reset: nothing asked"

    def test_a_caller_goes_back_to_the_shared_permissions(self, registry, dialog_of, monkeypatch):
        save(registry, callers={"mcp": CallerPolicy(), "ditto": CallerPolicy()},
             grants={"mcp": [grant("1")], "ditto": [grant("2")]})
        dialog = dialog_of(lang="en", caller="mcp")
        Boxes(monkeypatch, answer="yes")
        assert dialog.reset_caller("mcp")
        policy = registry.policy()
        assert set(policy.callers) == {"ditto"} and set(policy.grants) == {"ditto"}
        assert "uses the shared permissions" in dialog.caller_note.text()
        assert not dialog.btn_reset_caller.isEnabled()

    def test_a_reset_is_refused_when_the_permissions_changed_meanwhile(self, registry, dialog_of, monkeypatch,
                                                                         tmp_path):
        save(registry, callers={"mcp": CallerPolicy()})
        dialog = dialog_of(lang="en")

        def elsewhere():   # another window adds a section while the question is open
            PolicyStore(tmp_path).update(lambda p: p.model_copy(update={"callers": {**p.callers,
                                                                                     "ditto": CallerPolicy()}}))
        boxes = Boxes(monkeypatch, answer="yes", before=elsewhere)
        assert dialog.reset_shared() is False
        assert "changed elsewhere" in boxes.texts()
        assert set(registry.policy().callers) == {"mcp", "ditto"}, "nothing was reset"

    def test_a_token_goes_back_to_read_only(self, registry, dialog_of, monkeypatch):
        registry.tokens.add("box")
        registry.tokens.set_policy("box", ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
                                                     reviewed=["echo", "write"]))
        assert registry.decide(registry.get("write"), caller="mcp:box") == Decision.ALLOW
        dialog = dialog_of(lang="en")
        dialog.token_table.selectRow(0)
        boxes = Boxes(monkeypatch, answer="no")
        dialog.token_buttons["reset"].click()
        assert boxes.questions()[0].default_is_no and "local_write: allow" in boxes.questions()[0].text
        assert registry.tokens.get("box").policy.configured
        boxes.answer = "yes"
        assert dialog.reset_token("box")
        assert registry.tokens.get("box").policy == read_only_policy()
        assert registry.decide(registry.get("write"), caller="mcp:box") == Decision.DENY
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"]) == ("gui", "mcp_token.permissions")
        boxes.asked.clear()
        assert dialog.reset_token("box") is False and not boxes.questions(), "a new token already"
        # The token's own window resets it as well, and shows the new permissions.
        registry.tokens.set_policy("box", ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        own = dialog_of(lang="en", token="box")
        assert own.btn_reset.objectName() == "btn_permissions_reset_token" and not hasattr(own, "token_table")
        own.btn_reset.click()
        assert registry.tokens.get("box").policy == read_only_policy()
        assert own.tier_combos[ToolTier.LOCAL_WRITE].currentData() == "deny"

    def test_a_new_token_can_start_as_a_copy_of_another_tokens_permissions(self, registry, dialog_of):
        registry.tokens.add("box")
        registry.tokens.set_policy("box", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        dialog = dialog_of(lang="en")
        choices = dict(dialog.start_choices())
        assert "box" in choices["mcp:box"] and set(choices) >= {"read", "mcp", "ditto", "link"}
        assert dialog.add_token("copy", "mcp:box")
        assert registry.tokens.get("copy").policy.overrides == {"write": Decision.ALLOW}
        name, token = dialog.shown[0]
        assert name == "copy" and registry.tokens.find(token).name == "copy"
        entry = registry.audit.entries(1)[0]
        assert entry["tool"] == "mcp_token.add" and token not in json.dumps(entry)
        texts = [dialog.token_table.item(r, c).text() for r in range(dialog.token_table.rowCount())
                 for c in range(dialog.token_table.columnCount())]
        assert all(token not in text for text in texts), "never listed again"

    def test_revoke_removes_the_tokens_standing_approvals_too(self, registry, dialog_of, monkeypatch):
        registry.tokens.add("box")
        save(registry, grants={"mcp:box": [grant("1")], "mcp": [grant("2")]})
        dialog = dialog_of(lang="en")
        assert dialog.grant_table.rowCount() == 2
        dialog.token_table.selectRow(0)
        boxes = Boxes(monkeypatch, answer="no")
        dialog.token_buttons["revoke"].click()
        assert boxes.questions()[0].default_is_no and "standing approvals (1)" in boxes.questions()[0].text
        assert registry.tokens.get("box") is not None, "No keeps the token"
        boxes.answer = "yes"
        dialog.token_buttons["revoke"].click()
        assert registry.tokens.get("box") is None and set(registry.policy().grants) == {"mcp"}
        assert dialog.grant_table.rowCount() == 1, "the list of standing approvals follows"
        assert "1 standing approvals are deleted" in boxes.texts()
        dialog.save()   # the window carries on from the new state: its save is not refused as stale
        assert registry.audit.entries(1)[0]["tool"] == "tool_policy"


# --- Folders the AI May Use: the kind -------------------------------------------------------------------------------

def test_adding_a_folder_asks_what_it_holds(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    from orchestrator.gui.dialogs import AIFoldersDialog
    from orchestrator.tools.local_files import FolderStore
    from test_local_files import cli_framework
    fw = cli_framework(tmp_path)
    (tmp_path / "mine").mkdir()
    offered = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "mine"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("mine", True))

    def choose(parent, title, label, items, current, editable):
        offered.append(list(items))
        return items[1], True
    monkeypatch.setattr(QInputDialog, "getItem", choose)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    dialog = AIFoldersDialog(fw, fw.tool_registry)
    try:
        dialog.add_button.click()
        assert offered == [["Model or dataset files (any)", "Model files (model)", "Dataset files (dataset)"]]
        assert "Kind: model" in asked[0]
        assert [(e["label"], e["kind"]) for e in FolderStore(tmp_path / "state").load()] == [("mine", "model")]
        assert fw.tool_registry.audit.entries(1)[0]["actor"] == "gui"
    finally:
        close(qapp, dialog)


# --- Tools > Deep Ingest Notice... ----------------------------------------------------------------------------------

def test_the_deep_ingest_notice_has_its_own_entry(tmp_path, monkeypatch):
    from orchestrator.gui.menus.tools_mcp import open_crawl_notice
    from orchestrator.utils.crawl_notice import CrawlNotice
    audit = AuditLog(tmp_path / "audit.db")
    window = SimpleNamespace(framework=SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path),
                                                       tool_registry=SimpleNamespace(audit=audit)))
    boxes = Boxes(monkeypatch, answer="no")
    assert open_crawl_notice(window, lang="en") == "declined"
    assert boxes.questions()[0].default_is_no and "I have read the notice" in boxes.questions()[0].text
    assert CrawlNotice(tmp_path).needed() and audit.count() == 0
    boxes.answer = "yes"
    assert open_crawl_notice(window, lang="en") == "acknowledged"
    assert CrawlNotice(tmp_path).acknowledged_via() == "gui"
    entry = audit.entries(1)[0]
    assert (entry["actor"], entry["tool"]) == ("gui", "crawl_notice_acknowledged")
    boxes.asked.clear()
    assert open_crawl_notice(window, lang="de") == "already"
    assert not boxes.questions() and "Bereits gelesen (bestätigt in der GUI)" in boxes.texts()


# --- Security > Audit Log... -------------------------------------------------------------------------------------------

class TestAuditLogWindow:

    @pytest.fixture
    def window(self, qapp, registry):
        from orchestrator.gui.audit_window import AuditLogWindow
        for i in range(7):
            registry.audit.record("mcp:box", "write", "local_write", "ask", "ok", arguments={"n": i}, approved=1)
        made = []

        def make(lang="en"):
            dialog = AuditLogWindow(registry, lang=lang)
            made.append(dialog)
            return dialog
        yield make
        for dialog in made:
            if dialog.running:
                wait_until(qapp, lambda d=dialog: d.running is None)
        close(qapp, *made)

    def test_entries_page_by_page_with_details(self, window):
        dialog = window()
        assert dialog.table.rowCount() == 7 and dialog.btn_older.isEnabled() is False
        dialog.spin_limit.setValue(3)
        assert dialog.table.rowCount() == 3 and dialog.page.text() == "Entries 1 to 3 of 7, newest first."
        dialog.btn_older.click()
        assert dialog.offset == 3 and dialog.table.item(0, 2).text() == "Write (write)"
        dialog.btn_older.click()
        assert dialog.table.rowCount() == 1 and not dialog.btn_older.isEnabled()
        dialog.btn_newer.click()
        assert dialog.offset == 3
        assert dialog.table.columnCount() == 8
        dialog.chk_details.setChecked(True)
        assert dialog.table.columnCount() == 13 and dialog.table.horizontalHeaderItem(8).text() == "Repairs"
        assert window(lang="de").table.horizontalHeaderItem(0).text() == "Zeit"

    def test_the_integrity_check_runs_off_the_gui_thread_and_speaks_plainly(self, qapp, window):
        from orchestrator.gui.audit_window import check_words
        dialog = window()
        assert dialog.check()
        wait_until(qapp, lambda: dialog.running is None)
        assert dialog.threads == [False], "the whole chain is read in a worker thread"
        assert dialog.check_result.text() == check_words(dialog.last_check, "en")
        assert "The audit log is intact: 7 entries" in dialog.check_result.text()
        german = window(lang="de")
        german.check()
        wait_until(qapp, lambda: german.running is None)
        assert "Das Prüfprotokoll ist unversehrt: 7 Einträge" in german.check_result.text()

    @pytest.mark.parametrize("problem, entry, words", [
        ("modified", 4, "entry 4 no longer fits the chain"), ("modified", None, "The audit log was changed."),
        ("removed", 3, "from entry 3 on"), ("key_mismatch", None, "another key"),
        ("key_mismatch", 2, "from entry 2 on"), ("anchor_missing", None, "anchor file of the audit log is missing"),
        ("anchor_missing", 5, "when entry 5 was written")])
    def test_every_check_result_has_plain_words(self, problem, entry, words):
        from orchestrator.gui.audit_window import check_words
        from orchestrator.tools.audit import AuditCheck
        result = AuditCheck(False, problem, entry)
        assert words in check_words(result, "en")
        assert check_words(result, "de") != check_words(result, "en")

    def test_archive_asks_and_moves_the_log_aside_off_the_gui_thread(self, qapp, window, registry, monkeypatch):
        dialog = window()
        boxes = Boxes(monkeypatch, answer="no")
        assert dialog.archive() is False
        assert boxes.questions()[0].default_is_no and str(registry.audit.path) in boxes.questions()[0].text
        assert registry.audit.count() == 7
        boxes.answer = "yes"
        assert dialog.archive()
        wait_until(qapp, lambda: dialog.running is None)
        assert dialog.threads == [False]
        archived = [p for p in registry.audit.path.parent.glob("audit.db.*") if not p.name.endswith(".anchor")]
        assert archived and registry.audit.count() == 0 and dialog.table.rowCount() == 0
        assert "Audit log moved to" in boxes.texts()

    def test_the_summary_per_caller_counts_off_the_gui_thread(self, qapp, window, registry):
        registry.audit.record("ditto", "write", "local_write", "deny", "denied", arguments={})
        dialog = window()
        dialog.tabs.setCurrentIndex(1)   # the tab counts when it is first shown
        wait_until(qapp, lambda: dialog.summarized)
        assert dialog.threads == [False]
        rows = {dialog.summary_table.item(r, 0).text(): dialog.summary_table.item(r, 1).text()
                for r in range(dialog.summary_table.rowCount())}
        assert rows["Ditto (ditto)"] == "0" and any(name.endswith("(mcp:box)") and calls == "7"
                                                    for name, calls in rows.items())


# --- Security > AI App Connections (MCP)... -----------------------------------------------------------------------------

SERVER_STAND_IN = (
    "import sys, time\n"
    "print('MCP server on http://127.0.0.1:%s/mcp - each client sends its own named token' % sys.argv[1], "
    "file=sys.stderr, flush=True)\n"
    "time.sleep(120)\n")


class TestMcpConnections:

    @pytest.fixture
    def window_of(self, qapp, registry, tmp_path, monkeypatch):
        from orchestrator.gui import dialogs
        from orchestrator.gui.mcp_window import McpConnectionsWindow, McpHttpServer
        monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *a, **k: None)
        framework = SimpleNamespace(tool_registry=registry, config=SimpleNamespace(user_state_dir=tmp_path))
        made, servers = [], []

        def make(command_for=None, lang="en"):
            server = McpHttpServer(framework, app_root=REPO_ROOT, command_for=command_for)
            dialog = McpConnectionsWindow(framework, server, lang=lang, app_root=REPO_ROOT)
            made.append(dialog)
            servers.append(server)
            return dialog
        yield make
        for server in servers:
            server.stop_and_wait(5)
        close(qapp, *made)

    def test_the_connection_entries_are_the_clis_with_copy_buttons(self, qapp, window_of):
        from PySide6.QtGui import QGuiApplication

        from orchestrator import mcp_server
        dialog = window_of()
        snippets = mcp_server.client_snippets(REPO_ROOT)
        assert {k: f.toPlainText() for k, f in dialog.snippet_fields.items()} == snippets
        assert all(field.isReadOnly() for field in dialog.snippet_fields.values())
        labels = " ".join(label.text() for label in dialog.findChildren(type(dialog.copied)))
        assert str(mcp_server.claude_desktop_config_path()) in labels
        dialog.findChild(type(dialog.btn_write), "btn_mcp_copy_vscode").click()
        assert QGuiApplication.clipboard().text() == snippets["vscode"]
        assert "lcf_mcp_" not in " ".join(snippets.values()), "no token in an entry"

    def test_write_to_claude_desktop_shows_the_change_asks_and_keeps_a_backup(self, window_of, tmp_path,
                                                                              monkeypatch):
        from orchestrator import mcp_server
        path = tmp_path / "Claude" / "claude_desktop_config.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        dialog = window_of()
        boxes = Boxes(monkeypatch, answer="no")
        assert dialog.write_desktop(path) is False
        question = boxes.questions()[0]
        assert question.default_is_no
        for words in (str(path), "The file exists.", f"adds the entry '{mcp_server.SERVER_NAME}'", "other",
                      str(path.with_name(path.name + ".bak"))):
            assert words in question.text, words
        assert json.loads(path.read_text()) == {"mcpServers": {"other": {"command": "x"}}}
        boxes.answer = "yes"
        assert dialog.write_desktop(path)
        assert set(json.loads(path.read_text())["mcpServers"]) == {"other", mcp_server.SERVER_NAME}
        assert json.loads(path.with_name(path.name + ".bak").read_text()) == {"mcpServers": {"other":
                                                                                             {"command": "x"}}}
        boxes.asked.clear()
        assert dialog.write_desktop(path) is False and not boxes.questions(), "there already: nothing asked"

    def test_the_server_refuses_what_the_cli_refuses(self, window_of, monkeypatch):
        dialog = window_of()
        boxes = Boxes(monkeypatch)
        assert dialog.start_server() is False
        assert "needs a named token" in boxes.texts() and dialog.server.process is None
        dialog.registry.tokens.add("laptop")
        dialog.host.setEditText("0.0.0.0")
        assert dialog.start_server() is False and "loopback" in boxes.texts()
        dialog.host.setEditText("127.0.0.1")
        dialog.container.setChecked(True)
        assert dialog.start_server() is False and dialog.server.process is None

    def test_start_runs_the_clis_entry_without_the_token_variable(self, window_of, monkeypatch):
        from orchestrator import mcp_server
        from orchestrator.gui import mcp_window
        dialog = window_of()
        dialog.registry.tokens.add("laptop")
        monkeypatch.setenv("LCF_MCP_TOKEN", "lcf_mcp_" + "x" * 43)
        started = []

        class Popen:
            def __init__(self, argv, **kwargs):
                started.append((argv, kwargs))
                self.pid, self.stdout = 4242, iter(())

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0
        monkeypatch.setattr(mcp_window.subprocess, "Popen", Popen)
        dialog.port.setValue(9123)
        assert dialog.start_server()
        argv, kwargs = started[0]
        expected = mcp_server.http_serve_command(REPO_ROOT, "127.0.0.1", 9123)
        assert argv == [expected["command"], *expected["args"]]
        assert argv[-6:] == ["--transport", "http", "--host", "127.0.0.1", "--port", "9123"]
        assert "LCF_MCP_TOKEN" not in kwargs["env"] and kwargs["env"]["PYTHONPATH"] == expected["env"]["PYTHONPATH"]
        assert kwargs["stdin"] is mcp_window.subprocess.DEVNULL and "shell" not in kwargs

    def test_the_server_runs_as_a_child_with_its_address_and_state_and_stops(self, qapp, window_of):
        def stand_in(host, port, container):
            return {"command": sys.executable, "args": ["-c", SERVER_STAND_IN, str(port)], "cwd": str(REPO_ROOT),
                    "env": {}}
        dialog = window_of(command_for=stand_in)
        dialog.registry.tokens.add("laptop")
        dialog.port.setValue(9124)
        on_gui = []
        dialog.server.changed.connect(lambda: on_gui.append(threading.current_thread() is threading.main_thread()))
        assert dialog.start_server()
        wait_until(qapp, lambda: dialog.server.state == "running")
        assert all(on_gui), "its output reaches the window on the GUI thread"
        assert "Serving at http://127.0.0.1:9124/mcp" in dialog.state.text()
        assert str(dialog.server.pid) in dialog.state.text()
        assert "each client sends its own named token" in dialog.output.toPlainText()
        assert not dialog.btn_start.isEnabled() and dialog.btn_stop.isEnabled() and not dialog.port.isEnabled()
        dialog.btn_stop.click()
        wait_until(qapp, lambda: dialog.server.state == "stopped")
        assert dialog.state.text().startswith("Stopped") and dialog.btn_start.isEnabled()
        assert not dialog.server.running

    def test_tokens_are_managed_here_too(self, window_of, monkeypatch):
        dialog = window_of()
        monkeypatch.setattr(dialog, "_show_token", lambda name, token: None)
        assert dialog.add_token("laptop") and dialog.token_table.item(0, 0).text() == "laptop"
        assert dialog.revoke_token("laptop") and dialog.registry.tokens.get("laptop") is None


# --- texts --------------------------------------------------------------------------------------------------------

def test_every_text_exists_in_english_and_german_with_the_same_placeholders():
    from orchestrator.utils.l10n_tools_mcp import TEXTS
    from orchestrator.utils.localization import ui_text
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key in TEXTS["en"]:
        fields = [set(re.findall(r"{(\w+)}", TEXTS[lang][key])) for lang in ("en", "de")]
        assert fields[0] == fields[1], key
    assert ui_text("tmcp.menu.audit", "de") == "Prüfprotokoll..."


def test_the_new_windows_are_plain_text_only():
    for name in ("audit_window.py", "mcp_window.py", "menus/tools_mcp.py"):
        source = (Path(REPO_ROOT) / "orchestrator" / "gui" / name).read_text(encoding="utf-8")
        for api in ("setHtml", "RichText", "QMessageBox.information(", "QMessageBox.warning(", "QMessageBox.question("):
            assert api not in source, (name, api)
