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

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from orchestrator.Core.vault_key import write_private
from orchestrator.utils.localization import texts_in_every_form, ui_text

TEXT_KEYS = ("chat.ditto.notice.title", "chat.ditto.notice.text")
VIA = ("gui", "cli")   # the framework's own windows where a person reads the notice

logger = logging.getLogger(__name__)


def notice_text(lang: Optional[str] = None) -> Tuple[str, str]:
    """(title, text) of the notice in lang (default: the UI language)."""
    return ui_text(TEXT_KEYS[0], lang), ui_text(TEXT_KEYS[1], lang)


def notice_sha256() -> str:
    """Changes whenever the text in any language changes, also its formal ("Sie") form."""
    texts = texts_in_every_form(TEXT_KEYS)
    return hashlib.sha256(json.dumps(texts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class DittoNotice:
    """Remembers per person whether the current notice was shown."""

    FILE_NAME = "ditto_notice.json"

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / self.FILE_NAME

    def needed(self) -> bool:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["notice_sha256"] != notice_sha256()
        except FileNotFoundError:
            return True
        except (OSError, ValueError, TypeError, KeyError) as e:   # unreadable or damaged: show the notice again
            logger.info(f"Showing the notice on Ditto again: {self.path} cannot be used ({str(e)[:200]}).")
            return True

    def acknowledge(self, via: str) -> None:
        """Records that a person was shown the current notice, and where (`via`: gui or cli)."""
        if via not in VIA:
            raise ValueError(f"The notice on Ditto is shown in the GUI or the CLI, not via {via!r}.")
        write_private(self.path, json.dumps({
            "notice_sha256": notice_sha256(), "via": via,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))

    def acknowledged_via(self) -> Optional[str]:
        """Where the current notice was shown (gui or cli); None when it is needed."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("notice_sha256") != notice_sha256():
            return None
        via = data.get("via")
        return via if via in VIA else None
