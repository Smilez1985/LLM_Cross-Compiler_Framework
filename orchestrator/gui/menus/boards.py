#!/usr/bin/env python3
"""
The Boards menu of the main window (W1' of 2026-10-16: every action of `llmcf deploy devices` and `llmcf device` also
by mouse).

Boards > Boards and Devices... (act_boards_devices) opens one window for every board (orchestrator/gui/
boards_window.py): the stored and the trusted boards in one table, with Add a trusted board..., Edit address or
key..., Remove trust..., Settings..., Runtime plan, Status, Build runtime..., Deploy runtime..., Verify..., Remove older runtime packages...,
Remove older builds on the board... and Check this board (Mojo). The menu sits after the Build menu (after File while
there is none). The window and the board modules load when the entry is first used (lazy loading, RG1).

CLI_PATHS names, for each `llmcf` command path of this area, the object that does it by mouse; WINDOW_OBJECTS names,
for an object that lives in a window, the menu action that opens that window. MACHINE_ONLY lists the switches of this
area that are for scripts only, with the reason (the integrator lists them once).
"""

from typing import Dict

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

MENU_NAME = place.MENU_BOARDS
ACTION_NAME = "act_boards_devices"

CLI_PATHS: Dict[str, str] = {
    "deploy devices": ACTION_NAME,
    "deploy devices list": ACTION_NAME,              # the table: label, address, user, key, paired, since, last
    "deploy devices add": "btn_boards_add",
    "deploy devices add --user": "btn_boards_add",
    "deploy devices add --port": "btn_boards_add",
    "deploy devices add --label": "btn_boards_add",
    "deploy devices remove": "btn_boards_remove_trust",
    # Q5: host name or IP address, port and the host key (pasted, or read from the board with the dialog's check box)
    "deploy devices edit": "btn_boards_edit",
    "deploy devices edit --host": "btn_boards_edit",
    "deploy devices edit --port": "btn_boards_edit",
    "deploy devices edit --key": "btn_boards_edit",
    "deploy devices edit --read-key": "btn_boards_edit",
    # round 25 Q4: the framework's own OpenSSH settings (the file shown whole, a board's block set or imported)
    "deploy ssh-config": "btn_boards_ssh",
    "deploy ssh-config show": "btn_boards_ssh",
    "deploy ssh-config edit": "btn_boards_ssh",
    "deploy ssh-config edit --line": "btn_boards_ssh",
    "deploy ssh-config import": "btn_boards_ssh",            # its dialog's Import from ~/.ssh/config
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
BUILD_MENU_TITLES = ("Build", "Bauen")


def _after(menubar):
    """The menu the Boards menu follows: the Build menu, else the first menu (File)."""
    build = place.find_menu(menubar, place.MENU_BUILD, BUILD_MENU_TITLES)
    if build is not None:
        return build
    menus = place.menus(menubar)
    return menus[0] if menus else None


def install(window, menubar) -> None:
    """Adds the Boards menu with Boards and Devices... (window.act_boards_devices); finds an existing Boards menu by
    its object name."""
    menu = place.find_menu(menubar, MENU_NAME)
    if menu is None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(ui_text("boards.menu"), menubar)
        menu.setObjectName(MENU_NAME)
        place.place_menu_after(menubar, _after(menubar), menu)
    action = place.new_action(window, ACTION_NAME, ui_text("boards.menu.devices"), lambda: open_boards_window(window))
    menu.addAction(action)
    window.menu_boards = menu
    window.act_boards_devices = action


def open_boards_window(window):
    """Shows the Boards window of the main window (fresh data each time it opens)."""
    from orchestrator.gui.boards_window import open_boards
    return open_boards(window)


__all__ = ["ACTION_NAME", "CLI_PATHS", "MACHINE_ONLY", "MENU_NAME", "WINDOW_OBJECTS", "install",
           "open_boards_window"]
