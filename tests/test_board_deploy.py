"""Board deployment of golden artifacts over a network that drops (eighth round N4, N5, N6).

- N4: the device address is an IP address or a host name (myboard.local); the host key stays per host string.
- N5: when the network drops during a deploy (a Wi-Fi board such as the Radxa ZERO 3W), the deploy waits for the board
  with a visible state (elapsed time, limit, cancel), then logs in again with the same host key check and continues:
  the upload resumes at what arrived once its sha256 matches, and deploy.sh is started on the board once, never twice.
- N6: a board that cannot be reached or logged in to says why with a reason code (device_host_down,
  device_port_closed, device_auth_failed, device_host_key_changed), the person's next step in English and German, and
  a plain code for an AI (no account, no address, no path).
"""

import errno
import hashlib
import os
import shlex
import socket
import subprocess
import time
import zipfile

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.Core import deployment_manager as dmod  # noqa: E402
from orchestrator.Core.deployment_manager import DeploymentManager  # noqa: E402
from orchestrator.runtime.device import reasons  # noqa: E402
from orchestrator.utils import ssh_trust  # noqa: E402

import test_deploy_tools as dt  # noqa: E402
from test_deployment import FakeFramework, config  # noqa: E402,F401

fw = dt.fw
online = dt.online
roomy_disk = dt.roomy_disk

TARGET = "/tmp/llm_deploy.Ab12Cd34"  # nosec B108 - the fake board's folder, never a local path
HOST = "myboard.local"


# --- a fake board ---------------------------------------------------------------------------------------------------

class Gone(OSError):
    """What paramiko raises when the network dropped mid-transfer."""


class Channel:
    def __init__(self, status):
        self.status = status

    def recv_exit_status(self):
        return self.status


class Stream:
    def __init__(self, data=b"", status=0):
        self.data, self.channel = data, Channel(status)

    def read(self):
        return self.data


class Transport:
    def __init__(self, client):
        self.client = client

    def is_active(self):
        return self.client.board.online and not self.client.closed


class RemoteFile:
    def __init__(self, board, path, mode):
        self.board, self.path, self.pos = board, path, 0
        if mode == "wb":
            board.files[path] = bytearray()
        elif path not in board.files:
            raise FileNotFoundError(errno.ENOENT, path)

    def set_pipelined(self, on):
        pass

    def seek(self, offset):
        self.pos = offset

    def write(self, data):
        board = self.board
        if not board.online:
            raise Gone("Socket is closed")
        if board.disk_full:
            raise OSError("Failure")  # the SFTP server's answer: the session itself is fine
        buf = board.files[self.path]
        cut = len(data)
        if board.drop_after is not None and board.sent + len(data) > board.drop_after:
            cut = max(0, board.drop_after - board.sent)
        del buf[self.pos:self.pos + cut]
        buf[self.pos:self.pos] = data[:cut]
        self.pos += cut
        board.sent += cut
        if cut < len(data):
            board.drop_after = None
            board.go_offline()
            raise Gone("Socket is closed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SFTP:
    def __init__(self, board):
        self.board = board

    def stat(self, path):
        if not self.board.online:
            raise Gone("Socket is closed")
        if path not in self.board.files:
            raise FileNotFoundError(errno.ENOENT, path)
        return type("Stat", (), {"st_size": len(self.board.files[path])})()

    def open(self, path, mode):
        if not self.board.online:
            raise Gone("Socket is closed")
        return RemoteFile(self.board, path, mode)

    def close(self):
        pass


class Client:
    def __init__(self, board):
        self.board, self.closed = board, False

    def get_transport(self):
        return Transport(self)

    def open_sftp(self):
        if not self.board.online or self.closed:
            raise Gone("Socket is closed")
        return SFTP(self.board)

    def close(self):
        self.closed = True

    def exec_command(self, command):
        if not self.board.online or self.closed:
            raise Gone("Socket is closed")
        status, out = self.board.run(command)
        return None, Stream(out.encode(), status), Stream(b"")


class Board:
    """A board with a private folder, files, an installation that runs on its own and a network that can drop."""

    def __init__(self, clock):
        self.clock = clock
        self.online = True
        self.back_at = None            # fake time at which the board answers again
        self.files = {}
        self.dirs = set()
        self.sent = 0
        self.drop_after = None         # bytes after which the network drops during the upload
        self.drop_on_poll = 0          # polls of the installation after which the network drops once
        self.disk_full = False
        self.starts = 0                # how often deploy.sh was started
        self.polls = 0
        self.logins = 0
        self.install_polls_needed = 2
        self.docker_ps = ""            # what `docker ps -a` answers on the board (the old Qdrant sidecar's look)
        self.docker_commands = []

    def go_offline(self):
        self.online = False

    def tick(self):
        if not self.online and self.back_at is not None and self.clock.now >= self.back_at:
            self.online = True

    def run(self, command):
        if command == DeploymentManager.REMOTE_MKTEMP:
            self.dirs.add(TARGET)
            return 0, TARGET + "\n"
        if "docker " in command:
            self.docker_commands.append(command)
            if command == dmod.OLD_SIDECAR_CHECK:
                return 0, self.docker_ps
            raise AssertionError(f"the framework runs no other Docker command on the board: {command}")
        words = shlex.split(command) if "|" not in command and "{" not in command and "nohup" not in command else []
        if words[:1] == ["sha256sum"]:
            data = self.files.get(words[1])
            return (0, hashlib.sha256(bytes(data)).hexdigest() + f"  {words[1]}\n") if data is not None else (1, "")
        if command.startswith("head -c "):
            n, path = command.split()[2], shlex.split(command.split("|")[0])[3]
            return 0, hashlib.sha256(bytes(self.files[path][:int(n)])).hexdigest() + "  -\n"
        if words[:2] == ["mv", "-f"]:
            self.files[words[3]] = self.files.pop(words[2])
            return 0, ""
        if words[:2] == ["rm", "-f"]:
            self.files.pop(words[2], None)
            return 0, ""
        if words[:2] == ["rm", "-rf"]:
            self.dirs.discard(words[2])
            return 0, ""
        if words[:1] == ["["]:
            return (0 if words[2] in self.dirs else 1), ""
        if "nohup" in command:
            if TARGET not in self.dirs:
                return 1, ""  # cd into the upload folder fails: the board restarted
            marker = f"{TARGET}/{dmod.INSTALL_STARTED}"
            if marker in self.files:
                return 0, "started=already\n"
            self.files[marker] = bytearray()
            self.starts += 1
            return 0, "started=now\n"
        if command.startswith(f"cat {TARGET}/{dmod.INSTALL_STATUS}"):
            if TARGET not in self.dirs:
                return 0, "gone\n"
            self.polls += 1
            if self.drop_on_poll and self.polls == self.drop_on_poll:
                self.drop_on_poll = 0
                self.go_offline()
                raise Gone("Socket is closed")
            return (0, "0\n") if self.polls >= self.install_polls_needed else (0, "running\n")
        if command.startswith("tail -n "):
            return 0, "[1/3] Checking Integrity...\nDeployment Complete. System is ready.\n"
        raise AssertionError(f"unexpected command on the board: {command}")


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def board(monkeypatch):
    clock = Clock()
    b = Board(clock)
    key = paramiko.ECDSAKey.generate()
    b.key = key
    monkeypatch.setattr(dmod, "PARAMIKO_AVAILABLE", True)

    def fetch(host, port=22, timeout=10.0):
        b.tick()
        if not b.online:
            raise socket.timeout("timed out")
        return key

    def connect(host, user, state_dir, port=22, password=None, timeout=10.0):
        b.tick()
        if not b.online:
            raise socket.timeout("timed out")
        b.logins += 1
        return Client(b)

    monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "known")
    monkeypatch.setattr(ssh_trust, "connect", connect)
    monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=3.0: None if b.online else reasons.HOST_DOWN)
    return b


@pytest.fixture
def manager(config, board):
    dm = DeploymentManager(FakeFramework(config))
    clock = board.clock
    dm._clock = clock.time

    def sleep(seconds):
        clock.sleep(seconds)
        board.tick()
    dm._sleep = sleep
    return dm


@pytest.fixture
def package(tmp_path):
    pkg = tmp_path / "deploy_model_1.zip"
    pkg.write_bytes(bytes(range(256)) * 12000)  # about 3 MB: several upload chunks
    return pkg


def run(manager, package, **kw):
    events = []
    result = manager.deploy_package(package, HOST, "radxa", on_event=events.append, **kw)
    return result, events


# --- N5: waiting for the board, resuming, never installing twice ----------------------------------------------------

class TestWaitingForTheBoard:

    def test_an_upload_that_drops_resumes_at_what_arrived(self, manager, board, package):
        board.drop_after = 1_300_000
        board.back_at = 42.0
        result, events = run(manager, package)
        assert result.success and result.device_state == "ok" and result.resumed == 1
        arrived = board.files[f"{TARGET}/{package.name}"]
        assert bytes(arrived) == package.read_bytes(), "the package on the board is complete and unchanged"
        assert board.starts == 1
        waiting = [e for e in events if e.kind == "waiting"]
        assert waiting and waiting[0].elapsed == 0 and waiting[-1].elapsed >= 40
        assert all(e.limit == dmod.DEPLOY_WAIT_SECONDS and e.step == "upload" for e in waiting)
        back = [e for e in events if e.kind == "back"]
        assert back and back[0].done == 1_300_000 and back[0].total == package.stat().st_size, \
            "the upload continued at the bytes that arrived, not from the beginning"
        uploads = [e.done for e in events if e.kind == "uploading"]
        assert uploads[-1] == package.stat().st_size and result.waited_s >= 40

    def test_what_arrived_is_checked_before_it_is_continued(self, manager, board, package):
        """Bytes on the board that do not match the package (sha256 of what arrived) are not continued: the upload
        starts again from the beginning."""
        board.drop_after = 1_000_000
        board.back_at = 10.0
        real_tick = board.tick

        def tick():
            was = board.online
            real_tick()
            if board.online and not was:
                part = f"{TARGET}/{package.name}.part"
                board.files[part][:4] = b"XXXX"  # damaged while the board was away
        board.tick = tick
        result, events = run(manager, package)
        assert result.success and bytes(board.files[f"{TARGET}/{package.name}"]) == package.read_bytes()
        assert not [e for e in events if e.kind == "back"], "a damaged part is never continued"

    def test_a_board_that_does_not_come_back_ends_the_wait_with_its_reason(self, manager, board, package):
        board.drop_after = 500_000
        result, events = run(manager, package, wait_seconds=60)
        assert not result.success and result.code == reasons.HOST_DOWN and result.device_state == "ok"
        assert "60 s" in result.reason and "deploy.sh did not run" in result.reason
        assert board.starts == 0
        assert max(e.elapsed for e in events if e.kind == "waiting") >= 55

    def test_a_drop_during_the_installation_never_starts_it_twice(self, manager, board, package):
        board.drop_on_poll = 1
        board.back_at = 20.0
        board.install_polls_needed = 3
        result, events = run(manager, package)
        assert result.success and board.starts == 1 and result.resumed == 1
        assert [e.step for e in events if e.kind == "waiting"][0] == "install"
        assert [(e.kind, e.step) for e in events if e.kind == "back"] == [("back", "install")], \
            "back after the drop: the installation is followed again (the upload had finished)"
        assert "Deployment Complete" in " ".join(result.output)

    @staticmethod
    def drop_after_the_start_command(board):
        """The start command reaches the board (its marker is made there), but the answer never comes back."""
        real_run = board.run

        def on_board(command):
            answer = real_run(command)
            if "nohup" in command and board.starts == 1 and not getattr(board, "dropped_once", False):
                board.dropped_once = True
                board.go_offline()
                raise Gone("Socket is closed")
            return answer
        board.run = on_board

    def test_a_start_command_without_an_answer_is_never_reported_as_not_installed(self, manager, board, package):
        """The installation may run on the board although its answer was lost: the device state is unknown, and
        neither the result nor a cancel says that deploy.sh did not run."""
        self.drop_after_the_start_command(board)
        result, events = run(manager, package, wait_seconds=30)
        assert not result.success and board.starts == 1
        assert result.device_state == "unknown", "the installation may have started on the board"
        assert "did not run" not in result.reason and "may still have finished" in result.reason
        assert [e.step for e in events if e.kind == "waiting"][0] == "install"

        board2_events = []
        board.online, board.dropped_once, board.starts = True, False, 0
        board.files.clear()
        cancel = manager.deploy_package(package, HOST, "radxa", cancelled=lambda: len(board2_events) >= 2,
                                        on_event=lambda e: board2_events.append(e) if e.kind == "waiting" else None)
        assert not cancel.success and cancel.device_state == "unknown"
        assert "nothing was installed" not in cancel.reason and "unknown" in cancel.reason

    def test_a_start_command_without_an_answer_is_not_sent_twice_when_the_board_is_back(self, manager, board,
                                                                                          package):
        self.drop_after_the_start_command(board)
        board.back_at = 15.0
        result, _events = run(manager, package)
        assert result.success and board.starts == 1 and result.resumed == 1

    def test_a_board_that_restarted_after_the_start_command_leaves_the_state_unknown(self, manager, board, package):
        """The start command may have run before the board restarted: when the board is back without its upload
        folder, the result says the state is unknown, never that nothing was installed."""
        self.drop_after_the_start_command(board)
        board.back_at = 15.0
        real_tick = board.tick

        def tick():
            was = board.online
            real_tick()
            if board.online and not was:
                board.dirs.clear()
        board.tick = tick
        result, _events = run(manager, package)
        assert not result.success and board.starts == 1
        assert result.device_state == "unknown" and "may have started" in result.reason

    def test_cancel_while_waiting_installs_nothing(self, manager, board, package):
        board.drop_after = 200_000
        waited = []

        def stop():
            return len(waited) >= 3

        result = manager.deploy_package(package, HOST, "radxa", cancelled=stop,
                                        on_event=lambda e: waited.append(e) if e.kind == "waiting" else None)
        assert not result.success and board.starts == 0 and result.device_state == "ok"
        assert "cancelled" in result.reason

    def test_a_failure_on_the_board_is_not_a_network_drop(self, manager, board, package):
        """A full disk on the board (the SSH session is fine) fails at once: no waiting, no endless resuming."""
        board.disk_full = True
        result, events = run(manager, package)
        assert not result.success and "upload" in result.reason and result.resumed == 0
        assert not [e for e in events if e.kind == "waiting"] and board.starts == 0

    def test_a_board_that_restarted_lost_its_upload_folder(self, manager, board, package):
        board.drop_after = 600_000
        board.back_at = 30.0
        real_tick = board.tick

        def tick():
            was = board.online
            real_tick()
            if board.online and not was:
                board.dirs.clear()  # /tmp is empty after a restart
        board.tick = tick
        result, _events = run(manager, package)
        assert not result.success and "restarted" in result.reason and board.starts == 0

    def test_a_changed_host_key_after_the_drop_is_never_accepted(self, manager, board, package, monkeypatch):
        board.drop_after = 400_000
        board.back_at = 10.0
        other = paramiko.ECDSAKey.generate()
        shown = ssh_trust.fingerprint(board.key)

        def fetch(host, port=22, timeout=10.0):
            board.tick()
            if not board.online:
                raise socket.timeout("timed out")
            return board.key if board.logins == 0 else other  # another device answers after the drop
        monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
        result, _events = run(manager, package, expect_fingerprint=shown)
        assert not result.success and result.code == reasons.HOST_KEY_CHANGED and board.starts == 0

    def test_the_start_command_runs_deploy_sh_once_on_a_real_shell(self, tmp_path):
        """The installation runs on the board on its own; sending its start command twice starts it once."""
        board_dir = tmp_path / "board"
        board_dir.mkdir()
        counter = tmp_path / "runs"
        with zipfile.ZipFile(board_dir / "pkg.zip", "w") as z:
            z.writestr("deploy.sh", f"echo run >> {counter}\necho installed\n")
        cmd = DeploymentManager._remote_start_cmd(str(board_dir), "pkg.zip")
        assert "./deploy.sh" not in cmd and "sh deploy.sh" in cmd and "nohup" in cmd
        first = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=30)
        second = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=30)
        assert "started=now" in first.stdout and "started=already" in second.stdout
        status = board_dir / dmod.INSTALL_STATUS
        for _ in range(200):
            if status.exists() and status.read_text().strip():
                break
            time.sleep(0.05)
        assert status.read_text().strip() == "0"
        assert counter.read_text().splitlines() == ["run"], "deploy.sh ran exactly once"
        assert "installed" in (board_dir / dmod.INSTALL_LOG).read_text()


# --- N6: why a board cannot be reached -----------------------------------------------------------------------------

class TestReasonCodes:

    @pytest.mark.parametrize("error, code", [
        (socket.gaierror(-2, "Name or service not known"), reasons.HOST_DOWN),
        (socket.timeout("timed out"), reasons.HOST_DOWN),
        (OSError(errno.EHOSTUNREACH, "No route to host"), reasons.HOST_DOWN),
        (ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"), reasons.PORT_CLOSED),
        (paramiko.SSHException("Error reading SSH protocol banner"), reasons.PORT_CLOSED),
        (paramiko.AuthenticationException("Authentication failed."), reasons.AUTH_FAILED),
        (paramiko.ssh_exception.NoValidConnectionsError({("10.0.0.5", 22): ConnectionRefusedError()}),
         reasons.PORT_CLOSED),
        (ssh_trust.HostKeyChanged("10.0.0.5", 22), reasons.HOST_KEY_CHANGED),
        (ValueError("something else"), None),
    ])
    def test_errors_map_to_codes(self, error, code):
        assert reasons.classify(error) == code

    def test_an_unknown_host_key_is_the_first_contact_not_a_reason(self):
        assert reasons.classify(ssh_trust.UnknownHostKey("10.0.0.5", 22, paramiko.ECDSAKey.generate())) is None

    @pytest.mark.parametrize("text, code", [
        ("ssh: connect to host myboard.local port 22: Connection refused", reasons.PORT_CLOSED),
        ("radxa@myboard.local: Permission denied (publickey,password).", reasons.AUTH_FAILED),
        ("ssh: Could not resolve hostname myboard.local: Name or service not known", reasons.HOST_DOWN),
        ("@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @\nHost key verification failed.",
         reasons.HOST_KEY_CHANGED),
        ("No ED25519 host key is known for myboard.local and you have requested strict checking.\n"
         "Host key verification failed.", None),
    ])
    def test_openssh_messages_map_to_codes(self, text, code):
        assert reasons.classify_ssh_output(text) == code

    def test_check_port_tells_down_from_closed(self, monkeypatch):
        def refused(address, timeout=None):
            raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        monkeypatch.setattr(socket, "create_connection", refused)
        assert reasons.check_port("10.0.0.5", 22) == reasons.PORT_CLOSED

        def silent(address, timeout=None):
            raise socket.timeout("timed out")
        monkeypatch.setattr(socket, "create_connection", silent)
        assert reasons.check_port("10.0.0.5", 22) == reasons.HOST_DOWN

    @pytest.mark.parametrize("lang", ["en", "de"])
    def test_every_code_has_a_title_and_a_next_step_in_both_languages(self, lang):
        from orchestrator.utils.l10n_device import TEXTS
        for code in reasons.REASONS:
            for kind in ("reason", "next"):
                assert TEXTS[lang].get(f"device.{kind}.{code}"), (lang, kind, code)
            text = reasons.person_text(code, lang, address="myboard.local", port=2222)
            assert "myboard.local" in text and "{" not in text
        assert set(TEXTS["en"]) == set(TEXTS["de"])

    def test_the_codes_for_an_ai_name_no_account_address_or_path(self):
        for code in reasons.REASONS:
            assert code in reasons.TOOL_CODES and code in reasons.AI_NEXT
            text = reasons.AI_NEXT[code]
            assert "{" not in text and "/" not in text and "@" not in text
        from orchestrator.tools.facts_catalog import BLOCKERS
        for code in reasons.REASONS:
            assert BLOCKERS[code].reason_code == reasons.TOOL_CODES[code], code

    def test_a_refused_port_at_the_start_fails_at_once_with_its_code(self, config, package, monkeypatch):
        dm = DeploymentManager(FakeFramework(config))
        monkeypatch.setattr(dm, "_check_conn", lambda *a, **k: False)
        monkeypatch.setattr(reasons, "check_port", lambda *a, **k: reasons.PORT_CLOSED)
        result = dm.deploy_package(package, HOST, "radxa")
        assert not result.success and result.code == reasons.PORT_CLOSED and "not reachable" in result.reason

    def test_a_refused_login_says_device_auth_failed_without_the_exception_text(self, manager, board, package,
                                                                                 monkeypatch):
        def refuse(*a, **k):
            raise paramiko.AuthenticationException("Authentication failed for /home/radxa/.ssh/authorized_keys")
        monkeypatch.setattr(ssh_trust, "connect", refuse)
        result, _events = run(manager, package)
        assert result.code == reasons.AUTH_FAILED and "/home/" not in result.reason

    def test_a_changed_host_key_at_the_start_says_so(self, manager, board, package, monkeypatch):
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda *a: "changed")
        result, _events = run(manager, package)
        assert result.code == reasons.HOST_KEY_CHANGED and not result.success and board.logins == 0


# --- an old Qdrant sidecar on the board: told, never removed ----------------------------------------------------------

class TestOldSidecar:
    """Packages with Docker images from earlier versions started a Qdrant sidecar on the board. A deploy tells the
    person when it is still there and records it for Mojo; it never stops or removes it (round 5 f)."""

    @pytest.mark.parametrize("answer, state", [("qdrant-sidecar|Up 3 days\n", "running"),
                                               ("llm-node|Up 1 hour\nqdrant-sidecar|Exited (0) 2 days ago\n",
                                                "stopped")])
    def test_a_deploy_names_the_old_container_and_leaves_it_alone(self, manager, board, package, answer, state):
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.profile import device_id_for_host_key
        board.docker_ps = answer
        result, _events = run(manager, package)
        assert result.success and result.old_sidecar == state
        assert any("qdrant-sidecar" in n and state in n and "does not remove it" in n for n in result.notes)
        assert board.docker_commands == [dmod.OLD_SIDECAR_CHECK], "only a look; nothing is stopped or removed"
        device_id = device_id_for_host_key(ssh_trust.fingerprint(board.key))
        row = TrustedDevices(manager._state_dir()).last_deploys()[device_id]
        assert row["old_sidecar"] == state and row["board_checked_at"]

    @pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="needs a POSIX shell")
    def test_a_docker_that_hangs_on_the_board_never_holds_a_finished_deploy(self, tmp_path):
        """The look runs after the deploy succeeded: a Docker daemon that does not answer on the board must not keep
        the deploy (the GUI, the AI's job, the terminal) waiting. It is bounded with `timeout` where the board has
        it, and runs as before where it does not."""
        import stat
        slow = tmp_path / "slow"
        slow.mkdir()
        (slow / "docker").write_text("#!/bin/sh\nsleep 30\necho 'qdrant-sidecar|Up 3 days'\n")
        quick = tmp_path / "quick"
        quick.mkdir()
        (quick / "docker").write_text("#!/bin/sh\necho 'qdrant-sidecar|Up 3 days'\n")
        for script in (slow / "docker", quick / "docker"):
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
        check = dmod.old_sidecar_check(1)
        started = time.monotonic()
        answer = subprocess.run(["/bin/sh", "-c", check], capture_output=True, text=True, timeout=25,
                                env={**os.environ, "PATH": f"{slow}{os.pathsep}{os.environ.get('PATH', '')}"}).stdout
        assert time.monotonic() - started < 15 and dmod.old_sidecar_state(answer) == ""
        # a board without `timeout` (only the fake docker on PATH): the look still runs
        answer = subprocess.run(["/bin/sh", "-c", check], capture_output=True, text=True, timeout=25,
                                env={"PATH": str(quick)}).stdout
        assert dmod.old_sidecar_state(answer) == "running"
        assert dmod.OLD_SIDECAR_CHECK == dmod.old_sidecar_check(dmod.OLD_SIDECAR_SECONDS)

    @pytest.mark.parametrize("answer", ["", "my-qdrant-sidecar|Up 3 days\n", "qdrant-sidecar-2|Up 1 day\n"])
    def test_no_note_without_exactly_that_container(self, manager, board, package, answer):
        board.docker_ps = answer
        result, _events = run(manager, package)
        assert result.success and result.old_sidecar == "" and result.notes == []

    def test_a_later_deploy_that_finds_none_clears_the_record_and_keeps_it_across_the_tool_note(self, manager, board,
                                                                                               package):
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.profile import device_id_for_host_key
        store = TrustedDevices(manager._state_dir())
        device_id = device_id_for_host_key(ssh_trust.fingerprint(board.key))
        board.docker_ps = "qdrant-sidecar|Up 3 days\n"
        run(manager, package)
        store.note_deploy(device_id, True, package.name)   # the deploy tool's own note keeps what the board showed
        assert store.last_deploys()[device_id]["old_sidecar"] == "running"
        board.docker_ps = ""
        run(manager, package)
        assert store.last_deploys()[device_id]["old_sidecar"] == ""

    def test_the_gui_tells_the_person_in_their_language(self, qapp, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.gui.deployment_window import DeploymentWorker
        from orchestrator.utils.localization import ui_text
        package = tmp_path / "deploy_model_1.zip"
        package.write_bytes(b"zip")

        class Deployment:
            def create_deployment_package(self, **kw):
                return package

            def deploy_package(self, *a, **kw):
                return DeployResult(True, exit_status=0, device_state="ok", old_sidecar="stopped",
                                    notes=["English framework note"])

        framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default),
                                    resolve_path=lambda value: tmp_path / value, get_component=lambda n: Deployment())
        worker = DeploymentWorker(framework, tmp_path / "model.gguf", "Rockchip", {}, "192.0.2.40", "radxa", None)
        monkeypatch.setattr(worker, "_remember_device", lambda *a, **k: None)
        results = []
        worker.finished.connect(lambda ok, message: results.append((ok, message)))
        worker.run()
        (ok, message), = results
        expected = ui_text("device.deploy.old_sidecar", state=ui_text("device.deploy.state.stopped"))
        assert ok and expected in message and "docker rm -f qdrant-sidecar" in message

    def test_mojo_names_the_board_with_the_next_step_and_offers_no_repair(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from orchestrator.Core.mojo import Severity
        from orchestrator.Core.mojo.checks import devices as devices_check
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = tmp_path / "state"
        key = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "192.0.2.30", 22, key)
        store = TrustedDevices(state)
        device = store.record("192.0.2.30", 22, "radxa", key, via="cli", paired=True, label="Shed")
        monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=3.0: None)
        framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state))
        assert not [f for f in devices_check.check_devices(framework) if f.code.endswith(".old_sidecar")]
        store.note_board(device.device_id, old_sidecar="running")
        (line,) = [f for f in devices_check.check_devices(framework) if f.code.endswith(".old_sidecar")]
        assert line.severity == Severity.INFO and "Shed" in line.message and "running" in line.message
        assert "docker rm -f qdrant-sidecar" in line.fix_hint and line.cannot == "person" and not line.repair_id
        assert line.fix is None and "radxa" not in line.message + line.fix_hint + line.where


# --- N4: an address or a host name; the host key stays per host string -----------------------------------------------

class TestAddressOrHostName:

    def test_a_host_name_deploys_like_an_address(self, manager, board, package):
        result, _events = run(manager, package)
        assert result.success

    @pytest.mark.parametrize("host", ["fe80::1", "[fe80::1]", "[fe80::1", "fe80::1]", "[[fe80::1]]", "[x]",
                                      "myboard.local", "192.168.1.50", "[192.168.1.50]"])
    def test_the_cli_and_gui_check_and_the_deploy_agree_on_every_address(self, manager, board, package, monkeypatch,
                                                                          host):
        """valid_host (the CLI's and the GUI's check) accepted [fe80::1], which the deploy then refused. Both now give
        the same answer for every form: an IPv6 address is written without brackets, and a refused address never
        reaches the network."""
        from orchestrator.runtime.remote_probe import valid_host
        seen = []
        monkeypatch.setattr(reasons, "check_port", lambda h, port=22, timeout=3.0: seen.append(h) or None)
        result = manager.deploy_package(package, host, "radxa")
        refused = "Invalid address" in (result.reason or "")
        assert valid_host(host) is not refused, (host, result.reason)
        assert refused == (seen == []), "a refused address is never tried"

    def test_the_host_key_is_trusted_per_host_string(self, tmp_path):
        """Trusting the board under its host name does not trust its IP address (and the other way round): the
        person compares the fingerprint once for each string, as before."""
        state = tmp_path / "state"
        key = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "myboard.local", 22, key)
        assert ssh_trust.check_host_key(state, "myboard.local", 22, key) == "known"
        assert ssh_trust.check_host_key(state, "192.168.1.50", 22, key) == "unknown"

    def test_the_device_record_stays_the_same_board_under_another_address(self, tmp_path):
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = tmp_path / "state"
        key = paramiko.ECDSAKey.generate()
        store = TrustedDevices(state)
        first = store.record("myboard.local", 22, "radxa", key, via="cli", paired=True, label="Radxa")
        second = store.record("192.168.1.50", 22, "radxa", key, via="cli", paired=True)
        assert first.device_id == second.device_id and second.host == "192.168.1.50" and second.label == "Radxa"
        assert len(store.list()) == 1


# --- what an AI sees of it -------------------------------------------------------------------------------------------

class TestTheDeployTool:

    def test_the_job_shows_the_wait_by_label_and_returns_the_codes(self, fw, online, monkeypatch):
        from orchestrator.Core.deployment_manager import DeployEvent, DeployResult
        seen = []

        def fake(package, host, user, **kw):
            for event in (DeployEvent("uploading", done=50, total=100),
                          DeployEvent("waiting", elapsed=12.0, limit=300.0, step="upload")):
                kw["on_event"](event)
                job_now = [j for j in fw.job_service.list() if j.kind == "deploy"][-1]
                seen.append((job_now.done, job_now.total, job_now.message))
            return DeployResult(False, device_state="ok", code=reasons.HOST_DOWN, resumed=1, waited_s=300.0,
                                reason=f"The board did not answer again within 300 s ({reasons.HOST_DOWN}); "
                                       f"deploy.sh did not run. {user}@{host}")
        manager = fw.deployment_manager if getattr(fw, "deployment_manager", None) is not None \
            else fw.get_component("deployment_manager")
        monkeypatch.setattr(manager, "deploy_package", fake)
        device, _key, pkg = dt.TestDeploy().ready(fw, online)
        started = dt.call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                          confirm=dt.asking(True))
        job = dt.wait_job(fw, started.job_id)
        assert job.result["reason_code"] == reasons.HOST_DOWN and job.result["resumed"] == 1
        texts = " ".join(str(p) for p in seen)
        assert "waiting for the board Living room board: 12 s of at most 300 s" in texts
        assert "192.168.1.50" not in texts, "the progress names the board by its label"
        assert "dietpi" not in texts + str(job.result), "the device account never reaches an AI (D0)"

    @pytest.mark.parametrize("error, code", [(ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
                                              reasons.PORT_CLOSED),
                                             (socket.timeout("timed out"), reasons.HOST_DOWN)])
    def test_a_board_that_cannot_be_reached_is_refused_with_its_code(self, fw, online, monkeypatch, error, code):
        from orchestrator.tools.registry import ToolError
        device, _key, pkg = dt.TestDeploy().ready(fw, online)

        def fail(*a, **k):
            raise error
        monkeypatch.setattr(ssh_trust, "fetch_host_key", fail)
        with pytest.raises(ToolError) as caught:
            dt.call(fw, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                    confirm=dt.asking(True))
        assert caught.value.blocker == code and caught.value.code == reasons.TOOL_CODES[code]
        assert "192.168.1.50" not in str(caught.value) and "dietpi" not in str(caught.value)
        assert caught.value.person_action, "the person is told what to do"


    def test_the_wait_during_the_installation_never_says_the_upload_continues(self):
        """The upload had finished: while the installation is followed again, the texts say so (the tool's progress,
        the GUI, the CLI)."""
        from orchestrator.Core.deployment_manager import DeployEvent
        from orchestrator.tools.tools_deploy import event_text
        from orchestrator import cli as cli_module
        waiting = DeployEvent("waiting", elapsed=12.0, limit=300.0, step="install")
        back = DeployEvent("back", step="install")
        for text in (event_text(waiting, "Living room board"), cli_module._deploy_event_text(None, waiting, HOST)):
            assert "upload" not in text and "installation" in text, text
        for text in (event_text(back, "Living room board"), cli_module._deploy_event_text(None, back, HOST)):
            assert "upload" not in text and "twice" in text, text
        upload = DeployEvent("waiting", elapsed=12.0, limit=300.0, step="upload")
        assert "upload" in cli_module._deploy_event_text(None, upload, HOST)


# --- the CLI ---------------------------------------------------------------------------------------------------------

class TestTheCli:

    @pytest.fixture(autouse=True)
    def keep_logger_levels(self):
        """The CLI's -q sets logger levels; later tests read log records again."""
        import logging
        saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
        root = logging.getLogger().level
        yield
        for name in list(logging.Logger.manager.loggerDict):
            logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
        logging.getLogger().setLevel(root)

    @staticmethod
    def context(dep_mgr, tmp_path):
        from types import SimpleNamespace
        from orchestrator import cli as cli_module
        ctx = cli_module.FrameworkContext()
        ctx._initialized = True
        ctx.framework_manager = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default,
                                                                       user_state_dir=str(tmp_path / "state")),
                                                resolve_path=lambda p: tmp_path / p)
        ctx.deployment_manager = dep_mgr
        return ctx

    def test_ctrl_c_cancels_the_deploy_and_says_what_happened_on_the_board(self, tmp_path, monkeypatch, package):
        """Ctrl+C (SIGINT) ends the wait for the board through the deploy's own cancel, so the person reads the
        device state instead of a bare "Aborted!"; the handler before the deploy is back afterwards."""
        import signal
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from orchestrator.Core.deployment_manager import DeployResult
        from test_cli_builds import quiet_console
        printed = quiet_console(monkeypatch)
        before = signal.getsignal(signal.SIGINT)

        class Manager:
            def host_key_status(self, host, port=22):
                return "known", paramiko.ECDSAKey.generate()

            def deploy_package(self, path, host, user, *, cancelled=None, on_client=None, **kw):
                signal.raise_signal(signal.SIGINT)  # the person presses Ctrl+C while the deploy waits for the board
                assert cancelled is not None and cancelled(), "Ctrl+C reached the deploy's cancel"
                return DeployResult(False, device_state="unknown",
                                    reason="The deploy was cancelled; deploy.sh runs on the board on its own and is "
                                           "never stopped halfway, so the device state is unknown.")
        result = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "run", str(package), "--host", HOST, "--user",
                                                     "radxa"], obj=self.context(Manager(), tmp_path))
        out = printed.getvalue() + result.output
        assert result.exit_code == 1 and "Aborted" not in out
        assert "cancelled" in out and "device state is unknown" in out
        assert signal.getsignal(signal.SIGINT) is before

    def test_a_login_that_fails_for_another_reason_keeps_its_words(self, tmp_path, monkeypatch):
        """Only a refused login is "device_auth_failed"; any other failure is shown as it is, never relabelled."""
        from orchestrator import cli as cli_module
        from orchestrator.runtime.profile import device_id_for_host_key
        from test_cli_builds import quiet_console
        printed = quiet_console(monkeypatch)
        key = paramiko.ECDSAKey.generate()
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "known")

        def connect(*a, **k):
            raise EOFError("the board closed the connection")
        monkeypatch.setattr(ssh_trust, "connect", connect)
        device_id = device_id_for_host_key(ssh_trust.fingerprint(key))
        with pytest.raises(SystemExit):
            cli_module._connect_device(tmp_path, device_id, HOST, 22, "radxa", None)
        out = printed.getvalue()
        assert "the board closed the connection" in out and "refused the login" not in out

        def refused(*a, **k):
            raise paramiko.AuthenticationException("Authentication failed.")
        monkeypatch.setattr(ssh_trust, "connect", refused)
        with pytest.raises(SystemExit):
            cli_module._connect_device(tmp_path, device_id, HOST, 22, "radxa", None)
        assert "refused the login" in printed.getvalue() and reasons.AUTH_FAILED in printed.getvalue()
