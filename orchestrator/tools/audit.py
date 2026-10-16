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

import contextlib
import hashlib
import hmac
import json
import os
import re
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
        key_id TEXT,
        extra TEXT
    )
    """,
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
)
_FIELDS = ("ts", "actor", "tool", "tier", "decision", "approved", "args_sha256", "args_preview", "outcome", "detail")
_INSERT = ("INSERT INTO events (ts, actor, tool, tier, decision, approved, args_sha256, args_preview, outcome, detail, "
           "prev_hash, hash, key_id, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
GENESIS = "0" * 64
UNKEYED = "unkeyed"

# What an entry can record. `started` is the write-ahead entry of a call that changes something (written before
# the handler runs; the call is refused when it cannot be written), `confirmation_requested` the record of a
# question put to a person; the other outcomes are end states. `folder_added` and `folder_removed` record a person's
# change to the folders the AI may use, `policy_changed` a saved change of the tool permissions with the old and new
# decisions, `approval_decided` a person's answer to a pending approval (framework-side events, actor cli or gui).
# For delegations and the chat (design 3.11): `ai_answered` an AI's answer to its own question, `delegation_lapsed`,
# the person's trash, snapshot and chat cleanups, and `relay_question` a person's question relayed to an AI client.
OUTCOMES = frozenset({
    "ok", "error", "denied", "not_confirmed", "invalid_input",
    "started", "confirmation_requested", "cancelled", "rate_limited", "confirmation_timeout",
    "audit_unavailable", "unknown_tool", "repeat_refused", "cannot_reported",
    "folder_added", "folder_removed", "policy_changed", "approval_decided", "grant_lapsed",
    "ai_answered", "delegation_lapsed", "trash_restored", "trash_emptied", "snapshots_pruned", "feed_cleared",
    "relay_question",
})
# How a person answered (or did not answer) a question; `escalated` and `withdrawn` are an AI's answers to its own
# question ("ask the person", "no").
ANSWERS = frozenset({"accepted", "declined", "cancelled", "timeout", "client_disconnected", "unavailable",
                     "unanswered", "escalated", "withdrawn"})
# Where a confirmation came from: none, an MCP form elicitation, the framework's own windows, a pending approval
# decided in the framework, a standing approval, or a delegation.
_CHANNEL = re.compile(r"^(none|elicitation|framework|framework-gui|framework-cli|approval:[\w-]{1,64}|"
                      r"grant:[\w-]{1,64}|delegation:d_[0-9a-f]{16})$")
# Fields kept in an entry's `extra` column. The entry's hash stays the 3.0.0 one (so a 3.0.0 process that still
# runs next to this version verifies and appends to the same log); the extra fields are bound to it by their
# SHA-256 at the end of the chained `detail` column, so adding, removing or editing them is detected.
EXTRA_FIELDS = frozenset({"client", "session", "confirm_channel", "question_sha256", "answer", "name_requested",
                          "repairs", "reason_code", "duration_ms", "result", "links",
                          # delegations and the chat feed (design 3.11). The removed remote mode also wrote place,
                          # taint, remote_server and transfer: old entries keep them (bound by the extra sha256, so
                          # they still verify and show), new ones cannot carry them.
                          "answered_by", "delegation", "reason_sha256", "facts_sha256", "warnings", "version",
                          "feed_entry", "deletion_sha256"})
# Who answered a question: the person, or an AI under a delegation.
ANSWERED_BY = frozenset({"person", "ai"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# Kept when the extra fields are too long and must be cut: what ties an entry to its question, answer, decision record
# and deletion plan.
_KEEP = ("reason_code", "answer", "confirm_channel", "question_sha256", "duration_ms", "links", "answered_by",
         "delegation", "reason_sha256", "facts_sha256", "feed_entry", "deletion_sha256")
_EXTRA_MAX = 2000
_EXTRA_REF = re.compile(r"\n\[extra sha256:([0-9a-f]{64})\]\Z")
_EXTRA_TAG = "[extra sha256:"
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


def extra_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """The parsed extra fields of an entry ({} when it has none or they cannot be read)."""
    raw = row.get("extra")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _shown(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows as a reader gets them: the extra fields also as keys of their own (they never replace a column), the
    detail without their binding."""
    for row in rows:
        if isinstance(row.get("detail"), str):
            row["detail"] = _EXTRA_REF.sub("", row["detail"])
        for key, value in extra_fields(row).items():
            row.setdefault(key, value)
    return rows


def _extra_ref(extra: str) -> str:
    """The binding of an entry's extra fields, appended to its detail (which the 3.0.0 hash covers)."""
    return f"\n{_EXTRA_TAG}{hashlib.sha256(extra.encode('utf-8', 'surrogatepass')).hexdigest()}]"


def _extra_bound(row: Dict[str, Any]) -> bool:
    """Whether the extra fields are exactly the ones the entry's detail names (none named: none stored)."""
    extra, found = row.get("extra"), _EXTRA_REF.search(str(row.get("detail") or ""))
    if extra is None:
        return found is None
    if found is None or not isinstance(extra, str):
        return False
    return hmac.compare_digest(_extra_ref(extra), found.group(0))


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
            columns = {r[1] for r in db.execute("PRAGMA table_info(events)")}
            # Logs from before per-entry key ids, and from before the extra fields.
            for column in ("key_id", "extra"):
                if column not in columns:
                    try:
                        db.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
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
        return _same(self._digest(str(row["prev_hash"]), row, key_id, key), row["hash"]) and _extra_bound(row)

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

    @staticmethod
    def _check_delegation_fields(fields: Dict[str, Any]) -> None:
        """The fields of delegated answers take only the values the design gives them (design 3.11); anything else is
        a bug in the caller, refused like an unknown field."""
        from orchestrator.tools.delegation import delegation_id_ok
        if "answered_by" in fields and fields["answered_by"] not in ANSWERED_BY:
            raise ValueError(f"answered_by must be person or ai, not {fields['answered_by']!r}")
        if "delegation" in fields and not delegation_id_ok(fields["delegation"]):
            raise ValueError(f"delegation must be a delegation id (d_<16 hex>), not {fields['delegation']!r}")
        for key in ("reason_sha256", "facts_sha256", "deletion_sha256"):
            if key in fields and not (isinstance(fields[key], str) and _HEX64.fullmatch(fields[key])):
                raise ValueError(f"{key} must be a sha256 in hex")
        if "version" in fields and (isinstance(fields["version"], bool) or not isinstance(fields["version"], int)
                                    or not 1 <= fields["version"] <= 99):
            raise ValueError("version must be a whole number from 1 to 99")
        if "feed_entry" in fields:
            from orchestrator.Core.chat_feed import ENTRY_ID_RE   # the feed owns its entry ids
            if not re.fullmatch(ENTRY_ID_RE, str(fields["feed_entry"])):
                raise ValueError("feed_entry must be a feed entry id (fe_<16 hex>)")
        if "warnings" in fields:
            from orchestrator.tools.spec import WARNING_CODE_RE, _catalog   # the shared warning vocabulary
            codes = fields["warnings"]
            if not isinstance(codes, list) or not all(isinstance(c, str) and re.fullmatch(WARNING_CODE_RE, c)
                                                      for c in codes):
                raise ValueError("warnings must be a list of warning codes")
            catalog = _catalog()
            known = getattr(catalog, "WARNINGS", None) if catalog is not None else None
            unknown = sorted(c for c in codes if known is not None and c not in known)
            if unknown:
                raise ValueError(f"unknown warning codes {unknown}")

    @staticmethod
    def _extra(outcome: str, extra: Optional[Dict[str, Any]]) -> Optional[str]:
        """The extra fields as canonical JSON (redacted), None when there are none. Unknown fields and values
        outside the documented sets are refused: they are a bug in the caller, and an audit entry must not carry
        text nobody can interpret."""
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown audit outcome {outcome!r}")
        fields = {k: v for k, v in (extra or {}).items() if v not in (None, "", [], {})}
        if not fields:
            return None
        unknown = set(fields) - EXTRA_FIELDS
        if unknown:
            raise ValueError(f"unknown audit fields {sorted(unknown)}")
        if "answer" in fields and fields["answer"] not in ANSWERS:
            raise ValueError(f"unknown answer {fields['answer']!r}")
        # fullmatch: "$" alone would also accept a channel with a newline at its end
        if "confirm_channel" in fields and not _CHANNEL.fullmatch(str(fields["confirm_channel"])):
            raise ValueError(f"unknown confirmation channel {fields['confirm_channel']!r}")
        AuditLog._check_delegation_fields(fields)
        text = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        text = _text(redact_secrets(text))
        if len(text) > _EXTRA_MAX:  # never cut JSON in the middle: keep the fields that fit, say what was cut
            kept: Dict[str, Any] = {"truncated": sorted(fields)}
            for key in _KEEP:
                if key in fields:
                    kept[key] = fields[key]
            text = _text(redact_secrets(json.dumps(kept, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                                   default=str)))[:_EXTRA_MAX]
        return text

    def record(self, actor: str, tool: str, tier: str, decision: str, outcome: str,
               arguments: Any = None, approved: Optional[bool] = None, detail: str = "",
               extra: Optional[Dict[str, Any]] = None, omit: Tuple[str, ...] = ()) -> int:
        """Appends one entry and returns its id. `extra`: fields of EXTRA_FIELDS (client, session, confirmation
        channel, question hash, answer, repaired names and arguments, reason code, duration, result hash, links to
        earlier entries; the fields of delegated answers). `omit`: argument names whose values stay out of the
        preview (a tool's FieldMeta(audit=False), e.g. a data chunk); args_sha256 still covers the full arguments."""
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str) if arguments is not None else ""
        preview = text
        if omit and isinstance(arguments, dict) and any(k in arguments for k in omit):
            shown = {k: (f"[not recorded: {len(str(v))} characters]" if k in omit else v) for k, v in arguments.items()}
            preview = json.dumps(shown, ensure_ascii=False, sort_keys=True, default=str)
        extra_text = self._extra(outcome, extra)
        # Free text (it can quote what a model wrote) never ends like the binding of extra fields.
        detail_text = _text(redact_secrets(detail or ""))[:1000].replace(_EXTRA_TAG, "[extra-sha256:")
        if extra_text is not None:
            detail_text += _extra_ref(extra_text)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": _text(actor), "tool": _text(tool), "tier": _text(tier), "decision": _text(decision),
            "approved": None if approved is None else int(bool(approved)),
            "args_sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
            "args_preview": _text(redact_secrets(preview))[:500],
            "outcome": _text(outcome),
            "detail": detail_text,
            "extra": extra_text,
        }
        with self._lock:
            db = self._connect()
            try:
                # One write transaction for checking the anchor, reading the last hash and appending: other
                # processes (GUI, CLI, one MCP server per AI client session) wait instead of forking the chain.
                db.execute("BEGIN IMMEDIATE")
                first_anchor = False
                try:
                    entry_id, digest, anchor_readable, new_log = self._append(db, row)
                    if new_log and anchor_readable:
                        # The first entry of a log gets its anchor before the commit makes it visible: a writer
                        # waiting for this transaction would otherwise find entries but no anchor and record
                        # "anchor_missing". Later entries only move an anchor that exists (a lagging one is fine).
                        try:
                            self._write_anchor(entry_id, digest)
                            first_anchor = True
                        except OSError as e:
                            self._warn(f"Audit anchor not written: {e}")
                    db.execute("COMMIT")
                except BaseException:
                    if first_anchor:  # it names an entry that was never committed; removed while still locked
                        with contextlib.suppress(OSError):
                            os.unlink(self.anchor_path)
                    _rollback(db)
                    raise
                if anchor_readable and not first_anchor:
                    # The entry is kept even if the anchor cannot follow it; the anchor then lags behind.
                    try:
                        self._move_anchor(db, entry_id, digest)
                    except (sqlite3.Error, OSError) as e:
                        self._warn(f"Audit anchor not updated: {e}")
            finally:
                db.close()
        return entry_id

    def _append(self, db, row: Dict[str, Any]) -> Tuple[int, str, bool, bool]:
        """Appends row; returns (entry id, hash, whether the anchor could be read, whether a new log started)."""
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
        cur = db.execute(_INSERT, [row[f] for f in _FIELDS] + [prev, digest, own, row.get("extra")])
        entry_id = cur.lastrowid
        if problem:
            db.execute("INSERT INTO meta (k, v) VALUES ('break', ?)",
                       (json.dumps({"problem": problem[0], "entry": problem[1] or entry_id}),))
        return entry_id, digest, readable, new_log

    @staticmethod
    def _warn(message: str) -> None:
        from orchestrator.utils.logging import get_logger
        get_logger("AuditLog").warning(message)

    def entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """The newest entries, newest first. The extra fields are also given as keys of their own (they never
        replace a column); `detail` is shown without their binding."""
        db = self._connect()
        try:
            rows = _dicts(db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)))
        finally:
            db.close()
        return _shown(rows)

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

    def grant_uses(self, grant_id: str) -> Tuple[int, Optional[str]]:
        """(how often a standing approval was used, the time of its last use), read from the end entries of the calls
        that ran under it; the policy file never counts uses. The record of its lapse is no use: counting it would
        wake a lapsed standing approval up again for another 90 days. (0, None) without a log."""
        if not re.fullmatch(r"g_[0-9a-f]{16}", grant_id or "") or not os.path.lexists(self.path):
            return 0, None
        needle = f'"confirm_channel":"grant:{grant_id}"'
        db = self._connect()
        try:
            row = db.execute("SELECT COUNT(*), MAX(ts) FROM events WHERE instr(extra, ?) > 0 AND outcome IN "
                             "('ok', 'error', 'cancelled')", (needle,)).fetchone()
        finally:
            db.close()
        return int(row[0] or 0), (row[1] if row else None)

    def delegation_uses(self, delegation_id: str) -> Tuple[int, Optional[str]]:
        """(how often a delegation ran a call, the time of the last one), read from the `started` entries of the calls
        an AI answered under it (`confirm_channel: delegation:<id>`); the delegation file never counts uses, so a use
        never rewrites it. Its lapse record is no use. (0, None) without a log."""
        from orchestrator.tools.delegation import delegation_id_ok
        if not delegation_id_ok(delegation_id) or not os.path.lexists(self.path):
            return 0, None
        needle = f'"confirm_channel":"delegation:{delegation_id}"'
        db = self._connect()
        try:
            row = db.execute("SELECT COUNT(*), MAX(ts) FROM events WHERE instr(extra, ?) > 0 AND outcome = 'started'",
                             (needle,)).fetchone()
        finally:
            db.close()
        return int(row[0] or 0), (row[1] if row else None)

    def entries_after(self, last_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """The entries after `last_id`, oldest first, at most `limit` (as entries() gives them): a watcher's id cursor,
        so a burst of entries never hides one between two looks."""
        if not os.path.lexists(self.path):
            return []
        db = self._connect()
        try:
            rows = _dicts(db.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?",
                                     (int(last_id), max(0, int(limit)))))
        finally:
            db.close()
        return _shown(rows)

    def kept_with_key(self) -> bool:
        """Whether this log is kept with a key (its newest entry, or the key it started with, names one): then a
        signed per-user file must carry a keyed signature too. Never creates the log; True when it cannot be read
        (fail closed)."""
        if not os.path.lexists(self.path):
            return False
        try:
            db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
        except (sqlite3.Error, OSError, ValueError):
            return True
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "events" not in tables:
                return False
            ids = [r[0] for r in db.execute("SELECT key_id FROM events ORDER BY id DESC LIMIT 1")]
            if "meta" in tables:
                ids += [r[0] for r in db.execute("SELECT v FROM meta WHERE k = 'key_id'")]
            return any(kid not in (None, UNKEYED) for kid in ids)
        except sqlite3.Error:
            return True
        finally:
            db.close()

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
