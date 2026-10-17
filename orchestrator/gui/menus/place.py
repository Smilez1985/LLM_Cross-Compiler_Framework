#!/usr/bin/env python3
"""
Finding and placing the main window's menus and menu entries: ONE helper for every area module of
orchestrator/gui/menus/ (the GUI parity wave built this lookup once per chain; the final integration keeps this one).

A menu is found by its object name first (the main window names its menus: menu_file, menu_ditto, menu_security,
menu_view, menu_tools, menu_help; the area modules name theirs: menu_build, menu_boards), then by one of its titles
(with any "&" mnemonic removed), so a translated title never hides a menu. An entry is found by its object name first
(anywhere in the menu bar, submenus included), its text only as a fallback (wave 6: the labels are translated).

arrange() puts every menu and entry where MENU_LAYOUT (orchestrator/utils/next_steps/common.py) says - the one table
of the menu structure the mouse paths of the next steps are built from; retranslate_menus() gives the menus, their
entries and the main window's tabs the texts of another language.
"""

import importlib
from typing import Any, Callable, Dict, Iterable, List, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from orchestrator.utils.localization import ui_text

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


def all_menus(menubar: Any) -> List[QMenu]:
    """Every menu of the menu bar and every submenu, menus first."""
    found, pending = [], list(menus(menubar))
    while pending:
        menu = pending.pop(0)
        found.append(menu)
        pending.extend(a.menu() for a in menu.actions() if a.menu() is not None)
    return found


def menu_holding(menubar: Any, action: Any) -> Optional[QMenu]:
    """The menu (or submenu) of the menu bar that holds `action` (None for None or an action in no menu)."""
    if action is None:
        return None
    for menu in all_menus(menubar):
        if action in menu.actions():
            return menu
    return None


def find_entry(menubar: Any, name: str) -> Optional[QAction]:
    """The entry with the object name `name` anywhere in the menu bar (None when there is none)."""
    if not name or menubar is None:
        return None
    for menu in all_menus(menubar):
        for action in menu.actions():
            if action.objectName() == name:
                return action
    return None


def entry(menu: Optional[QMenu], text: str, name: str = "", menubar: Any = None) -> Optional[QAction]:
    """The entry with the object name `name` (anywhere in `menubar` when given, else in `menu`), else the entry of
    `menu` that reads `text` (None when there is none)."""
    if name:
        found = find_entry(menubar, name) if menubar is not None else None
        if found is None and menu is not None:
            found = next((a for a in menu.actions() if a.objectName() == name), None)
        if found is not None:
            return found
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


# --- the menu structure of MENU_LAYOUT (wave 6, DECISIONS round 10 Q2 and V2) --------------------------------------

def _layout_menus(layout) -> Dict[str, tuple]:
    found: Dict[str, tuple] = {}

    def walk(spec):
        found[spec[0]] = spec
        for item in spec[2]:
            if isinstance(item, tuple) and len(item) == 3 and isinstance(item[2], tuple):
                walk(item)
    for spec in layout:
        walk(spec)
    return found


def arrange(menubar: Any, layout: Optional[tuple] = None, lang: Optional[str] = None) -> None:
    """Puts the menus and entries of the menu bar where MENU_LAYOUT says: the menus in its order (others after them),
    each menu's entries, separators and submenus in its order, every entry found anywhere in the menu bar by its object
    name and moved out of the menu it was in. An entry the layout names that does not exist (yet) is skipped; an entry
    a menu holds that the layout does not name stays in that menu, after the named ones. Menus and submenus the layout
    names get their title from its keys (created when missing)."""
    from orchestrator.utils.next_steps import menu_layout
    layout = tuple(layout if layout is not None else menu_layout())
    specs = _layout_menus(layout)
    existing = {m.objectName(): m for m in all_menus(menubar) if m.objectName()}
    actions: Dict[str, QAction] = {}
    for menu in all_menus(menubar):
        for action in menu.actions():
            if action.objectName() and action.menu() is None:
                actions.setdefault(action.objectName(), action)
    before = {id(menu): [a for a in menu.actions() if not a.isSeparator()] for menu in all_menus(menubar)}
    built: Dict[str, QMenu] = {}

    def menu_for(name: str, parent: Any) -> QMenu:
        menu = existing.get(name) or built.get(name)
        if menu is None:
            menu = QMenu(ui_text(specs[name][1], lang), parent)
            menu.setObjectName(name)
        built[name] = menu
        menu.setTitle(ui_text(specs[name][1], lang))
        return menu

    wanted: Dict[str, List[Any]] = {}

    def plan(spec, parent):
        menu = menu_for(spec[0], parent)
        items: List[Any] = []
        for item in spec[2]:
            if item == "-":
                items.append("-")
            elif isinstance(item, tuple) and len(item) == 3 and isinstance(item[2], tuple):
                items.append(plan(item, menu).menuAction())
            elif isinstance(item, tuple) and item[0] in actions:
                items.append(actions[item[0]])
        wanted[spec[0]] = items
        return menu

    top = [plan(spec, menubar) for spec in layout]
    placed = {id(a) for items in wanted.values() for a in items if not isinstance(a, str)}
    every: Dict[int, QMenu] = {}
    for menu in [*all_menus(menubar), *built.values()]:
        every.setdefault(id(menu), menu)
    for menu in every.values():
        old = before.get(id(menu), [])
        for action in list(menu.actions()):
            menu.removeAction(action)
            if action.isSeparator() and action.parent() is menu:
                action.deleteLater()
        items = list(wanted.get(menu.objectName(), []))
        rest = [a for a in old if id(a) not in placed]
        if items and rest:
            items.append("-")
        items += rest
        previous: Any = "-"
        for item in items:
            if isinstance(item, str):
                if not isinstance(previous, str):
                    menu.addSeparator()
                previous = item
            else:
                menu.addAction(item)
                previous = item
        now = menu.actions()
        if now and now[-1].isSeparator():
            menu.removeAction(now[-1])
    others = [m for m in menus(menubar) if m not in top]
    for menu in [*top, *others]:
        menubar.removeAction(menu.menuAction())
        menubar.addAction(menu.menuAction())


# The main window's tabs: attribute -> (the symbol in front, the title's ui_text key).
TAB_TITLES = {
    "builder_tab": ("🏗️", "main.tab.builder"),
    "deployment_window": ("🚀", "main.tab.deployment"),
    "tuning_tab": ("🛠️", "main.tab.tuning"),
    "models_tab": ("🧠", "main.tab.models"),
}


def retitle_tabs(window: Any, lang: Optional[str] = None) -> None:
    """The main window's tab titles in `lang`: the symbol in front, the text alone as the tab's accessible name."""
    tabs = getattr(window, "tabs", None)
    if tabs is None:
        return
    for attribute, (symbol, key) in TAB_TITLES.items():
        widget = getattr(window, attribute, None)
        index = tabs.indexOf(widget) if widget is not None else -1
        if index < 0:
            continue
        text = ui_text(key, lang)
        tabs.setTabText(index, f"{symbol} {text}")
        tabs.tabBar().setAccessibleTabName(index, text)


def retranslate_menus(window: Any, lang: Optional[str] = None) -> None:
    """The menus, their entries and the main window's tabs in `lang` (LocalizationManager.language_changed; the shell
    chain's language switch calls it): every menu and entry the mouse labels name, found by its object name."""
    from orchestrator.utils.next_steps import mouse_labels
    labels = mouse_labels()
    for menu in all_menus(window.menuBar()):
        if menu.objectName() in labels:
            menu.setTitle(ui_text(labels[menu.objectName()][1][0], lang))
        for action in menu.actions():
            name = action.objectName()
            if name in labels and action.menu() is None:
                action.setText(ui_text(labels[name][1][0], lang))
    retitle_tabs(window, lang)


def optional_module(name: str) -> Any:
    """orchestrator/gui/menus/<name>.py when a later chain has added it (an install hook: no shared list to edit),
    else None."""
    full = f"{__package__}.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name != full:   # the module exists, but something it imports does not
            raise
        return None


__all__ = ["MENU_BOARDS", "MENU_BUILD", "MENU_DITTO", "MENU_FILE", "MENU_HELP", "MENU_SECURITY", "MENU_TOOLS",
           "MENU_VIEW", "TAB_TITLES", "all_menus", "arrange", "entry", "find_entry", "find_menu", "insert_after",
           "insert_before", "menu_holding", "menu_or_new", "menus", "name_entry", "new_action", "optional_module",
           "place_menu_after", "place_menu_before", "retitle_tabs", "retranslate_menus"]
