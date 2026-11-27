#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - reading the framework's log (step 2 of "One log the person and Ditto read")

framework.log (utils/crash_report.add_framework_log) holds one JSON object per line from every process; when a
process starts, a long file moves to framework.log.1 and on. This module reads them, oldest first, and filters them
the same way for every reader: `llmcf logs show` and `llmcf logs export`, the Help menu's Framework Log window, and
utils/logging's LoggingManager.export_logs. Qt-free; it only reads, except export(), which writes the one file the
person named.

- read_entries(folder, ...): the entries of framework.log and its old files, filtered by level, time and part of the
  framework (the logger name), the newest `limit` of them, oldest first. A line that is no JSON object is skipped
  (a half-written last line, a foreign file).
- entry_text(entry): one entry as one readable line.
- A step's duration (utils/logging.log_step, level PERFORMANCE) is ranked as INFO, TRACE as DEBUG.
- export(entries, path, fmt): the entries as JSON or text in `path`, private, with the home folder written as
  ~USER (security/paths.hide_home), for a bug report or for the person's own look.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FRAMEWORK_LOG = "framework.log"
FRAMEWORK_LOG_BYTES = 5 * 1024 * 1024       # rotated when a process starts and the file is larger
FRAMEWORK_LOG_BACKUPS = 3                   # framework.log.1 to .3
FRAMEWORK_LOG_HANDLER = "llmcf-framework-log"   # the root logger's handler that writes it (crash_report)
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
FORMATS = ("json", "text")
MAX_LINE_BYTES = 64 * 1024   # a longer line is no entry of the framework (its records are cut long before)


def log_files(folder: Path) -> List[Path]:
    """framework.log and its old files in `folder`, oldest first (framework.log.3, .2, .1, then framework.log)."""
    folder = Path(folder)
    names = [f"{FRAMEWORK_LOG}.{n}" for n in range(FRAMEWORK_LOG_BACKUPS, 0, -1)] + [FRAMEWORK_LOG]
    return [folder / name for name in names if (folder / name).is_file()]


RANKED_AS = {"PERFORMANCE": "INFO", "TRACE": "DEBUG"}   # the framework's own levels (utils/logging.LogLevel)


def _level_rank(level: Any) -> int:
    name = str(level or "").upper()
    name = RANKED_AS.get(name, name)
    return LEVELS.index(name) if name in LEVELS else 0


def _moment(entry: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp") or ""))
    except ValueError:
        return None


def _lines(path: Path) -> Iterable[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if len(line) <= MAX_LINE_BYTES:
                    yield line
    except OSError:
        return


def read_entries(folder: Path, *, level: str = "DEBUG", since: Optional[datetime] = None,
                 until: Optional[datetime] = None, part: str = "", limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """The entries of the framework's log in `folder` at `level` or above, from `since` to `until` (local time, as
    the log writes it), whose part of the framework (the logger name) contains `part` (any case), the newest `limit`
    of them (all: None), oldest first. Never raises for the files' state: what cannot be read is left out."""
    floor = _level_rank(level)
    wanted = str(part or "").lower()
    found: List[Dict[str, Any]] = []
    for path in log_files(folder):
        for line in _lines(path):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or _level_rank(entry.get("level")) < floor:
                continue
            if wanted and wanted not in str(entry.get("logger_name") or "").lower():
                continue
            if since is not None or until is not None:
                moment = _moment(entry)
                if moment is None or (since is not None and moment < since) or (until is not None and moment > until):
                    continue
            found.append(entry)
    return found[-limit:] if limit else found


def minutes_ago(minutes: Optional[float]) -> Optional[datetime]:
    """The moment `minutes` ago (None for none or 0: no limit)."""
    return datetime.now() - timedelta(minutes=float(minutes)) if minutes else None


def entry_text(entry: Dict[str, Any]) -> str:
    """One entry as one readable line: time, level, part, program and process, message, and an error's type."""
    when = str(entry.get("timestamp") or "")[:19].replace("T", " ")
    extra = entry.get("extra_fields") if isinstance(entry.get("extra_fields"), dict) else {}
    who = " ".join(str(v) for v in (extra.get("program"), extra.get("pid")) if v not in (None, ""))
    message = " ".join(str(entry.get("message") or "").split())
    error = entry.get("exception_type")
    return (f"{when} {entry.get('level', '?')} {entry.get('logger_name', '?')}"
            + (f" [{who}]" if who else "") + f": {message}" + (f" ({error})" if error else ""))


def export(entries: List[Dict[str, Any]], path: Path, fmt: str = "json") -> Path:
    """`entries` written to `path` (private: 0600 where the system has modes), as JSON ({export_time, total_entries,
    entries}) or text (one line per entry, an error's trace below it), with this computer's home folder written as
    ~USER in every spelling (security/paths.hide_home): ready to look at or to attach to a bug report. ValueError for
    another format; OSError when the file cannot be written."""
    if fmt not in FORMATS:
        raise ValueError(f"the format is json or text, not '{fmt}'")
    from orchestrator.security.paths import hide_home
    from orchestrator.utils.atomic import write_private
    if fmt == "json":
        text = json.dumps({"export_time": datetime.now().isoformat(timespec="seconds"),
                           "total_entries": len(entries), "entries": entries}, indent=2, default=str)
    else:
        lines = []
        for entry in entries:
            lines.append(entry_text(entry))
            if entry.get("stack_trace"):
                lines.extend("    " + line for line in str(entry["stack_trace"]).rstrip().splitlines())
        text = "\n".join(lines) + ("\n" if lines else "")
    path = Path(path)
    write_private(path, hide_home(text))
    return path


__all__ = ["FORMATS", "FRAMEWORK_LOG", "FRAMEWORK_LOG_BACKUPS", "FRAMEWORK_LOG_BYTES", "FRAMEWORK_LOG_HANDLER", "LEVELS", "entry_text", "export", "log_files", "minutes_ago", "read_entries"]
