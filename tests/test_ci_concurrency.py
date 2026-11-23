"""A push to `development` or `main` never cancels a running workflow run (maintainer, 2026-11-23: a push had cancelled
a run minutes before the end of its Windows job, DESIGN decision round 115). Every workflow of the repository with a
`concurrency` block either never cancels or cancels only off those two branches (pull requests and other branches
still cancel the older run); none cancels unconditionally."""

import pytest
import yaml

from conftest import REPO_ROOT

WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
NEVER_ON_THE_MAIN_BRANCHES = "${{ github.ref != 'refs/heads/development' && github.ref != 'refs/heads/main' }}"


def concurrency(path):
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("concurrency")


def test_the_workflows_are_found():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "security.yml", "docker.yml", "framework-container.yml",
                                            "retrieval-eval.yml", "deps-update.yml", "release-tools.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_cancels_a_running_run_on_development_or_main(path):
    block = concurrency(path)
    if block is None:
        return
    assert isinstance(block, dict), f"{path.name}: concurrency needs cancel-in-progress spelled out"
    cancel = block.get("cancel-in-progress", False)
    assert cancel in (False, NEVER_ON_THE_MAIN_BRANCHES), (
        f"{path.name}: cancel-in-progress is {cancel!r}; on development and main a push must never cancel a running run")


@pytest.mark.parametrize("name", ["ci.yml", "security.yml", "docker.yml", "framework-container.yml", "retrieval-eval.yml"])
def test_pull_requests_and_other_branches_still_cancel_the_older_run(name):
    assert concurrency(REPO_ROOT / ".github" / "workflows" / name)["cancel-in-progress"] == NEVER_ON_THE_MAIN_BRANCHES
