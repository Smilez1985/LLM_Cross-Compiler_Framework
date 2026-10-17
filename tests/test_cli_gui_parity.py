"""Every person-facing command and option of the command line has a mouse path in the GUI (W1', decision of
2026-10-16; round 9 U2: the mouse path is for the person, many people never use a terminal).

The test walks the whole click command tree of orchestrator.cli (every command, subcommand and option that is not
hidden) and reads the tables of every module in orchestrator/gui/menus/:

- CLI_PATHS: command path (e.g. "deploy devices remove", "mojo --board") -> objectName of the QAction or widget that
  does it by mouse;
- MACHINE_ONLY: command path -> why it is for scripts only (machine output, a switch that skips a question, ...);
- ARGUMENT_PATHS: "command ARGUMENT" -> the object for a command whose argument makes it another action;
- WINDOW_OBJECTS / TAB_OBJECTS: object -> the menu action that opens its window / the main window's tab it lives in
  (PANEL_OBJECTS: the provider to select in that window first).

It fails when a person-facing command or option has neither a mouse path nor a machine-only reason, when a table
names a command that does not exist, and, in an offscreen main window, when a named object does not exist or a QAction
cannot be reached from the menu bar (window and tab objects through the window or tab their entry opens).

A group that only lists its commands (click.Group without invoke_without_command) needs no entry; its commands do.
A new CLI command therefore needs its mouse path and a CLI_PATHS entry, or a MACHINE_ONLY reason (CONTRIBUTING.md)."""

import importlib
import pkgutil
import time
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional

import click
import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

TABLES = ("CLI_PATHS", "MACHINE_ONLY", "ARGUMENT_PATHS", "WINDOW_OBJECTS", "TAB_OBJECTS", "PANEL_OBJECTS")


# --- the command tree ------------------------------------------------------------------------------------------------

def option_names(param: click.Option) -> List[str]:
    """The long names of an option (a flag pair such as --docker/--no-docker has two)."""
    return [o for o in (*param.opts, *param.secondary_opts) if o.startswith("--")]


def cli_tree(root: Optional[click.Command] = None) -> Dict[str, dict]:
    """Every command path of the CLI that is not hidden: path -> {"kind": command | group | container | option,
    "names": the option's long names, "arguments": the command's argument names}. A group that only lists its
    commands is a "container" and needs no entry."""
    from orchestrator import cli as cli_module
    tree: Dict[str, dict] = {}

    def walk(command: click.Command, prefix: str) -> None:
        if prefix:
            if isinstance(command, click.Group):
                kind = "group" if command.invoke_without_command else "container"
            else:
                kind = "command"
            tree[prefix] = {"kind": kind, "names": [], "arguments": [p.name.upper() for p in command.params
                                                                    if isinstance(p, click.Argument)]}
        for param in command.params:
            if not isinstance(param, click.Option) or param.hidden:
                continue
            names = option_names(param)
            if not names or "--help" in names:
                continue
            tree[f"{prefix} {names[0]}".strip()] = {"kind": "option", "names": names, "arguments": []}
        for name, sub in getattr(command, "commands", {}).items():
            if not getattr(sub, "hidden", False):
                walk(sub, f"{prefix} {name}".strip())

    walk(root or cli_module.cli, "")
    return tree


def entry_keys(path: str, node: dict) -> List[str]:
    """The table keys that cover `path`: the path itself, or for an option any of its long names."""
    if node["kind"] != "option":
        return [path]
    command = path.rsplit(" ", 1)[0] if " " in path else ""
    return [f"{command} {name}".strip() for name in node["names"]]


# --- the menu modules ------------------------------------------------------------------------------------------------

def area_modules() -> List[object]:
    """Every module of orchestrator/gui/menus/ that names mouse paths (the helpers have no CLI_PATHS)."""
    import orchestrator.gui.menus as package
    modules = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        if hasattr(module, "CLI_PATHS"):
            modules.append(module)
    return modules


def collect(modules: Iterable[object]) -> SimpleNamespace:
    """The tables of all area modules merged; `owners` names the modules a key comes from."""
    merged = {table: {} for table in TABLES}
    owners: Dict[str, Dict[str, List[str]]] = {table: {} for table in TABLES}
    for module in modules:
        name = module.__name__.rsplit(".", 1)[-1]
        for table in TABLES:
            for key, value in (getattr(module, table, None) or {}).items():
                merged[table][key] = value
                owners[table].setdefault(key, []).append(name)
    return SimpleNamespace(**{table.lower(): merged[table] for table in TABLES}, owners=owners)


def missing_paths(tree: Dict[str, dict], tables: SimpleNamespace) -> List[str]:
    """The person-facing paths with neither a mouse path nor a machine-only reason."""
    covered = set(tables.cli_paths) | set(tables.machine_only)
    return [path for path, node in tree.items()
            if node["kind"] != "container" and not covered & set(entry_keys(path, node))]


def table_problems(tree: Dict[str, dict], tables: SimpleNamespace) -> List[str]:
    """Entries naming no command, a path that is both a mouse path and machine-only, a key two modules claim, a
    machine-only entry without a reason, an argument path without that argument."""
    problems = []
    known = {key for path, node in tree.items() for key in entry_keys(path, node)}

    def names_options_together(key: str) -> bool:   # e.g. "tools permissions --reset --caller"
        words = key.split()
        first = next((i for i, w in enumerate(words) if w.startswith("--")), None)
        if first is None or first == 0:
            return False
        command = " ".join(words[:first])
        return command in tree and all(f"{command} {w}" in known for w in words[first:])

    for table in ("cli_paths", "machine_only"):
        for key in getattr(tables, table):
            if key not in known and not names_options_together(key):
                problems.append(f"{table.upper()} names {key!r}, which is no command or option of the CLI")
    for key in set(tables.cli_paths) & set(tables.machine_only):
        problems.append(f"{key!r} is both a mouse path and machine-only")
    for table in ("CLI_PATHS", "MACHINE_ONLY", "ARGUMENT_PATHS"):
        for key, modules in tables.owners[table].items():
            if len(modules) > 1:
                problems.append(f"{key!r} is in {table} of {', '.join(modules)}: one module owns a path")
    for key, reason in tables.machine_only.items():
        if not isinstance(reason, str) or len(reason.split()) < 3:
            problems.append(f"MACHINE_ONLY {key!r} has no reason")
    for key in tables.argument_paths:
        command, _, argument = key.rpartition(" ")
        if command not in tree or argument not in tree[command]["arguments"]:
            problems.append(f"ARGUMENT_PATHS names {key!r}, but {command!r} has no argument {argument}")
    for name, value in tables.cli_paths.items():
        if not value or not isinstance(value, str):
            problems.append(f"CLI_PATHS {name!r} names no object")
    return problems


# --- the walk and the tables -----------------------------------------------------------------------------------------

def test_every_person_facing_command_and_option_has_a_mouse_path_or_a_reason():
    tree, tables = cli_tree(), collect(area_modules())
    assert len(tree) > 250, "the walk reached the whole command tree"
    missing = missing_paths(tree, tables)
    assert not missing, "no mouse path and no MACHINE_ONLY reason:\n" + "\n".join(missing)


def test_every_table_entry_names_a_real_command_once():
    tree, tables = cli_tree(), collect(area_modules())
    problems = table_problems(tree, tables)
    assert not problems, "\n".join(problems)


def test_the_area_modules_have_the_shared_shape():
    modules = {m.__name__.rsplit(".", 1)[-1]: m for m in area_modules()}
    assert {"boards", "build", "deploy", "ditto_models", "ditto_runtime", "existing", "knowledge", "mojo", "signin",
            "tools_mcp"} <= set(modules)
    for name, module in modules.items():
        for table in TABLES:
            assert isinstance(getattr(module, table, {}), dict), (name, table)
        if name != "existing":
            assert callable(getattr(module, "install", None)), f"menus/{name}.py has no install(window, menubar)"
    assert not hasattr(modules["existing"], "install"), "existing.py only names what the main window wires itself"


def test_the_check_bites():
    """Removing one entry makes the walk fail; a stale or doubled entry is found."""
    tree, tables = cli_tree(), collect(area_modules())
    path = "deploy devices remove"
    assert path in tables.cli_paths
    fewer = SimpleNamespace(**vars(tables))
    fewer.cli_paths = {k: v for k, v in tables.cli_paths.items() if k != path}
    assert missing_paths(tree, fewer) == [path]
    option = "ditto eval --min-pass"
    assert option in tables.machine_only
    fewer.machine_only = {k: v for k, v in tables.machine_only.items() if k != option}
    assert option in missing_paths(tree, fewer)
    stale = SimpleNamespace(**vars(tables))
    stale.cli_paths = dict(tables.cli_paths, **{"deploy devices forget": "act_boards_devices"})
    stale.machine_only = dict(tables.machine_only, **{"mojo": "machine-readable output for scripts"})
    problems = table_problems(tree, stale)
    assert any("'deploy devices forget'" in p for p in problems)
    assert any("'mojo' is both" in p for p in problems)


# --- the objects in the main window ----------------------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch, memory_keyring):
    """The main window offscreen; modal windows show without blocking, questions answer "No", Mojo runs a fake."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    import orchestrator.Core.mojo as mojo_package
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.Core.mojo import MojoReport
    from orchestrator.gui import main_window, plain

    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    class FakeMojo:
        def __init__(self, framework, checks=None, contact=()):
            self.framework = framework

        def run(self):
            return MojoReport([])

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(mojo_package, "Mojo", FakeMojo)
    monkeypatch.setattr(mojo_package, "offers", lambda framework, report: [])
    monkeypatch.setattr(QDialog, "exec", lambda self: (self.show(), 0)[1])
    monkeypatch.setattr(plain, "_exec", lambda box: QMessageBox.No)
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.No))
    real_open = main_window.MainOrchestrator._open_tool_permissions
    first_runs = []

    def open_permissions(self, first_run=False):
        if first_run:   # the first-start question is the same window; the test opens it from the menu
            first_runs.append(True)
            return None
        return real_open(self)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", open_permissions)
    win = main_window.MainOrchestrator(REPO_ROOT)
    qapp.processEvents()
    yield win
    win.update_timer.stop()
    for child in win.findChildren(QDialog):
        child.close()
    win.close()
    qapp.processEvents()


def menu_bar_actions(win) -> list:
    """Every QAction reachable from the menu bar, submenus included."""
    found, pending = [], [a.menu() for a in win.menuBar().actions() if a.menu() is not None]
    while pending:
        menu = pending.pop()
        for action in menu.actions():
            found.append(action)
            if action.menu() is not None:
                pending.append(action.menu())
    return found


def reachable_in(container, action) -> bool:
    """`action` can be clicked inside `container`: a menu of one of its buttons holds it (submenus included), or one
    of its widgets shows it (e.g. a tool button)."""
    from PySide6.QtWidgets import QAbstractButton, QToolButton, QWidget
    pending = [b.menu() for b in container.findChildren(QAbstractButton) if hasattr(b, "menu") and b.menu()]
    seen = set()
    while pending:
        menu = pending.pop()
        if id(menu) in seen:
            continue
        seen.add(id(menu))
        for item in menu.actions():
            if item is action:
                return True
            if item.menu() is not None:
                pending.append(item.menu())
    widgets = [container, *container.findChildren(QWidget)]
    return any(action in w.actions() and (not isinstance(w, QToolButton) or w.isVisibleTo(container))
               for w in widgets)


def object_problems(qapp, win, tables: SimpleNamespace, names: Optional[Iterable[str]] = None,
                    stats: Optional[dict] = None) -> List[str]:
    """What is wrong with the named objects in the main window `win` (all of them, or `names`); `stats` gets how many
    objects were checked, how many windows were opened and how many objects were found in them."""
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

    wanted = set(names) if names is not None else (set(tables.cli_paths.values()) | set(tables.argument_paths.values())
                                                   | set(tables.window_objects) | set(tables.tab_objects))
    in_menus = menu_bar_actions(win)
    problems, opened = [], {}

    def open_window(opener: str) -> List[QWidget]:
        if opener not in opened:
            # The widgets that existed before are held while comparing: the id of a freed wrapper can be given to a
            # new one, and the window the entry opens would then count as old.
            existing = [*QApplication.topLevelWidgets(), *win.findChildren(QWidget)]
            before = set(map(id, existing))
            action = win.findChild(QAction, opener)
            action.trigger()
            qapp.processEvents()
            opened[opener] = [w for w in {*QApplication.topLevelWidgets(), *win.findChildren(QWidget)}
                              if id(w) not in before and w.isWindow() and w.isVisible()]
            del existing
        return opened[opener]

    tabs = [t for t in win.findChildren(QTabWidget)]
    for name in sorted(wanted):
        if name in tables.window_objects:
            opener = tables.window_objects[name]
            action = win.findChild(QAction, opener)
            if action is None or action not in in_menus:
                problems.append(f"{name}: its window's entry {opener} is not in the menu bar")
                continue
            windows = open_window(opener)
            if not windows:
                problems.append(f"{name}: {opener} opened no window")
                continue
            if name in tables.panel_objects:
                for w in windows:
                    if hasattr(w, "select_provider"):
                        w.select_provider(tables.panel_objects[name])
                        panel = getattr(w, "panel", None)
                        for _ in range(500):
                            if panel is None or not getattr(panel, "busy", False):
                                break
                            qapp.processEvents()
                            time.sleep(0.005)
                qapp.processEvents()
            found = [w.findChild(QObject, name) for w in windows]
            found = [f for f in found if f is not None]
            if not found:
                kinds = ", ".join(sorted(type(w).__name__ for w in windows))
                problems.append(f"{name}: not in the window {opener} opens ({kinds})")
                continue
            if stats is not None:
                stats["in_windows"] = stats.get("in_windows", 0) + 1
            if isinstance(found[0], QAction) and not any(reachable_in(w, found[0]) for w in windows):
                problems.append(f"{name}: no button or menu of the window {opener} opens shows it")
        elif name in tables.tab_objects:
            tab = getattr(win, tables.tab_objects[name], None)
            if tab is None or not any(t.indexOf(tab) >= 0 for t in tabs):
                problems.append(f"{name}: its tab {tables.tab_objects[name]} is no tab of the main window")
                continue
            found = tab.findChild(QObject, name)
            if found is None:
                problems.append(f"{name}: not in the tab {tables.tab_objects[name]}")
            elif isinstance(found, QAction) and not reachable_in(tab, found):
                problems.append(f"{name}: no button or menu of the tab {tables.tab_objects[name]} shows it")
        else:
            found = win.findChild(QObject, name)
            if found is None:
                problems.append(f"{name}: not in the main window (and no window or tab is named for it)")
            elif isinstance(found, QAction) and found not in in_menus:
                problems.append(f"{name}: a QAction the menu bar does not reach")
    if stats is not None:
        stats.update(objects=len(wanted), windows=len([o for o in opened.values() if o]))
    return problems


def test_every_named_object_exists_and_every_action_is_reachable(qapp, window):
    tables = collect(area_modules())
    stats = {}
    problems = object_problems(qapp, window, tables, stats=stats)
    assert not problems, "\n".join(problems)
    openers = set(tables.window_objects.values())
    assert stats["windows"] == len(openers) >= 15, (stats, sorted(openers))
    assert stats["in_windows"] >= 100 and stats["objects"] >= 150, stats


def test_every_menu_once_in_the_design_order_with_every_entry_once(window):
    from orchestrator.gui.menus import place
    menus = place.menus(window.menuBar())
    names = [m.objectName() for m in menus]
    assert names == [place.MENU_FILE, place.MENU_BUILD, place.MENU_BOARDS, place.MENU_DITTO, place.MENU_SECURITY,
                     place.MENU_VIEW, place.MENU_TOOLS, place.MENU_HELP], names
    named = [a.objectName() for a in menu_bar_actions(window) if a.objectName()]
    assert len(named) == len(set(named)), named
    for menu in menus:
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert len(texts) == len(set(texts)), (menu.title(), texts)


def test_a_removed_menu_entry_is_found(qapp, window):
    """The object check bites: an entry taken out of the menu bar is unreachable, and so is its window."""
    tables = collect(area_modules())
    window.menu_boards.removeAction(window.act_boards_devices)
    problems = object_problems(qapp, window, tables, names=["act_boards_devices", "btn_boards_add"])
    assert problems == ["act_boards_devices: a QAction the menu bar does not reach",
                        "btn_boards_add: its window's entry act_boards_devices is not in the menu bar"]
    ghost = SimpleNamespace(**vars(tables))
    ghost.cli_paths = dict(tables.cli_paths, **{"trash restore": "btn_trash_gone"})
    ghost.window_objects = dict(tables.window_objects, btn_trash_gone="act_file_trash")
    assert object_problems(qapp, window, ghost, names=["btn_trash_gone"]) == [
        "btn_trash_gone: not in the window act_file_trash opens (TrashDialog)"]
