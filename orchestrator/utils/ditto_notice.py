#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Ditto is experimental: the one-time notice (DECISIONS round 8, Q-2)

Ditto stays optional and is marked experimental. The first time a person uses Ditto, the framework shows this notice
once: "Use at your own risk and responsibility. The creator of LLMCF is not liable." It repeats the no-warranty terms
of LICENSE.txt and claims nothing beyond them.

It is shown once per person and again when its text changes: the state file (per-user state directory, JSON, mode
0600, written atomically) stores a hash of the notice texts in every language, when it was shown and where (`via`:
gui or cli). A missing, damaged or different hash shows it again.

It never blocks the rest of the framework: builds, deployments, the MCP server and every other window work without
it, and no tool, resource or prompt shows or answers it. The GUI shows it at the top of the chat panel until the
person clicks "Understood" (a hidden panel is shown once for it, without the focus, because the builder and the module
wizard use Ditto without the panel; the AI settings show the sentence every time). `llm-cli ditto` and `llm-cli module
generate-ai` print it on stderr before the first command at a terminal and record it then. Without a terminal nothing
is printed or recorded, so the next command at a terminal prints it.
"""

from typing import Optional, Tuple

from orchestrator.utils import notice_store
from orchestrator.utils.notice_store import OneTimeNotice

TEXT_KEYS = ("chat.ditto.notice.title", "chat.ditto.notice.text")
VIA = notice_store.VIA   # the framework's own windows where a person reads the notice


def notice_text(lang: Optional[str] = None) -> Tuple[str, str]:
    """(title, text) of the notice in lang (default: the UI language)."""
    return notice_store.notice_text(TEXT_KEYS, lang)


def notice_sha256() -> str:
    """Changes whenever the text in any language changes, also its formal ("Sie") form."""
    return notice_store.notice_sha256(TEXT_KEYS)


class DittoNotice(OneTimeNotice):
    """Remembers per person whether the current notice was shown (the mechanism: utils/notice_store.py)."""

    FILE_NAME = "ditto_notice.json"
    TEXT_KEYS = TEXT_KEYS
    AGAIN = "Showing the notice on Ditto again"
    WRONG_VIA = "The notice on Ditto is shown in the GUI or the CLI, not via {via!r}."
