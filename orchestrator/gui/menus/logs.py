#!/usr/bin/env python3
"""
The framework's log in the main window's menus (W1': every action of `llm-cli logs` also by mouse).

Help > Framework Log... opens orchestrator/gui/log_window.py: the entries of framework.log with the CLI's filters
(level, last minutes, part of the framework, number of entries), Export..., Open Log Folder and the row "Level of a
part" (`logs level`, `logs levels`). The window loads when
the entry is first used, not with the main window (lazy loading).
"""

from typing import Dict, Optional

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

HELP_MENU = "Help"   # a title is only the fallback: the menus are found by their object names

CLI_PATHS: Dict[str, str] = {
    "logs show": "act_framework_log",               # Help > Framework Log...: the newest entries when it opens
    "logs show --level": "cmb_log_level",
    "logs show --since": "spin_log_since",
    "logs show --part": "txt_log_part",
    "logs show --lines": "spin_log_lines",
    "logs export": "btn_log_export",                # Framework Log window > Export... (a .json file is JSON)
    "logs export --level": "cmb_log_level",
    "logs export --since": "spin_log_since",
    "logs export --format": "btn_log_export",
    "logs level": "btn_part_level_set",             # Framework Log window > Level of a part: part, level, Set Level
    "logs level --reset": "btn_part_level_reset",
    "logs levels": "lbl_part_levels",               # the parts with their own level, beside the row
}
WINDOW_OBJECTS: Dict[str, str] = {name: "act_framework_log" for name in (
    "cmb_log_level", "spin_log_since", "txt_log_part", "spin_log_lines", "btn_log_export", "btn_log_folder",
    "btn_log_refresh", "txt_part_level_part", "cmb_part_level", "btn_part_level_set", "btn_part_level_reset",
    "lbl_part_levels")}
MACHINE_ONLY: Dict[str, str] = {
    "logs show --json": "machine-readable output; the Framework Log window shows the same entries",
    "logs levels --json": "machine-readable output; the Framework Log window shows the same levels",
}


def install(window, menubar) -> None:
    """Adds Help > Framework Log... (window.act_framework_log); MENU_LAYOUT puts it above About."""
    menu = place.menu_or_new(menubar, place.MENU_HELP, HELP_MENU)
    action = place.new_action(window, "act_framework_log", ui_text("logs.menu"), lambda: open_log_window(window))
    menu.addAction(action)
    window.act_framework_log = action


def open_log_window(window) -> Optional[object]:
    """Shows the Framework Log window for the log of this framework (an open one comes to the front and reads
    again)."""
    from orchestrator.gui.log_window import FrameworkLogWindow
    from orchestrator.utils.crash_report import framework_log_folder
    existing = getattr(window, "framework_log_window", None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        existing.refresh()
        return existing
    framework = getattr(window, "framework", None)
    config = getattr(framework, "config", None)
    dialog = FrameworkLogWindow(framework_log_folder(config), parent=window, config=config)
    window.framework_log_window = dialog
    dialog.show()
    return dialog


__all__ = ["CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_log_window"]
