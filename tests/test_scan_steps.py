"""The person's hardware scans and packages as core steps (W1' of 2026-10-16, GUI parity wave 5a, chain "deploy").

`llmcf scan` (this computer, --device, --import) and `llmcf deploy package` keep no logic of their own any more: they
call orchestrator/runtime/scan_steps.py and DeploymentManager's make_package, as the GUI's Builder tab > Scan /
Import... menu and the Deployment tab's "Create package only..." do. These tests pin the core steps; the CLI tests
(test_cli_builds.py, test_hardware_probe.py, test_targets_modules.py, test_review_capabilities_paths.py) stay as they
were and pin the CLI's output.
"""

import ast
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"
RK3566 = FIXTURES / "schema2_rk3566_dietpi.txt"


class FakeHostKey:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"made-up host key of a scanned fixture board"


def board():
    from orchestrator.runtime.profile import parse_profile
    return parse_profile(RK3566.read_text(encoding="utf-8"))


# --- this computer ------------------------------------------------------------------------------------------------

class TestScanLocal:

    def probes(self, tmp_path):
        from orchestrator.runtime.probe_service import HardwareProbes
        return HardwareProbes(tmp_path / "state")

    def test_the_python_probe_writes_the_file_and_stores_this_computer(self, tmp_path, monkeypatch):
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import read_profile
        from orchestrator.runtime.scan_steps import scan_local
        monkeypatch.setattr(hardware, "local_profile", lambda **options: board())
        probes = self.probes(tmp_path)
        out = tmp_path / "out" / "probe.txt"
        scan = scan_local(probes, out)
        assert scan.how == "Python probe" and scan.notes == [] and not scan.needs_reprobe
        assert read_profile(out) == board() and scan.output_file == out
        assert scan.local_path == probes.store.local_path() and probes.store.load_local() == board().stable()

    def test_the_json_view(self, tmp_path, monkeypatch):
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import parse_profile_json
        from orchestrator.runtime.scan_steps import scan_local
        monkeypatch.setattr(hardware, "local_profile", lambda **options: board())
        out = tmp_path / "probe.json"
        scan_local(self.probes(tmp_path), out, as_json=True)
        assert parse_profile_json(out.read_text(encoding="utf-8")) == board()

    def test_the_fallback_script_writes_the_file_itself(self, tmp_path, monkeypatch):
        from orchestrator.runtime import probe_service
        from orchestrator.runtime.scan_steps import scan_local
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            Path(cmd[cmd.index("--output") + 1]).write_text(RK3566.read_text(encoding="utf-8"), encoding="utf-8")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(probe_service, "platform", SimpleNamespace(system=lambda: "Linux"))
        monkeypatch.setattr(probe_service, "subprocess", SimpleNamespace(
            run=run, CalledProcessError=subprocess.CalledProcessError, TimeoutExpired=subprocess.TimeoutExpired,
            DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE))
        out = tmp_path / "script.txt"
        scan = scan_local(self.probes(tmp_path), out, use_script=True)
        assert scan.how == "fallback script" and len(calls) == 1 and calls[0][-2:] == ["--output", str(out)]
        assert out.read_text(encoding="utf-8") == RK3566.read_text(encoding="utf-8"), "the script's own file stays"

    def test_a_file_that_cannot_be_written_is_its_own_error(self, tmp_path, monkeypatch):
        from orchestrator.runtime import hardware
        from orchestrator.runtime.scan_steps import ProfileWriteError, scan_local
        monkeypatch.setattr(hardware, "local_profile", lambda **options: board())
        blocker = tmp_path / "a-file"
        blocker.write_text("x")
        with pytest.raises(ProfileWriteError) as raised:
            scan_local(self.probes(tmp_path), blocker / "probe.txt")
        assert raised.value.path == blocker / "probe.txt" and str(raised.value) == str(raised.value.error)

    def test_a_failing_probe_raises_the_probe_error(self, tmp_path, monkeypatch):
        from orchestrator.runtime.probe_service import ProbeError
        from orchestrator.runtime.scan_steps import scan_local
        probes = self.probes(tmp_path)

        def broken(**options):
            raise ProbeError("no probe here", next="Run it in a terminal.")

        monkeypatch.setattr(probes, "run_local", broken)
        with pytest.raises(ProbeError, match="no probe here"):
            scan_local(probes, tmp_path / "probe.txt")
        assert not (tmp_path / "probe.txt").exists()


# --- a board's probe file -----------------------------------------------------------------------------------------

def test_label_problem():
    from orchestrator.runtime.scan_steps import label_problem
    assert not label_problem(None) and not label_problem("Zero 3W") and not label_problem("  a   b ")
    assert label_problem("") and label_problem("   ") and label_problem("x" * 65) and label_problem("a\nb")


def test_import_probe_with_a_label_and_the_matching_target(tmp_path):
    from orchestrator.runtime.probe_service import HardwareProbes, ProbeError
    from orchestrator.runtime.scan_steps import import_probe
    probes = HardwareProbes(tmp_path / "state")
    targets = SimpleNamespace(list_targets=lambda: [{"id": "Rockchip", "vendor": "Rockchip",
                                                     "architecture_family": "aarch64"}],
                              get_capabilities=lambda tid: SimpleNamespace(boards=["rk3566"]),
                              get_target=lambda tid: {})
    imported = import_probe(probes, RK3566, "Zero 3W", targets)
    assert imported.info.label == "Zero 3W" and probes.get(imported.probe_id) == imported.profile
    assert imported.match.target == "Rockchip" and imported.match.board == "rk3566"
    assert import_probe(probes, RK3566).match is None, "no targets: no match, still imported"
    with pytest.raises(ValueError):
        import_probe(probes, RK3566, "x" * 65)
    bad = tmp_path / "x.txt"
    bad.write_text("PROBE_SCHEMA=2\nOS=linux\nOS=linux\n")
    with pytest.raises(ProbeError):
        import_probe(probes, bad)


# --- a board over SSH ---------------------------------------------------------------------------------------------

class TestScanDevice:

    @pytest.fixture
    def ssh(self, monkeypatch):
        from orchestrator.runtime import remote_probe as rp
        from orchestrator.utils import ssh_trust
        seen = {"connect": [], "paired": [], "closed": 0, "fail": None, "probe_fail": None}

        class Client:
            def close(self):
                seen["closed"] += 1

            def get_transport(self):
                return SimpleNamespace(get_remote_server_key=lambda: FakeHostKey())

        def connect(host, user, state, port=22, password=None):
            seen["connect"].append((host, user, port, password))
            if seen["fail"] is not None:
                raise seen["fail"]
            return Client()

        def probe(client):
            if seen["probe_fail"] is not None:
                raise seen["probe_fail"]
            return rp.RemoteProbeResult(board(), "sh", ["no python3 on the device; used hardware_probe.sh"])

        monkeypatch.setattr(ssh_trust, "connect", connect)
        monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line, *_a, **_k: seen["paired"].append(line))
        monkeypatch.setattr(rp, "probe_over_ssh", probe)
        return seen

    def test_the_board_is_stored_remembered_and_trusted(self, tmp_path, ssh):
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.profile import ProfileStore, device_id_for_host_key, read_profile
        from orchestrator.runtime.scan_steps import scan_device
        from orchestrator.utils import ssh_trust
        state, audited = tmp_path / "state", []
        audit = SimpleNamespace(record=lambda *a, **k: audited.append((a, k)))
        out = tmp_path / "board.txt"
        clients = []
        scan = scan_device(state, "192.0.2.10", 2222, "dietpi", FakeHostKey(), password="pw-made-up",
                           label="ZERO 3W", output_file=out, via="gui", actor="gui", audit=audit,
                           on_client=clients.append)
        device_id = device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
        assert ssh["connect"] == [("192.0.2.10", "dietpi", 2222, "pw-made-up")] and len(ssh["paired"]) == 1
        assert ssh["closed"] == 1 and len(clients) == 1, "the session is handed out for a Cancel and closed"
        assert (scan.device_id, scan.tool, scan.output_file) == (device_id, "sh", out)
        assert scan.notes == ["no python3 on the device; used hardware_probe.sh"]
        store = ProfileStore(state)
        assert store.load_device(device_id) == board().stable() and read_profile(out) == board()
        info = store.device_info(device_id)
        assert (info["label"], info["address"]) == ("ZERO 3W", "192.0.2.10:2222")
        assert store.load_deploy_settings(device_id)["ssh_user"] == "dietpi"
        trusted = TrustedDevices(state).get(device_id)
        assert scan.trusted == trusted and trusted.paired and trusted.label == "ZERO 3W"
        assert (trusted.host, trusted.port, trusted.trusted_via) == ("192.0.2.10", 2222, "gui")
        (args, kwargs), = audited
        assert args[:2] == ("gui", "device_trusted") and kwargs["extra"]["confirm_channel"] == "framework-gui"

    @pytest.mark.parametrize("given", ["signer", "lookup"])
    def test_the_pairing_takes_the_callers_framework_signer(self, tmp_path, ssh, monkeypatch, given):
        """Regression (failed before: scan_device had no signer of its own and paired with the state folder's store
        opened as a keyring store, so a passphrase or helper store gave the board no signing key)."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from orchestrator.Core.community_pack import BoardSigner
        from orchestrator.runtime.device import board_ops
        from orchestrator.runtime.scan_steps import scan_device
        from orchestrator.utils import ssh_trust
        bundles = []
        monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line, bundle=None, **_k: bundles.append(bundle))
        monkeypatch.setattr(board_ops, "framework_signer", lambda *a, **k: pytest.fail("the state folder's store"))
        signer = BoardSigner(Ed25519PrivateKey.generate())
        scan_device(tmp_path / "state", "192.0.2.10", 22, "dietpi", FakeHostKey(), password="pw-made-up",
                    record=False, signer=signer if given == "signer" else (lambda: signer))
        assert [b.signers for b in bundles] == [signer.allowed_signers]

    def test_without_a_key_the_session_s_key_names_the_board_and_record_can_be_left_out(self, tmp_path, ssh):
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.profile import device_id_for_host_key
        from orchestrator.runtime.scan_steps import scan_device
        from orchestrator.utils import ssh_trust
        scan = scan_device(tmp_path / "state", "myboard.local", 22, "dietpi", record=False)
        assert scan.device_id == device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
        assert scan.trusted is None and TrustedDevices(tmp_path / "state").list() == []
        assert scan.output_file is None and ssh["paired"] == [], "no password: no key is installed"

    def test_a_failed_login_carries_its_reason_and_is_recorded_for_mojo(self, tmp_path, ssh):
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        state = tmp_path / "state"
        paramiko = pytest.importorskip("paramiko")
        known = TrustedDevices(state).record("192.0.2.10", 22, "dietpi", FakeHostKey(), via="cli", paired=True)
        ssh["fail"] = paramiko.AuthenticationException("bad password")
        with pytest.raises(DeviceScanError) as raised:
            scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey())
        assert raised.value.kind == "login" and raised.value.reason == reasons.AUTH_FAILED
        reach = TrustedDevices(state).last_deploys()[known.device_id]["reach"]
        assert (reach["step"], reach["code"]) == ("probe", reasons.AUTH_FAILED)
        ssh["fail"] = None
        scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey())
        assert "reach" not in TrustedDevices(state).last_deploys().get(known.device_id, {}), "reached: problem ends"

    def test_a_failed_probe_closes_the_session_and_stores_nothing(self, tmp_path, ssh):
        from orchestrator.runtime.profile import ProfileStore
        from orchestrator.runtime.remote_probe import RemoteProbeError
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        ssh["probe_fail"] = RemoteProbeError("hardware_probe.sh exited with status 3")
        with pytest.raises(DeviceScanError) as raised:
            scan_device(tmp_path / "state", "192.0.2.10", 22, "dietpi", FakeHostKey())
        assert raised.value.kind == "probe" and "status 3" in str(raised.value) and ssh["closed"] == 1
        assert ProfileStore(tmp_path / "state").list_devices() == []

    def test_a_profile_that_cannot_be_saved(self, tmp_path, ssh):
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        with pytest.raises(DeviceScanError) as raised:
            scan_device(tmp_path / "state", "192.0.2.10", 22, "dietpi", FakeHostKey(), label="x" * 65)
        assert raised.value.kind == "save"

    def test_a_wait_for_the_board_is_said_and_the_cancel_ends_it_at_once(self, tmp_path, ssh, monkeypatch):
        """Stage C wave 2 (remote-callers + N5): the probe's session waits up to 300 s for a board whose network
        dropped. The caller's on_event gets the wait (the person's line through remote/text.py wait_lines, as the
        Boards window says it) and its CancelToken ends the wait at once: DeviceScanError "cancelled", nothing
        stored."""
        from orchestrator.remote import reasons as remote_reasons
        from orchestrator.remote.session import CancelToken
        from orchestrator.remote.text import wait_lines
        from orchestrator.runtime import remote_probe as rp
        from orchestrator.runtime.profile import ProfileStore
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        monkeypatch.setattr(remote_reasons, "check_port", lambda host, port=22, timeout=3.0: remote_reasons.HOST_DOWN)
        monkeypatch.setattr(rp, "probe_over_ssh", lambda session: session.wait_for_host("command"))  # a drop
        cancel, lines, kinds = CancelToken(), [], []
        say = wait_lines(lines.append, "192.0.2.10", "en")

        def on_event(event):
            kinds.append(event.kind)
            say(event)
            if event.kind == "waiting":
                cancel.cancel()   # the person's Cancel while the scan waits for the board
        with pytest.raises(DeviceScanError) as raised:
            scan_device(tmp_path / "state", "192.0.2.10", 22, "dietpi", FakeHostKey(), cancel=cancel,
                        on_event=on_event)
        assert raised.value.kind == "cancelled" and kinds == ["waiting"], kinds
        assert len(lines) == 1 and lines[0].startswith("Waiting for the board at 192.0.2.10"), lines
        assert ProfileStore(tmp_path / "state").list_devices() == [] and ssh["closed"] >= 1

    def test_a_cancel_before_the_probe_starts_stores_nothing(self, tmp_path, ssh):
        from orchestrator.remote.session import CancelToken
        from orchestrator.runtime.profile import ProfileStore
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        cancel = CancelToken()
        cancel.cancel()
        with pytest.raises(DeviceScanError) as raised:
            scan_device(tmp_path / "state", "192.0.2.10", 22, "dietpi", FakeHostKey(), cancel=cancel)
        assert raised.value.kind == "cancelled" and ProfileStore(tmp_path / "state").list_devices() == []

    def test_the_audit_log_is_opened_only_for_the_record(self, tmp_path, ssh):
        """Review (w5-deploy-v): the CLI opened its audit log (the OS keyring's audit key, created on first use) only
        after a successful probe; the extracted step got it before the login. `audit_factory` opens it when the
        trusted record is written, and never for a scan that fails or writes no record."""
        from orchestrator.runtime.remote_probe import RemoteProbeError
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        opened, audited = [], []

        def factory():
            opened.append(1)
            return SimpleNamespace(record=lambda *a, **k: audited.append(a[:2]))

        state = tmp_path / "state"
        ssh["fail"] = ConnectionRefusedError(111, "refused")
        with pytest.raises(DeviceScanError):
            scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey(), audit_factory=factory)
        ssh["fail"], ssh["probe_fail"] = None, RemoteProbeError("hardware_probe.sh exited with status 3")
        with pytest.raises(DeviceScanError):
            scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey(), audit_factory=factory)
        ssh["probe_fail"] = None
        with pytest.raises(DeviceScanError):
            scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey(), label="x" * 65, audit_factory=factory)
        scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey(), record=False, audit_factory=factory)
        assert opened == [], "no audit log for a failed scan or one without a record"
        scan = scan_device(state, "192.0.2.10", 22, "dietpi", FakeHostKey(), via="cli", actor="cli",
                           audit_factory=factory)
        assert opened == [1] and audited == [("cli", "device_trusted")] and scan.trusted is not None


def test_no_ai_reachable_code_imports_the_person_s_scan_steps():
    """The scan steps log in with a password, write the trusted record and run the local probe for the person;
    nothing an AI can call imports them (the AI's probe tools use HardwareProbes directly)."""
    from test_deploy_tools import _python_files
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else \
                [a.name for a in node.names] if isinstance(node, ast.Import) else []
            assert not any(name.endswith("scan_steps") for name in names), path


# --- a package without a board ------------------------------------------------------------------------------------

class TestMakePackage:

    def manager(self):
        calls = []

        class Manager:
            def create_package(self, artifact_path, profile_name, docker_config, output_dir, **extra):
                calls.append((artifact_path, profile_name, docker_config, output_dir, extra))
                return Path(output_dir) / "deploy_x.zip"

        return Manager(), calls

    def framework(self, tmp_path):
        return SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: {
            "build_dir": str(tmp_path / "builds"), "output_dir": str(tmp_path / "out")}.get(key, default)),
            resolve_path=lambda value: Path(value))

    def test_the_cli_call_is_unchanged_and_the_gui_adds_kv_and_cancel(self, tmp_path):
        from orchestrator.Core.deployment_manager import DEFAULT_INFERENCE_IMAGE, make_package
        manager, calls = self.manager()
        build = tmp_path / "out" / "Rockchip" / "m"
        build.mkdir(parents=True)
        assert make_package(manager, self.framework(tmp_path), "Rockchip/m", "Rockchip", False,
                            tmp_path / "out") == tmp_path / "out" / "deploy_x.zip"
        assert calls[-1] == (build, "Rockchip", {"use_docker": False}, tmp_path / "out", {})
        stop = lambda: False  # noqa: E731
        make_package(manager, self.framework(tmp_path), str(build), "Rockchip", True, tmp_path / "out",
                     kv_cache="q8_0", cancelled=stop)
        assert calls[-1][2] == {"use_docker": True, "image_name": DEFAULT_INFERENCE_IMAGE}
        assert calls[-1][4] == {"kv_cache": "q8_0", "cancelled": stop}

    def test_the_source_is_looked_up_like_list_artifacts_names_it(self, tmp_path, monkeypatch):
        from orchestrator.Core.deployment_manager import resolve_package_source
        framework = self.framework(tmp_path)
        waiting = tmp_path / "builds" / "Rockchip" / "w"
        waiting.mkdir(parents=True)
        assert resolve_package_source(framework, "Rockchip/w", tmp_path / "out") == waiting
        assert resolve_package_source(framework, "nowhere", tmp_path / "out") == tmp_path / "out" / "nowhere"
        monkeypatch.chdir(tmp_path)
        (tmp_path / "here").mkdir()
        assert resolve_package_source(framework, "here", tmp_path / "out") == (tmp_path / "here").resolve()

    def test_the_model_a_package_installs(self, tmp_path):
        from orchestrator.Core.deployment_manager import package_model_name

        def package(name, *files):
            path = tmp_path / name
            with zipfile.ZipFile(path, "w") as zf:
                for file in files:
                    zf.writestr(file, b"x")
            return path

        assert package_model_name(package("a.zip", "smol-q4.gguf", "deploy.sh", "data/configs/x")) == "smol-q4.gguf"
        assert package_model_name(package("b.zip", "smol-q4.gguf", "images/inference.tar")) is None
        assert package_model_name(package("c.zip", "model.rkllm", "deploy.sh")) is None
        broken = tmp_path / "d.zip"
        broken.write_bytes(b"not a zip")
        assert package_model_name(broken) is None and package_model_name(tmp_path / "missing.zip") is None

    def test_a_failed_package_keeps_its_reason(self, tmp_path):
        from orchestrator.Core.deployment_manager import DeployError, DeploymentManager
        manager = DeploymentManager(SimpleNamespace())

        def refuse(*a, **k):
            raise DeployError("The image is not pinned.", next="Package without Docker images.")

        manager.create_package = refuse
        assert manager.create_deployment_package(tmp_path / "m.gguf", "Rockchip", {}, tmp_path) is None
        assert manager.last_package_error.next == "Package without Docker images."
        manager.create_package = lambda *a, **k: tmp_path / "deploy.zip"
        assert manager.create_deployment_package(tmp_path / "m.gguf", "Rockchip", {}, tmp_path) is not None
        assert manager.last_package_error is None
