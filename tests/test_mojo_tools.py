"""The Mojo tools for AI clients (round 6 design 7.8 and 7.11; round 7 item 11').

run_mojo_oracle only reads and gives an AI labels, never host paths; run_mojo_miracle asks the person every time,
shows the exact plan (the full path only in the framework's own window), applies exactly that plan or nothing, never
deletes, is never delegated and refuses everything but a harmless repair before any question; run_doctor stays
callable but hidden, and a setting for either name applies to both (the stricter wins).
"""

import os
import re
import stat
from pathlib import Path

import pytest

from orchestrator.Core.mojo import Finding, Severity, runner
from orchestrator.Core.mojo.checks import check_user_state
from orchestrator.tools.confirmation import MAX_ELICITATION, MAX_FRAMEWORK
from orchestrator.tools.policy import (TOOL_RENAMES, CallerPolicy, Decision, ToolPolicy, ToolTier,
                                       renamed_names)
from orchestrator.tools.registry import ToolContext, ToolError, current_question

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
ABSOLUTE = re.compile(r"(?:^|[\s('\"=])(?:/|~/|[A-Za-z]:\\)[^\s'\")]+")
OTHERS = {"disk.cache": "deletion_list", "tools.audit": "person", "docker.daemon": "admin",
          "data.calibration": "not_automatic"}


@pytest.fixture
def open_state(booted_framework, monkeypatch):
    """The state folder open to other users (ConfigManager.user_state_dir would close it on every read, next-fixes
    118), and one problem of each reason Mojo Miracle does not repair."""
    state = Path(booted_framework.config.user_state_dir)
    monkeypatch.setattr(type(booted_framework.config), "user_state_dir", property(lambda self: state))
    os.chmod(state, 0o755)

    def others(_fw):
        return [Finding("x", code, Severity.ERROR, f"problem {code} in {state / 'cache'}", "a hint",
                        where=str(state / "cache"), why="it matters", cannot=cannot)
                for code, cannot in OTHERS.items()]
    monkeypatch.setattr(runner, "DEFAULT_CHECKS", [check_user_state, others])
    return state


def mode(path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


def texts_of(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from texts_of(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from texts_of(v)
    elif isinstance(value, str):
        yield value


@POSIX
def test_oracle_tool_reads_only_and_gives_labels_never_paths(booted_framework, open_state):
    registry = booted_framework.tool_registry
    assert registry.get("run_mojo_oracle").annotations()["readOnlyHint"] is True
    out = registry.call("run_mojo_oracle", {}, ToolContext("mcp"))
    for text in texts_of(out.model_dump()):
        assert str(open_state) not in text and str(Path.home()) not in text, text
        assert not ABSOLUTE.search(text), text
    found = {f.code: f for f in out.findings}
    state = found["state.permissions"]
    assert state.where == "the state folder" and state.why and state.repair.how == "miracle"
    assert 'run_mojo_miracle {"code": "state.permissions"}' in state.repair.next
    assert {code: found[code].repair.how for code in OTHERS} == OTHERS
    assert found["disk.cache"].where == "state:cache"
    assert "No tool deletes" in found["disk.cache"].repair.next
    assert all(f.repair is None for f in out.findings if f.severity in ("ok", "info"))
    assert out.summary.startswith("Mojo Oracle found 5 problems (4 errors, 1 warning): Mojo Miracle can repair 1")
    assert "need the person" not in out.summary or "needs the person" in out.summary
    assert mode(open_state) == 0o755, "Mojo Oracle only looks"


@POSIX
def test_miracle_tool_asks_with_the_plan_and_repairs_exactly_it(booted_framework, open_state):
    registry = booted_framework.tool_registry
    spec = registry.get("run_mojo_miracle")
    asked = []
    out = registry.call("run_mojo_miracle", {"code": "state.permissions"},
                        ToolContext("mcp", confirm=lambda s, i: asked.append(current_question()) or True))
    assert len(asked) == 1, "every call is asked"
    question = asked[0]
    assert "Mojo Miracle repairs state.permissions: only you can open the state folder then" in question
    assert "the state folder: 0755 -> 0700" in question and "Deletes: nothing" in question
    assert str(open_state) in question, "the framework's own window shows the person the full path"
    assert out.outcome == "repaired" and out.repaired is True and mode(open_state) == 0o700
    assert "only you can open the state folder" in out.what and out.reason == ""
    # A client's form (an MCP elicitation, which an AI may read) never carries the path.
    os.chmod(open_state, 0o755)
    inp = registry.validate_input(spec, {"code": "state.permissions"})
    facts = registry.look_up(spec, inp, ToolContext("mcp"))
    client_form = registry.question_text(spec, {"code": "state.permissions"}, ToolContext("mcp"), facts,
                                         MAX_ELICITATION)
    assert str(open_state) not in client_form and "the state folder: 0755 -> 0700" in client_form
    assert str(open_state) in registry.question_text(spec, {"code": "state.permissions"}, ToolContext("mcp"),
                                                     facts, MAX_FRAMEWORK)
    assert facts.scope == {"repair_class": "delegable"} and len(facts.bound["plan_sha256"]) == 64


@POSIX
def test_miracle_tool_refuses_every_other_verdict_before_any_question(booted_framework, open_state):
    registry = booted_framework.tool_registry
    for code, how in OTHERS.items():
        asked = []
        with pytest.raises(ToolError) as e:
            registry.call("run_mojo_miracle", {"code": code},
                          ToolContext("mcp", confirm=lambda s, i: asked.append(1) or True))
        assert asked == [], f"{code}: refused before any question"
        assert e.value.code == ("not_supported" if how == "not_automatic" else "needs_person"), code
        assert "Nothing was changed" in e.value.message and str(open_state) not in e.value.message
    with pytest.raises(ToolError) as e:
        registry.call("run_mojo_miracle", {"code": "python.version"}, ToolContext("mcp", confirm=lambda s, i: True))
    assert e.value.code == "missing_information" and "nothing to repair" in e.value.message
    assert mode(open_state) == 0o755


@POSIX
def test_miracle_tool_changes_nothing_when_the_problem_changed_during_the_question(booted_framework, open_state):
    registry = booted_framework.tool_registry

    def answer(_spec, _inp):
        os.chmod(open_state, 0o750)   # someone changed the folder while the person read the plan
        return True
    out = registry.call("run_mojo_miracle", {"code": "state.permissions"}, ToolContext("mcp", confirm=answer))
    assert out.outcome == "plan_changed" and out.repaired is False and "nothing was changed" in out.reason
    assert mode(open_state) == 0o750


def test_miracle_tool_is_never_standing_delegated_or_deleting(booted_framework):
    from orchestrator.tools import delegation, facts_catalog
    registry = booted_framework.tool_registry
    spec = registry.get("run_mojo_miracle")
    assert spec.grant is None, "no standing approval"
    assert spec.cap() == Decision.ASK and spec.deletes is False and spec.tier == ToolTier.LOCAL_WRITE
    assert facts_catalog.TOOL_FACTS["run_mojo_miracle"].delegable == "never"
    assert delegation.DELEGATION_SCOPE["run_mojo_miracle"].who == "person"
    # even a person's "allow" for the tool keeps every call asked
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"run_mojo_miracle": Decision.ALLOW}))
    assert registry.decide(spec, caller="mcp") == Decision.ASK
    source = Path(__file__).resolve().parent.parent / "orchestrator" / "tools" / "tools_mojo.py"
    text = source.read_text(encoding="utf-8")
    for deleting in ("docker_objects", "delete_framework_images", "delete_snapshots", "Trash", "accept(", "rmtree",
                     "unlink"):
        assert deleting not in text, deleting


def test_run_doctor_is_hidden_but_callable(booted_framework):
    registry = booted_framework.tool_registry
    rows = {r["tool"]: r for r in registry.permissions("mcp")}
    assert rows["run_doctor"]["listed"] is False and rows["run_mojo_oracle"]["listed"] is True
    assert rows["run_mojo_miracle"]["listed"] is True
    assert "run_doctor" not in {s.name for s in registry.visible("mcp")}
    out = registry.call("run_doctor", {}, ToolContext("mcp"))
    assert any(f.code == "python.version" for f in out.findings) and not hasattr(out, "summary")


def test_a_setting_for_either_name_applies_to_both_and_the_stricter_wins(booted_framework):
    assert TOOL_RENAMES == {"run_doctor": "run_mojo_oracle"}
    assert renamed_names("run_doctor") == ("run_mojo_oracle",) and renamed_names("run_mojo_oracle") == ("run_doctor",)
    registry = booted_framework.tool_registry
    oracle, doctor = registry.get("run_mojo_oracle"), registry.get("run_doctor")
    store = registry.policy_store
    store.replace(ToolPolicy(configured=True, overrides={"run_doctor": Decision.DENY}))
    assert registry.decide(oracle, caller="mcp") == Decision.DENY, "a deny of the old name closes the new one"
    store.replace(ToolPolicy(configured=True, callers={"ditto": CallerPolicy(
        overrides={"run_mojo_oracle": Decision.DENY})}))
    assert registry.decide(doctor, caller="ditto") == Decision.DENY, "and a deny of the new name the old one"
    assert registry.decide(doctor, caller="mcp") == Decision.ALLOW
    store.replace(ToolPolicy(configured=True, tiers={ToolTier.READ: Decision.ASK},
                             overrides={"run_doctor": Decision.ALLOW}))
    assert registry.decide(oracle, caller="mcp") == Decision.ASK, "an allow of the old name opens nothing more"
    # a permission file of 3.0.0 reviewed run_doctor: run_mojo_oracle counts as reviewed under it
    assert ToolPolicy(configured=True).reviewed_tool(None, "run_mojo_oracle")
    assert not ToolPolicy(configured=True, reviewed=[]).reviewed_tool(None, "run_mojo_oracle")


def test_the_audit_log_takes_only_well_formed_repair_fields():
    from orchestrator.tools.audit import AuditLog, confirm_channel_ok
    good = {"finding_code": "targets.Rockchip", "repair_id": "state.tighten", "plan_sha256": "a" * 64,
            "repair_class": "delegable"}
    assert AuditLog._extra("ok", good)
    for key, bad in (("finding_code", "a b"), ("repair_id", "State"), ("plan_sha256", "x"),
                     ("repair_class", "harmless")):
        with pytest.raises(ValueError):
            AuditLog._extra("ok", {**good, key: bad})
    assert confirm_channel_ok("elicitation") and not confirm_channel_ok("elicitation\n")


def test_the_check_installation_prompt_leads_to_the_mojo_tools(booted_framework):
    registry = booted_framework.tool_registry
    prompts = {p.name: p for p in registry.prompts("mcp")}
    spec = prompts["check_installation"]
    assert set(spec.tools) == {"run_mojo_oracle", "run_mojo_miracle"}
    assert "Never say a problem is repaired unless run_mojo_miracle returned the outcome 'repaired'" in \
        spec.instructions
    assert "run_mojo_oracle" in prompts["ask_about_framework"].tools
    assert "run_doctor" not in prompts["ask_about_framework"].instructions


@POSIX
def test_over_mcp_run_doctor_is_unlisted_but_answers_and_miracle_asks_without_a_host_path(booted_framework,
                                                                                          open_state):
    """The real MCP path (round 6 design A-M1): a client of 3.0.0 that calls run_doctor by name still gets its answer
    (audited under that name) although the tool is not listed; run_mojo_miracle asks the client's person once, and
    neither the question nor any result carries a host path."""
    pytest.importorskip("mcp")
    import asyncio
    import json

    from mcp import Client
    from mcp_types import ElicitResult

    from orchestrator import mcp_server
    server = mcp_server.build_mcp_server(booted_framework, events=False)
    asked = []

    async def accept(_ctx, params):
        asked.append(params.message)
        return ElicitResult(action="accept", content={"approve": True})

    async def go():
        async with Client(server, elicitation_callback=accept) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            doctor = await client.call_tool("run_doctor", {})
            oracle = await client.call_tool("run_mojo_oracle", {})
            miracle = await client.call_tool("run_mojo_miracle", {"code": "state.permissions"})
            return names, doctor, oracle, miracle
    names, doctor, oracle, miracle = asyncio.run(go())
    assert "run_doctor" not in names and {"run_mojo_oracle", "run_mojo_miracle"} <= names
    assert not doctor.is_error and {f["code"] for f in doctor.structured_content["findings"]} >= {"state.permissions"}
    assert not miracle.is_error and miracle.structured_content["outcome"] == "repaired" and mode(open_state) == 0o700
    assert len(asked) == 1 and "the state folder: 0755 -> 0700" in asked[0]
    for text in [*asked, json.dumps(doctor.structured_content), json.dumps(oracle.structured_content),
                 json.dumps(miracle.structured_content)]:
        assert str(open_state) not in text and str(Path.home()) not in text
    tools = [row["tool"] for row in booted_framework.tool_registry.audit.entries(50)]
    assert "run_doctor" in tools and "run_mojo_miracle" in tools and "mojo" in tools, "every call is audited"

