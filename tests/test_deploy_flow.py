"""One deploy step and one device-runtime flow (consolidation c3-deploy-flow): the deploy of a build to a board
(`llmcf deploy run` and the Deployment tab's Deploy) and the deploy of the self-built runtime (`llmcf device runtime
deploy`, the Boards window's Deploy runtime... and the AI's deploy_device_runtime through service.deploy).

Characterization first: what each channel does today, step by step (the login, the board's trusted record in the
person's channels only, the early refusal of root, the short benchmark's consent, the install and the 'tested on'
record), so the shared core steps keep it.

No network, no Docker, no board: SSH, the package's upload and the board are stand-ins.
"""

import json
import logging
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_device_runtime import PIN, RK3566, FakeHostKey

HOST = "192.0.2.61"


def auto_kv() -> str:
    """The KV-cache type the deploy settings choose for the fixture board by themselves (automatic)."""
    from orchestrator.runtime.device.settings import DeploySettings
    return DeploySettings(ssh_user="dietpi").kv_choice(RK3566).kind


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append(dict(actor=actor, tool=tool, decision=decision, outcome=outcome, **kwargs))


def a_build(folder: Path, name: str = "build-f") -> Path:
    build = folder / name
    build.mkdir(parents=True)
    (build / "smol-q4_0.gguf").write_bytes(b"GGUF fixture model of the deploy flow")
    (build / "build_info.json").write_text(json.dumps({"model": "smol", "format": "gguf", "primary": "smol-q4_0.gguf",
                                                      "quantization": "Q4_0", "target": "Rockchip"}))
    return build


def device_id_of(key) -> str:
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    return device_id_for_host_key(ssh_trust.fingerprint(key))


class Framework:
    """What the Deployment tab's worker reads of the framework: the settings (the output folder, the per-user folder),
    the path rule, the registry's audit log (None: no registry) and the real DeploymentManager."""

    def __init__(self, root: Path, *, registry: bool = True):
        from orchestrator.Core.deployment_manager import DeploymentManager
        self.root = root
        self.config = SimpleNamespace(get=lambda key, default=None: default, user_state_dir=str(root / "state"))
        self.tool_registry = SimpleNamespace(audit=Audit()) if registry else None
        self.manager = DeploymentManager(self)

    def resolve_path(self, value):
        return self.root / value

    def get_component(self, name):
        return self.manager if name == "deployment_manager" else None


@pytest.fixture
def keep_logger_levels():
    """`llm-cli -q` sets every existing logger to ERROR (set_global_level); later tests read WARNING logs."""
    saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
    root = logging.getLogger().level
    yield
    for name in list(logging.Logger.manager.loggerDict):
        logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
    logging.getLogger().setLevel(root)


# --- the deploy of a build: `llmcf deploy run` ----------------------------------------------------------------------

@pytest.mark.usefixtures("keep_logger_levels")
class TestDeployRun:

    def run(self, tmp_path, monkeypatch, result, *args):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from test_cli_builds import quiet_console
        printed = quiet_console(monkeypatch)
        package = tmp_path / "deploy_smol_1.zip"
        package.write_bytes(b"PK")
        calls = []

        class Manager:
            def host_key_status(self, host, port=22):
                calls.append(("key", host, port))
                return "known", FakeHostKey()

            def deploy_package(self, path, host, user, **kw):
                calls.append(("deploy", path, host, user, kw))
                return result

        audit = Audit()
        ctx = cli_module.FrameworkContext()
        ctx._initialized = True
        ctx.framework_manager = SimpleNamespace(
            config=SimpleNamespace(get=lambda key, default=None: default, user_state_dir=str(tmp_path / "state")),
            resolve_path=lambda p: tmp_path / p, tool_registry=SimpleNamespace(audit=audit))
        ctx.deployment_manager = Manager()
        out = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "run", str(package), "--host", HOST, "--user",
                                                  "radxa", *args], obj=ctx)
        return SimpleNamespace(code=out.exit_code, out=printed.getvalue() + out.output, calls=calls, audit=audit,
                               package=package, state=tmp_path / "state")

    def test_the_key_is_read_then_the_package_deployed_then_the_board_remembered_with_the_cli_audit(self, tmp_path,
                                                                                                  monkeypatch):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.runtime.device.trusted import TrustedDevices
        done = self.run(tmp_path, monkeypatch, DeployResult(True, exit_status=0, device_state="ok"),
                        "--port", "2222", "--label", "Shed board", "--wait", "60")
        assert done.code == 0, done.out
        (key, deploy) = done.calls
        assert key == ("key", HOST, 2222)
        _kind, path, host, user, kw = deploy
        assert (Path(path), host, user) == (done.package, HOST, "radxa")
        assert kw["port"] == 2222 and kw["wait_seconds"] == 60 and kw["password"] is None
        assert callable(kw["cancelled"]) and not kw["cancelled"]() and callable(kw["on_event"])
        assert "expect_fingerprint" not in kw, "deploy run binds the deploy to known_hosts, not to the shown key"
        device = TrustedDevices(done.state).get(device_id_of(FakeHostKey()))
        assert (device.label, device.port, device.user, device.trusted_via, device.paired) == (
            "Shed board", 2222, "radxa", "cli", True)
        (row,) = done.audit.rows
        assert (row["actor"], row["tool"]) == ("cli", "device_trusted")
        assert row["extra"]["confirm_channel"] == "framework-cli"
        assert done.out.index("Deployment Execution Successful") < done.out.index(f"Trusted device {device.device_id}")

    def test_a_failed_deploy_remembers_nothing(self, tmp_path, monkeypatch):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.runtime.device.trusted import TrustedDevices
        done = self.run(tmp_path, monkeypatch, DeployResult(False, code="device_port_closed",
                                                            reason="Target not reachable."))
        assert done.code == 1 and "Deployment Failed" in done.out and "Target not reachable." in done.out
        assert TrustedDevices(done.state).list() == [] and done.audit.rows == []


# --- the deploy of a build: the Deployment tab -----------------------------------------------------------------------

class TestDeploymentTabDeploy:

    def worker(self, framework, source, **kw):
        pytest.importorskip("PySide6")
        from orchestrator.gui.deployment_window import DeploymentWorker
        options = dict(port=2222, label="Shed board", wait_seconds=60, key=FakeHostKey())
        options.update(kw)
        password = options.pop("password", "pw-made-up")
        worker = DeploymentWorker(framework, source, "Rockchip", {"use_docker": False}, "myboard.local", "radxa",
                                  password, **options)
        done = []
        worker.finished.connect(lambda ok, msg: done.append((ok, msg)))
        return worker, done

    def deploys(self, framework, monkeypatch, result):
        calls = []
        monkeypatch.setattr(framework.manager, "deploy_package", lambda path, host, user, **kw: calls.append(
            (Path(path), host, user, kw)) or result)
        return calls

    def test_the_build_is_packaged_in_the_output_folder_and_deployed_to_the_key_shown(self, qapp, tmp_path,
                                                                                      monkeypatch):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.utils import ssh_trust
        framework = Framework(tmp_path)
        calls = self.deploys(framework, monkeypatch, DeployResult(True, exit_status=0, device_state="ok"))
        worker, done = self.worker(framework, a_build(tmp_path / "b"))
        worker.kv_cache = "q8_0"
        worker.run()
        ((path, host, user, kw),) = calls
        assert path.parent == tmp_path / "output" and path.name.startswith("deploy_smol-q4_0_")
        with zipfile.ZipFile(path) as zf:
            assert "smol-q4_0.gguf" in zf.namelist() and "q8_0" in zf.read("deploy.sh").decode()
        assert (host, user) == ("myboard.local", "radxa")
        assert kw["port"] == 2222 and kw["wait_seconds"] == 60 and kw["password"] == "pw-made-up"
        assert kw["expect_fingerprint"] == ssh_trust.fingerprint(FakeHostKey())
        assert callable(kw["cancelled"]) and callable(kw["on_client"]) and callable(kw["on_event"])
        assert worker.password is None, "the one-time password is used once"
        device = TrustedDevices(tmp_path / "state").get(worker.device_id)
        assert (device.label, device.port, device.trusted_via) == ("Shed board", 2222, "gui")
        (row,) = framework.tool_registry.audit.rows
        assert (row["actor"], row["tool"]) == ("gui", "device_trusted")
        assert row["extra"]["confirm_channel"] == "framework-gui"
        ((ok, msg),) = done
        assert ok and str(path) in msg and f"Trusted board {device.device_id} (Shed board)" in msg
        assert worker.model_name == "smol-q4_0.gguf"

    def test_an_existing_package_is_deployed_as_it_is(self, qapp, tmp_path, monkeypatch):
        from orchestrator.Core.deployment_manager import DeployResult
        framework = Framework(tmp_path)
        calls = self.deploys(framework, monkeypatch, DeployResult(True, exit_status=0, device_state="ok"))
        package = tmp_path / "deploy_given.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("smol-q4_0.gguf", b"GGUF")
        worker, done = self.worker(framework, None, package=package, key=None, password=None)
        monkeypatch.setattr(worker, "_remember_device", lambda *a, **k: None)
        worker.run()
        ((path, _host, _user, kw),) = calls
        assert path == package and "expect_fingerprint" not in kw and kw["password"] is None
        assert not (tmp_path / "output").exists(), "nothing is packaged"
        assert done[0][0] and worker.model_name == "smol-q4_0.gguf"

    def test_a_package_that_cannot_be_made_names_its_reason_and_nothing_is_deployed(self, qapp, tmp_path,
                                                                                   monkeypatch):
        framework = Framework(tmp_path)
        calls = self.deploys(framework, monkeypatch, None)
        empty = tmp_path / "empty-build"
        empty.mkdir()
        worker, done = self.worker(framework, empty)
        worker.run()
        ((ok, msg),) = done
        assert not ok and "Package generation failed" in msg and "No model file found" in msg
        assert calls == [] and worker.device_id is None

    def test_without_the_registry_the_board_is_audited_in_the_clis_audit_log(self, qapp, tmp_path, monkeypatch,
                                                                             memory_keyring):
        """The wave-5 minor (w6-deploy-hf, done by c3-deploy-flow): without the registry's audit log the Deployment
        tab used to write the board's trusted record with no audit row; it now audits it in the per-user audit log, as
        `llmcf deploy run` does (person_steps.device_audit, the CLI's opener)."""
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.Core.vault_key import audit_key
        from orchestrator.tools.audit import AuditLog
        framework = Framework(tmp_path, registry=False)
        self.deploys(framework, monkeypatch, DeployResult(True, exit_status=0, device_state="ok"))
        worker, done = self.worker(framework, a_build(tmp_path / "b"))
        worker.run()
        assert done[0][0] and worker.device_id
        state = tmp_path / "state"
        rows = [r for r in AuditLog(state / "audit.db", key=audit_key(None, state)).entries(10)
                if r["tool"] == "device_trusted"]
        assert len(rows) == 1 and rows[0]["actor"] == "gui"
        extra = rows[0]["extra"] if isinstance(rows[0]["extra"], dict) else json.loads(rows[0]["extra"])
        assert extra["confirm_channel"] == "framework-gui" and extra["answer"] == "accepted"

    def test_the_package_is_made_as_deploy_package_makes_it_with_its_image_and_cancel(self, qapp, tmp_path,
                                                                                    monkeypatch):
        """The Deployment tab packages through make_package, the step of `llmcf deploy package` and Create package
        only...: a Docker package gets package_docker_config's image name, and the deploy's Cancel also stops the
        packaging between its steps (no package stays, nothing is deployed)."""
        from orchestrator.Core.deployment_manager import DEFAULT_INFERENCE_IMAGE, DeployResult
        framework = Framework(tmp_path)
        calls = self.deploys(framework, monkeypatch, DeployResult(True, exit_status=0, device_state="ok"))
        real_create = framework.manager.create_package
        seen = []

        def create_package(artifact_path, profile_name, docker_config, output_dir, **extra):
            seen.append((dict(docker_config), sorted(extra)))
            return real_create(artifact_path, profile_name, {"use_docker": False}, output_dir, **extra)

        monkeypatch.setattr(framework.manager, "create_package", create_package)
        worker, done = self.worker(framework, a_build(tmp_path / "b"))
        worker.docker_config = {"use_docker": True}
        worker.kv_cache = "q8_0"
        worker.run()
        assert seen == [({"use_docker": True, "image_name": DEFAULT_INFERENCE_IMAGE}, ["cancelled", "kv_cache"])]
        assert done[0][0] and len(calls) == 1
        seen.clear()
        worker, done = self.worker(framework, a_build(tmp_path / "c"))
        worker.cancel()
        worker.run()
        ((ok, msg),) = done
        assert not ok and "Packaging was cancelled; no package was written." in msg
        assert len(calls) == 1, "nothing more was deployed"
        assert len(list((tmp_path / "output").glob("*.zip"))) == 1 and not list((tmp_path / "output").glob(".*"))

    def test_a_failed_deploy_remembers_nothing(self, qapp, tmp_path, monkeypatch):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.runtime.device.trusted import TrustedDevices
        framework = Framework(tmp_path)
        self.deploys(framework, monkeypatch, DeployResult(False, code="device_port_closed", reason="Not reachable."))
        worker, done = self.worker(framework, a_build(tmp_path / "b"))
        worker.run()
        ((ok, msg),) = done
        assert not ok and "Not reachable." in msg
        assert TrustedDevices(tmp_path / "state").list() == [] and framework.tool_registry.audit.rows == []


# --- the device runtime: the stand-ins of its core steps ------------------------------------------------------------

def runtime_stand_ins(monkeypatch, order, *, uid=1000, ok=True):
    """The core steps behind the three device-runtime deploys, as stand-ins that write down their order: the plan,
    the stored build and its tarball, the person's login (person_steps.connect_stored), the shell, the board's facts,
    the short benchmark's model, the install and the 'tested on' record."""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.build.verify import Check, VerifyReport
    from orchestrator.runtime.device import person_steps, remote, service, steps
    build = SimpleNamespace(build_id="b" * 64, plan={"verify": {}, "target": {"arch": "arm64"}})
    report = VerifyReport([Check("version", True, "b1"), Check("smoke", True, "llama-bench ran")])
    monkeypatch.setattr(steps, "plan_runtime", lambda profile: SimpleNamespace(build_id="b" * 64,
                                                                               target={"arch": "arm64"}, verify={}))
    monkeypatch.setattr(BuildStore, "find", lambda self, plan: build)
    monkeypatch.setattr(steps, "ensure_package", lambda state, device_id, builds, b, source: (
        SimpleNamespace(cleanup_plan=lambda: None, device_id=device_id),
        SimpleNamespace(path=Path("/x.tar.gz"), sha256="a" * 64)))

    class Session:
        key = FakeHostKey()

        def close(self):
            order.append("closed")

    def connect(state, device_id, host, port, user, password=None, *, step="", on_client=None, cancel=None,
                on_event=None):
        order.append(("connect", user, password, step))
        session = Session()
        if on_client is not None:
            on_client(session)
        return session, FakeHostKey()

    def facts(shell):
        order.append("facts")
        return SimpleNamespace(uid=uid, home="/home/dietpi")

    def choose(shell, home, model, *, may_run_untrusted, record_consent):
        allowed = bool(model) and may_run_untrusted(f"{home}/{model}")
        if allowed:
            record_consent("d" * 64)
        order.append(("bench", model, allowed))
        return steps.BenchChoice(f"{home}/{model}" if model else None, allowed, "d" * 64 if model else None)

    def install(shell, **kw):
        # whether the short benchmark may run (no choice at all is no benchmark, as install_runtime reads it)
        order.append(("install", kw["kv_cache"], kw["arch"], bool(kw.get("bench") and kw["bench"].allowed)))
        result = SimpleNamespace(root="/home/dietpi/.local/share/llm-framework/runtime", previous=None,
                                 notes=["start the server with: ..."], unit_started=False, older=[])
        return steps.InstallOutcome(result, report, ok)

    def tested(output_dirs, device, profile, rep, digest, build_id, source, kv):
        order.append(("tested", device.device_id, digest, kv))
        return [Path("output/build-1")] if digest else []

    monkeypatch.setattr(person_steps, "connect_stored", connect)
    monkeypatch.setattr(remote, "RemoteShell", lambda client: client)
    monkeypatch.setattr(remote, "read_facts", facts)
    monkeypatch.setattr(steps, "choose_bench_model", choose)
    monkeypatch.setattr(steps, "install_runtime", install)
    monkeypatch.setattr(service, "record_board_test", tested)
    return build


# --- `llmcf device runtime deploy` ------------------------------------------------------------------------------------

@pytest.mark.usefixtures("keep_logger_levels")
class TestCliRuntimeDeploy:

    def deploy(self, monkeypatch, order, *args, uid=1000, ok=True, input=None):
        from orchestrator import cli as cli_module
        from test_cli_builds import invoke_plain
        from test_device_runtime import cli_state, stored_device
        from orchestrator.runtime.device.trusted import TrustedDevices
        runtime_stand_ins(monkeypatch, order, uid=uid, ok=ok)
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: PIN)
        consents = []
        monkeypatch.setattr(cli_module, "_record_consent", lambda ctx, tool, arguments, detail: (
            order.append(("consent", tool)), consents.append(arguments)))
        _store, device_id = stored_device(ssh_user="dietpi")
        state = cli_state()
        real_facts = __import__("orchestrator.runtime.device.remote", fromlist=["read_facts"]).read_facts

        def facts_after_the_record(shell):
            order.append(("trusted", TrustedDevices(state).get(device_id) is not None))
            return real_facts(shell)

        monkeypatch.setattr("orchestrator.runtime.device.remote.read_facts", facts_after_the_record)
        res = invoke_plain("device", "runtime", "deploy", "ZERO 3W", *args, input=input)
        return res, device_id, state, consents

    def test_the_steps_in_their_order(self, monkeypatch):
        from orchestrator.runtime.device.trusted import TrustedDevices
        order = []
        res, device_id, state, consents = self.deploy(monkeypatch, order, "--bench-model", "models/own.gguf",
                                                      "--allow-untrusted-model")
        assert res.code == 0, res.out
        assert order == [("connect", "dietpi", None, "runtime_deploy"), ("trusted", True), "facts",
                         ("consent", "device_runtime_untrusted_model"), ("bench", "models/own.gguf", True),
                         ("install", auto_kv(), "arm64", True), "closed", ("tested", device_id, "d" * 64, auto_kv())]
        assert consents == [{"device": device_id, "model_sha256": "d" * 64}]
        device = TrustedDevices(state).get(device_id)
        assert device.trusted_via == "cli" and device.user == "dietpi" and device.paired
        out = " ".join(res.out.split())
        assert out.index("Deployed") < out.index("version: passed") < out.index("tested on")
        assert "KV cache:" in out and "127.0.0.1:8080" in out

    def test_without_the_persons_yes_the_untrusted_model_does_not_run(self, monkeypatch):
        order = []
        res, device_id, _state, consents = self.deploy(monkeypatch, order, "--bench-model", "models/own.gguf")
        assert res.code == 0, res.out
        assert ("bench", "models/own.gguf", False) in order and consents == []
        assert not [o for o in order if isinstance(o, tuple) and o[0] == "tested"], \
            "the model did not run: nothing is recorded as tested"

    def test_root_on_the_board_is_refused_before_the_benchmark_question(self, monkeypatch):
        order = []
        res, _device_id, _state, _consents = self.deploy(monkeypatch, order, "--bench-model", "models/own.gguf",
                                                         uid=0)
        assert res.code == 1 and "never runs as root" in res.out
        assert [o for o in order if isinstance(o, tuple) and o[0] in ("bench", "install", "tested")] == []
        assert order[-1] == "closed", "the session is closed"

    def test_a_build_that_fails_its_checks_is_not_recorded_as_tested(self, monkeypatch):
        order = []
        res, _device_id, _state, _consents = self.deploy(monkeypatch, order, ok=False)
        assert res.code == 1 and "did not pass its checks" in res.out
        assert not [o for o in order if isinstance(o, tuple) and o[0] == "tested"]


# --- the Boards window's Deploy runtime... ------------------------------------------------------------------------------

def test_the_boards_window_refuses_root_on_the_board_before_the_benchmark_question(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from test_boards_window import Boxes, framework, stored, wait
    from orchestrator.gui.boards_window import BoardsWindow
    fw = framework(tmp_path)
    Path(fw.config.user_state_dir).mkdir(parents=True)
    window = BoardsWindow(fw, lang="en")
    try:
        order = []
        runtime_stand_ins(monkeypatch, order, uid=0)
        stored(Path(fw.config.user_state_dir), ssh_user="dietpi")
        window.reload()
        boxes = Boxes(monkeypatch, answer="yes")
        window.deploy(window.entries[0], RK3566, None, None, "models/own.gguf")
        wait(qapp, window)
        wait(qapp, window)
        assert ("connect", "dietpi", None, "runtime_deploy") in order and "facts" in order
        assert not [o for o in order if isinstance(o, tuple) and o[0] in ("bench", "install", "tested")]
        assert "closed" in order and boxes.questions() == []
        assert "root" in window.output.toPlainText()
        rows = fw.tool_registry.audit.rows
        assert [r["tool"] for r in rows] == ["device_trusted"], "the board is remembered right after the login"
        assert rows[0]["extra"]["confirm_channel"] == "framework-gui"
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_boards_window_steps_in_their_order(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from test_boards_window import Boxes, framework, stored, wait
    from orchestrator.Core import artifact_output
    from orchestrator.gui.boards_window import BoardsWindow
    fw = framework(tmp_path)
    Path(fw.config.user_state_dir).mkdir(parents=True)
    window = BoardsWindow(fw, lang="en")
    try:
        order = []
        runtime_stand_ins(monkeypatch, order)
        monkeypatch.setattr(artifact_output, "artifact_folders", lambda framework: [Path("output")])
        stored(Path(fw.config.user_state_dir), ssh_user="dietpi")
        window.reload()
        Boxes(monkeypatch, answer="yes")
        window.deploy(window.entries[0], RK3566, None, "pw-once", "models/own.gguf")
        for _ in range(3):
            wait(qapp, window)
        device_id = "0123456789abcdef"
        assert order == [("connect", "dietpi", "pw-once", "runtime_deploy"), "facts",
                         ("bench", "models/own.gguf", True), ("install", auto_kv(), "arm64", True), "closed",
                         ("tested", device_id, "d" * 64, auto_kv())], order
        tools = [r["tool"] for r in fw.tool_registry.audit.rows]
        assert tools == ["device_trusted", "device_runtime_untrusted_model"], "remembered, then the consent"
        text = window.output.toPlainText()
        assert "Deployed build bbbbbbbbbbbbbbbb" in text and "with 1 build(s) of this model" in text
    finally:
        window.deleteLater()
        qapp.processEvents()


# --- the AI's deploy_device_runtime (service.deploy) ---------------------------------------------------------------

@pytest.fixture
def ai_board(tmp_path, monkeypatch):
    """A trusted board, its stand-in session (service.connect) and the AI's deploy with its build ready."""
    from orchestrator.runtime.device import service, steps
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.runtime.device.trusted import TrustedDevices
    state = tmp_path / "state"
    key = FakeHostKey()
    device = TrustedDevices(state).record(HOST, 22, "dietpi", key, via="cli", paired=True, label="Shelf board")
    order = []
    build = runtime_stand_ins(monkeypatch, order)
    packaged = SimpleNamespace(path=Path("/x.tar.gz"), sha256="a" * 64)
    monkeypatch.setattr(service, "ensure_build", lambda state_dir, d, profile, build_id, **kw: (
        order.append("ensure_build"), (None, build, packaged))[1])
    monkeypatch.setattr(service, "pinned_source", lambda: PIN)

    class Session:
        def __init__(self):
            self.key = key

        def close(self):
            order.append("closed")

    def connect(d, state_dir, user, fp, on_client=None, step="", *, cancel=None, on_event=None, wait_seconds=None):
        order.append(("connect", user, fp, step))
        session = Session()
        if on_client is not None:
            on_client(session)
        return session

    monkeypatch.setattr(service, "connect", connect)
    monkeypatch.setattr(steps, "choose_bench_model", lambda *a, **k: pytest.fail("the AI's deploy runs no benchmark"))
    settings = DeploySettings(ssh_user="dietpi")
    return SimpleNamespace(state=state, device=device, order=order, settings=settings, records=TrustedDevices(state))


class TestServiceDeploy:

    def test_the_ai_deploy_logs_in_with_the_trusted_key_and_remembers_nothing(self, ai_board):
        from orchestrator.runtime.device import service
        before = ai_board.records.list()
        out = service.deploy(ai_board.state, ai_board.device, RK3566, ai_board.settings, ai_board.device.fingerprint,
                             "b" * 64)
        assert ai_board.order == ["ensure_build", ("connect", "dietpi", ai_board.device.fingerprint,
                                                   "runtime_deploy"), ("install", auto_kv(), "arm64", False), "closed"]
        assert "facts" not in ai_board.order, "the root refusal is the install's own (deploy_runtime)"
        assert ai_board.records.list() == before, "no AI path writes a board's trusted record"
        assert out == {"activated": True, "build_id": "b" * 64, "install": "user", "previous": None,
                       "older_builds_kept": 0, "checks": [{"name": "version", "result": "passed", "detail": "b1"},
                                                          {"name": "smoke", "result": "passed",
                                                           "detail": "llama-bench ran"}],
                       "notes": ["start the server with: ..."]}

    def test_a_cancel_before_the_install_stops_it_and_closes_the_session(self, ai_board):
        from orchestrator.runtime.device import service
        asked = []

        def cancelled():
            asked.append(len(ai_board.order))
            return any(isinstance(o, tuple) and o[0] == "connect" for o in ai_board.order)

        with pytest.raises(service.DeviceRuntimeError) as e:
            service.deploy(ai_board.state, ai_board.device, RK3566, ai_board.settings, ai_board.device.fingerprint,
                           "b" * 64, cancelled=cancelled)
        assert "The deploy was cancelled." == str(e.value)
        assert not [o for o in ai_board.order if isinstance(o, tuple) and o[0] == "install"]
        assert ai_board.order[-1] == "closed"

    def test_a_failure_on_the_board_is_the_devices_words_kept_apart(self, ai_board, monkeypatch):
        from orchestrator.runtime.device import service, steps
        from orchestrator.runtime.device.remote import RemoteError

        def root(shell, **kw):
            raise RemoteError("the SSH user on the device is root: the runtime never runs as root. Deploy as an "
                              "unprivileged user (llm-cli device settings DEVICE --user NAME)")

        monkeypatch.setattr(steps, "install_runtime", root)
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.deploy(ai_board.state, ai_board.device, RK3566, ai_board.settings, ai_board.device.fingerprint,
                           "b" * 64)
        assert e.value.code == "failed" and "Shelf board" in str(e.value) and HOST not in str(e.value)
        assert "never runs as root" in e.value.external_text[0]
        assert ai_board.order[-1] == "closed"


def test_the_threads_of_a_test_never_outlive_it():
    """Guard of this file: no stand-in leaves a thread running (the Boards window's steps end in each test)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and [t for t in threading.enumerate() if t.name == "boards-step"]:
        time.sleep(0.05)
    assert not [t for t in threading.enumerate() if t.name == "boards-step"]


def test_no_framework_code_reads_the_shared_package_error():
    """The reason a package could not be made comes back with the call (make_package raises it, deploy_build returns
    it as package_error): no framework module reads DeploymentManager.last_package_error, which every caller of one
    manager shares (it stays for older callers of create_deployment_package only)."""
    from conftest import REPO_ROOT
    readers = [p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / "orchestrator").rglob("*.py")
               if "last_package_error" in p.read_text(encoding="utf-8")]
    assert readers == ["orchestrator/Core/deployment_manager.py"], readers


def test_the_ai_s_deploy_hands_the_flow_no_person_only_hook():
    """The board's trusted record (`remember`), the person's benchmark question (`choose_bench`) and the 'tested on'
    record are the person's hooks of steps.deploy_runtime_flow: the AI-reachable service.py builds its DeployHooks
    without them (and without **kwargs that could carry them), and the shared steps.py reaches no person step."""
    import ast
    from conftest import REPO_ROOT
    tree = ast.parse((REPO_ROOT / "orchestrator/runtime/device/service.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == "DeployHooks"]
    assert calls, "service.deploy runs the one flow"
    for call in calls:
        names = {k.arg for k in call.keywords}
        assert None not in names and not call.args, "every hook is named"
        assert not names & {"remember", "choose_bench", "tested_on"}, names
        assert any(k.arg == "refuse_root" and isinstance(k.value, ast.Constant) and k.value.value is False
                   for k in call.keywords), "the AI's root refusal stays the install's own (its words, as before)"
    steps_tree = ast.parse((REPO_ROOT / "orchestrator/runtime/device/steps.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(steps_tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(steps_tree) if isinstance(n, ast.Import) for a in n.names}
    imported_names = {a.name for n in ast.walk(steps_tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not [m for m in imported if m and ("person_steps" in m or "pairing" in m)], imported
    assert not imported_names & {"person_steps", "pairing"}, imported_names
