"""The two-phase confirmation (S5) feeds the pending approvals (S9) and the standing approvals (S9a), and the limits on
questions (S8) cover every way of asking the person (MCP design 6.5, 6.6 rule 8, 6.9, 6.10).

- A pending approval shows the facts the tool looked up and the speaking names of its ids; the framework runs the
  call bound to exactly the facts of the question the person approved; a fact that changed before the approval is
  asked again instead of running.
- A pending approval is a question: a recent "no" (also one given in the framework's window), a paused tool and the
  questions per hour apply to it; the limit of open approvals is a limit, audited and never a failed call.
- A request to stop asking counts as a question too.
- A standing approval is matched on the looked-up facts over MCP as well, so a client that can ask is not asked.
- The quotas are checked again when the approved call runs.
"""

import asyncio
import dataclasses
import re
import threading
import time
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
from orchestrator.tools.approvals import MAX_PENDING_PER_CALLER  # noqa: E402
from orchestrator.tools.grants import tool_rule  # noqa: E402
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import LimitHit, ToolContext, ToolError, ToolSpec  # noqa: E402
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, GrantRule, ToolLimits  # noqa: E402
from pydantic import BaseModel  # noqa: E402

JOB_RE = re.compile(r"job_[a-z0-9]{16}")


class ItemIn(BaseModel):
    item_id: str


class ItemOut(ToolOutput):
    value: str = ""


@pytest.fixture
def env(make_framework):
    fm = make_framework()
    registry = fm.tool_registry
    registry.approvals.poll = 0.05
    # together: a barrier the next look-ups wait at; gate: an event the handler waits for once it has run
    # hold: called once by the next look-up (and then cleared), to stop that look between reading and deciding
    state = SimpleNamespace(commit="c1", looked_up=0, seen=[], running=0, together=None, gate=None, hold=None)

    def prepare(inp, ctx):
        state.looked_up += 1
        commit = state.commit  # what this look-up finds, also when it is held before it returns
        hold, state.hold = state.hold, None
        if hold is not None:
            hold()
        together = state.together
        if together is not None:
            together.wait(10)
        return CallFacts(consequence=f"Changes item {inp.item_id} to upstream commit {commit}.",
                         facts=(Fact("Upstream commit", commit),), bound={"commit": commit})

    def handler(inp, ctx):
        state.seen.append(ctx.facts.bound["commit"] if ctx.facts is not None else None)
        if state.gate is not None:
            state.gate.wait(10)
        return ItemOut(value=state.seen[-1] or "")

    registry.register(ToolSpec(
        "change_item", "Change an item", "Changes an item to an upstream commit.", ToolTier.LOCAL_WRITE, ItemIn,
        ItemOut, handler, takes_context=True, prepare=prepare,
        fields={"item_id": FieldMeta(kind="id", describe=lambda v: f"the item called {v}-name")},
        limits=ToolLimits(concurrent=1, running=lambda ctx: state.running, what="changes"),
        grant=GrantRule(rule=tool_rule("change_item"), warning="change items",
                        # the scope comes from the facts the framework looked up, never from the AI's words
                        facts=lambda inp, ctx: {"commit": ctx.facts.bound["commit"]} if ctx.facts else None)))
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return SimpleNamespace(fm=fm, registry=registry, store=registry.approvals.store, state=state,
                           jobs=registry.approvals.jobs, server=mcp_server.build_mcp_server(fm))


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


def cli(registry, *args, input=None):
    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self
    with mock.patch.object(cli_module, "_interactive", lambda: True):
        return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), input=input)


def ask_pending(env, item="a1", caller="mcp"):
    """A call from a client that cannot ask: (the job id, the pending record)."""
    with pytest.raises(ToolError) as exc:
        env.registry.call("change_item", {"item_id": item},
                          ToolContext(caller, connection="c", session="s", pending_approvals=True))
    assert exc.value.code == "waiting_for_approval", str(exc.value)
    job_id = JOB_RE.search(str(exc.value)).group(0)
    record = next(r for r in env.store.list() if r.job_id == job_id)
    return job_id, record


def decide(env, record, answer="approve"):
    result = cli(env.registry, "tools", "approvals", answer, record.id, input="y\n")
    assert result.exit_code == 0, result.output
    env.registry.approvals.poll_once()


def wait_job(env, job_id, states=("done", "failed", "expired", "cancelled"), timeout=10.0):
    deadline = time.monotonic() + timeout
    while env.jobs.get(job_id).state not in states:
        assert time.monotonic() < deadline, env.jobs.get(job_id)
        time.sleep(0.02)
    return env.jobs.get(job_id)


def outcomes(env):
    return [e["outcome"] for e in reversed(env.registry.audit.entries(200))]


class TestThePendingQuestionIsTheTwoPhaseQuestion:

    def test_it_shows_the_looked_up_facts_and_speaking_names_and_the_handler_uses_them(self, env):
        job_id, record = ask_pending(env)
        assert "Changes item a1 to upstream commit c1." in record.question, "the consequence, from server facts"
        assert "Upstream commit: c1" in record.question
        assert "item_id: a1 (that is: the item called a1-name)" in record.question, "speaking names for the person"
        assert record.question.startswith("Caller: all local stdio clients")
        decide(env, record)
        job = wait_job(env, job_id)
        assert job.state == "done" and job.result["value"] == "c1"
        assert env.state.seen == ["c1"], "the handler got exactly the facts of the approved question"

    def test_a_fact_that_changed_before_the_approval_is_asked_again(self, env):
        job_id, record = ask_pending(env)
        env.state.commit = "c2"  # a new upstream commit while the question waited
        decide(env, record)
        again = env.store.get(record.id)
        assert again.state == "pending" and "Upstream commit: c2" in again.question
        assert env.state.seen == [], "nothing ran on the answer to the old question"
        decide(env, again)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c2"]

    def test_an_older_copy_with_an_earlier_yes_runs_nothing_the_person_did_not_approve(self, env):
        """Why the pending approvals keep no revision anchor (port follow-up e): an older signed copy of
        tool_approvals.json put back as the very file brings back the person's "yes" to a question that has changed
        since (here even with the facts changed back to the ones of that question). Nothing runs on it: a yes counts
        only for the question the holding process asks now, so the person is asked again; a standing approval is never
        read from this file (the deciding window saves it with the permissions)."""
        job_id, record = ask_pending(env)
        env.state.commit = "c2"  # a new upstream commit while the question waited
        result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert result.exit_code == 0, result.output
        older = env.store.path.read_bytes()  # the yes to the c1 question
        env.registry.approvals.poll_once()   # the question changed: asked again
        assert env.store.get(record.id).state == "pending" and env.state.seen == []
        env.state.commit = "c1"
        env.store.path.write_bytes(older)    # the earlier yes put back
        env.registry.approvals.poll_once()
        again = env.store.get(record.id)
        assert again.state == "pending" and "Upstream commit: c1" in again.question
        assert env.state.seen == [], "nothing ran on the yes that was put back"
        decide(env, again)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c1"]

    def test_a_look_that_read_the_put_back_yes_before_another_look_asked_again_runs_nothing(self, env):
        """The interleaving behind a rare failure of the test above: the watch thread reads the put-back file (the yes
        to the c1 question) and stops before it decides; meanwhile another look asks the c1 question again (pending).
        The first look then finds the question it would ask (c1) the same as the one in its old view and the one this
        process asks now: before, it started the call on the yes that was put back. A decision is taken on the record
        as it is now: the record is pending again, so nothing runs."""
        env.registry.approvals.poll = 60  # the watch thread stays asleep: the test makes both looks itself
        job_id, record = ask_pending(env)
        env.state.commit = "c2"
        result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert result.exit_code == 0, result.output
        older = env.store.path.read_bytes()  # the yes to the c1 question
        env.registry.approvals.poll_once()   # the question changed: asked again
        env.state.commit = "c1"
        env.store.path.write_bytes(older)    # the earlier yes put back
        stopped, go_on = threading.Event(), threading.Event()
        env.state.hold = lambda: (stopped.set(), go_on.wait(10))
        first = threading.Thread(target=env.registry.approvals.poll_once)
        first.start()                        # reads the put-back yes, then stops in its look-up
        assert stopped.wait(10)
        env.registry.approvals.poll_once()   # the other look: the c1 question is asked again
        assert env.store.get(record.id).state == "pending"
        go_on.set()
        first.join(10)
        assert not first.is_alive()
        again = env.store.get(record.id)
        assert again.state == "pending" and "Upstream commit: c1" in again.question, again.state
        assert env.jobs.get(job_id).state == "waiting_for_approval"
        assert env.state.seen == [], "nothing ran on the yes that was put back"
        decide(env, again)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c1"]

    def test_a_look_whose_view_a_put_back_copy_brings_back_asks_nothing_stale(self, env):
        """The other order: the watch thread reads the person's yes and looks up the facts (c2) while another look
        asks the c2 question again; then the facts go back to c1 and the older copy (that same yes) is put back. The
        file now looks exactly as the first look read it, yet it asks nothing with its old facts (and runs nothing):
        the holder asks another question since that look began. The next look asks the c1 question."""
        env.registry.approvals.poll = 60  # the watch thread stays asleep: the test makes both looks itself
        job_id, record = ask_pending(env)
        result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert result.exit_code == 0, result.output
        older = env.store.path.read_bytes()  # the yes to the c1 question
        env.state.commit = "c2"
        stopped, go_on = threading.Event(), threading.Event()
        env.state.hold = lambda: (stopped.set(), go_on.wait(10))
        first = threading.Thread(target=env.registry.approvals.poll_once)
        first.start()                        # reads the yes, finds c2, then stops
        assert stopped.wait(10)
        env.registry.approvals.poll_once()   # the other look: the c2 question is asked
        env.state.commit = "c1"
        env.store.path.write_bytes(older)    # the yes put back: the file is as the first look read it
        go_on.set()
        first.join(10)
        assert not first.is_alive()
        assert env.state.seen == [] and env.jobs.get(job_id).state == "waiting_for_approval"
        env.registry.approvals.poll_once()
        again = env.store.get(record.id)
        assert again.state == "pending" and "Upstream commit: c1" in again.question, again.question
        assert env.state.seen == []
        decide(env, again)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c1"]

    def test_a_look_that_read_the_file_while_the_question_was_asked_again_asks_nothing_stale(self, env):
        """A look reads the store while another look is asking the question again: the new question is not yet in
        the file. The holder takes the new asking as its own only once the file holds it, so this look decides on its
        old view and cannot mistake it for the current one, even when the older copy is put back after it."""
        env.registry.approvals.poll = 60  # the watch thread stays asleep: the test makes both looks itself
        job_id, record = ask_pending(env)
        env.state.commit = "c2"
        result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert result.exit_code == 0, result.output
        older = env.store.path.read_bytes()  # the yes to the c1 question
        writing, write_on = threading.Event(), threading.Event()
        write = env.store._write

        def held_write(records):  # the asking look stops right before its new question reaches the file
            writing.set()
            write_on.wait(10)
            write(records)
        env.store._write = held_write
        asking = threading.Thread(target=env.registry.approvals.poll_once)
        asking.start()                       # finds c2: asks again, held before the write
        assert writing.wait(10)
        env.store._write = write
        stopped, go_on = threading.Event(), threading.Event()
        env.state.hold = lambda: (stopped.set(), go_on.wait(10))
        first = threading.Thread(target=env.registry.approvals.poll_once)
        first.start()                        # reads the yes still in the file, finds c2, then stops
        assert stopped.wait(10)
        write_on.set()
        asking.join(10)
        assert env.store.get(record.id).state == "pending"
        env.state.commit = "c1"
        env.store.path.write_bytes(older)    # the yes put back: the file is as the first look read it
        go_on.set()
        first.join(10)
        assert not first.is_alive() and not asking.is_alive()
        assert env.state.seen == [] and env.jobs.get(job_id).state == "waiting_for_approval"
        env.registry.approvals.poll_once()
        again = env.store.get(record.id)
        assert again.state == "pending" and "Upstream commit: c1" in again.question, again.question
        decide(env, again)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c1"]

    def test_an_earlier_yes_to_the_same_text_put_back_after_it_was_asked_again_runs_nothing(self, env):
        """The question is asked again with the very text the person once approved (the facts went back to it): the
        older copy with that yes, put back now, still runs nothing, since that yes belongs to an earlier asking."""
        env.registry.approvals.poll = 60  # the watch thread stays asleep: the test makes every look itself
        job_id, record = ask_pending(env)

        def approve():
            result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
            assert result.exit_code == 0, result.output
        approve()
        older = env.store.path.read_bytes()  # the yes to the c1 question
        env.state.commit = "c2"
        env.registry.approvals.poll_once()   # the question changed: asked again (c2)
        approve()
        env.state.commit = "c1"
        env.registry.approvals.poll_once()   # changed back: the c1 text is asked again
        assert env.store.get(record.id).state == "pending" and env.state.seen == []
        env.store.path.write_bytes(older)    # the yes to the earlier asking of that same text put back
        env.registry.approvals.poll_once()
        again = env.store.get(record.id)
        assert again.state == "pending" and "Upstream commit: c1" in again.question, again.state
        assert env.state.seen == [] and env.jobs.get(job_id).state == "waiting_for_approval"
        decide(env, again)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c1"]

    def test_an_approved_call_runs_once_when_two_looks_see_the_yes_at_once(self, env):
        """Two looks at the store at the same time (the watch thread and another poll_once) both find the yes: the
        call runs once. Before, both started it while the first run was still going on."""
        env.registry.approvals.poll = 60  # the watch thread stays asleep: the test makes both looks itself
        job_id, record = ask_pending(env)
        result = cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n")
        assert result.exit_code == 0, result.output
        env.state.gate = threading.Event()  # the first run is still going on when the second look decides
        # both looks wait for each other in the look-up, after they saw the yes and before either starts the call
        env.state.together = threading.Barrier(2, action=lambda: setattr(env.state, "together", None))
        looks = [threading.Thread(target=env.registry.approvals.poll_once) for _ in range(2)]
        for look in looks:
            look.start()
        for look in looks:
            look.join(10)
        env.state.gate.set()
        assert wait_job(env, job_id).state == "done"
        assert env.state.seen == ["c1"], "the approved call ran once"

    def test_a_value_only_the_handler_sees_changes_nothing_the_person_approved(self, env):
        """The question reads the same: the handler gets the facts the person saw (it uses them or refuses)."""
        job_id, record = ask_pending(env)
        original = env.registry.get("change_item").prepare

        def prepare(inp, ctx):
            facts = original(inp, ctx)
            return dataclasses.replace(facts, bound={"commit": "moved-on"})
        spec = dataclasses.replace(env.registry.get("change_item"), prepare=prepare)
        env.registry._tools["change_item"] = spec
        decide(env, record)
        assert wait_job(env, job_id).state == "done" and env.state.seen == ["c1"]


class TestAPendingApprovalIsAQuestion:

    def test_a_no_in_the_framework_is_not_asked_again_soon(self, env):
        job_id, record = ask_pending(env)
        decide(env, record, "decline")
        assert wait_job(env, job_id).error["code"] == "declined"
        with pytest.raises(LimitHit) as exc:
            env.registry.call("change_item", {"item_id": "a1"},
                              ToolContext("mcp", connection="c", session="s", pending_approvals=True))
        assert exc.value.code == "declined" and "declined this exact call" in exc.value.message
        assert [r.id for r in env.store.pending()] == [], "no new pending approval for the same call"
        assert outcomes(env)[-1] == "rate_limited"

    def test_pending_approvals_count_toward_the_questions_per_hour(self, env):
        env.registry.limits = dataclasses.replace(env.registry.limits, questions_per_hour=2)
        ask_pending(env, "a1")
        ask_pending(env, "a2")
        with pytest.raises(LimitHit) as exc:
            env.registry.call("change_item", {"item_id": "a3"},
                              ToolContext("mcp", connection="c", session="s", pending_approvals=True))
        assert exc.value.code == "limit_reached" and "2 questions in the last hour" in exc.value.message
        assert len(env.store.pending()) == 2

    def test_the_limit_of_open_approvals_is_audited_and_never_a_failed_call(self, env):
        for n in range(MAX_PENDING_PER_CALLER):
            ask_pending(env, f"a{n}")
        texts = []
        for _ in range(3):
            with pytest.raises(LimitHit) as exc:
                env.registry.call("change_item", {"item_id": "over"},
                                  ToolContext("mcp", connection="c", session="s", pending_approvals=True))
            texts.append(str(exc.value))
        assert all("calls waiting for approval" in t for t in texts)
        assert not any("already failed" in t or "Stop retrying" in t for t in texts), "a limit is no failure"
        assert outcomes(env).count("rate_limited") == 3, "each refusal is in the audit log"

    def test_a_request_to_stop_asking_counts_as_a_question(self, env):
        env.registry.limits = dataclasses.replace(env.registry.limits, questions_per_hour=2)
        asked = []

        async def answer(ctx, params):
            asked.append(params)
            return ElicitResult(action="accept", content={"approve": True, "stop_asking": True})

        async def go():
            async with Client(env.server, elicitation_callback=answer) as client:
                first = await client.call_tool("change_item", {"item_id": "a1"})
                second = await client.call_tool("change_item", {"item_id": "a2"})
                return first, second
        first, second = run(go())
        assert not first.is_error and len(asked) == 1
        assert [r.kind for r in env.store.list()] == ["grant_request"]
        assert second.is_error and "2 questions in the last hour" in text_of(second), \
            "the question and the request used up the hour's questions"


class TestStandingApprovalsUseTheLookedUpFacts:

    def test_over_mcp_a_client_that_can_ask_is_not_asked_under_a_standing_approval(self, env):
        spec = env.registry.get("change_item")
        ctx = ToolContext("mcp")
        ctx.facts = env.registry.look_up(spec, ItemIn(item_id="a1"), ctx)
        offer = env.registry.grant_offer(spec, ItemIn(item_id="a1"), ctx)
        grant = env.registry.create_grant(offer, "this", "24h", "gui", "gui")
        asked = []

        async def answer(ctx, params):
            asked.append(params)
            return ElicitResult(action="accept", content={"approve": True})

        async def go():
            async with Client(env.server, elicitation_callback=answer) as client:
                return await client.call_tool("change_item", {"item_id": "a1"})
        result = run(go())
        assert not result.is_error and asked == [], "the standing approval covers the call: nobody is asked"
        ok = [e for e in env.registry.audit.entries(50) if e["outcome"] == "ok"]
        assert ok[0]["confirm_channel"] == f"grant:{grant.id}"


class TestQuotasWhenTheApprovedCallRuns:

    def test_the_quota_is_checked_again_when_the_person_approves(self, env):
        job_id, record = ask_pending(env)
        env.state.running = 1  # meanwhile the client has an unfinished change
        decide(env, record)
        job = wait_job(env, job_id)
        assert job.state == "failed" and job.error["code"] == "limit_reached"
        assert env.state.seen == [], "the quota stopped the approved call before its handler"


class TestDittosLongDialogKeepsTheStandingApproval:
    """A question longer than a message box (up to 16,000 characters) is shown in a scrollable dialog; where the
    framework offers "do not ask again", that dialog offers it too."""

    def test_the_long_dialog_has_the_button_only_with_an_offer(self, qapp):
        from orchestrator.gui import tool_confirmation
        dialog, allow = tool_confirmation.long_question_dialog(None, "Change an item", "x" * 2000, offer=object())
        assert dialog.always_button is not None and not dialog.always_button.isDefault() and not allow.isDefault()
        dialog.always_button.click()
        assert dialog.result() == tool_confirmation.ALWAYS
        plain, _ = tool_confirmation.long_question_dialog(None, "Change an item", "x" * 2000)
        assert plain.always_button is None
        dialog.deleteLater()
        plain.deleteLater()

    def test_a_long_question_can_still_get_a_standing_approval(self, qapp, monkeypatch):
        from orchestrator.gui import approvals_window, tool_confirmation
        def press_always(dialog):  # the person presses the dialog's own "Allow and do not ask again..." button
            dialog.always_button.click()
            return dialog.result()
        monkeypatch.setattr(tool_confirmation.QDialog, "exec", press_always)
        monkeypatch.setattr(approvals_window, "choose_grant", lambda offer, parent: ("this", "24h"))
        answer = tool_confirmation.ToolConfirmation().ask_user("Change an item", "x" * 2000, object())
        assert answer and answer.grant_choice == ("this", "24h")
