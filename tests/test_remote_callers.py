"""The board's callers on the one board session (consolidation chain c2-remote-callers).

Characterization first: these tests pin what the runtime deploy, the probe, the AI path's board contacts and the
person's board steps do today, so they hold before and after the callers move onto orchestrator/remote/session.py
RemoteSession:

- RemoteShell's contract: one `sh -c` per script, a 1 MiB output cap, a time limit, and the framework's sentence for
  each failure ("no answer from the device within N s", "the SSH connection failed: ...")
- the runtime deploy's private staging folder under the install folder (umask 077, never /tmp), the tarball's sha256
  and then SHA256SUMS checked on the board, and the short benchmark's results and refusals
- service.connect never uses a password: a refused login becomes needs_person with device_auth_failed; another key
  than the trusted or the shown one is refused before any login
- person_steps' one-time password installs the framework's key once, and a failed key install is its own kind
- the "hold the client so Cancel can close it" copies: a client handed after the cancel is closed at once
"""

import io
import socket
from types import SimpleNamespace

import pytest

import test_device_runtime as tdr
from orchestrator.runtime.device.remote import RemoteError, RemoteShell, verify_on_device

POSIX = tdr.POSIX


# --- RemoteShell's contract -----------------------------------------------------------------------------------------

class Channel:
    def __init__(self, status=0):
        self.status, self.write_closed, self.closed = status, False, False

    def shutdown_write(self):
        self.write_closed = True

    def recv_exit_status(self):
        return self.status

    def close(self):
        self.closed = True


class Out:
    def __init__(self, channel, data=b""):
        self.channel, self.buffer = channel, io.BytesIO(data)

    def read(self, size=-1):
        return self.buffer.read(size)


class In:
    def __init__(self, channel):
        self.channel, self.sent = channel, b""

    def write(self, data):
        self.sent += data

    def flush(self):
        pass


class Client:
    """An SSH client stand-in: each command answers (status, stdout, stderr) or raises."""

    def __init__(self, answer=(0, b"", b"")):
        self.answer, self.commands, self.timeouts, self.stdin = answer, [], [], []

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        self.timeouts.append(timeout)
        if isinstance(self.answer, BaseException):
            raise self.answer
        status, out, err = self.answer
        channel = Channel(status)
        stdin = In(channel)
        self.stdin.append(stdin)
        return stdin, Out(channel, out), Out(channel, err)


class TestRemoteShellContract:

    def test_one_sh_c_per_script_with_its_time_limit(self):
        client = Client((0, b"uid=1000\n", b""))
        result = RemoteShell(client).run("id -u && echo done", timeout=33)
        assert client.commands == ["sh -c 'id -u && echo done'"] and client.timeouts == [33]
        assert (result.status, result.out, result.err) == (0, "uid=1000\n", "")

    def test_stdin_is_sent_and_closed(self):
        client = Client()
        RemoteShell(client).run("cat > x", stdin=[b"ab", b"cd"])
        assert client.stdin[0].sent == b"abcd" and client.stdin[0].channel.write_closed

    def test_more_than_one_mib_is_refused(self):
        client = Client((0, b"x" * ((1 << 20) + 1), b""))
        with pytest.raises(RemoteError, match="more than 1048576 bytes"):
            RemoteShell(client).run("yes")

    def test_a_silent_board_and_a_lost_connection_say_so(self):
        with pytest.raises(RemoteError, match=r"^no answer from the device within 7 s$"):
            RemoteShell(Client(socket.timeout("timed out"))).run("sleep 99", timeout=7)
        with pytest.raises(RemoteError, match=r"^the SSH connection failed: Socket is closed$"):
            RemoteShell(Client(OSError("Socket is closed"))).run("true")
        with pytest.raises(RemoteError, match=r"^the SSH connection failed: $"):
            RemoteShell(Client(EOFError())).run("true")

    def test_the_shell_keeps_its_client(self):
        client = Client()
        assert RemoteShell(client).client is client


# --- the runtime deploy and the checks on the board --------------------------------------------------------------

@POSIX
class TestRuntimeDeployOnTheBoard:

    def test_the_staging_folder_is_private_and_inside_the_install_folder(self, tmp_path, source, device_base):
        build, packaged = tdr.packaged_build(tmp_path, source)
        dev = tdr.FakeDevice(device_base)
        tdr.deploy(dev, build, packaged)
        staging = [c for c in dev.commands if ".staging-XXXXXXXX" in c]
        assert len(staging) == 1 and "umask 077" in staging[0] and f"{dev.root}/.staging-XXXXXXXX" in staging[0]
        assert not any("/tmp" in c.replace(str(dev.home), "") for c in dev.commands), "nothing is put into /tmp"

    def test_the_tarball_and_then_every_file_are_checked_on_the_board(self, tmp_path, source, device_base):
        build, packaged = tdr.packaged_build(tmp_path, source)
        dev = tdr.FakeDevice(device_base)
        tdr.deploy(dev, build, packaged)
        (check,) = [c for c in dev.commands if "sha256sum -c SHA256SUMS" in c]
        assert check.index("sha256sum runtime.tar.gz") < check.index("tar -xzf") < check.index("sha256sum -c")
        assert packaged.sha256 in check

    def test_a_short_benchmark_that_fails_or_writes_no_json(self, tmp_path, source, device_base):
        for name, bench, expected in (
                ("sigill", "#!/bin/sh\ncase \"$1\" in --version) echo \"version: 0.6.0 (build @BUILD@, commit "
                           "@COMMIT@)\" >&2; exit 0 ;; --list-devices) echo \"Available devices:\"; exit 0 ;; esac\n"
                           "echo 'Illegal instruction' >&2; exit 132\n",
                 "llama-bench failed on the device (exit 132); SIGILL"),
                ("nojson", "#!/bin/sh\ncase \"$1\" in --version) echo \"version: 0.6.0 (build @BUILD@, commit "
                           "@COMMIT@)\" >&2; exit 0 ;; --list-devices) echo \"Available devices:\"; exit 0 ;; esac\n"
                           "echo 'not json'; exit 0\n",
                 "llama-bench wrote no usable JSON")):
            build, packaged = tdr.packaged_build(tmp_path / name, source, bench=tdr.fake_program(bench))
            dev = tdr.FakeDevice(device_base / name)
            tdr.deploy(dev, build, packaged)
            model = dev.home / "m.gguf"
            model.write_bytes(b"GGUF")
            smoke = tdr.verify(dev, build, bench_model=str(model), bench_allowed=True).check("smoke")
            assert smoke.ok is False and expected in smoke.detail, smoke.detail

    def test_a_benchmark_model_path_that_is_not_plain_is_refused(self):
        client = Client((0, b"", b""))
        with pytest.raises(RemoteError, match="not a plain absolute path"):
            verify_on_device(RemoteShell(client), home="/home/u", plan_verify={}, pin_build=1, pin_commit="a" * 40,
                             port=8080, bench_model="/home/u/../etc/x.gguf", bench_allowed=True)


@pytest.fixture
def source(tmp_path):
    """The pinned source as tests/test_device_runtime.py's fixture makes it."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text("project(llama.cpp)\n")
    (src / "LICENSE").write_bytes(tdr.LICENCE.encode("utf-8"))
    return src


@pytest.fixture
def device_base(tmp_path_factory):
    return tmp_path_factory.mktemp("board")


# --- the AI path's login (service.connect) ----------------------------------------------------------------------

class HostKey:
    def get_name(self):
        return "ssh-ed25519"


@pytest.fixture
def trusted_board(monkeypatch):
    """A trusted board whose key the person confirmed; returns (device, logins, set_presented)."""
    from orchestrator.runtime.device.trusted import TrustedDevice
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    trusted_fp = "SHA256:" + "A" * 43
    device = TrustedDevice(device_id_for_host_key(trusted_fp), "Shelf board", "192.0.2.20", 22, "dietpi",
                           "ssh-ed25519", trusted_fp, True, "2026-10-12T00:00:00Z", "cli")
    presented = {"fp": trusted_fp, "status": "known", "login": None}
    logins = []

    def connect(host, user, state, port=22, password=None):
        logins.append((host, user, port, password))
        if presented["login"] is not None:
            raise presented["login"]
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: HostKey())
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: presented["status"])
    monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: presented["fp"])
    monkeypatch.setattr(ssh_trust, "connect", connect)
    return device, logins, presented


class TestServiceLogin:

    def test_the_framework_key_only_never_a_password(self, tmp_path, trusted_board):
        from orchestrator.runtime.device import service
        device, logins, _presented = trusted_board
        handed = []
        session = service.connect(device, tmp_path, "dietpi", device.fingerprint, on_client=handed.append,
                                  step="probe")
        assert logins == [("192.0.2.20", "dietpi", 22, None)] and len(handed) == 1
        session.close()

    def test_a_refused_login_needs_the_person(self, tmp_path, trusted_board):
        paramiko = pytest.importorskip("paramiko")
        from orchestrator.runtime.device import reasons, service
        device, _logins, presented = trusted_board
        presented["login"] = paramiko.AuthenticationException("no")
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(device, tmp_path, "dietpi", device.fingerprint, step="probe")
        assert e.value.code == "needs_person" and e.value.reason == reasons.AUTH_FAILED
        assert "pair the device once" in e.value.next

    def test_a_board_that_does_not_answer_at_the_login(self, tmp_path, trusted_board):
        from orchestrator.runtime.device import reasons, service
        device, _logins, presented = trusted_board
        presented["login"] = ConnectionRefusedError(111, "refused")
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(device, tmp_path, "dietpi", device.fingerprint, step="probe")
        assert e.value.reason == reasons.PORT_CLOSED and e.value.code == reasons.TOOL_CODES[reasons.PORT_CLOSED]

    def test_another_key_than_the_trusted_or_the_shown_one_is_refused_before_any_login(self, tmp_path,
                                                                                         trusted_board):
        from orchestrator.runtime.device import reasons, service
        device, logins, presented = trusted_board
        presented["fp"] = "SHA256:" + "B" * 43
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(device, tmp_path, "dietpi", device.fingerprint, step="probe")
        assert e.value.code == "needs_person" and "not the one the person trusted" in str(e.value)
        presented["fp"] = device.fingerprint
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(device, tmp_path, "dietpi", "SHA256:" + "C" * 43, step="probe")
        assert e.value.code == "needs_person" and "another host key than the one shown" in str(e.value)
        presented["status"] = "changed"
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(device, tmp_path, "dietpi", device.fingerprint, step="probe")
        assert e.value.reason == reasons.HOST_KEY_CHANGED
        assert logins == [], "no login to a key that is not the trusted, shown one"


# --- the person's login to a stored board (person_steps.connect_stored) -------------------------------------------

class TestPersonLogin:

    @pytest.fixture
    def board(self, monkeypatch):
        from orchestrator.runtime.profile import device_id_for_host_key
        from orchestrator.utils import ssh_trust
        seen = []
        fp = "SHA256:" + "D" * 43
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: HostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
        monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: fp)
        monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: (
            seen.append(("login", password)) or SimpleNamespace(close=lambda: seen.append("closed"))))
        monkeypatch.setattr(ssh_trust, "client_key", lambda state: (None, "ssh-ed25519 AAAA framework"))
        return seen, device_id_for_host_key(fp)

    def test_without_a_password_no_key_is_installed(self, tmp_path, board, monkeypatch):
        from orchestrator.runtime.device import person_steps
        from orchestrator.utils import ssh_trust
        seen, device_id = board
        monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line: seen.append("installed"))
        person_steps.connect_stored(tmp_path, device_id, "192.0.2.10", 22, "dietpi")
        assert seen == [("login", None)]

    def test_a_key_that_cannot_be_installed_is_its_own_kind_and_closes_the_session(self, tmp_path, board,
                                                                                  monkeypatch):
        from orchestrator.runtime.device import person_steps
        from orchestrator.utils import ssh_trust
        seen, device_id = board

        def refuse(client, line):
            raise ssh_trust.HostKeyError("Could not install the framework key: read-only")

        monkeypatch.setattr(ssh_trust, "install_public_key", refuse)
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.connect_stored(tmp_path, device_id, "192.0.2.10", 22, "dietpi", "once")
        assert e.value.kind == "key_install" and "read-only" in str(e.value)
        assert seen == [("login", "once"), "closed"]


# --- the cancel of a running board step -------------------------------------------------------------------------

class TestCancelHoldsTheClient:

    def test_the_boards_window_closes_a_client_handed_after_the_cancel(self):
        pytest.importorskip("PySide6")
        from orchestrator.gui.boards_window import BoardsWindow
        closed = []
        # the window's cancel flag: a threading.Event before the move, the one CancelToken since (the stand-in
        # follows the window; what is pinned, a client handed after the cancel is closed at once, stays)
        from orchestrator.remote.session import CancelToken
        flag = CancelToken()
        flag.cancel()
        window = SimpleNamespace(_cancel=flag, _client=None)
        BoardsWindow._keep_client(window, SimpleNamespace(close=lambda: closed.append("closed")))
        assert closed == ["closed"]

    def test_the_deploy_worker_closes_its_client_on_cancel(self):
        pytest.importorskip("PySide6")
        from orchestrator.gui.deployment_window import DeploymentWorker
        closed = []
        worker = DeploymentWorker(SimpleNamespace(), "a", "p", {}, "192.0.2.1", "u", None)
        worker._on_client(SimpleNamespace(close=lambda: closed.append("first")))
        worker.cancel()
        worker._on_client(SimpleNamespace(close=lambda: closed.append("second")))
        assert closed == ["first", "second"]
