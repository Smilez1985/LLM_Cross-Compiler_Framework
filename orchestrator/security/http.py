#!/usr/bin/env python3
"""
HTTP that reaches only the addresses a check allowed (consolidation chain c1-imports: moved unchanged from
orchestrator/Core/crawler_manager.py, which keeps the names, so the image registry client in utils no longer imports
the crawler).

PublicOnlyAdapter is a requests adapter whose connections check the address again when the socket is opened and
connect to exactly that address (DNS rebinding): a name that resolves to any non-public address is refused then
(PermissionError from utils.validation.resolve_public_addresses), and TLS is still checked against the host name. The
crawler and the registry client mount it for http and https; each checks every URL with validate_public_url before
it asks, and follows redirects by hand.

LocalOnlyAdapter is the same pinned connection for offline mode (chain offline-lan, DECISIONS round 24 L-NET): it
connects only to hosts the network-locality rule counts as local (utils/locality.connect_address, with the person's
opt-outs), at the address that rule checked for the run, so a DNS answer that changes after the check cannot reach a
public host (LocalityRefused, a PermissionError). The runtime's model downloads mount it in offline mode.
"""

from typing import Any, Callable

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from orchestrator.utils.validation import resolve_public_addresses

Vet = Callable[[str, int], str]


def _public_address(host: str, port: int) -> str:
    return resolve_public_addresses(host, port)[0]


class _PinnedConnectionMixin:
    """Re-checks the address at connect time and connects to exactly that IP (defeats DNS rebinding).
    TLS still verifies against the original host name, which urllib3 reads after _new_conn(). `_vet` is the check:
    (host, port) -> the address to connect to, raising PermissionError when the host may not be reached."""

    _vet: Vet = staticmethod(_public_address)

    def _new_conn(self):
        vetted = self._vet(self._dns_host, self.port)
        original, self._dns_host = self._dns_host, vetted
        try:
            return super()._new_conn()
        finally:
            self._dns_host = original


class _PinnedHTTPConnection(_PinnedConnectionMixin, HTTPConnection):
    pass


class _PinnedHTTPSConnection(_PinnedConnectionMixin, HTTPSConnection):
    pass


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class PublicOnlyAdapter(HTTPAdapter):
    """Direct connections only reach public addresses. Requests through a configured proxy are
    resolved by the proxy and therefore only covered by the URL check."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _PinnedHTTPConnectionPool, "https": _PinnedHTTPSConnectionPool}


def _pools(vet: Vet):
    """Connection pools whose connections go to the address `vet` hands out."""
    class Connection(_PinnedConnectionMixin, HTTPConnection):
        _vet = staticmethod(vet)

    class SecureConnection(_PinnedConnectionMixin, HTTPSConnection):
        _vet = staticmethod(vet)

    class Pool(HTTPConnectionPool):
        ConnectionCls = Connection

    class SecurePool(HTTPSConnectionPool):
        ConnectionCls = SecureConnection

    return {"http": Pool, "https": SecurePool}


class LocalOnlyAdapter(HTTPAdapter):
    """Offline mode: direct connections only to hosts on this computer or in the person's own network (the
    network-locality rule with the opt-outs in `settings`), each at its checked address. Mount it on a session with
    trust_env off, so no proxy from the environment receives the request."""

    def __init__(self, settings: Any = None, what: str = "the download", **kwargs):
        self._settings, self._what = settings, what
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        from orchestrator.utils.locality import connect_address
        super().init_poolmanager(*args, **kwargs)
        settings, what = self._settings, self._what
        self.poolmanager.pool_classes_by_scheme = _pools(lambda host, port: connect_address(host, settings, what))


def local_only_session(settings: Any = None, what: str = "the download"):
    """A requests session for offline mode: LocalOnlyAdapter for http and https, no proxy from the environment."""
    import requests
    session = requests.Session()
    session.trust_env = False
    adapter = LocalOnlyAdapter(settings, what)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


__all__ = ["LocalOnlyAdapter", "PublicOnlyAdapter", "local_only_session"]
