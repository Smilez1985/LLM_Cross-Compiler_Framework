#!/usr/bin/env python3
"""
A notice a person is shown once, and again when its text changes (consolidation chain c1-imports: the Deep Ingest
notice, utils/crawl_notice.py, and the notice on Ditto, utils/ditto_notice.py, were the same class twice; they are
subclasses of OneTimeNotice now and keep their files and hashes).

The state file (per-user state directory, JSON, mode 0600, written atomically) stores a hash of the notice texts in
every language and form (notice_sha256), when the person was shown the notice and through which of the framework's
own windows (`via`: gui or cli). A missing, damaged or different hash shows the notice again. Only a person
acknowledges a notice, in the GUI or the CLI; no tool, resource or prompt can.

A subclass names its FILE_NAME, its TEXT_KEYS (title, text), AGAIN (the start of the log line when its state file
cannot be used) and WRONG_VIA (the refusal of another window, with {via!r}). It logs through its own module's logger.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional, Sequence, Tuple

from orchestrator.utils.atomic import write_private
from orchestrator.utils.localization import texts_in_every_form, ui_text

VIA = ("gui", "cli")   # the framework's own windows where a person reads a notice


def notice_text(text_keys: Sequence[str], lang: Optional[str] = None) -> Tuple[str, str]:
    """(title, text) of a notice in lang (default: the UI language)."""
    return ui_text(text_keys[0], lang), ui_text(text_keys[1], lang)


def notice_sha256(text_keys: Sequence[str]) -> str:
    """Changes whenever the text in any language changes, also its formal ("Sie") form."""
    texts = texts_in_every_form(text_keys)
    return hashlib.sha256(json.dumps(texts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class OneTimeNotice:
    """Remembers per person whether the current notice was shown."""

    FILE_NAME: ClassVar[str] = ""
    TEXT_KEYS: ClassVar[Tuple[str, str]] = ("", "")
    VIA: ClassVar[Tuple[str, ...]] = VIA
    AGAIN: ClassVar[str] = "Showing the notice again"
    WRONG_VIA: ClassVar[str] = "The notice is acknowledged in the GUI or the CLI, not via {via!r}."

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / self.FILE_NAME

    def sha256(self) -> str:
        return notice_sha256(self.TEXT_KEYS)

    def needed(self) -> bool:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["notice_sha256"] != self.sha256()
        except FileNotFoundError:
            return True
        except (OSError, ValueError, TypeError, KeyError) as e:   # unreadable or damaged: show the notice again
            logging.getLogger(type(self).__module__).info(
                f"{self.AGAIN}: {self.path} cannot be used ({str(e)[:200]}).")
            return True

    def acknowledge(self, via: str) -> None:
        """Records that a person was shown the current notice, and where (`via`: gui or cli)."""
        if via not in self.VIA:
            raise ValueError(self.WRONG_VIA.format(via=via))
        write_private(self.path, json.dumps({
            "notice_sha256": self.sha256(), "via": via,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))

    def acknowledged_via(self) -> Optional[str]:
        """Where the current notice was shown (gui or cli); None when it is needed or was recorded without."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("notice_sha256") != self.sha256():
            return None
        via = data.get("via")
        return via if via in self.VIA else None


__all__ = ["OneTimeNotice", "VIA", "notice_sha256", "notice_text"]
