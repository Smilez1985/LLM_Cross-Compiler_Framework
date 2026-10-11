"""Audit hardening and cancellation (MCP design S1, gaps G1-G4 and G29).

A call that changes something gets a write-ahead `started` entry and is refused when that entry cannot be written
(fail closed); reads stay available. A question put to a person is recorded with the hash of its text before it is
asked. Unanswered, timed-out and cancelled questions and calls are recorded in both protocol eras, and a client's
cancel reaches the running call at once. The new entry fields are part of the hash chain, and logs written before
them stay verifiable.
"""

import asyncio
import hashlib
import hmac
import json
import threading
import time
from types import SimpleNamespace

import anyio
import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
import mcp_types  # noqa: E402
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools.audit import AuditLog, extra_fields  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import (AuditUnavailable, ToolContext, ToolDenied, ToolRegistry, ToolSpec,  # noqa: E402
                                         text_sha256)
from test_audit_chain import FIELDS, KEY, query, run_sql  # noqa: E402
from test_mcp_server import answering, text_of  # noqa: E402


class In(BaseModel):
    text: str


class Out(BaseModel):
    echoed: str


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db", key=KEY))
    reg.writes, reg.release, reg.entered = [], threading.Event(), threading.Event()

    def slow(inp):
        reg.entered.set()
        reg.release.wait(10)
        reg.writes.append(inp.text)
        return Out(echoed="slow done")

    reg.register(ToolSpec("echo", "Echo", "Echo", ToolTier.READ, In, Out, lambda i: Out(echoed=i.text)))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out,
                          lambda i: reg.writes.append(i.text) or Out(echoed="written")))
    reg.register(ToolSpec("slow", "Slow write", "Writes slowly", ToolTier.LOCAL_WRITE, In, Out, slow))
    return reg


@pytest.fixture
def server(registry):
    return mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=registry))


def entries(registry):
    """Every audit entry, oldest first."""
    return list(reversed(registry.audit.entries(100)))


def outcomes(registry):
    return [(e["tool"], e["outcome"]) for e in entries(registry)]


def allow(registry, *names):
    registry.policy_store.save(ToolPolicy(configured=True, overrides={n: Decision.ALLOW for n in names}))


def break_the_audit_log(tmp_path):
    (tmp_path / "audit.db").write_bytes(b"not a database" * 50)


def call(server, tool, arguments, callback=None, mode="auto"):
    async def go():
        async with Client(server, elicitation_callback=callback, mode=mode) as client:
            return await client.call_tool(tool, arguments)
    return asyncio.run(go())


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- Write-ahead entry and fail-closed audit (G1, G2) ---------------------------------------------------------

class TestWriteAhead:

    def test_a_change_is_recorded_before_its_handler_runs(self, registry):
        seen = []
        registry.register(ToolSpec("peek", "Peek", "", ToolTier.LOCAL_WRITE, In, Out,
                                   lambda i: seen.append(outcomes(registry)) or Out(echoed="ok")))
        allow(registry, "peek")
        registry.call("peek", {"text": "x"}, ToolContext("mcp"))
        assert seen == [[("peek", "started")]], "the started entry exists while the handler runs"
        started, end = entries(registry)
        assert (end["outcome"], end["links"]) == ("ok", [started["id"]])
        assert end["duration_ms"] >= 0 and end["result"]["sha256"] == text_sha256('{"echoed":"ok"}')

    def test_a_handler_that_dies_leaves_the_started_entry(self, registry):
        def crash(_inp):
            raise KeyboardInterrupt  # the process is stopped in the middle of the side effect
        registry.register(ToolSpec("crash", "Crash", "", ToolTier.LOCAL_WRITE, In, Out, crash))
        allow(registry, "crash")
        with pytest.raises(KeyboardInterrupt):
            registry.call("crash", {"text": "x"}, ToolContext("mcp"))
        assert outcomes(registry) == [("crash", "started"), ("crash", "cancelled")]

    def test_reads_have_no_started_entry(self, registry):
        registry.call("echo", {"text": "x"}, ToolContext("mcp"))
        assert outcomes(registry) == [("echo", "ok")]

    def test_audit_failure_refuses_a_change(self, registry, tmp_path):
        """Twin of test_review_security_core.py::test_audit_failure_never_breaks_the_call, which keeps that
        meaning for reads: a call that changes something does not run unrecorded."""
        allow(registry, "write")
        break_the_audit_log(tmp_path)
        with pytest.raises(AuditUnavailable) as exc:
            registry.call("write", {"text": "x"}, ToolContext("mcp"))
        assert registry.writes == [] and "audit log cannot be written" in str(exc.value)
        assert registry.call("echo", {"text": "y"}, ToolContext("mcp")).echoed == "y", "reads still run"

    def test_a_question_that_cannot_be_recorded_is_not_asked(self, registry, tmp_path):
        asked = []
        break_the_audit_log(tmp_path)
        with pytest.raises(AuditUnavailable):
            registry.call("write", {"text": "x"}, ToolContext("ditto", confirm=lambda s, i: asked.append(s) or True))
        assert asked == [] and registry.writes == []

    def test_mcp_change_is_refused_and_read_runs_without_the_audit_log(self, server, registry, tmp_path):
        allow(registry, "write")
        break_the_audit_log(tmp_path)
        result = call(server, "write", {"text": "x"})
        assert result.is_error and "audit log cannot be written" in text_of(result) and registry.writes == []
        result = call(server, "echo", {"text": "y"})
        assert not result.is_error and result.structured_content == {"echoed": "y"}

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    def test_mcp_question_is_not_asked_without_the_audit_log(self, server, registry, tmp_path, mode):
        asked = []
        break_the_audit_log(tmp_path)
        result = call(server, "write", {"text": "x"}, answering("accept", asked=asked), mode)
        assert result.is_error and asked == [] and registry.writes == []
        assert "audit log cannot be written" in text_of(result)


# --- Questions: recorded, answered, timed out, unanswered (G3, G4) --------------------------------------------

class TestQuestions:

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    def test_the_question_is_recorded_with_the_hash_of_its_text(self, server, registry, mode):
        asked = []
        assert not call(server, "write", {"text": "x"}, answering("accept", asked=asked), mode).is_error
        flow = entries(registry)
        assert [e["outcome"] for e in flow] == ["confirmation_requested", "started", "ok"]
        digest = text_sha256(asked[0])
        assert all(e["question_sha256"] == digest for e in flow)
        assert flow[0]["confirm_channel"] == "elicitation" and flow[-1]["answer"] == "accepted"
        assert flow[-1]["approved"] == 1

    @pytest.mark.parametrize("action, answer", [("decline", "declined"), ("cancel", "cancelled")])
    def test_declined_and_cancelled_answers_are_named(self, server, registry, action, answer):
        assert call(server, "write", {"text": "x"}, answering(action)).is_error
        end = entries(registry)[-1]
        assert (end["outcome"], end["answer"], end["approved"]) == ("not_confirmed", answer, 0)

    def test_dittos_question_names_its_channel(self, registry):
        def confirm(spec, inp):
            return True
        confirm.confirm_channel = "framework-gui"
        registry.call("write", {"text": "x"}, ToolContext("ditto", confirm=confirm))
        flow = entries(registry)
        assert [e["outcome"] for e in flow] == ["confirmation_requested", "started", "ok"]
        assert {e["confirm_channel"] for e in flow} == {"framework-gui"}
        shown = registry.question_for(registry.get("write"), In(text="x"), ToolContext("ditto"))
        assert flow[0]["question_sha256"] == text_sha256(shown) and "Ditto wants to run" in shown

    def test_nobody_to_ask_is_recorded_as_unavailable(self, server, registry):
        assert call(server, "write", {"text": "x"}).is_error
        end = entries(registry)[-1]
        assert (end["outcome"], end["answer"], end["approved"]) == ("not_confirmed", "unavailable", None)

    def test_a_classic_question_times_out_and_is_audited(self, server, registry, monkeypatch):
        monkeypatch.setattr(mcp_server, "ELICITATION_TIMEOUT", 0.3)

        async def never(ctx, params):
            await anyio.sleep(5)
            return ElicitResult(action="accept", content={"approve": True})
        result = call(server, "write", {"text": "x"}, never, "legacy")
        assert result.is_error and "no answer within" in text_of(result) and registry.writes == []
        requested, end = entries(registry)
        assert requested["outcome"] == "confirmation_requested"
        assert (end["outcome"], end["answer"]) == ("confirmation_timeout", "timeout")
        assert end["question_sha256"] == requested["question_sha256"]

    def test_an_unanswered_question_of_the_rounds_era_expires_as_unanswered(self, server, registry):
        expired = []

        async def go():
            async with Client(server, elicitation_callback=answering("accept"), mode="auto") as client:
                first = await client.session.call_tool("write", {"text": "x"}, allow_input_required=True)
                # While the client is still connected (its session's end records an open question itself).
                expired.append(server.open_questions.expire())
                expired.append(server.open_questions.expire(time.monotonic() + mcp_server.QUESTION_EXPIRY + 1))
                return first
        first = asyncio.run(go())
        assert isinstance(first, mcp_types.InputRequiredResult)
        assert expired == [0, 1], "not yet, then once"
        requested, end = entries(registry)
        assert requested["outcome"] == "confirmation_requested"
        assert (end["outcome"], end["answer"]) == ("not_confirmed", "unanswered")
        assert end["question_sha256"] == requested["question_sha256"] and registry.writes == []

    def test_an_answered_question_of_the_rounds_era_does_not_expire(self, server, registry):
        assert not call(server, "write", {"text": "x"}, answering("accept"), "auto").is_error
        assert server.open_questions.expire(time.monotonic() + mcp_server.QUESTION_EXPIRY + 1) == 0
        assert [o for _, o in outcomes(registry)] == ["confirmation_requested", "started", "ok"]

    # "Write": review fix, a call made with a spelling variant of the tool name was not recorded when cancelled.
    @pytest.mark.parametrize("name", ["write", "Write"])
    def test_a_question_abandoned_by_the_client_is_audited(self, server, registry, name):
        """Classic era: the client cancels the call while its question is open."""
        opened = threading.Event()

        async def hang(ctx, params):
            opened.set()
            await anyio.sleep(30)

        async def go():
            async with Client(server, elicitation_callback=hang, mode="legacy") as client:
                with anyio.move_on_after(0.5):
                    await client.call_tool(name, {"text": "x"})
        asyncio.run(go())
        assert opened.is_set() and wait_for(lambda: len(entries(registry)) == 2)
        requested, end = entries(registry)
        assert (end["tool"], end["outcome"], end["answer"]) == ("write", "cancelled", "unanswered")
        assert end["question_sha256"] == requested["question_sha256"] and registry.writes == []


# --- A client's cancel reaches the running call (G29) ---------------------------------------------------------

class TestCancellation:

    def test_cancel_event_is_set_while_the_handler_runs_and_the_end_follows(self, server, registry, monkeypatch):
        allow(registry, "slow")
        contexts, real_call = [], registry.call

        def capture(name, arguments, ctx, approved=None):
            contexts.append(ctx)
            return real_call(name, arguments, ctx, approved=approved)
        monkeypatch.setattr(registry, "call", capture)

        async def go():
            async with Client(server) as client:
                with anyio.move_on_after(0.5):
                    await client.call_tool("slow", {"text": "x"})
                # Back in the client while the handler still runs: the cancel event must already be set.
                assert await anyio.to_thread.run_sync(lambda: contexts[0].cancel.wait(5))
                assert registry.writes == [], "the handler has not returned yet"
                registry.release.set()
        asyncio.run(go())
        assert wait_for(lambda: [o for _, o in outcomes(registry)][-1:] == ["ok"])
        started, cancelled, end = entries(registry)
        assert [started["outcome"], cancelled["outcome"]] == ["started", "cancelled"]
        assert end["links"] == [started["id"]] and "cancelled the call while it ran" in end["detail"]
        assert registry.writes == ["x"], "what already ran finishes"

    def test_a_cancelled_call_does_not_start_its_handler(self, registry):
        ctx = ToolContext("mcp")
        ctx.cancel.set()
        allow(registry, "write")
        with pytest.raises(Exception, match="cancelled before it started"):
            registry.call("write", {"text": "x"}, ctx)
        assert registry.writes == [] and outcomes(registry) == [("write", "started"), ("write", "cancelled")]

    def test_the_chain_verifies_after_every_kind_of_end(self, server, registry, monkeypatch):
        monkeypatch.setattr(mcp_server, "ELICITATION_TIMEOUT", 0.2)

        async def never(ctx, params):
            await anyio.sleep(5)
        call(server, "write", {"text": "a"}, answering("accept"))
        call(server, "write", {"text": "b"}, answering("decline"))
        call(server, "write", {"text": "c"}, never, "legacy")
        call(server, "echo", {"text": "d"})
        with pytest.raises(ToolDenied):
            registry.call("write", {"text": "e"}, ToolContext("mcp"))
        result = registry.audit.check()
        assert result.intact and result.entries == len(entries(registry)) >= 9, result.message


# --- Extra fields in the hash chain --------------------------------------------------------------------------

class TestExtraFields:

    def test_extra_fields_are_stored_and_chained(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        entry = log.record("mcp", "write", "local_write", "ask", "not_confirmed", extra={
            "confirm_channel": "elicitation", "answer": "declined", "question_sha256": "a" * 64, "reason_code": "declined"})
        [row] = log.entries(1)
        assert row["id"] == entry and extra_fields(row)["answer"] == "declined" and log.check().intact
        run_sql(log.path, "UPDATE events SET extra = ? WHERE id = ?", (row["extra"].replace("declined", "accepted"), entry))
        assert log.check().problem == "modified"

    def test_removing_the_extra_fields_is_detected(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        log.record("mcp", "write", "local_write", "ask", "ok", extra={"answer": "accepted"})
        run_sql(log.path, "UPDATE events SET extra = NULL")
        assert log.check().problem == "modified"

    def test_extra_fields_added_to_an_older_entry_are_detected(self, tmp_path):
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        log.record("mcp", "write", "local_write", "ask", "ok")
        run_sql(log.path, "UPDATE events SET extra = '{\"answer\":\"accepted\"}'")
        assert log.check().problem == "modified"

    @pytest.mark.parametrize("extra", [{"unknown": 1}, {"answer": "maybe"}, {"confirm_channel": "a;b"}])
    def test_undocumented_values_are_refused(self, tmp_path, extra):
        with pytest.raises(ValueError):
            AuditLog(tmp_path / "audit.db", key=KEY).record("mcp", "t", "read", "allow", "ok", extra=extra)

    def test_unknown_outcomes_are_refused(self, tmp_path):
        with pytest.raises(ValueError):
            AuditLog(tmp_path / "audit.db", key=KEY).record("mcp", "t", "read", "allow", "approved-ish")

    def test_secrets_in_extra_fields_are_redacted(self, tmp_path):
        token = "hf_" + "abcdefghijklmnopqrstuvwxyz0123"
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        log.record("mcp", "t", "read", "allow", "ok", extra={"client": f"evil {token}"})
        assert token not in log.entries(1)[0]["extra"]

    def test_a_300_log_without_the_column_is_migrated_and_stays_verifiable(self, tmp_path):
        path = tmp_path / "audit.db"
        run_sql(path, """CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                         actor TEXT NOT NULL, tool TEXT NOT NULL, tier TEXT NOT NULL, decision TEXT NOT NULL,
                         approved INTEGER, args_sha256 TEXT NOT NULL, args_preview TEXT NOT NULL,
                         outcome TEXT NOT NULL, detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
                         key_id TEXT)""")
        run_sql(path, "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        append_300_entry(path, KEY)
        log = AuditLog(path, key=KEY)
        assert log.check().intact, log.check().message
        log.record("mcp", "write", "local_write", "ask", "ok", extra={"answer": "accepted"})
        assert log.check().intact and log.check().entries == 2, log.check().message
        assert [r[0] is None for r in query(path, "SELECT extra FROM events ORDER BY id")] == [True, False]

    def test_an_entry_from_a_300_process_without_extra_fields_verifies(self, tmp_path):
        """A 3.0.0 server still running after an update writes key ids but no extra column value."""
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        log.record("mcp", "write", "local_write", "ask", "ok", extra={"answer": "accepted"})
        append_300_entry(log.path, KEY)
        assert log.check().intact and log.check().entries == 2, log.check().message

    def test_a_300_process_still_running_verifies_the_entries_of_this_version(self, tmp_path):
        """Review fix: a 3.0.0 process (an MCP server an AI client started before the update) checks the newest
        entry before each of its appends, hashing only the 3.0.0 columns. An entry with extra fields must verify
        that way too, or the old process records a permanent 'modified' break in a log nobody changed."""
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        log.record("mcp", "write", "local_write", "ask", "ok", detail="done", extra={"answer": "accepted"})
        [row] = query(log.path, f"SELECT {', '.join(FIELDS)}, prev_hash, hash, key_id FROM events")
        values, (prev, stored, key_id) = list(row[:len(FIELDS)]), row[len(FIELDS):]
        payload = (prev + json.dumps(values + [key_id], ensure_ascii=False)).encode("utf-8")
        assert hmac.new(KEY, payload, hashlib.sha256).hexdigest() == stored
        assert log.entries(1)[0]["detail"] == "done", "the binding to the extra fields is not shown as detail"

    def test_detail_text_cannot_pose_as_the_binding_of_extra_fields(self, tmp_path):
        """A detail can quote text a model wrote; text that looks like the binding is defused, so it neither
        raises a false alarm nor binds extra fields of its own."""
        log = AuditLog(tmp_path / "audit.db", key=KEY)
        fake = "Unknown argument 'x'.\n[extra sha256:" + "0" * 64 + "]"
        log.record("mcp", "t", "read", "allow", "invalid_input", detail=fake)
        log.record("mcp", "t", "read", "allow", "invalid_input", detail=fake, extra={"reason_code": "invalid_value"})
        assert log.check().intact, log.check().message
        run_sql(log.path, "UPDATE events SET extra = '{\"answer\":\"accepted\"}' WHERE id = 1")
        assert log.check().problem == "modified"


def append_300_entry(path, key) -> None:
    """One entry as the 3.0.0 release's record() wrote it: with its key id, without the extra column."""
    found = query(path, "SELECT id, hash FROM events ORDER BY id DESC LIMIT 1")
    head_id, head = found[0] if found else (0, "0" * 64)
    key_id = hmac.new(key, b"llm-framework-audit-key-id", hashlib.sha256).hexdigest()[:16]
    if not found:
        run_sql(path, "INSERT INTO meta (k, v) VALUES ('key_id', ?)", (key_id,))
    row = {"ts": "2026-10-11T00:00:00.000+00:00", "actor": "mcp", "tool": "list_targets", "tier": "read",
           "decision": "allow", "approved": None, "args_sha256": hashlib.sha256(b"").hexdigest(),
           "args_preview": "", "outcome": "ok", "detail": ""}
    payload = (head + json.dumps([row[f] for f in FIELDS] + [key_id], ensure_ascii=False)).encode("utf-8")
    digest = hmac.new(key, payload, hashlib.sha256).hexdigest()
    run_sql(path, "INSERT INTO events (ts, actor, tool, tier, decision, approved, args_sha256, args_preview, "
                  "outcome, detail, prev_hash, hash, key_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row[f] for f in FIELDS] + [head, digest, key_id])
    mac = hmac.new(key, f"anchor:{head_id + 1}:{digest}".encode(), hashlib.sha256).hexdigest()
    path.with_name(path.name + ".anchor").write_text(json.dumps({"id": head_id + 1, "hash": digest, "mac": mac}))
