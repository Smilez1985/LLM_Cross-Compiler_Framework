#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the note of an open Mojo Oracle run (Mojo closes the circle)

After Mojo Oracle ran for the person at a terminal, a later Mojo Miracle run of the person closes the circle: the
console shows the crystal ball (Core/mojo/banner.py, the CLI's drawing). The open Oracle run is one small private file
next to Mojo Miracle's lock in the per-user state folder, holding only "open", "cli" and the time (no path, no account
name).

The note's lifetime (decided as the Mojo circle follow-ups):
- it expires after one hour (OPEN_FOR): a note older than that, one whose time is in the future or cannot be read, or
  a file that is no note at all counts as not open and is removed;
- every Miracle run of the person closes it: `llmcf mojo --miracle` at a terminal (with its logo), and without a logo
  `llmcf mojo --miracle --json`, `llm-cli doctor --fix` (also with --json) and a Mojo Miracle run in the GUI (through
  Mojo.miracle(closes_circle=True), so no GUI code writes the file); an AI's Miracle run (run_mojo_miracle) leaves it;
- record_oracle, oracle_open and close_oracle never raise, whatever is in the state folder (a folder in place of the
  file, bad JSON, a non-dict, a read-only folder, any other error); only the error's type name is logged.

This module is Qt-free and draws nothing; banner.py re-exports these names for the CLI.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from orchestrator.utils.atomic import remove_file, write_private

logger = logging.getLogger(__name__)

RECORD_NAME = "oracle_open.json"
# How long an open Oracle run waits for its Miracle run.
OPEN_FOR = timedelta(hours=1)


def record_path(framework) -> Path:
    """Where the open Oracle run is noted: next to Mojo Miracle's lock in the per-user state folder (of this
    LLM_FRAMEWORK_HOME)."""
    from orchestrator.Core.mojo.repairs.engine import lock_path
    return lock_path(framework).with_name(RECORD_NAME)


def record_oracle(framework) -> None:
    """Notes that Mojo Oracle ran for the person at a terminal (the CLI); a later Miracle run of the person closes the
    circle. Only "open", "cli" and the time (UTC, ISO): no path, no account name. Never raises."""
    try:
        write_private(record_path(framework), json.dumps(
            {"mojo_oracle": "open", "via": "cli",
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    except Exception as e:  # noqa: BLE001 - e.g. a read-only state folder: no greeting later, Mojo itself unchanged
        logger.debug("The open Mojo Oracle run could not be noted: %s", type(e).__name__)


def _still_open(data, now: datetime) -> bool:
    if not isinstance(data, dict) or data.get("mojo_oracle") != "open":
        return False
    at = data.get("at")
    if not isinstance(at, str):
        return False
    when = datetime.fromisoformat(at)
    if when.tzinfo is None or when.utcoffset() is None:
        return False
    return when <= now and now - when <= OPEN_FOR


def oracle_open(framework, now: Optional[datetime] = None) -> bool:
    """Whether an Oracle run of the person at a terminal is still open: noted, not closed by a Miracle run and at most
    OPEN_FOR old. Anything else (expired, in the future, unreadable, no note) is not open, and a file that is there is
    removed. Never raises."""
    now = now or datetime.now(timezone.utc)
    try:
        path = record_path(framework)
        if not (path.exists() or path.is_symlink()):
            return False
        if _still_open(json.loads(path.read_text(encoding="utf-8")), now):
            return True
    except Exception as e:  # noqa: BLE001 - a folder in place of the file, bad JSON, bad time, a broken double
        logger.debug("The note of the open Mojo Oracle run is not usable: %s", type(e).__name__)
    close_oracle(framework)
    return False


def close_oracle(framework) -> None:
    """Removes the note of the open Oracle run (a Miracle run of the person ended, whatever its outcome). Never
    raises."""
    try:
        remove_file(record_path(framework))
    except Exception as e:  # noqa: BLE001 - e.g. a folder in place of the file or a read-only state folder
        logger.debug("The note of the open Mojo Oracle run could not be removed: %s", type(e).__name__)


__all__ = ["OPEN_FOR", "RECORD_NAME", "close_oracle", "oracle_open", "record_oracle", "record_path"]
