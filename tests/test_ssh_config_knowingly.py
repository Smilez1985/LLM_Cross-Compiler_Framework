"""llmcf's own SSH settings: a ProxyJump or ProxyCommand is used only with the person's recorded knowing yes, the
key and environment options are refused, and a board behind a jump goes through OpenSSH (paramiko reads no file).

A hand edit after the yes, or a line written before the framework kept the yes, makes prepare() refuse with what the
line does and the person's way to accept it; nothing is ever used silently."""

import pytest

from orchestrator.remote import ssh_config
from orchestrator.runtime.device import person_steps

BOARD = "192.0.2.40"
JUMP = "jump.example"


def state(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    return folder


def write_block(folder, lines, knowingly=True):
    plan = person_steps.plan_ssh_block(folder, BOARD, lines)
    person_steps.save_ssh_block(folder, plan, via="cli", knowingly=knowingly)
    return plan


# --- the refused kinds ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line, kind", [
    ("AddKeysToAgent yes", ssh_config.KEYS), ("IdentityFile ~/.ssh/other_key", ssh_config.KEYS),
    ("CertificateFile ~/.ssh/cert.pub", ssh_config.KEYS), ("IdentityAgent /tmp/agent.sock", ssh_config.KEYS),
    ("SetEnv LC_ALL=C", ssh_config.SESSION), ("SendEnv LANG", ssh_config.SESSION)])
def test_key_and_environment_options_are_refused_with_their_reason(line, kind):
    check = ssh_config.check_lines([line])
    assert [(p.kind, p.reason) for p in check.refused] == [(kind, ssh_config.REASONS[kind])]


def test_loglevel_stays_allowed():
    assert ssh_config.check_lines(["LogLevel DEBUG3"]).ok


def test_a_hand_written_key_line_stops_every_openssh_call(tmp_path):
    folder = state(tmp_path)
    ssh_config.ensure(folder)
    path = ssh_config.config_path(folder)
    path.write_text(ssh_config.HEADER + f"Host {BOARD}\n    IdentityFile ~/.ssh/other_key\n")
    with pytest.raises(ssh_config.SshConfigRefused) as e:
        ssh_config.prepare(folder)
    assert e.value.problems[0].kind == ssh_config.KEYS


# --- the knowing yes --------------------------------------------------------------------------------------------------

def test_a_jump_the_person_accepted_is_used(tmp_path):
    folder = state(tmp_path)
    write_block(folder, [f"ProxyJump {JUMP}"])
    assert ssh_config.prepare(folder) == ssh_config.config_path(folder)
    assert len(ssh_config.accepted_digests(folder)) == 1


@pytest.mark.parametrize("edit", [f"ProxyJump other.{JUMP}", "ProxyCommand nc %h %p"],
                         ids=["jump-changed", "jump-replaced-by-command"])
def test_a_hand_edit_after_the_yes_is_refused_with_what_it_does_and_the_way_to_accept(tmp_path, edit):
    folder = state(tmp_path)
    write_block(folder, [f"ProxyJump {JUMP}"])
    path = ssh_config.config_path(folder)
    path.write_text(path.read_text().replace(f"ProxyJump {JUMP}", edit))
    with pytest.raises(ssh_config.SshConfigRefused) as e:
        ssh_config.prepare(folder)
    reason = e.value.problems[0].reason
    keyword = edit.split()[0].lower()
    assert ssh_config.KNOWINGLY[keyword] in reason and ssh_config.ACCEPT_WAY in reason
    assert e.value.problems[0].kind == "knowingly" and BOARD in reason


def test_a_jump_written_before_there_was_a_record_is_refused(tmp_path):
    folder = state(tmp_path)
    ssh_config.ensure(folder)
    ssh_config.config_path(folder).write_text(ssh_config.HEADER + f"Host {BOARD}\n    ProxyJump {JUMP}\n")
    with pytest.raises(ssh_config.SshConfigRefused):
        ssh_config.prepare(folder)


def test_a_jump_before_the_first_host_block_is_never_covered(tmp_path):
    folder = state(tmp_path)
    write_block(folder, [f"ProxyJump {JUMP}"])
    path = ssh_config.config_path(folder)
    path.write_text(f"ProxyJump {JUMP}\n" + path.read_text())
    with pytest.raises(ssh_config.SshConfigRefused) as e:
        ssh_config.prepare(folder)
    assert "before the first Host block" in e.value.problems[0].reason


def test_the_person_accepts_a_hand_edit_as_it_is_with_the_same_lines(tmp_path):
    folder = state(tmp_path)
    ssh_config.ensure(folder)
    path = ssh_config.config_path(folder)
    path.write_text(ssh_config.HEADER + f"Host {BOARD}\n    ProxyJump {JUMP}\n")
    plan = person_steps.plan_ssh_block(folder, BOARD, [f"ProxyJump {JUMP}"])
    assert plan.unchanged and plan.pending_yes
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.save_ssh_block(folder, plan, via="cli")
    assert e.value.kind == "ssh_knowingly", "never without the knowing yes"
    before = path.read_text()
    person_steps.save_ssh_block(folder, plan, via="gui", knowingly=True)
    assert path.read_text() == before, "accepted as it is, nothing rewritten"
    ssh_config.prepare(folder)
    again = person_steps.plan_ssh_block(folder, BOARD, [f"ProxyJump {JUMP}"])
    assert again.unchanged and not again.pending_yes


def test_an_ai_channel_cannot_accept(tmp_path):
    folder = state(tmp_path)
    plan = person_steps.plan_ssh_block(folder, BOARD, [f"ProxyJump {JUMP}"])
    with pytest.raises(PermissionError):
        person_steps.save_ssh_block(folder, plan, via="mcp", knowingly=True)
    assert not ssh_config.accepted_digests(folder)


def test_a_plain_block_needs_no_record(tmp_path):
    folder = state(tmp_path)
    write_block(folder, ["ConnectionAttempts 3"], knowingly=False)
    ssh_config.prepare(folder)
    assert not ssh_config.accepted_path(folder).exists()


# --- which boards need OpenSSH ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, host, expected", [
    (f"Host {BOARD}\n    ProxyJump {JUMP}\n", BOARD, True),
    (f"Host {BOARD}\n    ProxyJump {JUMP}\n", "192.0.2.41", False),
    (f"Host 192.0.2.*\n    ProxyCommand nc %h %p\n", BOARD, True),
    (f"Host 192.0.2.* !{BOARD}\n    ProxyJump {JUMP}\n", BOARD, False),
    (f"Host {BOARD}\n    ProxyJump none\nHost *\n    ProxyJump {JUMP}\n", BOARD, False),
    (f"Host {BOARD}\n    ConnectionAttempts 3\nHost *\n    ProxyJump {JUMP}\n", BOARD, True),
    ("", BOARD, False)], ids=["exact", "other-host", "pattern", "negated", "first-value-none", "later-block",
                              "no-file"])
def test_needs_proxy_reads_the_file_as_openssh_does(tmp_path, text, host, expected):
    folder = state(tmp_path)
    if text:
        ssh_config.ensure(folder)
        ssh_config.config_path(folder).write_text(text)
    assert ssh_config.needs_proxy(folder, host) is expected


class FakeOpenSsh:
    made = []

    def __init__(self, endpoint, user, state_dir):
        FakeOpenSsh.made.append((endpoint.host, user))


def _session(folder, **kw):
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.session import RemoteSession
    return RemoteSession(Endpoint(BOARD), "pi", folder, **kw)


def test_a_board_behind_a_jump_goes_through_openssh_never_paramiko(tmp_path, monkeypatch):
    import shutil
    from orchestrator.remote import session as session_module, transport
    folder = state(tmp_path)
    write_block(folder, [f"ProxyJump {JUMP}"])
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/ssh")
    monkeypatch.setattr(transport, "SystemSshTransport", FakeOpenSsh)
    opened = []
    monkeypatch.setattr(session_module.RemoteSession, "_open_openssh", lambda self, waiting: opened.append(1))

    def no_paramiko(*a, **k):
        raise AssertionError("paramiko reads no settings file and must not connect here")
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(ssh_trust, "connect", no_paramiko)
    s = _session(folder)
    s.open()
    assert opened == [1] and s.openssh is FakeOpenSsh


@pytest.mark.parametrize("case", ["no-ssh", "password", "fingerprint"])
def test_where_openssh_cannot_do_it_the_refusal_is_plain(tmp_path, monkeypatch, case):
    import shutil
    from orchestrator.remote.session import OneTimePassword, SessionFailure
    folder = state(tmp_path)
    write_block(folder, [f"ProxyJump {JUMP}"])
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if case == "no-ssh" else "/usr/bin/ssh")
    kw = {"password": {"credentials": OneTimePassword("example-not-a-password")},
          "fingerprint": {"expect_fingerprint": "SHA256:" + "A" * 43}}.get(case, {})
    with pytest.raises(SessionFailure) as e:
        _session(folder, **kw).open()
    assert e.value.args and "jump host or a proxy command" in str(e.value)
    assert getattr(e.value, "kind", "needs_openssh") == "needs_openssh"
