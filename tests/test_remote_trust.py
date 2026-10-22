"""The trust in a board's host key (consolidation stage C, c2-remote-trust; DECISIONS round 10 Q5).

orchestrator/remote/trust.py is the read-only host-key store (TrustStore: status, status_of, check,
read_presented_key); orchestrator/remote/pairing.py holds the person's writers (trust a key with its audit row in one
step, pair with a one-time password, replace a board's key or address for Q5, record a consent). No module an AI can
reach imports pairing.

The characterization tests at the top pin what every channel does today with a known, an unknown and a changed host
key, a key of another device and a fingerprint other than the one shown: the CLI (--trust-fingerprint, the refusal
without a terminal, "No" by default), the GUI (pinned in tests/test_deploy_parity.py TestHostKeyCheck: "No" by
default, a changed key recorded for Mojo), the deploy tool (host_key_unknown, step deploy), the device runtime steps
and the person's board steps (other_device), the reach rows each writes for Mojo, and the connections a GUI deploy
opens to the board's SSH port.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.runtime.device import reasons  # noqa: E402
from orchestrator.utils import ssh_trust  # noqa: E402

from test_board_deploy import HOST as BOARD_HOST, board, manager, package  # noqa: E402,F401
from test_deploy_tools import roomy_disk  # noqa: E402,F401 - a fitting disk for the packages of these tests
from test_deployment import FakeFramework, config  # noqa: E402,F401

HOST = "192.0.2.40"


def reach_rows(state: Path):
    from orchestrator.runtime.device.trusted import TrustedDevices
    return {device_id: row.get("reach") for device_id, row in TrustedDevices(state).last_deploys().items()}


def record(state: Path, key, host=HOST, port=22, user="radxa", label="Desk board"):
    """A board the person trusted: its key in known_hosts and its record."""
    from orchestrator.runtime.device.trusted import TrustedDevices
    ssh_trust.trust_host_key(state, host, port, key)
    return TrustedDevices(state).record(host, port, user, key, via="cli", paired=True, label=label)


@pytest.fixture
def keep_logger_levels():
    """`llm-cli -q` sets every existing logger to ERROR; later tests read WARNING logs."""
    import logging
    saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
    root = logging.getLogger().level
    yield
    for name in list(logging.Logger.manager.loggerDict):
        logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
    logging.getLogger().setLevel(root)


@pytest.fixture
def terminal(monkeypatch):
    """Makes the CliRunner's standard input a terminal (sys.stdin.isatty() is True), so the CLI asks. The CLI's shared
    console prints plain text to the runner's output meanwhile (a console made while it looks like a terminal would
    keep its colours) and is the earlier one again afterwards."""
    import click.testing
    from rich.console import Console
    from orchestrator import cli as cli_module

    def make(on=True):
        monkeypatch.setattr(cli_module.console, "_console", Console(color_system=None, width=500))
        monkeypatch.setattr(click.testing._NamedTextIOWrapper, "isatty", lambda self: on, raising=False)
    return make


# --- characterization: the CLI's trust on first use ----------------------------------------------------------------

@pytest.mark.usefixtures("keep_logger_levels")
class TestCliDeployRunFirstUse:
    """`llmcf deploy run` with a key the framework does not know yet: compared with --trust-fingerprint, else asked at
    a terminal with "No" as the default, else refused; a changed key always stops (recorded for Mojo)."""

    def run(self, tmp_path, monkeypatch, config, status, *args, answer=None):  # noqa: F811 - the fixture
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from orchestrator.Core.deployment_manager import DeploymentManager, DeployResult
        from test_cli_builds import quiet_console
        printed = quiet_console(monkeypatch)
        key = paramiko.ECDSAKey.generate()
        seen = {"trusted": [], "deploys": [], "key": key}
        dm = DeploymentManager(FakeFramework(config))
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: status)
        monkeypatch.setattr(ssh_trust, "trust_host_key", lambda state, host, port, k: seen["trusted"].append(
            (Path(state), host, port, ssh_trust.fingerprint(k))))
        monkeypatch.setattr(dm, "deploy_package", lambda *a, **k: seen["deploys"].append(a) or DeployResult(
            False, reason="stopped by the test"))
        package = tmp_path / "package.zip"
        package.write_bytes(b"zip")
        ctx = cli_module.FrameworkContext()
        ctx._initialized = True
        ctx.framework_manager = SimpleNamespace(config=config, resolve_path=lambda p: tmp_path / p)
        ctx.deployment_manager = dm
        result = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "run", str(package), "--host", HOST, "--user",
                                                     "radxa", *args], obj=ctx, input=answer)
        seen.update(code=result.exit_code, out=printed.getvalue() + result.output, folder=dm._state_dir(), dm=dm)
        return seen

    def test_a_known_key_asks_nothing(self, tmp_path, monkeypatch, config):  # noqa: F811
        seen = self.run(tmp_path, monkeypatch, config, "known")
        assert seen["trusted"] == [] and len(seen["deploys"]) == 1 and "New device" not in seen["out"]

    def test_without_a_terminal_an_unknown_key_is_refused(self, tmp_path, monkeypatch, config):  # noqa: F811
        seen = self.run(tmp_path, monkeypatch, config, "unknown")
        assert seen["code"] == 1 and seen["trusted"] == [] and seen["deploys"] == []
        fp = ssh_trust.fingerprint(seen["key"])
        assert f"New device {HOST}:22 - ecdsa-sha2-nistp256 {fp}" in seen["out"]
        assert "Device not trusted; nothing was deployed." in seen["out"]

    def test_a_fingerprint_given_must_match(self, tmp_path, monkeypatch, config):  # noqa: F811
        seen = self.run(tmp_path, monkeypatch, config, "unknown", "--trust-fingerprint", "SHA256:" + "A" * 43)
        assert seen["code"] == 1 and seen["trusted"] == [] and seen["deploys"] == []
        assert "The fingerprint does not match --trust-fingerprint." in seen["out"]

    def test_the_matching_fingerprint_trusts_the_key_in_the_deploy_s_folder(self, tmp_path, monkeypatch,
                                                                          config):  # noqa: F811
        import paramiko as real
        key = real.ECDSAKey.generate()
        monkeypatch.setattr(real.ECDSAKey, "generate", staticmethod(lambda *a, **k: key))
        seen = self.run(tmp_path, monkeypatch, config, "unknown", "--trust-fingerprint", ssh_trust.fingerprint(key))
        assert seen["trusted"] == [(seen["folder"], HOST, 22, ssh_trust.fingerprint(key))]
        assert len(seen["deploys"]) == 1

    def test_at_a_terminal_no_is_the_default(self, tmp_path, monkeypatch, config, terminal):  # noqa: F811
        terminal()
        seen = self.run(tmp_path, monkeypatch, config, "unknown", answer="\n")
        assert seen["code"] == 1 and seen["trusted"] == [] and seen["deploys"] == []
        assert "Trust it? [y/N]" in seen["out"] and "ssh-keygen -lf" in seen["out"]
        yes = self.run(tmp_path, monkeypatch, config, "unknown", answer="y\n\n")
        assert len(yes["trusted"]) == 1 and len(yes["deploys"]) == 1

    def test_a_changed_key_stops_and_is_recorded_for_mojo(self, tmp_path, monkeypatch, config):  # noqa: F811
        from orchestrator.runtime.profile import default_state_dir
        state = default_state_dir()
        board = record(state, paramiko.ECDSAKey.generate())
        seen = self.run(tmp_path, monkeypatch, config, "changed")
        assert seen["code"] == 1 and seen["trusted"] == [] and seen["deploys"] == []
        assert "has CHANGED" in seen["out"] and "(device_host_key_changed)" in seen["out"], seen["out"]
        row = reach_rows(state)[board.device_id]
        assert (row["step"], row["code"]) == ("deploy", reasons.HOST_KEY_CHANGED)


@pytest.mark.usefixtures("keep_logger_levels")
class TestCliScanFirstUse:
    """`llmcf scan --device` asks the same question (tests/test_hardware_probe.py pins --trust-fingerprint and the
    refusal without a terminal); here: "No" by default and the reach row of a changed key."""

    def fake(self, monkeypatch, status):
        seen = {"trusted": [], "logins": []}
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: _SCAN_KEY)
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: status)
        monkeypatch.setattr(ssh_trust, "trust_host_key", lambda state, host, port, k: seen["trusted"].append(
            (host, port)))
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: seen["logins"].append(a) or pytest.fail("login"))
        return seen

    def test_at_a_terminal_no_is_the_default(self, tmp_path, monkeypatch, terminal):
        from test_cli_builds import invoke_plain
        terminal()
        seen = self.fake(monkeypatch, "unknown")
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--device", HOST, "--user", "radxa", input="\n")
        assert result.code == 1 and seen["trusted"] == [] and seen["logins"] == []
        assert "Trust it? [y/N]" in result.out and "Device not trusted; nothing was probed." in result.out

    def test_a_changed_key_stops_and_is_recorded_for_mojo(self, tmp_path, monkeypatch):
        from orchestrator.runtime.profile import default_state_dir
        from test_cli_builds import invoke_plain
        state = default_state_dir()
        board = record(state, paramiko.ECDSAKey.generate())
        seen = self.fake(monkeypatch, "changed")
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--device", HOST, "--user", "radxa")
        assert result.code == 1 and "CHANGED" in result.out and seen["trusted"] == []
        row = reach_rows(state)[board.device_id]
        assert (row["step"], row["code"]) == ("probe", reasons.HOST_KEY_CHANGED)


_SCAN_KEY = paramiko.ECDSAKey.generate()


# --- characterization: the deploy tool (an AI) ------------------------------------------------------------------

class TestDeployToolKeyRead:
    """deploy_to_device reads the key the board presents now and refuses anything but the trusted one; nothing is
    written but the reach row of a changed key (step deploy)."""

    @pytest.fixture
    def ready(self, make_framework, monkeypatch):
        import test_deploy_tools as dt
        from orchestrator.tools.policy import ToolPolicy
        fm = make_framework()
        fm.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fm.tool_registry.list()]))
        device, key = dt.trust(fm, host=HOST)
        presented = {"key": key}
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: presented["key"])
        pkg = dt.package(fm, dt.build(fm))
        return SimpleNamespace(fm=fm, device=device, key=key, presented=presented, pkg=pkg, dt=dt)

    def call(self, ready):
        from orchestrator.tools.registry import ToolError
        with pytest.raises(ToolError) as e:
            ready.dt.call(ready.fm, "deploy_to_device", {"package_id": ready.pkg["package_id"],
                                                         "device_id": ready.device.device_id},
                          confirm=ready.dt.asking(True))
        return e.value

    def test_an_unknown_key_is_host_key_unknown_without_a_reach_row(self, ready, monkeypatch):
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "unknown")
        error = self.call(ready)
        assert error.blocker == "host_key_unknown" and error.field == "device_id"
        assert HOST not in str(error) and ready.device.label in str(error)
        assert reach_rows(Path(ready.fm.config.user_state_dir)).get(ready.device.device_id) is None

    @pytest.mark.parametrize("how", ["changed", "other key, known"])
    def test_another_key_is_host_key_changed_with_its_reach_row(self, ready, monkeypatch, how):
        if how == "changed":
            ready.presented["key"] = paramiko.ECDSAKey.generate()
        else:
            ready.presented["key"] = paramiko.ECDSAKey.generate()
            monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "known")
        error = self.call(ready)
        assert error.blocker == reasons.HOST_KEY_CHANGED and error.field == "device_id"
        assert HOST not in str(error) and "CHANGED" in str(error)
        row = reach_rows(Path(ready.fm.config.user_state_dir))[ready.device.device_id]
        assert (row["step"], row["code"]) == ("deploy", reasons.HOST_KEY_CHANGED)


# --- characterization: the device runtime steps and the person's board steps ------------------------------------

class TestDeviceSteps:

    @pytest.fixture
    def board(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        key = paramiko.ECDSAKey.generate()
        device = record(state, key)
        presented = {"key": key}
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: presented["key"])
        return SimpleNamespace(state=state, key=key, device=device, presented=presented)

    def test_service_read_key(self, board):
        from orchestrator.runtime.device import service
        assert service.read_key(board.device, board.state, "probe") == board.device.fingerprint
        board.presented["key"] = paramiko.ECDSAKey.generate()
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.read_key(board.device, board.state, "probe")
        assert e.value.reason == reasons.HOST_KEY_CHANGED and e.value.code == reasons.TOOL_CODES[
            reasons.HOST_KEY_CHANGED]
        row = reach_rows(board.state)[board.device.device_id]
        assert (row["step"], row["code"]) == ("probe", reasons.HOST_KEY_CHANGED)

    def test_service_refuses_a_key_other_than_the_record_s_or_the_one_shown(self, board, monkeypatch):
        from orchestrator.runtime.device import service
        board.presented["key"] = paramiko.ECDSAKey.generate()
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "known")
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.read_key(board.device, board.state)
        assert e.value.code == "needs_person" and "not the one the person trusted" in str(e.value)
        board.presented["key"] = board.key
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.connect(board.device, board.state, "radxa", "SHA256:" + "B" * 43, step="probe")
        assert e.value.code == "needs_person" and "another host key than the one shown" in str(e.value)

    def test_service_refuses_an_unknown_key(self, board, monkeypatch):
        from orchestrator.runtime.device import service
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "unknown")
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.read_key(board.device, board.state, "probe")
        assert e.value.code == "needs_person" and reach_rows(board.state).get(board.device.device_id) is None

    def test_person_steps_read_host_key(self, board):
        from orchestrator.runtime.device import person_steps
        key, status, fp = person_steps.read_host_key(board.state, HOST, 22)
        assert (key, status, fp) == (board.key, "known", board.device.fingerprint)
        board.presented["key"] = paramiko.ECDSAKey.generate()
        assert person_steps.read_host_key(board.state, HOST, 22)[1] == "changed"
        assert person_steps.read_host_key(board.state, "192.0.2.41", 22)[1] == "unknown"

    @pytest.mark.parametrize("case", ["changed", "unknown", "other_device"])
    def test_person_steps_connect_stored(self, board, monkeypatch, case):
        from orchestrator.runtime.device import person_steps
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: pytest.fail("logged in"))
        device_id = board.device.device_id
        if case == "changed":
            board.presented["key"] = paramiko.ECDSAKey.generate()
        elif case == "unknown":
            monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "unknown")
        else:
            device_id = "0" * 16
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.connect_stored(board.state, device_id, HOST, 22, "radxa", step="runtime_deploy")
        kind = {"changed": "host_key_changed", "unknown": "not_trusted", "other_device": "other_device"}[case]
        assert e.value.kind == kind
        row = reach_rows(board.state).get(board.device.device_id)
        if case == "changed":
            assert e.value.reason == reasons.HOST_KEY_CHANGED and "CHANGED" in str(e.value)
            assert (row["step"], row["code"]) == ("runtime_deploy", reasons.HOST_KEY_CHANGED)
        else:
            assert row is None

    def test_the_record_s_key_status_reads_only_known_hosts(self, board):
        from orchestrator.runtime.device.trusted import TrustedDevices
        store = TrustedDevices(board.state)
        assert store.key_status(board.device) == "known"
        other = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(board.state, HOST, 22, other)          # the same key type, another key
        assert store.key_status(board.device) == "changed"
        (board.state / "known_hosts").write_text("")
        assert store.key_status(board.device) == "unknown"
        (board.state / "known_hosts").write_text("not a known_hosts line at all\n\x00")
        assert store.key_status(board.device) in ("unknown", "changed")


# --- characterization: the connections one GUI deploy opens to the board's SSH port ------------------------------

def test_a_gui_deploy_opens_four_connections_to_the_board_s_ssh_port(qapp, manager, board, package, monkeypatch):
    """The Deployment tab's deploy on the fake board of tests/test_board_deploy.py: the key read before the question
    (gui/host_key.HostKeyCheck), the deploy's port check, the key read of its login and the login. Characterized with
    five connections; since c2-remote-trust (on purpose) the window hands the key of its question to the deploy, which
    must reach exactly that key (expect_fingerprint), and records the board with it instead of reading it a fifth time.
    A worker started without that key (no question before it) still reads it again."""
    from orchestrator.gui import host_key
    from orchestrator.gui.deployment_window import DeploymentWorker
    from orchestrator.runtime.device.trusted import TrustedDevices
    contacts, deploys = [], []

    def counted(name, function):
        def call(*args, **kwargs):
            contacts.append(name)
            return function(*args, **kwargs)
        return call

    monkeypatch.setattr(ssh_trust, "fetch_host_key", counted("key read", ssh_trust.fetch_host_key))
    monkeypatch.setattr(ssh_trust, "connect", counted("login", ssh_trust.connect))
    monkeypatch.setattr(reasons, "check_port", counted("port check", reasons.check_port))
    real_deploy = manager.deploy_package
    monkeypatch.setattr(manager, "deploy_package", lambda *a, **k: deploys.append(k) or real_deploy(*a, **k))
    framework = manager.framework
    framework._components["deployment_manager"] = manager
    state = Path(framework.config.user_state_dir)
    for hand_over in (True, False):
        contacts.clear()
        key, status = host_key.read_host_key(state, BOARD_HOST, 22)   # the question's read
        assert status == "known" and key is board.key
        worker = DeploymentWorker(framework, None, "", {}, BOARD_HOST, "radxa", None, port=22, package=package,
                                  key=key if hand_over else None)
        done = []
        worker.finished.connect(lambda ok, text: done.append((ok, text)))
        worker.run()
        assert done and done[0][0] is True, done
        assert worker.trusted is not None and TrustedDevices(state).get(worker.trusted.device_id) is not None
        assert worker.trusted.trusted_via == "gui" and worker.trusted.fingerprint == ssh_trust.fingerprint(board.key)
        expected = ["key read", "port check", "key read", "login"] + ([] if hand_over else ["key read"])
        assert contacts == expected, hand_over
        assert deploys[-1].get("expect_fingerprint") == (ssh_trust.fingerprint(key) if hand_over else None)


def test_the_host_key_check_keeps_the_key_of_a_check_that_went_on(qapp, monkeypatch):
    """HostKeyCheck.key: the key the deploy of the Deployment tab is bound to (None after "No" or a changed key)."""
    from PySide6.QtWidgets import QWidget
    from orchestrator.gui import host_key, plain
    from test_deploy_parity import wait_for
    key = paramiko.ECDSAKey.generate()
    status = {"value": "known"}
    monkeypatch.setattr(host_key, "read_host_key", lambda folder, host, port: (key, status["value"]))
    monkeypatch.setattr(host_key, "_note", lambda *a: None)
    monkeypatch.setattr(plain, "_exec", lambda box: plain.QMessageBox.No)
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir="unused"))
    parent = QWidget()
    check = host_key.HostKeyCheck(parent, framework)
    answers = []
    check.start(HOST, 22, answers.append)
    assert wait_for(qapp, lambda: answers) and answers == [True] and check.key is key
    status["value"] = "changed"
    check.start(HOST, 22, answers.append)
    assert wait_for(qapp, lambda: len(answers) == 2) and answers[1] is False and check.key is None


# --- remote/trust.py: the read-only store ----------------------------------------------------------------------------

class TestTrustStore:

    def test_the_old_names_are_the_store_s(self):
        from orchestrator.remote import trust
        from orchestrator.runtime import profile
        for name in ("HostKeyError", "UnknownHostKey", "HostKeyChanged", "fingerprint", "host_entry",
                     "known_hosts_path", "check_host_key"):
            assert getattr(ssh_trust, name) is getattr(trust, name), name
        assert ssh_trust._host_keys is trust.load_host_keys
        assert profile.device_id_for_host_key is trust.device_id_for
        assert profile._FINGERPRINT is trust.FINGERPRINT

    def test_check_reads_the_key_once_and_compares_what_was_expected(self, tmp_path, monkeypatch):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import TrustStore
        key, other = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
        reads = []
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: reads.append(
            (host, port)) or key)
        store = TrustStore(tmp_path)
        at = Endpoint(HOST, 2222)
        check = store.check(at)
        assert reads == [(HOST, 2222)] and check.status == "unknown" and check.key is key
        assert check.fingerprint == ssh_trust.fingerprint(key) and check.key_type == "ecdsa-sha2-nistp256"
        assert not check.fingerprint_differs and not check.other_device
        ssh_trust.trust_host_key(tmp_path, HOST, 2222, key)
        known = store.check(at, key, expect_fingerprint=ssh_trust.fingerprint(other), expect_device_id="0" * 16)
        assert reads == [(HOST, 2222)], "a key handed in is not read again"
        assert known.known and known.fingerprint_differs and known.other_device
        assert store.check(at, other).status == "changed"
        assert store.status(Endpoint(HOST, 22), key) == "unknown", "the trust is per address and port"
        assert list(store.keys_at(at)) == ["ecdsa-sha2-nistp256"] and store.keys_at(Endpoint(HOST, 22)) == {}
        assert store.keys_at(at)["ecdsa-sha2-nistp256"].asbytes() == key.asbytes()

    def test_status_follows_a_test_s_check_host_key(self, tmp_path, monkeypatch):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import TrustStore
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: "changed")
        assert TrustStore(tmp_path).status(Endpoint(HOST), paramiko.ECDSAKey.generate()) == "changed"

    def test_the_store_never_writes(self, tmp_path, monkeypatch):
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import TrustStore
        key = paramiko.ECDSAKey.generate()
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        state = tmp_path / "state"
        state.mkdir()
        store = TrustStore(state)
        store.check(Endpoint(HOST))
        store.status_of(SimpleNamespace(host=HOST, port=22, key_type=key.get_name(),
                                        fingerprint=ssh_trust.fingerprint(key)))
        assert list(state.iterdir()) == []
        import orchestrator.remote.trust as module
        source = Path(module.__file__).read_text(encoding="utf-8")
        for writer in ("trust_host_key", "save_host_keys", ".save(", "write_", "os.replace", "open("):
            assert writer not in source, writer


def _ed25519_key():
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    line = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
    kind, blob = line.split()
    return paramiko.PKey.from_type_string(kind, base64.b64decode(blob))


class TestParsePublicKey:

    @pytest.fixture(params=["ed25519", "ecdsa", "rsa"])
    def key(self, request):
        if request.param == "ed25519":
            return _ed25519_key()
        return paramiko.ECDSAKey.generate() if request.param == "ecdsa" else paramiko.RSAKey.generate(1024)

    def test_the_line_of_a_pub_file_known_hosts_or_keyscan(self, key):
        from orchestrator.remote.trust import parse_public_key
        line = f"{key.get_name()} {key.get_base64()}"
        for text in (line, line + " root@board", f"\n  {line}  \n", f"[{HOST}]:2222 {line}", f"{HOST} {line}",
                     f"# {HOST}:22 SSH-2.0-OpenSSH_9.2\n{HOST} {line}\n"):
            parsed = parse_public_key(text)
            assert parsed.get_name() == key.get_name() and parsed.asbytes() == key.asbytes(), text

    @pytest.mark.parametrize("text, kind", [
        ("", "empty"), ("\n  \n# only a comment\n", "empty"),
        ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI\nssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAJ", "several"),
        ("hello board", "not_a_key"), ("ssh-dss AAAAB3NzaC1kc3MAAACBAP", "not_a_key"),
        ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBroken", "not_a_key"),
        ("@cert-authority *.lan ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf0", "not_a_key"),
        ("ssh-ed25519 " + "A" * 40000, "not_a_key"),
    ], ids=["nothing", "comment only", "two lines", "words", "dss key", "broken data", "cert authority",
            "too long"])
    def test_anything_else_is_refused_with_its_kind(self, text, kind):
        from orchestrator.remote.trust import KeyTextError, parse_public_key
        with pytest.raises(KeyTextError) as e:
            parse_public_key(text)
        assert e.value.kind == kind

    def test_a_line_whose_data_is_of_another_type_is_refused(self):
        from orchestrator.remote.trust import KeyTextError, parse_public_key
        ecdsa = paramiko.ECDSAKey.generate()
        with pytest.raises(KeyTextError) as e:
            parse_public_key(f"ecdsa-sha2-nistp384 {ecdsa.get_base64()}")
        assert e.value.kind == "not_a_key"
        with pytest.raises(KeyTextError):
            parse_public_key(f"ssh-ed25519 {ecdsa.get_base64()}")


# --- remote/pairing.py: the person's writers ----------------------------------------------------------------------

class Audit:
    """An audit log that keeps its rows (or fails when told to)."""

    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, actor, tool, tier, decision, outcome, arguments=None, approved=None, detail="", extra=None):
        if self.fail:
            raise RuntimeError("audit log locked")
        self.rows.append({"actor": actor, "tool": tool, "outcome": outcome, "arguments": arguments,
                          "approved": approved, "detail": detail, "extra": extra})


def ai_reachable_closure(edges):
    """Every module an AI can reach through imports (every kind the layering test counts): the tool modules, the MCP
    server, the language-model layer, Ditto and the device runtime steps the tools call, with one path to each."""
    roots = [m for m in edges if m.startswith(("orchestrator.tools.", "orchestrator.llm.", "orchestrator.mcp."))
             or m in ("orchestrator.tools", "orchestrator.llm", "orchestrator.mcp", "orchestrator.mcp_server",
                      "orchestrator.Core.ditto_manager", "orchestrator.runtime.device.service",
                      "orchestrator.runtime.bench")]
    via, stack = {m: None for m in roots}, list(roots)
    while stack:
        module = stack.pop()
        for target in edges.get(module, ()):
            if target not in via:
                via[target] = module
                stack.append(target)
    return via


def path_to(via, module):
    chain = [module]
    while via.get(chain[-1]) is not None:
        chain.append(via[chain[-1]])
    return " <- ".join(chain)


PERSON_ONLY = ("orchestrator.remote.pairing", "orchestrator.runtime.device.person_steps",
               "orchestrator.runtime.scan_steps")


def test_no_module_an_ai_can_reach_imports_the_person_s_trust_writers():
    """Q5 and decision 2: trusting a key, pairing and replacing a board's key or address are the person's. Nothing an
    AI can reach (tools/tools_*, the MCP server, Ditto, runtime/device/service.py) imports remote/pairing.py or the
    person's board and scan steps, at module level, inside a function or by name."""
    import test_layering
    via = ai_reachable_closure(test_layering.build_graph().edges())
    assert "orchestrator.tools.tools_deploy" in via and "orchestrator.runtime.device.service" in via
    assert "orchestrator.remote.trust" in via, "the read-only store is what the AI side uses"
    for module in PERSON_ONLY:
        assert module not in via, path_to(via, module)


def test_the_import_guard_bites():
    edges = {"orchestrator.tools.tools_x": {"orchestrator.Core.helper"},
             "orchestrator.Core.helper": {"orchestrator.remote.pairing"}}
    via = ai_reachable_closure(edges)
    assert "orchestrator.remote.pairing" in via
    assert path_to(via, "orchestrator.remote.pairing") == ("orchestrator.remote.pairing <- orchestrator.Core.helper "
                                                          "<- orchestrator.tools.tools_x")


class TestTrustPresentedKey:

    def test_the_key_and_its_audit_row_are_written_in_one_step(self, tmp_path):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import TrustStore, device_id_for
        key, audit = paramiko.ECDSAKey.generate(), Audit()
        store = TrustStore(tmp_path / "state")
        pairing.trust_presented_key(store, Endpoint(HOST, 2222), key, via="cli", audit=audit, actor="cli")
        assert store.status(Endpoint(HOST, 2222), key) == "known"
        if sys.platform != "win32":
            assert store.path.stat().st_mode & 0o077 == 0
        (row,) = audit.rows
        fp = ssh_trust.fingerprint(key)
        assert (row["actor"], row["tool"], row["outcome"], row["approved"]) == ("cli", "host_key_trusted", "ok", True)
        assert row["arguments"] == {"device_id": device_id_for(fp), "fingerprint": fp,
                                    "key_type": "ecdsa-sha2-nistp256"}
        assert row["extra"] == {"confirm_channel": "framework-cli", "answer": "accepted"}
        assert HOST not in json.dumps(row), "an audit row names the board by its key, never by its address (D0)"

    def test_the_audit_log_is_opened_only_to_write_and_never_fails_the_trust(self, tmp_path):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import TrustStore
        store = TrustStore(tmp_path / "state")
        opened = []
        for audit in (None, Audit(fail=True), lambda: opened.append("opened") or None,
                      lambda: (_ for _ in ()).throw(OSError("no keyring"))):
            key = paramiko.ECDSAKey.generate()
            pairing.trust_presented_key(store, Endpoint(HOST), key, via="gui", audit=audit)
            assert store.status(Endpoint(HOST), key) == "known"
        assert opened == ["opened"]


class TestFirstUse:

    def decide(self, tmp_path, status, **kwargs):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import KeyCheck, TrustStore
        key = paramiko.ECDSAKey.generate()
        self.calls = getattr(self, "calls", 0) + 1
        store, audit = TrustStore(tmp_path / f"state{self.calls}"), Audit()   # a fresh known_hosts each time
        check = KeyCheck.of(Endpoint(HOST), key, status)
        outcome = pairing.first_use(store, check, via="cli", audit=audit, **kwargs)
        return outcome, store.status(Endpoint(HOST), key), audit.rows

    def test_known_goes_on_and_changed_is_refused_without_a_question(self, tmp_path):
        asked = []
        for status, verdict in (("known", "known"), ("changed", "changed")):
            outcome, _stored, rows = self.decide(tmp_path, status, ask=asked.append)
            assert outcome.verdict == verdict and outcome.trusted is (status == "known") and rows == []
        assert asked == []

    def test_an_unknown_key_needs_the_fingerprint_or_the_person_s_yes(self, tmp_path):
        outcome, stored, rows = self.decide(tmp_path, "unknown", ask=None)
        assert (outcome.verdict, stored, rows) == ("declined", "unknown", [])
        outcome, stored, rows = self.decide(tmp_path, "unknown", ask=lambda check: False)
        assert (outcome.verdict, stored, rows) == ("declined", "unknown", [])
        outcome, stored, rows = self.decide(tmp_path, "unknown", ask=lambda check: True)
        assert (outcome.verdict, stored, len(rows)) == ("trusted", "known", 1) and outcome.trusted
        outcome, stored, rows = self.decide(tmp_path, "unknown", ask=lambda c: pytest.fail("asked"),
                                            preset_fingerprint="SHA256:" + "C" * 43)
        assert (outcome.verdict, stored, rows) == ("mismatch", "unknown", [])

    def test_the_matching_preset_fingerprint_trusts_without_a_question(self, tmp_path, monkeypatch):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.trust import KeyCheck, TrustStore
        key = paramiko.ECDSAKey.generate()
        store = TrustStore(tmp_path / "state")
        check = KeyCheck.of(Endpoint(HOST), key, "unknown")
        outcome = pairing.first_use(store, check, ask=lambda c: pytest.fail("asked"),
                                    preset_fingerprint=check.fingerprint, via="cli")
        assert outcome.verdict == "trusted" and store.status(Endpoint(HOST), key) == "known"


def test_pair_installs_the_framework_s_own_key_line(tmp_path, monkeypatch):
    from orchestrator.remote import pairing
    seen = []
    monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line: seen.append((client, line)))
    pairing.pair("session", tmp_path / "state")
    ((client, line),) = seen
    assert client == "session" and line == ssh_trust.client_key(tmp_path / "state")[1]
    assert ssh_trust.PUBLIC_LINE.fullmatch(line)


def test_the_consent_writer_is_the_one_of_pairing():
    from orchestrator.remote import pairing
    from orchestrator.runtime.device import person_steps
    assert person_steps.record_consent is pairing.record_consent
    with pytest.raises(RuntimeError):
        pairing.record_consent(None, "device_runtime_systemd", {}, "detail", channel="framework-gui")
    audit = Audit()
    pairing.record_consent(audit, "device_runtime_systemd", {"device": "0" * 16}, "detail", channel="framework-gui")
    assert audit.rows[0]["actor"] == "user" and audit.rows[0]["extra"]["confirm_channel"] == "framework-gui"


class TestReplaceKey:
    """Q5: the person's change of a trusted board's address and host key in the framework's known_hosts."""

    @pytest.fixture
    def trusted_board(self, tmp_path):
        from orchestrator.remote.trust import TrustStore
        state = tmp_path / "state"
        key = paramiko.ECDSAKey.generate()
        device = record(state, key)
        neighbour = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "192.0.2.99", 22, neighbour)   # another board: never touched
        return SimpleNamespace(state=state, key=key, device=device, store=TrustStore(state), neighbour=neighbour)

    def lines(self, state):
        return sorted((state / "known_hosts").read_text().splitlines())

    def test_a_new_address_moves_the_key(self, trusted_board):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        b, audit = trusted_board, Audit()
        plan = pairing.plan_replacement(b.store, b.device, Endpoint("myboard.local", 2222))
        assert plan.address_changed and not plan.key_changed and plan.displaced == ""
        assert plan.old_fingerprint == plan.new_fingerprint == b.device.fingerprint
        pairing.replace_key(b.store, plan, via="gui", audit=audit, actor="gui")
        assert b.store.status(Endpoint("myboard.local", 2222), b.key) == "known"
        assert b.store.status(Endpoint(HOST), b.key) == "unknown", "the old address no longer maps to the board"
        assert b.store.status(Endpoint("192.0.2.99"), b.neighbour) == "known"
        (row,) = audit.rows
        assert row["tool"] == "host_key_replaced" and row["arguments"]["address_changed"] is True
        assert row["arguments"]["old_device_id"] == row["arguments"]["new_device_id"] == b.device.device_id
        assert HOST not in json.dumps(row) and "myboard.local" not in json.dumps(row)

    def test_a_new_key_replaces_the_old_one_at_the_same_address(self, trusted_board):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        b, audit = trusted_board, Audit()
        new = paramiko.ECDSAKey.generate()
        plan = pairing.plan_replacement(b.store, b.device, None, new)
        assert plan.key_changed and not plan.address_changed and plan.displaced == ""
        assert (plan.old_fingerprint, plan.new_fingerprint) == (b.device.fingerprint, ssh_trust.fingerprint(new))
        assert plan.new_device_id != plan.old_device_id
        pairing.replace_key(b.store, plan, via="cli", audit=audit)
        assert b.store.status(Endpoint(HOST), new) == "known" and b.store.status(Endpoint(HOST), b.key) == "changed"
        assert audit.rows[0]["arguments"]["new_fingerprint"] == ssh_trust.fingerprint(new)

    def test_a_key_of_another_type_leaves_no_old_key_behind(self, trusted_board):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        b = trusted_board
        new = _ed25519_key()
        pairing.replace_key(b.store, pairing.plan_replacement(b.store, b.device, None, new), via="cli",
                            audit=Audit())
        assert b.store.keys_at(Endpoint(HOST)).keys() == {"ssh-ed25519"}

    def test_a_key_the_new_address_held_is_named_and_replaced(self, trusted_board):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        b = trusted_board
        plan = pairing.plan_replacement(b.store, b.device, Endpoint("192.0.2.99"))
        assert plan.displaced == ssh_trust.fingerprint(b.neighbour)
        pairing.replace_key(b.store, plan, via="gui", audit=Audit())
        assert b.store.status(Endpoint("192.0.2.99"), b.key) == "known"

    @pytest.mark.parametrize("change, kind", [("nothing", "unchanged"), ("bad address", "address"),
                                              ("gone", "no_key")])
    def test_what_cannot_be_planned(self, trusted_board, change, kind):
        from orchestrator.remote import pairing
        from orchestrator.remote.address import Endpoint
        b = trusted_board
        target = {"nothing": None, "bad address": Endpoint("-oProxyCommand=x"), "gone": None}[change]
        if change == "gone":
            (b.state / "known_hosts").write_text("")
            target = Endpoint("192.0.2.98")
        with pytest.raises(pairing.KeyReplaceError) as e:
            pairing.plan_replacement(b.store, b.device, target)
        assert e.value.kind == kind

    @pytest.mark.parametrize("audit", [None, Audit(fail=True)], ids=["no audit log", "audit log fails"])
    def test_without_its_audit_row_nothing_changes(self, trusted_board, audit):
        from orchestrator.remote import pairing
        b = trusted_board
        before = self.lines(b.state)
        plan = pairing.plan_replacement(b.store, b.device, None, paramiko.ECDSAKey.generate())
        with pytest.raises(pairing.KeyReplaceError) as e:
            pairing.replace_key(b.store, plan, via="gui", audit=audit)
        assert e.value.kind == "audit" and self.lines(b.state) == before

    def test_a_known_hosts_changed_since_the_plan_is_refused(self, trusted_board):
        from orchestrator.remote import pairing
        b, audit = trusted_board, Audit()
        plan = pairing.plan_replacement(b.store, b.device, None, paramiko.ECDSAKey.generate())
        ssh_trust.trust_host_key(b.state, HOST, 22, paramiko.ECDSAKey.generate())   # another writer meanwhile
        before = self.lines(b.state)
        with pytest.raises(pairing.KeyReplaceError) as e:
            pairing.replace_key(b.store, plan, via="gui", audit=audit)
        assert e.value.kind == "stale" and self.lines(b.state) == before and audit.rows == []


# --- Q5: the person's change of a board's address and key (person_steps, CLI) --------------------------------------

class TestBoardChange:

    @pytest.fixture
    def b(self, tmp_path, monkeypatch):
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.profile import ProfileStore, parse_profile
        from test_hardware_probe import FIXTURES
        state = tmp_path / "state"
        key = paramiko.ECDSAKey.generate()
        device = record(state, key, label="Desk board", user="radxa")
        profiles = ProfileStore(state)
        profiles.save_device(device.device_id, parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text()),
                             label="Desk board", address=HOST)
        logins = []
        monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: logins.append(
            (host, port, user, password)) or SimpleNamespace(close=lambda: None))
        return SimpleNamespace(state=state, key=key, device=device, store=TrustedDevices(state), profiles=profiles,
                               logins=logins, audit=Audit())

    def test_a_new_address_moves_the_record_and_the_probe_s_address(self, b):
        from orchestrator.runtime.device import person_steps
        change = person_steps.plan_board_change(b.state, b.device.device_id, host="myboard.local", port=2222)
        lines = person_steps.change_lines(change)
        assert f"Address now: {HOST}:22" in lines and "Address after the change: myboard.local:2222" in lines
        assert not any("device ID becomes" in line for line in lines)
        moved, paired = person_steps.change_board(b.state, change, via="cli", audit=b.audit, actor="cli")
        assert (moved.device_id, moved.host, moved.port, moved.user, moved.label) == (
            b.device.device_id, "myboard.local", 2222, "radxa", "Desk board")
        assert paired is True and moved.paired and moved.trusted_via == "cli" and b.logins == []
        assert b.store.key_status(moved) == "known" and b.store.list() == [moved]
        assert b.profiles.device_info(b.device.device_id)["address"] == "myboard.local:2222"
        assert [r["tool"] for r in b.audit.rows] == ["host_key_replaced"]

    @pytest.mark.parametrize("logs_in", [True, False])
    def test_a_new_key_moves_the_record_to_the_new_device_id(self, b, monkeypatch, logs_in):
        from orchestrator.runtime.device import person_steps
        from orchestrator.runtime.profile import device_id_for_host_key
        if not logs_in:
            monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: (_ for _ in ()).throw(EOFError("closed")))
        new = paramiko.ECDSAKey.generate()
        change = person_steps.plan_board_change(b.state, b.device.device_id,
                                                key_text=f"{HOST} {new.get_name()} {new.get_base64()}")
        new_id = device_id_for_host_key(ssh_trust.fingerprint(new))
        lines = person_steps.change_lines(change)
        assert f"Host key now: ecdsa-sha2-nistp256 {b.device.fingerprint}" in lines
        assert f"Host key after the change: ecdsa-sha2-nistp256 {ssh_trust.fingerprint(new)}" in lines
        assert any(f"device ID becomes {new_id}" in line for line in lines)
        moved, paired = person_steps.change_board(b.state, change, via="gui", audit=b.audit, actor="gui")
        assert moved.device_id == new_id and (moved.label, moved.user, moved.host) == ("Desk board", "radxa", HOST)
        assert paired is logs_in and moved.paired is logs_in
        assert b.store.get(b.device.device_id) is None and b.store.list() == [moved]
        assert b.store.key_status(moved) == "known"
        assert b.profiles.list_devices() == [b.device.device_id], "the stored probe stays under its old ID"
        assert b.logins in ([(HOST, 22, "radxa", None)], [])

    def test_the_key_read_from_the_board_is_read_at_the_new_address(self, b, monkeypatch):
        from orchestrator.runtime.device import person_steps
        new = paramiko.ECDSAKey.generate()
        reads = []
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: reads.append(
            (host, port)) or new)
        change = person_steps.plan_board_change(b.state, b.device.device_id, host="192.0.2.41", read_key=True)
        assert reads == [("192.0.2.41", 22)] and change.plan.new_fingerprint == ssh_trust.fingerprint(new)
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: (_ for _ in ()).throw(
            ConnectionRefusedError(111, "refused")))
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.plan_board_change(b.state, b.device.device_id, read_key=True)
        assert e.value.kind == "unreachable" and e.value.reason == reasons.PORT_CLOSED

    @pytest.mark.parametrize("kwargs, kind, detail", [
        (dict(device_id="0" * 16), "no_board", None),
        (dict(host="-oProxyCommand=x"), "address", None),
        (dict(key_text="hello"), "key_text", "not_a_key"),
        (dict(key_text="a b\nc d"), "key_text", "several"),
        (dict(port=22), "unchanged", None),
    ])
    def test_what_is_refused_before_anything_changes(self, b, kwargs, kind, detail):
        from orchestrator.runtime.device import person_steps
        device_id = kwargs.pop("device_id", b.device.device_id)
        before = (b.state / "known_hosts").read_bytes(), (b.state / "devices.json").read_bytes()
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.plan_board_change(b.state, device_id, **kwargs)
        assert e.value.kind == kind and e.value.facts.get("detail") == detail
        assert ((b.state / "known_hosts").read_bytes(), (b.state / "devices.json").read_bytes()) == before

    def test_without_an_audit_log_nothing_changes(self, b):
        from orchestrator.runtime.device import person_steps
        change = person_steps.plan_board_change(b.state, b.device.device_id, host="192.0.2.41")
        before = (b.state / "known_hosts").read_bytes(), (b.state / "devices.json").read_bytes()
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.change_board(b.state, change, via="cli", audit=None, actor="cli")
        assert e.value.kind == "audit"
        assert ((b.state / "known_hosts").read_bytes(), (b.state / "devices.json").read_bytes()) == before


@pytest.mark.usefixtures("keep_logger_levels")
class TestCliDevicesEdit:
    """`llmcf deploy devices edit DEVICE --host/--port/--key/--read-key`: needs a terminal, shows the old and the new
    address and fingerprint, asks with "No" as the default and writes the change to the audit log."""

    @pytest.fixture
    def b(self, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        from orchestrator.runtime.profile import default_state_dir
        state = default_state_dir()
        key = paramiko.ECDSAKey.generate()
        device = record(state, key, label="Desk board")
        audit = Audit()
        monkeypatch.setattr(cli_module, "_device_audit", lambda ctx, folder: audit)
        monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
        return SimpleNamespace(state=state, key=key, device=device, audit=audit)

    def edit(self, *args, answer=None):
        from test_cli_builds import invoke_plain
        return invoke_plain("deploy", "devices", "edit", *args, input=answer)

    def test_without_a_terminal_nothing_is_changed(self, b):
        before = (b.state / "known_hosts").read_bytes()
        result = self.edit(b.device.device_id, "--host", "192.0.2.41")
        assert result.code == 1 and "needs a terminal" in result.out
        assert (b.state / "known_hosts").read_bytes() == before and b.audit.rows == []

    @pytest.mark.parametrize("args", [[], ["--key", "x", "--read-key"]])
    def test_what_to_change_is_named_once(self, b, args):
        assert self.edit(b.device.device_id, *args).code == 2

    def test_no_is_the_default_and_yes_changes_and_audits(self, b, terminal):
        from orchestrator.runtime.device.trusted import TrustedDevices
        terminal()
        new = paramiko.ECDSAKey.generate()
        line = f"{new.get_name()} {new.get_base64()} root@board"
        before = (b.state / "known_hosts").read_bytes()
        result = self.edit("Desk board", "--key", line, answer="\n")
        assert result.code == 0 and "Nothing was changed." in result.out and "[y/N]" in result.out
        assert f"Host key now: ecdsa-sha2-nistp256 {b.device.fingerprint}" in result.out
        assert f"Host key after the change: ecdsa-sha2-nistp256 {ssh_trust.fingerprint(new)}" in result.out
        assert (b.state / "known_hosts").read_bytes() == before and b.audit.rows == []
        result = self.edit(b.device.device_id, "--key", line, "--host", "myboard.local", answer="y\n")
        assert result.code == 0, result.out
        (moved,) = TrustedDevices(b.state).list()
        assert (moved.host, moved.fingerprint, moved.label) == ("myboard.local", ssh_trust.fingerprint(new),
                                                                "Desk board")
        assert f"Board {moved.device_id} (Desk board): myboard.local:22, host key" in result.out
        assert [row["tool"] for row in b.audit.rows] == ["host_key_replaced"]

    def test_an_unknown_board_is_named(self, b, terminal):
        terminal()
        result = self.edit("Garage", "--host", "192.0.2.41")
        assert result.code == 1 and "No trusted board Garage" in result.out


# --- scan_device checks the SSH user like every other board path (default of the stage-B reviews) ------------------

@pytest.mark.parametrize("user", ["-oProxyCommand=x", "radxa\nroot", "ſudo", "bad user", ""])
def test_scan_device_refuses_a_user_that_is_no_account_name_before_any_contact(tmp_path, monkeypatch, user):
    from orchestrator.runtime import scan_steps
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: pytest.fail("logged in"))
    with pytest.raises(scan_steps.DeviceScanError) as e:
        scan_steps.scan_device(tmp_path / "state", HOST, 22, user, None)
    assert e.value.kind == "user" and str(e.value) == scan_steps.USER_HINT
    assert not (tmp_path / "state").exists(), "nothing was written"


def test_scan_device_takes_root_as_every_board_path_does(tmp_path, monkeypatch):
    from orchestrator.runtime import scan_steps
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError(111, "x")))
    with pytest.raises(scan_steps.DeviceScanError) as e:
        scan_steps.scan_device(tmp_path / "state", HOST, 22, "root", None)
    assert e.value.kind == "login"


@pytest.mark.usefixtures("keep_logger_levels")
def test_the_cli_refuses_such_a_user_with_its_reason(monkeypatch):
    from test_cli_builds import invoke_plain
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda *a, **k: pytest.fail("the board was contacted"))
    result = invoke_plain("scan", "--device", HOST, "--user", "radxaſ")
    assert result.code == 2 and "account name on the board" in result.out


# --- review (remote-trust-v): regression tests -----------------------------------------------------------------------

@pytest.mark.parametrize("device_id", ["", None], ids=["empty", "none"])
def test_connect_stored_without_a_device_id_is_refused_as_another_device(tmp_path, monkeypatch, device_id):
    """Before c2-remote-trust, connect_stored compared the presented key's device ID with the one it was given, so a
    missing ID never matched (other_device). KeyCheck.other_device took "" for "nothing expected" and let such a call
    log in; an expectation that was given is now always checked."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import KeyCheck
    from orchestrator.runtime.device import person_steps
    state = tmp_path / "state"
    key = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, HOST, 22, key)
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: pytest.fail("logged in"))
    with pytest.raises(person_steps.BoardStepError) as e:
        person_steps.connect_stored(state, device_id, HOST, 22, "radxa")
    assert e.value.kind == "other_device"
    assert KeyCheck.of(Endpoint(HOST), key, "known", expect_device_id="").other_device
    assert not KeyCheck.of(Endpoint(HOST), key, "known").other_device, "None: nothing is expected"


@pytest.mark.parametrize("prefix", ['command="/bin/sh"', 'no-pty,command="/bin/sh -c id",from="192.0.2.1"',
                                    'environment="LD_PRELOAD=/tmp/x.so"', "$(reboot)", "`id`", "host;reboot"],
                         ids=["command", "several options", "environment", "subshell", "backticks", "semicolon"])
def test_a_pasted_line_with_options_or_commands_is_refused(prefix):
    """A host key is pasted, never typed (Q5): a line with authorized_keys options or shell words in front of the key
    is not a public key, known_hosts or ssh-keyscan line, and is refused instead of silently taking the key from it."""
    from orchestrator.remote.trust import KeyTextError, parse_public_key
    key = paramiko.ECDSAKey.generate()
    with pytest.raises(KeyTextError) as e:
        parse_public_key(f"{prefix} {key.get_name()} {key.get_base64()}")
    assert e.value.kind == "not_a_key"


def test_a_pasted_line_with_a_second_key_is_refused():
    """One key only: a second key on the same line (where a comment would be) is refused like a second line."""
    from orchestrator.remote.trust import KeyTextError, parse_public_key
    first, second = _ed25519_key(), _ed25519_key()
    with pytest.raises(KeyTextError) as e:
        parse_public_key(f"{first.get_name()} {first.get_base64()} {second.get_name()} {second.get_base64()}")
    assert e.value.kind == "several"


@pytest.mark.parametrize("hosts", ["192.0.2.40,myboard.local", "[192.0.2.40]:2222,[myboard.local]:2222",
                                   "fe80::1%eth0", "*.lan,!192.0.2.9", "|1|c2FsdHNhbHRzYWx0c2FsdA==|aGFzaGhhc2g="],
                         ids=["list", "ports", "zone", "patterns", "hashed"])
def test_the_host_fields_of_known_hosts_stay_accepted(hosts):
    from orchestrator.remote.trust import parse_public_key
    key = paramiko.ECDSAKey.generate()
    assert parse_public_key(f"{hosts} {key.get_name()} {key.get_base64()}").asbytes() == key.asbytes()
