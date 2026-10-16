"""Regression tests for the build engine fixes of the Phase 1+2 review (commit 1a714ea).

A cancel is never undone by a later stage: the Trivy scan container, the buildx process and the stage
containers are stopped, _enter_stage refuses after a cancel, and a cancel racing container.start()
kills the container. Only a real read timeout counts as a build timeout. A cancelled build keeps its
slot until its worker returns. The orchestrator records build ids before it submits them and cancels
a build that was submitted during a cancel. A retry after a healing fix keeps its own id and log. Missing
calibration data fails low-bit GGUF requests up front.

Follow-up round: a build's files are staged and reach its output folder only once it can no longer be
cancelled; an outcome becomes visible together with the free slot; the orchestrator waits for a slot
instead of failing a job; a request with nothing to build is an error.

Second follow-up: links in a build's output are never followed, a build is staged next to its real output
folder and only staging folders are hidden from the listings, the cleanup runs before the outcome is settled,
a cancel that comes too late is reported, and a hanging security scan ends with the build timeout.

Docker is replaced by FakeDockerClient (containers follow Docker's semantics: stopping a container that is
not running is a no-op, killing one is a 409) and `docker buildx` by FakeBuildx. Every fake blocks at most
SAFETY seconds, so a regression fails the test instead of hanging it.
"""

import asyncio
import json
import os
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import docker
import pytest
import requests
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

import orchestrator.Core.builder as builder_module
from orchestrator.Core.build_service import BuildService
from orchestrator.Core.builder import (BUILD_MANIFEST, BuildCancelled, BuildConfiguration, BuildEngine,
                                       BuildProgress, BuildStatus, ModelFormat, OptimizationLevel)
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.deployment_manager import list_builds
from orchestrator.Core.orchestrator import (BuildJob, BuildRequest, LLMOrchestrator, OrchestrationStatus,
                                            PriorityLevel, WorkflowState, WorkflowType)
from orchestrator.Core.self_healing_manager import HealingProposal, HealingStrategy
from orchestrator.utils.calibration import CalibrationDataError
from orchestrator.utils.helpers import redact_secrets
from test_pipeline import FakeBuildEngine

SAFETY = 10  # seconds; no fake blocks longer than this
TERMINAL = (BuildStatus.COMPLETED, BuildStatus.FAILED, BuildStatus.CANCELLED)


# ---------------------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------------------

class FakeContainer:
    """A Docker container. `auto_exit` containers print their lines and exit with `exit_code` once
    started; the others run until they are stopped, killed or force-removed."""

    def __init__(self, client, name, *, volumes=None, exit_code=0, lines=(), auto_exit=True, running=False,
                 start_gate=None, wait_error=None, stream_error=None, outputs=None):
        self.client, self.name, self.volumes = client, name, volumes or {}
        self.exit_code, self.lines, self.auto_exit = exit_code, list(lines), auto_exit
        self.running = running
        self.start_gate = start_gate      # start() blocks on it: the daemon is still starting the container
        self.wait_error = wait_error      # exception (or factory) raised by wait()
        self.stream_error = stream_error  # raised by the log stream after a stop (container already gone)
        self.outputs = outputs or {}      # files written to /build-cache/output on a clean exit
        self.exited = threading.Event()
        self.following = threading.Event()  # all lines delivered, the reader now follows the live log
        self.waiting = threading.Event()
        self.stopped = threading.Event()
        self.removed = False

    def _event(self, what):
        self.client.events.append((self.name, what))

    def _finish(self, code):
        if not self.exited.is_set():
            self.exit_code = code
            self.running = False
            self.exited.set()

    def _exit_by_itself(self):
        if self.exit_code == 0:
            for host, bind in self.volumes.items():
                if bind.get("bind") == "/build-cache/output":
                    for name, data in self.outputs.items():
                        (Path(host) / name).write_bytes(data)
        self._finish(self.exit_code)

    def start(self):
        self._event("start-requested")
        if self.start_gate is not None:
            self.start_gate.wait(SAFETY)
        self.running = True
        self._event("running")
        if self.auto_exit:
            self._exit_by_itself()

    def stop(self, timeout=None):
        if not self.running:
            self._event("stop-noop")  # Docker answers 304 Not Modified, no exception
            return
        self._event("stop")
        self.stopped.set()
        self._finish(137)

    def kill(self):
        if not self.running:
            self._event("kill-not-running")
            raise docker.errors.APIError("409 Client Error: Conflict (container is not running)")
        self._event("kill")
        self.stopped.set()
        self._finish(137)

    def remove(self, force=False):
        self._event("remove")
        self.removed = True
        if self.running:
            self.stopped.set()
            self._finish(137)

    def logs(self, stream=False, follow=False):
        def generate():
            for line in self.lines:
                yield line.encode()
            self.following.set()
            if follow:
                self.exited.wait(SAFETY)
            if self.stream_error is not None and self.stopped.is_set():
                raise self.stream_error
        return generate()

    def wait(self, timeout=None):
        self._event("wait")
        self.waiting.set()
        if self.wait_error is not None:
            raise self.wait_error() if callable(self.wait_error) else self.wait_error
        if not self.exited.wait(SAFETY):
            raise requests.exceptions.ReadTimeout("fake container never exited")
        return {"StatusCode": self.exit_code}


class FakeBuildx:
    """`docker buildx build`: prints its lines, then finishes (finish=True) or runs until terminated."""

    def __init__(self, lines=("#1 [internal] load build definition from Dockerfile",), finish=True, code=0):
        self.cmd = None
        self.lines = list(lines)
        self.code = code
        self.returncode = None
        self.calls = []
        self.reading = threading.Event()  # the engine has read every line and waits for more
        self.done = threading.Event()
        if finish:
            self.done.set()
        self.stdout = self._stream()

    def _stream(self):
        for line in self.lines:
            yield line + "\n"
        self.reading.set()
        self.done.wait(SAFETY)
        if self.returncode is None:
            self.returncode = self.code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.done.wait(SAFETY)
        if self.returncode is None:
            self.returncode = self.code
        return self.returncode

    def terminate(self):
        self.calls.append("terminate")
        if self.returncode is None:
            self.returncode = -15
        self.done.set()

    def kill(self):
        self.calls.append("kill")
        if self.returncode is None:
            self.returncode = -9
        self.done.set()


class _FakeImage:
    attrs = {"Size": 9}

    def __init__(self, store, image_id):
        self.store, self.id = store, image_id

    @staticmethod
    def save(named=False):
        # what `docker save` streams; the security scan reads that archive and never gets the socket
        return iter([b"image", b".tar"])

    @property
    def tags(self):
        return sorted(name for name, image_id in self.store.names.items() if image_id == self.id)

    def tag(self, repository, tag=None):
        self.store.names[f"{repository}:{tag}"] = self.id
        return True


class FakeImageStore:
    """Docker's image names as the builder sees them. Removing a name that is an image's last one would delete the
    image, and the builder never deletes an image (round 7 item 12): the fake fails then, and on any prune."""

    def __init__(self, events):
        self.events = events
        self.names = {}          # name -> image id
        self.removed = []
        self.on_remove = None    # called when a name is removed (the build's final cleanup)

    def get(self, ref):
        if ref not in self.names:
            if ref.endswith(":last-build"):
                raise RuntimeError(f"No such image: {ref}")
            self.names[ref] = f"sha256:{ref}"   # the image the build made
        return _FakeImage(self, self.names[ref])

    def remove(self, image=None, force=False, noprune=False, **kwargs):
        image_id = self.names.pop(image)
        assert image_id in self.names.values(), "the builder removed an image's last name: that deletes the image"
        self.events.append((image, "image-remove"))
        self.removed.append(image)
        if self.on_remove is not None:
            self.on_remove(image)

    def prune(self, *args, **kwargs):
        raise AssertionError("the builder never prunes images")


class FakeDockerClient:
    MODEL_FILE = "SmolLM2-Q4_K_M.gguf"

    def __init__(self):
        self.events = []         # (container name, event), in order
        self.created = []        # containers.create(...) calls
        self.scans = []          # Trivy containers started through containers.run
        self.scan_runs = []      # their containers.run arguments, with each mounted file as it was at the start
        self.buildx = []         # FakeBuildx processes
        self.everything = []
        self.stage_options = {}  # container name (exact, else prefix) -> FakeContainer options
        self.scan_options = {}
        self.on_scan_run = None  # called while containers.run is still in flight
        self.buildx_factory = FakeBuildx
        self.containers = SimpleNamespace(run=self._run, create=self._create)
        self.images = FakeImageStore(self.events)

    def ping(self):
        return True

    def container(self, name):
        return next((c for c in self.everything if c.name == name), None)

    def _run(self, image, command=None, volumes=None, detach=False, **kwargs):
        assert detach, "the scan must run detached, or cancel_build cannot reach it"
        files = {host: (Path(host).read_bytes(), Path(host).stat().st_mode & 0o777,
                        Path(host).parent.stat().st_mode & 0o777)
                 for host in (volumes or {}) if Path(host).is_absolute() and Path(host).is_file()}
        self.scan_runs.append(dict(image=image, command=command, volumes=volumes, files=files, **kwargs))
        scan = FakeContainer(self, f"trivy-{len(self.scans) + 1}", running=True, **self.scan_options)
        self.scans.append(scan)
        self.everything.append(scan)
        self.events.append((scan.name, "run"))
        if self.on_scan_run is not None:
            self.on_scan_run()
        if scan.auto_exit:
            scan._exit_by_itself()
        return scan

    def _create(self, image, command, volumes, environment, name, **kwargs):
        self.created.append(dict(name=name, volumes=volumes, environment=environment, **kwargs))
        options = self.stage_options.get(name)
        if options is None:
            options = next((o for prefix, o in self.stage_options.items() if name.startswith(prefix)), {})
        options = dict(options)
        if name.startswith("llm-build-"):
            options.setdefault("outputs", {self.MODEL_FILE: b"gguf-weights"})
        container = FakeContainer(self, name, volumes=volumes, **options)
        self.everything.append(container)
        self.events.append((name, "create"))
        return container

    def popen(self, cmd, **kwargs):
        proc = self.buildx_factory()
        proc.cmd = cmd
        self.buildx.append(proc)
        return proc

    def release_all(self):
        for container in self.everything:
            container._finish(container.exit_code if container.exit_code is not None else 0)
        for proc in self.buildx:
            proc.done.set()


class HeldExecutor:
    """Keeps submitted builds until run_all(): a build waiting for a worker thread."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((fn, args))

    def run_all(self):
        for fn, args in self.jobs:
            fn(*args)

    def shutdown(self, **kwargs):
        pass


class RecordingEngine(FakeBuildEngine):
    """Keeps every attempt's progress. First attempts fail, retries (`_r1`) run until cancelled."""

    def __init__(self):
        super().__init__()
        self.builds = {}
        self.cancels = []

    def build_model(self, config):
        self.configs.append(config)
        retry = config.build_id.endswith("_r1")
        self.builds[config.build_id] = SimpleNamespace(
            status=BuildStatus.BUILDING if retry else BuildStatus.FAILED,
            errors=[] if retry else ["attempt 1: Killed"], logs=[f"log of {config.build_id}"])
        return config.build_id

    def cancel_build(self, build_id):
        progress = self.builds.get(build_id)
        ok = progress is not None and progress.status not in TERMINAL
        if ok:
            progress.status = BuildStatus.CANCELLED
        self.cancels.append((build_id, ok))
        return ok

    def get_build_status(self, build_id):
        return self.builds.get(build_id)


# ---------------------------------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------------------------------

def wait_for(predicate, timeout=5.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.005)


async def until(predicate, timeout=5.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.005)


def finish(engine, build_id, timeout=5.0):
    """Waits until the build's worker thread has returned and gives its progress."""
    wait_for(lambda: build_id not in engine._running, timeout, f"the worker of {build_id}")
    return engine.get_build_status(build_id)


def local_model(tmp_path):
    model = tmp_path / "SmolLM2"
    model.mkdir(exist_ok=True)
    (model / "config.json").write_text("{}")
    return model


def config_for(tmp_path, **overrides):
    build_id = overrides.get("build_id", "b1")
    values = dict(build_id=build_id, timestamp="t", model_source=str(local_model(tmp_path)), target_arch="Rockchip",
                  target_format=ModelFormat.GGUF, output_dir=str(tmp_path / "out" / build_id),
                  quantization="Q4_K_M", target_board="rk3566", enable_hadolint=False)
    values.update(overrides)
    return BuildConfiguration(**values)


def make_request(tmp_path, **overrides):
    values = dict(request_id="r1", workflow_type=WorkflowType.SIMPLE_CONVERSION, priority=PriorityLevel.NORMAL,
                  models=[str(local_model(tmp_path))], targets=["Rockchip"], target_formats=[ModelFormat.GGUF],
                  optimization_level=OptimizationLevel.BALANCED, quantization_options=["Q4_K_M"],
                  parallel_builds=False, output_base_dir=str(tmp_path / "out"), target_board="rk3566")
    values.update(overrides)
    return BuildRequest(**values)


def run_pipeline(orch, req):
    orch._workflows[req.request_id] = WorkflowState(req.request_id, OrchestrationStatus.PREPARING, datetime.now())
    asyncio.run(orch._run_build_pipeline(req))
    return orch._workflows[req.request_id]


def proposal():
    return HealingProposal("Lower the build jobs", "The compiler ran out of memory", "build_jobs=2", 0.9,
                           HealingStrategy.CONFIG_ADJUSTMENT, "HOST")


@pytest.fixture
def client():
    fake = FakeDockerClient()
    yield fake
    fake.release_all()


@pytest.fixture
def make_engine(tmp_path, monkeypatch, client):
    """Real BuildEngines on the fake Docker client and fake buildx; only the Dockerfile staging is stubbed."""
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    monkeypatch.setattr(builder_module, "subprocess", SimpleNamespace(
        Popen=client.popen, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, DEVNULL=subprocess.DEVNULL,
        run=subprocess.run))
    dockerfile = tmp_path / "context" / "Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text("FROM scratch\n")
    engines = []

    def factory(max_concurrent_builds=2):
        engine = BuildEngine(ConfigManager(tmp_path / "configs"), max_concurrent_builds=max_concurrent_builds,
                             base_dir=tmp_path)
        engine.docker_client = client
        engine.config.set("enable_security_scan", True)
        assert engine.targets_dir.is_relative_to(tmp_path)
        (engine.targets_dir / "Rockchip").mkdir(parents=True, exist_ok=True)
        engine._generate_dockerfile = lambda config, progress, target_path: dockerfile
        engine._buildkit_available = lambda: True
        engines.append(engine)
        return engine

    yield factory
    client.release_all()
    for engine in engines:
        engine._executor.shutdown(wait=True, cancel_futures=True)


@pytest.fixture
def engine(make_engine):
    return make_engine()


@pytest.fixture
def fast_polling(monkeypatch):
    """The orchestrator polls build status once per second; poll every 10 ms instead."""
    real_sleep = asyncio.sleep

    async def quick(delay, result=None):
        return await real_sleep(min(delay, 0.01), result)

    monkeypatch.setattr(asyncio, "sleep", quick)


@pytest.fixture
def fake_orchestrator(tmp_path):
    cfg = ConfigManager(tmp_path / "configs")
    (tmp_path / "targets" / "Rockchip").mkdir(parents=True)
    cfg.set("targets_dir", str(tmp_path / "targets"))
    cfg.set("cache_dir", str(tmp_path / "cache"))
    return LLMOrchestrator(cfg, build_engine=FakeBuildEngine())


# ---------------------------------------------------------------------------------------------------
# BuildEngine: cancellation
# ---------------------------------------------------------------------------------------------------

class TestFakePipeline:

    def test_an_uncancelled_build_completes_with_its_manifest(self, engine, client, tmp_path):
        """Baseline: the fakes drive the real stages to COMPLETED, so the cancel tests below mean something."""
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.COMPLETED, progress.errors
        assert (Path(cfg.output_dir) / BUILD_MANIFEST).is_file()
        assert [c["name"] for c in client.created] == ["llm-build-b1"]
        assert client.scans[0].removed and client.buildx[0].calls == []
        assert engine._active_containers == {} and engine._active_processes == {}


class TestCancelDuringSecurityScan:

    def test_trivy_is_stopped_and_no_conversion_container_starts(self, engine, client, tmp_path):
        client.scan_options = dict(auto_exit=False, lines=["Downloading vulnerability DB..."])
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        wait_for(lambda: client.scans and client.scans[0].following.is_set(), what="the Trivy log stream")
        scan = client.scans[0]
        assert engine._active_containers.get("b1") is scan, "the scan must be reachable for cancel_build"

        assert engine.cancel_build("b1") is True
        progress = finish(engine, "b1")

        assert progress.status == BuildStatus.CANCELLED
        assert (scan.name, "stop") in client.events and scan.removed
        assert client.created == [], "no imatrix or conversion container after a cancel"
        assert not (Path(cfg.output_dir) / BUILD_MANIFEST).exists()
        assert not Path(cfg.output_dir).exists() or not any(Path(cfg.output_dir).iterdir())
        assert progress.errors == []
        assert not any("Security" in w for w in progress.warnings), "a cancel is not a failed scan"
        assert "b1" not in engine._active_containers
        assert engine.cancel_build("b1") is False, "a cancelled build stays cancelled"

    def test_cancel_while_the_scan_container_is_being_started(self, engine, client, tmp_path):
        """containers.run has not returned yet, so cancel_build cannot stop the scan: the engine must."""
        client.scan_options = dict(auto_exit=False, lines=["never read"])
        client.on_scan_run = lambda: engine.cancel_build("b1")
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        progress = finish(engine, "b1")

        scan = client.scans[0]
        assert progress.status == BuildStatus.CANCELLED
        assert scan.removed and scan.exited.is_set(), "force-removed, i.e. killed"
        assert not scan.following.is_set(), "its log is not read after the cancel"
        assert client.created == [] and not (Path(cfg.output_dir) / BUILD_MANIFEST).exists()

    def test_a_log_stream_that_breaks_on_stop_still_counts_as_cancel(self, engine, client, tmp_path):
        client.scan_options = dict(auto_exit=False, stream_error=docker.errors.NotFound("No such container: trivy-1"))
        engine.build_model(config_for(tmp_path))
        wait_for(lambda: client.scans and client.scans[0].following.is_set(), what="the Trivy log stream")
        assert engine.cancel_build("b1")
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.CANCELLED
        assert not any("Security scan failed" in w for w in progress.warnings)
        assert client.created == []

    @pytest.mark.parametrize("code, expected", [
        (0, ("log", "Security scan passed.")),
        (1, ("warning", "Security vulnerabilities found")),
        (2, ("warning", "Security scan failed to run (exit code 2)")),
    ])
    def test_scan_results_without_cancel(self, engine, client, code, expected):
        client.scan_options = dict(exit_code=code, lines=["Total: 3 (HIGH: 2, CRITICAL: 1)"])
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress
        engine._scan_image_security("img", progress, build_id="b1")
        kind, text = expected
        haystack = progress.warnings if kind == "warning" else progress.logs
        assert any(text in line for line in haystack), haystack
        assert any("TRIVY: Total: 3" in line for line in progress.logs)
        assert client.scans[0].removed and engine._active_containers == {}

    def test_a_scan_that_cannot_start_does_not_fail_the_build(self, engine, client, tmp_path):
        def missing(*args, **kwargs):
            raise docker.errors.ImageNotFound("aquasec/trivy:0.75.0 not found")

        client.containers.run = missing
        engine.build_model(config_for(tmp_path))
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.COMPLETED
        assert any("Security scan failed to run" in w for w in progress.warnings)

    def test_a_hanging_scan_is_stopped_after_the_build_timeout(self, engine, client, tmp_path):
        client.scan_options = dict(auto_exit=False, lines=["Downloading vulnerability DB..."])
        timeouts = []

        def honour_the_timeout():  # the daemon answers wait(timeout=...) with a read timeout
            scan = client.scans[-1]

            def wait(timeout=None):
                timeouts.append(timeout)
                if not scan.exited.wait(min(timeout or SAFETY, SAFETY)):
                    raise requests.exceptions.ReadTimeout("Read timed out.")
                return {"StatusCode": scan.exit_code}

            scan.wait = wait

        client.on_scan_run = honour_the_timeout
        engine.build_model(config_for(tmp_path, build_timeout=1))
        progress = finish(engine, "b1", timeout=SAFETY / 2)
        assert progress.status == BuildStatus.COMPLETED, progress.errors
        assert timeouts == [1]
        assert any("Security scan" in w and "1 s" in w for w in progress.warnings), progress.warnings
        assert client.scans[0].removed and engine._active_containers == {}


class TestCancelDuringImageBuild:

    def test_buildx_is_terminated_and_the_build_ends_cancelled(self, engine, client, tmp_path):
        proc = FakeBuildx(lines=["#5 [2/9] RUN apt-get install -y build-essential"], finish=False)
        client.buildx_factory = lambda: proc
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        wait_for(proc.reading.is_set, what="the buildx output")
        assert engine._active_processes.get("b1") is proc

        assert engine.cancel_build("b1") is True
        progress = finish(engine, "b1")

        assert proc.calls == ["terminate"]
        assert progress.status == BuildStatus.CANCELLED
        assert progress.errors == [], "the stopped image build is the cancel, not a failure"
        assert client.scans == [] and client.created == []
        assert engine._active_processes == {}
        assert not (Path(cfg.output_dir) / BUILD_MANIFEST).exists()

    def test_cancel_before_the_process_is_registered_kills_it_unread(self, engine, client, tmp_path):
        proc = FakeBuildx(lines=["never read"], finish=False)
        client.buildx_factory = lambda: proc
        progress = BuildProgress("b1", BuildStatus.CANCELLED, "")
        with pytest.raises(BuildCancelled):
            engine._build_with_buildkit("llm-framework/rockchip:b1", tmp_path / "context" / "Dockerfile", {}, progress)
        assert proc.calls == ["kill"] and not proc.reading.is_set()
        assert engine._active_processes == {}

    def test_a_failing_image_build_is_unregistered_and_reported(self, engine, client, tmp_path):
        client.buildx_factory = lambda: FakeBuildx(lines=["ERROR: No space left on device"], code=1)
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        with pytest.raises(RuntimeError, match="exited with code 1"):
            engine._build_with_buildkit("t", tmp_path / "context" / "Dockerfile", {}, progress)
        assert engine._active_processes == {}

    def test_cancel_does_not_terminate_a_finished_image_build(self, engine):
        finished = FakeBuildx()
        finished.returncode = 0
        engine._builds["b1"] = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._active_processes["b1"] = finished
        assert engine.cancel_build("b1") is True
        assert finished.calls == []

    def test_a_failing_terminate_does_not_stop_the_cancel(self, engine, client):
        proc = FakeBuildx(finish=False)

        def gone():
            raise ProcessLookupError("no such process")

        proc.terminate = gone
        container = FakeContainer(client, "llm-build-b1", running=True, auto_exit=False)
        engine._builds["b1"] = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._active_processes["b1"] = proc
        engine._active_containers["b1"] = container
        assert engine.cancel_build("b1") is True
        assert engine.get_build_status("b1").status == BuildStatus.CANCELLED
        assert ("llm-build-b1", "stop") in client.events


class TestCancelIsNeverUndone:

    @pytest.mark.parametrize("stage", [BuildStatus.PREPARING, BuildStatus.CALIBRATING, BuildStatus.BUILDING,
                                       BuildStatus.COMPLETED])
    def test_enter_stage_refuses_after_a_cancel(self, engine, stage):
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._builds["b1"] = progress
        assert engine.cancel_build("b1") is True
        with pytest.raises(BuildCancelled):
            engine._enter_stage(progress, stage)
        assert progress.status == BuildStatus.CANCELLED

    def test_enter_stage_moves_an_active_build_on(self, engine):
        progress = BuildProgress("b1", BuildStatus.PREPARING, "")
        engine._enter_stage(progress, BuildStatus.BUILDING)
        assert progress.status == BuildStatus.BUILDING

    @pytest.mark.parametrize("stage", ["_generate_imatrix", "_execute_build_modules"])
    def test_stage_functions_do_not_start_a_container_after_a_cancel(self, engine, client, tmp_path, stage):
        dataset = tmp_path / "calib.txt"
        dataset.write_text("text")
        cfg = config_for(tmp_path, use_imatrix=True, dataset_path=str(dataset))
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress
        assert engine.cancel_build("b1")
        image = SimpleNamespace(id="sha256:img", tags=["img"])
        with pytest.raises(BuildCancelled):
            getattr(engine, stage)(cfg, progress, image, engine.targets_dir / "Rockchip", ("/build-cache/m", {}))
        assert progress.status == BuildStatus.CANCELLED
        assert client.created == []

    def test_cancel_while_queued_is_not_undone_by_preparing(self, engine, client, tmp_path):
        engine._executor = held = HeldExecutor()
        engine.build_model(config_for(tmp_path))
        assert engine.get_build_status("b1").status == BuildStatus.QUEUED
        assert engine.cancel_build("b1") is True

        stages = []
        engine._resolve_dataset = lambda *a: stages.append("dataset")
        engine._prepare_build_environment = lambda *a: stages.append("prepare")
        held.run_all()

        assert engine.get_build_status("b1").status == BuildStatus.CANCELLED
        assert stages == [] and client.buildx == [] and client.scans == [] and client.created == []
        assert engine._running == set()

    def test_cancel_during_preparation_stops_before_any_container(self, engine, client, tmp_path):
        real_prepare = engine._prepare_build_environment

        def prepare_then_cancel(config, progress, target_path):
            real_prepare(config, progress, target_path)
            assert engine.cancel_build(config.build_id) is True

        engine._prepare_build_environment = prepare_then_cancel
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.CANCELLED and progress.errors == []
        assert client.scans == [] and client.created == []
        # if buildx was spawned at all, it was killed before a line of it was read
        assert all(p.calls == ["kill"] and not p.reading.is_set() for p in client.buildx)

    def test_cancel_during_the_importance_matrix_skips_the_conversion(self, engine, client, tmp_path):
        client.stage_options["llm-imatrix-"] = dict(auto_exit=False, lines=["computing imatrix chunk 1/100"])
        dataset = tmp_path / "calib.txt"
        dataset.write_text("calibration text")
        cfg = config_for(tmp_path, quantization="IQ2_XS", use_imatrix=True, dataset_path=str(dataset))
        engine.build_model(cfg)
        wait_for(lambda: client.container("llm-imatrix-b1") and client.container("llm-imatrix-b1").waiting.is_set(),
                 what="the imatrix container")

        assert engine.cancel_build("b1") is True
        progress = finish(engine, "b1")

        imatrix = client.container("llm-imatrix-b1")
        assert progress.status == BuildStatus.CANCELLED and progress.errors == []
        assert [c["name"] for c in client.created] == ["llm-imatrix-b1"]
        assert ("llm-imatrix-b1", "stop") in client.events and imatrix.removed
        assert not (Path(cfg.output_dir) / BUILD_MANIFEST).exists()

    def test_cancel_during_the_conversion_is_not_a_failed_script(self, engine, client, tmp_path):
        client.stage_options["llm-build-"] = dict(auto_exit=False, lines=["[ 12/291] quantizing blk.1.attn_q"])
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        wait_for(lambda: client.container("llm-build-b1") and client.container("llm-build-b1").waiting.is_set(),
                 what="the conversion container")

        assert engine.cancel_build("b1") is True
        progress = finish(engine, "b1")

        assert progress.status == BuildStatus.CANCELLED
        assert progress.errors == [], "exit code 137 after a stop is the cancel, not 'Build script failed'"
        assert client.container("llm-build-b1").removed
        assert not (Path(cfg.output_dir) / BUILD_MANIFEST).exists()
        assert engine._active_containers == {}

    def test_cancel_after_the_conversion_never_ends_completed(self, engine, client, tmp_path):
        real_extract = engine._extract_artifacts

        def extract_then_cancel(config, progress):
            real_extract(config, progress)
            assert engine.cancel_build(config.build_id) is True  # the user cancels while files are copied

        engine._extract_artifacts = extract_then_cancel
        engine.build_model(config_for(tmp_path))
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.CANCELLED and progress.errors == []

    def test_cancel_during_artifact_extraction_leaves_no_deployable_build(self, engine, client, tmp_path):
        real_extract = engine._extract_artifacts

        def extract_then_cancel(config, progress):
            real_extract(config, progress)
            assert engine.cancel_build(config.build_id) is True

        engine._extract_artifacts = extract_then_cancel
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        assert finish(engine, "b1").status == BuildStatus.CANCELLED
        assert not (Path(cfg.output_dir) / BUILD_MANIFEST).exists()
        assert list_builds(tmp_path / "out") == []

    def test_cancel_during_packaging_leaves_nothing_behind(self, engine, client, tmp_path, monkeypatch):
        real_hash = BuildEngine._sha256_file

        def hash_then_cancel(path, *args, **kwargs):
            engine.cancel_build("b1")  # the user cancels while a multi-GB model is hashed
            return real_hash(path, *args, **kwargs)

        monkeypatch.setattr(engine, "_sha256_file", hash_then_cancel)
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        progress = finish(engine, "b1")
        out = Path(cfg.output_dir)
        assert progress.status == BuildStatus.CANCELLED and progress.errors == []
        assert not (out / BUILD_MANIFEST).exists() and not (out.parent / f"{out.name}.zip").exists()
        assert not out.exists() or not any(out.iterdir()), "no file of the cancelled build in its output folder"
        assert progress.artifacts == [], "a cancelled build claims no artifacts"
        assert list_builds(tmp_path / "out") == []

    def test_a_cancelled_rebuild_keeps_the_earlier_build_in_its_folder(self, engine, client, tmp_path):
        cfg = config_for(tmp_path)
        out = Path(cfg.output_dir)
        out.mkdir(parents=True)
        old_model = out / FakeDockerClient.MODEL_FILE  # the same file name: the board is not part of it
        old_model.write_bytes(b"weights built for rk3588")
        old_manifest = {"build_id": "old", "board": "rk3588", "primary": old_model.name,
                        "artifacts": [{"file": old_model.name, "bytes": 24, "sha256": "x"}]}
        (out / BUILD_MANIFEST).write_text(json.dumps(old_manifest))
        client.stage_options["llm-build-"] = dict(outputs={FakeDockerClient.MODEL_FILE: b"weights for rk3566",
                                                           BUILD_MANIFEST: b'{"board": "rk3566"}'})
        real_extract = engine._extract_artifacts

        def extract_then_cancel(config, progress):
            real_extract(config, progress)
            assert engine.cancel_build(config.build_id) is True

        engine._extract_artifacts = extract_then_cancel
        engine.build_model(cfg)
        assert finish(engine, "b1").status == BuildStatus.CANCELLED
        assert old_model.read_bytes() == b"weights built for rk3588"
        assert json.loads((out / BUILD_MANIFEST).read_text()) == old_manifest
        assert [b["build_id"] for b in list_builds(tmp_path / "out")] == ["old"]

    def test_a_build_is_listed_only_once_it_is_complete(self, engine, client, tmp_path):
        real_package = engine._create_golden_artifact
        listed_while_packaging = []

        def package_then_look(config, progress):
            real_package(config, progress)
            listed_while_packaging.append(list_builds(tmp_path / "out"))

        engine._create_golden_artifact = package_then_look
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.COMPLETED, progress.errors
        assert listed_while_packaging == [[]]
        out = Path(cfg.output_dir)
        assert [b["build_id"] for b in list_builds(tmp_path / "out")] == ["b1"]
        assert (out.parent / f"{out.name}.zip").is_file()
        assert sorted(Path(a).name for a in progress.artifacts) == [FakeDockerClient.MODEL_FILE, f"{out.name}.zip"]
        assert all(Path(a).exists() for a in progress.artifacts)
        assert [p.name for p in out.parent.iterdir() if p.name.startswith(".")] == [], "no staging folder left"

    def test_a_cancel_during_the_final_cleanup_is_refused_or_complete(self, engine, client, tmp_path):
        answers = []
        client.images.on_remove = lambda name: answers.append(engine.cancel_build("b1"))
        engine.build_model(config_for(tmp_path))
        progress = finish(engine, "b1")
        listed = bool(list_builds(tmp_path / "out"))
        assert answers and (answers[0], progress.status, listed) in [(False, BuildStatus.COMPLETED, True),
                                                                     (True, BuildStatus.CANCELLED, False)]

    def test_a_cancel_during_the_cleanup_after_a_failure_is_consistent(self, engine, client, tmp_path):
        client.stage_options["llm-build-"] = dict(exit_code=1, lines=["Killed"])
        answers = []
        client.images.on_remove = lambda name: answers.append(engine.cancel_build("b1"))
        engine.build_model(config_for(tmp_path))
        progress = finish(engine, "b1")
        assert answers and (answers[0], progress.status) in [(False, BuildStatus.FAILED),
                                                             (True, BuildStatus.CANCELLED)]

    def test_cleanup_keeps_a_cancelled_build_cancelled(self, engine, client):
        progress = BuildProgress("b1", BuildStatus.CANCELLED, "")
        engine._builds["b1"] = progress
        engine._build_images["b1"] = "llm-framework/rockchip:b1"
        seen = []
        client.images.remove = lambda tag, **kw: seen.append(progress.status)
        engine.cleanup_build("b1")
        assert seen == [BuildStatus.CANCELLED], "never shown as 'cleaning' meanwhile"
        assert progress.status == BuildStatus.CANCELLED

    def test_cancel_of_a_finished_or_unknown_build_is_refused(self, engine):
        for status in TERMINAL:
            engine._builds[status.value] = BuildProgress(status.value, status, "")
            assert engine.cancel_build(status.value) is False
            assert engine._builds[status.value].status == status
        assert engine.cancel_build("nope") is False


class TestArtifactExtraction:

    def test_a_link_in_the_build_output_never_copies_a_host_file(self, engine, client, tmp_path):
        # The conversion runs target code (possibly generated) without access to the user's files; the host
        # copies its output folder, so a link there must not reach a file outside it.
        secret = tmp_path / "home" / ".ssh" / "id_ed25519"
        secret.parent.mkdir(parents=True)
        secret.write_text("PRIVATE KEY")
        try:
            (tmp_path / "probe").symlink_to(secret)
        except OSError:
            pytest.skip("symlinks not permitted")
        real_extract = engine._extract_artifacts

        def plant_links(config, progress):
            output = engine.cache_dir / "builds" / config.build_id / "output"
            (output / "id_ed25519.gguf").symlink_to(secret)
            (output / "ssh").symlink_to(secret.parent, target_is_directory=True)
            real_extract(config, progress)

        engine._extract_artifacts = plant_links
        cfg = config_for(tmp_path)
        engine.build_model(cfg)
        progress = finish(engine, "b1")
        out = Path(cfg.output_dir)
        assert progress.status == BuildStatus.COMPLETED, progress.errors
        assert [p for p in out.rglob("*") if p.is_file() and b"PRIVATE KEY" in p.read_bytes()] == []
        with zipfile.ZipFile(out.parent / f"{out.name}.zip") as zf:
            assert not any(b"PRIVATE KEY" in zf.read(n) for n in zf.namelist() if not n.endswith("/"))
        manifest = json.loads((out / BUILD_MANIFEST).read_text())
        assert [a["file"] for a in manifest["artifacts"]] == [FakeDockerClient.MODEL_FILE]
        assert any("id_ed25519.gguf" in w for w in progress.warnings), progress.warnings

    def test_a_build_below_a_folder_whose_name_starts_with_a_dot_is_listed(self, engine, client, tmp_path):
        # e.g. a local model folder ".qwen": the orchestrator builds into <out>/<target>/.qwen/<quantization>
        cfg = config_for(tmp_path, output_dir=str(tmp_path / "out" / "Rockchip" / ".qwen" / "Q4_K_M"))
        engine.build_model(cfg)
        assert finish(engine, "b1").status == BuildStatus.COMPLETED
        assert [b["build_id"] for b in list_builds(tmp_path / "out")] == ["b1"]
        assert [p.name for p in Path(cfg.output_dir).parent.iterdir()] == ["Q4_K_M", "Q4_K_M.zip"]

    def test_a_symlinked_output_folder_is_staged_on_its_own_disk(self, engine, client, tmp_path):
        disk = tmp_path / "bigdisk" / "Q4_K_M"  # the output folder moved to another disk
        disk.mkdir(parents=True)
        link = tmp_path / "out" / "Q4_K_M"
        link.parent.mkdir()
        try:
            link.symlink_to(disk, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not permitted")
        staged_in = []
        real_package = engine._create_golden_artifact

        def package_and_look(config, progress):
            real_package(config, progress)
            staged_in.extend(p.parent for p in tmp_path.rglob(".*.partial"))

        engine._create_golden_artifact = package_and_look
        engine.build_model(config_for(tmp_path, output_dir=str(link)))
        assert finish(engine, "b1").status == BuildStatus.COMPLETED
        assert staged_in == [disk.parent], "os.replace cannot move a file from one disk to another"
        assert link.is_symlink() and (disk / FakeDockerClient.MODEL_FILE).is_file()
        assert [b["build_id"] for b in list_builds(tmp_path / "out")] == ["b1"]


class TestContainerStartRace:

    def test_cancel_while_start_is_in_flight_kills_the_container(self, engine, client, tmp_path):
        gate = threading.Event()
        container = FakeContainer(client, "llm-build-b1", start_gate=gate, auto_exit=False)
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress
        outcome = {}

        def run():
            try:
                outcome["code"] = engine._run_container(config_for(tmp_path), progress, container, "CONT: ")
            except BaseException as e:  # noqa: BLE001 - the test inspects it
                outcome["error"] = e

        worker = threading.Thread(target=run)
        worker.start()
        wait_for(lambda: ("llm-build-b1", "start-requested") in client.events, what="start()")
        assert engine.cancel_build("b1") is True
        gate.set()  # the daemon finishes starting the container after the (no-op) stop
        worker.join(SAFETY)

        assert isinstance(outcome.get("error"), BuildCancelled), outcome
        assert [e for _, e in client.events] == ["start-requested", "stop-noop", "running", "kill", "remove"]
        assert not container.waiting.is_set(), "the stage did not run to its end"
        assert engine._active_containers == {}

    def test_cancel_before_start_never_starts_the_container(self, engine, client, tmp_path):
        container = FakeContainer(client, "llm-build-b1", auto_exit=False)
        progress = BuildProgress("b1", BuildStatus.CANCELLED, "")
        with pytest.raises(BuildCancelled):
            engine._run_container(config_for(tmp_path), progress, container, "CONT: ")
        assert [e for _, e in client.events] == ["remove"]


# ---------------------------------------------------------------------------------------------------
# BuildEngine: waiting for a container, timeouts
# ---------------------------------------------------------------------------------------------------

def _connection_error_after_retries():
    """What requests raises for an expired read on docker's unix socket: ConnectionError <- MaxRetryError
    <- urllib3 ReadTimeoutError (chained, not in args)."""
    try:
        try:
            raise ReadTimeoutError(None, "/containers/x/wait", "Read timed out. (read timeout=5)")
        except ReadTimeoutError as reason:
            raise MaxRetryError(None, "/containers/x/wait", reason=reason) from reason
    except MaxRetryError as e:
        try:
            raise requests.exceptions.ConnectionError(e)
        except requests.exceptions.ConnectionError as wrapped:
            return wrapped


class TestWaitErrors:

    def run_stage(self, engine, client, tmp_path, wait_error, build_timeout=5):
        container = FakeContainer(client, "llm-build-b1", auto_exit=False, lines=["building layer 3"])

        def raise_after_the_log(error=wait_error):
            container.following.wait(SAFETY)  # every line is in the progress log
            return error() if callable(error) else error

        container.wait_error = raise_after_the_log
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress
        cfg = config_for(tmp_path, build_timeout=build_timeout)
        return container, progress, lambda: engine._run_container(cfg, progress, container, "CONT: ")

    @pytest.mark.parametrize("error, message", [
        (docker.errors.NotFound("No such container: llm-build-b1"), "No such container: llm-build-b1"),
        (requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected())"), "Connection aborted"),
        (docker.errors.APIError("500 Server Error: Internal Server Error"), "500 Server Error"),
    ], ids=["not-found", "connection-error", "api-error"])
    def test_other_errors_keep_their_own_message(self, engine, client, tmp_path, error, message):
        container, progress, run = self.run_stage(engine, client, tmp_path, error)
        with pytest.raises(RuntimeError) as excinfo:
            run()
        text = str(excinfo.value)
        assert not isinstance(excinfo.value, TimeoutError)
        assert message in text and "ran longer than" not in text and "build_timeout" not in text
        assert "CONT: building layer 3" in text, "the log tail is part of the message"
        assert excinfo.value.__cause__ is error
        assert ("llm-build-b1", "kill") in client.events and container.removed
        assert engine._active_containers == {}

    @pytest.mark.parametrize("make_error", [
        lambda: requests.exceptions.ReadTimeout("UnixHTTPConnectionPool(host='localhost', port=None): Read timed out."),
        lambda: requests.exceptions.ConnectionError(ReadTimeoutError(None, "/containers/x/wait", "Read timed out.")),
        _connection_error_after_retries,
    ], ids=["read-timeout", "connection-error-wrapping-read-timeout", "chained-through-max-retries"])
    def test_a_read_timeout_is_a_build_timeout(self, engine, client, tmp_path, make_error):
        container, _, run = self.run_stage(engine, client, tmp_path, make_error)
        with pytest.raises(TimeoutError, match=r"ran longer than 5 s .*build_timeout"):
            run()
        assert ("llm-build-b1", "kill") in client.events and container.removed

    def test_any_error_after_the_timeout_has_passed_is_a_timeout(self, engine, client, tmp_path):
        def late_disconnect():
            time.sleep(0.15)
            return requests.exceptions.ConnectionError("Connection aborted.")

        _, _, run = self.run_stage(engine, client, tmp_path, late_disconnect, build_timeout=0.05)
        with pytest.raises(TimeoutError, match="ran longer than"):
            run()

    def test_an_error_caused_by_a_cancel_is_the_cancel(self, engine, client, tmp_path):
        container = FakeContainer(client, "llm-build-b1", auto_exit=False)

        def vanished_after_stop():
            container.stopped.wait(SAFETY)
            return docker.errors.NotFound("No such container: llm-build-b1")

        container.wait_error = vanished_after_stop
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress
        threading.Thread(target=lambda: container.waiting.wait(SAFETY) and engine.cancel_build("b1")).start()
        with pytest.raises(BuildCancelled):
            engine._run_container(config_for(tmp_path), progress, container, "CONT: ")
        assert container.removed

    def test_the_real_cause_reaches_the_build_errors(self, engine, client, tmp_path):
        client.stage_options["llm-build-"] = dict(auto_exit=False,
                                                  wait_error=docker.errors.NotFound("No such container: llm-build-b1"))
        engine.build_model(config_for(tmp_path))
        progress = finish(engine, "b1")
        assert progress.status == BuildStatus.FAILED
        assert "No such container: llm-build-b1" in progress.errors[-1]
        assert "build_timeout" not in progress.errors[-1]


# ---------------------------------------------------------------------------------------------------
# BuildEngine: concurrency slots
# ---------------------------------------------------------------------------------------------------

class TestConcurrencySlots:

    def blocked_in_preparation(self, engine):
        entered, release = threading.Event(), threading.Event()
        real_prepare = engine._prepare_build_environment

        def slow_prepare(config, progress, target_path):
            entered.set()
            release.wait(SAFETY)
            real_prepare(config, progress, target_path)

        engine._prepare_build_environment = slow_prepare
        return entered, release, real_prepare

    def test_a_cancelled_build_keeps_its_slot_until_its_worker_returns(self, make_engine, client, tmp_path):
        engine = make_engine(max_concurrent_builds=1)
        entered, release, real_prepare = self.blocked_in_preparation(engine)
        engine.build_model(config_for(tmp_path, build_id="A"))
        assert entered.wait(5)
        assert engine.cancel_build("A") is True

        with pytest.raises(RuntimeError, match="Max concurrent builds reached"):
            engine.build_model(config_for(tmp_path, build_id="B"))
        assert engine.get_build_status("B") is None, "a refused build leaves no progress behind"

        release.set()
        assert finish(engine, "A").status == BuildStatus.CANCELLED
        assert client.scans == [] and client.created == []

        engine._prepare_build_environment = real_prepare
        engine.build_model(config_for(tmp_path, build_id="B"))
        assert finish(engine, "B").status == BuildStatus.COMPLETED

    def test_a_running_build_id_cannot_be_submitted_again(self, engine, tmp_path):
        entered, release, _ = self.blocked_in_preparation(engine)
        engine.build_model(config_for(tmp_path, build_id="A"))
        assert entered.wait(5)
        assert engine.cancel_build("A")
        first = engine.get_build_status("A")
        with pytest.raises(RuntimeError, match="still running"):
            engine.build_model(config_for(tmp_path, build_id="A"))
        assert engine.get_build_status("A") is first, "the running build's progress is not replaced"
        release.set()
        assert finish(engine, "A") is first and first.status == BuildStatus.CANCELLED

    def test_a_refused_submission_releases_its_slot(self, make_engine, tmp_path):
        engine = make_engine(max_concurrent_builds=1)
        engine._executor.shutdown()
        with pytest.raises(RuntimeError):
            engine.build_model(config_for(tmp_path, build_id="A"))
        assert engine._running == set()
        engine._executor = ThreadPoolExecutor(max_workers=1)
        engine.build_model(config_for(tmp_path, build_id="B"))
        assert finish(engine, "B").status == BuildStatus.COMPLETED

    def test_a_failed_build_is_not_terminal_while_its_cleanup_holds_the_slot(self, engine, client, tmp_path):
        """Pollers see CLEANING, not FAILED, while the worker removes the image and the build folder,
        so the orchestrator does not submit its next job into a slot that is still taken."""
        client.stage_options["llm-build-"] = dict(exit_code=1, lines=["Killed"])
        seen = []
        client.images.on_remove = lambda name: seen.append((engine.get_build_status("b1").status,
                                                                "b1" in engine._running))
        engine.build_model(config_for(tmp_path))
        assert finish(engine, "b1").status == BuildStatus.FAILED
        assert seen == [(BuildStatus.CLEANING, True)]

    def test_next_job_is_not_refused_before_the_failed_worker_returns(self, make_engine, client, tmp_path,
                                                                      fast_polling):
        engine = make_engine(max_concurrent_builds=2)  # the framework's default
        client.stage_options["llm-build-rA_001"] = dict(auto_exit=False)  # another request's long conversion
        client.stage_options["llm-build-r1_001"] = dict(exit_code=1, lines=["Killed"])
        attempts, refused = [], []
        real_build_model = engine.build_model

        def recording_build_model(config):
            try:
                return real_build_model(config)
            except RuntimeError as e:
                refused.append((config.build_id, str(e)))
                raise
            finally:
                attempts.append(config.build_id)

        engine.build_model = recording_build_model

        class SlowErrorLog:
            """Widens the real window between `status = FAILED` and cleanup_build (add_error and
            logger.error with a traceback, normally about a millisecond) so a poll lands in it."""

            def __init__(self, logger):
                self._logger = logger

            def __getattr__(self, name):
                return getattr(self._logger, name)

            def error(self, *args, **kwargs):
                deadline = time.monotonic() + 2
                while "r1_002" not in attempts and time.monotonic() < deadline:
                    time.sleep(0.005)
                return self._logger.error(*args, **kwargs)

        engine.logger = SlowErrorLog(engine.logger)
        orch = LLMOrchestrator(engine.config, build_engine=engine)

        async def scenario():
            await orch.submit_build_request(make_request(tmp_path, request_id="rA",
                                                         output_base_dir=str(tmp_path / "outA")))
            await until(lambda: client.container("llm-build-rA_001") is not None
                        and client.container("llm-build-rA_001").waiting.is_set(), what="request rA's conversion")
            await orch.submit_build_request(make_request(tmp_path, quantization_options=["Q4_K_M", "Q8_0"]))
            await until(lambda: orch._workflows["r1"].status in (OrchestrationStatus.COMPLETED,
                                                                 OrchestrationStatus.ERROR), timeout=10)
            client.container("llm-build-rA_001")._finish(0)
            await until(lambda: not orch._active_tasks, timeout=10)
            return orch._workflows["r1"]

        state = asyncio.run(scenario())
        assert refused == [], "r1_002 was refused although r1_001 had already reported FAILED"
        assert [c["name"] for c in client.created if c["name"].startswith("llm-build-r1")] == \
            ["llm-build-r1_001", "llm-build-r1_002"]
        assert state.completed_builds == 1 and state.failed_builds == 1

    def test_a_job_waits_for_a_slot_held_by_a_cancelled_build(self, make_engine, client, tmp_path, fast_polling):
        engine = make_engine(max_concurrent_builds=1)
        entered, release, _ = self.blocked_in_preparation(engine)  # e.g. a long model download
        refused = []
        real_build_model = engine.build_model

        def recording_build_model(config):
            try:
                return real_build_model(config)
            except RuntimeError as e:
                refused.append((config.build_id, str(e)))
                raise

        engine.build_model = recording_build_model
        orch = LLMOrchestrator(engine.config, build_engine=engine)

        async def scenario():
            await orch.submit_build_request(make_request(tmp_path, request_id="rA",
                                                         output_base_dir=str(tmp_path / "outA")))
            await until(entered.is_set, what="rA's build in preparation")
            assert await orch.cancel_request("rA") is True
            await orch.submit_build_request(make_request(tmp_path, request_id="rB"))
            await until(lambda: refused or orch._workflows["rB"].status == OrchestrationStatus.ERROR,
                        what="rB's first attempt")
            release.set()  # rA's worker stops; its slot becomes free
            await until(lambda: orch._workflows["rB"].status in (OrchestrationStatus.COMPLETED,
                                                                 OrchestrationStatus.ERROR), timeout=10)
            await until(lambda: not orch._active_tasks, timeout=10)
            return orch._workflows["rB"]

        state = asyncio.run(scenario())
        assert refused and refused[0] == ("rB_001", "Max concurrent builds reached")
        assert state.status == OrchestrationStatus.COMPLETED, state.errors
        assert state.completed_builds == 1 and state.failed_builds == 0
        assert engine.get_build_status("rA_001").status == BuildStatus.CANCELLED
        assert engine.get_build_status("rB_001").status == BuildStatus.COMPLETED

    def test_a_cancel_while_waiting_for_a_slot_ends_the_wait(self, make_engine, client, tmp_path, fast_polling):
        engine = make_engine(max_concurrent_builds=1)
        entered, release, _ = self.blocked_in_preparation(engine)
        attempts = []
        real_build_model = engine.build_model

        def recording_build_model(config):
            attempts.append(config.build_id)
            return real_build_model(config)

        engine.build_model = recording_build_model
        orch = LLMOrchestrator(engine.config, build_engine=engine)

        async def scenario():
            await orch.submit_build_request(make_request(tmp_path, request_id="rA",
                                                         output_base_dir=str(tmp_path / "outA")))
            await until(entered.is_set, what="rA's build in preparation")
            await orch.submit_build_request(make_request(tmp_path, request_id="rB"))
            await until(lambda: "rB_001" in attempts, what="rB's first attempt")
            assert await orch.cancel_request("rB") is True
            await until(lambda: "rB" not in orch._active_tasks, what="rB's pipeline")
            release.set()
            await until(lambda: not orch._active_tasks, timeout=10)
            return orch._workflows["rB"]

        state = asyncio.run(scenario())
        assert state.status == OrchestrationStatus.CANCELLED
        assert engine.get_build_status("rB_001") is None, "never submitted after the cancel"
        assert finish(engine, "rA_001").status == BuildStatus.COMPLETED


# ---------------------------------------------------------------------------------------------------
# Orchestrator: cancel during submission, retries, calibration data
# ---------------------------------------------------------------------------------------------------

class TestOrchestratorCancel:

    def test_build_id_is_recorded_before_the_engine_sees_it(self, fake_orchestrator, tmp_path):
        seen = []
        real_build_model = fake_orchestrator.build_engine.build_model

        def build_model(config):
            seen.append(list(fake_orchestrator._workflows["r1"].build_ids))
            return real_build_model(config)

        fake_orchestrator.build_engine.build_model = build_model
        state = run_pipeline(fake_orchestrator, make_request(tmp_path, quantization_options=["Q4_K_M", "Q8_0"]))
        assert seen == [["r1_001"], ["r1_001", "r1_002"]]
        assert state.build_ids == ["r1_001", "r1_002"] and state.status == OrchestrationStatus.COMPLETED

    def test_a_repeated_attempt_is_recorded_once(self, fake_orchestrator, tmp_path):
        req = make_request(tmp_path)
        state = WorkflowState("r1", OrchestrationStatus.BUILDING, datetime.now())
        fake_orchestrator._workflows["r1"] = state
        job = BuildJob("r1_001", req.models[0], "Rockchip", ModelFormat.GGUF, OptimizationLevel.BALANCED,
                       "Q4_K_M", str(tmp_path / "out"), BuildStatus.QUEUED)

        async def twice():
            return [await fake_orchestrator._run_job(job, req), await fake_orchestrator._run_job(job, req)]

        assert asyncio.run(twice()) == [True, True]
        assert state.build_ids == ["r1_001"]

    def test_a_request_with_nothing_to_build_is_an_error(self, fake_orchestrator, tmp_path):
        state = run_pipeline(fake_orchestrator, make_request(tmp_path, targets=["NoSuchTarget"]))
        assert fake_orchestrator.build_engine.configs == []
        assert state.status == OrchestrationStatus.ERROR
        assert any("'NoSuchTarget' not found" in w for w in state.warnings)
        assert state.errors and state.end_time is not None and state.total_builds == 0

    @pytest.mark.parametrize("attempt, build_id", [(1, "r1_001"), (2, "r1_001_r1"), (3, "r1_001_r2")])
    def test_every_attempt_has_its_own_build_id(self, fake_orchestrator, tmp_path, attempt, build_id):
        job = BuildJob("r1_001", "a/b", "Rockchip", ModelFormat.GGUF, OptimizationLevel.BALANCED, "Q4_K_M",
                       str(tmp_path / "out"), BuildStatus.QUEUED)
        config = fake_orchestrator._map_job_to_config(job, make_request(tmp_path), attempt)
        assert config.build_id == build_id and config.output_dir == job.output_path

    def test_cancel_while_build_model_runs_cancels_the_submitted_build(self, engine, client, tmp_path, fast_polling):
        client.stage_options["llm-build-"] = dict(auto_exit=False)  # only a cancel ends this build
        orch = LLMOrchestrator(engine.config, build_engine=engine)
        entered, release = threading.Event(), threading.Event()
        real_build_model, real_cancel = engine.build_model, engine.cancel_build
        cancels = []

        def slow_build_model(config):
            entered.set()
            release.wait(SAFETY)
            return real_build_model(config)

        def recording_cancel(build_id):
            result = real_cancel(build_id)
            cancels.append((build_id, result))
            return result

        engine.build_model, engine.cancel_build = slow_build_model, recording_cancel

        async def scenario():
            rid = await orch.submit_build_request(make_request(tmp_path))
            await until(entered.is_set, what="build_model in the executor")
            state = orch._workflows[rid]
            assert state.build_ids == ["r1_001"], "known before the engine has it"
            assert await orch.cancel_request(rid) is True
            assert engine.get_build_status("r1_001") is None
            release.set()
            await until(lambda: ("r1_001", True) in cancels, what="cancel_build for the submitted build")
            await until(lambda: "r1_001" not in engine._running, what="the build's worker")
            return state

        state = asyncio.run(scenario())
        assert state.status == OrchestrationStatus.CANCELLED
        assert cancels[0] == ("r1_001", False), "the first attempt found no build yet"
        assert engine.get_build_status("r1_001").status == BuildStatus.CANCELLED
        assert not list(tmp_path.joinpath("out").rglob(BUILD_MANIFEST))

    def test_a_build_returned_into_a_cancelled_request_is_cancelled(self, engine, client, tmp_path, fast_polling):
        """cancel_request ran while build_model was in the executor, but the task was not interrupted."""
        client.stage_options["llm-build-"] = dict(auto_exit=False)
        orch = LLMOrchestrator(engine.config, build_engine=engine)
        req = make_request(tmp_path)
        state = WorkflowState("r1", OrchestrationStatus.BUILDING, datetime.now())
        orch._workflows["r1"] = state
        real_build_model = engine.build_model

        def build_model_racing_a_cancel(config):
            state.status = OrchestrationStatus.CANCELLED          # cancel_request marked the request ...
            assert engine.cancel_build(config.build_id) is False  # ... and found nothing to cancel yet
            return real_build_model(config)

        engine.build_model = build_model_racing_a_cancel
        job = BuildJob("r1_001", req.models[0], "Rockchip", ModelFormat.GGUF, OptimizationLevel.BALANCED, "Q4_K_M",
                       str(tmp_path / "out" / "Q4_K_M"), BuildStatus.QUEUED)

        assert asyncio.run(orch._run_job(job, req)) is False
        assert job.error_log == "Cancelled by the user."
        assert finish(engine, "r1_001").status == BuildStatus.CANCELLED
        assert not list(tmp_path.joinpath("out").rglob(BUILD_MANIFEST))

    def test_cancel_during_a_retry_reaches_the_retry_build(self, tmp_path, fast_polling):
        cfg = ConfigManager(tmp_path / "configs")
        (tmp_path / "targets" / "Rockchip").mkdir(parents=True)
        cfg.set("targets_dir", str(tmp_path / "targets"))
        engine = RecordingEngine()
        orch = LLMOrchestrator(cfg, build_engine=engine)
        orch.self_healing = SimpleNamespace(analyze_error=lambda log, ctx: proposal(), apply_fix=lambda p: True)

        async def scenario():
            rid = await orch.submit_build_request(make_request(tmp_path))
            await until(lambda: orch._workflows[rid].status == OrchestrationStatus.AWAITING_APPROVAL)
            assert orch.resolve_healing(rid, True)
            await until(lambda: "r1_001_r1" in engine.builds, what="the retry")
            assert await orch.cancel_request(rid) is True
            await until(lambda: not orch._active_tasks)
            return orch._workflows[rid]

        state = asyncio.run(scenario())
        assert state.status == OrchestrationStatus.CANCELLED
        assert state.build_ids == ["r1_001", "r1_001_r1"]
        assert ("r1_001_r1", True) in engine.cancels
        assert engine.builds["r1_001_r1"].status == BuildStatus.CANCELLED
        assert engine.builds["r1_001"].status == BuildStatus.FAILED, "the failed attempt keeps its outcome"

    def test_a_request_cancelled_during_the_final_cleanup_lists_no_build(self, engine, client, tmp_path,
                                                                         fast_polling):
        orch = LLMOrchestrator(engine.config, build_engine=engine)
        in_cleanup, release = threading.Event(), threading.Event()

        def slow_cleanup(name=None):  # the final cleanup can take a while
            in_cleanup.set()
            release.wait(SAFETY)

        client.images.on_remove = slow_cleanup

        async def scenario():
            rid = await orch.submit_build_request(make_request(tmp_path))
            await until(in_cleanup.is_set, what="the final cleanup")
            assert await orch.cancel_request(rid) is True  # e.g. BuildService.cancel from an MCP client
            release.set()
            await until(lambda: "r1_001" not in engine._running, what="the build's worker")
            await until(lambda: not orch._active_tasks)
            return orch._workflows[rid]

        state = asyncio.run(scenario())
        assert state.status == OrchestrationStatus.CANCELLED
        assert engine.get_build_status("r1_001").status == BuildStatus.CANCELLED
        assert list_builds(tmp_path / "out") == []

    def test_a_build_too_far_along_to_cancel_is_reported(self, engine, client, tmp_path, fast_polling):
        orch = LLMOrchestrator(engine.config, build_engine=engine)
        publishing, release = threading.Event(), threading.Event()
        real_publish = engine._publish_artifacts

        def slow_publish(config, progress):
            publishing.set()
            release.wait(SAFETY)
            real_publish(config, progress)

        engine._publish_artifacts = slow_publish

        async def scenario():
            rid = await orch.submit_build_request(make_request(tmp_path))
            await until(publishing.is_set, what="publishing")
            assert await orch.cancel_request(rid) is True
            release.set()
            await until(lambda: "r1_001" not in engine._running, what="the build's worker")
            await until(lambda: not orch._active_tasks)
            return orch._workflows[rid]

        state = asyncio.run(scenario())
        assert state.status == OrchestrationStatus.CANCELLED
        assert engine.get_build_status("r1_001").status == BuildStatus.COMPLETED
        assert [b["build_id"] for b in list_builds(tmp_path / "out")] == ["r1_001"]
        assert any("r1_001" in w and "could no longer be cancelled" in w for w in state.warnings), state.warnings


class TestHealingRetryLog:

    def test_retry_keeps_both_attempts_and_log_tail_shows_each_once(self, engine, client, tmp_path, fast_polling):
        client.stage_options["llm-build-r1_001"] = dict(exit_code=1, lines=["attempt 1: Killed"])
        client.stage_options["llm-build-r1_001_r1"] = dict(lines=["attempt 2: quantizing"])
        orch = LLMOrchestrator(engine.config, build_engine=engine)
        analyzed = []
        orch.self_healing = SimpleNamespace(analyze_error=lambda log, ctx: analyzed.append(log) or proposal(),
                                            apply_fix=lambda p: True)
        service = BuildService(SimpleNamespace(orchestrator=orch))

        async def scenario():
            rid = await orch.submit_build_request(make_request(tmp_path))
            await until(lambda: orch._workflows[rid].status == OrchestrationStatus.AWAITING_APPROVAL,
                        timeout=10, what="the healing proposal")
            service.resolve_healing(rid, True)  # a configuration change is applied automatically
            await until(lambda: not orch._active_tasks, timeout=10, what="the pipeline")
            return orch._workflows[rid]

        state = asyncio.run(scenario())

        assert state.status == OrchestrationStatus.COMPLETED, state.errors
        assert (state.completed_builds, state.failed_builds) == (1, 0)
        assert state.build_ids == ["r1_001", "r1_001_r1"]
        assert [c["name"] for c in client.created] == ["llm-build-r1_001", "llm-build-r1_001_r1"]
        first, retry = engine.get_build_status("r1_001"), engine.get_build_status("r1_001_r1")
        assert first is not retry
        assert (first.status, retry.status) == (BuildStatus.FAILED, BuildStatus.COMPLETED)
        assert "attempt 1: Killed" in analyzed[0], "the healed failure reached the analysis"
        assert any("attempt 1: Killed" in line for line in first.logs)
        assert any("attempt 2: quantizing" in line for line in retry.logs)
        assert not any("attempt 1" in line for line in retry.logs)

        lines = service.log_tail("r1", lines=2000).lines
        assert lines == ["===== r1_001 =====", *map(redact_secrets, first.logs),
                         "===== r1_001_r1 =====", *map(redact_secrets, retry.logs)]

    @staticmethod
    def service_for(build_ids, logs):
        state = WorkflowState("r1", OrchestrationStatus.COMPLETED, datetime.now())
        state.build_ids = list(build_ids)
        engine = SimpleNamespace(get_build_status=lambda bid: SimpleNamespace(logs=logs[bid]) if bid in logs else None)
        return BuildService(SimpleNamespace(orchestrator=SimpleNamespace(_workflows={"r1": state},
                                                                         build_engine=engine)))

    def test_a_single_attempt_has_no_separator(self):
        assert self.service_for(["r1_001"], {"r1_001": ["a", "b"]}).log_tail("r1").lines == ["a", "b"]

    def test_duplicate_ids_are_listed_once(self):
        svc = self.service_for(["r1_001", "r1_001"], {"r1_001": ["a", "b"]})
        assert svc.log_tail("r1").lines == ["a", "b"]
        svc = self.service_for(["r1_001", "r1_001_r1", "r1_001_r1"], {"r1_001": ["a"], "r1_001_r1": ["b"]})
        assert svc.log_tail("r1").lines == ["===== r1_001 =====", "a", "===== r1_001_r1 =====", "b"]

    def test_the_line_window_and_redaction_cover_every_attempt(self):
        secret = "sk-abcdefghijklmnopqrstuvwx1234"
        svc = self.service_for(["r1_001", "r1_001_r1"], {"r1_001": [f"token {secret}"], "r1_001_r1": ["x", "y"]})
        assert secret not in "\n".join(svc.log_tail("r1").lines)
        assert svc.log_tail("r1", lines=2).lines == ["x", "y"]

    def test_an_attempt_the_engine_no_longer_knows_is_skipped(self):
        svc = self.service_for(["r1_001", "r1_001_r1"], {"r1_001_r1": ["b"]})
        assert svc.log_tail("r1").lines == ["===== r1_001_r1 =====", "b"]


class TestCalibrationDataFailure:

    @pytest.fixture
    def broken_calibration(self, monkeypatch):
        def fail(path, language):
            raise CalibrationDataError("calibration_de.txt.gz: checksum mismatch")

        monkeypatch.setattr("orchestrator.Core.orchestrator.write_calibration_dataset", fail)

    @pytest.mark.parametrize("quants, named", [
        (["IQ2_XS"], "IQ2_XS"),
        (["iq2_xs"], "iq2_xs"),
        (["Q4_K_M", "IQ1_S"], "IQ1_S"),
    ])
    def test_low_bit_types_end_the_request_in_error(self, fake_orchestrator, tmp_path, broken_calibration,
                                                     quants, named):
        req = make_request(tmp_path, use_imatrix=True, quantization_options=quants)
        state = run_pipeline(fake_orchestrator, req)
        assert state.status == OrchestrationStatus.ERROR
        assert fake_orchestrator.build_engine.configs == [], "nothing is built"
        assert any(named in e and "importance matrix" in e and "checksum mismatch" in e for e in state.errors), \
            state.errors
        assert "Q4_K_M" not in " ".join(state.errors)
        assert not any("IMatrix disabled" in w for w in state.warnings)
        assert state.end_time is not None

    def test_an_unwritable_cache_is_an_error_too(self, fake_orchestrator, tmp_path, monkeypatch):
        def disk_full(path, language):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("orchestrator.Core.orchestrator.write_calibration_dataset", disk_full)
        state = run_pipeline(fake_orchestrator, make_request(tmp_path, use_imatrix=True, quantization_options=["IQ2_S"]))
        assert state.status == OrchestrationStatus.ERROR
        assert any("No space left on device" in e for e in state.errors)

    def test_other_types_only_lose_the_matrix(self, fake_orchestrator, tmp_path, broken_calibration):
        state = run_pipeline(fake_orchestrator, make_request(tmp_path, use_imatrix=True,
                                                             quantization_options=["Q4_K_M"]))
        assert state.status == OrchestrationStatus.COMPLETED
        assert any("IMatrix disabled" in w for w in state.warnings)
        config = fake_orchestrator.build_engine.configs[0]
        assert config.use_imatrix is False and config.dataset_path is None

    def test_the_error_survives_the_queue_worker(self, fake_orchestrator, tmp_path, broken_calibration):
        async def scenario():
            rid = await fake_orchestrator.submit_build_request(
                make_request(tmp_path, use_imatrix=True, quantization_options=["IQ2_XS"]))
            await until(lambda: fake_orchestrator._workflows[rid].status == OrchestrationStatus.ERROR
                        and not fake_orchestrator._active_tasks)
            return fake_orchestrator._workflows[rid]

        state = asyncio.run(scenario())
        assert state.status == OrchestrationStatus.ERROR and fake_orchestrator.build_engine.configs == []


# ---------------------------------------------------------------------------------------------------
# The security scan reads a saved archive: the scanner never gets the Docker socket (round-6 design, S0 b)
# ---------------------------------------------------------------------------------------------------

class TestScanWithoutTheDockerSocket:
    TAG = "llm-framework/rockchip:b1"

    @staticmethod
    def scan(engine, tag=TAG):
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress
        engine._scan_image_security(tag, progress, build_id="b1")
        return progress

    def test_trivy_scan_mounts_no_docker_socket(self, engine, client):
        progress = self.scan(engine)
        (run,) = client.scan_runs
        mounts = {str(host): bind["bind"] for host, bind in run["volumes"].items()}
        assert not any("docker.sock" in text for pair in mounts.items() for text in pair), mounts
        # Nothing else of this computer either (a socket under another name, /var/run, /run): the archive and the
        # scanner's own database volume only.
        assert sorted(mounts.values()) == ["/root/.cache/", builder_module.SCAN_ARCHIVE], mounts
        assert "trivy_cache" in mounts and not run.get("privileged") and run.get("network_mode") != "host"
        archive = next(host for host, target in mounts.items() if target == builder_module.SCAN_ARCHIVE)
        assert run["volumes"][archive]["mode"] == "ro"
        assert run["command"][:3] == ["image", "--input", builder_module.SCAN_ARCHIVE]
        assert self.TAG not in run["command"], "Trivy reads the archive, not the image through the daemon"
        assert run["cap_drop"] == ["ALL"] and "no-new-privileges:true" in run["security_opt"]
        content, mode, folder_mode = run["files"][archive]
        assert content == b"image.tar", "the archive is what docker save streamed"
        if os.name == "posix":
            assert mode == 0o644 and folder_mode == 0o700, "readable for the scanner, inside a private folder"
        assert not Path(archive).parent.exists(), "the temporary archive is removed after the scan"
        assert any("Removed the temporary image archive" in line for line in progress.logs)
        assert any("Security scan passed." in line for line in progress.logs)

    def test_a_refused_scan_names_the_archive_by_label_not_by_its_host_path(self, engine, client):
        """Docker names a bind mount's host path when it refuses it, and build warnings reach AI clients
        (get_build_status): the cache folder appears as 'cache:...', never with the user's folders."""
        def refuse(image, command=None, volumes=None, detach=False, **kwargs):
            host = next(h for h, bind in volumes.items() if bind["bind"] == builder_module.SCAN_ARCHIVE)
            raise RuntimeError(f'invalid mount config for type "bind": bind source path does not exist: {host}')

        client.containers.run = refuse
        progress = self.scan(engine)
        (warning,) = [w for w in progress.warnings if w.startswith("Security scan failed to run")]
        assert str(engine.cache_dir) not in warning and os.path.realpath(engine.cache_dir) not in warning
        assert "cache:builds/b1/trivy-" in warning and warning.endswith("image.tar")
        assert not list((engine.cache_dir / "builds" / "b1").glob("trivy-*")), "the archive is removed anyway"

    def test_no_room_for_the_archive_skips_the_scan_and_says_why(self, engine, client, monkeypatch):
        monkeypatch.setattr(builder_module.shutil, "disk_usage", lambda path: SimpleNamespace(
            total=100 * 1000 ** 3, used=100 * 1000 ** 3, free=0))
        progress = self.scan(engine)
        assert client.scan_runs == []
        assert any("Security scan skipped" in w and "must stay free" in w for w in progress.warnings)
        assert not list((engine.cache_dir / "builds" / "b1").glob("trivy-*"))

    def test_a_cancel_while_the_image_is_saved_leaves_no_archive(self, engine, client):
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        engine._builds["b1"] = progress

        def save(named=False):
            yield b"first part"
            progress.status = BuildStatus.CANCELLED
            yield b"second part"

        client.images.get = lambda tag: SimpleNamespace(id="sha256:x", tags=[tag], attrs={"Size": 9}, save=save)
        with pytest.raises(BuildCancelled):
            engine._scan_image_security(self.TAG, progress, build_id="b1")
        assert client.scan_runs == []
        assert not list((engine.cache_dir / "builds" / "b1").glob("trivy-*"))
