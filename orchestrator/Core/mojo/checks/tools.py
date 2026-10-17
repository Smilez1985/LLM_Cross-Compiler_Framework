#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: tool permissions, MCP tokens and the audit log
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


AUDIT_WHY = "Calls that change something run only when they are recorded, so they are refused."
AUDIT_TRUST_WHY = "The record of what AI clients did cannot be trusted from that entry on."


def check_tool_policy(framework) -> List[Finding]:
    registry = getattr(framework, "tool_registry", None)
    if registry is None:
        return [Finding("ai", "tools.registry", Severity.WARN, "Tool registry not loaded.",
                        where="the tool registry", why="AI clients and Ditto cannot use the framework's tools.",
                        cannot="not_automatic")]
    findings = []
    policy = registry.policy()
    problem = getattr(registry.policy_store, "problem", None)
    if problem:
        findings.append(Finding("ai", "tools.permissions", Severity.ERROR, problem,
                                "Run 'llm-cli tools permissions' (or Tools > Tool Permissions in the GUI) to set "
                                "them up again.",
                                where=str(getattr(registry.policy_store, "path", "") or "the tool permissions"),
                                why="Every tool is denied until the permissions are set up again.", cannot="person"))
    elif not policy.configured:
        findings.append(Finding("ai", "tools.permissions", Severity.INFO,
                                "Tool permissions are not set up yet (safe defaults apply: read allowed, everything "
                                "else asks).", "Run 'llm-cli ditto setup' or use Tools > Tool Permissions in the GUI."))
    else:
        findings.append(_ok("ai", "tools.permissions", "Tool permissions set up"))
    store = registry.policy_store
    if not problem and getattr(store, "key", b"") is None and store.path.exists():
        findings.append(Finding("ai", "tools.permissions.signature", Severity.WARN,
                                "The tool permissions are signed without a key (no OS keyring), so a hand edit of "
                                f"{store.path.name} by anyone who can write the per-user folder is not detected.",
                                "Make an OS keyring available to the framework; the next save signs with a key.",
                                where=str(store.path), why="A changed permission file would not be detected.",
                                cannot="admin"))
    tokens = getattr(registry, "tokens", None)
    if tokens is not None:
        try:
            tokens.list()
        except Exception as e:  # TokenStoreError: damaged, unsigned, open to others
            findings.append(Finding("ai", "tools.tokens", Severity.ERROR, str(e),
                                    "Remove the file and create the tokens again ('llm-cli mcp token add NAME').",
                                    where=str(getattr(tokens, "path", "") or "the MCP token store"),
                                    why="MCP clients that sign in with a token cannot connect.", cannot="person"))
    if registry.audit is not None:
        archive = ("'llm-cli tools audit --archive' moves it and its anchor aside (kept with a timestamp) and "
                   "starts a new log.")
        try:
            result = registry.audit.check()
        except Exception as e:
            return findings + [Finding("ai", "tools.audit", Severity.ERROR,
                                       f"The tool audit log {registry.audit.path} cannot be opened: {e}",
                                       f"Keep the damaged file for inspection: {archive}",
                                       where=str(registry.audit.path), why=AUDIT_WHY, cannot="person")]
        # A log this key cannot verify may just as well have been rewritten, so it is no lesser finding.
        if result.problem in ("modified", "removed", "key_mismatch"):
            findings.append(Finding("ai", "tools.audit", Severity.ERROR, result.message,
                                    f"Inspect {registry.audit.path}; {archive}", where=str(registry.audit.path),
                                    why=AUDIT_TRUST_WHY, cannot="person"))
        elif result.problem:
            findings.append(Finding("ai", "tools.audit", Severity.WARN, result.message,
                                    f"Inspect {registry.audit.path}; {archive}", where=str(registry.audit.path),
                                    why=AUDIT_TRUST_WHY, cannot="person"))
        elif not result.keyed:
            findings.append(Finding("ai", "tools.audit", Severity.WARN, result.message,
                                    "The next tool call adds a keyed entry, which protects the whole chain."
                                    if result.key_available else
                                    "Install a system keyring (e.g. gnome-keyring or KWallet), so the audit chain "
                                    "is protected by a key.", where=str(registry.audit.path),
                                    why="Without a key, a changed audit log is harder to detect.",
                                    cannot="not_automatic" if result.key_available else "admin"))
        else:
            findings.append(_ok("ai", "tools.audit", result.message))
    return findings
