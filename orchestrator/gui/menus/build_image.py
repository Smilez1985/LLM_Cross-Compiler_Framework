#!/usr/bin/env python3
"""
Build > Build Container Images... (DECISIONS round 36): the mouse path of `llm-cli build image plan / sources / export
/ import` (orchestrator/gui/build_image_window.py over orchestrator/Core/build_image.py, the same functions as the CLI).

CLI_PATHS names, for every command and option of this area, the object that does it by mouse; WINDOW_OBJECTS names the
menu action that opens the window those objects live in; MACHINE_ONLY lists the switches for scripts. The window loads
when the entry is first used, not with the main window (lazy loading).
"""

from typing import Dict

from orchestrator.gui.menus import place
from orchestrator.utils.localization import current_language, ui_text

CLI_PATHS: Dict[str, str] = {
    "build image plan": "btn_bimg_plan",
    "build image plan --gpu": "chk_bimg_gpu",
    "build image sources": "act_build_images",           # the window shows the build sources
    "build image sources --apt-mirror": "txt_bimg_apt_mirror",
    "build image sources --apt-proxy": "txt_bimg_apt_proxy",
    "build image sources --pip-index": "txt_bimg_pip_index",
    "build image export": "btn_bimg_export",
    "build image export --to": "btn_bimg_export",         # its file dialog chooses the file
    "build image export --image": "list_bimg_images",
    "build image import": "btn_bimg_import",
    "build image import --sha256": "txt_bimg_sha256",
    "build image import --target": "combo_bimg_target",
}
WINDOW_OBJECTS: Dict[str, str] = {name: "act_build_images" for name in (
    "btn_bimg_plan", "chk_bimg_gpu", "txt_bimg_apt_mirror", "txt_bimg_apt_proxy", "txt_bimg_pip_index",
    "btn_bimg_export", "list_bimg_images", "btn_bimg_import", "txt_bimg_sha256", "combo_bimg_target")}
MACHINE_ONLY: Dict[str, str] = {
    "build image plan --json": "machine-readable output for scripts; the window shows the same plan",
    "build image import --yes": "skips the question before the image is loaded, for scripts; the window always asks",
}


def install(window, menubar) -> None:
    """Adds Build > Build Container Images... after Build History... (window.act_build_images)."""
    lang = current_language()
    menu = place.find_menu(menubar, place.MENU_BUILD, (ui_text("bld.menu", lang),))
    action = place.new_action(window, "act_build_images", ui_text("bimg.menu", lang), lambda: open_window(window))
    if menu is not None:
        menu.addAction(action)
    window.act_build_images = action


def open_window(window):
    from orchestrator.gui.build_image_window import open_build_images
    from orchestrator.gui.plain import one_line, plain_box
    try:
        dialog = open_build_images(window)
    except Exception as e:  # noqa: BLE001 - plain text (SEC-M2)
        plain_box(window, "critical", ui_text("bimg.title"), one_line(str(e)))
        return None
    window.build_images_window = dialog
    return dialog


__all__ = ["CLI_PATHS", "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_window"]
