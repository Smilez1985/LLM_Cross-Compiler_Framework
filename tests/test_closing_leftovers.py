"""Leftovers of the closing-pass review (items 82-85).

- 82: a sandbox benchmark mounts a private file that is exactly the checked model: a hard link next to the model, else
  in the framework's own cache or state folder on the same file system, else a copy hashed again; an honest reason code
  when nothing works; the private folder is removed when the run ends, also on failure and cancel; a cancel kills the
  container.
- 83: a recipe's calibration dataset is used only as a copy in the build's own folder whose bytes have the pinned
  sha256, so a file swapped between the check and the use is never used.
- 84: paths with spaces become correct labels or file names, and never leave a piece of the user name or a host path.
- 85: a deploy's end entry is written also when recording it in the device list fails; the board runtime jobs, the
  local runtime jobs and create_deploy_package write job-end entries linked to their start; the tools reach built
  models through a public BuildService method.
"""

import ast
import hashlib
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.tools.registry import ToolContext

POSIX = pytest.mark.skipif(os.name == "nt", reason="descriptor paths and hard links through /proc are POSIX")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# =====================================================================================================================
# 84: paths with spaces
# =====================================================================================================================

class TestPathsWithSpaces:

    def test_a_home_folder_with_a_space_leaves_no_part_of_the_user_name(self, monkeypatch):
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", "/home/Jane Doe")
        for text, expected in (("Cannot read /home/Jane Doe/private/notes.txt now", "Cannot read notes.txt now"),
                               ("home is /home/Jane Doe here", "home is the home folder here"),
                               ("bad uri file:///home/Jane Doe/private/a.bin", "bad uri file://a.bin")):
            out = label_paths(text, {})
            assert out == expected
            assert "Jane" not in out and "Doe" not in out

    def test_a_known_folder_with_a_space_gets_its_label(self, monkeypatch):
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", "/home/Jane Doe")
        roots = {"/srv/my out": "output", "/home/Jane Doe/My Models": "models"}
        assert label_paths("ok (/srv/my out/Rk/m.gguf)", roots) == "ok (output:Rk/m.gguf)"
        assert label_paths("load /home/Jane Doe/My Models/qwen 2/model.gguf failed", roots) == \
            "load models:qwen 2/model.gguf failed"
        assert label_paths("nothing in /srv/my out.", roots) == "nothing in the output folder."

    def test_a_quoted_path_with_spaces_is_replaced_whole(self):
        from orchestrator.tools.tools_runtime import label_paths
        text = "[Errno 2] No such file or directory: '/home/u/My Models/x.gguf'"
        assert label_paths(text, {}) == "[Errno 2] No such file or directory: 'x.gguf'"
        assert label_paths('cannot open "/data/my files/it\'s here.bin"', {}) == 'cannot open "it\'s here.bin"'

    def test_a_folder_with_a_space_on_another_computer(self):
        """A board's path cannot be checked here: the word after the space is clearly the rest of the path."""
        from orchestrator.tools.tools_runtime import label_paths
        out = label_paths("No model at /home/dietpi/My Models/x.gguf on the device", {})
        assert out == "No model at x.gguf on the device" and "Models" not in out
        assert label_paths(r"Bad file C:\Program Files\Vendor\x.bin", {}) == "Bad file x.bin"

    def test_a_path_with_spaces_that_exists_here(self, tmp_path, monkeypatch):
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", str(tmp_path))
        folder = tmp_path / "My Models" / "sub dir"
        folder.mkdir(parents=True)
        model = folder / "x y.gguf"
        model.write_bytes(b"GGUF")
        assert label_paths(f"Cannot read {model} now", {}) == "Cannot read x y.gguf now"
        assert label_paths(f"Cannot read {model} now", {str(tmp_path / "My Models"): "models"}) == \
            "Cannot read models:sub dir/x y.gguf now"

    def test_text_from_outside_cannot_probe_folders_outside_the_known_ones(self, tmp_path, monkeypatch):
        """Whether a longer path exists is looked up only inside a known folder or the home folder."""
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        outside = tmp_path / "elsewhere" / "a dir"
        outside.mkdir(parents=True)
        looked = []
        import orchestrator.security.paths as tr   # label_paths' home since chain c1-paths (tools_runtime re-exports it)
        monkeypatch.setattr(tr, "_path_exists", lambda p: looked.append(p) or True)
        label_paths(f"x {tmp_path}/elsewhere/a dir/y", {})
        assert looked == []
        label_paths(f"x {tmp_path}/home/a dir/y", {})
        assert looked == [f"{tmp_path}/home/a dir"]

    def test_a_windows_home_with_a_space(self, monkeypatch):
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", r"C:\Users\Jane Doe")
        out = label_paths(r"see C:\Users\Jane Doe and C:\Users\Jane Doe\Desktop\m.gguf", {})
        assert out == "see the home folder and m.gguf" and "Jane" not in out and "Doe" not in out

    def test_prose_addresses_and_urls_stay(self):
        from orchestrator.tools.tools_runtime import label_paths
        kept = "https://github.com/ggml-org/llama.cpp and 127.0.0.1:8080/health, os/arch, and/or"
        assert label_paths(kept, {}) == kept
        assert label_paths("copy /a/b.txt and/or /c/d.txt", {}) == "copy b.txt and/or d.txt"
        assert label_paths("see /a/b https://x.org/y", {}) == "see b https://x.org/y"
        assert label_paths("from /a/b to 127.0.0.1:8080/x", {}) == "from b to 127.0.0.1:8080/x"

    def test_a_board_tool_error_with_a_spaced_home_path_names_no_user(self, make_framework, monkeypatch):
        paramiko = pytest.importorskip("paramiko")
        import test_runtime_tools as rtt
        from orchestrator.runtime.device import service
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.utils import ssh_trust
        fm = make_framework()
        state = Path(fm.config.user_state_dir)
        key = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "192.168.1.50", 22, key)
        device = TrustedDevices(state).record("192.168.1.50", 22, "dietpi", key, via="cli", paired=True,
                                              label="Board")
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        monkeypatch.setenv("HOME", "/home/Jane Doe")

        def failing(*a, **k):
            raise service.DeviceRuntimeError("The probe of Board could not read /home/Jane Doe/My Models/x.gguf "
                                             "nor '/home/Jane Doe/other place/y.txt'.")
        monkeypatch.setattr(service, "probe", failing)
        rtt.allow(fm, "probe_remote_device")
        started = rtt.call(fm, "probe_remote_device", {"device_id": device.device_id}, confirm=rtt.asking(True))
        job = rtt.wait_job(fm, started.job_id)
        message = job.error["message"]
        assert job.state == "failed" and "x.gguf" in message and "'y.txt'" in message
        assert "Jane" not in message and "Doe" not in message and "Models" not in message and "place" not in message


# =====================================================================================================================
# 85: job ends in the audit log, public artifact lookup
# =====================================================================================================================

def job_ends(fm, tool):
    return [e for e in fm.tool_registry.audit.entries(500)
            if e.get("tool") == tool and str(e.get("detail") or "").startswith("job end:")]


def wait_end(fm, tool, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ends = job_ends(fm, tool)
        if ends:
            return ends
        time.sleep(0.05)
    return []


def started_entry(fm, tool):
    (start,) = [e for e in fm.tool_registry.audit.entries(500) if e.get("tool") == tool
                and e.get("outcome") == "started"]
    return start


class TestJobAudit:
    """The wrapper itself: every way out of a job's work is on record, linked to the call."""

    class Registry:
        def __init__(self):
            self.ends = []

        def record_job_end(self, name, arguments, ctx, outcome, detail, reason_code=""):
            self.ends.append((name, outcome, detail, reason_code))

    class Handle:
        def __init__(self, cancelled=False):
            self.job = SimpleNamespace(job_id="job_0123456789abcdef")
            self.cancelled = cancelled

    def test_ok_error_cancelled_and_an_unexpected_exception(self):
        from orchestrator.Core.job_service import JobCancelled
        from orchestrator.tools.job_audit import audited_job
        reg = self.Registry()
        run = audited_job(reg, "t", {}, None, lambda h: {"n": 2}, "device Board",
                          done=lambda out: ("ok", f"{out['n']} done", ""))
        assert run(self.Handle()) == {"n": 2}

        def refused(h):
            err = ValueError("text with /home/u/secret path")
            err.code = "needs_person"
            raise err
        with pytest.raises(ValueError):
            audited_job(reg, "t", {}, None, refused, "device Board")(self.Handle())

        def stopped(h):
            raise JobCancelled()
        with pytest.raises(JobCancelled):
            audited_job(reg, "t", {}, None, stopped, "device Board", cancelled=lambda: "cancelled; state unknown")(
                self.Handle())

        def broken(h):
            raise KeyError("x")
        with pytest.raises(KeyError):
            audited_job(reg, "t", {}, None, broken, "device Board")(self.Handle())
        assert reg.ends == [
            ("t", "ok", "job job_0123456789abcdef, device Board: 2 done", ""),
            ("t", "error", "job job_0123456789abcdef, device Board: failed (ValueError)", "needs_person"),
            ("t", "cancelled", "job job_0123456789abcdef, device Board: cancelled; state unknown", ""),
            ("t", "error", "job job_0123456789abcdef, device Board: failed (KeyError)", "failed")]
        assert not any("/home/u" in e[2] for e in reg.ends), "an error is named by its type, never its text"

    def test_a_result_after_a_cancel_and_a_broken_summary_are_on_record(self):
        from orchestrator.tools.job_audit import audited_job
        reg = self.Registry()
        audited_job(reg, "t", {}, None, lambda h: {}, "x")(self.Handle(cancelled=True))
        audited_job(reg, "t", {}, None, lambda h: {}, "x", done=lambda out: 1 / 0)(self.Handle())
        assert [e[1] for e in reg.ends] == ["cancelled", "ok"]
        assert "result is not used" in reg.ends[0][2] and "summary not available" in reg.ends[1][2]

    def test_a_failed_audit_write_never_fails_the_job(self):
        from orchestrator.tools.job_audit import audited_job

        class Broken:
            def record_job_end(self, *a, **k):
                raise OSError("disk full")
        assert audited_job(Broken(), "t", {}, None, lambda h: {"ok": 1}, "x")(self.Handle()) == {"ok": 1}


class TestDeployEnds:

    @pytest.fixture
    def deploy_env(self, make_framework, monkeypatch):
        pytest.importorskip("paramiko")
        import test_deploy_tools as dt
        from orchestrator.Core import hf_hub
        from orchestrator.tools.policy import ToolPolicy
        monkeypatch.setattr(hf_hub, "disk_check", lambda folder, needed, ledger=None: {
            "free": 500 * 10 ** 9, "reserve": 2 * 10 ** 9, "reserved": 0, "needed": needed, "fits": True,
            "fits_standing": True})
        fm = make_framework()
        fm.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fm.tool_registry.list()]))
        return fm, dt

    def test_a_deploy_whose_device_list_entry_fails_still_writes_its_end(self, deploy_env, monkeypatch):
        """Review: when note_deploy raised after a successful deploy_package, the job failed and no end entry was
        written, although the deploy had happened."""
        fm, dt = deploy_env
        from orchestrator.runtime.device.trusted import TrustedDevices
        device, key = dt.trust(fm)
        from orchestrator.utils import ssh_trust
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        fake = dt.FakeDeploy()
        monkeypatch.setattr(fm.deployment_manager if getattr(fm, "deployment_manager", None) is not None
                            else fm.get_component("deployment_manager"), "deploy_package", fake)
        pkg = dt.package(fm, dt.build(fm))

        def broken(self, *a, **k):
            raise OSError("the device list cannot be written")
        monkeypatch.setattr(TrustedDevices, "note_deploy", broken)
        started = dt.call(fm, "deploy_to_device", {"package_id": pkg["package_id"], "device_id": device.device_id},
                          confirm=dt.asking(True))
        job = dt.wait_job(fm, started.job_id)
        assert job.state == "done" and job.result["success"] is True and "could not record" in job.result["note"]
        (end,) = wait_end(fm, "deploy_to_device")
        assert end["outcome"] == "ok" and end["links"] == [started_entry(fm, "deploy_to_device")["id"]]
        assert "deployed" in end["detail"] and "not recorded in the device list" in end["detail"]

    def test_packaging_writes_its_end_linked_to_its_start(self, deploy_env):
        fm, dt = deploy_env
        pkg = dt.package(fm, dt.build(fm))
        (end,) = wait_end(fm, "create_deploy_package")
        assert end["outcome"] == "ok" and end["links"] == [started_entry(fm, "create_deploy_package")["id"]]
        assert pkg["name"] in end["detail"] and pkg["sha256"] in end["detail"] and "output:" in end["detail"]
        assert fm.tool_registry.audit.check().intact

    def test_a_failed_packaging_ends_as_error_with_its_reason_code(self, deploy_env, monkeypatch):
        fm, dt = deploy_env
        from orchestrator.Core.deployment_manager import DeployError
        manager = fm.deployment_manager if getattr(fm, "deployment_manager", None) is not None \
            else fm.get_component("deployment_manager")

        def full(*a, **k):
            raise DeployError(f"No space left in {Path.home()}/out", code="insufficient_resources")
        monkeypatch.setattr(manager, "create_package", full)
        started = dt.call(fm, "create_deploy_package", {"artifact_id": dt.build(fm)}, confirm=dt.asking(True))
        assert dt.wait_job(fm, started.job_id).state == "failed"
        (end,) = wait_end(fm, "create_deploy_package")
        assert end["outcome"] == "error" and end["reason_code"] == "insufficient_resources"
        assert str(Path.home()) not in end["detail"]


class TestRuntimeEnds:

    @pytest.fixture
    def board(self, make_framework, monkeypatch):
        paramiko = pytest.importorskip("paramiko")
        import test_runtime_tools as rtt
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.utils import ssh_trust
        fm = make_framework()
        state = Path(fm.config.user_state_dir)
        key = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "192.168.1.50", 22, key)
        device = TrustedDevices(state).record("192.168.1.50", 22, "dietpi", key, via="cli", paired=True,
                                              label="Living room board")
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
        return fm, rtt, device

    def test_a_board_probe_writes_its_end(self, board, monkeypatch):
        fm, rtt, device = board
        from orchestrator.runtime.device import service
        monkeypatch.setattr(service, "probe", lambda d, s, fp, on_client=None, on_event=None: {
            "probe_id": d.device_id, "tool": "python", "warnings": 2})
        rtt.allow(fm, "probe_remote_device")
        started = rtt.call(fm, "probe_remote_device", {"device_id": device.device_id}, confirm=rtt.asking(True))
        assert rtt.wait_job(fm, started.job_id).state == "done"
        (end,) = wait_end(fm, "probe_remote_device")
        assert end["outcome"] == "ok" and end["links"] == [started_entry(fm, "probe_remote_device")["id"]]
        assert "Living room board" in end["detail"] and "2 warnings" in end["detail"]

    def test_a_failed_board_probe_ends_as_error(self, board, monkeypatch):
        fm, rtt, device = board
        from orchestrator.runtime.device import service

        def failing(*a, **k):
            raise service.DeviceRuntimeError("unreachable", code="hardware_unavailable")
        monkeypatch.setattr(service, "probe", failing)
        rtt.allow(fm, "probe_remote_device")
        started = rtt.call(fm, "probe_remote_device", {"device_id": device.device_id}, confirm=rtt.asking(True))
        assert rtt.wait_job(fm, started.job_id).state == "failed"
        (end,) = wait_end(fm, "probe_remote_device")
        assert end["outcome"] == "error" and end["reason_code"] == "hardware_unavailable"

    def fake_device_service(self, monkeypatch, fm, device, deployed=True):
        from orchestrator.runtime.device import service
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        from orchestrator.runtime.device.settings import KvChoice
        settings = SimpleNamespace(ssh_user="dietpi", install="script", sudo_confirmed_at=None,
                                   model="/home/dietpi/My Models/x.gguf",
                                   kv_choice=lambda profile: KvChoice("f16", "unknown"))
        plan = SimpleNamespace(built=True, source_cached=True, build_id="d" * 64, builder={"base": "debian"},
                               target={"os": "linux", "arch": "arm64", "libc": "glibc", "libc_version": "2.36"})
        monkeypatch.setattr(service, "device_profile", lambda state, d: SimpleNamespace())
        monkeypatch.setattr(service, "deploy_settings", lambda state, d, profile: settings)
        monkeypatch.setattr(service, "plan_facts", lambda state, profile: plan)
        monkeypatch.setattr(service, "read_key", lambda d, state, step="": d.fingerprint)
        monkeypatch.setattr(service, "deploy", lambda *a, **k: {
            "activated": deployed, "build_id": "d" * 64, "install": "script", "previous": None,
            "checks": [{"name": "version", "result": "passed", "detail": "ok"},
                       {"name": "smoke", "result": "passed" if deployed else "failed", "detail": "-"}],
            "notes": []})
        monkeypatch.setattr(service, "benchmark", lambda *a, **k: {
            "ok": True, "trusted_model": False, "model_sha256": "e" * 64, "build_id": "d" * 64,
            "checks": [{"name": "bench", "result": "passed", "detail": "ok"}], "prompt_ts": 50.0, "gen_ts": 5.0})
        monkeypatch.setattr(DeviceRuntimeStore, "record", lambda self: {"build_id": "d" * 64})

    def test_a_board_runtime_deploy_writes_its_end(self, board, monkeypatch):
        fm, rtt, device = board
        self.fake_device_service(monkeypatch, fm, device, deployed=False)
        rtt.allow(fm, "deploy_device_runtime")
        started = rtt.call(fm, "deploy_device_runtime", {"device_id": device.device_id}, confirm=rtt.asking(True))
        assert rtt.wait_job(fm, started.job_id).state == "done"
        (end,) = wait_end(fm, "deploy_device_runtime")
        assert end["outcome"] == "error" and end["reason_code"] == "failed"
        assert end["links"] == [started_entry(fm, "deploy_device_runtime")["id"]]
        assert "not activated: 1 checks passed, 1 failed" in end["detail"] and "Living room board" in end["detail"]

    def test_a_board_benchmark_writes_its_end_without_the_device_path(self, board, monkeypatch):
        fm, rtt, device = board
        self.fake_device_service(monkeypatch, fm, device)
        rtt.allow(fm, "run_device_benchmark")
        started = rtt.call(fm, "run_device_benchmark", {"device_id": device.device_id}, confirm=rtt.asking(True))
        assert rtt.wait_job(fm, started.job_id).state == "done"
        (end,) = wait_end(fm, "run_device_benchmark")
        assert end["outcome"] == "ok" and end["links"] == [started_entry(fm, "run_device_benchmark")["id"]]
        assert "untrusted" in end["detail"] and "50.0 t/s" in end["detail"] and "My Models" not in end["detail"]

    def test_a_cancelled_board_runtime_deploy_ends_as_cancelled(self, board, monkeypatch):
        fm, rtt, device = board
        from orchestrator.runtime.device import service
        self.fake_device_service(monkeypatch, fm, device)
        entered = threading.Event()

        def slow(*a, cancelled=None, **k):
            entered.set()
            while not cancelled():
                time.sleep(0.02)
            raise service.DeviceRuntimeError("stopped")
        monkeypatch.setattr(service, "deploy", slow)
        rtt.allow(fm, "deploy_device_runtime")
        started = rtt.call(fm, "deploy_device_runtime", {"device_id": device.device_id}, confirm=rtt.asking(True))
        assert entered.wait(5)
        fm.tool_registry.call("cancel_job", {"job_id": started.job_id}, ToolContext("ditto", session="s"))
        (end,) = wait_end(fm, "deploy_device_runtime")
        assert end["outcome"] == "cancelled" and "device state is unknown" in end["detail"]

    def test_a_local_benchmark_writes_its_end(self, make_framework, tmp_path, monkeypatch):
        import test_runtime_tools as rtt
        from orchestrator.Core import hf_hub
        from orchestrator.runtime import bench as bench_module
        monkeypatch.setattr(hf_hub, "disk_check", lambda folder, needed, ledger=None: {
            "free": 500 * 10 ** 9, "reserve": 2 * 10 ** 9, "reserved": 0, "needed": needed, "fits": True,
            "fits_standing": True})
        fm = make_framework()
        seen = {}

        def fake_run(self, model, **kw):
            seen["cache_dir"] = self.cache_dir
            return bench_module.BenchRun(run_id="bench_000000000001", kind=kw["kind"], model=kw["label"],
                                         sha256=kw["sha256"], trusted=kw["trusted"], where="sandbox",
                                         build_id="b" * 64, backend="cpu", prompt_ts=100.0, gen_ts=10.0)
        monkeypatch.setattr(bench_module.BenchRunner, "run", fake_run)
        build = SimpleNamespace(build_id="b" * 64, backend="cpu")
        monkeypatch.setattr(bench_module.BenchRunner, "bench_build", lambda self, sandbox=False: (build, Path("x")))
        aid = rtt.TestBenchmark().gguf_artifact(fm)
        rtt.allow(fm, "run_benchmark")
        started = rtt.call(fm, "run_benchmark", {"kind": "artifact_speed", "artifact_id": aid, "sandbox": True},
                           confirm=rtt.asking(True))
        assert rtt.wait_job(fm, started.job_id).state == "done"
        (end,) = wait_end(fm, "run_benchmark")
        assert end["outcome"] == "ok" and end["links"] == [started_entry(fm, "run_benchmark")["id"]]
        assert "in the Docker sandbox" in end["detail"] and "10.0 t/s" in end["detail"]
        assert seen["cache_dir"] is not None, "the sandbox's private model file may use the framework's cache folder"


def test_the_tools_reach_built_models_only_through_public_build_service_methods():
    for rel in ("orchestrator/tools/tools_deploy.py", "orchestrator/tools/tools_runtime.py",
                "orchestrator/tools/tools_community.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(text)
        private = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                          and isinstance(n.value, ast.Name) and n.value.id == "service" and n.attr.startswith("_")})
        assert private == [], f"{rel} reaches into BuildService: {private}"
        assert '"_artifact"' not in text, f"{rel} looks up the private BuildService._artifact by name"


def test_the_public_artifact_lookup(tmp_path):
    from orchestrator.Core.build_service import BuildService, BuildServiceError
    service = BuildService.__new__(BuildService)
    entry = ("a_0123456789abcdef", "output:Rk/m", {"path": str(tmp_path)})
    service._artifacts = lambda: [entry]
    assert service.artifact_entry("a_0123456789abcdef") == entry
    with pytest.raises(BuildServiceError) as e:
        service.artifact_entry("a_ffffffffffffffff")
    assert e.value.code == "unknown_id"


# =====================================================================================================================
# 82: the sandbox benchmark's private model file
# =====================================================================================================================

class FakeEngine:
    def __init__(self, rc=0, on_run=None):
        self.rc, self.on_run, self.runs = rc, on_run, []

    def unavailable(self):
        return None

    def image_exists(self, tag):
        return True

    def run(self, image, command, *, mounts, env, options, log, timeout, stop=None):
        source = next(m.source for m in mounts if m.target == "/models/model.gguf")
        self.runs.append({"source": Path(source), "bytes": Path(source).read_bytes(),
                          "links": os.stat(source).st_nlink, "stop": stop})
        if self.on_run is not None:
            self.on_run(Path(source), stop)
        log.write_bytes(b'[{"n_prompt": 512, "n_gen": 0, "avg_ts": 100.0}, '
                        b'{"n_prompt": 0, "n_gen": 128, "avg_ts": 10.0}]')
        return self.rc


@pytest.fixture
def sandbox_bench(tmp_path, monkeypatch):
    from orchestrator.runtime import bench, server_policy
    monkeypatch.setattr(server_policy, "check_command", lambda argv: None)
    models = tmp_path / "models"
    models.mkdir()
    model = models / "m.gguf"
    model.write_bytes(b"GGUF checked model bytes")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    build = SimpleNamespace(build_id="b" * 64, backend="cpu", bin_dir=bin_dir,
                            manifest={"builder_image": "llm-framework/llama-builder:debian-0123456789ab"})

    def make(engine, cache=True):
        runner = bench.BenchRunner(tmp_path / "state", engine=engine,
                                   cache_dir=(tmp_path / "cache") if cache else None)
        runner.bench_build = lambda sandbox=False: (build, Path("x"))
        return runner

    def read_only(*folders):
        """Folders that cannot hold a private folder (as root a chmod would not stop the test)."""
        real = bench.tempfile.mkdtemp
        blocked = {str(Path(f)) for f in folders}

        def mkdtemp(suffix=None, prefix=None, dir=None):
            if dir is not None and str(Path(dir)) in blocked:
                raise PermissionError(13, "Read-only file system")
            return real(suffix, prefix, dir)
        monkeypatch.setattr(bench.tempfile, "mkdtemp", mkdtemp)
    return SimpleNamespace(bench=bench, model=model, digest=sha(model.read_bytes()), make=make, tmp=tmp_path,
                           read_only=read_only)


def leftovers(*folders):
    found = []
    for folder in folders:
        if Path(folder).is_dir():
            found += [p.name for p in Path(folder).iterdir() if p.name.startswith(".llm-bench-")]
    return found


@POSIX
class TestSandboxModelFile:

    def run(self, env, runner, **kw):
        return runner.run(env.model, kind="artifact_speed", label="output:Rk/m", trusted=False, sha256=env.digest,
                          sandbox=True, **kw)

    def test_a_link_next_to_the_model_as_before(self, sandbox_bench):
        env = sandbox_bench
        engine = FakeEngine()
        assert self.run(env, env.make(engine)).gen_ts == 10.0
        (run,) = engine.runs
        assert run["source"].parent.parent == env.model.parent and run["links"] == 2, "a hard link, no copy"
        assert leftovers(env.model.parent) == []

    def test_a_read_only_model_folder_links_into_the_framework_s_cache_folder(self, sandbox_bench):
        """Review: a read-only model folder failed the job with a generic error."""
        env = sandbox_bench
        env.read_only(env.model.parent)
        engine = FakeEngine()
        assert self.run(env, env.make(engine)).gen_ts == 10.0
        (run,) = engine.runs
        assert run["source"].parent.parent == env.tmp / "cache" / "bench"
        assert run["links"] == 2 and run["bytes"] == b"GGUF checked model bytes"
        assert leftovers(env.tmp / "cache" / "bench") == [], "the extra link is removed when the run ends"

    def test_without_a_cache_folder_the_state_folder_is_used(self, sandbox_bench):
        env = sandbox_bench
        env.read_only(env.model.parent)
        engine = FakeEngine()
        self.run(env, env.make(engine, cache=False))
        assert engine.runs[0]["source"].parent.parent == env.tmp / "state" / "benchmarks" / "pinned"

    def test_without_a_link_a_verified_copy_of_the_checked_bytes(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        monkeypatch.setattr(env.bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        engine = FakeEngine()
        real_open = env.bench.open_pinned

        def open_then_swap(model, digest, label):
            fd = real_open(model, digest, label)
            other = model.with_name("other.gguf")
            other.write_bytes(b"GGUF another model")
            os.replace(other, model)          # the path names another file from now on
            return fd
        monkeypatch.setattr(env.bench, "open_pinned", open_then_swap)
        self.run(env, env.make(engine))
        (run,) = engine.runs
        assert run["bytes"] == b"GGUF checked model bytes" and run["links"] == 1, "a copy of the checked file"
        assert run["source"].parent.parent == env.tmp / "cache" / "bench"
        assert leftovers(env.tmp / "cache" / "bench") == []

    def test_a_copy_whose_bytes_changed_in_place_never_runs(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        monkeypatch.setattr(env.bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        real_open = env.bench.open_pinned

        def open_then_write(model, digest, label):
            fd = real_open(model, digest, label)
            with open(model, "r+b") as f:     # the same file, changed after its hash was taken
                f.write(b"EVIL")
            return fd
        monkeypatch.setattr(env.bench, "open_pinned", open_then_write)
        engine = FakeEngine()
        with pytest.raises(env.bench.BenchError) as e:
            self.run(env, env.make(engine))
        assert "another sha256" in str(e.value) and engine.runs == []
        assert leftovers(env.tmp / "cache" / "bench", env.model.parent) == []

    def test_nothing_writable_is_an_honest_needs_person(self, sandbox_bench):
        env = sandbox_bench
        env.read_only(env.model.parent, env.tmp / "cache" / "bench", env.tmp / "state" / "benchmarks" / "pinned")
        engine = FakeEngine()
        with pytest.raises(env.bench.BenchError) as e:
            self.run(env, env.make(engine))
        assert e.value.code == "needs_person" and "writable" in str(e.value) and engine.runs == []

    def test_no_room_for_a_copy_is_insufficient_resources(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        monkeypatch.setattr(env.bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        monkeypatch.setattr(env.bench.shutil, "disk_usage", lambda p: SimpleNamespace(total=10, used=10, free=0))
        engine = FakeEngine()
        with pytest.raises(env.bench.BenchError) as e:
            self.run(env, env.make(engine))
        assert e.value.code == "insufficient_resources" and "GB needed" in str(e.value) and engine.runs == []

    def test_the_private_folder_goes_on_failure_and_on_an_exception(self, sandbox_bench):
        env = sandbox_bench
        env.read_only(env.model.parent)
        with pytest.raises(env.bench.BenchError):
            self.run(env, env.make(FakeEngine(rc=1)))

        def boom(source, stop):
            raise RuntimeError("docker went away")
        with pytest.raises(RuntimeError):
            self.run(env, env.make(FakeEngine(on_run=boom)))
        assert leftovers(env.tmp / "cache" / "bench") == []

    def test_a_cancel_stops_the_container_and_removes_the_private_folder(self, sandbox_bench):
        env = sandbox_bench
        env.read_only(env.model.parent)
        stop = threading.Event()
        engine = FakeEngine(on_run=lambda source, s: stop.set())
        with pytest.raises(env.bench.BenchStopped):
            self.run(env, env.make(engine), stop=stop)
        assert engine.runs[0]["stop"] is stop, "the engine gets the stop event to kill the container"
        assert leftovers(env.tmp / "cache" / "bench") == []

    def test_a_crash_s_leftover_is_removed_by_a_later_run(self, sandbox_bench):
        env = sandbox_bench
        stale = env.tmp / "cache" / "bench" / ".llm-bench-old"
        stale.mkdir(parents=True)
        (stale / "model.gguf").write_bytes(b"x")
        old = time.time() - env.bench.PIN_STALE_SECONDS - 60
        os.utime(stale, (old, old))
        fresh = env.tmp / "cache" / "bench" / ".llm-bench-running"
        fresh.mkdir()
        self.run(env, env.make(FakeEngine()))
        assert not stale.exists() and fresh.exists(), "only folders older than any run are removed"


def test_the_docker_engine_kills_the_container_on_stop(tmp_path):
    from orchestrator.runtime.build.executor import DockerEngine

    class Container:
        def __init__(self):
            self.killed = threading.Event()
            self.removed = False

        def start(self):
            pass

        def kill(self):
            self.killed.set()

        def logs(self, stream=True, follow=True):
            while not self.killed.is_set():
                time.sleep(0.02)
            yield b"killed\n"

        def wait(self, timeout=60):
            return {"StatusCode": 137}

        def remove(self, force=True):
            self.removed = True

    container = Container()
    client = SimpleNamespace(containers=SimpleNamespace(create=lambda **kw: container))
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    started = time.monotonic()
    rc = DockerEngine(client).run("img", ["x"], mounts=[], env={}, options={}, log=tmp_path / "log", timeout=600,
                                  stop=stop)
    assert rc == 137 and container.killed.is_set() and container.removed and time.monotonic() - started < 10


# =====================================================================================================================
# 83: the recipe's calibration dataset, checked and used as one
# =====================================================================================================================

@pytest.fixture
def engine(tmp_path, monkeypatch):
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    return BuildEngine(ConfigManager(tmp_path / "configs"))


def dataset_config(engine, path, digest, **overrides):
    from orchestrator.Core.builder import BuildConfiguration, ModelFormat
    values = dict(build_id="b1", timestamp="t", model_source="HuggingFaceTB/SmolLM2-135M-Instruct",
                  target_arch="Rockchip", target_format=ModelFormat.RKLLM, output_dir="out", quantization="w8a8",
                  target_board="rk3588", dataset_path=str(path), dataset_sha256=digest)
    values.update(overrides)
    (engine.cache_dir / "builds" / values["build_id"]).mkdir(parents=True, exist_ok=True)
    return BuildConfiguration(**values)


def progress():
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    return BuildProgress("b1", BuildStatus.QUEUED, "")


class TestRecipeDataset:

    CHECKED = b"calibration text the recipe pins\n"

    def test_a_dataset_swapped_between_the_check_and_the_use_is_never_used(self, engine, tmp_path):
        """Review: start_recipe_build checked the sha256 at commit, but the build opened the path again later."""
        from orchestrator.Core.build_service import BuildService
        ds = tmp_path / "calib.txt"
        ds.write_bytes(self.CHECKED)
        recipe = SimpleNamespace(id="r", options=SimpleNamespace(dataset=SimpleNamespace(name="calib.txt",
                                                                                       sha256=sha(self.CHECKED))))
        BuildService.check_recipe_dataset(recipe, str(ds))          # the check passes
        (tmp_path / "evil.txt").write_bytes(b"something else entirely\n")
        os.replace(tmp_path / "evil.txt", ds)                       # the swap
        config = dataset_config(engine, ds, sha(self.CHECKED))
        with pytest.raises(ValueError) as e:
            engine._stage_dataset(config, progress())
        assert "not the file that was checked" in str(e.value)
        assert config.dataset_path == str(ds) and not config.dataset_source
        assert list((engine.cache_dir / "builds" / "b1" / "dataset").iterdir()) == []

    def test_the_build_mounts_the_checked_copy_even_if_the_file_changes_afterwards(self, engine, tmp_path):
        ds = tmp_path / "calib.txt"
        ds.write_bytes(self.CHECKED)
        config = dataset_config(engine, ds, sha(self.CHECKED))
        engine._stage_dataset(config, progress())
        ds.write_bytes(b"changed after the copy\n")
        vols, inner = engine._dataset_mount(config)
        ((host, bind),) = vols.items()
        assert inner == "/build-cache/dataset.txt" and bind == {"bind": inner, "mode": "ro"}
        assert Path(host).read_bytes() == self.CHECKED and Path(host) != ds
        assert Path(host).parent == engine.cache_dir / "builds" / "b1" / "dataset"

    def test_an_rknn_sample_list_keeps_its_folder_with_the_checked_list_laid_over(self, engine, tmp_path):
        from orchestrator.Core.builder import ModelFormat
        folder = tmp_path / "samples"
        folder.mkdir()
        (folder / "img1.jpg").write_bytes(b"jpg")
        ds = folder / "list.txt"
        ds.write_bytes(b"img1.jpg\n")
        config = dataset_config(engine, ds, sha(b"img1.jpg\n"), target_format=ModelFormat.RKNN, quantization="i8")
        engine._stage_dataset(config, progress())
        vols, inner = engine._dataset_mount(config)
        copy = engine.cache_dir / "builds" / "b1" / "dataset" / "list.txt"
        assert inner == "/build-cache/dataset/list.txt"
        assert vols == {str(folder.resolve()): {"bind": "/build-cache/dataset", "mode": "ro"},
                        str(copy): {"bind": "/build-cache/dataset/list.txt", "mode": "ro"}}

    @POSIX
    def test_a_link_in_place_of_the_dataset_is_refused(self, engine, tmp_path):
        target = tmp_path / "real.txt"
        target.write_bytes(self.CHECKED)
        ds = tmp_path / "calib.txt"
        ds.symlink_to(target)
        with pytest.raises(ValueError) as e:
            engine._stage_dataset(dataset_config(engine, ds, sha(self.CHECKED)), progress())
        assert "cannot be opened" in str(e.value)

    def test_without_a_pin_nothing_is_copied(self, engine, tmp_path):
        ds = tmp_path / "calib.txt"
        ds.write_bytes(self.CHECKED)
        config = dataset_config(engine, ds, None)
        engine._stage_dataset(config, progress())
        assert config.dataset_path == str(ds) and not config.dataset_source

    def test_the_orchestrator_hands_the_pin_to_the_build(self):
        from orchestrator.Core.builder import BuildStatus, ModelFormat, OptimizationLevel
        from orchestrator.Core.orchestrator import BuildJob, BuildRequest, LLMOrchestrator, PriorityLevel, WorkflowType
        req = BuildRequest(request_id="req_1", workflow_type=WorkflowType.SIMPLE_CONVERSION,
                           priority=PriorityLevel.NORMAL, models=["m"], targets=["Rockchip"],
                           target_formats=[ModelFormat.RKLLM], optimization_level=OptimizationLevel.BALANCED,
                           quantization_options=["w8a8"], parallel_builds=False, output_base_dir="out",
                           dataset_path="/data/calib.txt", dataset_sha256="d" * 64)
        job = BuildJob("j1", "m", "Rockchip", ModelFormat.RKLLM, OptimizationLevel.BALANCED, "w8a8", "out",
                       BuildStatus.QUEUED)
        config = LLMOrchestrator._map_job_to_config(SimpleNamespace(config=SimpleNamespace()), job, req)
        assert config.dataset_sha256 == "d" * 64 and config.dataset_path == "/data/calib.txt"
        req.dataset_generated = True
        assert LLMOrchestrator._map_job_to_config(SimpleNamespace(config=SimpleNamespace()), job, req) \
            .dataset_sha256 is None, "the shipped calibration text carries no recipe pin"

    def test_start_recipe_hands_the_pinned_sha256_to_the_build(self, monkeypatch, tmp_path):
        """The recipe path: the request the build service queues carries the dataset's pin."""
        from orchestrator.Core.build_service import BuildService
        ds = tmp_path / "calib.txt"
        ds.write_bytes(self.CHECKED)
        named = SimpleNamespace(name="calib.txt", sha256=sha(self.CHECKED))
        recipe = SimpleNamespace(id="r", options=SimpleNamespace(dataset=named))
        request = SimpleNamespace(model="m", revision="c" * 40, dataset_path=str(ds))
        service = BuildService.__new__(BuildService)
        validated = SimpleNamespace(dataset_path=str(ds), target="t", format="gguf", board="b", quantization="q")
        monkeypatch.setattr(service, "recipe_request", lambda rid, dataset_path, use_gpu: (request, recipe, None),
                            raising=False)
        monkeypatch.setattr(service, "_validated", lambda req, local: (validated, None), raising=False)
        seen = {}
        monkeypatch.setattr(service, "start_build", lambda req, **kw: seen.update(kw) or "handle", raising=False)
        assert service.start_recipe("r", dataset_path=str(ds), local_paths=True) == "handle"
        assert seen["dataset_sha256"] == sha(self.CHECKED)


# =====================================================================================================================
# Review of the leftovers
# =====================================================================================================================

class TestPathsWithSpacesReview:
    """Review: three ways a home folder with a space still left the user name in a label."""

    @POSIX
    def test_the_real_path_of_a_linked_home_folder_is_the_home_folder(self, tmp_path, monkeypatch):
        """The framework resolves paths (realpath), so its errors name a linked home folder by its real path."""
        from orchestrator.tools.tools_runtime import label_paths
        real = tmp_path / "data" / "Jane Doe"
        real.mkdir(parents=True)
        (tmp_path / "linked home").symlink_to(real)
        real = Path(os.path.realpath(real))
        monkeypatch.setenv("HOME", str(tmp_path / "linked home"))
        for text, expected in ((f"cannot open {real}", "cannot open the home folder"),
                               (f"cannot open '{real}'", "cannot open 'the home folder'"),
                               (f"cannot open {real}/notes.txt now", "cannot open notes.txt now"),
                               (f"PATH={tmp_path}/bin:{real}", "PATH=bin:the home folder"),
                               (f"a copy in /mnt{real} and {tmp_path}/linked home{real}",
                                "a copy in mntthe home folder and the home folderthe home folder")):
            out = label_paths(text, {})
            assert out == expected
            assert "Jane" not in out and "Doe" not in out

    def test_a_home_folder_further_on_in_a_path_list(self, monkeypatch):
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", "/home/Jane Doe")
        for text, expected in (("PATH=/usr/bin:/home/Jane Doe", "PATH=bin:the home folder"),
                               ("HOME=/home/Jane Doe|grep x", "HOME=the home folder|grep x"),
                               ("not in /opt/tools|/home/Jane Doe or /usr/lib:/home/Jane Doe.",
                                "not in tools|the home folder or lib:the home folder.")):
            out = label_paths(text, {})
            assert out == expected
            assert "Jane" not in out and "Doe" not in out

    def test_a_quoted_text_with_more_than_one_path(self, monkeypatch):
        from orchestrator.tools.tools_runtime import label_paths
        monkeypatch.setenv("HOME", "/home/Jane Doe")
        for text, expected in (("'/home/Jane Doe/x.gguf and /home/Jane Doe'", "'x.gguf and the home folder'"),
                               ('"/home/Jane Doe/a, /home/Jane Doe)"', '"a, the home folder)"'),
                               ("copy in '/mnt/home/Jane Doe'", "copy in 'mntthe home folder'"),
                               ("('/home/Jane Doe)' and `/home/Jane Doe's`)",
                                "('the home folder)' and `the home folder's`)"),
                               ("'/home/Jane Doe/My Models/x.gguf'", "'x.gguf'")):
            out = label_paths(text, {})
            assert out == expected
            assert "Jane" not in out and "Doe" not in out


@POSIX
class TestSandboxCancelReview:
    """Review: a cancel during the private copy went on copying and still started the container."""

    def test_a_cancel_during_the_copy_stops_it_and_starts_no_container(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        env.model.write_bytes(b"GGUF" + b"x" * (3 << 20))
        env.digest = sha(env.model.read_bytes())
        bench = env.bench
        monkeypatch.setattr(bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        stop = threading.Event()
        reads = []
        real_pread, real_copy = os.pread, bench.BenchRunner._pinned_copy

        def pread(fd, n, offset):
            if stop.is_set():
                reads.append(offset)
            return real_pread(fd, n, offset)

        def copy(*a, **k):
            stop.set()  # the person cancels while the copy is written
            return real_copy(*a, **k)
        monkeypatch.setattr(bench.os, "pread", pread)
        monkeypatch.setattr(bench.BenchRunner, "_pinned_copy", staticmethod(copy))
        engine = FakeEngine()
        with pytest.raises(bench.BenchStopped):
            TestSandboxModelFile().run(env, env.make(engine), stop=stop)
        assert engine.runs == [], "no container starts after a cancel"
        assert len(reads) <= 1, "the copy stops at the cancel"
        assert leftovers(env.tmp / "cache" / "bench", env.model.parent) == []

    def test_a_cancel_while_the_link_is_made_starts_no_container(self, sandbox_bench, monkeypatch):
        env = sandbox_bench
        bench = env.bench
        stop = threading.Event()
        real_link = bench.BenchRunner._pinned_link

        def link(*a, **k):
            stop.set()
            return real_link(*a, **k)
        monkeypatch.setattr(bench.BenchRunner, "_pinned_link", staticmethod(link))
        engine = FakeEngine()
        with pytest.raises(bench.BenchStopped):
            TestSandboxModelFile().run(env, env.make(engine), stop=stop)
        assert engine.runs == [] and leftovers(env.model.parent, env.tmp / "cache" / "bench") == []


def test_a_known_folder_that_holds_the_home_path_in_its_middle_keeps_its_label(monkeypatch):
    """The home folder further on starts a path of its own, but never inside a known folder taken whole."""
    from orchestrator.tools.tools_runtime import label_paths
    monkeypatch.setenv("HOME", "/home/Jane Doe")
    roots = {"/mnt/home/Jane Doe/models": "models"}
    assert label_paths("cannot load /mnt/home/Jane Doe/models/x.gguf", roots) == "cannot load models:x.gguf"


@pytest.mark.parametrize("home", ["/home/jane doe", "/home/Jane Doe", "/Users/jane doe smith"])
def test_no_part_of_a_spaced_user_name_survives_generated_texts(home, monkeypatch):
    """Property guard: the home folder with a space, in thousands of generated error texts (quoted, in path lists,
    after '=' or '|', with tails and punctuation), never leaves a word of the user name."""
    import random
    import re
    from orchestrator.tools.tools_runtime import label_paths
    monkeypatch.setenv("HOME", home)
    words = home.split("/")[-1].split()
    rnd = random.Random(11)
    tails = ["", "/", "/x.gguf", "/My Models/x.gguf", "/my models", "/my models/qwen", "/a b/c", ".", ":", ")", "'s"]
    before = ["", " ", "=", ":", "|", "(", "'", '"', "`", "file://", "/mnt", "/usr/bin:", "[", "<", "--m="]
    after = ["", " ", ".", ",", ";", ")", "'", '"', "`", ":", "!", " and", "\n", ">", "]", "|", "="]
    roots = [{}, {home + "/My Models": "models"}, {home + "/data cache": "cache"}]
    checked = 0
    for _ in range(3000):
        parts = [rnd.choice(before) + home + rnd.choice(tails) + rnd.choice(after) for _ in range(rnd.randint(1, 3))]
        text = rnd.choice(["", "error: ", "cannot open "]) + rnd.choice(["", " "]).join(parts)
        if re.search(re.escape(home) + r"[\w\-+@~]", text):
            continue  # the home path glued to a name is another folder's name
        out = label_paths(text, rnd.choice(roots))
        checked += 1
        assert not any(w in out for w in words), (text, out)
    assert checked > 2500
