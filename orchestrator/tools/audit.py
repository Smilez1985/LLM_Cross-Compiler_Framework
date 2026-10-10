#!/usr/bin/env python3
"""
Append-only audit log of tool calls and approvals (SQLite, hash-chained).

Each entry stores the hash of its predecessor, so changing or deleting an entry in the middle breaks
the chain. With a key (kept in the OS keyring, not next to the database) the chain is an HMAC chain that
cannot be recomputed by someone who can only edit the database file. The newest entry is also written to
an anchor file after every append; removing the newest entries then shows up as well, unless the anchor
file is removed too (check() reports a missing anchor). Arguments are stored redacted and truncated, plus
a hash of the full text.
"""

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.utils.helpers import redact_secrets

_SCHEMA = (
    """
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
    """,
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
)
_FIELDS = ("ts", "actor", "tool", "tier", "decision", "approved", "args_sha256", "args_preview", "outcome", "detail")
GENESIS = "0" * 64
UNKEYED = "unkeyed"


@dataclass
class AuditCheck:
    """Result of AuditLog.check()."""
    intact: bool
    problem: str = ""            # "", "modified", "removed", "key_mismatch", "anchor_missing"
    entry: Optional[int] = None  # first broken or missing entry
    keyed: bool = False
    entries: int = 0

    @property
    def message(self) -> str:
        if self.problem == "modified":
            return f"Audit log was modified at entry {self.entry}."
        if self.problem == "removed":
            return f"Audit entries were removed (from entry {self.entry} on)."
        if self.problem == "key_mismatch":
            return ("The audit log was written with a different key than the one in this user's keyring; "
                    "it cannot be checked (keyring reset or another user's log).")
        if self.problem == "anchor_missing":
            return ("The audit anchor file is missing: changes are still detected, but removal of the newest "
                    "entries is not.")
        suffix = "" if self.keyed else " (unkeyed: no keyring available, so the chain could be recomputed)"
        return f"Audit log intact, {self.entries} entries{suffix}."


class AuditLog:
    def __init__(self, path: Path, key: Optional[bytes] = None):
        """key: optional secret (from the keyring). The database is opened on first use, so a damaged or
        unreachable file never stops the framework from starting; record() failures are the caller's to log."""
        self.path = Path(path)
        self.anchor_path = self.path.with_name(self.path.name + ".anchor")
        self._key = key
        self._lock = threading.Lock()
        self._ready = False

    @property
    def keyed(self) -> bool:
        return bool(self._key)

    def _key_id(self) -> str:
        if not self._key:
            return UNKEYED
        return hmac.new(self._key, b"llm-framework-audit-key-id", hashlib.sha256).hexdigest()[:16]

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)  # explicit transactions below
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError as e:
            if "not a database" in str(e) or "malformed" in str(e):
                db.close()
                raise
            # e.g. network filesystems without shared memory: the default journal works too
        if not self._ready:
            for statement in _SCHEMA:
                db.execute(statement)
            self._ready = True
        return db

    def _digest(self, prev_hash: str, row: Dict[str, Any]) -> str:
        payload = (prev_hash + json.dumps([row[f] for f in _FIELDS], ensure_ascii=False)).encode("utf-8")
        if self._key:
            return hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def _anchor_mac(self, entry_id: int, entry_hash: str) -> str:
        payload = f"anchor:{entry_id}:{entry_hash}".encode("ascii")
        if self._key:
            return hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def _write_anchor(self, entry_id: int, entry_hash: str) -> None:
        data = {"id": entry_id, "hash": entry_hash, "mac": self._anchor_mac(entry_id, entry_hash)}
        fd, tmp = tempfile.mkstemp(dir=self.anchor_path.parent, prefix=".audit.anchor.")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.anchor_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read_anchor(self) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self.anchor_path.read_text(encoding="ascii"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {}

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
        with self._lock:
            db = self._connect()
            try:
                # One write transaction for reading the last hash and appending: other processes (GUI, CLI,
                # one MCP server per AI client session) wait instead of forking the chain.
                db.execute("BEGIN IMMEDIATE")
                try:
                    if db.execute("SELECT v FROM meta WHERE k = 'key_id'").fetchone() is None:
                        db.execute("INSERT INTO meta (k, v) VALUES ('key_id', ?)", (self._key_id(),))
                    last = db.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
                    prev = last[0] if last else GENESIS
                    digest = self._digest(prev, row)
                    cur = db.execute(
                        f"INSERT INTO events ({', '.join(_FIELDS)}, prev_hash, hash) "
                        f"VALUES ({', '.join('?' * (len(_FIELDS) + 2))})",
                        [row[f] for f in _FIELDS] + [prev, digest])
                    entry_id = cur.lastrowid
                    # Written while the write lock is held, so the anchor never moves backwards.
                    self._write_anchor(entry_id, digest)
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
            finally:
                db.close()

    def entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        db = self._connect()
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            db.close()
        return [dict(r) for r in rows]

    def check(self) -> AuditCheck:
        """Walks the whole chain and compares its head with the anchor file."""
        db = self._connect()
        try:
            db.row_factory = sqlite3.Row
            stored_key = db.execute("SELECT v FROM meta WHERE k = 'key_id'").fetchone()
            if stored_key is not None and stored_key["v"] != self._key_id():
                return AuditCheck(False, "key_mismatch", keyed=self.keyed)
            prev, count, hashes = GENESIS, 0, {}
            for r in db.execute("SELECT * FROM events ORDER BY id"):
                row = dict(r)
                if row["prev_hash"] != prev or self._digest(prev, row) != row["hash"]:
                    return AuditCheck(False, "modified", row["id"], self.keyed, count)
                prev = row["hash"]
                hashes[row["id"]] = row["hash"]
                count += 1
        finally:
            db.close()
        anchor = self._read_anchor()
        if anchor is None:
            if count:
                return AuditCheck(False, "anchor_missing", keyed=self.keyed, entries=count)
            return AuditCheck(True, keyed=self.keyed, entries=0)
        try:
            anchor_id, anchor_hash = int(anchor["id"]), str(anchor["hash"])
            valid_mac = hmac.compare_digest(str(anchor["mac"]), self._anchor_mac(anchor_id, anchor_hash))
        except (KeyError, TypeError, ValueError):
            return AuditCheck(False, "anchor_missing", keyed=self.keyed, entries=count)
        if not valid_mac:
            return AuditCheck(False, "modified", anchor_id, self.keyed, count)
        if hashes.get(anchor_id) != anchor_hash:
            # The newest entry the log had is gone (or was rewritten together with everything before it).
            return AuditCheck(False, "removed", anchor_id, self.keyed, count)
        return AuditCheck(True, keyed=self.keyed, entries=count)

    def verify(self) -> Optional[int]:
        """None if the log is intact; else the first broken or missing entry (0 if no single entry is to blame)."""
        result = self.check()
        if result.intact:
            return None
        return result.entry or 0
