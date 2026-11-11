#!/usr/bin/env python3
"""
The kept F16 intermediates of GGUF builds (DESIGN "Build pipeline and target modules", the row "Large models").

A quantized GGUF build converts the model to F16 first and quantizes from that file. The convert stage writes it to one
place in the build's work folder (F16_FILE), where the importance-matrix job and the build job of the same build share
it. After a finished build of a Hugging Face model at a known commit the build engine keeps the file here, so the next
build of the same model (another type, an Auto-Tuning run, the recommended type) quantizes from it without converting
again; the folder is then mounted read-only into that build.

- The key: the repository (case-insensitive), the commit and the llama.cpp pin of the image's converter (key()). A
  local model folder has no commit and is never kept: a changed folder would reuse a wrong model.
- The folder: <cache>/f16/<key>/ with model-F16.gguf and f16.json (repository, commit, pin, size, kept, last used).
- Keeping needs room: the disk must keep more than twice its reserve free with the file on it (keep_allowed), and the
  setting keep_f16_intermediates must be on (the default).
- The person lists and deletes them (llm-cli build f16 list|delete, Build > F16 Intermediates...): deletion_plan() and
  delete() with the deletion dialog's accepted plan, into the trash or for good, audited, never by an AI.
"""

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional

CACHE_FOLDER = "f16"
FILE_NAME = "model-F16.gguf"
META_FILE = "f16.json"
SETTING = "keep_f16_intermediates"
MOUNT = "/build-cache/f16"               # the kept folder in a build container (read-only)
WORK_FILE = "/build-cache/work/f16/" + FILE_NAME   # where a build writes its own F16 (the work folder)
_KEY = re.compile(r"^[0-9a-f]{24}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[a-z0-9][\w.-]*/[a-z0-9][\w.-]*$")


def key(repo: str, commit: str, pin: str) -> Optional[str]:
    """The cache key of a Hugging Face model at `commit` converted by llama.cpp `pin`; None when one is missing (no
    commit: never kept)."""
    repo, commit, pin = str(repo or "").strip().lower(), str(commit or "").strip().lower(), str(pin or "").strip()
    if not _REPO.match(repo) or not _COMMIT.match(commit) or not pin:
        return None
    return hashlib.sha256(f"{repo}\n{commit}\n{pin}".encode("utf-8")).hexdigest()[:24]


def setting_on(config: Any) -> bool:
    from orchestrator.Core.build_defaults import as_bool
    get = getattr(config, "get", None)
    try:
        value = get(SETTING, True) if callable(get) else getattr(config, SETTING, True)
    except Exception:  # noqa: BLE001 - an unreadable setting is its default
        value = True
    return as_bool(value, True)


@dataclass(frozen=True)
class Entry:
    key: str
    folder: Path
    file: Path
    size_bytes: int
    repo: str
    commit: str
    pin: str
    kept: str = ""
    last_used: str = ""

    def label(self) -> str:
        return f"{self.repo}@{self.commit[:12]} (llama.cpp {self.pin})"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class F16Cache:
    def __init__(self, cache_dir: Path):
        self.root = Path(cache_dir) / CACHE_FOLDER

    @classmethod
    def for_framework(cls, framework: Any) -> "F16Cache":
        from orchestrator.Core.disk_plan import folder
        return cls(folder(framework, "cache_dir", "cache"))

    def folder(self, cache_key: str) -> Path:
        if not _KEY.match(str(cache_key or "")):
            raise ValueError("an F16 cache key is 24 hexadecimal characters")
        return self.root / cache_key

    def entry(self, cache_key: Optional[str]) -> Optional[Entry]:
        if not cache_key or not _KEY.match(cache_key):
            return None
        folder = self.root / cache_key
        file = folder / FILE_NAME
        try:
            meta = json.loads((folder / META_FILE).read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or file.is_symlink() or not file.is_file():
                return None
            size = file.stat().st_size
        except (OSError, ValueError):
            return None
        if int(meta.get("bytes") or -1) != size:
            return None   # a file that changed since it was kept is not reused
        return Entry(cache_key, folder, file, size, str(meta.get("repo") or ""), str(meta.get("commit") or ""),
                     str(meta.get("pin") or ""), str(meta.get("kept") or ""), str(meta.get("last_used") or ""))

    def entries(self) -> List[Entry]:
        if not self.root.is_dir():
            return []
        found = [self.entry(p.name) for p in sorted(self.root.iterdir()) if p.is_dir() and _KEY.match(p.name)]
        return [e for e in found if e is not None]

    def total_bytes(self) -> int:
        return sum(e.size_bytes for e in self.entries())

    def touch(self, cache_key: str) -> None:
        """Records that a build used the kept file (last_used)."""
        entry = self.entry(cache_key)
        if entry is None:
            return
        self._write_meta(entry.folder, {"repo": entry.repo, "commit": entry.commit, "pin": entry.pin,
                                        "bytes": entry.size_bytes, "kept": entry.kept, "last_used": _now()})

    @staticmethod
    def _write_meta(folder: Path, meta: dict) -> None:
        tmp = folder / f".{META_FILE}.{os.getpid()}.tmp"
        tmp.write_bytes((json.dumps(meta, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        os.replace(tmp, folder / META_FILE)

    def keep(self, cache_key: str, source: Path, *, repo: str, commit: str, pin: str) -> Entry:
        """Moves a build's F16 (`source`, in the build's work folder on the same disk) into the cache. A file already
        kept under the key stays (the source is left for the build's cleanup)."""
        existing = self.entry(cache_key)
        if existing is not None:
            return existing
        folder = self.folder(cache_key)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / FILE_NAME
        part = folder / (FILE_NAME + ".part")
        try:
            os.replace(source, part)
        except OSError:
            shutil.copyfile(source, part)   # another file system: a copy (the build's cleanup removes the source)
        size = part.stat().st_size
        os.chmod(part, 0o644)
        os.replace(part, target)
        self._write_meta(folder, {"repo": repo, "commit": commit, "pin": pin, "bytes": size, "kept": _now(),
                                  "last_used": _now()})
        entry = self.entry(cache_key)
        if entry is None:
            raise OSError("the kept F16 intermediate could not be read back")
        return entry

    # --- the person's list and deletion ---
    def deletion_plan(self):
        from orchestrator.Core.deletion import plan_of
        from orchestrator.tools.spec import DeletionItem
        items = [DeletionItem(kind="f16_intermediate", speaking_name=f"F16 intermediate of {e.label()}",
                              full_path=str(e.folder), file_name=e.folder.name, size_bytes=e.size_bytes,
                              what=f"the F16 GGUF a build converted from {e.repo}, kept {e.kept[:10] or 'earlier'} "
                                   f"for the next build of this model; last used {e.last_used[:10] or 'never'}"[:300])
                 for e in self.entries()]
        return plan_of(items, ("trash", "permanent"))

    def delete(self, accepted: Any, trash: Any = None, audit: Any = None):
        """Deletes exactly the entries the person accepted (Core/deletion.py): into the trash or for good. Only the
        person; the list is checked again first; the deletion is audited before anything goes."""
        from orchestrator.Core.deletion import DeletionResult, note_kept, require_person, require_same, same_items
        from orchestrator.utils.atomic import remove_tree
        accepted = require_person(accepted)
        if accepted.actor not in ("gui", "cli"):
            raise PermissionError("Only the person deletes kept F16 intermediates, in the GUI or at a terminal.")
        require_same(accepted, same_items(self.deletion_plan(), accepted.plan), "the list of F16 intermediates")
        if accepted.mode == "trash" and trash is None:
            raise RuntimeError("The trash is not available; nothing was deleted.")
        if audit is None or not callable(getattr(audit, "record", None)):
            raise RuntimeError("The audit log is not available, so nothing was deleted.")
        names = [i.speaking_name for i in accepted.plan.items]
        try:
            audit.record(accepted.actor, "f16_intermediates", "framework", "person", "started", approved=True,
                         arguments={"entries": names, "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                         detail=f"{len(names)} F16 intermediates "
                                + ("moved to the trash" if accepted.mode == "trash" else "deleted for good"),
                         extra=accepted.audit_extra())
        except Exception:  # noqa: BLE001 - the reason could name a host path
            raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
        result = DeletionResult()
        for item in accepted.plan.items:
            path = Path(item.full_path)
            try:
                if accepted.mode == "trash":
                    entry = trash.move("f16_intermediate", path, self.root, "cache", item.speaking_name, accepted,
                                       accepted.actor)
                    result.trash_ids.append(entry.id)
                else:
                    if note_kept(result, item.speaking_name, path, remove_tree(path)):
                        continue
                    result.freed_bytes += item.size_bytes
            except Exception as e:  # noqa: BLE001 - kept and reported
                result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
                continue
            result.done.append(item.speaking_name)
        return result


def keep_allowed(folder: Path, disk_usage: Optional[Callable] = None) -> bool:
    """Room to keep a file that already lies on the disk of `folder`: the disk keeps more than twice its reserve free
    with it (the disk rule's reserve, disk_plan.reserve_for)."""
    from orchestrator.Core.disk_plan import free_space, reserve_for
    try:
        usage = free_space(Path(folder), disk_usage)
    except OSError:
        return False
    return usage.free > 2 * reserve_for(usage.total)


__all__ = ["CACHE_FOLDER", "Entry", "F16Cache", "FILE_NAME", "META_FILE", "MOUNT", "SETTING", "WORK_FILE",
           "keep_allowed", "key", "setting_on"]
