"""Security tests: archive extraction, input validation, SSRF guard, secret redaction."""

import io
import json
import re
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.utils.helpers import redact_secrets, safe_extract_archive, sanitize_filename
from orchestrator.utils.validation import validate_ip_address, validate_public_url


class TestArchiveExtraction:

    def test_zip_slip_is_blocked(self, tmp_path):
        archive = tmp_path / "malicious.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped.txt", "pwned")
        dest = tmp_path / "extract"
        dest.mkdir()

        with pytest.raises(Exception, match="Zip Slip"):
            safe_extract_archive(archive, dest)
        assert not (tmp_path / "escaped.txt").exists()

    def test_tar_slip_is_blocked(self, tmp_path):
        archive = tmp_path / "malicious.tar"
        with tarfile.open(archive, "w") as tf:
            payload = b"pwned"
            info = tarfile.TarInfo("../escaped.txt")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        dest = tmp_path / "extract"
        dest.mkdir()

        with pytest.raises(Exception, match="Tar Slip"):
            safe_extract_archive(archive, dest)
        assert not (tmp_path / "escaped.txt").exists()

    def test_sibling_directory_with_same_prefix_is_blocked(self, tmp_path):
        archive = tmp_path / "prefix.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../extract2/escaped.txt", "pwned")
        dest = tmp_path / "extract"
        dest.mkdir()

        with pytest.raises(Exception, match="Zip Slip"):
            safe_extract_archive(archive, dest)
        assert not (tmp_path / "extract2").exists()

    @pytest.mark.parametrize("kind, link, match", [
        (tarfile.SYMTYPE, "../../etc", "symlink"),
        (tarfile.LNKTYPE, "../outside.txt", "hardlink"),
        (tarfile.CHRTYPE, "", "Device"),
    ])
    def test_dangerous_tar_members_are_blocked(self, tmp_path, kind, link, match):
        archive = tmp_path / "links.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo("inner")
            info.type = kind
            info.linkname = link
            tf.addfile(info)
        dest = tmp_path / "extract"
        dest.mkdir()

        with pytest.raises(Exception, match=match):
            safe_extract_archive(archive, dest)
        assert list(dest.iterdir()) == []

    def test_benign_archives_extract(self, tmp_path):
        src = tmp_path / "model.txt"
        src.write_text("weights")
        tgz = tmp_path / "ok.tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(src, arcname="sub/model.txt")
        dest = tmp_path / "extract"
        dest.mkdir()

        safe_extract_archive(tgz, dest)

        assert (dest / "sub" / "model.txt").read_text() == "weights"


class TestFilenameSanitization:

    @pytest.mark.parametrize("name", [
        "../../../etc/passwd", "file;rm -rf /", "file$(whoami).txt", "file`id`.txt", "file\x00.txt", "a/b\\c",
    ])
    def test_result_stays_inside_directory(self, tmp_path, name):
        sanitized = sanitize_filename(name)

        for forbidden in ("/", "\\", ";", "$", "`", "\x00", " "):
            assert forbidden not in sanitized
        assert (tmp_path / sanitized).resolve().parent == tmp_path.resolve()


class TestValidationResultTruthiness:
    """Call sites use `if not validate_x(...)`; a result object must behave like its .valid flag."""

    @pytest.mark.parametrize("ip", ["192.168.1.100", "10.0.0.1", "8.8.8.8", "::1"])
    def test_valid_ips(self, ip):
        assert validate_ip_address(ip)

    @pytest.mark.parametrize("ip", ["256.1.1.1", "192.168.1", "not-an-ip", "192.168.1.1.1", "", "1.2.3.4; rm -rf /"])
    def test_invalid_ips(self, ip):
        result = validate_ip_address(ip)
        assert not result
        assert result.valid is False


class TestSSRFGuard:

    @pytest.mark.parametrize("url", [
        "http://localhost/",
        "http://127.0.0.1:6333/collections",
        "http://10.0.0.5/",
        "http://192.168.1.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",   # Alibaba metadata, CGNAT range
        "http://100.64.0.1/",                         # Tailscale / CGNAT
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://2130706433/",                         # 127.0.0.1 as a decimal integer
        "http://127.0.0.1\\@example.com/",            # urllib and urllib3 disagree on the host
        "http://93.184.215.14\\@127.0.0.1/",
        "http://exa mple.com/",
        "http://93.184.215.14/\n",
        "file:///etc/passwd",
        "ftp://93.184.215.14/",
        "javascript:alert(1)",
        "not-a-url",
    ])
    def test_blocks_internal_ambiguous_and_non_http_targets(self, url):
        assert not validate_public_url(url)

    def test_allows_public_address(self):
        assert validate_public_url("https://93.184.215.14/docs")
        assert validate_public_url("http://93.184.215.14:8080/a/b?c=d")


# The crawler drops pages that langdetect does not classify as English, links included.
EN = " This guide explains how to install the software development kit on your single board computer. "


def response(status=200, html="", location=None, content_type="text/html", headers=None, content=None):
    from requests.structures import CaseInsensitiveDict

    all_headers = CaseInsensitiveDict({"Content-Type": content_type, **(headers or {})})
    if location:
        all_headers["Location"] = location
    return SimpleNamespace(status_code=status, headers=all_headers, text=html,
                           content=html.encode() if content is None else content,
                           is_redirect=status in (301, 302, 303, 307, 308) and location is not None)


class FakeWeb:
    """Stands in for Session.send; records every request so tests can assert after crawl()."""

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def __call__(self, prepared, **kwargs):
        self.requests.append((prepared.url, kwargs))
        page = self.pages.get(prepared.url)
        if callable(page):
            return page(prepared.url)
        return page if page is not None else response(404)

    @property
    def urls(self):
        return [u for u, _ in self.requests]

    @property
    def page_urls(self):
        """Requested URLs without the per-origin robots.txt and TDMRep files."""
        return [u for u in self.urls if not u.endswith(("/robots.txt", "/.well-known/tdmrep.json"))]


class TestCrawlerSecurity:

    @pytest.fixture
    def make_crawler(self, tmp_path, monkeypatch):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.crawler_manager import CrawlerManager

        def factory(pages, **options):
            options.setdefault("respect_robots", False)
            options.setdefault("request_delay", 0)
            crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path)), options)
            web = FakeWeb(pages)
            monkeypatch.setattr(crawler.session, "send", web)
            return crawler, web

        return factory

    def test_internal_url_is_never_fetched(self, make_crawler):
        crawler, web = make_crawler({})
        assert crawler.crawl("http://127.0.0.1:6333/") == []
        assert web.requests == []

    def test_redirect_to_internal_address_is_not_followed(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/docs": response(302, location="http://169.254.169.254/latest/meta-data/"),
        })

        assert crawler.crawl("http://93.184.215.14/docs") == []
        assert web.urls == ["http://93.184.215.14/.well-known/tdmrep.json", "http://93.184.215.14/docs"]
        assert all(kwargs["allow_redirects"] is False for _, kwargs in web.requests)

    def test_backslash_redirect_never_reaches_internal_host(self, make_crawler):
        from urllib3.util import parse_url

        crawler, web = make_crawler({
            "http://93.184.215.14/docs": response(302, location="http://127.0.0.1:8080\\@example.com/secret"),
        })
        crawler.crawl("http://93.184.215.14/docs")
        assert all(parse_url(u).host not in ("127.0.0.1", "localhost") for u in web.urls), web.urls

    def test_redirect_to_other_public_host_is_crawled_with_its_links(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/": response(301, location="http://93.184.215.15/"),
            "http://93.184.215.15/": response(html='<a href="/guide">Guide</a>' + EN),
            "http://93.184.215.15/guide": response(html="Guide." + EN),
        })

        docs = crawler.crawl("http://93.184.215.14/")

        assert [d.metadata["source"] for d in docs] == ["http://93.184.215.15/", "http://93.184.215.15/guide"]

    def test_redirect_chain_is_bounded(self, make_crawler):
        from orchestrator.Core.crawler_manager import CrawlerManager

        def endless(url):
            n = int(url.rsplit("/", 1)[1])
            return response(302, location=f"http://93.184.215.14/r/{n + 1}")

        crawler, web = make_crawler({f"http://93.184.215.14/r/{i}": endless for i in range(100)})
        crawler.crawl("http://93.184.215.14/r/0")
        assert len(web.page_urls) == CrawlerManager.MAX_REDIRECTS + 1

    def test_total_requests_are_bounded(self, make_crawler):
        links = "".join(f'<a href="/missing/{i}">x</a>' for i in range(50))
        crawler, web = make_crawler({"http://93.184.215.14/": response(html=EN * 6 + links)}, max_pages=2)
        crawler.crawl("http://93.184.215.14/")
        assert len(web.page_urls) == crawler.max_requests == 10

    def test_robots_txt_redirect_to_internal_address_is_not_followed(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/robots.txt": response(302, location="http://127.0.0.1:8080/admin/delete?all=1"),
            "http://93.184.215.14/docs": response(html="Docs." + EN),
        }, respect_robots=True)

        docs = crawler.crawl("http://93.184.215.14/docs")

        assert web.urls == ["http://93.184.215.14/robots.txt", "http://93.184.215.14/.well-known/tdmrep.json",
                            "http://93.184.215.14/docs"]
        assert len(docs) == 1
        assert all(kwargs["allow_redirects"] is False for _, kwargs in web.requests)

    def test_robots_txt_rules_are_honoured(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/robots.txt": response(html="User-agent: *\nDisallow: /private\n", content_type="text/plain"),
            "http://93.184.215.14/": response(html='<a href="/private/x">p</a><a href="/public">q</a>' + EN),
            "http://93.184.215.14/public": response(html="Public." + EN),
        }, respect_robots=True)

        crawler.crawl("http://93.184.215.14/")

        assert "http://93.184.215.14/private/x" not in web.urls
        assert "http://93.184.215.14/public" in web.urls

    def test_pdfs_are_skipped_when_disabled(self, make_crawler):
        crawler, web = make_crawler({}, download_pdfs=False)
        assert crawler.crawl("http://93.184.215.14/manual.pdf") == []
        assert web.requests == []


class TestSecretRedaction:

    @pytest.mark.parametrize("secret, marker", [
        ("sk-proj-AbCdEf0123456789_xyz-ABCDEFGHIJ", "sk-REDACTED"),
        ("sk-ant-api03-AbCdEf0123456789abcdefghij", "sk-REDACTED"),
        ("sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2 + "k1L2m3N4", "sk-REDACTED"),   # legacy 48-char OpenAI key
        ("hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456", "hf_REDACTED"),
        ("AIzaSyA1234567890abcdefghijklmnopqrstuv", "AIza-REDACTED"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "gh-REDACTED"),
    ])
    def test_known_key_formats_are_redacted(self, secret, marker):
        text = f"Traceback ... api_key={secret} failed"
        redacted = redact_secrets(text)
        assert secret not in redacted
        assert marker in redacted

    @pytest.mark.parametrize("wrapped", [
        repr("OPENAI_API_KEY:\n" + "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2 + "k1L2m3N4"),
        "Authorization:Bearer%20sk-proj-AbCdEf0123456789_xyz-ABCDEFGHIJ",
        repr("token\n" + "hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456"),
    ])
    def test_keys_after_escapes_are_redacted(self, wrapped):
        assert "REDACTED" in redact_secrets(wrapped)

    def test_normal_text_is_untouched(self):
        text = "Build failed: sk-learn not found, see task-runner and disk-usage-monitoring-service logs"
        assert redact_secrets(text) == text

    def test_telemetry_uses_redaction(self):
        from orchestrator.utils.telemetry import TelemetryManager

        tm = TelemetryManager(SimpleNamespace(config={}))
        report = tm._sanitize(f"key sk-proj-AbCdEf0123456789_xyz-ABCDEFGHIJ in {Path.home()}/project")

        assert "sk-proj-AbCdEf" not in report
        assert str(Path.home()) not in report

    def test_swarm_upload_uses_redaction(self):
        from orchestrator.Core.community_manager import CommunityManager

        assert "sk-REDACTED" in CommunityManager._sanitize_content(None, "token sk-ant-api03-AbCdEf0123456789abcdefghij")



class TestConnectionPinning:
    """The address is checked again when the socket is opened (DNS rebinding)."""

    @staticmethod
    def fake_dns(monkeypatch, answers):
        import socket as socket_module
        real = socket_module.getaddrinfo
        calls = []

        def getaddrinfo(host, port, *args, **kwargs):
            if host in answers:
                calls.append(host)
                ips = answers[host]
                ip = ips[min(len(calls), len(ips)) - 1]
                family = socket_module.AF_INET6 if ":" in ip else socket_module.AF_INET
                return [(family, socket_module.SOCK_STREAM, 6, "", (ip, port))]
            return real(host, port, *args, **kwargs)

        monkeypatch.setattr(socket_module, "getaddrinfo", getaddrinfo)
        return calls

    def test_crawler_session_uses_pinned_pools(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.crawler_manager import CrawlerManager, PublicOnlyAdapter, _PinnedHTTPSConnectionPool

        crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path)))
        adapter = crawler.session.get_adapter("https://example.com/")
        assert isinstance(adapter, PublicOnlyAdapter)
        assert adapter.poolmanager.pool_classes_by_scheme["https"] is _PinnedHTTPSConnectionPool

    def test_connect_goes_to_the_vetted_ip_and_keeps_the_host_name(self, monkeypatch):
        import urllib3.util.connection
        from orchestrator.Core.crawler_manager import _PinnedHTTPSConnection

        self.fake_dns(monkeypatch, {"docs.example": ["93.184.215.14"]})
        seen = []
        monkeypatch.setattr(urllib3.util.connection, "create_connection", lambda address, *a, **k: seen.append(address) or object())

        conn = _PinnedHTTPSConnection("docs.example", 443)
        conn._new_conn()

        assert seen == [("93.184.215.14", 443)]
        assert conn.host == "docs.example"

    def test_dns_rebinding_never_reaches_the_internal_server(self, tmp_path, monkeypatch):
        import http.server
        import threading
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.crawler_manager import CrawlerManager

        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"internal secret")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            # Public for the URL checks, loopback when the socket is opened.
            self.fake_dns(monkeypatch, {"rebind.test": ["93.184.215.14", "93.184.215.14", "127.0.0.1"]})
            monkeypatch.delenv("HTTP_PROXY", raising=False)
            monkeypatch.delenv("http_proxy", raising=False)
            crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path)),
                                     {"respect_robots": False, "request_delay": 0})

            assert crawler.crawl(f"http://rebind.test:{port}/admin") == []
            assert hits == []
        finally:
            server.shutdown()

    def test_idn_hosts_are_accepted(self, monkeypatch):
        self.fake_dns(monkeypatch, {"xn--bcher-kva.example": ["93.184.215.14"]})
        assert validate_public_url("https://bücher.example/docs")

    @pytest.mark.parametrize("url", [
        "http://[64:ff9b::7f00:1]/",   # NAT64 of 127.0.0.1
        "http://[::127.0.0.1]/",       # IPv4-compatible
        "http://[2002:7f00:1::]/",     # 6to4 of 127.0.0.1
        "http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/",  # Teredo
    ])
    def test_ipv4_wrapped_in_ipv6_is_blocked(self, url):
        assert not validate_public_url(url)


class TestCrawlerPoliteness:

    @pytest.fixture
    def make_crawler(self, tmp_path, monkeypatch):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.crawler_manager import CrawlerManager

        def factory(pages, **options):
            options.setdefault("request_delay", 0)
            crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path)), options)
            web = FakeWeb(pages)
            monkeypatch.setattr(crawler.session, "send", web)
            return crawler, web

        return factory

    @pytest.mark.parametrize("status", [500, 503])
    def test_robots_server_error_means_disallow(self, make_crawler, status):
        crawler, web = make_crawler({
            "http://93.184.215.14/robots.txt": response(status),
            "http://93.184.215.14/docs": response(html="Docs." + EN),
        }, respect_robots=True)

        crawler.crawl("http://93.184.215.14/docs")

        assert web.urls == ["http://93.184.215.14/robots.txt"]

    def test_robots_network_error_means_disallow(self, make_crawler):
        import requests

        def unreachable(url):
            raise requests.ConnectionError("connection reset")

        crawler, web = make_crawler({"http://93.184.215.14/robots.txt": unreachable}, respect_robots=True)
        crawler.crawl("http://93.184.215.14/docs")
        assert web.urls == ["http://93.184.215.14/robots.txt"]

    def test_ca_bundle_from_environment_is_used(self, make_crawler, monkeypatch, tmp_path):
        bundle = tmp_path / "corporate-ca.pem"
        bundle.write_text("dummy")
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
        crawler, web = make_crawler({"https://93.184.215.14/": response(html="Home." + EN)}, respect_robots=False)

        crawler.crawl("https://93.184.215.14/")

        assert web.requests[0][1]["verify"] == str(bundle)

    def test_links_with_spaces_are_fetched_encoded(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/": response(html='<a href="/files/User Guide.html">guide</a>' + EN),
            "http://93.184.215.14/files/User%20Guide.html": response(html="Guide." + EN),
        }, respect_robots=False)

        docs = crawler.crawl("http://93.184.215.14/")

        assert "http://93.184.215.14/files/User%20Guide.html" in web.urls
        assert len(docs) == 2

    def test_pages_are_yielded_one_by_one(self, make_crawler):
        """The caller stores each page before the next request (so Stop keeps what was read)."""
        crawler, web = make_crawler({
            "http://93.184.215.14/": response(html='<a href="/a">a</a><a href="/b">b</a>' + EN),
            "http://93.184.215.14/a": response(html="A." + EN),
            "http://93.184.215.14/b": response(html="B." + EN),
        }, respect_robots=False)

        pages = crawler.iter_crawl("http://93.184.215.14/")
        assert next(pages).metadata["source"] == "http://93.184.215.14/"
        assert web.page_urls == ["http://93.184.215.14/"]
        assert [d.metadata["source"] for d in pages] == ["http://93.184.215.14/a", "http://93.184.215.14/b"]

    def test_stop_cuts_the_politeness_delay_short(self, make_crawler):
        import threading
        import time

        crawler, web = make_crawler({"http://93.184.215.14/": response(html="Home." + EN)},
                                    respect_robots=False, request_delay=30)
        stop = threading.Event()
        threading.Timer(0.1, stop.set).start()

        start = time.monotonic()
        assert list(crawler.iter_crawl("http://93.184.215.14/", stop=stop)) == []
        assert time.monotonic() - start < 5
        assert crawler.stopped and web.page_urls == []

    def test_unfetchable_pages_are_listed_as_failed(self, make_crawler):
        import requests
        from orchestrator.Core.crawler_manager import CrawlerManager

        def reset(url):
            raise requests.ConnectionError("connection reset\x1b[2J")

        def endless(url):
            n = int(url.rsplit("/", 1)[1])
            return response(302, location=f"http://93.184.215.14/r/{n + 1}")

        pages = {"http://93.184.215.14/": response(html='<a href="/gone">g</a><a href="/reset">r</a>'
                                                      '<a href="/r/0">loop</a>' + EN),
                 "http://93.184.215.14/reset": reset}
        pages.update({f"http://93.184.215.14/r/{i}": endless for i in range(10)})
        crawler, web = make_crawler(pages, respect_robots=False)

        crawler.crawl("http://93.184.215.14/")

        failed = {page.url: page.reason for page in crawler.failed}
        assert failed["http://93.184.215.14/gone"] == "HTTP status 404"
        assert failed["http://93.184.215.14/reset"] == "connection reset\\u001b[2J"   # escaped for the log
        assert failed[f"http://93.184.215.14/r/{CrawlerManager.MAX_REDIRECTS}"] == "more than 5 redirects"
        assert crawler.skipped == [] and not crawler.stopped


SITE = "http://93.184.215.14"
OTHER_SITE = "http://93.184.215.15"
TDM_FILE = "/.well-known/tdmrep.json"


def page_with(head="", body='<a href="/guide">Guide</a>', **kwargs):
    return response(html=f"<html><head>{head}</head><body>{body}{EN}</body></html>", **kwargs)


def tdm_file(*rules):
    return response(html=json.dumps([{"location": loc, "tdm-reservation": flag} for loc, flag in rules]),
                    content_type="application/json")


class TestCrawlerOptOuts:
    """Machine-readable reservations (robots directives, TDMRep) keep pages out of the knowledge base, and
    every skipped page is named with its reason (EU DSM Art. 4 / UrhG 44b: mining unless reserved)."""

    @pytest.fixture
    def make_crawler(self, tmp_path, monkeypatch):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.crawler_manager import CrawlerManager

        def factory(pages, **options):
            options.setdefault("respect_robots", False)
            options.setdefault("request_delay", 0)
            crawler = CrawlerManager(SimpleNamespace(config=ConfigManager(tmp_path)), options)
            web = FakeWeb(pages)
            monkeypatch.setattr(crawler.session, "send", web)
            return crawler, web

        return factory

    @staticmethod
    def sources(docs):
        return sorted(d.metadata["source"] for d in docs)

    @staticmethod
    def reasons(crawler):
        assert all(page.opt_out for page in crawler.skipped)
        return {page.url: page.reason for page in crawler.skipped}

    def test_user_agent_reports_the_framework_version(self, make_crawler, monkeypatch):
        from orchestrator import __version__

        crawler, _ = make_crawler({})
        sent = []
        monkeypatch.setattr(crawler.session, "send",
                            lambda prepared, **kw: sent.append(prepared.headers["User-Agent"]) or response(404))
        crawler.crawl(f"{SITE}/")

        assert sent and all(ua.startswith(f"LLM-Conversion-Framework-Bot/{__version__} (") for ua in sent)

    @pytest.mark.parametrize("directive", ["noindex", "none", "noai", "noimageai", "NoAI"])
    def test_x_robots_tag_keeps_the_page_out(self, make_crawler, directive):
        crawler, web = make_crawler({
            f"{SITE}/": page_with(headers={"X-Robots-Tag": directive}),
            f"{SITE}/guide": page_with(body="Guide."),
        })

        docs = crawler.crawl(f"{SITE}/")

        followed = directive != "none"  # "none" is noindex plus nofollow
        assert self.sources(docs) == ([f"{SITE}/guide"] if followed else [])
        assert (f"{SITE}/guide" in web.urls) is followed
        assert self.reasons(crawler) == {f"{SITE}/": f"X-Robots-Tag: {directive.lower()}"}

    def test_x_robots_tag_nofollow_keeps_the_page_but_not_its_links(self, make_crawler):
        crawler, web = make_crawler({
            f"{SITE}/": page_with(headers={"X-Robots-Tag": "nofollow"}),
            f"{SITE}/guide": page_with(body="Guide."),
        })

        assert self.sources(crawler.crawl(f"{SITE}/")) == [f"{SITE}/"]
        assert f"{SITE}/guide" not in web.urls
        assert crawler.skipped == []

    @pytest.mark.parametrize("header, kept, followed", [
        ("googlebot: noindex, nofollow", True, True),           # another crawler's rules
        ("LLM-Conversion-Framework-Bot: noai", False, True),     # ours
        ("googlebot: noindex, llm-conversion-framework-bot: nofollow", True, False),
        ("unavailable_after: 25 Jun 2010 15:00:00 PST, noindex", False, True),
    ])
    def test_x_robots_tag_scopes(self, make_crawler, header, kept, followed):
        crawler, web = make_crawler({
            f"{SITE}/": page_with(headers={"X-Robots-Tag": header}),
            f"{SITE}/guide": page_with(body="Guide."),
        })

        sources = self.sources(crawler.crawl(f"{SITE}/"))

        assert (f"{SITE}/" in sources) is kept
        assert (f"{SITE}/guide" in web.urls) is followed

    def test_repeated_x_robots_tag_headers_keep_their_own_scope(self, make_crawler):
        from urllib3 import HTTPHeaderDict

        raw = HTTPHeaderDict()
        raw.add("X-Robots-Tag", "googlebot: noindex")
        raw.add("X-Robots-Tag", "noai")  # a header of its own: applies to every crawler
        page = page_with(headers={"X-Robots-Tag": "googlebot: noindex, noai"})  # how requests joins them
        page.raw = SimpleNamespace(headers=raw)
        crawler, _ = make_crawler({f"{SITE}/": page})

        assert crawler.crawl(f"{SITE}/") == []
        assert self.reasons(crawler) == {f"{SITE}/": "X-Robots-Tag: noai"}

    @pytest.mark.parametrize("meta, reason, followed", [
        ('<meta name="robots" content="noindex">', '<meta name="robots">: noindex', True),
        ('<meta name="ROBOTS" content="noimageai, noai">', '<meta name="robots">: noai, noimageai', True),
        ('<meta name="LLM-Conversion-Framework-Bot" content="none">',
         '<meta name="llm-conversion-framework-bot">: none', False),
        ('<meta name="tdm-reservation" content="1">', 'TDM reservation (<meta name="tdm-reservation">)', True),
        ('<meta name="robots" content="noindex nofollow">', '<meta name="robots">: noindex', False),   # spaces only
    ])
    def test_meta_tags_keep_the_page_out(self, make_crawler, meta, reason, followed):
        crawler, web = make_crawler({f"{SITE}/": page_with(meta), f"{SITE}/guide": page_with(body="Guide.")})

        docs = crawler.crawl(f"{SITE}/")

        assert f"{SITE}/" not in self.sources(docs)
        assert (f"{SITE}/guide" in web.urls) is followed
        assert self.reasons(crawler) == {f"{SITE}/": reason}

    @pytest.mark.parametrize("meta, followed", [
        ('<meta name="robots" content="nofollow">', False),
        ('<meta name="googlebot" content="noindex, nofollow">', True),   # another crawler's rules
        ('<meta name="tdm-reservation" content="0">', True),
        ('<meta name="robots" content="max-image-preview: none">', True),   # "none" is a value here
    ])
    def test_meta_tags_that_keep_the_page(self, make_crawler, meta, followed):
        crawler, web = make_crawler({f"{SITE}/": page_with(meta), f"{SITE}/guide": page_with(body="Guide.")})

        assert f"{SITE}/" in self.sources(crawler.crawl(f"{SITE}/"))
        assert (f"{SITE}/guide" in web.urls) is followed
        assert crawler.skipped == []

    @pytest.mark.parametrize("value, reserved", [("1", True), (" 1 ", True), ("0", False), ("yes", False)])
    def test_tdm_reservation_header_alone(self, make_crawler, value, reserved):
        crawler, web = make_crawler({
            f"{SITE}/": page_with(headers={"tdm-reservation": value, "tdm-policy": f"{SITE}/policy.json"}),
            f"{SITE}/guide": page_with(body="Guide."),
        })

        docs = crawler.crawl(f"{SITE}/")

        assert f"{SITE}{TDM_FILE}" in web.urls  # answered 404: the site has no TDMRep file
        assert (f"{SITE}/" not in self.sources(docs)) is reserved
        assert f"{SITE}/guide" in self.sources(docs)
        if reserved:
            assert self.reasons(crawler) == {
                f"{SITE}/": f"TDM reservation (tdm-reservation header, policy {SITE}/policy.json)"}
        else:
            assert crawler.skipped == []  # "yes" is a protocol error, which TDMRep treats as unset

    @pytest.mark.parametrize("value", ["1, 1", "0, 1"])
    def test_repeated_tdm_reservation_headers(self, make_crawler, value):
        """requests joins repeated headers with commas; a reservation in any of them counts."""
        from urllib3 import HTTPHeaderDict

        joined = page_with(headers={"tdm-reservation": value})
        separate = page_with(headers={"tdm-reservation": value})
        raw = HTTPHeaderDict()
        for item in value.split(", "):
            raw.add("tdm-reservation", item)
        separate.raw = SimpleNamespace(headers=raw)
        crawler, _ = make_crawler({f"{SITE}/": joined, f"{SITE}/b": separate})

        assert crawler.crawl(f"{SITE}/") == [] and crawler.crawl(f"{SITE}/b") == []
        assert self.reasons(crawler) == {f"{SITE}/b": "TDM reservation (tdm-reservation header)"}

    def test_tdmrep_file_with_a_byte_order_mark(self, make_crawler):
        raw = b"\xef\xbb\xbf" + json.dumps([{"location": "/", "tdm-reservation": 1}]).encode()
        crawler, web = make_crawler({
            f"{SITE}{TDM_FILE}": response(html=raw.decode("utf-8"), content=raw, content_type="application/json"),
            f"{SITE}/": page_with(),
        })

        assert crawler.crawl(f"{SITE}/") == []
        assert web.urls == [f"{SITE}{TDM_FILE}"]

    @pytest.mark.parametrize("location", ["private/*", f"{SITE}/private/*"])
    def test_tdmrep_location_written_relative_or_as_url(self, make_crawler, location):
        crawler, web = make_crawler({
            f"{SITE}{TDM_FILE}": tdm_file((location, 1)),
            f"{SITE}/": page_with(body='<a href="/private/a">a</a>'),
            f"{SITE}/private/a": page_with(body="Private."),
        })

        assert self.sources(crawler.crawl(f"{SITE}/")) == [f"{SITE}/"]
        assert f"{SITE}/private/a" not in web.urls

    @pytest.mark.parametrize("rules, path", [
        ("User-agent: *\nDisallow: /*.pdf$\n", "/files/manual.pdf"),       # '*' and '$' patterns
        ("User-agent: *\nDisallow: /*/private\n", "/docs/private"),
        ("User-agent: *\nDisallow: *\n", "/docs"),
        ("User-agent: *\nAllow: /\nDisallow: /private\n", "/private"),      # the longest rule wins, not the first
        ("User-agent: *\nDisallow: /x\n\nUser-agent: *\nDisallow: /private\n", "/private"),   # groups merged
        ("\ufeffUser-agent: *\nDisallow: /\n", "/docs"),                     # byte order mark
        ("User-agent: LLM-Conversion-Framework-Bot/3.1\nDisallow: /\n", "/docs"),   # our token with a version
        ("User-agent: otherbot\nUser-agent: *\nDisallow: /docs # comment\n", "/docs"),
        ("User-agent: *\nAllow: https://example.org/\nDisallow: /private\n", "/private"),   # URL counts as "/"
    ])
    def test_robots_txt_as_in_rfc_9309(self, make_crawler, rules, path):
        raw = rules.encode()   # requests decodes text/plain without a charset as ISO-8859-1
        crawler, web = make_crawler({
            f"{SITE}/robots.txt": response(html=raw.decode("latin-1"), content=raw, content_type="text/plain"),
            f"{SITE}{path}": page_with(body="P."),
        }, respect_robots=True)

        assert crawler.crawl(f"{SITE}{path}") == []
        assert f"{SITE}{path}" not in web.urls
        assert self.reasons(crawler) == {f"{SITE}{path}": "disallowed by robots.txt"}

    @pytest.mark.parametrize("rules", [
        "User-agent: *\nDisallow: /private\nAllow: /private/public\n",          # the longer allow wins
        "User-agent: *\nAllow: /private/public\nDisallow: /private/public\n",   # a tie goes to allow
        "User-agent: llm-conversion-framework-bot\nDisallow:\n\nUser-agent: *\nDisallow: /\n",   # our group
        "User-agent: otherbot\nDisallow: /\n",
        "User-agent: *\nDisallow: /private/public/x\n",
    ])
    def test_robots_txt_rules_that_allow(self, make_crawler, rules):
        crawler, _ = make_crawler({
            f"{SITE}/robots.txt": response(html=rules, content_type="text/plain"),
            f"{SITE}/private/public": page_with(body="P."),
        }, respect_robots=True)

        assert self.sources(crawler.crawl(f"{SITE}/private/public")) == [f"{SITE}/private/public"]

    def test_tdmrep_file_alone_reserves_paths_before_they_are_fetched(self, make_crawler):
        crawler, web = make_crawler({
            f"{SITE}{TDM_FILE}": tdm_file(("/private/*", 1)),
            f"{SITE}/": page_with(body='<a href="/private/a">a</a><a href="/public">p</a><a href="/private">x</a>'),
            f"{SITE}/private/a": page_with(body="Private."),
            f"{SITE}/public": page_with(body="Public."),
            f"{SITE}/private": page_with(body="Not under /private/."),
        })

        docs = crawler.crawl(f"{SITE}/")

        assert self.sources(docs) == [f"{SITE}/", f"{SITE}/private", f"{SITE}/public"]
        assert f"{SITE}/private/a" not in web.urls
        assert web.urls.count(f"{SITE}{TDM_FILE}") == 1  # cached per origin
        assert self.reasons(crawler) == {
            f"{SITE}/private/a": f"TDM reservation ({TDM_FILE} rule '/private/*')"}

    def test_tdmrep_first_matching_rule_decides(self, make_crawler):
        crawler, web = make_crawler({
            f"{SITE}{TDM_FILE}": tdm_file(("/docs/open/*", 0), ("/docs/*", 1)),
            f"{SITE}/": page_with(body='<a href="/docs/open/a">a</a><a href="/docs/b">b</a>'),
            f"{SITE}/docs/open/a": page_with(body="Open."),
            f"{SITE}/docs/b": page_with(body="Reserved."),
        })

        docs = crawler.crawl(f"{SITE}/")

        assert self.sources(docs) == [f"{SITE}/", f"{SITE}/docs/open/a"]
        assert f"{SITE}/docs/b" not in web.urls

    def test_tdmrep_whole_site_reservation_fetches_no_page(self, make_crawler):
        crawler, web = make_crawler({f"{SITE}{TDM_FILE}": tdm_file(("/", 1)), f"{SITE}/": page_with()})

        assert crawler.crawl(f"{SITE}/") == []
        assert web.urls == [f"{SITE}{TDM_FILE}"]

    @pytest.mark.parametrize("answer", [
        response(404), response(500), response(html="not json"), response(html='{"location": "/"}'),
        response(html='[{"location": "/", "tdm-reservation": "yes"}]'),   # protocol error: unset
        response(302, location="http://127.0.0.1:8080/tdmrep.json"),        # SSRF guard: never fetched
    ])
    def test_unusable_tdmrep_file_means_no_reservation(self, make_crawler, answer):
        from urllib3.util import parse_url

        crawler, web = make_crawler({f"{SITE}{TDM_FILE}": answer, f"{SITE}/": page_with(body="Home.")})

        assert self.sources(crawler.crawl(f"{SITE}/")) == [f"{SITE}/"]
        assert all(parse_url(u).host == "93.184.215.14" for u in web.urls)

    def test_tdmrep_file_is_followed_through_redirects(self, make_crawler):
        crawler, web = make_crawler({
            f"{SITE}{TDM_FILE}": response(301, location=f"{OTHER_SITE}/tdm.json"),
            f"{OTHER_SITE}/tdm.json": tdm_file(("/", 1)),
            f"{SITE}/": page_with(),
        })

        assert crawler.crawl(f"{SITE}/") == []
        assert f"{SITE}/" not in web.urls

    @pytest.mark.parametrize("other_site, reason", [
        ({f"{OTHER_SITE}{TDM_FILE}": tdm_file(("/", 1))}, f"TDM reservation ({TDM_FILE} rule '/')"),
        ({f"{OTHER_SITE}/robots.txt": response(html="User-agent: *\nDisallow: /\n", content_type="text/plain")},
         "disallowed by robots.txt"),
        ({f"{OTHER_SITE}/": page_with(headers={"X-Robots-Tag": "noai"})}, "X-Robots-Tag: noai"),
    ])
    def test_redirect_onto_a_reserved_host(self, make_crawler, other_site, reason):
        crawler, web = make_crawler({
            f"{SITE}/": response(301, location=f"{OTHER_SITE}/"),
            f"{OTHER_SITE}/": page_with(),
            **other_site,
        }, respect_robots=True)

        assert crawler.crawl(f"{SITE}/") == []
        assert self.reasons(crawler) == {f"{OTHER_SITE}/": reason}
        if "X-Robots-Tag" not in reason:
            assert f"{OTHER_SITE}/" not in web.urls  # reserved before the request

    def test_redirect_from_a_deeper_page_to_another_host_is_skipped(self, make_crawler):
        crawler, web = make_crawler({
            f"{SITE}/": page_with(body='<a href="/download">dl</a>'),
            f"{SITE}/download": response(302, location=f"{OTHER_SITE}/file"),
            f"{OTHER_SITE}/file": page_with(body="Elsewhere."),
        })

        docs = crawler.crawl(f"{SITE}/")

        assert self.sources(docs) == [f"{SITE}/"]
        assert not [u for u in web.urls if u.startswith(OTHER_SITE)]
        assert [(p.url, p.opt_out) for p in crawler.skipped] == [(f"{OTHER_SITE}/file", False)]
        assert "leaves the crawled site" in crawler.skipped[0].reason

    @pytest.mark.parametrize("xmp", [b"<tdm:reservation>1</tdm:reservation>", b'tdm:reservation="1"'])
    def test_tdm_reservation_in_pdf_metadata(self, make_crawler, xmp):
        crawler, _ = make_crawler({
            f"{SITE}/manual.pdf": response(content_type="application/pdf", content=b"%PDF-1.7\n" + xmp + b"\n%%EOF"),
        })

        assert crawler.crawl(f"{SITE}/manual.pdf") == []
        assert self.reasons(crawler) == {f"{SITE}/manual.pdf": "TDM reservation (tdm:reservation in the PDF metadata)"}

    def test_each_skip_is_logged_with_its_reason(self, make_crawler, monkeypatch):
        crawler, _ = make_crawler({f"{SITE}/": page_with(headers={"X-Robots-Tag": "noai"})})
        logged = []
        monkeypatch.setattr(crawler.logger, "log", lambda level, msg, *a, **k: logged.append(msg))

        crawler.crawl(f"{SITE}/")

        assert f"Skipped {SITE}/: X-Robots-Tag: noai" in logged

    def test_site_controlled_text_in_reasons_is_escaped(self, make_crawler):
        crawler, _ = make_crawler({
            f"{SITE}{TDM_FILE}": tdm_file(("/‮hidden\n*", 1)),
            f"{SITE}/": page_with(body='<a href="/%E2%80%AEhidden%0Ax">x</a>'),
        })

        crawler.crawl(f"{SITE}/")

        reason = self.reasons(crawler)[f"{SITE}/%E2%80%AEhidden%0Ax"]
        assert "\n" not in reason and "‮" not in reason

    @pytest.mark.parametrize("location, path, expected", [
        ("/", "/anything", True),
        ("/docs/*", "/docs/a/b", True),
        ("/docs/*", "/doc", False),
        ("/docs$", "/docs", True),
        ("/docs$", "/docs/x", False),
        ("*.pdf$", "/files/a.pdf", True),
        ("*.pdf$", "/files/a.pdf.html", False),
        ("/a*b*c", "/axxbyyc/z", True),
        ("/a*b*c$", "/axxbyycz", False),
        ("/Docs", "/docs", False),               # case-sensitive
        ("/User Guide", "/User%20Guide", True),  # compared percent-encoded
        ("/*$", "/", True),
        ("docs/*", "/docs/a", True),                      # written relative: from the root
        ("https://example.org/docs/*", "/docs/a", True),  # written as a URL: its path
        ("https://example.org/docs/*", "/other", False),
    ])
    def test_tdm_location_patterns(self, location, path, expected):
        from orchestrator.Core.crawler_manager import tdm_location_matches
        assert tdm_location_matches(location, path) is expected

    def test_pathological_tdm_pattern_is_fast(self):
        import time as time_module
        from orchestrator.Core.crawler_manager import tdm_location_matches

        start = time_module.monotonic()
        assert not tdm_location_matches("/" + "*a" * 200 + "b", "/" + "a" * 5000)
        assert time_module.monotonic() - start < 1.0


class TestCrawlsStayWithTheUser:
    """Crawls start only from the GUI's Deep Ingest: MCP clients and Ditto have no way to start one."""

    AUTOMATION = ["orchestrator/mcp_server.py", "orchestrator/Core/ditto_manager.py", "orchestrator/tools",
                  "orchestrator/llm"]

    def test_automation_code_never_reaches_the_crawler(self):
        import ast
        from conftest import REPO_ROOT

        files = []
        for entry in self.AUTOMATION:
            path = REPO_ROOT / entry
            files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
        offenders = []
        for path in files:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Attribute):
                    names.append(node.attr)
                elif isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.append(node.value)   # getattr(rag, "ingest_url")
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names += [getattr(node, "module", None) or ""] + [a.name for a in node.names]
                for name in names:
                    if name in ("ingest_url", "crawl", "iter_crawl", "CrawlerManager") or "crawler_manager" in name:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {name}")
        assert len(files) > 5 and offenders == []

    def test_no_tool_crawls_or_takes_a_url(self, tmp_path):
        from unittest.mock import Mock
        from orchestrator.tools.framework_tools import register_framework_tools
        from orchestrator.tools.policy import PolicyStore
        from orchestrator.tools.registry import ToolRegistry

        registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path)), SimpleNamespace(build_service=Mock()))
        assert registry.list()
        for spec in registry.list():
            assert not re.search(r"crawl|ingest|url|fetch", spec.name + spec.description, re.I), spec.name
            assert not [f for f in spec.input_model.model_fields if "url" in f.lower()], spec.name
