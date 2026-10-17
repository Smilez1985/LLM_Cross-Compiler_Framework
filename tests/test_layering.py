"""The layers of orchestrator/ (DECISIONS round 11, A2: one repository, the layers enforced by this test).

Each module belongs to a layer, and an import may only point to its own layer or a lower one:

    0 bottom        orchestrator (the package), orchestrator.utils, orchestrator.security, orchestrator.schemas
    1 remote        orchestrator.remote (the one package that talks to a board)
    2 kernel        the tools modules that define no tools: registry, policy, audit, approvals, signing, spec, ...
    3 domain        orchestrator.Core, orchestrator.runtime, orchestrator.rag, orchestrator.llm, orchestrator.evals;
                    llm and rag are libraries the Core uses and never import orchestrator.Core
    4 tool defs     orchestrator.tools.tools_*, framework_*, gui_bridge, gui_surface
    5 adapters      orchestrator.cli, orchestrator.mcp_server (and a later orchestrator.mcp), orchestrator.gui with
                    orchestrator.main (its start); an adapter never imports another adapter

Every import counts: at module level, inside a function, under `if TYPE_CHECKING:`, and a module named as a string
in a module that imports by name (importlib.import_module, lazy_attributes, LazyModule). Third-party libraries have
their one home: PySide6 and shiboken6 only in the GUI, click and rich only in the CLI, mcp and uvicorn only in the MCP
adapter, paramiko only in the SSH layer.

What breaks a rule today stands in tests/data/layering_allowlist.json, each entry with its reason and the chain that
removes it. The allowlist only shrinks: the test fails on a new violation and on an entry that no longer happens
(remove it). The largest import cycle (strongly connected component) may only shrink as well; when it gets smaller,
lower `largest_cycle` in the allowlist to the new size.

Regenerate the lists after a deliberate change with `python tests/test_layering.py`: it prints the violations and the
cycle size the allowlist must hold (keep the reasons of entries that stay).
"""

import ast
import json
import sys
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "orchestrator"
ALLOWLIST = Path(__file__).resolve().parent / "data" / "layering_allowlist.json"

LAYERS = ("bottom", "remote", "kernel", "domain", "tool definitions", "adapters")
BOTTOM, REMOTE, KERNEL, DOMAIN, TOOL_DEFS, ADAPTERS = range(len(LAYERS))
_PREFIX_LAYERS = (("orchestrator.utils", BOTTOM), ("orchestrator.security", BOTTOM), ("orchestrator.schemas", BOTTOM),
                  ("orchestrator.remote", REMOTE),
                  ("orchestrator.Core", DOMAIN), ("orchestrator.runtime", DOMAIN), ("orchestrator.rag", DOMAIN),
                  ("orchestrator.llm", DOMAIN), ("orchestrator.evals", DOMAIN),
                  ("orchestrator.cli", ADAPTERS), ("orchestrator.mcp_server", ADAPTERS), ("orchestrator.mcp", ADAPTERS),
                  ("orchestrator.gui", ADAPTERS), ("orchestrator.main", ADAPTERS))
# The tools package: tool definitions are tools_* plus the modules that put them together or reach the window.
_TOOL_DEFINITIONS = ("framework_tools", "framework_prompts", "framework_resources", "gui_bridge", "gui_surface")
# The adapters, each one unit: the GUI's start (orchestrator.main) belongs to the GUI.
_ADAPTERS = (("orchestrator.cli", "cli"), ("orchestrator.mcp_server", "mcp"), ("orchestrator.mcp", "mcp"),
             ("orchestrator.gui", "gui"), ("orchestrator.main", "gui"))
# Third-party libraries and the only places that may import them.
THIRD_PARTY_HOMES = {
    "PySide6": ("orchestrator.gui", "orchestrator.main"),
    "shiboken6": ("orchestrator.gui", "orchestrator.main"),
    "click": ("orchestrator.cli",),
    "rich": ("orchestrator.cli",),
    "mcp": ("orchestrator.mcp_server", "orchestrator.mcp"),
    "uvicorn": ("orchestrator.mcp_server", "orchestrator.mcp"),
    "paramiko": ("orchestrator.utils.ssh_trust", "orchestrator.remote.transport"),
}
# Inside the domain: the language-model layer and the knowledge base are used by the Core and never import it.
_BELOW_CORE = ("orchestrator.llm", "orchestrator.rag")
# Calls that import a module named by a string.
_BY_NAME = {"import_module", "__import__", "lazy_attributes", "LazyModule"}


def _within(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def module_name(path: Path) -> str:
    """orchestrator/Core/builder.py -> orchestrator.Core.builder; a package's __init__.py -> the package."""
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def layer_of(module: str) -> int:
    if module == "orchestrator":
        return BOTTOM
    if _within(module, "orchestrator.tools"):
        last = module.split(".")[2] if module.count(".") >= 2 else ""
        return TOOL_DEFS if last.startswith("tools_") or last in _TOOL_DEFINITIONS else KERNEL
    for prefix, layer in _PREFIX_LAYERS:
        if _within(module, prefix):
            return layer
    raise AssertionError(f"{module} belongs to no layer: add its package to tests/test_layering.py")


def adapter_of(module: str) -> Optional[str]:
    for prefix, name in _ADAPTERS:
        if _within(module, prefix):
            return name
    return None


class Import(NamedTuple):
    source: str      # the importing module
    target: str      # an orchestrator module, or the top-level name of another package
    line: int
    kind: str        # module, function, type_checking or by_name


def all_modules(root: Path = PACKAGE) -> Dict[str, Path]:
    return {module_name(p): p for p in sorted(root.rglob("*.py"))}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
        (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _resolve(name: str, modules: Set[str]) -> str:
    """The longest module of the framework that `name` starts with (an attribute after it is no module)."""
    parts = name.split(".")
    for n in range(len(parts), 0, -1):
        candidate = ".".join(parts[:n])
        if candidate in modules:
            return candidate
    return name


def _absolute(source: str, is_package: bool, module: Optional[str], level: int) -> str:
    """The absolute name of `from <level dots><module> import ...` written in `source`."""
    if not level:
        return module or ""
    base = source.split(".")
    base = base if is_package else base[:-1]
    base = base[:len(base) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def imports_of(path: Path, modules: Set[str]) -> Iterator[Import]:
    """Every import in the module at `path`: import statements wherever they stand, and module names given as text to
    a call that imports by name."""
    source = module_name(path)
    is_package = path.name == "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls: List[ast.Call] = []

    def visit(node: ast.AST, kind: str) -> Iterator[Import]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            kind = "function" if kind == "module" else kind
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.body:
                yield from visit(child, "type_checking")
            for child in node.orelse:
                yield from visit(child, kind)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield Import(source, _target(alias.name), node.lineno, kind)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(source, is_package, node.module, node.level)
            for alias in node.names:
                full = f"{base}.{alias.name}"
                yield Import(source, _target(full if full in modules else base), node.lineno, kind)
        elif isinstance(node, ast.Call):
            func = node.func
            if (func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else "") \
                    in _BY_NAME:
                calls.append(node)
        for child in ast.iter_child_nodes(node):
            yield from visit(child, kind)

    def _target(name: str) -> str:
        return _resolve(name, modules) if _within(name, "orchestrator") else name.split(".")[0]

    yield from visit(tree, "module")
    for node in _texts_given_to(calls, tree):
        # A module name given as text to a call that imports by name, directly or through the variables and tables
        # the call reads (lazy_attributes' tables, `name = "..."; import_module(name)`, f"{__name__}.{x}" with x a
        # submodule's name).
        for name in (node.value, f"{source}.{node.value}"):
            if name in modules and name != source:
                yield Import(source, name, node.lineno, "by_name")


def _texts_given_to(calls: List[ast.Call], tree: ast.AST) -> List[ast.Constant]:
    """The text constants that reach `calls`: in their arguments and, transitively, in the values assigned to the
    names their arguments (and those values) read."""
    assigned: Dict[str, List[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        assigned.setdefault(name.id, []).append(node.value)
    todo: List[ast.AST] = [arg for call in calls for arg in (*call.args, *(k.value for k in call.keywords))]
    seen_names: Set[str] = set()
    found: List[ast.Constant] = []
    while todo:
        for node in ast.walk(todo.pop()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.append(node)
            elif isinstance(node, ast.Name) and node.id not in seen_names:
                seen_names.add(node.id)
                todo.extend(assigned.get(node.id, ()))
    return found


class Graph(NamedTuple):
    modules: Dict[str, Path]
    imports: List[Import]

    def edges(self) -> Dict[str, Set[str]]:
        out: Dict[str, Set[str]] = {m: set() for m in self.modules}
        for imp in self.imports:
            if imp.target in self.modules and imp.target != imp.source:
                out[imp.source].add(imp.target)
        return out


def build_graph(root: Path = PACKAGE) -> Graph:
    modules = all_modules(root)
    names = set(modules)
    return Graph(modules, [imp for path in modules.values() for imp in imports_of(path, names)])


def points_up(source: str, target: str) -> bool:
    """Whether an import of `target` in `source` breaks the layers: a higher layer, another adapter, or the Core from
    llm or rag."""
    if layer_of(target) > layer_of(source):
        return True
    a, b = adapter_of(source), adapter_of(target)
    if a and b and a != b:
        return True
    return any(_within(source, low) for low in _BELOW_CORE) and _within(target, "orchestrator.Core")


def why(source: str, target: str) -> str:
    a, b = adapter_of(source), adapter_of(target)
    if a and b and a != b:
        return f"the {a} adapter imports the {b} adapter"
    if layer_of(target) > layer_of(source):
        return f"{LAYERS[layer_of(source)]} imports {LAYERS[layer_of(target)]}"
    return "llm and rag never import the Core"


def layer_violations(graph: Graph) -> Dict[Tuple[str, str], List[int]]:
    """(importer, imported) -> lines, for each import that points to a higher layer, into another adapter or from llm
    or rag into the Core."""
    found: Dict[Tuple[str, str], List[int]] = {}
    for imp in graph.imports:
        if imp.target not in graph.modules or imp.target == imp.source:
            continue
        if points_up(imp.source, imp.target):
            found.setdefault((imp.source, imp.target), []).append(imp.line)
    return found


def third_party_violations(graph: Graph) -> Dict[Tuple[str, str], List[int]]:
    """(importer, library) -> lines, for each import of a library outside its home."""
    found: Dict[Tuple[str, str], List[int]] = {}
    for imp in graph.imports:
        homes = THIRD_PARTY_HOMES.get(imp.target)
        if homes and not any(_within(imp.source, home) for home in homes):
            found.setdefault((imp.source, imp.target), []).append(imp.line)
    return found


def strongly_connected(edges: Dict[str, Set[str]]) -> List[List[str]]:
    """The strongly connected components (Tarjan, without recursion), largest first."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    found: List[List[str]] = []
    counter = 0
    for start in sorted(edges):
        if start in index:
            continue
        work = [(start, iter(sorted(edges[start])))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges.get(child, ())))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                found.append(sorted(component))
    return sorted(found, key=lambda c: (-len(c), c))


def load_allowlist() -> dict:
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))


def _entries(allowlist: dict, section: str, key: str) -> Dict[Tuple[str, str], dict]:
    return {(e["from"], e[key]): e for e in allowlist[section]}


@pytest.fixture(scope="module")
def graph() -> Graph:
    return build_graph()


@pytest.fixture(scope="module")
def allowlist() -> dict:
    return load_allowlist()


# --- the rules ------------------------------------------------------------------------------------------------------

def test_every_module_has_a_layer(graph):
    assert len(graph.modules) > 250, "the walk must see the whole package"
    for module in graph.modules:
        layer_of(module)


def test_every_import_points_down(graph, allowlist):
    found = layer_violations(graph)
    allowed = _entries(allowlist, "imports", "to")
    new = sorted(f"{a} -> {b} (line {', '.join(map(str, lines))}): {why(a, b)}"
                 for (a, b), lines in found.items() if (a, b) not in allowed)
    assert not new, ("An import points to a higher layer or into another adapter. Move the shared code down (utils, "
                     "security, the kernel or the domain) instead of importing upward:\n" + "\n".join(new))
    stale = sorted(f"{a} -> {b}" for a, b in allowed if (a, b) not in found)
    assert not stale, ("These allowlisted imports are gone: remove them from tests/data/layering_allowlist.json "
                       "(it only shrinks):\n" + "\n".join(stale))


def test_third_party_libraries_stay_in_their_layer(graph, allowlist):
    found = third_party_violations(graph)
    allowed = _entries(allowlist, "third_party", "library")
    new = sorted(f"{a} imports {lib} (line {', '.join(map(str, lines))}); only "
                 f"{', '.join(THIRD_PARTY_HOMES[lib])} may" for (a, lib), lines in found.items()
                 if (a, lib) not in allowed)
    assert not new, "A library is imported outside its home:\n" + "\n".join(new)
    stale = sorted(f"{a} -> {lib}" for a, lib in allowed if (a, lib) not in found)
    assert not stale, ("These allowlisted library imports are gone: remove them from "
                       "tests/data/layering_allowlist.json (it only shrinks):\n" + "\n".join(stale))


def test_the_largest_import_cycle_only_shrinks(graph, allowlist):
    components = strongly_connected(graph.edges())
    size = len(components[0])
    limit = allowlist["largest_cycle"]
    assert size <= limit, (f"The largest import cycle grew from {limit} to {size} modules. An import closed a new "
                           f"loop; import the shared code from a lower layer instead. The cycle: {components[0]}")
    assert size == limit, (f"The largest import cycle shrank from {limit} to {size} modules: lower largest_cycle in "
                           "tests/data/layering_allowlist.json to keep the gain")


def test_every_allowlist_entry_says_why_and_who_removes_it(allowlist):
    for section, key in (("imports", "to"), ("third_party", "library")):
        seen = set()
        for entry in allowlist[section]:
            assert set(entry) == {"from", key, "reason", "until"}, entry
            assert entry["reason"].strip() and entry["until"].strip(), entry
            assert (entry["from"], entry[key]) not in seen, f"twice: {entry}"
            seen.add((entry["from"], entry[key]))


# --- the walk itself: each kind of import is found, so the rules above can fail -------------------------------------

def _sample(tmp_path: Path, files: Dict[str, str]) -> Graph:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    modules = {module_name(p): p for p in sorted((tmp_path / "orchestrator").rglob("*.py"))}
    return Graph(modules, [imp for path in modules.values() for imp in imports_of(path, set(modules))])


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)
    return lambda files: _sample(tmp_path, files)


def test_the_walk_finds_every_kind_of_import(sample):
    g = sample({
        "orchestrator/__init__.py": "",
        "orchestrator/utils/__init__.py": "",
        "orchestrator/utils/low.py": (
            "import importlib\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from orchestrator.Core import high\n"
            "def f():\n    from orchestrator.tools.tools_x import helper\n    import PySide6.QtCore\n"
            "def g():\n    return importlib.import_module('orchestrator.cli')\n"),
        "orchestrator/Core/__init__.py": "",
        "orchestrator/Core/high.py": "from . import other\nfrom .other import name\nimport paramiko\n",
        "orchestrator/Core/other.py": "from orchestrator.utils import low\n",
        "orchestrator/tools/__init__.py": "",
        "orchestrator/tools/tools_x.py": "from orchestrator.Core.high import x\n",
        "orchestrator/tools/registry.py": "",
        "orchestrator/cli.py": "from orchestrator.gui import window\nimport click\n",
        "orchestrator/gui/__init__.py": "",
        "orchestrator/gui/window.py": "import click\n",
    })
    kinds = {(i.source, i.target): i.kind for i in g.imports}
    assert kinds[("orchestrator.utils.low", "orchestrator.Core.high")] == "type_checking"
    assert kinds[("orchestrator.utils.low", "orchestrator.tools.tools_x")] == "function"
    assert kinds[("orchestrator.utils.low", "orchestrator.cli")] == "by_name"
    assert kinds[("orchestrator.Core.high", "orchestrator.Core.other")] == "module"
    assert set(layer_violations(g)) == {("orchestrator.utils.low", "orchestrator.Core.high"),
                                        ("orchestrator.utils.low", "orchestrator.tools.tools_x"),
                                        ("orchestrator.utils.low", "orchestrator.cli"),
                                        ("orchestrator.cli", "orchestrator.gui.window")}
    assert set(third_party_violations(g)) == {("orchestrator.utils.low", "PySide6"),
                                              ("orchestrator.Core.high", "paramiko"),
                                              ("orchestrator.gui.window", "click")}
    cycle = strongly_connected(g.edges())[0]
    assert cycle == ["orchestrator.Core.high", "orchestrator.Core.other", "orchestrator.tools.tools_x",
                     "orchestrator.utils.low"]


def test_the_layers_of_the_tools_package():
    assert layer_of("orchestrator.tools.registry") == KERNEL
    assert layer_of("orchestrator.tools.tool_windows") == KERNEL
    assert layer_of("orchestrator.tools") == KERNEL
    assert layer_of("orchestrator.tools.tools_runtime") == TOOL_DEFS
    assert layer_of("orchestrator.tools.framework_tools") == TOOL_DEFS
    assert layer_of("orchestrator.tools.gui_bridge") == TOOL_DEFS
    assert layer_of("orchestrator.remote.transport") == REMOTE
    assert layer_of("orchestrator.security.paths") == BOTTOM
    assert adapter_of("orchestrator.main") == adapter_of("orchestrator.gui.app") == "gui"
    assert adapter_of("orchestrator.Core.builder") is None


def report(graph: Optional[Graph] = None) -> dict:
    """Today's violations and cycle size in the allowlist's form (reasons kept from the current allowlist)."""
    graph = graph or build_graph()
    try:
        current = load_allowlist()
    except (OSError, ValueError):
        current = {"imports": [], "third_party": []}
    old_imports = _entries(current, "imports", "to")
    old_libs = _entries(current, "third_party", "library")
    blank = {"reason": "", "until": ""}

    def keep(old: dict, key: tuple) -> dict:
        entry = old.get(key, blank)
        return {"reason": entry["reason"], "until": entry["until"]}

    return {
        "largest_cycle": len(strongly_connected(graph.edges())[0]),
        "imports": [{"from": a, "to": b, **keep(old_imports, (a, b))}
                    for a, b in sorted(layer_violations(graph))],
        "third_party": [{"from": a, "library": lib, **keep(old_libs, (a, lib))}
                        for a, lib in sorted(third_party_violations(graph))],
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=1, ensure_ascii=False))
