"""Review of the offline-mode follow-ups (chain offline-followups, DECISIONS round 26 Q3, Q4 and item d).

- The privacy filter and the client that carries the prompts come from ONE locality check: resolve() decides the
  filter (Target.local), and the adapter for that target is the pinned client whenever the filter is off, even when
  the name's answer changed in between (a second, separate check could hand unfiltered prompts to an unpinned client).
- The pinned clients (security/http.pinned_transport: the provider client offline and, online, for a custom server
  labelled local by its address; qdrant-client in offline mode) leave out only the proxy: the certificate trust the
  environment names (SSL_CERT_FILE, SSL_CERT_DIR: an own CA for servers in the own network) stays, as the libraries'
  own clients have it.
- A Qdrant server client opened online is not used once offline mode is switched on: the knowledge base opens it
  again under the rule (a cloud Qdrant is refused then).

Every public stand-in is a documentation address (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) and
every name an example name.
"""

import contextlib
import datetime
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from orchestrator.utils import locality


class Settings:
    def __init__(self, **values):
        self.values = {"ai_security_level": "STRICT", **values}
        self.config_dir = None

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def names(monkeypatch):
    """The resolver's answers in these tests (anything else does not resolve)."""
    table = {}

    def lookup(host):
        if host not in table:
            raise OSError(f"{host}: unknown")
        return table[host]
    monkeypatch.setattr(locality, "_system_lookup", lookup)
    return table


def client(tmp_path, **settings):
    from orchestrator.llm import LLMClient
    return LLMClient(Settings(**settings), None, state_dir=tmp_path / "state")


def pinned(http) -> bool:
    return type(http._transport._pool._network_backend).__name__ == "PinnedBackend"


# --- the privacy filter and the pinned client: one check ----------------------------------------------------------

def test_the_adapter_of_an_unfiltered_target_is_the_pinned_client_even_after_the_answer_changed(tmp_path, names):
    names["box.lan"] = ["192.168.1.20"]
    llm = client(tmp_path, ai_provider="custom", ai_model="m", ai_base_url="http://box.lan:8000/v1")
    target = llm.resolve()
    assert target.local is True, "labelled local by its address: this turn goes without the privacy filter"
    # the lookup ran out (LOOKUP_TTL) and the name now has only a public address
    names["box.lan"] = ["203.0.113.66"]
    locality.clear_cache()
    http = llm.adapter(target).http_client
    assert http is not None and pinned(http), "unfiltered prompts never go through an unpinned client"
    # the pinned client judges the name again when it connects: nothing reaches the public address
    with pytest.raises(locality.LocalityRefused) as info:
        http.get("http://box.lan:8000/v1/models", timeout=2)
    assert "no longer counts as local" in str(info.value)


def test_a_filtered_target_keeps_the_sdk_client_online(tmp_path, names):
    """The other direction stays as it was: a target the privacy filter covers (not local) gets the SDK's own client
    online, also when the name counts as local by the time the adapter is made."""
    names["box.example.com"] = ["203.0.113.66"]
    llm = client(tmp_path, ai_provider="custom", ai_model="m", ai_base_url="http://box.example.com:8000/v1")
    target = llm.resolve()
    assert target.local is False
    names["box.example.com"] = ["192.168.1.20"]
    locality.clear_cache()
    assert llm.adapter(target).http_client is None


# --- the certificate trust of the environment stays with the pinned clients -----------------------------------------

def own_ca(folder, name: str):
    """A throwaway CA and a server certificate for `name` signed by it (PEM files in `folder`): (the CA's certificate
    a client trusts, the server's certificate chain, the server's key). They carry the extensions a strict check
    (VERIFY_X509_STRICT) asks for."""
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.datetime.now(datetime.timezone.utc)

    def usage(**on):
        fields = ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment",
                  "key_agreement", "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")
        return x509.KeyUsage(**{f: on.get(f, False) for f in fields})

    def build(subject, issuer, public, signer, issuer_public, ca):
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(public)
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=5))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_public), critical=False))
        if ca:
            builder = builder.add_extension(usage(key_cert_sign=True, crl_sign=True), critical=True)
        else:
            builder = (builder.add_extension(usage(digital_signature=True), critical=True)
                       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                       .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False))
        return builder.sign(signer, hashes.SHA256())

    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "llmcf review CA")])
    ca = build(ca_name, ca_name, ca_key.public_key(), ca_key, ca_key.public_key(), True)
    server = build(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]), ca_name, key.public_key(), ca_key,
                   ca_key.public_key(), False)
    pem = serialization.Encoding.PEM
    ca_file, chain_file, key_file = folder / "own-ca.pem", folder / "chain.pem", folder / "key.pem"
    ca_file.write_bytes(ca.public_bytes(pem))
    chain_file.write_bytes(server.public_bytes(pem) + ca.public_bytes(pem))
    key_file.write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_file, chain_file, key_file


@contextlib.contextmanager
def https_server(chain, key):
    """An HTTPS server on 127.0.0.1 that answers every GET with a small JSON body; yields its port."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"result": {"collections": []}, "status": "ok", "time": 0.0, "data": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(chain, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def rag_for(settings):
    from orchestrator.Core import rag_manager as rag_module
    rag = rag_module.RAGManager.__new__(rag_module.RAGManager)
    rag.framework = SimpleNamespace(config=settings, secrets_manager=None)
    rag.config = settings
    return rag


def test_the_pinned_clients_keep_the_certificate_trust_of_the_environment(tmp_path, names, monkeypatch):
    """The SDK's own client trusts the CA that SSL_CERT_FILE names (httpx reads it, and SSL_CERT_DIR, with trust_env);
    the pinned clients turned trust_env off for the proxy and so lost that trust too: an https server in the own
    network with its own CA failed its TLS check online (a custom server labelled local) and in offline mode (every
    provider server, a Qdrant server)."""
    ca, chain, key = own_ca(tmp_path, "box.lan")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://203.0.113.1:9")   # the pinned clients never use it
    names["box.lan"] = ["127.0.0.1"]
    with https_server(chain, key) as port:
        online = client(tmp_path / "online", ai_provider="custom", ai_model="m",
                        ai_base_url=f"https://box.lan:{port}/v1")
        http = online._http_client_for(online.resolve())
        assert pinned(http) and http.get(f"https://box.lan:{port}/v1/models", timeout=10).status_code == 200
        from orchestrator.llm.client import offline_http_client
        offline = offline_http_client(Settings(offline_mode=True))
        assert offline.get(f"https://box.lan:{port}/v1/models", timeout=10).status_code == 200
        import httpx
        options = rag_for(Settings(offline_mode=True))._offline_server_options(f"https://box.lan:{port}")
        with httpx.Client(transport=options["transport"], trust_env=False) as qdrant:
            assert qdrant.get(f"https://box.lan:{port}/collections", timeout=10).status_code == 200


def test_the_pinned_clients_still_refuse_a_certificate_nobody_trusts(tmp_path, names, monkeypatch):
    """Keeping the environment's trust is no switch-off of the check: without the own CA the same server fails."""
    import httpx2
    _ca, chain, key = own_ca(tmp_path, "box.lan")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    names["box.lan"] = ["127.0.0.1"]
    with https_server(chain, key) as port:
        from orchestrator.llm.client import offline_http_client
        offline = offline_http_client(Settings(offline_mode=True))
        with pytest.raises(httpx2.ConnectError) as refused:
            offline.get(f"https://box.lan:{port}/v1/models", timeout=10)
    # The certificate check refused it, not anything else: OpenSSL says CERTIFICATE_VERIFY_FAILED, the system's trust
    # (truststore on Windows and macOS) its own words ("... not trusted by the trust provider"); both raise
    # ssl.SSLCertVerificationError, which the client keeps as the cause.
    causes, exc = [], refused.value
    while exc is not None and len(causes) < 10:
        causes.append(exc)
        exc = exc.__cause__ or exc.__context__
    assert any(isinstance(c, ssl.SSLCertVerificationError) for c in causes), [repr(c) for c in causes]


# --- (d) a Qdrant server client opened online, then offline mode ---------------------------------------------------

def test_a_qdrant_client_opened_online_is_not_used_once_offline_mode_is_on(names, monkeypatch):
    import qdrant_client

    from orchestrator.Core.rag_manager import KnowledgeBaseError, RAGManager
    from orchestrator.rag import client_store
    names["qdrant.example.com"] = ["203.0.113.30"]
    opened, closed = [], []

    class Qdrant:
        def __init__(self, **kwargs):
            opened.append(kwargs)

        def get_collections(self):
            return SimpleNamespace(collections=[])

        def close(self):
            closed.append(True)

    class Store:
        store_format = "server"
        collection = "framework_knowledge"
        model_id = "test-model"

        def __init__(self, client, *args, **kwargs):
            self.client = client

        def ensure(self):
            pass

    monkeypatch.setattr(qdrant_client, "QdrantClient", Qdrant)
    monkeypatch.setattr(client_store, "ClientKnowledgeStore", Store)
    settings = Settings(qdrant_url="https://qdrant.example.com:6333")
    framework = SimpleNamespace(config=settings, info=None,
                                secrets_manager=SimpleNamespace(get_secret=lambda key: "key-123"))
    rag = RAGManager(framework, embedder=SimpleNamespace(model_id="test-model", dim=4), sparse_embedder=object())
    store = rag._ensure_store()
    assert len(opened) == 1 and "transport" not in opened[0], "online: qdrant-client as before"
    assert rag._ensure_store() is store, "online the open client is used again"

    settings.values["offline_mode"] = True   # the person (or an AI, toward offline) switches offline mode on
    with pytest.raises(KnowledgeBaseError) as info:
        rag._ensure_store()
    assert "the Qdrant server (setting qdrant_url)" in str(info.value) and "203.0.113.30" in str(info.value)
    assert closed == [True] and len(opened) == 1, "the client opened online is closed, no new one to the cloud"
    assert rag.client is None and rag._store is None

    settings.values["offline_mode"] = False   # and back online: opened again as before
    assert rag._ensure_store() is not None and len(opened) == 2 and "transport" not in opened[1]


# --- a crawl that runs while offline mode is switched on ----------------------------------------------------------

@pytest.mark.parametrize("actor", ["", "mcp", "gui"])
def test_a_crawl_started_online_reads_nothing_more_once_offline_mode_is_on(monkeypatch, actor):
    """The crawler read offline mode once, when it was made: a long crawl (an AI's crawl_docs_site job or the
    person's Deep Ingest) started online kept reading public sites through the public-only guard after offline mode
    was switched on. Every address is judged again, so the rest of such a crawl is refused."""
    from orchestrator.Core import crawler_manager
    from orchestrator.Core.crawler_manager import OFFLINE_REFUSAL, CrawlerManager
    monkeypatch.setattr(crawler_manager, "validate_public_url", lambda url: True)   # a public site, online
    settings = Settings()
    crawler = CrawlerManager(SimpleNamespace(config=settings), {"request_delay": 0, "languages": []}, actor=actor,
                             _skip_robots_txt_for_tests=True)
    assert crawler._address_refusal("https://docs.example.com/guide") is None, "online: the public-only guard"
    settings.values["offline_mode"] = True
    assert crawler._address_refusal("https://docs.example.com/next") == OFFLINE_REFUSAL
    # the crawl's own loop says so for the next page, and asks for nothing
    asked = []
    monkeypatch.setattr(crawler.session, "send", lambda *a, **k: asked.append(a) or pytest.fail("a request was sent"))
    assert crawler.crawl("https://docs.example.com/next") == [] and asked == []
    assert [s.reason for s in crawler.skipped] == [OFFLINE_REFUSAL]
