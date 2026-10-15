"""Pending approvals (MCP design S9, section 6.9).

When a client cannot ask the person (no form elicitation), the question is longer than an elicitation may be, or the
person wants the tool confirmed in the framework, the call becomes a pending approval in the framework (GUI notice
and window, `llm-cli tools approvals`) instead of a refusal. The person approves or declines there, and the framework
runs the call itself under the original caller; the client reads the result with get_job_status. An approval is bound
to the caller, the tool, the argument hash and the question hash, runs once, expires after 15 minutes and ends with
the session that asked.
"""

import asyncio
import json
import re
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

pytest.importorskip("mcp")
from click.testing import CliRunner  # noqa: E402

# cli first: importing pydantic's BaseModel before the framework's CLI trips PySide's import hook in this environment
from orchestrator import cli as cli_module  # noqa: E402
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools import approvals as approvals_module  # noqa: E402
from orchestrator.tools.approvals import MAX_PENDING_PER_CALLER, ApprovalStoreError  # noqa: E402
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError, ToolSpec  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

JOB_RE = re.compile(r"job_[a-z0-9]{16}")


class NoteIn(BaseModel):
    text: str = Field(max_length=40_000)


class NoteOut(ToolOutput):
    written: str = ""


@pytest.fixture
def env(make_framework):
    fm = make_framework()
    registry = fm.tool_registry
    registry.approvals.poll = 0.05
    writes = []
    registry.register(ToolSpec("write_note", "Write a note", "Writes a note", ToolTier.LOCAL_WRITE, NoteIn, NoteOut,
                               lambda i: writes.append(i.text) or NoteOut(written=i.text[:20])))
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return SimpleNamespace(fm=fm, registry=registry, store=registry.approvals.store, writes=writes,
                           server=mcp_server.build_mcp_server(fm))


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


def job_of(result) -> str:
    found = JOB_RE.search(text_of(result))
    assert found, text_of(result)
    return found.group(0)


def accept(asked):
    async def callback(ctx, params):
        asked.append(params.message)
        return ElicitResult(action="accept", content={"approve": True})
    return callback


async def wait_for_job(client, job_id, states=("done", "failed", "expired", "cancelled"), timeout=15.0):
    deadline = time.monotonic() + timeout
    while True:
        result = await client.call_tool("get_job_status", {"job_id": job_id, "wait_seconds": 1})
        assert not result.is_error, text_of(result)
        if result.structured_content["state"] in states or time.monotonic() > deadline:
            return result.structured_content


def cli(registry, *args, input=None, terminal=True):
    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self
    with mock.patch.object(cli_module, "_interactive", lambda: terminal):
        return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), input=input)


def flat(text):
    return " ".join(text.split())


def entries(registry, outcome=None):
    rows = registry.audit.entries(100)
    return [e for e in rows if outcome is None or e["outcome"] == outcome]


def run(coro):
    return asyncio.run(coro)


def pending_call(env, text="hello", callback=None):
    """One call over MCP from a client that cannot ask (no elicitation), and the pending record."""
    async def go():
        async with Client(env.server, elicitation_callback=callback) as client:
            return await client.call_tool("write_note", {"text": text})
    return run(go())


class TestPendingApproval:

    def test_a_client_that_cannot_ask_gets_a_pending_approval_with_a_job(self, env):
        async def go():
            async with Client(env.server) as client:
                result = await client.call_tool("write_note", {"text": "hello"})
                return result, env.store.pending()
        result, pending = run(go())
        assert result.is_error and "Waiting for the person to approve 'Write a note'" in text_of(result)
        assert "Do not call this tool again" in text_of(result) and "get_job_status" in text_of(result)
        (record,) = pending
        assert (record.caller, record.tool, record.state, record.job_id) == ("mcp", "write_note", "pending",
                                                                             job_of(result))
        assert env.writes == []
        requested = entries(env.registry, "confirmation_requested")[0]
        assert requested["confirm_channel"] == f"approval:{record.id}"
        assert requested["question_sha256"] == record.question_sha256

    def test_the_question_shows_the_caller_class_first_and_the_client_as_self_reported(self, env):
        async def go():
            async with Client(env.server) as client:
                await client.call_tool("write_note", {"text": "hello"})
                return env.store.pending()[0]
        record = run(go())
        lines = record.question.splitlines()
        assert lines[0] == "Caller: all local stdio clients (they share one set of permissions)"
        assert lines[1].startswith("Client: ") and lines[1].endswith("(self-reported)")
        assert "text: hello" in record.question

    def test_the_same_call_while_waiting_returns_the_same_job(self, env):
        async def go():
            async with Client(env.server) as client:
                first = await client.call_tool("write_note", {"text": "hello"})
                second = await client.call_tool("write_note", {"text": "hello"})
                return first, second, env.store.pending()
        first, second, pending = run(go())
        assert job_of(first) == job_of(second) and len(pending) == 1
        assert "already failed" not in text_of(second), "a pending approval is not a failure"

    def test_approved_in_the_cli_the_framework_runs_the_call_once(self, env):
        async def go():
            async with Client(env.server) as client:
                result = await client.call_tool("write_note", {"text": "hello"})
                record = env.store.pending()[0]
                decided = await asyncio.to_thread(cli, env.registry, "tools", "approvals", "approve", record.id,
                                                  input="y\n")
                assert decided.exit_code == 0, decided.output
                return record, await wait_for_job(client, job_of(result))
        record, job = run(go())
        assert job["state"] == "done" and job["result"]["written"] == "hello" and job["yours"]
        assert env.writes == ["hello"], "the framework ran the call exactly once"
        decision = entries(env.registry, "approval_decided")[0]
        assert (decision["actor"], decision["approved"], decision["answer"]) == ("cli", 1, "accepted")
        ran = [e for e in entries(env.registry) if e["tool"] == "write_note" and e["outcome"] in ("started", "ok")]
        assert {e["actor"] for e in ran} == {"mcp"}, "it runs under the original caller"
        assert {e["confirm_channel"] for e in ran} == {f"approval:{record.id}"}
        assert env.store.get(record.id).state == "done"

    def test_declined_in_the_cli_nothing_runs(self, env):
        async def go():
            async with Client(env.server) as client:
                result = await client.call_tool("write_note", {"text": "hello"})
                record = env.store.pending()[0]
                await asyncio.to_thread(cli, env.registry, "tools", "approvals", "decline", record.id, input="y\n")
                return await wait_for_job(client, job_of(result))
        job = run(go())
        assert job["state"] == "failed" and job["error"]["code"] == "declined"
        assert env.writes == []
        refused = [e for e in entries(env.registry, "not_confirmed") if e["tool"] == "write_note"][0]
        assert refused["answer"] == "declined"

    def test_a_changed_question_is_shown_again_instead_of_running(self, env, monkeypatch):
        service = env.registry.approvals
        real_question = service.question
        changed = {"now": False}

        def question(spec, inp, ctx, facts=None):
            text = real_question(spec, inp, ctx, facts)
            return text + "\n  upstream commit: 2b" if changed["now"] else text
        monkeypatch.setattr(service, "question", question)

        async def go():
            async with Client(env.server) as client:
                result = await client.call_tool("write_note", {"text": "hello"})
                first = env.store.pending()[0]
                changed["now"] = True  # e.g. a new upstream commit since the question was asked
                await asyncio.to_thread(cli, env.registry, "tools", "approvals", "approve", first.id, input="y\n")
                deadline = time.monotonic() + 10
                while env.store.get(first.id).state != "pending" or \
                        env.store.get(first.id).question_sha256 == first.question_sha256:
                    assert time.monotonic() < deadline, env.store.get(first.id)
                    await asyncio.sleep(0.05)
                asked_again = env.store.get(first.id)
                assert env.writes == [], "nothing runs on an answer to the old question"
                await asyncio.to_thread(cli, env.registry, "tools", "approvals", "approve", first.id, input="y\n")
                return asked_again, await wait_for_job(client, job_of(result))
        asked_again, job = run(go())
        assert "upstream commit: 2b" in asked_again.question and "asked again" in asked_again.note
        assert job["state"] == "done" and env.writes == ["hello"]

    def test_another_callers_identical_call_does_not_use_the_approval(self, env):
        registry = env.registry
        a = ToolContext("mcp", connection="a", pending_approvals=True)
        b = ToolContext("mcp", connection="b", pending_approvals=True)
        jobs = []
        for ctx in (a, b):
            with pytest.raises(ToolError) as info:
                registry.call("write_note", {"text": "same"}, ctx)
            assert info.value.code == "waiting_for_approval"
            jobs.append(JOB_RE.search(str(info.value)).group(0))
        assert jobs[0] != jobs[1] and len(env.store.pending()) == 2
        first = next(r for r in env.store.pending() if r.job_id == jobs[0])
        env.store.decide(first.id, True, "cli")
        registry.approvals.poll_once()
        deadline = time.monotonic() + 10
        while env.fm.job_service.get(jobs[0]).state != "done":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert env.writes == ["same"]
        assert env.fm.job_service.get(jobs[1]).state == "waiting_for_approval"

    def test_expiry_withdraws_it_and_an_expired_one_cannot_be_approved(self, env):
        registry = env.registry
        ctx = ToolContext("mcp", connection="x", pending_approvals=True)
        with pytest.raises(ToolError) as info:
            registry.call("write_note", {"text": "late"}, ctx)
        job_id = JOB_RE.search(str(info.value)).group(0)
        record = env.store.pending()[0]
        # Its time is up before the holder looked: the person can no longer approve it.
        env.store.update(record.id, expires=approvals_module._iso(approvals_module._now() - timedelta(seconds=1)))
        refused = cli(registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert refused.exit_code == 1 and "expired" in flat(refused.output)
        registry.approvals.poll_once(now=approvals_module._now() + timedelta(minutes=16))
        assert env.fm.job_service.get(job_id).state == "expired" and env.store.get(record.id).state == "expired"
        unanswered = [e for e in entries(registry, "not_confirmed") if e["tool"] == "write_note"][0]
        assert unanswered["answer"] == "unanswered" and env.writes == []

    def test_the_end_of_the_session_withdraws_it(self, env):
        result = pending_call(env)  # the client's session ends here
        record = env.store.get(next(r.id for r in env.store.list()))
        assert record.state == "withdrawn" and record.job_id == job_of(result)
        assert env.fm.job_service.get(record.job_id).state == "cancelled"
        cancelled = [e for e in entries(env.registry, "cancelled") if e["tool"] == "write_note"]
        assert cancelled and cancelled[0]["confirm_channel"] == f"approval:{record.id}"
        assert cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n").exit_code == 1
        assert env.writes == []

    def test_cancel_job_withdraws_it(self, env):
        async def go():
            async with Client(env.server) as client:
                result = await client.call_tool("write_note", {"text": "hello"})
                cancelled = await client.call_tool("cancel_job", {"job_id": job_of(result)})
                return cancelled, env.store.list()[0]
        cancelled, record = run(go())
        assert not cancelled.is_error and cancelled.structured_content["state"] == "cancelled"
        assert record.state == "withdrawn" and env.writes == []

    def test_a_client_has_at_most_five_open_approvals(self, env):
        ctx = ToolContext("mcp", connection="busy", pending_approvals=True)
        for i in range(MAX_PENDING_PER_CALLER):
            with pytest.raises(ToolError, match="Waiting for the person"):
                env.registry.call("write_note", {"text": f"n{i}"}, ctx)
        with pytest.raises(ToolError) as info:
            env.registry.call("write_note", {"text": "one too many"}, ctx)
        assert info.value.code == "limit_reached" and len(env.store.pending()) == MAX_PENDING_PER_CALLER

    def test_a_hand_edited_approval_is_never_run(self, env):
        ctx = ToolContext("mcp", connection="x", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "forged"}, ctx)
        data = json.loads(env.store.path.read_text())
        data["approvals"][0]["state"] = "approved"  # an AI with write access to the per-user folder
        env.store.path.write_text(json.dumps(data))
        env.registry.approvals.poll_once()
        time.sleep(0.2)
        assert env.writes == [] and env.store.pending() == [] and "cannot be used" in env.store.problem
        listed = cli(env.registry, "tools", "approvals")
        assert "cannot be used" in flat(listed.output)

    def test_no_tool_can_create_read_or_decide_an_approval(self, env):
        for spec in env.registry.list():
            assert "approv" not in spec.name, spec.name
        # The file lies in the per-user folder, which no local-file reference reaches.
        from orchestrator.tools.local_files import LocalFileError, LocalFiles
        state = env.store.path.parent
        files = LocalFiles(state, {"models": state})
        with pytest.raises(LocalFileError):
            files.resolve(f"models:{env.store.path.name}")
        # Another caller reading the job of an approved call never sees its result.
        a = ToolContext("mcp", connection="a", pending_approvals=True)
        with pytest.raises(ToolError) as info:
            env.registry.call("write_note", {"text": "mine"}, a)
        job_id = JOB_RE.search(str(info.value)).group(0)
        env.store.decide(env.store.pending()[0].id, True, "gui")
        env.registry.approvals.poll_once()
        deadline = time.monotonic() + 10
        while env.fm.job_service.get(job_id).state != "done":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        own = env.registry.call("get_job_status", {"job_id": job_id}, a)
        other = env.registry.call("get_job_status", {"job_id": job_id}, ToolContext("mcp", connection="b"))
        assert own.result["written"] == "mine" and other.result is None

    def test_a_call_withdrawn_while_the_approval_is_being_checked_never_runs(self, env, monkeypatch):
        """The person approves; the client cancels its job (or its session ends) in the moment the framework checks
        the approved call again: nothing may run then."""
        ctx = ToolContext("mcp", connection="x", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "withdrawn"}, ctx)
        record = env.store.pending()[0]
        env.store.decide(record.id, True, "gui")
        service = env.registry.approvals
        real_validate = env.registry.validate_input

        def validate_while_the_client_cancels(spec, arguments):
            env.fm.job_service.cancel(record.job_id)  # cancel_job arrives right now
            return real_validate(spec, arguments)
        monkeypatch.setattr(env.registry, "validate_input", validate_while_the_client_cancels)
        service.poll_once()
        time.sleep(0.3)
        assert env.writes == [] and env.store.get(record.id).state == "withdrawn"
        assert env.fm.job_service.get(record.job_id).state == "cancelled"

    def test_the_frameworks_shutdown_ends_its_pending_approvals_and_their_watch(self, env):
        ctx = ToolContext("mcp", connection="x", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "left"}, ctx)
        service = env.registry.approvals
        record = env.store.pending()[0]
        assert service._thread is not None and service._thread.is_alive()
        env.fm.shutdown()
        assert env.store.get(record.id).state == "withdrawn" and service.waiting_ids() == []
        deadline = time.monotonic() + 5
        while service._thread is not None and service._thread.is_alive():
            assert time.monotonic() < deadline, "the watch keeps polling after the shutdown"
            time.sleep(0.05)
        ended = [e for e in entries(env.registry, "cancelled") if e["tool"] == "write_note"]
        assert ended and ended[0]["confirm_channel"] == f"approval:{record.id}"
        assert cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n").exit_code == 1
        assert env.writes == []

    def test_only_the_frameworks_windows_decide(self, env):
        ctx = ToolContext("mcp", connection="x", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "x"}, ctx)
        record = env.store.pending()[0]
        with pytest.raises(ApprovalStoreError):
            env.store.decide(record.id, True, "mcp")


class TestCli:

    def make_pending(self, env):
        ctx = ToolContext("mcp", connection="x", client="Some Client 1.0 (stdio)", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "hello"}, ctx)
        return env.store.pending()[0]

    @pytest.mark.parametrize("args", [(), ("list",), ("show", "ID"), ("approve", "ID"), ("decline", "ID")])
    def test_every_command_needs_a_terminal(self, env, args):
        record = self.make_pending(env)
        args = tuple(record.id if a == "ID" else a for a in args)
        result = cli(env.registry, "tools", "approvals", *args, input="y\n", terminal=False)
        assert result.exit_code == 2 and "needs a terminal" in flat(result.output)
        assert env.store.get(record.id).state == "pending"

    def test_list_and_show_name_the_caller_class_and_the_self_reported_client(self, env):
        record = self.make_pending(env)
        listed = flat(cli(env.registry, "tools", "approvals").output)
        assert record.id in listed and "all local stdio clients" in listed and "Some Client 1.0" in listed
        shown = cli(env.registry, "tools", "approvals", "show", record.id).output
        assert shown.index("Caller: all local stdio clients") < shown.index("Client: Some Client 1.0")
        assert "(self-reported)" in shown and "text: hello" in shown

    def test_nothing_is_decided_without_a_yes(self, env):
        record = self.make_pending(env)
        assert cli(env.registry, "tools", "approvals", "approve", record.id, input="n\n").exit_code == 1
        assert env.store.get(record.id).state == "pending"

    def test_nothing_is_decided_when_the_audit_cannot_record_it(self, env, monkeypatch):
        record = self.make_pending(env)

        def broken(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(env.registry.audit, "record", broken)
        result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert result.exit_code == 1 and "nothing was decided" in flat(result.output)
        monkeypatch.undo()
        assert env.store.get(record.id).state == "pending"

    def test_confirm_in_framework_is_set_by_a_person_and_audited(self, env):
        assert cli(env.registry, "tools", "permissions", "--confirm-in-framework", "write_note",
                   terminal=False).exit_code == 2
        result = cli(env.registry, "tools", "permissions", "--caller", "mcp", "--confirm-in-framework", "write_note")
        assert result.exit_code == 0, result.output
        assert env.registry.policy().callers["mcp"].framework_only == ["write_note"]
        assert "write_note asked in the framework only" in entries(env.registry, "policy_changed")[0]["detail"]
        assert cli(env.registry, "tools", "permissions", "--confirm-in-framework", "nope").exit_code == 2


class TestChannels:

    def test_a_9000_character_question_from_a_client_with_elicitation_becomes_a_pending_approval(self, env):
        asked = []
        result = pending_call(env, text="x" * 9000, callback=accept(asked))
        assert result.is_error and "Waiting for the person to approve" in text_of(result)
        assert asked == [], "too long for an elicitation: the client was not asked"
        record = next(iter(env.store.list()))
        assert len(record.question) > 9000 and env.writes == []

    def test_a_question_over_16000_characters_is_refused_everywhere(self, env):
        asked = []
        result = pending_call(env, text="x" * 17000, callback=accept(asked))
        assert result.is_error and "too long to show" in text_of(result)
        assert asked == [] and env.store.list() == [] and env.writes == []

    def test_a_tool_set_to_be_confirmed_in_the_framework_is_never_asked_through_the_client(self, env):
        env.registry.policy_store.update(lambda p: p.model_copy(update={"framework_only": ["write_note"]}))
        asked = []
        result = pending_call(env, callback=accept(asked))
        assert result.is_error and "Waiting for the person to approve" in text_of(result)
        assert asked == [] and env.writes == []

    def test_a_client_that_can_ask_is_still_asked(self, env):
        asked = []
        result = pending_call(env, callback=accept(asked))
        assert not result.is_error and len(asked) == 1 and env.writes == ["hello"]
        assert env.store.list() == []

    def test_ditto_in_process_never_gets_a_pending_approval(self, env):
        with pytest.raises(ToolError) as info:
            env.registry.call("write_note", {"text": "x"}, ToolContext("ditto"))
        assert info.value.code == "needs_person" and env.store.list() == []


class TestGui:

    def test_the_window_approves_and_the_decision_is_audited(self, env, qapp):
        from orchestrator.gui.approvals_window import ApprovalNotifier, ApprovalsDialog
        ctx = ToolContext("mcp", connection="x", client="Some Client", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "hello"}, ctx)
        notifier = ApprovalNotifier(env.store)
        assert notifier.check() == 1 and notifier.new_ids
        dialog = ApprovalsDialog(env.registry)
        text = dialog.text.toPlainText()
        assert text.startswith("Caller: all local stdio clients") and "(self-reported)" in text
        assert dialog.decide(True)
        record = env.store.list()[0]
        assert record.state == "approved" and record.decided_by == "gui"
        assert entries(env.registry, "approval_decided")[0]["actor"] == "gui"
        assert notifier.check() == 0

    def test_the_window_never_swaps_the_call_you_read_for_another(self, env, qapp):
        """You read the second call; the client withdraws it while you read. The window must not select the first
        call in its place, where your click on "Approve once" would approve a call you never read."""
        from orchestrator.gui.approvals_window import ApprovalsDialog
        for text in ("first call", "second call"):
            with pytest.raises(ToolError):
                env.registry.call("write_note", {"text": text}, ToolContext("mcp", connection="x",
                                                                            pending_approvals=True))
        dialog = ApprovalsDialog(env.registry)
        dialog.list.setCurrentRow(1)
        read = dialog.selected()
        assert "second call" in dialog.text.toPlainText()
        env.registry.approvals.withdraw(read.id)
        dialog.refresh()
        assert dialog.selected() is None and not dialog.approve_button.isEnabled()
        assert "no longer waiting" in dialog.text.toPlainText()
        assert not dialog.decide(True)
        assert [r.state for r in env.store.list()] == ["pending", "withdrawn"]

    def test_a_question_that_changes_while_you_read_it_is_not_approved_by_the_next_click(self, env, qapp):
        from orchestrator.gui.approvals_window import ApprovalsDialog
        from orchestrator.tools.approvals import question_sha256
        with pytest.raises(ToolError):
            env.registry.call("write_note", {"text": "hello"}, ToolContext("mcp", connection="x",
                                                                           pending_approvals=True))
        dialog = ApprovalsDialog(env.registry)
        record = dialog.selected()
        changed = record.question + "\n  upstream commit: 2b"
        env.store.update(record.id, question=changed, question_sha256=question_sha256(changed))  # asked again
        dialog.refresh()
        assert "upstream commit: 2b" in dialog.text.toPlainText() and "changed" in dialog.text.toPlainText()
        assert not dialog.approve_button.isEnabled() and not dialog.decide(True)
        assert env.store.get(record.id).state == "pending"
        dialog._arm()  # after the pause to read it again
        assert dialog.approve_button.isEnabled() and dialog.decide(True)
        assert env.store.get(record.id).question_sha256 == question_sha256(changed)

    def test_the_permissions_dialog_sets_tools_confirmed_in_the_framework(self, env, qapp):
        from orchestrator.gui.dialogs import ToolPermissionsDialog
        dialog = ToolPermissionsDialog(env.registry)
        dialog.framework_checks["write_note"].setChecked(True)
        dialog.save()
        assert env.registry.policy().framework_only == ["write_note"]


def test_ditto_sessions_never_share_an_approval(env):
    """Ditto has one owner for all its tasks: two tasks asking for the same call wait for two approvals, so one task
    never receives the other's (SEC-L2). An MCP client's owner names its connection already."""
    from orchestrator.tools.policy import Decision
    service, spec = env.registry.approvals, env.registry.get("write_note")
    inp = spec.input_model(text="hello")
    args = inp.model_dump(mode="json")

    def request(ctx):
        return service.request(spec, inp, args, ctx, Decision.ASK).job_id
    first = request(ToolContext("ditto", session="task-1"))
    assert request(ToolContext("ditto", session="task-1")) == first, "the same task waits for its one approval"
    assert request(ToolContext("ditto", session="task-2")) != first, "another task never gets this one"
    client = request(ToolContext("mcp", session="a", connection="c1"))
    assert request(ToolContext("mcp", session="b", connection="c1")) == client
    assert request(ToolContext("mcp", session="a", connection="c2")) != client
    assert len(env.store.pending()) == 4


def _reserve_note(env, state, ran):
    """A tool whose question shows the free disk space (a volatile fact) and, when the disk got tight, a warning."""
    from orchestrator.tools import facts_catalog as catalog
    from orchestrator.tools.spec import CallFacts, Fact

    def prepare(inp, ctx):
        warnings = ((catalog.warn("disk_tight", free_after_gb=state["free"] / 10, reserve_gb=2.0),)
                    if state["tight"] else ())
        return CallFacts(consequence=f"Writes the note {inp.text}; {state['free']} GB are free now.",
                         consequence_template=f"Writes the note {inp.text}; {{free}} are free now.",
                         facts=(Fact("Note", inp.text), Fact("Disk", f"{state['free']} GB free", volatile=True)),
                         bound={"text": inp.text}, warnings=warnings)

    def handler(inp, ctx):
        ran.append(ctx.facts.facts[1].value)
        return NoteOut(written=inp.text)
    env.registry.register(ToolSpec("reserve_note", "Reserve a note", "Reserves a note", ToolTier.LOCAL_WRITE, NoteIn,
                                   NoteOut, handler, takes_context=True, prepare=prepare))
    env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in env.registry.list()]))
    return mcp_server.build_mcp_server(env.fm)


def _approve_after(env, server, change):
    """A client that cannot ask calls reserve_note; `change` happens while the person reads; they approve in the
    CLI. Returns the record as it was first shown and as it is afterwards (seen while the session still lives)."""
    async def go():
        async with Client(server) as client:
            await client.call_tool("reserve_note", {"text": "hello"})
            record = env.store.pending()[0]
            change()
            decided = await asyncio.to_thread(cli, env.registry, "tools", "approvals", "approve", record.id,
                                              input="y\n")
            assert decided.exit_code == 0, decided.output
            deadline = time.monotonic() + 10
            while env.store.get(record.id).state in ("pending", "approved", "running") and \
                    env.store.get(record.id).question_sha256 == record.question_sha256 and \
                    time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            return record, env.store.get(record.id)
    return run(go())


def test_free_space_change_does_not_ask_again(env):
    """F3 ([A5], delegation design 4.4): the question of a pending approval shows the free disk space; the space moved
    while the person read it, so the question text changed and the person was asked again, every time. The decision
    is bound to the facts' digest: free space alone (a volatile fact) never makes the question new, and the call runs
    once with the facts of now."""
    state, ran = {"free": 500, "tight": False}, []
    server = _reserve_note(env, state, ran)
    record, after = _approve_after(env, server, lambda: state.update(free=480))
    assert after.state == "done", after
    assert ran == ["480 GB free"], "it ran once, bound to the current facts, without asking again"
    assert env.store.get(record.id).question_sha256 == record.question_sha256


def test_a_new_warning_asks_again(env):
    """A change the decision depends on (the disk is now tight: a new warning) puts the new question to the person;
    nothing runs on the answer to the old one."""
    state, ran = {"free": 500, "tight": False}, []
    server = _reserve_note(env, state, ran)
    record, asked_again = _approve_after(env, server, lambda: state.update(free=30, tight=True))
    assert asked_again.state == "pending" and asked_again.question_sha256 != record.question_sha256
    assert "tight" in asked_again.question and ran == []
