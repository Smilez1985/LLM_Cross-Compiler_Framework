#!/usr/bin/env python3
"""
Tool permissions, the audit log, the AI folders, the Deep Ingest notice and the MCP connections in the main window's
menus (W1' of 2026-10-16: every action of `llmcf tools` and `llmcf mcp` of this area also by mouse).

* Security > Audit Log... (act_tools_mcp_audit): orchestrator/gui/audit_window.py.
* Security > AI App Connections (MCP)... (act_tools_mcp_connections): orchestrator/gui/mcp_window.py, with the HTTP
  server as a child process of the GUI; the framework asks before it closes while that server still serves.
* Tools > Deep Ingest Notice... (act_tools_mcp_crawl_notice): the notice at any time; acknowledged on the person's yes.
* The existing Tools > Tool Permissions (Ditto / MCP)... and Folders the AI May Use... get object names
  (act_tools_permissions, act_tools_folders) for the paths they cover.
The windows load when an entry is first used (lazy loading, RG1).

CLI_PATHS names, for each `llmcf` command path of this area, the object that does it by mouse; WINDOW_OBJECTS names,
for an object that lives in a window, the menu action that opens that window. MACHINE_ONLY lists the paths of this
area that are for scripts or AI clients only, with the reason (the integrator lists them once).
"""

from typing import Dict, Optional

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction

from orchestrator.gui.menus import place
from orchestrator.utils.localization import current_language, ui_text

SECURITY_MENU = "Security"   # a title is only the fallback: the menus are found by their object names
TOOLS_MENU = "Tools"
PERMISSIONS_TEXT = "Tool Permissions (Ditto / MCP)..."
FOLDERS_TEXT = "Folders the AI May Use..."
APPROVALS_TEXT = "Pending Approvals..."

ACT_PERMISSIONS = "act_tools_permissions"
ACT_FOLDERS = "act_tools_folders"
ACT_APPROVALS = "act_tools_approvals"
ACT_AUDIT = "act_tools_mcp_audit"
ACT_CONNECTIONS = "act_tools_mcp_connections"
ACT_CRAWL = "act_tools_mcp_crawl_notice"

CLI_PATHS: Dict[str, str] = {
    "tools": ACT_PERMISSIONS,
    "tools list": ACT_PERMISSIONS,                          # per tool: decision ("Effective now") and why, callers
    "tools list --caller": "combo_permissions_caller",
    "tools permissions": ACT_PERMISSIONS,
    "tools permissions --caller": "combo_permissions_caller",
    "tools permissions --token": "btn_tokens_permissions",
    "tools permissions --reset": "btn_permissions_reset",
    "tools permissions --reset --caller": "btn_permissions_reset_caller",
    "tools permissions --reset --token": "btn_tokens_reset",  # also btn_permissions_reset_token in a token's window
    "tools permissions --confirm-in-framework": ACT_PERMISSIONS,   # the "Ask in the framework" column
    "tools permissions --confirm-anywhere": ACT_PERMISSIONS,
    "tools permissions --grants": ACT_PERMISSIONS,          # the standing approvals list
    "tools permissions --revoke-grant": ACT_PERMISSIONS,    # Revoke selected standing approval
    "tools audit": ACT_AUDIT,
    "tools audit --limit": "spin_audit_limit",
    "tools audit --details": "chk_audit_details",
    "tools audit --verify": "btn_audit_check",
    "tools audit --archive": "btn_audit_archive",
    "tools audit --summary": "btn_audit_summary",
    "tools folders": ACT_FOLDERS,
    "tools folders list": ACT_FOLDERS,
    "tools folders add": "btn_ai_folders_add",
    "tools folders add --label": "btn_ai_folders_add",
    "tools folders add --kind": "btn_ai_folders_add",
    "tools folders remove": "btn_ai_folders_remove",
    "tools crawl-notice": ACT_CRAWL,
    "tools approvals": ACT_APPROVALS,                       # Tools > Pending Approvals... (covered before wave 5)
    "tools approvals list": ACT_APPROVALS,
    "tools approvals show": ACT_APPROVALS,                  # select a call: the question as it will run
    "tools approvals approve": "btn_approvals_approve",
    "tools approvals approve --always": "btn_approvals_always",
    "tools approvals approve --width": "btn_approvals_always",
    "tools approvals approve --duration": "btn_approvals_always",
    "tools approvals decline": "btn_approvals_decline",
    "mcp": ACT_CONNECTIONS,
    "mcp config": ACT_CONNECTIONS,                          # the three entries with Copy buttons
    "mcp config --write": "btn_mcp_write_desktop",
    "mcp serve --transport": "btn_mcp_http_start",          # http; stdio is started by the AI app (MACHINE_ONLY)
    "mcp serve --host": "combo_mcp_host",
    "mcp serve --port": "spin_mcp_port",
    "mcp serve --container": "chk_mcp_container",
    "mcp token": ACT_CONNECTIONS,
    "mcp token list": ACT_CONNECTIONS,                      # also in Tool Permissions (Ditto / MCP)...
    "mcp token add": "btn_tokens_add",
    "mcp token add --policy-from": "btn_tokens_add",
    "mcp token add --link": "btn_tokens_add",
    "mcp token rotate": "btn_tokens_rotate",
    "mcp token revoke": "btn_tokens_revoke",
}
# Objects that live in a window, and the menu action that opens it. The token buttons exist in Tool Permissions
# (Ditto / MCP)... as well; the folder buttons in Folders the AI May Use....
WINDOW_OBJECTS: Dict[str, str] = {
    "combo_permissions_caller": ACT_PERMISSIONS,
    "btn_permissions_reset": ACT_PERMISSIONS,
    "btn_permissions_reset_caller": ACT_PERMISSIONS,
    "btn_tokens_permissions": ACT_CONNECTIONS,
    "btn_tokens_reset": ACT_CONNECTIONS,
    "btn_tokens_add": ACT_CONNECTIONS,
    "btn_tokens_rotate": ACT_CONNECTIONS,
    "btn_tokens_revoke": ACT_CONNECTIONS,
    "spin_audit_limit": ACT_AUDIT,
    "chk_audit_details": ACT_AUDIT,
    "btn_audit_check": ACT_AUDIT,
    "btn_audit_archive": ACT_AUDIT,
    "btn_audit_summary": ACT_AUDIT,
    "btn_ai_folders_add": ACT_FOLDERS,
    "btn_ai_folders_remove": ACT_FOLDERS,
    "btn_approvals_approve": ACT_APPROVALS,
    "btn_approvals_always": ACT_APPROVALS,
    "btn_approvals_decline": ACT_APPROVALS,
    "btn_mcp_write_desktop": ACT_CONNECTIONS,
    "btn_mcp_http_start": ACT_CONNECTIONS,
    "combo_mcp_host": ACT_CONNECTIONS,
    "spin_mcp_port": ACT_CONNECTIONS,
    "chk_mcp_container": ACT_CONNECTIONS,
}
MACHINE_ONLY: Dict[str, str] = {
    "mcp serve": "stdio: the AI app starts the server itself with the entry from AI App Connections (MCP)...",
}


def _action(window, name: str, key: str, slot) -> QAction:
    return place.new_action(window, name, ui_text(key), lambda: slot(window))


class _CloseGuard(QObject):
    """Asks before the main window closes while the HTTP MCP server it started still serves ("No" is the default:
    the window stays open); on "Yes" the server is stopped before the window closes."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
        if obj is self.window and event.type() == QEvent.Close:
            server = getattr(self.window, "mcp_http_server", None)
            if server is not None and server.running:
                from orchestrator.gui.plain import ask
                if not ask(self.window, ui_text("tmcp.mcp.title"), ui_text("tmcp.http.exit.question", url=server.url)):
                    event.ignore()
                    return True
                server.stop_and_wait()
        return False


def install(window, menubar) -> None:
    """Adds Security > Audit Log..., Security > AI App Connections (MCP)... and Tools > Deep Ingest Notice... (after
    Folders the AI May Use...), names the existing Tool Permissions and Folders entries, and installs the question
    before closing while the HTTP MCP server serves."""
    security = place.menu_or_new(menubar, place.MENU_SECURITY, SECURITY_MENU)
    window.act_tools_mcp_audit = _action(window, ACT_AUDIT, "tmcp.menu.audit", open_audit_log)
    window.act_tools_mcp_connections = _action(window, ACT_CONNECTIONS, "tmcp.menu.mcp", open_connections)
    security.addAction(window.act_tools_mcp_audit)
    security.addAction(window.act_tools_mcp_connections)

    tools = place.menu_or_new(menubar, place.MENU_TOOLS, TOOLS_MENU)
    place.name_entry(place.entry(tools, PERMISSIONS_TEXT, ACT_PERMISSIONS, menubar), ACT_PERMISSIONS)
    place.name_entry(place.entry(tools, APPROVALS_TEXT, ACT_APPROVALS, menubar), ACT_APPROVALS)
    folders = place.name_entry(place.entry(tools, FOLDERS_TEXT, ACT_FOLDERS, menubar), ACT_FOLDERS)
    window.act_tools_mcp_crawl_notice = _action(window, ACT_CRAWL, "tmcp.menu.crawl", open_crawl_notice)
    place.insert_after(place.menu_holding(menubar, folders) or tools, folders, window.act_tools_mcp_crawl_notice)

    guard = _CloseGuard(window)
    window.installEventFilter(guard)
    window._tools_mcp_close_guard = guard


# --- the handlers --------------------------------------------------------------------------------------------------

def _registry(window):
    return getattr(getattr(window, "framework", None), "tool_registry", None)


def open_audit_log(window, lang: Optional[str] = None):
    """Security > Audit Log...: the audit window (fresh entries each time it opens; an open one comes to the front)."""
    from orchestrator.gui.plain import plain_box
    current = getattr(window, "audit_window", None)
    try:
        if current is not None and current.isVisible():
            current.raise_()
            current.activateWindow()
            current.load()
            return current
    except RuntimeError:   # its window object is gone
        current = None
    try:
        from orchestrator.gui.audit_window import AuditLogWindow
        dialog = AuditLogWindow(_registry(window), window, lang=lang)
    except Exception as e:  # noqa: BLE001 - plain text (SEC-M2)
        plain_box(window, "critical", ui_text("tmcp.audit.title", lang), str(e))
        return None
    if current is not None:
        current.deleteLater()
    window.audit_window = dialog
    dialog.show()
    return dialog


def open_connections(window, lang: Optional[str] = None):
    """Security > AI App Connections (MCP)...."""
    from orchestrator.gui.mcp_window import open_mcp_window
    return open_mcp_window(window, lang=lang)


def open_crawl_notice(window, lang: Optional[str] = None) -> str:
    """Tools > Deep Ingest Notice... (`llmcf tools crawl-notice`): always shows the notice. When it is still needed,
    the person confirms that they read it ("No" is the default) and the acknowledgement is remembered and audited
    (actor gui) by the CLI's own function; otherwise it says where it was acknowledged. Returns what happened:
    already, acknowledged, declined or failed."""
    from orchestrator.gui.plain import ask, one_line, plain_box
    from orchestrator.utils.crawl_notice import CrawlNotice, acknowledge_and_audit, notice_text
    lang = lang or current_language()
    title, text = notice_text(lang)
    framework = getattr(window, "framework", None)
    try:
        notice = CrawlNotice(framework.config.user_state_dir)
        needed = notice.needed()
    except (OSError, AttributeError, TypeError) as e:
        plain_box(window, "warning", title, f"{text}\n\n" + ui_text("tmcp.crawl.not_saved", lang,
                                                                     reason=one_line(str(e))))
        return "failed"
    if not needed:
        via = notice.acknowledged_via()
        where = ui_text(f"tmcp.crawl.via.{via}" if via in ("gui", "cli") else "tmcp.crawl.via.unknown", lang)
        plain_box(window, "information", title, f"{text}\n\n" + ui_text("tmcp.crawl.already", lang, via=where))
        return "already"
    if not ask(window, title, f"{text}\n\n" + ui_text("tmcp.crawl.confirm", lang)):
        plain_box(window, "information", title, ui_text("tmcp.crawl.not_ack", lang))
        return "declined"
    try:
        problem = acknowledge_and_audit(notice, "gui", getattr(_registry(window), "audit", None))
    except OSError as e:
        plain_box(window, "warning", title, ui_text("tmcp.crawl.not_saved", lang, reason=one_line(str(e))))
        return "failed"
    message = ui_text("tmcp.crawl.ack", lang)
    if problem:
        message += "\n\n" + ui_text("tmcp.crawl.ack_unrecorded", lang, reason=one_line(problem))
    plain_box(window, "information", title, message)
    return "acknowledged"


__all__ = ["CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_audit_log", "open_connections",
           "open_crawl_notice"]
