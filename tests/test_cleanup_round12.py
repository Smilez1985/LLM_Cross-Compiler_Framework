"""Round 12 cleanup (recon first, one implementation): what the removals and rewirings of the unwired audit keep
working. Each block names the audit item; the characterization tests were written before the change and pass on
both sides of it."""

import sys
import types

import pytest


# --- item 10: Mojo's Python check runs on the original version check of utils/validation.py -------------------------

def _python_check(monkeypatch, info):
    from orchestrator.Core.mojo.checks import python as python_check
    monkeypatch.setattr(python_check, "sys", types.SimpleNamespace(version_info=info, executable=sys.executable))
    return python_check.check_python(None)


@pytest.mark.parametrize("info", [(3, 11, 0, "final", 0), (3, 13, 1, "final", 0), (3, 14, 12, "final", 0)])
def test_mojo_python_check_accepts_3_11_to_3_14(monkeypatch, info):
    (finding,) = _python_check(monkeypatch, info)
    assert (finding.subsystem, finding.code, finding.severity.value) == ("python", "python.version", "ok")
    assert finding.message == f"Python {info[0]}.{info[1]}"


@pytest.mark.parametrize("info", [(3, 10, 14, "final", 0), (3, 15, 0, "alpha", 1), (4, 0, 0, "final", 0)])
def test_mojo_python_check_refuses_other_versions_with_the_same_texts(monkeypatch, info):
    (finding,) = _python_check(monkeypatch, info)
    assert (finding.code, finding.severity.value, finding.cannot) == ("python.version", "error", "admin")
    assert finding.message == f"Python {info[0]}.{info[1]} is not supported."
    assert finding.fix_hint == "Use Python 3.11 to 3.14 (e.g. 'uv python install 3.13')."
    assert finding.where == sys.executable
    assert finding.why == "The framework is made and tested for Python 3.11 to 3.14; other versions can fail anywhere."


def test_the_original_python_check_judges_the_supported_range():
    from orchestrator.utils.validation import ValidationSeverity, validate_python_version
    ok = validate_python_version("3.11", below="3.15", current="3.13.2")
    assert ok and ok.severity == ValidationSeverity.SUCCESS and ok.message == "Python version OK: 3.13.2 >= 3.11 and < 3.15"
    old = validate_python_version("3.11", below="3.15", current="3.10.14")
    assert not old and old.message == "Python version too old: 3.10.14 < 3.11"
    for newer in ("3.15.0", "3.15.0a1", "4.0"):
        result = validate_python_version("3.11", below="3.15", current=newer)
        assert not result and result.message == f"Python version too new: {newer} >= 3.15"
    # Without an upper bound it is the original minimum check, on this interpreter by default.
    here = validate_python_version("3.8.0")
    assert here and here.message.startswith("Python version OK: ") and here.message.endswith(" >= 3.8.0")
    assert "below" not in here.details


def test_validation_keeps_the_ssrf_guard_and_drops_only_duplicates():
    """The functions callers and tests use stay at their path (tests monkeypatch validate_public_url there); the ones
    newer code does better are gone (round 12 item 10, see the changelog for each replacement)."""
    from orchestrator.utils import validation
    for kept in ("validate_public_url", "is_public_ip", "resolve_public_addresses", "_idna_host", "validate_ip_address",
                 "ValidationResult", "ValidationSeverity", "ValidationType", "validate_python_version",
                 "validate_path", "validate_email", "ValidationError"):
        assert hasattr(validation, kept), kept
    for gone in ("validate_system_requirements", "validate_docker_environment", "create_comprehensive_validation_report",
                 "validate_config", "validate_directory_writable", "validate_url", "ValidationReport",
                 "SystemRequirements", "get_validation_info", "_validate_disk_space", "_validate_docker_daemon",
                 "_validate_command_availability", "_validate_docker_image_name", "_validate_target_name"):
        assert not hasattr(validation, gone), gone


# --- item 10: one Docker check for the build engine and Mojo (the build engine's original of 2025) -------------------

class _Client:
    def __init__(self, error=None):
        self.error = error

    def ping(self):
        if self.error:
            raise self.error
        return True


class _Framework:
    def __init__(self, client):
        self.client = client

    def get_component(self, name):
        return self.client if name == "docker_client" else None


def _docker_host(monkeypatch, docker_on_path=True, buildx=0):
    import shutil
    import subprocess
    runs = []

    def run(args, *a, **k):
        runs.append(list(args))
        if isinstance(buildx, BaseException):
            raise buildx
        if k.get("check") and buildx:
            raise subprocess.CalledProcessError(buildx, args)
        return subprocess.CompletedProcess(args, buildx, "", "")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker" if docker_on_path else None)
    monkeypatch.setattr(subprocess, "run", run)
    return runs


def _mojo_docker(monkeypatch, client):
    from orchestrator.Core.mojo.checks import docker as docker_check
    monkeypatch.setattr(docker_check, "_storage_findings", lambda framework: [])
    return [(f.code, f.severity.value) for f in docker_check.check_docker(_Framework(client))]


def test_mojo_docker_check_reports_daemon_and_buildx(monkeypatch):
    import subprocess
    runs = _docker_host(monkeypatch)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok"), ("docker.buildx", "ok")]
    assert runs == [["docker", "buildx", "version"]]
    _docker_host(monkeypatch, buildx=1)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok"), ("docker.buildx", "warn")]
    _docker_host(monkeypatch, buildx=subprocess.TimeoutExpired(["docker"], 15))
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok")]
    runs = _docker_host(monkeypatch, docker_on_path=False)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok")] and runs == []


def test_mojo_docker_check_stops_at_an_unreachable_daemon(monkeypatch):
    runs = _docker_host(monkeypatch)
    for client in (None, _Client(ConnectionError("refused"))):
        assert _mojo_docker(monkeypatch, client) == [("docker.daemon", "error")]
    assert runs == []


def _engine_warnings(client):
    from orchestrator.Core.builder import BuildEngine
    warnings = []
    engine = types.SimpleNamespace(docker_client=client,
                                   logger=types.SimpleNamespace(warning=warnings.append))
    BuildEngine._validate_docker_environment(engine)
    return warnings


def test_build_engine_start_warns_about_docker_as_before(monkeypatch):
    _docker_host(monkeypatch)
    assert _engine_warnings(_Client()) == []
    _docker_host(monkeypatch, buildx=1)
    assert _engine_warnings(_Client()) == ["Docker BuildX not available, standard build will be used."]
    _docker_host(monkeypatch, buildx=OSError("no such file"))
    assert _engine_warnings(_Client()) == ["Docker BuildX not available, standard build will be used."]
    _docker_host(monkeypatch, docker_on_path=False)
    assert _engine_warnings(_Client()) == ["Docker executable not found in PATH."]
    _docker_host(monkeypatch)
    assert _engine_warnings(_Client(ConnectionError("refused"))) == ["Docker environment check failed: refused"]


# --- item 11: what covers each job of the former utils/network.NetworkGuard ----------------------------------------
# NetworkGuard (2025) pinged 8.8.8.8:53, waited without limit until that answered, and downloaded with an optional
# hash. PinnedDownloader (utils/download.py) does each job through the session the download itself uses.

class _Response:
    def __init__(self, status, body=b"", headers=None, drop_after=None, url="https://files.example/model.bin"):
        self.status_code, self.body, self.headers, self.url = status, body, headers or {}, url
        self.drop_after = drop_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        import requests
        sent = 0
        for i in range(0, len(self.body), 10):
            if self.drop_after is not None and sent >= self.drop_after:
                raise requests.exceptions.ChunkedEncodingError("connection dropped")
            yield self.body[i:i + 10]
            sent += 10


class _Session:
    def __init__(self, script):
        self.script, self.ranges = list(script), []

    def get(self, url, headers=None, **kwargs):
        self.ranges.append((headers or {}).get("Range"))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def test_the_pinned_downloader_rides_out_a_lost_connection_and_resumes(tmp_path):
    import hashlib
    import requests
    from orchestrator.utils.download import PinnedDownloader
    data = bytes(range(100))
    session = _Session([_Response(200, data, {"Content-Length": "100"}, drop_after=40),
                        requests.ConnectionError("network unreachable"),
                        _Response(206, data[40:], {"Content-Range": "bytes 40-99/100"})])
    sleeps = []
    dest = PinnedDownloader(session, sleep=sleeps.append).fetch("https://files.example/model.bin",
                                                                tmp_path / "model.bin", 100,
                                                                hashlib.sha256(data).hexdigest())
    assert dest.read_bytes() == data and session.ranges == [None, "bytes=40-", "bytes=40-"]
    assert sleeps == [2, 4], "a bounded wait between attempts, never an endless loop"


def test_the_pinned_downloader_gives_up_with_a_plain_reason_and_keeps_the_part(tmp_path):
    import hashlib
    import requests
    from orchestrator.utils.download import DownloadError, PinnedDownloader
    data = bytes(range(100))
    session = _Session([_Response(200, data, {"Content-Length": "100"}, drop_after=40)]
                       + [requests.ConnectionError("offline")] * 4)
    with pytest.raises(DownloadError, match="did not complete after 5 attempts; run the command again to resume"):
        PinnedDownloader(session, sleep=lambda s: None).fetch("https://files.example/model.bin",
                                                              tmp_path / "model.bin", 100,
                                                              hashlib.sha256(data).hexdigest())
    assert (tmp_path / "model.bin.part").stat().st_size == 40 and not (tmp_path / "model.bin").exists()


def test_the_pinned_downloader_checks_what_network_guard_left_optional(tmp_path):
    from orchestrator.utils.download import IntegrityError, PinnedDownloader
    data = bytes(range(100))
    with pytest.raises(IntegrityError, match="No sha256 and size pinned"):
        PinnedDownloader(_Session([])).fetch("https://files.example/model.bin", tmp_path / "a.bin", None, None)
    with pytest.raises(IntegrityError, match="does not match its pin"):
        PinnedDownloader(_Session([_Response(200, data, {"Content-Length": "100"})]), sleep=lambda s: None).fetch(
            "https://files.example/model.bin", tmp_path / "b.bin", 100, "0" * 64)
    assert not (tmp_path / "b.bin").exists() and not (tmp_path / "b.bin.part").exists()
    redirected = _Response(200, data, {"Content-Length": "100"}, url="http://files.example/model.bin")
    with pytest.raises(Exception, match="redirected away from HTTPS"):
        PinnedDownloader(_Session([redirected]), sleep=lambda s: None).fetch(
            "https://files.example/model.bin", tmp_path / "c.bin", 100, "0" * 64)


def test_network_guard_is_gone():
    import importlib.util
    assert importlib.util.find_spec("orchestrator.utils.network") is None
