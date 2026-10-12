"""Adversarial review of the call path (MCP design S5 and S8, DECISIONS round 4 item 1).

Attacks on the two-phase confirmation and the question rounds (a client that sends an answer with its very first
call, a question too long for a form, a fact that makes an allowed call ask), on the binding of a decision to the
facts the person was shown, on host paths in refusals, and on the limits (Ditto's early stop on fixes the registry
reported, refusals of the question limits on Ditto's path, the global build queue and the person's own builds).
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

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
    registry.policy_store.save(ToolPolicy(configured=True, overrides={"wipe_item": Decision.ALLOW},
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
