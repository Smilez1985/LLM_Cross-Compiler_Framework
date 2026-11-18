"""Every workflow file is valid YAML with its jobs (a step name with a colon and no quotes once made ci.yml unreadable, which
stops the whole workflow from starting; actionlint in CI finds it only after it is pushed)."""

from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT

FILES = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))


def test_there_are_workflows():
    assert FILES


@pytest.mark.parametrize("path", FILES, ids=lambda p: Path(p).name)
def test_a_workflow_is_valid_yaml_with_jobs_and_steps(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data.get("jobs"), dict) and data["jobs"], path.name
    for name, job in data["jobs"].items():
        assert isinstance(job, dict), f"{path.name}: job {name}"
        assert "uses" in job or isinstance(job.get("steps"), list) and job["steps"], f"{path.name}: job {name} has no steps"
        for step in job.get("steps") or []:
            assert "uses" in step or "run" in step, f"{path.name}: job {name}: a step with neither uses nor run"
