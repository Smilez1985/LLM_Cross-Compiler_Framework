"""Tokens leave this computer only on Hugging Face's own way and only when Hugging Face needs one (DECISIONS round 25 Q2).

- The process runs with huggingface_hub's HF_HUB_DISABLE_IMPLICIT_TOKEN and HF_HUB_DISABLE_TELEMETRY on, set when the
  package `orchestrator` is imported (orchestrator/utils/hf_env.py), so no library (fastembed included) sends a token
  it found by itself, and huggingface_hub sends no agent-harnesses registry request (its switch in 1.33 is
  HF_HUB_DISABLE_TELEMETRY: utils/_headers.py calls detect_agent() only while telemetry is on).
- A public repository's look-ups, listings and files carry no token; the token goes to a gated or private repository
  only (hf_hub.needs_token, or the Hub's 401/403 answer, then one request with it), only to huggingface.co.
- fastembed's models come through the framework's fetcher into fastembed's cache (hf_hub.fetch_snapshot) without a
  token, and offline mode opens no socket.

huggingface_hub runs over its own client with a stand-in transport (httpx.MockTransport); the fetcher's requests go to
a stand-in session. No test reaches the network or the real home folder."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from conftest import REPO_ROOT

IMPLICIT = "hf_" + "Implicit" * 5       # HF_TOKEN in the environment: never to be sent
STORED = "hf_" + "Stored" * 6           # built at runtime: never a credential-shaped literal in the repository
FOREIGN = "https://mirror.example"      # an HF_ENDPOINT that is not huggingface.co
E5 = "intfloat/multilingual-e5-small"
COMMIT = "e" * 40
ONNX = b"onnx-graph" * 300
FILES = {"onnx/model.onnx": ONNX, "config.json": b'{"a": 1}', "tokenizer.json": b'{"t": 2}',
         "tokenizer_config.json": b'{"c": 3}', "special_tokens_map.json": b'{"s": 4}'}
BM25 = {"english.txt": b"the\nand\n", "german.txt": b"der\ndie\n"}


def siblings(files):
    out = []
    for name, data in files.items():
        entry = {"rfilename": name, "size": len(data)}
        if name.endswith((".onnx", ".txt")):
            entry["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "pointerSize": 134}
        out.append(entry)
    return out


class Hub:
    """The Hub's API (for huggingface_hub's client) and its file storage (for the fetcher's requests session):
    `api` and `files` hold (host, path, token or None) of every request."""

    def __init__(self):
        self.api, self.files, self.gated = [], [], False
        self.repos = {E5: FILES, "Qdrant/bm25": BM25}

    def handle(self, request):
        import httpx
        auth = request.headers.get("authorization", "")
        self.api.append((request.url.host, request.url.path, auth[len("Bearer "):] if auth else None))
        found = re.fullmatch(r"/api/models/([^/]+/[^/]+)(?:/revision/[^/]+)?", request.url.path)
        if found and found.group(1) in self.repos:
            return httpx.Response(200, json={"id": found.group(1), "sha": COMMIT, "gated": self.gated, "private": False,
                                             "siblings": siblings(self.repos[found.group(1)])})
        return httpx.Response(404, json={"error": "not found"})

    def get(self, url, headers=None, **kwargs):
        from urllib.parse import unquote, urlparse
        parsed = urlparse(url)
        self.files.append((parsed.hostname, parsed.path, (headers or {}).get("Authorization")))
        repo, _, rest = unquote(parsed.path).lstrip("/").partition("/resolve/")
        name = rest.split("/", 1)[1]
        return Body(self.repos[repo][name])


class Body:
    def __init__(self, data):
        self.data, self.status_code, self.url = data, 200, "https://cas-bridge.xethub.hf.co/blob"
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        yield self.data


@pytest.fixture
def hub(monkeypatch):
    """huggingface_hub's own client over the stand-in; HF_TOKEN is set and HF_ENDPOINT names another host."""
    import httpx
    from huggingface_hub import constants
    from huggingface_hub.utils import _http
    from orchestrator.Core import hf_hub
    monkeypatch.setenv("HF_TOKEN", IMPLICIT)
    monkeypatch.setenv("HF_ENDPOINT", FOREIGN)
    monkeypatch.setattr(constants, "ENDPOINT", FOREIGN)
    stand_in = Hub()
    _http.set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(stand_in.handle),
                                                  event_hooks={"request": [_http.hf_request_event_hook]},
                                                  follow_redirects=True))
    monkeypatch.setattr(hf_hub.HubFetcher, "session", property(lambda self: stand_in))
    try:
        yield stand_in
    finally:
        _http.set_client_factory(_http.default_client_factory)


def no_token_anywhere(hub):
    every = hub.api + hub.files
    assert every, "nothing was asked"
    assert [t for _, _, t in every if t] == [], "a token was sent"
    assert {host for host, path, _ in hub.api} == {"huggingface.co"}, hub.api
    assert all(host == "huggingface.co" for host, _, _ in hub.files), hub.files


# --- the process switches ------------------------------------------------------------------------------------------

def run_python(code, **env):
    variables = {k: v for k, v in os.environ.items() if not k.startswith("HF_HUB_DISABLE")}
    variables.update(env)
    return subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT), env=variables, capture_output=True,
                          text=True, timeout=120)


class TestTheProcessSwitches:

    def test_they_are_on_in_this_process_and_in_huggingface_hub(self):
        import huggingface_hub.constants as constants
        import orchestrator  # noqa: F401
        from orchestrator.utils.hf_env import SWITCHES, sealed
        assert sealed() and all(os.environ[name] == "1" for name in SWITCHES)
        assert constants.HF_HUB_DISABLE_IMPLICIT_TOKEN is True and constants.HF_HUB_DISABLE_TELEMETRY is True

    @pytest.mark.parametrize("first", ["import orchestrator", "import orchestrator.rag.embeddings",
                                       "import huggingface_hub.constants; import orchestrator"])
    def test_every_import_order_sees_them_also_against_the_persons_own_value(self, first):
        """huggingface_hub reads both once, when huggingface_hub.constants is imported: the package `orchestrator`
        sets them before any framework module can import it, and sets the module's values when a library was
        imported first; a person's 0 does not switch them off."""
        out = run_python(first + "; import huggingface_hub.constants as c; "
                         "print(c.HF_HUB_DISABLE_IMPLICIT_TOKEN, c.HF_HUB_DISABLE_TELEMETRY)",
                         HF_HUB_DISABLE_IMPLICIT_TOKEN="0", HF_HUB_DISABLE_TELEMETRY="0")
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == ["True", "True"]

    def test_fastembed_imported_through_the_knowledge_base_sees_them(self):
        pytest.importorskip("fastembed")
        out = run_python("import orchestrator.rag.embeddings, fastembed; import huggingface_hub.constants as c; "
                         "print(c.HF_HUB_DISABLE_IMPLICIT_TOKEN, c.HF_HUB_DISABLE_TELEMETRY)")
        assert out.returncode == 0, out.stderr
        assert out.stdout.split()[-2:] == ["True", "True"]

    def test_no_library_sends_the_implicit_token(self, hub, monkeypatch):
        """fastembed calls huggingface_hub's module-level model_info, which sends the implicit HF_TOKEN unless the
        switch is on (shown first with the switch off)."""
        import huggingface_hub
        from huggingface_hub import constants
        monkeypatch.setattr(constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", False)
        huggingface_hub.model_info(E5)
        assert hub.api[-1][2] == IMPLICIT, "without the switch huggingface_hub sends HF_TOKEN by itself"
        monkeypatch.setattr(constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", True)
        hub.api.clear()
        huggingface_hub.model_info(E5)
        huggingface_hub.list_repo_tree(E5)
        assert hub.api and [t for _, _, t in hub.api] == [None] * len(hub.api)

    def test_no_agent_harnesses_request(self, hub, monkeypatch, tmp_path):
        """huggingface_hub 1.33 fetches `{ENDPOINT}/api/agent-harnesses` once a day for its user agent; with the
        telemetry switch on it never asks (shown first with the switch off)."""
        from huggingface_hub import constants
        from huggingface_hub.utils import _detect_agent
        from orchestrator.Core.hf_hub import hub_api
        monkeypatch.setattr(constants, "AGENT_HARNESSES_PATH", str(tmp_path / "harnesses.json"))
        monkeypatch.setattr(_detect_agent, "_registry", None)
        monkeypatch.setattr(constants, "HF_HUB_DISABLE_TELEMETRY", False)
        hub_api(False).model_info(E5, token=False)
        assert ("mirror.example", "/api/agent-harnesses", None) in hub.api, "the guard sees the request"
        monkeypatch.setattr(_detect_agent, "_registry", None)
        (tmp_path / "harnesses.json").unlink(missing_ok=True)
        monkeypatch.setattr(constants, "HF_HUB_DISABLE_TELEMETRY", True)
        hub.api.clear()
        hub_api(False).model_info(E5, token=False)
        assert [path for _, path, _ in hub.api] == [f"/api/models/{E5}"]


# --- the token only where Hugging Face needs one -------------------------------------------------------------------

class Refusing:
    """A file request answered 401 without a token and served with one."""

    def __init__(self):
        self.seen = []

    def get(self, url, headers=None, **kwargs):
        self.seen.append((url, (headers or {}).get("Authorization")))
        body = Body(ONNX)
        body.url = url
        if not (headers or {}).get("Authorization"):
            body.status_code = 401
        return body


class TestTheFetcher:

    def test_needs_token_reads_gated_and_private(self):
        from orchestrator.Core.hf_hub import needs_token
        assert needs_token(SimpleNamespace(gated=False, private=False)) is False
        assert needs_token(SimpleNamespace(gated="manual")) and needs_token(SimpleNamespace(gated="auto"))
        assert needs_token(SimpleNamespace(gated=False, private=True))

    def test_a_public_file_carries_no_authorization_header(self, hub, tmp_path):
        from orchestrator.Core.hf_hub import HubFetcher, HubFile
        file = HubFile("onnx/model.onnx", len(ONNX), hashlib.sha256(ONNX).hexdigest())
        HubFetcher(token=STORED, token_needed=False, sleep=lambda s: None).fetch_file(E5, COMMIT, file,
                                                                                      tmp_path / "model.onnx")
        assert hub.files == [("huggingface.co", f"/{E5}/resolve/{COMMIT}/onnx/model.onnx", None)]

    def test_a_refused_file_is_asked_once_more_with_the_token_only_at_huggingface(self, monkeypatch, tmp_path):
        from orchestrator.Core import hf_hub
        session = Refusing()
        monkeypatch.setattr(hf_hub.HubFetcher, "session", property(lambda self: session))
        file = hf_hub.HubFile("m.onnx", len(ONNX), hashlib.sha256(ONNX).hexdigest())
        hf_hub.HubFetcher(token=STORED, token_needed=False, sleep=lambda s: None).fetch_file(
            "acme/private", COMMIT, file, tmp_path / "m.onnx")
        assert [auth for _, auth in session.seen] == [None, f"Bearer {STORED}"]
        assert all(url.startswith("https://huggingface.co/") for url, _ in session.seen)

    def test_without_a_token_a_refusal_stays_a_refusal(self, monkeypatch, tmp_path):
        from orchestrator.Core import hf_hub
        session = Refusing()
        monkeypatch.setattr(hf_hub.HubFetcher, "session", property(lambda self: session))
        file = hf_hub.HubFile("m.onnx", len(ONNX), hashlib.sha256(ONNX).hexdigest())
        with pytest.raises(hf_hub.HubError) as refused:
            hf_hub.HubFetcher(token=None, token_needed=False, sleep=lambda s: None).fetch_file(
                "acme/private", COMMIT, file, tmp_path / "m.onnx")
        assert refused.value.code == "permission_denied" and len(session.seen) == 1

    def test_tokenless_first_sends_the_token_only_after_a_refusal(self):
        from orchestrator.Core.hf_hub import tokenless_first
        seen = []

        class Refused(Exception):
            response = SimpleNamespace(status_code=401)

        def call(token):
            seen.append(token)
            if token is False:
                raise Refused()
            return "answer"
        assert tokenless_first(call, STORED) == "answer" and seen == [False, STORED]
        seen.clear()
        with pytest.raises(Refused):
            tokenless_first(call, None)
        assert seen == [False]
        seen.clear()

        def broken(token):
            seen.append(token)
            raise requests.ConnectionError("no network")
        with pytest.raises(requests.ConnectionError):
            tokenless_first(broken, STORED)
        assert seen == [False], "a network error is never retried with the token"


# --- fastembed's models through the framework's fetcher ----------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    """fastembed's model classes record what they are given instead of loading ONNX Runtime."""
    pytest.importorskip("fastembed")
    import fastembed
    from fastembed import SparseTextEmbedding, TextEmbedding
    seen = []

    class Dense(TextEmbedding):
        def __init__(self, **kwargs):
            seen.append(("dense", kwargs))
            if kwargs.get("local_files_only") and not kwargs.get("specific_model_path"):
                raise ValueError("Could not load model from any source.")   # fastembed, when it is not cached

    class Sparse(SparseTextEmbedding):
        def __init__(self, **kwargs):
            seen.append(("sparse", kwargs))

    monkeypatch.setattr(fastembed, "TextEmbedding", Dense)
    monkeypatch.setattr(fastembed, "SparseTextEmbedding", Sparse)
    return seen


def dense(tmp_path, offline=False):
    from orchestrator.Core.rag_manager import create_dense_embedder
    from orchestrator.rag.embeddings import MODEL_REGISTRY
    return create_dense_embedder(MODEL_REGISTRY[E5], tmp_path / "cache", lambda: offline)


class TestFastembedThroughTheFetcher:

    def test_the_model_lands_in_fastembeds_cache_without_any_token(self, hub, captured, tmp_path):
        embedder = dense(tmp_path)
        embedder.prepare()
        folder = tmp_path / "cache" / "models--intfloat--multilingual-e5-small" / "snapshots" / COMMIT
        assert captured == [("dense", {"model_name": E5, "cache_dir": str(tmp_path / "cache"),
                                       "local_files_only": True, "specific_model_path": str(folder)})]
        assert {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} == set(FILES)
        assert (folder / "onnx" / "model.onnx").read_bytes() == ONNX
        assert (tmp_path / "cache" / "models--intfloat--multilingual-e5-small" / "refs" / "main").read_text() == \
            COMMIT
        no_token_anywhere(hub)
        assert embedder.cached() is True, "the existing cache check finds it"

    def test_a_model_in_the_cache_asks_nobody(self, hub, captured, tmp_path):
        dense(tmp_path).prepare()
        hub.api.clear()
        hub.files.clear()
        dense(tmp_path).prepare()
        assert hub.api == [] and hub.files == []
        assert captured[-1][1]["specific_model_path"].endswith(COMMIT)

    def test_bm25_fetches_only_its_language(self, hub, captured, tmp_path):
        from orchestrator.Core.rag_manager import create_sparse_embedder
        create_sparse_embedder(tmp_path / "cache", lambda: False).prepare()
        folder = tmp_path / "cache" / "models--Qdrant--bm25" / "snapshots" / COMMIT
        assert sorted(p.name for p in folder.iterdir()) == ["english.txt"]
        assert captured[-1] == ("sparse", {"model_name": "Qdrant/bm25", "cache_dir": str(tmp_path / "cache"),
                                           "local_files_only": True, "language": "english",
                                           "specific_model_path": str(folder)})
        no_token_anywhere(hub)

    def test_offline_mode_opens_no_socket(self, captured, tmp_path, monkeypatch):
        """Offline: a model in the cache is loaded without any connection; one that is not there is reported as
        not on this computer, also without one."""
        from test_hf_online import guard_sockets
        from orchestrator.rag.errors import EmbeddingModelNotCached
        folder = tmp_path / "cache" / "models--intfloat--multilingual-e5-small"
        for name, data in FILES.items():
            (folder / "snapshots" / COMMIT / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / "snapshots" / COMMIT / name).write_bytes(data)
        (folder / "refs").mkdir()
        (folder / "refs" / "main").write_text(COMMIT)
        tried = guard_sockets(monkeypatch)
        dense(tmp_path, offline=True).prepare()
        assert captured[-1][1]["specific_model_path"] == str(folder / "snapshots" / COMMIT)
        with pytest.raises(EmbeddingModelNotCached, match="offline mode is on"):
            dense(tmp_path / "empty", offline=True).prepare()
        assert tried == []

    def test_a_gated_or_private_model_is_not_fetched(self, hub, captured, tmp_path):
        from orchestrator.rag.errors import EmbeddingError
        hub.gated = "manual"
        with pytest.raises(EmbeddingError, match="gated or private"):
            dense(tmp_path).prepare()
        assert hub.files == [] and captured == []
