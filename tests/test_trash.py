"""The trash (delegation design 3.10, round 5 f; WP7).

A deletion the person accepted in the mode "trash" moves the item into the trash of its data folder with one rename;
the space stays used and is never counted as free; nothing leaves the trash by itself; only the person empties it,
with the full list; an AI restores only what it asked to delete itself and never reaches the trash folders.
"""

import errno
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import POSIX_MODES_ONLY
from orchestrator.Core import trash as trash_module
from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept
from orchestrator.Core.disk_plan import DiskCheck, DiskNeed, InsufficientSpace, ReservationLedger, check_disk, require
from orchestrator.Core.knowledge_snapshots import delete_snapshots, snapshot_plan
from orchestrator.Core.trash import Trash, TrashError, restored_name, trash_folders
from orchestrator.tools.registry import ToolContext, ToolError

REPO = Path(__file__).resolve().parent.parent


class Audit:
    def __init__(self, fails=False):
        self.entries = []
        self.fails = fails

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fails:
            raise OSError("disk full")
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


def snapshots(state, *names, size=4):
    folder = state / "knowledge_snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        path = folder / f"{name}.jsonl.gz"
        path.write_bytes(bytes([65 + i]) * size)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    return folder


def trashed(state, *names, actor="gui", trash=None):
    """Moves the snapshots `names` to the trash as the person accepted it; returns (trash, entries)."""
    folder = snapshots(state, *names)
    trash = trash or Trash(state, audit=Audit())
    result = delete_snapshots(folder, accept(snapshot_plan(folder, names), actor, "trash"), trash=trash,
                              audit=Audit())
    assert result.done == list(names)
    return trash, trash.entries()


@POSIX_MODES_ONLY
def test_the_trash_folder_is_private(tmp_path):
    state = tmp_path / "state"
    trashed(state, "pre_ingest_1", "pre_ingest_2")
    assert oct((state / ".trash").stat().st_mode & 0o777) == "0o700"


def test_delete_moves_to_trash(tmp_path):
    state = tmp_path / "state"
    trash, entries = trashed(state, "pre_ingest_1", "pre_ingest_2")
    assert not (state / "knowledge_snapshots" / "pre_ingest_1.jsonl.gz").exists()
    for entry in entries:
        assert entry.stored().is_file() and entry.stored().parent.parent == state / ".trash"
        assert entry.origin == f"state:knowledge_snapshots/{entry.name}" and entry.kind == "knowledge_snapshot"
        assert entry.deleted_by == "gui" and entry.confirmed_by == "gui" and entry.size == 4 and entry.sha256
        assert datetime.fromisoformat(entry.remind_at) - datetime.fromisoformat(entry.deleted_at) == \
            timedelta(days=14)
    # The index is signed: a hand edit (or a forged "deleted_by") makes it unusable, nothing is restored from it.
    raw = json.loads((state / "tool_trash.json").read_text())
    raw["entries"][0]["deleted_by"] = "mcp:laptop"
    (state / "tool_trash.json").write_text(json.dumps(raw))
    with pytest.raises(TrashError, match="cannot be used"):
        trash.restore(entries[0].id, "mcp:laptop")
    assert entries[0].stored().is_file()


def test_a_move_to_another_disk_is_refused_never_copied(tmp_path, monkeypatch):
    state = tmp_path / "state"
    folder = snapshots(state, "s1")
    real_rename = os.rename

    def cross_device(src, dst):
        if ".trash" in str(dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst)
    monkeypatch.setattr(trash_module.os, "rename", cross_device)
    trash = Trash(state, audit=Audit())
    result = delete_snapshots(folder, accept(snapshot_plan(folder), "cli", "trash"), trash=trash, audit=Audit())
    assert result.done == [] and "another disk" in result.failed[0][1]
    assert (folder / "s1.jsonl.gz").is_file() and trash.entries() == []
    assert list((state / ".trash").iterdir()) == [], "no copy, no empty entry folder"


def test_restore_and_collision(tmp_path):
    state = tmp_path / "state"
    trash, entries = trashed(state, "s1", "s2")
    first, second = entries
    assert trash.restore(first.id, "gui") == state / "knowledge_snapshots" / "s1.jsonl.gz"
    assert (state / "knowledge_snapshots" / "s1.jsonl.gz").read_bytes() == b"AAAA"
    assert not first.stored().parent.exists() and [e.id for e in trash.entries()] == [second.id]
    # The place is taken again: the restored file comes next to it with a new name; nothing is overwritten.
    (state / "knowledge_snapshots" / "s2.jsonl.gz").write_bytes(b"new")
    dest = trash.restore(second.id, "cli")
    assert dest.name.startswith("s2-restored-") and dest.name.endswith(".jsonl.gz") and dest.read_bytes() == b"BBBB"
    assert (state / "knowledge_snapshots" / "s2.jsonl.gz").read_bytes() == b"new"
    assert restored_name("model.tar.gz", "x") == "model-restored-x.tar.gz" and restored_name("dir", "x") == \
        "dir-restored-x"
    with pytest.raises(TrashError) as exc:
        trash.restore(second.id, "gui")
    assert exc.value.code == "trash_unknown"
    assert [e["outcome"] for e in trash.audit.entries] == ["trash_restored", "trash_restored"]


def test_restore_never_follows_a_link(tmp_path):
    state = tmp_path / "state"
    trash, entries = trashed(state, "s1")
    folder = state / "knowledge_snapshots"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    folder.rmdir()
    folder.symlink_to(elsewhere)
    with pytest.raises(TrashError, match="link"):
        trash.restore(entries[0].id, "gui")
    assert list(elsewhere.iterdir()) == [] and entries[0].stored().is_file()


def test_trash_not_counted_free(tmp_path):
    """What lies in the trash still uses its disk: never counted as free; a refusal names the trash when emptying it
    would make room."""
    check = DiskCheck(["cache"], needed=10 * 10 ** 9, free=11 * 10 ** 9, total=100 * 10 ** 9, reserve=2 * 10 ** 9,
                      reserved_by_others=0, device="1")
    before = (check.after, check.fits)
    check.trash_bytes = 50 * 10 ** 9
    assert (check.after, check.fits) == before == (10 ** 9, False), "the trash never counts as free"
    assert check.fits_after_emptying_the_trash and "more after emptying the trash" in check.sentence()
    with pytest.raises(InsufficientSpace) as exc:
        require([check])
    assert "The trash holds 50.0 GB there: emptying it would make room" in str(exc.value)
    assert "only the person empties it" in str(exc.value)
    # check_disk fills it from the trash of the ledger's per-user folder.
    state = tmp_path / "state"
    trashed(state, "s1", "s2")
    usage = SimpleNamespace(free=10 ** 12, total=2 * 10 ** 12)
    checks = check_disk([DiskNeed("state", state, 1, "x")], ledger=ReservationLedger(state),
                        disk_usage=lambda path: usage)
    assert checks[0].trash_bytes == 8 and checks[0].free == 10 ** 12


def test_no_tool_empties_trash(booted_framework):
    """No tool empties the trash or deletes for good; the AI is told where the person does it."""
    from orchestrator.tools.framework_tools import NO_TOOL_ACTIONS
    names = {s.name for s in booted_framework.tool_registry.list()}
    assert not {n for n in names if "empty" in n or "purge" in n}
    action = dict(NO_TOOL_ACTIONS)["empty_trash"]
    assert "File > Trash..." in action and "llm-cli trash empty" in action and "no tool does" in action
    sources = [p.read_text(encoding="utf-8") for p in (REPO / "orchestrator" / "tools").glob("*.py")]
    sources += [(REPO / "orchestrator" / "mcp_server.py").read_text(encoding="utf-8")]
    sources += [p.read_text(encoding="utf-8") for p in (REPO / "orchestrator" / "llm").rglob("*.py")]
    assert not any(".empty(" in s or "delete_snapshots" in s for s in sources), "no AI path reaches them"


def run_cli(framework, monkeypatch, *args, terminal=False, answer=None):
    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=400, color_system=None))
    monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
    result = CliRunner().invoke(cli_module.cli, list(args), obj=Ctx(), input=answer)
    return result, printed.getvalue() + result.output


def test_cli_empty_needs_terminal(booted_framework, monkeypatch):
    state = Path(booted_framework.config.user_state_dir)
    trash = Trash.for_framework(booted_framework)
    _, entries = trashed(state, "s1", "s2", trash=trash)
    _, out = run_cli(booted_framework, monkeypatch, "trash", "empty", "--all")
    assert "Nothing was deleted: deleting needs your answer at a terminal" in out
    assert str(entries[0].stored()) in out and "knowledge snapshot moved to the trash" in out
    assert len(trash.entries()) == 2
    _, out = run_cli(booted_framework, monkeypatch, "trash", "empty", "--all", terminal=True, answer="\n")
    assert "Nothing was deleted." in out and len(trash.entries()) == 2, "nothing is ticked by default"
    _, out = run_cli(booted_framework, monkeypatch, "trash", "empty", "--all", terminal=True, answer="2\n\n")
    assert "Delete exactly 1 items" in out and "Nothing was deleted." in out and len(trash.entries()) == 2
    _, out = run_cli(booted_framework, monkeypatch, "trash", "empty", "--all", terminal=True, answer="2\ny\n")
    assert "Deleted for good: s2" in out and [e.id for e in trash.entries()] == [entries[0].id]
    assert not entries[1].stored().exists()
    audit = booted_framework.tool_registry.audit.entries(20)
    assert any(e["outcome"] == "trash_emptied" for e in audit)
    result, out = run_cli(booted_framework, monkeypatch, "trash", "restore", entries[0].id)
    assert result.exit_code == 0 and "Restored" in out and trash.entries() == []
    _, out = run_cli(booted_framework, monkeypatch, "trash")
    assert "The trash is empty." in out


def test_expiry_never_deletes_without_the_person(tmp_path, monkeypatch):
    state = tmp_path / "state"
    trash, entries = trashed(state, "s1", "s2")
    later = datetime.now() + timedelta(days=400)
    assert [e.id for e in trash.due(later)] == [e.id for e in entries]
    for call in (trash.entries, trash.total_bytes, trash.bytes_by_device, lambda: trash.due(later), trash.plan):
        call()
    assert all(e.stored().is_file() for e in entries), "a reminder date never deletes anything"
    due_day = datetime.now().replace(microsecond=0) + timedelta(days=15)
    monkeypatch.setattr(trash_module, "_now", lambda: due_day)   # the person answers the reminder: keep longer
    assert trash.remind_later([entries[0].id], "gui") == 1
    assert [e.id for e in trash.due(due_day + timedelta(days=5))] == [entries[1].id]
    with pytest.raises(PermissionError):
        trash.remind_later([entries[0].id], "mcp")
    with pytest.raises(PlanChanged):   # an accepted list that no longer matches deletes nothing
        plan = trash.plan()
        entries[0].stored().write_bytes(b"changed!")
        trash.empty(accept(plan, "gui"))
    assert all(e.stored().is_file() for e in entries)
    with pytest.raises(RuntimeError, match="nothing was deleted"):
        Trash(state, audit=Audit(fails=True)).empty(accept(trash.plan(), "gui"))
    assert all(e.stored().is_file() for e in entries)


def test_trash_is_unreachable_by_label_paths(tmp_path):
    """Every trash folder is a denied folder for AI callers: by identity, also through a link, also when the person
    shared the data folder it lies in."""
    from orchestrator.tools.local_files import LocalFileError, LocalFiles, denied_folders
    state = tmp_path / "state"
    models = tmp_path / "models"
    (models / "m").mkdir(parents=True)
    trash = Trash(state, audit=Audit())
    from orchestrator.Core.trash import TRASH_FOLDER
    (models / "m" / "model.gguf").write_bytes(b"GGUF")
    path = models / "m" / "model.gguf"
    plan_item = __import__("orchestrator.tools.spec", fromlist=["DeletionItem"]).DeletionItem(
        kind="model_file", speaking_name="model.gguf", full_path=str(path), file_name="model.gguf", size_bytes=4,
        what="model file")
    from orchestrator.Core.deletion import plan_of
    entry = trash.move("model_file", path, models, "models", "model.gguf",
                       accept(plan_of([plan_item], ("trash",)), "gui"))
    assert entry.stored().parent.parent == models / TRASH_FOLDER
    assert models / TRASH_FOLDER in trash_folders(state) and state / TRASH_FOLDER in trash_folders(state)
    denied = {d.path for d in denied_folders(state)}
    assert models / TRASH_FOLDER in denied
    files = LocalFiles(state, {"models": models, "datasets": tmp_path / "datasets"})
    with pytest.raises(LocalFileError, match="trash"):
        files.resolve(f"models:{TRASH_FOLDER}/{entry.id}/model.gguf")
    (models / "via-link").symlink_to(models / TRASH_FOLDER / entry.id)
    with pytest.raises(LocalFileError):
        files.resolve("models:via-link/model.gguf")
    entries, _, _ = files.list()
    assert all(TRASH_FOLDER not in e["ref"] and "via-link" not in e["ref"] for e in entries)


def test_build_listings_skip_the_trash(tmp_path):
    from orchestrator.Core.deployment_manager import list_builds
    out = tmp_path / "output"
    for folder in (out / "m", out / ".trash" / "tr_0123456789abcdef" / "m"):
        folder.mkdir(parents=True)
        (folder / "build_info.json").write_text(json.dumps({"build_id": folder.parent.name}))
    assert [b["path"] for b in list_builds(out)] == [str(out / "m")]


def test_ai_cannot_restore_a_person_deletion(booted_framework):
    """restore_from_trash restores only what the calling AI asked to delete; what the person deleted, the person
    restores (blocker trash_not_yours). list_trash shows labels, never a path."""
    from orchestrator.tools.policy import Decision, ToolPolicy
    registry = booted_framework.tool_registry
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()],
                                             overrides={"list_trash": Decision.ALLOW,
                                                        "restore_from_trash": Decision.ALLOW}))
    state = Path(booted_framework.config.user_state_dir)
    trash = Trash.for_framework(booted_framework)
    _, entries = trashed(state, "mine", trash=trash)
    listing = registry.call("list_trash", {}, ToolContext("ditto"))
    assert [(i.label, i.yours) for i in listing.items] == [("mine", False)]
    assert str(state) not in listing.model_dump_json() and "Only the person empties" in listing.note
    with pytest.raises(ToolError) as exc:
        registry.call("restore_from_trash", {"trash_id": entries[0].id},
                      ToolContext("ditto", confirm=lambda s, i: True))
    assert exc.value.blocker == "trash_not_yours" and exc.value.code == "needs_person"
    assert entries[0].stored().is_file()
    with pytest.raises(TrashError) as direct:
        trash.restore(entries[0].id, "ditto")
    assert direct.value.code == "trash_not_yours"
    with pytest.raises(ToolError) as unknown:
        registry.call("restore_from_trash", {"trash_id": "tr_" + "0" * 16},
                      ToolContext("ditto", confirm=lambda s, i: True))
    assert unknown.value.blocker == "trash_unknown"


def test_an_ai_restores_what_it_asked_to_delete(booted_framework):
    from orchestrator.tools.policy import Decision, ToolPolicy
    registry = booted_framework.tool_registry
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()],
                                             overrides={"restore_from_trash": Decision.ALLOW}))
    state = Path(booted_framework.config.user_state_dir)
    folder = snapshots(state, "asked")
    trash = Trash.for_framework(booted_framework)
    accepted = AcceptedDeletion(snapshot_plan(folder), "trash", "framework", "approval:ap_0123456789abcdef")
    entry = trash.move("knowledge_snapshot", folder / "asked.jsonl.gz", state, "state", "asked", accepted,
                       deleted_by="ditto")
    shown = []
    out = registry.call("restore_from_trash", {"trash_id": entry.id},
                        ToolContext("ditto", confirm=lambda s, i: shown.append(1) or True))
    assert out.restored and not out.renamed and shown == [1], "asked first"
    assert (folder / "asked.jsonl.gz").is_file() and trash.entries() == []
