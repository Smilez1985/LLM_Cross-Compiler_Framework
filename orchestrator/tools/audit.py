#!/usr/bin/env python3
"""
Append-only audit log of tool calls and approvals (SQLite, hash-chained).

Each entry stores the hash of its predecessor; changing or deleting an entry breaks the chain,
which verify() reports. Arguments are stored redacted and truncated, plus a hash of the full text.
"""

import hashlib
import hmac
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.utils.helpers import redact_secrets

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    tier TEXT NOT NULL,
    decision TEXT NOT NULL,
    approved INTEGER,
    args_sha256 TEXT NOT NULL,
    args_preview TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
)
"""
_FIELDS = ("ts", "actor", "tool", "tier", "decision", "approved", "args_sha256", "args_preview", "outcome", "detail")
GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path, key: Optional[bytes] = None):
        """key: optional secret (e.g. from the keyring); with it the chain is an HMAC chain that
        cannot be recomputed by someone who can only edit the database file."""
        self.path = Path(path)
        self._key = key
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _digest(self, prev_hash: str, row: Dict[str, Any]) -> str:
        payload = (prev_hash + json.dumps([row[f] for f in _FIELDS], ensure_ascii=False)).encode("utf-8")
        if self._key:
            return hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def record(self, actor: str, tool: str, tier: str, decision: str, outcome: str,
               arguments: Any = None, approved: Optional[bool] = None, detail: str = "") -> None:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str) if arguments is not None else ""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": actor, "tool": tool, "tier": tier, "decision": decision,
            "approved": None if approved is None else int(bool(approved)),
            "args_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "args_preview": redact_secrets(text)[:500],
            "outcome": outcome,
            "detail": redact_secrets(detail or "")[:1000],
        }
        with self._lock, self._connect() as db:
            last = db.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            prev = last[0] if last else GENESIS
            db.execute(
                f"INSERT INTO events ({', '.join(_FIELDS)}, prev_hash, hash) VALUES ({', '.join('?' * (len(_FIELDS) + 2))})",
                [row[f] for f in _FIELDS] + [prev, self._digest(prev, row)])

    def entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def verify(self) -> Optional[int]:
        """Returns None if the chain is intact, else the id of the first broken entry."""
        prev = GENESIS
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            for r in db.execute("SELECT * FROM events ORDER BY id"):
                row = dict(r)
                if row["prev_hash"] != prev or self._digest(prev, row) != row["hash"]:
                    return row["id"]
                prev = row["hash"]
        return None
