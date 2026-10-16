"""Round-1 leftovers of the tool call path (MCP design S1, S3a, S7): open questions per caller and session with a
timer and a record at session end, the long-poll's permission decision off the event loop, the audit view with the
new fields and speaking names, and a next step on every refusal whose reason is `failed`."""

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

pytest.importorskip("mcp")
import mcp_types  # noqa: E402
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.evals.ditto_eval import EvalEnvironment  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import FAILED_NEXT, ToolContext, ToolError, ToolRegistry, ToolSpec  # noqa: E402


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = "x"
    wait_seconds: int = 0


class Out(BaseModel):
    done: str = "ok"


class Strict(BaseModel):
    done: str


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    reg.register(ToolSpec("change", "Change an item", "", ToolTier.LOCAL_WRITE, ItemIn, Out, lambda i: Out()))
    reg.policy_store.replace(ToolPolicy(configured=True, reviewed=["change"]))
    return reg


def until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


# --- open questions -----------------------------------------------------------------------------------------------

class TestOpenQuestions:

    def test_the_same_question_of_two_sessions_is_two_questions(self, registry):
        questions = mcp_server._OpenQuestions(registry)
        one, two = ToolContext("mcp", session="a"), ToolContext("mcp:laptop", session="a")
        questions.asked("change", "d1", {"item": "x"}, one)
        questions.asked("change", "d1", {"item": "x"}, two)
        questions.answered("change", "d1", two)
        assert questions.open_count() == 1, "one client's answer never closes another client's question"

    def test_an_unanswered_question_expires_on_a_timer_without_another_request(self, registry):
        questions = mcp_server._OpenQuestions(registry, expiry=0.2)
        questions.asked("change", "d2", {"item": "x"}, ToolContext("mcp", session="s"))
        assert until(lambda: questions.open_count() == 0 and registry.audit.entries(1)), "the timer recorded it"
        entry = registry.audit.entries(1)[0]
        assert (entry["outcome"], entry["answer"], entry["question_sha256"]) == ("not_confirmed", "unanswered", "d2")

    def test_the_session_end_records_its_open_questions(self, registry):
        questions = mcp_server._OpenQuestions(registry)
        questions.asked("change", "d3", {}, ToolContext("mcp", session="s", connection="c1"))
        questions.asked("change", "d4", {}, ToolContext("mcp", session="t", connection="c2"))
        assert questions.close("c1") == 1 and questions.open_count() == 1
        assert registry.audit.entries(1)[0]["answer"] == "client_disconnected"

    def test_a_client_that_leaves_with_a_question_open_is_recorded(self, registry):
        server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                             tool_registry=registry))

        async def go():
            async def never(ctx, params):  # never called: the answer would come in a second round
                return ElicitResult(action="accept", content={"approve": True})
            async with Client(server, elicitation_callback=never, mode="auto") as client:
                first = await client.session.call_tool("change", {"item": "x"}, allow_input_required=True)
                assert isinstance(first, mcp_types.InputRequiredResult)
        asyncio.run(go())
        answers = [e.get("answer") for e in registry.audit.entries(10)]
        assert "client_disconnected" in answers


# --- the long-poll's decision off the event loop ------------------------------------------------------------------

def test_the_long_poll_decides_in_a_worker_thread(registry, monkeypatch):
    waited = []

    async def wait(inp, seconds, report):
        waited.append(seconds)
    registry.register(ToolSpec("watch", "Watch", "", ToolTier.READ, ItemIn, Out, lambda i: Out(), long_poll=wait))
    threads = []
    real = registry.decide

    def decide(*args, **kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)
    monkeypatch.setattr(registry, "decide", decide)
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry))

    async def go():
        async with Client(server) as client:
            return await client.call_tool("watch", {"wait_seconds": 5})
    result = asyncio.run(go())
    assert not result.is_error and waited == [5]
    assert threads and not any(threads), "the event loop (main thread) never reads the policy file"


# --- the audit view -----------------------------------------------------------------------------------------------

def invoke_audit(registry, *args):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self
    return CliRunner().invoke(cli_module.cli, ["-q", "tools", "audit", *args], obj=Ctx(), terminal_width=400,
                              env={"COLUMNS": "400"})


class TestAuditView:

    def test_new_fields_and_speaking_names(self, registry):
        registry.audit.record("mcp", "change", "local_write", "ask", "not_confirmed", arguments={"item": "x"},
                              approved=False, extra={"client": "Claude Code 2.1.295 (stdio)",
                                                     "confirm_channel": "elicitation", "answer": "declined",
                                                     "reason_code": "declined", "name_requested": "ChangeItem",
                                                     "repairs": [{"kind": "name", "field": "", "wrote": "ChangeItem"}],
                                                     "duration_ms": 12})
        registry.audit.record("mcp:laptop", "change", "local_write", "deny", "denied",
                              extra={"reason_code": "permission_denied"})
        result = invoke_audit(registry)
        assert result.exit_code == 0, result.output
        text = " ".join(result.output.split())
        for shown in ("all local stdio clients", "Claude Code 2.1.295 (stdio) (self-reported)",
                      "Change an item (change)", "the AI wrote ChangeItem", "elicitation: declined",
                      "the MCP token 'laptop'", "permission_denied"):
            assert shown in text, shown
        detailed = " ".join(invoke_audit(registry, "--details").output.split())
        assert "name:-" in detailed and "12" in detailed


# --- a next step on every refusal whose reason is 'failed' --------------------------------------------------------

class TestFailedRefusalsHaveANextStep:

    @pytest.mark.parametrize("raised", [ValueError("the service said no"), RuntimeError("disk gone"),
                                        ToolError("plain failure")])
    def test_handler_failures(self, registry, raised):
        def boom(_inp):
            raise raised
        registry.register(ToolSpec("boom", "Boom", "", ToolTier.READ, ItemIn, Out, boom))
        with pytest.raises(ToolError) as exc:
            registry.call("boom", {}, ToolContext("ditto"))
        assert exc.value.code == "failed" and exc.value.next == FAILED_NEXT

    def test_an_internal_error_and_a_cancelled_call(self, registry):
        registry.register(ToolSpec("bad", "Bad", "", ToolTier.READ, ItemIn, Strict, lambda i: {}))
        with pytest.raises(ToolError) as exc:
            registry.call("bad", {}, ToolContext("ditto"))
        assert exc.value.code == "failed" and exc.value.next == FAILED_NEXT
        registry.policy_store.replace(ToolPolicy(configured=True, overrides={"change": Decision.ALLOW},
                                              reviewed=["change"]))
        ctx = ToolContext("ditto")
        ctx.cancel.set()
        with pytest.raises(ToolError) as exc:
            registry.call("change", {}, ctx)
        assert exc.value.code == "failed" and "Call it again only if" in exc.value.next

    def test_framework_tools(self):
        environment = EvalEnvironment(REPO_ROOT / "targets")
        try:
            environment.registry.policy_store.replace(ToolPolicy(configured=True, overrides={
                "cancel_build": Decision.ALLOW}, reviewed=[s.name for s in environment.registry.list()]))
            with pytest.raises(ToolError) as exc:  # req-7 has ended
                environment.registry.call("cancel_build", {"request_id": "req-7"}, ToolContext("ditto"))
            assert exc.value.code == "failed" and "get_build_status" in exc.value.next
        finally:
            environment.close()

    def test_build_service_refusals_name_a_code_and_a_next_step(self, tmp_path):
        from orchestrator.Core.build_service import BuildService, BuildServiceError
        with pytest.raises(BuildServiceError) as exc:
            BuildService._check_model_source(str(tmp_path / "missing" / "x"))
        assert exc.value.code == "invalid_value" and "<label>:<relative path>" in exc.value.next
        (tmp_path / "notes.txt").write_text("x")
        with pytest.raises(BuildServiceError) as exc:
            BuildService._check_model_source(str(tmp_path / "notes.txt"))
        assert exc.value.code == "invalid_value" and exc.value.next
        with pytest.raises(BuildServiceError) as exc:
            BuildService(SimpleNamespace(orchestrator=None))._orchestrator
        assert exc.value.code == "failed" and "run_mojo_oracle" in exc.value.next and "llmcf mojo" in exc.value.next
        assert Path(tmp_path).exists()
