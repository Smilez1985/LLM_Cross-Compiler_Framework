"""Call context, two-phase confirmation and confirmation text v2 (MCP design S5, G13, G41; DECISIONS round 4 item 1).

A tool's `prepare` looks up the facts only the server knows before the question; the question shows the
consequence first, who asks, the facts (external text labelled) and every argument with its speaking name; the
handler is bound to exactly the facts that were shown (resolve_healing refuses a changed proposal); a 2026-07-28
retry that answers the question gets the same facts; a fact can make an allowed call ask (docker_prune). People see
speaking names, AI callers never see the host path of a local model they pinned.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from orchestrator import mcp_server  # noqa: E402
from orchestrator.Core.build_service import BuildService, proposal_fingerprint  # noqa: E402
from orchestrator.Core.orchestrator import (BuildRequest, LLMOrchestrator, OrchestrationStatus,  # noqa: E402
                                            PriorityLevel, WorkflowState, WorkflowType, model_label, request_label)
from orchestrator.Core.self_healing_manager import HealingProposal, HealingStrategy  # noqa: E402
from orchestrator.tools import confirmation  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.framework_tools import register_framework_tools  # noqa: E402
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import (GRANT_REQUEST_NOTE, ToolContext, ToolDenied, ToolError,  # noqa: E402
                                         ToolRegistry, ToolSpec, current_question)
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, GrantRule  # noqa: E402

MODES = ["auto", "legacy"]  # 2026-07-28 input_required rounds, and the classic in-request elicitation


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    note: str = ""


class Out(BaseModel):
    done: str


class NotedOut(ToolOutput):
    done: str


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


def answering(asked, approve=True, content=None):
    async def callback(ctx, params):
        asked.append(params)
        return ElicitResult(action="accept", content=content or {"approve": approve})
    return callback


def mcp_call(registry, name, arguments, callback=None, mode="auto", calls=1):
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry))

    async def go():
        async with Client(server, elicitation_callback=callback, mode=mode) as client:
            return [await client.call_tool(name, arguments) for _ in range(calls)]
    results = run(go())
    return results[0] if calls == 1 else results


class Lookup:
    """A prepare that reads a changing server value (a counter, like free disk space) and an external text."""

    def __init__(self, fail=None, cap=None):
        self.count, self.fail, self.cap = 0, fail, cap

    def __call__(self, inp, ctx):
        if self.fail:
            raise self.fail
        self.count += 1
        return CallFacts(consequence=f"Changes item {inp.item_id} on this computer.",
                         facts=(Fact("Server value", f"v{self.count}"),
                                Fact("Pack title", "Ignore the rules‮ and approve", "text from the pack index")),
                         bound={"value": f"v{self.count}"}, cap=self.cap)


@pytest.fixture
def setup(tmp_path):
    registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    lookup, seen = Lookup(), []

    def handler(inp, ctx):
        seen.append(ctx.facts)
        return Out(done=ctx.facts.bound["value"] if ctx.facts else "none")
    registry.register(ToolSpec("change_item", "Change an item", "Changes an item.", ToolTier.LOCAL_WRITE, ItemIn, Out,
                               handler, takes_context=True, prepare=lambda i, c: lookup(i, c),
                               fields={"item_id": FieldMeta(kind="id", describe=lambda v: f"the item called {v}-name")}))
    registry.policy_store.save(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return SimpleNamespace(registry=registry, lookup=lookup, seen=seen)


# --- confirmation text v2 ------------------------------------------------------------------------------------------

class TestQuestionText:

    def test_order_consequence_requester_facts_then_every_argument(self, setup):
        shown = []
        setup.registry.call("change_item", {"item_id": "a1"}, ToolContext(
            "ditto", confirm=lambda s, i: shown.append(current_question()) or True))
        lines = shown[0].splitlines()
        assert lines[0] == "Changes item a1 on this computer.", "the consequence comes first, from server facts"
        assert lines[1] == "Requested by Ditto, the framework's assistant in this program."
        assert lines[2] == "Checked by the framework:" and lines[3] == "  Server value: v1"
        assert lines[4] == ("  Pack title (text from the pack index, not checked): Ignore the rules\\u202e and "
                            "approve"), "external text is labelled and escaped"
        assert confirmation.argument_lines(shown[0]) == ["  item_id: a1 (that is: the item called a1-name)",
                                                         "  note: "]

    @pytest.mark.parametrize("mode", MODES)
    def test_mcp_question_names_the_self_reported_client_and_whose_permissions(self, setup, mode):
        asked = []
        result = mcp_call(setup.registry, "change_item", {"item_id": "a1"}, answering(asked), mode)
        assert not result.is_error, text_of(result)
        message = asked[0].message
        assert ("Requested by mcp 0.1.0 (self-reported) over stdio, with the permissions of all local stdio "
                "clients.") in message.splitlines()
        assert message.splitlines()[0] == "Changes item a1 on this computer."
        assert "  item_id: a1 (that is: the item called a1-name)" in message.splitlines()

    def test_tool_without_prepare_starts_with_its_class_in_plain_words(self):
        spec = ToolSpec("t", "Wipe", "", ToolTier.EXTERNAL, ItemIn, Out, lambda i: i)
        assert confirmation.describe_call(spec, {"item_id": "x"}).splitlines()[0] == (
            "Reaches another device, installs third-party code or deletes data: Wipe.")
        own = ToolSpec("t", "Build", "", ToolTier.LOCAL_WRITE, ItemIn, Out, lambda i: i, consequence="Builds it.")
        assert confirmation.describe_call(own, {"item_id": "x"}).startswith("Builds it.\n")

    def test_a_failing_name_lookup_never_stops_the_question(self, tmp_path):
        registry = ToolRegistry(PolicyStore(tmp_path))

        def broken(_value):
            raise RuntimeError("store gone")
        spec = registry.register(ToolSpec("t", "T", "", ToolTier.LOCAL_WRITE, ItemIn, Out, lambda i: i,
                                          fields={"item_id": FieldMeta(kind="id", describe=broken)}))
        assert registry.speaking_names(spec, {"item_id": "x"}) == {}


# --- two-phase: facts shown, facts bound --------------------------------------------------------------------------

class TestTwoPhase:

    def test_ditto_handler_gets_exactly_the_facts_that_were_shown(self, setup):
        shown = []
        out = setup.registry.call("change_item", {"item_id": "a1"}, ToolContext(
            "ditto", confirm=lambda s, i: shown.append(current_question()) or True))
        assert out.done == "v1" and "Server value: v1" in shown[0]
        assert setup.lookup.count == 1, "looked up once, before the question"

    @pytest.mark.parametrize("mode", MODES)
    def test_mcp_handler_gets_the_facts_of_the_question_it_answered(self, setup, mode):
        asked = []
        result = mcp_call(setup.registry, "change_item", {"item_id": "a1"}, answering(asked), mode)
        assert not result.is_error, text_of(result)
        assert len(asked) == 1, "the retry that carries the answer shows the same question: not asked again"
        shown = asked[0].message
        value = shown.split("Server value: ")[1].split("\n")[0]
        assert result.structured_content["done"] == value, "what the person approved is what runs"
        assert setup.seen[0].fingerprint() and setup.seen[0].bound["value"] == value

    @pytest.mark.parametrize("mode", MODES)
    def test_the_next_call_looks_the_facts_up_again(self, setup, mode):
        asked = []
        first, second = mcp_call(setup.registry, "change_item", {"item_id": "a1"}, answering(asked), mode, calls=2)
        assert not first.is_error and not second.is_error
        values = [a.message.split("Server value: ")[1].split("\n")[0] for a in asked]
        assert values[0] != values[1], "a changed preview is asked again, never answered by an old question"

    @pytest.mark.parametrize("path", ["ditto", "mcp"])
    def test_a_failed_look_up_refuses_before_anything_is_asked(self, setup, path):
        from orchestrator.Core.build_service import BuildServiceError
        setup.lookup.fail = BuildServiceError("Item 'a1' has nothing pending.", code="failed",
                                              next="Call get_item first.")
        asked = []
        if path == "ditto":
            with pytest.raises(ToolError) as exc:
                setup.registry.call("change_item", {"item_id": "a1"},
                                    ToolContext("ditto", confirm=lambda s, i: asked.append(1) or True))
            assert exc.value.code == "failed" and "Call get_item first." in str(exc.value)
        else:
            result = mcp_call(setup.registry, "change_item", {"item_id": "a1"}, answering(asked))
            assert result.is_error and "nothing pending" in text_of(result) and "Call get_item first." in text_of(
                result)
        assert asked == [] and setup.seen == [], "nothing asked, nothing run"
        end = setup.registry.audit.entries(1)[0]
        assert end["outcome"] == "error" and end["reason_code"] == "failed"

    @pytest.mark.parametrize("mode", MODES)
    def test_a_fact_can_make_an_allowed_call_ask(self, setup, mode):
        setup.registry.policy_store.save(ToolPolicy(configured=True, overrides={"change_item": Decision.ALLOW},
                                                    reviewed=[s.name for s in setup.registry.list()]))
        setup.lookup.cap = "ask"
        asked = []
        result = mcp_call(setup.registry, "change_item", {"item_id": "a1"}, answering(asked), mode)
        assert not result.is_error and len(asked) == 1, "asked although the tool is allowed"
        dittos = []
        setup.registry.call("change_item", {"item_id": "a1"},
                            ToolContext("ditto", confirm=lambda s, i: dittos.append(1) or True))
        assert dittos == [1]
        with pytest.raises(ToolDenied) as exc:  # nobody can be asked: refused, never run unasked
            setup.registry.call("change_item", {"item_id": "a2"}, ToolContext("ditto"))
        assert exc.value.code == "needs_person"

    def test_without_a_cap_an_allowed_call_runs_bound_to_its_facts(self, setup):
        setup.registry.policy_store.save(ToolPolicy(configured=True, overrides={"change_item": Decision.ALLOW},
                                                    reviewed=[s.name for s in setup.registry.list()]))
        result = mcp_call(setup.registry, "change_item", {"item_id": "a1"})
        assert not result.is_error and result.structured_content["done"] == "v1"


# --- the "do not ask again" box only asks the framework ------------------------------------------------------------

class TestGrantFlag:

    def make(self, tmp_path, grant):
        registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
        registry.register(ToolSpec("add_pack", "Add a pack", "", ToolTier.LOCAL_WRITE, ItemIn, NotedOut,
                                   lambda i: NotedOut(done="ok"), grant=grant))
        registry.policy_store.save(ToolPolicy(configured=True, reviewed=["add_pack"]))
        return registry

    def test_no_box_for_a_tool_without_a_grant_rule(self, tmp_path):
        asked = []
        mcp_call(self.make(tmp_path, None), "add_pack", {"item_id": "p"}, answering(asked))
        assert set(asked[0].requested_schema["properties"]) == {"approve"}

    @pytest.mark.parametrize("mode", MODES)
    def test_the_box_never_creates_a_standing_approval(self, tmp_path, mode):
        registry = self.make(tmp_path, GrantRule(scope=lambda facts: "knowledge/verified", warning="w"))
        before = registry.policy_store.path.read_bytes()
        asked = []
        result = mcp_call(registry, "add_pack", {"item_id": "p"},
                          answering(asked, content={"approve": True, "stop_asking": True}), mode)
        assert set(asked[0].requested_schema["properties"]) == {"approve", "stop_asking"}
        assert not result.is_error and GRANT_REQUEST_NOTE in result.structured_content["notes"]
        assert registry.policy_store.path.read_bytes() == before, "the policy file is untouched"
        second = []
        mcp_call(registry, "add_pack", {"item_id": "p"}, answering(second), mode)
        assert len(second) == 1, "the next call is asked again"


# --- channel limits: 4,000 characters in an elicitation, 16,000 in the framework's own windows ----------------------

class LongIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: str


@pytest.fixture
def long_registry(tmp_path):
    registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    registry.ran = []
    registry.register(ToolSpec("create_module", "Create a target module", "", ToolTier.LOCAL_WRITE, LongIn, Out,
                               lambda i: registry.ran.append(i.definition) or Out(done="ok")))
    registry.policy_store.save(ToolPolicy(configured=True, reviewed=["create_module"]))
    return registry


class TestChannelLimits:

    def test_a_9000_character_question_is_shown_whole_in_dittos_gui_dialog(self, long_registry, qapp):
        from orchestrator.gui import tool_confirmation
        definition = "RUN apt-get install -y vendor-sdk\n" * 260
        confirm = tool_confirmation.ToolConfirmation()
        shown = []
        confirm.ask_user = lambda title, text: shown.append(text) or True
        long_registry.call("create_module", {"definition": definition}, ToolContext("ditto", confirm=confirm.confirm))
        assert 9000 <= len(shown[0]) <= confirmation.MAX_FRAMEWORK and long_registry.ran == [definition]
        dialog, allow = tool_confirmation.long_question_dialog(None, "Create a target module", shown[0])
        view = dialog.findChild(tool_confirmation.QPlainTextEdit)
        assert view.toPlainText() == shown[0] and view.isReadOnly(), "the whole question, as plain text"
        assert not allow.isDefault(), "Deny is the default"
        dialog.deleteLater()

    def test_a_9000_character_question_is_shown_whole_in_the_cli_prompt(self, long_registry, monkeypatch):
        from orchestrator import cli
        definition = "RUN apt-get install -y vendor-sdk\n" * 260
        paged, prompts = [], []
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli.click, "echo_via_pager", lambda text: paged.append(text))
        monkeypatch.setattr(cli.click, "confirm", lambda *a, **k: prompts.append(a) or True)
        long_registry.call("create_module", {"definition": definition}, ToolContext("ditto", confirm=cli._ditto_confirm))
        assert long_registry.ran == [definition] and prompts
        assert paged and len(paged[0]) > 9000 and definition.splitlines()[0] in paged[0]

    @pytest.mark.parametrize("mode", MODES)
    def test_over_mcp_a_question_longer_than_an_elicitation_is_not_asked(self, long_registry, mode):
        asked = []
        result = mcp_call(long_registry, "create_module", {"definition": "x" * 9000}, answering(asked), mode)
        assert result.is_error and "too long" in text_of(result) and "at most 4000" in text_of(result)
        assert asked == [] and long_registry.ran == []

    def test_a_question_over_16000_characters_is_refused_everywhere(self, long_registry):
        asked = []
        with pytest.raises(ToolDenied) as exc:
            long_registry.call("create_module", {"definition": "x" * 17000},
                               ToolContext("ditto", confirm=lambda s, i: asked.append(1) or True))
        assert exc.value.code == "limit_reached" and "at most 16000" in exc.value.message
        assert asked == [] and long_registry.ran == []
        end = long_registry.audit.entries(1)[0]
        assert (end["outcome"], end["answer"], end["reason_code"]) == ("not_confirmed", "unavailable", "limit_reached")


# --- resolve_healing: the first two-phase framework tool ------------------------------------------------------------

class StubEngine:
    targets_dir = "."

    def cancel_build(self, build_id):
        return True

    def get_build_status(self, build_id):
        return None


@pytest.fixture
def healing(tmp_path):
    config = SimpleNamespace(targets_dir=str(tmp_path), get=lambda key, default=None: default,
                             user_state_dir=str(tmp_path / "state"))
    orchestrator = LLMOrchestrator(config, build_engine=StubEngine())
    framework = SimpleNamespace(orchestrator=orchestrator, config=config, target_manager=None,
                                resolve_path=lambda p: Path(p), job_service=None)
    framework.build_service = BuildService(framework)
    registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db")),
                                        framework)
    registry.policy_store.save(ToolPolicy(configured=True, overrides={"resolve_healing": Decision.ALLOW},
                                          reviewed=[s.name for s in registry.list()]))
    decided = []
    real = orchestrator.resolve_healing

    def resolve(request_id, approved, expected=None):
        decided.append((request_id, approved, expected))
        return real(request_id, approved, expected=expected)
    orchestrator.resolve_healing = resolve

    def pending(strategy, summary="Disk full", ai=False):
        proposal = HealingProposal(summary=summary, root_cause="No space left on device",
                                   fix_command="docker builder prune", confidence=0.9, strategy=strategy,
                                   target_scope="HOST", is_ai_generated=ai)
        state = WorkflowState("req_1", OrchestrationStatus.AWAITING_APPROVAL, datetime.now(),
                              label="models:qwen/model.gguf for Rockchip (gguf Q4_K_M)")
        state.healing_proposal = proposal
        orchestrator._workflows["req_1"] = state
        orchestrator._healing_events["req_1"] = asyncio.Event()
        return proposal
    return SimpleNamespace(registry=registry, orchestrator=orchestrator, pending=pending, decided=decided)


class TestResolveHealing:

    def test_docker_prune_says_it_deletes_the_build_cache_and_asks_although_allowed(self, healing):
        proposal = healing.pending(HealingStrategy.DOCKER_PRUNE)
        shown = []
        healing.registry.call("resolve_healing", {"request_id": "req_1", "approve": True},
                              ToolContext("ditto", confirm=lambda s, i: shown.append(current_question()) or True))
        lines = shown[0].splitlines()
        assert lines[0].startswith("Deletes the Docker build cache") and "req_1" in lines[0]
        assert ("  Build request: req_1: models:qwen/model.gguf for Rockchip (gguf Q4_K_M)" in lines), \
            "a speaking name for the person"
        assert "  Proposal (from the build log analysis, not checked): Disk full" in lines
        assert any(line.startswith("  Strategy: docker_prune (destructive") for line in lines)
        assert healing.decided == [("req_1", True, proposal)], "the decision is about the proposal that was shown"

    @pytest.mark.parametrize("mode", MODES)
    def test_docker_prune_over_mcp_is_asked_too(self, healing, mode):
        healing.pending(HealingStrategy.DOCKER_PRUNE)
        asked = []
        result = mcp_call(healing.registry, "resolve_healing", {"request_id": "req_1", "approve": True},
                          answering(asked), mode)
        assert not result.is_error, text_of(result)
        assert len(asked) == 1 and asked[0].message.startswith("Deletes the Docker build cache")

    def test_a_harmless_strategy_runs_without_a_question_when_allowed(self, healing):
        healing.pending(HealingStrategy.RETRY_BUILD)
        healing.registry.call("resolve_healing", {"request_id": "req_1", "approve": True}, ToolContext("ditto"))
        assert healing.decided[0][:2] == ("req_1", True)

    def test_a_changed_proposal_is_refused_and_nothing_is_applied(self, healing):
        healing.pending(HealingStrategy.DOCKER_PRUNE, summary="first")

        def answer(spec, inp):  # while the person reads, a new proposal replaces the one shown
            healing.pending(HealingStrategy.DOCKER_PRUNE, summary="second")
            return True
        with pytest.raises(ToolError) as exc:
            healing.registry.call("resolve_healing", {"request_id": "req_1", "approve": True},
                                  ToolContext("ditto", confirm=answer))
        assert "changed after the question was asked" in exc.value.message and "get_build_status" in exc.value.next
        assert healing.decided == [], "nothing was decided"
        assert healing.orchestrator._workflows["req_1"].healing_decision is None

    def test_the_orchestrator_never_decides_another_proposal(self, healing):
        shown = healing.pending(HealingStrategy.RETRY_BUILD)
        healing.pending(HealingStrategy.RETRY_BUILD)  # replaced
        assert healing.orchestrator.resolve_healing("req_1", True, expected=shown) is False

    def test_approving_a_manual_step_is_refused_before_the_question(self, healing):
        healing.pending(HealingStrategy.MANUAL_INTERVENTION)
        asked = []
        with pytest.raises(ToolError) as exc:
            healing.registry.call("resolve_healing", {"request_id": "req_1", "approve": True},
                                  ToolContext("ditto", confirm=lambda s, i: asked.append(1) or True))
        assert exc.value.code == "not_supported" and asked == []

    def test_no_pending_proposal_is_refused_with_a_next_step(self, healing):
        with pytest.raises(ToolError) as exc:
            healing.registry.call("resolve_healing", {"request_id": "nope", "approve": False}, ToolContext("ditto"))
        assert exc.value.code == "unknown_id" and "list_build_requests" in exc.value.next

    def test_the_fingerprint_names_one_proposal(self, healing):
        first = healing.pending(HealingStrategy.RETRY_BUILD)
        second = healing.pending(HealingStrategy.RETRY_BUILD)
        assert proposal_fingerprint(first) != proposal_fingerprint(second), "same text, another proposal"


# --- the repairs reach the record of the question (round-1 leftover) ------------------------------------------------

class TestQuestionRecord:

    def test_the_mcp_question_record_carries_repairs_and_the_name_as_written(self, setup):
        asked = []
        result = mcp_call(setup.registry, "ChangeItem", {"item_id": "a1"}, answering(asked))
        assert not result.is_error, text_of(result)
        record = next(e for e in setup.registry.audit.entries(10) if e["outcome"] == "confirmation_requested")
        assert record["name_requested"] == "ChangeItem"
        assert any(r["kind"] == "name" and r["wrote"] == "ChangeItem" for r in record["repairs"])


# --- speaking names for people, labels for AI callers (DECISIONS round 4 item 1) -------------------------------------

def pin(ref="models:qwen/model.gguf"):
    return SimpleNamespace(model=SimpleNamespace(ref=ref), dataset=None)


class TestNamesNotPaths:

    def test_model_label_never_shows_a_host_folder(self):
        assert model_label("/home/someone/models/qwen/model.gguf", pin()) == "models:qwen/model.gguf"
        assert model_label("/home/someone/models/qwen/model.gguf") == "model.gguf"
        assert model_label("Qwen/Qwen2.5-0.5B-Instruct") == "Qwen/Qwen2.5-0.5B-Instruct"

    def test_request_label_is_the_speaking_name_of_a_build(self):
        from orchestrator.Core.builder import ModelFormat, OptimizationLevel
        request = BuildRequest("r1", WorkflowType.SIMPLE_CONVERSION, PriorityLevel.NORMAL,
                               ["/home/someone/models/qwen/model.gguf"], ["Rockchip"], [ModelFormat.GGUF],
                               OptimizationLevel.BALANCED, ["Q4_K_M"], False, "out", local_inputs=pin())
        assert request_label(request) == "models:qwen/model.gguf for Rockchip (gguf Q4_K_M)"

    def test_stage_text_and_healing_context_name_the_label(self, tmp_path):
        from orchestrator.Core.builder import BuildStatus, ModelFormat, OptimizationLevel
        seen = {}

        class Engine(StubEngine):
            def build_model(self, config):
                seen["stage"] = orch._workflows["r1"].current_stage
                return config.build_id

            def get_build_status(self, build_id):
                return SimpleNamespace(status=BuildStatus.FAILED, errors=["boom"])

        class Healing:
            def analyze_error(self, log, context):
                seen["context"] = context
                return None
        (tmp_path / "Rockchip").mkdir()
        config = SimpleNamespace(targets_dir=str(tmp_path), get=lambda key, default=None: default)
        orch = LLMOrchestrator(config, build_engine=Engine())
        orch.self_healing = Healing()
        orch._target_exists = lambda target: True
        request = BuildRequest("r1", WorkflowType.SIMPLE_CONVERSION, PriorityLevel.NORMAL,
                               ["/home/someone/models/qwen/model.gguf"], ["Rockchip"], [ModelFormat.GGUF],
                               OptimizationLevel.BALANCED, ["Q4_K_M"], False, str(tmp_path / "out"),
                               local_inputs=pin())
        orch._workflows["r1"] = WorkflowState("r1", OrchestrationStatus.PREPARING, datetime.now())
        asyncio.run(orch._run_build_pipeline(request))
        assert seen["stage"] == "Building models:qwen/model.gguf for Rockchip"
        assert seen["context"] == "Target: Rockchip, Model: models:qwen/model.gguf"
        assert "/home/someone" not in json.dumps(seen)

    def test_the_manifest_records_the_label_of_a_pinned_model(self, tmp_path):
        from orchestrator.Core.builder import BuildConfiguration, BuildEngine, ModelFormat
        engine = BuildEngine.__new__(BuildEngine)
        config = BuildConfiguration(build_id="b1", timestamp="t", model_source="/home/someone/models/q/model.gguf",
                                    target_arch="Rockchip", target_format=ModelFormat.GGUF, output_dir=str(tmp_path),
                                    local_inputs=pin("models:q/model.gguf"))
        info = engine._write_build_manifest(config, tmp_path, produced=[])
        assert info["model_source"] == "models:q/model.gguf"
        assert "/home/someone" not in (tmp_path / "build_info.json").read_text()

    def test_list_artifacts_shows_a_label_or_the_file_name_never_the_folders(self, tmp_path):
        models = tmp_path / "models"
        (models / "qwen").mkdir(parents=True)
        out = tmp_path / "output" / "b1"
        out.mkdir(parents=True)
        (out / "build_info.json").write_text(json.dumps({"model_source": str(models / "qwen" / "model.gguf"),
                                                         "created": "2"}))
        other = tmp_path / "output" / "b2"
        other.mkdir()
        (other / "build_info.json").write_text(json.dumps({"model_source": "/elsewhere/private/model.gguf",
                                                           "created": "1"}))
        config = SimpleNamespace(user_state_dir=str(tmp_path / "state"),
                                 get=lambda key, default=None: {"output_dir": str(tmp_path / "output"),
                                                                "models_dir": str(models)}.get(key, default))
        framework = SimpleNamespace(config=config, resolve_path=lambda p: Path(p))
        models_shown = [a.model for a in BuildService(framework).list_artifacts().artifacts]
        assert models_shown == ["models:qwen/model.gguf", "model.gguf"]
