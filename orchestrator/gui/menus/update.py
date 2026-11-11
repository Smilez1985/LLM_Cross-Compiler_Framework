"""
Tools > Maintenance > Update Framework... (`llm-cli update` by mouse): the entry opens the page Update of File >
Settings... (orchestrator/gui/update_page.py), where the person checks for new commits and starts the update.

run_update() is that page's Update Framework...: it checks the checkout the framework was installed from
(orchestrator/utils/updater.py), asks with "No" as the default, and then runs the update where the person sees the
installer's questions: on Linux `llm-cli update --yes` in a terminal (sudo asks for the password there), on Windows the
installer in a console of its own, after which the framework closes (the installer replaces the environment it runs
from). An AI never starts an update.
"""

import platform
import shlex
import subprocess
from pathlib import Path
from typing import Sequence

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

TOOLS_MENU = "Tools"

CLI_PATHS: dict[str, str] = {
    "update": "btn_update_run",              # File > Settings... > Update: Update Framework... checks, asks, updates
    "update --check": "btn_update_check",    # Check for updates: the new commits, nothing changes
    "update --offline": "chk_update_offline",  # the tested dependency set without resolving anew
}
# The objects of the Update page, with the menu entry that opens it (the parity test opens it).
WINDOW_OBJECTS: dict[str, str] = {"btn_update_run": "act_tools_update", "btn_update_check": "act_tools_update",
                                  "chk_update_offline": "act_tools_update"}
MACHINE_ONLY: dict[str, str] = {
    "update --yes": "skips the question in scripts; the menu entry always asks",
}


def install(window, menubar) -> None:
    """Adds Tools > Maintenance > Update Framework... after Mojo Health Check... (window.act_update)."""
    menu = place.menu_or_new(menubar, place.MENU_TOOLS, TOOLS_MENU)
    action = place.new_action(window, "act_tools_update", ui_text("update.menu"), lambda: open_update_page(window))
    anchor = getattr(window, "act_mojo_health", None) or getattr(window, "act_images", None)
    place.insert_after(place.menu_holding(menubar, anchor) or menu, anchor, action)
    window.act_update = action


def open_update_page(window):
    """Tools > Maintenance > Update Framework...: File > Settings... at its page Update."""
    from orchestrator.gui.menus.build import open_settings
    return open_settings(window, "update")


def app_root_of(window) -> Path:
    """The installation the update is for: the framework's own, else this checkout."""
    root = getattr(getattr(window, "framework", None), "app_root", None)
    if root is None:
        from orchestrator.Core.framework import APP_ROOT
        root = APP_ROOT
    return Path(root)


def run_update(window, flags: Sequence[str] = (), parent=None) -> bool:
    """Checks, asks, starts the update with the installer's `flags` (e.g. --offline); True when it started. `parent`
    holds the questions (the Update page); `window` is the main window, which closes on Windows."""
    from orchestrator.gui.plain import ask, plain_box
    from orchestrator.utils.updater import UpdateError, UpdateManager
    title = ui_text("update.title")
    holder = parent if parent is not None else window
    manager = UpdateManager(app_root_of(window))
    source = manager.source_root()
    if source is None:
        plain_box(holder, "warning", title, ui_text("update.no_source", root=str(manager.app_root)))
        return False
    new = manager.check_for_updates()
    windows = platform.system() == "Windows"
    if not ask(holder, title, ui_text("update.ask_windows" if windows else "update.ask", source=str(source), new=new)):
        return False
    flags = tuple(flags)
    try:
        if windows:
            manager.run(*flags)
            plain_box(holder, "information", title, ui_text("update.started"))
            if window is not None:
                window.close()
            return True
        command = manager.terminal_command(*flags)
        if command is None:
            plain_box(holder, "information", title,
                      ui_text("update.no_terminal", command=shlex.join(["llm-cli", "update", *flags])))
            return False
        subprocess.Popen(command, cwd=str(manager.app_root))
    except (UpdateError, OSError) as e:
        plain_box(holder, "critical", title, ui_text("update.failed", reason=str(e)))
        return False
    plain_box(holder, "information", title, ui_text("update.started"))
    return True


def offer_take_over(window) -> bool:
    """At start: what an earlier version wrote into the installation folder (Core/install_migration.py) is offered
    for moving into the person's own folders, the person's own items in one question and items that may belong to
    another account in a second one ("No" is the default of both). A no is kept (not asked at start again). True when
    something was moved."""
    from orchestrator.Core import install_migration
    from orchestrator.gui.plain import ask, plain_box
    config = getattr(getattr(window, "framework", None), "config", None)
    try:
        left = install_migration.pending(config)
    except Exception:  # noqa: BLE001 - the offer never stops the start
        return False
    if not left:
        return False
    title = ui_text("takeover.title")
    mine = [i for i in left if i.mine]
    theirs = [i for i in left if not i.mine]
    chosen = []
    for group, key in ((mine, "takeover.ask_mine"), (theirs, "takeover.ask_others")):
        if not group:
            continue
        if ask(window, title, ui_text(key, items="\n".join(install_migration.summary(group)))):
            chosen += group
        else:
            install_migration.decline(group)
    if not chosen:
        return False
    report = install_migration.take_over(chosen)
    plain_box(window, "warning" if report.failed else "information", title,
              ui_text("takeover.done", lines="\n".join(report.lines())))
    return True
