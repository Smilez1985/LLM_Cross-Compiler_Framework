#!/usr/bin/env python3
"""
HTTP that reaches only public addresses (consolidation chain c1-imports: moved unchanged from
orchestrator/Core/crawler_manager.py, which keeps the names, so the image registry client in utils no longer imports
the crawler).

PublicOnlyAdapter is a requests adapter whose connections check the address again when the socket is opened and
connect to exactly that address (DNS rebinding): a name that resolves to any non-public address is refused then
(PermissionError from utils.validation.resolve_public_addresses), and TLS is still checked against the host name. The
crawler and the registry client mount it for http and https; each checks every URL with validate_public_url before
it asks, and follows redirects by hand.
"""

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from orchestrator.utils.validation import resolve_public_addresses


class _PinnedConnectionMixin:
    """Re-checks the address at connect time and connects to exactly that IP (defeats DNS rebinding).
    TLS still verifies against the original host name, which urllib3 reads after _new_conn()."""

    def _new_conn(self):
        vetted = resolve_public_addresses(self._dns_host, self.port)[0]
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


__all__ = ["PublicOnlyAdapter"]
