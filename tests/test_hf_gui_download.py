"""Hugging Face downloads by the person (DECISIONS round 11, A6): the GUI's Hugging Face browser downloads through the
same verified fetcher as the AI path (orchestrator/Core/hf_hub.py HubFetcher) into
<models_dir>/hf/<owner>--<name>/<commit[:12]>/<file>, under the one disk rule (Core/disk_plan reserve and the
reservation ledger) with its own reservation while it runs. Models downloaded before into the old huggingface_hub
cache (<models_dir>/models--<owner>--<name>/...) stay listed and usable; an optional one-time Take over copies them
into the new folder, checked, and offers to remove the old copies only with the exact list and the person's yes.

The Hub is a stand-in; no test reaches the network or the real home folder."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.tools.local_files import LocalFiles, mount_plan

OLD_COMMIT = "c" * 40
WEIGHTS = b"GGUF" + bytes(range(256)) * 16
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()
CONFIG = b'{"model_type": "llama"}'


def git_blob_id(data: bytes) -> str:
    """The id git (and the Hub, as the ETag of a file without LFS) gives a file's bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def old_cache(models: Path, repo: str = "acme/tiny-GGUF", commit: str = OLD_COMMIT, files=None,
              links: bool = True) -> Path:
    """A model as huggingface_hub's hf_hub_download(cache_dir=<models_dir>) left it: blobs named by their ETag (the
    sha256 of an LFS file, the git blob id of a small one), snapshot entries that link to them, and refs/main. Without
    `links` the snapshot holds the files themselves (huggingface_hub on a file system without links)."""
    files = files if files is not None else {"tiny-q4_k_m.gguf": (WEIGHTS, True)}
    owner, name = repo.split("/")
    folder = models / f"models--{owner}--{name}"
    snapshot = folder / "snapshots" / commit
    snapshot.mkdir(parents=True, exist_ok=True)
    (folder / "refs").mkdir(exist_ok=True)
    (folder / "refs" / "main").write_text(commit)
    for rel, (data, lfs) in files.items():
        entry = snapshot / rel
        entry.parent.mkdir(parents=True, exist_ok=True)
        if links:
            etag = hashlib.sha256(data).hexdigest() if lfs else git_blob_id(data)
            blob = folder / "blobs" / etag
            blob.parent.mkdir(exist_ok=True)
            blob.write_bytes(data)
            entry.symlink_to(Path(os.path.relpath(blob, entry.parent)))
        else:
            entry.write_bytes(data)
    return folder


@pytest.fixture
def area(tmp_path):
    state, models, datasets = tmp_path / "state", tmp_path / "data" / "models", tmp_path / "data" / "datasets"
    for folder in (state, models, datasets):
        folder.mkdir(parents=True)
    return SimpleNamespace(state=state, models=models, datasets=datasets, tmp=tmp_path,
                           files=LocalFiles(state, {"models": models, "datasets": datasets}))


# --- characterization: what holds before and after A6 -----------------------------------------------------------------

class TestTheOldCacheStaysListedAndUsable:
    """A file the GUI's browser downloaded before A6 lies in the old huggingface_hub cache inside the models folder.
    It is listed for AI callers and resolves for a build, as before (the link leads to its blob in the same folder)."""

    def test_a_downloaded_file_is_listed_and_binds_its_blob(self, area):
        folder = old_cache(area.models)
        ref = f"models:models--acme--tiny-GGUF/snapshots/{OLD_COMMIT}/tiny-q4_k_m.gguf"
        entries, _, total = area.files.list("model")
        assert [e["ref"] for e in entries] == [ref] and total == 1
        assert entries[0]["format"] == "gguf" and entries[0]["size_bytes"] == len(WEIGHTS)
        pin = area.files.resolve(ref)
        inner, volumes = mount_plan(pin, "/build-cache/input-model")
        blob = (folder / "blobs" / WEIGHTS_SHA).resolve()
        assert list(volumes) == [str(blob)], "the build reads the blob the link leads to, read-only"
        assert all(v["mode"] == "ro" for v in volumes.values())

    def test_a_downloaded_model_folder_is_one_entry_and_binds_file_by_file(self, area):
        folder = old_cache(area.models, "acme/tiny", files={"config.json": (CONFIG, False),
                                                            "model.safetensors": (WEIGHTS, True)})
        ref = f"models:models--acme--tiny/snapshots/{OLD_COMMIT}"
        entries, _, _ = area.files.list("model")
        assert [(e["ref"], e["format"]) for e in entries] == [(ref, "huggingface")]
        inner, volumes = mount_plan(area.files.resolve(ref), "/build-cache/input-model")
        assert volumes == {
            str((folder / "blobs" / git_blob_id(CONFIG)).resolve()): {"bind": "/build-cache/input-model/config.json",
                                                                      "mode": "ro"},
            str((folder / "blobs" / WEIGHTS_SHA).resolve()): {"bind": "/build-cache/input-model/model.safetensors",
                                                              "mode": "ro"}}

    def test_a_cache_without_links_is_listed_too(self, area):
        old_cache(area.models, links=False)
        entries, _, _ = area.files.list("model")
        assert [e["ref"] for e in entries] == [f"models:models--acme--tiny-GGUF/snapshots/{OLD_COMMIT}/"
                                               "tiny-q4_k_m.gguf"]


@pytest.mark.usefixtures("qapp")
class TestTheBrowsersDownloadWorker:
    """The browser's download runs in a worker thread and reports a failure as text (characterization)."""

    def test_a_failure_is_reported_with_its_text(self):
        from orchestrator.gui import huggingface_window as hf

        class Broken:
            def download_file(self, repo_id, filename, token=None, **kwargs):
                raise ValueError("Hugging Face refused config.json (HTTP 401).")

        worker = hf.DownloadWorker(Broken(), "acme/tiny-GGUF", "config.json")
        errors, done = [], []
        worker.error.connect(errors.append)
        worker.finished.connect(done.append)
        worker.run()
        assert errors == ["Hugging Face refused config.json (HTTP 401)."] and done == []


class TestTheOldCacheIsUsableForABuild:
    """Regression (A6, "stay usable"): a cache blob is named by its checksum, and the build tells a GGUF input by its
    suffix (config_module.sh: INPUT_FORMAT=gguf only for *.gguf). A linked file therefore keeps its link's name in the
    container, and a cached model folder chosen by path is bound file by file (its links lead to the cache's blobs,
    outside the folder, and did not resolve inside the container)."""

    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                            lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
        return BuildEngine(ConfigManager(tmp_path / "configs"))

    @staticmethod
    def stage(engine, source, pin=None):
        from orchestrator.Core.builder import BuildConfiguration, BuildProgress, BuildStatus, ModelFormat
        from orchestrator.tools.local_files import LocalInputs
        config = BuildConfiguration(build_id="b1", timestamp="t", model_source=str(source), target_arch="Rockchip",
                                    target_format=ModelFormat.GGUF, output_dir="out", quantization="Q4_K_M",
                                    local_inputs=LocalInputs(model=pin) if pin is not None else None)
        return engine._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))

    def test_an_ai_callers_reference_keeps_the_gguf_name(self, area):
        folder = old_cache(area.models)
        pin = area.files.resolve(f"models:models--acme--tiny-GGUF/snapshots/{OLD_COMMIT}/tiny-q4_k_m.gguf")
        inner, volumes = mount_plan(pin, "/build-cache/input-model")
        assert inner == "/build-cache/input-model/tiny-q4_k_m.gguf"
        assert volumes == {str((folder / "blobs" / WEIGHTS_SHA).resolve()): {"bind": inner, "mode": "ro"}}

    def test_a_plain_file_keeps_its_name_as_before(self, area):
        (area.models / "m.gguf").write_bytes(WEIGHTS)
        inner, _ = mount_plan(area.files.resolve("models:m.gguf"), "/build-cache/input-model")
        assert inner == "/build-cache/input-model/m.gguf"

    def test_the_builders_path_of_a_cached_gguf_keeps_its_name(self, area, engine):
        folder = old_cache(area.models)
        link = folder / "snapshots" / OLD_COMMIT / "tiny-q4_k_m.gguf"
        inner, volumes = self.stage(engine, link)
        assert inner == "/build-cache/input-model/tiny-q4_k_m.gguf"
        assert volumes == {str((folder / "blobs" / WEIGHTS_SHA).resolve()): {"bind": inner, "mode": "ro"}}

    def test_the_builders_path_of_a_cached_model_folder_binds_its_files(self, area, engine):
        folder = old_cache(area.models, "acme/tiny", files={"config.json": (CONFIG, False),
                                                            "model.safetensors": (WEIGHTS, True)})
        inner, volumes = self.stage(engine, folder / "snapshots" / OLD_COMMIT)
        assert inner == "/build-cache/input-model"
        assert volumes == {
            str((folder / "blobs" / git_blob_id(CONFIG)).resolve()): {"bind": f"{inner}/config.json", "mode": "ro"},
            str((folder / "blobs" / WEIGHTS_SHA).resolve()): {"bind": f"{inner}/model.safetensors", "mode": "ro"}}

    def test_a_folder_without_links_is_bound_whole_as_before(self, area, engine):
        model = area.models / "own-model"
        model.mkdir()
        (model / "config.json").write_bytes(CONFIG)
        assert self.stage(engine, model) == ("/build-cache/input-model",
                                             {str(model.resolve()): {"bind": "/build-cache/input-model",
                                                                     "mode": "ro"}})


# --- A6: the browser's download through the verified fetcher ----------------------------------------------------------

@pytest.fixture
def person(tmp_path, monkeypatch):
    """The browser's ModelManager over a framework stand-in with a per-user folder (the reservation ledger), the vault
    and a stand-in for the Hub (tests/test_hf_tools.py's)."""
    from orchestrator.Core import hf_hub
    from orchestrator.Core.disk_plan import ReservationLedger
    from orchestrator.Core.model_manager import ModelManager
    from test_hf_tools import DATA, FakeSession, Hub
    monkeypatch.setenv("HF_TOKEN", "hf_" + "Implicit" * 5)   # never to be used
    hub = Hub()
    models, state = tmp_path / "models", tmp_path / "state"
    models.mkdir()
    state.mkdir()
    settings = {"models_dir": str(models)}
    vault = {"hf_token": None}
    config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default), user_state_dir=str(state))
    framework = SimpleNamespace(config=config, resolve_path=lambda p: Path(p), hf_api_factory=hub.factory,
                                secrets_manager=SimpleNamespace(get_secret=lambda key: vault.get(key)))
    sessions = []

    def use(body=DATA, gate=None):
        session = FakeSession(body, gate)
        sessions.append(session)
        monkeypatch.setattr(hf_hub.HubFetcher, "session", property(lambda self: session))
        return session

    use()
    return SimpleNamespace(mm=ModelManager(framework), hub=hub, models=models, state=state, vault=vault,
                           framework=framework, use=use, sessions=sessions, ledger=ReservationLedger(state))


def new_place(person, name="tiny-q4_k_m.gguf"):
    from test_hf_tools import COMMIT
    return person.models / "hf" / "acme--tiny-GGUF" / COMMIT[:12] / name


class TestTheBrowsersDownload:

    def test_it_lands_pinned_in_the_new_folder_and_is_listed(self, person):
        from test_hf_tools import COMMIT, DATA, REPO
        path = person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert Path(path) == new_place(person) and Path(path).read_bytes() == DATA
        assert person.sessions[-1].seen[0][0] == f"https://huggingface.co/{REPO}/resolve/{COMMIT}/tiny-q4_k_m.gguf"
        assert ("model_info", REPO, {"revision": None, "files_metadata": True, "token": False}) in person.hub.calls
        assert not list(person.models.glob("models--*")), "nothing lands in huggingface_hub's cache any more"
        files = LocalFiles(person.state, {"models": person.models})
        refs = [e["ref"] for e in files.list("model")[0]]
        assert refs == [f"models:hf/acme--tiny-GGUF/{COMMIT[:12]}/tiny-q4_k_m.gguf"]

    def test_a_corrupted_byte_never_gets_its_name(self, person):
        from orchestrator.utils.download import IntegrityError
        from test_hf_tools import DATA, REPO
        bad = bytearray(DATA)
        bad[100] ^= 0xFF
        person.use(body=bytes(bad))
        with pytest.raises(IntegrityError, match="does not match"):
            person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert not new_place(person).exists() and not new_place(person).with_name("tiny-q4_k_m.gguf.part").exists()

    def test_a_small_file_without_lfs_is_pinned_by_size(self, person):
        from test_hf_tools import REPO
        person.use(body=b'{"a": 1234}\n')
        path = person.mm.download_file(REPO, "config.json")
        assert Path(path).read_bytes() == b'{"a": 1234}\n'

    def test_a_file_the_repository_does_not_list_is_refused_before_any_transfer(self, person):
        from orchestrator.Core.hf_hub import HubError
        from test_hf_tools import REPO
        with pytest.raises(HubError) as refused:
            person.mm.download_file(REPO, "other.gguf")
        assert refused.value.code == "unknown_id" and person.sessions[-1].seen == []

    def test_a_network_error_is_a_plain_error(self, person):
        from orchestrator.Core.hf_hub import HubError
        from test_hf_tools import REPO
        person.hub.fail = True
        with pytest.raises(HubError, match="could not be reached"):
            person.mm.download_file(REPO, "tiny-q4_k_m.gguf")

    def test_without_a_token_none_is_sent_never_an_implicit_one(self, person):
        from test_hf_tools import REPO
        person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert person.hub.tokens == [False]
        assert all("Authorization" not in headers for _, headers in person.sessions[-1].seen)

    def test_the_stored_token_or_the_typed_one_goes_only_to_huggingface(self, person):
        from test_hf_tools import REPO, TOKEN
        person.vault["hf_token"] = TOKEN
        person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert person.hub.tokens == [TOKEN]
        url, headers = person.sessions[-1].seen[0]
        assert url.startswith("https://huggingface.co/") and headers["Authorization"] == f"Bearer {TOKEN}"
        typed = "hf_" + "Typed" * 8
        new_place(person).unlink()
        person.mm.download_file(REPO, "tiny-q4_k_m.gguf", typed)
        assert person.hub.tokens[-1] == typed

    def test_progress_is_reported_and_a_stop_keeps_the_part(self, person):
        import threading
        from orchestrator.Core.hf_hub import DownloadStopped
        from test_hf_tools import DATA, REPO, Response
        stop = threading.Event()

        class Stopping(Response):
            def iter_content(self, chunk_size=1):
                yield self.data[:4096]
                stop.set()
                yield self.data[4096:]

        person.use(gate=lambda body, start: Stopping(body[start:], start, total=len(body)))
        seen = []
        with pytest.raises(DownloadStopped):
            person.mm.download_file(REPO, "tiny-q4_k_m.gguf", progress=lambda *a: seen.append(a), stop=stop)
        part = new_place(person).with_name("tiny-q4_k_m.gguf.part")
        assert seen == [("tiny-q4_k_m.gguf", 4096, len(DATA))]
        assert part.stat().st_size == 4096 and not new_place(person).exists()
        assert person.ledger.entries() == [], "a stopped download holds no space"


class TestTheTokenGoesOnlyToHuggingFace:
    """Regression (review of chain hf): huggingface_hub sends its API calls to the host in HF_ENDPOINT (a mirror the
    environment names). The person's typed or stored token went there with the browser's commit lookup, the Take
    over's Hub list and every look-up of an AI caller, while the fetcher sends it only to huggingface.co. The
    framework's own API calls are pinned to huggingface.co too."""

    @pytest.fixture
    def mirror(self, monkeypatch):
        import huggingface_hub
        from huggingface_hub import constants
        monkeypatch.setattr(constants, "ENDPOINT", "https://mirror.example")
        monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example")
        seen = []

        def model_info(self, repo_id, **kwargs):
            seen.append((self.endpoint, kwargs.get("token")))
            raise ConnectionError("no network in tests")

        monkeypatch.setattr(huggingface_hub.HfApi, "model_info", model_info)
        return seen

    @staticmethod
    def framework(tmp_path, token):
        settings = {"models_dir": str(tmp_path / "models")}
        config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                                 user_state_dir=str(tmp_path / "state"))
        return SimpleNamespace(config=config, resolve_path=lambda p: Path(p),
                               secrets_manager=SimpleNamespace(get_secret=lambda key: token))

    def test_the_browsers_lookup_sends_the_token_only_to_huggingface(self, mirror, tmp_path):
        from orchestrator.Core.hf_hub import HUB_URL, HubError
        from orchestrator.Core.model_manager import ModelManager
        from test_hf_tools import TOKEN
        manager = ModelManager(self.framework(tmp_path, TOKEN))
        typed = "hf_" + "Typed" * 8
        for token in (None, typed):
            with pytest.raises(HubError):
                manager.download_file("acme/tiny-GGUF", "tiny-q4_k_m.gguf", token)
        with pytest.raises(ConnectionError):
            manager._hub_lookup()("acme/tiny-GGUF", "c" * 40)
        assert mirror == [(HUB_URL, TOKEN), (HUB_URL, typed), (HUB_URL, TOKEN)]

    def test_an_ai_callers_lookup_too(self, mirror, tmp_path):
        from orchestrator.Core.hf_hub import HUB_URL, HubClient
        from test_hf_tools import TOKEN
        assert HubClient(self.framework(tmp_path, TOKEN)).api().endpoint == HUB_URL
        assert HubClient(self.framework(tmp_path, None)).api().endpoint == HUB_URL


class TestTheBrowsersDiskRule:
    """The one disk rule (Core/disk_plan, A4) for the browser's download: the reserve stays free beside what running
    builds and downloads reserved, and the download holds its own reservation while it runs (csC1 open item)."""

    def test_a_file_that_does_not_fit_is_refused_in_the_persons_language(self, person, monkeypatch):
        import shutil
        from orchestrator.Core.disk_plan import InsufficientSpace, person_reason
        from test_hf_tools import REPO
        monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(total=100 * 10 ** 9, used=0,
                                                                            free=2 * 1024 ** 3 + 100))
        with pytest.raises(InsufficientSpace) as refused:
            person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert "Not enough disk space for the download" in str(refused.value) and refused.value.checks
        assert person_reason(refused.value, "de").startswith("Nicht genug Speicherplatz")
        assert person.sessions[-1].seen == [] and not new_place(person).parent.exists()

    def test_space_a_running_build_holds_counts(self, person):
        from types import SimpleNamespace as NS
        from orchestrator.Core.disk_plan import DiskNeed, InsufficientSpace, person_reason
        from test_hf_tools import REPO, SHA, Sibling, info
        # conftest: the disk has 1000 GB with 600 GB free; reserve 20 GB. A build holds 575 GB of it.
        person.ledger.reserve([DiskNeed("cache", person.models, 575 * 10 ** 9, "build")], "build", "b1")
        person.hub.models[REPO] = info(files=[Sibling(rfilename="big.gguf", size=10 * 10 ** 9, lfs=NS(sha256=SHA))])
        with pytest.raises(InsufficientSpace) as refused:
            person.mm.download_file(REPO, "big.gguf")
        assert "reserved" in person_reason(refused.value, "en") and person.sessions[-1].seen == []

    def test_the_download_holds_its_own_reservation_while_it_runs(self, person):
        from orchestrator.Core.disk_plan import device_of
        from test_hf_tools import DATA, REPO, Response
        held = []
        person.use(gate=lambda body, start: held.append(person.ledger.entries()) or
                   Response(body[start:], start, total=len(body)))
        person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert [(e["owner"], e["label"], e["bytes"]) for e in held[0]] == [
            ("gui", f"{REPO}/tiny-q4_k_m.gguf", {device_of(person.models): len(DATA)})]
        assert person.ledger.entries() == [], "released when it ends"

    def test_a_failed_download_releases_its_reservation(self, person):
        from orchestrator.utils.download import IntegrityError
        from test_hf_tools import DATA, REPO
        person.use(body=DATA[:-1] + b"x")
        with pytest.raises(IntegrityError):
            person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert person.ledger.entries() == []

    def test_a_resumed_download_reserves_only_what_is_missing(self, person):
        from test_hf_tools import DATA, REPO, Response
        part = new_place(person).with_name("tiny-q4_k_m.gguf.part")
        part.parent.mkdir(parents=True)
        part.write_bytes(DATA[:5000])
        held = []
        person.use(gate=lambda body, start: held.append(person.ledger.entries()) or
                   Response(body[start:], start, total=len(body)))
        path = person.mm.download_file(REPO, "tiny-q4_k_m.gguf")
        assert Path(path).read_bytes() == DATA and person.sessions[-1].seen[0][1]["Range"] == "bytes=5000-"
        assert list(held[0][0]["bytes"].values()) == [len(DATA) - 5000]


@pytest.mark.usefixtures("qapp")
class TestTheBrowserWindowsDownload:
    """The window hands its progress callback and a stop event to the download, which runs in a worker thread."""

    def test_progress_arrives_from_the_worker(self):
        import threading
        from orchestrator.gui import huggingface_window as hf
        threads = []

        class Manager:
            def download_file(self, repo_id, filename, token=None, progress=None, stop=None):
                threads.append(threading.current_thread() is threading.main_thread())
                progress(filename, 50, 100)
                progress(filename, 50, 100)   # the same percent again: no second signal
                progress(filename, 100, 100)
                return "/models/hf/x"

        worker = hf.DownloadWorker(Manager(), "acme/tiny-GGUF", "x.gguf")
        seen = []
        worker.progress.connect(lambda done, total: seen.append((done, total)))
        worker.start()
        assert worker.wait(10000)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert seen == [(50, 100), (100, 100)] and threads == [False]

    def test_a_disk_refusal_is_shown_in_the_persons_language(self):
        from orchestrator.Core.disk_plan import person_reason, refusal, room
        from orchestrator.gui import huggingface_window as hf
        full = refusal([room(Path("."), 10 ** 9, label="models", disk_usage=lambda p: SimpleNamespace(
            total=100 * 10 ** 9, used=0, free=10 ** 9))], "the download")

        class Manager:
            def download_file(self, *args, **kwargs):
                raise full

        worker = hf.DownloadWorker(Manager(), "acme/tiny-GGUF", "x.gguf", lang="de")
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        assert errors == [person_reason(full, "de")] and errors[0].startswith("Nicht genug Speicherplatz")

    def test_the_stop_button_stops_the_download_and_keeps_the_part(self, qapp):
        import threading
        from orchestrator.Core.hf_hub import DownloadStopped
        from orchestrator.gui import huggingface_window as hf
        started = threading.Event()

        class Manager:
            def download_file(self, repo_id, filename, token=None, progress=None, stop=None):
                started.set()
                assert stop.wait(10)
                raise DownloadStopped("stopped")

        win = hf.HuggingFaceWindow(SimpleNamespace(model_manager=Manager(), secrets_manager=None))
        win.download_file("acme/tiny-GGUF", "x.gguf", None)
        assert started.wait(10) and win.stop_btn.isVisibleTo(win)
        win.stop_btn.click()
        assert win.dl_worker.wait(10000)
        qapp.processEvents()
        assert not win.stop_btn.isVisibleTo(win)
        assert "x.gguf" in win.status_label.text() and "kept" in win.status_label.text()


def test_there_is_one_hugging_face_downloader():
    """One code path (A6): no module downloads a model with huggingface_hub's own functions, which write its cache and
    check nothing the framework pinned; every file comes through hf_hub.HubFetcher."""
    import ast
    from conftest import REPO_ROOT
    found = []
    for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in ("hf_hub_download", "snapshot_download"):
                    found.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("huggingface_hub"):
                names = {a.name for a in node.names} & {"hf_hub_download", "snapshot_download"}
                if names:
                    found.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
    assert found == []
