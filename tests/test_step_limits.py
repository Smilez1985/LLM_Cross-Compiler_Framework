#!/usr/bin/env python3
"""No hard time limit for a build step (DECISIONS round 25 Q8; orchestrator/utils/step_limits.py): the soft cap
(build_timeout) stops only an AI-started build, a person's goes on with one note; the idle timeout
(build_idle_timeout) stops a step that prints nothing; an AI may only lower both; the runtime build's executor
follows the same rule."""

import os
import sys
from types import SimpleNamespace

import pytest

from orchestrator.utils.step_limits import StepClock, StepLimits, StepTimedOut

needs_posix = pytest.mark.skipif(os.name == "nt", reason="a POSIX process group")


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_a_person_s_step_goes_on_past_the_soft_cap_with_one_note():
    clock = Clock()
    watch = StepClock(StepLimits(soft_cap=10, idle=5, ai_started=False), "convert", clock)
    clock.now += 4
    watch.output()
    assert watch.check() is None
    clock.now += 4
    watch.output()
    clock.now += 3                      # 11 s: past the soft cap, still printing
    note = watch.check()
    assert note and "It goes on because you started it; Cancel stops it" in note and "build_timeout" in note
    clock.now += 1
    assert watch.check() is None, "the note comes once"


def test_an_ai_started_step_stops_at_the_soft_cap():
    clock = Clock()
    watch = StepClock(StepLimits(soft_cap=10, idle=None, ai_started=True), "build", clock)
    clock.now += 10
    with pytest.raises(StepTimedOut) as stop:
        watch.check()
    assert stop.value.kind == "soft_cap" and isinstance(stop.value, TimeoutError)
    assert "an AI started this build, and the soft cap of an AI-started build is never extended" in str(stop.value)


@pytest.mark.parametrize("ai", [False, True])
def test_a_step_that_prints_nothing_stops_at_the_idle_timeout_for_everyone(ai):
    clock = Clock()
    watch = StepClock(StepLimits(soft_cap=3600, idle=5, ai_started=ai), "target", clock)
    clock.now += 5
    with pytest.raises(StepTimedOut, match=r"printed nothing for 5 s .*build_idle_timeout") as stop:
        watch.check()
    assert stop.value.kind == "idle"


def test_next_wait_never_waits_past_a_limit():
    clock = Clock()
    watch = StepClock(StepLimits(soft_cap=10, idle=4, ai_started=True), "build", clock)
    assert watch.next_wait(30) == 4
    clock.now += 3
    watch.output()
    assert watch.next_wait(30) == 4
    assert watch.next_wait(2) == 2


def test_the_limits_from_the_settings():
    values = {"build_timeout": 7200, "build_idle_timeout": 0}
    limits = StepLimits.from_settings(lambda key, default: values.get(key, default), owner="mcp#s1")
    assert (limits.soft_cap, limits.idle, limits.ai_started) == (7200.0, 1800.0, True)
    assert not StepLimits.from_settings(lambda key, default: default).ai_started


def test_an_ai_may_only_shorten_the_soft_cap_and_the_idle_timeout():
    from orchestrator.llm.settings_change import TIGHTEN_ONLY, direction_problem
    from orchestrator.tools.tools_status import READ_ONLY, WRITABLE
    for key in ("build_timeout", "build_idle_timeout"):
        assert TIGHTEN_ONLY[key] == "lower" and key in WRITABLE and key not in READ_ONLY
        assert direction_problem(key, 3600, 600) is None
        assert "may only be lowered" in direction_problem(key, 3600, 7200)


def test_both_settings_are_in_the_schema_the_settings_views_show(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    cfg = ConfigManager(tmp_path / "configs")
    schema = cfg.config_schemas
    assert schema["build_timeout"].default_value == 3600 and "soft cap" in schema["build_timeout"].description.lower()
    assert schema["build_idle_timeout"].default_value == 1800 and cfg.get("build_idle_timeout") == 1800


# --- the runtime build's executor --------------------------------------------------------------------------------

@needs_posix
def test_the_executor_stops_a_silent_step_at_the_idle_timeout(tmp_path):
    from orchestrator.runtime.build.executor import default_process_runner
    log = tmp_path / "build.log"
    rc = default_process_runner([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, dict(os.environ),
                                log, StepLimits(soft_cap=3600, idle=0.5, ai_started=False))
    assert rc == -1
    assert "printed nothing for 0.5 s and was stopped (setting build_idle_timeout)" in log.read_text()


@needs_posix
def test_the_executor_lets_a_person_s_step_run_past_its_soft_cap(tmp_path):
    from orchestrator.runtime.build.executor import default_process_runner
    log = tmp_path / "build.log"
    script = "import time\nfor i in range(6):\n    print(i, flush=True)\n    time.sleep(0.2)\n"
    rc = default_process_runner([sys.executable, "-c", script], tmp_path, dict(os.environ), log,
                                StepLimits(soft_cap=0.3, idle=5, ai_started=False))
    assert rc == 0
    assert "It goes on because you started it" in log.read_text()


@needs_posix
def test_the_executor_stops_an_ai_started_step_at_its_soft_cap_and_a_number_stays_a_plain_limit(tmp_path):
    from orchestrator.runtime.build.executor import BuildExecutor, default_process_runner
    script = "import time\nfor i in range(50):\n    print(i, flush=True)\n    time.sleep(0.1)\n"
    log = tmp_path / "ai.log"
    assert default_process_runner([sys.executable, "-c", script], tmp_path, dict(os.environ), log,
                                  StepLimits(soft_cap=0.3, idle=5, ai_started=True)) == -1
    assert "an AI started this build" in log.read_text()
    log = tmp_path / "plain.log"
    assert default_process_runner([sys.executable, "-c", script], tmp_path, dict(os.environ), log, 0.3) == -1
    executor = BuildExecutor(ai_started=True, idle_timeout=60)
    limits = executor._limits(executor.build_timeout)
    assert (limits.soft_cap, limits.idle, limits.ai_started) == (executor.build_timeout, 60, True)


# --- an AI-started llama.cpp runtime build (the build_local_runtime and deploy_device_runtime tools) ----------------

def test_the_runtime_executor_of_an_ai_started_build_stops_at_its_soft_cap(tmp_path):
    """LocalLLMRuntime.install(ai_started=True) builds with an executor whose soft caps stop (an AI-started build is
    never extended); the person's install keeps the soft caps that only warn."""
    from orchestrator.runtime.llama_server import LocalLLMRuntime
    rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
    assert rt.executor(ai_started=True).ai_started is True
    assert rt.executor().ai_started is False


def test_the_device_runtime_build_of_an_ai_stops_at_its_soft_cap(tmp_path, monkeypatch):
    """steps.build_runtime and service.ensure_build/deploy carry ai_started to the BuildExecutor."""
    import inspect
    from orchestrator.runtime.device import service, steps
    assert "ai_started" in inspect.signature(steps.build_runtime).parameters
    assert "ai_started" in inspect.signature(service.ensure_build).parameters
    assert "ai_started" in inspect.signature(service.deploy).parameters
    from orchestrator.runtime.build.executor import BuildExecutor
    seen = []

    class Stop(Exception):
        pass

    def build(self, plan, **kw):
        seen.append(self.ai_started)
        raise Stop()
    monkeypatch.setattr(BuildExecutor, "build", build)
    monkeypatch.setattr("orchestrator.runtime.build.executor.DockerEngine", lambda: None)
    monkeypatch.setattr("orchestrator.runtime.build.source.cached_source", lambda pin, state: tmp_path)
    monkeypatch.setattr("orchestrator.runtime.build.store.BuildStore.find", lambda self, plan: None)
    monkeypatch.setattr("orchestrator.runtime.build.store.BuildStore.build",
                        lambda self, plan, executor=None, **kw: executor.build(plan))
    for ai in (True, False):
        with pytest.raises(Stop):
            steps.build_runtime(tmp_path, "0123456789abcdef", SimpleNamespace(perf_key=lambda: "k"), object(),
                                object(), ai_started=ai)
    assert seen == [True, False]


# --- one setting, one meaning: the runtime builds read build_idle_timeout like the stage containers ----------------

class _Settings:
    """A stand-in for the framework's settings (ConfigManager.get)."""

    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _idle_of(executor):
    """The idle timeout the executor's StepClock gets for its build step."""
    return StepClock(executor._limits(executor.build_timeout), "build").limits.idle


def test_the_executor_s_default_idle_timeout_is_the_one_of_the_step_limits():
    from orchestrator.runtime.build import executor
    from orchestrator.utils import step_limits
    assert executor.DEFAULT_IDLE == step_limits.DEFAULT_IDLE
    assert _idle_of(executor.BuildExecutor()) == step_limits.DEFAULT_IDLE
    assert _idle_of(executor.BuildExecutor(settings=_Settings(build_idle_timeout=777))) == 777
    # a number the caller passes wins over the setting (tests and the framework's own short steps)
    assert _idle_of(executor.BuildExecutor(idle_timeout=60, settings=_Settings(build_idle_timeout=777))) == 60


def test_the_local_runtime_build_reads_the_configured_idle_timeout(tmp_path):
    """LocalLLMRuntime.executor(): the configured build_idle_timeout reaches the build's StepClock."""
    from orchestrator.runtime.llama_server import LocalLLMRuntime
    rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                         settings=_Settings(build_idle_timeout=4321))
    assert _idle_of(rt.executor()) == 4321
    assert _idle_of(rt.executor(ai_started=True)) == 4321
    assert _idle_of(LocalLLMRuntime(data_dir=tmp_path / "s2", models_dir=tmp_path / "m2").executor()) == 1800


def test_the_device_runtime_build_reads_the_configured_idle_timeout(tmp_path, monkeypatch):
    """steps.build_runtime(settings=...): the configured build_idle_timeout reaches the build's StepClock."""
    from orchestrator.runtime.build.executor import BuildExecutor
    from orchestrator.runtime.device import steps
    seen = []

    class Stop(Exception):
        pass

    def build(self, plan, **kw):
        seen.append(_idle_of(self))
        raise Stop()
    monkeypatch.setattr(BuildExecutor, "build", build)
    monkeypatch.setattr("orchestrator.runtime.build.executor.DockerEngine", lambda: None)
    monkeypatch.setattr("orchestrator.runtime.build.source.cached_source", lambda pin, state: tmp_path)
    monkeypatch.setattr("orchestrator.runtime.build.store.BuildStore.find", lambda self, plan: None)
    monkeypatch.setattr("orchestrator.runtime.build.store.BuildStore.build",
                        lambda self, plan, executor=None, **kw: executor.build(plan))
    for settings in (_Settings(build_idle_timeout=2468), None):
        with pytest.raises(Stop):
            steps.build_runtime(tmp_path, "0123456789abcdef", SimpleNamespace(perf_key=lambda: "k"), object(),
                                object(), settings=settings)
    assert seen == [2468, 1800]
