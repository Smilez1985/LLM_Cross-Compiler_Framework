#!/usr/bin/env python3
"""
Ditto > Agents...: the ACP agents the person picks, by mouse (`llm-cli agents list|search|add|remove|login|run`).

install() adds the entry to the Ditto menu (act_acp_agents, found by the menu's object name) and keeps the window on
the main window (acp_agents_window). The window loads when the entry is first used (lazy loading). `agents run` is
the chat panel's agent picker (cb_chat_agent): the person picks the agent and writes as to Ditto.

CLI_PATHS names, for every person-facing `llm-cli agents ...` command and option, the object the person clicks or
fills instead; WINDOW_OBJECTS the entry that opens the window an object lives in. MACHINE_ONLY lists the switches for
scripts only, with the reason.
"""

from typing import Any, Dict

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

ACT_AGENTS = "act_acp_agents"

CLI_PATHS: Dict[str, str] = {
    "agents list": "tbl_acp_agents",            # Your agents: name, distribution, version, from, internet, keys
    "agents search": "tbl_acp_registry",        # the registry's agents (the search field filters them)
    "agents search --refresh": "btn_acp_refresh",
    "agents add": "btn_acp_add_registry",       # the selected registry agent, after the question ("No" default)
    "agents add --kind": "cb_acp_kind",
    "agents add --command": "txt_acp_command",  # Your own command: the program
    "agents add --name": "txt_acp_name",
    "agents add --arg": "txt_acp_args",
    "agents add --local": "chk_acp_local",
    "agents add --key": "txt_acp_keys",
    "agents remove": "btn_acp_remove",          # the full list, "No" as the default (Core/deletion)
    "agents login": "btn_acp_login",            # the agent's own sign-in methods
    "agents login --method": "btn_acp_login",   # chosen in the list of methods
    "agents login --key": "btn_acp_key",        # Store a key...: masked twice
    "agents run": "cb_chat_agent",              # the chat panel's agent picker
}
WINDOW_OBJECTS: Dict[str, str] = {name: ACT_AGENTS for name in (
    "tbl_acp_agents", "tbl_acp_registry", "btn_acp_refresh", "btn_acp_add_registry", "cb_acp_kind",
    "txt_acp_command", "txt_acp_name", "txt_acp_args", "chk_acp_local", "txt_acp_keys", "btn_acp_add_command",
    "btn_acp_remove", "btn_acp_login", "btn_acp_key", "txt_acp_search")}
MACHINE_ONLY: Dict[str, str] = {
    "agents list --json": "machine-readable output for scripts; the window's table shows the same rows",
    "agents search --json": "machine-readable output for scripts; the registry table shows the same rows",
}


def install(window: Any, menubar: Any) -> None:
    """Adds Agents... to the Ditto menu (window.act_acp_agents)."""
    from orchestrator.gui.menus.ditto_runtime import ditto_menu
    menu = ditto_menu(window, menubar)
    action = place.new_action(window, ACT_AGENTS, ui_text("acp.menu.agents"), lambda: open_window(window))
    menu.addAction(action)
    window.act_acp_agents = action


def open_window(window: Any) -> Any:
    from orchestrator.gui.acp_agents_window import open_agents_window
    return open_agents_window(window)


__all__ = ["ACT_AGENTS", "CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_window"]
