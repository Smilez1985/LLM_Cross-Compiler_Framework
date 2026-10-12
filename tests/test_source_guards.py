"""Source guards of the delegation design (WP1): rules that hold for the code as written, checked over every file.

One helper tells calls that ran without the person answering their question (a standing approval `grant:<id>`, a
delegation `delegation:<id>`) from all others: `confirmed_builds.unattended()`. A check of the prefix anywhere else
would treat a delegation like a person's answer (the smaller disk reserve, a pack that looks person-installed, a
first build nobody confirmed), so the literal is allowed only inside that helper.
"""

import ast
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.confirmed_builds import UNATTENDED_PREFIXES, person_confirmed, unattended, unattended_parts

SOURCES = sorted((REPO_ROOT / "orchestrator").rglob("*.py"))
HELPER = (REPO_ROOT / "orchestrator" / "Core" / "confirmed_builds.py", {"unattended_parts", "unattended"})


def _prefix_literals(node: ast.AST) -> list:
    """String constants in a call's arguments (also inside a tuple) that start with an unattended prefix."""
    found = []
    for arg in getattr(node, "args", []):
        values = arg.elts if isinstance(arg, (ast.Tuple, ast.List)) else [arg]
        for value in values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str) \
                    and value.value.startswith(tuple(UNATTENDED_PREFIXES)):
                found.append(value.value)
    return found


def prefix_checks(path: Path) -> list:
    """(line, text) of every startswith("grant:") / startswith("delegation:") and every slice by len("grant:") outside
    the helper functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed = set()
    if path == HELPER[0]:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in HELPER[1]:
                allowed.update(id(n) for n in ast.walk(node))
    hits = []
    for node in ast.walk(tree):
        if id(node) in allowed or not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in ("startswith", "len", "removeprefix", "partition", "split") and _prefix_literals(node):
            hits.append((node.lineno, ast.get_source_segment(path.read_text(encoding="utf-8"), node)))
    return hits


def test_no_grant_prefix_checks_outside_unattended():
    assert len(SOURCES) > 100, "the guard must see the whole package"
    offenders = {str(p.relative_to(REPO_ROOT)): hits for p in SOURCES if (hits := prefix_checks(p))}
    assert not offenders, ("check unattended channels with orchestrator.Core.confirmed_builds.unattended() "
                           f"(or unattended_parts()), not by their prefix: {offenders}")


def test_the_guard_finds_a_prefix_check(tmp_path):
    """The guard itself: a module that checks the prefix by hand is found (so the test above can fail)."""
    sample = tmp_path / "sample.py"
    sample.write_text('def f(c):\n    return c.startswith("grant:") or c.startswith(("x", "delegation:"))\n'
                      'def g(c):\n    return c[len("grant:"):]\n', encoding="utf-8")
    assert len(prefix_checks(sample)) == 3


@pytest.mark.parametrize("channel, kind", [("grant:g_0123456789abcdef", "grant"),
                                           ("delegation:d_0123456789abcdef", "delegation"),
                                           ("framework-gui", ""), ("approval:ap_0123456789abcdef", ""),
                                           ("elicitation", ""), ("", ""), (None, "")])
def test_unattended_covers_standing_approvals_and_delegations(channel, kind):
    assert unattended_parts(channel)[0] == kind
    assert unattended(channel) is bool(kind)
    if kind:
        assert not person_confirmed(channel), "an unattended run is never the person's confirmation"
