"""The first pairing of a board behind a jump host reads the board's key THROUGH the jump (chain followups-3).

A board whose entry in llmcf's own SSH settings has a ProxyJump or ProxyCommand is not reachable directly, so the key
it presents is read through this computer's OpenSSH with that file (the one argv builder, transport.openssh_options):
-F llmcf's file, a temporary UserKnownHostsFile, StrictHostKeyChecking=accept-new into that temporary file only,
BatchMode=yes, PreferredAuthentications=none, no command. The person then sees its fingerprint as for every other
board. A jump host OpenSSH does not trust yet stops the read with its own plain error (JumpHostNotTrusted). A board
reached directly keeps the paramiko read.

A fake ssh program stands in for OpenSSH (documentation addresses, no network).
"""

import base64
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("paramiko")

POSIX = pytest.mark.skipif(os.name == "nt", reason="the fake ssh is a POSIX script")
BOARD = "192.0.2.40"
JUMP = "jump.example"


def _key_line():
    import paramiko
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                                 serialization.PublicFormat.OpenSSH)
    kind, blob = raw.decode("ascii").split()[:2]
    key = paramiko.PKey.from_type_string(kind, base64.b64decode(blob))
    return f"{kind} {blob}", key


FAKE_SSH = r'''#!{python}
import sys
args = sys.argv[1:]
with open({log!r}, "a", encoding="utf-8") as log:
    log.write(repr(args) + "\n")
opts = [args[i + 1] for i, a in enumerate(args) if a == "-o"]
known = [o.split("=", 1)[1] for o in opts if o.startswith("UserKnownHostsFile=")][0]
if known.startswith('"'):
    known = known[1:-1].replace('\\"', '"').replace("\\\\", "\\")
known = known.replace("%%", "%")
mode = {mode!r}
if mode == "board":
    with open(known, "a", encoding="utf-8") as f:
        f.write({entry!r} + "\n")
    sys.stderr.write("Warning: Permanently added '{board}' to the list of known hosts.\n")
    sys.stderr.write("user@{board}: Permission denied (publickey).\n")
elif mode == "jump":
    sys.stderr.write("Host key verification failed.\n")
    sys.stderr.write("Connection closed by UNKNOWN port 65535\n")
else:
    sys.stderr.write("ssh: connect to host {board} port 22: Connection refused\n")
sys.exit(255)
'''


def _state(tmp_path, *, jump=True):
    from orchestrator.remote import ssh_config
    state = tmp_path / "state"
    state.mkdir()
    ssh_config.ensure(state)
    if jump:
        lines = [f"ProxyJump {JUMP}"]
        path = ssh_config.config_path(state)
        path.write_text(ssh_config.with_block(ssh_config.read(state), BOARD, lines), encoding="utf-8")
        ssh_config.record_accepted(state, BOARD, lines)
    return state


def _fake(tmp_path, monkeypatch, mode, entry=""):
    log = tmp_path / "ssh-calls.log"
    program = tmp_path / "bin" / "ssh"
    program.parent.mkdir()
    program.write_text(FAKE_SSH.format(python=sys.executable, log=str(log), mode=mode, entry=entry, board=BOARD),
                       encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{program.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    return log


@POSIX
def test_a_board_behind_a_jump_presents_its_key_through_openssh(tmp_path, monkeypatch):
    """Regression: the key was read with a direct paramiko connection, which cannot reach a board behind a jump host
    (and would have shown the jump host's or a stranger's key)."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import TrustStore, fingerprint
    from orchestrator.utils import ssh_trust
    line, key = _key_line()
    state = _state(tmp_path)
    log = _fake(tmp_path, monkeypatch, "board", entry=f"{BOARD} {line}")
    monkeypatch.setattr(ssh_trust, "fetch_host_key",
                        lambda *a, **k: pytest.fail("a board behind a jump is never read directly"))
    got = TrustStore(state).read_presented_key(Endpoint(BOARD, 22))
    assert fingerprint(got) == fingerprint(key)
    argv = eval(log.read_text(encoding="utf-8").splitlines()[-1])   # noqa: S307 - our own fake's repr of its argv
    opts = [argv[i + 1] for i, word in enumerate(argv) if word == "-o"]
    assert argv[0] == "-F" and argv[1].endswith("ssh_config")
    assert "StrictHostKeyChecking=accept-new" in opts and "StrictHostKeyChecking=yes" not in opts
    assert "BatchMode=yes" in opts and "PreferredAuthentications=none" in opts and "-N" in argv
    known = [o for o in opts if o.startswith("UserKnownHostsFile=")]
    assert len(known) == 1 and "known_hosts" in known[0]
    assert str(ssh_trust.known_hosts_path(state)) not in known[0], "never the framework's own known_hosts"
    assert not ssh_trust.known_hosts_path(state).exists(), "nothing is trusted before the person's yes"
    assert argv[-1].endswith(f"@{BOARD}"), "no command after the board"
    leftovers = [p for p in Path(tmp_path).rglob("*known_hosts*") if p.is_file()]
    assert leftovers == [], "the temporary known_hosts goes again"


@POSIX
def test_a_jump_host_openssh_does_not_trust_yet_is_named_first(tmp_path, monkeypatch):
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import JumpHostNotTrusted, TrustStore
    state = _state(tmp_path)
    _fake(tmp_path, monkeypatch, "jump")
    with pytest.raises(JumpHostNotTrusted) as refused:
        TrustStore(state).read_presented_key(Endpoint(BOARD, 22))
    text = str(refused.value)
    assert "jump host" in text and "pair" in text.lower() and BOARD in text


@POSIX
def test_a_board_behind_a_jump_that_cannot_be_reached_is_an_os_error(tmp_path, monkeypatch):
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import JumpHostNotTrusted, TrustStore
    state = _state(tmp_path)
    _fake(tmp_path, monkeypatch, "down")
    with pytest.raises(OSError) as failed:
        TrustStore(state).read_presented_key(Endpoint(BOARD, 22))
    assert not isinstance(failed.value, JumpHostNotTrusted)


def test_a_board_reached_directly_keeps_the_paramiko_read(tmp_path, monkeypatch):
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import TrustStore
    from orchestrator.utils import ssh_trust
    _, key = _key_line()
    state = _state(tmp_path, jump=False)
    seen = []
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: seen.append((host, port)) or key)
    assert TrustStore(state).read_presented_key(Endpoint(BOARD, 2222)) is key
    assert seen == [(BOARD, 2222)]


def test_the_one_argv_builder_takes_the_temporary_known_hosts(tmp_path):
    from orchestrator.remote import transport
    state = _state(tmp_path, jump=False)
    temp = tmp_path / "scan" / "known_hosts"
    options = transport.openssh_options(state, known_hosts=temp, accept_new=True)
    pairs = [options[i + 1] for i, word in enumerate(options) if word == "-o"]
    assert "StrictHostKeyChecking=accept-new" in pairs and "BatchMode=yes" in pairs
    assert f"UserKnownHostsFile={transport.openssh_file(temp)}" in pairs
    plain = transport.openssh_options(state)
    assert "StrictHostKeyChecking=yes" in plain, "every other call keeps the strict check"
