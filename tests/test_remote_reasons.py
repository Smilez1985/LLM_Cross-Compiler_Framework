"""Why a board cannot be reached (N6) and what each contact records for Mojo (W1), pinned for consolidation chain
c2-remote-base: the reason codes move into orchestrator/remote/reasons.py, the 'classify, else device_host_down for an
OSError' copies become one reach_code(), and the six note_reach helpers become one recorder. Every copy must keep
giving the same code and writing the same row.
"""

import errno
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.runtime.device import reasons  # noqa: E402
from orchestrator.utils import ssh_trust  # noqa: E402

from test_board_deploy import TARGET, board, manager, package, run  # noqa: E402,F401 - the fake board of the N5 tests
from test_deployment import config  # noqa: E402,F401 - the manager fixture's configuration

HOST, PORT = "192.0.2.10", 2222


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
