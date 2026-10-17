#!/usr/bin/env python3
"""
Finding and placing the main window's menus and menu entries: ONE helper for every area module of
orchestrator/gui/menus/ (the GUI parity wave built this lookup once per chain; the final integration keeps this one).

A menu is found by its object name first (the main window names its menus: menu_file, menu_security, menu_view,
menu_tools, menu_help; the area modules name theirs: menu_build, menu_boards, menu_ditto), then by one of its titles
(with any "&" mnemonic removed), so a translated title never hides a menu. An entry of a menu is found by its text.
"""

from typing import Any, Callable, Iterable, List, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

MENU_FILE = "menu_file"
MENU_BUILD = "menu_build"
MENU_BOARDS = "menu_boards"
MENU_DITTO = "menu_ditto"
MENU_SECURITY = "menu_security"
MENU_VIEW = "menu_view"
MENU_TOOLS = "menu_tools"
MENU_HELP = "menu_help"


def _clean(text: str) -> str:
    return (text or "").replace("&", "")


def menus(menubar: Any) -> List[QMenu]:
    """The menus of the menu bar, in their order."""
    return [action.menu() for action in menubar.actions() if action.menu() is not None]


def find_menu(menubar: Any, name: Optional[str] = None, titles: Iterable[str] = ()) -> Optional[QMenu]:
    """The menu of the menu bar with the object name `name`, else the first one titled one of `titles`; None when
    there is none. A menu found by its title gets the object name (so the next lookup finds it by name)."""
    menus_ = menus(menubar)
    if name:
        for menu in menus_:
            if menu.objectName() == name:
                return menu
    wanted = {_clean(title) for title in titles if title}
    for menu in menus_:
        if _clean(menu.title()) in wanted:
            if name and not menu.objectName():
                menu.setObjectName(name)
            return menu
    return None


def menu_or_new(menubar: Any, name: str, title: str, titles: Iterable[str] = ()) -> QMenu:
    """find_menu(name, titles + title), else a new menu `title` (object name `name`) at the end of the menu bar."""
    menu = find_menu(menubar, name, (title, *titles))
    if menu is None:
        menu = QMenu(title, menubar)
        menu.setObjectName(name)
        menubar.addMenu(menu)
    return menu


def menu_holding(menubar: Any, action: Any) -> Optional[QMenu]:
    """The menu of the menu bar that holds `action` (None for None or an action in no menu)."""
    if action is None:
        return None
    for entry in menubar.actions():
        menu = entry.menu()
        if menu is not None and action in menu.actions():
            return menu
    return None


def entry(menu: Optional[QMenu], text: str) -> Optional[QAction]:
    """The entry of `menu` that reads `text` (None when there is none)."""
    if menu is None:
        return None
    return next((action for action in menu.actions() if action.text() == text), None)


def name_entry(action: Optional[QAction], name: str) -> Optional[QAction]:
    """Gives an existing entry the object name `name` for CLI_PATHS; a name it has already stays. Returns it."""
    if action is not None and not action.objectName():
        action.setObjectName(name)
    return action


def new_action(window: Any, name: str, text: str, slot: Callable[[], Any]) -> QAction:
    """A menu entry `text` with the object name `name` that calls `slot()` (without Qt's checked flag)."""
    action = QAction(text, window)
    action.setObjectName(name)
    action.triggered.connect(lambda _checked=False: slot())
    return action


def insert_after(menu: QMenu, anchor: Optional[QAction], action: QAction) -> None:
    """Puts `action` right after `anchor` in `menu`; at the end when the anchor is missing or the last entry."""
    actions = menu.actions()
    if anchor is not None and anchor in actions and actions.index(anchor) + 1 < len(actions):
        menu.insertAction(actions[actions.index(anchor) + 1], action)
    else:
        menu.addAction(action)


def insert_before(menu: QMenu, anchor: Optional[QAction], action: QAction) -> None:
    """Puts `action` right before `anchor` in `menu`; at the end when the anchor is missing."""
    if anchor is not None and anchor in menu.actions():
        menu.insertAction(anchor, action)
    else:
        menu.addAction(action)


def place_menu_after(menubar: Any, after: Optional[QMenu], menu: QMenu) -> None:
    """Puts `menu` into the menu bar right after the menu `after`; first when `after` is None, at the end when it is
    the last one."""
    actions = menubar.actions()
    if after is None:
        if actions:
            menubar.insertMenu(actions[0], menu)
        else:
            menubar.addMenu(menu)
        return
    index = actions.index(after.menuAction()) + 1 if after.menuAction() in actions else len(actions)
    if index < len(actions):
        menubar.insertMenu(actions[index], menu)
    else:
        menubar.addMenu(menu)


def place_menu_before(menubar: Any, before: Optional[QMenu], menu: QMenu) -> None:
    """Puts `menu` into the menu bar right before the menu `before`; at the end when `before` is None."""
    if before is not None and before.menuAction() in menubar.actions():
        menubar.insertMenu(before.menuAction(), menu)
    else:
        menubar.addMenu(menu)


__all__ = ["MENU_BOARDS", "MENU_BUILD", "MENU_DITTO", "MENU_FILE", "MENU_HELP", "MENU_SECURITY", "MENU_TOOLS",
           "MENU_VIEW", "entry", "find_menu", "insert_after", "insert_before", "menu_holding", "menu_or_new", "menus",
           "name_entry", "new_action", "place_menu_after", "place_menu_before"]
