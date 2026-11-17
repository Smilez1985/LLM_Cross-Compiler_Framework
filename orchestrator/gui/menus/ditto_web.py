#!/usr/bin/env python3
"""
Ditto > Web Search...: Ditto's web search by mouse (GUI parity): the choice of ways and providers, the keys, the search
engine on this computer and a test search (gui/ditto_web_window.py).

install() puts the entry into the menu bar's Ditto menu (shared with the other Ditto windows: found by its object name
"menu_ditto") and keeps the action and the window on the main window (act_ditto_web, ditto_web_window). The window is
not modal; a second click shows the same window again with what is set read again.

CLI_PATHS names, for every `llm-cli ditto web ...` command and option, the object the person clicks or fills instead;
WINDOW_OBJECTS names the menu action that opens the window an object lives in; MACHINE_ONLY lists the switches that only
make machine output or skip a step for scripts, with the reason.
"""

from typing import Any, Dict

from orchestrator.Core.web_search import PROVIDERS, WAYS
from orchestrator.gui.menus import place
from orchestrator.gui.menus.ditto_runtime import ditto_menu
from orchestrator.utils.localization import ui_text

WEB_ACTION = "act_ditto_web_open"

CLI_PATHS: Dict[str, str] = {
    "ditto web": WEB_ACTION,
    "ditto web status": "lbl_web_status",                   # what is set, in one sentence
    "ditto web use": "btn_web_apply",                       # with its boxes and fields
    "ditto web use --ways": "chk_web_way_searxng",          # one box for each way
    "ditto web use --apis": "chk_web_api_tavily",           # one box for each provider
    "ditto web use --address": "edt_web_address",
    "ditto web use --zone": "edt_web_zone",
    "ditto web search": "btn_web_search",                   # the query box and its button
    "ditto web search --count": "spn_web_count",
    "ditto web setup": "btn_web_setup",                     # asks first, No by default
    "ditto web setup --way": "cb_web_engine_way",           # the own Python folder (default) or the container
    "ditto web setup --remove": "btn_web_remove",
}
WINDOW_OBJECTS: Dict[str, str] = {
    **{name: WEB_ACTION for name in CLI_PATHS.values() if name != WEB_ACTION},
    **{f"chk_web_way_{way}": WEB_ACTION for way in WAYS},
    **{f"chk_web_api_{name}": WEB_ACTION for name in PROVIDERS},
    "edt_web_query": WEB_ACTION,
    "tbl_web_hits": WEB_ACTION,
    "lbl_web_result": WEB_ACTION,
    "cb_web_key_provider": WEB_ACTION,       # the keys go into the secrets store: `llm-cli secrets set web_search_<name>_key`
    "edt_web_key": WEB_ACTION,
    "btn_web_key_save": WEB_ACTION,
}
MACHINE_ONLY: Dict[str, str] = {
    "ditto web status --json": "machine output for scripts; the window shows the same choice",
    "ditto web search --json": "machine output for scripts; the table shows the same hits",
    "ditto web setup --no-start": "for the installer and scripts: the window's button always checks that it answers",
    "ditto web setup --yes": "skips the question for scripts and the installer; the window always asks (No by default)",
}


def open_web(window: Any) -> Any:
    """Ditto > Web Search... (the same window again on a second click, what is set read again)."""
    from orchestrator.gui.plain import plain_box
    existing = getattr(window, "ditto_web_window", None)
    try:
        if existing is None:
            from orchestrator.gui.ditto_web_window import DittoWebWindow
            existing = DittoWebWindow(window.framework, window)
            window.ditto_web_window = existing
        else:
            existing.refresh()
    except Exception as e:  # noqa: BLE001 - plain text (SEC-M2)
        plain_box(window, "critical", ui_text("websearch.title"), str(e))
        return None
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


def install(window: Any, menubar: Any) -> None:
    """Ditto > Web Search... in the main window's menu bar."""
    menu = ditto_menu(window, menubar)
    action = place.new_action(window, WEB_ACTION, ui_text("websearch.menu"), lambda: open_web(window))
    menu.addAction(action)
    window.act_ditto_web = action


__all__ = ["CLI_PATHS", "MACHINE_ONLY", "WEB_ACTION", "WINDOW_OBJECTS", "install", "open_web"]
