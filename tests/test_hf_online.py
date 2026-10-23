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
        self.redirect = ""   # a host the Hub's API answers with a redirect to (a renamed repository, a mirror)

    def tokens_to(self, host: str):
        return [token for seen_host, _, token in self.seen if seen_host == host and token]

    def api_hosts(self):
        return {host for host, path, _ in self.seen if path != "/api/agent-harnesses"}

    def handle(self, request):
        import httpx
        auth = request.headers.get("authorization", "")
        self.seen.append((request.url.host, request.url.path, auth[len("Bearer "):] if auth else None))
        path = request.url.path
        if self.redirect and request.url.host == "huggingface.co" and path.startswith("/api/"):
            return httpx.Response(307, headers={"Location": f"{self.redirect}{path}"})
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

    def test_a_redirect_to_another_host_carries_no_token(self, wire, tmp_path):
        """An API answer that redirects to another host (a renamed repository, a mirror, a CDN): huggingface_hub's
        client follows it, and the token stays with huggingface.co (httpx drops Authorization when the origin changes;
        the fetcher's redirects are tests/test_hf_tools.py's)."""
        from orchestrator.Core.build_service import BuildService
        from orchestrator.Core.hf_hub import HubClient
        from orchestrator.Core.model_manager import ModelManager
        wire.redirect = FOREIGN
        manager = ModelManager(framework(tmp_path, STORED))
        assert "config.json" in manager.list_repo_files(REPO)
        assert manager.check_license(REPO)["license"] == "apache-2.0"
        assert [m["id"] for m in manager.search_huggingface_models("tiny")] == ["acme/free", "acme/locked"]
        assert real_look_up(REPO, None, STORED).commit == COMMIT
        assert BuildService(framework(tmp_path, STORED)).model_commit(REPO) == COMMIT
        assert HubClient(framework(tmp_path, STORED)).info(REPO).commit == COMMIT
        assert {host for host, _, _ in wire.seen} >= {"huggingface.co", "mirror.example"}, "the redirects were followed"
        assert wire.tokens_to("mirror.example") == [], "a token went through a redirect to another host"
        assert set(wire.tokens_to("huggingface.co")) == {STORED}


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


# --- offline mode: the browser refuses like every other Hugging Face caller -----------------------------------------

def offline_manager(tmp_path, token=None):
    from orchestrator.Core.model_manager import ModelManager
    return ModelManager(framework(tmp_path, token, offline_mode=True))


class TestOfflineMode:
    """Regression (maintainer, stage C wave 3): in offline mode the Hugging Face browser's search, licence check, file
    list and download still went online, while an AI caller's were refused (HubClient.check_online). They now refuse
    with the same refusal (hf_hub.offline_refusal) before any request, and the person reads it in plain words in their
    language (hf_hub.person_text); the Take over's Hub list asks nobody either."""

    def test_the_browsers_calls_refuse_before_any_request(self, wire, tmp_path):
        from orchestrator.Core.hf_hub import HubError
        manager = offline_manager(tmp_path, STORED)
        assert manager.offline() is True
        calls = [lambda: manager.search_huggingface_models("tiny"), lambda: manager.check_license(REPO),
                 lambda: manager.list_repo_files(REPO), lambda: manager.download_file(REPO, "tiny-q4_k_m.gguf"),
                 lambda: manager._hub_lookup()(REPO, COMMIT)]
        for call in calls:
            with pytest.raises(HubError) as refused:
                call()
            assert (refused.value.code, refused.value.blocker) == ("not_supported", "offline_mode")
        assert wire.seen == [], "nothing reached the network"
        assert not (tmp_path / "models").exists(), "nothing was written"

    def test_it_is_the_refusal_an_ai_caller_gets(self, wire, tmp_path):
        from orchestrator.Core.hf_hub import HubClient, HubError
        with pytest.raises(HubError) as person:
            offline_manager(tmp_path).search_huggingface_models("tiny")
        with pytest.raises(HubError) as ai:
            HubClient(framework(tmp_path, offline_mode=True)).search("tiny")
        assert (str(person.value), person.value.code, person.value.next, person.value.blocker) == \
            (str(ai.value), ai.value.code, ai.value.next, ai.value.blocker)
        assert wire.seen == []

    def test_the_person_reads_it_in_plain_words_in_their_language(self, tmp_path):
        from orchestrator.Core.hf_hub import HubError, person_text
        from orchestrator.utils.next_steps import mouse_path
        with pytest.raises(HubError) as refused:
            offline_manager(tmp_path).list_repo_files(REPO)
        from orchestrator.tools.facts_catalog import person_action_text
        english, german = person_text(refused.value, "en"), person_text(refused.value, "de")
        # Review of chain hf-online: it said "the framework does not contact Hugging Face", while a build the person
        # starts still downloads its model in offline mode (BuildEngine._stage_model does not read offline mode); the
        # text names what refuses: the Hugging Face browser.
        assert english.startswith("Offline mode is on, so the Hugging Face browser does not contact Hugging Face")
        assert german.startswith("Der Offline-Modus ist an, deshalb nimmt der Hugging-Face-Browser keinen Kontakt")
        assert mouse_path("act_ditto_settings", "en") in english, "where to switch it off"
        assert german.startswith("Der Offline-Modus ist an") and mouse_path("act_ditto_settings", "de") in german
        assert english.endswith(person_action_text("offline_mode", "en")), "the words of every offline refusal"
        assert "offline_mode" not in english + german, "no setting name, no reason code"

    def test_other_errors_keep_their_text(self):
        from orchestrator.Core.hf_hub import HubError, person_text
        assert person_text(HubError("Hugging Face could not be reached."), "de") == \
            "Hugging Face could not be reached."

    def test_the_take_over_works_offline_and_says_why_a_file_stays(self, wire, tmp_path):
        from test_hf_gui_download import WEIGHTS, old_cache
        models = tmp_path / "models"
        old_cache(models, "acme/linked", files={"a.gguf": (WEIGHTS, True)})
        old_cache(models, "acme/plain", files={"b.gguf": (WEIGHTS, True)}, links=False)
        manager = offline_manager(tmp_path, STORED)
        result = manager.take_over(manager.old_downloads())
        assert [f.repo for f in result.copied] == ["acme/linked"], "checked by its blob's name: no question needed"
        assert [(f.repo, code) for f, code in result.skipped] == [("acme/plain", "offline")]
        assert wire.seen == []

    def test_the_offline_reason_has_its_words(self):
        from orchestrator.Core.hf_takeover import REASONS
        from orchestrator.utils.localization import ui_text
        assert "offline" in REASONS
        for lang in ("en", "de"):
            assert ui_text("hf.takeover.why.offline", lang) != "hf.takeover.why.offline"


class TestTheBrowserWindowOffline:
    """The window refuses before a worker starts or a token is asked for, in a plain box (Tools > Hugging Face Model
    Browser...)."""

    @pytest.fixture
    def window(self, qapp, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        from orchestrator.gui import huggingface_window as hf, plain
        boxes = []
        monkeypatch.setattr(plain, "plain_box", lambda parent, kind, title, text, **kw: boxes.append((kind, title,
                                                                                                    text)))
        manager = offline_manager(tmp_path)
        win = hf.HuggingFaceWindow(SimpleNamespace(model_manager=manager, secrets_manager=None))
        return SimpleNamespace(win=win, boxes=boxes, hf=hf)

    def test_a_search_is_refused_in_a_plain_box(self, window, wire):
        from orchestrator.Core.hf_hub import offline_refusal, person_text
        from orchestrator.utils.localization import ui_text
        window.win.search_edit.setText("tiny")
        window.win.start_search()
        assert window.win.worker is None, "no worker started"
        assert window.boxes == [("information", ui_text("hf.offline.title", "en"),
                                 person_text(offline_refusal(), "en"))]
        assert window.win.status_label.text() == ui_text("blocker.offline_mode", "en")
        assert wire.seen == []

    def test_a_download_is_refused_before_the_token_question(self, window, monkeypatch):
        asked = []
        monkeypatch.setattr(window.hf, "AskTokenDialog", lambda *a, **k: asked.append(a))
        window.win.populate_table([{"id": "meta/locked", "downloads": 1, "likes": 0, "gated": True}])
        window.win.on_download_request(0, 0)
        assert asked == [] and window.win.list_worker is None and len(window.boxes) == 1

    def test_a_worker_reports_the_refusal_in_the_persons_language(self, window):
        worker = window.hf.SearchWorker(window.win.model_manager, "tiny", lang="de")
        seen = []
        worker.error.connect(seen.append)
        worker.run()
        from orchestrator.Core.hf_hub import offline_refusal, person_text
        assert seen == [person_text(offline_refusal(), "de")] and seen[0].startswith("Der Offline-Modus ist an")


# --- offline mode opens no connection at all (a socket guard, not only the stand-in transport) ----------------------

def guard_sockets(monkeypatch):
    """Every way to open a network connection refuses and is recorded: a name lookup, socket.create_connection
    (httpx, huggingface_hub's client) and a socket's connect (urllib3, the fetcher's requests). huggingface_hub uses its
    real client here, never the stand-in transport. A loopback connect is let through (Windows builds a socketpair
    with one). Returns the list of what was tried."""
    import socket
    from huggingface_hub.utils import _http
    tried = []

    def refuse(what, *args):
        tried.append((what,) + tuple(args[:1]))
        raise OSError(f"socket guard: {what} in a test that must not reach the network")

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def loopback(address) -> bool:
        return isinstance(address, tuple) and str(address[0]) in ("127.0.0.1", "::1")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: refuse("getaddrinfo", *a))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse("create_connection", *a))
    monkeypatch.setattr(socket.socket, "connect",
                        lambda self, address: real_connect(self, address) if loopback(address) else
                        refuse("connect", address))
    monkeypatch.setattr(socket.socket, "connect_ex",
                        lambda self, address: real_connect_ex(self, address) if loopback(address) else
                        refuse("connect_ex", address))
    _http.set_client_factory(_http.default_client_factory)
    return tried


@pytest.fixture
def no_network(monkeypatch):
    tried = guard_sockets(monkeypatch)
    yield tried
    from huggingface_hub.utils import _http
    _http.close_session()


class TestOfflineModeOpensNoSocket:
    """Offline mode is checked before any connection is opened, not only before a request reaches the stand-in
    transport: with every socket refused and recorded, the browser's calls, the Take over, an AI caller's calls, the
    GUI's commit check and the browser window in offline mode try none. The first test proves the guard sees a real
    request of huggingface_hub's client and of the fetcher."""

    def test_the_guard_sees_a_real_request(self, no_network, tmp_path):
        from orchestrator.Core.hf_hub import HubFetcher, HubFile
        from orchestrator.Core.model_manager import ModelManager
        with pytest.raises(Exception):
            ModelManager(framework(tmp_path)).list_repo_files(REPO)
        assert no_network, "huggingface_hub's own client was not stopped by the guard"
        before = len(no_network)
        with pytest.raises(Exception):
            HubFetcher(sleep=lambda s: None).fetch_file(REPO, COMMIT, HubFile("x.gguf", 1, None), tmp_path / "x.gguf",
                                                        attempts=1)
        assert len(no_network) > before, "the fetcher's requests were not stopped by the guard"

    def test_the_browsers_calls_and_the_take_over(self, no_network, tmp_path):
        from orchestrator.Core.hf_hub import HubError
        from test_hf_gui_download import WEIGHTS, old_cache
        manager = offline_manager(tmp_path, STORED)
        for call in (lambda: manager.search_huggingface_models("tiny"), lambda: manager.check_license(REPO),
                     lambda: manager.list_repo_files(REPO, "hf_" + "Typed" * 8),
                     lambda: manager.download_file(REPO, "tiny-q4_k_m.gguf"),
                     lambda: manager._hub_lookup()(REPO, COMMIT)):
            with pytest.raises(HubError):
                call()
        old_cache(tmp_path / "models", "acme/plain", files={"b.gguf": (WEIGHTS, True)}, links=False)
        result = manager.take_over(manager.old_downloads())
        assert [(f.repo, code) for f, code in result.skipped] == [("acme/plain", "offline")]
        assert no_network == []

    def test_an_ai_callers_calls_and_the_gui_commit_check(self, no_network, tmp_path):
        from orchestrator.Core.build_service import BuildService
        from orchestrator.Core.hf_hub import HubClient, HubError
        client = HubClient(framework(tmp_path, STORED, offline_mode=True))
        for call in (lambda: client.search("tiny"), lambda: client.info(REPO)):
            with pytest.raises(HubError):
                call()
        assert BuildService(framework(tmp_path, STORED, offline_mode=True)).model_commit(REPO) is None
        assert no_network == []

    def test_the_browser_window(self, qapp, no_network, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        from orchestrator.gui import huggingface_window as hf, plain
        from test_hf_gui_download import WEIGHTS, old_cache
        boxes = []
        monkeypatch.setattr(plain, "plain_box", lambda parent, kind, title, text, **kw: boxes.append(kind))
        monkeypatch.setattr(hf, "AskTokenDialog", lambda *a, **k: pytest.fail("no token question offline"))
        manager = offline_manager(tmp_path, STORED)
        win = hf.HuggingFaceWindow(SimpleNamespace(model_manager=manager, secrets_manager=None))
        win.search_edit.setText("tiny")
        win.start_search()
        win.populate_table([{"id": "meta/locked", "downloads": 1, "likes": 0, "gated": True}])
        win.on_download_request(0, 0)
        assert win.worker is None and win.list_worker is None and boxes == ["information", "information"]
        for worker in (hf.SearchWorker(manager, "tiny", lang="en"), hf.FileListWorker(manager, REPO, lang="en"),
                       hf.DownloadWorker(manager, REPO, "tiny-q4_k_m.gguf", lang="en")):
            worker.run()   # what the window's threads would run, had they been started
        old_cache(tmp_path / "models", "acme/plain", files={"b.gguf": (WEIGHTS, True)}, links=False)
        taken = []
        worker = hf.TakeOverWorker(manager, manager.old_downloads(), lang="en")
        worker.finished.connect(lambda result, plan: taken.append(result))
        worker.run()
        assert [code for _, code in taken[0].skipped] == ["offline"]
        assert no_network == []
