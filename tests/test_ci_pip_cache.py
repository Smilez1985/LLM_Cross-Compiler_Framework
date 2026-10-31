"""The pip cache is switched off in CI for now: the account's cache and egress quota ran out and a job hung at the
cache restore. No setup-python step of the framework's workflows asks for a cache, each workflow says why in a
comment, and the ROADMAP (EN and DE) has the row that brings the cache back once the quota allows."""

import re

import pytest

from conftest import REPO_ROOT

WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/security.yml", ".github/workflows/retrieval-eval.yml")


def _setup_python_steps(text):
    """Each setup-python step of a workflow: its lines up to the next step."""
    steps, current = [], None
    for line in text.splitlines():
        if re.match(r"\s*- (uses|name|run):", line):
            if current is not None:
                steps.append(current)
            current = [line] if "actions/setup-python@" in line else None
        elif current is not None:
            current.append(line)
    if current is not None:
        steps.append(current)
    return steps


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_setup_python_step_uses_the_pip_cache(name):
    steps = _setup_python_steps((REPO_ROOT / name).read_text(encoding="utf-8"))
    assert steps, f"{name} has no setup-python step"
    for step in steps:
        body = "\n".join(step)
        assert not re.search(r"^\s*cache:", body, re.M), f"{name}: a setup-python step still uses a cache:\n{body}"
        assert "cache-dependency-path" not in body, body
    assert any("pip cache off" in "\n".join(step) for step in steps), f"{name} does not say why the cache is off"


def test_the_roadmap_brings_the_pip_cache_back():
    assert "pip cache back in CI" in (REPO_ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
    assert "pip-Cache" in (REPO_ROOT / "docs/ROADMAP_DE.md").read_text(encoding="utf-8")


def _all_workflow_files():
    return sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml")) + \
        sorted((REPO_ROOT / "scripts" / "community_repo").glob("*.y*ml"))


def _cache_steps(data):
    """Every setup-python step of a parsed workflow that asks for a cache, however the step is written."""
    found = []
    for job in (data.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and "actions/setup-python@" in str(step.get("uses", "")):
                given = step.get("with") or {}
                if "cache" in given or "cache-dependency-path" in given:
                    found.append(step)
    return found


@pytest.mark.parametrize("path", _all_workflow_files(), ids=lambda p: p.name)
def test_no_workflow_restores_the_pip_cache(path):
    """Every workflow, also one added later and a step written with a name first: no setup-python cache."""
    yaml = pytest.importorskip("yaml")
    assert _cache_steps(yaml.safe_load(path.read_text(encoding="utf-8")) or {}) == []


def test_the_scan_sees_a_named_setup_python_step():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load("jobs:\n  a:\n    steps:\n      - name: Python\n        uses: actions/setup-python@x\n"
                          "        with:\n          cache: pip\n")
    assert len(_cache_steps(data)) == 1
