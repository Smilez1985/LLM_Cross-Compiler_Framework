"""Every Hugging Face API call of the framework goes through orchestrator/Core/hf_hub.py (hub_api, hub_file), so one
token rule holds everywhere: the token the person stored in the framework (or typed in the browser) goes only to
huggingface.co, never to the host in HF_ENDPOINT, and an implicit token (HF_TOKEN, the Hugging Face home folder's
token file) is never sent.

The callers are driven through huggingface_hub's own HTTP client (huggingface_hub.utils._http.set_client_factory)
over a stand-in for the Hub's API (httpx.MockTransport): every request is recorded with its host, its path and the
token it carries, and answered with the Hub's JSON. No test reaches the network or the real home folder."""

import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.hf_lookup import look_up as real_look_up

REPO = "acme/tiny-GGUF"
COMMIT = "c" * 40
LFS_SHA = "a" * 64
STORED = "hf_" + "Stored" * 6        # built at runtime: never a credential-shaped literal in the repository
IMPLICIT = "hf_" + "Implicit" * 5    # HF_TOKEN: never to be sent
FOREIGN = "https://mirror.example"   # an HF_ENDPOINT that is not huggingface.co

SIBLINGS = [{"rfilename": "config.json", "size": 12},
            {"rfilename": "model.safetensors", "size": 99, "lfs": {"sha256": LFS_SHA, "size": 99, "pointerSize": 134}},
            {"rfilename": "tiny-q4_k_m.gguf", "size": 77, "lfs": {"sha256": "b" * 64, "size": 77, "pointerSize": 134}}]


class Wire:
    """The Hub's API as huggingface_hub's client sees it: `seen` holds (host, path, token or None) of every request."""

    def __init__(self, licence="apache-2.0", gated=False):
        self.seen, self.licence, self.gated = [], licence, gated

    def tokens_to(self, host: str):
        return [token for seen_host, _, token in self.seen if seen_host == host and token]

    def api_hosts(self):
        return {host for host, path, _ in self.seen if path != "/api/agent-harnesses"}

    def handle(self, request):
        import httpx
        auth = request.headers.get("authorization", "")
        self.seen.append((request.url.host, request.url.path, auth[len("Bearer "):] if auth else None))
        path = request.url.path
        if path == "/api/models":
            return httpx.Response(200, json=[
                {"id": "acme/free", "modelId": "acme/free", "downloads": 10, "likes": 1, "gated": False,
                 "tags": ["license:mit", "gguf"]},
                {"id": "acme/locked", "modelId": "acme/locked", "downloads": 5, "likes": 0, "gated": "manual",
                 "tags": []}])
        if re.fullmatch(r"/api/models/[^/]+/[^/]+/tree/[^/]+", path):
            return httpx.Response(200, json=[{"type": "file", "path": s["rfilename"], "size": s["size"],
                                              "oid": "0" * 40} for s in SIBLINGS])
        if re.fullmatch(r"/api/models/[^/]+/[^/]+/auth-check", path):
            return httpx.Response(200, json={})
        found = re.fullmatch(r"/api/models/([^/]+/[^/]+)(?:/revision/[^/]+)?", path)
        if found:
            return httpx.Response(200, json={
                "id": found.group(1), "modelId": found.group(1), "sha": COMMIT, "siblings": SIBLINGS,
                "cardData": {"license": self.licence}, "tags": [f"license:{self.licence}"], "gated": self.gated,
                "safetensors": {"total": 1234, "parameters": {"F32": 1234}}})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def wire(monkeypatch):
    """huggingface_hub's own client over the stand-in transport; HF_TOKEN is set (and must never be sent)."""
    import httpx
    from huggingface_hub.utils import _http
    monkeypatch.setenv("HF_TOKEN", IMPLICIT)
    hub = Wire()
    _http.set_client_factory(lambda: httpx.Client(transport=httpx.MockTransport(hub.handle),
                                                  event_hooks={"request": [_http.hf_request_event_hook]},
                                                  follow_redirects=True))
    try:
        yield hub
    finally:
        _http.set_client_factory(_http.default_client_factory)


def framework(tmp_path, token=None, **settings):
    values = {"models_dir": str(tmp_path / "models"), **settings}
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                             user_state_dir=str(tmp_path / "state"))
    return SimpleNamespace(config=config, resolve_path=lambda p: Path(p), values=values,
                           secrets_manager=SimpleNamespace(get_secret=lambda key: token if key == "hf_token" else None))


def engine(tmp_path, token=None):
    from orchestrator.Core.builder import BuildEngine
    e = BuildEngine.__new__(BuildEngine)
    e.cache_dir, e.base_dir = tmp_path / "cache", tmp_path
    e.secrets_manager = SimpleNamespace(get_secret=lambda key: token if key == "hf_token" else None)
    e.framework, e.config = None, {}
    e.logger = SimpleNamespace(info=print, warning=print, debug=print, error=print)
    e._get_conf = lambda key, default=None: default
    return e


def stage(tmp_path, monkeypatch, token=None):
    """The Builder's staging of REPO with a stand-in for the fetcher: (what it fetched, the tokens it got)."""
    from orchestrator.Core.builder import BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, ModelFormat
    fetched, tokens = [], []

    class Fetcher:
        def __init__(self, token, stop):
            tokens.append(token)

        def fetch_file(self, repo, commit, file, dest, progress=None):
            fetched.append((file.name, file.size, file.sha256, commit))
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"x")
            return Path(dest), "0" * 64

    monkeypatch.setattr(BuildEngine, "_hub_fetcher", staticmethod(lambda token, stop: Fetcher(token, stop)))
    config = BuildConfiguration(build_id="b1", timestamp="t", model_source=REPO, target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir="out")
    engine(tmp_path, token)._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
    return fetched, tokens


# --- characterization: what every caller gets from the Hub (holds before and after the wiring) ---------------------

class TestWhatTheCallersGet:

    def test_the_browsers_search(self, wire, tmp_path):
        from orchestrator.Core.model_manager import ModelManager
        found = ModelManager(framework(tmp_path)).search_huggingface_models("tiny")
        assert found == [
            {"id": "acme/free", "downloads": 10, "likes": 1, "gated": False, "gating": "false", "license": "mit"},
            {"id": "acme/locked", "downloads": 5, "likes": 0, "gated": True, "gating": "manual",
             "license": "unknown"}]

    def test_the_browsers_licence_check(self, wire, tmp_path):
        from orchestrator.Core.model_manager import ModelManager
        manager = ModelManager(framework(tmp_path))
        assert manager.check_license(REPO) == {"license": "apache-2.0", "restrictive": "false",
                                               "is_restrictive": False, "message": "Permissive license: apache-2.0"}
        wire.licence = "llama3.1"
        restricted = manager.check_license(REPO)
        assert restricted["restrictive"] == "true" and restricted["is_restrictive"] is True

    def test_the_browsers_file_list(self, wire, tmp_path):
        from orchestrator.Core.model_manager import ModelManager
        assert ModelManager(framework(tmp_path)).list_repo_files(REPO) == [s["rfilename"] for s in SIBLINGS]

    def test_the_gui_commit_check_of_an_identical_build(self, wire, tmp_path):
        from orchestrator.Core.build_service import BuildService
        assert BuildService(framework(tmp_path)).model_commit(REPO) == COMMIT

    def test_the_build_plans_look_up(self, wire):
        facts = real_look_up(REPO, None, None)
        assert facts.commit == COMMIT and facts.license_class == "permissive" and facts.parameters == 1234
        assert [(f.name, f.size, f.sha256) for f in facts.files] == [
            ("config.json", 12, None), ("model.safetensors", 99, LFS_SHA), ("tiny-q4_k_m.gguf", 77, "b" * 64)]
        assert [f.name for f in facts.staged] == ["config.json", "model.safetensors"]

    def test_the_builders_staging(self, wire, tmp_path, monkeypatch):
        fetched, _ = stage(tmp_path, monkeypatch)
        assert fetched == [("config.json", 12, None, COMMIT), ("model.safetensors", 99, LFS_SHA, COMMIT)]


# --- Security: the token goes only to huggingface.co, also with HF_ENDPOINT set -------------------------------------

@pytest.fixture
def foreign(wire, monkeypatch):
    """HF_ENDPOINT names another host: huggingface_hub sends every call of an HfApi without its own endpoint there."""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "ENDPOINT", FOREIGN)
    monkeypatch.setenv("HF_ENDPOINT", FOREIGN)
    return wire


def only_to_huggingface(wire, token):
    """No request carries a token to the host in HF_ENDPOINT, none carries the implicit HF_TOKEN, every API call goes
    to huggingface.co, and the token sent there is exactly the framework's own (`token`), or none at all."""
    assert wire.tokens_to("mirror.example") == [], "a token went to the host in HF_ENDPOINT"
    assert IMPLICIT not in [token for _, _, token in wire.seen], "the implicit HF_TOKEN was sent"
    assert wire.api_hosts() == {"huggingface.co"}, wire.seen
    assert set(wire.tokens_to("huggingface.co")) == ({token} if token else set())


class TestTheTokenGoesOnlyToHuggingFace:
    """Regression (maintainer, stage C wave 3): the wave-2 fix pinned hf_hub's own calls, while the Builder's staging,
    the build plan's look-up and the GUI's commit check created their own HfApi() and sent the stored token to the
    host in HF_ENDPOINT, and the browser's search, licence check and file list sent the implicit HF_TOKEN there. Every
    caller now goes through hf_hub.hub_api."""

    @pytest.mark.parametrize("token", [STORED, None])
    def test_the_builders_staging(self, foreign, tmp_path, monkeypatch, token):
        fetched, tokens = stage(tmp_path, monkeypatch, token)
        assert [f[0] for f in fetched] == ["config.json", "model.safetensors"] and tokens == [token]
        only_to_huggingface(foreign, token)

    @pytest.mark.parametrize("token", [STORED, None])
    def test_the_build_plans_look_up(self, foreign, token):
        assert real_look_up(REPO, None, token).commit == COMMIT
        only_to_huggingface(foreign, token)

    @pytest.mark.parametrize("token", [STORED, None])
    def test_the_gui_commit_check_of_an_identical_build(self, foreign, tmp_path, token):
        from orchestrator.Core.build_service import BuildService
        assert BuildService(framework(tmp_path, token)).model_commit(REPO) == COMMIT
        only_to_huggingface(foreign, token)

    @pytest.mark.parametrize("token", [STORED, None])
    def test_the_browsers_search_licence_check_and_file_list(self, foreign, tmp_path, token):
        from orchestrator.Core.model_manager import ModelManager
        manager = ModelManager(framework(tmp_path, token))
        assert [m["id"] for m in manager.search_huggingface_models("tiny")] == ["acme/free", "acme/locked"]
        assert manager.check_license(REPO)["license"] == "apache-2.0"
        assert "config.json" in manager.list_repo_files(REPO)
        only_to_huggingface(foreign, token)

    def test_the_token_typed_in_the_browser_too(self, foreign, tmp_path):
        from orchestrator.Core.model_manager import ModelManager
        typed = "hf_" + "Typed" * 8
        assert "config.json" in ModelManager(framework(tmp_path, STORED)).list_repo_files(REPO, typed)
        only_to_huggingface(foreign, typed)

    def test_the_take_overs_hub_list(self, foreign, tmp_path):
        from orchestrator.Core.model_manager import ModelManager
        listed = ModelManager(framework(tmp_path, STORED))._hub_lookup()(REPO, COMMIT)
        assert listed["model.safetensors"].sha256 == LFS_SHA
        only_to_huggingface(foreign, STORED)

    def test_an_ai_callers_search_and_look_up(self, foreign, tmp_path):
        from orchestrator.Core.hf_hub import HubClient
        client = HubClient(framework(tmp_path, STORED))
        assert [m["id"] for m in client.search("tiny", gated_mode="any")] == ["acme/free", "acme/locked"]
        assert client.info(REPO).commit == COMMIT
        only_to_huggingface(foreign, STORED)


def test_every_hub_api_object_comes_from_hub_api():
    """One Hub API (R0): no module but orchestrator/Core/hf_hub.py creates huggingface_hub's HfApi or imports its
    module-level API functions, which send an implicit token to the host in HF_ENDPOINT; they go through
    hf_hub.hub_api (huggingface.co, the framework's token or none)."""
    import ast
    from conftest import REPO_ROOT
    api_names = {"HfApi", "model_info", "list_models", "list_repo_files", "list_repo_tree", "auth_check", "whoami",
                 "get_hf_file_metadata", "hf_hub_url", "get_paths_info", "repo_info", "file_exists"}
    found = []
    for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
        name = path.relative_to(REPO_ROOT).as_posix()
        if name == "orchestrator/Core/hf_hub.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("huggingface_hub"):
                found += [f"{name}:{node.lineno} imports {a.name}" for a in node.names if a.name in api_names]
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "HfApi":
                found.append(f"{name}:{node.lineno} calls HfApi")
    assert found == []
