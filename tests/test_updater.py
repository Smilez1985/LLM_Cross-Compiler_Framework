"""The updater is a fresh installer run: a git fast-forward of the checkout the framework was installed from, then
that checkout's installer with the person's flags (llm-cli update, Tools > Maintenance > Update Framework...)."""

import subprocess

import pytest
from click.testing import CliRunner

from orchestrator.utils import updater as updater_module
from orchestrator.utils.updater import SOURCE_FILE, UpdateError, UpdateManager


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return _Done(returncode=3 if "install" in " ".join(cmd) else 0, stdout="2\n")

    monkeypatch.setattr(updater_module.subprocess, "run", fake_run)
    monkeypatch.setattr(updater_module.platform, "system", lambda: "Linux")
    return seen


def _checkout(path):
    (path / ".git").mkdir(parents=True)
    return path


def test_the_checkout_is_its_own_source(tmp_path):
    assert UpdateManager(_checkout(tmp_path)).source_root() == tmp_path


def test_the_installed_copy_names_its_checkout(tmp_path):
    """install.sh / install.bat write the checkout they ran from into the installed copy (install.bat with a BOM)."""
    source, installed = _checkout(tmp_path / "src"), tmp_path / "opt"
    installed.mkdir()
    (installed / SOURCE_FILE).write_text("﻿" + str(source) + "\r\n", encoding="utf-8")
    assert UpdateManager(installed).source_root() == source


@pytest.mark.parametrize("content", ["", "relative/path", "/nowhere/at/all"])
def test_no_usable_source_is_none(tmp_path, content):
    (tmp_path / SOURCE_FILE).write_text(content, encoding="utf-8")
    manager = UpdateManager(tmp_path)
    assert manager.source_root() is None
    with pytest.raises(UpdateError, match="git pull, then run its installer"):
        manager.installer_command()


@pytest.mark.parametrize("system, head", [("Linux", ["sudo", "bash"]), ("Windows", ["cmd.exe", "/c"])])
def test_installer_command_per_platform(monkeypatch, tmp_path, system, head):
    monkeypatch.setattr(updater_module.platform, "system", lambda: system)
    cmd = UpdateManager(_checkout(tmp_path)).installer_command("--offline")
    assert cmd[:2] == head and cmd[-1] == "--offline"
    assert cmd[-2] == str(tmp_path / ("install.sh" if system == "Linux" else "install.bat"))


def test_run_fast_forwards_the_checkout_then_runs_its_installer(tmp_path, calls):
    source, installed = _checkout(tmp_path / "src"), tmp_path / "opt"
    installed.mkdir()
    (installed / SOURCE_FILE).write_text(str(source), encoding="utf-8")
    assert UpdateManager(installed).run("--offline") == 3
    assert calls == [["git", "pull", "--ff-only", "--quiet"], ["sudo", "bash", str(source / "install.sh"), "--offline"]]


def test_a_checkout_that_cannot_fast_forward_runs_nothing(monkeypatch, tmp_path):
    seen = []

    def diverged(cmd, **kwargs):
        seen.append(cmd)
        return _Done(returncode=128, stderr="fatal: Not possible to fast-forward, aborting.")
    monkeypatch.setattr(updater_module.subprocess, "run", diverged)
    with pytest.raises(UpdateError, match="fast-forward"):
        UpdateManager(_checkout(tmp_path)).run()
    assert len(seen) == 1


def test_on_windows_the_installer_gets_its_own_console(monkeypatch, tmp_path, calls):
    """The installer replaces the .venv this process runs from, which Windows refuses while it lives: started
    detached, the caller exits next."""
    monkeypatch.setattr(updater_module.platform, "system", lambda: "Windows")
    started = []
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert UpdateManager(_checkout(tmp_path)).run() == 0
    assert started == [["cmd.exe", "/c", str(tmp_path / "install.bat")]] and calls[-1][:2] == ["git", "pull"]


def test_check_counts_upstream_commits(tmp_path, calls):
    assert UpdateManager(tmp_path).check_for_updates() == 0  # no checkout, nothing asked
    assert calls == []
    assert UpdateManager(_checkout(tmp_path)).check_for_updates() == 2


def test_check_survives_a_failing_git(monkeypatch, tmp_path):
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(updater_module.subprocess, "run", boom)
    assert UpdateManager(_checkout(tmp_path)).check_for_updates() == 0


def test_the_terminal_runs_the_cli_update(monkeypatch, tmp_path):
    monkeypatch.setattr(updater_module.shutil, "which", lambda name: name == "konsole" and "/usr/bin/konsole")
    cmd = UpdateManager(tmp_path).terminal_command()
    assert cmd[:2] == ["konsole", "-e"] and cmd[-4:] == ["-m", "orchestrator.cli", "update", "--yes"]
    monkeypatch.setattr(updater_module.shutil, "which", lambda name: None)
    assert UpdateManager(tmp_path).terminal_command() is None


# --- llm-cli update -------------------------------------------------------------------------------------------------

def _cli(monkeypatch, tmp_path, *args, input=None):
    from orchestrator import cli as cli_module
    from orchestrator.Core import framework
    monkeypatch.setattr(framework, "APP_ROOT", tmp_path)
    return CliRunner().invoke(cli_module.cli, ["update", *args], input=input)


def test_cli_without_a_checkout_says_how(monkeypatch, tmp_path):
    result = _cli(monkeypatch, tmp_path)
    assert result.exit_code == 1 and "git pull, then run its installer again" in result.output


def test_cli_check_changes_nothing(monkeypatch, tmp_path, calls):
    _checkout(tmp_path)
    result = _cli(monkeypatch, tmp_path, "--check")
    assert result.exit_code == 0 and "2 new commit(s)" in result.output
    assert all(c[:2] != ["git", "pull"] for c in calls)


def test_cli_asks_with_no_as_the_default(monkeypatch, tmp_path, calls):
    _checkout(tmp_path)
    result = _cli(monkeypatch, tmp_path, input="\n")
    assert result.exit_code == 0 and "Nothing changed." in result.output
    assert all(c[:2] != ["git", "pull"] for c in calls)


def test_cli_yes_runs_the_installer_with_offline(monkeypatch, tmp_path, calls):
    _checkout(tmp_path)
    result = _cli(monkeypatch, tmp_path, "--yes", "--offline")
    assert result.exit_code == 3 and "exit code 3" in result.output
    assert calls[-1] == ["sudo", "bash", str(tmp_path / "install.sh"), "--offline"]


# --- the installers record their checkout ---------------------------------------------------------------------------

def test_the_installers_write_the_source_file():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert f'"$INSTALL_DIR/{SOURCE_FILE}"' in (root / "install.sh").read_text(encoding="utf-8")
    assert f'(Join-Path $InstallDir "{SOURCE_FILE}")' in (root / "install.bat").read_text(encoding="utf-8")
