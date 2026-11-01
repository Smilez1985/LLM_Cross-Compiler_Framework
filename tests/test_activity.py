"""AI activity in the core (wave 6, design B; DECISIONS round 9, U1 and consequence b).

What Ditto or an AI app does through the framework's tools becomes one signed chat-feed row per event, written by the
process that did the work (the GUI, llmcf, every stdio MCP server, the HTTP child), so the GUI shows it whichever
process it came from:

- the window vocabulary (orchestrator/tools/tool_windows.py): a closed list of windows, a window for every tool that
  changes something (a registry-wide guard), for every job kind, and what opens each window;
- the inputs: the registry's call_done after a call that changed something, the job service's finish listener (every
  job kind) and the orchestrator's build end (who cancelled, the saved folder as a label);
- the producer (Core/activity.py): no row for the person's own calls, the trusted caller id as the sender and the
  app's name only as its claim, closed values only, asked_in only for Ditto's turns in the GUI's own chat, and it
  fails open;
- ConfigManager.reload_user_config: settings another process saved reach this one, except where this process holds a
  value of its own.
"""

import asyncio
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import pytest
from pydantic import BaseModel

from orchestrator.Core import activity as activity_module
from orchestrator.Core.activity import (BLOCKED_QUIET, PARAM_RULES, ActivityProducer, closed_params,
                                        closed_refs, feed_program, row_for_build, row_for_call, row_for_event,
                                        row_for_job, trusted_caller)
from orchestrator.Core.chat_feed import TEXTS, FeedStore, StoredEntry
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.job_service import JOB_KINDS, JobService
from orchestrator.tools import tool_windows
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.output import ToolOutput
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.tool_windows import (JOB_KIND_WINDOWS, PLANNED_OPENERS, TOOL_WINDOWS, WINDOW_IDS,
                                             WINDOW_OPENERS, activity_problems, window_for_tool)

KEY = b"k" * 32            # stands in for the audit key from the keyring (tests build no real credential)
REPO_ROOT = Path(__file__).resolve().parent.parent
REQ, JOB, JOB2 = "req_1a2b3c4d", "job_k3j2h1g0f9e8d7c6", "job_a1b2c3d4e5f6a7b8"
SECRET = "hf_" + "Q7x" * 12   # the shape of a Hugging Face token, made up


class In(BaseModel):
    text: str = "x"


class Out(ToolOutput):
    request_id: str = ""
    job_id: str = ""


class Applied(BaseModel):
    key: str
    old: str
    new: str


class SettingsOut(ToolOutput):
    applied: List[Applied] = []


class CannotIn(BaseModel):
    reason: str = "permission_denied"
    action: Optional[str] = None


class CannotOut(ToolOutput):
    checked: str = "confirmed"


def registry_with(tmp_path, *specs):
    registry = ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "audit.db"))
    for spec in specs:
        registry.register(spec)
    names = [s.name for s in specs]
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=names,
                                             overrides={n: Decision.ALLOW for n in names}))
    return registry


def changing(name="start_build", handler=None, output=Out):
    return ToolSpec(name, "Start a build", "Starts", ToolTier.LOCAL_WRITE, In, output,
                    handler or (lambda i: Out(request_id=REQ)))


def reading(name="list_targets"):
    return ToolSpec(name, "List", "Lists", ToolTier.READ, In, Out, lambda i: Out())


def events_of(registry):
    seen = []
    registry.events.subscribe(lambda kind, data: seen.append((kind, data)))
    return seen


def producer(tmp_path, program="mcp", **kw):
    store = FeedStore(tmp_path / "state", key=KEY)
    return ActivityProducer(store, program=program, **kw), store


def rows(store, view="all"):
    return [e.entry for e in store.after(0, view=view).entries]


def wait_rows(store, count, timeout=10.0):
    """The rows of the feed once there are `count` of them (a job ends on its own thread), at most `timeout` s."""
    deadline = time.monotonic() + timeout
    while len(rows(store)) < count and time.monotonic() < deadline:
        time.sleep(0.02)
    return rows(store)


def call_done(**data):
    base = {"tool": "start_build", "actor": "mcp:laptop", "owner": "mcp:laptop", "channel": None,
            "client": "Claude Code 2.1", "request_id": REQ}
    base.update(data)
    return base


# --- the window vocabulary -----------------------------------------------------------------------------------------

class TestWindowVocabulary:

    def test_window_ids_are_one_closed_list_that_holds_the_gui_tools_windows(self):
        from orchestrator.tools.tools_gui import WINDOWS
        assert len(WINDOW_IDS) == len(set(WINDOW_IDS)) == 21
        assert set(WINDOWS) <= set(WINDOW_IDS), "show_window's ids are a part of the one list"
        for window in WINDOW_IDS:
            assert re.fullmatch(tool_windows.WINDOW_ID_RE, window)
        for bad in ("desktop", "builder ", "Builder", "builder|chat", ""):
            assert not re.fullmatch(tool_windows.WINDOW_ID_RE, bad)

    def test_every_job_kind_and_every_window_is_covered(self):
        assert JOB_KIND_WINDOWS == {
            "crawl": "knowledge_sources", "deploy": "deployment", "package": "deployment", "probe": "boards",
            "pack_import": "community_hub", "pack_export": "community_hub", "snapshot_restore": "knowledge_snapshots",
            "hf_download": "hf_browser", "runtime_build": "ditto_runtime", "runtime_start": "ditto_runtime",
            "benchmark": "ditto_runtime", "approval": "approvals", "docs_index": "knowledge_sources"}
        assert set(JOB_KIND_WINDOWS) == set(JOB_KINDS)
        assert set(WINDOW_OPENERS) == set(WINDOW_IDS)
        for table in (TOOL_WINDOWS, JOB_KIND_WINDOWS, tool_windows.REASON_WINDOWS, tool_windows.ACTION_WINDOWS):
            assert set(table.values()) <= set(WINDOW_IDS)

    def test_every_tool_that_changes_something_has_a_window(self, booted_framework):
        """The registry-wide guard (like deletion_rule_problems): every registered tool that is not read-only, and
        report_cannot_do, has a window; so a new tool that changes something can never stay invisible."""
        from orchestrator.tools.tools_gui import register_gui_tools
        registry = booted_framework.tool_registry
        register_gui_tools(registry, booted_framework)
        changing_tools = {s.name for s in registry.list() if s.tier != ToolTier.READ}
        assert len(changing_tools) >= 25 and "gui" in changing_tools
        assert activity_problems(registry) == []
        assert changing_tools | {"report_cannot_do"} <= set(TOOL_WINDOWS)

    def test_the_guard_finds_a_changing_tool_without_a_window(self, tmp_path):
        registry = registry_with(tmp_path, changing("brand_new_tool"), reading("brand_new_read"))
        problems = activity_problems(registry)
        assert problems == ["tool 'brand_new_tool' changes something but has no window in TOOL_WINDOWS"]

    def test_every_opener_names_an_object_the_gui_has(self):
        """The openers are the objectNames the menus already use (the CLI_PATHS names), the main window's tab
        attributes and the chat dock; the three QActions without a name yet are listed as planned (chain a0)."""
        gui = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "orchestrator" / "gui").rglob("*.py"))
        for window, opener in WINDOW_OPENERS.items():
            if opener in PLANNED_OPENERS:
                continue
            assert f'"{opener}"' in gui or f"self.{opener} =" in gui, (window, opener)

    def test_a_call_names_its_window_more_precisely_where_it_can(self):
        assert window_for_tool("gui", form="module_wizard") == "module_wizard"
        assert window_for_tool("gui", form="nonsense") == "builder"
        assert window_for_tool("report_cannot_do", reason="permission_denied") == "tool_permissions"
        assert window_for_tool("report_cannot_do", reason="failed", action="trust_device") == "boards"
        assert window_for_tool("report_cannot_do", reason="failed") == "chat"
        assert window_for_tool("start_build", form="ai_settings") == "builder", "only the gui tool names a form"
        assert window_for_tool("made_up") is None


# --- the inputs: call_done, job ends, build ends -------------------------------------------------------------------

class TestCallDone:

    def test_a_call_that_changed_something_reports_framework_facts_only(self, tmp_path):
        registry = registry_with(tmp_path, changing())
        seen = events_of(registry)
        registry.call("start_build", {"text": SECRET}, ToolContext("mcp", client="Claude Code 2.1",
                                                                    connection="c0ffee"))
        assert seen == [("call_done", {"tool": "start_build", "actor": "mcp", "owner": "mcp", "channel": None,
                                       "client": "Claude Code 2.1", "request_id": REQ})]
        assert SECRET not in str(seen) and "c0ffee" not in str(seen), "no argument, no connection key"

    def test_reads_failures_and_refusals_report_no_call_done(self, tmp_path):
        def fails(inp):
            raise ToolError("It failed.", code="failed")
        registry = registry_with(tmp_path, reading(), changing("broken", handler=fails))
        seen = events_of(registry)
        registry.call("list_targets", {}, ToolContext("mcp"))
        with pytest.raises(ToolError):
            registry.call("broken", {}, ToolContext("mcp"))
        assert [k for k, _ in seen if k == "call_done"] == []

    def test_report_cannot_do_is_reported_although_it_only_reads(self, tmp_path):
        spec = ToolSpec("report_cannot_do", "Report", "Reports", ToolTier.READ, CannotIn, CannotOut,
                        lambda i: CannotOut())
        registry = registry_with(tmp_path, spec)
        seen = events_of(registry)
        registry.call("report_cannot_do", {"reason": "permission_denied", "action": "change_permissions"},
                      ToolContext("ditto"))
        (kind, data), = seen
        assert kind == "call_done" and (data["reason"], data["action"]) == ("permission_denied", "change_permissions")

    def test_changed_settings_are_named_by_key_never_by_value(self, tmp_path):
        out = SettingsOut(applied=[Applied(key="language", old="en", new="de"),
                                   Applied(key="offline_mode", old="false", new=SECRET)])
        registry = registry_with(tmp_path, changing("update_settings", handler=lambda i: out, output=SettingsOut))
        seen = events_of(registry)
        registry.call("update_settings", {}, ToolContext("mcp"))
        assert seen[0][1]["keys"] == "language,offline_mode"
        assert SECRET not in str(seen) and "'de'" not in str(seen)

    def test_reporting_never_fails_the_finished_call(self, tmp_path, monkeypatch):
        from orchestrator.tools import registry as registry_module
        registry = registry_with(tmp_path, changing())
        registry.events.subscribe(lambda kind, data: 1 / 0)   # a broken observer
        assert registry.call("start_build", {}, ToolContext("mcp")).request_id == REQ
        # a value that is no plain fact: the event is refused, the finished call is not
        monkeypatch.setattr(registry_module, "_done_facts", lambda spec, inp, result: {"window": object()})
        assert registry.call("start_build", {}, ToolContext("mcp")).request_id == REQ


class TestJobEnds:

    def test_every_way_a_job_ends_is_told_once_with_its_summary(self):
        jobs, ended = JobService(), []
        remove = jobs.add_finish_listener(ended.append)
        jobs.add_finish_listener(lambda summary: 1 / 0)   # a broken listener changes nothing
        done = jobs.start("hf_download", "mcp:laptop#c1", lambda h: {"path": "/srv/models/model.gguf"})
        failed = jobs.start("crawl", "mcp#c2", lambda h: (_ for _ in ()).throw(ValueError("no route")))
        waiting = jobs.create("approval", "ditto", state="waiting_for_approval")
        cancelled = jobs.create("deploy", "mcp#c3")
        jobs.update(waiting.job_id, note="still waiting")                 # not an end
        jobs.update(waiting.job_id, state="running")                      # not an end
        jobs.cancel(cancelled.job_id)
        jobs.cancel(cancelled.job_id)                                     # ended already: told once
        jobs.update(waiting.job_id, state="expired")
        deadline = time.monotonic() + 10
        while len(ended) < 4 and time.monotonic() < deadline:
            time.sleep(0.02)
        by_id = {s["job_id"]: s for s in ended}
        assert len(ended) == 4 and set(by_id) == {done.job_id, failed.job_id, waiting.job_id, cancelled.job_id}
        assert by_id[done.job_id]["state"] == "done" and by_id[done.job_id]["owner"] == "mcp:laptop"
        assert by_id[failed.job_id]["error"] == "failed" and by_id[cancelled.job_id]["state"] == "cancelled"
        assert "/srv/models" not in str(ended) and "c1" not in str([s["owner"] for s in ended])
        remove()
        jobs.cancel(jobs.create("probe", "mcp").job_id)
        assert len(ended) == 4


class TestBuildEnds:

    @pytest.fixture
    def orchestrator(self, tmp_path, monkeypatch):
        from orchestrator.Core.orchestrator import LLMOrchestrator
        from test_pipeline import FakeBuildEngine
        real_sleep = asyncio.sleep

        async def quick(delay, result=None):
            return await real_sleep(min(delay, 0.01), result)
        monkeypatch.setattr(asyncio, "sleep", quick)
        cfg = ConfigManager(tmp_path / "configs")
        (tmp_path / "targets" / "Rockchip").mkdir(parents=True)
        cfg.set("targets_dir", str(tmp_path / "targets"))
        orch = LLMOrchestrator(cfg)
        orch.build_engine = FakeBuildEngine()
        orch.request_store = None
        return orch

    @staticmethod
    def request(tmp_path, rid="req_0000aaaa", owner="mcp#9f3c2a1b"):
        from orchestrator.Core.builder import ModelFormat, OptimizationLevel
        from orchestrator.Core.orchestrator import BuildRequest, PriorityLevel, WorkflowType
        return BuildRequest(request_id=rid, workflow_type=WorkflowType.SIMPLE_CONVERSION,
                            priority=PriorityLevel.NORMAL, models=["HuggingFaceTB/SmolLM2-135M-Instruct"],
                            targets=["Rockchip"], target_formats=[ModelFormat.GGUF],
                            optimization_level=OptimizationLevel.BALANCED, quantization_options=["Q4_K_M"],
                            parallel_builds=False, output_base_dir=str(tmp_path / "out"), owner=owner)

    def test_an_ai_build_ends_once_with_the_saved_folder_as_a_label(self, orchestrator, tmp_path):
        ended = []
        orchestrator.add_end_listener(ended.append)
        orchestrator.artifact_label = lambda path: "output:" + Path(path).relative_to(tmp_path / "out").as_posix()

        async def scenario():
            await orchestrator.submit_build_request(self.request(tmp_path))
            for _ in range(500):
                if ended:
                    break
                await asyncio.sleep(0.01)
        asyncio.run(scenario())
        assert ended == [{"request_id": "req_0000aaaa", "status": "completed", "end_reason": "done", "owner": "mcp",
                          "cancelled_by": "",
                          "saved": "output:Rockchip/SmolLM2-135M-Instruct/Q4_K_M", "artifacts": 1, "completed": 1,
                          "failed": 0}]
        assert str(tmp_path) not in str(ended) and "9f3c2a1b" not in str(ended)

    def test_without_a_label_the_folder_is_named_never_its_host_path(self, orchestrator, tmp_path):
        ended = []
        orchestrator.add_end_listener(ended.append)
        orchestrator.artifact_label = lambda path: str(path)   # a label function that hands back the path

        async def scenario():
            await orchestrator.submit_build_request(self.request(tmp_path))
            for _ in range(500):
                if ended:
                    break
                await asyncio.sleep(0.01)
        asyncio.run(scenario())
        assert ended[0]["saved"] == "Q4_K_M" and str(tmp_path) not in str(ended)

    def test_a_label_that_hides_a_host_path_after_its_prefix_names_the_folder(self, orchestrator, tmp_path):
        """Review b0 (w6-b0-v): "output:" + a host path passed the end listener's own check (the whole value is not
        absolute), so the build end handed the host path on. The folder's own name is used then."""
        ended = []
        orchestrator.add_end_listener(ended.append)
        orchestrator.artifact_label = lambda path: "output:" + str(path)

        async def scenario():
            await orchestrator.submit_build_request(self.request(tmp_path))
            for _ in range(500):
                if ended:
                    break
                await asyncio.sleep(0.01)
        asyncio.run(scenario())
        assert ended[0]["saved"] == "Q4_K_M" and str(tmp_path) not in str(ended)

    def test_who_cancelled_is_named_for_an_ai_only(self, orchestrator, tmp_path):
        from orchestrator.tools import registry as registry_module
        ended = []
        orchestrator.add_end_listener(ended.append)

        async def scenario():
            for rid in ("req_0000bbbb", "req_0000cccc", "req_0000dddd"):
                await orchestrator.submit_build_request(self.request(tmp_path, rid=rid, owner=""))
            # queued requests end at once when cancelled
            await orchestrator.cancel_request("req_0000bbbb", by="mcp:laptop")
            token = registry_module._CALL.set((None, "ditto"))   # inside a tool call of Ditto (cancel_build)
            try:
                await orchestrator.cancel_request("req_0000cccc")
            finally:
                registry_module._CALL.reset(token)
            await orchestrator.cancel_request("req_0000dddd")       # the person's own cancel names nobody
            for _ in range(50):                                      # the queue worker sees them once more
                await asyncio.sleep(0.01)
        asyncio.run(scenario())
        by_id = {e["request_id"]: e for e in ended}
        assert [by_id[r]["status"] for r in ("req_0000bbbb", "req_0000cccc", "req_0000dddd")] == ["cancelled"] * 3
        assert [by_id[r]["cancelled_by"] for r in ("req_0000bbbb", "req_0000cccc", "req_0000dddd")] == [
            "mcp:laptop", "ditto", ""]
        assert len(ended) == 3, "each request ends once, although the queue worker sees it again"


# --- the rows ------------------------------------------------------------------------------------------------------

class TestRows:

    def test_an_ai_call_becomes_one_signed_row_from_its_trusted_caller(self, tmp_path):
        prod, store = producer(tmp_path)
        prod.on_registry_event("call_done", call_done())
        assert prod.flush()
        (entry,) = rows(store)
        assert (entry.kind, entry.program, entry.text_key) == ("ai_action", "mcp", "activity.tool.start_build")
        assert (entry.sender.role, entry.sender.caller, entry.sender.client) == ("client", "mcp:laptop",
                                                                                 "Claude Code 2.1")
        assert entry.sender.caller_label == "", "the app's own name is only its claim, never its label"
        assert (entry.refs.window, entry.refs.request_id, entry.refs.asked_in) == ("builder", REQ, "")
        assert entry.params == {"tool": "start_build"}
        db = sqlite3.connect(store.path)
        db.execute("UPDATE entries SET body = replace(body, 'start_build', 'delete_artifact')")
        db.commit()
        db.close()
        page = store.after(0, view="all")
        assert page.entries == [] and page.unverified == 1, "a changed row does not verify and is not shown"

    @pytest.mark.parametrize("actor", ["gui", "cli", "", "tester", "mcp:Bad Name", "MCP", None])
    def test_the_persons_own_calls_and_unknown_actors_produce_nothing(self, actor):
        data = call_done(actor=actor)
        assert row_for_call(data, program="gui") is None
        for kind in ("grant_used", "approval", "approval_end", "refused_before_question"):
            assert row_for_event(kind, data) is None
        assert row_for_job({"job_id": JOB, "kind": "crawl", "owner": actor, "state": "done"}) is None
        # A build's end is the exception (DECISIONS round 40): every build gets its row, the person's from the framework
        own = row_for_build({"request_id": REQ, "status": "completed", "owner": actor or ""})
        assert (own.caller, own.text_key) == ("", "activity.build.ready")

    def test_job_ends_become_job_results_in_their_window(self, tmp_path):
        prod, store = producer(tmp_path)
        prod.on_job_end({"job_id": JOB, "kind": "hf_download", "owner": "mcp", "state": "failed",
                         "error": "insufficient_resources"})
        prod.on_job_end({"job_id": JOB2, "kind": "approval", "owner": "mcp", "state": "done"})   # its approval_end
        prod.on_job_end({"job_id": JOB2, "kind": "crawl", "owner": "gui", "state": "done"})      # the person's
        assert prod.flush()
        (entry,) = rows(store)
        assert (entry.kind, entry.text_key, entry.level) == ("job_result", "activity.job.hf_download.not_done",
                                                             "warning")
        assert entry.params == {"kind": "hf_download", "state": "failed", "code": "insufficient_resources"}
        assert (entry.refs.job_id, entry.refs.window) == (JOB, "hf_browser")

    def test_a_job_ends_in_the_window_of_the_call_that_started_it(self, tmp_path):
        prod, store = producer(tmp_path)
        prod.on_registry_event("call_done", call_done(tool="probe_local_hardware", request_id="", job_id=JOB))
        prod.on_job_end({"job_id": JOB, "kind": "probe", "owner": "mcp:laptop", "state": "done"})
        prod.on_job_end({"job_id": JOB2, "kind": "probe", "owner": "mcp:laptop", "state": "done"})
        assert prod.flush()
        started, ended, other = rows(store)
        assert started.refs.window == ended.refs.window == "builder", "a local probe: the Builder recommends a board"
        assert other.refs.window == "boards", "a job no call of this process started: its kind's window"

    def test_cancel_job_points_to_the_window_of_the_job_it_stopped(self, tmp_path):
        jobs = JobService()
        job = jobs.create("snapshot_restore", "mcp")
        prod, store = producer(tmp_path)
        prod.attach(job_service=jobs)
        prod.on_registry_event("call_done", call_done(tool="cancel_job", request_id="", job_id=job.job_id))
        assert prod.flush()
        assert rows(store)[0].refs.window == "knowledge_snapshots"

    def test_build_ends(self):
        """Every build's end, with the fixed vocabulary only (DECISIONS round 40): the folder, the counts and any
        other fact stay out of the row; the details come over MCP."""
        ready = row_for_build({"request_id": REQ, "status": "completed", "end_reason": "done", "owner": "mcp",
                               "saved": "output:Rockchip/m/Q4", "completed": 1, "failed": 0})
        assert (ready.kind, ready.caller, ready.text_key, ready.level) == ("job_result", "mcp",
                                                                           "activity.build.ready", "info")
        assert ready.params == {"reason": "done"} and ready.refs == {"request_id": REQ, "window": "builder"}
        partial = row_for_build({"request_id": REQ, "status": "completed", "end_reason": "failed", "owner": "ditto",
                                 "completed": 1, "failed": 2})
        assert (partial.text_key, partial.level) == ("activity.build.error.failed", "warning")
        cancelled = row_for_build({"request_id": REQ, "status": "cancelled", "owner": "", "cancelled_by": "mcp:ci"})
        assert (cancelled.caller, cancelled.text_key, cancelled.params) == (
            "mcp:ci", "activity.build.stopped", {"reason": "cancelled", "by": "mcp:ci"})
        idle = row_for_build({"request_id": REQ, "status": "error", "end_reason": "idle", "owner": ""})
        assert (idle.caller, idle.text_key, idle.params) == ("", "activity.build.error.idle", {"reason": "idle"})
        old = row_for_build({"request_id": REQ, "status": "error", "owner": ""})   # facts without an end reason
        assert old.text_key == "activity.build.error.failed"
        assert row_for_build({"request_id": REQ, "status": "running", "owner": ""}) is None

    def test_registry_events_become_their_rows(self, tmp_path):
        prod, store = producer(tmp_path, program="gui")
        prod.on_registry_event("grant_used", {"tool": "download_hf_file", "actor": "mcp", "owner": "mcp#c1",
                                              "grant_id": "g_0123456789abcdef"})
        base = {"approval_id": "ap_0123456789abcdef", "tool": "deploy_to_device", "actor": "mcp:laptop",
                "owner": "mcp:laptop", "job_id": JOB}
        prod.on_registry_event("approval", {**base, "state": "pending"})
        prod.on_registry_event("approval_end", {**base, "state": "declined"})
        prod.on_registry_event("refused_before_question", {"tool": "start_build", "actor": "ditto", "owner": "ditto",
                                                           "code": "insufficient_resources", "stage": "look_up",
                                                           "blocker": "disk_full",
                                                           "person_action": "Open the trash or free space."})
        assert prod.flush()
        grant, asked, ended, blocked = rows(store)
        assert (grant.kind, grant.refs.grant_id, grant.refs.window) == ("grant_used", "g_0123456789abcdef",
                                                                        "hf_browser")
        assert (asked.kind, asked.refs.approval_id, asked.refs.job_id, asked.refs.window) == (
            "approval", "ap_0123456789abcdef", JOB, "approvals")
        assert (ended.kind, ended.text_key) == ("approval_end", "activity.approval_end.declined")
        assert (blocked.kind, blocked.text_key, blocked.level) == ("blocked", "activity.blocked.insufficient_resources",
                                                                   "warning")
        assert blocked.params == {"tool": "start_build", "code": "insufficient_resources", "stage": "look_up",
                                  "blocker": "disk_full"}
        assert blocked.refs.steps == [], "the person's action as text never goes into a row; the GUI finds the step"
        assert blocked.sender.role == "ditto" and blocked.refs.window == "builder"

    def test_a_refusal_carries_catalogue_steps_only(self, tmp_path):
        """Merge seam (a0 + b0): refused_before_question rows took the event's person_action as a step when it had
        the shape of one, so a facts_catalog key such as action.free_disk (a ui_text key, not a step) went into
        refs.steps, where the GUI renders catalogue ids. A person_action key now becomes the step its blockers name,
        and only ids the catalogue knows are written."""
        prod, store = producer(tmp_path, program="gui")
        base = {"tool": "start_build", "actor": "ditto", "owner": "ditto", "code": "insufficient_resources",
                "stage": "look_up"}
        prod.on_registry_event("refused_before_question", {**base, "blocker": "disk_full", "step": "images.free",
                                                           "person_action": "action.free_disk"})
        prod.on_registry_event("refused_before_question", {**base, "tool": "create_snapshot",
                                                           "blocker": "snapshot_no_room",
                                                           "person_action": "action.free_snapshots"})
        prod.on_registry_event("refused_before_question", {**base, "tool": "publish_pack", "code": "needs_person",
                                                           "blocker": "commit_required",
                                                           "person_action": "action.commit_required"})
        prod.on_registry_event("refused_before_question", {**base, "tool": "deploy_to_device", "code": "needs_person",
                                                           "step": "nothing.known",
                                                           "person_action": "action.pair_device"})
        assert prod.flush()
        disk, snapshots, commit, board = rows(store)
        assert disk.refs.steps == ["images.free"], "the key is not a step; the registry's step stays"
        assert snapshots.refs.steps == ["snapshots.free"], "a person_action key becomes its blocker's step"
        assert commit.refs.steps == [], "a key without a step is left out"
        assert board.refs.steps == ["board.trust"], "an id the catalogue does not know is left out"
        assert closed_refs({"steps": ["runtime.install", "action.free_disk", "nothing.known"]}) == {
            "steps": ["runtime.install"]}
        from orchestrator.tools.facts_catalog import BLOCKERS
        from orchestrator.utils.next_steps import catalogue
        assert {spec.step for spec in BLOCKERS.values() if spec.step} <= set(catalogue().steps)

    def test_a_refusal_in_a_loop_is_written_once_per_quiet_time(self, tmp_path):
        now = [1000.0]
        prod, store = producer(tmp_path, clock=lambda: now[0])
        refused = {"tool": "start_build", "actor": "mcp", "owner": "mcp", "code": "permission_denied",
                   "stage": "policy"}
        for _ in range(5):
            prod.on_registry_event("refused_before_question", refused)
        prod.on_registry_event("refused_before_question", {**refused, "tool": "deploy_to_device"})
        now[0] += BLOCKED_QUIET + 1
        prod.on_registry_event("refused_before_question", refused)
        assert prod.flush()
        assert [(e.params["tool"], e.refs.window) for e in rows(store)] == [
            ("start_build", "tool_permissions"), ("deploy_to_device", "tool_permissions"),
            ("start_build", "tool_permissions")]

    def test_an_ai_that_gives_up_shows_where_to_fix_it(self, tmp_path):
        prod, store = producer(tmp_path)
        prod.on_registry_event("call_done", call_done(tool="report_cannot_do", request_id="",
                                                      reason="hardware_unavailable"))
        assert prod.flush()
        (entry,) = rows(store)
        assert (entry.kind, entry.text_key, entry.refs.window) == ("blocked", "activity.blocked.hardware_unavailable",
                                                                   "boards")

    def test_changed_settings_name_their_keys(self, tmp_path):
        prod, store = producer(tmp_path)
        prod.on_registry_event("call_done", call_done(tool="update_settings", request_id="",
                                                      keys="language,offline_mode"))
        prod.on_registry_event("call_done", call_done(tool="update_settings", request_id="", keys="language=de"))
        assert prod.flush()
        named, odd = rows(store)
        assert (named.text_key, named.params["keys"], named.refs.window) == (
            "activity.tool.update_settings", "language, offline_mode", "settings")
        assert odd.text_key == "activity.action" and "keys" not in odd.params, "a value never passes as a key"

    def test_a_settings_change_that_changed_nothing_claims_no_change(self, tmp_path):
        """Review b0 (w6-b0-v): update_settings ends ok with nothing applied when every change is refused right
        before it is set (the person moved the value while the question was open). The row said "Changed
        something." then; a call that changed nothing makes no row (the audit log keeps the call)."""
        out = SettingsOut(applied=[])
        registry = registry_with(tmp_path, changing("update_settings", handler=lambda i: out, output=SettingsOut))
        prod, store = producer(tmp_path)
        prod.attach(registry)
        registry.call("update_settings", {}, ToolContext("mcp"))
        assert prod.flush()
        assert rows(store) == []
        assert row_for_call(call_done(tool="update_settings", request_id=""), program="mcp") is None

    def test_rows_hold_closed_values_only(self):
        params = closed_params({"tool": "start build", "keys": "language,de", "saved": "D:\\llmcf\\out",
                                "by": "Claude", "state": "done", "kind": "hf_download", "free_text": "anything",
                                "completed": "12", "failed": "-1"})
        assert params == {"state": "done", "kind": "hf_download", "completed": "12"}
        refs = closed_refs({"job_id": "job_1", "request_id": REQ, "window": "desktop", "grant_id": None,
                            "steps": ["runtime.install", "rm -rf /", "board.prune_on_device", "runtime.install"],
                            "approval_id": "ap_0123456789ABCDEF", "other": "x"})
        assert refs == {"request_id": REQ, "steps": ["runtime.install", "board.prune_on_device"]}
        for saved in ("~/out", "../x", "output:../../etc", "/srv/out", "D:/out", "\\\\nas\\share", "a\nb"):
            assert not PARAM_RULES["saved"](saved), saved
        assert PARAM_RULES["saved"]("output:Rockchip/SmolLM2/Q4_K_M")

    def test_a_host_path_behind_a_label_prefix_is_no_label(self):
        """Review b0 (w6-b0-v): the label rule looked at the whole value only, so a host path after a "<where>:"
        prefix passed as a label (is_absolute_anywhere sees "output:..." as relative) and could reach the feed with
        the person's account name in it. The part after the prefix is checked as well now."""
        for saved in ("output:/home/alice/builds/m", "models:C:/Users/alice/m", "output:C:\\Users\\alice",
                      "output://nas/share/alice", "output:~alice/m", "file:///home/alice/m", "output:",
                      "output:Rockchip/C:/Users/alice", "builds:\\\\nas\\alice", "Output:/home/alice/m",
                      "build-dir:/srv/alice/m", "x y:~/m"):
            assert not PARAM_RULES["saved"](saved), saved
            row = row_for_build({"request_id": REQ, "status": "completed", "owner": "mcp", "saved": saved})
            assert "saved" not in row.params, saved
        for saved in ("output:Rockchip/SmolLM2/Q4_K_M", "builds:Rockchip/m", "Q4_K_M", "my build 2026"):
            assert PARAM_RULES["saved"](saved), saved

    def test_every_row_the_producer_can_make_holds_closed_values_and_a_known_text(self):
        made = []
        for tool in list(TOOL_WINDOWS) + ["made_up_tool"]:
            made.append(row_for_call(call_done(tool=tool, job_id=JOB, keys="language", reason="failed",
                                               form="module_wizard"), program="gui"))
        for kind in JOB_KINDS:
            for state in ("done", "failed", "cancelled", "expired"):
                made.append(row_for_job({"job_id": JOB, "kind": kind, "owner": "mcp", "state": state,
                                         "error": "failed"}))
        for status in ("completed", "error", "cancelled"):
            made.append(row_for_build({"request_id": REQ, "status": status, "owner": "ditto", "saved": "output:x/y",
                                       "completed": 1, "failed": 0}))
        for kind, state in (("approval", "pending"), ("approval_end", "done"), ("approval_end", "withdrawn"),
                            ("approval_end", "expired"), ("approval_end", "failed"), ("approval_end", "odd"),
                            ("grant_used", ""), ("refused_before_question", "")):
            made.append(row_for_event(kind, {"tool": "start_build", "actor": "mcp", "state": state,
                                             "code": "limit_reached", "approval_id": "ap_0123456789abcdef"}))
        made = [row for row in made if row is not None]
        assert len(made) > 80
        for row in made:
            assert row.text_key in TEXTS["en"] and row.text_key in TEXTS["de"], row
            for name, value in row.params.items():
                assert PARAM_RULES[name](value), (row, name)
            for name in re.findall(r"{(\w+)}", TEXTS["en"][row.text_key]):
                assert name in row.params, (row.text_key, name)
            assert row.refs.get("window") in WINDOW_IDS or "window" not in row.refs


class TestAskedInTheChat:

    def test_only_dittos_turns_in_the_guis_chat_are_asked_there(self, tmp_path):
        prod, store = producer(tmp_path, program="gui")
        prod.on_registry_event("call_done", call_done(actor="ditto", channel="gui-chat"))
        prod.on_registry_event("call_done", call_done(actor="mcp", channel="gui-chat"))   # never an app
        prod.on_registry_event("call_done", call_done(actor="ditto", channel="gui"))      # Ditto without a turn
        cli, cli_store = producer(tmp_path / "cli", program="cli")
        cli.on_registry_event("call_done", call_done(actor="ditto", channel="gui-chat"))  # not the GUI process
        assert prod.flush() and cli.flush()
        assert [e.refs.asked_in for e in rows(store)] == ["gui-chat", "", ""]
        assert rows(store)[0].sender.client == "", "Ditto has no self-reported name"
        assert [e.refs.asked_in for e in rows(cli_store)] == [""]

    def test_the_program_follows_the_entry_point(self, tmp_path):
        assert [feed_program(k) for k in ("gui", "cli", "mcp", "api", "framework", "")] == [
            "gui", "cli", "mcp", "mcp", "cli", "cli"]
        kind = ["framework"]
        prod, store = producer(tmp_path, program=lambda: kind[0])
        kind[0] = "mcp"   # `mcp serve` names its program after the boot
        prod.on_registry_event("call_done", call_done())
        assert prod.flush() and rows(store)[0].program == "mcp"
        assert trusted_caller("mcp:laptop-2") == "mcp:laptop-2" and trusted_caller("mcp:") is None


class TestFailOpen:

    def test_a_damaged_feed_never_raises_and_listeners_still_get_the_row(self, tmp_path):
        store = FeedStore(tmp_path / "broken", key=KEY)
        store.path.parent.mkdir()
        store.path.write_bytes(b"not a database" * 200)
        prod = ActivityProducer(store, program="mcp")
        got = []
        prod.add_listener(got.append)
        prod.on_registry_event("call_done", call_done())
        assert prod.flush()
        assert [s.row_id for s in got] == [-1] and got[0].entry.kind == "ai_action"

    def test_a_row_the_feed_refuses_at_saving_still_reaches_the_listeners(self, tmp_path):
        """Review b0 (w6-b0-v): the feed redacts every string once more when it saves a row. A connection name the
        person gave that looks like a key ("mcp:sk-...") became "mcp:sk-REDACTED", which is no caller id, and saving
        raised a validation error instead of FeedWriteError: write_entry did not fail open, so the listeners never
        got the row (the module promises row id -1). Now the row comes back unsaved, and nothing raises."""
        prod, store = producer(tmp_path)
        got = []
        prod.add_listener(got.append)
        caller = "mcp:sk-" + "a1b2c3d4e5" * 2 + "abcdefghi"
        prod.on_registry_event("call_done", call_done(actor=caller, owner=caller))
        prod.on_registry_event("call_done", call_done())
        assert prod.flush()
        assert [s.row_id for s in got][0] == -1 and got[0].entry.sender.caller == caller
        assert got[1].row_id > 0 and [e.sender.caller for e in rows(store)] == ["mcp:laptop"]

    def test_without_a_feed_only_the_listeners_get_rows(self):
        prod = ActivityProducer(None, program="gui")
        got = []
        remove = prod.add_listener(got.append)
        prod.add_listener(lambda stored: 1 / 0)   # a broken listener stops nothing
        prod.on_job_end({"job_id": JOB, "kind": "crawl", "owner": "ditto", "state": "done"})
        assert prod.flush()
        assert [(s.row_id, s.entry.kind) for s in got] == [(0, "job_result")]
        remove()
        prod.on_job_end({"job_id": JOB2, "kind": "crawl", "owner": "ditto", "state": "done"})
        assert prod.flush() and len(got) == 1

    def test_a_malformed_event_is_dropped_quietly(self, tmp_path):
        prod, store = producer(tmp_path)
        prod.on_registry_event("call_done", {"tool": None, "actor": "mcp"})
        prod.on_registry_event("call_done", "not a mapping")    # type: ignore[arg-type]
        prod.on_job_end({"kind": object(), "owner": "mcp"})
        prod.on_build_end({})
        assert prod.flush() and rows(store) == []

    def test_close_writes_what_is_queued_and_stops_listening(self, tmp_path):
        registry = registry_with(tmp_path, changing())
        prod, store = producer(tmp_path)
        prod.attach(registry)
        for _ in range(20):
            registry.call("start_build", {}, ToolContext("mcp"))
        prod.close()
        assert len(rows(store)) == 20
        registry.call("start_build", {}, ToolContext("mcp"))
        prod.on_registry_event("call_done", call_done())
        assert prod.flush() and len(rows(store)) == 20

    def test_a_full_queue_drops_rows_instead_of_waiting(self, tmp_path, monkeypatch):
        prod, store = producer(tmp_path, queue_size=2)
        gate = activity_module.threading.Event()
        real_write = ActivityProducer._write
        monkeypatch.setattr(ActivityProducer, "_write", lambda self, row: (gate.wait(5), real_write(self, row))[1])
        for _ in range(10):
            prod.on_registry_event("call_done", call_done())
        gate.set()
        assert prod.flush() and 1 <= len(rows(store)) <= 3


# --- the framework, and other processes ----------------------------------------------------------------------------

class TestFramework:

    def test_every_framework_writes_rows_of_its_ai_calls_and_jobs(self, booted_framework):
        fw = booted_framework
        assert isinstance(fw.activity, ActivityProducer) and fw.get_component("activity") is fw.activity
        assert fw.orchestrator.artifact_label == fw.build_service.artifact_label
        registry = fw.tool_registry
        registry.register(changing("test_activity_tool"))
        registry.policy_store.replace(ToolPolicy(configured=True, reviewed=["test_activity_tool"],
                                                 overrides={"test_activity_tool": Decision.ALLOW}))
        registry.call("test_activity_tool", {}, ToolContext("mcp", connection="c1"))
        registry.call("test_activity_tool", {}, ToolContext("gui"))
        job = fw.job_service.start("pack_export", "mcp:laptop#c1", lambda handle: {"file": "x.zip"})
        written = wait_rows(fw.chat_feed, 2)
        assert [(e.kind, e.sender.caller) for e in written] == [("ai_action", "mcp"), ("job_result", "mcp:laptop")]
        assert written[0].text_key == "activity.action", "a tool without its own text gets the general one"
        assert written[1].refs.window == "community_hub" and written[1].refs.job_id == job.job_id


CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from pydantic import BaseModel
from orchestrator.Core.activity import ActivityProducer
from orchestrator.Core.chat_feed import FeedStore
from orchestrator.Core.job_service import JobService
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.output import ToolOutput
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolRegistry, ToolSpec

class In(BaseModel):
    text: str = "x"

class Out(ToolOutput):
    job_id: str = ""

import time
base = Path(sys.argv[1])
key = bytes.fromhex(sys.stdin.read().strip())
jobs = JobService()
registry = ToolRegistry(PolicyStore(base / "child_policy"), AuditLog(base / "child_audit.db"))

def download(inp):
    job = jobs.start("hf_download", "mcp#c1", lambda handle: (time.sleep(0.2), {"bytes": 1})[1])
    return Out(job_id=job.job_id)

registry.register(ToolSpec("download_hf_file", "Download", "Downloads", ToolTier.LOCAL_WRITE, In, Out, download))
registry.policy_store.replace(ToolPolicy(configured=True, reviewed=["download_hf_file"],
                                         overrides={"download_hf_file": Decision.ALLOW}))
producer = ActivityProducer(FeedStore(base / "state", key=key), program="mcp").attach(registry, jobs)
written = []
producer.add_listener(written.append)
out = registry.call("download_hf_file", {}, ToolContext("mcp", client="Claude Code 2.1", connection="c1"))
deadline = time.monotonic() + 30
while len(written) < 2 and time.monotonic() < deadline:   # the call's row and the job's end
    time.sleep(0.02)
producer.close()
"""


ENDING_CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from orchestrator.Core.activity import ActivityProducer
from orchestrator.Core.chat_feed import FeedStore
key = bytes.fromhex(sys.stdin.read().strip())
producer = ActivityProducer(FeedStore(Path(sys.argv[1]) / "state", key=key), program="cli")
for _ in range(5):
    producer.on_job_end({"job_id": "job_k3j2h1g0f9e8d7c6", "kind": "crawl", "owner": "ditto", "state": "done"})
# the process ends here, without the framework's shutdown (as a llm-cli command does)
"""


class TestOtherProcesses:

    def test_a_process_that_ends_without_shutdown_still_writes_its_rows(self, tmp_path):
        child = subprocess.run([sys.executable, "-c", ENDING_CHILD, str(tmp_path), str(REPO_ROOT)], input=KEY.hex(),
                               capture_output=True, text=True, timeout=120, env=dict(os.environ))
        assert child.returncode == 0, child.stderr[-3000:]
        written = rows(FeedStore(tmp_path / "state", key=KEY))
        assert [(e.kind, e.program) for e in written] == [("job_result", "cli")] * 5

    def test_a_call_and_a_job_end_in_an_mcp_process_reach_the_guis_watcher(self, tmp_path, qapp):
        from orchestrator.gui.chat_panel.watcher import FeedWatcher
        store = FeedStore(tmp_path / "state", key=KEY)
        watcher = FeedWatcher(store, start=False)
        assert watcher.check() == 0
        child = subprocess.run([sys.executable, "-c", CHILD, str(tmp_path), str(REPO_ROOT)], input=KEY.hex(),
                               capture_output=True, text=True, timeout=180, env=dict(os.environ))
        assert child.returncode == 0, child.stderr[-3000:]
        got: List[StoredEntry] = []
        watcher.entries.connect(got.extend)
        assert watcher.check() == 2
        by_kind = {s.entry.kind: s.entry for s in got}
        call, end = by_kind["ai_action"], by_kind["job_result"]
        assert (call.kind, call.program, call.sender.caller, call.sender.client) == (
            "ai_action", "mcp", "mcp", "Claude Code 2.1")
        assert (call.text_key, call.refs.window) == ("activity.tool.download_hf_file", "hf_browser")
        assert (end.kind, end.text_key, end.refs.job_id) == ("job_result", "activity.job.hf_download.done",
                                                             call.refs.job_id)
        watcher.stop()


# --- settings another process saved ---------------------------------------------------------------------------------

class TestReloadUserConfig:

    @pytest.fixture
    def folder(self, tmp_path):
        folder = tmp_path / "configs"
        folder.mkdir()
        return folder

    def test_a_setting_another_process_saved_reaches_this_one(self, folder):
        gui, mcp = ConfigManager(folder), ConfigManager(folder)
        before = gui.revision("language")
        assert mcp.set("language", "de") and mcp.save_user_config()
        result = gui.reload_user_config(["language"])
        assert (result.changed, result.conflicts, result.problem) == (("language",), {}, "")
        assert gui.get("language") == "de" and gui.revision("language") == before + 1
        assert gui.reload_user_config(["language"]).changed == (), "in step: nothing changes twice"

    def test_a_value_this_process_set_and_did_not_save_is_kept_as_a_conflict(self, folder):
        gui, mcp = ConfigManager(folder), ConfigManager(folder)
        gui.set("build_jobs", 2)                      # the person's change in this process, not saved yet
        mcp.set("build_jobs", 8)
        mcp.save_user_config()
        result = gui.reload_user_config(["build_jobs"])
        assert result.changed == () and result.conflicts == {"build_jobs": "changed_here"}
        assert gui.get("build_jobs") == 2

    def test_a_value_this_process_saved_follows_a_later_change_elsewhere(self, folder):
        gui, mcp = ConfigManager(folder), ConfigManager(folder)
        gui.set("offline_mode", False)
        gui.save_user_config()
        mcp.reload_user_config()
        mcp.set("offline_mode", True)                 # an AI app tightened it (update_settings)
        mcp.save_user_config()
        result = gui.reload_user_config()
        assert "offline_mode" in result.changed and gui.get("offline_mode") is True

    def test_an_environment_variable_keeps_this_processs_value(self, folder, monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_GUI_THEME", "light")
        gui = ConfigManager(folder)
        (folder / "config.yml").write_text("gui_theme: dark\nlanguage: de\n", encoding="utf-8")
        result = gui.reload_user_config()
        assert result.conflicts == {"gui_theme": "environment"} and result.changed == ("language",)
        assert gui.get("gui_theme") == "light"

    def test_the_file_given_at_start_keeps_its_values(self, tmp_path):
        given = tmp_path / "given.yml"
        given.write_text("calibration_language: en\n", encoding="utf-8")
        cli = ConfigManager(given)   # `llm-cli -c given.yml`: the user's config.yml lies in the same folder
        assert cli.user_config_file == tmp_path / "config.yml" and cli.get("calibration_language") == "en"
        (tmp_path / "config.yml").write_text("calibration_language: mixed\nlanguage: de\n", encoding="utf-8")
        result = cli.reload_user_config()
        assert result.conflicts == {"calibration_language": "explicit_file"} and result.changed == ("language",)
        assert cli.get("calibration_language") == "en"

    def test_invalid_values_and_unreadable_files_change_nothing(self, folder):
        gui = ConfigManager(folder)
        (folder / "config.yml").write_text("build_jobs: many\nlanguage: de\n", encoding="utf-8")
        result = gui.reload_user_config()
        assert result.changed == ("language",) and gui.get("build_jobs") != "many"
        (folder / "config.yml").write_text("language: [unclosed\n", encoding="utf-8")
        broken = gui.reload_user_config()
        assert broken.problem.startswith("config.yml cannot be read") and broken.changed == ()
        assert str(folder) not in broken.problem, "no host path in the reason"
        (folder / "config.yml").write_text("- a list\n", encoding="utf-8")
        assert "does not contain settings" in gui.reload_user_config().problem
        assert gui.get("language") == "de"
