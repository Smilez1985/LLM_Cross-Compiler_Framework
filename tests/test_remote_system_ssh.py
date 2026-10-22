"""The OpenSSH fallback of the deploy (consolidation chain c2-remote-system, finding 3 of the architecture audit).

Characterization first: these tests pin what the fallback guarantees today, through the public deploy_package with
paramiko unavailable and a fake OpenSSH client in front of the fake board of tests/test_board_deploy.py. The fake
stands in for the `ssh` and `scp` programs at the one place every child process starts (subprocess.Popen, which
subprocess.run uses too), so the same tests hold before and after the fallback becomes a second transport:

- every OpenSSH call carries the framework key only and the framework's known_hosts only, never asks and never takes
  a new key (BatchMode, StrictHostKeyChecking, IdentitiesOnly, UserKnownHostsFile, -i)
- no call hands this process's stdin to OpenSSH (over MCP stdio it is the protocol stream), none goes through a shell
- the old Qdrant sidecar on the board is only looked at, never removed
- a deploy bound to a fingerprint the person was shown is refused without paramiko (OpenSSH cannot compare it)
- what OpenSSH prints gives the reason code (reasons.classify_ssh_output)
- the bytes that reach the board are the bytes whose sha256 was checked; a changed package reaches nothing
"""

import errno
import hashlib
import io
import shlex
import subprocess
import threading
from pathlib import Path

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.Core import deployment_manager as dmod  # noqa: E402
from orchestrator.Core.deployment_manager import DeploymentManager  # noqa: E402
from orchestrator.runtime.device import reasons  # noqa: E402
from orchestrator.utils import ssh_trust  # noqa: E402

import test_board_deploy as tbd  # noqa: E402
from test_board_deploy import HOST, TARGET  # noqa: E402
from test_deployment import FakeFramework, config  # noqa: E402,F401 - the manager's configuration

USER = "radxa"
# OpenSSH options that take a value (ssh(1), scp(1)); every other option is a flag
VALUE_OPTIONS = {"-o", "-i", "-p", "-P", "-F", "-l", "-c", "-m", "-J", "-E", "-S", "-b", "-D", "-L", "-R", "-W"}
ATTACHED_INSTALL = "sh deploy.sh; status=$?"   # the 1.x fallback ran deploy.sh attached to the session
DEPLOY_LINES = "[1/3] Checking Integrity...\nDeployment Complete. System is ready.\n"


# --- the board behind OpenSSH ---------------------------------------------------------------------------------------

class OpenSshBoard(tbd.Board):
    """test_board_deploy's fake board as OpenSSH reaches it: a login (`true`), files written from stdin (`cat > f`,
    `cat >> f`), a file's size and the 1.x fallback's attached installation; everything else as the board there."""

    def __init__(self, clock):
        super().__init__(clock)
        self.install_status = 0         # how deploy.sh ends on the board

    def run(self, command):
        if command == "true":
            self.logins += 1
            return 0, ""
        if command.startswith("if [ -f ") and "wc -c" in command:
            path = shlex.split(command)[3]
            return 0, (f"{len(self.files[path])}\n" if path in self.files else "none\n")
        if ATTACHED_INSTALL in command:
            self.starts += 1
            self.dirs.discard(TARGET)
            return self.install_status, DEPLOY_LINES
        if command.startswith(f"cat {TARGET}/{dmod.INSTALL_STATUS}"):
            status, out = super().run(command)
            return status, (f"{self.install_status}\n" if out == "0\n" else out)
        return super().run(command)


class Call:
    """One OpenSSH program as it was started: its words, the options and how this process started it."""

    def __init__(self, argv, kwargs):
        self.argv, self.kwargs = list(argv), dict(kwargs)
        self.program = Path(str(argv[0])).name.lower().removesuffix(".exe")
        self.options, self.flags, rest = [], [], []
        words = list(argv[1:])
        while words:
            word = words.pop(0)
            if rest or not word.startswith("-"):
                rest.append(word)
            elif word in VALUE_OPTIONS:
                self.options.append((word, words.pop(0)))
            else:
                self.flags.append(word)
        self.rest = rest
        self.wrapped = False
        self.script = ""
        if self.program == "ssh":
            command = " ".join(rest[1:])
            words = shlex.split(command) if command else []
            if len(words) == 3 and words[:2] == ["sh", "-c"] and command == "sh -c " + shlex.quote(words[2]):
                self.wrapped, self.script = True, words[2]
            else:
                self.script = command

    def option(self, name):
        """The values of `-o name=...` (OpenSSH's own quoting removed) or of a value option such as -i."""
        if name.startswith("-"):
            return [value for flag, value in self.options if flag == name]
        found = []
        for flag, value in self.options:
            key, _, val = value.partition("=")
            if flag == "-o" and key.lower() == name.lower():
                found.append(unquote_config(val))
        return found


def unquote_config(value):
    """An OpenSSH option value as its parser reads it (misc.c argv_split): double or single quotes group, a backslash
    escapes a quote, a backslash or (outside quotes) a space; any other backslash stays."""
    out, quote, i = [], "", 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and (value[i + 1] in "'\"\\" or (not quote and value[i + 1] == " ")):
            out.append(value[i + 1])
            i += 2
            continue
        if not quote and ch in "'\"":
            quote = ch
        elif quote and ch == quote:
            quote = ""
        elif not quote and ch in " \t":
            raise AssertionError(f"an unquoted space splits the option value: {value!r}")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class Pipe:
    """A child process's stdout or stderr: read() waits until the process has ended, then serves what it printed."""

    def __init__(self, process):
        self.process, self.data = process, b""

    def read(self, n=-1):
        self.process.ended.wait(30)
        chunk, self.data = (self.data, b"") if n is None or n < 0 else (self.data[:n], self.data[n:])
        return chunk

    def close(self):
        pass


class StdinWriter:
    """The stdin pipe of `ssh ... cat > FILE`: what this process writes lands in the board's file, until the network
    drops (board.drop_after) or the board's command ends."""

    def __init__(self, process, board, path, append):
        self.process, self.board, self.path = process, board, path
        if not append:
            board.files[path] = bytearray()
        elif path not in board.files:
            process.end(1, b"", f"sh: {path}: No such file\n".encode())
        self.closed = False

    def write(self, data):
        board, process = self.board, self.process
        if process.ended.is_set() or not board.online:
            if not process.ended.is_set():
                process.end(255, b"", b"client_loop: send disconnect: Broken pipe\r\n")
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        if board.disk_full:
            process.end(1, b"", b"cat: write error: No space left on device\n")
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        cut = len(data)
        if board.drop_after is not None and board.sent + len(data) > board.drop_after:
            cut = max(0, board.drop_after - board.sent)
        board.files[self.path].extend(data[:cut])
        board.sent += cut
        if cut < len(data):
            board.drop_after = None
            board.go_offline()
            process.end(255, b"", b"client_loop: send disconnect: Broken pipe\r\n")
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        return len(data)

    def flush(self):
        if self.process.ended.is_set() and self.process.returncode:
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    def close(self):
        if not self.closed:
            self.closed = True
            if not self.process.ended.is_set():
                self.process.end(0, b"", b"")


class FakeProcess:
    """One started `ssh` or `scp` against the board (the subset of subprocess.Popen the framework uses)."""

    def __init__(self, fake, call):
        self.fake, self.call, self.args = fake, call, call.argv
        kwargs = call.kwargs
        self.text = bool(kwargs.get("text") or kwargs.get("universal_newlines"))
        self.returncode = None
        self.ended = threading.Event()
        self.stdout = Pipe(self) if kwargs.get("stdout") is subprocess.PIPE else None
        self.stderr = Pipe(self) if kwargs.get("stderr") is subprocess.PIPE else None
        self.stdin = None
        fake.start(self)

    def end(self, status, out=b"", err=b""):
        if self.ended.is_set():
            return
        self.returncode = status
        if self.stdout is not None:
            self.stdout.data = out
        if self.stderr is not None:
            self.stderr.data = err
        self.ended.set()

    def communicate(self, input=None, timeout=None):
        if self.stdin is not None:
            if input:
                try:
                    self.stdin.write(input.encode() if isinstance(input, str) else input)
                except BrokenPipeError:
                    pass
            self.stdin.close()
        self.ended.wait(30)
        out = self.stdout.read() if self.stdout is not None else None
        err = self.stderr.read() if self.stderr is not None else None
        if self.text:
            out = out.decode() if out is not None else None
            err = err.decode() if err is not None else None
        return out, err

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.ended.is_set() and self.stdin is not None and self.stdin.closed:
            self.end(0)
        if not self.ended.wait(0 if timeout is None and self.stdin is not None and not self.stdin.closed else
                               (30 if timeout is None else timeout)):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self):
        self.fake.killed.append(self.call)
        self.end(-9)

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.stdin is not None:
            self.stdin.close()
        self.ended.wait(30)
        return False


class FakeOpenSSH:
    """`ssh` and `scp` in front of the board: every start is recorded (calls); the board answers each command. A
    board that is off the network (or `fail`, an answer OpenSSH itself gives) makes ssh exit 255 with its words."""

    def __init__(self, board, real_popen):
        self.board, self.real_popen = board, real_popen
        self.calls = []
        self.killed = []
        self.fail = None                 # (status, stderr) the next OpenSSH call ends with instead of reaching the board
        self.fail_count = 0              # ... for this many calls (0: every call)
        self.down_text = b"ssh: connect to host " + HOST.encode() + b" port 22: Connection timed out\r\n"

    def popen(self, args, *a, **kwargs):
        argv = list(args) if not isinstance(args, (str, bytes)) else [args]
        program = Path(str(argv[0])).name.lower().removesuffix(".exe")
        if program not in ("ssh", "scp", "sftp"):
            return self.real_popen(args, *a, **kwargs)
        call = Call(argv, kwargs)
        self.calls.append(call)
        return FakeProcess(self, call)

    def start(self, process):
        call, board = process.call, self.board
        assert not call.kwargs.get("shell"), "OpenSSH is never started through a shell"
        board.tick()
        if self.fail is not None:
            status, text = self.fail
            if self.fail_count:
                self.fail_count -= 1
                if not self.fail_count:
                    self.fail = None
            process.end(status, b"", text.encode())
            return
        if not board.online:
            process.end(255, b"", self.down_text)
            return
        if call.program == "scp":
            source, target = call.rest[-2], call.rest[-1]
            folder = target.partition(":")[2].rstrip("/")
            board.files[f"{folder}/{Path(source).name}"] = bytearray(Path(source).read_bytes())
            process.end(0)
            return
        script = call.script
        if call.kwargs.get("stdin") is subprocess.PIPE and script.startswith("cat >"):
            append = script.startswith("cat >>")
            path = shlex.split(script)[2]
            process.stdin = StdinWriter(process, board, path, append)
            return
        if call.kwargs.get("stdin") is subprocess.PIPE:
            process.stdin = io.BytesIO()
            process.stdin.closed_flag = False
        board.wire.append(script)
        try:
            status, out = board.run(script)
        except tbd.Gone:
            process.end(255, b"", b"Connection to " + HOST.encode() + b" closed by remote host.\r\n")
            return
        process.end(status, out.encode(), b"")

    def ssh_calls(self):
        return [c for c in self.calls if c.program == "ssh"]


@pytest.fixture
def system_board(monkeypatch):
    """The fake board behind a fake OpenSSH, with paramiko unavailable to the deploy (its OpenSSH fallback)."""
    clock = tbd.Clock()
    board = OpenSshBoard(clock)
    fake = FakeOpenSSH(board, subprocess.Popen)
    monkeypatch.setattr(subprocess, "Popen", fake.popen)
    monkeypatch.setattr(dmod, "PARAMIKO_AVAILABLE", False)
    monkeypatch.setattr(reasons, "check_port",
                        lambda host, port=22, timeout=3.0: None if board.online else reasons.HOST_DOWN)

    def no_paramiko_login(*a, **k):
        raise AssertionError("the OpenSSH fallback never logs in with paramiko")
    monkeypatch.setattr(ssh_trust, "connect", no_paramiko_login)
    monkeypatch.setattr(ssh_trust, "fetch_host_key", no_paramiko_login)
    board.fake = fake
    return board


@pytest.fixture
def sys_manager(config, system_board):
    dm = DeploymentManager(FakeFramework(config))
    clock = system_board.clock
    dm._clock = clock.time

    def sleep(seconds):
        clock.sleep(seconds)
        system_board.tick()
    dm._sleep = sleep
    return dm


@pytest.fixture
def package(tmp_path):
    pkg = tmp_path / "deploy_model_1.zip"
    pkg.write_bytes(bytes(range(256)) * 12000)  # about 3 MB: several upload chunks
    return pkg


def deploy(manager, package, **kw):
    events = []
    result = manager.deploy_package(package, HOST, USER, on_event=events.append, **kw)
    return result, events


# --- characterization: what the fallback guarantees today ------------------------------------------------------------

class TestTheFallbacksGuarantees:

    def test_a_clean_deploy_installs_the_package(self, sys_manager, system_board, package):
        result, _events = deploy(sys_manager, package)
        assert result.success and result.exit_status == 0 and result.device_state == "ok", result.reason
        assert system_board.starts == 1
        assert "Deployment Complete. System is ready." in result.output
        assert [c.program for c in system_board.fake.calls][:1] == ["ssh"]

    def test_every_call_uses_the_framework_key_and_known_hosts_only(self, sys_manager, system_board, package):
        assert deploy(sys_manager, package)[0].success
        state = sys_manager._state_dir()
        calls = system_board.fake.calls
        assert calls and {c.program for c in calls} <= {"ssh", "scp"}
        for call in calls:
            assert call.option("BatchMode") == ["yes"], "OpenSSH never asks for a password or a passphrase"
            assert call.option("StrictHostKeyChecking") == ["yes"], "a new or changed host key is never taken"
            assert call.option("IdentitiesOnly") == ["yes"]
            assert call.option("UserKnownHostsFile") == [str(ssh_trust.known_hosts_path(state))]
            assert call.option("-i") == [str(state / ssh_trust.KEY_NAME)]

    def test_no_call_inherits_this_process_stdin_or_goes_through_a_shell(self, sys_manager, system_board, package):
        assert deploy(sys_manager, package)[0].success
        for call in system_board.fake.calls:
            assert not call.kwargs.get("shell") and isinstance(call.argv, list)
            # over MCP stdio this process's stdin is the protocol stream: OpenSSH gets nothing or a pipe of ours
            assert call.kwargs.get("stdin") in (subprocess.DEVNULL, subprocess.PIPE), call.argv

    @pytest.mark.parametrize("docker_ps, state", [("", ""), ("qdrant-sidecar|Up 3 days\n", "running"),
                                                  ("qdrant-sidecar|Exited (0) 2 days ago\n", "stopped")])
    def test_the_old_sidecar_is_only_looked_at(self, sys_manager, system_board, package, docker_ps, state):
        system_board.docker_ps = docker_ps
        result, _events = deploy(sys_manager, package)
        assert result.success and result.old_sidecar == state
        # the fake board refuses every other Docker command: the look is the only one, never a removal
        assert system_board.docker_commands == [dmod.OLD_SIDECAR_CHECK]
        assert bool(result.notes) == bool(state)

    def test_a_deploy_bound_to_a_shown_fingerprint_is_refused(self, sys_manager, system_board, package):
        result, _events = deploy(sys_manager, package, expect_fingerprint="SHA256:" + "A" * 43)
        assert not result.success and system_board.fake.calls == []
        assert result.reason == ("Checking the device's host key against the fingerprint shown needs the 'paramiko' "
                                 "package; nothing was deployed.")

    @pytest.mark.parametrize("text, code", [
        (f"ssh: connect to host {HOST} port 22: Connection refused", reasons.PORT_CLOSED),
        (f"{USER}@{HOST}: Permission denied (publickey).", reasons.AUTH_FAILED),
        (f"ssh: Could not resolve hostname {HOST}: Name or service not known", reasons.HOST_DOWN),
        ("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
         "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
         "Host key verification failed.", reasons.HOST_KEY_CHANGED),
    ])
    def test_what_openssh_prints_gives_the_reason_code(self, sys_manager, system_board, package, text, code):
        system_board.fake.fail = (255, text + "\r\n")
        result, _events = deploy(sys_manager, package)
        assert not result.success and result.code == code
        assert system_board.starts == 0 and not system_board.files

    def test_a_package_that_changed_after_it_was_shown_reaches_nothing(self, sys_manager, system_board, package):
        result, _events = deploy(sys_manager, package, expect_sha256="0" * 64)
        assert not result.success and system_board.fake.calls == []
        assert result.reason == ("The package deploy_model_1.zip changed after it was shown (its sha256 differs); "
                                 "nothing was deployed.")

    def test_the_bytes_on_the_board_are_the_checked_bytes(self, sys_manager, system_board, package):
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        result, _events = deploy(sys_manager, package, expect_sha256=digest)
        assert result.success
        arrived = system_board.files[f"{TARGET}/{package.name}"]
        assert hashlib.sha256(bytes(arrived)).hexdigest() == digest

    def test_a_failing_deploy_sh_fails_the_deploy(self, sys_manager, system_board, package):
        system_board.install_status = 3
        result, _events = deploy(sys_manager, package)
        assert not result.success and result.exit_status == 3 and result.device_state == "failed"
        assert result.reason == "deploy.sh failed on the device (exit 3)."
