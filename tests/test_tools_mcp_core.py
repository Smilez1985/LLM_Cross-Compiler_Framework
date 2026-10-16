"""Wave 5 (GUI parity, chain tools-mcp): the core functions the CLI and the GUI now share for the tool permissions,
the MCP tokens, the audit log, the crawl notice and the MCP connections.

Each test pins one extracted function: a caller's own section (created from the shared permissions, never looser),
the resets (shared, per caller, per token), the --policy-from copy, the complete token revoke (token, permissions and
standing approvals), the audit rows, summary and pages, the Claude Desktop plan, the HTTP refusals and the command
of the HTTP server as a child process.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from conftest import REPO_ROOT
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.grants import Grant
from orchestrator.tools.mcp_tokens import (TokenStore, TokenStoreError, UnknownCaller, add_token, copied_policy,
                                           person_change, policy_summary, read_only_policy, reset_token_policy,
                                           revoke_token, set_token_policy)
from orchestrator.tools.policy import (CallerPolicy, Decision, PolicyStore, ToolPolicy, ToolTier, caller_section,
                                       reset_changes, reset_policy, with_section)
from orchestrator.tools.registry import ToolRegistry, ToolSpec


class In(BaseModel):
    text: str = "x"


class Out(BaseModel):
    echoed: str = ""


def grant(gid: str, tool: str = "write") -> Grant:
    return Grant(id="g_" + gid * 16, tool=tool, scope={"tool": tool}, summary="every call", rule_version=1,
                 created="2026-10-11T00:00:00+00:00", created_via="gui")


@pytest.fixture
def registry(tmp_path):
    tokens, audit = TokenStore(tmp_path), AuditLog(tmp_path / "audit.db")
    reg = ToolRegistry(PolicyStore(tmp_path, audit=audit), audit, tokens=tokens)
    reg.register(ToolSpec("echo", "Echo", "Echo text", ToolTier.READ, In, Out, lambda i: Out(echoed=i.text)))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out, lambda i: Out(echoed="w")))
    return reg


# --- a caller's own section and the resets ---------------------------------------------------------------------------

class TestCallerSections:

    def test_a_new_section_starts_from_every_shared_setting(self):
        shared = ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
                            overrides={"write": Decision.DENY}, reviewed=["echo"], framework_only=["write"])
        section = caller_section(shared, "mcp")
        assert section.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW and section.overrides == {"write": Decision.DENY}
        assert section.reviewed == ["echo"] and section.framework_only == ["write"]
        changed = with_section(shared, "mcp", tiers={ToolTier.READ: Decision.ALLOW, ToolTier.LOCAL_WRITE: Decision.ASK,
                                                     ToolTier.EXTERNAL: Decision.DENY})
        own = changed.callers["mcp"]
        assert own.tiers[ToolTier.LOCAL_WRITE] == Decision.ASK
        assert own.framework_only == ["write"], "a stricter shared setting is never dropped"
        assert changed.tiers == shared.tiers, "the shared permissions stay as they were"

    def test_an_existing_section_and_the_shared_permissions_change_only_as_given(self):
        policy = ToolPolicy(callers={"ditto": CallerPolicy(framework_only=["echo"])})
        changed = with_section(policy, "ditto", overrides={"write": Decision.ALLOW})
        assert changed.callers["ditto"].framework_only == ["echo"]
        assert changed.callers["ditto"].overrides == {"write": Decision.ALLOW}
        shared = with_section(policy, None, framework_only=["write"])
        assert shared.framework_only == ["write"] and shared.callers == policy.callers

    @pytest.mark.parametrize("caller, field", [("mcp:laptop", "tiers"), ("other", "tiers"), ("mcp", "grants")])
    def test_a_token_an_unknown_caller_or_field_is_refused(self, caller, field):
        with pytest.raises(ValueError):
            with_section(ToolPolicy(), caller, **{field: {}})

    def test_reset_of_the_shared_permissions_is_the_recommended_defaults(self):
        policy = ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
                            callers={"mcp": CallerPolicy()}, grants={"ditto": [grant("1")]})
        assert reset_policy(policy) == ToolPolicy()
        changes = reset_changes(policy)
        assert "configured: True -> False" in changes and "mcp: own permissions removed (uses the shared ones)" in changes
        assert any("standing approval g_1111111111111111 for write revoked" in line for line in changes)
        assert reset_changes(ToolPolicy()) == [], "nothing to change, nothing listed"

    def test_reset_of_one_caller_removes_its_section_and_its_grants_only(self):
        policy = ToolPolicy(configured=True, callers={"mcp": CallerPolicy(), "ditto": CallerPolicy()},
                            grants={"mcp": [grant("1")], "ditto": [grant("2")]})
        after = reset_policy(policy, "mcp")
        assert set(after.callers) == {"ditto"} and set(after.grants) == {"ditto"} and after.configured
        assert reset_changes(ToolPolicy(configured=True), "mcp") == []
        with pytest.raises(ValueError):
            reset_policy(policy, "mcp:laptop")

    def test_the_cli_keeps_a_new_sections_framework_only_list(self, registry):
        """`tools permissions --caller mcp` built the new section without the tools asked in the framework only, so
        those questions could reach the client's own dialog again: the shared core keeps them."""
        from test_tool_policy_callers import invoke
        registry.policy_store.replace(ToolPolicy(configured=True, framework_only=["write"]))
        result = invoke(registry, "tools", "permissions", "--caller", "mcp", input="allow\nask\nask\n")
        assert result.exit_code == 0, result.output
        assert registry.policy().callers["mcp"].framework_only == ["write"]


# --- the tokens ----------------------------------------------------------------------------------------------------

class TestTokens:

    def test_policy_from_copies_a_caller_or_a_token(self, registry):
        registry.policy_store.replace(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW},
                                                 reviewed=["echo", "write"]))
        copy = copied_policy(registry, "mcp")
        assert copy.configured and copy.overrides == {"write": Decision.ALLOW}
        registry.tokens.add("box")
        registry.tokens.set_policy("box", ToolPolicy(configured=True, overrides={"write": Decision.DENY}))
        assert copied_policy(registry, "box").overrides == {"write": Decision.DENY}
        assert copied_policy(registry, "mcp:box").overrides == {"write": Decision.DENY}
        registry.tokens.add("plain")
        assert not copied_policy(registry, "mcp:plain").configured, "a new token's copy stays a new token's"
        with pytest.raises(UnknownCaller):
            copied_policy(registry, "nobody")
        with pytest.raises(UnknownCaller):
            copied_policy(registry, "mcp:mcp"), "mcp:NAME is always a token"

    def test_policy_from_refuses_unusable_stored_permissions(self, registry):
        registry.policy_store.path.write_text(json.dumps({"version": 1, "configured": True}))
        with pytest.raises(TokenStoreError) as e:
            copied_policy(registry, "ditto")
        assert not isinstance(e.value, UnknownCaller)

    def test_add_set_and_reset_are_audited_without_the_token(self, registry):
        token, problem = add_token(registry, "laptop", "gui", via="gui")
        assert problem is None and registry.tokens.find(token).name == "laptop"
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"]) == ("gui", "mcp_token.add") and token not in json.dumps(entry)
        with pytest.raises(TokenStoreError):
            add_token(registry, "other", "cli", policy_from="mcp", link="ditto")
        open_policy = ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW},
                                 reviewed=["echo", "write"])
        assert set_token_policy(registry, "laptop", open_policy, "cli") is None
        assert registry.decide(registry.get("write"), caller="mcp:laptop") == Decision.ALLOW
        preview = registry.audit.entries(1)[0]["args_preview"]
        assert "local_write: deny" in preview and "local_write: allow" in preview
        assert reset_token_policy(registry, "laptop", "gui", via="gui") is None
        stored = registry.tokens.get("laptop").policy
        assert stored == read_only_policy() and not stored.configured, "a new token again, not hand-set Deny"
        assert registry.decide(registry.get("write"), caller="mcp:laptop") == Decision.DENY
        with pytest.raises(TokenStoreError):
            set_token_policy(registry, "nobody", open_policy, "cli")

    def test_revoke_removes_the_token_its_permissions_and_its_standing_approvals(self, registry):
        registry.tokens.add("box")
        registry.policy_store.replace(ToolPolicy(configured=True, grants={"mcp:box": [grant("1")],
                                                                          "mcp": [grant("2")]}))
        result = revoke_token(registry, "box", "gui", via="gui")
        assert registry.tokens.get("box") is None
        assert result.grants_removed == 1 and result.grants_problem is None and result.audit_problem is None
        assert set(registry.policy().grants) == {"mcp"}, "another caller's standing approval stays"
        tools = [e["tool"] for e in registry.audit.entries(2)]
        assert tools == ["tool_policy", "mcp_token.revoke"]
        with pytest.raises(TokenStoreError):
            revoke_token(registry, "box", "cli")

    def test_a_grant_cleanup_that_fails_is_reported(self, registry, monkeypatch):
        registry.tokens.add("box")
        registry.policy_store.replace(ToolPolicy(configured=True, grants={"mcp:box": [grant("1")]}))

        def refuse(*a, **k):
            raise OSError("read-only folder")
        monkeypatch.setattr(registry.policy_store, "update", refuse)
        result = revoke_token(registry, "box", "cli")
        assert registry.tokens.get("box") is None and "read-only folder" in result.grants_problem

    def test_an_audit_that_fails_is_reported_not_raised(self, registry, monkeypatch):
        def broken(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(registry.audit, "record", broken)
        assert person_change(registry, "gui", "mcp_token.add", {"token": "x"}) == "disk full"
        assert person_change(type("R", (), {"audit": None})(), "gui", "e", {}) is None
        assert policy_summary(read_only_policy()) == "read: allow, local_write: deny, external: deny"


# --- the audit log -------------------------------------------------------------------------------------------------

class TestAuditView:

    def test_rows_summary_and_pages(self, registry):
        from orchestrator.tools.audit_view import DETAIL_COLUMNS, ENTRY_COLUMNS, audit_rows, audit_summary
        audit = registry.audit
        for i in range(5):
            audit.record("mcp:box", "write", "local_write", "ask", "ok", arguments={"n": i}, approved=1)
        audit.record("ditto", "write", "local_write", "deny", "denied", arguments={})
        audit.record("ditto", "report_cannot_do", "read", "allow", "cannot_reported", arguments={},
                     detail="checked: confirmed")
        assert audit.count() == 7
        first, second = audit.entries(3), audit.entries(3, offset=3)
        assert [e["id"] for e in first] == [7, 6, 5] and [e["id"] for e in second] == [4, 3, 2]
        assert audit.entries(3, offset=99) == []
        rows = list(audit_rows(registry, audit.entries(10), details=True))
        assert len(rows[0]) == len(ENTRY_COLUMNS) + len(DETAIL_COLUMNS)
        box = rows[-1]
        assert box[2] == "Write (write)" and box[4] == "yes" and box[5] == "ok"
        summary = {row[0]: row[1:] for row in audit_summary(audit.entries(100))}
        assert summary["mcp:box"][:2] == [5, 0]
        assert summary["ditto"][:4] == [0, 1, 1, 1]

    def test_the_cli_uses_the_shared_rows(self, registry, monkeypatch):
        from test_tool_policy_callers import invoke
        monkeypatch.setenv("COLUMNS", "400")
        registry.audit.record("mcp", "write", "local_write", "ask", "ok", arguments={}, approved=0)
        out = " ".join(invoke(registry, "tools", "audit", "--details").output.split())
        assert "Write (write)" in out and "Repairs" in out


# --- the crawl notice ----------------------------------------------------------------------------------------------

def test_the_crawl_notice_is_acknowledged_and_audited_once_for_cli_and_gui(tmp_path):
    from orchestrator.utils.crawl_notice import CrawlNotice, acknowledge_and_audit
    audit = AuditLog(tmp_path / "audit.db")
    notice = CrawlNotice(tmp_path)
    assert acknowledge_and_audit(notice, "gui", audit) is None
    assert not notice.needed() and notice.acknowledged_via() == "gui"
    entry = audit.entries(1)[0]
    assert (entry["actor"], entry["tool"], entry["approved"]) == ("gui", "crawl_notice_acknowledged", 1)
    assert acknowledge_and_audit(CrawlNotice(tmp_path / "x"), "cli") is None

    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")
    assert acknowledge_and_audit(notice, "cli", Broken()) == "disk full"
    with pytest.raises(ValueError):
        acknowledge_and_audit(notice, "mcp", audit)


# --- the MCP connections -------------------------------------------------------------------------------------------

class TestMcpConnections:

    def test_the_installation_root_is_the_clis(self):
        from orchestrator import mcp_server
        assert mcp_server.installation_root() == REPO_ROOT.resolve()

    def test_the_plan_names_the_file_the_change_the_kept_entries_and_the_backup(self, tmp_path):
        from orchestrator import mcp_server
        path = tmp_path / "Claude" / "claude_desktop_config.json"
        plan = mcp_server.plan_claude_desktop_config(REPO_ROOT, path)
        assert plan.original is None and plan.backup is None and not plan.replaces and plan.others == []
        assert not path.exists(), "a plan writes nothing"
        path.parent.mkdir()
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
        plan = mcp_server.plan_claude_desktop_config(REPO_ROOT, path)
        assert plan.others == ["other"] and not plan.replaces and plan.backup == path.with_name(path.name + ".bak")
        assert plan.entry == mcp_server.launch_command(REPO_ROOT)
        assert mcp_server.write_claude_desktop_config(REPO_ROOT, path, plan=plan) == path
        data = json.loads(path.read_text())
        assert data["theme"] == "dark" and set(data["mcpServers"]) == {"other", mcp_server.SERVER_NAME}
        assert json.loads(plan.backup.read_text())["mcpServers"] == {"other": {"command": "x"}}
        again = mcp_server.plan_claude_desktop_config(REPO_ROOT, path)
        assert again.replaces and again.unchanged

    def test_a_file_changed_after_it_was_shown_is_not_overwritten(self, tmp_path):
        from orchestrator import mcp_server
        path = tmp_path / "claude_desktop_config.json"
        path.write_text("{}")
        plan = mcp_server.plan_claude_desktop_config(REPO_ROOT, path)
        path.write_text('{"mcpServers": {"new": {}}}')
        with pytest.raises(ValueError, match="changed since it was shown"):
            mcp_server.write_claude_desktop_config(REPO_ROOT, path, plan=plan)
        assert json.loads(path.read_text()) == {"mcpServers": {"new": {}}}

    def test_the_http_refusals_are_the_servers(self, tmp_path):
        from orchestrator import mcp_server
        tokens = TokenStore(tmp_path)
        assert "loopback" in mcp_server.http_refusal(tokens, tmp_path, "0.0.0.0", False)
        assert "inside a container" in mcp_server.http_refusal(tokens, tmp_path, "127.0.0.1", True, inside=False)
        assert "named token" in mcp_server.http_refusal(tokens, tmp_path, "127.0.0.1", False)
        tokens.add("laptop")
        assert mcp_server.http_refusal(tokens, tmp_path, "::1", False) is None
        assert mcp_server.http_refusal(tokens, tmp_path, "127.0.0.1", True, inside=True) is None

    def test_the_child_process_runs_the_clis_serve_command(self):
        from orchestrator import cli as cli_module
        from orchestrator import mcp_server
        command = mcp_server.http_serve_command(REPO_ROOT, "::1", 9000, container=True)
        launch = mcp_server.launch_command(REPO_ROOT)
        assert command["command"] == launch["command"] and command["cwd"] == launch["cwd"]
        assert command["args"] == [*launch["args"], "--transport", "http", "--host", "::1", "--port", "9000",
                                   "--container"]
        options = {opt for param in cli_module.mcp_serve.params for opt in param.opts}
        assert {"--transport", "--host", "--port", "--container"} <= options
        assert mcp_server.http_url("::1", 9000) == "http://[::1]:9000/mcp"
        assert mcp_server.http_url("0.0.0.0", 9000, container=True) == "http://127.0.0.1:9000/mcp"
        assert Path(command["env"]["PYTHONPATH"]) == Path(launch["cwd"])
