"""Where golden artifacts go (DECISIONS, eighth round, answer 3).

The person sets the output folder once (default ~/llmcf/output). A build the person starts in the GUI waits in the
framework's build folder; when it has finished, a native save dialog opens at the output folder: OK saves it there or
in the folder the person picked, Cancel saves nothing elsewhere and deletes nothing (Save... later). AI and CLI builds
without a person at a terminal go to the output folder, and the result names it (a label for AIs, the full path for
the person). Earlier builds stay where they are and stay listed. System folders and the program folder are refused,
free space is checked before copying, every copy is checked by sha256, and no build ever replaces another one.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import artifact_output as ao
from orchestrator.Core.build_service import BuildService

GB = 1000 ** 3
REPO = Path(__file__).resolve().parent.parent


class Cfg:
    def __init__(self, tmp: Path, **values):
        self.user_state_dir = str(tmp / "state")
        Path(self.user_state_dir).mkdir(parents=True, exist_ok=True)
        self.values = {"output_dir": str(tmp / "out"), "build_dir": str(tmp / "builds"),
                       "cache_dir": str(tmp / "cache"), **values}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True


@pytest.fixture
def fw(tmp_path):
    return SimpleNamespace(config=Cfg(tmp_path), resolve_path=lambda p: Path(str(p)).expanduser())


def finished_build(root: Path, rel="Rockchip/m/Q4_K_M", payload=b"weights", zip_payload=b"zip", **manifest):
    folder = root / rel
    folder.mkdir(parents=True)
    (folder / "m.gguf").write_bytes(payload)
    (folder / "Model_Card.md").write_text("card")
    info = {"primary": "m.gguf", "format": "gguf", "created": "2026-10-15T10:00:00", "target": "Rockchip",
            "artifacts": [{"file": "m.gguf", "sha256": hashlib.sha256(payload).hexdigest()}], **manifest}
    (folder / "build_info.json").write_text(json.dumps(info))
    if zip_payload is not None:
        (folder.parent / f"{folder.name}.zip").write_bytes(zip_payload)
    return folder


# --- the folders and what is refused ------------------------------------------------------------------------------

def test_the_default_output_folder_is_in_the_home_folder(make_framework):
    fm = make_framework(initialize=False)
    assert fm.config.get("output_dir") == "~/llmcf/output"
    assert ao.output_folder(fm) == Path.home() / "llmcf" / "output"
    assert ao.build_folder(fm) == Path(fm.info.installation_path) / "output", "where builds went before"


@pytest.mark.parametrize("folder", ["/", "/etc", "/etc/llm", "/usr/lib/models", "/proc/1", "/home", "/var",
                                    "/boot/x"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX system folders")
def test_system_folders_are_refused(fw, folder):
    assert "system folder" in ao.refusal(fw, Path(folder))
    with pytest.raises(ao.DestinationRefused):
        ao.check_destination(fw, folder, create=True)


def test_the_program_build_state_and_trash_folders_are_refused(fw, tmp_path):
    assert "program folder" in ao.refusal(fw, REPO / "models")
    assert "build folder" in ao.refusal(fw, tmp_path / "builds" / "x")
    assert "per-user folder" in ao.refusal(fw, tmp_path / "state" / "out")
    assert "trash" in ao.refusal(fw, tmp_path / "out" / ".trash")
    assert ao.refusal(fw, tmp_path / "big-disk" / "models") == ""


def test_a_new_folder_is_made_only_when_asked(fw, tmp_path):
    with pytest.raises(ao.DestinationRefused, match="does not exist"):
        ao.check_destination(fw, tmp_path / "new")
    assert ao.check_destination(fw, tmp_path / "new", create=True).is_dir()
    (tmp_path / "file").write_text("x")
    with pytest.raises(ao.DestinationRefused, match="a file"):
        ao.check_destination(fw, tmp_path / "file")


# --- saving -------------------------------------------------------------------------------------------------------

def test_saving_on_the_same_disk_renames_and_copies_nothing(fw, tmp_path):
    folder = finished_build(tmp_path / "builds")
    result = ao.save_artifact(fw, folder, tmp_path / "out", create=True)
    target = tmp_path / "out" / "Rockchip" / "m" / "Q4_K_M"
    assert result.renamed and result.folder == target and result.zip == target.parent / "Q4_K_M.zip"
    assert (target / "m.gguf").read_bytes() == b"weights" and result.zip.read_bytes() == b"zip"
    assert not folder.exists() and not (folder.parent / "Q4_K_M.zip").exists(), "moved, not copied"
    assert [a.where for a in ao.list_artifacts(fw)] == ["output"]


def test_a_taken_name_is_never_overwritten(fw, tmp_path):
    earlier = finished_build(tmp_path / "out", payload=b"earlier")
    folder = finished_build(tmp_path / "builds")
    result = ao.save_artifact(fw, folder, tmp_path / "out")
    assert result.folder.name == "Q4_K_M-2" and (earlier / "m.gguf").read_bytes() == b"earlier"
    assert (earlier.parent / "Q4_K_M.zip").read_bytes() == b"zip" and result.zip.name == "Q4_K_M-2.zip"


def test_saving_onto_another_disk_copies_checks_and_then_removes_the_build_folders_copy(fw, tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "_device", lambda path: 1 if str(path).startswith(str(tmp_path / "builds")) else 2)
    folder = finished_build(tmp_path / "builds")
    other = tmp_path / "disk2"
    other.mkdir()
    result = ao.save_artifact(fw, folder, other)
    assert not result.renamed and result.files == 4 and (result.folder / "m.gguf").read_bytes() == b"weights"
    assert json.loads((result.folder / "build_info.json").read_text())["primary"] == "m.gguf"
    assert not folder.exists() and not (folder.parent / "Q4_K_M.zip").exists()
    assert not [p for p in other.rglob("*") if p.name.endswith(".partial")], "no unfinished copy is left"
    listed = {a.where: a for a in ao.list_artifacts(fw)}
    assert listed["saved"].label == "saved:Rockchip/m/Q4_K_M", "remembered, and named without a host path"


def test_two_mounts_of_one_disk_get_a_checked_copy(fw, tmp_path, monkeypatch):
    """The same disk, but the system cannot rename between the two folders (EXDEV): copied and checked instead."""
    import errno
    folder = finished_build(tmp_path / "builds")
    real_rename = os.rename

    def rename(src, dst):
        if Path(src) == folder.resolve():
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst)

    monkeypatch.setattr(ao.os, "rename", rename)
    result = ao.save_artifact(fw, folder, tmp_path / "out", create=True)
    assert not result.renamed and (result.folder / "m.gguf").read_bytes() == b"weights" and not folder.exists()


def test_a_copy_that_does_not_match_moves_nothing(fw, tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "_device", lambda path: 1 if str(path).startswith(str(tmp_path / "builds")) else 2)
    real_sha = ao._sha256
    monkeypatch.setattr(ao, "_sha256", lambda path: "0" * 64 if path.name == "m.gguf" else real_sha(path))
    folder = finished_build(tmp_path / "builds")
    (tmp_path / "disk2").mkdir()
    with pytest.raises(ao.DestinationRefused, match="does not match"):
        ao.save_artifact(fw, folder, tmp_path / "disk2")
    assert (folder / "m.gguf").read_bytes() == b"weights" and (folder.parent / "Q4_K_M.zip").exists()
    assert list((tmp_path / "disk2").rglob("*.gguf")) == [] and not list((tmp_path / "disk2").rglob("*.partial"))


def test_a_model_file_that_no_longer_matches_its_manifest_is_not_saved(fw, tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "_device", lambda path: 1 if str(path).startswith(str(tmp_path / "builds")) else 2)
    folder = finished_build(tmp_path / "builds")
    (folder / "m.gguf").write_bytes(b"changed after the build")
    (tmp_path / "disk2").mkdir()
    with pytest.raises(ao.DestinationRefused, match="does not match"):
        ao.save_artifact(fw, folder, tmp_path / "disk2")
    assert folder.is_dir()


def test_free_space_is_checked_before_copying(fw, tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "_device", lambda path: 1 if str(path).startswith(str(tmp_path / "builds")) else 2)
    monkeypatch.setattr(ao, "_disk_usage", lambda p: shutil._ntuple_diskusage(100 * GB, 99 * GB, 1 * GB))
    folder = finished_build(tmp_path / "builds")
    (tmp_path / "disk2").mkdir()
    with pytest.raises(ao.DestinationRefused) as exc:
        ao.save_artifact(fw, folder, tmp_path / "disk2")
    assert exc.value.code == "insufficient_resources" and folder.is_dir()
    assert list((tmp_path / "disk2").iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="links without extra rights")
def test_a_link_that_is_not_copied_is_named_and_the_build_folder_keeps_its_copy(fw, tmp_path, monkeypatch):
    """Onto another disk a link in the build is never followed and not copied: the result says what was left out and
    why, and the build folder's copy stays, so nothing the new copy does not hold is removed."""
    monkeypatch.setattr(ao, "_device", lambda path: 1 if str(path).startswith(str(tmp_path / "builds")) else 2)
    folder = finished_build(tmp_path / "builds")
    (tmp_path / "elsewhere.json").write_text("{}")
    (folder / "tokenizer.json").symlink_to(tmp_path / "elsewhere.json")
    (tmp_path / "disk2").mkdir()
    result = ao.save_artifact(fw, folder, tmp_path / "disk2")
    assert (result.folder / "m.gguf").read_bytes() == b"weights" and not (result.folder / "tokenizer.json").exists()
    assert any("tokenizer.json" in note and "link" in note for note in result.notes), result.notes
    assert (folder / "tokenizer.json").is_symlink() and (folder / "m.gguf").is_file(), "the build folder keeps it"
    assert (folder.parent / "Q4_K_M.zip").is_file()
    from orchestrator.gui.artifact_save import result_text
    text = result_text(result, "en")
    assert result.kept and "tokenizer.json" in text and "stays" in text and "was removed" not in text


def test_an_output_folder_that_holds_the_frameworks_own_folders_is_refused(fw, tmp_path):
    """Everything below the output folder is listed: one that holds the build folder or the per-user folder (for
    example the home folder) would list and label the framework's own folders, so it is refused. A single model may
    still be saved there."""
    fw.config.save_user_config = lambda: True
    fw.config.values["build_dir"] = str(tmp_path / "disk" / "lcf" / "builds")
    with pytest.raises(ao.DestinationRefused, match="build folder"):
        ao.set_output_folder(fw, tmp_path / "disk")
    home = tmp_path / "users" / "me"
    fw.config.user_state_dir = str(home / ".llm-framework")
    Path(fw.config.user_state_dir).mkdir(parents=True)
    with pytest.raises(ao.DestinationRefused, match="per-user folder"):
        ao.set_output_folder(fw, home)
    assert fw.config.get("output_dir") == str(tmp_path / "out"), "unchanged"
    assert ao.refusal(fw, home) == "", "a single model may be saved there"
    assert ao.set_output_folder(fw, home / "llmcf" / "output") == home / "llmcf" / "output"


def test_only_a_build_waiting_in_the_build_folder_is_saved(fw, tmp_path):
    saved = finished_build(tmp_path / "out")
    with pytest.raises(ao.DestinationRefused, match="build folder"):
        ao.save_artifact(fw, saved, tmp_path / "elsewhere", create=True)
    half = tmp_path / "builds" / "half"
    half.mkdir(parents=True)
    with pytest.raises(ao.DestinationRefused, match="not a finished build"):
        ao.save_artifact(fw, half, tmp_path / "out")


# --- listings: earlier builds stay listed; an AI reads labels -------------------------------------------------------

def test_every_folder_is_listed_once_and_an_ai_reads_labels(fw, tmp_path):
    finished_build(tmp_path / "builds", "Rockchip/old/Q4_0")
    finished_build(tmp_path / "out", "Rockchip/new/Q4_0")
    service = BuildService(fw)
    labels = sorted(a.label for a in service.list_artifacts().artifacts)
    assert labels == ["builds:Rockchip/old/Q4_0", "output:Rockchip/new/Q4_0"]
    ids = {a.artifact_id for a in service.list_artifacts().artifacts}
    assert len(ids) == 2
    for item in service.list_artifacts().artifacts:
        assert str(tmp_path) not in item.label


def test_builds_in_an_earlier_output_folder_stay_listed(fw, tmp_path):
    """The person moves the output folder to another disk: what is in the old one stays there and stays listed."""
    fw.config.save_user_config = lambda: True
    finished_build(tmp_path / "out", "Rockchip/old/Q4_0")
    assert ao.set_output_folder(fw, tmp_path / "d-drive") == tmp_path / "d-drive"
    finished_build(tmp_path / "d-drive", "Rockchip/new/Q4_0")
    listed = {a.label: a.where for a in ao.list_artifacts(fw)}
    assert listed == {"output:Rockchip/new/Q4_0": "output", "saved:Rockchip/old/Q4_0": "saved"}
    assert (tmp_path / "out" / "Rockchip" / "old" / "Q4_0" / "m.gguf").is_file(), "nothing was moved or deleted"
    fw.config.save_user_config = lambda: False
    with pytest.raises(OSError):
        ao.set_output_folder(fw, tmp_path / "e-drive")
    assert fw.config.get("output_dir") == str(tmp_path / "d-drive"), "not saved: unchanged"


def test_the_build_status_names_artifacts_by_label(fw, tmp_path):
    from datetime import datetime

    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    folder = finished_build(tmp_path / "out")
    state = WorkflowState("r1", OrchestrationStatus.COMPLETED, datetime.now(), artifacts=[str(folder),
                                                                                         "/elsewhere/x/model.gguf"])
    out = BuildService(fw)._status_of(state)
    assert out.artifacts == ["output:Rockchip/m/Q4_K_M", "model.gguf"]


def test_the_build_log_an_ai_reads_names_the_output_folder_by_label(fw, tmp_path, monkeypatch):
    """The output folder lies in the home folder (with the user name in its path); the log reaches AI callers only."""
    from datetime import datetime

    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    home = tmp_path / "home" / "jane"
    monkeypatch.setenv("HOME", str(home))
    fw.config.values["output_dir"] = str(home / "llmcf" / "output")
    out = home / "llmcf" / "output" / "Rockchip" / "m"
    logs = [f"Golden Artifact created: {out / 'Q4_K_M-2.zip'}", f"An earlier build is in {out / 'Q4_K_M'}",
            f"cached in {home / 'other' / 'x.bin'}", "CONT: /workspace/build/llama-quantize done"]
    state = WorkflowState("r1", OrchestrationStatus.COMPLETED, datetime.now(), build_ids=["b1"])
    engine = SimpleNamespace(get_build_status=lambda bid: SimpleNamespace(logs=logs))
    fw.orchestrator = SimpleNamespace(_workflows={"r1": state}, build_engine=engine)
    lines = BuildService(fw).log_tail("r1").lines
    assert lines == ["Golden Artifact created: output:Rockchip/m/Q4_K_M-2.zip",
                     "An earlier build is in output:Rockchip/m/Q4_K_M", "cached in x.bin",
                     "CONT: /workspace/build/llama-quantize done"], "a line without a host path stays as it is"
    assert not any("jane" in line for line in lines)
    # The Builder's live log is the person's own window (N8): the full paths, as for every other question and
    # result the person reads (round 5 f); only secrets are redacted there too
    person = BuildService(fw).log_tail("r1", for_person=True).lines
    assert person[:3] == [f"Golden Artifact created: {out / 'Q4_K_M-2.zip'}",
                          f"An earlier build is in {out / 'Q4_K_M'}", f"cached in {home / 'other' / 'x.bin'}"]


def test_the_build_log_names_no_user_from_paths_outside_the_home_folder(fw, tmp_path, monkeypatch):
    """A local model on a removable disk (/media/<user>/..., /run/media/<user>/...) or in another user's folder is
    named by its file name in the log an AI reads, like every other host path."""
    from datetime import datetime

    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "me"))
    logs = ["Using local model /media/jane/USB/models/x.gguf", "Using local model /run/media/jane/disk/y.gguf",
            "Using local model /home/bob/models/z.gguf", "Using local model /Users/bob/m.gguf",
            "Using local model C:\\Users\\bob\\w.gguf", "CONT: /workspace/build/llama-quantize done"]
    state = WorkflowState("r1", OrchestrationStatus.COMPLETED, datetime.now(), build_ids=["b1"])
    engine = SimpleNamespace(get_build_status=lambda bid: SimpleNamespace(logs=logs))
    fw.orchestrator = SimpleNamespace(_workflows={"r1": state}, build_engine=engine)
    lines = BuildService(fw).log_tail("r1").lines
    assert lines == ["Using local model x.gguf", "Using local model y.gguf", "Using local model z.gguf",
                     "Using local model m.gguf", "Using local model w.gguf",
                     "CONT: /workspace/build/llama-quantize done"]


def test_the_gui_commit_look_up_asks_nobody_offline_or_for_a_local_model(fw, monkeypatch):
    import huggingface_hub
    asked = []

    class Api:
        def model_info(self, repo, token=None, timeout=None):
            asked.append((repo, timeout))
            return SimpleNamespace(sha="c" * 40)

    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    service = BuildService(fw)
    assert service.model_commit("org/model") == "c" * 40 and asked == [("org/model", 10)]
    assert service.model_commit("/models/local.gguf") is None
    fw.config.values["offline_mode"] = True
    assert service.model_commit("org/model") is None and len(asked) == 1, "offline mode: Hugging Face is not asked"


def test_a_rebuild_never_replaces_an_earlier_build(tmp_path):
    """The builder publishes next to an earlier finished build ("-2"); the person's earlier artifact stays whole."""
    from orchestrator.Core.builder import BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, ModelFormat
    out = tmp_path / "out" / "Rockchip" / "m" / "Q4_K_M"
    earlier = finished_build(tmp_path / "out", payload=b"earlier")
    engine = BuildEngine.__new__(BuildEngine)
    engine._records = {"b2": {"output_dir": str(out)}}
    config = BuildConfiguration(build_id="b2", timestamp="t", model_source="org/m", target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir=str(out))
    staged = engine._staged_output(config)
    staged.mkdir(parents=True)
    (staged / "m.gguf").write_bytes(b"new")
    (staged / "build_info.json").write_text("{}")
    (engine._staging_root(config) / "Q4_K_M.zip").write_bytes(b"newzip")
    progress = BuildProgress("b2", BuildStatus.PACKAGING, "", artifacts=[str(out / "m.gguf"),
                                                                       str(out.parent / "Q4_K_M.zip")])
    engine._publish_artifacts(config, progress)
    assert (earlier / "m.gguf").read_bytes() == b"earlier" and (earlier.parent / "Q4_K_M.zip").read_bytes() == b"zip"
    second = out.parent / "Q4_K_M-2"
    assert (second / "m.gguf").read_bytes() == b"new" and (out.parent / "Q4_K_M-2.zip").read_bytes() == b"newzip"
    assert progress.published == str(second) and engine._records["b2"]["output_dir"] == str(second)
    assert progress.artifacts == [str(second / "m.gguf"), str(out.parent / "Q4_K_M-2.zip")]


# --- the CLI ------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(booted_framework, tmp_path, monkeypatch)


def test_cli_asks_at_a_terminal_with_the_output_folder_as_default(cli, monkeypatch, tmp_path):
    from orchestrator import cli as cli_module
    asked = []
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    monkeypatch.setattr(cli_module.click, "prompt", lambda text, default=None, **k: asked.append(default) or
                        str(tmp_path / "big-disk"))
    result = cli.invoke("build", "start", "-m", "HuggingFaceTB/SmolLM2-135M-Instruct", "-t", "Rockchip")
    assert result.code == 0, result.out
    assert asked == [str(Path.home() / "llmcf" / "output")]
    assert cli.submitted[0].output_base_dir == str(tmp_path / "big-disk")


def test_cli_build_save_and_output_folder(cli, tmp_path):
    builds = Path(cli.framework.info.installation_path) / "output"
    folder = finished_build(builds, "Rockchip/s/Q4_0")
    shown = cli.invoke("config", "output-folder")
    assert shown.code == 0 and str(Path.home() / "llmcf" / "output") in "".join(shown.out.split())
    refused = cli.invoke("config", "output-folder", "/etc")
    assert refused.code == 2 and "system folder" in refused.out
    assert cli.invoke("config", "output-folder", str(tmp_path / "models-d")).code == 0
    assert cli.framework.config.get("output_dir") == str(tmp_path / "models-d")
    saved = cli.invoke("build", "save", "Rockchip/s/Q4_0")
    assert saved.code == 0, saved.out
    assert (tmp_path / "models-d" / "Rockchip" / "s" / "Q4_0" / "m.gguf").is_file() and not folder.exists()
    missing = cli.invoke("build", "save", "Rockchip/s/Q4_0")
    assert missing.code == 2 and "No finished build" in missing.out


# --- the GUI ------------------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
class TestSaveDialog:

    def wait(self, worker):
        assert worker.wait(10000)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def test_ok_saves_where_the_dialog_points_and_cancel_keeps_it(self, fw, tmp_path, monkeypatch):
        from orchestrator.gui import artifact_save
        folder = finished_build(tmp_path / "builds")
        asked, done = [], []
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: asked.append(start) or "")
        assert artifact_save.ask_and_save(None, fw, folder, lambda ok, text: done.append((ok, text))) is None
        assert asked == [str(tmp_path / "out")], "the native dialog opens at the output folder"
        assert (tmp_path / "out").is_dir(), "made first, so the dialog can open there"
        assert done[0][0] is False and "stays in the framework's build folder" in done[0][1] and folder.is_dir()
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: start)
        worker = artifact_save.ask_and_save(None, fw, folder, lambda ok, text: done.append((ok, text)))
        self.wait(worker)
        assert done[-1][0] is True and str(tmp_path / "out" / "Rockchip" / "m" / "Q4_K_M") in done[-1][1]
        assert not folder.exists()

    def test_when_the_output_folder_is_the_build_folder_the_dialog_still_opens(self, fw, tmp_path, monkeypatch):
        """For every golden artifact the dialog opens; OK at the output folder keeps the build where it is."""
        from orchestrator.gui import artifact_save
        fw.config.values["build_dir"] = str(tmp_path / "out")
        folder = finished_build(tmp_path / "out")
        asked, done = [], []
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: asked.append(start) or start)
        assert artifact_save.ask_and_save(None, fw, folder, lambda ok, text: done.append((ok, text))) is None
        assert asked == [str(tmp_path / "out")] and done[0][0] is True and "in the output folder" in done[0][1]
        assert (folder / "m.gguf").is_file() and not (tmp_path / "out" / "Rockchip" / "m" / "Q4_K_M-2").exists()
        (tmp_path / "disk2").mkdir()
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: str(tmp_path / "disk2"))
        worker = artifact_save.ask_and_save(None, fw, folder, lambda ok, text: done.append((ok, text)))
        self.wait(worker)
        assert done[-1][0] is True and (tmp_path / "disk2" / "Rockchip" / "m" / "Q4_K_M" / "m.gguf").is_file()

    def test_a_refused_folder_moves_nothing(self, fw, tmp_path, monkeypatch):
        from orchestrator.gui import artifact_save
        folder = finished_build(tmp_path / "builds")
        done = []
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: str(tmp_path / "state"))
        worker = artifact_save.ask_and_save(None, fw, folder, lambda ok, text: done.append((ok, text)))
        self.wait(worker)
        assert done[0][0] is False and "per-user folder" in done[0][1] and folder.is_dir()

    def test_the_output_folder_menu_sets_the_folder_once(self, fw, tmp_path, monkeypatch):
        from orchestrator.gui import artifact_save
        from orchestrator.gui.main_window import MainOrchestrator
        saved, messages = [], []
        fw.config.save_user_config = lambda: saved.append(True) or True
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: str(tmp_path / "d-drive"))
        window = SimpleNamespace(framework=fw, status=SimpleNamespace(showMessage=lambda t, *a: messages.append(t)))
        MainOrchestrator._choose_output_folder(window)
        assert fw.config.get("output_dir") == str(tmp_path / "d-drive") and saved == [True]
        assert "old folder stay where they are" in messages[0]

    def test_every_golden_artifact_of_a_finished_gui_build_gets_the_dialog(self, fw, tmp_path, monkeypatch):
        from orchestrator.gui import artifact_save
        from orchestrator.gui.builder_tab import BuilderTab
        first = finished_build(tmp_path / "builds", "Rockchip/m/Q4_K_M")
        second = finished_build(tmp_path / "builds", "Rockchip/m/Q8_0")
        asked, logged = [], []
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: asked.append(title) or "")
        fw.orchestrator = SimpleNamespace(_workflows={"r1": SimpleNamespace(artifacts=[str(first), str(second)])})
        tab = SimpleNamespace(framework=fw, _save_workers=[], _on_saved=lambda ok, text: logged.append(text))
        BuilderTab._offer_save(tab, "r1")
        assert len(asked) == 2 and "Q4_K_M" in asked[0] and "Q8_0" in asked[1]
        assert all("stays in the framework's build folder" in t for t in logged) and first.is_dir() and second.is_dir()

    def test_the_person_sees_where_an_ai_build_was_saved(self, tmp_path):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        from orchestrator.gui.main_window import MainOrchestrator
        messages = []
        window = SimpleNamespace(status=SimpleNamespace(showMessage=lambda t, *a: messages.append(t)))
        folder = str(tmp_path / "out" / "Rockchip" / "m" / "Q4_K_M")
        flows = [SimpleNamespace(status=OrchestrationStatus.COMPLETED, owner="mcp#s1", artifacts=[folder],
                                 request_id="r1"),
                 SimpleNamespace(status=OrchestrationStatus.COMPLETED, owner="", artifacts=["/x"], request_id="r2")]
        MainOrchestrator._announce_saved_builds(window, flows)
        MainOrchestrator._announce_saved_builds(window, flows)
        assert messages == [f"A build an AI started has finished and is saved in: {folder}"], "once, full path"


def test_a_full_disk_names_the_build_folder_too(fw, tmp_path):
    """GUI builds wait in the build folder until they are saved: a full disk lists it by label, once."""
    from orchestrator.Core.self_healing_manager import SelfHealingManager
    manager = SelfHealingManager.__new__(SelfHealingManager)
    manager.framework = fw
    assert [label for label, _ in manager._folders()] == ["model cache", "output folder", "build folder"]
    fw.config.values["build_dir"] = fw.config.values["output_dir"]
    assert [label for label, _ in manager._folders()] == ["model cache", "output folder"]


def test_only_the_person_changes_the_output_and_build_folders():
    """An AI reads the folders as labels and never changes them (update_settings offers neither)."""
    from orchestrator.tools import tools_status
    assert tools_status.FOLDER_KEYS["output_dir"] == "output" and tools_status.FOLDER_KEYS["build_dir"] == "builds"
    assert "output_dir" not in tools_status.WRITABLE and "build_dir" not in tools_status.WRITABLE
