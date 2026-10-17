"""Shared helpers that left the tool modules (consolidation chain c1-imports): each has one home below the tools,
and the old names stay where callers and tests still reach them."""


def test_the_audit_key_lives_next_to_the_other_keyring_entries():
    """The audit HMAC key (Core/vault_key.audit_key) is what the tool registry, the chat feed and the CLI's audit
    commands read; framework_tools keeps _audit_key (build_tool_registry reads it there), _key_lock and AUDIT_KEY_ID."""
    import ast

    from conftest import REPO_ROOT
    from orchestrator.Core import vault_key
    from orchestrator.tools import framework_tools
    assert framework_tools._audit_key is vault_key.audit_key
    assert framework_tools._key_lock is vault_key._key_lock and framework_tools.AUDIT_KEY_ID == vault_key.AUDIT_KEY_ID
    for rel in ("orchestrator/Core/chat_feed.py", "orchestrator/cli.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        names = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert ("orchestrator.tools.framework_tools", "_audit_key") not in names, rel
        assert ("orchestrator.Core.vault_key", "audit_key") in names, rel
