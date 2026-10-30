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
    # No [Unreleased] section (DECISIONS round 34): the newest dated section is the version being built, so a new
    # section and the version bump land in the same commit.
    assert "## [Unreleased]" not in text, "the changelog has no [Unreleased] section; planned work is in the roadmap"
    assert released and released[0][0] == __version__, "newest changelog version differs from __version__"


def test_framework_and_cli_report_the_same_version():
    from orchestrator.Core.framework import FrameworkInfo
    from orchestrator import cli
    assert FrameworkInfo().version == __version__ == cli.__version__


def test_the_roadmap_and_the_readme_name_the_version_in_progress():
    # The version in progress is the current one: the roadmap's overview has its row "in progress" and no "Rest of"
    # row of a version that is already released; the README's "What comes next" names it, in both languages.
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    older = [v for v in released if v != __version__]
    for name, rest, progress in (("docs/ROADMAP.md", "Rest of", "in progress"),
                                 ("docs/ROADMAP_DE.md", "Rest von", "in Arbeit")):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert re.search(rf"^\| {re.escape(__version__)} \| [^|]+ \| {progress} \|$", text, re.M), name
        assert re.search(rf"^## {re.escape(__version__)} - .+\({progress}\)$", text, re.M), name
        for version in older:
            assert f"{rest} {version}" not in text, f"{name} still has the rest of the released {version}"
    for name, line in (("README.md", f"Version {__version__} is in progress."),
                       ("README_DE.md", f"Version {__version__} ist in Arbeit.")):
        text = " ".join((REPO_ROOT / name).read_text(encoding="utf-8").split())   # wrapped lines read as one
        assert line in text, name
