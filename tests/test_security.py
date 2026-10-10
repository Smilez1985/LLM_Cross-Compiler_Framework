"""Security tests: archive extraction, input validation, SSRF guard, secret redaction."""

import io
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


def response(status=200, html="", location=None, content_type="text/html"):
    headers = {"Content-Type": content_type}
    if location:
        headers["Location"] = location
    return SimpleNamespace(status_code=status, headers=headers, text=html, content=html.encode(),
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
        assert web.urls == ["http://93.184.215.14/docs"]
        assert web.requests[0][1]["allow_redirects"] is False

    def test_backslash_redirect_is_not_followed(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/docs": response(302, location="http://127.0.0.1:8080\\@example.com/secret"),
        })
        crawler.crawl("http://93.184.215.14/docs")
        assert web.urls == ["http://93.184.215.14/docs"]

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
        assert len(web.requests) == CrawlerManager.MAX_REDIRECTS + 1

    def test_total_requests_are_bounded(self, make_crawler):
        links = "".join(f'<a href="/missing/{i}">x</a>' for i in range(50))
        crawler, web = make_crawler({"http://93.184.215.14/": response(html=EN * 6 + links)}, max_pages=2)
        crawler.crawl("http://93.184.215.14/")
        assert len(web.requests) == crawler.max_requests == 10

    def test_robots_txt_redirect_to_internal_address_is_not_followed(self, make_crawler):
        crawler, web = make_crawler({
            "http://93.184.215.14/robots.txt": response(302, location="http://127.0.0.1:8080/admin/delete?all=1"),
            "http://93.184.215.14/docs": response(html="Docs." + EN),
        }, respect_robots=True)

        docs = crawler.crawl("http://93.184.215.14/docs")

        assert web.urls == ["http://93.184.215.14/robots.txt", "http://93.184.215.14/docs"]
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

