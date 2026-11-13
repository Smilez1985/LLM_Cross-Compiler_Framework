"""The Linux test jobs get Qt's system libraries through scripts/ci_qt_libraries.sh: only what the runner image
lacks, from the runner's own sources first and then from official Ubuntu mirrors on other hosts, which apt checks
against the Ubuntu archive keyring already on the image (docs/DESIGN.md, Tests, CI and the Windows hazards)."""

import re

import pytest

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "ci_qt_libraries.sh"
WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/deps-update.yml")


@pytest.mark.parametrize("name", WORKFLOWS)
def test_the_workflows_install_qt_libraries_only_through_the_script(name):
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert "bash scripts/ci_qt_libraries.sh" in text, f"{name} does not use the script"
    assert not re.search(r"apt-get install[^\n]*\n?[^\n]*libegl1", text), f"{name} still installs Qt's libraries itself"


def test_the_script_skips_what_is_there_and_falls_back_to_signed_mirrors():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "ldconfig -p" in text and "nothing to install" in text, "libraries already on the image are not installed"
    mirrors = re.findall(r'"(https://[^"]+/ubuntu)"', text)
    assert len(mirrors) >= 2 and all("ubuntu.com" not in m for m in mirrors), "fallback mirrors live on other hosts"
    assert "Signed-By: ${KEYRING}" in text and 'KEYRING="/usr/share/keyrings/ubuntu-archive-keyring.gpg"' in text
    assert "trusted=yes" not in text and "allow-insecure" not in text.lower() and "AllowUnauthenticated" not in text
    assert "Dir::Etc::SourceList=/dev/null" in text, "a fallback mirror is tried on its own, not beside the dead ones"


def _job(text, name):
    """The lines of one job of a workflow, up to the next job."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"  {name}:")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"  [\w-]+:$", lines[i])), len(lines))
    return "\n".join(lines[start:end])


def test_linux_runs_the_suite_on_every_core_and_a_failed_report_upload_never_fails_a_job():
    """Serially the Linux suite had grown to 45 to 55 minutes against a limit of 60 and was cancelled; it runs on
    every core with pytest-xdist, as it is run locally. The test report is for reading only: GitHub's artifact service
    answering with an error page must not turn a job whose tests passed red."""
    text = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    test_job = _job(text, "test")
    assert "--junitxml=test-results.xml ${{ runner.os == 'Linux' && '-n auto' || '' }}" in test_job
    assert "--junitxml=test-results-headless.xml -n auto" in _job(text, "test-headless"), "the non-GUI suite too"
    uploads = re.findall(r"- name: Upload test results\n(?:\s+#[^\n]*\n)*?\s+if: always\(\)\n(?:\s+#[^\n]*\n)*"
                         r"\s+continue-on-error: true", text)
    assert len(uploads) == text.count("- name: Upload test results") == 2
    assert "pytest-xdist" in (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
