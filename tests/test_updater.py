"""The updater is a fresh installer run: a git fast-forward, then this platform's installer with the person's flags."""

import subprocess

import pytest

from orchestrator.utils import updater as updater_module
from orchestrator.utils.updater import UpdateManager


class _Done:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return _Done(returncode=3 if "install" in " ".join(cmd) else 0, stdout="2\n")

    monkeypatch.setattr(updater_module.subprocess, "run", fake_run)
    return seen


@pytest.mark.parametrize("system, head", [("Linux", ["sudo", "bash"]), ("Windows", ["cmd.exe", "/c"])])
def test_installer_command_per_platform(monkeypatch, tmp_path, system, head):
    monkeypatch.setattr(updater_module.platform, "system", lambda: system)
    cmd = UpdateManager(tmp_path).installer_command("--offline")
    assert cmd[:2] == head
    assert cmd[-1] == "--offline"
    assert cmd[-2].endswith("install.sh" if system == "Linux" else "install.bat")


def test_run_fast_forwards_a_checkout_then_runs_the_installer(monkeypatch, tmp_path, calls):
    monkeypatch.setattr(updater_module.platform, "system", lambda: "Linux")
    (tmp_path / ".git").mkdir()
    assert UpdateManager(tmp_path).run("--headless") == 3
    assert calls[0] == ["git", "pull", "--ff-only", "--quiet"]
    assert calls[1] == ["sudo", "bash", str(tmp_path / "install.sh"), "--headless"]


def test_run_without_a_checkout_only_runs_the_installer(monkeypatch, tmp_path, calls):
    monkeypatch.setattr(updater_module.platform, "system", lambda: "Linux")
    UpdateManager(tmp_path).run()
    assert len(calls) == 1 and calls[0][-1].endswith("install.sh")


def test_check_counts_upstream_commits(tmp_path, calls):
    assert UpdateManager(tmp_path).check_for_updates() is False  # no checkout, nothing asked
    assert calls == []
    (tmp_path / ".git").mkdir()
    assert UpdateManager(tmp_path).check_for_updates() is True


def test_check_survives_a_failing_git(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(updater_module.subprocess, "run", boom)
    assert UpdateManager(tmp_path).check_for_updates() is False
