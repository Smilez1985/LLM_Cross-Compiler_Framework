"""The MCP server as one piece (integration of the S0, S1-S3a, S6, S7 and S12 work): a call over the real SDK passes
the checked caller identity, the fool-proof layer, the caller's own permissions, the path confinement for local
files, the audit log and the output filter, and the same caller identity owns the builds and jobs it starts."""

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.tools.policy import Decision, ToolPolicy

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult, Implementation  # noqa: E402

from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools.registry import ToolContext  # noqa: E402

BUILD = {"model": "HuggingFaceTB/SmolLM2-135M-Instruct", "target": "Rockchip", "format": "gguf", "board": "rk3566"}


class FakeOrchestrator:
    """Builds that stay queued; cancel_request marks them cancelled."""

    def __init__(self):
        self._workflows = {}
        self.submitted = []
        self.cancelled = []
        self.build_engine = SimpleNamespace(get_build_status=lambda bid: None, is_active=lambda bid: False)

    async def submit_build_request(self, req):
        from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
        self.submitted.append(req)
        self._workflows[req.request_id] = WorkflowState(req.request_id, OrchestrationStatus.QUEUED, datetime.now(),
                                                        owner=req.owner)
        return req.request_id

    async def cancel_request(self, rid):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        self.cancelled.append(rid)
        self._workflows[rid].status = OrchestrationStatus.CANCELLED
        return True


@pytest.fixture
def framework(booted_framework):
    booted_framework.orchestrator = FakeOrchestrator()
    return booted_framework


def run(coro):
    return asyncio.run(coro)


def text_of(result) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


def models_dir(fw) -> Path:
    return Path(fw.resolve_path(fw.config.get("models_dir")))


def token_with_permissions(registry, name, **policy):
    token = registry.tokens.add(name)
    registry.tokens.set_policy(name, ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()],
                                                **policy))
    return token


async def accept(ctx, params):
    return ElicitResult(action="accept", content={"approve": True})


class TestOneCallThroughEveryLayer:

    def test_a_tool_call_over_the_sdk_passes_identity_normalizer_policy_paths_audit_and_output(self, framework):
        registry = framework.tool_registry
        # Identity (S12): the stdio server runs for the named token from LCF_MCP_TOKEN; its own permissions allow
        # start_build, while your shared permissions (the caller "mcp") would ask.
        token = token_with_permissions(registry, "desk", overrides={"start_build": Decision.ALLOW})
        caller, still_valid = mcp_server.stdio_caller(registry.tokens, token)
        assert caller == "mcp:desk"
        assert registry.decide(registry.get("start_build"), caller="mcp") == Decision.ASK
        # Path confinement (S6): a local model is named by label, never by path.
        model = models_dir(framework) / "smol" / "model.gguf"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"weights")
        secret = "hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH"  # built at runtime, no real credential
        # The client's self-reported name holds a key right where the audit label used to be cut (80 characters).
        info = Implementation(name="a" * 70 + " " + secret, version="1.0")
        server = mcp_server.build_mcp_server(framework, caller=caller, still_valid=still_valid)

        async def go():
            async with Client(server, client_info=info) as client:
                # Fool-proof layer (S3a): a spelling variant of the tool name and a declared alias in lower case.
                return await client.call_tool("StartBuild", {**BUILD, "model": "models:smol/model.gguf",
                                                             "quant": "q4_k_m"})
        result = run(go())
        assert not result.is_error, text_of(result)

        # The handler got the normalized call, and the build service pinned the confined file.
        [req] = framework.orchestrator.submitted
        assert req.quantization_options == ["Q4_K_M"] and req.local_inputs.model.ref == "models:smol/model.gguf"
        assert req.models == [str(model.resolve())]
        # Ownership (S7) uses the same identity: the token and this connection.
        rid = result.structured_content["request_id"]
        assert framework.build_service.owner_of(rid).startswith("mcp:desk#")

        # Output filter (S2): schema-exact structured content, the framework's notes with the repairs, and a first
        # line built from framework facts only.
        notes = " ".join(result.structured_content["notes"])
        assert "start_build" in notes and "quantization" in notes
        first_line = text_of(result).splitlines()[0]
        assert first_line == f"Build request {rid} is queued."

        # Audit (S1): a write-ahead entry and the end entry, under the token's actor, with the repairs; the client
        # label is redacted before it is cut, so no part of the key reaches the log.
        entries = [e for e in registry.audit.entries(10) if e["tool"] == "start_build"]
        assert [e["outcome"] for e in reversed(entries)] == ["started", "ok"]
        assert {e["actor"] for e in entries} == {"mcp:desk"}
        assert entries[0]["name_requested"] == "StartBuild"
        assert {r["field"] for r in entries[0]["repairs"] if r.get("field")} >= {"quantization"}
        for entry in entries:  # only the redaction's own marker may remain, never the key's characters
            assert "abcdefghi" not in str(entry) and entry["client"].startswith("a" * 60), entry["client"]
        assert registry.audit.check().intact

    def test_a_path_from_the_same_caller_is_refused_and_audited_under_its_token(self, framework):
        registry = framework.tool_registry
        token = token_with_permissions(registry, "desk", overrides={"start_build": Decision.ALLOW})
        caller, still_valid = mcp_server.stdio_caller(registry.tokens, token)
        server = mcp_server.build_mcp_server(framework, caller=caller, still_valid=still_valid)

        async def go():
            async with Client(server) as client:
                return await client.call_tool("start_build", {**BUILD, "model": "~/.ssh/id_ed25519"})
        result = run(go())
        assert result.is_error and "<untrusted_data" in text_of(result) and framework.orchestrator.submitted == []
        latest = registry.audit.entries(1)[0]
        assert (latest["actor"], latest["tool"], latest["outcome"]) == ("mcp:desk", "start_build", "error")


class TestOneCallerIdentity:

    def test_the_audit_client_label_is_redacted_before_it_is_cut(self, framework):
        """Integration fix: the audit label (S1) cut the client's name at 80 characters before the audit log
        redacted it, so a key placed there left its first characters in the log; the stderr line (S0) was already
        fixed. Both now use the same label, redacted first."""
        registry = framework.tool_registry
        secret = "hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH"
        for padding in (60, 70, 75):
            info = Implementation(name="a" * padding + " " + secret, version="1")
            server = mcp_server.build_mcp_server(framework)

            async def go():
                async with Client(server, client_info=info) as client:
                    await client.call_tool("list_targets", {})
            run(go())
            entry = registry.audit.entries(1)[0]
            assert entry["tool"] == "list_targets" and entry["client"], entry
            assert "abcdef" not in entry["client"], (padding, entry["client"])

    def test_an_http_server_never_falls_back_to_your_own_permissions(self, framework):
        """Without a caller that ClientTracker checked (no request, so no middleware ran), a server uses a caller no
        token can be, which gets nothing: an HTTP-built server, and (S12 leftover: the default fails closed) a stdio
        server too, which no longer falls back to its own caller."""
        registry = framework.tool_registry
        assert registry.decide(registry.get("list_targets"), caller=mcp_server.UNCHECKED_CALLER) == Decision.DENY
        http = mcp_server.build_mcp_server(framework, transport="http")
        stdio = mcp_server.build_mcp_server(framework)
        assert run(http.list_tools()) == []
        assert run(stdio.list_tools()) == []

    def test_waits_are_counted_per_client_over_one_connection(self, framework, monkeypatch):
        """Over HTTP the server lifespan is shared: the cap of 4 running waits holds per token, not per server."""
        import anyio
        from orchestrator.Core import job_service
        monkeypatch.setattr(job_service, "MAX_WAIT_SECONDS", 5)
        registry = framework.tool_registry
        for name in ("one", "two"):
            registry.tokens.add(name)
        session = mcp_server.McpSession("k")
        release = anyio.Event()

        async def wait(_inp, _seconds, _report):
            await release.wait()
        spec = registry.get("get_job_status")
        spec = type(spec)(**{**spec.__dict__, "long_poll": wait})
        mcp_ctx = SimpleNamespace(request_context=SimpleNamespace(
            lifespan_context={mcp_server.SESSION_STATE_KEY: session}), report_progress=None)
        contexts = [ToolContext("mcp:one", connection="k") for _ in range(5)] + [ToolContext("mcp:two",
                                                                                              connection="k")]

        async def go():
            async with anyio.create_task_group() as tg:
                for ctx in contexts:
                    tg.start_soon(mcp_server._long_poll, spec, registry,
                                  {"job_id": "job_0123456789abcdef", "wait_seconds": 5}, ctx, mcp_ctx)
                await anyio.sleep(0.2)
                running = dict(session.waits)
                release.set()
            return running
        running = anyio.run(go)
        assert running == {"mcp:one#k": 4, "mcp:two#k": 1}
        skipped = [ctx for ctx in contexts if ctx.metadata.get("wait_notes")]
        assert [ctx.actor for ctx in skipped] == ["mcp:one"] and session.waits == {}

    def test_get_status_shows_a_tokens_own_permissions(self, framework):
        registry = framework.tool_registry
        registry.policy_store.save(ToolPolicy(configured=True))
        registry.tokens.add("fresh")
        out = registry.call("get_status", {}, ToolContext("mcp:fresh"))
        assert out.permissions.caller == "mcp:fresh" and out.permissions.configured is False
        decisions = {t.tool: t.decision for t in out.permissions.tools}
        assert decisions["list_targets"] == "allow" and decisions["start_build"] == "deny"

    def test_each_token_owns_its_builds_over_one_connection(self, framework):
        """Over HTTP one server lifespan serves every token: a token cancels its own build without a question,
        another token's build only after one, and the end of the session cancels the builds of every token, each
        audited under its own actor."""
        registry = framework.tool_registry
        for name in ("one", "two"):
            token_with_permissions(registry, name, overrides={"start_build": Decision.ALLOW})
        session = mcp_server.McpSession("k")
        one, two = ToolContext("mcp:one", connection="k"), ToolContext("mcp:two", connection="k")
        session.owners.update({one.owner, two.owner})
        rid_one = registry.call("start_build", BUILD, one).request_id
        rid_two = registry.call("start_build", BUILD, two).request_id
        cancel = registry.get("cancel_build")
        assert registry.decide(cancel, cancel.input_model(request_id=rid_one), one) == Decision.ALLOW
        assert registry.decide(cancel, cancel.input_model(request_id=rid_one), two) == Decision.ASK, \
            "another token's build needs a question"
        mcp_server.end_session(framework, registry, session, wait_seconds=0)
        assert set(framework.orchestrator.cancelled) == {rid_one, rid_two}
        ended = {(e["actor"], e["tool"]) for e in registry.audit.entries(10) if e["outcome"] == "cancelled"}
        assert ended == {("mcp:one", "cancel_build"), ("mcp:two", "cancel_build")}

    def test_work_the_gui_started_is_never_anyones_own(self, framework):
        """A decision without a named caller must not match the owner of a job the GUI or the CLI started (empty):
        cancelling it follows the permissions instead of the own-work rule."""
        job = framework.job_service.create("crawl", "", state="running")
        cancel = framework.tool_registry.get("cancel_job")
        assert framework.tool_registry.decide(cancel, cancel.input_model(job_id=job.job_id), "") != Decision.ALLOW

    def test_a_new_token_reads_no_more_than_your_shared_setting(self, framework):
        """A token whose permissions you have not set yet may only read, and never a read tool your shared
        permissions deny (S3's new-token rule with S12's token store)."""
        registry = framework.tool_registry
        registry.tokens.add("fresh")
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"read_build_log": Decision.DENY}))
        assert registry.decide(registry.get("list_targets"), caller="mcp:fresh") == Decision.ALLOW
        assert registry.decide(registry.get("read_build_log"), caller="mcp:fresh") == Decision.DENY
        assert registry.decide(registry.get("start_build"), caller="mcp:fresh") == Decision.DENY

    def test_policy_from_a_caller_copies_its_own_section(self, framework):
        """`mcp token add --policy-from mcp` copies the decisions of the caller mcp, which can have permissions
        of its own (S3), with its reviewed tools; the copy counts as set by you."""
        from orchestrator.tools.policy import CallerPolicy, ToolTier
        registry = framework.tool_registry
        reviewed = [s.name for s in registry.list()]
        registry.policy_store.save(ToolPolicy(configured=True, reviewed=reviewed, callers={
            "mcp": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}, reviewed=reviewed)}))
        copied = registry.policy("mcp")
        assert copied.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW and copied.reviewed == reviewed
        registry.tokens.add("copy", policy=copied.model_copy(update={"configured": True}))
        assert registry.decide(registry.get("start_build"), caller="mcp:copy") == Decision.ALLOW
        assert registry.decide(registry.get("start_build"), caller="ditto") == Decision.ASK, "the shared ones"
