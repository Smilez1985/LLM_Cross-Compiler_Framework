"""llmcf's command gate on a board (orchestrator/remote/gate.py, orchestrator/runtime/device/board_ops.py): the
framework's key on a board runs only llmcf's operations, each a fixed verb with checked arguments.

The gate runs here under this computer's /bin/sh with SSH_ORIGINAL_COMMAND set, as sshd runs a forced command, in a
temporary home folder (never this computer's own). Container, sudo and sshd parts need a POSIX system and skip on
Windows with their reason.
"""

import hashlib
import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.remote import gate
from orchestrator.remote.transport import Result
from orchestrator.runtime.device import board_ops  # noqa: F401 - defines every board operation
from orchestrator.runtime.device.launcher import render_systemd_unit

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the gate is a POSIX sh program; Windows runners have no /bin/sh to run it")
PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExample0 llm-cross-compiler-framework"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPersonsOwnKeyPersonsOwnKeyPersonsOwn01 person@example.com"

# Every operation the framework sends to a board (characterization: a new command needs its operation here and in
# board_ops.py; a gated board runs nothing else).
EXPECTED_GATE_OPS = {
    "bench-stop", "bench-stop-group", "bench-sweep", "bench-tools", "build-remove", "builds-list", "dir-exists",
    "dir-remove", "facts", "facts-sudo", "file-append", "file-create", "file-move", "file-prefix-sha256", "file-read",
    "file-remove", "file-sha256", "file-size", "file-write", "file-write-private", "install-log", "job-poll", "keygen",
    "login-check", "mkprivate", "model-sha256", "old-sidecar", "ping", "probe-python", "probe-python-check",
    "probe-shell", "programs-check", "root-unit-enable", "root-unit-install", "root-unit-start", "run-program",
    "run-program-split", "runtime-activate", "runtime-check", "runtime-install", "server-tools-check",
    "staging-create", "start-bench-job", "start-bundle-kept", "start-bundle-run", "bundle-root",
    "start-bench-job-setsid", "start-bench-job-setsid-timeout", "start-bench-job-timeout", "start-install",
    "start-status", "status", "thermal", "measure-idle", "start-measure-load", "limits-write",
}
def _signer():
    """A signing key of this test only (the framework's board signer shape)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from orchestrator.Core.community_pack import BoardSigner
    return BoardSigner(Ed25519PrivateKey.generate())


SETUP_ONLY = {"gate-install", "root-board", "root-setup", "unit-write-legacy", "unit-enable-legacy", "unit-start-legacy"}


@pytest.fixture
def board(tmp_path):
    home = tmp_path / "home"
    (home / gate.PLACE_HOME_REL / "llama.cpp").mkdir(parents=True)
    path = tmp_path / "llmcf-gate"
    path.write_text(gate.render_gate(), encoding="utf-8")

    def run(line, stdin=b""):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "SSH_ORIGINAL_COMMAND": line}
        done = subprocess.run(["sh", str(path)], input=stdin, env=env, capture_output=True, timeout=60)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    run.home = home
    return run


def test_the_gate_runs_exactly_the_frameworks_operations():
    ops = {op.verb for op in gate.gate_operations()}
    assert ops == EXPECTED_GATE_OPS
    assert SETUP_ONLY <= set(gate.OPERATIONS) and not (SETUP_ONLY & ops), "setup steps never run in the gate"
    assert board_ops.PLACES_FOLLOW_LAUNCHER


def test_a_setup_step_never_becomes_a_gate_call():
    with pytest.raises(gate.GateRefused):
        gate.gate_call(gate.script("unit-write-legacy"))
    with pytest.raises(gate.GateRefused):
        gate.gate_call("sh -c 'id'")   # a free shell string is no operation


@pytest.mark.parametrize("verb, args", [
    ("file-sha256", ("/etc/passwd",)), ("file-sha256", ("/home/x/../y",)), ("dir-remove", ("/a b",)),
    ("ping", ("extra",)), ("nope", ()), ("runtime-activate", ("/r", "not-hex")), ("run-program", ("/h", "/p", "$(id)")),
])
def test_arguments_are_checked_before_a_command_is_made(verb, args):
    if verb == "file-sha256" and args == ("/etc/passwd",):
        assert gate.script(verb, *args)   # a plain path: only the board's gate knows its folders
        return
    with pytest.raises(gate.GateRefused):
        gate.script(verb, *args)


@POSIX
@pytest.mark.parametrize("line, why", [
    ("", "no operation"),
    ("sh -c 'id'", "characters"),
    ("llmcf-gate ping;id", "characters"),
    ("llmcf-gate ping\nid", "characters"),
    ("llmcf-gate ping $(id)", "characters"),
    ("llmcf-gate ping `id`", "characters"),
    ("id", "not one of llmcf's operations"),
    ("llmcf-gate nope", "unknown operation"),
    ("llmcf-gate ping extra", "arguments"),
    ("llmcf-gate file-sha256 /etc/passwd", "arguments"),
    ("llmcf-gate dir-remove HOME/.local/share/llm-framework/../../.ssh", "arguments"),
    ("llmcf-gate dir-remove HOME/.ssh", "arguments"),
    ("llmcf-gate gate-install ssh-ed25519 AAAA x", "unknown operation"),
    ("llmcf-gate unit-write-legacy", "unknown operation"),
    ("llmcf-gate run-program /tmp /bin/sh -c", "arguments"),
    ("llmcf-gate home-of /", "unknown operation"),
])
def test_the_gate_refuses_with_one_plain_line(board, line, why):
    done = board(line.replace("HOME", str(board.home)))
    assert done.status == gate.REFUSED_STATUS and gate.is_refusal(done.status, done.err), done
    assert why in gate.refusal_text(done.err) and done.out == ""


@POSIX
def test_the_gate_runs_an_operation_with_its_checked_arguments(board):
    root = f"{board.home}/{gate.PLACE_HOME_REL}/llama.cpp"
    assert board("llmcf-gate status").out.strip() == (f"restricted=yes gate={gate.gate_version(gate.render_gate())} "
                                                      "keys=home signing=no tier="
                                                      # a gate that is not root-owned is the home gate (the test's
                                                      # gate is root-owned only when the suite runs as root)
                                                      + ("dropbear" if getattr(os, "getuid", lambda: 1)() == 0
                                                         else "home"))
    made = board("llmcf-gate mkprivate " + root + " .bench")
    assert made.status == 0 and made.out.startswith(root + "/.bench.")
    folder = made.out.strip()
    assert board("llmcf-gate file-write " + folder + "/out", b"hello").status == 0
    assert board("llmcf-gate file-read " + folder + "/out").out == "hello"
    assert board("llmcf-gate file-size " + folder + "/out").out.strip() == "5"
    digest = hashlib.sha256(b"hello").hexdigest()
    assert board("llmcf-gate file-sha256 " + folder + "/out").out.split()[0] == digest
    assert board("llmcf-gate dir-remove " + folder).status == 0 and not Path(folder).exists()


@POSIX
def test_the_gate_runs_only_the_frameworks_own_probe(board):
    probe = (Path(board_ops.__file__).resolve().parents[3] / "scripts" / "hardware_probe.sh").read_bytes()
    other = b"echo pwned\n"
    refused = board("llmcf-gate probe-shell", other)
    assert gate.is_refusal(refused.status, refused.err) and "pwned" not in refused.out
    ran = board("llmcf-gate probe-shell", probe)
    assert not gate.is_refusal(ran.status, ran.err) and ran.out


@POSIX
def test_pairing_installs_the_gate_and_the_restricted_line_and_keeps_the_persons_lines(tmp_path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    keys = home / ".ssh" / "authorized_keys"
    keys.write_text(OTHER + "\n" + PUBLIC + "\n", encoding="utf-8")   # an unrestricted framework key from before

    def run(script, stdin=None):
        done = subprocess.run(["sh", "-c", str(script)], input=b"".join(stdin or []), capture_output=True,
                              env={"PATH": os.environ.get("PATH", ""), "HOME": str(home)}, timeout=60)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    text = gate.render_gate()
    for _ in range(2):   # idempotent: the line is there once
        assert gate.install_gate(run, PUBLIC, text) == (True, "")
    lines = keys.read_text(encoding="utf-8").splitlines()
    gate_file = home / gate.GATE_REL
    assert lines == [OTHER, gate.restricted_line(PUBLIC, gate_file.as_posix())]
    assert gate_file.read_text(encoding="utf-8") == text
    for option in gate.KEY_OPTIONS:
        assert option in lines[1]


@POSIX
def test_a_board_that_cannot_take_the_gate_says_so_and_changes_nothing(tmp_path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    keys = home / ".ssh" / "authorized_keys"
    keys.write_text(OTHER + "\n", encoding="utf-8")
    (home / ".config").write_text("a file where the folder belongs", encoding="utf-8")   # cannot be written

    def run(script, stdin=None):
        done = subprocess.run(["sh", "-c", str(script)], input=b"".join(stdin or []), capture_output=True,
                              env={"PATH": os.environ.get("PATH", ""), "HOME": str(home)}, timeout=60)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    assert gate.install_gate(run, PUBLIC, gate.render_gate()) == (False, "read-only")
    assert keys.read_text(encoding="utf-8") == OTHER + "\n"


class _Client:
    """A paramiko stand-in whose board answers the pairing's setup step as `answer`."""

    def __init__(self, answer):
        self.answer, self.commands = answer, []

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        out = io.BytesIO(self.answer.encode())

        class Channel:
            def recv_exit_status(self):
                return 0

            def shutdown_write(self):
                pass
        out.channel = Channel()
        stdin = io.BytesIO()
        stdin.channel = out.channel
        return stdin, out, io.BytesIO()


def test_pairing_without_the_gate_is_refused_unless_the_person_allows_it():
    from orchestrator.utils import ssh_trust
    bundle = board_ops.bundle(_signer())
    with pytest.raises(ssh_trust.GateUnavailable) as refused:
        ssh_trust.install_public_key(_Client("root-board=unavailable reason=no-sshd\n"), PUBLIC, bundle)
    assert "OpenSSH" in str(refused.value) and refused.value.reason == "no-sshd"
    assert ssh_trust.install_public_key(_Client("root-board=installed home=removed\n"), PUBLIC, bundle) == "restricted"
    with pytest.raises(ssh_trust.GateUnavailable) as no_signer:   # nothing to give the board: no key at all
        ssh_trust.install_public_key(_Client("root-board=installed home=none\n"), PUBLIC, board_ops.bundle())
    assert no_signer.value.reason == "no-signer"
    plain = _Client("root-board=unavailable reason=no-sudo\n")
    plain.answer = ""
    assert ssh_trust.install_public_key(plain, PUBLIC, bundle, allow_unrestricted=True) == "unrestricted"


@pytest.mark.skipif(shutil.which("visudo") is None and not Path("/usr/sbin/visudo").exists(),
                    reason="visudo is not installed here (Windows runners, minimal images)")
def test_the_sudoers_file_is_exact_and_valid(tmp_path):
    text = board_ops.sudoers_text("radxa")
    assert "ALL=(root) NOPASSWD:" in text and text.count(board_ops.ROOT_HELPER) == 3
    assert "ALL\n" not in text and " ALL, " not in text   # no general rule
    path = tmp_path / "llmcf-radxa"
    path.write_text(text, encoding="utf-8")
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    done = subprocess.run([visudo, "-cf", str(path)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    with pytest.raises(ValueError):
        board_ops.sudoers_text("root")


@POSIX
@pytest.mark.parametrize("kv", ["f16", "q8_0"])
def test_the_root_helper_writes_exactly_the_frameworks_unit(tmp_path, kv):
    passwd = tmp_path / "passwd"
    passwd.write_text("radxa:x:1000:1000::/home/radxa:/bin/sh\n", encoding="utf-8")
    unit = tmp_path / "llm-llama-server.service"
    helper = tmp_path / "llmcf-root"
    helper.write_text(board_ops.render_root_helper(unit_path=str(unit), passwd=str(passwd), systemctl="true"))
    env = {"PATH": os.environ.get("PATH", ""), "SUDO_USER": "radxa"}
    done = subprocess.run(["sh", str(helper), "install-unit", "/home/radxa/m.gguf", "8080", kv], env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert unit.read_text() == render_systemd_unit(user="radxa", home="/home/radxa", model="/home/radxa/m.gguf",
                                                   port=8080, kv_cache=kv)
    for bad in (["install-unit", "/home/radxa/m.gguf", "80", kv], ["install-unit", "../m", "8080", kv],
                ["install-unit", "/m;id", "8080", kv], ["anything"], ["enable", "extra"]):
        refused = subprocess.run(["sh", str(helper), *bad], env=env, capture_output=True, text=True, timeout=30)
        assert refused.returncode == 126 and refused.stderr.startswith("llmcf-root: refused: ")
    root = subprocess.run(["sh", str(helper), "enable"], env={**env, "SUDO_USER": "root"}, capture_output=True,
                          text=True, timeout=30)
    assert root.returncode == 126


@POSIX
def test_a_session_learns_from_the_refusal_that_the_board_is_gated(board):
    """Every flow's commands pass the gate: the session sends the script form once, the gate refuses it, and the
    same operation goes again as a gate call (nothing ran in between)."""
    from orchestrator.remote.session import RemoteSession

    class Transport:
        sent = []

        def run(self, script, *, stdin=None, timeout=0, limit=0):
            from orchestrator.remote.transport import sh_command
            line = sh_command(script)
            self.sent.append(line)
            return board(line, b"".join(stdin or []))

        def alive(self):
            return True
    session = RemoteSession.attached(object(), transport_factory=lambda _c: Transport())
    root = f"{board.home}/{gate.PLACE_HOME_REL}/llama.cpp"
    folder = session.private_dir(root, ".bench")
    assert session.gated and folder.startswith(root + "/.bench.")
    sent = session.transport.sent
    assert sent[0].startswith("sh -c ") and sent[1].startswith("llmcf-gate mkprivate ")
    with pytest.raises(Exception) as not_an_op:
        session.run("rm -rf ~")
    assert getattr(not_an_op.value, "kind", "") == "not_an_operation"


class _LoginPerCommand:
    """A board reached as over the system OpenSSH transport: every command is a login of its own, so once the
    framework key's line in authorized_keys is the restricted one, the very next command already runs in the gate."""

    def __init__(self, home, bin_dir, gate_file):
        self.home, self.bin, self.gate_file, self.sent = home, bin_dir, gate_file, []

    def run(self, script, *, stdin=None, timeout=0, limit=0):
        from orchestrator.remote.transport import sh_command
        keys = self.home / ".ssh" / "authorized_keys"
        gated = keys.exists() and any(gate.is_restricted_line(line, self.gate_file.as_posix())
                                      for line in keys.read_text(encoding="utf-8").splitlines())
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(self.home)}
        argv = ["sh", "-c", str(script)]
        if gated:
            env["SSH_ORIGINAL_COMMAND"] = sh_command(script)
            argv = ["sh", str(self.gate_file)]
        self.sent.append(("gate" if gated else "shell", getattr(script, "verb", "")))
        done = subprocess.run(argv, input=b"".join(stdin or []), env=env, capture_output=True, timeout=60)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())

    def alive(self):
        return True


def _upgrade_board(tmp_path, monkeypatch, sudo_status):
    from orchestrator.remote.session import RemoteSession
    from orchestrator.utils import ssh_trust
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text(PUBLIC + "\n", encoding="utf-8")   # unrestricted, from before
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sudo.log"
    for name, body in (("sudo", f'echo "$*" >> "{log}"; cat >/dev/null; exit {sudo_status}'), ("visudo", "exit 0")):
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setattr(ssh_trust, "client_key", lambda _state: (None, PUBLIC))
    transport = _LoginPerCommand(home, bin_dir, home / gate.GATE_REL)
    session = RemoteSession.attached(object(), transport_factory=lambda _c: transport)
    session.can_relogin, session.state_dir = True, tmp_path
    return session, home, log


@POSIX
def test_the_upgrade_installs_the_root_helper_also_when_every_command_is_a_login_of_its_own(tmp_path, monkeypatch):
    """Over the system OpenSSH transport the gate is active from the command after gate-install on: the root helper
    and its sudo rules (a setup step the gate never runs) must be installed before the key is restricted."""
    session, home, log = _upgrade_board(tmp_path, monkeypatch, 0)
    order = []

    def root_board(run, line, package, password=""):   # the root step itself: tests/test_board_signing.py
        order.append(("root-board", board_ops.ROOT_HELPER in log.read_text(encoding="utf-8"), bool(package.signers)))
        return True, "", "removed"
    monkeypatch.setattr(gate, "install_root_board", root_board)
    notes = board_ops.restrict_key(session, sudo_user="radxa", signer=_signer())
    assert any("root helper and its exact sudo rules are installed" in n for n in notes), notes
    assert board_ops.KEY_ROOT_NOTE in notes, notes
    assert order == [("root-board", True, True)]   # the helper first, then the key's line in its root-owned place


@POSIX
def test_a_key_the_systemd_setting_needs_unrestricted_stays_so_when_the_root_helper_cannot_be_installed(
        tmp_path, monkeypatch):
    """The systemd setting over a key without llmcf's root helper: when the helper cannot be installed (sudo
    refuses), restricting the key would leave the next deploy unable to write the unit (a restricted key never runs
    the earlier sudo commands and cannot install the helper). The key stays as it was, with the plain reason."""
    session, home, _log = _upgrade_board(tmp_path, monkeypatch, 1)
    notes = board_ops.restrict_key(session, sudo_user="radxa")
    assert (home / ".ssh" / "authorized_keys").read_text(encoding="utf-8") == PUBLIC + "\n"
    assert any("NOT restricted" in n and "sudo refused" in n for n in notes), notes
    assert not session.gated


class _GatedClient(_Client):
    """A paramiko stand-in logged in with the framework key that already runs only the gate: every `sh -c` line is
    the gate's refusal (paramiko tries the framework key before the one-time password)."""

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        out = io.BytesIO(b"")

        class Channel:
            def recv_exit_status(self):
                return gate.REFUSED_STATUS

            def shutdown_write(self):
                pass
        out.channel = Channel()
        stdin = io.BytesIO()
        stdin.channel = out.channel
        err = io.BytesIO((gate.REFUSAL_PREFIX + "the line holds characters none of llmcf's operations takes\n").encode())
        return stdin, out, err


def test_pairing_a_board_whose_key_already_runs_the_gate_is_no_failure():
    """Pairing (or a deploy with the password) again over a board paired with the gate logs in with the framework
    key, whose gate refuses the setup step: the key is there and restricted, not "a board that cannot take the
    gate"."""
    from orchestrator.utils import ssh_trust
    client = _GatedClient("")
    assert ssh_trust.install_public_key(client, PUBLIC, board_ops.bundle(_signer())) == "restricted"
    assert len(client.commands) <= 2   # no plain unrestricted line after the refusal


def test_the_note_on_an_older_gate_names_a_way_that_works():
    """A restricted key cannot rewrite its own gate, and pairing again logs in with that key: the way to update it
    goes through the account's own login (its authorized_keys line), never only "pair again"."""
    class Shell:
        session = type("S", (), {"can_relogin": True, "state_dir": Path("."), "gated": True})()

        def run(self, script, **_k):
            return Result(0, "restricted=yes gate=0123456789abcdef\n", "")
    notes = board_ops.restrict_key(Shell())
    assert len(notes) == 1 and "authorized_keys" in notes[0] and "older" in notes[0]


def test_the_sudoers_file_name_has_no_dot_sudo_would_skip():
    """sudo's #includedir skips a file whose name holds a '.', so a board account such as first.last would get
    rules that never apply."""
    path = board_ops.sudoers_path("first.last")
    assert "." not in path.rsplit("/", 1)[1]
    assert path in gate.script("root-setup", "first.last")
