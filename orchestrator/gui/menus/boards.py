#!/usr/bin/env python3
"""
The Boards menu of the main window (W1' of 2026-10-16: every action of `llmcf deploy devices` and `llmcf device` also
by mouse).

Boards > Boards and Devices... (act_boards_devices) opens one window for every board (orchestrator/gui/
boards_window.py): the stored and the trusted boards in one table, with Add a trusted board..., Remove trust...,
Settings..., Runtime plan, Status, Build runtime..., Deploy runtime..., Verify..., Remove older runtime packages...,
Remove older builds on the board... and Check this board (Mojo). The menu sits after the Build menu (after File while
there is none). The window and the board modules load when the entry is first used (lazy loading, RG1).

CLI_PATHS names, for each `llmcf` command path of this area, the object that does it by mouse; WINDOW_OBJECTS names,
for an object that lives in a window, the menu action that opens that window. MACHINE_ONLY lists the switches of this
area that are for scripts only, with the reason (the integrator lists them once).
"""

from typing import Dict

from PySide6.QtGui import QAction

from orchestrator.utils.localization import ui_text

MENU_NAME = "menu_boards"
ACTION_NAME = "act_boards_devices"

CLI_PATHS: Dict[str, str] = {
    "deploy devices": ACTION_NAME,
    "deploy devices list": ACTION_NAME,              # the table: label, address, user, key, paired, since, last
    "deploy devices add": "btn_boards_add",
    "deploy devices add --user": "btn_boards_add",
    "deploy devices add --port": "btn_boards_add",
    "deploy devices add --label": "btn_boards_add",
    "deploy devices remove": "btn_boards_remove_trust",
    "device": ACTION_NAME,
    "device list": ACTION_NAME,                      # the table: system, deploy settings, deployed runtime
    "device settings": "btn_boards_settings",
    "device settings --install": "btn_boards_settings",
    "device settings --user": "btn_boards_settings",
    "device settings --port": "btn_boards_settings",
    "device settings --model": "btn_boards_settings",
    "device settings --kv-cache": "btn_boards_settings",
    "device runtime": ACTION_NAME,
    "device runtime plan": "btn_boards_plan",
    "device runtime build": "btn_boards_build",
    "device runtime build --jobs": "btn_boards_build",
    "device runtime build --mirror": "btn_boards_build",
    "device runtime prune": "btn_boards_prune",
    "device runtime prune --on-device": "btn_boards_prune_board",
    "device runtime deploy": "btn_boards_deploy",
    "device runtime deploy --user": "btn_boards_deploy",
    "device runtime deploy --password": "btn_boards_deploy",
    "device runtime deploy --bench-model": "btn_boards_deploy",
    "device runtime deploy --allow-untrusted-model": "btn_boards_deploy",
    "device runtime verify": "btn_boards_verify",
    "device runtime verify --user": "btn_boards_verify",
    "device runtime verify --bench-model": "btn_boards_verify",
    "device runtime verify --allow-untrusted-model": "btn_boards_verify",
    "device runtime status": "btn_boards_status",
}
# Objects that live in the Boards window, and the menu action that opens it. The window's Check this board (Mojo)
# (btn_boards_check) does what `llmcf mojo --oracle --board NAME` does for the selected board; the Mojo menu module
# names its own objects for that path.
WINDOW_OBJECTS: Dict[str, str] = {name: ACTION_NAME for name in set(CLI_PATHS.values()) if name != ACTION_NAME}
WINDOW_OBJECTS["btn_boards_check"] = ACTION_NAME
MACHINE_ONLY: Dict[str, str] = {
    "deploy devices list --json": "machine-readable output; the window shows the same rows",
    "device list --json": "machine-readable output; the window shows the same rows",
    "device runtime plan --json": "machine-readable output; Runtime plan shows the plan",
    "device runtime status --json": "machine-readable output; Status shows the same facts",
    "device runtime build --yes": "script switch: the window asks the source question instead",
    "device settings --confirm-sudo": "script switch: the window asks the sudo question instead",
    "device runtime deploy --password-stdin": "script switch: the window asks for the password each time",
}
BUILD_MENU_NAMES = ("menu_build",)
BUILD_MENU_TITLES = ("Build", "Bauen")


def _after(menubar):
    """The menu bar action the Boards menu follows: the Build menu, else the first menu (File)."""
    actions = [a for a in menubar.actions() if a.menu() is not None]
    for action in actions:
        menu = action.menu()
        if menu.objectName() in BUILD_MENU_NAMES or action.text().replace("&", "") in BUILD_MENU_TITLES:
            return action
    return actions[0] if actions else None


def install(window, menubar) -> None:
    """Adds the Boards menu with Boards and Devices... (window.act_boards_devices); finds an existing Boards menu by
    its object name."""
    menu = next((a.menu() for a in menubar.actions() if a.menu() is not None
                 and a.menu().objectName() == MENU_NAME), None)
    if menu is None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(ui_text("boards.menu"), menubar)
        menu.setObjectName(MENU_NAME)
        after = _after(menubar)
        actions = menubar.actions()
        following = actions[actions.index(after) + 1] if after is not None and actions.index(after) + 1 < len(
            actions) else None
        if following is not None:
            menubar.insertMenu(following, menu)
        else:
            menubar.addMenu(menu)
    action = QAction(ui_text("boards.menu.devices"), window)
    action.setObjectName(ACTION_NAME)
    action.triggered.connect(lambda _=False: open_boards_window(window))
    menu.addAction(action)
    window.menu_boards = menu
    window.act_boards_devices = action


def open_boards_window(window):
    """Shows the Boards window of the main window (fresh data each time it opens)."""
    from orchestrator.gui.boards_window import open_boards
    return open_boards(window)


__all__ = ["ACTION_NAME", "CLI_PATHS", "MACHINE_ONLY", "MENU_NAME", "WINDOW_OBJECTS", "install",
           "open_boards_window"]
