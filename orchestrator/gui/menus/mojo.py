#!/usr/bin/env python3
"""
Mojo in the main window's menus (W1' of 2026-10-16: every action of `llmcf mojo` also by mouse).

Tools > Mojo Health Check... opens the Mojo window (orchestrator/gui/mojo_window.py): Mojo Oracle's findings, Run Mojo
Miracle..., Check <board> now and Check a board.... The Deployment tab's "Benchmark on the board" group has the same
board check as "Check this board" (deployment_window.py). The Mojo window and the Mojo package load when the entry is
first used, not with the main window (lazy loading, RG1).

CLI_PATHS names, for each `llmcf` command path of Mojo, the object that does it by mouse; WINDOW_OBJECTS names, for
an object that lives in a window, the menu action that opens that window. The doctor is the 3.0.0 name of Mojo (a
compatibility alias with the same checks): its paths lead to the same objects. MACHINE_ONLY lists `mojo --json` and
`doctor --json`, machine output only, with the reason.
"""

from typing import Dict, Optional

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

TOOLS_MENU = "Tools"   # a title is only the fallback: the menus are found by their object names

CLI_PATHS: Dict[str, str] = {
    "mojo": "act_mojo_health",                    # Tools > Mojo Health Check...: Mojo Oracle runs when it opens
    "mojo --oracle": "act_mojo_health",
    "mojo --miracle": "btn_mojo_miracle",         # Mojo window > Run Mojo Miracle... (with the build image list)
    "mojo --board": "btn_mojo_check_board",       # Mojo window > Check a board... (any trusted board); also the
                                                  # Deployment tab's "Check this board" (btn_check_board) and the
                                                  # Mojo window's "Check <board> now" for a recorded problem
    "doctor": "act_mojo_health",
    "doctor --fix": "btn_mojo_miracle",
    "doctor --tier": "act_framework_images",      # tier 2 lists the build images: Tools > Framework Build Images...
}
# Objects that live in a window, and the menu action that opens it (the Deployment tab is part of the main window).
WINDOW_OBJECTS: Dict[str, str] = {
    "btn_mojo_miracle": "act_mojo_health",
    "btn_mojo_check_board": "act_mojo_health",
    "btn_mojo_check": "act_mojo_health",
}
# Switches of this area that are for scripts only, with the reason (the same shape as the other area modules).
MACHINE_ONLY: Dict[str, str] = {
    "mojo --json": "machine-readable output; the Mojo window shows the same findings",
    "doctor --json": "machine-readable output of the 3.0.0 alias; the Mojo window shows the same findings",
}


def install(window, menubar) -> None:
    """Adds Tools > Mojo Health Check... (window.act_mojo_health) after Framework Build Images..., and names that
    existing entry (act_framework_images) for the CLI paths it already covered."""
    menu = place.menu_or_new(menubar, place.MENU_TOOLS, TOOLS_MENU)
    action = place.new_action(window, "act_mojo_health", ui_text("mojo.menu"), lambda: open_mojo_window(window))
    images = getattr(window, "act_images", None)
    place.insert_after(place.menu_holding(menubar, images) or menu, images, action)
    place.name_entry(images, "act_framework_images")
    window.act_mojo_health = action


def open_mojo_window(window) -> Optional[object]:
    """Shows the Mojo window (a new check each time it opens; an open one comes to the front)."""
    from orchestrator.gui.plain import plain_box
    current = getattr(window, "mojo_window", None)
    try:
        if current is not None and current.isVisible():
            current.raise_()
            current.activateWindow()
            return current
    except RuntimeError:   # its window object is gone
        current = None
    try:
        from orchestrator.gui.mojo_window import MojoWindow
        dialog = MojoWindow(window.framework, window)
    except Exception as e:  # noqa: BLE001 - plain text (SEC-M2)
        plain_box(window, "critical", ui_text("mojo.title"), str(e))
        return None
    if current is not None:
        current.deleteLater()
    window.mojo_window = dialog
    dialog.show()
    return dialog


__all__ = ["CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_mojo_window"]
