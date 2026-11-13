#!/usr/bin/env python3
"""
Ditto > Ditto's Archive...: Ditto's archive by mouse (GUI parity): the list, the search, the person's confirm, take-back,
delete and undo, and the level (gui/ditto_archive_window.py).

install() puts the entry into the menu bar's Ditto menu (shared with the other Ditto windows: found by its object name
"menu_ditto") and keeps the action and the window on the main window (act_ditto_archive, ditto_archive_window). The
window is not modal; a second click shows the same window again with the archive read again. It loads when the entry is
first used (lazy loading).

CLI_PATHS names, for every `llm-cli ditto archive ...` command and option, the object the person clicks or fills instead;
WINDOW_OBJECTS names the menu action that opens the window an object lives in; MACHINE_ONLY lists the switches that only
make machine output or skip a question for scripts, with the reason.
"""

from typing import Any, Dict

from orchestrator.gui.menus import place
from orchestrator.gui.menus.ditto_runtime import ditto_menu
from orchestrator.utils.localization import ui_text

ARCHIVE_ACTION = "act_ditto_archive_open"

CLI_PATHS: Dict[str, str] = {
    "ditto archive": ARCHIVE_ACTION,
    "ditto archive list": "tbl_archive_entries",             # the entries with status, ground, sources, key, sentence
    "ditto archive list --status": "cb_archive_status",
    "ditto archive list --copies": "chk_archive_copies",
    "ditto archive search": "btn_archive_search",            # the search box and its button
    "ditto archive search --confirmed-only": "chk_archive_confirmed_only",
    "ditto archive show": "lbl_archive_detail",              # the selected entry in full, with its evidence
    "ditto archive confirm": "btn_archive_confirm",
    "ditto archive unconfirm": "btn_archive_unconfirm",
    "ditto archive delete": "btn_archive_delete",
    "ditto archive undo": "btn_archive_undo",
    "ditto archive level": "cb_archive_level",               # with its Set Level button
}
WINDOW_OBJECTS: Dict[str, str] = {
    **{name: ARCHIVE_ACTION for name in CLI_PATHS.values() if name != ARCHIVE_ACTION},
    "edt_archive_search": ARCHIVE_ACTION,
    "btn_archive_level_apply": ARCHIVE_ACTION,
    "btn_archive_refresh": ARCHIVE_ACTION,
}
MACHINE_ONLY: Dict[str, str] = {
    "ditto archive list --json": "machine output for scripts; the table shows the same entries",
    "ditto archive search --json": "machine output for scripts; the table shows the same hits",
    "ditto archive show --json": "machine output for scripts; the detail shows the same entry",
    "ditto archive level --json": "machine output for scripts; the level box shows the same level",
    "ditto archive delete --yes": "skips the question; the GUI always asks (No by default)",
    "ditto archive undo --yes": "skips the question; the GUI always asks (No by default)",
}


def open_archive(window: Any) -> Any:
    """Ditto > Ditto's Archive... (the same window again on a second click, the archive read again)."""
    from orchestrator.gui.plain import plain_box
    existing = getattr(window, "ditto_archive_window", None)
    try:
        if existing is None:
            from orchestrator.gui.ditto_archive_window import DittoArchiveWindow
            existing = DittoArchiveWindow(window.framework, window)
            window.ditto_archive_window = existing
        else:
            existing.refresh()
    except Exception as e:  # noqa: BLE001 - e.g. no archive; plain text (SEC-M2)
        plain_box(window, "critical", ui_text("archive.title"), str(e))
        return None
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


def install(window: Any, menubar: Any) -> None:
    """Ditto > Ditto's Archive... in the main window's menu bar."""
    menu = ditto_menu(window, menubar)
    action = place.new_action(window, ARCHIVE_ACTION, ui_text("archive.menu"), lambda: open_archive(window))
    menu.addAction(action)
    window.act_ditto_archive = action


__all__ = ["ARCHIVE_ACTION", "CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_archive"]
