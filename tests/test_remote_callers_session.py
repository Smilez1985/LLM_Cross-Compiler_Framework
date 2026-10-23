"""The board's callers on the one board session, after the move (consolidation chain c2-remote-callers).

The runtime deploy, the checks on the board, the on-board benchmark and the probe run through
orchestrator/remote/session.py RemoteSession (runtime/device/remote.py RemoteShell is its facade), so a Wi-Fi drop no
longer ends them with "the SSH connection failed" (N5):

- the runtime tarball goes over SFTP, is checked on the board and continues at what arrived after a drop; a board
  without SFTP gets it on a command's stdin as before
- a check that only reads runs again after a drop; a step that must not run twice ends with "dropped" once the board
  answers again, and the staging folder still goes
- a board that does not answer again ends every later step at once with its reason code (no second wait)
- the benchmark runs on the board on its own (run_detached), is followed again after a drop and started once;
  follow() alone follows a job started before
- a command that stays silent while its connection stands is tried once more, then its timeout ends it
- the wait's texts for the other steps and the benchmark (person EN/DE, AI by label)
- the AI path logs in with KeyOnly credentials
"""

import os
import socket
from types import SimpleNamespace

import pytest

import test_device_runtime as tdr
from orchestrator.runtime.device.remote import RemoteError, RemoteShell, deploy_runtime, verify_on_device
from test_remote_callers import Client, HostKey, device_base, source, trusted_board  # noqa: F401 - fixtures

POSIX = tdr.POSIX


class NoSubsystem(Exception):
    """What paramiko says when the board has no SFTP server (the connection itself stands)."""


class LocalFile:
    def __init__(self, board, fh):
        self.board, self.fh = board, fh

    def set_pipelined(self, on):
        pass

    def seek(self, offset):
        self.fh.seek(offset)

    def write(self, data):
        board = self.board
        if not board.online:
            raise OSError("Socket is closed")
        cut = len(data)
        if board.drop_after_bytes is not None and board.sent_bytes + len(data) > board.drop_after_bytes:
            cut = max(0, board.drop_after_bytes - board.sent_bytes)
        self.fh.write(data[:cut])
        self.fh.flush()
        board.sent_bytes += cut
        if cut < len(data):
            board.drop_after_bytes = None
            board.go_offline()
            raise OSError("Socket is closed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fh.close()
        return False


class LocalSFTP:
    """SFTP onto the fake board's files (they are this computer's files under the board's folder)."""

    def __init__(self, board):
        self.board = board

    def stat(self, path):
        if not self.board.online:
            raise OSError("Socket is closed")
        return os.stat(path)

    def open(self, path, mode):
        if not self.board.online:
            raise OSError("Socket is closed")
        return LocalFile(self.board, open(path, mode))

    def close(self):
        pass


class LostAnswer:
    """The board's stdout of a command that ran there, but whose answer never arrives (the network dropped)."""

    def __init__(self, stream, board):
        self.stream, self.board, self.channel = stream, board, stream.channel

    def read(self, size=-1):
        self.stream.read(size)   # the command runs on the board
        self.board.go_offline()
        raise OSError("Socket is closed")


class SessionBoard(tdr.FakeDevice):
    """The fake board of tests/test_device_runtime.py behind a network that drops on cue: drop_on (the first command
    with that text runs on the board, then the network drops before its answer), drop_before (it never reaches the
    board), drop_after_bytes (the SFTP upload drops after so many bytes); has_sftp False is a board without SFTP."""

    def __init__(self, base, **kw):
        super().__init__(base, **kw)
        self.online, self.closed, self.has_sftp = True, False, True
        self.drop_on = self.drop_before = self.drop_after_bytes = None
        self.sent_bytes = 0

    def go_offline(self):
        self.online = False

    def exec_command(self, command, timeout=None):
        if not self.online or self.closed:
            raise OSError("Socket is closed")
        if self.drop_before and self.drop_before in command:
            self.drop_before = None
            self.go_offline()
            raise OSError("Socket is closed")
        stdin, out, err = super().exec_command(command, timeout)
        if self.drop_on and self.drop_on in command:
            self.drop_on = None
            return stdin, LostAnswer(out, self), err
        return stdin, out, err

    def open_sftp(self):
        if not self.online or self.closed:
            raise OSError("Socket is closed")
        if not self.has_sftp:
            raise NoSubsystem("Channel closed.")
        return LocalSFTP(self)

    def get_transport(self):
        return SimpleNamespace(is_active=lambda: self.online and not self.closed, set_keepalive=lambda n: None,
                               get_remote_server_key=lambda: HostKey())

    def close(self):
        self.closed = True


@pytest.fixture
def board_session(tmp_path, monkeypatch, device_base):  # noqa: F811 - the fixture of test_remote_callers
    """(board, a logged-in RemoteSession with it on a fake clock, the session's events, the clock). The board comes
    back when `board.back` is true (it is by default)."""
    from orchestrator.remote import reasons
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.session import RemoteSession
    from orchestrator.utils import ssh_trust
    board = SessionBoard(device_base)
    board.back = True
    board.logins = []

    def connect(host, user, state, port=22, password=None):
        if not board.back:
            raise OSError(113, "No route to host")
        board.logins.append(password)
        board.online, board.closed = True, False
        return board

    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: HostKey())
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
    monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: "SHA256:" + "E" * 43)
    monkeypatch.setattr(ssh_trust, "connect", connect)
    monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=3.0: reasons.HOST_DOWN)
    clock, events = [0.0], []
    session = RemoteSession(Endpoint("192.0.2.30"), "dietpi", tmp_path / "state", clock=lambda: clock[0],
                            sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                            reachable=lambda host, port: board.back, on_event=events.append)
    session.open()
    return board, session, events, clock


def deploy_over(session, build, packaged, **kw):
    def check(home, folder):
        return verify_on_device(RemoteShell(session), home=home, folder=folder, plan_verify=build.plan["verify"],
                                pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080)
    return deploy_runtime(RemoteShell(session), tarball=packaged.path, tarball_sha256=packaged.sha256,
                          build_id=build.build_id, arch="arm64", settings=tdr.USER, check=kw.pop("check", check), **kw)


@POSIX
class TestTheRuntimeDeployThroughDrops:

    def test_the_upload_goes_over_sftp_and_continues_after_a_drop(self, tmp_path, source, board_session):
        board, session, events, _clock = board_session
        build, packaged = tdr.packaged_build(tmp_path, source)
        size = packaged.path.stat().st_size
        board.drop_after_bytes = size // 2
        result = deploy_over(session, build, packaged)
        assert result.activated and result.report.ok
        assert not any("cat >" in c for c in board.commands), "the tarball went over SFTP"
        assert any(c.startswith("sh -c 'head -c ") for c in board.commands), "it continued at what arrived"
        kinds = [(e.kind, e.step) for e in events if e.kind in ("waiting", "back")]
        assert kinds[0] == ("waiting", "upload") and ("back", "") in kinds
        back = next(e for e in events if e.kind == "back")
        assert 0 < back.done < size and back.total == size
        assert not list(board.root.glob(".staging-*")) and len(board.logins) == 2

    def test_a_board_without_sftp_gets_the_tarball_on_stdin(self, tmp_path, source, board_session):
        board, session, _events, _clock = board_session
        board.has_sftp = False
        build, packaged = tdr.packaged_build(tmp_path, source)
        assert deploy_over(session, build, packaged).activated
        (upload,) = [c for c in board.commands if "cat >" in c]
        assert "umask 077" in upload and "runtime.tar.gz" in upload

    def test_a_check_that_only_reads_runs_again_after_a_drop(self, tmp_path, source, board_session):
        board, session, events, _clock = board_session
        build, packaged = tdr.packaged_build(tmp_path, source)
        board.drop_on = "--list-devices"
        result = deploy_over(session, build, packaged)
        assert result.activated and result.report.check("devices").ok
        assert len([c for c in board.commands if "--list-devices" in c]) == 2
        assert ("back", "command") in [(e.kind, e.step) for e in events]

    def test_a_step_that_must_not_run_twice_ends_the_deploy_and_the_staging_folder_still_goes(
            self, tmp_path, source, board_session):
        board, session, _events, _clock = board_session
        build, packaged = tdr.packaged_build(tmp_path, source)
        board.drop_on = "echo existed=1"   # the move into place ran, its answer was lost
        with pytest.raises(RemoteError) as e:
            deploy_over(session, build, packaged)
        assert e.value.kind == "dropped" and "not repeated" in str(e.value)
        assert len([c for c in board.commands if "echo existed=1" in c]) == 1, "never run twice"
        assert not list(board.root.glob(".staging-*")), "the staging folder goes once the board answers again"
        assert not (board.root / "current").exists(), "nothing was activated"

    def test_a_board_that_does_not_answer_again_ends_every_later_step_at_once(self, tmp_path, source,
                                                                               board_session):
        from orchestrator.remote import reasons
        board, session, _events, clock = board_session
        build, packaged = tdr.packaged_build(tmp_path, source)
        board.back = False
        board.drop_before = "SHA256SUMS"
        with pytest.raises(RemoteError) as e:
            deploy_over(session, build, packaged)
        assert e.value.kind == "wait" and e.value.reason == reasons.HOST_DOWN
        assert 300 <= clock[0] < 600, "one wait, not a second one for the clean-up"


@POSIX
class TestTheBenchmarkOnTheBoard:

    def test_it_runs_on_its_own_and_is_followed_again_after_a_drop(self, tmp_path, source, board_session):
        board, session, events, _clock = board_session
        build, packaged = tdr.packaged_build(tmp_path, source)
        assert deploy_over(session, build, packaged).activated
        model = board.home / "models" / "qwen.gguf"
        model.parent.mkdir()
        model.write_bytes(b"GGUF")
        board.drop_on = "echo running"   # the first look whether it finished
        report = verify_on_device(RemoteShell(session), home=str(board.home), plan_verify=build.plan["verify"],
                                  pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080,
                                  bench_model=str(model), bench_allowed=True)
        smoke = report.check("smoke")
        assert smoke.ok and "pp 21.5 t/s" in smoke.detail and smoke.data["bench"][0]["n_prompt"] == 64
        assert len([c for c in board.commands if "nohup" in c and "-o json" in c]) == 1, "started once"
        steps = [(e.kind, e.step) for e in events]
        assert ("waiting", "bench") in steps and ("back", "bench") in steps
        assert not list(board.root.glob(".bench.*")), "its folder goes afterwards"
        assert not any("/tmp" in c.replace(str(board.home), "") for c in board.commands)

    def test_a_board_without_nohup_runs_it_attached_as_before(self, tmp_path, source, board_session):
        board, session, _events, _clock = board_session
        build, packaged = tdr.packaged_build(tmp_path, source)
        assert deploy_over(session, build, packaged).activated
        model = board.home / "m.gguf"
        model.write_bytes(b"GGUF")
        real_answer = board.answer
        board.answer = lambda command, stdin: ((0, b"nohup=no\n", b"") if "command -v nohup" in command
                                               else real_answer(command, stdin))
        report = verify_on_device(RemoteShell(session), home=str(board.home), plan_verify=build.plan["verify"],
                                  pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080,
                                  bench_model=str(model), bench_allowed=True)
        assert report.check("smoke").ok and not any("nohup sh -c" in c for c in board.commands)

    def test_a_job_started_before_is_followed_without_starting_it_again(self, board_session):
        from orchestrator.remote.session import RemoteSession
        board, session, _events, _clock = board_session
        folder = session.private_dir(str(board.home), "job")
        assert session.run(RemoteSession.detached_command(folder, "(exit 7)")).out.strip() == "started=now"
        board.commands.clear()
        assert session.follow(folder, poll=0.1) == 7
        assert not any("nohup" in c for c in board.commands), "follow never starts the job"

    def test_a_start_whose_answer_was_lost_never_starts_the_job_twice(self, board_session):
        board, session, events, _clock = board_session
        folder = session.private_dir(str(board.home), "job")
        board.drop_on = "nohup"   # the start ran on the board, its answer never came
        assert session.run_detached(folder, "echo ran >> runs.log; (exit 3)", poll=0.1, step="bench") == 3
        assert len([c for c in board.commands if "nohup" in c]) == 2, "sent again after the drop"
        assert (board.home / folder.rpartition("/")[2] / "runs.log").read_text() == "ran\n", "it ran once"
        assert ("back", "bench") in [(e.kind, e.step) for e in events]


@POSIX
def test_the_probe_runs_its_command_again_after_a_drop(tmp_path, board_session):
    """The probe only reads and writes nothing on the board: after a drop the session waits and sends it again."""
    from orchestrator.runtime import remote_probe as rp
    board, session, events, _clock = board_session
    profile = (tdr.PROBES / "schema2_rk3566_dietpi.txt").read_bytes()
    real_answer = board.answer

    def answer(command, stdin):
        if command == rp.PYTHON_CHECK:
            return 0, b"3.11\n", b""
        if command == rp.PYTHON_COMMAND:
            return 0, profile, b""
        return real_answer(command, stdin)

    board.answer = answer
    board.drop_on = "python3 -I -c"
    result = rp.probe_over_ssh(session)
    assert result.tool == "python" and result.profile.get("SOC_MODEL") == tdr.RK3566.get("SOC_MODEL")
    assert len([c for c in board.commands if c == rp.PYTHON_CHECK]) == 2
    assert ("back", "command") in [(e.kind, e.step) for e in events]


class TestResumable:

    def test_a_silent_command_is_tried_once_more_then_its_timeout_ends_it(self, tmp_path, monkeypatch):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.session import RemoteSession
        from orchestrator.utils import ssh_trust
        runs = []

        class Silent:
            def run(self, script, **kw):
                runs.append(script)
                raise socket.timeout("timed out")

            def alive(self):
                return True

            def close(self):
                pass

        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: HostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
        monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: "SHA256:" + "F" * 43)
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
        clock = [0.0]
        session = RemoteSession(Endpoint("192.0.2.31"), "u", tmp_path, clock=lambda: clock[0],
                                sleep=lambda s: clock.__setitem__(0, clock[0] + s), reachable=lambda h, p: True,
                                transport_factory=lambda client: Silent())
        session.open()
        with pytest.raises(socket.timeout):
            session.run_resumable("cat big", again=True)
        # the connection is silent too (its `exit 0` gets no answer either): the session waits for the board, tries
        # the command once more, then its timeout is the end (the review added the `exit 0` that tells a busy board
        # from a dropped network, TestASilentCommandIsNoNetworkDrop below)
        assert runs == ["cat big", "exit 0", "cat big"]

    def test_an_attached_session_never_waits(self):
        from orchestrator.remote.session import RemoteSession
        session = RemoteSession.attached(Client(OSError("Socket is closed")))
        with pytest.raises(OSError):
            session.run_resumable("true")
        assert session.resumed == 0 and not session.can_relogin

    def test_the_session_refuses_another_board_than_the_one_meant(self, tmp_path, monkeypatch):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.session import RemoteSession, SessionFailure
        from orchestrator.utils import ssh_trust
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: HostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
        monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: "SHA256:" + "G" * 43)
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: pytest.fail("logged in to another board"))
        session = RemoteSession(Endpoint("192.0.2.32"), "u", tmp_path, expect_device_id="0" * 16)
        with pytest.raises(SessionFailure) as e:
            session.open()
        assert e.value.kind == "other_device" and session.last_check.device_id != "0" * 16


class TestTheEventsOfTheOtherSteps:

    @pytest.mark.parametrize("step, en, de", [
        ("command", "The step on the board continues", "Der Schritt auf dem Board geht weiter"),
        ("bench", "The benchmark runs on the board on its own", "Der Benchmark läuft auf dem Board für sich")])
    def test_a_wait_says_what_goes_on(self, step, en, de):
        from orchestrator.remote.session import RemoteEvent
        from orchestrator.remote.text import event_text
        waiting = RemoteEvent("waiting", elapsed=65, limit=300, step=step)
        assert en in event_text(waiting, "192.0.2.5", "en") and "192.0.2.5" in event_text(waiting, "192.0.2.5", "en")
        assert de in event_text(waiting, "192.0.2.5", "de")
        ai = event_text(waiting, "Shelf board", audience="ai")
        assert "Shelf board" in ai and "192.0.2" not in ai
        back = event_text(RemoteEvent("back", step=step), "192.0.2.5", "en")
        assert back.startswith("The board answers again") and "upload" not in back


def test_service_connect_opens_a_key_only_session(tmp_path, trusted_board):
    from orchestrator.remote.session import KeyOnly, RemoteSession
    from orchestrator.runtime.device import service
    device, logins, _presented = trusted_board
    session = service.connect(device, tmp_path, "dietpi", device.fingerprint, step="probe")
    assert isinstance(session, RemoteSession) and isinstance(session.credentials, KeyOnly)
    assert session.expect_fingerprint == device.fingerprint and logins == [("192.0.2.20", "dietpi", 22, None)]


class TestThePersonsLoginOnTheSession:

    @pytest.fixture
    def stored_board(self, monkeypatch):
        from orchestrator.runtime.profile import device_id_for_host_key
        from orchestrator.utils import ssh_trust
        fp = "SHA256:" + "H" * 43
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: HostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
        monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: fp)
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line: None)
        monkeypatch.setattr(ssh_trust, "client_key", lambda state: (None, "ssh-ed25519 AAAA framework"))
        return device_id_for_host_key(fp)

    def test_a_password_is_a_one_time_password_and_none_logs_in_with_the_key_only(self, tmp_path, stored_board):
        """Decision 2: the person's board steps may log in with a one-time password; without one the key only."""
        from orchestrator.remote.session import KeyOnly, OneTimePassword, RemoteSession
        from orchestrator.runtime.device import person_steps
        session, key = person_steps.connect_stored(tmp_path, stored_board, "192.0.2.10", 22, "dietpi", "once")
        assert isinstance(session, RemoteSession) and isinstance(session.credentials, OneTimePassword)
        assert session.expect_device_id == stored_board and isinstance(key, HostKey)
        assert "once" not in repr(session.credentials), "the password never shows"
        session, _key = person_steps.connect_stored(tmp_path, stored_board, "192.0.2.10", 22, "dietpi")
        assert isinstance(session.credentials, KeyOnly)


class TestTheOneCancel:

    def test_closing_a_session_ends_its_wait_for_the_board_at_once(self, board_session):
        """A Cancel holds the board session (CancelToken.hold) and closes it: a wait for the board ends at once
        (Stopped) instead of logging in again."""
        from orchestrator.remote.session import CancelToken, Stopped
        board, session, _events, clock = board_session
        token = CancelToken()
        token.hold(session)
        board.back = False
        seen = []

        def reachable(host, port):
            seen.append(clock[0])
            token.cancel()   # the person presses Cancel while the session waits
            return False

        session._reachable = reachable
        with pytest.raises(Stopped):
            session.wait_for_host("command")
        assert session.ended and session.client is None and len(seen) == 1 and clock[0] < 5
        assert token.is_set() and token.cancelled

    def test_a_session_handed_after_the_cancel_is_closed_at_once(self, board_session):
        from orchestrator.remote.session import CancelToken
        _board, session, _events, _clock = board_session
        token = CancelToken()
        token.cancel()
        token.hold(session)
        assert session.ended and session.client is None


class TestSshAvailability:
    """The person's board steps, `scan --device` and Mojo read remote/transport.py available() (a real import), and
    ssh_trust's old seam (ssh_trust.paramiko = None) counts as missing there too."""

    def test_every_reader_asks_available(self, tmp_path, monkeypatch):
        from types import SimpleNamespace as NS
        from orchestrator.Core.mojo.checks import devices as mojo_devices
        from orchestrator.remote import transport
        from orchestrator.runtime.device import person_steps
        from orchestrator.runtime.device.trusted import TrustedDevice, TrustedDevices
        monkeypatch.setattr(transport, "available", lambda: (False, "the 'paramiko' package cannot be loaded (X)"))
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.read_host_key(tmp_path, "192.0.2.10", 22)
        assert e.value.kind == "no_paramiko"
        board = TrustedDevice("0123456789abcdef", "Shelf", "192.0.2.10", 22, "dietpi", "ssh-ed25519",
                              "SHA256:" + "I" * 43, True, "2026-10-12T00:00:00Z", "cli")
        monkeypatch.setattr(TrustedDevices, "list", lambda self: [board])
        findings = mojo_devices.check_devices(NS(config=NS(user_state_dir=str(tmp_path))))
        assert any(f.code == "devices.unchecked" for f in findings)

    def test_the_cli_scan_asks_available(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from orchestrator.remote import transport
        monkeypatch.setattr(transport, "available", lambda: (False, "the 'paramiko' package is not installed"))
        monkeypatch.setattr(cli_module, "_trust_device", lambda *a, **k: pytest.fail("contacted the board"))
        result = CliRunner().invoke(cli_module.cli, ["scan", "--device", "192.0.2.10", "--user", "dietpi",
                                                     "--output", str(tmp_path / "p.txt")])
        assert result.exit_code == 1 and "needs the 'paramiko' package" in result.output

    def test_the_old_seam_counts_as_missing(self, monkeypatch):
        from orchestrator.remote import transport
        from orchestrator.utils import ssh_trust
        monkeypatch.setattr(ssh_trust, "paramiko", None)
        assert transport.available() == (False, "the 'paramiko' package is not installed")


class TestALocalProblemNamesTheFile:
    """GUI places that show local_problem name what failed on this computer: the system's words and the file's
    name, never its folder or the account (reasons.local_detail)."""

    def test_the_person_text_names_the_file_only(self):
        from orchestrator.remote import reasons
        error = PermissionError(13, "Permission denied", "/var/lib/state/known_hosts")
        text = reasons.person_text(reasons.LOCAL_PROBLEM, "en", address="192.0.2.5", port=22, error=error)
        assert "What failed on this computer: Permission denied: known_hosts." in text
        assert "/var/lib/state" not in text
        de = reasons.person_text(reasons.LOCAL_PROBLEM, "de", address="192.0.2.5", port=22, error=error)
        assert "Was auf diesem Computer nicht ging: Permission denied: known_hosts." in de
        plain = reasons.person_text(reasons.HOST_DOWN, "en", address="192.0.2.5", port=22, error=error)
        assert "What failed" not in plain, "only a local problem names a file"

    def test_the_host_key_question_and_the_ai_path_carry_it(self, tmp_path, trusted_board, monkeypatch):
        pytest.importorskip("PySide6")
        from orchestrator.gui.host_key import unreachable_text
        from orchestrator.remote import reasons
        from orchestrator.runtime.device import service
        from orchestrator.utils import ssh_trust
        error = PermissionError(13, "Permission denied", "/var/lib/state/id_ed25519_llm_framework")
        assert "id_ed25519_llm_framework" in unreachable_text("192.0.2.5", 22, error, "en")
        device, _logins, _presented = trusted_board

        def refuse(*a, **k):
            raise error

        monkeypatch.setattr(ssh_trust, "fetch_host_key", refuse)
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(device, tmp_path, "dietpi", device.fingerprint, step="probe")
        assert e.value.reason == reasons.LOCAL_PROBLEM and e.value.error is error
        assert "/var/lib" not in str(e.value), "the AI's text names no path"


class TestTheEventLines:

    def test_an_ais_job_gets_each_upload_percent_once_and_each_wait_line(self):
        from orchestrator.remote.session import RemoteEvent
        from orchestrator.remote.text import progress_events
        seen = []
        on_event = progress_events(lambda done, total, text: seen.append((done, total, text)), "Shelf board")
        for event in (RemoteEvent("uploading", done=0, total=1000), RemoteEvent("uploading", done=4, total=1000),
                      RemoteEvent("uploading", done=10, total=1000),
                      RemoteEvent("waiting", elapsed=5, limit=300, step="command"),
                      RemoteEvent("back", step="command")):
            on_event(event)
        assert [s[:2] for s in seen] == [(0, 1000), (10, 1000), (None, None), (None, None)]
        assert all("Shelf board" in s[2] for s in seen)

    def test_the_person_gets_the_first_line_of_a_wait_and_the_board_s_answer(self):
        from orchestrator.remote.session import RemoteEvent
        from orchestrator.remote.text import wait_lines
        lines = []
        on_event = wait_lines(lines.append, "192.0.2.5", "en")
        for event in (RemoteEvent("uploading", done=1, total=2), RemoteEvent("waiting", elapsed=0, limit=300),
                      RemoteEvent("waiting", elapsed=5, limit=300), RemoteEvent("back", done=1, total=2),
                      RemoteEvent("waiting", elapsed=0, limit=300, step="bench")):
            on_event(event)
        assert len(lines) == 3 and lines[0].startswith("Waiting for the board at 192.0.2.5")
        assert lines[1].startswith("The board answers again") and "benchmark" in lines[2]


# --- review of the chain (remote-callers-v): regression tests ------------------------------------------------------

def _silent_on(board, marker):
    """Commands with `marker` stay silent on the board (their read times out) while the connection itself stands."""
    real = board.exec_command

    def exec_command(command, timeout=None):
        if marker in command:
            board.commands.append(command)
            raise socket.timeout("timed out")
        return real(command, timeout)

    board.exec_command = exec_command


# the fake board answers `exit 0` and the commands with a real POSIX shell, as every test over it here
@POSIX
class TestASilentCommandIsNoNetworkDrop:
    """N5 waits for a network loss. A command that stays silent while its connection still answers is busy on the
    board, not cut off: its time limit ends it at once with the sentence RemoteShell always gave, without logging in
    again, running it a second time on the board or telling the person that the network dropped."""

    @pytest.mark.parametrize("again", [True, False])
    def test_its_time_limit_ends_it_with_the_old_sentence(self, board_session, again):
        board, session, events, _clock = board_session
        _silent_on(board, "hung-program")
        with pytest.raises(RemoteError, match=r"^no answer from the device within 7 s$"):
            RemoteShell(session).run("hung-program --list-devices", timeout=7, again=again)
        assert len([c for c in board.commands if "hung-program" in c]) == 1, "never started a second time"
        assert len(board.logins) == 1, "no second login"
        assert not [e for e in events if e.kind in ("waiting", "back")], "no network drop is reported"

    def test_a_command_whose_connection_is_silent_too_waits_for_the_board(self, board_session, monkeypatch):
        """The network is gone while paramiko's connection still stands (it does until TCP gives up): the session
        waits for the board, logs in again and runs a command that may run twice once more, as before."""
        from orchestrator.utils import ssh_trust
        board, session, events, _clock = board_session
        board.stalled = True
        real = board.exec_command

        def exec_command(command, timeout=None):
            if board.stalled:
                board.commands.append(command)
                raise socket.timeout("timed out")
            return real(command, timeout)

        board.exec_command = exec_command
        login = ssh_trust.connect

        def back(*a, **k):
            board.stalled = False   # the network is back
            return login(*a, **k)

        monkeypatch.setattr(ssh_trust, "connect", back)
        result = RemoteShell(session).run("echo read-only", again=True)
        assert result.status == 0 and result.out.strip() == "read-only"
        assert len(board.logins) == 2
        steps = [(e.kind, e.step) for e in events]
        assert ("waiting", "command") in steps and ("back", "command") in steps


@POSIX
def test_a_failed_step_names_the_board_only_by_its_label_for_an_ai(tmp_path, source, board_session, monkeypatch):
    """D0 and the chain's plan: the AI path's texts name the board by its label, never its address (the address, like
    the login, is the person's to see). The board session's own sentences name the host; the AI's copy of them, the
    external text, names the label instead."""
    from orchestrator.runtime.device import service
    from orchestrator.runtime.device.trusted import TrustedDevice
    board, session, _events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)

    def full(self, data):
        raise OSError("Failure")   # the board's disk is full; the connection stands

    monkeypatch.setattr(LocalFile, "write", full)
    with pytest.raises(RemoteError) as e:
        deploy_over(session, build, packaged)
    assert "192.0.2.30" in str(e.value), "the person's own text names the address"
    device = TrustedDevice("0123456789abcdef", "Shelf board", "192.0.2.30", 22, "dietpi", "ssh-ed25519",
                           "SHA256:" + "E" * 43, True, "2026-10-12T00:00:00Z", "cli")
    failed = service._session_failed(device, e.value, tmp_path / "state", "runtime_deploy", "The deploy")
    seen = " ".join([str(failed), *failed.external_text])
    assert "192.0.2.30" not in seen and "Shelf board" in " ".join(failed.external_text)
    assert "Failure" in " ".join(failed.external_text), "the board's words stay"


@POSIX
def test_the_tarball_on_the_board_is_the_owners_only_as_before(tmp_path, source, board_session):
    """Before the board session the tarball was written under umask 077 (`umask 077 && cat >`). An SFTP server creates
    a file with its own umask (often 022), so the upload makes the file under umask 077 first."""
    board, session, _events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)
    modes = []
    real = board.answer

    def answer(command, stdin):
        if "sha256sum -c SHA256SUMS" in command:
            (staging,) = list(board.root.glob(".staging-*"))
            modes.append((staging / "runtime.tar.gz").stat().st_mode & 0o777)
        return real(command, stdin)

    board.answer = answer
    old = os.umask(0o022)   # what an SFTP server usually runs with
    try:
        assert deploy_over(session, build, packaged).activated
    finally:
        os.umask(old)
    assert modes == [0o600]


@POSIX
def test_a_drop_at_the_sftp_check_still_uploads_over_sftp_after_the_wait(tmp_path, source, board_session,
                                                                         monkeypatch):
    """The network drops just as RemoteShell asks whether the board has SFTP: that is a drop, not a board without
    SFTP, so the upload waits for the board and goes over SFTP (with resume) instead of starting a stdin upload into
    the dropped network, which ended the deploy."""
    board, session, events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)
    real_stat = LocalSFTP.stat
    first = {"done": False}

    def stat(self, path):
        if not first["done"]:
            first["done"] = True
            self.board.go_offline()
        return real_stat(self, path)

    monkeypatch.setattr(LocalSFTP, "stat", stat)
    assert deploy_over(session, build, packaged).activated
    assert not any("cat >" in c for c in board.commands), "over SFTP after the wait"
    assert any(e.kind == "waiting" for e in events), "it waited for the board"


@POSIX
def test_the_person_is_told_of_every_wait_also_after_an_upload_that_started_again(tmp_path, source, board_session):
    """The network drops before a byte of the tarball arrived: after the wait the upload starts again from the
    beginning (no "back" event, nothing on the board to continue at). When the network drops again later, the person's
    output pane and console still get the first line of that second wait (wait_lines), as of the first one."""
    from orchestrator.remote.text import wait_lines
    board, session, events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)
    board.drop_after_bytes = 0          # the first chunk never arrives
    board.drop_on = "--list-devices"    # a check that only reads loses its answer later
    assert deploy_over(session, build, packaged).activated
    lines = []
    on_event = wait_lines(lines.append, "192.0.2.30", "en")
    for event in events:
        on_event(event)
    waits = [line for line in lines if line.startswith("Waiting for the board at 192.0.2.30")]
    assert len(waits) == 2, lines
    assert "upload" in waits[0] and "step on the board" in waits[1]
    assert lines[-1] == "The board answers again: the step continues."


@POSIX
def test_the_tarball_stays_the_owners_only_when_a_damaged_copy_is_sent_again(tmp_path, source, board_session,
                                                                            monkeypatch):
    """The first copy arrives damaged (its sha256 on the board differs), so the upload removes it and sends the file
    once more: that second copy is the user's only too, as `umask 077 && cat >` made every copy (an SFTP server
    creates a new file with its own umask)."""
    board, session, _events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)
    real_write = LocalFile.write
    damaged = []

    def write(self, data):
        if not damaged:
            damaged.append(True)
            data = bytes([data[0] ^ 0xFF]) + data[1:]   # one flipped byte on the way
        return real_write(self, data)

    monkeypatch.setattr(LocalFile, "write", write)
    modes = []
    real = board.answer

    def answer(command, stdin):
        if "sha256sum -c SHA256SUMS" in command:
            (staging,) = list(board.root.glob(".staging-*"))
            modes.append((staging / "runtime.tar.gz").stat().st_mode & 0o777)
        return real(command, stdin)

    board.answer = answer
    old = os.umask(0o022)   # what an SFTP server usually runs with
    try:
        assert deploy_over(session, build, packaged).activated
    finally:
        os.umask(old)
    assert damaged and any(c.startswith("sh -c 'rm -f ") and ".part" in c for c in board.commands), "sent twice"
    assert modes == [0o600]


# --- stage C wave 2: the CLI's wait lines and a failed wait (follow-up of remote-callers-v) -------------------------

def test_the_first_line_of_a_wait_carries_the_key_that_ends_it():
    from orchestrator.remote.session import RemoteEvent
    from orchestrator.remote.text import wait_lines
    lines = []
    on_event = wait_lines(lines.append, "192.0.2.5", "en", hint=" (Ctrl+C)")
    for event in (RemoteEvent("waiting", elapsed=0, limit=300), RemoteEvent("waiting", elapsed=5, limit=300),
                  RemoteEvent("back", done=1, total=2)):
        on_event(event)
    assert len(lines) == 2 and lines[0].endswith(" (Ctrl+C)") and not lines[1].endswith("(Ctrl+C)")


def test_the_cli_names_ctrl_c_in_its_wait_line_as_deploy_run_does(tmp_path, monkeypatch):
    """`device runtime deploy|verify|prune --on-device` (cli._connect_device) say a wait for the board with the key
    that ends it, as `deploy run` says it (cli._deploy_event_text)."""
    from orchestrator import cli as cli_module
    from orchestrator.remote.session import RemoteEvent
    from orchestrator.runtime.device import person_steps
    from test_cli_builds import quiet_console
    printed = quiet_console(monkeypatch)

    def connect(state, device_id, host, port, user, password=None, *, step="", on_client=None, cancel=None,
                on_event=None):
        on_event(RemoteEvent("waiting", elapsed=0, limit=300, step="command"))
        return object(), None
    monkeypatch.setattr(person_steps, "connect_stored", connect)
    cli_module._connect_device(tmp_path, "0123456789abcdef", "192.0.2.30", 22, "dietpi", None, step="verify")
    (line,) = [ln for ln in printed.getvalue().splitlines() if ln.startswith("Waiting for the board at 192.0.2.30")]
    assert line.endswith(" (Ctrl+C)"), line


def test_a_failed_wait_prints_the_plain_reason_not_the_raw_code(monkeypatch):
    """A board that did not answer again after its network dropped ends the command with what the reason code means
    and the next step (cli._print_reason, as `deploy run` prints it), not with the session's sentence that only names
    the code; any other failure keeps its own words."""
    from orchestrator import cli as cli_module
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device.remote import RemoteError
    from test_cli_builds import quiet_console
    printed = quiet_console(monkeypatch)
    waited = RemoteError(f"The board did not answer again within 300 s ({reasons.HOST_DOWN})", "wait",
                         reasons.HOST_DOWN)
    with pytest.raises(SystemExit) as code:
        cli_module._board_step_failed(None, waited, "192.0.2.30", 22)
    out = printed.getvalue()
    assert code.value.code == 1
    assert reasons.person_title(reasons.HOST_DOWN, "en", address="192.0.2.30", port=22) in out
    assert reasons.person_next(reasons.HOST_DOWN, "en", address="192.0.2.30", port=22)[:40] in out
    assert "within 300 s" not in out, out
    printed.truncate(0)
    printed.seek(0)
    with pytest.raises(SystemExit):
        cli_module._board_step_failed(None, RemoteError("installing on the device failed (exit 2): no space"),
                                      "192.0.2.30", 22)
    assert "installing on the device failed (exit 2): no space" in printed.getvalue()


@POSIX
def test_a_ctrl_c_during_a_wait_is_not_hidden_by_the_staging_clean_up(tmp_path, source, board_session):
    """Ctrl+C while the runtime deploy waits for the board (the wait line names it) ends the command as a Ctrl+C
    (click's "Aborted!"): the staging folder's clean-up, which cannot reach the board then, no longer replaces it
    with "the connection to the device is closed"."""
    board, session, _events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)
    board.back = False
    board.drop_before = "SHA256SUMS"

    def ctrl_c(_seconds):
        raise KeyboardInterrupt
    session._sleep = ctrl_c
    with pytest.raises(KeyboardInterrupt):
        deploy_over(session, build, packaged)
