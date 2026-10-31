"""A deletion the person accepted says "done" only when it is true: when anything stays, the result says so plainly
("kept: could not remove N items", relative names only, which may reach an AI) and the person's text (the GUI's
result box, the CLI's lines: deletion.kept_lines) names every path that stayed so the person can delete it by hand.

Removals are made to fail by a remove_tree that leaves a file behind (the way a file open on Windows or on a
read-only mount stays), so the tests behave the same on Linux and Windows."""

import json
from pathlib import Path

import pytest

from orchestrator.Core import deletion
from orchestrator.tools.spec import DeletionItem, DeletionPlan


def _stuck_remove_tree(stuck_name="stuck.bin"):
    """A remove_tree that removes nothing and reports one file left in the folder."""
    def remove_tree(path, *, label=""):
        path = Path(path)
        return [path / stuck_name] if path.is_dir() else [path]
    return remove_tree


def _plan(items, modes=("permanent",)):
    return DeletionPlan(items=items, mode_offered=modes, total_bytes=sum(i.size_bytes for i in items))


class _Audit:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def _folder(tmp_path, *parts):
    folder = tmp_path.joinpath(*parts)
    folder.mkdir(parents=True)
    (folder / "stuck.bin").write_bytes(b"x")
    return folder


def _person_text(result):
    return "\n".join(deletion.kept_lines(result, "en"))


# --- the one helper ----------------------------------------------------------------------------------------------

def test_the_kept_reason_names_relative_paths_only(tmp_path):
    root = tmp_path / "account-name-x" / "builds" / "b1"
    reason = deletion.kept_reason(root, [root / "lib" / "a.so", root / "b.txt"])
    assert reason.startswith("kept: could not remove 2 items")
    assert "lib/a.so" in reason and "b.txt" in reason
    assert str(tmp_path) not in reason and "account-name-x" not in reason
    assert deletion.kept_reason(root, [root]).startswith("kept: could not remove 1 item (the item itself")


@pytest.mark.parametrize("lang", ["en", "de"])
def test_kept_lines_name_the_paths_for_the_person(tmp_path, lang):
    result = deletion.DeletionResult()
    root = tmp_path / "folder"
    assert deletion.note_kept(result, "Old build", root, [root / "x.bin"]) is True
    assert deletion.note_kept(result, "Other", root, []) is False
    assert result.failed == [("Old build", deletion.kept_reason(root, [root / "x.bin"]))]
    lines = deletion.kept_lines(result, lang)
    assert any("Old build" in line for line in lines)
    assert any(str(root / "x.bin") in line for line in lines), lines


# --- Ditto's mode switch -----------------------------------------------------------------------------------------

def test_the_mode_switch_never_reports_a_build_folder_that_stayed_as_deleted(tmp_path, monkeypatch):
    from orchestrator.runtime import runtime_mode
    from orchestrator.runtime.build import source
    root = tmp_path / "runtime"
    build = _folder(root, "builds", "abcdef0123456789")
    forgotten = []
    store = type("Store", (), {"running_build_ids": lambda self: [], "forget": lambda self, n: forgotten.append(n)})()
    rt = type("Rt", (), {"root": root, "store": store})()
    item = DeletionItem(speaking_name="Ditto's build abcdef01", full_path=str(build), file_name=build.name,
                        size_bytes=1, what="a build", kind="runtime_build")
    monkeypatch.setattr(source, "remove_tree", _stuck_remove_tree())

    kept = runtime_mode._remove_item(rt, item, "bare-metal")

    assert kept is not None, "a build folder that stayed was reported as deleted"
    assert kept.startswith("kept: could not remove 1 item") and str(tmp_path) not in kept
    assert not forgotten, "the store forgot a build whose folder is still there"
    remaining = {}
    runtime_mode._remove_item(rt, item, "bare-metal", remaining)
    assert remaining == {item.speaking_name: [str(build / "stuck.bin")]}


def test_the_mode_switch_result_carries_the_paths_for_the_person(tmp_path):
    from orchestrator.runtime.runtime_mode import Removal
    removal = Removal()
    deletion.note_kept(removal, "Ditto's build", tmp_path / "b", [tmp_path / "b" / "stuck.bin"])
    assert removal.failed and not removal.deleted
    assert str(tmp_path / "b" / "stuck.bin") in _person_text(removal)


# --- the other person-accepted deletions -------------------------------------------------------------------------

def test_a_dependency_folder_that_stayed_is_kept_with_its_path(tmp_path, monkeypatch):
    from orchestrator.runtime.build import deps
    cache = deps.DependencyCache(tmp_path / "cache")
    folder = _folder(cache.root, "boringssl", "abc")
    item = DeletionItem(speaking_name="BoringSSL abc", full_path=str(folder), file_name="abc", size_bytes=1,
                        what="a build dependency", kind="build_dependency")
    plan = _plan([item], ("trash", "permanent"))
    monkeypatch.setattr(cache, "deletion_plan", lambda: plan)
    monkeypatch.setattr("orchestrator.utils.atomic.remove_tree", _stuck_remove_tree())

    result = cache.delete(deletion.accept(plan, "cli", "permanent"), audit=_Audit())

    assert item.speaking_name not in result.done, "a folder that stayed was reported as deleted"
    assert result.failed and result.failed[0][1].startswith("kept: could not remove 1 item")
    assert str(folder / "stuck.bin") in _person_text(result)


def test_an_old_huggingface_copy_that_stayed_names_its_path(tmp_path, monkeypatch):
    from orchestrator.Core import hf_takeover
    models = tmp_path / "models"
    folder = _folder(models, "hub-copy")
    item = DeletionItem(speaking_name="old copy of a model", full_path=str(folder), file_name=folder.name,
                        size_bytes=1, what="an old copy", kind="model_file")
    plan = _plan([item], ("trash", "permanent"))
    monkeypatch.setattr(hf_takeover, "old_copies_plan", lambda models_dir: plan)
    monkeypatch.setattr("orchestrator.rag.storage_lock.remove_tree", _stuck_remove_tree())

    result = hf_takeover.delete_old_copies(models, deletion.accept(plan, "cli", "permanent"), audit=_Audit())

    assert not result.done
    assert result.failed[0][1].startswith("kept: could not remove 1 item"), result.failed
    assert str(folder / "stuck.bin") in _person_text(result)


def test_a_trash_entry_that_stayed_is_kept_with_its_path(tmp_path, monkeypatch):
    from orchestrator.Core import trash as trash_module
    from orchestrator.Core.trash import Trash
    state = tmp_path / "state"
    source = _folder(state, "models", "m1")
    box = Trash(state, audit=_Audit())
    item = DeletionItem(speaking_name="m1", full_path=str(source), file_name="m1", size_bytes=1, what="a model",
                        kind="model_file")
    box.move("model_file", source, state, "state", "m1", deletion.accept(_plan([item], ("trash",)), "cli", "trash"),
             "cli")
    plan = box.plan()
    monkeypatch.setattr(trash_module, "remove_tree", _stuck_remove_tree(), raising=False)

    result = box.empty(deletion.accept(plan, "cli", "permanent"))

    assert not result.deleted, "a trash entry that stayed was reported as deleted"
    assert result.failed[0][1].startswith("kept: could not remove 1 item")
    assert str(tmp_path) not in json.dumps(result.failed)
    assert str(tmp_path) in _person_text(result)
    assert box.entries(), "the entry stays listed while its files are there"


# --- the person's channels: the CLI and the GUI show the same lines ----------------------------------------------

def _kept_removal(tmp_path):
    from orchestrator.runtime.runtime_mode import Removal
    removal = Removal()
    deletion.note_kept(removal, "Ditto's build", tmp_path / "b", [tmp_path / "b" / "stuck.bin"])
    return removal


def test_the_cli_names_every_path_that_stayed(tmp_path, monkeypatch):
    from orchestrator import cli
    from test_cli_builds import quiet_console
    printed = quiet_console(monkeypatch)
    removal = _kept_removal(tmp_path)
    cli._runtime_removed(removal)
    out = printed.getvalue()
    assert "could not remove 1 item" in out and str(tmp_path / "b" / "stuck.bin") in out
    assert "0 deleted for good" in out


def test_the_gui_names_the_same_paths(tmp_path):
    pytest.importorskip("PySide6")
    from orchestrator.gui.runtime_mode_box import RuntimeModeBox
    removal = _kept_removal(tmp_path)
    text = RuntimeModeBox._removed_text(None, removal)
    for line in deletion.kept_lines(removal):
        assert line in text
    assert str(tmp_path / "b" / "stuck.bin") in text
