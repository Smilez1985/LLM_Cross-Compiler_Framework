"""The optional local and device runtime tools (MCP design 2.10, work package T14; runtime decisions D1, D7, D23).

Hidden until the person enables them (also when the whole tier is allowed); not registered before their runtime package
exists; a missing toolchain part refuses with needs_person and the CLI command; no input takes a CMake option or a
command and none offers a prebuilt archive; an unverified build is never started; a cancelled build is never activated;
a cancelled model download stops and keeps its part; a model built for a board is never benchmarked natively on this
computer (seventh round 2': in the Docker sandbox here, for real values on the board); no result holds the server's
address or key; the board tools reach only trusted devices with the trusted host key, are always asked and never
granted.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from orchestrator.tools.policy import Decision, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry

import test_runtime_selfbuilt as rs  # noqa: E402  (fakes for the self-built runtime: executor, verifier, toolchain)

LOCAL = ("build_local_runtime", "start_local_runtime", "stop_local_runtime", "run_benchmark")
BOARD = ("probe_remote_device", "deploy_device_runtime", "run_device_benchmark")
ALL = LOCAL + BOARD


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch):
    """The disk of the test machine does not decide."""
    from orchestrator.Core import hf_hub
    monkeypatch.setattr(hf_hub, "disk_check", lambda folder, needed: {
        "free": 500 * 10 ** 9, "reserve": 2 * 10 ** 9, "reserved": 0, "needed": needed, "fits": True,
        "fits_standing": True})


@pytest.fixture
def fw(make_framework):
    return make_framework()


def allow(fw, *names, tiers=None):
    """The person's permissions: these tools enabled (allowed)."""
    policy = ToolPolicy(configured=True, reviewed=[s.name for s in fw.tool_registry.list()],
                        overrides={n: Decision.ALLOW for n in names})
    if tiers:
        policy.tiers.update(tiers)
    fw.tool_registry.policy_store.replace(policy)


def call(fw, name, arguments, confirm=None, session="s"):
    return fw.tool_registry.call(name, arguments, ToolContext("ditto", session=session, confirm=confirm))


def asking(answer=True):
    asked = []

    def confirm(spec, inp, *rest):
        from orchestrator.tools.registry import current_question
        asked.append(current_question() or "")
        return answer
    confirm.asked = asked
    return confirm


def wait_job(fw, job_id, seconds=30):
    deadline = time.monotonic() + seconds
    job = fw.job_service.get(job_id)
    while not job.finished and time.monotonic() < deadline:
        job = fw.job_service.wait(job_id, 1)
    return job


@pytest.fixture
def fake_rt(fw, tmp_path, monkeypatch, no_source):
    """The framework's LocalLLMRuntime is the self-built runtime with fake executor, verifier and toolchain."""
    from orchestrator.runtime.llama_server import LocalLLMRuntime
    holder = {"rt": rs.runtime(tmp_path / "rt")}
    monkeypatch.setattr(LocalLLMRuntime, "from_framework", classmethod(lambda cls, framework, **kw: holder["rt"]))
    return holder


no_source = rs.no_source


# --- visibility and registration ------------------------------------------------------------------------------------

class TestHidden:

    def test_registered_and_hidden_until_enabled_even_with_the_whole_tier_allowed(self, fw):
        allow(fw, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW})
        rows = {r["tool"]: r for r in fw.tool_registry.permissions("mcp")}
        for name in ALL:
            assert name in rows, name
            assert rows[name]["decision"] == "deny" and not rows[name]["listed"], name
        allow(fw, "stop_local_runtime")
        rows = {r["tool"]: r for r in fw.tool_registry.permissions("mcp")}
        assert rows["stop_local_runtime"]["listed"] and rows["stop_local_runtime"]["decision"] == "allow"

    def test_the_board_tools_are_always_asked_and_never_granted(self, fw):
        from orchestrator.tools.grants import NEVER_GRANT
        for name in BOARD:
            spec = fw.tool_registry.get(name)
            assert spec.tier == ToolTier.EXTERNAL and spec.cap() == Decision.ASK and spec.grant is None, name
            assert spec.meta() == {"anthropic/requiresUserInteraction": True}
        assert {"probe_remote_device", "deploy_device_runtime", "run_device_benchmark"} <= NEVER_GRANT

    def test_not_registered_before_the_runtime_package_exists(self, fw, monkeypatch):
        from orchestrator.tools import tools_runtime
        for key in tools_runtime.PACKAGES:
            monkeypatch.setitem(tools_runtime.PACKAGES, key, lambda: False)
        fresh = ToolRegistry(fw.tool_registry.policy_store, fw.tool_registry.audit)
        tools_runtime.register_runtime_tools(fresh, fw, service=fw.build_service, jobs=fw.job_service)
        assert not {s.name for s in fresh.list()} & set(ALL)

    def test_no_input_takes_a_cmake_option_a_command_or_a_prebuilt_archive(self, fw):
        words = ("cmake", "flag", "flags", "option", "command", "args", "prebuilt", "url", "path", "script")
        for name in ALL:
            for field in fw.tool_registry.get(name).input_model.model_fields:
                assert not set(field.split("_")) & set(words), (name, field)
        schema = fw.tool_registry.get("build_local_runtime").input_model.model_json_schema()
        assert schema["properties"]["backend"]["enum"] == ["auto", "cpu", "cuda", "vulkan", "rocm", "metal"]


# --- build_local_runtime --------------------------------------------------------------------------------------------

class TestBuild:

    def test_a_missing_toolchain_part_needs_the_person_and_names_the_command(self, fw, tmp_path, monkeypatch):
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        rt = rs.runtime(tmp_path / "rt", native=False)  # no compiler, no Docker
        monkeypatch.setattr(LocalLLMRuntime, "from_framework", classmethod(lambda cls, framework, **kw: rt))
        allow(fw, "build_local_runtime")
        confirm = asking(True)
        with pytest.raises(ToolError) as e:
            call(fw, "build_local_runtime", {}, confirm=confirm)
        assert e.value.code == "needs_person" and "llm-cli ditto runtime install" in str(e.value)
        assert confirm.asked == []

    def test_an_explicit_backend_that_cannot_be_built_here_needs_the_person(self, fw, tmp_path, monkeypatch,
                                                                             no_source):
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        rt = rs.runtime(tmp_path / "rt", profile=rs.PC, hw=rs.machine([]))  # CUDA waits for its package
        monkeypatch.setattr(LocalLLMRuntime, "from_framework", classmethod(lambda cls, framework, **kw: rt))
        allow(fw, "build_local_runtime")
        with pytest.raises(ToolError) as e:
            call(fw, "build_local_runtime", {"backend": "cuda"}, confirm=asking(True))
        assert e.value.code == "needs_person" and "cuda" in str(e.value)

    def test_the_build_is_a_job_that_shows_its_plan_and_activates_the_self_built_server(self, fw, fake_rt):
        fw.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fw.tool_registry.list()],
                                                         overrides={"build_local_runtime": Decision.ASK}))
        confirm = asking(True)
        started = call(fw, "build_local_runtime", {"backend": "cpu"}, confirm=confirm)
        job = wait_job(fw, started.job_id)
        assert job.state == "done", job.error
        assert job.result["origin"] == "self-built" and job.result["verified"] and job.result["backend"] == "cpu"
        assert fake_rt["rt"].installed().build_id == job.result["build_id"]
        (question,) = confirm.asked
        assert "pinned commit" in question and "self-built" in question and "cpu build" in question

    def test_the_build_of_an_ai_removes_no_old_build(self, fw, fake_rt, monkeypatch):
        from orchestrator.runtime.build.store import BuildStore
        removed = []   # BuildStore.remove deletes only what the person chose from the list (WP7)
        monkeypatch.setattr(BuildStore, "remove", lambda self, *a, **k: removed.append(a) or ([], []))
        other = fake_rt["rt"].root / "b1000-cpu-x64"  # the official archive of another llama.cpp build
        other.mkdir(parents=True)
        (other / "runtime.json").write_text("{}")
        allow(fw, "build_local_runtime")
        job = wait_job(fw, call(fw, "build_local_runtime", {"backend": "cpu"}, confirm=asking(True)).job_id)
        assert job.state == "done", job.error
        assert removed == [] and other.is_dir(), "an AI never reaches a deletion (round 5 f)"

    def test_a_cancelled_build_is_never_activated(self, tmp_path, no_source):
        ex = rs.FakeExecutor()
        rt = rs.runtime(tmp_path / "rt", executor=ex)
        cancelled = {"now": False}

        class CancelDuringBuild(rs.FakeExecutor):
            def build(self, plan, **kw):
                result = ex.build(plan, **kw)
                cancelled["now"] = True  # the person cancels while the CPU build runs
                return result

        rt = rs.runtime(tmp_path / "rt2", executor=CancelDuringBuild())
        with pytest.raises(Exception, match="cancelled"):
            rt.install(cancelled=lambda: cancelled["now"])
        assert rt.store.references() == {} and rt.installed() is None, "nothing is activated"

    def test_a_cancelled_gpu_build_leaves_the_cpu_build_active(self, tmp_path, no_source):
        seen = []

        class CancelAfterCpu(rs.FakeExecutor):
            def build(self, plan, **kw):
                seen.append(plan.backend)
                return super().build(plan, **kw)

        rt = rs.runtime(tmp_path / "rt", executor=CancelAfterCpu())
        result = rt.install(cancelled=lambda: "vulkan" in seen)
        assert result.active.backend == "cpu", "the cancelled GPU build is not activated"
        assert any("cancelled" in p for p in result.problems)


class TestModelDownload:

    def test_a_stopped_download_ends_and_keeps_its_part(self, tmp_path):
        import hashlib
        from test_runtime import FakeSession
        from orchestrator.utils.download import DownloadStopped, PinnedDownloader
        data = b"x" * (3 * 1024 * 1024)
        url = "https://example.invalid/model.gguf"
        stop = threading.Event()
        dest = tmp_path / "model.gguf"
        with pytest.raises(DownloadStopped):
            PinnedDownloader(FakeSession({url: data})).fetch(
                url, dest, len(data), hashlib.sha256(data).hexdigest(),
                progress=lambda name, done, total: stop.set(), stop=stop)
        part = dest.with_name(dest.name + ".part")
        assert not dest.exists() and part.is_file() and 0 < part.stat().st_size < len(data)
        # a later run resumes it
        done = PinnedDownloader(FakeSession({url: data})).fetch(url, dest, len(data),
                                                                 hashlib.sha256(data).hexdigest())
        assert done.read_bytes() == data and not part.exists()


# --- start and stop -------------------------------------------------------------------------------------------------

class TestStartStop:

    def test_an_unverified_build_is_never_started(self, fw, fake_rt):
        rt = fake_rt["rt"]
        build = rs._stored(rt, verifier=rs.FakeVerifier(ran=False))
        rt._update_active(build_id=build.build_id, use="self-built")
        allow(fw, "start_local_runtime")
        confirm = asking(True)
        with pytest.raises(ToolError) as e:
            call(fw, "start_local_runtime", {}, confirm=confirm)
        # never started and nobody asked; the AI can clear it itself (facts catalogue: runtime_not_built is resolved
        # by build_local_runtime, so its reason code is missing_information, not needs_person)
        assert e.value.code == "missing_information" and e.value.blocker == "runtime_not_built"
        assert "build_local_runtime" in str(e.value) and confirm.asked == []

    def test_no_result_holds_the_address_or_the_key(self, fw, fake_rt, monkeypatch):
        rt = fake_rt["rt"]
        rt.install()
        model, _ = rt.resolve_model(None)
        rt.model_path(model).parent.mkdir(parents=True, exist_ok=True)
        rt.model_path(model).write_bytes(b"GGUF")
        from orchestrator.runtime.llama_server import LocalEndpoint, RuntimeStatus
        secret_key = "k" * 40
        monkeypatch.setattr(type(rt), "start", lambda self, **kw: LocalEndpoint(
            base_url="http://127.0.0.1:41234/v1", api_key=secret_key, model=model.alias, port=41234, pid=1))
        monkeypatch.setattr(type(rt), "status", lambda self, **kw: RuntimeStatus(
            build=rt.build, installed=rt.installed(), running=True, healthy=True, port=41234, pid=1,
            model=model.alias, tier=model.tier, ctx=4096, base_url="http://127.0.0.1:41234/v1",
            log_file=tmp_log(rt)))
        allow(fw, "start_local_runtime")
        started = call(fw, "start_local_runtime", {"context_length": 4096}, confirm=asking(True))
        job = wait_job(fw, started.job_id)
        assert job.state == "done", job.error
        text = json.dumps(job.result)
        assert secret_key not in text and "41234" not in text and "base_url" not in text
        assert job.result["running"] and job.result["tier"] == model.tier

    def test_stop(self, fw, fake_rt, monkeypatch):
        monkeypatch.setattr(type(fake_rt["rt"]), "stop", lambda self, timeout=15: 1)
        allow(fw, "stop_local_runtime")
        assert call(fw, "stop_local_runtime", {}).stopped_processes == 1


def tmp_log(rt):
    return rt.log_file


# --- run_benchmark --------------------------------------------------------------------------------------------------

class TestBenchmark:

    def gguf_artifact(self, fw):
        from orchestrator.Core.build_service import artifact_id
        out = Path(fw.resolve_path(fw.config.get("output_dir", "output")))
        folder = out / "Rockchip" / "mine"
        folder.mkdir(parents=True)
        (folder / "model.gguf").write_bytes(b"GGUF user conversion")
        (folder / "build_info.json").write_text(json.dumps({"model": "mine", "format": "gguf", "target": "Rockchip",
                                                             "primary": "model.gguf"}))
        return artifact_id("Rockchip/mine")

    @pytest.fixture
    def bench(self, fw, fake_rt, monkeypatch):
        """A built runtime and a stand-in llama-bench run; records how each run was made."""
        from orchestrator.runtime import bench as bench_module
        rt = fake_rt["rt"]
        rt.install()
        runs = []

        def fake_run(self, model, **kw):
            runs.append(kw)
            return bench_module.BenchRun(run_id="bench_000000000001", kind=kw["kind"], model=kw["label"],
                                         sha256=kw["sha256"], trusted=kw["trusted"],
                                         where="sandbox" if kw.get("sandbox") else "native", build_id="b" * 64,
                                         backend="cpu", prompt_ts=100.0, gen_ts=10.0)
        monkeypatch.setattr(bench_module.BenchRunner, "run", fake_run)
        build = type("Build", (), {"build_id": "b" * 64, "backend": "cpu"})()
        monkeypatch.setattr(bench_module.BenchRunner, "bench_build", lambda self, sandbox=False: (build, Path("x")))
        return runs

    def test_the_question_is_only_in_the_framework(self, fw):
        assert fw.tool_registry.get("run_benchmark").channels == ("framework",)
        assert fw.tool_registry.get("run_device_benchmark").channels == ("framework",)

    def test_a_model_built_for_a_board_is_never_benchmarked_natively(self, fw, bench):
        """Seventh round 2': a built model runs in the Docker sandbox here (the board gives the real values); asking
        for a native run is refused, and nothing runs."""
        aid = self.gguf_artifact(fw)
        allow(fw, "run_benchmark")
        with pytest.raises(ToolError) as e:
            call(fw, "run_benchmark", {"kind": "artifact_speed", "artifact_id": aid, "sandbox": False},
                 confirm=asking(True))
        assert e.value.code == "not_supported" and "run_device_benchmark" in str(e.value) and bench == []
        fw.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                         reviewed=[s.name for s in fw.tool_registry.list()],
                                                         overrides={"run_benchmark": Decision.ASK}))
        confirm = asking(True)
        started = call(fw, "run_benchmark", {"kind": "artifact_speed", "artifact_id": aid}, confirm=confirm)
        (question,) = confirm.asked
        assert "UNTRUSTED" in question and "Docker sandbox" in question and "run_device_benchmark" in question
        assert wait_job(fw, started.job_id).state == "done"
        (run,) = bench
        assert run["trusted"] is False and run["sandbox"] is True
        rows = fw.tool_registry.audit.entries(limit=50)
        assert not any(r.get("tool") == "benchmark.untrusted_model_native" for r in rows)

    def test_the_runner_refuses_a_native_run_of_a_model_that_is_not_a_catalog_model(self, tmp_path):
        """The rule at the root: whatever a caller asks, BenchRunner never runs such a model natively."""
        from orchestrator.runtime.bench import BenchError, BenchRunner
        from orchestrator.runtime.catalog import load_catalog
        model = tmp_path / "board-model.gguf"
        model.write_bytes(b"GGUF built for a board")
        started = []
        runner = BenchRunner(tmp_path / "state", catalog=load_catalog(),
                             popen=lambda *a, **k: started.append(a) or pytest.fail("llama-bench started"))
        with pytest.raises(BenchError) as e:
            runner.run(model, kind="artifact_speed", label="board model", trusted=False, sha256="0" * 64)
        assert e.value.code == "not_supported" and "never benchmarked natively" in str(e.value)
        assert "Deployment window" in e.value.next and "Benchmark window" not in e.value.next, \
            "the next step names the window that measures on a board (the old Benchmark window is not wired)"
        assert started == []

    def test_an_allowed_benchmark_of_a_built_model_runs_in_the_sandbox_only(self, fw, bench):
        """Allowed by the person, a built model is measured in the Docker sandbox without a question; a native run is
        refused even then (before 3.1 it ran natively after the person's consent)."""
        aid = self.gguf_artifact(fw)
        allow(fw, "run_benchmark")
        started = call(fw, "run_benchmark", {"kind": "artifact_speed", "artifact_id": aid}, confirm=None)
        assert wait_job(fw, started.job_id).state == "done" and bench[0]["sandbox"] is True
        with pytest.raises(ToolError):
            call(fw, "run_benchmark", {"kind": "artifact_speed", "artifact_id": aid, "sandbox": False}, confirm=None)
        assert len(bench) == 1

    def test_the_sandbox_opt_in(self, fw, bench):
        aid = self.gguf_artifact(fw)
        allow(fw, "run_benchmark")
        started = call(fw, "run_benchmark", {"kind": "artifact_speed", "artifact_id": aid, "sandbox": True},
                       confirm=asking(True))
        assert wait_job(fw, started.job_id).state == "done"
        assert bench[0]["sandbox"] is True

    def test_a_tier_model_that_is_not_downloaded_names_the_next_step(self, fw, bench):
        allow(fw, "run_benchmark")
        from orchestrator.runtime.catalog import load_catalog
        name = load_catalog().models[0].tier
        with pytest.raises(ToolError) as e:
            call(fw, "run_benchmark", {"kind": "tier_speed", "tier": name}, confirm=asking(True))
        assert e.value.code == "missing_information" and "build_local_runtime" in str(e.value)


# --- the board tools ------------------------------------------------------------------------------------------------

class TestBoard:

    @pytest.fixture
    def device(self, fw, monkeypatch):
        paramiko = pytest.importorskip("paramiko")
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.utils import ssh_trust
        state = Path(fw.config.user_state_dir)
        key = paramiko.ECDSAKey.generate()
        ssh_trust.trust_host_key(state, "192.168.1.50", 22, key)
        device = TrustedDevices(state).record("192.168.1.50", 22, "dietpi", key, via="cli", paired=True,
                                              label="Living room board")
        presented = {"key": key}
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: presented["key"])
        return device, presented, paramiko

    def test_the_probe_question_shows_the_device_and_its_host_key(self, fw, device, monkeypatch):
        from orchestrator.runtime.device import service
        dev, presented, _ = device
        probed = []
        monkeypatch.setattr(service, "probe", lambda d, s, fp, on_client=None: probed.append(fp) or
                            {"probe_id": d.device_id, "tool": "python", "warnings": 0})
        allow(fw, "probe_remote_device")
        confirm = asking(True)
        started = call(fw, "probe_remote_device", {"device_id": dev.device_id}, confirm=confirm)
        assert "Living room board" in confirm.asked[0] and dev.fingerprint in confirm.asked[0]
        assert wait_job(fw, started.job_id).state == "done" and probed == [dev.fingerprint]

    def test_a_changed_host_key_is_refused_before_the_question(self, fw, device):
        dev, presented, paramiko = device
        presented["key"] = paramiko.ECDSAKey.generate()
        allow(fw, "probe_remote_device", "deploy_device_runtime")
        for name in ("probe_remote_device",):
            confirm = asking(True)
            with pytest.raises(ToolError) as e:
                call(fw, name, {"device_id": dev.device_id}, confirm=confirm)
            assert e.value.code == "needs_person" and confirm.asked == []

    def test_deploying_the_runtime_needs_a_probe_of_the_board(self, fw, device):
        dev, _, _ = device
        allow(fw, "deploy_device_runtime")
        with pytest.raises(ToolError) as e:
            call(fw, "deploy_device_runtime", {"device_id": dev.device_id}, confirm=asking(True))
        assert e.value.code == "missing_information" and "probe_remote_device" in str(e.value)

    def test_an_unknown_device_is_refused(self, fw, device):
        allow(fw, "run_device_benchmark")
        with pytest.raises(ToolError) as e:
            call(fw, "run_device_benchmark", {"device_id": "0" * 16}, confirm=asking(True))
        assert e.value.code == "unknown_id" and "list_devices" in str(e.value)


def test_the_runtime_catalog_resource(fw):
    allow(fw)
    text = fw.tool_registry.read_resource("lcf://runtime/catalog", {}, ToolContext("mcp"))
    assert "commit" in str(text) and "tier" in str(text)
