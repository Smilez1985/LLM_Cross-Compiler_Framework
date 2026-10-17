#!/usr/bin/env python3
"""
Signatures of the per-user files that decide what an AI may do: the tool permissions (`tool_policy.json`, with the
standing approvals), the named MCP tokens (`mcp_tokens.json`) and the pending approvals (`tool_approvals.json`).

Each file carries a `signature` object: an HMAC-SHA256 over the file's canonical JSON (keys sorted, no spaces) and
its kind, with a key derived from the audit key in the OS keyring. Only framework code that holds that key writes
these files, and only on a person's action (CLI at a terminal, GUI); a file edited by hand, damaged, written with
another key or carrying no signature is refused, and what it would decide is denied.

Without a keyring there is no key: the signature is then a plain SHA-256 ("unkeyed"). It still catches damage and
a careless edit, but anyone who can write the file can recompute it, as with the unkeyed audit chain; `llm-cli
doctor` reports it. A reader that has a key never accepts an unkeyed signature, and a reader without a key refuses
one too while the audit log is kept with a key (so a process started without the keyring cannot be made to accept
a file someone edited).

A signature only says that the framework wrote a file at some time. So the newest revision saved of the permissions
and of the token store is also kept in the OS keyring (RevisionAnchor): an older signed copy put back over the file
(to bring back a revoked standing approval, a revoked token or a looser permission) is refused like a hand edit, and a
removed permission file denies everything instead of falling back to the defaults.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

SIGNATURE_FIELD = "signature"
UNKEYED = "unkeyed"
# confirmed_builds: the builds the person confirmed (orchestrator/Core/confirmed_builds.py), which open build standing
# approvals for packs an AI installed. tool_trash: what the trash holds and who asked to delete it
# (orchestrator/Core/trash.py), which decides who may restore it. (ditto_places, the removed remote mode's place
# index, is no kind any more: nothing reads its old file.)
KINDS = ("tool_policy", "mcp_tokens", "tool_approvals", "confirmed_builds", "tool_trash")
_UNSEEN = object()


class SignatureError(ValueError):
    """The file's signature is missing or does not verify. `reason`: unsigned, invalid, other_key, no_key,
    unkeyed_refused; the message says it in words (never the key or the MAC)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _mac_key(key: bytes) -> bytes:
    """The signing key: derived from the audit key, so a MAC of a file can never stand in for an audit entry."""
    return hmac.new(key, b"lcf-signed-file-v1", hashlib.sha256).digest()


def key_id(key: Optional[bytes]) -> str:
    """Names the key a file was signed with (never the key itself)."""
    if not key:
        return UNKEYED
    return hmac.new(key, b"lcf-signed-file-key-id", hashlib.sha256).hexdigest()[:16]


def _payload(kind: str, data: Dict[str, Any]) -> bytes:
    if kind not in KINDS:
        raise ValueError(f"unknown signed file kind {kind!r}")
    body = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return f"lcf-signed:{kind}:v1\n".encode("ascii") + body.encode("utf-8", "surrogatepass")


def _mac(kind: str, data: Dict[str, Any], key: Optional[bytes]) -> str:
    payload = _payload(kind, data)
    if key:
        return hmac.new(_mac_key(key), payload, hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def sign(kind: str, data: Dict[str, Any], key: Optional[bytes]) -> Dict[str, Any]:
    """`data` (JSON values only) with its signature; a signature already in it is replaced."""
    body = {k: v for k, v in data.items() if k != SIGNATURE_FIELD}
    return {**body, SIGNATURE_FIELD: {"key_id": key_id(key), "mac": _mac(kind, body, key)}}


def verify(kind: str, data: Any, key: Optional[bytes],
           refuse_unkeyed: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """`data` without its signature when the signature verifies; SignatureError otherwise. `refuse_unkeyed`: asked
    (only when this reader has no key and the file carries an unkeyed signature) whether unkeyed signatures must be
    refused on this machine, e.g. because the audit log is kept with a key."""
    if not isinstance(data, dict):
        raise SignatureError("invalid", "it is not a JSON object")
    signature = data.get(SIGNATURE_FIELD)
    body = {k: v for k, v in data.items() if k != SIGNATURE_FIELD}
    if signature is None:
        raise SignatureError("unsigned", "it carries no signature (edited by hand, or written by a framework "
                                         "version before signing)")
    if (not isinstance(signature, dict) or set(signature) != {"key_id", "mac"}
            or not all(isinstance(v, str) for v in signature.values())):
        raise SignatureError("invalid", "its signature is malformed")
    stored_id, stored_mac = signature["key_id"], signature["mac"]
    if key:
        if stored_id == UNKEYED:
            raise SignatureError("unkeyed_refused", "it carries only an unkeyed signature, but this computer has a "
                                                    "signing key (it was edited by hand or written without the "
                                                    "keyring)")
        if not hmac.compare_digest(stored_id.encode("utf-8", "replace"), key_id(key).encode("ascii")):
            raise SignatureError("other_key", "it was signed with another key (the keyring was reset, or the file "
                                              "comes from another user or computer)")
    elif stored_id != UNKEYED:
        raise SignatureError("no_key", "it was signed with a key from the OS keyring, which this process cannot "
                                       "reach (keyring unavailable)")
    elif refuse_unkeyed is not None:
        try:
            refused = bool(refuse_unkeyed())
        except Exception:  # cannot tell: fail closed
            refused = True
        if refused:
            raise SignatureError("unkeyed_refused", "it carries only an unkeyed signature, while the audit log is "
                                                    "kept with a key (this process cannot reach the keyring)")
    try:
        expected = _mac(kind, body, key)
    except (TypeError, ValueError) as e:  # values JSON cannot hold canonically
        raise SignatureError("invalid", f"its content cannot be checked ({e})") from None
    if not hmac.compare_digest(stored_mac.encode("utf-8", "replace"), expected.encode("ascii")):
        raise SignatureError("invalid", "its signature does not match its content (edited by hand or damaged)")
    return body


# --- signed rows ---------------------------------------------------------------------------------------------------
# Rows of a database that many framework processes append to (the chat feed, design 5.3) are signed one by one: a
# file signature would make every append rewrite the whole file. A row's kind is never a file kind, its MAC key is
# derived apart from the files' key, and its payload has its own prefix, so a row MAC can never stand in for a file's
# signature, an audit entry or a row of another kind. The same key rules as for files apply: a reader with a key never
# accepts an unkeyed row, a reader without one never accepts a keyed row, and refuse_unkeyed decides the rest.

ROW_KINDS = ("chat_feed", "chat_feed_meta")


def _row_mac(kind: str, data: str, key: Optional[bytes]) -> str:
    if kind not in ROW_KINDS:
        raise ValueError(f"unknown signed row kind {kind!r}")
    if not isinstance(data, str):
        raise TypeError("a signed row is text")
    payload = f"lcf-signed-row:{kind}:v1\n".encode("ascii") + data.encode("utf-8", "surrogatepass")
    if key:
        return hmac.new(hmac.new(key, b"lcf-signed-row-v1", hashlib.sha256).digest(), payload,
                        hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def sign_row(kind: str, data: str, key: Optional[bytes]) -> str:
    """The signature of one row's text: "<key id>:<mac>" (the key id names the key, never the key itself)."""
    return f"{key_id(key)}:{_row_mac(kind, data, key)}"


def verify_row(kind: str, data: str, signature: Any, key: Optional[bytes],
               refuse_unkeyed: Optional[Callable[[], bool]] = None) -> None:
    """Returns when `signature` (from sign_row) verifies `data` for this reader; SignatureError otherwise, with the
    reasons of verify()."""
    if not isinstance(signature, str) or signature.count(":") != 1:
        raise SignatureError("unsigned" if not signature else "invalid", "the row carries no valid signature")
    stored_id, stored_mac = signature.split(":")
    if key:
        if stored_id == UNKEYED:
            raise SignatureError("unkeyed_refused", "the row carries only an unkeyed signature, but this computer "
                                                    "has a signing key")
        if not hmac.compare_digest(stored_id.encode("utf-8", "replace"), key_id(key).encode("ascii")):
            raise SignatureError("other_key", "the row was signed with another key")
    elif stored_id != UNKEYED:
        raise SignatureError("no_key", "the row was signed with a key from the OS keyring, which this process "
                                       "cannot reach")
    elif refuse_unkeyed is not None:
        try:
            refused = bool(refuse_unkeyed())
        except Exception:  # cannot tell: fail closed
            refused = True
        if refused:
            raise SignatureError("unkeyed_refused", "the row carries only an unkeyed signature, while the audit log "
                                                    "is kept with a key")
    try:
        expected = _row_mac(kind, data, key)
    except (TypeError, ValueError) as e:
        raise SignatureError("invalid", f"the row cannot be checked ({e})") from None
    if not hmac.compare_digest(stored_mac.encode("utf-8", "replace"), expected.encode("ascii")):
        raise SignatureError("invalid", "the row's signature does not match its content (edited or damaged)")


class RevisionAnchor:
    """The newest revision of a signed file that the framework saved, kept outside the per-user folder (in the OS
    keyring, next to the audit key). A valid signature only says that the framework wrote the file at some time; an
    older copy put back over the current file would bring back a revoked standing approval, a revoked token or a looser
    permission, and a removed file would fall back to the defaults. With the anchor both are refused like a hand edit.

    `read()` returns the stored text (None when nothing is stored yet) and `write(text)` stores it; either may raise.
    The value only ever grows, and this process keeps the highest one it has seen, so a keyring that cannot be read for
    a moment or an entry someone removed never lowers it. It is read again only when the file changed since the last
    look (a cheap stat), so checking it costs nothing on the many reads of an unchanged file."""

    def __init__(self, read: Callable[[], Optional[str]], write: Callable[[str], None]):
        self._read_value, self._write_value = read, write
        self._known = 0
        self._seen: Any = _UNSEEN   # the identity of the file the known value was last read for
        self._lock = threading.Lock()

    @staticmethod
    def _identity(path: Any) -> Optional[tuple]:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

    def current(self, path: Any) -> int:
        """The highest revision the framework saved of the file at `path` (0 when it never saved one)."""
        identity = self._identity(path)
        with self._lock:
            if identity == self._seen:
                return self._known
            try:
                raw = self._read_value()
            except Exception:  # unreachable for now: keep the value known and look again next time
                return self._known
            text = str(raw or "").strip()
            if text.isdigit() and len(text) <= 18:
                self._known = max(self._known, int(text))
            self._seen = identity
            return self._known

    def saved(self, path: Any, revision: int) -> None:
        """Records the revision just written to `path` (the file is replaced before the anchor moves, so another
        process that reads in between sees a file newer than the anchor, never an older one)."""
        with self._lock:
            self._known = max(self._known, int(revision))
            self._seen = self._identity(path)
            value = self._known
        try:
            self._write_value(str(value))
        except Exception as e:  # the file is saved; only the protection against an older copy lags behind
            logging.getLogger("SignedFiles").warning(f"The revision of {Path(str(path)).name} could not be kept in the "
                                                     f"keyring ({type(e).__name__}); an older copy put back would not "
                                                     "be noticed until the next save.")
