#!/usr/bin/env python3
"""
TokenProvider: an OAuth access token that is refreshed before it expires, safely across processes.

get():
1. Read the record. While more than 5 minutes remain (or the vendor's earliest_refresh_at has not come yet and the
   token is still valid), return the access token.
2. Take the per-credential lock (30 s): the GUI, the CLI and an MCP server may refresh at the same time.
3. Read the record again: another process may have refreshed meanwhile, and then its token is used.
4. Call the refresher (the vendor's token endpoint) with the record.
5. Store the new record, with the new refresh token, before the new access token is used: a rotated refresh token
   that is lost or used twice ends the grant (RFC 6749 section 6, RFC 9700 section 4.14.2). When it cannot be
   stored, the new token is not used.
6. Release the lock and return the token.

A refresher raises RefreshRejected when the token endpoint refuses the refresh token (invalid_grant and the
vendor's codes for a dead grant): the credential is marked signed out, without a retry loop. Any other error
(network) leaves the record as it is. The vendor modules (later packages) provide the refreshers.
"""

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from orchestrator.llm.auth.errors import auth_error
from orchestrator.utils.file_lock import LockTimeout

REFRESH_SKEW = 300.0  # refresh when less than 5 minutes remain
LOCK_TIMEOUT = 30.0
DEAD_GRANT = ("invalid_grant", "refresh_token_reused", "refresh_token_expired", "invalid_client")

# Takes the current record ({"kind": "oauth", "access_token", "refresh_token", "expires_at", ...}) and returns the
# new token fields (access_token, refresh_token when rotated, expires_at or expires_in, ...).
Refresher = Callable[[Dict[str, Any]], Dict[str, Any]]


class RefreshRejected(Exception):
    """The token endpoint refused the refresh token: the grant is gone and the user has to sign in again."""

    def __init__(self, error: str = "invalid_grant", *, code: Optional[str] = None, detail: str = ""):
        super().__init__(error)
        self.error = error
        self.code = code      # an error code of the auth table for this vendor; None: auth.signed_out
        self.detail = detail  # the vendor's own text (redacted when shown)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _iso(epoch: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds") if epoch is not None else None


def needs_refresh(record: Dict[str, Any], now: float, skew: float = REFRESH_SKEW) -> bool:
    if not record.get("access_token"):
        return True
    expires = _number(record.get("expires_at"))
    if expires is None:
        return False  # no known expiry: used until the provider rejects it
    remaining = expires - now
    if remaining > skew:
        return False
    earliest = _number(record.get("earliest_refresh_at"))
    return not (earliest is not None and now < earliest and remaining > 0)


class TokenProvider:
    """Callable: returns a valid access token (an SDK can take it as api_key)."""

    def __init__(self, store: Any, provider: str, method: str, refresher: Refresher, *, origin: Optional[str] = None,
                 label: Optional[str] = None, skew: float = REFRESH_SKEW, lock_timeout: float = LOCK_TIMEOUT,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.provider = provider
        self.method = method
        self.refresher = refresher
        self.origin = origin
        self.label = label or provider
        self.skew = skew
        self.lock_timeout = lock_timeout
        self.clock = clock

    def __repr__(self) -> str:
        return f"TokenProvider(provider={self.provider!r}, method={self.method!r})"

    def __reduce_ex__(self, protocol):
        raise TypeError("a TokenProvider is not pickled: it reads a credential")

    def _error(self, code: str, **kw):
        return auth_error(code, provider=self.provider, provider_label=self.label, **kw)

    def _record(self) -> Dict[str, Any]:
        if self.store.is_signed_out(self.provider, self.method, self.origin):
            raise self._error("auth.signed_out")
        record = self.store.get_record(self.provider, self.method, self.origin)
        if not isinstance(record, dict) or record.get("kind") != "oauth":
            raise self._error("auth.not_signed_in")
        return record

    def get(self) -> str:
        record = self._record()
        if not needs_refresh(record, self.clock(), self.skew):
            return record["access_token"]
        try:
            with self.store.credential_lock(self.provider, self.method, self.origin, timeout=self.lock_timeout):
                record = self._record()  # another process may have refreshed while this one waited
                if not needs_refresh(record, self.clock(), self.skew):
                    return record["access_token"]
                return self._refresh(record)
        except LockTimeout:
            raise self._error("auth.refresh_busy") from None

    __call__ = get

    def _refresh(self, record: Dict[str, Any]) -> str:
        now = self.clock()
        if not record.get("refresh_token"):
            expires = _number(record.get("expires_at"))
            if record.get("access_token") and (expires is None or expires > now):
                return record["access_token"]  # nothing to refresh with; valid for a little longer
            self.store.mark_signed_out(self.provider, self.method, self.origin, reason="expired")
            raise self._error("auth.signed_out")
        try:
            fresh = self.refresher(dict(record))
        except RefreshRejected as e:
            self.store.mark_signed_out(self.provider, self.method, self.origin, reason=e.error)
            raise self._error(e.code or "auth.signed_out", detail=e.detail or None) from None
        if not isinstance(fresh, dict) or not isinstance(fresh.get("access_token"), str) or not fresh["access_token"]:
            raise self._error("auth.refresh_failed")
        new = {**record, **{k: v for k, v in fresh.items() if v is not None and k != "expires_in"}, "kind": "oauth"}
        if not fresh.get("refresh_token"):
            new["refresh_token"] = record["refresh_token"]  # not rotated: the old one stays valid
        if fresh.get("expires_at") is None and _number(fresh.get("expires_in")) is not None:
            new["expires_at"] = int(now + float(fresh["expires_in"]))
        new["refreshed_at"] = int(now)
        # Persisted before use; save_record raises when the vault refuses, and the new token is then not used.
        self.store.save_record(self.provider, self.method, new, origin=self.origin,
                               metadata={"expires_at": _iso(_number(new.get("expires_at"))), "state": None,
                                         "refreshed_at": _iso(now)})
        return new["access_token"]


def sign_out(store: Any, provider: str, method: str, *, origin: Optional[str] = None,
             revoker: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Optional[bool]:
    """Signs out locally first (record and metadata deleted), then asks the vendor to revoke the grant when it can.
    Returns True (revoked), False (revocation not confirmed: say so) or None (the vendor offers none)."""
    with store.credential_lock(provider, method, origin):
        record = store.get_record(provider, method, origin)
        deleter = getattr(store.secrets, "delete_secret", None)
        if record is not None and callable(deleter):
            from orchestrator.llm.auth.store import vault_name
            deleter(vault_name(provider, method, origin))
        store.remove_metadata(provider, method, origin)
    if revoker is None or not record:
        return None
    try:
        return bool(revoker(record))
    except Exception:
        return False
