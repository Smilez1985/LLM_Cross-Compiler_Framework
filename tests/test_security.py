#!/usr/bin/env python3
"""Security tests: archive extraction, input validation, SSRF guard, secret redaction."""

import io
import sys
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
        "http://[::1]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://93.184.215.14/",
        "javascript:alert(1)",
        "not-a-url",
    ])
    def test_blocks_internal_and_non_http_targets(self, url):
        assert not validate_public_url(url)

    def test_allows_public_address(self):
        assert validate_public_url("https://93.184.215.14/docs")


class TestCrawlerSecurity:

    @pytest.fixture
    def crawler(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.crawler_manager import CrawlerManager

        cfg = ConfigManager(tmp_path)
        cfg.set("crawler_respect_robots", False)
        cfg.set("crawler_rate_limit", 0.0)
        return CrawlerManager(SimpleNamespace(config=cfg))

    def test_internal_url_is_never_fetched(self, crawler, monkeypatch):
        monkeypatch.setattr(crawler.session, "get", lambda *a, **k: pytest.fail("internal URL was fetched"))
        assert crawler.crawl("http://127.0.0.1:6333/") == []

    def test_redirect_to_internal_address_is_not_followed(self, crawler, monkeypatch):
        requested = []

        def fake_get(url, **kwargs):
            requested.append(url)
            assert kwargs.get("allow_redirects") is False
            return SimpleNamespace(is_redirect=True, status_code=302,
                                   headers={"Location": "http://169.254.169.254/latest/meta-data/"})

        monkeypatch.setattr(crawler.session, "get", fake_get)

        assert crawler.crawl("http://93.184.215.14/docs") == []
        assert requested == ["http://93.184.215.14/docs"]

    def test_pdfs_are_skipped_when_disabled(self, crawler, monkeypatch):
        crawler.download_pdfs = False
        monkeypatch.setattr(crawler.session, "get", lambda *a, **k: pytest.fail("PDF was downloaded"))
        assert crawler.crawl("http://93.184.215.14/manual.pdf") == []


class TestSecretRedaction:

    @pytest.mark.parametrize("secret, marker", [
        ("sk-proj-AbCdEf0123456789_xyz-ABCDEFGHIJ", "sk-REDACTED"),
        ("sk-ant-api03-AbCdEf0123456789abcdefghij", "sk-REDACTED"),
        ("sk-abcdefghijklmnopqrstuvwx", "sk-REDACTED"),
        ("hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456", "hf_REDACTED"),
        ("AIzaSyA1234567890abcdefghijklmnopqrstuv", "AIza-REDACTED"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "gh-REDACTED"),
    ])
    def test_known_key_formats_are_redacted(self, secret, marker):
        text = f"Traceback ... api_key={secret} failed"
        redacted = redact_secrets(text)
        assert secret not in redacted
        assert marker in redacted

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


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
