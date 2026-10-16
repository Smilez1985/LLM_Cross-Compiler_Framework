"""Mojo Oracle's lines for the boards the person trusted (eighth round N6, next-fixes 130) and for Ditto's image in
Docker mode (DECISIONS round 7 D).

A default Mojo Oracle run makes no network contact with any board (W1 of 2026-10-16): it reports what was recorded
(the host key in the framework's known_hosts, the last deploy, verify, benchmark or probe that could not reach a
board) and offers to check a board such a fact points to. Only a board named on request gets one connection to its
SSH port. Mojo never logs in, and the device account is never named. For Ditto's Docker mode it asks Docker about the recorded image: never built,
pulled or removed by Mojo.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Severity
from orchestrator.Core.mojo.checks import devices as devices_check
from orchestrator.Core.mojo.checks import runtime as runtime_check
from orchestrator.Core.mojo.offers import offer_for
from orchestrator.runtime import container_server as cs

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def state(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    return folder


def board(state, host, port=22, label=None, trusted_key=True, key=None):
    """A board the person trusted: its record and (trusted_key) its host key in the framework's known_hosts."""
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    key = key or paramiko.ECDSAKey.generate()
    if trusted_key:
        ssh_trust.trust_host_key(state, host, port, key)
    return TrustedDevices(state).record(host, port, "radxa", key, via="cli", paired=True, label=label)


def framework_at(state):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state))


def record(state, device, step, code, at=None):
    """A recorded contact with the board (TrustedDevices.note_reach), at a fixed time when `at` is given."""
    from orchestrator.runtime.device.trusted import TrustedDevices
    store = TrustedDevices(state)
    store.note_reach(device.device_id, step, code)
    if at is not None:
        rows = json.loads(store.deploys_path.read_text(encoding="utf-8"))
        rows[device.device_id]["reach"]["at"] = at
        store.deploys_path.write_text(json.dumps(rows), encoding="utf-8")


@pytest.fixture
def no_network(monkeypatch):
    """Every network connection (IPv4 or IPv6, also the board check's own) is recorded and refused: a check that
    crashes is a Mojo finding, so the test reads the list instead of relying on the exception."""
    import socket
    from orchestrator.runtime.device import reasons
    attempts = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refuse(what):
        attempts.append(what)
        raise AssertionError(f"network contact: {what}")

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            refuse(address)
        return real_connect_ex(self, address)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", lambda address, *a, **k: refuse(address))
    monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=3.0: refuse((host, port)))
    return attempts


def test_a_default_oracle_run_opens_no_socket(booted_framework, no_network):
    """W1: the default Mojo Oracle run makes no network contact with any board, also when recorded facts point to
    problems with them; it reports those facts and offers the check."""
    from orchestrator.Core.mojo import Mojo
    from orchestrator.Core.mojo.runner import DEFAULT_CHECKS
    from orchestrator.runtime.device import reasons
    state = Path(booted_framework.config.user_state_dir)
    off = board(state, "192.0.2.11", label="Garage")
    board(state, "192.0.2.10", label="Kitchen")
    record(state, off, "deploy", reasons.HOST_DOWN)
    report = Mojo(booted_framework).run()
    assert no_network == [], "no connection in a default run"
    assert devices_check.check_devices in DEFAULT_CHECKS
    found = {f.code: f for f in report.findings if f.subsystem == "devices"}
    assert "devices.check_crashed" not in found
    garage = found[f"devices.{off.device_id}"]
    assert garage.board_check == off.device_id and garage.board_name == "Garage"
    assert "Mojo did not contact the board" in garage.message and "the last deploy" in garage.message
    assert found["devices.recorded"].severity == Severity.OK and "1 trusted board" in found["devices.recorded"].message


def test_recorded_facts_are_reported_with_the_check_offered_without_the_network(state, no_network):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device import reasons
    from orchestrator.utils import ssh_trust
    off = board(state, "192.0.2.11", label="Garage")
    locked = board(state, "192.0.2.12", port=2222, label="Desk")
    ssh_trust.trust_host_key(state, "192.0.2.20", 22, paramiko.ECDSAKey.generate())
    swapped = board(state, "192.0.2.20", label="Reinstalled", trusted_key=False)   # known_hosts holds another key
    gone = board(state, "192.0.2.21", label="Removed key", trusted_key=False)
    quiet = board(state, "192.0.2.13", label="Kitchen")
    record(state, off, "deploy", reasons.HOST_DOWN)
    record(state, locked, "benchmark", reasons.AUTH_FAILED)
    found = {f.code: f for f in devices_check.check_devices(framework_at(state))}
    assert no_network == []
    down = found[f"devices.{off.device_id}"]
    assert down.severity == Severity.INFO and "the last deploy (" in down.message
    assert "failed with device_host_down" in down.message and down.where == "192.0.2.11"
    assert "switched on" in down.fix_hint and down.board_check == off.device_id
    login = found[f"devices.{locked.device_id}"]
    assert login.severity == Severity.WARN and "the last benchmark (" in login.message
    assert "device_auth_failed" in login.message and login.cannot == "person" and login.board_name == "Desk"
    assert offer_for(framework_at(state), login).verdict == "person", "only the person enters a password"
    changed = found[f"devices.{swapped.device_id}"]
    assert changed.severity == Severity.WARN and "(device_host_key_changed)" in changed.message
    assert "known_hosts" in changed.message and changed.board_check == swapped.device_id
    missing = found[f"devices.{gone.device_id}"]
    assert missing.severity == Severity.WARN and "known_hosts" in missing.message and not missing.board_check, \
        "a key the person removed is trusted again, not checked"
    assert f"devices.{quiet.device_id}" not in found and "1 trusted board" in found["devices.recorded"].message
    for finding in found.values():
        assert "radxa" not in finding.message + finding.fix_hint + finding.where, "the device account never shows"


def test_a_contact_that_reaches_the_board_ends_its_record_and_a_newer_trust_outdates_it(state):
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device.trusted import TrustedDevices
    off = board(state, "192.0.2.11", label="Garage")
    store = TrustedDevices(state)
    record(state, off, "verify", reasons.PORT_CLOSED)
    assert store.reach_problem(off)["code"] == reasons.PORT_CLOSED
    store.note_deploy(off.device_id, False, "pkg.zip")
    assert store.reach_problem(off)["step"] == "verify", "a deploy's bookkeeping keeps the record"
    store.note_reach(off.device_id, "deploy", "")
    assert store.reach_problem(off) is None, "reached and logged in: the problem is over"
    record(state, off, "deploy", reasons.HOST_DOWN, at="2020-01-01T00:00:00Z")
    assert store.reach_problem(off) is None, "the person trusted the board again after it"
    store.note_reach(off.device_id, "deploy", "not a reason code")
    store.note_reach(off.device_id, "anything", reasons.HOST_DOWN)
    assert store.reach_problem(off) is None, "only the four codes and the known steps are recorded"


def test_on_request_only_the_named_board_gets_one_connection(state, monkeypatch):
    from orchestrator.Core.mojo import Mojo
    from orchestrator.runtime.device import reasons
    off = board(state, "192.0.2.11", label="Garage")
    locked = board(state, "192.0.2.12", port=2222, label="Desk")
    other = board(state, "192.0.2.13", label="Kitchen")
    record(state, off, "deploy", reasons.HOST_DOWN)
    record(state, locked, "benchmark", reasons.AUTH_FAILED)
    asked = []
    monkeypatch.setattr(reasons, "check_port",
                        lambda host, port=22, timeout=reasons.CONNECT_TIMEOUT: asked.append((host, port)) or None)
    found = {f.code: f for f in devices_check.check_devices(framework_at(state), contact=(off.device_id,))}
    assert asked == [("192.0.2.11", 22)], "one connection, to the named board only"
    now = found[f"devices.{off.device_id}"]
    assert now.severity == Severity.OK and "checked now" in now.message and "Recorded: the last deploy" in now.message
    assert not now.board_check, "checked: nothing left to offer"
    assert found[f"devices.{locked.device_id}"].board_check == locked.device_id, "the other one stays a record"
    asked.clear()
    found = {f.code: f for f in devices_check.check_devices(framework_at(state), contact=(locked.device_id,))}
    login = found[f"devices.{locked.device_id}"]
    assert asked == [("192.0.2.12", 2222)] and login.severity == Severity.WARN
    assert "Mojo does not log in" in login.message and "device_auth_failed" in login.message
    asked.clear()
    monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=3.0: asked.append(host)
                        or reasons.PORT_CLOSED)
    report = Mojo(framework_at(state), checks=[devices_check.check_devices], contact=(other.device_id,)).run()
    (refused,) = [f for f in report.findings if f.code == f"devices.{other.device_id}"]
    assert asked == ["192.0.2.13"] and refused.severity == Severity.WARN and "(device_port_closed)" in refused.message
    assert Mojo(framework_at(state)).checks[-1] is devices_check.check_devices, "a default Mojo checks no board"


def test_mojo_never_logs_in_to_a_board_and_checks_no_board_without_records(state, monkeypatch):
    from orchestrator.runtime.device import reasons
    monkeypatch.setattr(reasons, "check_port", lambda *a, **k: pytest.fail("no board was trusted"))
    assert devices_check.check_devices(framework_at(state)) == []
    source = (REPO / "orchestrator/Core/mojo/checks/devices.py").read_text(encoding="utf-8")
    for login in ("ssh_trust.connect", "SSHClient", "install_public_key", "password=", "secrets_manager",
                  ".note_reach(", ".note_deploy(", ".note_board(", "._write("):
        assert login not in source, f"Mojo Oracle neither logs in nor writes: {login}"


def test_every_board_contact_records_what_it_found(state, monkeypatch, tmp_path):
    """The records Mojo reads are written by the framework's own contacts: a deploy (DeploymentManager, also behind
    the GUI and the deploy tool), the device runtime steps (service.read_key/connect) and the CLI's connection."""
    from types import SimpleNamespace as NS
    from orchestrator.Core.deployment_manager import DeploymentManager, DeployResult
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device import service as dev
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    off = board(state, "192.0.2.11", label="Garage")
    store = TrustedDevices(state)
    manager = DeploymentManager.__new__(DeploymentManager)
    manager.framework = framework_at(state)
    monkeypatch.setattr(DeploymentManager, "_deploy_package", lambda self, *a, **k: DeployResult(
        False, code=reasons.PORT_CLOSED))
    manager.deploy_package(tmp_path / "p.zip", "192.0.2.11", "radxa")
    assert store.reach_problem(off) == {"step": "deploy", "code": reasons.PORT_CLOSED,
                                        "at": store.reach_problem(off)["at"]}
    monkeypatch.setattr(DeploymentManager, "_deploy_package", lambda self, *a, **k: DeployResult(False))
    manager.deploy_package(tmp_path / "p.zip", "192.0.2.11", "radxa")
    assert store.reach_problem(off)["code"] == reasons.PORT_CLOSED, "a failure of another kind changes nothing"
    monkeypatch.setattr(DeploymentManager, "_deploy_package", lambda self, *a, **k: DeployResult(True))
    manager.deploy_package(tmp_path / "p.zip", "192.0.2.11", "radxa")
    assert store.reach_problem(off) is None
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: (_ for _ in ()).throw(
        ConnectionRefusedError(111, "refused")))
    with pytest.raises(dev.DeviceRuntimeError) as e:
        dev.read_key(off, state, step="benchmark")
    assert e.value.reason == reasons.PORT_CLOSED and store.reach_problem(off)["step"] == "benchmark"
    key = NS()
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: key)
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda *a: "known")
    monkeypatch.setattr(ssh_trust, "fingerprint", lambda k: off.fingerprint)
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: NS(close=lambda: None))
    dev.connect(off, state, "radxa", off.fingerprint, step="runtime_deploy")
    assert store.reach_problem(off) is None, "logged in: the record ends"
    import paramiko
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: (_ for _ in ()).throw(
        paramiko.AuthenticationException("no")))
    with pytest.raises(dev.DeviceRuntimeError):
        dev.connect(off, state, "radxa", off.fingerprint, step="probe")
    assert store.reach_problem(off)["code"] == reasons.AUTH_FAILED


# --- on request: the tool for an AI and the person's command ---------------------------------------------------------

def counted_port_checks(monkeypatch, answer=None):
    from orchestrator.runtime.device import reasons
    asked = []
    monkeypatch.setattr(reasons, "check_port",
                        lambda host, port=22, timeout=reasons.CONNECT_TIMEOUT: asked.append((host, port)) or answer)
    return asked


def only_board_checks(monkeypatch):
    """Mojo with its board check only (the other checks are tested elsewhere and may look at Docker)."""
    from orchestrator.Core.mojo import runner
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [runner.check_devices])


def test_the_tool_contacts_no_board_names_the_request_and_checks_one_board_by_its_label(booted_framework,
                                                                                    monkeypatch):
    from orchestrator.runtime.device import reasons
    from orchestrator.tools.registry import ToolContext, ToolError
    only_board_checks(monkeypatch)
    state = Path(booted_framework.config.user_state_dir)
    off = board(state, "192.0.2.11", label="Garage")
    board(state, "192.0.2.12", label="Twin")
    board(state, "192.0.2.13", label="Twin")
    record(state, off, "deploy", reasons.HOST_DOWN)
    asked = counted_port_checks(monkeypatch)
    registry = booted_framework.tool_registry
    out = registry.call("run_mojo_oracle", {}, ToolContext("mcp"))
    assert asked == [], "the tool's default run contacts no board"
    (row,) = [f for f in out.findings if f.code == f"devices.{off.device_id}"]
    assert 'run_mojo_oracle {"board": "Garage"}' in row.check_board and "Mojo did not contact" in row.check_board
    assert "radxa" not in json.dumps(out.model_dump()), "the device account never reaches the AI"
    out = registry.call("run_mojo_oracle", {"board": "garage"}, ToolContext("mcp"))
    assert asked == [("192.0.2.11", 22)], "one connection, to that board only"
    (row,) = [f for f in out.findings if f.code == f"devices.{off.device_id}"]
    assert row.severity == "ok" and "checked now" in row.message and row.check_board is None
    asked.clear()
    for name, code in (("192.0.2.11", "unknown_id"), ("radxa@192.0.2.11", "unknown_id"), ("Twin", "invalid_value")):
        with pytest.raises(ToolError) as e:
            registry.call("run_mojo_oracle", {"board": name}, ToolContext("mcp"))
        assert e.value.code == code and e.value.field == "board", name
        assert "192.0.2.12" not in e.value.message + e.value.next
    assert asked == [], "an address is never taken from an AI, and a refusal contacts nothing"
    spec = registry.get("run_mojo_oracle")
    assert spec.annotations()["readOnlyHint"] is True and spec.annotations()["openWorldHint"] is True


def test_the_command_contacts_no_board_and_asks_before_checking_one(booted_framework, monkeypatch):
    from test_mojo_cli import run_cli
    from orchestrator.runtime.device import reasons
    only_board_checks(monkeypatch)
    state = Path(booted_framework.config.user_state_dir)
    off = board(state, "192.0.2.11", label="Garage board")
    record(state, off, "verify", reasons.PORT_CLOSED)
    asked = counted_port_checks(monkeypatch)
    result, out = run_cli(booted_framework, monkeypatch, "mojo")
    assert asked == [] and "Mojo did not contact the board Garage board" in out
    assert 'llmcf mojo --oracle --board "Garage board"' in out and "Check Garage board now?" not in out
    result, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="n\n")
    assert asked == [] and "Check Garage board now?" in out and "did not contact Garage board" in out, out
    result, out = run_cli(booted_framework, monkeypatch, "mojo", terminal=True, answer="y\n")
    assert asked == [("192.0.2.11", 22)] and "checked now" in out, out
    asked.clear()
    result, out = run_cli(booted_framework, monkeypatch, "mojo", "--json")
    data = json.loads(result.stdout)
    (row,) = [f for f in data["findings"] if f["code"] == f"devices.{off.device_id}"]
    assert row["check_board"]["device_id"] == off.device_id and "--board" in row["check_board"]["command"]
    assert asked == [], "--json never asks and contacts no board"
    for name in ("Garage board", "192.0.2.11", off.device_id):
        result, out = run_cli(booted_framework, monkeypatch, "mojo", "--oracle", "--board", name)
        assert asked == [("192.0.2.11", 22)], name
        asked.clear()
    result, out = run_cli(booted_framework, monkeypatch, "mojo", "--board", "nothing")
    assert result.exit_code == 2 and "No trusted board is named 'nothing'" in out and "Garage board" in out
    result, out = run_cli(booted_framework, monkeypatch, "mojo", "--miracle", "--board", "Garage board")
    assert result.exit_code == 2 and "--board goes with --oracle" in out and asked == []


# --- Ditto's image in Docker mode ------------------------------------------------------------------------------------

PIN = SimpleNamespace(commit="c" * 40, tag="b1")
IMAGE_ID = "sha256:" + "a" * 64


class FakeDocker:
    def __init__(self, reason=None, image=None):
        self.reason, self._image, self.calls = reason, image, []

    def unavailable(self):
        return self.reason

    def image(self, ref):
        self.calls.append(ref)
        return self._image if self._image is not None and self._image.image_id == ref else None

    def __getattr__(self, name):  # build, pull, remove, run: Mojo only asks
        if name.startswith("__"):
            raise AttributeError(name)
        pytest.fail(f"Mojo called docker.{name}")


def image(commit=PIN.commit, build="b" * 64, digests=()):
    return cs.ImageInfo(image_id=IMAGE_ID, tags=("llm-framework/ditto-runtime:b1-bbbb",),
                        labels={cs.LABEL: "image", cs.LABEL_COMMIT: commit, cs.LABEL_BUILD: build}, size=300_000_000,
                        repo_digests=tuple(digests))


def mode_file(root, mode="docker"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "mode.json").write_text(json.dumps({"mode": mode, "docker": {
        "image_id": IMAGE_ID, "build_id": "b" * 64, "tag": "llm-framework/ditto-runtime:b1-bbbb",
        "verified_at": "2026-10-15T10:00:00+00:00"}}), encoding="utf-8")
    return root


def test_dittos_docker_image_is_checked_only_in_docker_mode(tmp_path, monkeypatch):
    root = mode_file(tmp_path / "runtime" / "llama.cpp", mode="bare-metal")
    monkeypatch.setattr(runtime_check, "_ditto_docker", lambda: pytest.fail("bare metal: Docker is not asked"))
    assert runtime_check._docker_mode(root, PIN) == []
    assert runtime_check._docker_mode(tmp_path / "nothing", PIN) == []


@pytest.mark.parametrize("docker, code, severity, cannot", [
    (FakeDocker(image=image()), "runtime.docker", Severity.OK, ""),
    (FakeDocker(reason="the Docker daemon is not running"), "runtime.docker.unavailable", Severity.WARN, "admin"),
    (FakeDocker(), "runtime.docker.image", Severity.WARN, "not_automatic"),
    (FakeDocker(image=image(commit="d" * 40)), "runtime.docker.image", Severity.WARN, "not_automatic"),
    (FakeDocker(image=image(build="e" * 64)), "runtime.docker.image", Severity.WARN, "not_automatic"),
    (FakeDocker(image=image(digests=["llm-framework/ditto-runtime@sha256:" + "f" * 64])), "runtime.docker.image",
     Severity.WARN, "not_automatic"),
])
def test_dittos_docker_image_is_there_built_here_of_the_pin_and_verified(tmp_path, monkeypatch, docker, code,
                                                                        severity, cannot):
    root = mode_file(tmp_path / "runtime" / "llama.cpp")
    monkeypatch.setattr(runtime_check, "_ditto_docker", lambda: docker)
    (finding,) = runtime_check._docker_mode(root, PIN)
    assert (finding.code, finding.severity, finding.cannot) == (code, severity, cannot), finding.message
    if severity == Severity.WARN:
        assert finding.where == "llm-framework/ditto-runtime:b1-bbbb" and finding.why
        assert "ensure --mode" in finding.fix_hint and "the models stay" in finding.fix_hint
    else:
        assert "never pulled" in finding.message and IMAGE_ID[7:19] in finding.message


def test_the_runtime_check_reports_dittos_docker_mode(tmp_path, monkeypatch):
    from orchestrator.runtime.build import toolchain
    mode_file(tmp_path / "runtime" / "llama.cpp")
    monkeypatch.setattr(runtime_check, "_ditto_docker", lambda: FakeDocker(reason="the Docker daemon is not running"))

    def no_toolchain(full=False):
        raise toolchain.ToolchainError("not probed in this test")
    monkeypatch.setattr(toolchain, "detect_toolchain", no_toolchain)
    codes = [f.code for f in runtime_check.check_runtime(framework_at(tmp_path))]
    assert "runtime.docker.unavailable" in codes
