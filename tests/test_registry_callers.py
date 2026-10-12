"""Callers, visibility and the deletion rule of the tool registry (delegation design WP1; sections 3.3, 3.6, 3.10;
SEC-M13; round 5 f).

- `ToolSpec.callers`, `model_facing` and `visible_when` decide which tools a session is offered. They are checked
  against the real actor of a call (a token linked to Ditto is not Ditto), when the tools are listed and again when one
  is called by name: a hidden tool is refused (not_supported) and audited.
- A tool that deletes something the person can see is the person's alone: only the framework's own window asks (never
  a client's form), never a standing approval, never a delegation, never "allow". The registry refuses to register a
  tool that breaks this, and the rule holds at run time for every call whose facts carry a deletion plan too.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from orchestrator import mcp_server  # noqa: E402
from orchestrator.Core.job_service import JobService  # noqa: E402
from orchestrator.tools import delegation as delegation_module  # noqa: E402
from orchestrator.tools.approvals import ApprovalService, ApprovalStore  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.delegation import NEVER_DELEGATE, ToolScope  # noqa: E402
from orchestrator.tools.grants import NEVER_GRANT, tool_rule  # noqa: E402
from orchestrator.tools.mcp_tokens import TokenStore  # noqa: E402
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import (ToolContext, ToolError, ToolRegistry, ToolSpec,  # noqa: E402
                                         current_question, deletion_rule_problems)
from orchestrator.tools.spec import CallFacts, DeletionItem, DeletionPlan, GrantRule  # noqa: E402
from test_mcp_server import answering  # noqa: E402


class In(BaseModel):
    text: str = "x"


class Out(ToolOutput):
    echoed: str = ""


RAN: list = []


def spec(name, tier=ToolTier.LOCAL_WRITE, **fields):
    return ToolSpec(name, name.replace("_", " ").title(), f"The {name} tool", tier, In, Out,
                    lambda i: RAN.append(name) or Out(echoed=name), **fields)


def deleting(name="delete_artifact", **fields):
    values = dict(deletes=True, channels=("framework",), max_decision=Decision.ASK)
    values.update(fields)
    return spec(name, ToolTier.EXTERNAL, destructive=True, **values)


PLAN = DeletionPlan(items=[DeletionItem(kind="artifact", speaking_name="Qwen 0.5B for RK3588 (Q4_K_M)",
                                        full_path="/srv/output/Rockchip/qwen/Q4_K_M", file_name="Q4_K_M",
                                        size_bytes=1234, what="built model folder with its golden ZIP")],
                    total_bytes=1234, mode_offered=("permanent",))


@pytest.fixture
def registry(tmp_path):
    RAN.clear()
    tokens = TokenStore(tmp_path)
    tokens.add("laptop")
    tokens.add("remote-ditto", link="ditto")
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"), tokens=tokens)
    return reg


def allow_all(registry):
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()],
                                             overrides={s.name: Decision.ALLOW for s in registry.list()}))


def names(specs):
    return {s.name for s in specs}


def mcp_listing(registry, caller):
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry),
                                         caller=caller)

    async def go():
        async with Client(server) as client:
            listed = {t.name for t in (await client.list_tools()).tools}
            called = await client.call_tool("ditto_only", {"text": "x"})
            return listed, called
    return asyncio.run(go())


# --- callers and visibility ---------------------------------------------------------------------------------------

def test_callers_and_visibility_checked_against_the_real_actor(registry):
    for s in (spec("ditto_only", callers=("ditto",)), spec("mcp_only", callers=("mcp", "mcp:*")),
              spec("internal", model_facing=False), spec("everyone"),
              spec("hidden_now", visible_when=lambda ctx: ctx.metadata.get("show") is True),
              spec("broken_check", visible_when=lambda ctx: 1 / 0)):
        registry.register(s)
    allow_all(registry)

    assert names(registry.visible("ditto")) == {"ditto_only", "everyone"}
    assert names(registry.visible("mcp")) == {"mcp_only", "everyone"}
    assert names(registry.visible("mcp:laptop")) <= {"mcp_only", "everyone"}
    # A token linked to Ditto has Ditto's permissions, but it is not Ditto: Ditto-only tools are never its own.
    assert names(registry.visible("mcp:remote-ditto")) == {"mcp_only", "everyone"}
    assert names(registry.visible("ditto", ToolContext("ditto", metadata={"show": True}))) == {
        "ditto_only", "everyone", "hidden_now"}
    rows = {r["tool"]: r["listed"] for r in registry.permissions("mcp")}
    assert rows["mcp_only"] and not rows["ditto_only"] and not rows["internal"] and not rows["hidden_now"]

    # Called by name, a hidden tool is refused and audited; its handler never runs.
    for actor, tool in (("mcp:remote-ditto", "ditto_only"), ("mcp", "ditto_only"), ("ditto", "mcp_only"),
                        ("ditto", "hidden_now"), ("ditto", "broken_check")):
        with pytest.raises(ToolError) as exc:
            registry.call(tool, {"text": "x"}, ToolContext(actor, session="s"))
        assert exc.value.code == "not_supported", (actor, tool)
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"], entry["outcome"], entry["reason_code"]) == (
            actor, tool, "denied", "not_supported")
    assert RAN == []

    registry.call("ditto_only", {}, ToolContext("ditto"))
    registry.call("mcp_only", {}, ToolContext("mcp:remote-ditto"))  # "mcp:*": every named token
    registry.call("internal", {}, ToolContext("mcp"))  # never listed, callable by name (internal tools)
    registry.call("hidden_now", {}, ToolContext("ditto", metadata={"show": True}))
    assert RAN == ["ditto_only", "mcp_only", "internal", "hidden_now"]

    # The MCP server lists what the registry offers this caller and refuses the rest by name.
    listed, called = mcp_listing(registry, "mcp:remote-ditto")
    assert listed == {"mcp_only", "everyone"}
    assert called.is_error and called.structured_content["error"]["code"] == "not_supported"
    assert RAN == ["ditto_only", "mcp_only", "internal", "hidden_now"]


def test_callers_must_name_someone(registry):
    with pytest.raises(ValueError, match="callers"):
        registry.register(spec("nobody", callers=()))


# --- the deletion rule (round 5 f) ---------------------------------------------------------------------------------

REVIEWED_DESTRUCTIVE = {
    # destructive, but nothing the person can see is deleted:
    "restore_knowledge_snapshot": "the current knowledge base is saved as a snapshot first; nothing is pruned",
    "deploy_to_device": "replaces files on a board the person trusts; nothing on this computer is deleted",
    "deploy_device_runtime": "the previous runtime on the board stays as the fallback",
    # deletes only with one strategy: that call carries a deletion plan in its facts (WP7), then the rule applies
    "resolve_healing": "only its docker_prune strategy deletes (the Docker build cache)",
}


def test_deleting_tool_must_be_person_framework_only_and_never_granted(registry, monkeypatch):
    registry.register(deleting())  # in NEVER_GRANT and NEVER_DELEGATE, framework window only, always asks
    broken = {
        "the client's form": deleting(channels=("framework", "elicitation")),
        "a standing approval list": deleting("remove_thing"),
        "a grant rule": deleting(grant=GrantRule(tool_rule("delete_artifact"), "delete")),
        "a delegation": deleting("resolve_healing"),  # never granted, but not in NEVER_DELEGATE
        "allow": spec("delete_artifact", ToolTier.LOCAL_WRITE, deletes=True, channels=("framework",)),
        "a cap per call": deleting(max_decision=lambda inp, who: Decision.ASK),
        "a read tool": spec("delete_artifact", ToolTier.READ, deletes=True, channels=("framework",),
                            max_decision=Decision.ASK),
    }
    for what, bad in broken.items():
        with pytest.raises(ValueError) as exc:
            ToolRegistry(PolicyStore(registry.policy_store.path.parent / what.replace(" ", "_"))).register(bad)
        assert "deletes something" in str(exc.value) or "never gets a standing approval" in str(exc.value), what
    monkeypatch.setitem(delegation_module.DELEGATION_SCOPE, "delete_artifact", ToolScope("delegable", group="x"))
    with pytest.raises(ValueError, match="delegation scope"):
        ToolRegistry(PolicyStore(registry.policy_store.path.parent / "scoped")).register(deleting())


def test_every_registered_tool_follows_the_deletion_rule(booted_framework):
    """The guard over every tool the framework registers (present and future): a tool that deletes is the person's
    alone, and a destructive tool either declares that it deletes or is reviewed here with its reason."""
    specs = booted_framework.tool_registry.list()
    deleting_tools = {s.name for s in specs if s.deletes}
    assert "delete_artifact" in deleting_tools, "the rule must hold for a real tool, not only in theory"
    for s in specs:
        if s.deletes:
            assert deletion_rule_problems(s) == [], s.name
            assert s.channels == ("framework",) and s.name in NEVER_GRANT and s.name in NEVER_DELEGATE
            assert s.grant is None and s.cap() == Decision.ASK
    unreviewed = sorted(s.name for s in specs if s.destructive and not s.deletes and s.name not in REVIEWED_DESTRUCTIVE)
    assert not unreviewed, f"destructive tools must declare deletes=True or be reviewed with a reason: {unreviewed}"


def test_a_deletion_is_confirmed_only_by_the_person_in_the_framework(registry):
    registry.register(deleting())
    allow_all(registry)  # "allow" is capped at ask: every deletion is asked
    assert registry.decide(registry.get("delete_artifact"), caller="ditto") == Decision.ASK
    shown = []
    registry.call("delete_artifact", {}, ToolContext("ditto", confirm=lambda s, i: shown.append(current_question())
                                                     or True))
    assert len(shown) == 1 and RAN == ["delete_artifact"], "Ditto's own dialog is the framework's window"

    # An accepted MCP form is the client's statement, never the person's confirmation of a deletion.
    with pytest.raises(ToolError) as exc:
        registry.call("delete_artifact", {}, ToolContext("mcp", confirm_channel="elicitation", answer="accepted"),
                      approved=True)
    assert exc.value.code == "needs_person" and RAN == ["delete_artifact"]

    # A confirm callback of another channel is not asked about a deletion.
    other = lambda s, i: True  # noqa: E731
    other.confirm_channel = "elicitation"
    with pytest.raises(ToolError):
        registry.call("delete_artifact", {"text": "y"}, ToolContext("ditto", confirm=other))
    assert RAN == ["delete_artifact"]

    # The person's decision on a pending approval in the framework counts.
    registry.call("delete_artifact", {"text": "z"},
                  ToolContext("mcp", confirm_channel="approval:ap_0123456789abcdef", answer="accepted"),
                  approved=True)
    assert RAN == ["delete_artifact", "delete_artifact"]


def test_a_call_whose_facts_carry_a_deletion_plan_is_the_persons_alone(registry):
    """Not only tools marked `deletes`: a call of any tool whose looked-up facts carry a deletion plan (a healing
    strategy that deletes) always asks, never in a client's form and never under a standing approval."""
    registry.register(spec("heal", prepare=lambda i, ctx: CallFacts("Deletes the build cache.", deletion=PLAN),
                           grant=GrantRule(tool_rule("heal"), "heal builds")))
    registry.register(spec("heal_plain", prepare=lambda i, ctx: CallFacts("Retries the build.")))
    allow_all(registry)
    facts = CallFacts("Deletes the build cache.", deletion=PLAN)
    assert registry.framework_channel_required(registry.get("heal"), "mcp", facts)
    assert not registry.framework_channel_required(registry.get("heal_plain"), "mcp", CallFacts("Retries."))
    ctx = ToolContext("ditto", facts=facts)
    assert registry.grant_offer(registry.get("heal"), In(), ctx) is None
    assert registry.matching_grant(registry.get("heal"), In(), ctx) is None
    shown = []
    registry.call("heal", {}, ToolContext("ditto", confirm=lambda s, i: shown.append(current_question()) or True))
    assert len(shown) == 1, "allowed, yet asked: the call deletes"
    assert "Would be deleted:" in shown[0] and "/srv/output/Rockchip/qwen/Q4_K_M" in shown[0]
    registry.call("heal_plain", {}, ToolContext("ditto", confirm=lambda s, i: shown.append(1) or True))
    assert len(shown) == 1 and RAN == ["heal", "heal_plain"]


def mcp_call(registry, tool, asked):
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry))

    async def go():
        async with Client(server, elicitation_callback=answering("accept", asked=asked)) as client:
            return await client.call_tool(tool, {"text": "x"})
    return asyncio.run(go())


def test_an_mcp_client_is_never_asked_about_a_deletion_in_its_own_form(registry, tmp_path):
    registry.register(deleting())
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"delete_artifact": Decision.ASK}))
    asked = []
    result = mcp_call(registry, "delete_artifact", asked)  # no pending approvals here: nobody can be asked
    assert result.is_error and asked == [] and RAN == []
    registry.attach_approvals(ApprovalService(ApprovalStore(tmp_path / "approvals"), JobService(), poll=3600.0))
    result = mcp_call(registry, "delete_artifact", asked)
    assert result.is_error and "Waiting for the person" in " ".join(getattr(c, "text", "") for c in result.content)
    assert asked == [] and RAN == []
    assert [r.tool for r in registry.approvals.store.pending()] == ["delete_artifact"]
