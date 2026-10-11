#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Build records

Every build engine (CLI, GUI, MCP server) writes one small JSON record per build into
<cache_dir>/build_records/: what is built, by which process, its status and stage. `llm-cli build status`
reads them, so it also shows builds that another process started. A record whose process has ended
while the build was not finished is reported as interrupted.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from orchestrator.utils.helpers import redact_secrets

RECORDS_FOLDER = "build_records"
FINISHED = ("completed", "failed", "cancelled")
KEEP_FINISHED = 200  # older finished records are removed when a new build starts
MAX_ERROR = 2000


def records_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / RECORDS_FOLDER


def _process_started(pid: int) -> Optional[float]:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError, ValueError):
        return None


def this_process() -> Dict[str, Any]:
    """pid and start time of this process: together they identify it even after its pid is reused."""
    pid = os.getpid()
    return {"pid": pid, "process_started": _process_started(pid)}


def write_record(folder: Path, record: Dict[str, Any]) -> None:
    """Writes the record atomically (readers never see half a file). Errors and stages are redacted."""
    data = dict(record)
    for key in ("error", "stage"):
        if isinstance(data.get(key), str):
            data[key] = redact_secrets(data[key])[:MAX_ERROR]
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{data['build_id']}.json"
    tmp = folder / f".{data['build_id']}.{os.getpid()}.{threading.get_ident()}.tmp"  # one per writing thread
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def is_alive(record: Dict[str, Any]) -> bool:
    """Whether the process that runs the build still exists (same pid and same start time)."""
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    started = _process_started(pid)
    recorded = record.get("process_started")
    if started is None:
        return False
    return recorded is None or abs(float(recorded) - started) < 2.0


def read_records(folder: Path) -> List[Dict[str, Any]]:
    """All readable records, newest first. An unfinished build whose process has ended is marked interrupted."""
    records = []
    if not folder.is_dir():
        return records
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("build_id"):
            continue
        if data.get("status") not in FINISHED and not is_alive(data):
            data["status"] = "interrupted"
        records.append(data)
    records.sort(key=lambda r: str(r.get("started") or ""), reverse=True)
    return records


def prune(folder: Path, keep: int = KEEP_FINISHED) -> None:
    """Removes the oldest finished records beyond `keep`; unfinished ones stay."""
    if not folder.is_dir():
        return
    finished = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("status") in FINISHED:
            finished.append((str(data.get("started") or ""), path))
    finished.sort(reverse=True)
    for _, path in finished[keep:]:
        try:
            path.unlink()
        except OSError:
            pass
