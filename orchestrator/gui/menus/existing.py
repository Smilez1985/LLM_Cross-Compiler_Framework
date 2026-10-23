#!/usr/bin/env python3
"""
Mouse paths that existed before the GUI parity wave and belong to no area module (W1': every action of the
command line also by mouse). This module adds nothing to the menu bar (it has no install()): the entries below are
wired by the main window itself; it only names them for tests/test_cli_gui_parity.py.

The other paths covered before wave 5 sit in their area modules: File > Output Folder... and Tools > Create Hardware
Module... (build), Knowledge Snapshots... and its Delete/Export (knowledge), Pending Approvals... and Folders the AI
May Use... (tools_mcp), the Deployment tab's build list (deploy).

CLI_PATHS names, for each `llm-cli` command path, the object that does it by mouse; WINDOW_OBJECTS names, for an object
that lives in a window, the menu action that opens that window; MACHINE_ONLY lists the switches of the command line
program itself, which only change how the CLI starts or prints, with the reason.
"""

from typing import Dict

ACT_TRASH = "act_file_trash"   # File > Trash...
ACT_HF_BROWSER = "act_tools_hf_browser"   # Tools > Hugging Face Model Browser...

CLI_PATHS: Dict[str, str] = {
    "trash": ACT_TRASH,                             # `trash` alone lists, as `trash list`
    "trash list": ACT_TRASH,                        # the table: item, what it is, size, moved there, asked by, ...
    "trash restore": "btn_trash_restore",           # Put back (the ticked entries)
    "trash empty": "btn_trash_delete",              # Delete for good... (the ticked entries, the full deletion list)
    "trash empty --all": "btn_trash_delete",        # the window lists every entry; the person ticks
    "trash empty --due": "btn_trash_delete",        # the Reminder column shows which ones are due; the person ticks
    "ditto setup": "act_tools_permissions",         # Tools > Tool Permissions (Ditto / MCP)... (also at first start)
    "models take-over": "btn_hf_take_over",         # the browser's Take over old downloads... (the same list and yes)
    "--version": "act_help_about",                  # Help > About shows the version
}
WINDOW_OBJECTS: Dict[str, str] = {
    "btn_trash_restore": ACT_TRASH,
    "btn_trash_delete": ACT_TRASH,
    "btn_hf_take_over": ACT_HF_BROWSER,
}
MACHINE_ONLY: Dict[str, str] = {
    "--config": "start switch of the command line program; the GUI uses its own settings (File > Settings...)",
    "--verbose": "how much the command line prints; the GUI shows its messages in its own windows",
    "--quiet": "how much the command line prints; the GUI shows its messages in its own windows",
    "--log-level": "how much the command line logs; the GUI shows its messages in its own windows",
    "--debug-http": "a developer switch that logs the HTTP traffic of the command line to the console",
}

__all__ = ["ACT_HF_BROWSER", "ACT_TRASH", "CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS"]
