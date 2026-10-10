"""One version everywhere: orchestrator.__version__, pyproject.toml and the newest changelog entry."""

import re

from conftest import REPO_ROOT
from orchestrator import __version__


def test_pyproject_matches_the_package_version():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == __version__


def test_changelog_has_an_entry_for_the_current_version():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", text, re.M)
    assert "## [Unreleased]" in text
    assert released and released[0][0] == __version__, "newest changelog version differs from __version__"


def test_framework_and_cli_report_the_same_version():
    from orchestrator.Core.framework import FrameworkInfo
    from orchestrator import cli
    assert FrameworkInfo().version == __version__ == cli.__version__
