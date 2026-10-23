#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Taking over the old Hugging Face download cache (DECISIONS round 11, A6)

Before A6 the GUI's Hugging Face browser saved through huggingface_hub's hf_hub_download into that library's cache
inside the models folder: `<models_dir>/models--<owner>--<name>/snapshots/<commit>/<file>`, each file a link to a blob
named by its ETag (`blobs/<sha256>` for a file stored with LFS, `blobs/<git blob id>` for a small one), or the file
itself where the file system has no links. Since A6 every Hugging Face download lands in
`<models_dir>/hf/<owner>--<name>/<commit[:12]>/` (orchestrator/Core/hf_hub.py download_path). The old models stay
listed and usable (tools/local_files.py lists them and binds them for a build); this module is the optional one-time
Take over:

- old_cache(models_dir): every file of a model in that cache (read with huggingface_hub's own scan_cache_dir), where it
  lands in the new layout and its state: `new` (not taken over yet), `taken` (the new folder holds a file of that name
  and size: a file there only ever gets its name after its check) or `conflict` (another file of that name is there;
  it is never replaced).
- take_over(models_dir, files): copies each new file into the new folder, read once and checked while it is copied:
  against the sha256 its blob is named by (a file stored with LFS), its git blob id (a small file), or, where the
  cache kept no checksum (a cache without links), against what the Hub lists for that commit (`lookup`: the LFS
  sha256, a small file by its size, as the downloader pins it); without `lookup`, when the Hub cannot be asked or in
  offline mode (the lookup refuses before any request), such a file is skipped with the reason and stays where it
  is. A copy is a hidden temporary file under the downloader's per-file lock until its check passed, then it gets its
  name. The one disk rule comes first (room for the copies
  beside the reserve and what other calls reserved), and the copy holds its own reservation in the ledger while it
  runs. Nothing is removed from the old cache here, and a link that leads out of the models folder is never
  followed (a link may lead into huggingface_hub's shared store of the same cache, `<models_dir>/blobs/`).
- old_copies_plan / delete_old_copies: the old cache's models that were taken over completely, as a deletion plan
  (orchestrator/Core/deletion.py) with the exact list (full path, size, what it is): each model's cache folder, and
  each file of the cache's shared store (`<models_dir>/blobs/`, where newer huggingface_hub versions keep the bytes
  their cache folders link to) that only those folders use. Only the person accepts it, in
  the deletion dialog, where "No" is the default; never an AI. The plan is built again from the disk before anything
  goes and must be the accepted one (PlanChanged otherwise); the deletion is audited before anything goes; into the
  trash or for good, as the person chose.

This module imports nothing from the GUI; the Hugging Face browser runs it in worker threads.
"""

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.Core.hf_hub import COMMIT, REPO_ID, HubError, download_path, hub_error, safe_filename

STATES = ("new", "taken", "conflict")
OWNER = "gui"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
WHAT = "take over old Hugging Face downloads"
AUDIT_TOOL = "hf_take_over"
# Why a file stays in the old cache (TakeOverResult.skipped), in English; the window shows hf.takeover.why.<code>.
REASONS = {
    "conflict": "another file of that name is in the models folder; it is not replaced",
    "no_checksum": "the old cache kept no checksum for it",
    "hub": "Hugging Face could not be asked for its checksum",
    "offline": "offline mode is on, so Hugging Face was not asked for its checksum",
    "not_listed": "Hugging Face does not list it for that commit",
    "size": "its size does not match",
    "mismatch": "it does not match its checksum",
    "link": "its link leads out of the models folder",
    "copy_failed": "it could not be copied",
}


@dataclass(frozen=True)
class OldFile:
    """One file of a model in the old huggingface_hub cache. `path`: its snapshot entry (a link to its blob, or the
    file itself); `sha256` / `git_id`: the checksum its blob is named by, when the cache kept it; `dest`: where the Take
    over puts it; `folder`: the model's cache folder (models--<owner>--<name>)."""
    repo: str
    commit: str
    name: str
    path: Path
    size: int
    sha256: Optional[str]
    git_id: Optional[str]
    dest: Path
    state: str
    folder: Path

    @property
    def checked_here(self) -> bool:
        """Its checksum is known from the cache itself (no question to the Hub)."""
        return bool(self.sha256 or self.git_id)


@dataclass
class TakeOverResult:
    """What a Take over did: the files copied (checked), the files skipped with their reason code (REASONS; they stay
    in the old cache), whether the person stopped it, and the bytes copied."""
    copied: List[OldFile] = field(default_factory=list)
    skipped: List[Tuple[OldFile, str]] = field(default_factory=list)
    stopped: bool = False
    bytes_copied: int = 0


def _state(dest: Path, size: int) -> str:
    try:
        if dest.is_symlink() or (os.path.lexists(dest) and not dest.is_file()):
            return "conflict"
        if dest.is_file():
            return "taken" if dest.stat().st_size == size else "conflict"
    except OSError:
        return "conflict"
    return "new"


def _real_inside(path: Path, folder: Path) -> Optional[Path]:
    """The real path `path` leads to when it lies inside `folder` (links followed), else None."""
    try:
        real, root = Path(path).resolve(strict=True), Path(folder).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real if real != root and root in real.parents else None


def old_cache(models_dir: Path) -> List[OldFile]:
    """Every file of every model in huggingface_hub's cache inside `models_dir`, sorted by model, commit and name. An
    empty list when there is none or the cache cannot be read (it stays as it is)."""
    return _scan(models_dir)[0]


def _scan(models_dir: Path) -> Tuple[List[OldFile], set]:
    """old_cache's files, and the cache folders that hold a file old_cache leaves out (a revision folder that is no
    commit, a name the downloader refuses): such a folder is never taken over completely, so it is never offered
    for removal."""
    models_dir = Path(models_dir)
    if not models_dir.is_dir():
        return [], set()
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir(models_dir)
    except Exception:  # noqa: BLE001 - no cache there, or an unreadable folder: nothing to take over
        return [], set()
    found: List[OldFile] = []
    partial: set = set()
    for repo in sorted(info.repos, key=lambda r: r.repo_id):
        if getattr(repo, "repo_type", "") != "model" or not REPO_ID.match(repo.repo_id or ""):
            continue
        folder = Path(repo.repo_path)
        for revision in sorted(repo.revisions, key=lambda r: r.commit_hash):
            if not COMMIT.match(revision.commit_hash or ""):
                if revision.files:
                    partial.add(folder)
                continue
            snapshot = Path(revision.snapshot_path)
            for cached in sorted(revision.files, key=lambda f: str(f.file_path)):
                path = Path(cached.file_path)
                try:
                    name = path.relative_to(snapshot).as_posix()
                except ValueError:
                    partial.add(folder)
                    continue
                if not safe_filename(name):
                    partial.add(folder)
                    continue
                etag = Path(cached.blob_path).name.lower() if path.is_symlink() else ""
                size = int(cached.size_on_disk)
                dest = download_path(models_dir, repo.repo_id, revision.commit_hash, name)
                found.append(OldFile(repo.repo_id, revision.commit_hash, name, path, size,
                                     etag if _SHA256.match(etag) else None, etag if _GIT_ID.match(etag) else None,
                                     dest, _state(dest, size), folder))
    return found, partial


def _expected(file: OldFile, lookup: Optional[Callable[[str, str], Dict[str, Any]]],
              listed: Dict[Tuple[str, str], Any]) -> Tuple[Optional[str], Optional[str], str]:
    """(sha256, git blob id, "") to check `file` against, or (None, None, the REASONS code why it cannot be checked)."""
    if file.checked_here:
        return file.sha256, file.git_id, ""
    if lookup is None:
        return None, None, "no_checksum"
    key = (file.repo, file.commit)
    if key not in listed:
        try:
            listed[key] = lookup(file.repo, file.commit)
        except Exception as e:  # noqa: BLE001 - offline, a gated repository without a stored token, ...
            listed[key] = hub_error(e, f"Hugging Face could not list {file.repo}", False)
    hub = listed[key]
    if isinstance(hub, HubError) and hub.blocker == "offline_mode":
        return None, None, "offline"   # the lookup asked nobody (hf_hub.offline_refusal)
    if isinstance(hub, HubError) or not isinstance(hub, dict):
        return None, None, "hub"
    listed_file = hub.get(file.name)
    if listed_file is None or listed_file.size is None:
        return None, None, "not_listed"
    if int(listed_file.size) != file.size:
        return None, None, "size"
    return listed_file.sha256, None, ""  # a small file without LFS: pinned by its size and commit, as downloads are


def _copy_one(models_dir: Path, file: OldFile, sha256: Optional[str], git_id: Optional[str],
              stop: Optional[threading.Event]) -> str:
    """Copies `file` to its place, checked: "" when it is there now, else the REASONS code why not. HashStopped when
    stopped. The file is read once from the real path its link leads to (inside the models folder, opened without
    following a link), and the copy holds exactly the bytes that were checked."""
    from orchestrator.utils.download import FileLock
    from orchestrator.utils.helpers import HashStopped, copy_hashed, digest_matches, git_blob_id
    real = _real_inside(file.path, models_dir)
    if real is None:
        return "link"
    dest = file.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(dest.with_name(f".{dest.name}.lock")):
        state = _state(dest, file.size)
        if state != "new":
            return "" if state == "taken" else "conflict"
        tmp = dest.with_name(f".{dest.name}.takeover.tmp")
        tmp.unlink(missing_ok=True)
        try:
            digest = copy_hashed(real, tmp, cancel=stop.is_set if stop is not None else None)
            if tmp.stat().st_size != file.size:
                problem = "size"
            elif sha256 is not None and not digest_matches(digest, sha256):
                problem = "mismatch"
            elif git_id is not None and not digest_matches(git_blob_id(tmp, follow_links=False), git_id):
                problem = "mismatch"
            else:
                problem = ""
            if problem:
                tmp.unlink(missing_ok=True)
                return problem
            os.replace(tmp, dest)
            return ""
        except OSError:
            tmp.unlink(missing_ok=True)
            return "copy_failed"
        except BaseException:  # stopped (HashStopped), or a second Ctrl+C at a terminal: no half copy stays
            tmp.unlink(missing_ok=True)
            raise


def take_over(models_dir: Path, files: List[OldFile], *, ledger: Any = None, stop: Optional[threading.Event] = None,
              progress: Optional[Callable[[int, int, str], None]] = None,
              lookup: Optional[Callable[[str, str], Dict[str, Any]]] = None, disk_usage: Any = None,
              owner: str = OWNER) -> TakeOverResult:
    """Copies the `files` (old_cache entries) whose state is still `new` into the new folder, each checked (see the
    module text). disk_plan.InsufficientSpace before anything is copied when the disk cannot hold them beside the
    reserve; while copying, the space is reserved in `ledger`. `progress(done, total, name)` before each file; `stop`
    ends it between two chunks (the file being copied is not kept). The old cache is never changed."""
    from orchestrator.Core.disk_plan import refusal, room
    from orchestrator.Core.hf_hub import reserve_space
    from orchestrator.utils.helpers import HashStopped
    models_dir = Path(models_dir)
    result = TakeOverResult()
    todo: List[OldFile] = []
    for file in files:
        if not isinstance(file, OldFile) or file.dest != download_path(models_dir, file.repo, file.commit, file.name):
            continue  # only what old_cache listed for this models folder, never another place
        state = _state(file.dest, file.size)
        if state == "new":
            todo.append(file)
        elif state == "conflict":
            result.skipped.append((file, "conflict"))
    need = sum(f.size for f in todo)
    problem = refusal([room(models_dir, need, label="models", what=WHAT, ledger=ledger, disk_usage=disk_usage)],
                      "the take-over")
    if problem is not None:
        raise problem
    reservation = reserve_space(ledger, models_dir, need, owner, "Hugging Face take-over", need_label="models",
                                what=WHAT, key=f"take-over/{models_dir}", disk_usage=disk_usage, call="the take-over")
    listed: Dict[Tuple[str, str], Any] = {}
    try:
        for index, file in enumerate(todo):
            if stop is not None and stop.is_set():
                result.stopped = True
                break
            if progress is not None:
                progress(index, len(todo), file.name)
            sha256, git_id, why = _expected(file, lookup, listed)
            if why:
                result.skipped.append((file, why))
                continue
            try:
                why = _copy_one(models_dir, file, sha256, git_id, stop)
            except HashStopped:
                result.stopped = True
                break
            if why:
                result.skipped.append((file, why))
            else:
                result.copied.append(file)
                result.bytes_copied += file.size
    finally:
        reservation.release()
    if progress is not None and not result.stopped:
        progress(len(todo), len(todo), "")
    return result


# --- removing the old copies: only the person's yes to the exact list -------------------------------------------------

CACHE_FOLDER_PREFIXES = ("models--", "datasets--", "spaces--", "kernels--")


def _store_files(folder: Path, store: Optional[Path]) -> List[Path]:
    """The files of the cache's shared store (`store`, resolved) that the blob links of `folder` lead to."""
    if store is None:
        return []
    found = []
    try:
        entries = list((folder / "blobs").iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_symlink():
            continue
        try:
            target = entry.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if store in target.parents and target.is_file() and not target.is_symlink():
            found.append(target)
    return found


def old_copies_plan(models_dir: Path):
    """The deletion plan (DeletionPlan, trash or for good) of the old cache's models that the new folder holds
    completely (every file `taken`), or None when there is none: each model's cache folder with its full path and size
    on disk (blobs, snapshots, refs), and each file of the cache's shared store that only those folders link to."""
    from orchestrator.Core.deletion import plan_of
    from orchestrator.Core.trash import tree_size
    from orchestrator.tools.spec import DeletionItem
    models_dir = Path(models_dir)
    by_folder: Dict[Path, List[OldFile]] = {}
    listed, partial = _scan(models_dir)
    for file in listed:
        by_folder.setdefault(file.folder, []).append(file)
    done: Dict[Path, str] = {}
    items = []
    for folder, files in sorted(by_folder.items(), key=lambda kv: str(kv[0])):
        if (folder in partial or any(f.state != "taken" for f in files) or folder.is_symlink()
                or not folder.is_dir()):
            continue
        repo = done[folder] = files[0].repo
        items.append(DeletionItem(
            kind="model_file", speaking_name=f"{repo} (old download cache)", full_path=str(folder),
            file_name=folder.name, size_bytes=tree_size(folder),
            what=(f"the copy of {repo} in the download cache of earlier versions ({len(files)} "
                  f"{'file' if len(files) == 1 else 'files'}); the models folder holds each of them, checked, "
                  f"in hf/{folder.name[len('models--'):]}")))
    store_dir = models_dir / "blobs"
    try:
        store = store_dir.resolve(strict=True) if store_dir.is_dir() and not store_dir.is_symlink() else None
    except (OSError, RuntimeError):
        store = None
    if done and store is not None:
        users: Dict[Path, set] = {}
        try:
            folders = [p for p in models_dir.iterdir() if p.name.startswith(CACHE_FOLDER_PREFIXES) and p.is_dir()]
        except OSError:
            folders = []
        for folder in folders:
            for target in _store_files(folder, store):
                users.setdefault(target, set()).add(folder)
        for target, folders_using in sorted(users.items(), key=lambda kv: str(kv[0])):
            if not folders_using or any(f not in done for f in folders_using):
                continue  # another model of the cache still uses it
            repos = ", ".join(sorted(done[f] for f in folders_using))
            items.append(DeletionItem(
                kind="model_file", speaking_name=f"shared file of {repos} (old download cache)"[:300],
                full_path=str(target), file_name=target.name, size_bytes=int(target.stat().st_size),
                what=(f"bytes of {repos} in the shared store of the download cache of earlier versions; only "
                      "the old copies listed here use it")[:300]))
    return plan_of(items, ("trash", "permanent"))


def delete_old_copies(models_dir: Path, accepted: Any, trash: Any = None, audit: Any = None):
    """Deletes exactly the old copies the person accepted (orchestrator/Core/deletion.py): only a person's
    accepted plan in the GUI or at a terminal, built again from the disk first (PlanChanged when it is not the accepted
    one), audited before anything goes, into the trash (restorable) or for good. Returns a DeletionResult; an item
    that cannot go is kept and reported."""
    from orchestrator.Core.deletion import DeletionResult, require_person, require_same
    from orchestrator.rag.storage_lock import remove_tree
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person removes old downloads, in the GUI or at a terminal.")
    models_dir = Path(models_dir)
    require_same(accepted, old_copies_plan(models_dir), "the list of old downloads")
    if accepted.mode == "trash" and trash is None:
        raise RuntimeError("The trash is not available; nothing was deleted.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was deleted.")
    items = accepted.plan.items
    try:
        audit.record(accepted.actor, AUDIT_TOOL, "framework", "person", "old_downloads_deleted", approved=True,
                     arguments={"items": len(items), "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                     detail="old Hugging Face downloads " + ("moved to the trash" if accepted.mode == "trash"
                                                             else "deleted for good"),
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
    result = DeletionResult()
    for item in items:
        path = Path(item.full_path)
        try:
            if accepted.mode == "trash":
                entry = trash.move("model_file", path, models_dir, "models", item.speaking_name, accepted,
                                   accepted.actor)
                result.trash_ids.append(entry.id)
            else:
                if path.is_symlink():
                    raise OSError("a link")
                if path.is_dir():
                    remove_tree(path)
                elif path.is_file():
                    path.unlink()
                if os.path.lexists(path):
                    raise OSError("a file in it could not be deleted")
                result.freed_bytes += item.size_bytes
            result.done.append(item.speaking_name)
        except Exception as e:  # noqa: BLE001 - a TrashError or an OSError: the folder stays and is reported
            result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
    return result


__all__ = ["CACHE_FOLDER_PREFIXES", "OldFile", "REASONS", "STATES", "TakeOverResult", "delete_old_copies", "old_cache", "old_copies_plan", "take_over"]
