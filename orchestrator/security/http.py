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
connects only to hosts the network-locality rule counts as local (utils/locality.connect_addresses, with the person's
opt-outs), at the local addresses that rule checked (tried in turn; a name with local and public addresses only at its
local ones, round 26 Q4), so a DNS answer that changes after the check cannot reach a public host (LocalityRefused, a
PermissionError); when every checked address failed, the lookup is dropped (locality.forget) and the next connection
asks the resolver again. The runtime's model downloads mount it in offline mode, and with `crawl` a crawl the person
starts in offline mode (locality.crawl_addresses: also never a cloud metadata service or a link-local IPv4 address,
round 26 Q5).
"""

from typing import Any, Callable, Optional, Sequence, Union

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from orchestrator.utils.validation import resolve_public_addresses

Vet = Callable[[str, int], Union[str, Sequence[str]]]


def _public_address(host: str, port: int) -> str:
    return resolve_public_addresses(host, port)[0]


class _PinnedConnectionMixin:
    """Re-checks the address at connect time and connects to exactly that IP (defeats DNS rebinding).
    TLS still verifies against the original host name, which urllib3 reads after _new_conn(). `_vet` is the check:
    (host, port) -> the address to connect to, or the checked addresses to try in turn (offline mode's
    connect_addresses, as the system's own connection tries every address), raising PermissionError when the host may
    not be reached."""

    _vet: Vet = staticmethod(_public_address)
    _forget: Optional[Callable[[str], None]] = None   # called with the host when every checked address failed

    def _new_conn(self):
        vetted = self._vet(self._dns_host, self.port)
        candidates = (vetted,) if isinstance(vetted, str) else tuple(vetted)
        if not candidates:
            raise PermissionError(f"{self._dns_host} has no checked address")
        original, failed = self._dns_host, None
        try:
            for address in candidates:
                self._dns_host = address
                try:
                    return super()._new_conn()
                except (NewConnectionError, ConnectTimeoutError) as e:   # the next checked address
                    failed = e
        finally:
            self._dns_host = original
        if self._forget is not None:
            self._forget(original)   # the next connection asks the resolver again (another network, a new answer)
        raise failed


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


def _pools(vet: Vet, forget: Optional[Callable[[str], None]] = None):
    """Connection pools whose connections go to the address `vet` hands out (`forget`: called with the host when every
    one of them failed)."""
    hook = staticmethod(forget) if forget is not None else None

    class Connection(_PinnedConnectionMixin, HTTPConnection):
        _vet = staticmethod(vet)
        _forget = hook

    class SecureConnection(_PinnedConnectionMixin, HTTPSConnection):
        _vet = staticmethod(vet)
        _forget = hook

    class Pool(HTTPConnectionPool):
        ConnectionCls = Connection

    class SecurePool(HTTPSConnectionPool):
        ConnectionCls = SecureConnection

    return {"http": Pool, "https": SecurePool}


class LocalOnlyAdapter(HTTPAdapter):
    """Offline mode: direct connections only to hosts on this computer or in the person's own network (the
    network-locality rule with the opt-outs in `settings`), each at its checked local addresses. `crawl`: a crawl the
    person starts (locality.crawl_addresses, never a cloud metadata service or a link-local IPv4 address). Mount it on
    a session with trust_env off, so no proxy from the environment receives the request."""

    def __init__(self, settings: Any = None, what: str = "the download", *, crawl: bool = False, **kwargs):
        self._settings, self._what, self._crawl = settings, what, crawl
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        from orchestrator.utils.locality import connect_addresses, crawl_addresses, forget
        super().init_poolmanager(*args, **kwargs)
        settings, what = self._settings, self._what
        if self._crawl:
            vet = lambda host, port: crawl_addresses(host, settings)  # noqa: E731
        else:
            vet = lambda host, port: connect_addresses(host, settings, what)  # noqa: E731
        self.poolmanager.pool_classes_by_scheme = _pools(vet, forget)


def pinned_transport(settings: Any, what: str, *, httpx: Any, httpcore: Any, offline: bool = True):
    """An httpx transport without the proxy from the environment whose every connection goes only to the checked
    local addresses of its host (locality.connect_addresses with the person's opt-outs in `settings`, tried in turn;
    locality.forget when every one failed), for the httpx/httpcore pair a client library uses: httpx2/httpcore2 of
    the AI SDKs (llm/client.offline_http_client) or httpx/httpcore of qdrant-client (a Qdrant server in offline
    mode). A host that is not local is refused when the connection is opened (LocalityRefused). `offline` False: the
    location of a server labelled local by its address (no opt-outs; the refusal says so)."""
    from orchestrator.utils.locality import connect_addresses, forget

    class PinnedBackend(httpcore.NetworkBackend):
        def __init__(self, inner):
            self._inner = inner

        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            failed = None
            for address in connect_addresses(host, settings, what, offline=offline):
                try:
                    return self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                                   socket_options=socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:   # the next checked address
                    failed = e
            forget(host)   # every checked address failed: the next connection asks the resolver again
            raise failed

        def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover - never used
            raise PermissionError("offline mode connects to checked network addresses only")

        def sleep(self, seconds):
            self._inner.sleep(seconds)

    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool._network_backend = PinnedBackend(transport._pool._network_backend)
    return transport


def local_only_session(settings: Any = None, what: str = "the download", *, crawl: bool = False):
    """A requests session for offline mode: LocalOnlyAdapter for http and https, no proxy from the environment."""
    import requests
    session = requests.Session()
    session.trust_env = False
    adapter = LocalOnlyAdapter(settings, what, crawl=crawl)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


__all__ = ["LocalOnlyAdapter", "PublicOnlyAdapter", "local_only_session", "pinned_transport"]
