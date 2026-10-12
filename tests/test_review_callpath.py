"""Adversarial review of the call path (MCP design S5 and S8, DECISIONS round 4 item 1).

Attacks on the two-phase confirmation and the question rounds (a client that sends an answer with its very first
call, a question too long for a form, a fact that makes an allowed call ask), on the binding of a decision to the
facts the person was shown, on host paths in refusals, and on the limits (Ditto's early stop on fixes the registry
reported, refusals of the question limits on Ditto's path, the global build queue and the person's own builds).
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from orchestrator import mcp_server  # noqa: E402
from orchestrator.Core.build_service import BuildService, BuildServiceError  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec  # noqa: E402
from orchestrator.tools.spec import CallFacts  # noqa: E402

MODES = ["auto", "legacy"]


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = "a"


class LongIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: str


class Out(BaseModel):
    done: str = "ok"


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


@pytest.fixture
def registry(tmp_path):
    registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    registry.ran = []
    registry.register(ToolSpec("change_item", "Change an item", "", ToolTier.LOCAL_WRITE, ItemIn, Out,
                               lambda i: registry.ran.append(i.item_id) or Out()))
    registry.register(ToolSpec("wipe_item", "Wipe an item", "", ToolTier.LOCAL_WRITE, ItemIn, Out,
                               lambda i, c: registry.ran.append(i.item_id) or Out(), takes_context=True,
                               prepare=lambda i, c: CallFacts(consequence=f"Deletes item {i.item_id}.", cap="ask")))
    registry.register(ToolSpec("create_module", "Create a target module", "", ToolTier.LOCAL_WRITE, LongIn, Out,
                               lambda i: registry.ran.append("module") or Out()))
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"wipe_item": Decision.ALLOW},
                                          reviewed=[s.name for s in registry.list()]))
    return registry


def session(registry, calls, callback, mode="auto"):
    """Runs (name, arguments, input_responses) calls in one MCP session; input_responses seeds a call's first round."""
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry))

    async def go():
        async with Client(server, elicitation_callback=callback, mode=mode) as client:
            return [await client.call_tool(name, arguments, input_responses=seeded)
                    for name, arguments, seeded in calls]
    return asyncio.run(go())


def answers(asked, *actions):
    actions = list(actions)

    async def callback(ctx, params):
        asked.append(params.message)
        action = actions.pop(0) if actions else "accept"
        return ElicitResult(action=action, content={"approve": True} if action == "accept" else None)
    return callback


FORGED = {"anything": ElicitResult(action="accept", content={"approve": True})}


def outcomes(registry, tool):
    return [e for e in reversed(registry.audit.entries(100)) if e["tool"] == tool]


# --- S5: an answer the server never asked for opens no way around the question's record and limits -----------------

class TestAnswerWithTheFirstCall:

    def test_after_a_no_a_call_carrying_its_own_answer_is_not_asked_again(self, registry):
        asked = []
        first, again = session(registry, [("change_item", {"item_id": "a"}, None),
                                          ("change_item", {"item_id": "a"}, FORGED)], answers(asked, "decline"))
        assert first.is_error and again.is_error, text_of(again)
        assert "declined this exact call" in text_of(again)
        assert len(asked) == 1, "the person's 'no' stands for 10 minutes, whatever the client sends along"
        assert registry.ran == []

    def test_a_question_asked_for_a_round_that_carried_an_answer_is_recorded_first(self, registry):
        asked = []
        result, = session(registry, [("change_item", {"item_id": "b"}, FORGED)], answers(asked))
        assert not result.is_error and len(asked) == 1, text_of(result)
        entries = [e["outcome"] for e in outcomes(registry, "change_item")]
        assert entries == ["confirmation_requested", "started", "ok"], "the question is recorded before it is asked"
        requested = outcomes(registry, "change_item")[0]
        assert requested["question_sha256"] == outcomes(registry, "change_item")[-1]["question_sha256"]

    def test_such_a_round_counts_toward_the_questions_per_hour(self, registry):
        registry.limits = type(registry.limits)(questions_per_hour=1)
        asked = []
        first, second = session(registry, [("change_item", {"item_id": "c"}, FORGED),
                                           ("change_item", {"item_id": "d"}, FORGED)], answers(asked))
        assert not first.is_error and second.is_error and "questions in the last hour" in text_of(second)
        assert len(asked) == 1 and registry.ran == ["c"]

    def test_a_real_second_round_still_answers_its_question(self, registry):
        asked = []
        result, = session(registry, [("change_item", {"item_id": "e"}, None)], answers(asked))
        assert not result.is_error and len(asked) == 1 and registry.ran == ["e"]
        assert [e["outcome"] for e in outcomes(registry, "change_item")] == ["confirmation_requested", "started", "ok"]


# --- S5: the same record on both paths ------------------------------------------------------------------------------

class TestSameRecordOnBothPaths:

    @pytest.mark.parametrize("mode", MODES)
    def test_a_question_too_long_for_a_form_is_recorded_as_unavailable_with_a_next_step(self, registry, mode):
        asked = []
        result, = session(registry, [("create_module", {"definition": "x" * 9000}, None)], answers(asked), mode)
        assert result.is_error and asked == [] and registry.ran == []
        assert "Next: Make the call smaller" in text_of(result)
        end = outcomes(registry, "create_module")[-1]
        assert (end["outcome"], end["answer"], end["reason_code"]) == ("not_confirmed", "unavailable",
                                                                         "limit_reached")

    @pytest.mark.parametrize("mode", MODES)
    def test_a_question_a_fact_called_for_is_recorded_as_asked(self, registry, mode):
        asked = []
        result, = session(registry, [("wipe_item", {"item_id": "f"}, None)], answers(asked), mode)
        assert not result.is_error and len(asked) == 1
        requested = outcomes(registry, "wipe_item")[0]
        assert (requested["outcome"], requested["decision"]) == ("confirmation_requested", "ask"), \
            "the tool is allowed, the fact made it ask: the record says so, as on Ditto's path"
        registry.call("wipe_item", {"item_id": "g"}, ToolContext("ditto", confirm=lambda s, i: True))
        dittos = [e for e in outcomes(registry, "wipe_item") if e["actor"] == "ditto"]
        assert dittos[0]["decision"] == "ask"

    def test_a_question_refused_by_a_recent_no_records_the_decision_it_was_asked_under(self, registry):
        asked = []
        session(registry, [("wipe_item", {"item_id": "h"}, None), ("wipe_item", {"item_id": "h"}, None)],
                answers(asked, "decline"))
        refused = outcomes(registry, "wipe_item")[-1]
        assert (refused["outcome"], refused["decision"]) == ("rate_limited", "ask")


# --- S5: a decision on a healing proposal is bound to the proposal that was shown -----------------------------------

def test_resolve_healing_decides_nothing_without_the_facts_it_was_shown(tmp_path):
    from orchestrator.tools.framework_tools import register_framework_tools
    decided = []
    service = SimpleNamespace(resolve_healing=lambda *a, **k: decided.append((a, k)))
    framework = SimpleNamespace(build_service=service, job_service=None)
    registry = ToolRegistry(PolicyStore(tmp_path))
    try:
        register_framework_tools(registry, framework)
    except Exception:  # a stand-in service: only the handler matters here
        pass
    spec = registry.get("resolve_healing")
    inp = spec.input_model(request_id="req_1", approve=True)
    with pytest.raises(ToolError) as exc:
        spec.handler(inp, ToolContext("mcp"))
    assert "nothing was applied" in exc.value.message and decided == []


@pytest.mark.parametrize("approve, first", [
    (True, "Deletes the Docker build cache and the dangling framework images on this computer, then retries build "
           "request req_1 (models:qwen/model.gguf for Rockchip (gguf Q4_K_M)) once."),
    (False, "Dismisses the self-healing proposal of build request req_1 (models:qwen/model.gguf for Rockchip (gguf "
            "Q4_K_M)); the failed build is not retried."),
])
def test_the_healing_questions_first_line_names_the_build_not_only_its_id(approve, first):
    """DECISIONS round 4 item 1: what the person reads first names the build, not only a raw id."""
    from orchestrator.tools.framework_tools import healing_facts
    proposal = {"strategy": "docker_prune", "summary": "Disk full", "root_cause": "No space", "fix": "prune",
                "automatic": True, "ai_generated": False, "fingerprint": "f",
                "request": "models:qwen/model.gguf for Rockchip (gguf Q4_K_M)"}
    facts = healing_facts(SimpleNamespace(healing_proposal=lambda rid, a: proposal), "req_1", approve)
    assert facts.consequence == first


# --- labels for AI callers: refusals never name the real path of a pinned input --------------------------------------

class TestRefusalsNameTheLabel:

    def test_a_pinned_model_that_vanished_is_named_by_its_label(self, tmp_path):
        gone = tmp_path / "private-folder" / "model.gguf"
        with pytest.raises(BuildServiceError) as exc:
            BuildService._check_model_source(str(gone), "models:model.gguf")
        assert "models:model.gguf" in str(exc.value) and "private-folder" not in str(exc.value)

    def test_a_pinned_dataset_that_vanished_is_named_by_its_label(self, tmp_path, monkeypatch):
        from orchestrator.schemas.build import BuildRequestIn
        caps = SimpleNamespace(normalize=lambda f, b, q: ("gguf", None, "Q4_K_M"), check_request=lambda *a, **k: [])
        service = BuildService(SimpleNamespace())
        monkeypatch.setattr(service, "resolve_target", lambda target: "Rockchip")
        monkeypatch.setattr(service, "capabilities", lambda target: caps)
        gone = str(tmp_path / "private-folder" / "calib.txt")
        pins = SimpleNamespace(model=None, dataset=SimpleNamespace(ref="datasets:calib.txt"))
        monkeypatch.setattr(service, "_confined", lambda request: (request.model_copy(
            update={"dataset_path": gone}), pins))
        request = BuildRequestIn(model="Qwen/Qwen2.5-0.5B-Instruct", target="Rockchip", format="gguf",
                                 dataset_path="datasets:calib.txt")
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request)
        assert "datasets:calib.txt" in str(exc.value) and "private-folder" not in str(exc.value)


# --- S8: Ditto's early stop sees the field the registry's input errors name -----------------------------------------

class ModuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z]+$")
    architecture: Literal["aarch64", "x86_64"]
    cores: int = Field(gt=0)


GOOD = {"name": "board", "architecture": "aarch64", "cores": 4}


@pytest.fixture
def modules(tmp_path):
    registry = ToolRegistry(PolicyStore(tmp_path))
    registry.created = []
    registry.register(ToolSpec("create_target_module", "Create a target module", "", ToolTier.READ, ModuleIn, Out,
                               lambda i: registry.created.append(i.name) or Out()))
    return registry


def ditto_loop(registry, calls):
    """Ditto's real loop and executor over the registry: a model that makes `calls`, one per turn, then answers."""
    from orchestrator.Core.ditto_manager import DittoCoder
    from orchestrator.llm.tool_loop import run_tool_loop
    from orchestrator.llm.types import ChatRequest, ChatResult, Message, ToolCall, ToolSpecLLM, Usage
    calls = list(calls)

    class Model:
        def chat(self, request):
            if calls:
                call = ToolCall(id=f"c{len(calls)}", name="create_target_module", arguments=calls.pop(0))
                return ChatResult(text="", stop="tool_use", tool_calls=[call], usage=Usage(),
                                  message=Message(role="assistant", tool_calls=[call]))
            return ChatResult(text="done", stop="end", usage=Usage(), message=Message(role="assistant", content="done"))
    execute = DittoCoder._executor(SimpleNamespace(), registry, None, [], None)
    tools = [ToolSpecLLM(name="create_target_module", description="", input_schema={"type": "object"})]
    return run_tool_loop(Model(), ChatRequest(messages=[Message.user("add my board")], tools=tools), execute,
                         max_steps=8)


class TestEarlyStopThroughTheRegistry:

    def test_fixing_one_field_after_another_is_progress(self, modules):
        loop = ditto_loop(modules, [{**GOOD, "name": "Bad Name"}, {**GOOD, "architecture": "riscv"},
                                    {**GOOD, "cores": 0}, GOOD])
        assert [m for m in loop.new_messages if m.role == "system"] == [], "three fields fixed: never stopped"
        assert loop.stop == "end" and modules.created == ["board"], "the fixed call ran"

    def test_three_wrong_values_for_one_field_still_stop(self, modules):
        loop = ditto_loop(modules, [{**GOOD, "name": "A A"}, {**GOOD, "name": "B B"}, {**GOOD, "name": "C C"}, GOOD])
        assert len([m for m in loop.new_messages if m.role == "system"]) == 1 and modules.created == []

    @pytest.mark.parametrize("arguments, field", [
        ({**GOOD, "architecture": "riscv"}, "architecture"),     # the normalizer's own check
        ({**GOOD, "cores": 0}, "cores"),                          # the input model's
        ({"name": "board", "architecture": "aarch64"}, "cores"),  # a missing argument
        ({**GOOD, "name": "Bad Name", "cores": 0}, "cores,name"),
    ])
    def test_input_errors_name_their_field(self, modules, arguments, field):
        with pytest.raises(ToolError) as exc:
            modules.call("create_target_module", arguments, ToolContext("ditto"))
        assert exc.value.field == field

    def test_the_build_services_input_refusals_name_their_field(self, tmp_path):
        with pytest.raises(BuildServiceError) as exc:
            BuildService._check_model_source("not a model or repo", None)
        assert exc.value.field == "model"
        registry = ToolRegistry(PolicyStore(tmp_path))

        def refuse(inp):
            raise exc.value
        registry.register(ToolSpec("start", "Start", "", ToolTier.READ, ItemIn, Out, refuse))
        with pytest.raises(ToolError) as raised:
            registry.call("start", {}, ToolContext("ditto"))
        assert (raised.value.code, raised.value.field) == ("invalid_value", "model")


# --- S8: a refusal of the question limits is not a failed call, on either path ---------------------------------------

class TestQuestionLimitsAreNotFailures:

    def test_after_a_no_dittos_retries_are_refused_as_declined_like_an_mcp_clients(self, registry):
        asked, errors = [], []
        for _ in range(4):
            with pytest.raises(ToolError) as exc:
                registry.call("change_item", {"item_id": "a"},
                              ToolContext("ditto", session="s", confirm=lambda s, i: bool(asked.append(1))))
            errors.append(exc.value)
        assert len(asked) == 1
        assert [e.code for e in errors[1:]] == ["declined"] * 3, "never repeat_refused for a limit"
        assert all("declined this exact call" in e.message and not e.message.startswith("This exact call already")
                   for e in errors[1:])
        mcp_asked = []
        results = session(registry, [("change_item", {"item_id": "b"}, None)] * 4, answers(mcp_asked, "decline"))
        assert len(mcp_asked) == 1 and all("declined this exact call" in text_of(r) for r in results[1:])

    def test_the_questions_per_hour_refusal_is_not_counted_either(self, registry):
        registry.limits = type(registry.limits)(questions_per_hour=1)
        registry.call("change_item", {"item_id": "a"}, ToolContext("ditto", session="s", confirm=lambda s, i: True))
        codes = []
        for _ in range(3):
            with pytest.raises(ToolError) as exc:
                registry.call("change_item", {"item_id": "b"},
                              ToolContext("ditto", session="s", confirm=lambda s, i: True))
            codes.append(exc.value.code)
        assert codes == ["limit_reached"] * 3


# --- S8: the global build queue never refuses the person's own builds ------------------------------------------------

def test_a_queue_full_of_ai_builds_never_refuses_the_persons_own_build(tmp_path):
    from orchestrator.Core.builder import ModelFormat, OptimizationLevel
    from orchestrator.Core.orchestrator import (MAX_UNFINISHED_REQUESTS, BuildQueueFull, BuildRequest,
                                                LLMOrchestrator, OrchestrationStatus, PriorityLevel, WorkflowState,
                                                WorkflowType)
    orch = LLMOrchestrator(SimpleNamespace(targets_dir=str(tmp_path), get=lambda k, d=None: d),
                           build_engine=SimpleNamespace(targets_dir=str(tmp_path)))
    for i in range(MAX_UNFINISHED_REQUESTS):
        orch._workflows[f"q{i}"] = WorkflowState(f"q{i}", OrchestrationStatus.QUEUED, datetime.now(),
                                                 owner=f"mcp#{i}")
    orch._ensure_worker_running = lambda: None

    def request(owner):
        return BuildRequest("", WorkflowType.SIMPLE_CONVERSION, PriorityLevel.NORMAL, ["Qwen/Qwen2.5-0.5B-Instruct"],
                            ["Rockchip"], [ModelFormat.GGUF], OptimizationLevel.BALANCED, [], False, "out",
                            owner=owner)

    async def submit():
        own = await orch.submit_build_request(request(""))  # the GUI or the CLI: the person's own build
        with pytest.raises(BuildQueueFull):
            await orch.submit_build_request(request("mcp#x"))
        with pytest.raises(BuildQueueFull):
            await orch.submit_build_request(request("ditto"))
        return own
    assert asyncio.run(submit())


def test_a_refused_start_does_not_use_up_the_daily_quota(tmp_path):
    """The queue was full (or the build service refused the request): nothing started, so nothing is counted."""
    from orchestrator.tools.spec import ToolLimits
    registry = ToolRegistry(PolicyStore(tmp_path))
    results = [BuildServiceError("The build queue is full.", code="limit_reached", next="Wait."), None]

    def start(inp):
        error = results.pop(0)
        if error is not None:
            raise error
        return Out()
    registry.register(ToolSpec("start", "Start", "", ToolTier.READ, ItemIn, Out, start,
                               limits=ToolLimits(per_day=1, what="builds")))
    with pytest.raises(ToolError) as exc:
        registry.call("start", {}, ToolContext("ditto"))
    assert exc.value.code == "limit_reached"
    registry.call("start", {"item_id": "b"}, ToolContext("ditto"))  # still the first start of the day
    with pytest.raises(ToolError) as exc:
        registry.call("start", {"item_id": "c"}, ToolContext("ditto"))
    assert "in the last 24 hours" in exc.value.message


def test_a_model_that_ignores_the_stop_gets_two_more_turns_not_the_whole_budget(modules):
    loop = ditto_loop(modules, [{**GOOD, "name": "A A"}] * 3 + [GOOD] * 6)
    not_run = [m for m in loop.new_messages if m.role == "tool" and m.content.startswith("Not run")]
    assert loop.steps == 5 and len(not_run) == 2 and modules.created == [], "nothing runs after the stop"


# --- round-1 leftover: a confirmation that failed or timed out names a reason code and a next step -------------------

class TestFailedConfirmationsNameAReasonAndANextStep:

    def test_a_failed_confirmation_request(self, registry):
        import mcp_types

        async def closed(ctx, params):
            return mcp_types.ErrorData(code=-32603, message="the confirmation window was closed")
        result, = session(registry, [("change_item", {"item_id": "a"}, None)], closed, "legacy")
        assert result.is_error and registry.ran == [] and "Next:" in text_of(result)
        end = outcomes(registry, "change_item")[-1]
        assert (end["outcome"], end["reason_code"]) == ("not_confirmed", "failed")

    def test_a_timed_out_confirmation(self, registry, monkeypatch):
        import anyio
        monkeypatch.setattr(mcp_server, "ELICITATION_TIMEOUT", 0.3)

        async def never(ctx, params):
            await anyio.sleep(5)
            return ElicitResult(action="accept", content={"approve": True})
        result, = session(registry, [("change_item", {"item_id": "a"}, None)], never, "legacy")
        assert result.is_error and registry.ran == [] and "Next:" in text_of(result)
        end = outcomes(registry, "change_item")[-1]
        assert (end["outcome"], end["answer"], end["reason_code"]) == ("confirmation_timeout", "timeout",
                                                                         "needs_person")
