#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the secrets store, how credentials are kept and the keyring
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


SECRETS_WHY = ("Without a readable store no key or token can be used or changed (sign-in, Hugging Face, MCP "
               "tokens).")


def check_secrets(framework) -> List[Finding]:
    sm = getattr(framework, "secrets_manager", None)
    if sm is None:
        return [Finding("security", "secrets.store", Severity.ERROR, "The secrets store is not available.",
                        where="the secrets store", why=SECRETS_WHY, cannot="not_automatic")]
    refresh = getattr(sm, "refresh", None)
    if callable(refresh):
        refresh()  # a long-running GUI or MCP server: another process may have changed the store
    if getattr(sm, "_load_failed", False):
        failed = getattr(sm, "failed_file", None) or sm.secrets_file
        reason = getattr(sm, "load_error", None)
        reason = f" ({reason})" if isinstance(reason, str) and reason else ""
        if failed != sm.secrets_file:
            return [Finding("security", "secrets.store", Severity.ERROR,
                            f"The old secrets store {failed} cannot be read, so its secrets were not taken over "
                            f"into {sm.secrets_file}; secrets cannot be changed.{reason}",
                            "Repair the file (its secrets are then taken over) or move it aside.",
                            where=str(failed), why=SECRETS_WHY, cannot="person")]
        return [Finding("security", "secrets.store", Severity.ERROR,
                        f"The secrets store {sm.secrets_file} cannot be read; secrets cannot be changed.{reason}",
                        "Repair the file or move it aside and sign in again (llm-cli ditto login <provider>; "
                        "other tokens: llm-cli secrets set).", where=str(sm.secrets_file), why=SECRETS_WHY,
                        cannot="person")]
    findings = [_ok("security", "secrets.store", "Secrets store ready")]
    mode = getattr(sm, "storage_mode", None)
    if mode == "insecure":
        findings.append(Finding("security", "secrets.storage", Severity.WARN,
                                "The vault key is stored in a file next to the vault (secret_storage: insecure): "
                                "credentials are not encrypted at rest.",
                                "Use the OS keyring, a credential helper or a passphrase "
                                "(llm-cli ditto auth storage --use keyring|helper|passphrase).",
                                where=str(getattr(sm, "secrets_file", "") or "the secrets store"),
                                why="Anyone who can read your per-user folder can read your keys and tokens.",
                                cannot="person"))
    elif mode == "ephemeral":
        findings.append(Finding("security", "secrets.storage", Severity.INFO,
                                "Credentials are kept in memory only (secret_storage: ephemeral); nothing is stored."))
    if mode not in (None, "keyring"):
        return findings
    try:
        import keyring
        backend = type(keyring.get_keyring()).__module__ + "." + type(keyring.get_keyring()).__name__
        if any(name in backend.lower() for name in ("null", "fail", "plaintext")):
            findings.append(Finding("security", "secrets.keyring", Severity.WARN,
                                    f"The keyring backend ({backend}) does not protect secrets.",
                                    "Install a system keyring (e.g. gnome-keyring or KWallet), or choose a "
                                    "credential helper or a passphrase (llm-cli ditto auth storage --use ...).",
                                    where=f"the keyring backend {backend}",
                                    why="Secrets kept there are not protected by the operating system.",
                                    cannot="admin"))
    except Exception:
        pass
    return findings
