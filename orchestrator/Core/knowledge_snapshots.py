#!/usr/bin/env python3
"""
Knowledge-base snapshots as the person sees them.

Before every Deep Ingest, every index of the framework's own code and every restore, the knowledge base is saved as a
snapshot (`knowledge_snapshots/` in the per-user folder), so the change can be undone. There is no limit: the framework
keeps as many as the disk holds and deletes none by itself, on any channel, and no count of them reminds or warns
(DECISIONS, sixth round; round 5 f: deleting always asks the person, with the full list). `llm-cli knowledge
snapshots` and Tools > Knowledge Snapshots... list each one with its full path, file name, size and what it is; to
free space the person deletes the ones they choose (multiple choice, nothing ticked by default) or exports them.

- Deleting (`snapshot_plan`, `delete_snapshots`): exactly the snapshots the person ticked, after the person accepted
  that list in the framework's deletion dialog or at a terminal; into the trash (the default: restorable, the space
  stays used) or for good. Audited (`snapshots_pruned`) before anything is moved or deleted.
- Exporting (`export_snapshots`): copies the chosen snapshots to a folder the person picks (on this computer, a USB
  stick, a network share). Each copy is checked against the original by sha256 before it counts as done; a copy that
  does not match is removed again and reported. Exporting never deletes or changes a snapshot.

This module reads the folder only; it never opens the knowledge base itself (no embedding model, no Qdrant).
"""

import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

FOLDER = "knowledge_snapshots"
SUFFIX = ".jsonl.gz"
# What the person's listings say about keeping and deleting (the CLI and the GUI show the same words).
KEEPING_NOTE = ("There is no limit: the framework keeps as many snapshots as the disk holds and deletes no snapshot by "
                "itself. To free space, copy the ones you want to keep elsewhere (export) or delete the ones you "
                "choose (they go to the trash first, where you can restore them).")

# What a snapshot is, by the prefix the framework gives its name.
_WHAT = (("pre_ingest_", "backup of the knowledge base taken before a Deep Ingest"),
         ("pre_codebase_", "backup of the knowledge base taken before the framework's own documents were indexed"),
         ("pre_restore_", "backup of the knowledge base taken before an earlier snapshot was restored"))


@dataclass(frozen=True)
class SnapshotFile:
    """One snapshot as the person sees it: its name, its full path, its size and when it was taken."""
    name: str
    path: Path
    size_bytes: int
    modified: float

    @property
    def file_name(self) -> str:
        return self.path.name

    @property
    def what(self) -> str:
        return next((what for prefix, what in _WHAT if self.name.startswith(prefix)), "snapshot of the knowledge base")

    @property
    def taken(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified))


def snapshot_folder(state_dir: Any) -> Path:
    return Path(state_dir) / FOLDER


def snapshot_files(folder: Path) -> List[Path]:
    """The snapshot files in `folder`, oldest first (a file removed meanwhile is left out)."""
    if not folder.is_dir():
        return []
    stamped = []
    for path in folder.glob(f"*{SUFFIX}"):
        try:
            stamped.append((path.stat().st_mtime, path.name, path))
        except OSError:
            continue
    return [path for _, _, path in sorted(stamped)]


def list_snapshots(folder: Path) -> List[SnapshotFile]:
    """Every snapshot in `folder`, oldest first, with its size and time."""
    out = []
    for path in snapshot_files(folder):
        try:
            info = path.stat()
        except OSError:
            continue
        out.append(SnapshotFile(name=path.name[:-len(SUFFIX)], path=path, size_bytes=int(info.st_size),
                                modified=float(info.st_mtime)))
    return out


def size_text(n: int) -> str:
    return f"{n / 1000 ** 3:.1f} GB" if n >= 10 ** 9 else f"{n / 1000 ** 2:.1f} MB"


# --- deleting exactly the snapshots the person chose -----------------------------------------------------------------

def snapshot_plan(folder: Path, names: Optional[Iterable[str]] = None):
    """The deletion plan of the snapshots `names` (default: every snapshot, for the multiple choice): each with its
    name, full path, file name, size and what it is (with the time it was taken), oldest first. A name that is not a
    snapshot in `folder` is refused (ValueError); None when there is nothing to list."""
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    snapshots = {s.name: s for s in list_snapshots(Path(folder))}
    wanted = list(snapshots) if names is None else list(dict.fromkeys(names))
    unknown = [n for n in wanted if n not in snapshots]
    if unknown:
        raise ValueError(f"No such knowledge snapshot: {', '.join(unknown[:5])}.")
    items = [DeletionItem(kind="knowledge_snapshot", speaking_name=s.name, full_path=str(s.path),
                          file_name=s.file_name, size_bytes=s.size_bytes, what=f"{s.what} (taken {s.taken})")
             for s in sorted((snapshots[n] for n in wanted), key=lambda s: (s.modified, s.name))]
    return plan_of(items, ("trash", "permanent"))


@dataclass
class SnapshotDeletion:
    """What a deletion did: the snapshots moved to the trash or deleted for good, the bytes freed now (0 for the
    trash: the space stays used until the person empties it) and the ones that failed (name, reason)."""
    done: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    trash_ids: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)


def delete_snapshots(folder: Path, accepted: Any, trash: Any = None, audit: Any = None) -> SnapshotDeletion:
    """Deletes exactly the snapshots of the plan the person accepted (orchestrator/Core/deletion.py): into the trash
    of the per-user folder (`trash`, a Trash) or for good. The plan is built again first and must be the accepted one
    (PlanChanged otherwise: nothing is deleted); the deletion is audited before anything goes (RuntimeError, nothing
    deleted, without the audit). Only the person: in the GUI or at a terminal (PermissionError otherwise)."""
    from orchestrator.Core.deletion import require_person, require_same
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person deletes knowledge snapshots, in the GUI or at a terminal.")
    folder = Path(folder)
    names = [i.speaking_name for i in accepted.plan.items]
    try:
        current = snapshot_plan(folder, names)
    except ValueError:
        current = None
    require_same(accepted, current, "the list of knowledge snapshots")
    if accepted.mode == "trash" and trash is None:
        raise RuntimeError("The trash is not available; no snapshot was deleted.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so no snapshot was deleted.")
    try:
        audit.record(accepted.actor, "knowledge", "framework", "person", "snapshots_pruned", approved=True,
                     arguments={"snapshots": names, "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                     detail=f"{len(names)} knowledge snapshots "
                            + ("moved to the trash" if accepted.mode == "trash" else "deleted for good"),
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        raise RuntimeError("The audit log cannot record the deletion; no snapshot was deleted.") from None
    result = SnapshotDeletion()
    state_dir = folder.parent
    for item in accepted.plan.items:
        path = Path(item.full_path)
        try:
            if accepted.mode == "trash":
                entry = trash.move("knowledge_snapshot", path, state_dir, "state", item.speaking_name, accepted,
                                   accepted.actor)
                result.trash_ids.append(entry.id)
            else:
                if path.is_symlink() or not path.is_file():
                    raise OSError("not a plain file")
                path.unlink()
                result.freed_bytes += item.size_bytes
        except Exception as e:  # noqa: BLE001 - a TrashError or an OSError: that snapshot stays and is reported
            result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
            continue
        result.done.append(item.speaking_name)
    return result


# --- exporting (copying elsewhere; never deletes) ---------------------------------------------------------------------

@dataclass
class ExportedSnapshot:
    """One snapshot of an export: the copy's full path, whether it is done (its sha256 matches the original) and,
    when not, why."""
    name: str
    copy: str
    size_bytes: int
    sha256: str = ""
    ok: bool = False
    problem: str = ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_checked(source: Path, target: Path, original: str) -> str:
    """Copies `source` to the new file `target` through an unfinished file next to it and returns the copy's sha256;
    the unfinished file is the export's own and is removed again whatever happens. OSError on a failure,
    ValueError when the copy does not match `original`."""
    partial = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        with open(source, "rb") as src, open(partial, "xb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
            dst.flush()
            os.fsync(dst.fileno())
        copied = _sha256(partial)
        if copied != original:
            raise ValueError("the copy does not match the original (sha256); it was removed again")
        try:
            os.link(partial, target)   # never replaces a file that appeared meanwhile
        except FileExistsError:
            raise
        except OSError:   # a file system without hard links (a FAT USB stick, some network shares)
            if os.path.lexists(target):
                raise FileExistsError(str(target)) from None
            os.rename(partial, target)
        return copied
    finally:
        try:
            if os.path.lexists(partial):
                partial.unlink()
        except OSError:
            pass


def export_snapshots(folder: Path, names: Iterable[str], destination: Path) -> List[ExportedSnapshot]:
    """Copies the snapshots `names` into the existing folder `destination` (on this computer, a USB stick, a network
    share) and checks each copy against the original by sha256. A file of the same name that is there already is
    never overwritten (it counts as done only when it has the same content). A copy that does not match is removed
    again (it is the export's own unfinished file) and reported. Nothing in `folder` is changed. ValueError for an
    unknown name, a destination that is not a folder, or one without room for every copy."""
    folder, destination = Path(folder), Path(destination)
    snapshots = {s.name: s for s in list_snapshots(folder)}
    wanted = list(dict.fromkeys(names))
    unknown = [n for n in wanted if n not in snapshots]
    if unknown:
        raise ValueError(f"No such knowledge snapshot: {', '.join(unknown[:5])}.")
    if not wanted:
        raise ValueError("Choose at least one snapshot to export.")
    if not destination.is_dir():
        raise ValueError(f"{destination} is not a folder.")
    try:
        same = destination.resolve() == folder.resolve()
    except OSError:
        same = False
    if same:
        raise ValueError("Choose a folder other than the snapshots' own folder.")
    needed = sum(snapshots[n].size_bytes for n in wanted if not os.path.lexists(destination / snapshots[n].file_name))
    try:
        free = shutil.disk_usage(destination).free
    except OSError:
        free = None
    if free is not None and needed > free:
        raise ValueError(f"{destination} has {size_text(free)} free; the export needs {size_text(needed)}.")
    results = []
    for name in wanted:
        snap = snapshots[name]
        target = destination / snap.file_name
        result = ExportedSnapshot(name=name, copy=str(target), size_bytes=snap.size_bytes)
        results.append(result)
        try:
            original = _sha256(snap.path)
            if os.path.lexists(target):
                if not target.is_symlink() and target.is_file() and _sha256(target) == original:
                    result.sha256, result.ok = original, True
                    result.problem = "already there with the same content"
                else:
                    result.problem = "a different file of that name is there already; nothing was overwritten"
                continue
            result.sha256 = _copy_checked(snap.path, target, original)
            result.ok = True
        except FileExistsError:
            result.problem = "a different file of that name is there already; nothing was overwritten"
        except ValueError as e:
            result.problem = str(e)
        except OSError as e:
            result.problem = f"could not be copied ({type(e).__name__}: {e.strerror or e})"
    return results


__all__ = ["ExportedSnapshot", "FOLDER", "KEEPING_NOTE", "SUFFIX", "SnapshotDeletion", "SnapshotFile",
           "delete_snapshots", "export_snapshots", "list_snapshots", "size_text", "snapshot_files",
           "snapshot_folder", "snapshot_plan"]
