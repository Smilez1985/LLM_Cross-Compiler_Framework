"""Jobs, build ownership, capped waits and the end of a session (design S7, G12): get_job_status and cancel_job,
own builds and jobs cancelled without a question and another client's only with one, long-polls that hold no worker
thread and stop without touching the build, and a disconnected session that cancels its own work."""

import asyncio
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import anyio
import pytest

from orchestrator.Core import job_service as job_module
from orchestrator.Core.job_service import JOB_ID_RE, JobError, JobService
from orchestrator.tools.policy import Decision, ToolPolicy
from orchestrator.tools.registry import ToolContext, ToolDenied

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from orchestrator import mcp_server  # noqa: E402

MODEL = "HuggingFaceTB/SmolLM2-135M-Instruct"
BUILD = {"model": MODEL, "target": "Rockchip", "format": "gguf", "board": "rk3566"}


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


# --- the job service ------------------------------------------------------------------------------------------

class TestJobService:

    def test_a_job_runs_reports_progress_and_holds_its_result(self, tmp_path):
        jobs = JobService(tmp_path / "jobs")
        release = threading.Event()

        def work(handle):
            handle.progress(1, 3, "step one")
            release.wait(5)
            return {"pages": 3}

        job = jobs.start("crawl", "mcp#a", work)
        assert JOB_ID_RE.fullmatch(job.job_id) and job.state == "running"
        jobs.wait(job.job_id, 2)
        assert (job.done, job.total, job.message) == (1, 3, "step one")
        release.set()
        deadline = time.monotonic() + 5
        while not job.finished and time.monotonic() < deadline:
            jobs.wait(job.job_id, 1)
        assert job.state == "done" and job.result == {"pages": 3}

    def test_errors_keep_a_refusal_but_hide_a_crash(self):
        jobs = JobService()

        def refuse(_handle):
            raise ValueError("The disk is full (12 GB needed, 3 GB free).")

        def crash(_handle):
            raise RuntimeError("token " + "hf_" + "secret" * 4 + " in a traceback")  # built at runtime

        refused, crashed = jobs.start("hf_download", "mcp#a", refuse), jobs.start("hf_download", "mcp#a", crash)
        for job in (refused, crashed):
            while not job.finished:
                jobs.wait(job.job_id, 1)
        assert refused.state == "failed" and "12 GB needed" in refused.error["message"]
        assert crashed.state == "failed" and "secret" not in crashed.error["message"]

    def test_cancel_sets_the_event_runs_the_stop_hook_and_drops_a_late_result(self):
        jobs = JobService()
        stopped, release = [], threading.Event()

        def work(handle):
            release.wait(5)
            return {"late": True}

        job = jobs.start("deploy", "mcp#a", work, on_cancel=lambda: stopped.append(True))
        jobs.cancel(job.job_id, "device state unknown")
        release.set()
        time.sleep(0.1)
        assert job.state == "cancelled" and job.cancel_event.is_set() and stopped == [True]
        assert job.result is None and job.note == "device state unknown"

    def test_a_wait_returns_on_change_and_at_the_timeout(self):
        jobs = JobService()
        job = jobs.create("probe", "mcp#a", state="running")
        started = time.monotonic()
        jobs.wait(job.job_id, 0.3)
        assert time.monotonic() - started >= 0.25, "no change: the wait runs to its timeout"
        threading.Timer(0.2, lambda: jobs.update(job.job_id, progress=(1, 2, ""))).start()
        started = time.monotonic()
        jobs.wait(job.job_id, 10)
        assert time.monotonic() - started < 5, "a change ends the wait at once"

    def test_an_async_wait_returns_on_change_and_at_the_timeout_without_a_thread(self):
        jobs = JobService()
        job = jobs.create("probe", "mcp#a", state="running")

        async def go():
            threads = threading.active_count()
            started = time.monotonic()
            await jobs.wait_async(job.job_id, 0.3)
            timed_out = time.monotonic() - started
            threading.Timer(0.2, lambda: jobs.update(job.job_id, state="done")).start()
            started = time.monotonic()
            await jobs.wait_async(job.job_id, 10)
            return timed_out, time.monotonic() - started, threads
        timed_out, changed, _ = run(go())
        assert timed_out >= 0.25 and changed < 5 and job.state == "done"
        assert job._waiters == [], "a finished wait leaves no waiter behind"

    def test_waits_are_capped_at_50_seconds(self, monkeypatch):
        jobs = JobService()
        job = jobs.create("probe", "mcp#a", state="running")
        monkeypatch.setattr(job_module, "MAX_WAIT_SECONDS", 0.2)
        started = time.monotonic()
        jobs.wait(job.job_id, 3600)
        assert time.monotonic() - started < 2

    def test_cancel_owned_cancels_only_that_owners_unfinished_jobs(self):
        jobs = JobService()
        mine = jobs.create("crawl", "mcp#a", state="running")
        other = jobs.create("crawl", "mcp#b", state="running")
        done = jobs.create("crawl", "mcp#a", state="running")
        jobs.update(done.job_id, state="done")
        assert [j.job_id for j in jobs.cancel_owned("mcp#a", "client disconnected")] == [mine.job_id]
        assert (mine.state, other.state, done.state) == ("cancelled", "running", "done")

    def test_summaries_are_private_and_never_hold_the_result_or_session(self, tmp_path):
        jobs = JobService(tmp_path / "jobs")
        job = jobs.create("hf_download", "mcp#session-secret", state="running")
        jobs.update(job.job_id, state="done", result={"sha256": "ab" * 32})
        summary = jobs.summaries()[0]
        assert summary == {"job_id": job.job_id, "kind": "hf_download", "owner": "mcp", "state": "done",
                           "created": job.created, "updated": job.updated, "error": None}
        path = tmp_path / "jobs" / f"{job.job_id}.json"
        assert "session-secret" not in path.read_text() and (path.stat().st_mode & 0o077) == 0

    def test_unknown_ids_name_where_ids_come_from(self):
        with pytest.raises(JobError, match="do not guess"):
            JobService().get("job_0000000000000000")


# --- a framework with a fake orchestrator ----------------------------------------------------------------------

class FakeOrchestrator:
    """Builds that stay queued until a test changes them; cancel_request marks them cancelled."""

    def __init__(self):
        self._workflows = {}
        self.cancelled = []
        self.build_engine = SimpleNamespace(get_build_status=lambda bid: None, is_active=lambda bid: False)

    async def submit_build_request(self, req):
        from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
        state = WorkflowState(req.request_id, OrchestrationStatus.QUEUED, datetime.now(), owner=req.owner)
        state.build_ids.append(f"{req.request_id}_001")
        self._workflows[req.request_id] = state
        return req.request_id

    async def cancel_request(self, rid):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        self.cancelled.append(rid)
        self._workflows[rid].status = OrchestrationStatus.CANCELLED
        return True

    def set_status(self, rid, status, stage=None):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        state = self._workflows[rid]
        state.status = OrchestrationStatus(status)
        if stage:
            state.current_stage = stage


@pytest.fixture
def framework(booted_framework):
    booted_framework.orchestrator = FakeOrchestrator()
    booted_framework.tool_registry.policy_store.save(
        ToolPolicy(configured=True, overrides={"start_build": Decision.ALLOW}))
    return booted_framework


def answering(asked, approve=True):
    async def callback(ctx, params):
        asked.append(params.message)
        return ElicitResult(action="accept", content={"approve": approve})
    return callback


def rows(framework, n=20):
    return [(e["actor"], e["tool"], e["outcome"], e["detail"]) for e in framework.tool_registry.audit.entries(n)]


class TestOwnership:

    def test_start_build_records_the_caller_as_owner(self, framework):
        registry = framework.tool_registry
        handle = registry.call("start_build", BUILD, ToolContext("mcp", session="abc"))
        assert framework.build_service.owner_of(handle.request_id) == "mcp#abc"
        assert framework.build_service.get_status(handle.request_id).owner == "mcp", "only the caller class is shown"

    def test_own_cancel_needs_no_question_another_callers_asks(self, framework):
        registry = framework.tool_registry
        rid = registry.call("start_build", BUILD, ToolContext("mcp", session="a")).request_id
        asked = []
        other = ToolContext("mcp", session="b", confirm=lambda spec, inp: asked.append(spec.name) or False)
        with pytest.raises(ToolDenied):
            registry.call("cancel_build", {"request_id": rid}, other)
        assert asked == ["cancel_build"] and framework.orchestrator.cancelled == []
        owner = ToolContext("mcp", session="a", confirm=lambda spec, inp: pytest.fail("must not ask"))
        assert registry.call("cancel_build", {"request_id": rid}, owner).status == "cancelled"

    def test_an_explicit_deny_still_wins_over_ownership(self, framework):
        registry = framework.tool_registry
        rid = registry.call("start_build", BUILD, ToolContext("mcp", session="a")).request_id
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"start_build": Decision.ALLOW,
                                                                          "cancel_build": Decision.DENY}))
        with pytest.raises(ToolDenied):
            registry.call("cancel_build", {"request_id": rid}, ToolContext("mcp", session="a"))

    def test_a_damaged_policy_denies_even_own_cancels(self, framework):
        registry = framework.tool_registry
        rid = registry.call("start_build", BUILD, ToolContext("mcp", session="a")).request_id
        registry.policy_store.path.write_text("{broken")
        with pytest.raises(ToolDenied):
            registry.call("cancel_build", {"request_id": rid}, ToolContext("mcp", session="a"))

    def test_own_job_cancel_needs_no_question_another_callers_asks(self, framework):
        registry = framework.tool_registry
        job = framework.job_service.create("crawl", "mcp#a", state="running")
        with pytest.raises(ToolDenied):
            registry.call("cancel_job", {"job_id": job.job_id}, ToolContext("mcp", session="b"))
        assert job.state == "running"
        out = registry.call("cancel_job", {"job_id": job.job_id}, ToolContext("mcp", session="a"))
        assert out.state == "cancelled" and job.cancel_event.is_set()


class TestMcpSessions:

    def test_each_connection_owns_its_builds_and_cancels_its_own_without_a_question(self, framework):
        server = mcp_server.build_mcp_server(framework)
        asked = []

        async def go():
            async with Client(server, elicitation_callback=answering(asked)) as a, \
                    Client(server, elicitation_callback=answering(asked, approve=False)) as b:
                rid = (await a.call_tool("start_build", BUILD)).structured_content["request_id"]
                refused = await b.call_tool("cancel_build", {"request_id": rid})
                own = await a.call_tool("cancel_build", {"request_id": rid})
                return rid, refused, own
        rid, refused, own = run(go())
        assert refused.is_error and len(asked) == 1 and "Cancel a build" in asked[0]
        assert not own.is_error and framework.orchestrator.cancelled == [rid]

    def test_a_disconnected_session_cancels_its_own_builds_and_jobs_and_audits_it(self, framework):
        server = mcp_server.build_mcp_server(framework)
        jobs = framework.job_service

        async def go():
            async with Client(server) as other:
                other_rid = (await other.call_tool("start_build", BUILD)).structured_content["request_id"]
                async with Client(server) as client:
                    rid = (await client.call_tool("start_build", BUILD)).structured_content["request_id"]
                    owner = framework.build_service.owner_of(rid)
                    job = jobs.create("crawl", owner, state="running")
                return rid, other_rid, job
        rid, other_rid, job = run(go())
        assert rid in framework.orchestrator.cancelled and job.state == "cancelled"
        assert other_rid in framework.orchestrator.cancelled, "the other session ended after it"
        entries = rows(framework)
        ended = [e for e in entries if e[2] == "cancelled"]
        assert ("mcp", "cancel_build", "cancelled") in [e[:3] for e in ended]
        assert ("mcp", "cancel_job", "cancelled") in [e[:3] for e in ended]
        assert all("client disconnected" in e[3] for e in ended)

    def test_another_sessions_build_survives_a_disconnect(self, framework):
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as staying:
                other_rid = (await staying.call_tool("start_build", BUILD)).structured_content["request_id"]
                async with Client(server) as leaving:
                    await leaving.call_tool("list_targets", {})
                return other_rid, list(framework.orchestrator.cancelled)
        other_rid, cancelled_while_open = run(go())
        assert other_rid not in cancelled_while_open

    def test_serve_shuts_the_framework_down_on_exit(self, monkeypatch):
        calls = []
        fake = SimpleNamespace(tool_registry=None, shutdown=lambda: calls.append("shutdown"))
        monkeypatch.setattr(mcp_server, "build_mcp_server",
                            lambda framework, **kw: SimpleNamespace(run=lambda transport: calls.append(transport)))
        monkeypatch.setattr(mcp_server, "_logging_to_stderr", lambda: None)
        monkeypatch.setattr(mcp_server, "_stdout_fd_to_stderr", lambda stdout: None)
        mcp_server.serve(fake)
        assert calls == ["stdio", "shutdown"]


class TestLongPolls:

    @pytest.fixture(autouse=True)
    def quick_progress(self, monkeypatch):
        monkeypatch.setattr(job_module, "PROGRESS_EVERY", 0.2)

    def test_get_build_status_returns_on_change(self, framework):
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as client:
                rid = (await client.call_tool("start_build", BUILD)).structured_content["request_id"]
                threading.Timer(0.4, framework.orchestrator.set_status, (rid, "building", "Converting")).start()
                started = time.monotonic()
                result = await client.call_tool("get_build_status", {"request_id": rid, "wait_seconds": 20})
                return result, time.monotonic() - started
        result, waited = run(go())
        assert not result.is_error and result.structured_content["status"] == "building"
        assert 0.3 <= waited < 10

    def test_get_build_status_returns_at_the_timeout_and_reduces_long_waits(self, framework, monkeypatch):
        monkeypatch.setattr(job_module, "MAX_WAIT_SECONDS", 1)
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as client:
                rid = (await client.call_tool("start_build", BUILD)).structured_content["request_id"]
                started = time.monotonic()
                result = await client.call_tool("get_build_status", {"request_id": rid, "wait_seconds": 600})
                return result, time.monotonic() - started
        result, waited = run(go())
        assert result.structured_content["status"] == "queued" and 0.8 <= waited < 5
        assert any("reduced" in n for n in result.structured_content["notes"])

    def test_progress_is_sent_only_with_a_progress_token(self, framework, monkeypatch):
        monkeypatch.setattr(job_module, "MAX_WAIT_SECONDS", 1)
        server = mcp_server.build_mcp_server(framework)
        seen = []

        async def progress(value, total, message):
            seen.append((value, total, message))

        async def go():
            async with Client(server) as client:
                rid = (await client.call_tool("start_build", BUILD)).structured_content["request_id"]
                await client.call_tool("get_build_status", {"request_id": rid, "wait_seconds": 1})
                without = list(seen)
                await client.call_tool("get_build_status", {"request_id": rid, "wait_seconds": 1},
                                       progress_callback=progress)
                return without
        without = run(go())
        assert without == [] and seen, "progress only for the call that asked for it"
        assert all("queued" in (message or "") for _, _, message in seen)

    def test_a_cancelled_long_poll_leaves_the_build_running(self, framework):
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as client:
                rid = (await client.call_tool("start_build", BUILD)).structured_content["request_id"]
                started = time.monotonic()
                with anyio.move_on_after(0.5):
                    await client.call_tool("get_build_status", {"request_id": rid, "wait_seconds": 30})
                stopped_after = time.monotonic() - started
                status = await client.call_tool("get_build_status", {"request_id": rid})
                return status, list(framework.orchestrator.cancelled), stopped_after
        status, cancelled_while_open, stopped_after = run(go())
        assert stopped_after < 5, "the client's cancel ended the wait"
        assert cancelled_while_open == [], "cancelling the wait never cancels the build"
        assert status.structured_content["status"] == "queued"

    def test_get_job_status_waits_for_the_job(self, framework):
        server = mcp_server.build_mcp_server(framework)
        jobs = framework.job_service

        async def go():
            async with Client(server) as client:
                await client.call_tool("list_targets", {})
                job = jobs.create("crawl", "mcp#someone", state="running")
                threading.Timer(0.3, lambda: jobs.update(job.job_id, state="done", result={"pages": 2})).start()
                return await client.call_tool("get_job_status", {"job_id": job.job_id, "wait_seconds": 20})
        result = run(go()).structured_content
        assert result["state"] == "done" and result["result"] == {"pages": 2}
        assert result["owner"] == "mcp" and result["yours"] is False and "someone" not in str(result)

    def test_a_client_has_at_most_four_waits_at_once(self, framework, monkeypatch):
        monkeypatch.setattr(job_module, "MAX_WAIT_SECONDS", 1.5)
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as client:
                rid = (await client.call_tool("start_build", BUILD)).structured_content["request_id"]
                results = []

                async def poll():
                    results.append(await client.call_tool("get_build_status",
                                                          {"request_id": rid, "wait_seconds": 50}))
                async with anyio.create_task_group() as tg:
                    for _ in range(5):
                        tg.start_soon(poll)
                return results
        results = run(go())
        skipped = [r for r in results if any("4 waits" in n for n in r.structured_content["notes"])]
        assert len(results) == 5 and len(skipped) == 1

    def test_ditto_waits_in_its_own_thread(self, framework):
        registry = framework.tool_registry
        rid = registry.call("start_build", BUILD, ToolContext("ditto")).request_id
        threading.Timer(0.3, framework.orchestrator.set_status, (rid, "building")).start()
        started = time.monotonic()
        out = registry.call("get_build_status", {"request_id": rid, "wait_seconds": 20}, ToolContext("ditto"))
        assert out.status == "building" and time.monotonic() - started < 10

    def test_unknown_build_after_a_wait_is_an_error_naming_the_list(self, framework):
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as client:
                return await client.call_tool("get_build_status", {"request_id": "req_missing", "wait_seconds": 5})
        result = run(go())
        assert result.is_error and "Unknown build request" in text_of(result)


# --- adversarial review of S7 ----------------------------------------------------------------------------------

SIGTERM_SCRIPT = r"""
import os, signal, sys, threading
from types import SimpleNamespace
import anyio
from orchestrator import mcp_server

calls = []

def run(transport):
    async def session():
        try:
            threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
            await anyio.sleep(30)
        finally:
            with anyio.CancelScope(shield=True):  # the lifespan's cleanup (end_session) is shielded the same way
                os.kill(os.getpid(), signal.SIGTERM)  # a second SIGTERM while cleaning up
                await anyio.sleep(0.3)
                calls.append("session cleanup")
    anyio.run(session)

mcp_server.build_mcp_server = lambda framework, **kw: SimpleNamespace(run=run)
mcp_server._logging_to_stderr = lambda: None
mcp_server._stdout_fd_to_stderr = lambda stdout: None
mcp_server.serve(SimpleNamespace(tool_registry=None, shutdown=lambda: calls.append("shutdown")))
print(" | ".join(calls), flush=True)
"""


class TestReviewS7:
    """Findings of the adversarial review: each test fails without its fix."""

    @pytest.mark.skipif(not hasattr(__import__("signal"), "SIGKILL"), reason="POSIX signals")
    def test_sigterm_ends_a_stdio_server_with_its_session_cleanup(self):
        """Clients stop a stdio server with SIGTERM when it has not ended a few seconds after its input closed (and
        some send it right away). Python's default ended the process at once: the lifespan never cancelled the
        session's builds and jobs, and their containers ran on with nobody to clean up."""
        import subprocess
        import sys
        from conftest import REPO_ROOT
        proc = subprocess.run([sys.executable, "-c", SIGTERM_SCRIPT], capture_output=True, text=True, timeout=60,
                              cwd=str(REPO_ROOT))
        assert proc.returncode == 0, (proc.returncode, proc.stderr[-2000:])
        assert proc.stdout.strip() == "session cleanup | shutdown"

    def test_summaries_and_pruning_survive_a_summary_removed_meanwhile(self, tmp_path):
        """Several processes keep summaries in one folder and prune it; a file another process removed between the
        listing and the look at its time stopped every later pruning and made summaries() fail."""
        folder = tmp_path / "jobs"
        jobs = JobService(folder, keep=1)
        first = jobs.create("crawl", "mcp#a", state="running")
        (folder / "job_0000000000000000.json").symlink_to(folder / "removed-by-another-process.json")
        time.sleep(0.05)
        second = jobs.create("crawl", "mcp#a", state="running")
        assert not (folder / f"{first.job_id}.json").exists(), "the older summary is pruned"
        assert [s["job_id"] for s in jobs.summaries()] == [second.job_id]
