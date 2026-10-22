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
    """Makes the CliRunner's standard input a terminal (sys.stdin.isatty() is True), so the CLI asks."""
    import click.testing

    def make(on=True):
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

def test_a_gui_deploy_opens_five_connections_to_the_board_s_ssh_port(qapp, manager, board, package, monkeypatch):
    """The Deployment tab's deploy on the fake board of tests/test_board_deploy.py: the key read before the question
    (gui/host_key.HostKeyCheck), the deploy's port check, the key read of its login and the login, and the key read
    again for the board's record afterwards."""
    from orchestrator.gui import host_key
    from orchestrator.gui.deployment_window import DeploymentWorker
    from orchestrator.runtime.device.trusted import TrustedDevices
    contacts = []

    def counted(name, function):
        def call(*args, **kwargs):
            contacts.append(name)
            return function(*args, **kwargs)
        return call

    monkeypatch.setattr(ssh_trust, "fetch_host_key", counted("key read", ssh_trust.fetch_host_key))
    monkeypatch.setattr(ssh_trust, "connect", counted("login", ssh_trust.connect))
    monkeypatch.setattr(reasons, "check_port", counted("port check", reasons.check_port))
    framework = manager.framework
    framework._components["deployment_manager"] = manager
    state = Path(framework.config.user_state_dir)
    key, status = host_key.read_host_key(state, BOARD_HOST, 22)
    assert status == "known" and key is board.key
    worker = DeploymentWorker(framework, None, "", {}, BOARD_HOST, "radxa", None, port=22, package=package)
    done = []
    worker.finished.connect(lambda ok, text: done.append((ok, text)))
    worker.run()
    assert done and done[0][0] is True, done
    assert worker.trusted is not None and TrustedDevices(state).get(worker.trusted.device_id) is not None
    assert worker.trusted.trusted_via == "gui" and worker.trusted.fingerprint == ssh_trust.fingerprint(board.key)
    assert contacts == ["key read", "port check", "key read", "login", "key read"]
