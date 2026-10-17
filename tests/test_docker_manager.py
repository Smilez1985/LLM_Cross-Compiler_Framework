"""The Docker manager and the build containers' CPU and memory (DECISIONS round 12, item 6).

DockerManager came first, with a build path of its own (start, a monitor thread, container stats, a healing signal,
stop). Builds now run through one path (BuildService -> LLMOrchestrator -> BuildEngine). What only the old path
could do is wired into it: the engine samples the running stage container's CPU and memory (Docker's own formulas),
the person's Builder shows them under the progress bar, and the build log warns once when the container nears its
memory limit (Docker's, or the WSL2 / Docker Desktop VM's). The healing proposal after a failure was already the one
path's (LLMOrchestrator._heal_and_retry asks the person in the GUI and the CLI). DockerManager keeps the Docker client
and the build engine's construction, without Qt.
"""

import threading
from types import SimpleNamespace

import pytest

from orchestrator.Core import docker_manager
from orchestrator.Core.builder import BuildProgress, BuildStatus
from orchestrator.Core.docker_manager import DockerManager

MIB = 1024 * 1024


def stats_sample(total=2_000_000, pre_total=1_000_000, system=20_000_000, pre_system=10_000_000, cpus=4,
                 usage=900 * MIB, limit=1000 * MIB, inactive=None, v1_inactive=None):
    memory = {"usage": usage, "limit": limit, "stats": {}}
    if inactive is not None:
        memory["stats"]["inactive_file"] = inactive
    if v1_inactive is not None:
        memory["stats"]["total_inactive_file"] = v1_inactive
    return {"cpu_stats": {"cpu_usage": {"total_usage": total, "percpu_usage": [0] * cpus},
                          "system_cpu_usage": system, "online_cpus": cpus},
            "precpu_stats": {"cpu_usage": {"total_usage": pre_total}, "system_cpu_usage": pre_system},
            "memory_stats": memory}


# --- characterization: what the manager does today ------------------------------------------------------------------

class FakeEngine:
    made = []

    def __init__(self, config, base_dir=None, secrets_manager=None, fail_ping=False):
        self.args = (config, base_dir, secrets_manager)
        FakeEngine.made.append(self)
        self.docker_client = SimpleNamespace(ping=self._ping)
        self.fail_ping = False

    def _ping(self):
        if self.fail_ping:
            raise ConnectionError("no daemon")
        return True


@pytest.fixture
def fake_engine(monkeypatch):
    from orchestrator.Core import builder
    FakeEngine.made = []
    monkeypatch.setattr(builder, "BuildEngine", FakeEngine)
    return FakeEngine


def test_initialize_makes_the_one_build_engine_and_pings_docker(fake_engine):
    config, secrets = object(), object()
    manager = DockerManager(config, base_dir="/install", secrets_manager=secrets)
    assert manager.initialize() is True
    assert manager.builder is fake_engine.made[0] and manager.builder.args == (config, "/install", secrets)


def test_initialize_says_false_when_docker_does_not_answer_and_keeps_the_engine(fake_engine, monkeypatch):
    monkeypatch.setattr(FakeEngine, "_ping", lambda self: (_ for _ in ()).throw(ConnectionError("no daemon")))
    manager = DockerManager(object())
    assert manager.initialize() is False and manager.builder is not None, "builds stay possible once Docker runs"


def test_initialize_says_false_when_the_engine_cannot_be_made(monkeypatch):
    from orchestrator.Core import builder

    def broken(*args, **kwargs):
        raise RuntimeError("broken")
    monkeypatch.setattr(builder, "BuildEngine", broken)
    manager = DockerManager(object())
    assert manager.initialize() is False and manager.builder is None


def test_cpu_percent_is_dockers_formula():
    """(container CPU delta / host CPU delta) x online CPUs x 100, rounded to 2 places; 0.0 without a delta or data."""
    assert docker_manager.cpu_percent(stats_sample()) == 40.0
    assert docker_manager.cpu_percent(stats_sample(total=1_000_000)) == 0.0
    assert docker_manager.cpu_percent(stats_sample(system=10_000_000)) == 0.0
    assert docker_manager.cpu_percent({"cpu_stats": {}}) == 0.0
    no_online = stats_sample()
    del no_online["cpu_stats"]["online_cpus"]
    assert docker_manager.cpu_percent(no_online) == 40.0, "the per-CPU list counts the CPUs"


# --- the build containers' CPU and memory in the one build path -----------------------------------------------------

def test_container_resources_count_memory_as_docker_stats_does():
    sample = docker_manager.container_resources(stats_sample(usage=900 * MIB, limit=1000 * MIB, inactive=300 * MIB))
    assert sample == {"cpu_percent": 40.0, "memory_bytes": 600.0 * MIB, "memory_limit_bytes": 1000.0 * MIB}
    assert docker_manager.container_resources(stats_sample(v1_inactive=100 * MIB))["memory_bytes"] == 800.0 * MIB
    assert docker_manager.container_resources(stats_sample())["memory_bytes"] == 900.0 * MIB, "no cache figure"
    assert docker_manager.container_resources({"memory_stats": {}}) is None, "an ended container has no figures"
    assert docker_manager.container_resources(None) is None
    assert docker_manager.memory_near_limit({"memory_bytes": 900.0, "memory_limit_bytes": 1000.0})
    assert not docker_manager.memory_near_limit({"memory_bytes": 899.0, "memory_limit_bytes": 1000.0})
    assert not docker_manager.memory_near_limit({"memory_bytes": 900.0, "memory_limit_bytes": 0.0})
    assert not docker_manager.memory_near_limit(None)


def test_memory_on_a_cgroup_v1_host_leaves_out_the_hierarchical_cache_as_docker_stats_does():
    """Review of chain qt: a cgroup v1 host reports both inactive_file (the container's own cgroup) and
    total_inactive_file (with its child cgroups). `docker stats` subtracts total_inactive_file there, and inactive_file
    only when the v1 figure is missing or not below the usage (cgroup v2); the first version took inactive_file first,
    so a container with child cgroups counted their page cache as memory the build needs."""
    v1 = stats_sample(usage=900 * MIB, inactive=100 * MIB, v1_inactive=300 * MIB)
    assert docker_manager.container_resources(v1)["memory_bytes"] == 600.0 * MIB
    odd = stats_sample(usage=900 * MIB, inactive=100 * MIB, v1_inactive=900 * MIB)
    assert docker_manager.container_resources(odd)["memory_bytes"] == 800.0 * MIB, "then the v2 figure, as docker does"
    assert docker_manager.memory_used({"usage": 900, "stats": {"inactive_file": 950}}) == 900, "never below zero"


class StatsContainer:
    """A stage container whose statistics the engine reads; it runs until the engine read them `reads_before_end`
    times."""

    def __init__(self, samples, reads_before_end=3):
        self.samples, self.reads, self.reads_before_end = list(samples), 0, reads_before_end
        self.seen_while_running = []
        self.progress = None
        self.enough = threading.Event()

    def start(self):
        pass

    def logs(self, stream=True, follow=True):
        return iter([b"converting\n"])

    def stats(self, stream=False):
        assert stream is False, "one sample per read"
        self.reads += 1
        sample = self.samples[min(self.reads, len(self.samples)) - 1]
        if self.reads >= self.reads_before_end:
            self.enough.set()
        return sample

    def wait(self, timeout=None):
        assert self.enough.wait(10), "the engine never read the container's statistics"
        for _ in range(200):   # the last read's figures reach the progress
            if self.progress.resources is not None and any("memory limit" in line for line in self.progress.logs):
                break
            threading.Event().wait(0.01)
        self.seen_while_running.append(self.progress.resources)
        return {"StatusCode": 0}

    def kill(self):
        pass

    def remove(self, force=True):
        pass


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from orchestrator.Core import builder
    from orchestrator.Core.builder import BuildEngine
    monkeypatch.setattr(builder, "RESOURCES_EVERY", 0.01)
    monkeypatch.setattr(builder, "CONTAINER_WATCH_EVERY", 60)
    e = BuildEngine.__new__(BuildEngine)
    e.cache_dir, e.base_dir, e.default_timeout = tmp_path / "cache", tmp_path, 60
    e._lock, e._active_containers = threading.Lock(), {}
    e.logger = SimpleNamespace(info=print, warning=print, debug=print, error=print)
    return e


def build_config():
    from orchestrator.Core.builder import BuildConfiguration, ModelFormat
    return BuildConfiguration(build_id="b1", timestamp="t", model_source="org/model", target_arch="Rockchip",
                              target_format=ModelFormat.GGUF, output_dir="out", build_timeout=60)


def test_the_engine_samples_the_running_stage_container_and_warns_once_near_its_memory_limit(engine):
    near = stats_sample(usage=950 * MIB, limit=1000 * MIB)
    container = StatsContainer([stats_sample(usage=100 * MIB), near, near, near], reads_before_end=4)
    progress = container.progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    assert engine._run_container(build_config(), progress, container, "CONT: ") == 0
    assert container.seen_while_running[0]["memory_limit_bytes"] == 1000.0 * MIB
    assert progress.resources is None, "no figures once the stage container ended"
    warnings = [line for line in progress.logs if "[WARNING]" in line and "memory limit" in line]
    assert len(warnings) == 1, progress.logs
    assert "CONT: The build container uses 996.1 MB of its 1.0 GB memory limit (90 % or more)" in warnings[0]
    assert "Docker Desktop" in warnings[0] and "WSL2" in warnings[0]


def test_a_container_without_statistics_builds_as_before(engine):
    class Plain(StatsContainer):
        def stats(self, stream=False):
            self.enough.set()
            raise AttributeError("no statistics")

        def wait(self, timeout=None):
            assert self.enough.wait(10)
            return {"StatusCode": 0}
    container = Plain([])
    progress = container.progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    assert engine._run_container(build_config(), progress, container, "CONT: ") == 0
    assert progress.resources is None and not [line for line in progress.logs if "[WARNING]" in line]


def service_with(state, build):
    from orchestrator.Core.build_service import BuildService
    engine = SimpleNamespace(get_build_status=lambda build_id: build if build_id == "b1" else None)
    orchestrator = SimpleNamespace(_workflows={state.request_id: state}, build_engine=engine)
    return BuildService(SimpleNamespace(orchestrator=orchestrator))


def running_state(status=None):
    from datetime import datetime
    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    state = WorkflowState(request_id="req_1", status=status or OrchestrationStatus.BUILDING, start_time=datetime.now(),
                          total_builds=1)
    state.build_ids.append("b1")
    return state


def test_the_build_service_hands_the_running_builds_figures_to_the_window():
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.orchestrator import OrchestrationStatus
    figures = {"cpu_percent": 250.0, "memory_bytes": 2e9, "memory_limit_bytes": 8e9}
    build = BuildProgress("b1", BuildStatus.BUILDING, "Converting", resources=figures)
    service = service_with(running_state(), build)
    shown = service.live_resources("req_1")
    assert shown == figures and shown is not figures, "a copy: a window never changes the engine's sample"
    assert service.live_resources("req_unknown") is None
    assert service_with(running_state(OrchestrationStatus.COMPLETED), build).live_resources("req_1") is None
    assert "resources" not in service.get_status("req_1").model_dump(), "the AI's build status stays as it was"
    build.resources = None
    assert service.live_resources("req_1") is None
    assert BuildService(SimpleNamespace()).live_resources("req_1") is None, "the framework not booted"


def test_the_builder_shows_the_containers_cpu_and_memory_under_the_progress_bar(qapp):
    from orchestrator.gui.builder_parts import BuildProgressPanel
    figures = {"cpu_percent": 251.4, "memory_bytes": 2_000_000_000.0, "memory_limit_bytes": 8_000_000_000.0}
    status = SimpleNamespace(status="building", stage="Converting", progress_percent=40)
    service = SimpleNamespace(get_status=lambda rid: status, live_resources=lambda rid: figures,
                              log_tail=lambda rid, lines, for_person: None)
    panel = BuildProgressPanel()
    panel.lang = "en"
    panel.start("req_1")
    assert panel.lbl_resources.isHidden()
    panel.poll(service, "req_1")
    assert not panel.lbl_resources.isHidden()
    assert panel.lbl_resources.text() == "Build container: CPU 251 % · memory 2.0 GB of 8.0 GB"
    panel.lang = "de"
    figures["memory_bytes"] = 7_600_000_000.0
    panel.poll(service, "req_1")
    assert panel.lbl_resources.text() == "Build-Container: CPU 251 % · Arbeitsspeicher 7,6 GB von 8,0 GB, fast voll"
    status.status = "completed"
    panel.poll(service, "req_1")
    assert panel.lbl_resources.isHidden(), "nothing runs any more"
    status.status = "building"
    service.live_resources = lambda rid: (_ for _ in ()).throw(RuntimeError("an older service"))
    panel.poll(service, "req_1")
    assert panel.lbl_resources.isHidden()


def test_the_old_build_path_is_gone_and_its_jobs_are_the_one_paths():
    """Round 12, item 6: start_build, the monitor thread, stop_build and the six Qt signals (no .connect() anywhere)
    were covered by the one path: BuildService.start_build/cancel, the Builder's progress panel (status and live log),
    live_resources (CPU and memory) and LLMOrchestrator._heal_and_retry (the healing proposal after a failed build,
    asked in the GUI and the CLI). Only the client and the engine's construction stay."""
    for name in ("start_build", "stop_build", "_monitor_build", "_calculate_cpu_percent", "build_started",
                 "build_progress", "build_completed", "build_output", "build_stats", "healing_requested"):
        assert not hasattr(DockerManager, name), name
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.orchestrator import LLMOrchestrator
    assert callable(BuildService.start_build) and callable(BuildService.cancel)
    assert callable(LLMOrchestrator._heal_and_retry) and callable(LLMOrchestrator.resolve_healing)
