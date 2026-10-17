"""The SSRF-safe HTTP adapter (consolidation chain c1-imports: orchestrator/security/http.py, moved from
Core/crawler_manager.py, so the registry client in utils no longer imports the crawler).

Characterization: PublicOnlyAdapter makes every direct connection check the address again when the socket opens and
connect to exactly that address (DNS rebinding); a name that resolves to any non-public address is refused then, with
TLS still checked against the host name. The crawler and the image registry client mount it for http and https.
"""

import socket

import pytest

from orchestrator.Core.crawler_manager import (PublicOnlyAdapter, _PinnedHTTPConnection, _PinnedHTTPConnectionPool,
                                               _PinnedHTTPSConnection, _PinnedHTTPSConnectionPool)


@pytest.fixture
def dns(monkeypatch):
    """getaddrinfo answers from a table (host -> addresses); every other name is looked up as usual."""
    table = {}
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                    for ip in table[host]]
        return real(host, port, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table


@pytest.fixture
def connects(monkeypatch):
    import urllib3.util.connection
    seen = []
    monkeypatch.setattr(urllib3.util.connection, "create_connection",
                        lambda address, *a, **k: seen.append(address) or object())
    return seen


def test_the_adapter_mounts_the_pinned_pools():
    adapter = PublicOnlyAdapter()
    assert adapter.poolmanager.pool_classes_by_scheme == {"http": _PinnedHTTPConnectionPool,
                                                          "https": _PinnedHTTPSConnectionPool}
    assert _PinnedHTTPConnectionPool.ConnectionCls is _PinnedHTTPConnection
    assert _PinnedHTTPSConnectionPool.ConnectionCls is _PinnedHTTPSConnection


@pytest.mark.parametrize("cls, port", [(_PinnedHTTPConnection, 80), (_PinnedHTTPSConnection, 443)])
def test_connects_to_the_checked_public_address_under_the_host_name(cls, port, dns, connects):
    dns["docs.example"] = ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"]
    conn = cls("docs.example", port)
    conn._new_conn()
    assert connects == [("93.184.215.14", port)] and conn.host == "docs.example"


@pytest.mark.parametrize("answers", [["127.0.0.1"], ["10.0.0.7"], ["93.184.215.14", "192.168.1.1"], ["::1"],
                                     ["100.64.0.1"], ["::ffff:127.0.0.1"], ["169.254.169.254"]])
def test_a_name_with_any_non_public_address_is_refused_when_the_socket_opens(answers, dns, connects):
    dns["rebind.example"] = answers
    with pytest.raises(PermissionError, match="rebind.example resolves to non-public address"):
        _PinnedHTTPSConnection("rebind.example", 443)._new_conn()
    assert connects == [], "nothing was connected"


def test_the_crawler_and_the_registry_client_mount_it():
    from types import SimpleNamespace

    from orchestrator.Core.crawler_manager import CrawlerManager
    from orchestrator.utils.image_registry import RegistryClient
    config = SimpleNamespace(get=lambda key, default=None: default)
    for session in (CrawlerManager(SimpleNamespace(config=config)).session, RegistryClient().session):
        for url in ("http://x.example/", "https://x.example/"):
            assert isinstance(session.get_adapter(url), PublicOnlyAdapter)


def test_the_old_names_are_the_one_implementation():
    from orchestrator.Core import crawler_manager
    from orchestrator.security import http
    for name in ("PublicOnlyAdapter", "_PinnedConnectionMixin", "_PinnedHTTPConnection", "_PinnedHTTPSConnection",
                 "_PinnedHTTPConnectionPool", "_PinnedHTTPSConnectionPool"):
        assert getattr(crawler_manager, name) is getattr(http, name), name
