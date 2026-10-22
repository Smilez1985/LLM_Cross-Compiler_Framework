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
