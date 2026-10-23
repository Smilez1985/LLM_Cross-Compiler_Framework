"""Hugging Face and local files for AI callers (MCP design T6): search with the gated filter, model details with
licence, gating and the stored token's access, download_hf_file through one cancellable, resumable fetcher checked
against the Hub's sha256, and list_local_files. The Hub is a stand-in; no test reaches the network."""

import hashlib
import io
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from orchestrator.Core import hf_hub
from orchestrator.Core.hf_hub import HubFetcher, HubFile, classify_license
from orchestrator.Core.job_service import JobService
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.framework_tools import register_framework_tools
from orchestrator.tools.grants import DOWNLOAD_RULE
from orchestrator.tools.local_files import LocalFiles
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry

TOKEN = "hf_" + "Zq9" * 12   # built at runtime: never a credential-shaped literal in the repository
COMMIT = "a" * 40
REPO = "acme/tiny-GGUF"
DATA = b"GGUF" + bytes(range(256)) * 40
SHA = hashlib.sha256(DATA).hexdigest()


# --- a stand-in for the Hub ------------------------------------------------------------------------------------------

class Sibling(SimpleNamespace):
    pass


def info(repo=REPO, gated=False, license="apache-2.0", base=None, files=None, extra_card=None):
    card = {"license": license, **({"base_model": base} if base else {}), **(extra_card or {})}
    siblings = files if files is not None else [
        Sibling(rfilename="tiny-q4_k_m.gguf", size=len(DATA), lfs=SimpleNamespace(sha256=SHA)),
        Sibling(rfilename="config.json", size=12, lfs=None)]
    return SimpleNamespace(id=repo, sha=COMMIT, gated=gated, card_data=card, tags=[], siblings=siblings,
                           safetensors=SimpleNamespace(total=494_000_000))


class FakeApi:
    def __init__(self, hub, token):
        self.hub, self.token = hub, token
        hub.tokens.append(token)

    def list_models(self, **kwargs):
        self.hub.calls.append(("list_models", kwargs))
        if self.hub.fail:
            raise requests.ConnectionError("no network")
        return [SimpleNamespace(id="acme/free", gated=False, downloads=10, likes=1, tags=["license:mit"]),
                SimpleNamespace(id="acme/locked", gated="manual", downloads=5, likes=0, tags=[])]

    def model_info(self, repo, **kwargs):
        self.hub.calls.append(("model_info", repo, kwargs))
        if self.hub.fail:
            raise requests.ConnectionError("no network")
        return self.hub.models[repo]

    def auth_check(self, repo, **kwargs):
        self.hub.calls.append(("auth_check", repo, kwargs))
        from huggingface_hub.errors import GatedRepoError
        if not self.hub.access:
            raise GatedRepoError("no access", response=SimpleNamespace(headers={}, status_code=403, request=None))


class Hub:
    def __init__(self):
        self.calls, self.tokens, self.fail, self.access = [], [], False, True
        self.models = {REPO: info()}

    def factory(self, token):
        return FakeApi(self, token)


class Response:
    def __init__(self, data, start=0, status=None, url=None, total=None):
        self.data, self.status_code = data, status or (206 if start else 200)
        self.url = url or "https://cas-bridge.xethub.hf.co/blob"
        self.headers = {"Content-Length": str(len(data))}
        if start:
            self.headers["Content-Range"] = f"bytes {start}-{start + len(data) - 1}/{total}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.data), 1024):
            yield self.data[i:i + 1024]


def urllib3_ssl_error(cause):
    """A TLS error inside a response as urllib3 wraps it (requests turns it into requests.exceptions.SSLError)."""
    import urllib3
    return urllib3.exceptions.SSLError(cause)


def as_requests_raises(cause, url="https://huggingface.co/x"):
    """A TLS error of a request exactly as requests raises it: SSLError(MaxRetryError(reason=urllib3 SSLError(...)))."""
    import urllib3
    return requests.exceptions.SSLError(urllib3.exceptions.MaxRetryError(None, url, urllib3_ssl_error(cause)),
                                        request=requests.Request("GET", url).prepare())


class FakeSession:
    """Serves DATA (or `body`), honouring Range; records every request's url and headers."""

    def __init__(self, body=DATA, gate=None):
        self.body, self.gate, self.seen = body, gate, []

    def get(self, url, headers=None, **kwargs):
        self.seen.append((url, dict(headers or {})))
        start = int(headers["Range"].split("=")[1].rstrip("-")) if headers and "Range" in headers else 0
        if self.gate is not None:
            return self.gate(self.body, start)
        return Response(self.body[start:], start, total=len(self.body))


# --- the environment -------------------------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_" + "Implicit" * 5)   # never to be used
    # The disk rule reads the file system; the tests never depend on how full this machine's disk is.
    monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: SimpleNamespace(total=10 ** 12, used=0, free=10 ** 12))
    hub = Hub()
    settings = {"models_dir": str(tmp_path / "models"), "offline_mode": False}
    (tmp_path / "models").mkdir()
    config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                             user_state_dir=str(tmp_path / "state"))
    vault = {"hf_token": None}
    framework = SimpleNamespace(config=config, resolve_path=lambda p: Path(p), hf_api_factory=hub.factory,
                                secrets_manager=SimpleNamespace(get_secret=lambda key: vault.get(key)),
                                build_service=None, job_service=JobService())
    registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "a.db")),
                                        framework)
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"download_hf_file": Decision.ALLOW},
                                             reviewed=[s.name for s in registry.list()]))
    sessions = []

    def session_factory(body=DATA, gate=None):
        session = FakeSession(body, gate)
        sessions.append(session)
        monkeypatch.setattr(hf_hub.HubFetcher, "session", property(lambda self: session))
        return session

    session_factory()
    yield SimpleNamespace(registry=registry, hub=hub, settings=settings, vault=vault, framework=framework,
                          tmp=tmp_path, session=session_factory, sessions=sessions, jobs=framework.job_service)


def call(env, name, arguments, actor="mcp", confirm=None):
    return env.registry.call(name, arguments, ToolContext(actor, confirm=confirm))


def wait_job(env, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = env.jobs.get(job_id)
        if job.finished:
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not end")


def dest(env, name="tiny-q4_k_m.gguf"):
    return env.tmp / "models" / "hf" / "acme--tiny-GGUF" / COMMIT[:12] / name


# --- search ------------------------------------------------------------------------------------------------------------

class TestSearch:

    def test_free_is_the_default_and_passes_gated_false_with_a_note(self, env):
        out = call(env, "search_hf_models", {"query": "tiny model"})
        kwargs = env.hub.calls[-1][1]
        assert kwargs["gated"] is False and kwargs["token"] is False
        assert [m.id for m in out.models] == ["acme/free"], "a gated model the Hub let through is still left out"
        assert out.notes[0] == "Gated models were left out; search again with gated=any to include them."

    def test_any_and_gated_keep_the_gating_mode(self, env):
        out = call(env, "search_hf_models", {"query": "tiny model", "gated": "any"})
        assert env.hub.calls[-1][1]["gated"] is None and [m.gated for m in out.models] == ["false", "manual"]
        call(env, "search_hf_models", {"query": "tiny model", "gated": "gated"})
        assert env.hub.calls[-1][1]["gated"] is True

    def test_offline_mode_refuses_without_a_request(self, env):
        env.settings["offline_mode"] = True
        for name, args in (("search_hf_models", {"query": "tiny"}), ("get_hf_model_info", {"model": REPO}),
                           ("download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})):
            with pytest.raises(ToolError) as info:
                call(env, name, args)
            assert info.value.code == "not_supported" and "offline" in str(info.value), name
        assert env.hub.calls == []

    def test_a_network_error_is_an_error_not_an_empty_list(self, env):
        env.hub.fail = True
        with pytest.raises(ToolError) as info:
            call(env, "search_hf_models", {"query": "tiny"})
        assert info.value.code == "failed" and "could not be reached" in info.value.message

    def test_the_search_is_rate_limited(self, env):
        for _ in range(30):
            call(env, "search_hf_models", {"query": "tiny"})
        with pytest.raises(ToolError) as info:
            call(env, "search_hf_models", {"query": "tiny"})
        assert info.value.code == "limit_reached"

    def test_model_manager_raises_instead_of_an_empty_list(self, monkeypatch, tmp_path):
        import huggingface_hub
        from orchestrator.Core.model_manager import ModelManager

        def broken(self, **kwargs):
            raise requests.ConnectionError("no network")
        monkeypatch.setattr(huggingface_hub.HfApi, "list_models", broken)
        with pytest.raises(requests.ConnectionError):
            ModelManager({"models_dir": str(tmp_path)}).search_huggingface_models("x")


# --- the token ----------------------------------------------------------------------------------------------------------

class TestToken:

    def test_without_a_stored_token_no_call_sends_one(self, env):
        env.hub.models[REPO] = info(gated="auto")
        out = call(env, "get_hf_model_info", {"model": REPO})
        assert out.token_access == "no_token" and set(env.hub.tokens) == {False}
        assert all(c[-1].get("token") is False for c in env.hub.calls if isinstance(c[-1], dict))

    def test_the_stored_token_is_used_inside_and_never_leaves(self, env):
        env.vault["hf_token"] = TOKEN
        env.hub.models[REPO] = info(gated="manual")
        out = call(env, "get_hf_model_info", {"model": REPO})
        assert out.token_access == "yes" and env.hub.tokens[-1] == TOKEN
        assert TOKEN not in out.model_dump_json()
        env.hub.access = False
        with pytest.raises(ToolError) as info_error:
            call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        assert TOKEN not in str(info_error.value)
        with sqlite3.connect(env.tmp / "a.db") as db:
            assert TOKEN not in json.dumps(db.execute("SELECT * FROM events").fetchall())

    def test_no_input_takes_a_token_or_a_path(self, env):
        for name in ("search_hf_models", "get_hf_model_info", "download_hf_file", "list_local_files"):
            fields = env.registry.get(name).input_model.model_fields
            assert not [f for f in fields if any(w in f for w in ("token", "secret", "path", "dir", "folder"))], name

    def test_the_token_goes_only_to_huggingface_co_and_never_through_a_redirect(self, tmp_path):
        seen = []

        class Adapter(requests.adapters.HTTPAdapter):
            def send(self, request, **kwargs):
                seen.append((request.url, request.headers.get("Authorization")))
                response = requests.Response()
                response.request, response.url = request, request.url
                if "huggingface.co" in request.url:
                    response.status_code, response.headers["Location"] = 302, "https://cdn-lfs.hf.co/blob"
                    response.raw = io.BytesIO(b"")
                else:
                    response.status_code, response.raw = 200, io.BytesIO(DATA)
                    response.headers["Content-Length"] = str(len(DATA))
                return response

        session = requests.Session()
        session.mount("https://", Adapter())
        fetcher = HubFetcher(token=TOKEN, session=session, sleep=lambda s: None)
        path, digest = fetcher.fetch_file(REPO, COMMIT, HubFile("m.gguf", len(DATA), SHA), tmp_path / "m.gguf")
        assert digest == SHA and path.read_bytes() == DATA
        assert seen[0] == (f"https://huggingface.co/{REPO}/resolve/{COMMIT}/m.gguf", f"Bearer {TOKEN}")
        assert seen[1] == ("https://cdn-lfs.hf.co/blob", None), "the file storage never gets the token"

    def test_a_redirect_to_another_host_is_refused(self, tmp_path):
        class Session(FakeSession):
            def get(self, url, headers=None, **kwargs):
                return Response(DATA, url="https://evil.example/blob")
        fetcher = HubFetcher(token=TOKEN, session=Session(), sleep=lambda s: None)
        with pytest.raises(Exception, match="left Hugging Face"):
            fetcher.fetch_file(REPO, COMMIT, HubFile("m.gguf", len(DATA), SHA), tmp_path / "m.gguf")


class TestTheFetcherSaysWhyAtOnce:
    """Seam of stage C wave 3 (chains port-runtime and hf-online): HubFetcher ran its own copy of the pinned
    downloader's range download, without port-runtime's fixes ("A pinned download that cannot work says why at once"):
    a TLS failure was retried five times like a dropped connection, and any other request error and a .part file that
    cannot be written escaped as a raw exception. The fetcher now runs PinnedDownloader._download_range and adds only
    what is the Hub's own (the token for huggingface.co, the refusal of another host, 401/403)."""

    def fetch(self, tmp_path, session, data=DATA):
        fetcher = HubFetcher(token=TOKEN, session=session, sleep=lambda s: None)
        file = HubFile("m.gguf", len(data), hashlib.sha256(data).hexdigest())
        return fetcher.fetch_file(REPO, COMMIT, file, tmp_path / "m.gguf")

    def test_there_is_one_range_download(self):
        assert "_download_range" not in vars(HubFetcher), "HubFetcher keeps no copy of the pinned downloader's"

    def test_a_tls_failure_is_not_retried_and_names_only_the_host_that_failed(self, tmp_path):
        calls = []
        cdn = "https://cas-bridge.xethub.hf.co/blob?X-Amz-Signature=secret"

        class Session:
            def get(self, url, **kwargs):
                calls.append(url)
                raise requests.exceptions.SSLError("certificate verify failed",
                                                   request=requests.Request("GET", cdn).prepare())
        with pytest.raises(hf_hub.DownloadError, match="TLS certificate of cas-bridge.xethub.hf.co could not") as err:
            self.fetch(tmp_path, Session())
        assert len(calls) == 1, "a certificate problem does not go away on its own: no retry"
        assert "Signature" not in str(err.value) and TOKEN not in str(err.value)
        assert "REQUESTS_CA_BUNDLE" in str(err.value)

    def test_a_tls_connection_that_ends_early_is_a_dropped_connection(self, tmp_path):
        """Review of stage C wave 3: the one range download took every TLS error for a certificate that cannot be
        verified. Before 9e71d3c the Hub's fetcher retried them all; since, a connection that ended during its TLS
        handshake (a flapping link, a proxy) ended a multi-gigabyte download at once with the certificate text and its
        REQUESTS_CA_BUNDLE advice. It is a dropped connection: retried, and a broken record mid-way resumes the part."""
        import ssl
        handshake = ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
        record = ssl.SSLError(1, "[SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC] decryption failed or bad record mac")
        session = FakeSession()
        dropped = [as_requests_raises(handshake)]

        class Broken(Response):
            def iter_content(self, chunk_size=1):
                yield self.data[:2048]
                raise requests.exceptions.SSLError(urllib3_ssl_error(record))

        def gate(body, start):
            if dropped:
                raise dropped.pop()
            return (Broken if not start else Response)(body[start:], start, total=len(body))
        session.gate = gate
        path, digest = self.fetch(tmp_path, session)
        assert digest == SHA and path.read_bytes() == DATA
        assert [headers.get("Range") for _url, headers in session.seen] == [None, None, "bytes=2048-"]

    def test_a_certificate_that_cannot_be_verified_is_still_not_retried(self, tmp_path):
        import ssl
        calls = []

        class Session:
            def get(self, url, **kwargs):
                calls.append(url)
                raise as_requests_raises(ssl.SSLCertVerificationError(
                    1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer "
                       "certificate"))
        with pytest.raises(hf_hub.DownloadError, match="TLS certificate of huggingface.co could not"):
            self.fetch(tmp_path, Session())
        assert len(calls) == 1

    @pytest.mark.parametrize("error", [requests.exceptions.TooManyRedirects("30 redirects"),
                                       requests.exceptions.InvalidURL("bad")])
    def test_other_request_errors_end_with_a_clear_message(self, tmp_path, error):
        class Session:
            def get(self, url, **kwargs):
                raise error
        with pytest.raises(hf_hub.DownloadError, match=f"failed \\({type(error).__name__}\\)"):
            self.fetch(tmp_path, Session())

    def test_a_part_file_that_cannot_be_written(self, tmp_path):
        data = os.urandom(100000)
        (tmp_path / "m.gguf.part").mkdir()   # stands in for a full or read-only disk
        with pytest.raises(hf_hub.DownloadError, match="Could not write .*resumes on the next run"):
            self.fetch(tmp_path, FakeSession(body=data), data)

    def test_the_hub_s_own_checks_stay(self, tmp_path):
        """What only the Hub's fetcher does is kept: the token only to huggingface.co, a stop ends the transfer
        before the next chunk is written, and the part is kept for a resume."""
        session = FakeSession()
        stop = threading.Event()

        class Stopping(Response):
            def iter_content(self, chunk_size=1):
                yield self.data[:1024]
                stop.set()
                yield self.data[1024:]
        session.gate = lambda body, start: Stopping(body[start:], start, total=len(body))
        fetcher = HubFetcher(token=TOKEN, session=session, stop=stop, sleep=lambda s: None)
        with pytest.raises(hf_hub.DownloadStopped):
            fetcher.fetch_file(REPO, COMMIT, HubFile("m.gguf", len(DATA), SHA), tmp_path / "m.gguf")
        assert (tmp_path / "m.gguf.part").stat().st_size == 1024 and not (tmp_path / "m.gguf").exists()
        assert session.seen[0][1]["Authorization"] == f"Bearer {TOKEN}"
        stop.clear()
        session.gate = None
        path, digest = fetcher.fetch_file(REPO, COMMIT, HubFile("m.gguf", len(DATA), SHA), tmp_path / "m.gguf")
        assert digest == SHA and path.read_bytes() == DATA
        assert session.seen[-1][1]["Range"] == "bytes=1024-", "the second run resumes the kept part"


# --- licence and gating --------------------------------------------------------------------------------------------------

class TestLicence:

    @pytest.mark.parametrize("value, expected", [
        ("apache-2.0", ("apache-2.0", "false")), ("MIT", ("MIT", "false")), ("llama3.1", ("llama3.1", "true")),
        ("gemma", ("gemma", "true")), ("cc-by-nc-4.0", ("cc-by-nc-4.0", "true")), ("other", ("other", "unknown")),
        (["mit", "apache-2.0"], ("mit, apache-2.0", "unknown")), (None, ("unknown", "unknown"))])
    def test_only_the_allowlist_is_permissive(self, value, expected):
        assert classify_license(value) == expected

    def test_other_shows_its_name_and_link(self, env):
        env.hub.models[REPO] = info(license="other", extra_card={"license_name": "acme-license",
                                                                 "license_link": "https://acme.example/LICENSE"})
        out = call(env, "get_hf_model_info", {"model": REPO})
        assert (out.license, out.license_restrictive, out.license_name, out.license_link) == (
            "other", "unknown", "acme-license", "https://acme.example/LICENSE")
        assert out.parameters == 494_000_000 and out.commit == COMMIT and out.total_bytes == len(DATA) + 12

    def test_a_gguf_reupload_of_a_gated_model_is_shown_and_outside_every_standing_approval(self, env):
        env.hub.models[REPO] = info(base="meta/gated-base")
        env.hub.models["meta/gated-base"] = info("meta/gated-base", gated="manual", license="llama3.2")
        out = call(env, "get_hf_model_info", {"model": REPO})
        assert (out.base_model, out.base_gated, out.base_license_restrictive) == ("meta/gated-base", "manual", "true")
        assert any("re-upload" in n for n in out.notes)
        spec = env.registry.get("download_hf_file")
        ctx = ToolContext("mcp")
        ctx.facts = env.registry.look_up(spec, spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf"), ctx)
        facts = spec.grant.facts(None, ctx)
        assert facts["base_gated"] is True and DOWNLOAD_RULE.scope(facts, "this") is None
        env.hub.models[REPO] = info()
        ctx.facts = env.registry.look_up(spec, spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf"), ctx)
        assert DOWNLOAD_RULE.scope(spec.grant.facts(None, ctx), "this") == {"repo": REPO, "max_gb": 5}


# --- download_hf_file ----------------------------------------------------------------------------------------------------

class TestDownload:

    def test_a_download_lands_pinned_and_is_listed_and_usable_by_its_id(self, env):
        started = call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        assert started.commit == COMMIT and started.size_bytes == len(DATA)
        job = wait_job(env, started.job_id)
        assert job.state == "done", job.error
        assert job.result["sha256"] == SHA and job.result["commit"] == COMMIT
        assert job.result["ref"] == f"models:hf/acme--tiny-GGUF/{COMMIT[:12]}/tiny-q4_k_m.gguf"
        assert dest(env).read_bytes() == DATA
        url, headers = env.sessions[-1].seen[0]
        assert url == f"https://huggingface.co/{REPO}/resolve/{COMMIT}/tiny-q4_k_m.gguf"
        assert "Authorization" not in headers, "no stored token: no token at all (HF_TOKEN is set)"
        listed = call(env, "list_local_files", {"kind": "model"})
        assert [(f.file_id, f.format) for f in listed.files] == [(job.result["file_id"], "gguf")]
        pin = LocalFiles.for_framework(env.framework).resolve(job.result["file_id"])
        assert Path(pin.path) == dest(env).resolve()

    def test_offline_mode_switched_on_while_the_person_is_asked_refuses_before_any_request(self, env):
        """Review of stage C wave 3 (hf-online's open item): the look-up refuses in offline mode, but the download
        did not look again after the question. Offline mode switched on while the person's question was open, then
        a Yes, and the file still came from Hugging Face. The download refuses before any request now, with the one
        offline refusal (hf_hub.offline_refusal), and nothing is reserved or started."""
        def yes_after_going_offline(spec, inp):
            env.settings["offline_mode"] = True
            return True
        env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[]))
        session = env.session()
        with pytest.raises(ToolError) as info:
            call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"},
                 confirm=yes_after_going_offline)
        assert info.value.code == "not_supported" and "offline" in str(info.value)
        assert session.seen == [] and env.jobs.list() == [], "no request, no job"
        assert not dest(env).exists() and not dest(env).with_name(dest(env).name + ".part").exists()

    def test_the_question_shows_size_disk_licence_and_gating(self, env):
        asked = []

        def yes(spec, inp):
            from orchestrator.tools.registry import current_question
            asked.append(current_question())
            return True
        env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[]))
        started = call(env, "download_hf_file", {"model": REPO, "filename": "TINY-Q4_K_M.gguf"}, confirm=yes)
        assert asked and asked[0].startswith(f"Downloads tiny-q4_k_m.gguf (0.0 MB) of {REPO} from huggingface.co")
        for part in ("Commit", "Disk", "free on the models folder's disk", "Licence", "permissive", "Gating"):
            assert part in asked[0], part
        assert "filename: tiny-q4_k_m.gguf (you wrote TINY-Q4_K_M.gguf)" in started.notes
        wait_job(env, started.job_id)

    def test_a_gated_repository_without_access_is_refused_before_the_question(self, env):
        env.hub.models[REPO] = info(gated="manual")
        asked = []
        for stored, code in ((None, "needs_person"), (TOKEN, "permission_denied")):
            env.vault["hf_token"] = stored
            env.hub.access = False
            with pytest.raises(ToolError) as info_error:
                call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"},
                     confirm=lambda s, i: asked.append(1) or True)
            assert info_error.value.code == code and "llm-cli secrets set hf_token" in str(info_error.value)
        assert asked == [] and env.sessions[-1].seen == []

    def test_a_file_the_repository_does_not_list_is_refused(self, env):
        with pytest.raises(ToolError) as info_error:
            call(env, "download_hf_file", {"model": REPO, "filename": "../../etc/passwd"})
        assert info_error.value.code == "invalid_value" and "get_hf_model_info" in str(info_error.value)

    def test_a_cancelled_download_lands_no_file_and_keeps_its_part(self, env):
        release = threading.Event()

        class Slow(Response):
            def iter_content(self, chunk_size=1):
                yield self.data[:4096]
                release.wait(5)
                yield self.data[4096:]

        env.session(gate=lambda body, start: Slow(body[start:], start, total=len(body)))
        started = call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        deadline = time.monotonic() + 5
        part = dest(env).with_name("tiny-q4_k_m.gguf.part")
        while not (part.exists() and part.stat().st_size) and time.monotonic() < deadline:
            time.sleep(0.01)
        call(env, "cancel_job", {"job_id": started.job_id})
        release.set()
        thread = next((t for t in threading.enumerate() if t.name == f"job-{started.job_id}"), None)
        if thread is not None:
            thread.join(5)
        assert env.jobs.get(started.job_id).state == "cancelled"
        assert not dest(env).exists() and part.stat().st_size == 4096

    def test_a_resumed_download_completes_and_matches_the_hub(self, env):
        part = dest(env).with_name("tiny-q4_k_m.gguf.part")
        part.parent.mkdir(parents=True)
        part.write_bytes(DATA[:5000])
        started = call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        job = wait_job(env, started.job_id)
        assert job.state == "done" and job.result["sha256"] == SHA and dest(env).read_bytes() == DATA
        assert env.sessions[-1].seen[0][1]["Range"] == "bytes=5000-"

    def test_a_corrupted_byte_fails_the_check_and_the_file_never_gets_its_name(self, env):
        bad = bytearray(DATA)
        bad[100] ^= 0xFF
        env.session(body=bytes(bad))
        started = call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        job = wait_job(env, started.job_id)
        assert job.state == "failed" and "does not match" in job.error["message"]
        assert not dest(env).exists() and not dest(env).with_name("tiny-q4_k_m.gguf.part").exists()

    def test_a_small_file_without_lfs_is_pinned_by_size(self, env):
        env.session(body=b'{"a": 1234}\n')
        started = call(env, "download_hf_file", {"model": REPO, "filename": "config.json"})
        job = wait_job(env, started.job_id)
        assert job.state == "done" and job.result["sha256"] == hashlib.sha256(b'{"a": 1234}\n').hexdigest()

    def test_the_disk_rule_has_no_size_cap(self, env, monkeypatch):
        big = 145 * 10 ** 9
        env.hub.models[REPO] = info(files=[Sibling(rfilename="big.gguf", size=big, lfs=SimpleNamespace(sha256=SHA))])
        usage = SimpleNamespace(total=2 * 10 ** 12, used=0, free=610 * 10 ** 9)
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: usage)
        spec = env.registry.get("download_hf_file")
        facts = env.registry.look_up(spec, spec.input_model(model=REPO, filename="big.gguf"), ToolContext("mcp"))
        assert facts.bound["size"] == big, "a 145 GB file is allowed when the disk holds it"
        usage.free = 100 * 10 ** 9
        with pytest.raises(ToolError) as info_error:
            env.registry.look_up(spec, spec.input_model(model=REPO, filename="big.gguf"), ToolContext("mcp"))
        assert info_error.value.code == "insufficient_resources" and "145.00 GB needed" in info_error.value.message

    def test_a_standing_approval_needs_the_larger_reserve_or_the_person_is_asked(self, env, monkeypatch):
        """Design 6.5: a call under a standing approval must leave 20 GiB or 10 % free, or it asks instead."""
        gib = 1024 ** 3
        usage = SimpleNamespace(total=100 * gib, used=0, free=15 * gib)  # normal reserve 2 GiB: fits; 20 GiB: no
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: usage)
        spec = env.registry.get("download_hf_file")
        ctx = ToolContext("mcp")
        ctx.facts = env.registry.look_up(spec, spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf"), ctx)
        assert spec.grant.facts(None, ctx) is None, "outside every standing approval: the person is asked"
        usage.total, usage.free = 1000 * gib, 150 * gib  # 10 % of 1000 GiB = 100 GiB kept free: fits
        ctx.facts = env.registry.look_up(spec, spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf"), ctx)
        assert DOWNLOAD_RULE.scope(spec.grant.facts(None, ctx), "this") is not None
        usage.free = 90 * gib  # less than the 10 % reserve
        ctx.facts = env.registry.look_up(spec, spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf"), ctx)
        assert spec.grant.facts(None, ctx) is None

    def test_one_download_at_a_time(self, env):
        release = threading.Event()

        class Slow(Response):
            def iter_content(self, chunk_size=1):
                release.wait(5)
                yield self.data

        env.session(gate=lambda body, start: Slow(body[start:], start, total=len(body)))
        first = call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        with pytest.raises(ToolError) as info_error:
            call(env, "download_hf_file", {"model": REPO, "filename": "config.json"})
        assert info_error.value.code == "limit_reached"
        release.set()
        wait_job(env, first.job_id)

    def test_the_handler_refuses_without_the_facts_the_person_was_shown(self, env):
        spec = env.registry.get("download_hf_file")
        with pytest.raises(ToolError, match="not bound"):
            spec.handler(spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf"), ToolContext("mcp"))


def mcp_call(env, name, arguments):
    """One call by an MCP client of the framework's server over this registry."""
    pytest.importorskip("mcp")
    import asyncio

    from mcp import Client

    from orchestrator import mcp_server
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                         tool_registry=env.registry), caller="mcp")

    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(go())


FILENAME_NOTE = "filename: tiny-q4_k_m.gguf (you wrote TINY-Q4_K_M.gguf)"


class TestTheFileNameRepairIsNamed:
    """Review finding (client feedback 79): the one repair the look-up makes, a file name matched in another letter
    case, was a plain note of the look-up. Over MCP the look-up runs before the question with a context of its own,
    so a successful result lost the note; a refusal after it (too little disk space) never named it on any path;
    and the audit log did not record it as a repair."""

    WRITTEN = {"model": REPO, "filename": "TINY-Q4_K_M.gguf"}

    def full_disk(self, monkeypatch):
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: SimpleNamespace(total=10 ** 9, used=0, free=10))

    def test_a_successful_call_names_it_on_both_paths(self, env):
        started = call(env, "download_hf_file", self.WRITTEN)
        assert started.notes.count(FILENAME_NOTE) == 1, started.notes
        wait_job(env, started.job_id)
        result = mcp_call(env, "download_hf_file", self.WRITTEN)
        assert not result.is_error, result.content
        assert result.structured_content["notes"].count(FILENAME_NOTE) == 1, result.structured_content
        wait_job(env, result.structured_content["job_id"])

    def test_a_refusal_after_it_names_it_on_both_paths(self, env, monkeypatch):
        self.full_disk(monkeypatch)
        with pytest.raises(ToolError) as refused:
            call(env, "download_hf_file", self.WRITTEN)
        assert refused.value.code == "insufficient_resources"
        assert refused.value.notes == [FILENAME_NOTE] and str(refused.value).endswith(f"Notes: {FILENAME_NOTE}.")
        result = mcp_call(env, "download_hf_file", self.WRITTEN)
        assert result.is_error and result.structured_content["error"]["code"] == "insufficient_resources"
        assert result.structured_content["error"]["notes"] == [FILENAME_NOTE]
        assert " ".join(c.text for c in result.content).endswith(f"Notes: {FILENAME_NOTE}.")

    def test_a_framework_refusal_of_the_look_up_names_it_too(self, env):
        """The look-up's own ToolError (the Hub lists no size for the file) after the repair, not only a service's."""
        env.hub.models[REPO] = info(files=[Sibling(rfilename="tiny-q4_k_m.gguf", size=None, lfs=None)])
        with pytest.raises(ToolError) as refused:
            call(env, "download_hf_file", self.WRITTEN)
        assert refused.value.code == "not_supported" and refused.value.notes == [FILENAME_NOTE], str(refused.value)

    def test_it_is_audited_as_a_repair_without_the_names(self, env, monkeypatch):
        """Kind and field only (design 6.7: values only for enum fields), on a call that ran and on a refusal."""
        started = call(env, "download_hf_file", self.WRITTEN)
        wait_job(env, started.job_id)
        self.full_disk(monkeypatch)
        with pytest.raises(ToolError):
            call(env, "download_hf_file", self.WRITTEN)
        rows = [e for e in env.registry.audit.entries(50) if e["tool"] == "download_hf_file"]
        by_outcome = {e["outcome"]: e for e in rows}
        for outcome in ("started", "ok", "error"):
            assert by_outcome[outcome].get("repairs") == [{"kind": "filename", "field": "filename"}], (outcome, rows)

    def ask(self, env):
        env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in env.registry.list()]))

    def test_a_question_nobody_answered_names_it_and_audits_it(self, env, monkeypatch):
        """Over MCP the look-up runs before the question: a refusal there (no answer in time) names its repair."""
        pytest.importorskip("mcp")
        import asyncio

        from mcp import Client

        from orchestrator import mcp_server
        self.ask(env)
        monkeypatch.setattr(mcp_server, "ELICITATION_TIMEOUT", 0.05)
        server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                             tool_registry=env.registry), caller="mcp")

        async def slow(ctx, params):
            await asyncio.sleep(2)

        async def go():
            async with Client(server, elicitation_callback=slow, mode="legacy") as client:
                return await client.call_tool("download_hf_file", self.WRITTEN)
        result = asyncio.run(go())
        assert result.is_error and "got no answer within" in " ".join(c.text for c in result.content)
        assert result.structured_content["error"]["notes"] == [FILENAME_NOTE]
        [end] = [e for e in env.registry.audit.entries(50) if e["outcome"] == "confirmation_timeout"]
        assert end["repairs"] == [{"kind": "filename", "field": "filename"}]

    def test_a_question_the_client_abandoned_audits_it(self, env):
        pytest.importorskip("mcp")
        import asyncio

        import anyio
        from mcp import Client

        from orchestrator import mcp_server
        self.ask(env)
        server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                             tool_registry=env.registry), caller="mcp")
        opened = threading.Event()

        async def hang(ctx, params):
            opened.set()
            await anyio.sleep(30)

        async def go():
            async with Client(server, elicitation_callback=hang, mode="legacy") as client:
                with anyio.move_on_after(0.5):
                    await client.call_tool("download_hf_file", self.WRITTEN)
        asyncio.run(go())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not [e for e in env.registry.audit.entries(50)
                                                   if e["outcome"] == "cancelled"]:
            time.sleep(0.02)
        assert opened.is_set()
        [end] = [e for e in env.registry.audit.entries(50) if e["outcome"] == "cancelled"]
        assert end["repairs"] == [{"kind": "filename", "field": "filename"}], end

    def test_an_exact_name_has_no_note(self, env):
        started = call(env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        assert not [n for n in started.notes if n.startswith("filename:")]
        wait_job(env, started.job_id)


# --- list_local_files ----------------------------------------------------------------------------------------------------

class TestLocalFiles:

    def test_models_and_datasets_with_formats_and_pickle_flag(self, env):
        models = env.tmp / "models"
        (models / "qwen").mkdir()
        (models / "qwen" / "config.json").write_text("{}")
        (models / "qwen" / "model.safetensors").write_bytes(b"x" * 10)
        (models / "old.bin").write_bytes(b"p" * 5)
        (models / "a.gguf").write_bytes(b"g" * 7)
        (models / ".hidden.gguf").write_bytes(b"h")
        (models / "b.gguf.part").write_bytes(b"h")
        (models / "embeddings").mkdir()
        (models / "embeddings" / "model.onnx").write_bytes(b"e")
        datasets = env.tmp / "datasets"
        datasets.mkdir()
        (datasets / "calib.txt").write_text("hello")
        out = call(env, "list_local_files", {})
        rows = {f.ref: (f.kind, f.format, f.pickle, f.size_bytes) for f in out.files}
        assert rows == {"models:a.gguf": ("model", "gguf", False, 7), "models:old.bin": ("model", "pytorch", True, 5),
                        "models:qwen": ("model", "huggingface", False, 12),
                        "datasets:calib.txt": ("dataset", "text", False, 5)}
        assert {(f.label, f.kind) for f in out.folders} == {("models", "model"), ("datasets", "dataset")}
        assert [f.ref for f in call(env, "list_local_files", {"kind": "dataset"}).files] == ["datasets:calib.txt"]
        assert [f.ref for f in call(env, "list_local_files", {"query": "QWEN"}).files] == ["models:qwen"]

    def test_links_out_of_the_folder_and_hard_links_are_never_listed(self, env):
        models = env.tmp / "models"
        outside = env.tmp / "outside.gguf"
        outside.write_bytes(b"secret")
        os.symlink(outside, models / "link.gguf")
        (models / "real.gguf").write_bytes(b"r")
        os.link(models / "real.gguf", models / "twin.gguf")
        (models / "ok.gguf").write_bytes(b"o")
        out = call(env, "list_local_files", {})
        assert [f.ref for f in out.files] == ["models:ok.gguf"]

    def test_pages_of_fifty(self, env):
        for i in range(55):
            (env.tmp / "models" / f"m{i:02d}.gguf").write_bytes(b"x")
        first = call(env, "list_local_files", {})
        second = call(env, "list_local_files", {"page": 2})
        assert (len(first.files), first.more, first.total, len(second.files), second.more) == (50, True, 55, 5, False)
