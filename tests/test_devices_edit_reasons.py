"""`llm-cli deploy devices edit` ends with the plain reason, never a traceback, when something on this computer stops
it after the person said yes (wave 3c chain cli-gui): known_hosts held by another process of the framework (a
LockTimeout from pairing.replace_key) and settings that cannot be read (_settings) print the local_problem reason of
_print_reason and what failed, exit with code 1 and change nothing."""

from types import SimpleNamespace

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.utils import ssh_trust  # noqa: E402
from test_remote_trust import Audit, keep_logger_levels, record, terminal  # noqa: E402,F401


@pytest.fixture
def board(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from orchestrator.runtime.profile import default_state_dir
    state = default_state_dir()
    key = paramiko.ECDSAKey.generate()
    device = record(state, key, label="Desk board")
    audit = Audit()
    monkeypatch.setattr(cli_module, "_device_audit", lambda ctx, folder: audit)
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
    return SimpleNamespace(state=state, key=key, device=device, audit=audit)


def edit(*args, answer="y\n"):
    from test_cli_builds import invoke_plain
    return invoke_plain("deploy", "devices", "edit", *args, input=answer)


def new_key_line():
    new = paramiko.ECDSAKey.generate()
    return f"{new.get_name()} {new.get_base64()} root@board"


@pytest.mark.usefixtures("keep_logger_levels")
def test_known_hosts_held_by_another_process_gives_the_plain_reason(board, terminal, monkeypatch):
    from orchestrator.remote import pairing
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils.file_lock import LockTimeout
    terminal()

    def held(*a, **k):
        raise LockTimeout("known_hosts.lock is held by another process (GUI, CLI or MCP server)")

    monkeypatch.setattr(pairing, "replace_key", held)
    before = (board.state / "known_hosts").read_bytes()
    result = edit(board.device.device_id, "--key", new_key_line())
    assert result.code == 1, result.out
    assert result.exception is None or isinstance(result.exception, SystemExit), "no traceback"
    assert "Something on this computer stopped the contact" in result.out and "(local_problem)" in result.out
    assert "known_hosts is being changed by another part of the framework" in result.out
    assert "nothing was changed" in result.out and "Traceback" not in result.out
    assert (board.state / "known_hosts").read_bytes() == before
    assert [d.device_id for d in TrustedDevices(board.state).list()] == [board.device.device_id]


@pytest.mark.usefixtures("keep_logger_levels")
def test_settings_that_cannot_be_read_after_the_yes_give_the_plain_reason(board, terminal, monkeypatch):
    from orchestrator import cli as cli_module
    terminal()
    real_settings, real_confirm, said = cli_module._settings, cli_module.click.confirm, []

    def confirm(*a, **k):
        said.append(True)
        return real_confirm(*a, **k)

    def settings(ctx):
        if said:   # the settings break after the person said yes
            raise ValueError("config.yml: bad")
        return real_settings(ctx)

    monkeypatch.setattr(cli_module.click, "confirm", confirm)
    monkeypatch.setattr(cli_module, "_settings", settings)
    before = (board.state / "known_hosts").read_bytes()
    result = edit(board.device.device_id, "--key", new_key_line())
    assert result.code == 1, result.out
    assert "(local_problem)" in result.out and "settings could not be read (config.yml: bad)" in result.out
    assert "nothing was changed" in result.out
    assert (board.state / "known_hosts").read_bytes() == before and board.audit.rows == []


@pytest.mark.usefixtures("keep_logger_levels")
def test_a_file_that_cannot_be_read_before_the_question_gives_the_plain_reason(board, terminal, monkeypatch):
    from orchestrator.runtime.device import person_steps
    terminal()

    def unreadable(*a, **k):
        raise PermissionError(13, "Permission denied", str(board.state / "known_hosts"))

    monkeypatch.setattr(person_steps, "plan_board_change", unreadable)
    result = edit(board.device.device_id, "--host", "192.0.2.41")
    assert result.code == 1, result.out
    assert "(local_problem)" in result.out and "What failed on this computer: Permission denied: known_hosts" in \
        result.out
    assert "[y/N]" not in result.out, "nothing was asked"
