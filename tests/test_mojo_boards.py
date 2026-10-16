"""Mojo Oracle's lines for the boards the person trusted (eighth round N6, next-fixes 130) and for Ditto's image in
Docker mode (DECISIONS round 7 D).

For a board Mojo reads the framework's known_hosts and opens one connection to the SSH port: it never logs in, and
the device account is never named. For Ditto's Docker mode it asks Docker about the recorded image: never built,
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


def test_each_trusted_board_is_named_with_its_reason_code_and_the_next_step(state, monkeypatch):
    from orchestrator.runtime.device import reasons
    up = board(state, "192.0.2.10", label="Kitchen")
    off = board(state, "192.0.2.11", label="Garage")
    closed = board(state, "192.0.2.12", port=2222, label="Desk")
    answers = {"192.0.2.10": None, "192.0.2.11": reasons.HOST_DOWN, "192.0.2.12": reasons.PORT_CLOSED}
    asked = []
    monkeypatch.setattr(reasons, "check_port",
                        lambda host, port=22, timeout=reasons.CONNECT_TIMEOUT: asked.append((host, port))
                        or answers[host])
    framework = framework_at(state)
    found = {f.code: f for f in devices_check.check_devices(framework)}
    assert sorted(asked) == [("192.0.2.10", 22), ("192.0.2.11", 22), ("192.0.2.12", 2222)], "one connection each"
    assert found[f"devices.{up.device_id}"].severity == Severity.OK
    down = found[f"devices.{off.device_id}"]
    assert down.severity == Severity.INFO and "(device_host_down)" in down.message and "Garage" in down.message
    assert down.where == "192.0.2.11" and down.why and "switched on" in down.fix_hint
    refused = found[f"devices.{closed.device_id}"]
    assert refused.severity == Severity.WARN and "(device_port_closed)" in refused.message
    assert refused.where == "192.0.2.12:2222" and "2222" in refused.message and refused.why
    assert offer_for(framework, refused).verdict == "person", "only the person opens the board's SSH port"
    for finding in found.values():
        assert "radxa" not in finding.message + finding.fix_hint + finding.where, "the device account never shows"


def test_a_changed_or_missing_host_key_is_found_without_the_network(state, monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device import reasons
    from orchestrator.utils import ssh_trust
    other = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, "192.0.2.20", 22, other)   # known_hosts holds another key for the board now
    changed = board(state, "192.0.2.20", label="Reinstalled", trusted_key=False)
    gone = board(state, "192.0.2.21", label="Removed key", trusted_key=False)
    monkeypatch.setattr(reasons, "check_port", lambda host, port=22, timeout=3.0: None)
    found = {f.code: f for f in devices_check.check_devices(framework_at(state))}
    swapped = found[f"devices.{changed.device_id}"]
    assert swapped.severity == Severity.WARN and "(device_host_key_changed)" in swapped.message
    assert swapped.cannot == "person" and swapped.where == "192.0.2.20"
    missing = found[f"devices.{gone.device_id}"]
    assert missing.severity == Severity.WARN and "known_hosts" in missing.message and missing.cannot == "person"


def test_mojo_never_logs_in_to_a_board_and_checks_no_board_without_records(state, monkeypatch):
    from orchestrator.runtime.device import reasons
    monkeypatch.setattr(reasons, "check_port", lambda *a, **k: pytest.fail("no board was trusted"))
    assert devices_check.check_devices(framework_at(state)) == []
    source = (REPO / "orchestrator/Core/mojo/checks/devices.py").read_text(encoding="utf-8")
    for login in ("ssh_trust.connect", "SSHClient", "install_public_key", "password=", "secrets_manager"):
        assert login not in source, login
    from orchestrator.Core.mojo.runner import DEFAULT_CHECKS
    assert devices_check.check_devices in DEFAULT_CHECKS


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
