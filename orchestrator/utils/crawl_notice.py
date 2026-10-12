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

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from orchestrator.Core.vault_key import write_private
from orchestrator.utils.localization import LocalizationManager, ui_text

TEXT_KEYS = ("crawl.notice.title", "crawl.notice.text")
VIA = ("gui", "cli")   # the framework's own windows where a person acknowledges the notice

logger = logging.getLogger(__name__)


def notice_text(lang: Optional[str] = None) -> Tuple[str, str]:
    """(title, text) of the notice in lang (default: the UI language)."""
    return ui_text(TEXT_KEYS[0], lang), ui_text(TEXT_KEYS[1], lang)


def notice_sha256() -> str:
    """Changes whenever the text in any language changes."""
    texts = {lang: [table.get(key, "") for key in TEXT_KEYS]
             for lang, table in sorted(LocalizationManager.TRANSLATIONS.items())}
    return hashlib.sha256(json.dumps(texts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class CrawlNotice:
    """Remembers per user whether the current notice was shown."""

    FILE_NAME = "crawl_notice.json"

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / self.FILE_NAME

    def needed(self) -> bool:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["notice_sha256"] != notice_sha256()
        except FileNotFoundError:
            return True
        except (OSError, ValueError, TypeError, KeyError) as e:   # unreadable or damaged: show the notice again
            logger.info(f"Showing the Deep Ingest notice again: {self.path} cannot be used ({str(e)[:200]}).")
            return True

    def acknowledge(self, via: str) -> None:
        """Records that a person read the current notice, and where (`via`: gui or cli)."""
        if via not in VIA:
            raise ValueError(f"The Deep Ingest notice is acknowledged in the GUI or the CLI, not via {via!r}.")
        write_private(self.path, json.dumps({
            "notice_sha256": notice_sha256(), "via": via,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))

    def acknowledged_via(self) -> Optional[str]:
        """Where the current notice was acknowledged (gui or cli); None when it is needed or was recorded without."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("notice_sha256") != notice_sha256():
            return None
        via = data.get("via")
        return via if via in VIA else None
