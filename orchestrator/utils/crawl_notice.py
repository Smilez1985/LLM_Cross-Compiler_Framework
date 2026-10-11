#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - One-time notice before the first Deep Ingest

The notice says what the crawler reads, which opt-outs it honours, that the knowledge stays on this computer
and that sharing it needs an explicit release. It is information, not a legal confirmation: whether a site may
be mined is decided by the site's machine-readable opt-outs, which the crawler checks, not by the user.

It is shown once per user and again when its text changes: the state file (per-user state directory, JSON,
mode 0600, written atomically) stores a hash of the notice texts in every language. A missing, damaged or
different hash shows the notice again.
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

    def acknowledge(self) -> None:
        write_private(self.path, json.dumps({
            "notice_sha256": notice_sha256(),
            "acknowledged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))
