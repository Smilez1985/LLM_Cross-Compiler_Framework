"""Standing approvals: "yes, and do not ask again" (MCP design S9a, section 6.10; maintainer decisions rounds 2-4).

Created only by a person in the framework's own window; the scope is computed from facts the framework looked up;
never for the tools of the design's "never" list; packs only for knowledge and recipe packs (both trust levels) and
verified target packs; for the shared stdio caller only the narrowest scope with a warning and a shorter expiry;
listed and revocable in Tool Permissions; lapse after 90 days without use; uses come from the audit log; saved with
revision and lock.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
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
from orchestrator.tools.grants import (BUILD_RULE, DOWNLOAD_RULE, NEVER_GRANT, PACK_RULE, GrantChoice,  # noqa: E402
                                       hold_gui_lock, tool_rule)
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError, ToolSpec  # noqa: E402
from orchestrator.tools.spec import GrantRule  # noqa: E402
from pydantic import BaseModel  # noqa: E402

KEY1, KEY2 = "a1" * 16, "b2" * 16  # index key fingerprints (test values)
INDEX = {
    "know-v": {"kind": "knowledge", "trust": "verified", "signing_key": KEY1, "source": "index"},
    "know-v2": {"kind": "knowledge", "trust": "verified", "signing_key": KEY1, "source": "index"},
    "know-c": {"kind": "knowledge", "trust": "community", "signing_key": KEY1, "source": "index"},
    "know-k2": {"kind": "knowledge", "trust": "verified", "signing_key": KEY2, "source": "index"},
    "recipe-v": {"kind": "recipe", "trust": "verified", "signing_key": KEY1, "source": "index"},
    "target-v": {"kind": "target", "trust": "verified", "signing_key": KEY1, "source": "index"},
    "target-c": {"kind": "target", "trust": "community", "signing_key": KEY1, "source": "index"},
    "revoked": {"kind": "knowledge", "trust": "revoked", "signing_key": KEY1, "source": "index"},
}
APPROVAL_RE = re.compile(r"ap_[0-9a-f]{16}")


class PackIn(BaseModel):
    pack_id: str


class PackOut(ToolOutput):
    installed: str = ""


def pack_facts(inp, ctx):
    """Stands in for the framework's look-up in the signed index (the AI's words never enter the scope)."""
    return dict(INDEX[inp.pack_id])


@pytest.fixture
def env(make_framework):
    fm = make_framework()
    registry = fm.tool_registry
    registry.approvals.poll = 0.05
    installed = []
    registry.register(ToolSpec(
        "add_pack_from_index", "Install a pack", "Installs a pack from the signed index", ToolTier.EXTERNAL, PackIn,
        PackOut, lambda i: installed.append(i.pack_id) or PackOut(installed=i.pack_id), open_world=True,
        grant=GrantRule(rule=PACK_RULE, facts=pack_facts,
                        warning="install packs from the signed index within this scope")))
    # probe_local_hardware is the framework's own tool now (design T5), with a standing approval scoped to the tool.
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return SimpleNamespace(fm=fm, registry=registry, store=registry.approvals.store, installed=installed,
                           server=mcp_server.build_mcp_server(fm), state=registry.policy_store.path.parent)


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


def accept(asked, stop_asking=False):
    async def callback(ctx, params):
        asked.append(params)
        content = {"approve": True}
        if stop_asking:
            content["stop_asking"] = True
        return ElicitResult(action="accept", content=content)
    return callback


def mcp_calls(env, calls, callback=None):
    async def go():
        async with Client(env.server, elicitation_callback=callback) as client:
            return [await client.call_tool(name, args) for name, args in calls]
    return run(go())


def cli(registry, *args, input=None, terminal=True):
    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(tool_registry=registry)
            return self
    with mock.patch.object(cli_module, "_interactive", lambda: terminal):
        return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), input=input)


def flat(text):
    return " ".join(text.split())


def grants(registry, caller=None):
    stored = registry.policy().grants
    return [g for c, gs in stored.items() for g in gs if caller is None or c == caller]


def grant_for(env, pack="know-v", caller="mcp:laptop", width="this", duration="revoke", via="gui"):
    """A standing approval as the person creates it in the framework's window, for a named token."""
    if caller.startswith("mcp:") and env.registry.tokens.get(caller[4:]) is None:
        env.registry.tokens.add(caller[4:])
        env.registry.policy_store.update(lambda p: p.model_copy(update={"callers": {
            **p.callers, caller: p.callers.get(caller) or __import__("orchestrator.tools.policy", fromlist=["x"])
            .CallerPolicy(reviewed=[s.name for s in env.registry.list()])}}))
    spec = env.registry.get("add_pack_from_index")
    offer = env.registry.grant_offer(spec, PackIn(pack_id=pack), ToolContext(caller))
    assert offer is not None
    return env.registry.create_grant(offer, width, duration, via, "gui" if via in ("gui", "ditto-gui") else "cli")


def call_as(env, caller, pack, confirm=None, pending=False):
    return env.registry.call("add_pack_from_index", {"pack_id": pack},
                             ToolContext(caller, confirm=confirm, connection="c", pending_approvals=pending))


# --- only a person, only in the framework's window ----------------------------------------------------------------

class TestOnlyThePersonCreatesThem:

    @pytest.mark.parametrize("flag", [False, True])
    def test_an_elicitation_never_creates_a_standing_approval(self, env, flag):
        asked = []
        results = mcp_calls(env, [("add_pack_from_index", {"pack_id": "know-v"})] * 2, accept(asked, flag))
        assert all(not r.is_error for r in results) and env.installed == ["know-v", "know-v"]
        assert len(asked) == 2, "the second call is asked again: nothing was approved for good"
        assert grants(env.registry) == []
        assert ("stop_asking" in asked[0].requested_schema["properties"]) is True
        requests = [r for r in env.store.list() if r.kind == "grant_request"]
        assert len(requests) == (1 if flag else 0), "the flag only asks the framework, once per caller and tool"

    def test_the_form_has_no_flag_for_a_tool_without_a_grant_rule(self, env):
        from orchestrator.tools.output import ToolOutput as _Out

        class Note(BaseModel):
            text: str = "x"
        env.registry.register(ToolSpec("plain_write", "Plain", "No grant", ToolTier.LOCAL_WRITE, Note, _Out,
                                       lambda i: _Out()))
        env.registry.policy_store.update(lambda p: p.model_copy(update={"reviewed": [s.name for s in
                                                                                     env.registry.list()]}))
        asked = []
        server = mcp_server.build_mcp_server(env.fm)

        async def go():
            async with Client(server, elicitation_callback=accept(asked)) as client:
                return await client.call_tool("plain_write", {})
        assert not run(go()).is_error
        assert "stop_asking" not in asked[0].requested_schema["properties"]

    def test_a_declined_request_is_not_asked_again_for_24_hours(self, env):
        asked = []

        def requests():
            return [r for r in env.store.list() if r.kind == "grant_request"]

        async def go():
            async with Client(env.server, elicitation_callback=accept(asked, True)) as client:
                await client.call_tool("add_pack_from_index", {"pack_id": "know-v"})
                request = requests()[0]
                declined = await asyncio.to_thread(cli, env.registry, "tools", "approvals", "decline", request.id,
                                                   input="y\n")
                assert declined.exit_code == 0, declined.output
                await client.call_tool("add_pack_from_index", {"pack_id": "know-v"})
                assert [r.state for r in requests()] == ["declined"], "not asked again within 24 hours"
                past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
                env.store.update(request.id, decided_at=past)
                await client.call_tool("add_pack_from_index", {"pack_id": "know-v"})
                assert "pending" in [r.state for r in requests()], "after 24 hours it may be asked again"
        run(go())
        assert "withdrawn" in [r.state for r in requests()], "a request ends with the session"

    def test_a_request_confirmed_in_the_window_becomes_the_standing_approval(self, env):
        asked = []

        async def go():
            async with Client(env.server, elicitation_callback=accept(asked, True)) as client:
                await client.call_tool("add_pack_from_index", {"pack_id": "know-v"})
                request = next(r for r in env.store.list() if r.kind == "grant_request")
                approved = await asyncio.to_thread(cli, env.registry, "tools", "approvals", "approve", request.id,
                                                   input="y\n")
                assert approved.exit_code == 0, approved.output
                return await client.call_tool("add_pack_from_index", {"pack_id": "know-v2"})
        second = run(go())
        assert not second.is_error and len(asked) == 1, "inside the scope it no longer asks"
        (grant,) = grants(env.registry, "mcp")
        assert grant.created_via == "cli" and grant.expires is not None, "the shared stdio caller: 30 days at most"
        used = [e for e in env.registry.audit.entries(30) if e.get("confirm_channel") == f"grant:{grant.id}"]
        assert {e["outcome"] for e in used} == {"started", "ok"} and {e["actor"] for e in used} == {"mcp"}

    def test_approve_always_on_a_pending_approval_runs_the_call_and_saves_the_standing_approval(self, env):
        async def go():
            async with Client(env.server) as client:  # cannot ask
                first = await client.call_tool("add_pack_from_index", {"pack_id": "know-v"})
                approval = APPROVAL_RE.search(json.dumps([r.id for r in env.store.pending()])).group(0)
                decided = await asyncio.to_thread(cli, env.registry, "tools", "approvals", "approve", approval,
                                                  "--always", "--duration", "24h", input="y\n")
                assert decided.exit_code == 0, decided.output
                for _ in range(200):
                    if env.installed:
                        break
                    await asyncio.sleep(0.05)
                second = await client.call_tool("add_pack_from_index", {"pack_id": "know-v2"})
                return first, second
        first, second = run(go())
        assert first.is_error and "Waiting for the person" in text_of(first)
        assert not second.is_error, "the next call in scope runs without a pending approval"
        assert env.installed == ["know-v", "know-v2"]
        (grant,) = grants(env.registry, "mcp")
        assert grant.scope == {"kind": "knowledge", "trust": "verified", "signing_key": KEY1}

    def test_always_is_refused_on_the_command_line_while_a_gui_session_runs(self, env):
        call_as_pending = ToolContext("mcp", connection="c", pending_approvals=True)
        with pytest.raises(ToolError):
            env.registry.call("add_pack_from_index", {"pack_id": "know-v"}, call_as_pending)
        approval = env.store.pending()[0]
        lock = hold_gui_lock(env.state)
        try:
            result = cli(env.registry, "tools", "approvals", "approve", approval.id, "--always", input="y\n")
        finally:
            lock.release()
        assert result.exit_code == 1 and "use its approval window" in flat(result.output)
        assert env.store.get(approval.id).state == "pending" and grants(env.registry) == []

    def test_dittos_cli_prompt_offers_always_and_announces_it_in_the_gui_later(self, env, monkeypatch):
        answers = iter(["a", "revoke"])
        monkeypatch.setattr(cli_module.click, "prompt", lambda *a, **k: next(answers))
        monkeypatch.setattr(cli_module.click, "confirm", lambda *a, **k: True)
        monkeypatch.setattr(cli_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
        env.registry.call("add_pack_from_index", {"pack_id": "know-v"},
                          ToolContext("ditto", confirm=cli_module._ditto_confirm))
        (grant,) = grants(env.registry, "ditto")
        assert grant.created_via == "ditto-cli" and grant.announced is False
        assert env.registry.call("add_pack_from_index", {"pack_id": "know-v2"}, ToolContext("ditto")).installed

    def test_dittos_cli_always_is_refused_while_a_gui_session_runs(self, env, monkeypatch):
        answers = iter(["a"])
        monkeypatch.setattr(cli_module.click, "prompt", lambda *a, **k: next(answers))
        monkeypatch.setattr(cli_module.click, "confirm", lambda *a, **k: True)
        monkeypatch.setattr(cli_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
        lock = hold_gui_lock(env.state)
        try:
            env.registry.call("add_pack_from_index", {"pack_id": "know-v"},
                              ToolContext("ditto", confirm=cli_module._ditto_confirm))
        finally:
            lock.release()
        assert env.installed == ["know-v"] and grants(env.registry) == [], "allowed once, no standing approval"

    def test_dittos_gui_dialog_answer_creates_one_from_the_framework_window(self, env):
        def dialog(spec, inp):
            return GrantChoice("this", "30d")
        dialog.confirm_channel = "framework-gui"
        env.registry.call("add_pack_from_index", {"pack_id": "know-v"}, ToolContext("ditto", confirm=dialog))
        (grant,) = grants(env.registry, "ditto")
        assert grant.created_via == "ditto-gui" and grant.announced

    def test_the_gui_window_approves_and_does_not_ask_again(self, env, qapp):
        from orchestrator.gui.approvals_window import ApprovalsDialog
        with pytest.raises(ToolError):
            env.registry.call("add_pack_from_index", {"pack_id": "know-v"},
                              ToolContext("mcp", connection="c", pending_approvals=True))
        dialog = ApprovalsDialog(env.registry)
        assert dialog.always_button.isEnabled()
        assert dialog.decide_always(("this", "24h"))
        (grant,) = grants(env.registry, "mcp")
        assert grant.created_via == "gui"
        assert [e for e in env.registry.audit.entries(10) if e["outcome"] == "approval_decided"][0]["actor"] == "gui"


# --- the scope -------------------------------------------------------------------------------------------------

class TestScope:

    def test_packs_only_knowledge_and_recipe_of_both_trust_levels_and_verified_target_packs(self):
        def offered(pack):
            return PACK_RULE.scope(INDEX[pack], "this") is not None
        assert offered("know-v") and offered("know-c") and offered("recipe-v") and offered("target-v")
        assert not offered("target-c") and not offered("revoked")
        assert PACK_RULE.scope(dict(INDEX["know-v"], source="local"), "this") is None
        assert PACK_RULE.scope(INDEX["know-v"], "any") is None, "no wide scopes for packs"

    def test_a_call_outside_the_scope_asks(self, env):
        grant_for(env, "know-v")
        asked = []

        def confirm(spec, inp):
            asked.append(inp.pack_id)
            return True
        for pack in ("know-v2", "recipe-v", "know-c", "know-k2", "target-c"):
            call_as(env, "mcp:laptop", pack, confirm=confirm)
        assert asked == ["recipe-v", "know-c", "know-k2", "target-c"], \
            "another kind, another trust level, a changed index key and a community target pack all ask"

    def test_build_and_download_rules(self):
        base = {"target": "rockchip", "target_trust": "shipped", "weights_format": "safetensors",
                "image_base_official": True, "source": "hf", "model": "org/m", "gated": False, "base_gated": False,
                "license": "apache-2.0", "size_bytes": 3 * 10 ** 9}
        scope = BUILD_RULE.scope(base, "this")
        assert scope == {"target": "rockchip", "source": "hf", "max_gb": 5, "model": "org/m"}
        assert BUILD_RULE.covers(scope, dict(base, size_bytes=4 * 10 ** 9))
        assert not BUILD_RULE.covers(scope, dict(base, size_bytes=12 * 10 ** 9)), "a larger size class asks"
        assert not BUILD_RULE.covers(scope, dict(base, model="org/other")), "another model asks under 'this model'"
        assert BUILD_RULE.covers(BUILD_RULE.scope(base, "any"), dict(base, model="org/other"))
        for change in ({"weights_format": "pytorch"}, {"gated": True}, {"base_gated": True},
                       {"license": "llama3.1"}, {"license": "other"}, {"target_trust": "community"},
                       {"image_base_official": False}, {"installed_via": "grant:g_0123456789abcdef"}):
            assert BUILD_RULE.scope(dict(base, **change), "this") is None, change
        assert BUILD_RULE.scope(dict(base, installed_via="grant:g_0123456789abcdef", confirmed_build=True), "this")
        local = dict(base, source="local", weights_format="pytorch")
        assert BUILD_RULE.scope(local, "this") is None, "a local .bin build asks too"
        dl = {"repo": "org/m", "filename": "model.Q4_K_M.gguf", "gated": False, "base_gated": False,
              "license": "mit", "size_bytes": 2 * 10 ** 9}
        dscope = DOWNLOAD_RULE.scope(dl, "this")
        assert dscope == {"repo": "org/m", "max_gb": 5}
        assert DOWNLOAD_RULE.scope(dict(dl, filename="pytorch_model.bin"), "this") is None, "never pickle weights"
        assert not DOWNLOAD_RULE.covers(dscope, dict(dl, repo="org/second")), "a second repository asks"
        assert DOWNLOAD_RULE.covers(DOWNLOAD_RULE.scope(dl, "any"), dict(dl, repo="org/second"))
        assert DOWNLOAD_RULE.scope(dict(dl, license="llama3.1"), "this") is None

    def test_the_never_list_is_exact_and_enforced(self, env):
        assert NEVER_GRANT == {"deploy_to_device", "probe_remote_device", "deploy_device_runtime",
                               "run_device_benchmark", "update_settings", "crawl_docs_site",
                               "create_target_module", "export_pack", "resolve_healing", "cancel_build", "cancel_job",
                               "delete_artifact", "restore_knowledge_snapshot"}
        for name in sorted(NEVER_GRANT):
            with pytest.raises(ValueError, match="never gets a standing approval"):
                env.registry.register(ToolSpec(name, name, "", ToolTier.LOCAL_WRITE, PackIn, PackOut,
                                               lambda i: PackOut(),
                                               grant=GrantRule(rule=tool_rule(name), warning="x",
                                                               facts=lambda i, c: {})))
        for spec in env.registry.list():
            if spec.name in NEVER_GRANT:
                assert spec.grant is None, spec.name

    def test_tools_without_a_grant_rule_never_offer_it(self, env):
        for spec in env.registry.list():
            if spec.grant is None:
                assert env.registry.grant_offer(spec, None, ToolContext("mcp:laptop")) is None, spec.name

    def test_no_standing_approval_for_a_tool_set_to_require_the_framework_channel(self, env):
        grant_for(env, "know-v")
        env.registry.policy_store.update(lambda p: p.model_copy(update={"callers": {
            name: s.model_copy(update={"framework_only": ["add_pack_from_index"]}) for name, s in p.callers.items()}}))
        spec = env.registry.get("add_pack_from_index")
        ctx = ToolContext("mcp:laptop")
        assert env.registry.grant_offer(spec, PackIn(pack_id="know-v"), ctx) is None
        assert env.registry.matching_grant(spec, PackIn(pack_id="know-v"), ctx) is None


# --- callers ---------------------------------------------------------------------------------------------------

class TestCallers:

    def test_the_shared_stdio_caller_gets_the_narrowest_scope_a_warning_and_a_shorter_expiry(self, env):
        spec = env.registry.get("add_pack_from_index")
        shared = env.registry.grant_offer(spec, PackIn(pack_id="know-v"), ToolContext("mcp", client="Claude Code 2"))
        assert list(shared.scopes) == ["this"] and shared.durations == ["30d", "24h"]
        assert "Every local stdio client" in shared.warning and "llm-cli mcp token add" in shared.warning
        assert "Claude Code 2, as it reports itself" in shared.warning
        assert "Claude Code still shows its own prompt" in shared.warning, "an external tool"
        env.registry.tokens.add("laptop")
        own = env.registry.grant_offer(spec, PackIn(pack_id="know-v"), ToolContext("mcp:laptop"))
        assert own.durations == ["revoke", "30d", "24h"] and "token 'laptop'" in own.warning

    def test_a_standing_approval_covers_only_its_caller(self, env):
        grant_for(env, "know-v", caller="mcp:laptop")
        with pytest.raises(ToolError) as info:
            call_as(env, "mcp", "know-v")  # the shared caller is asked (and here nobody can be asked)
        assert info.value.code == "needs_person"
        assert call_as(env, "mcp:laptop", "know-v").installed == "know-v"

    def test_a_tokens_standing_approval_dies_with_the_token(self, env):
        grant_for(env, "know-v", caller="mcp:laptop")
        assert cli(env.registry, "mcp", "token", "revoke", "laptop", input="y\n").exit_code == 0
        assert grants(env.registry, "mcp:laptop") == []
        # Even one left in the file never covers a new token of the same name.
        env.registry.tokens.add("laptop")
        stale = grant_for(env, "know-v", caller="mcp:laptop")
        env.registry.tokens.revoke("laptop")
        env.registry.tokens.add("laptop")
        spec = env.registry.get("add_pack_from_index")
        assert stale in grants(env.registry, "mcp:laptop")
        assert env.registry.matching_grant(spec, PackIn(pack_id="know-v"), ToolContext("mcp:laptop")) is None

    def test_policy_from_never_copies_standing_approvals_and_a_linked_token_uses_dittos(self, env, monkeypatch):
        monkeypatch.setattr(cli_module, "_stdout_is_terminal", lambda: True)
        grant_for(env, "know-v", caller="ditto")
        assert cli(env.registry, "mcp", "token", "add", "copy", "--policy-from", "ditto").exit_code == 0
        assert cli(env.registry, "mcp", "token", "add", "linked", "--link", "ditto").exit_code == 0
        spec = env.registry.get("add_pack_from_index")
        inp = PackIn(pack_id="know-v")
        assert env.registry.matching_grant(spec, inp, ToolContext("mcp:copy")) is None
        assert env.registry.matching_grant(spec, inp, ToolContext("mcp:linked")) is not None
        assert "linked" in env.registry.grant_offer(spec, inp, ToolContext("ditto")).warning


# --- lifecycle -------------------------------------------------------------------------------------------------

class TestLifecycle:

    def test_a_question_asked_again_offers_only_what_its_new_facts_allow(self, env, monkeypatch):
        """The person approved; meanwhile the index entry is verified with another key, so the question is asked again.
        Its "do not ask again" must be computed from the new facts, not offer the scope of the old key."""
        service = env.registry.approvals
        real_question = service.question
        changed = {"now": False}
        monkeypatch.setattr(service, "question", lambda spec, inp, ctx, facts=None: real_question(
            spec, inp, ctx, facts) + ("\n  index key: changed" if changed["now"] else ""))
        with pytest.raises(ToolError):
            call_as(env, "mcp", "know-v", pending=True)
        record = env.store.pending()[0]
        assert record.grant.scopes["this"]["signing_key"] == KEY1
        changed["now"] = True
        monkeypatch.setitem(INDEX, "know-v", dict(INDEX["know-v"], signing_key=KEY2))
        assert cli(env.registry, "tools", "approvals", "approve", record.id, input="y\n").exit_code == 0
        service.poll_once()
        asked_again = env.store.get(record.id)
        assert asked_again.state == "pending" and "index key: changed" in asked_again.question
        assert asked_again.grant.scopes["this"]["signing_key"] == KEY2 and env.installed == []

    def test_a_lapsed_standing_approval_stays_lapsed(self, env):
        """90 days without use: the tool asks again, also on every later call (the record of the lapse in the audit
        log is no use that would wake the standing approval up again)."""
        spec = env.registry.get("add_pack_from_index")
        inp, ctx = PackIn(pack_id="know-v"), ToolContext("mcp:laptop")
        grant = grant_for(env, "know-v")
        old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat(timespec="seconds")
        env.registry.policy_store.update(lambda p: p.model_copy(update={"grants": {
            c: [x.model_copy(update={"created": old}) if x.id == grant.id else x for x in gs]
            for c, gs in p.grants.items()}}))
        for _ in range(3):
            assert env.registry.matching_grant(spec, inp, ctx) is None
        with pytest.raises(ToolError) as info:
            call_as(env, "mcp:laptop", "know-v")
        assert info.value.code == "needs_person" and env.installed == []
        row = next(r for r in env.registry.grant_rows() if r["grant"].id == grant.id)
        assert row["state"] == "lapsed" and row["uses"] == 0 and row["last_use"] is None
        assert len([e for e in env.registry.audit.entries(100) if e["outcome"] == "grant_lapsed"]) == 1

    def test_a_request_of_a_revoked_token_never_becomes_a_new_tokens_standing_approval(self, env):
        """The token 'laptop' asked; the person revoked it and made a new token with the same name. Approving the old
        request with "do not ask again" must not give the new token a standing approval it never asked for."""
        from orchestrator.tools.policy import CallerPolicy
        env.registry.tokens.add("laptop")
        env.registry.policy_store.update(lambda p: p.model_copy(update={"callers": {
            **p.callers, "mcp:laptop": CallerPolicy(reviewed=[s.name for s in env.registry.list()])}}))
        with pytest.raises(ToolError):
            call_as(env, "mcp:laptop", "know-v", pending=True)
        record = env.store.pending()[0]
        assert record.grant is not None
        env.registry.tokens.revoke("laptop")
        env.registry.tokens.add("laptop")
        result = cli(env.registry, "tools", "approvals", "approve", record.id, "--always", input="y\n")
        assert result.exit_code == 1 and grants(env.registry) == []
        assert env.store.get(record.id).state == "pending", "nothing was decided"

    def test_only_offers_the_framework_computed_for_a_tool_with_a_rule_become_standing_approvals(self, env):
        from orchestrator.tools.grants import GrantOffer
        forged = GrantOffer(tool="cancel_job", caller="ditto", rule_version=1, scopes={"this": {"tool": "cancel_job"}},
                            summaries={"this": "every call"}, durations=["revoke"], warning="x")
        with pytest.raises(ValueError):
            env.registry.create_grant(forged, "this", "revoke", "gui", "gui")
        outdated = env.registry.grant_offer(env.registry.get("add_pack_from_index"), PackIn(pack_id="know-v"),
                                            ToolContext("ditto")).model_copy(update={"rule_version": 99})
        with pytest.raises(ValueError):
            env.registry.create_grant(outdated, "this", "revoke", "gui", "gui")
        assert grants(env.registry) == []

    def test_what_the_person_sees_uses_speaking_names(self, env, qapp, monkeypatch):
        """Notices, lists and announcements name the caller as a person reads it and the tool by its title, never the
        raw caller id."""
        from PySide6.QtWidgets import QMessageBox

        from orchestrator.gui.dialogs import ToolPermissionsDialog
        from orchestrator.gui.main_window import MainOrchestrator
        grant = grant_for(env, "know-v", via="cli")
        messages = []
        window = SimpleNamespace(framework=SimpleNamespace(tool_registry=env.registry),
                                 status=SimpleNamespace(showMessage=lambda text, *a: messages.append(text)),
                                 btn_revoke_grant=SimpleNamespace(show=lambda: None, hide=lambda: None),
                                 _used_grant="")
        MainOrchestrator._grant_used(window, "add_pack_from_index", grant.id, "mcp:laptop", "")
        assert "The client with the MCP token 'laptop'" in messages[0] and "Install a pack" in messages[0]
        assert "mcp:laptop" not in messages[0] and "knowledge packs from the signed index" in messages[0]
        assert window._used_grant == grant.id
        dialog = ToolPermissionsDialog(env.registry)
        assert dialog.grant_table.item(0, 1).text() == "the MCP token 'laptop'"
        assert dialog.grant_table.item(0, 2).text().startswith("Install a pack: ")
        shown = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
        MainOrchestrator._announce_cli_grants(window)
        assert "Install a pack for the MCP token 'laptop'" in shown[0] and "mcp:laptop" not in shown[0]
        listed = flat(cli(env.registry, "tools", "permissions", "--grants", terminal=False).output)
        assert "Install a pack for the MCP token 'laptop'" in listed and "add_pack_from_index" not in listed

    def test_uses_come_from_the_audit_log_and_never_rewrite_the_policy_file(self, env):
        grant = grant_for(env, "know-v")
        before = env.registry.policy_store.path.read_bytes()
        call_as(env, "mcp:laptop", "know-v")
        call_as(env, "mcp:laptop", "know-v2")
        assert env.registry.policy_store.path.read_bytes() == before
        row = next(r for r in env.registry.grant_rows() if r["grant"].id == grant.id)
        assert row["uses"] == 2 and row["last_use"] and row["state"] == "active"

    def test_a_revocation_is_never_undone_by_a_concurrent_use(self, env):
        grant = grant_for(env, "know-v")
        from orchestrator.tools.policy import PolicyStore
        other = PolicyStore(env.state, key=env.registry.policy_store.key)  # e.g. the GUI process
        errors = []

        def use():
            for _ in range(20):
                try:
                    call_as(env, "mcp:laptop", "know-v")
                except ToolError:
                    pass  # after the revocation it asks; nobody can be asked here
                except Exception as e:  # pragma: no cover
                    errors.append(e)
        import threading
        worker = threading.Thread(target=use)
        worker.start()
        time.sleep(0.05)
        other.update(lambda p: p.model_copy(update={"grants": {}}), actor="gui")
        worker.join()
        assert errors == [] and grants(env.registry) == []
        ran = len(env.installed)
        with pytest.raises(ToolError) as info:
            env.registry.call("add_pack_from_index", {"pack_id": "know-v"}, ToolContext("mcp:laptop", session="new"))
        assert info.value.code == "needs_person" and len(env.installed) == ran
        assert grant.id not in env.registry.policy_store.path.read_text()

    def test_revoked_expired_lapsed_and_outdated_ones_ask_again(self, env):
        spec = env.registry.get("add_pack_from_index")
        inp, ctx = PackIn(pack_id="know-v"), ToolContext("mcp:laptop")
        grant = grant_for(env, "know-v")
        assert cli(env.registry, "tools", "permissions", "--revoke-grant", grant.id).exit_code == 0
        assert env.registry.matching_grant(spec, inp, ctx) is None
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat(timespec="seconds")
        for change in ({"expires": past}, {"created": old}, {"rule_version": 7}):
            g = grant_for(env, "know-v")
            env.registry.policy_store.update(lambda p: p.model_copy(update={"grants": {c: [
                x.model_copy(update=change) if x.id == g.id else x for x in gs] for c, gs in p.grants.items()}}))
            assert env.registry.matching_grant(spec, inp, ctx) is None, change
            env.registry.revoke_grant(g.id, "cli")
        lapsed = [e for e in env.registry.audit.entries(100) if e["outcome"] == "grant_lapsed"]
        assert len(lapsed) == 1, "a lapse is recorded once"

    def test_a_standing_approval_added_by_hand_is_not_honoured(self, env):
        env.registry.tokens.add("laptop")
        data = json.loads(env.registry.policy_store.path.read_text())
        data["grants"] = {"mcp:laptop": [{"id": "g_" + "0" * 16, "tool": "add_pack_from_index",
                                          "scope": INDEX["know-v"] | {}, "summary": "x", "rule_version": 1,
                                          "created": "2026-10-11T00:00:00+00:00", "created_via": "gui"}]}
        env.registry.policy_store.path.write_text(json.dumps(data))
        with pytest.raises(ToolError):
            call_as(env, "mcp:laptop", "know-v")
        assert env.installed == [] and "cannot be trusted" in env.registry.policy_store.problem

    def test_the_cli_lists_them_and_reset_clears_them(self, env):
        grant = grant_for(env, "know-v")
        call_as(env, "mcp:laptop", "know-v")
        listed = flat(cli(env.registry, "tools", "permissions", "--grants", terminal=False).output)
        assert grant.id in listed and "knowledge packs from the signed index" in listed and "used 1 times" in listed
        assert cli(env.registry, "tools", "permissions", "--revoke-grant", grant.id, terminal=False).exit_code == 2
        grant_for(env, "know-v")
        assert cli(env.registry, "tools", "permissions", "--reset").exit_code == 0
        assert grants(env.registry) == []

    def test_get_status_shows_the_callers_own_standing_approvals(self, env):
        grant = grant_for(env, "know-v")
        grant_for(env, "know-v", caller="ditto")
        status = env.registry.call("get_status", {}, ToolContext("mcp:laptop"))
        assert [s.id for s in status.permissions.standing_approvals] == [grant.id]
        assert status.permissions.standing_approvals[0].state == "active"

    def test_the_gui_lists_revokes_and_shows_each_use(self, env, qapp):
        from orchestrator.gui.approvals_window import GrantUseWatcher
        from orchestrator.gui.dialogs import ToolPermissionsDialog
        grant = grant_for(env, "know-v")
        watcher = GrantUseWatcher(env.registry.audit)
        seen = []
        watcher.used.connect(lambda tool, gid, actor, detail: seen.append((tool, gid, actor)))
        call_as(env, "mcp:laptop", "know-v")
        assert watcher.check() == 1 and seen == [("add_pack_from_index", grant.id, "mcp:laptop")]
        dialog = ToolPermissionsDialog(env.registry)
        assert dialog.grant_table.rowCount() == 1 and dialog.grant_table.item(0, 0).text() == grant.id
        assert dialog.revoke_grant(grant.id) and dialog.grant_table.rowCount() == 0
        dialog.save()  # the rest of the window is saved on top of the revocation, not over it
        assert grants(env.registry) == []

    def test_standing_approvals_made_on_a_command_line_are_shown_once_in_the_gui(self, env, qapp, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from orchestrator.gui.main_window import MainOrchestrator
        grant = grant_for(env, "know-v", via="cli")
        assert grant.announced is False
        shown = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
        window = SimpleNamespace(framework=SimpleNamespace(tool_registry=env.registry),
                                 status=SimpleNamespace(showMessage=lambda *a, **k: None))
        MainOrchestrator._announce_cli_grants(window)
        assert len(shown) == 1 and grant.id in shown[0] and "via cli" in shown[0]
        assert grants(env.registry)[0].announced is True
        MainOrchestrator._announce_cli_grants(window)
        assert len(shown) == 1, "shown once"
        assert "shown to you in the GUI" in [e for e in env.registry.audit.entries(5)
                                             if e["outcome"] == "policy_changed"][0]["detail"]
