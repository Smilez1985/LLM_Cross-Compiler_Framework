"""The waiters end on the end record (DECISIONS round 40, TASK 2 and 3): one end reason per request, carried in
WorkflowState, BuildStatusOut, the shared build state and build history; the CLI's wait says it in plain words with
its exit code (0 done, 1 failed/soft_cap/idle, 2 cancelled, 3 still running); waits return at once on the end;
runtime build jobs carry the same vocabulary."""

import os
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from orchestrator.Core import request_store
from orchestrator.Core.build_service import BuildService
from orchestrator.Core.job_service import JobService, job_end_reason
from orchestrator.Core.orchestrator import LLMOrchestrator, OrchestrationStatus, WorkflowState
from orchestrator.utils.step_limits import StepClock, StepLimits, StepTimedOut, stop_kind
from test_cli_builds import invoke_plain, quiet_console, write_user_config


def _state(rid="req_0000aaaa", status=OrchestrationStatus.COMPLETED, **kw):
    return WorkflowState(rid, status, datetime(2026, 10, 30, 9, 0, 0), **kw)


class _Engine:
    def __init__(self, reasons):
        self.reasons = reasons

    def get_build_status(self, build_id):
        return SimpleNamespace(end_reason=self.reasons.get(build_id), status=None)


def _orchestrator(reasons=None):
    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.build_engine = _Engine(reasons or {})
    return orch


@pytest.mark.parametrize("status, failed, reasons, expected", [
    (OrchestrationStatus.COMPLETED, 0, {}, "done"),
    (OrchestrationStatus.CANCELLED, 0, {}, "cancelled"),
    (OrchestrationStatus.ERROR, 1, {"b1": "idle"}, "idle"),
    (OrchestrationStatus.ERROR, 1, {"b1": "soft_cap"}, "soft_cap"),
    (OrchestrationStatus.ERROR, 1, {"b1": "failed"}, "failed"),
    (OrchestrationStatus.ERROR, 0, {}, "failed"),                      # ended before a build ran
    (OrchestrationStatus.COMPLETED, 1, {"b1": "failed"}, "failed"),    # partial success is no "done"
])
def test_one_end_reason_per_request(status, failed, reasons, expected):
    state = _state(status=status, failed_builds=failed, build_ids=list(reasons))
    assert _orchestrator(reasons).end_reason_of(state) == expected
    assert state.end_reason == expected


def test_a_running_request_has_no_end_reason():
    assert _orchestrator().end_reason_of(_state(status=OrchestrationStatus.BUILDING)) is None


def test_the_old_statuses_stay_for_old_readers():
    from orchestrator.Core.build_end import STATUS_OF
    for reason, status in STATUS_OF.items():
        assert status in {s.value for s in OrchestrationStatus}


def test_the_shared_summary_and_the_history_carry_the_end_reason(tmp_path):
    from orchestrator.Core.build_history import REQUEST_FIELDS, request_rows
    store = request_store.RequestStore(tmp_path)
    store.save(_state(end_reason="idle", status=OrchestrationStatus.ERROR))
    record = store.get("req_0000aaaa")
    assert record["end_reason"] == "idle"
    assert dict(request_rows(record))["end"] == "idle" and "end" in REQUEST_FIELDS
    assert dict(request_rows({"end_reason": "anything else"}))["end"] is None


class _FakeOrchestrator:
    def __init__(self, state):
        self._workflows = {state.request_id: state}
        self.build_engine = _Engine({})

    def end_reason_of(self, state):
        return LLMOrchestrator.end_reason_of(self, state)


def _service(state):
    framework = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: d), resolve_path=lambda p: p)
    service = BuildService(framework, _FakeOrchestrator(state))
    service.artifact_label = lambda path: "output:x"
    return service


def test_build_status_out_has_the_end_reason_and_none_while_running():
    state = _state(status=OrchestrationStatus.BUILDING)
    service = _service(state)
    assert service.get_status(state.request_id).end_reason is None
    state.status = OrchestrationStatus.COMPLETED
    assert service.get_status(state.request_id).end_reason == "done"


def test_a_wait_returns_at_once_when_the_build_ends():
    """The long poll (MCP get_build_status, Ditto) ends on the end, not at its timeout."""
    state = _state(status=OrchestrationStatus.BUILDING, total_builds=1)
    service = _service(state)

    def end_soon():
        time.sleep(0.2)
        state.status = OrchestrationStatus.ERROR

    threading.Thread(target=end_soon, daemon=True).start()
    started = time.monotonic()
    assert service.wait_for_change(state.request_id, 40, poll=0.05) is True
    assert time.monotonic() - started < 5
    assert service.get_status(state.request_id).end_reason == "failed"
    # an ended build: the next wait returns at once
    started = time.monotonic()
    assert service.wait_for_change(state.request_id, 40) is False
    assert time.monotonic() - started < 1


# --- the CLI's wait ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, reason, code, words", [
    (OrchestrationStatus.COMPLETED, "done", 0, "Build ready"),
    (OrchestrationStatus.ERROR, "failed", 1, "Error while building"),
    (OrchestrationStatus.ERROR, "idle", 1, "idle timeout"),
    (OrchestrationStatus.ERROR, "soft_cap", 1, "soft cap"),
    (OrchestrationStatus.CANCELLED, "cancelled", 2, "Build stopped"),
])
def test_build_status_wait_ends_on_the_end_reason(tmp_path, monkeypatch, status, reason, code, words):
    write_user_config(tmp_path, cache_dir=str(tmp_path / "cache"))
    from orchestrator.Core.config_manager import ConfigManager
    request_store.RequestStore(ConfigManager.user_state_path()).save(
        _state(rid="req_0000bbbb", status=status, end_reason=reason))
    printed = quiet_console(monkeypatch)
    result = invoke_plain("build", "status", "req_0000bbbb", "--wait", "5")
    out = printed.getvalue() + result.out
    assert result.code == code, out
    assert words in out and "End: " + reason in out


def test_build_status_wait_says_when_the_build_still_runs(tmp_path, monkeypatch):
    write_user_config(tmp_path, cache_dir=str(tmp_path / "cache"))
    from orchestrator.Core.config_manager import ConfigManager
    store = request_store.RequestStore(ConfigManager.user_state_path())
    store.save(_state(rid="req_0000cccc", status=OrchestrationStatus.BUILDING))
    assert store.get("req_0000cccc")["pid"] == os.getpid()   # this process still runs it: not interrupted
    printed = quiet_console(monkeypatch)
    result = invoke_plain("build", "status", "req_0000cccc", "--wait", "0")
    assert result.code == 3, printed.getvalue() + result.out
    assert "still running" in printed.getvalue() + result.out


# --- runtime build jobs --------------------------------------------------------------------------------------------

def _finish(service, job, seconds=5):
    deadline = time.monotonic() + seconds
    while not service.get(job.job_id).finished and time.monotonic() < deadline:
        time.sleep(0.01)
    return service.get(job.job_id)


def test_a_runtime_build_job_has_an_end_reason(tmp_path):
    service = JobService()
    done = _finish(service, service.start("runtime_build", "ditto", lambda h: {"ok": True}))
    assert done.end_reason == "done" and done.summary()["end_reason"] == "done"

    def stopped(handle):
        raise RuntimeError("the build failed (exit -1):\n[executor] The build stage printed nothing for 5 s and "
                           "was stopped (setting build_idle_timeout): it no longer worked.")
    idle = _finish(service, service.start("runtime_build", "ditto", stopped))
    assert idle.state == "failed" and idle.end_reason == "idle"

    def plain(handle):
        raise ValueError("refused")
    assert _finish(service, service.start("runtime_build", "ditto", plain)).end_reason == "failed"


def test_other_job_kinds_keep_their_summary():
    service = JobService()
    job = _finish(service, service.start("crawl", "ditto", lambda h: {}))
    assert job.end_reason is None and "end_reason" not in job.summary()


def test_a_cancelled_runtime_build_job_is_cancelled():
    service = JobService()
    gate = threading.Event()
    job = service.start("runtime_build", "ditto", lambda h: gate.wait(5) or {})
    service.cancel(job.job_id)
    gate.set()
    assert service.get(job.job_id).end_reason == "cancelled"


def test_the_stop_sentences_are_matched_where_they_are_written():
    clock = SimpleNamespace(now=0.0)
    step = StepClock(StepLimits(soft_cap=10, idle=5, ai_started=True), "build", clock=lambda: clock.now)
    clock.now = 6
    with pytest.raises(StepTimedOut) as idle:
        step.check()
    assert stop_kind(str(idle.value)) == "idle"
    step = StepClock(StepLimits(soft_cap=10, idle=None, ai_started=True), "build", clock=lambda: clock.now)
    clock.now = 20
    with pytest.raises(StepTimedOut) as cap:
        step.check()
    assert stop_kind(str(cap.value)) == "soft_cap" and stop_kind("anything else") is None
    assert job_end_reason("failed", cap.value) == "soft_cap" and job_end_reason("expired") is None
