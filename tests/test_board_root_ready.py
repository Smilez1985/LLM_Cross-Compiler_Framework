"""The board's root readiness for llmcf-install (orchestrator/runtime/bundle/readiness.py): fake boards (POSIX only)
with stub sudo, su, visudo and package managers and the real llmcf-install test build (tests/test_llmcf_install.py
builds it, or LLMCF_INSTALL_TEST_BIN names it)."""

import os
import shutil

import pytest

from orchestrator.runtime.bundle import readiness
from test_llmcf_install import FAKE_SUDO, POSIX, _autostart_bundle, _ready, _root_board, helper_binary  # noqa: F401

# the tools a fake board's scripts use, linked into a folder of their own (so no sudo of this computer is found)
TOOLS = ("sh", "cat", "cp", "mv", "rm", "mkdir", "mktemp", "ls", "grep", "sed", "tr", "cut", "sha256sum", "chmod",
         "env", "head", "tail", "wc", "awk", "ssh-keygen", "openssl", "printf", "test", "[", "dirname", "basename",
         "date", "sort", "uniq", "find", "tee", "true", "false", "stat", "readlink", "touch", "ln", "od", "base64")


def _no_sudo_path(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        found = shutil.which(name)
        if found:
            os.symlink(found, tools / name)
    shim = tmp_path / "shim"
    pending = shim / "sudo"
    (tmp_path / "sudo.pending").write_bytes(pending.read_bytes())
    pending.unlink()
    # apk on the fake Alpine board: `apk add --no-cache sudo` puts the fake sudo in place
    apk = shim / "apk"
    apk.write_bytes(('#!/bin/sh\necho "apk $*" >> "%s/calls.log"\ncase "$*" in *add*sudo*) cp "%s/sudo.pending" '
                     '"%s/sudo"; chmod 0755 "%s/sudo" ;; esac\n' % (tmp_path, tmp_path, shim, shim)).encode())
    apk.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{tools}")


@pytest.fixture
def board(tmp_path, helper_binary, monkeypatch):  # noqa: F811 - the module fixture of test_llmcf_install
    from test_board_bundle import GateShell
    board = _root_board(tmp_path, helper_binary)
    monkeypatch.setenv("PATH", f"{tmp_path / 'shim'}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}")
    store, built = _autostart_bundle(tmp_path, board, helper_binary)
    from orchestrator.runtime.bundle.board import run_bundle
    shell = GateShell(board)
    assert run_bundle(shell, built, "apply").report.result == "stopped-root"
    board.shell, board.built = shell, built
    return board


def _check(board):
    from orchestrator.runtime.bundle.board import helper_sha256
    return readiness.check(board.shell.run, helper_sha256(board.built))


@POSIX
def test_the_check_reads_what_is_missing_and_a_second_setup_changes_nothing(board, tmp_path):
    (tmp_path / "sudo-password").write_text("pw-once")
    found = _check(board)
    assert (found.sudo, found.rootway, found.signers, found.helper, found.dropin) == \
        ("yes", "password", "root", "absent", "no"), found
    assert found.missing() == ["helper", "dropin"] and found.way() == "sudo" and found.needs_password()
    text = " ".join(readiness.plan_lines(found))
    assert readiness.FIXED_HELPER in text and readiness.DROPIN in text and "visudo -cf" in text
    done = _ready(board.shell, board.built, password="pw-once")
    assert done.ok and done.changed == ("helper", "dropin"), done
    rule = (tmp_path / "etc/sudoers.d/llmcf-install").read_text()
    assert "NOPASSWD:" in rule and "ALL=(root)" in rule and "NOPASSWD: ALL" not in rule
    assert oct((tmp_path / "etc/sudoers.d/llmcf-install").stat().st_mode & 0o777) == "0o440"
    assert "visudo -cf" in (tmp_path / "calls.log").read_text()
    again = _check(board)
    assert again.ready and again.rootway == "password", again
    second = _ready(board.shell, board.built)   # nothing missing: no password needed, nothing changed
    assert second.ok and second.changed == (), second
    # the password never reaches the board's command lines or the answer
    assert "pw-once" not in (tmp_path / "calls.log").read_text()


@POSIX
def test_a_bundle_with_another_llmcf_install_is_an_update_through_the_same_setup(board, tmp_path):
    assert _ready(board.shell, board.built).ok
    fixed = tmp_path / "usr/local/libexec/llmcf/llmcf-install"
    fixed.write_bytes(fixed.read_bytes() + b"\0")   # an older or other program at the fixed place
    found = _check(board)
    assert found.helper == "other" and found.missing() == ["helper-update"], found
    assert "Replace llmcf-install" in " ".join(readiness.plan_lines(found))
    from orchestrator.runtime.bundle.board import run_root
    assert run_root(board.shell, board.built, "plan").report.result == "not-ready"
    assert _ready(board.shell, board.built).changed == ("helper",)
    assert run_root(board.shell, board.built, "plan").report.result == "plan"


@POSIX
def test_a_board_without_sudo_gets_it_through_its_package_manager_as_root_once_with_su(board, tmp_path, monkeypatch):
    _no_sudo_path(tmp_path, monkeypatch)
    (tmp_path / "root-password").write_text("root-once")
    (tmp_path / "sudo-password").write_text("pw-once")   # the account's sudo asks for a password once it is there
    found = _check(board)
    found = readiness.Readiness(**{**found.__dict__, "family": "alpine", "managers": ("apk",)})
    assert found.sudo == "no" and found.su == "yes" and found.way() == "su" and found.manager == "apk", found
    assert "apk add --no-cache sudo" in " ".join(readiness.plan_lines(found))
    # enterprise mode never goes through su
    assert found.way("enterprise") == "" and "enterprise mode" in found.problem("enterprise")
    from orchestrator.runtime.bundle.board import helper_sha256
    refused = readiness.setup(board.shell.run, board.built.bundle_id, helper_sha256(board.built), found,
                              mode="enterprise", password="root-once")
    assert (refused.ok, refused.reason) == (False, "enterprise-su")
    wrong = readiness.setup(board.shell.run, board.built.bundle_id, helper_sha256(board.built), found,
                            password="wrong")
    assert (wrong.ok, wrong.reason) == (False, "su-refused"), wrong
    done = readiness.setup(board.shell.run, board.built.bundle_id, helper_sha256(board.built), found,
                           password="root-once")
    assert done.ok and done.changed == ("sudo", "helper", "dropin"), done
    calls = (tmp_path / "calls.log").read_text()
    assert "apk add --no-cache sudo" in calls and "root-once" not in calls
    from orchestrator.runtime.bundle.board import run_root
    assert run_root(board.shell, board.built, "plan").report.result == "plan"


@POSIX
def test_an_unknown_distribution_without_sudo_is_refused_before_anything_runs(board, tmp_path, monkeypatch):
    _no_sudo_path(tmp_path, monkeypatch)
    (tmp_path / "shim" / "apk").unlink()
    found = readiness.Readiness(**{**_check(board).__dict__, "family": "unknown", "managers": ()})
    assert found.problem() == readiness.REASONS["no-manager"]


@POSIX
def test_the_removal_removes_exactly_the_rule_and_the_program(board, tmp_path):
    (tmp_path / "sudo-password").write_text("pw-once")
    assert _ready(board.shell, board.built, password="pw-once").ok
    keep = tmp_path / "usr/local/libexec/llmcf/llmcf-gate"
    keep.write_text("#!/bin/sh\n")
    assert not readiness.remove(board.shell.run, _check(board)).ok   # the rule runs only llmcf-install: a password
    done = readiness.remove(board.shell.run, _check(board), password="pw-once")
    assert done.ok and done.removed == ("dropin", "helper"), done
    assert not (tmp_path / "etc/sudoers.d/llmcf-install").exists()
    assert not (tmp_path / "usr/local/libexec/llmcf/llmcf-install").exists()
    assert keep.exists() and keep.parent.is_dir()
    assert readiness.remove(board.shell.run, _check(board), password="pw-once").removed == ()


def test_the_readiness_words_and_reasons():
    found = readiness.Readiness.parse("sudo=yes\nrootway=nopass\nsigners=none\nhelper=same\ndropin=yes\n"
                                      "account=ok\nmanager=apk\nmanager=rm -rf\nhelper=../x\n")
    assert found.missing() == ["signers"] and found.managers == ("apk",) and found.helper == "same"
    assert "root-owned key line" in " ".join(readiness.plan_lines(found))
    assert readiness.Readiness.parse("").missing() == ["sudo", "signers", "helper", "dropin"]
    assert set(found.as_dict()) >= {"ready", "missing"} and "account" not in found.as_dict()
    text = readiness.declined_text("voice-test", "ZERO 3W", found)
    assert "voice-test" in text and "llm-cli" not in text
    assert readiness.SetupDone(False, "sudoers", ("helper",)).text().startswith("The board's root readiness could not")


def test_bundle_deploy_reads_the_readiness_and_never_sets_it_up():
    """The MCP tool only reads the board's root readiness (fixed words) when the root actions wait."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "orchestrator/tools/tools_bundle.py").read_text(encoding="utf-8")
    assert "readiness_on_board" in text and "root_readiness" in text
    assert "ready_setup" not in text and "ready_remove" not in text
