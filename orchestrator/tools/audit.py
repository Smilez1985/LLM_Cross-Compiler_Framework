#!/usr/bin/env python3
"""
Append-only audit log of tool calls and approvals (SQLite, hash-chained).

Each entry stores the hash of its predecessor, so changing or deleting an entry in the middle breaks
the chain. With a key (kept in the OS keyring, not next to the database) the chain is an HMAC chain that
cannot be recomputed by someone who can only edit the database file. Every entry names the key it was
written with: unkeyed entries may only come before the first keyed one, and a writer with another key (or
none) is refused instead of appending entries nobody can verify. The newest entry is also written to an
anchor file. Before each append the anchor is compared with the chain, and a removal it reveals (of the
newest entries or of the anchor itself) is stored in the database, so it stays reported after later
appends. Arguments are stored redacted and truncated, plus a hash of the full text.
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
from typing import Any, Dict, List, Optional, Tuple

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
        hash TEXT NOT NULL,
        key_id TEXT
    )
    """,
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
)
_FIELDS = ("ts", "actor", "tool", "tier", "decision", "approved", "args_sha256", "args_preview", "outcome", "detail")
_INSERT = ("INSERT INTO events (ts, actor, tool, tier, decision, approved, args_sha256, args_preview, outcome, detail, "
           "prev_hash, hash, key_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
GENESIS = "0" * 64
UNKEYED = "unkeyed"
_BREAKS = ("removed", "modified", "anchor_missing")
_ANCHOR_MAX = 4096  # a real anchor has about 170 bytes

# How an entry verifies for the reader: with its key, unkeyed, with a key it does not have, or not at all.
_OWN, _PLAIN, _FOREIGN, _BAD = "own", "plain", "foreign", "bad"


class AuditKeyMismatch(RuntimeError):
    """The log is kept with another key than this process has; its entries could not be verified."""


@dataclass
class AuditCheck:
    """Result of AuditLog.check()."""
    intact: bool
    problem: str = ""            # "", "modified", "removed", "key_mismatch", "anchor_missing"
    entry: Optional[int] = None  # first broken or missing entry
    keyed: bool = False          # protected by this user's key
    entries: int = 0
    key_available: bool = False  # this user has a key, so the next entry is keyed

    @property
    def message(self) -> str:
        if self.problem == "modified":
            return f"Audit log was modified at entry {self.entry}." if self.entry else "Audit log was modified."
        if self.problem == "removed":
            return f"Audit entries were removed (from entry {self.entry} on)."
        if self.problem == "key_mismatch":
            where = f" from entry {self.entry} on" if self.entry else ""
            return (f"The audit log cannot be verified{where}: it was written with a different key than the one "
                    "in this user's keyring (keyring reset or unavailable, another user's log), or it was "
                    "tampered with.")
        if self.problem == "anchor_missing":
            if self.entry:
                return (f"The audit anchor file was missing when entry {self.entry} was written: removal of the "
                        "newest entries before it cannot be ruled out.")
            return ("The audit anchor file is missing: changes are still detected, but removal of the newest "
                    "entries is not.")
        if self.keyed:
            suffix = ""
        elif self.key_available:
            suffix = " (unkeyed so far: written before a key was available; the next entry is keyed)"
        else:
            suffix = " (unkeyed: no keyring available, so the chain could be recomputed)"
        return f"Audit log intact, {self.entries} entries{suffix}."


def _text(value: str) -> str:
    """Text SQLite can store: lone surrogates (valid in Python's JSON, e.g. from model output) become escapes."""
    return value.encode("utf-8", "backslashreplace").decode("utf-8")


def _same(expected: str, stored: Any) -> bool:
    """Constant-time comparison that also copes with whatever an editor put into the database or anchor."""
    return hmac.compare_digest(expected.encode("utf-8"), str(stored).encode("utf-8", "backslashreplace"))


def _dicts(cursor) -> List[Dict[str, Any]]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _sequence(db) -> int:
    """The highest id the events table ever had (AUTOINCREMENT never reuses ids)."""
    row = db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _rollback(db) -> None:
    try:
        if db.in_transaction:
            db.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _parse_anchor(raw: Optional[bytes]) -> Optional[Tuple[int, str, str]]:
    if raw is None or len(raw) > _ANCHOR_MAX:
        return None
    try:
        data = json.loads(raw.decode("ascii"))
        anchor = int(data["id"]), str(data["hash"]), str(data["mac"])
    except (ValueError, KeyError, TypeError, RecursionError, OverflowError):
        return None
    if not 0 < anchor[0] < 2 ** 63:  # beyond SQLite's integers record() could not even look the entry up
        return None
    return anchor if anchor[1].isascii() and anchor[2].isascii() else None  # JSON escapes allow any text


class AuditLog:
    def __init__(self, path: Path, key: Optional[bytes] = None):
        """key: optional secret (from the keyring). The database is opened on first use, so a damaged or
        unreachable file never stops the framework from starting; record() failures are the caller's to log."""
        self.path = Path(path)
        self.anchor_path = self.path.with_name(self.path.name + ".anchor")
        self._key = key
        self._lock = threading.Lock()

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
            try:
                db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError as e:
                if "not a database" in str(e) or "malformed" in str(e):
                    raise
                # e.g. network filesystems without shared memory: the default journal works too
            # On every connect: the file may have been moved aside since the last one (a new log starts).
            for statement in _SCHEMA:
                db.execute(statement)
            if "key_id" not in {r[1] for r in db.execute("PRAGMA table_info(events)")}:
                try:  # logs from before per-entry key ids
                    db.execute("ALTER TABLE events ADD COLUMN key_id TEXT")
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e):  # another process was first
                        raise
        except BaseException:
            db.close()
            raise
        return db

    @staticmethod
    def _digest(prev_hash: str, row: Dict[str, Any], key_id: Optional[str], key: Optional[bytes]) -> str:
        values = [row[f] for f in _FIELDS]
        if key_id is not None:  # entries from before per-entry key ids are hashed without it
            values.append(key_id)
        payload = (prev_hash + json.dumps(values, ensure_ascii=False, default=repr)).encode("utf-8")
        if key:
            return hmac.new(key, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def _matches(self, row: Dict[str, Any], key_id: Optional[str], key: Optional[bytes]) -> bool:
        return _same(self._digest(str(row["prev_hash"]), row, key_id, key), row["hash"])

    def _classify(self, row: Dict[str, Any], legacy_key_id: Optional[str]) -> str:
        """legacy_key_id: the key id meta names for entries from before per-entry key ids."""
        own = self._key_id()
        kid = row.get("key_id")
        if kid is None:
            if self._key and self._matches(row, None, self._key):
                return _OWN
            if self._matches(row, None, None):
                return _PLAIN
            return _BAD if legacy_key_id == own else _FOREIGN
        if kid == UNKEYED:
            return _PLAIN if self._matches(row, UNKEYED, None) else _BAD
        if self._key and kid == own:
            return _OWN if self._matches(row, own, self._key) else _BAD
        if self._key and self._matches(row, own, self._key):
            return _BAD  # written with this key, only its key id was changed
        return _FOREIGN

    @staticmethod
    def _anchor_mac(entry_id: int, entry_hash: str, key: Optional[bytes]) -> str:
        payload = f"anchor:{entry_id}:{entry_hash}".encode("utf-8")
        if key:
            return hmac.new(key, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def _anchor_valid(self, anchor: Tuple[int, str, str], kind: Optional[str]) -> bool:
        """kind: how the named entry verified (None if it is missing: then any key this reader knows)."""
        if kind == _OWN:
            keys = [self._key]
        elif kind == _PLAIN:
            keys = [None]
        elif kind is None:
            keys = [self._key, None] if self._key else [None]
        else:
            return False
        anchor_id, anchor_hash, mac = anchor
        return any(_same(self._anchor_mac(anchor_id, anchor_hash, k), mac) for k in keys)

    def _write_anchor(self, entry_id: int, entry_hash: str) -> None:
        data = {"id": entry_id, "hash": entry_hash, "mac": self._anchor_mac(entry_id, entry_hash, self._key)}
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

    def _move_anchor(self, db, entry_id: int, entry_hash: str) -> None:
        """Points the anchor at a committed entry. Writers wait meanwhile, and a newer entry moves it itself,
        so the anchor never moves backwards and never names an entry that was rolled back."""
        db.execute("BEGIN IMMEDIATE")
        try:
            head = db.execute("SELECT id FROM events ORDER BY id DESC LIMIT 1").fetchone()
            if head is not None and head[0] == entry_id:
                self._write_anchor(entry_id, entry_hash)
        finally:
            _rollback(db)

    def _read_anchor(self) -> Optional[bytes]:
        """The anchor file's content, None if there is none; other OSErrors are raised."""
        try:
            with open(self.anchor_path, "rb") as f:
                return f.read(_ANCHOR_MAX + 1)
        except FileNotFoundError:
            return None

    def _chain_key_id(self, last: Optional[Dict[str, Any]], meta: Dict[str, str]) -> Optional[str]:
        """The key id the chain continues with (that of its newest entry), None for an empty chain."""
        if last is None:
            return None
        if last["key_id"] is not None:
            return last["key_id"]
        kind = self._classify(last, meta.get("key_id"))
        if kind == _OWN:
            return self._key_id()
        if kind == _PLAIN:
            return UNKEYED
        return meta.get("key_id")

    def _anchor_break(self, db, last: Optional[Dict[str, Any]], meta: Dict[str, str],
                      raw: Optional[bytes]) -> Optional[Tuple[str, Optional[int]]]:
        """Compares the anchor with the chain before an append; an anchor that only lags behind (its entry is
        still there with the same hash) is fine. Returns (problem, entry), entry None for the new entry."""
        head = last["id"] if last else 0
        newest_gone = _sequence(db) > head  # ids are never reused: higher ones existed and were deleted
        anchor = _parse_anchor(raw)
        if anchor is None:
            if newest_gone:
                return "removed", head + 1
            return ("anchor_missing", None) if raw is not None or last is not None else None
        rows = _dicts(db.execute("SELECT * FROM events WHERE id = ?", (anchor[0],)))
        named = rows[0] if rows and str(rows[0]["hash"]) == anchor[1] else None
        if not self._anchor_valid(anchor, self._classify(named, meta.get("key_id")) if named else None):
            return "modified", anchor[0]
        if named is None:
            return "removed", anchor[0]
        if newest_gone:
            return "removed", head + 1
        return None

    def record(self, actor: str, tool: str, tier: str, decision: str, outcome: str,
               arguments: Any = None, approved: Optional[bool] = None, detail: str = "") -> None:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str) if arguments is not None else ""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": _text(actor), "tool": _text(tool), "tier": _text(tier), "decision": _text(decision),
            "approved": None if approved is None else int(bool(approved)),
            "args_sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
            "args_preview": _text(redact_secrets(text))[:500],
            "outcome": _text(outcome),
            "detail": _text(redact_secrets(detail or ""))[:1000],
        }
        with self._lock:
            db = self._connect()
            try:
                # One write transaction for checking the anchor, reading the last hash and appending: other
                # processes (GUI, CLI, one MCP server per AI client session) wait instead of forking the chain.
                db.execute("BEGIN IMMEDIATE")
                try:
                    entry_id, digest, anchor_readable = self._append(db, row)
                    db.execute("COMMIT")
                except BaseException:
                    _rollback(db)
                    raise
                if anchor_readable:
                    # The entry is kept even if the anchor cannot follow it; the anchor then lags behind.
                    try:
                        self._move_anchor(db, entry_id, digest)
                    except (sqlite3.Error, OSError) as e:
                        self._warn(f"Audit anchor not updated: {e}")
            finally:
                db.close()

    def _append(self, db, row: Dict[str, Any]) -> Tuple[int, str, bool]:
        meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
        found = _dicts(db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1"))
        last = found[0] if found else None
        own = self._key_id()
        chain = self._chain_key_id(last, meta)
        if chain is not None and chain != own and not (chain == UNKEYED and self._key):
            # Unkeyed entries after keyed ones, or entries with a second key, could never be verified.
            raise AuditKeyMismatch("The audit log is kept with a different key than this process has (keyring "
                                   "unavailable or reset); entry not written. 'llm-cli tools audit --archive' "
                                   "starts a new log.")
        try:
            raw, readable = self._read_anchor(), True
        except OSError:
            raw, readable = None, False  # e.g. briefly locked: compared and moved next time
        problem = None
        # A brand-new database (none, or moved aside) starts a new chain; an older anchor does not belong to it.
        new_log = last is None and not meta and not _sequence(db)
        if readable and not new_log and "break" not in meta:
            problem = self._anchor_break(db, last, meta, raw)
        if "key_id" not in meta:
            db.execute("INSERT INTO meta (k, v) VALUES ('key_id', ?)", (own,))
        prev = str(last["hash"]) if last else GENESIS  # an edited head may hold a BLOB; check() reports it
        digest = self._digest(prev, row, own, self._key)
        cur = db.execute(_INSERT, [row[f] for f in _FIELDS] + [prev, digest, own])
        entry_id = cur.lastrowid
        if problem:
            db.execute("INSERT INTO meta (k, v) VALUES ('break', ?)",
                       (json.dumps({"problem": problem[0], "entry": problem[1] or entry_id}),))
        return entry_id, digest, readable

    @staticmethod
    def _warn(message: str) -> None:
        from orchestrator.utils.logging import get_logger
        get_logger("AuditLog").warning(message)

    def entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        db = self._connect()
        try:
            return _dicts(db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)))
        finally:
            db.close()

    def check(self) -> AuditCheck:
        """Walks the whole chain and compares its head with the anchor file. Writers wait meanwhile, so the
        rows and the anchor are read as one consistent state."""
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                return self._check(db)
            finally:
                _rollback(db)
        finally:
            db.close()

    def _check(self, db) -> AuditCheck:
        meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
        prev, head, count, seen = GENESIS, 0, 0, {}
        first_foreign, own_seen, keyed_seen = None, False, False

        def result(problem: str = "", entry: Optional[int] = None) -> AuditCheck:
            return AuditCheck(not problem, problem, entry, self.keyed and (own_seen or not count), count, self.keyed)

        for row in _dicts(db.execute("SELECT * FROM events ORDER BY id")):
            if row["id"] != head + 1:
                return result("removed", head + 1)
            if row["prev_hash"] != prev:
                return result("modified", row["id"])
            kind = self._classify(row, meta.get("key_id"))
            # Unkeyed entries only before keyed ones. Entries without a key id may follow newer ones (a process
            # still running the previous version), but only if they verify like any other entry.
            if kind == _BAD or (kind == _PLAIN and keyed_seen):
                return result("modified", row["id"])
            if kind == _FOREIGN and first_foreign is None:
                first_foreign = row["id"]
            keyed_seen = keyed_seen or kind in (_OWN, _FOREIGN)
            own_seen = own_seen or kind == _OWN
            prev, head = row["hash"], row["id"]
            seen[head] = (prev, kind)
            count += 1
        try:
            raw, readable = self._read_anchor(), True
        except OSError:
            raw, readable = None, False
        anchor = _parse_anchor(raw)
        if first_foreign is not None:
            # A second key never appends to a chain (see record()), so if this key verifies anything, the
            # entries it cannot verify were changed; only when nothing verifies is it really another key.
            ours = self.keyed and anchor is not None and self._anchor_valid(anchor, _OWN)
            return result("modified" if own_seen or ours else "key_mismatch", first_foreign)
        if "break" in meta:  # found by an earlier append, see _anchor_break()
            try:
                stored = json.loads(meta["break"])
                problem, entry = str(stored["problem"]), int(stored["entry"])
            except (ValueError, KeyError, TypeError, RecursionError):
                problem, entry = "modified", None
            return result(problem if problem in _BREAKS else "modified", entry)
        newest_gone = _sequence(db) > head
        if anchor is None:
            if newest_gone:
                return result("removed", head + 1)
            if raw is None and readable and not count:
                return result()
            return result("anchor_missing")
        named = seen.get(anchor[0])
        if named is not None and named[0] == anchor[1]:
            if not self._anchor_valid(anchor, named[1]):
                return result("modified", anchor[0])
            # An anchor behind the head is fine (its update failed or another writer was faster).
            return result("removed", head + 1) if newest_gone else result()
        if not self._anchor_valid(anchor, None):
            return result("modified", anchor[0])
        # The newest entry the log had is gone (or was rewritten together with everything before it).
        return result("removed", anchor[0])

    def verify(self) -> Optional[int]:
        """None if the log is intact; else the first broken or missing entry (0 if no single entry is to blame)."""
        result = self.check()
        if result.intact:
            return None
        return result.entry or 0

    def archive(self) -> Optional[Path]:
        """Puts the log and its anchor aside (audit.db.<UTC time> and audit.db.<UTC time>.anchor); the next
        entry starts a new chain. Returns the path of the old log, None if there was nothing to archive.

        The log is copied and emptied in one write transaction instead of being renamed: the GUI and MCP
        servers may write at the same time, and a writer that opened the file before a rename would append to
        the old log and move the new log's anchor (a false alarm in both). A damaged file is moved as it is."""
        with self._lock:
            if not self.path.exists() and not os.path.lexists(self.anchor_path):
                return None
            try:
                return self._archive_in_place()
            except sqlite3.OperationalError:
                raise  # busy, read-only, disk full: nothing was changed
            except sqlite3.DatabaseError:  # not a database or malformed: nobody can write to it anyway
                target = self._archive_target()
                for suffix in ("", "-wal", "-shm"):
                    source = Path(str(self.path) + suffix)
                    if source.exists():
                        os.replace(source, Path(str(target) + suffix))
                if os.path.lexists(self.anchor_path):
                    os.replace(self.anchor_path, Path(str(target) + ".anchor"))
                return target

    def _archive_target(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target, n = self.path.with_name(f"{self.path.name}.{stamp}"), 1
        while any(os.path.lexists(str(target) + s) for s in ("", "-journal", "-wal", "-shm", ".anchor")):
            target, n = self.path.with_name(f"{self.path.name}.{stamp}-{n}"), n + 1
        return target

    def _archive_in_place(self) -> Optional[Path]:
        db = self._connect()
        target, anchor_moved, committed = None, False, False
        try:
            db.execute("BEGIN IMMEDIATE")  # writers wait; anchors only move under this lock
            if (db.execute("SELECT 1 FROM events LIMIT 1").fetchone() is None and _sequence(db) == 0
                    and db.execute("SELECT 1 FROM meta LIMIT 1").fetchone() is None
                    and not os.path.lexists(self.anchor_path)):
                return None  # a new, empty log
            target = self._archive_target()  # chosen under the lock, so two archives never pick the same name
            # A second connection can read while this one holds the write lock (it could not copy itself).
            source, copy = sqlite3.connect(self.path, timeout=15), sqlite3.connect(target)
            try:
                source.backup(copy)
                copy.execute("PRAGMA journal_mode=DELETE")  # one self-contained file
            finally:
                copy.close()
                source.close()
            if os.path.lexists(self.anchor_path):
                os.replace(self.anchor_path, Path(str(target) + ".anchor"))
                anchor_moved = True
            db.execute("DELETE FROM events")
            db.execute("DELETE FROM meta")
            db.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
            db.execute("COMMIT")
            committed = True
            return target
        finally:
            if not committed:
                _rollback(db)
                try:  # back to how it was; the original error is the one to report
                    if anchor_moved and not os.path.lexists(self.anchor_path):
                        os.replace(Path(str(target) + ".anchor"), self.anchor_path)
                    for suffix in ("", "-journal", "-wal", "-shm") if target is not None else ():
                        if os.path.lexists(str(target) + suffix):
                            os.unlink(str(target) + suffix)
                except OSError:
                    pass
            db.close()
