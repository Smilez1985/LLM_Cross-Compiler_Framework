#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - One-time notice before the first Deep Ingest

The notice says what the crawler reads, which opt-outs it honours, that the knowledge stays on this computer
and that sharing it needs an explicit release. It is information, not a legal confirmation: whether a site may
be mined is decided by the site's machine-readable opt-outs, which the crawler checks, not by the user.

It is shown once per user and again when its text changes: the state file (per-user state directory, JSON,
mode 0600, written atomically) stores a hash of the notice texts in every language, when it was acknowledged and
through which of the framework's own windows (`via`: gui or cli). A missing, damaged or different hash shows the
notice again.

Only a person acknowledges it, in the GUI's notice dialog or with `llm-cli tools crawl-notice` in a terminal. No tool,
resource or prompt can: an AI client that wants to crawl (crawl_docs_site) is refused while the notice is needed and
is told those two places (MCP design 2.5, DECISIONS round 1 item 1).
"""

from typing import Optional, Tuple

from orchestrator.utils import notice_store
from orchestrator.utils.notice_store import OneTimeNotice

TEXT_KEYS = ("crawl.notice.title", "crawl.notice.text")
VIA = notice_store.VIA   # the framework's own windows where a person acknowledges the notice


def notice_text(lang: Optional[str] = None) -> Tuple[str, str]:
    """(title, text) of the notice in lang (default: the UI language)."""
    return notice_store.notice_text(TEXT_KEYS, lang)


def notice_sha256() -> str:
    """Changes whenever the text in any language changes, also its formal ("Sie") form."""
    return notice_store.notice_sha256(TEXT_KEYS)


class CrawlNotice(OneTimeNotice):
    """Remembers per user whether the current notice was shown (the mechanism: utils/notice_store.py)."""

    FILE_NAME = "crawl_notice.json"
    TEXT_KEYS = TEXT_KEYS
    AGAIN = "Showing the Deep Ingest notice again"
    WRONG_VIA = "The Deep Ingest notice is acknowledged in the GUI or the CLI, not via {via!r}."


def acknowledge_and_audit(notice: CrawlNotice, via: str, audit=None) -> Optional[str]:
    """A person read the notice in the framework's window `via` (gui or cli): remembers it and records it in the
    audit log (actor `via`), so AI clients may ask to crawl. OSError when it cannot be remembered (nothing recorded
    then). Returns why the audit log could not record it, or None (also without an audit log)."""
    notice.acknowledge(via)
    if audit is None:
        return None
    try:
        audit.record(via, "crawl_notice_acknowledged", "framework", "person", "ok", arguments={"via": via},
                     approved=True)
    except Exception as e:  # noqa: BLE001 - acknowledged; the person is told it is not recorded
        from orchestrator.utils.helpers import redact_secrets
        return redact_secrets(str(e)) or type(e).__name__
    return None
