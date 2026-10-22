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
