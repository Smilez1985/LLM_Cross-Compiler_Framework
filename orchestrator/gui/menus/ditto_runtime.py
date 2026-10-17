#!/usr/bin/env python3
"""
Ditto > Local Runtime...: Ditto's local runtime by mouse (W1', gui/ditto_runtime_window.py).

install() puts the entry into the menu bar's Ditto menu (shared with the local models chain: found by its object name
"menu_ditto" or its title, else created before the Security menu) and keeps the action and the window on the main
window (act_ditto_runtime, ditto_runtime_window). The window is not modal and stays open while a build or a download
runs; a second click shows the same window again.

CLI_PATHS names, for every person-facing `llm-cli ditto runtime ...` command and option, the object the person
clicks or fills instead: the menu action, or a widget of the window (its objects exist once the window was opened
with act_ditto_runtime_open; the mode box's objects also exist in Tools > Configure AI Agent (Ditto)...).
MACHINE_ONLY lists the switches that only skip a question or change the output format, with the reason.
"""

from typing import Any

from PySide6.QtWidgets import QMenu

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

DITTO_MENU = place.MENU_DITTO
BEFORE_MENU = "Security"   # the Ditto menu comes after File, Build and Boards, before Security (GUI_PARITY.md)

CLI_PATHS = {
    "ditto runtime status": "act_ditto_runtime_open",
    "ditto runtime install": "btn_ditto_runtime_build",
    "ditto runtime install --backend": "cb_ditto_runtime_backend",
    "ditto runtime install --placement": "cb_ditto_runtime_placement",
    "ditto runtime install --jobs": "sp_ditto_runtime_jobs",
    "ditto runtime install --prebuilt": "btn_ditto_runtime_prebuilt",
    "ditto runtime install --force": "chk_ditto_runtime_force",
    "ditto runtime ensure": "btn_ditto_runtime_switch",
    "ditto runtime ensure --mode": "cb_ditto_runtime_mode",
    "ditto runtime ensure --force": "btn_ditto_runtime_switch",
    "ditto runtime ensure --placement": "cb_ditto_runtime_placement",
    "ditto runtime ensure --backend": "cb_ditto_runtime_backend",
    "ditto runtime ensure --jobs": "sp_ditto_runtime_jobs",
    "ditto runtime ensure --no-model": "chk_ditto_runtime_offer_model",
    "ditto runtime import": "btn_ditto_runtime_import",
    "ditto runtime export": "btn_ditto_runtime_export",
    "ditto runtime prune": "btn_ditto_runtime_prune",
    "ditto runtime start": "btn_ditto_runtime_start",
    "ditto runtime start --model": "cb_ditto_runtime_tier",
    "ditto runtime start --ctx": "sp_ditto_runtime_ctx",
    "ditto runtime start --sleep-idle": "sp_ditto_runtime_idle",
    "ditto runtime stop": "btn_ditto_runtime_stop",
    "ditto runtime export-source": "btn_ditto_runtime_export_source",
    "ditto runtime export-source --mirror": "txt_ditto_runtime_mirror",
    "ditto runtime import-source": "btn_ditto_runtime_import_source",
}

# Objects that live in Ditto > Local Runtime..., and the menu action that opens it (the mode box's objects also exist
# in Tools > Configure AI Agent (Ditto)...). Added by the wave 5 integration for tests/test_cli_gui_parity.py.
WINDOW_OBJECTS = {name: "act_ditto_runtime_open" for name in CLI_PATHS.values() if name != "act_ditto_runtime_open"}

MACHINE_ONLY = {
    "ditto runtime status --json": "machine-readable output; the window shows the same values as text",
    "ditto runtime install --yes": "skips the questions; the GUI always asks (No by default)",
    "ditto runtime ensure --yes": "skips the questions; the GUI always asks (No by default)",
    "ditto runtime import --yes": "skips the question before the imported programs run; the GUI always asks",
    "ditto runtime export-source --yes": "skips the question before the source is fetched; the GUI always asks",
}


def ditto_menu(window: Any, menubar: Any) -> QMenu:
    """The menu bar's Ditto menu: found by its object name or its title, else created before the Security menu."""
    title = ui_text("ditto_runtime.menu")
    menu = place.find_menu(menubar, DITTO_MENU, (title,))
    if menu is not None:
        return menu
    menu = QMenu(title, menubar)
    menu.setObjectName(DITTO_MENU)
    place.place_menu_before(menubar, place.find_menu(menubar, place.MENU_SECURITY, (BEFORE_MENU,)), menu)
    window.menu_ditto = menu
    return menu


def open_window(window: Any) -> Any:
    """Shows Ditto > Local Runtime... (the same window again on a second click, with its status checked again)."""
    from orchestrator.gui.plain import plain_box
    existing = getattr(window, "ditto_runtime_window", None)
    try:
        if existing is None:
            from orchestrator.gui.ditto_runtime_window import LocalRuntimeWindow
            existing = window.ditto_runtime_window = LocalRuntimeWindow(window.framework, window)
        else:
            existing.refresh_status()
    except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text (SEC-M2)
        plain_box(window, "critical", ui_text("ditto_runtime.title"), str(e))
        return None
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


def install(window: Any, menubar: Any) -> None:
    """Ditto > Local Runtime... in the main window's menu bar."""
    menu = ditto_menu(window, menubar)
    action = place.new_action(window, "act_ditto_runtime_open", ui_text("ditto_runtime.menu.open"),
                              lambda: open_window(window))
    menu.addAction(action)
    window.act_ditto_runtime = action


__all__ = ["CLI_PATHS", "DITTO_MENU", "MACHINE_ONLY", "WINDOW_OBJECTS", "ditto_menu", "install", "open_window"]
