"""Why a board cannot be reached (N6) and what each contact records for Mojo (W1), pinned for consolidation chain
c2-remote-base: the reason codes move into orchestrator/remote/reasons.py, the 'classify, else device_host_down for an
OSError' copies become one reach_code(), and the six note_reach helpers become one recorder. Every copy must keep
giving the same code and writing the same row.
"""

import contextlib
import errno
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.runtime.device import reasons  # noqa: E402
from orchestrator.utils import ssh_trust  # noqa: E402

from test_board_deploy import TARGET, board, manager, package, run  # noqa: E402,F401 - the fake board of the N5 tests
from test_deployment import config  # noqa: E402,F401 - the manager fixture's configuration

HOST, PORT = "192.0.2.10", 2222
# The real key read and login, kept before the fake board's fixture replaces them.
REAL_FETCH_HOST_KEY, REAL_CONNECT = ssh_trust.fetch_host_key, ssh_trust.connect


def _errors():
    """(error, classify's code) for every kind of error a contact with a board raises."""
    no_valid = paramiko.ssh_exception.NoValidConnectionsError
    return [
        (socket.gaierror(-2, "Name or service not known"), reasons.HOST_DOWN),
        (socket.timeout("timed out"), reasons.HOST_DOWN),
        (TimeoutError("timed out"), reasons.HOST_DOWN),
        (OSError(errno.EHOSTUNREACH, "No route to host"), reasons.HOST_DOWN),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), reasons.HOST_DOWN),
        (OSError(errno.ETIMEDOUT, "Connection timed out"), reasons.HOST_DOWN),
        (ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"), reasons.PORT_CLOSED),
        (OSError(errno.ECONNREFUSED, "Connection refused"), reasons.PORT_CLOSED),
        (OSError(10061, "refused (Windows)"), reasons.PORT_CLOSED),
        (paramiko.AuthenticationException("Authentication failed."), reasons.AUTH_FAILED),
        (paramiko.BadHostKeyException("h", paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()),
         reasons.HOST_KEY_CHANGED),
        (ssh_trust.HostKeyChanged("10.0.0.5", 22), reasons.HOST_KEY_CHANGED),
        (ssh_trust.HostKeyError("SSH needs the 'paramiko' package."), None),
        (ssh_trust.UnknownHostKey("10.0.0.5", 22, paramiko.ECDSAKey.generate()), None),
        (no_valid({("10.0.0.5", 22): ConnectionRefusedError(errno.ECONNREFUSED, "refused")}), reasons.PORT_CLOSED),
        (no_valid({("10.0.0.5", 22): ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
                   ("fe80::1", 22, 0, 0): socket.timeout("timed out")}), reasons.HOST_DOWN),
        (paramiko.SSHException("Error reading SSH protocol banner"), reasons.PORT_CLOSED),
        (paramiko.SSHException("Incompatible ssh peer (no acceptable kex algorithm); protocol"), reasons.PORT_CLOSED),
        (paramiko.SSHException("No authentication methods available"), reasons.AUTH_FAILED),
        (paramiko.SSHException("No existing session"), None),
        # A dropped connection is none of the four (a board that restarts its SSH server raises these).
        (ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), None),
        (ConnectionAbortedError(errno.ECONNABORTED, "Software caused connection abort"), None),
        (BrokenPipeError(errno.EPIPE, "Broken pipe"), None),
        (EOFError(), None),
        (OSError("Socket is closed"), None),
        (ValueError("something else"), None),
    ]


ERRORS = _errors()


@pytest.mark.parametrize("error, code", ERRORS, ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else
                         str(v))
def test_classify(error, code):
    assert reasons.classify(error) == code


def expected_reach(error, code):
    """The code every 'classify, else device_host_down for an OSError' copy gives ("" when none)."""
    if code is not None:
        return code
    return reasons.HOST_DOWN if isinstance(error, OSError) else ""


@pytest.mark.parametrize("error, code", ERRORS, ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else
                         str(v))
def test_every_copy_gives_the_same_code(error, code, tmp_path, monkeypatch):
    from orchestrator.runtime import scan_steps
    from orchestrator.runtime.device import person_steps, service
    from orchestrator.tools import tools_deploy
    want = expected_reach(error, code)
    assert scan_steps.reach_code(error) == want
    assert person_steps.unreachable_error(error, HOST, PORT).reason == want
    tool_error = tools_deploy.unreachable_error(SimpleNamespace(label="Shelf"), error)
    assert (getattr(tool_error, "blocker", None) or "") == want
    if not want:
        assert tool_error.code == "failed"

    device = SimpleNamespace(host=HOST, port=PORT, label="Shelf", device_id="0123456789abcdef")

    def fetch(host, port=22, timeout=10.0):
        raise error
    monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
    with pytest.raises(service.DeviceRuntimeError) as raised:
        service.read_key(device, tmp_path)
    assert raised.value.reason == want
    if want:
        assert raised.value.code == reasons.TOOL_CODES[want] and raised.value.next == reasons.AI_NEXT[want]

    import orchestrator.cli as cli
    noted = []
    monkeypatch.setattr(cli, "_note_reach", lambda ctx, host, port, step, c, device_id="", state_dir=None:
                        noted.append(c))
    monkeypatch.setattr(cli, "_print_reason", lambda ctx, c, host, port: noted.append(("shown", c)))
    with pytest.raises(SystemExit):
        cli._unreachable(None, HOST, PORT, error, step="deploy", state_dir=tmp_path)
    assert noted == ([want, ("shown", want)] if want else [])


def test_the_gui_shows_the_same_code(monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    for error, code in ERRORS:
        want = expected_reach(error, code)
        text = host_key.unreachable_text(HOST, PORT, error, "en")
        if want:
            assert text == reasons.person_text(want, "en", address=HOST, port=PORT)
        else:
            assert str(error) in text


def test_check_port_is_one_tcp_connection_without_icmp(monkeypatch):
    seen = []

    def connect(address, timeout=None):
        seen.append((address, timeout))
        raise ConnectionResetError(errno.ECONNRESET, "reset")
    monkeypatch.setattr(socket, "create_connection", connect)
    assert reasons.check_port(HOST, PORT) == reasons.HOST_DOWN, "an OSError that is none of the four: host down"
    assert seen == [((HOST, PORT), reasons.CONNECT_TIMEOUT)]

    class Opened:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout=None: Opened())
    assert reasons.check_port(HOST, str(PORT)) is None


def test_the_reasons_module_names():
    assert reasons.REASONS == ("device_host_down", "device_port_closed", "device_auth_failed",
                               "device_host_key_changed")
    assert set(reasons.__all__) >= {"AI_NEXT", "AUTH_FAILED", "CONNECT_TIMEOUT", "HOST_DOWN", "HOST_KEY_CHANGED",
                                    "PORT_CLOSED", "REASONS", "TOOL_CODES", "check_port", "classify",
                                    "classify_ssh_output", "person_next", "person_text", "person_title"}


# --- which row each contact writes for Mojo -------------------------------------------------------------------------

@pytest.fixture
def rows(monkeypatch):
    """What TrustedDevices.note_reach (by device ID) and note_reach_at (by address) are asked to record."""
    from orchestrator.runtime.device.trusted import TrustedDevices
    seen = []
    monkeypatch.setattr(TrustedDevices, "note_reach",
                        lambda self, device_id, step, code: seen.append(("id", device_id, step, code)))
    monkeypatch.setattr(TrustedDevices, "note_reach_at",
                        lambda self, host, port, step, code: seen.append(("at", host, port, step, code)))
    return seen


def test_the_deploy_records_by_address(rows, tmp_path):
    from orchestrator.Core.deployment_manager import DeployResult, DeploymentManager
    from test_deployment import FakeFramework
    from orchestrator.Core.config_manager import ConfigManager
    dm = DeploymentManager(FakeFramework(ConfigManager(tmp_path / "configs")))
    dm._note_reach(HOST, PORT, DeployResult(True, exit_status=0))
    dm._note_reach(HOST, PORT, DeployResult(False, code=reasons.PORT_CLOSED))
    dm._note_reach(HOST, PORT, DeployResult(False, exit_status=1))           # deploy.sh ran there and failed
    dm._note_reach(HOST, PORT, DeployResult(False, exit_status=255))         # ssh's own failure: says nothing
    dm._note_reach(HOST, PORT, DeployResult(False, reason="no package"))     # never reached the board
    dm._note_reach(HOST, PORT, DeployResult(False, code="not a reason"))
    assert rows == [("at", HOST, PORT, "deploy", ""), ("at", HOST, PORT, "deploy", reasons.PORT_CLOSED),
                    ("at", HOST, PORT, "deploy", "")]


def test_the_ai_steps_record_by_device_id(rows, tmp_path):
    from orchestrator.runtime.device import service
    device = SimpleNamespace(device_id="0123456789abcdef")
    service.note_reach(tmp_path, device, "probe", reasons.HOST_DOWN)
    service.note_reach(tmp_path, device, "benchmark", "")
    service.note_reach(tmp_path, device, "", reasons.HOST_DOWN)
    assert rows == [("id", "0123456789abcdef", "probe", reasons.HOST_DOWN),
                    ("id", "0123456789abcdef", "benchmark", "")]


def test_the_person_steps_record_by_device_id(rows, tmp_path):
    from orchestrator.runtime.device import person_steps
    person_steps._note(tmp_path, "0123456789abcdef", "verify", reasons.AUTH_FAILED)
    person_steps._note(tmp_path, "0123456789abcdef", "verify", "")
    person_steps._note(tmp_path, "0123456789abcdef", "", reasons.AUTH_FAILED)
    person_steps.unreachable_error(socket.timeout(), HOST, PORT, state_dir=tmp_path, device_id="0123456789abcdef",
                                   step="runtime_deploy")
    person_steps.unreachable_error(EOFError(), HOST, PORT, state_dir=tmp_path, device_id="0123456789abcdef",
                                   step="runtime_deploy")
    person_steps.unreachable_error(socket.timeout(), HOST, PORT, step="runtime_deploy")   # no state folder
    assert rows == [("id", "0123456789abcdef", "verify", reasons.AUTH_FAILED),
                    ("id", "0123456789abcdef", "verify", ""),
                    ("id", "0123456789abcdef", "runtime_deploy", reasons.HOST_DOWN)]


def test_the_scan_records_by_address(rows, tmp_path):
    from orchestrator.runtime import scan_steps
    scan_steps._note_reach(tmp_path, HOST, PORT, reasons.AUTH_FAILED)
    scan_steps._note_reach(tmp_path, HOST, PORT, "")
    assert rows == [("at", HOST, PORT, "probe", reasons.AUTH_FAILED), ("at", HOST, PORT, "probe", "")]


def test_the_gui_host_key_question_records_only_a_problem(rows, tmp_path):
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    host_key._note(tmp_path, HOST, PORT, "deploy", reasons.HOST_KEY_CHANGED)
    host_key._note(tmp_path, HOST, PORT, "deploy", "")      # reading a key is no login: it ends no problem
    host_key._note(tmp_path, HOST, PORT, "", reasons.HOST_DOWN)
    assert rows == [("at", HOST, PORT, "deploy", reasons.HOST_KEY_CHANGED)]


def test_the_cli_records_by_device_id_else_by_address(rows, tmp_path):
    import orchestrator.cli as cli
    cli._note_reach(None, HOST, PORT, "verify", reasons.HOST_DOWN, "0123456789abcdef", tmp_path)
    cli._note_reach(None, HOST, PORT, "probe", "", state_dir=tmp_path)
    cli._note_reach(None, HOST, PORT, "", reasons.HOST_DOWN, state_dir=tmp_path)
    assert rows == [("id", "0123456789abcdef", "verify", reasons.HOST_DOWN), ("at", HOST, PORT, "probe", "")]


def test_no_recorder_fails_its_caller(monkeypatch, tmp_path):
    """Bookkeeping never changes what a step does: a store that cannot record is ignored."""
    from orchestrator.Core.deployment_manager import DeployResult, DeploymentManager
    from orchestrator.runtime import scan_steps
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import TrustedDevices
    import orchestrator.cli as cli

    def broken(*a, **k):
        raise RuntimeError("the store is broken")
    monkeypatch.setattr(TrustedDevices, "note_reach", broken)
    monkeypatch.setattr(TrustedDevices, "note_reach_at", broken)
    from test_deployment import FakeFramework
    from orchestrator.Core.config_manager import ConfigManager
    DeploymentManager(FakeFramework(ConfigManager(tmp_path / "configs")))._note_reach(
        HOST, PORT, DeployResult(False, code=reasons.HOST_DOWN))
    person_steps._note(tmp_path, "0123456789abcdef", "verify", reasons.HOST_DOWN)
    scan_steps._note_reach(tmp_path, HOST, PORT, reasons.HOST_DOWN)
    cli._note_reach(None, HOST, PORT, "verify", reasons.HOST_DOWN, "0123456789abcdef", tmp_path)
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    host_key._note(tmp_path, HOST, PORT, "deploy", reasons.HOST_DOWN)


def test_a_real_store_keeps_the_last_problem(tmp_path):
    """The rows land in device_deploys.json as Mojo reads them (TrustedDevices.reach_problem)."""
    from test_mojo_boards import board
    from orchestrator.runtime.device import service
    from orchestrator.runtime.device.trusted import TrustedDevices
    device = board(Path(tmp_path), HOST, PORT)
    service.note_reach(tmp_path, device, "benchmark", reasons.PORT_CLOSED)
    assert TrustedDevices(tmp_path).reach_problem(device)["code"] == reasons.PORT_CLOSED
    service.note_reach(tmp_path, device, "benchmark", "")
    assert TrustedDevices(tmp_path).reach_problem(device) is None


# --- the one implementation (orchestrator/remote/reasons.py) ---------------------------------------------------------

def test_the_old_module_is_the_moved_one():
    """runtime/device/reasons.py is remote/reasons.py itself: a patch on either name reaches every caller."""
    import orchestrator.runtime.device.reasons as old
    from orchestrator.remote import reasons as moved
    assert old is moved and reasons is moved


@pytest.mark.parametrize("error, code", ERRORS, ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else
                         str(v))
def test_reach_code(error, code):
    from orchestrator.remote.reasons import reach_code
    assert reach_code(error) == expected_reach(error, code)


def test_the_copies_call_reach_code(monkeypatch, tmp_path):
    """The six 'classify, else device_host_down' copies are calls of reach_code now (one patch changes them all)."""
    from orchestrator.remote import reasons as moved
    from orchestrator.runtime import scan_steps
    from orchestrator.runtime.device import person_steps
    from orchestrator.tools import tools_deploy
    assert scan_steps.reach_code is moved.reach_code
    monkeypatch.setattr(moved, "reach_code", lambda error: reasons.PORT_CLOSED)
    assert person_steps.unreachable_error(ValueError("x"), HOST, PORT).reason == reasons.PORT_CLOSED
    assert tools_deploy.unreachable_error(SimpleNamespace(label="Shelf"), ValueError("x")).blocker == \
        reasons.PORT_CLOSED
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    assert host_key.unreachable_text(HOST, PORT, ValueError("x"), "en") == \
        reasons.person_text(reasons.PORT_CLOSED, "en", address=HOST, port=PORT)


# --- regression (Fixed): a dropped connection while the deploy waits for the board ----------------------------------

TRANSIENT = [ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"),
             ConnectionAbortedError(errno.ECONNABORTED, "Software caused connection abort"),
             BrokenPipeError(errno.EPIPE, "Broken pipe"), EOFError()]


@pytest.mark.parametrize("error", TRANSIENT, ids=lambda e: type(e).__name__)
def test_while_waiting_a_dropped_connection_means_not_back_yet(error):
    from orchestrator.remote.reasons import reach_code
    assert reach_code(error, while_waiting=True) == reasons.HOST_DOWN
    assert reasons.classify(error) is None, "outside the wait it is still none of the four by itself"


def test_while_waiting_changes_nothing_else():
    from orchestrator.remote.reasons import reach_code
    for error, code in ERRORS:
        if not any(type(error) is type(t) for t in TRANSIENT):
            assert reach_code(error, while_waiting=True) == reach_code(error), error


@pytest.mark.parametrize("error", TRANSIENT, ids=lambda e: type(e).__name__)
def test_the_deploy_keeps_waiting_while_the_ssh_server_restarts(error, manager, board, package, monkeypatch):
    """After a network drop the deploy waits for the board (N5). When the board is back, its SSH server may still be
    starting: the key exchange ends with a reset or EOF. classify() gave None for these, the login reported code "",
    and wait_for_board gave up at once instead of waiting on."""
    board.drop_after = 600_000
    board.back_at = 30.0
    restarting = {"left": 2}

    def fetch(host, port=22, timeout=10.0):
        board.tick()
        if not board.online:
            raise socket.timeout("timed out")
        if board.logins and restarting["left"]:   # back on the network, sshd not ready yet
            restarting["left"] -= 1
            raise error
        return board.key
    monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
    result, events = run(manager, package)
    assert result.success and result.resumed == 1, result.reason
    assert restarting["left"] == 0 and board.starts == 1
    assert bytes(board.files[f"{TARGET}/{package.name}"]) == package.read_bytes()


@contextlib.contextmanager
def dropping_ssh_server(mode):
    """A local TCP server that drops every connection the way a board's starting SSH server does: at once ("close"),
    after its banner ("after_banner") or during the key exchange, with the client's bytes unread ("during_kex")."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    server.settimeout(0.1)   # accept() wakes up to see the end (closing alone does not wake it on Linux)
    done = threading.Event()

    def serve():
        while not done.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.settimeout(5.0)
                if mode != "close":
                    conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
                if mode == "after_banner":
                    conn.recv(64)
                elif mode == "during_kex":
                    time.sleep(0.2)
            except OSError:
                pass
            finally:
                conn.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        done.set()
        thread.join(timeout=10)
        server.close()


DROP_MODES = ["close", "after_banner", "during_kex"]


@pytest.mark.parametrize("mode", DROP_MODES)
def test_what_paramiko_really_raises_on_a_drop_keeps_the_wait_going(mode, tmp_path):
    """The exceptions paramiko itself raises (an SSHException about the banner, EOFError, ConnectionResetError), not
    stand-ins: reading the host key and logging in both give a code wait_for_board keeps waiting on."""
    from orchestrator.remote.reasons import reach_code
    with dropping_ssh_server(mode) as port:
        for contact in (lambda: REAL_FETCH_HOST_KEY("127.0.0.1", port, timeout=5.0),
                        lambda: REAL_CONNECT("127.0.0.1", "radxa", tmp_path, port=port, timeout=5.0)):
            with pytest.raises(Exception) as raised:
                contact()
            assert reach_code(raised.value, while_waiting=True) in (reasons.HOST_DOWN, reasons.PORT_CLOSED), \
                repr(raised.value)


@pytest.mark.parametrize("mode", DROP_MODES)
def test_the_deploy_waits_through_what_paramiko_really_raises(mode, manager, board, package, monkeypatch):
    """The N5 wait end to end with paramiko's own exceptions: the board is back on the network, but its first two
    key reads reach a server that drops the connection."""
    board.drop_after = 600_000
    board.back_at = 30.0
    restarting = {"left": 2}
    with dropping_ssh_server(mode) as port:
        def fetch(host, port_=22, timeout=10.0):
            board.tick()
            if not board.online:
                raise socket.timeout("timed out")
            if board.logins and restarting["left"]:   # back on the network, sshd not ready yet
                restarting["left"] -= 1
                return REAL_FETCH_HOST_KEY("127.0.0.1", port, timeout=5.0)
            return board.key
        monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
        result, _events = run(manager, package)
    assert result.success and result.resumed == 1, result.reason
    assert restarting["left"] == 0 and board.starts == 1


def test_a_reset_at_the_first_login_is_recorded_like_everywhere_else(manager, board, package, monkeypatch):
    """The deploy's first contact uses the same rule as every other path: an OSError that is none of the four means the
    board does not answer (it reported code "" with the exception's text before)."""
    def fetch(host, port=22, timeout=10.0):
        raise ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")
    monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
    result, _events = run(manager, package)
    assert not result.success and result.code == reasons.HOST_DOWN and "(device_host_down)" in result.reason


def test_an_eof_at_the_first_login_is_still_no_reason_code(manager, board, package, monkeypatch):
    def fetch(host, port=22, timeout=10.0):
        raise EOFError()
    monkeypatch.setattr(ssh_trust, "fetch_host_key", fetch)
    result, _events = run(manager, package)
    assert not result.success and result.code == "" and "Could not read the host key" in result.reason


# --- the one reach recorder and the one failure type -----------------------------------------------------------------

def test_every_contact_records_through_record_reach(monkeypatch, tmp_path):
    """The six note_reach helpers are calls of trusted.record_reach (by device ID, else by endpoint)."""
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.deployment_manager import DeployResult, DeploymentManager
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime import scan_steps
    from orchestrator.runtime.device import person_steps, service, trusted
    import orchestrator.cli as cli
    from test_deployment import FakeFramework
    seen = []
    monkeypatch.setattr(trusted, "record_reach", lambda state, step, code, device_id="", endpoint=None:
                        seen.append((step, code, device_id, endpoint)))
    DeploymentManager(FakeFramework(ConfigManager(tmp_path / "configs")))._note_reach(
        HOST, PORT, DeployResult(False, code=reasons.HOST_DOWN))
    service.note_reach(tmp_path, SimpleNamespace(device_id="0123456789abcdef"), "probe", "")
    person_steps._note(tmp_path, "0123456789abcdef", "verify", reasons.AUTH_FAILED)
    scan_steps._note_reach(tmp_path, HOST, PORT, "")
    cli._note_reach(None, HOST, PORT, "verify", reasons.PORT_CLOSED, "", tmp_path)
    at = Endpoint(HOST, PORT)
    assert seen == [("deploy", reasons.HOST_DOWN, "", at), ("probe", "", "0123456789abcdef", None),
                    ("verify", reasons.AUTH_FAILED, "0123456789abcdef", None), ("probe", "", "", at),
                    ("verify", reasons.PORT_CLOSED, "", at)]
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    host_key._note(tmp_path, HOST, PORT, "deploy", reasons.HOST_KEY_CHANGED)
    assert seen[-1] == ("deploy", reasons.HOST_KEY_CHANGED, "", at)


def test_record_reach_never_fails_and_needs_a_step(rows, monkeypatch, tmp_path):
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime.device import service
    from orchestrator.runtime.device.trusted import TrustedDevices, record_reach
    record_reach(tmp_path, "", reasons.HOST_DOWN, device_id="0123456789abcdef")
    record_reach(tmp_path, "probe", reasons.HOST_DOWN)   # neither a device ID nor an endpoint: nothing to record
    record_reach(tmp_path, "probe", reasons.HOST_DOWN, endpoint=Endpoint(HOST, PORT))
    assert rows == [("at", HOST, PORT, "probe", reasons.HOST_DOWN)]

    def broken(*a, **k):
        raise RuntimeError("the store is broken")
    monkeypatch.setattr(TrustedDevices, "note_reach", broken)
    record_reach(tmp_path, "probe", reasons.HOST_DOWN, device_id="0123456789abcdef")
    service.note_reach(tmp_path, SimpleNamespace(device_id="0123456789abcdef"), "probe", reasons.HOST_DOWN)


def test_every_board_step_failure_is_a_remote_failure():
    from orchestrator.remote.errors import RemoteFailure
    from orchestrator.runtime.device.person_steps import BoardStepError
    from orchestrator.runtime.device.service import DeviceRuntimeError
    from orchestrator.runtime.scan_steps import DeviceScanError
    cause = socket.timeout("timed out")
    failures = [BoardStepError("Could not reach it", "unreachable", reason=reasons.HOST_DOWN, error=cause, a=1),
                DeviceScanError("Could not log in", "login", reason=reasons.HOST_DOWN, error=cause),
                DeviceRuntimeError("cannot be used now", code="hardware_unavailable", next="Ask the person.",
                                   reason=reasons.HOST_DOWN)]
    for failure in failures:
        assert isinstance(failure, RemoteFailure) and isinstance(failure, RuntimeError)
        assert failure.reason == reasons.HOST_DOWN
        with pytest.raises(RemoteFailure):
            raise failure
    assert (failures[0].kind, failures[0].error, failures[0].facts) == ("unreachable", cause, {"a": 1})
    assert (failures[1].kind, failures[1].error) == ("login", cause)
    assert (failures[2].kind, failures[2].error, failures[2].code, failures[2].next) == \
        ("", None, "hardware_unavailable", "Ask the person.")
    assert str(failures[2]) == "cannot be used now" and failures[2].external_text == []


# --- regression (Fixed): a problem on this computer is not "the board does not answer" ------------------------------
# reach_code counted every OSError that is none of the four as device_host_down. A full disk, a read-only disk or no
# permission on the framework's own files (its SSH key, known_hosts) then told the person to switch the board on, and
# the deploy recorded the board as down for Mojo. These now have their own code, local_problem, with the person's text
# and next step (EN/DE) and the AI's next step; they are never recorded with the board.

KEY_FILE = "/srv/state/llm-framework/id_ed25519_llm_framework"   # nosec B108 - a made-up path, never opened
LOCAL_ERRORS = [
    PermissionError(errno.EACCES, "Permission denied", KEY_FILE),
    OSError(errno.ENOSPC, "No space left on device", "/srv/state/llm-framework/known_hosts"),
    OSError(errno.EROFS, "Read-only file system", "/srv/state/llm-framework/.known_hosts.tmp"),
    OSError(errno.ENOSPC, "No space left on device"),
    OSError(errno.EMFILE, "Too many open files"),
    IsADirectoryError(errno.EISDIR, "Is a directory", "/srv/state/llm-framework/known_hosts"),
    FileNotFoundError(errno.ENOENT, "No such file or directory", KEY_FILE),
    PermissionError(errno.EPERM, "Operation not permitted"),   # this computer refuses the connection itself
]


@pytest.mark.parametrize("error", LOCAL_ERRORS, ids=lambda e: f"{type(e).__name__}-{e.errno}")
def test_a_problem_on_this_computer_has_its_own_code(error):
    from orchestrator.remote.reasons import LOCAL_PROBLEM, is_local, reach_code
    assert is_local(error) and reach_code(error) == LOCAL_PROBLEM
    assert reach_code(error, while_waiting=True) == LOCAL_PROBLEM, "waiting for the board does not help"
    assert reasons.classify(error) is None and LOCAL_PROBLEM not in reasons.REASONS


def test_the_board_s_own_errors_keep_their_codes():
    from orchestrator.remote.reasons import is_local, reach_code
    for error, code in ERRORS:
        assert not is_local(error), error
        assert reach_code(error) == expected_reach(error, code), error
    for error in TRANSIENT + [socket.timeout("timed out"), socket.gaierror(-2, "Name or service not known")]:
        assert not is_local(error), error


def test_the_detail_names_the_file_never_its_folder():
    from orchestrator.remote.reasons import local_detail
    assert local_detail(LOCAL_ERRORS[0]) == "Permission denied: id_ed25519_llm_framework"
    assert local_detail(OSError(errno.ENOSPC, "No space left on device")) == "No space left on device"
    assert local_detail(PermissionError(errno.EACCES, "Access is denied", "C:\\Users\\Jane Roe\\state\\known_hosts")) \
        == "Access is denied: known_hosts"


@pytest.mark.parametrize("folder", ["/home/jroe", "/home/jroe/", "/Users/jroe", "C:\\Users\\jroe", "/usr/home/jroe",
                                    "/var/home/jroe", "/export/home/jroe"])
def test_the_detail_never_names_an_account_s_home_folder(folder):
    """A state folder whose home folder cannot be made (a service account without one): the file name would be the
    account's name, and the deploy's reason reaches an AI (no host account name, D0). It says which kind of folder."""
    from orchestrator.remote.reasons import local_detail
    from orchestrator.remote.session import failed_because
    error = PermissionError(errno.EACCES, "Permission denied", folder)
    assert local_detail(error) == "Permission denied: a home folder"
    assert "jroe" not in failed_because(reasons.LOCAL_PROBLEM, error)


def test_the_detail_never_names_the_own_account(monkeypatch):
    from orchestrator.remote.reasons import local_detail
    monkeypatch.setenv("HOME", "/srv/accounts/jroe")
    monkeypatch.setenv("USERPROFILE", "C:\\Accounts\\jroe")
    monkeypatch.setenv("USER", "jroe")
    monkeypatch.setenv("USERNAME", "jroe")
    for path in ("/srv/accounts/jroe", "C:\\Accounts\\jroe", "/opt/jroe"):
        assert "jroe" not in local_detail(PermissionError(errno.EACCES, "Permission denied", path)), path
    assert local_detail(PermissionError(errno.EACCES, "Permission denied", "/srv/accounts/jroe/.ssh")) == \
        "Permission denied: .ssh"


def test_the_own_account_is_every_name_of_the_one_home_folder_helper(monkeypatch):
    """R0 (stage C seam): this computer's home folders come from orchestrator/security/paths.py home_folders, the one
    helper the AI-facing scrubs use (Path.home(), HOME and USERPROFILE), not from a second lookup of `~` only; so a
    Windows profile folder other than HOME is never named either."""
    from orchestrator.remote.reasons import local_detail
    monkeypatch.setenv("HOME", "/srv/accounts/alice")
    monkeypatch.setenv("USERPROFILE", "C:\\Accounts\\bob")
    monkeypatch.setenv("USER", "carol")
    monkeypatch.setenv("USERNAME", "carol")
    for name in ("alice", "bob", "carol"):
        detail = local_detail(PermissionError(errno.EACCES, "Permission denied", f"/opt/{name}"))
        assert detail == "Permission denied: a folder named after the account", (name, detail)


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_code_has_the_person_s_text_and_the_ai_s_next_step(lang):
    from orchestrator.tools.facts_catalog import BLOCKERS
    from orchestrator.utils.l10n_device import TEXTS
    from orchestrator.utils import next_steps
    assert reasons.CODES == reasons.REASONS + (reasons.LOCAL_PROBLEM,)
    for code in reasons.CODES:
        assert TEXTS[lang].get(f"device.reason.{code}") and TEXTS[lang].get(f"device.next.{code}"), code
        text = reasons.person_text(code, lang, address="myboard.local", port=2222)
        assert "myboard.local" in text and "{" not in text
        ai = reasons.AI_NEXT[code]
        assert "{" not in ai and "/" not in ai and "@" not in ai
        assert reasons.TOOL_CODES[code] in ("hardware_unavailable", "needs_person")
    assert "board" in reasons.person_title(reasons.LOCAL_PROBLEM, "en", address="myboard.local") and \
        "this computer" in reasons.person_title(reasons.LOCAL_PROBLEM, "en", address="myboard.local")
    # a problem on this computer is no blocker of the delegation rules (it is no fact about the call); its next step
    # is Mojo's check of the installation, which looks at this computer's disks and the state folder
    assert reasons.LOCAL_PROBLEM not in BLOCKERS and reasons.LOCAL_STEPS == ("mojo.oracle",)
    assert all(step in next_steps.steps() for step in reasons.LOCAL_STEPS)


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_local_problem_s_next_step_promises_no_log_line(lang):
    """The person's next step of local_problem is shown by the GUI's key read (gui/host_key.py), the Boards window,
    the remote scan, the benchmark window and Mojo's board check, and none of them writes the error to a log: a text
    that sends the person to "the log" for the file sends them nowhere. It says what to check instead."""
    text = reasons.person_next(reasons.LOCAL_PROBLEM, lang, address="myboard.local")
    assert "log" not in text.lower(), text
    assert "known_hosts" in text and ("state folder" in text or "Statusordner" in text)


def test_check_port_tells_a_refusal_of_this_computer(monkeypatch):
    def refused_here(address, timeout=None):
        raise PermissionError(errno.EPERM, "Operation not permitted")
    monkeypatch.setattr(socket, "create_connection", refused_here)
    assert reasons.check_port("192.0.2.10", 22) == reasons.LOCAL_PROBLEM


@pytest.mark.parametrize("where", ["key read", "login"])
def test_a_deploy_stopped_by_this_computer_says_so_and_blames_no_board(where, manager, board, package, monkeypatch,
                                                                       tmp_path):
    from orchestrator.runtime.device.trusted import TrustedDevices
    full = OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "state" / "known_hosts"))

    def fail(*a, **k):
        raise full
    monkeypatch.setattr(ssh_trust, "check_host_key" if where == "key read" else "connect", fail)
    noted = []
    monkeypatch.setattr(TrustedDevices, "note_reach_at", lambda self, *a: noted.append(a))
    result, events = run(manager, package)
    assert not result.success and result.code == reasons.LOCAL_PROBLEM
    assert "a problem on this computer, not on the board (No space left on device: known_hosts; local_problem)" \
        in result.reason
    assert str(tmp_path) not in result.reason, "the reason names the file, never its folder"
    assert noted == [], "the board is not recorded as down for Mojo"
    assert not [e for e in events if e.kind == "waiting"]


def test_a_problem_on_this_computer_during_the_wait_ends_it(manager, board, package, monkeypatch):
    board.drop_after = 600_000
    board.back_at = 10.0
    real_connect = ssh_trust.connect

    def connect(*a, **k):
        if board.logins:
            raise PermissionError(errno.EACCES, "Permission denied", KEY_FILE)
        return real_connect(*a, **k)
    monkeypatch.setattr(ssh_trust, "connect", connect)
    result, events = run(manager, package)
    assert not result.success and result.code == reasons.LOCAL_PROBLEM and board.starts == 0
    assert max(e.elapsed for e in events if e.kind == "waiting") < 60, "it does not wait out the 300 s"


def test_the_ai_is_told_it_is_this_computer(monkeypatch):
    from orchestrator.tools import tools_deploy
    error = tools_deploy.unreachable_error(SimpleNamespace(label="Shelf"), LOCAL_ERRORS[1])
    assert error.code == "needs_person" and error.blocker == "" and "(local_problem)" in str(error)
    assert "a problem on this computer" in str(error) and "known_hosts" not in str(error) and "/" not in str(error)
    assert error.next == reasons.AI_NEXT[reasons.LOCAL_PROBLEM]
    assert [s.id for s in error.steps] == ["mojo.oracle"], "the next step comes from the catalogue"


def test_the_person_steps_record_no_board_for_a_problem_on_this_computer(tmp_path):
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import TrustedDevices
    device_id = "d" * 16
    failure = person_steps.unreachable_error(LOCAL_ERRORS[0], HOST, PORT, state_dir=tmp_path, device_id=device_id,
                                             step="deploy")
    assert failure.reason == reasons.LOCAL_PROBLEM and failure.kind == "unreachable"
    assert TrustedDevices(tmp_path).last_deploys() == {}, "nothing is recorded with the board"
    person_steps.unreachable_error(socket.timeout("timed out"), HOST, PORT, state_dir=tmp_path, device_id=device_id,
                                   step="deploy")
    assert TrustedDevices(tmp_path).last_deploys()[device_id]["reach"]["code"] == reasons.HOST_DOWN, \
        "a board that does not answer still is"


def test_the_cli_shows_the_reason_and_this_computer_s_words(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import quiet_console
    printed = quiet_console(monkeypatch)
    with pytest.raises(SystemExit):
        cli_module._unreachable(None, HOST, PORT, LOCAL_ERRORS[1], state_dir=tmp_path)
    out = printed.getvalue()
    assert reasons.LOCAL_PROBLEM in out and "this computer" in out and "No space left on device" in out
    assert "switched on" not in out, "never the board's next step"


def test_the_gui_shows_the_reason_of_this_computer():
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    # the person's text of the local problem, and since c2-remote-callers (on purpose: the GUI names the failing
    # file, as the CLI prints the error) what failed on this computer, by the file's name only
    text = host_key.unreachable_text(HOST, PORT, LOCAL_ERRORS[0], "de")
    assert text.startswith(reasons.person_text(reasons.LOCAL_PROBLEM, "de", address=HOST, port=PORT))
    assert text == reasons.person_text(reasons.LOCAL_PROBLEM, "de", address=HOST, port=PORT, error=LOCAL_ERRORS[0])
    assert reasons.local_detail(LOCAL_ERRORS[0]) in text
