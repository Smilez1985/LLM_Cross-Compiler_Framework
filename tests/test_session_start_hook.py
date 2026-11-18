"""The SessionStart hook of Claude Code (.claude/hooks/session-start.sh): it turns the secret-scan git hooks on in a fresh clone,
quietly and only when needed, and with the hooks on a commit that holds a credential is stopped. Real git in a throw-away
repository; the key-shaped value is put together here, because the repository's own scan would stop this file otherwise."""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

HOOK = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"
pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None or shutil.which("git") is None,
                                reason="a bash script and git")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})


def start(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(HOOK)], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "CLAUDE_CODE_REMOTE": "true"})


@pytest.fixture
def clone(tmp_path):
    """A fresh checkout of the framework's hooks and scanner: what a new container starts with (the hooks off)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git(repo, "init", "-q").returncode == 0
    git(repo, "config", "user.email", "t@example.org")
    git(repo, "config", "user.name", "t")
    shutil.copytree(REPO_ROOT / ".githooks", repo / ".githooks")
    (repo / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "secret_scan.py", repo / "scripts" / "secret_scan.py")
    (repo / "orchestrator" / "security").mkdir(parents=True)          # the two tables the scanner loads by path
    for name in ("secret_patterns.py", "personal_data.py"):
        shutil.copy(REPO_ROOT / "orchestrator" / "security" / name, repo / "orchestrator" / "security" / name)
    for path in (repo / ".githooks").iterdir():
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return repo


class TestTheHookFile:

    def test_it_is_registered_for_the_start_of_every_session_and_is_executable(self):
        settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        commands = [h["command"] for entry in settings["hooks"]["SessionStart"] for h in entry["hooks"]]
        assert commands == ["$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"]
        assert os.access(HOOK, os.X_OK)

    def test_it_installs_nothing_and_asks_for_no_network(self):
        text = HOOK.read_text(encoding="utf-8")
        for word in ("pip ", "apt-get", "npm ", "curl", "wget", "git clone", "sudo"):
            assert word not in text, word


class TestTurningTheHooksOn:

    def test_a_fresh_clone_has_them_off_and_the_hook_turns_them_on(self, clone):
        assert git(clone, "config", "--get", "core.hooksPath").stdout.strip() == ""
        run = start(clone)
        assert run.returncode == 0 and "git hooks enabled" in run.stdout
        assert git(clone, "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"

    def test_a_second_start_is_quiet_and_changes_nothing(self, clone):
        start(clone)
        again = start(clone)
        assert again.returncode == 0 and again.stdout == "" and again.stderr == ""
        assert git(clone, "config", "--get-all", "core.hooksPath").stdout.split() == [".githooks"]

    def test_a_hook_that_is_not_executable_is_made_executable_because_git_skips_it_silently(self, clone):
        (clone / ".githooks" / "pre-push").chmod(0o644)
        start(clone)
        assert os.access(clone / ".githooks" / "pre-push", os.X_OK)

    def test_a_folder_that_is_not_such_a_checkout_is_left_alone_without_an_error(self, tmp_path):
        other = tmp_path / "export"
        other.mkdir()
        run = start(other)                                     # no .githooks, not a repository
        assert run.returncode == 0 and run.stdout == ""
        repo = tmp_path / "plain"
        repo.mkdir()
        git(repo, "init", "-q")
        run = start(repo)                                      # a repository without the hooks folder
        assert run.returncode == 0 and git(repo, "config", "--get", "core.hooksPath").stdout.strip() == ""


class TestWithTheHooksOnACredentialStopsTheCommit:

    def test_a_commit_with_a_key_header_is_refused_and_a_clean_one_goes_through(self, clone):
        start(clone)
        (clone / "probe.txt").write_text('value = "' + "-----BEGIN RSA " + 'PRIVATE KEY-----"\n', encoding="utf-8")
        git(clone, "add", "probe.txt")
        refused = git(clone, "commit", "-q", "-m", "probe")
        assert refused.returncode != 0 and "private key" in refused.stdout + refused.stderr
        assert git(clone, "rev-parse", "--verify", "-q", "HEAD").returncode != 0      # nothing was committed
        git(clone, "reset", "-q", "probe.txt")
        (clone / "probe.txt").write_text("nothing secret here\n", encoding="utf-8")
        git(clone, "add", "probe.txt")
        assert git(clone, "commit", "-q", "-m", "clean").returncode == 0

    def test_without_the_hook_the_same_commit_would_go_through(self, clone):
        # what happened on 2026-11-18: hooks off in a fresh clone, so nothing stopped the commit
        (clone / "probe.txt").write_text('value = "' + "-----BEGIN RSA " + 'PRIVATE KEY-----"\n', encoding="utf-8")
        git(clone, "add", "probe.txt")
        assert git(clone, "commit", "-q", "-m", "probe").returncode == 0
