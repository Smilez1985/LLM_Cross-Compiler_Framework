#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the chat feed
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_chat_feed(framework) -> List[Finding]:
    """The chat feed (delegation design 5.3): it can be written - an AI's decision is recorded there before it takes
    effect, or nothing runs - and its newest rows verify. Reads only; a probe write is rolled back."""
    feed = getattr(framework, "chat_feed", None)
    if feed is None:
        return [Finding("ai", "chat_feed.store", Severity.WARN,
                        "The chat feed is not available, so the chat cannot keep messages and AI decisions.",
                        "Check that the per-user folder exists and is writable ('llm-cli doctor' lists it).")]
    result = feed.check()
    # The file by name only: findings also reach AI clients (run_doctor), which never get host paths.
    aside = (f"Move {feed.path.name} (and its -wal and -shm files, if any) out of the per-user folder "
             "(~/.llm-framework or LLM_FRAMEWORK_HOME) and keep it for inspection; the framework starts a new chat "
             "feed with the next message.")
    if result.problem:
        return [Finding("ai", "chat_feed.store", Severity.ERROR, result.problem,
                        f"Free disk space or fix the folder's permissions. If the file is damaged: {aside}")]
    if not result.exists:
        return [_ok("ai", "chat_feed.store", "Chat feed not started yet (its folder is writable)")]
    findings = [_ok("ai", "chat_feed.store", f"Chat feed writable; {result.checked - result.unverified - result.unknown}"
                                             f" of the newest {result.checked} entries verify")]
    if result.unverified:
        findings.append(Finding("ai", "chat_feed.rows", Severity.WARN,
                                f"{result.unverified} chat entries do not verify (changed outside the framework, or "
                                "written with another key or without the keyring); the chat does not show them.",
                                f"If the keyring was reset, they stay hidden. To start a clean feed: {aside}"))
    if result.unknown:
        findings.append(Finding("ai", "chat_feed.version", Severity.INFO,
                                f"{result.unknown} chat entries were written by a newer version of the framework; "
                                "this version does not show them.", "Update the framework."))
    if not result.keyed and result.exists:
        findings.append(Finding("ai", "chat_feed.signature", Severity.WARN,
                                "The chat feed is signed without a key (no OS keyring), so an entry changed by anyone "
                                f"who can write {feed.path.name} is not detected.",
                                "Make an OS keyring available to the framework; new entries are then signed with a "
                                "key."))
    return findings
