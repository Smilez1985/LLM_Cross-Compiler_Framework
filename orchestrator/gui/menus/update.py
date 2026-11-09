"""
Tools > Maintenance > Update Framework... (`llm-cli update` by mouse).

The entry checks the checkout the framework was installed from (orchestrator/utils/updater.py), asks with "No" as the
default, and then runs the update where the person sees the installer's questions: on Linux `llm-cli update --yes` in
a terminal (sudo asks for the password there), on Windows the installer in a console of its own, after which the
framework closes (the installer replaces the environment it runs from). An AI never starts an update.
"""

import platform
import shlex
import subprocess

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

TOOLS_MENU = "Tools"

CLI_PATHS: dict[str, str] = {
    "update": "act_tools_update",            # Tools > Maintenance > Update Framework...: checks, asks, updates
    "update --check": "act_tools_update",    # the question shows the new commits before anything changes
    "update --offline": "act_tools_update",  # the installer asks no question about it: --offline stays a CLI switch
}
MACHINE_ONLY: dict[str, str] = {
    "update --yes": "skips the question in scripts; the menu entry always asks",
}


def install(window, menubar) -> None:
    """Adds Tools > Maintenance > Update Framework... after Mojo Health Check... (window.act_update)."""
    menu = place.menu_or_new(menubar, place.MENU_TOOLS, TOOLS_MENU)
    action = place.new_action(window, "act_tools_update", ui_text("update.menu"), lambda: run_update(window))
    anchor = getattr(window, "act_mojo_health", None) or getattr(window, "act_images", None)
    place.insert_after(place.menu_holding(menubar, anchor) or menu, anchor, action)
    window.act_update = action


def run_update(window) -> bool:
    """Checks, asks, starts the update; True when it started."""
    from orchestrator.gui.plain import ask, plain_box
    from orchestrator.utils.updater import UpdateError, UpdateManager
    title = ui_text("update.title")
    manager = UpdateManager(window.framework.app_root)
    source = manager.source_root()
    if source is None:
        plain_box(window, "warning", title, ui_text("update.no_source", root=str(manager.app_root)))
        return False
    new = manager.check_for_updates()
    windows = platform.system() == "Windows"
    if not ask(window, title, ui_text("update.ask_windows" if windows else "update.ask", source=str(source), new=new)):
        return False
    try:
        if windows:
            manager.run()
            plain_box(window, "information", title, ui_text("update.started"))
            window.close()
            return True
        command = manager.terminal_command()
        if command is None:
            plain_box(window, "information", title,
                      ui_text("update.no_terminal", command=shlex.join(["llm-cli", "update"])))
            return False
        subprocess.Popen(command, cwd=str(manager.app_root))
    except (UpdateError, OSError) as e:
        plain_box(window, "critical", title, ui_text("update.failed", reason=str(e)))
        return False
    plain_box(window, "information", title, ui_text("update.started"))
    return True
