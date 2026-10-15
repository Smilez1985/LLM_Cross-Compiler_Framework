#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - The trash (delegation design 3.10, round 5 f)

A deletion the person accepted in the mode "trash" moves the file or folder into the trash of the data folder it lies
in (`<data folder>/.trash/<entry id>/<name>`: the output folder for built models, the per-user folder for knowledge
snapshots), with one `os.rename`: nothing is copied, and a move to another file system is refused, never copied
(EXDEV). The space stays used until the person empties the trash. With the planned host layout (round 6, H1) every
data folder lies below one `Data` folder, and its trash becomes `Data/.trash`.

The index (`tool_trash.json` in the per-user folder, mode 0600, written under a lock) is signed like the permission
file (orchestrator/tools/signing.py): what each entry is, where it came from (`<label>:<relative path>`), who asked to
delete it (`deleted_by`, e.g. "mcp:laptop" or "gui") and who accepted it. An index edited by hand or signed with
another key is not used: nothing is restored or emptied from it until the person looks at it.

Rules:
- Nothing leaves the trash by itself. The retention (setting `trash_retention_days`, default 14, which an AI cannot
  change) is a reminder date, never an automatic deletion.
- Emptying is the person's alone: in the trash window (File > Trash...) or with `llm-cli trash empty` at a terminal,
  always with the full list and "No" as the default. No tool empties the trash.
- Restoring moves an entry back to where it was (or next to it as `<name>-restored-<time>` when that place is taken),
  never through a link and never out of its data folder. An AI restores only what it asked to delete itself
  (`restore_from_trash`); what the person deleted, the person restores.
- No AI reaches the trash folders: they are denied folders for every AI (local_files.denied_folders), and the
  listings of built models skip them.
"""

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.Core.deletion import (AcceptedDeletion, PlanChanged, plan_of, require_person, require_same,
                                        size_text)
from orchestrator.tools.signing import SignatureError, sign, verify
from orchestrator.tools.spec import DeletionItem, DeletionPlan
from orchestrator.utils.file_lock import file_lock

TRASH_FOLDER = ".trash"
INDEX_FILE = "tool_trash.json"
KIND = "tool_trash"   # the signed file kind
DEFAULT_RETENTION_DAYS = 14
MAX_ENTRIES = 5000
ENTRY_ID = r"^tr_[0-9a-f]{16}$"
_ENTRY_ID = re.compile(ENTRY_ID)
# What the trash holds, in the person's words.
WHAT = {"artifact": "built model", "model_file": "model file", "knowledge_snapshot": "knowledge snapshot",
        "module": "hardware module", "pack": "community pack"}
EntryKind = Literal["artifact", "model_file", "knowledge_snapshot", "module", "pack"]
# Names that keep their double suffix when a restored copy needs another name.
_SUFFIXES = (".jsonl.gz", ".tar.gz", ".zip", ".gguf", ".rkllm", ".rknn", ".onnx", ".bin", ".safetensors")


class TrashError(RuntimeError):
    """A trash operation the framework refuses, with a reason code (the facts catalogue's blockers trash_unknown and
    trash_not_yours, or failed) and never a host path in the message (it can reach an AI)."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


class TrashEntry(BaseModel):
    """One thing in the trash. `root` and the moved item's place are for the person's windows and this module only;
    an AI sees `ai_view()`."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ENTRY_ID)
    kind: EntryKind
    label: str = Field(min_length=1, max_length=300)          # speaking name
    origin: str = Field(min_length=1, max_length=4200)        # <label>:<relative path> (never absolute)
    root: str = Field(min_length=1, max_length=4096)          # the data folder it was moved within
    name: str = Field(min_length=1, max_length=400)           # its file or folder name
    is_dir: bool
    size: int = Field(ge=0)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    deleted_at: str
    deleted_by: str = Field(min_length=1, max_length=200)    # who asked: "gui", "cli", "mcp:laptop", "ditto", ...
    confirmed_by: Literal["gui", "cli", "framework"]          # where the person accepted the deletion plan
    remind_at: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def relative(self) -> str:
        return self.origin.split(":", 1)[1] if ":" in self.origin else self.origin

    def original(self) -> Path:
        """Where it was before it was moved (the person's view)."""
        return Path(self.root) / self.relative

    def stored(self) -> Path:
        """Where it is now: <root>/.trash/<id>/<name>."""
        return Path(self.root) / TRASH_FOLDER / self.id / self.name

    def what(self) -> str:
        return WHAT.get(self.kind, self.kind)

    def ai_view(self, actor: str = "") -> Dict[str, Any]:
        """What an AI may see of the entry: its id, kind, speaking name, size, dates and whether it asked to delete it
        itself (only then can it restore it). Never a path, never who else deleted it."""
        return {"trash_id": self.id, "kind": self.kind, "label": self.label, "size_bytes": self.size,
                "deleted_at": self.deleted_at, "remind_at": self.remind_at,
                "yours": bool(actor) and self.deleted_by == actor}


@dataclass
class EmptyResult:
    """What emptying did: the entries deleted for good (by speaking name), the bytes freed, and those that could not
    be deleted (speaking name, reason)."""
    deleted: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    failed: List[Tuple[str, str]] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def tree_size(path: Path) -> int:
    """Bytes of a file or of every file below a folder, never following a link."""
    try:
        if path.is_symlink() or not path.is_dir():
            return int(path.lstat().st_size)
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def file_sha256(path: Path) -> Optional[str]:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def restored_name(name: str, stamp: str) -> str:
    """`<name>-restored-<stamp>` with a known suffix kept at the end (a restored snapshot stays a snapshot)."""
    suffix = next((s for s in _SUFFIXES if name.lower().endswith(s) and len(name) > len(s)), "")
    stem = name[:len(name) - len(suffix)] if suffix else name
    return f"{stem}-restored-{stamp}{suffix}"


def trash_folders(state_dir: Optional[Path]) -> List[Path]:
    """Every trash folder the framework uses (the per-user folder's and those of the data folders it moved something
    within), for the folders no AI may reach. Read without checking the index's signature: a list of folders to
    deny never grants anything."""
    if state_dir is None:
        return []
    found = [Path(state_dir) / TRASH_FOLDER]
    try:
        data = json.loads((Path(state_dir) / INDEX_FILE).read_text(encoding="utf-8"))
        roots = data.get("roots") if isinstance(data, dict) else None
    except (OSError, ValueError, RecursionError):
        roots = None
    for root in roots if isinstance(roots, list) else []:
        if isinstance(root, str) and root and os.path.isabs(root) and Path(root) / TRASH_FOLDER not in found:
            found.append(Path(root) / TRASH_FOLDER)
    return found


def trash_bytes_by_device(state_dir: Optional[Path]) -> Dict[str, int]:
    """Bytes in the trash per file system (disk_plan.device_of), for the disk checks: used space that is not free
    until the person empties the trash. Read without checking the index's signature: a number shown next to the free
    space never decides anything. Entries whose moved item is gone are not counted."""
    from orchestrator.Core.disk_plan import device_of
    totals: Dict[str, int] = {}
    if state_dir is None:
        return totals
    try:
        data = json.loads((Path(state_dir) / INDEX_FILE).read_text(encoding="utf-8"))
        items = data.get("entries") if isinstance(data, dict) else None
    except (OSError, ValueError, RecursionError):
        return totals
    for item in items if isinstance(items, list) else []:
        try:
            entry = TrashEntry.model_validate(item)
        except ValueError:
            continue
        if os.path.lexists(entry.stored()):
            device = device_of(Path(entry.root))
            totals[device] = totals.get(device, 0) + entry.size
    return totals


class Trash:
    """The trash of one user. `key`/`refuse_unkeyed`: the signing key of the permission file (as ConfirmedBuilds);
    `audit`: the tool audit log (emptying writes its entry before anything is deleted)."""

    def __init__(self, state_dir: Path, key: Optional[bytes] = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None, audit: Any = None,
                 retention_days: int = DEFAULT_RETENTION_DAYS):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / INDEX_FILE
        self.key = key
        self.refuse_unkeyed = refuse_unkeyed
        self.audit = audit
        self.retention_days = max(1, int(retention_days or DEFAULT_RETENTION_DAYS))
        self.problem: Optional[str] = None   # set when the stored index exists but could not be used

    @classmethod
    def for_framework(cls, framework) -> "Trash":
        """The trash of the framework's user, signed with the permission file's key."""
        config = getattr(framework, "config", None)
        registry = getattr(framework, "tool_registry", None)
        policy_store = getattr(registry, "policy_store", None)
        key = getattr(policy_store, "key", None)
        try:
            days = int(config.get("trash_retention_days", DEFAULT_RETENTION_DAYS) or DEFAULT_RETENTION_DAYS)
        except (AttributeError, TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        return cls(Path(config.user_state_dir), key=key if isinstance(key, bytes) else None,
                   refuse_unkeyed=getattr(policy_store, "refuse_unkeyed", None),
                   audit=getattr(registry, "audit", None), retention_days=days)

    # --- the index ------------------------------------------------------------------------------------------------

    def _lock(self):
        return file_lock(self.state_dir / f".{INDEX_FILE}.lock", timeout=30)

    def _load(self) -> Tuple[Dict[str, TrashEntry], List[str]]:
        try:
            if self.path.is_symlink():
                raise ValueError("it is a symbolic link")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            data = verify(KIND, raw, self.key, self.refuse_unkeyed)
        except FileNotFoundError:
            self.problem = None
            return {}, []
        except (OSError, ValueError, RecursionError, SignatureError) as e:
            first = (str(e).splitlines() or [type(e).__name__])[0][:200]
            self.problem = (f"The trash's list ({INDEX_FILE}) cannot be used ({first}); nothing is restored or "
                            "emptied from it. The moved items are still in the .trash folders.")
            raise TrashError(self.problem) from None
        self.problem = None
        entries: Dict[str, TrashEntry] = {}
        for item in data.get("entries") or []:
            try:
                entry = TrashEntry.model_validate(item)
            except ValueError:
                continue
            entries[entry.id] = entry
        roots = [r for r in data.get("roots") or [] if isinstance(r, str)]
        return entries, roots

    def _save(self, entries: Dict[str, TrashEntry], roots: List[str]) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        body = {"version": 1, "roots": sorted(set(roots)),
                "entries": [e.model_dump(mode="json") for e in sorted(entries.values(), key=lambda e: e.id)]}
        data = sign(KIND, body, self.key)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # --- reading --------------------------------------------------------------------------------------------------

    def entries(self) -> List[TrashEntry]:
        """Every entry, oldest first, then by name (TrashError when the index cannot be used)."""
        entries, _ = self._load()
        return sorted(entries.values(), key=lambda e: (e.deleted_at, e.label, e.id))

    def get(self, entry_id: str) -> Optional[TrashEntry]:
        if not isinstance(entry_id, str) or not _ENTRY_ID.fullmatch(entry_id):
            return None
        return self._load()[0].get(entry_id)

    @staticmethod
    def present(entry: TrashEntry) -> bool:
        return os.path.lexists(entry.stored())

    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries() if self.present(e))

    def bytes_by_device(self) -> Dict[str, int]:
        """Bytes in the trash per file system: space that is used but not free until the person empties the trash."""
        return trash_bytes_by_device(self.state_dir)

    def due(self, now: Optional[datetime] = None) -> List[TrashEntry]:
        """Entries past their reminder date: the person is reminded; nothing is deleted."""
        now = now or _now()
        due = []
        for entry in self.entries():
            try:
                if datetime.fromisoformat(entry.remind_at) <= now:
                    due.append(entry)
            except ValueError:
                due.append(entry)
        return due

    # --- moving into the trash ------------------------------------------------------------------------------------

    def move(self, kind: str, path: Path, root: Path, root_label: str, label: str, accepted: AcceptedDeletion,
             deleted_by: str = "") -> TrashEntry:
        """Moves `path` (inside the data folder `root`, named `root_label` for the origin) into the trash, as part of
        the deletion the person accepted in the mode "trash". TrashError when it lies outside `root`, is a link, is
        in the trash already or another file system would need a copy (nothing is moved then)."""
        accepted = require_person(accepted)
        if accepted.mode != "trash":
            raise TrashError("The person chose to delete for good, not to move to the trash.")
        if kind not in WHAT:
            raise TrashError(f"The trash does not take a {kind}.")
        path = Path(path)
        try:
            real_root = Path(root).resolve(strict=True)
            if path.is_symlink():
                raise TrashError(f"{label} is a link; links are not moved to the trash.")
            real = path.parent.resolve(strict=True) / path.name
            if not os.path.lexists(real):
                raise TrashError(f"{label} is no longer there; nothing was moved.", code="unknown_id")
        except OSError:
            raise TrashError(f"{label} or its folder is no longer there; nothing was moved.",
                             code="unknown_id") from None
        if real == real_root or real_root not in real.parents:
            raise TrashError(f"{label} does not lie inside its data folder; it is not moved.")
        relative = real.relative_to(real_root)
        if relative.parts[0] == TRASH_FOLDER:
            raise TrashError(f"{label} is in the trash already.")
        is_dir = real.is_dir()
        size = tree_size(real)
        digest = None if is_dir else file_sha256(real)
        now = _now()
        entry_id = f"tr_{secrets.token_hex(8)}"
        entry = TrashEntry(id=entry_id, kind=kind, label=label, origin=f"{root_label}:{relative.as_posix()}",
                           root=str(real_root), name=real.name, is_dir=is_dir, size=size, sha256=digest,
                           deleted_at=now.isoformat(), deleted_by=deleted_by or accepted.actor,
                           confirmed_by=accepted.actor, remind_at=(now + timedelta(days=self.retention_days)).isoformat(),
                           plan_sha256=accepted.sha256)
        with self._lock():
            entries, roots = self._load()
            if len(entries) >= MAX_ENTRIES:
                raise TrashError("The trash is full (too many entries); empty it first.")
            if str(real_root) not in roots:   # recorded before anything lands in it: no AI ever reaches it
                roots.append(str(real_root))
                self._save(entries, roots)
            folder = real_root / TRASH_FOLDER
            folder.mkdir(mode=0o700, exist_ok=True)
            if folder.is_symlink() or not folder.is_dir():
                raise TrashError("The trash folder is not a plain folder; nothing was moved.")
            target = folder / entry_id
            target.mkdir(mode=0o700)
            try:
                os.rename(real, target / real.name)
            except OSError as e:
                target.rmdir()
                if e.errno == errno.EXDEV:
                    raise TrashError(f"{label} lies on another disk than its trash; it is not copied. Delete it for "
                                     "good instead, or move it yourself.") from None
                raise TrashError(f"{label} could not be moved to the trash ({type(e).__name__}).") from None
            entries[entry_id] = entry
            try:
                self._save(entries, roots)
            except OSError:
                os.rename(target / real.name, real)   # without its entry it would be lost to the person
                target.rmdir()
                raise TrashError("The trash's list cannot be written; nothing was moved.") from None
        return entry

    # --- restoring ------------------------------------------------------------------------------------------------

    def restore(self, entry_id: str, actor: str) -> Path:
        """Moves an entry back to where it was, or next to it as `<name>-restored-<time>` when that place is taken.
        The person (actor gui or cli) restores any entry; any other actor only an entry it asked to delete itself
        (TrashError trash_not_yours). Never through a link, never out of its data folder."""
        from orchestrator.Core.deletion import person_actor
        with self._lock():
            entries, roots = self._load()
            entry = entries.get(entry_id) if isinstance(entry_id, str) else None
            if entry is None:
                raise TrashError("There is no such item in the trash.", code="trash_unknown")
            if not (person_actor(actor) and actor != "framework") and entry.deleted_by != actor:
                raise TrashError("The person deleted this, so only the person restores it (File > Trash... or "
                                 "'llm-cli trash restore').", code="trash_not_yours")
            stored = entry.stored()
            if not os.path.lexists(stored):
                raise TrashError(f"{entry.label} is no longer in the trash folder.", code="trash_unknown")
            root = Path(entry.root)
            relative = Path(entry.relative)
            if relative.is_absolute() or not relative.parts or any(p in ("", ".", "..") for p in relative.parts):
                raise TrashError(f"The place of {entry.label} is not inside its data folder; nothing was restored.")
            dest = root / relative
            try:
                if root.is_symlink() or root.resolve(strict=True) != root:
                    raise TrashError(f"The data folder of {entry.label} changed (it is a link now); nothing was "
                                     "restored.")
                folder = root
                for part in relative.parts[:-1]:   # every folder on the way is a plain folder, or made as one
                    folder = folder / part
                    if not os.path.lexists(folder):
                        folder.mkdir(mode=0o700)
                    elif folder.is_symlink() or not folder.is_dir():
                        raise TrashError(f"The place of {entry.label} is a link or not a folder now; nothing was "
                                         "restored.")
                if os.path.lexists(dest):
                    dest = dest.with_name(restored_name(dest.name, _now().strftime("%Y%m%d_%H%M%S")))
                    if os.path.lexists(dest):
                        raise TrashError(f"The place of {entry.label} is taken; try again in a second.")
                os.rename(stored, dest)
            except TrashError:
                raise
            except OSError as e:
                raise TrashError(f"{entry.label} could not be restored ({type(e).__name__}).") from None
            del entries[entry.id]
            try:
                self._save(entries, roots)
            finally:
                try:
                    stored.parent.rmdir()
                except OSError:
                    pass
        self._record(actor, "trash_restored", {"trash_id": entry.id, "kind": entry.kind, "bytes": entry.size},
                     f"{entry.label} restored from the trash to {entry.origin}")
        return dest

    def remind_later(self, entry_ids: Iterable[str], actor: str) -> int:
        """The person's "Keep longer": the reminder of these entries moves by the retention period. Deletes
        nothing. Returns how many entries were changed."""
        from orchestrator.Core.deletion import person_actor
        if not person_actor(actor) or actor == "framework":
            raise PermissionError("Only the person changes the trash's reminders.")
        wanted = set(entry_ids)
        later = (_now() + timedelta(days=self.retention_days)).isoformat()
        with self._lock():
            entries, roots = self._load()
            changed = 0
            for entry_id in wanted & set(entries):
                entries[entry_id] = entries[entry_id].model_copy(update={"remind_at": later})
                changed += 1
            if changed:
                self._save(entries, roots)
        return changed

    # --- emptying (the person's alone) ----------------------------------------------------------------------------

    def plan(self, entry_ids: Optional[Iterable[str]] = None, due_only: bool = False) -> Optional[DeletionPlan]:
        """The deletion plan of emptying the trash (every entry, the given ids or those past their reminder): each
        entry with its speaking name, its full path in the trash, its name, its size and what it is. Entries whose
        moved item is gone are left out. None when nothing would be deleted."""
        wanted = None if entry_ids is None else set(entry_ids)
        due = {e.id for e in self.due()} if due_only else None
        items = []
        for entry in self.entries():
            if (wanted is not None and entry.id not in wanted) or (due is not None and entry.id not in due):
                continue
            if not self.present(entry):
                continue
            items.append(DeletionItem(
                kind="trash_entry", speaking_name=entry.label, full_path=str(entry.stored()), file_name=entry.name,
                size_bytes=tree_size(entry.stored()),
                what=f"{entry.what()} moved to the trash on {entry.deleted_at[:10]} from {entry.origin}"[:300]))
        return plan_of(items, ("permanent",))

    def empty(self, accepted: AcceptedDeletion) -> EmptyResult:
        """Deletes for good exactly the entries of the plan the person accepted in the trash window or at a terminal
        (never an AI, never under a delegation, never through a tool). The plan is looked up again first: if it
        changed, nothing is deleted (PlanChanged). The emptying is audited before anything is deleted; nothing is
        deleted when the audit cannot be written."""
        accepted = require_person(accepted)
        if accepted.actor not in ("gui", "cli"):
            raise PermissionError("Only the person empties the trash, in the trash window or at a terminal.")
        ids = [Path(i.full_path).parent.name for i in accepted.plan.items]
        if any(not _ENTRY_ID.fullmatch(i) for i in ids):
            raise PlanChanged("the trash")
        result = EmptyResult()
        with self._lock():
            require_same(accepted, self.plan(ids), "the trash")
            if self.audit is None or not callable(getattr(self.audit, "record", None)):
                raise RuntimeError("The audit log is not available, so the trash was not emptied.")
            try:
                self.audit.record(accepted.actor, "trash", "framework", "person", "trash_emptied", approved=True,
                                  arguments={"entries": len(ids), "bytes": accepted.plan.total_bytes},
                                  detail=f"emptying {len(ids)} trash entries for good", extra=accepted.audit_extra())
            except Exception:  # noqa: BLE001 - the reason could name a host path
                raise RuntimeError("The audit log cannot record the emptying; nothing was deleted.") from None
            entries, roots = self._load()
            for item, entry_id in zip(accepted.plan.items, ids):
                entry = entries.get(entry_id)
                folder = Path(item.full_path).parent
                try:
                    if folder.is_symlink():
                        raise OSError("the entry's folder is a link")
                    shutil.rmtree(folder)   # never follows a link inside the folder
                except OSError as e:
                    result.failed.append((item.speaking_name, type(e).__name__))
                    continue
                entries.pop(entry_id, None)
                result.deleted.append(entry.label if entry else item.speaking_name)
                result.freed_bytes += item.size_bytes
            self._save(entries, roots)
        return result

    def _record(self, actor: str, outcome: str, arguments: Dict[str, Any], detail: str) -> None:
        if self.audit is None or not callable(getattr(self.audit, "record", None)):
            return
        try:
            self.audit.record(actor, "trash", "framework", "person" if actor in ("gui", "cli") else "ask", outcome,
                              approved=True, arguments=arguments, detail=detail)
        except Exception:  # noqa: BLE001 - restoring deletes nothing; its entry is a record, not a guard
            pass


def describe(entry: TrashEntry) -> Dict[str, str]:
    """An entry as the person's windows show it."""
    return {"id": entry.id, "name": entry.label, "what": entry.what(), "size": size_text(entry.size),
            "from": str(entry.original()), "deleted": entry.deleted_at.replace("T", " ")[:16],
            "by": entry.deleted_by, "remind": entry.remind_at[:10], "where": str(entry.stored())}


__all__ = ["DEFAULT_RETENTION_DAYS", "EmptyResult", "INDEX_FILE", "KIND", "TRASH_FOLDER", "Trash", "TrashEntry",
           "TrashError", "WHAT", "describe", "file_sha256", "restored_name", "trash_folders", "tree_size"]
