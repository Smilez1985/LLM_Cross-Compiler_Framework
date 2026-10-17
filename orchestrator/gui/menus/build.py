#!/usr/bin/env python3
"""
Menu wiring of the build chain of the GUI parity wave (W1': every action a person can start with the CLI is also
startable with the mouse):

  Build > Targets...        llm-cli targets list / targets validate   (orchestrator/gui/targets_window.py)
  Build > Build History...  llm-cli build status [--all] [REQUEST_ID] (orchestrator/gui/build_history_window.py)
  File > Settings...        llm-cli config show / config repos         (orchestrator/gui/settings_window.py)

The Build menu sits right after File; Settings... sits above File > Exit. Every window calls the same core function
as the CLI. CLI_PATHS names, for every command of this area, the QAction or button that starts it by mouse (its
objectName); tests/test_cli_gui_parity.py of the integrator checks that each exists in the main window.
"""

from typing import Dict, Optional

from PySide6.QtWidgets import QMenu

from orchestrator.gui.menus import place
from orchestrator.gui.plain import one_line, plain_box
from orchestrator.utils.localization import current_language, ui_text

# CLI command path -> objectName of the QAction or button that does it by mouse. Machine-only switches are in
# MACHINE_ONLY below.
CLI_PATHS: Dict[str, str] = {
    "targets list": "act_build_targets",
    "targets validate": "btn_builder_check_target",           # also Build > Targets... > Check target
    "module create": "act_tools_module_wizard",               # Tools > Create Hardware Module...
    "module create --name": "act_tools_module_wizard",
    "module create --arch": "act_tools_module_wizard",
    "module create --sdk": "act_tools_module_wizard",
    "module create --probe": "act_tools_module_wizard",       # its intro page: import a probe file
    "module generate-ai": "act_tools_module_wizard",          # its intro page: the AI import (Ditto)
    "module generate-ai --provider": "act_tools_module_wizard",   # "for this analysis only" provider
    "module generate-ai --model": "act_tools_module_wizard",      # "for this analysis only" model
    "build start": "btn_builder_start",
    "build start --model": "combo_builder_model",
    "build start --target": "combo_builder_target",
    "build start --format": "combo_builder_format",
    "build start --board": "combo_builder_board",
    "build start --quantization": "combo_builder_quantization",
    "build start --output": "act_file_output_folder",         # and the save dialog after every finished build
    "build start --optimization": "combo_builder_optimization",
    "build start --priority": "combo_builder_priority",
    "build start --follow": "panel_builder_progress",
    "build start --gpu": "chk_builder_gpu",
    "build start --imatrix": "chk_builder_imatrix",
    "build start --dataset": "combo_builder_dataset",
    "build save": "btn_deploy_save",                          # Deployment tab > Save...
    "build save --to": "btn_deploy_save",                     # its folder dialog chooses the folder
    "build status": "act_build_history",
    "build status --all": "act_build_history",
    "config show": "act_file_settings",
    "config output-folder": "act_file_output_folder",
    "config repos": "act_file_settings",
    "config repos --add": "act_file_settings",
    "config repos --remove": "act_file_settings",
}
# The switches of this area that are for scripts only, with the reason (as the other menu modules list theirs; the
# hidden, deprecated build start --parallel is not offered at all).
MACHINE_ONLY: Dict[str, str] = {
    "targets list --format": "json or yaml output for scripts; Build > Targets... shows the same rows as a table",
}


def install(window, menubar) -> None:
    """Adds the Build menu after File and File > Settings... above Exit; names the existing entries of this area."""
    lang = current_language()
    file_menu = place.menu_holding(menubar, getattr(window, "act_output_folder", None)) \
        or place.find_menu(menubar, place.MENU_FILE, (window.i18n.t("menu.file", "File"),))

    build_menu = QMenu(ui_text("bld.menu", lang), menubar)
    build_menu.setObjectName(place.MENU_BUILD)
    place.place_menu_after(menubar, file_menu, build_menu)
    window.menu_build = build_menu
    window.act_build_targets = place.new_action(window, "act_build_targets", ui_text("bld.menu.targets", lang),
                                                lambda: open_targets(window))
    window.act_build_history = place.new_action(window, "act_build_history", ui_text("bld.menu.history", lang),
                                                lambda: open_build_history(window))
    build_menu.addAction(window.act_build_targets)
    build_menu.addAction(window.act_build_history)

    window.act_file_settings = place.new_action(window, "act_file_settings", ui_text("bld.menu.settings", lang),
                                                lambda: open_settings(window))
    if file_menu is not None:
        place.insert_before(file_menu, place.entry(file_menu, window.i18n.t("menu.exit", "Exit"), "act_file_exit"),
                            window.act_file_settings)

    place.name_entry(getattr(window, "act_output_folder", None), "act_file_output_folder")
    tools = place.find_menu(menubar, place.MENU_TOOLS, ("Tools",))
    place.name_entry(place.entry(tools, "Create Hardware Module...", "act_tools_module_wizard", menubar),
                     "act_tools_module_wizard")


def _refresh_target_lists(window) -> None:
    builder = getattr(window, "builder_tab", None)
    if builder is not None:
        builder._refresh_targets()
    deployment = getattr(window, "deployment_window", None)
    if deployment is not None:
        deployment.refresh_data()


def open_targets(window, select: Optional[str] = None):
    """Build > Targets...: every target and the check of one (BuildService.list_targets, TargetManager.validate)."""
    from orchestrator.gui.targets_window import open_targets_dialog
    builder = getattr(window, "builder_tab", None)
    if select is None and builder is not None:
        select = builder.target_combo.currentData()
    return open_targets_dialog(window, window.framework, select=select,
                               on_reload=lambda: _refresh_target_lists(window))


def open_build_history(window):
    """Build > Build History...: every build of every framework process and its details (build_history)."""
    from orchestrator.gui.build_history_window import BuildHistoryDialog
    try:
        dialog = BuildHistoryDialog(window.framework, window)
    except Exception as e:  # noqa: BLE001 - e.g. no cache folder; plain text (SEC-M2)
        plain_box(window, "critical", ui_text("bld.history.title"), one_line(str(e)))
        return None
    dialog.exec()
    return dialog


def open_settings(window):
    """File > Settings...: the effective settings and the source repository overrides (settings_view)."""
    from orchestrator.gui.settings_window import SettingsDialog
    try:
        dialog = SettingsDialog(window.framework, window,
                                choose_output_folder=getattr(window, "_choose_output_folder", None))
    except Exception as e:  # noqa: BLE001 - plain text (SEC-M2)
        plain_box(window, "critical", ui_text("bld.settings.title"), one_line(str(e)))
        return None
    dialog.exec()
    return dialog


__all__ = ["CLI_PATHS", "MACHINE_ONLY", "install", "open_build_history", "open_settings", "open_targets"]
