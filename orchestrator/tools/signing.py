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
"""

import hashlib
import hmac
import json
from typing import Any, Callable, Dict, Optional

SIGNATURE_FIELD = "signature"
UNKEYED = "unkeyed"
KINDS = ("tool_policy", "mcp_tokens", "tool_approvals")


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
