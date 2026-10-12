#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Shared build state (MCP design T11)

Every framework process (the GUI, a CLI call, each MCP server) writes one small summary per build request into the
per-user folder (<user_state_dir>/build_requests/<request_id>.json) whenever the request changes: what it builds (the
model by its repo id or label, never a host path), its status and stage, its progress, who started it (the caller
class) and which process runs it. So the GUI status bar, `llm-cli build status`, list_build_requests and
get_build_status see the same builds whichever process started them, including requests that still wait in a queue
(the build engine's records in the cache folder only exist once a build runs).

A request whose process has ended while it was not finished is reported as interrupted: its process (pid plus start
time, so a reused pid does not count) no longer exists. Nothing here starts, stops or changes a build; another
process's requests are read-only.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.Core.build_records import _private_folder, is_alive, this_process
from orchestrator.utils.helpers import redact_secrets

REQUESTS_FOLDER = "build_requests"
FINISHED = ("completed", "error", "cancelled")
KEEP_FINISHED = 200       # older finished summaries are removed when a new request is queued
MAX_TEXT = 2000           # one error, warning or stage
MAX_MESSAGES = 5          # the newest errors and warnings kept per request
SUMMARY_KEYS = ("model", "target", "format", "board", "quantization", "optimization", "revision")
PROGRAMS = ("gui", "cli", "mcp", "api", "framework")

_PROGRAM = {"kind": "framework"}


def set_program(kind: str) -> None:
    """Which kind of program this process is (gui, cli, mcp, api); set once by its entry point and written into
    every summary, so a person sees where a build runs and where to stop it."""
    _PROGRAM["kind"] = kind if kind in PROGRAMS else "framework"


def program() -> str:
    return _PROGRAM["kind"]


def requests_dir(state_dir: Path) -> Path:
    return Path(state_dir) / REQUESTS_FOLDER


def _text(value: Any) -> str:
    return redact_secrets(str(value))[:MAX_TEXT]


def snapshot(state: Any) -> Dict[str, Any]:
    """The summary of a WorkflowState as it is shared: no host path (the model by its label, no artifact paths), the
    owner by its caller class only (never an MCP session key), errors and warnings redacted and capped."""
    from orchestrator.Core.job_service import owner_actor
    status = getattr(getattr(state, "status", None), "value", str(getattr(state, "status", "")))
    summary = getattr(state, "summary", None) or {}
    started, ended = getattr(state, "start_time", None), getattr(state, "end_time", None)
    return {
        "request_id": str(state.request_id),
        "status": status,
        "stage": _text(getattr(state, "current_stage", "") or ""),
        "progress": int(getattr(state, "progress_percent", 0) or 0),
        "total_builds": int(getattr(state, "total_builds", 0) or 0),
        "completed_builds": int(getattr(state, "completed_builds", 0) or 0),
        "failed_builds": int(getattr(state, "failed_builds", 0) or 0),
        "artifacts": len(getattr(state, "artifacts", []) or []),
        "errors": [_text(e) for e in list(getattr(state, "errors", []) or [])[-MAX_MESSAGES:]],
        "warnings": [_text(w) for w in list(getattr(state, "warnings", []) or [])[-MAX_MESSAGES:]],
        "awaiting_decision": status == "awaiting_approval",
        "owner": owner_actor(getattr(state, "owner", "") or ""),
        "label": _text(getattr(state, "label", "") or ""),
        "summary": {k: (None if summary.get(k) is None else str(summary.get(k))) for k in SUMMARY_KEYS},
        "program": program(),
        "started": started.isoformat(timespec="seconds") if hasattr(started, "isoformat") else None,
        "ended": ended.isoformat(timespec="seconds") if hasattr(ended, "isoformat") else None,
    }


class RequestStore:
    """The build request summaries in the per-user folder. Thread-safe within a process; files are written
    atomically, so a reader in another process never sees half a summary."""

    def __init__(self, state_dir: Path):
        self.folder = requests_dir(Path(state_dir))
        self._lock = threading.Lock()
        self._last: Dict[str, str] = {}  # request id -> the summary last written (unchanged ones are not rewritten)
        self._process = this_process()

    def save(self, state: Any) -> bool:
        """Writes the request's summary when it changed; True when a file was written. Errors are raised (the
        caller decides; a summary that cannot be written never stops a build)."""
        data = snapshot(state)
        data.update(self._process)
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        rid = data["request_id"]
        with self._lock:
            if self._last.get(rid) == text:
                return False
            data["updated"] = datetime.now().isoformat(timespec="seconds")
            _private_folder(self.folder)
            path = self.folder / f"{rid}.json"
            tmp = self.folder / f".{rid}.{os.getpid()}.{threading.get_ident()}.tmp"
            tmp.unlink(missing_ok=True)
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp, path)
            self._last[rid] = text
            return True

    def read(self) -> List[Dict[str, Any]]:
        """Every readable summary, newest first. An unfinished request whose process has ended is marked
        `interrupted` (its stored status is kept as `last_status`)."""
        return read_requests(self.folder)

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        for record in self.read():
            if record.get("request_id") == request_id:
                return record
        return None

    def prune(self, keep: int = KEEP_FINISHED) -> None:
        """Removes the oldest finished (or interrupted) summaries beyond `keep`; requests that still run stay."""
        finished = [(str(r.get("started") or ""), r) for r in self.read()
                    if r.get("status") in FINISHED or r.get("status") == "interrupted"]
        finished.sort(key=lambda pair: pair[0], reverse=True)
        for _, record in finished[keep:]:
            try:
                (self.folder / f"{record['request_id']}.json").unlink()
            except OSError:
                pass


def read_requests(folder: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not Path(folder).is_dir():
        return records
    for path in Path(folder).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("request_id") or path.stem != data.get("request_id"):
            continue
        if data.get("status") not in FINISHED and not is_alive(data):
            data["last_status"] = data.get("status")
            data["status"] = "interrupted"
        records.append(data)
    records.sort(key=lambda r: str(r.get("started") or ""), reverse=True)
    return records


def for_config(config: Any) -> Optional[RequestStore]:
    """The store of a ConfigManager (its per-user folder), or None for a stand-in config without one."""
    try:
        value = getattr(config, "user_state_dir", None)
    except Exception:  # noqa: BLE001 - ConfigManager raises AttributeError for unknown names
        value = None
    return RequestStore(Path(value)) if isinstance(value, (str, Path)) else None
