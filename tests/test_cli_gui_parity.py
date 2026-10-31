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

TABLES = ("CLI_PATHS", "MACHINE_ONLY", "ARGUMENT_PATHS", "WINDOW_OBJECTS", "TAB_OBJECTS", "PANEL_OBJECTS",
          "CLI_ONLY_LINES")


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
    for table in ("CLI_PATHS", "MACHINE_ONLY", "ARGUMENT_PATHS", "CLI_ONLY_LINES"):
        for key, modules in tables.owners[table].items():
            if len(modules) > 1:
                problems.append(f"{key!r} is in {table} of {', '.join(modules)}: one module owns a path")
    for key, reason in tables.machine_only.items():
        if not isinstance(reason, str) or len(reason.split()) < 3:
            problems.append(f"MACHINE_ONLY {key!r} has no reason")
    for key, reason in tables.cli_only_lines.items():
        command, _, label = key.rpartition(" ")
        if command not in tree or tree[command]["kind"] == "option" or not label.endswith(":"):
            problems.append(f"CLI_ONLY_LINES names {key!r}, which is no 'command Label:' of the CLI")
        if not isinstance(reason, str) or len(reason.split()) < 3:
            problems.append(f"CLI_ONLY_LINES {key!r} has no reason")
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


# --- lines of a command's output the GUI does not show yet -----------------------------------------------------------

# The lines `llm-cli scan` printed before probe 3.1.0 (stage C wave 3c); a line added since needs its GUI line or a
# CLI_ONLY_LINES reason.
SCAN_LINES_BEFORE_3_1 = ("System:", "CPU:", "GPU:", "NPU:", "Notes:")
# Lines the Builder tab's scan logs as well (probe 3.3.0's access facts: probe_service.access_line for both).
SCAN_LINES_IN_GUI = ("Access:",)


def scan_summary_labels() -> List[str]:
    """The label of every line `llm-cli scan` (local, --device, --import) prints for a board with every kind of fact:
    the Rockchip fixture with a Hailo-8L on PCIe (HAILO_PLAN 2.5)."""
    from orchestrator import cli as cli_module
    from orchestrator.runtime.profile import parse_profile
    text = (REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt").read_text(
        encoding="utf-8")
    assert "SUPPORTS_HAILO=OFF\n" in text
    text = text.replace("SUPPORTS_HAILO=OFF\n", "SUPPORTS_HAILO=ON\nHAILO_FAMILY=hailo8\nHAILO_ARCH=hailo8l\n"
                                                "HAILO_DEVICES=1\nHAILO_INTERFACE=pcie\n")
    from orchestrator.runtime.profile import HardwareProfile
    profile = parse_profile(text)
    values = dict(profile.values, PROBE_TOOL_VERSION="3.3.0", SSH_SERVER="dropbear", SUDO="nopasswd",
                  SSH_KEYGEN="none", PACKAGE_MANAGER="apt")   # probe 3.3.0's access facts
    profile = HardwareProfile(values, schema=profile.schema)
    return [line.split(" ", 1)[0] for line in cli_module._profile_summary(profile)]


def test_every_new_line_of_scan_has_a_gui_line_or_a_reason():
    """Probe 3.1.0 added the 'Hailo:' line to `llm-cli scan`; the Builder tab's scan has none yet, so it is listed
    CLI-only with its reason (gui/menus/deploy.py CLI_ONLY_LINES) until the Hailo wave brings the GUI line."""
    tables = collect(area_modules())
    labels = scan_summary_labels()
    assert "Hailo:" in labels and "NPU:" in labels and "Access:" in labels, labels
    unlisted = [label for label in labels if label not in SCAN_LINES_BEFORE_3_1 + SCAN_LINES_IN_GUI
                and f"scan {label}" not in tables.cli_only_lines]
    assert unlisted == [], "a new line of `llm-cli scan` without a GUI line or a CLI_ONLY_LINES reason"
    assert tables.cli_only_lines["scan Hailo:"] == "GUI line comes with the Hailo wave"
    stale = [key for key in tables.cli_only_lines if key.startswith("scan ") and key[len("scan "):] not in labels]
    assert stale == [], "CLI_ONLY_LINES names a line `llm-cli scan` does not print"


def test_the_gui_lines_are_logged_by_the_builder_tab():
    """Every line of SCAN_LINES_IN_GUI comes from one helper that the Builder tab's scan logs too."""
    source = (REPO_ROOT / "orchestrator" / "gui" / "builder_tab.py").read_text(encoding="utf-8")
    assert "probe_service import access_line" in source and "self._log(access_line)" in source
    cli_source = (REPO_ROOT / "orchestrator" / "cli.py").read_text(encoding="utf-8")
    assert "access_line(profile, \"en\")" in cli_source


def test_the_line_check_bites():
    tree, tables = cli_tree(), collect(area_modules())
    wrong = SimpleNamespace(**vars(tables))
    wrong.cli_only_lines = dict(tables.cli_only_lines, **{"scan --json Hailo:": "GUI line comes later",
                                                          "scan Hailo": "GUI line comes later",
                                                          "scan Notes:": "later"})
    problems = table_problems(tree, wrong)
    assert any("'scan --json Hailo:'" in p for p in problems)
    assert any("'scan Hailo'" in p for p in problems)
    assert any("CLI_ONLY_LINES 'scan Notes:' has no reason" in p for p in problems)


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


# --- the next steps' mouse paths (wave 6, chain a0; DECISIONS round 10 V2) -------------------------------------------

def test_every_next_step_names_a_real_command_object_tool_window_and_text():
    """Every catalogue step's command is a CLI_PATHS key whose object is the step's target, its tool is a known tool,
    its window a known window, and its texts exist in English and German with the same placeholders."""
    from orchestrator.tools.framework_tools import KNOWN_TOOLS
    from orchestrator.tools.tool_windows import WINDOW_IDS
    from orchestrator.utils.next_steps import catalogue_problems
    assert catalogue_problems(KNOWN_TOOLS, collect(area_modules()).cli_paths, WINDOW_IDS) == []


def live_text(obj) -> str:
    """What an object reads on screen (a menu's title, an entry's or a button's text)."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton, QMenu
    if isinstance(obj, QMenu):
        return obj.title()
    if isinstance(obj, (QAction, QAbstractButton)):
        return obj.text()
    return ""


def mouse_label_problems(qapp, win, lang: str) -> List[str]:
    """Where a mouse label of the next-step catalogue disagrees with the live main window in `lang`: an object that
    does not exist, a parent it does not sit in, or a label that is not the text on screen (menus, entries, buttons and
    tabs; a field's own label is not compared). Windows are opened through their entries."""
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication, QMenu, QWidget
    from orchestrator.gui.menus import place
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.next_steps import clean_label, mouse_labels, steps
    labels = mouse_labels()
    planned = {s.target for s in steps().values() if s.planned}
    tables = collect(area_modules())
    menubar = win.menuBar()
    problems, opened = [], {}

    def window_widgets(opener: str) -> List[QWidget]:
        if opener not in opened:
            existing = [*QApplication.topLevelWidgets(), *win.findChildren(QWidget)]
            before = set(map(id, existing))
            from PySide6.QtGui import QAction
            action = win.findChild(QAction, opener)
            if action is not None:
                action.trigger()
                qapp.processEvents()
            opened[opener] = [w for w in {*QApplication.topLevelWidgets(), *win.findChildren(QWidget)}
                              if id(w) not in before and w.isWindow()]
            del existing
        return opened[opener]

    def find(name: str, parent: Optional[str]):
        menu_entry = place.find_entry(menubar, name)
        if menu_entry is not None:
            return menu_entry
        menu = next((m for m in place.all_menus(menubar) if m.objectName() == name), None)
        if menu is not None:
            return menu
        if name in place.TAB_TITLES:
            return getattr(win, name, None)
        if parent in tables.window_objects.values() or (parent and parent.startswith("act_")):
            found = [w.findChild(QObject, name) for w in window_widgets(parent)]
            found = [f for f in found if f is not None]
            if found:
                return found[0]
        return win.findChild(QObject, name)

    for name, (parent, keys) in sorted(labels.items()):
        if name in planned:
            continue
        obj = find(name, parent)
        if obj is None:
            problems.append(f"{name}: not found (parent {parent})")
            continue
        if isinstance(obj, QMenu) or place.find_entry(menubar, name) is not None:
            holder = place.menu_holding(menubar, obj.menuAction() if isinstance(obj, QMenu) else obj)
            where = holder.objectName() if holder is not None else None
            if where != parent:
                problems.append(f"{name}: sits in {where}, its mouse label says {parent}")
        elif name in place.TAB_TITLES:
            index = win.tabs.indexOf(obj)
            text = win.tabs.tabText(index) if index >= 0 else ""
            if clean_label(text) != clean_label(ui_text(keys[0], lang)):
                problems.append(f"{name}: tab reads {text!r}, its label {ui_text(keys[0], lang)!r}")
            continue
        text = live_text(obj)
        if text and clean_label(text) not in {clean_label(ui_text(k, lang)) for k in keys}:
            problems.append(f"{name}: reads {text!r}, its mouse label says {ui_text(keys[0], lang)!r}")
    return problems


def test_the_mouse_paths_are_the_live_labels(qapp, window):
    """V2: a path a text names is built from the same labels the window shows; every mouse label of the catalogue
    names an object that exists where the label says, with the text on screen (English: every window; German: the
    menus, their entries and the tabs, which follow the language)."""
    from orchestrator.gui.menus import place
    from orchestrator.utils.next_steps import mouse_path
    assert mouse_label_problems(qapp, window, "en") == []
    place.retranslate_menus(window, "de")
    try:
        bar = window.menuBar()
        in_bar = {a.objectName() for m in place.all_menus(bar) for a in m.actions()}
        in_bar |= {m.objectName() for m in place.all_menus(bar)} | set(place.TAB_TITLES)
        # windows created in English keep their texts until they are opened again (the shell chain offers that)
        menu_problems = [p for p in mouse_label_problems(qapp, window, "de") if p.split(":")[0] in in_bar]
        assert menu_problems == []
        assert mouse_path("act_tools_permissions", "de") == "Ditto > Tool-Berechtigungen..."
    finally:
        place.retranslate_menus(window, "en")


def test_a_wrong_mouse_label_is_found(qapp, window, monkeypatch):
    """The check bites: a label that names another parent or another text is reported."""
    from orchestrator.utils import next_steps
    labels = dict(next_steps.mouse_labels())
    labels["act_mojo_health"] = ("menu_tools_knowledge", ("mojo.menu",))
    labels["btn_trash_restore"] = ("act_file_trash", ("trash.delete",))
    monkeypatch.setattr(next_steps, "mouse_labels", lambda: labels)
    problems = mouse_label_problems(qapp, window, "en")
    assert any(p.startswith("act_mojo_health: sits in menu_tools_maintenance") for p in problems), problems
    assert any(p.startswith("btn_trash_restore: reads") for p in problems), problems


def test_the_menus_follow_the_language_by_object_name(qapp, window):
    """Q2: translated titles with mnemonics, the entries found by their object names in any language."""
    from orchestrator.gui.menus import place
    titles = {m.objectName(): m.title() for m in place.menus(window.menuBar())}
    assert titles[place.MENU_TOOLS] == "&Tools" and titles[place.MENU_DITTO] == "&Ditto"
    place.retranslate_menus(window, "de")
    try:
        titles = {m.objectName(): m.title() for m in place.menus(window.menuBar())}
        assert titles[place.MENU_TOOLS] == "E&xtras" and titles[place.MENU_SECURITY] == "&Sicherheit"
        assert place.find_entry(window.menuBar(), "act_tools_permissions").text() == "Tool-Berechtigungen..."
        assert window.tabs.tabText(window.tabs.indexOf(window.models_tab)) == "🧠 Modelle"
        assert window.tabs.tabBar().accessibleTabName(window.tabs.indexOf(window.models_tab)) == "Modelle"
    finally:
        place.retranslate_menus(window, "en")
    ditto = [a.objectName() for a in window.menu_ditto.actions() if not a.isSeparator()]
    assert ditto[:2] == ["act_ditto_setup", "act_ditto_settings"], "Set up Ditto... comes first"
    advanced = window.menu_ditto.actions()[-1].menu()
    assert [a.objectName() for a in advanced.actions()] == ["act_ditto_runtime_open", "act_ditto_models_open",
                                                            "act_ditto_eval_open"]


def test_a_steps_row_names_the_place_and_opens_it(qapp, window):
    """The next steps under a message: the German path with [Open], another program's command with [Copy], a page
    with [Open page]; nothing takes the focus when the row appears, and no command of the framework is shown."""
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    from orchestrator.gui.next_step_widgets import StepsRow, open_object
    row = StepsRow(["runtime.install", "docker.start", "hf.access"], lang="de", window=window)
    try:
        texts = [label.text() for label in row.findChildren(QLabel)]
        assert texts[0] == ("Dittos lokales Modellprogramm bauen: Ditto > Erweitert > Lokale Laufzeit... > "
                            "llama-server bauen…")
        assert "sudo systemctl start docker" in texts[1] and not any("llmcf" in t for t in texts)
        names = sorted(b.text() for b in row.findChildren(QPushButton))
        assert names == ["Kopieren", "Seite öffnen", "Öffnen"]
        assert all(b.accessibleName() and not b.hasFocus() for b in row.buttons)
        opened = []
        row.open_requested.connect(opened.append)
        next(b for b in row.buttons if b.text() == "Öffnen").click()
        qapp.processEvents()
        assert opened == ["btn_ditto_runtime_build"]
        assert window.ditto_runtime_window.isVisible(), "[Open] opened the window the button lives in"
        next(b for b in row.buttons if b.text() == "Kopieren").click()
        assert QApplication.clipboard().text() == "sudo systemctl start docker"
    finally:
        row.deleteLater()
    assert open_object(window, "act_deploy_scan_remote") is True
    assert window.tabs.currentWidget() is window.builder_tab, "an entry of the Builder's scan menu: the Builder tab"
    window.tabs.setCurrentWidget(window.deployment_window)
    assert open_object(window, "btn_deploy_package") is True and window.tabs.currentWidget() is window.deployment_window
    assert open_object(window, "act_does_not_exist") is False
