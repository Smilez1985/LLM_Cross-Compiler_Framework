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
        assert runs == ["cat big", "cat big"]

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
