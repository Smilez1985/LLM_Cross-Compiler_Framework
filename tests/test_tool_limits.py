"""Limits, quotas and the repeat guard (MCP design S8, 6.5, 6.11 D8; G12 queue part).

The limits table in one place (spec.CallerLimits, ToolSpec.limits), counted per client before the policy and the
question; a limit hit is an audited `rate_limited` refusal with reason `limit_reached` and a retry time. After a
person's "no" the same call (normalized: a respelled call is the same) is not asked again for 10 minutes, three "no"
pause the tool's questions; the repeat guard refuses for a while, not until the process ends; Ditto's loop stops
early only on failures without progress and gives a fix a few more steps.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.evals.ditto_eval import EvalEnvironment  # noqa: E402
from orchestrator.llm.tool_loop import NOT_RUN_TEXT, STOP_TEXT, run_tool_loop  # noqa: E402
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ToolCall, ToolSpecLLM, Usage  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.limits import Limiter, wait_text  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError, ToolInputError, ToolRegistry, ToolSpec  # noqa: E402
from orchestrator.tools.spec import CallerLimits, Rate, ToolLimits  # noqa: E402

MODES = ["auto", "legacy"]
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
BUILD = {"model": MODEL, "target": "Rockchip", "format": "gguf", "quantization": "Q4_K_M"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = "x"


class Out(BaseModel):
    done: str = "ok"


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


def answering(asked, approve=True):
    async def callback(ctx, params):
        asked.append(params.message)
        return ElicitResult(action="accept", content={"approve": approve})
    return callback


def mcp_session(registry, calls, callback=None, mode="auto"):
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry))

    async def go():
        async with Client(server, elicitation_callback=callback, mode=mode) as client:
            return [await client.call_tool(name, arguments) for name, arguments in calls]
    return asyncio.run(go())


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make(tmp_path, clock):
    def build(limits=CallerLimits(), **tool_limits):
        registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"), limits=limits, clock=clock)
        registry.ran = []
        registry.register(ToolSpec("look", "Look", "", ToolTier.READ, ItemIn, Out, lambda i: Out()))
        registry.register(ToolSpec("change", "Change", "", ToolTier.LOCAL_WRITE, ItemIn, Out,
                                   lambda i: registry.ran.append(i.item) or Out(), limits=ToolLimits(**tool_limits)))
        registry.policy_store.save(ToolPolicy(configured=True, reviewed=["look", "change"]))
        return registry
    return build


def refused(registry, name, arguments, ctx):
    with pytest.raises(ToolError) as exc:
        registry.call(name, arguments, ctx)
    return exc.value


# --- rates and quotas ---------------------------------------------------------------------------------------------

class TestRatesAndQuotas:

    def test_reads_have_a_rate_per_client_and_say_when_to_retry(self, make, clock):
        registry = make(CallerLimits(read_rate=Rate(2, 10.0)))
        ctx = ToolContext("ditto", session="s")
        registry.call("look", {}, ctx)
        registry.call("look", {"item": "y"}, ctx)
        error = refused(registry, "look", {"item": "z"}, ctx)
        assert error.code == "limit_reached" and "Try again in 5 seconds." in error.message
        entry = registry.audit.entries(1)[0]
        assert (entry["outcome"], entry["reason_code"]) == ("rate_limited", "limit_reached")
        registry.call("look", {"item": "z"}, ToolContext("mcp", session="s"))  # another client: its own
        clock.now += 5
        registry.call("look", {"item": "z"}, ctx)  # refilled

    def test_a_limit_hit_is_never_a_failure_for_the_repeat_guard(self, make, clock):
        registry = make(CallerLimits(read_rate=Rate(1, 10.0)))
        ctx = ToolContext("ditto", session="s")
        registry.call("look", {}, ctx)
        for _ in range(3):
            assert refused(registry, "look", {}, ctx).code == "limit_reached"
        clock.now += 10
        registry.call("look", {}, ctx)  # not repeat_refused

    @pytest.mark.parametrize("mode", MODES)
    def test_over_mcp_the_limit_is_checked_before_the_question(self, make, mode):
        registry = make(CallerLimits(), per_day=1)
        asked = []
        results = mcp_session(registry, [("change", {"item": "a"}), ("change", {"item": "b"})], answering(asked),
                              mode)
        assert not results[0].is_error and results[1].is_error
        assert "1 calls in the last 24 hours" in text_of(results[1]) and "Try again in 24 hours" in text_of(results[1])
        assert len(asked) == 1 and registry.ran == ["a"], "the person is not asked about a call over its quota"

    def test_unfinished_runs_per_client(self, make):
        running = {"n": 3}
        registry = make(CallerLimits(), concurrent=3, running=lambda ctx: running["n"], what="builds")
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"change": Decision.ALLOW},
                                              reviewed=["look", "change"]))
        error = refused(registry, "change", {}, ToolContext("ditto"))
        assert "already has 3 unfinished builds (at most 3 at a time)" in error.message and registry.ran == []
        running["n"] = 2
        registry.call("change", {}, ToolContext("ditto"))
        assert registry.ran == ["x"]

    def test_a_declined_call_does_not_use_up_the_daily_quota(self, make):
        registry = make(CallerLimits(), per_day=1)
        with pytest.raises(ToolError):
            registry.call("change", {"item": "a"}, ToolContext("ditto", confirm=lambda s, i: False))
        registry.call("change", {"item": "b"}, ToolContext("ditto", confirm=lambda s, i: True))
        assert registry.ran == ["b"]

    def test_start_build_declares_the_build_quota(self):
        environment = EvalEnvironment(REPO_ROOT / "targets")
        try:
            limits = environment.registry.get("start_build").limits
            assert (limits.per_day, limits.concurrent, limits.what) == (20, 3, "builds")
            assert limits.running is not None
        finally:
            environment.close()


class TestGlobalQueue:

    def test_the_build_queue_holds_at_most_20_unfinished_requests(self, tmp_path):
        from orchestrator.Core.builder import ModelFormat, OptimizationLevel
        from orchestrator.Core.orchestrator import (MAX_UNFINISHED_REQUESTS, BuildQueueFull, BuildRequest,
                                                    LLMOrchestrator, OrchestrationStatus, PriorityLevel,
                                                    WorkflowState, WorkflowType)
        engine = SimpleNamespace(targets_dir=str(tmp_path))
        orch = LLMOrchestrator(SimpleNamespace(targets_dir=str(tmp_path), get=lambda k, d=None: d),
                               build_engine=engine)
        for i in range(MAX_UNFINISHED_REQUESTS - 1):
            orch._workflows[f"q{i}"] = WorkflowState(f"q{i}", OrchestrationStatus.QUEUED, datetime.now())
        orch._workflows["done"] = WorkflowState("done", OrchestrationStatus.COMPLETED, datetime.now())
        orch._ensure_worker_running = lambda: None
        request = BuildRequest("", WorkflowType.SIMPLE_CONVERSION, PriorityLevel.NORMAL, [MODEL], ["Rockchip"],
                               [ModelFormat.GGUF], OptimizationLevel.BALANCED, [], False, "out")

        async def submit():
            first = await orch.submit_build_request(request)
            with pytest.raises(BuildQueueFull):
                await orch.submit_build_request(BuildRequest("", WorkflowType.SIMPLE_CONVERSION, PriorityLevel.NORMAL,
                                                             [MODEL], ["Rockchip"], [ModelFormat.GGUF],
                                                             OptimizationLevel.BALANCED, [], False, "out"))
            return first
        assert asyncio.run(submit())
        assert MAX_UNFINISHED_REQUESTS == 20

    def test_a_full_queue_is_a_limit_with_a_next_step_for_tool_callers(self):
        from orchestrator.Core.build_service import BuildService, BuildServiceError
        from orchestrator.Core.orchestrator import BuildQueueFull

        class Full(BuildService):
            def _validated(self, request, local_paths):
                return request, None

        async def submit(_request):
            raise BuildQueueFull("The build queue is full (20 unfinished build requests); the request was not queued.")

        def run_coroutine(coro, timeout=None):
            return asyncio.run(coro)
        framework = SimpleNamespace(orchestrator=SimpleNamespace(submit_build_request=submit),
                                    run_coroutine=run_coroutine,
                                    config=SimpleNamespace(get=lambda k, d=None: d), resolve_path=lambda p: p)
        from orchestrator.schemas.build import BuildRequestIn
        with pytest.raises(BuildServiceError) as exc:
            Full(framework).start_build(BuildRequestIn(model=MODEL, target="Rockchip", format="gguf"))
        assert exc.value.code == "limit_reached" and "cancel one of your builds" in exc.value.next


# --- questions to the person ---------------------------------------------------------------------------------------

class TestQuestions:

    def test_after_a_no_the_same_call_is_not_asked_for_10_minutes(self, make, clock):
        registry = make()
        asked = []

        def no(spec, inp):
            asked.append(inp.item)
            return False
        refused(registry, "change", {"item": "a"}, ToolContext("ditto", confirm=no))
        error = refused(registry, "change", {"item": "a"}, ToolContext("ditto", confirm=no))
        assert error.code == "declined" and "not asked again for another 10 minutes" in error.message
        assert asked == ["a"], "not asked a second time"
        assert registry.audit.entries(1)[0]["outcome"] == "rate_limited"
        refused(registry, "change", {"item": "b"}, ToolContext("ditto", confirm=no))  # other arguments: asked
        assert asked == ["a", "b"]
        clock.now += 600
        refused(registry, "change", {"item": "a"}, ToolContext("ditto", confirm=no))
        assert asked == ["a", "b", "a"]

    def test_three_no_pause_the_tool_for_30_minutes(self, make, clock):
        registry = make()
        asked = []

        def no(spec, inp):
            asked.append(inp.item)
            return False
        for item in "abc":
            refused(registry, "change", {"item": item}, ToolContext("ditto", confirm=no))
            clock.now += 60
        error = refused(registry, "change", {"item": "d"}, ToolContext("ditto", confirm=no))
        assert error.code == "declined" and "paused for another" in error.message and asked == ["a", "b", "c"]
        clock.now += 1800
        refused(registry, "change", {"item": "d"}, ToolContext("ditto", confirm=no))
        assert asked[-1] == "d"

    def test_a_dismissed_dialog_is_not_a_no(self, make):
        registry = make()
        ctx = ToolContext("mcp", answer="cancelled")
        with pytest.raises(ToolError):
            registry.call("change", {"item": "a"}, ctx, approved=False)
        asked = []
        refused(registry, "change", {"item": "a"}, ToolContext("mcp", confirm=lambda s, i: asked.append(1) or False))
        assert asked == [1]

    def test_questions_per_hour_per_client(self, make, clock):
        registry = make(CallerLimits(questions_per_hour=2))
        ok = ToolContext("ditto", confirm=lambda s, i: True)
        registry.call("change", {"item": "a"}, ok)
        registry.call("change", {"item": "b"}, ok)
        error = refused(registry, "change", {"item": "c"}, ok)
        assert error.code == "limit_reached" and "2 questions in the last hour" in error.message
        assert registry.ran == ["a", "b"]
        clock.now += 3600
        registry.call("change", {"item": "c"}, ok)

    @pytest.mark.parametrize("mode", MODES)
    def test_over_mcp_a_respelled_call_counts_as_the_same(self, mode):
        environment = EvalEnvironment(REPO_ROOT / "targets")
        try:
            environment.registry.policy_store.save(ToolPolicy(
                configured=True, reviewed=[s.name for s in environment.registry.list()]))
            asked = []
            results = mcp_session(environment.registry, [("start_build", BUILD),
                                                         ("StartBuild", {**BUILD, "quantization": "q4_k_m"})],
                                  answering(asked, approve=False), mode)
            assert len(asked) == 1, "StartBuild with q4_k_m is the call the person just declined"
            assert "declined this exact call" in text_of(results[1])
            assert environment.framework.build_service.started == []
        finally:
            environment.close()


# --- the repeat guard over time ------------------------------------------------------------------------------------

class TestRepeatGuardCooldown:

    def test_a_refused_repeat_runs_again_after_the_cooldown(self, make, clock):
        registry = make()
        ran = []
        registry.register(ToolSpec("fail", "Fail", "", ToolTier.READ, ItemIn, Out,
                                   lambda i: ran.append(1) or (_ for _ in ()).throw(ValueError("busy"))))
        ctx = ToolContext("ditto", session="s")
        refused(registry, "fail", {}, ctx)
        refused(registry, "fail", {}, ctx)
        third = refused(registry, "fail", {}, ctx)
        assert third.code == "repeat_refused" and "another 10 minutes" in third.next and len(ran) == 2
        clock.now += 300
        assert refused(registry, "fail", {}, ctx).code == "repeat_refused", "a refusal does not extend the time"
        clock.now += 300
        assert refused(registry, "fail", {}, ctx).code == "failed" and len(ran) == 3, "runs again"

    def test_waiting_for_approval_is_not_a_failure(self, make):
        registry = make()
        registry.register(ToolSpec("wait", "Wait", "", ToolTier.READ, ItemIn, Out,
                                   lambda i: (_ for _ in ()).throw(ToolError("waiting", code="waiting_for_approval"))))
        ctx = ToolContext("ditto", session="s")
        for _ in range(4):
            assert refused(registry, "wait", {}, ctx).code == "waiting_for_approval"


def test_wait_text():
    assert (wait_text(1), wait_text(40), wait_text(600), wait_text(86400)) == (
        "in 1 second", "in 40 seconds", "in 10 minutes", "in 24 hours")
    limiter = Limiter(Clock())
    assert limiter.take("k", 1, 10.0) is None and limiter.take("k", 1, 10.0) == pytest.approx(10.0)


# --- Ditto's early honest stop and the step budget ----------------------------------------------------------------

TOOLS = [ToolSpecLLM(name="create_target_module", description="", input_schema={"type": "object"}),
         ToolSpecLLM(name="list_targets", description="", input_schema={"type": "object"})]


class Script:
    """A model that makes the given tool calls, one per turn, then answers."""

    def __init__(self, calls):
        self.calls, self.requests = list(calls), []

    def chat(self, request):
        self.requests.append(request)
        if self.calls:
            name, args = self.calls.pop(0)
            call = ToolCall(id=f"c{len(self.requests)}", name=name, arguments=args)
            return ChatResult(text="", stop="tool_use", tool_calls=[call], usage=Usage(),
                              message=Message(role="assistant", tool_calls=[call]))
        return ChatResult(text="done", stop="end", usage=Usage(), message=Message(role="assistant", content="done"))


def executor(errors):
    """Fails each call with the next (code, field), succeeds when the next is None."""
    errors = list(errors)

    def execute(name, args):
        error = errors.pop(0) if errors else None
        if error is None:
            return "ok"
        raise ToolInputError(f"bad {error[1]}", code=error[0], field=error[1])
    return execute


def notes(loop):
    return [m for m in loop.new_messages if m.role == "system"]


class TestEarlyHonestStop:

    def test_fixing_one_field_after_another_is_progress(self):
        client = Script([("create_target_module", {})] * 4)
        loop = run_tool_loop(client, ChatRequest(messages=[Message.user("add a board")], tools=TOOLS),
                             executor([("invalid_value", "name"), ("missing_argument", "architecture"),
                                       ("invalid_value", "base_os"), None]), max_steps=8)
        assert loop.stop == "end" and notes(loop) == [], "three errors on different fields, then a success"

    def test_three_identical_errors_stop_the_loop_once(self):
        client = Script([("create_target_module", {})] * 3 + [("list_targets", {})])
        loop = run_tool_loop(client, ChatRequest(messages=[Message.user("add a board")], tools=TOOLS),
                             executor([("invalid_value", "name")] * 3), max_steps=8)
        assert [m.content for m in notes(loop)] == [STOP_TEXT], "one instruction turn"
        not_run = [m for m in loop.new_messages if m.role == "tool" and m.content == NOT_RUN_TEXT]
        assert len(not_run) == 1, "the call after the stop is answered, never run"
        assert loop.stop == "end"

    def test_a_repeat_refusal_stops_at_once(self):
        client = Script([("list_targets", {})])
        loop = run_tool_loop(client, ChatRequest(messages=[Message.user("q")], tools=TOOLS),
                             executor([("repeat_refused", "")]), max_steps=8)
        assert [m.content for m in notes(loop)] == [STOP_TEXT]

    def test_the_history_only_grows_and_the_tools_stay_the_same_object(self):
        client = Script([("create_target_module", {})] * 3)
        run_tool_loop(client, ChatRequest(messages=[Message.user("add a board")], tools=TOOLS),
                      executor([("invalid_value", "name")] * 3), max_steps=8)
        for earlier, later in zip(client.requests, client.requests[1:]):
            assert later.messages[:len(earlier.messages)] == earlier.messages
            assert later.tools is earlier.tools

    def test_the_budget_grows_by_up_to_four_steps_while_a_fix_is_under_way(self):
        fixes = [("invalid_value", "name"), ("invalid_value", "architecture"), ("missing_argument", "board"),
                 ("invalid_value", "base_os"), None]
        client = Script([("create_target_module", {})] * 5)
        loop = run_tool_loop(client, ChatRequest(messages=[Message.user("add a board")], tools=TOOLS),
                             executor(fixes), max_steps=2, extra_steps=4)
        assert loop.stop == "end" and loop.steps == 6
        short = run_tool_loop(Script([("create_target_module", {})] * 5),
                              ChatRequest(messages=[Message.user("add a board")], tools=TOOLS), executor(fixes),
                              max_steps=2)
        assert short.stop == "max_steps" and short.steps == 2

    def test_the_budget_does_not_grow_for_other_failures(self):
        client = Script([("list_targets", {})] * 5)
        loop = run_tool_loop(client, ChatRequest(messages=[Message.user("q")], tools=TOOLS),
                             executor([("unknown_id", "a"), ("failed", "b"), ("not_supported", "c")]),
                             max_steps=2, extra_steps=4)
        assert loop.stop == "max_steps" and loop.steps == 2

    def test_the_stop_note_joins_the_tool_results_on_claude(self):
        # Imported here: importing the provider module while the whole suite is collected made a later GUI test
        # crash in this environment (a C extension at collection time, not the code under test).
        from orchestrator.llm.providers.anthropic import render_messages
        messages = [Message.user("q"),
                    Message(role="assistant", tool_calls=[ToolCall(id="c1", name="list_targets", arguments={})]),
                    Message.tool_result("c1", "bad", is_error=True), Message(role="system", content=STOP_TEXT)]
        _, turns = render_messages(messages)
        assert [t["role"] for t in turns] == ["user", "assistant", "user"]
        assert turns[-1]["content"][0]["type"] == "tool_result"
        assert turns[-1]["content"][1] == {"type": "text", "text": f"[System note] {STOP_TEXT}"}

    def test_dittos_executor_keeps_the_code_and_field(self, tmp_path):
        from orchestrator.Core.ditto_manager import DittoCoder
        registry = ToolRegistry(PolicyStore(tmp_path))
        registry.register(ToolSpec("fail", "Fail", "", ToolTier.READ, ItemIn, Out,
                                   lambda i: (_ for _ in ()).throw(ToolError("no", code="not_supported",
                                                                             field="item"))))
        execute = DittoCoder._executor(SimpleNamespace(), registry, None, [], None)
        with pytest.raises(ToolError) as exc:
            execute("fail", {})
        assert (exc.value.code, exc.value.field) == ("not_supported", "item")
