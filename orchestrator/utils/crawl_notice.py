#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - One-time notice before the first Deep Ingest

The notice says what the crawler reads, which opt-outs it honours, that the knowledge stays on this computer
and that sharing it needs an explicit release. It is information, not a legal confirmation: whether a site may
be mined is decided by the site's machine-readable opt-outs, which the crawler checks, not by the user.

It is shown once per user and again when its text changes: the state file (per-user state directory, JSON,
mode 0600, written atomically) stores NOTICE_VERSION and a hash of the English and German texts. A missing,
damaged or older state file shows the notice again.
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.utils.localization import LocalizationManager, ui_text

NOTICE_VERSION = 1
TEXT_KEYS = ("crawl.notice.title", "crawl.notice.text")

logger = logging.getLogger(__name__)


def notice_text(lang: Optional[str] = None) -> Tuple[str, str]:
    """(title, text) of the notice in lang (default: the UI language)."""
    return ui_text(TEXT_KEYS[0], lang), ui_text(TEXT_KEYS[1], lang)


def notice_sha256() -> str:
    """Changes whenever the version or the text in any language changes."""
    texts = {lang: [table.get(key, "") for key in TEXT_KEYS]
             for lang, table in sorted(LocalizationManager.TRANSLATIONS.items())}
    data = json.dumps([NOTICE_VERSION, texts], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class NoticeAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    notice_version: int
    notice_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_at: str
    via: Literal["gui", "cli"]


class CrawlNotice:
    """Remembers per user whether the current notice was shown."""

    FILE_NAME = "crawl_notice.json"

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / self.FILE_NAME

    def needed(self) -> bool:
        try:
            ack = NoticeAcknowledgement.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return True
        except (OSError, ValueError) as e:   # unreadable or damaged: show the notice again
            logger.info(f"Showing the Deep Ingest notice again: {self.path} cannot be used ({str(e)[:200]}).")
            return True
        return ack.notice_version != NOTICE_VERSION or ack.notice_sha256 != notice_sha256()

    def acknowledge(self, via: Literal["gui", "cli"]) -> None:
        ack = NoticeAcknowledgement(notice_version=NOTICE_VERSION, notice_sha256=notice_sha256(), via=via,
                                    acknowledged_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".crawl_notice.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ack.model_dump_json(indent=2))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
