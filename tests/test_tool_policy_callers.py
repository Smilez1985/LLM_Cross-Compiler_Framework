"""ToolSpec declarations and per-caller permissions (MCP design S3, gaps G9 in part, G10, G20, G21, G28).

A tool's own default and its cap come before and after the caller's settings; tools the person has not reviewed
never inherit a loosened tier decision; Ditto, local stdio clients and named tokens have their own permissions;
denied tools are not listed but explained in get_status; annotations follow the declared flags.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
from click.testing import CliRunner  # noqa: E402
from mcp import Client  # noqa: E402

from orchestrator import cli as cli_module  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.Core.ditto_manager import DittoCoder  # noqa: E402
from orchestrator.tools import framework_tools  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.mcp_tokens import TokenStore  # noqa: E402
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import (CallerPolicy, Decision, PolicyStore, ToolPolicy, ToolTier,  # noqa: E402
                                       restrictive_policy)
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec  # noqa: E402
from orchestrator.tools.spec import FieldMeta, ToolLimits  # noqa: E402
from test_mcp_server import answering  # noqa: E402


class In(BaseModel):
    text: str = "x"


class Out(ToolOutput):
    echoed: str = ""


def spec(name, tier=ToolTier.LOCAL_WRITE, **fields):
    return ToolSpec(name, name.replace("_", " ").title(), f"The {name} tool", tier, In, Out,
                    lambda i: Out(echoed=name), **fields)


@pytest.fixture
def registry(tmp_path):
    # Named tokens come from the token store (S12); "laptop" is one whose permissions the person has not set yet.
    tokens = TokenStore(tmp_path)
    tokens.add("laptop")
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"), tokens=tokens)
    for s in (spec("read_thing", ToolTier.READ, idempotent=True), spec("start_build"), spec("export_pack"),
              spec("deploy_to_device", ToolTier.EXTERNAL, destructive=False, open_world=True),
              spec("delete_artifact", ToolTier.EXTERNAL, destructive=True, default_decision=Decision.DENY),
              spec("capped_write", max_decision=Decision.ASK)):
        reg.register(s)
    return reg


def save(registry, **fields):
    registry.policy_store.save(ToolPolicy(configured=True, **fields))


def decision(registry, name, caller="mcp", inp=None):
    return registry.decide(registry.get(name), inp, caller)


def listed(registry, caller="mcp"):
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry),
                                         caller=caller)

    async def go():
        async with Client(server) as client:
            return {t.name: t for t in (await client.list_tools()).tools}
    return asyncio.run(go())


# --- the tool's default and cap -------------------------------------------------------------------------------

class TestDefaultsAndCaps:

    def test_an_override_above_the_cap_is_capped(self, registry):
        save(registry, overrides={"deploy_to_device": Decision.ALLOW, "capped_write": Decision.ALLOW})
        assert decision(registry, "deploy_to_device") == Decision.ASK, "external tools always ask"
        assert decision(registry, "capped_write") == Decision.ASK

    def test_a_capped_override_still_asks_over_mcp(self, registry):
        save(registry, overrides={"deploy_to_device": Decision.ALLOW})
        server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                             tool_registry=registry))
        asked = []

        async def go():
            async with Client(server, elicitation_callback=answering("accept", asked=asked)) as client:
                return await client.call_tool("deploy_to_device", {"text": "x"})
        assert not asyncio.run(go()).is_error and len(asked) == 1

    def test_a_tier_allow_lifts_no_tool_over_its_cap_or_its_default_deny(self, registry):
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
             reviewed=["start_build", "export_pack", "capped_write"])
        assert decision(registry, "capped_write") == Decision.ASK
        assert decision(registry, "start_build") == Decision.ALLOW
        assert decision(registry, "delete_artifact") == Decision.DENY

    def test_a_default_never_opens_a_tier_the_person_denied(self, registry):
        registry.register(spec("cancel_own", default_decision=lambda inp, caller: Decision.ALLOW))
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.DENY})
        assert decision(registry, "cancel_own") == Decision.DENY
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ASK})
        assert decision(registry, "cancel_own") == Decision.ALLOW

    def test_a_callable_default_sees_the_input_and_the_caller(self, registry):
        seen = []

        def own(inp, caller):
            seen.append((getattr(inp, "text", None), caller))
            return Decision.ALLOW if inp is not None and inp.text == "mine" else Decision.ASK
        registry.register(spec("cancel_job", default_decision=own))
        registry.call("cancel_job", {"text": "mine"}, ToolContext("ditto"))
        with pytest.raises(ToolError, match="confirmation"):
            registry.call("cancel_job", {"text": "theirs"}, ToolContext("ditto"))
        assert ("mine", "ditto") in seen and ("theirs", "ditto") in seen

    def test_a_callable_default_gets_the_owner_of_the_call(self, registry):
        seen = []
        registry.register(spec("cancel_mine", default_decision=lambda inp, who: seen.append(who)))
        registry.decide(registry.get("cancel_mine"), None, ToolContext("mcp", connection="k1"))
        registry.decide(registry.get("cancel_mine"), None, "mcp")
        assert seen == ["mcp#k1", "mcp"]

    def test_a_failing_rule_is_never_looser(self, registry):
        def broken(inp, who):
            raise RuntimeError("lookup failed")
        registry.register(spec("broken_default", default_decision=broken))
        registry.register(spec("broken_cap", max_decision=broken))
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
             overrides={"broken_cap": Decision.ALLOW}, reviewed=["broken_default", "broken_cap"])
        assert decision(registry, "broken_default") == Decision.ASK
        assert decision(registry, "broken_cap") == Decision.DENY

    def test_a_damaged_policy_denies_tools_with_an_allowing_default(self, registry):
        registry.register(spec("cancel_own", default_decision=Decision.ALLOW))
        registry.policy_store.path.write_text("{broken")
        assert decision(registry, "cancel_own") == Decision.DENY
        assert restrictive_policy().decide_tool(registry.get("read_thing"), None, "mcp:laptop") == Decision.DENY

    def test_the_result_limit_is_the_tools_own(self, registry):
        registry.register(ToolSpec("small", "Small", "", ToolTier.READ, In, Out, lambda i: Out(echoed="y" * 5000),
                                   limits=ToolLimits(max_result_bytes=2048)))
        with pytest.raises(ToolError, match="more than the 2 KiB"):
            registry.call("small", {}, ToolContext("mcp"))


# --- tools the person has not reviewed (G28) ------------------------------------------------------------------

class TestReviewedTools:

    def test_a_tool_missing_from_the_reviewed_list_gets_its_own_default(self, registry):
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}, reviewed=["start_build"])
        assert decision(registry, "start_build") == Decision.ALLOW
        assert decision(registry, "export_pack") == Decision.ASK, "a new tool never inherits the loosened tier"

    def test_a_tighter_tier_still_applies_to_a_new_tool(self, registry):
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.DENY}, reviewed=[])
        assert decision(registry, "export_pack") == Decision.DENY

    def test_a_300_policy_file_counts_the_300_tools_as_reviewed(self, registry):
        registry.policy_store.path.write_text(json.dumps(
            {"version": 1, "configured": True, "tiers": {"local_write": "allow"}}))
        assert decision(registry, "start_build") == Decision.ALLOW, "existed when the person chose the tier"
        assert decision(registry, "export_pack") == Decision.ASK, "added later"

    def test_permissions_mark_new_tools_and_say_why(self, registry):
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}, reviewed=["start_build"])
        rows = {r["tool"]: r for r in registry.permissions("mcp")}
        assert rows["export_pack"]["new"] and not rows["start_build"]["new"]
        assert "new tool" in rows["export_pack"]["why"] and rows["delete_artifact"]["default"] == "deny"
        assert rows["deploy_to_device"]["max"] == "ask" and rows["start_build"]["max"] == "allow"


# --- per caller (G9 in part) -----------------------------------------------------------------------------------

class TestCallers:

    def test_per_caller_decisions_differ(self, registry):
        save(registry, callers={"ditto": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
                                                      reviewed=["start_build"]),
                                "mcp": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.DENY})})
        assert decision(registry, "start_build", "ditto") == Decision.ALLOW
        assert decision(registry, "start_build", "mcp") == Decision.DENY
        assert decision(registry, "start_build", "gui") == Decision.ASK, "the shared permissions"
        assert "start_build" not in listed(registry, "mcp") and "start_build" in listed(registry, "ditto")

    def test_the_actor_of_a_call_picks_its_permissions(self, registry):
        save(registry, callers={"mcp": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.DENY})})
        with pytest.raises(ToolError, match="not allowed"):
            registry.call("start_build", {}, ToolContext("mcp"))
        assert registry.call("start_build", {}, ToolContext("ditto", confirm=lambda s, i: True)).echoed

    def test_a_named_token_without_permissions_may_only_read(self, registry):
        assert decision(registry, "read_thing", "mcp:laptop") == Decision.ALLOW
        assert decision(registry, "start_build", "mcp:laptop") == Decision.DENY
        assert set(listed(registry, "mcp:laptop")) == {"read_thing"}

    def test_a_new_token_never_reads_what_a_default_or_the_shared_setting_denies(self, registry):
        """Review fix: a token without permissions of its own read every read tool by the shared read tier,
        ignoring an optional read tool's own default ("deny", hidden) and your shared setting for a single tool."""
        registry.register(spec("optional_read", ToolTier.READ, default_decision=Decision.DENY))
        registry.register(spec("private_read", ToolTier.READ))
        save(registry, overrides={"private_read": Decision.DENY})
        assert decision(registry, "optional_read", "mcp:laptop") == Decision.DENY
        assert decision(registry, "private_read", "mcp:laptop") == Decision.DENY
        assert decision(registry, "read_thing", "mcp:laptop") == Decision.ALLOW
        assert set(listed(registry, "mcp:laptop")) == {"read_thing"}

    @pytest.mark.parametrize("bad", ["root", "mcp:", "mcp:Laptop!", "ditto:x"])
    def test_unknown_callers_make_the_file_invalid(self, registry, bad):
        with pytest.raises(ValueError):
            ToolPolicy(callers={bad: CallerPolicy()})

    def test_ditto_and_an_mcp_client_list_the_same_tools_for_the_same_policy(self, registry):
        save(registry, overrides={"export_pack": Decision.DENY})
        ditto = {t.name for t in DittoCoder._tool_specs(SimpleNamespace(logger=None), registry)}
        assert ditto == set(listed(registry, "mcp")) == {s.name for s in registry.visible("ditto")}
        assert "export_pack" not in ditto and "delete_artifact" not in ditto


# --- annotations and listing (G20, G21) -----------------------------------------------------------------------

class TestListing:

    def test_annotations_follow_the_flags(self, registry):
        save(registry, overrides={"delete_artifact": Decision.ASK})
        tools = listed(registry)
        assert tools["read_thing"].annotations.read_only_hint and tools["read_thing"].annotations.idempotent_hint
        assert tools["delete_artifact"].annotations.destructive_hint is True
        assert tools["deploy_to_device"].annotations.destructive_hint is False, "external is not destructive"
        assert tools["deploy_to_device"].annotations.open_world_hint is True
        assert tools["deploy_to_device"].meta == {"anthropic/requiresUserInteraction": True}
        assert not tools["start_build"].meta

    def test_denied_tools_are_hidden_but_still_refused_and_audited(self, registry):
        save(registry, overrides={"start_build": Decision.DENY})
        assert "start_build" not in listed(registry) and "delete_artifact" not in listed(registry)
        server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                             tool_registry=registry))

        async def go():
            async with Client(server) as client:
                return await client.call_tool("start_build", {})
        assert asyncio.run(go()).is_error
        assert registry.audit.entries(1)[0]["outcome"] == "denied"

    def test_get_status_explains_a_denied_tool(self, tmp_path):
        reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
        framework = SimpleNamespace(build_service=SimpleNamespace(start_build=None), info=SimpleNamespace(version="t"),
                                    tool_registry=reg)
        framework_tools.register_framework_tools(reg, framework)
        save(reg, overrides={"start_build": Decision.DENY})
        server = mcp_server.build_mcp_server(framework)

        async def go():
            async with Client(server) as client:
                names = {t.name for t in (await client.list_tools()).tools}
                return names, await client.call_tool("get_status", {})
        names, result = asyncio.run(go())
        assert "start_build" not in names and "get_status" in names
        permissions = result.structured_content["permissions"]
        row = next(t for t in permissions["tools"] if t["tool"] == "start_build")
        assert (row["decision"], row["listed"], row["why"]) == ("deny", False, "your setting for this tool")
        assert permissions["caller"] == "mcp" and permissions["listed"] == len(names)
        actions = {a["action"] for a in permissions["no_tool_actions"]}
        assert {"sign_in", "change_permissions", "trust_device"} <= actions


# --- declarations are checked at registration ----------------------------------------------------------------

class TestRegistration:

    def test_aliases_and_metadata_must_name_fields(self, registry):
        with pytest.raises(ValueError, match="unknown fields"):
            registry.register(spec("bad_alias", arg_aliases={"txt": "missing"}))
        with pytest.raises(ValueError, match="unknown fields"):
            registry.register(spec("bad_meta", fields={"missing": FieldMeta()}))
        with pytest.raises(ValueError, match="own fields"):
            registry.register(spec("alias_clash", arg_aliases={"text": "text"}))

    def test_example_calls_must_be_valid(self, registry):
        with pytest.raises(ValueError, match="invalid example"):
            registry.register(spec("bad_example", examples=({"text": 5},)))
        registry.register(spec("good_example", examples=({"text": "hello"},)))

    def test_unknown_channels_are_refused(self, registry):
        with pytest.raises(ValueError, match="channels"):
            registry.register(spec("bad_channel", channels=("email",)))


# --- CLI and GUI ----------------------------------------------------------------------------------------------

def invoke(registry, *args, input=None):
    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self
    return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), input=input)


class TestCli:

    def test_permissions_for_one_caller_keep_the_shared_ones(self, registry):
        save(registry, overrides={"export_pack": Decision.DENY})
        result = invoke(registry, "tools", "permissions", "--caller", "mcp", input="allow\nallow\nask\n")
        assert result.exit_code == 0, result.output
        policy = registry.policy()
        section = policy.callers["mcp"]
        assert section.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW
        assert set(section.reviewed) == {s.name for s in registry.list()}
        assert section.overrides == {"export_pack": Decision.DENY}, "a new section never loosens a tool setting"
        assert policy.tiers[ToolTier.LOCAL_WRITE] == Decision.ASK, "the shared permissions are unchanged"
        assert decision(registry, "export_pack", "mcp") == Decision.DENY

    def test_reset_of_one_caller_returns_it_to_the_shared_permissions(self, registry):
        save(registry, callers={"mcp": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.DENY})})
        assert invoke(registry, "tools", "permissions", "--caller", "mcp", "--reset").exit_code == 0
        assert registry.policy().callers == {} and decision(registry, "start_build", "mcp") == Decision.ASK

    def test_shared_permissions_mark_every_tool_reviewed(self, registry):
        save(registry, reviewed=[])
        assert "export_pack" in invoke(registry, "tools", "permissions", input="allow\nallow\nask\n").output
        assert set(registry.policy().reviewed) == {s.name for s in registry.list()}
        assert decision(registry, "export_pack") == Decision.ALLOW

    def test_the_setup_shows_default_cap_new_and_standing_approval_per_tool(self, registry):
        """Review fix: `tools permissions` (and `ditto setup`) only named the new tools; design S3 and the changelog
        say the setup shows each tool's default, cap, whether it is new and whether a standing approval may be
        offered, before the person decides."""
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}, reviewed=["start_build"])
        out = " ".join(invoke(registry, "tools", "permissions", input="allow\nallow\nask\n").output.split())
        assert "delete_artifact (external): default deny, at most ask, new, no standing approval" in out
        assert "start_build (local_write): default tier setting, at most allow, no standing approval" in out

    def test_list_shows_default_cap_and_new(self, registry):
        save(registry, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}, reviewed=["start_build"])
        out = " ".join(invoke(registry, "tools", "list").output.split())
        assert "at most: ask" in out and "default: deny" in out and "new" in out
        assert "no standing approval" in out and "use their own default" in out


@pytest.mark.usefixtures("qapp")
def test_gui_dialog_keeps_caller_permissions_and_marks_tools_reviewed(registry):
    from orchestrator.gui.dialogs import ToolPermissionsDialog
    save(registry, reviewed=[], callers={"mcp": CallerPolicy(tiers={ToolTier.LOCAL_WRITE: Decision.DENY})})
    dialog = ToolPermissionsDialog(registry)
    assert dialog.table.item(registry.list().index(registry.get("export_pack")), 3).text() == "new"
    # Review fix: the dialog also says whether a confirmation may offer a standing approval (design S3).
    facts = dialog.table.item(registry.list().index(registry.get("delete_artifact")), 2).text()
    assert facts == "deny / ask / no standing approval"
    dialog.save()
    policy = registry.policy()
    assert policy.callers["mcp"].tiers[ToolTier.LOCAL_WRITE] == Decision.DENY
    assert set(policy.reviewed) == {s.name for s in registry.list()}
