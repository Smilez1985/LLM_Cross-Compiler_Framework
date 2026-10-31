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
