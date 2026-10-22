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
import os
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
        self.fail = None                 # (status, stderr): what OpenSSH answers instead of reaching the board
        self.fail_count = 0              # ... for this many calls (0: every call)
        self.hang = None                 # a script for which ssh never answers (until it is killed)
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
        if self.hang is not None and self.hang(call.script):
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


def openssh_board(monkeypatch):
    """The fake board behind a fake OpenSSH, with paramiko unavailable to the deploy (its OpenSSH fallback); its
    `fake` holds the OpenSSH calls (tests/test_deployment.py uses it too)."""
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
def system_board(monkeypatch):
    return openssh_board(monkeypatch)


def on_fake_clock(manager, board):
    """The manager waits on the board's fake clock (no real sleeping)."""
    clock = board.clock
    manager._clock = clock.time

    def sleep(seconds):
        clock.sleep(seconds)
        board.tick()
    manager._sleep = sleep
    return manager


@pytest.fixture
def sys_manager(config, system_board):
    return on_fake_clock(DeploymentManager(FakeFramework(config)), system_board)


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
        known_hosts = ssh_trust.known_hosts_path(state)
        calls = system_board.fake.calls
        assert calls and {c.program for c in calls} <= {"ssh", "scp"}
        for call in calls:
            assert call.option("BatchMode") == ["yes"], "OpenSSH never asks for a password or a passphrase"
            assert call.option("StrictHostKeyChecking") == ["yes"], "a new or changed host key is never taken"
            assert call.option("IdentitiesOnly") == ["yes"]
            # the path as OpenSSH reads it back (the 1.x code passed it as it is; a Windows path now with /)
            assert call.option("UserKnownHostsFile") in ([str(known_hosts)], [known_hosts.as_posix()])
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


# --- A5: the fallback is a second transport (waiting, resume, detached install, timeouts, keepalive, quoting) --------

class TestTheSecondTransport:
    """Since decision A5 the fallback is the board session of the paramiko deploy over OpenSSH
    (remote/transport.py SystemSshTransport): what the paramiko deploy does after a network drop, it does too."""

    def test_every_command_reaches_the_board_as_one_sh_c(self, sys_manager, system_board, package):
        assert deploy(sys_manager, package)[0].success
        calls = system_board.fake.ssh_calls()
        assert calls and all(call.wrapped for call in calls), [c.script for c in calls if not c.wrapped]
        assert {c.program for c in system_board.fake.calls} == {"ssh"}, "the upload streams into cat, no scp"

    def test_every_call_gives_up_on_a_dead_network(self, sys_manager, system_board, package):
        from orchestrator.remote import transport
        assert deploy(sys_manager, package)[0].success
        for call in system_board.fake.calls:
            assert call.option("ConnectTimeout") == [str(int(transport.LOGIN_TIMEOUT))]
            assert call.option("ServerAliveInterval") == [str(transport.KEEPALIVE_SECONDS)]
            assert call.option("ServerAliveCountMax") == [str(transport.KEEPALIVE_COUNT)]
            assert "-T" in call.flags and call.option("-p") == ["22"]

    def test_a_state_folder_with_a_space_stays_one_known_hosts_file(self, config, monkeypatch, tmp_path, package):
        """UserKnownHostsFile takes several files separated by spaces: unquoted, a Windows account name with a space
        split the framework's known_hosts into two files that do not exist, and every host key was 'unknown'."""
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "Jane Doe" / ".llm-framework"))
        board = openssh_board(monkeypatch)
        manager = on_fake_clock(DeploymentManager(FakeFramework(config)), board)
        assert deploy(manager, package)[0].success
        known_hosts = ssh_trust.known_hosts_path(manager._state_dir())
        assert " " in str(known_hosts)
        written = known_hosts.as_posix() if os.name == "nt" else str(known_hosts)   # OpenSSH for Windows takes /
        for call in board.fake.calls:
            raw = [value for flag, value in call.options if value.startswith("UserKnownHostsFile=")]
            assert raw == [f'UserKnownHostsFile="{written}"']
            assert call.option("UserKnownHostsFile") == [written]

    def test_an_upload_that_drops_waits_for_the_board_and_resumes(self, sys_manager, system_board, package):
        system_board.drop_after = 1_300_000
        system_board.back_at = 42.0
        result, events = deploy(sys_manager, package)
        assert result.success and result.resumed == 1 and result.device_state == "ok", result.reason
        assert bytes(system_board.files[f"{TARGET}/{package.name}"]) == package.read_bytes()
        assert system_board.sent == package.stat().st_size, "nothing was sent twice"
        assert system_board.starts == 1
        waiting = [e for e in events if e.kind == "waiting"]
        assert waiting and all(e.step == "upload" and e.limit == dmod.DEPLOY_WAIT_SECONDS for e in waiting)
        assert [e.done for e in events if e.kind == "back"] == [1_300_000]
        prefix = [c.script for c in system_board.fake.ssh_calls() if c.script.startswith("head -c ")]
        assert prefix == [f"head -c 1300000 {TARGET}/{package.name}.part | sha256sum"]
        appended = [c.script for c in system_board.fake.ssh_calls() if c.script.startswith("cat >")]
        assert appended == [f"cat > {TARGET}/{package.name}.part", f"cat >> {TARGET}/{package.name}.part"]
        assert result.waited_s >= 40

    def test_the_wait_takes_wait_seconds_and_says_why_it_ended(self, sys_manager, system_board, package):
        system_board.drop_after = 500_000
        result, events = deploy(sys_manager, package, wait_seconds=60)
        assert not result.success and result.code == reasons.HOST_DOWN and result.device_state == "ok"
        assert "60 s" in result.reason and "deploy.sh did not run" in result.reason
        assert system_board.starts == 0
        assert max(e.elapsed for e in events if e.kind == "waiting") >= 55

    def test_deploy_sh_runs_on_its_own_and_is_followed_again_after_a_drop(self, sys_manager, system_board, package):
        system_board.drop_on_poll = 1
        system_board.back_at = 20.0
        system_board.install_polls_needed = 3
        result, events = deploy(sys_manager, package)
        assert result.success and system_board.starts == 1 and result.resumed == 1, result.reason
        starts = [c.script for c in system_board.fake.ssh_calls() if "nohup" in c.script]
        assert starts == [DeploymentManager._remote_start_cmd(TARGET, package.name)], \
            "deploy.sh starts once, detached from the session (nohup behind a marker), never attached"
        assert not [c for c in system_board.fake.ssh_calls() if ATTACHED_INSTALL in c.script]
        assert [(e.kind, e.step) for e in events if e.kind == "back"] == [("back", "install")]

    def test_cancel_while_waiting_installs_nothing(self, sys_manager, system_board, package):
        system_board.drop_after = 200_000
        waited = []
        result = sys_manager.deploy_package(package, HOST, USER, cancelled=lambda: len(waited) >= 3,
                                            on_event=lambda e: waited.append(e) if e.kind == "waiting" else None)
        assert not result.success and system_board.starts == 0 and result.device_state == "ok"
        assert "cancelled" in result.reason

    def test_each_session_reaches_on_client_and_its_close_ends_the_running_call(self, sys_manager, system_board,
                                                                                 package):
        from orchestrator.remote.transport import SystemSshTransport
        sessions = []
        result, _events = deploy(sys_manager, package, on_client=sessions.append)
        assert result.success and len(sessions) == 1 and isinstance(sessions[0], SystemSshTransport)

        system_board.files.clear()
        system_board.dirs.clear()
        system_board.starts = 0
        system_board.fake.hang = lambda script: script.startswith("sha256sum")   # the board stays silent

        def on_client(session):
            threading.Timer(0.3, session.close).start()   # a Cancel button elsewhere
        result = sys_manager.deploy_package(package, HOST, USER, on_client=on_client,
                                            cancelled=lambda: bool(system_board.fake.killed))
        assert not result.success and system_board.starts == 0 and "cancelled" in result.reason
        assert {c.script.split()[0] for c in system_board.fake.killed} == {"sha256sum"}

    def test_a_password_is_said_not_to_be_used_instead_of_being_dropped(self, sys_manager, system_board, package):
        result, _events = deploy(sys_manager, package, password="one-time")
        assert result.success and result.notes == [dmod.NO_PASSWORD_NOTE]
        assert all("one-time" not in " ".join(c.argv) for c in system_board.fake.calls), "never on a command line"

        system_board.fake.fail = (255, f"{USER}@{HOST}: Permission denied (publickey).\r\n")
        result, _events = deploy(sys_manager, package, password="one-time")
        assert not result.success and result.code == reasons.AUTH_FAILED
        assert result.reason.endswith(dmod.NO_PASSWORD_NOTE)

    def test_a_full_disk_on_the_board_is_not_a_network_drop(self, sys_manager, system_board, package):
        system_board.disk_full = True
        result, events = deploy(sys_manager, package)
        assert not result.success and "upload" in result.reason and result.resumed == 0
        assert not [e for e in events if e.kind == "waiting"] and system_board.starts == 0
        assert "No space left" not in result.reason, "the board's words stay out of the reason"

    def test_an_unknown_host_key_is_not_trusted_yet(self, sys_manager, system_board, package):
        system_board.fake.fail = (255, f"No ED25519 host key is known for {HOST} and you have requested strict "
                                       "checking.\r\nHost key verification failed.\r\n")
        result, _events = deploy(sys_manager, package)
        assert not result.success and result.code == ""
        assert result.reason == f"The device {HOST} is not trusted yet: confirm its host key fingerprint first."

    def test_a_refused_key_file_is_a_problem_on_this_computer(self, sys_manager, system_board, package):
        system_board.fake.fail = (255, "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\r\n"
                                       "@         WARNING: UNPROTECTED PRIVATE KEY FILE!          @\r\n"
                                       "Load key \"x\": bad permissions\r\n")
        result, _events = deploy(sys_manager, package)
        assert not result.success and result.code == reasons.LOCAL_PROBLEM
        assert ssh_trust.KEY_NAME in result.reason and str(sys_manager._state_dir()) not in result.reason

    def test_a_board_that_restarts_its_ssh_server_while_waited_for_is_not_back_yet(self, sys_manager,
                                                                                   system_board, package):
        """While the deploy waits, a connection that opens and drops again (sshd starting) keeps it waiting."""
        system_board.drop_after = 900_000
        system_board.back_at = 10.0
        real_start = system_board.fake.start
        resets = {"left": 2}

        def start(process):
            if system_board.online and resets["left"] and process.call.script == "true" and system_board.logins:
                resets["left"] -= 1
                process.end(255, b"", b"kex_exchange_identification: read: Connection reset by peer\r\n")
                return
            real_start(process)
        system_board.fake.start = start
        result, _events = deploy(sys_manager, package)
        assert result.success and result.resumed == 1 and resets["left"] == 0, result.reason


# --- the transport itself ---------------------------------------------------------------------------------------

class TestTheOpenSshTransport:

    @staticmethod
    def endpoint(host="192.168.1.50", port=22):
        from orchestrator.remote.address import Endpoint
        return Endpoint(host, port)

    def test_the_one_argv_builder(self):
        from orchestrator.remote.transport import openssh_argv, remote_location
        options = ["-o", "BatchMode=yes"]
        assert openssh_argv("ssh", self.endpoint(port=2222), USER, options, "sh -c true") == \
            ["ssh", "-o", "BatchMode=yes", "-T", "-p", "2222", f"{USER}@192.168.1.50", "sh -c true"]
        v6 = self.endpoint("fe80::1")
        assert openssh_argv("ssh", v6, USER, options)[-1] == f"{USER}@fe80::1"
        assert openssh_argv("scp", v6, USER, options, "pkg.zip", remote_location(v6, USER, "/tmp/x/")) == \
            ["scp", "-o", "BatchMode=yes", "-P", "22", "pkg.zip", f"{USER}@[fe80::1]:/tmp/x/"]
        assert openssh_argv("sftp", v6, USER, options, remote_location(v6, USER))[-3:] == \
            ["-P", "22", f"{USER}@[fe80::1]"]
        with pytest.raises(ValueError):
            openssh_argv("rsh", v6, USER, options)
        assert openssh_argv("/usr/local/bin/ssh", v6, USER, options)[:1] == ["/usr/local/bin/ssh"]
        assert openssh_argv(r"C:\Program Files\OpenSSH\scp.exe", v6, USER, options, "a", "b")[-4:] == \
            ["-P", "22", "a", "b"]

    @pytest.mark.parametrize("path, value", [
        ("/home/radxa/.llm-framework/known_hosts", '"/home/radxa/.llm-framework/known_hosts"'),
        ("/srv/a b/known_hosts", '"/srv/a b/known_hosts"'),
        ('/srv/a"b\\c/known_hosts', '"/srv/a\\"b\\\\c/known_hosts"'),
    ])
    def test_a_path_is_one_quoted_option_value(self, path, value):
        from orchestrator.remote.transport import config_value
        assert config_value(path) == value
        assert unquote_config(value) == str(path), "OpenSSH's parser reads back the same path"

    def test_a_windows_path_is_written_with_forward_slashes(self):
        from pathlib import PureWindowsPath

        from orchestrator.remote.transport import config_value
        path = PureWindowsPath(r"C:\Users\Jane Doe\.llm-framework\known_hosts")
        assert config_value(path) == '"C:/Users/Jane Doe/.llm-framework/known_hosts"'
        with pytest.raises(AssertionError, match="splits"):
            unquote_config(f"{path}")   # what the option carried before: two files

    def test_the_old_option_name_is_the_one_list(self, tmp_path):
        from orchestrator.remote import transport
        assert ssh_trust.system_ssh_options(tmp_path) == transport.openssh_options(tmp_path)

    @pytest.mark.parametrize("text, reason, unknown, local", [
        (f"ssh: connect to host {HOST} port 22: Connection refused", reasons.PORT_CLOSED, False, False),
        (f"{USER}@{HOST}: Permission denied (publickey).", reasons.AUTH_FAILED, False, False),
        ("@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @\nHost key verification failed.",
         reasons.HOST_KEY_CHANGED, False, False),
        (f"No ED25519 host key is known for {HOST} and you have requested strict checking.\n"
         "Host key verification failed.", "", True, False),
        ("Load key \"/x/id\": bad permissions", "", False, True),
        ("client_loop: send disconnect: Broken pipe", "", False, False),
        ("", "", False, False),
    ])
    def test_what_openssh_said(self, text, reason, unknown, local):
        from orchestrator.remote.transport import SshFailed
        failure = SshFailed.of(text, key_file="/state/id_ed25519_llm_framework")
        assert isinstance(failure, OSError) and failure.reason == reason and failure.unknown_key is unknown
        assert bool(failure.filename) is local
        expected = reason or (reasons.LOCAL_PROBLEM if local else "" if unknown else reasons.HOST_DOWN)
        assert reasons.reach_code(failure) == expected, "the first contact is no reason code (as for paramiko)"
        assert text.strip() == "" or failure.output, "OpenSSH's words are kept apart"
        assert HOST not in str(failure) and USER not in str(failure)

    @staticmethod
    def make(tmp_path):
        from orchestrator.remote.transport import SystemSshTransport
        return SystemSshTransport(TestTheOpenSshTransport.endpoint(HOST), USER, tmp_path)

    def test_a_silent_board_ends_the_call_at_its_time_limit(self, system_board, tmp_path):
        from orchestrator.remote.transport import timed_out
        transport = self.make(tmp_path)
        transport.login()
        system_board.fake.hang = lambda script: script == "sleep 1000"
        with pytest.raises(TimeoutError) as e:
            transport.run("sleep 1000", timeout=0.3)
        assert timed_out(e.value), "RemoteSession waits through it like a drop"
        assert {c.script for c in system_board.fake.killed} == {"sleep 1000"}

    def test_more_output_than_the_cap_ends_the_call(self, system_board, tmp_path):
        from orchestrator.remote.transport import TooMuchOutput
        transport = self.make(tmp_path)
        system_board.run = lambda command: (0, "x" * 5000)
        with pytest.raises(TooMuchOutput):
            transport.run("yes", limit=1000)
        assert transport.run("yes", limit=10_000).out == "x" * 5000

    def test_alive_follows_what_the_last_call_found(self, system_board, tmp_path):
        from orchestrator.remote.transport import SshFailed
        transport = self.make(tmp_path)
        assert not transport.alive(), "nothing reached the board yet"
        transport.login()
        assert transport.alive() and system_board.logins == 1
        assert transport.run("[ -d /nowhere ]").status == 1 and transport.alive(), "the board's own answer"
        system_board.go_offline()
        with pytest.raises(SshFailed) as e:
            transport.run("true")
        assert e.value.reason == reasons.HOST_DOWN and not transport.alive()
        system_board.online = True
        transport.close()
        with pytest.raises(SshFailed):
            transport.run("true")
        assert not transport.alive()

    def test_stdin_is_a_pipe_of_ours(self, system_board, tmp_path):
        transport = self.make(tmp_path)
        system_board.run = lambda command: (0, "done\n")
        assert transport.run("wc -c", stdin=[b"probe"]).out == "done\n"
        call = system_board.fake.calls[-1]
        assert call.kwargs["stdin"] is subprocess.PIPE and call.kwargs["stdout"] is subprocess.PIPE


# --- the framework key: private to this account before OpenSSH uses it --------------------------------------------

def readable_key(path):
    """Whether `path` holds an Ed25519 private key this account can read (loaded with cryptography)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return isinstance(serialization.load_ssh_private_key(Path(path).read_bytes(), None), Ed25519PrivateKey)


POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes (the Windows way is tested below)")


class TestTheFrameworkKeyIsOwnerOnly:

    @POSIX_ONLY
    @pytest.mark.parametrize("before, after", [(0o644, 0o600), (0o640, 0o600), (0o604, 0o600), (0o600, 0o600),
                                               (0o400, 0o400), (0o755, 0o700)])
    def test_posix_keeps_the_owner_bits_and_drops_the_others(self, tmp_path, before, after):
        from orchestrator.utils.atomic import owner_only
        path = tmp_path / "secret"
        path.write_text("x")
        os.chmod(path, before)
        owner_only(path)
        assert path.stat().st_mode & 0o777 == after

    @POSIX_ONLY
    def test_a_key_others_can_read_is_narrowed_before_openssh_uses_it(self, tmp_path):
        from orchestrator.remote import transport
        private, _ = ssh_trust.client_key(tmp_path)
        assert private.stat().st_mode & 0o777 == 0o600
        os.chmod(private, 0o644)   # copied with another umask, for example
        options = transport.openssh_options(tmp_path)
        assert private.stat().st_mode & 0o777 == 0o600 and options[-2:] == ["-i", str(private)]

    def test_the_key_is_narrowed_when_it_is_made_and_before_each_openssh_session(self, tmp_path, monkeypatch):
        from orchestrator.remote import transport
        from orchestrator.utils import atomic
        narrowed = []
        monkeypatch.setattr(atomic, "owner_only", lambda path: narrowed.append(Path(path)))
        private, _ = ssh_trust.client_key(tmp_path)
        assert narrowed == [private], "made private before the key is written into it"
        transport.openssh_options(tmp_path)
        assert narrowed == [private, private]

    def test_a_key_that_cannot_be_narrowed_still_works_and_is_logged(self, tmp_path, monkeypatch, caplog):
        from orchestrator.remote import transport
        from orchestrator.utils import atomic

        def refuse(path):
            raise PermissionError(13, "Access is denied", str(path))
        monkeypatch.setattr(atomic, "owner_only", refuse)
        private, _ = ssh_trust.client_key(tmp_path)
        assert readable_key(private)
        assert transport.openssh_options(tmp_path)[-1] == str(private)
        assert "could not be made private" in caplog.text and str(tmp_path) not in caplog.text

    def test_windows_gets_a_protected_acl_with_this_account_alone(self, tmp_path, monkeypatch):
        """The Windows way, with fakes of advapi32 and kernel32 (it runs for real in the next test on Windows)."""
        import ctypes

        from orchestrator.utils import atomic
        sid = ctypes.create_string_buffer(b"\x01\x05" + b"S" * 26)   # a SID of 28 bytes
        calls, seen = [], {}

        class Fn:
            def __init__(self, name, impl):
                self.name, self.impl = name, impl

            def __call__(self, *args):
                calls.append(self.name)
                return self.impl(*args)

        def open_token(process, access, token):
            assert access == atomic._TOKEN_QUERY
            token._obj.value = 4242
            return 1

        def token_information(token, kind, buffer, size, needed):
            assert token.value == 4242 and kind == atomic._TOKEN_USER
            if buffer is None:
                needed._obj.value = 16
                return 0
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(sid)
            return 1

        def add_ace(acl, revision, mask, user_sid):
            seen["ace"] = (revision, mask, user_sid.value)
            return 1

        def set_security(path, kind, info, owner, group, dacl, sacl):
            seen["set"] = (path, kind, info, owner, group, sacl, ctypes.sizeof(dacl))
            return 0

        advapi32, kernel32 = type("Advapi32", (), {})(), type("Kernel32", (), {})()
        advapi32.OpenProcessToken = Fn("OpenProcessToken", open_token)
        advapi32.GetTokenInformation = Fn("GetTokenInformation", token_information)
        advapi32.GetLengthSid = Fn("GetLengthSid", lambda s: 28 if s.value == ctypes.addressof(sid) else 0)
        advapi32.InitializeAcl = Fn("InitializeAcl", lambda acl, size, revision: 1)
        advapi32.AddAccessAllowedAce = Fn("AddAccessAllowedAce", add_ace)
        advapi32.SetNamedSecurityInfoW = Fn("SetNamedSecurityInfoW", set_security)
        kernel32.GetCurrentProcess = Fn("GetCurrentProcess", lambda: -1)
        kernel32.CloseHandle = Fn("CloseHandle", lambda handle: 1)
        monkeypatch.setattr(atomic, "WINDOWS", True)
        monkeypatch.setattr(atomic, "_windows_api", lambda: (advapi32, kernel32))
        atomic.owner_only(tmp_path / "id_ed25519_llm_framework")
        assert "CloseHandle" in calls and calls[-1] == "SetNamedSecurityInfoW"
        assert seen["ace"] == (2, 0x001F01FF, ctypes.addressof(sid)), "full access for the token's user alone"
        path, kind, info, owner, group, sacl, size = seen["set"]
        assert path == str(tmp_path / "id_ed25519_llm_framework") and kind == 1
        assert (owner, group, sacl) == (None, None, None), "only the DACL is set"
        assert info == 0x80000004, "a new DACL (DACL_SECURITY_INFORMATION) that inherits nothing (PROTECTED)"
        assert size == 8 + 8 + 28, "the ACL holds exactly one entry"

        advapi32.SetNamedSecurityInfoW = Fn("SetNamedSecurityInfoW", lambda *a: 5)   # ERROR_ACCESS_DENIED
        with pytest.raises(OSError) as e:
            atomic.owner_only(tmp_path / "other")
        assert e.value.errno == 5 and e.value.filename == str(tmp_path / "other")

    @pytest.mark.skipif(os.name != "nt", reason="the Windows ACL itself")
    def test_on_windows_the_key_has_one_entry_and_inherits_nothing(self, tmp_path):
        private, _ = ssh_trust.client_key(tmp_path)
        listing = subprocess.run(["icacls", str(private)], capture_output=True, text=True, check=True,
                                 stdin=subprocess.DEVNULL).stdout
        assert listing.count(":(") == 1 and "(I)" not in listing, listing
        assert readable_key(private), "this account still reads it"


# --- the session itself over OpenSSH ------------------------------------------------------------------------------

class TestTheSessionOverOpenSsh:

    def test_a_one_time_password_is_refused_when_the_session_is_made(self, tmp_path):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.session import KeyOnly, OneTimePassword, RemoteSession
        from orchestrator.remote.transport import SystemSshTransport
        with pytest.raises(ValueError, match="paramiko"):
            RemoteSession(Endpoint(HOST), USER, tmp_path, credentials=OneTimePassword("pw"),
                          openssh=SystemSshTransport)
        session = RemoteSession(Endpoint(HOST), USER, tmp_path, credentials=KeyOnly(), openssh=SystemSshTransport)
        assert session.pair() is False

    def test_a_shown_fingerprint_cannot_be_compared(self, system_board, tmp_path):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.session import RemoteSession, SessionFailure
        from orchestrator.remote.transport import SystemSshTransport
        session = RemoteSession(Endpoint(HOST), USER, tmp_path, expect_fingerprint="SHA256:x",
                                openssh=SystemSshTransport)
        with pytest.raises(SessionFailure) as e:
            session.open()
        assert e.value.kind == "needs_paramiko" and system_board.fake.calls == []

    def test_no_openssh_on_this_computer_is_a_local_problem(self, tmp_path):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.session import RemoteSession, SessionFailure
        from orchestrator.remote.transport import SystemSshTransport
        missing = str(tmp_path / "no-openssh" / "ssh")

        def openssh(endpoint, user, state_dir):
            return SystemSshTransport(endpoint, user, state_dir, program=missing)
        session = RemoteSession(Endpoint(HOST), USER, tmp_path, openssh=openssh)
        with pytest.raises(SessionFailure) as e:
            session.open()
        assert e.value.kind == "login" and e.value.reason == reasons.LOCAL_PROBLEM
        assert str(tmp_path) not in str(e.value)


# --- a real OpenSSH against a real SSH server on this computer --------------------------------------------------

def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def real_sshd(tmp_path):
    """sshd on 127.0.0.1 with its own host key, logging in this account with the framework key of a state folder
    whose name has a space; skipped where OpenSSH's client or server is missing (Windows, a minimal image)."""
    import getpass
    import shutil
    import socket
    import time
    sshd = shutil.which("sshd") or ("/usr/sbin/sshd" if os.path.exists("/usr/sbin/sshd") else None)
    if os.name == "nt" or sshd is None or shutil.which("ssh") is None or shutil.which("ssh-keygen") is None:
        pytest.skip("needs OpenSSH's ssh, ssh-keygen and sshd on a POSIX system")
    for tool in ("sha256sum", "unzip", "nohup", "mktemp"):
        if shutil.which(tool) is None:
            pytest.skip(f"the board side needs {tool}")
    server = tmp_path / "sshd"
    server.mkdir()
    host_key = server / "host_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True,
                   stdin=subprocess.DEVNULL, capture_output=True)
    state = tmp_path / "Jane Doe" / ".llm-framework"
    state.mkdir(parents=True)
    private, public = ssh_trust.client_key(state)
    (server / "authorized_keys").write_text(public + "\n")
    port = _free_port()
    config_file = server / "sshd_config"
    config_file.write_text("\n".join([
        f"Port {port}", "ListenAddress 127.0.0.1", f"HostKey {host_key}",
        f"AuthorizedKeysFile {server}/authorized_keys", "StrictModes no", "PasswordAuthentication no",
        "KbdInteractiveAuthentication no", "UsePAM no", "PermitRootLogin prohibit-password",
        f"PidFile {server}/sshd.pid", "LogLevel ERROR", ""]))
    process = subprocess.Popen([sshd, "-D", "-e", "-f", str(config_file)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process.poll() is None:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    if process.poll() is not None:
        why = process.stderr.read().decode(errors="replace")[-300:]
        process.stderr.close()
        pytest.skip(f"sshd does not start here: {why}")
    user = getpass.getuser()
    # Whether this account can log in to a local sshd at all (a locked account, a policy of this computer), checked
    # with plain OpenSSH options of the test's own, so a fault of the framework's options is never skipped as one.
    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o",
                            "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "IdentitiesOnly=yes",
                            "-i", str(private), "-p", str(port), f"{user}@127.0.0.1", "true"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        process.kill()
        process.wait(10)
        process.stderr.close()
        pytest.skip(f"this account cannot log in to a local sshd here: {probe.stderr.strip()[-300:]}")
    line = host_key.with_suffix(".pub").read_text().split()
    ssh_trust.known_hosts_path(state).write_text(f"[127.0.0.1]:{port} {line[0]} {line[1]}\n")
    yield {"state": state, "port": port, "user": user}
    process.kill()
    process.wait(10)
    process.stderr.close()


def test_a_real_deploy_over_openssh(real_sshd, config, monkeypatch, tmp_path):
    """The whole fallback against a real sshd: login, private folder, upload into cat, sha256sum, the detached
    deploy.sh, its log and the look for the old sidecar, with the framework's known_hosts in a folder with a space."""
    import zipfile
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(real_sshd["state"]))
    monkeypatch.setattr(dmod, "PARAMIKO_AVAILABLE", False)
    package = tmp_path / "deploy_model_1.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("deploy.sh", "#!/bin/sh\necho '[1/3] Checking Integrity...'\necho 'Deployment Complete.'\n")
        zf.writestr("model.bin", bytes(range(256)) * 9000)
    manager = DeploymentManager(FakeFramework(config))
    assert manager._state_dir() == real_sshd["state"]
    sessions, events = [], []
    result = manager.deploy_package(package, "127.0.0.1", real_sshd["user"], port=real_sshd["port"],
                                    on_client=sessions.append, on_event=events.append, wait_seconds=5)
    assert result.success and result.exit_status == 0 and result.device_state == "ok", (result.reason, result.output)
    assert result.output[-1] == "Deployment Complete."
    assert [e.kind for e in events][-1] == "installing" and len(sessions) == 1


def test_a_real_openssh_refuses_an_unknown_host_key(real_sshd, config, monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(real_sshd["state"]))
    monkeypatch.setattr(dmod, "PARAMIKO_AVAILABLE", False)
    ssh_trust.known_hosts_path(real_sshd["state"]).write_text("")
    package = tmp_path / "deploy_model_1.zip"
    package.write_bytes(b"PK")
    result = DeploymentManager(FakeFramework(config)).deploy_package(package, "127.0.0.1", real_sshd["user"],
                                                                     port=real_sshd["port"])
    assert not result.success and result.code == ""
    assert result.reason == "The device 127.0.0.1 is not trusted yet: confirm its host key fingerprint first."
