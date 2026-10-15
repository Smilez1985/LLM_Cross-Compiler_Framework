#!/usr/bin/env python3
"""
Knowledge-base snapshots as the person sees them.

Before every Deep Ingest, every index of the framework's own code and every restore, the knowledge base is saved as a
snapshot (`knowledge_snapshots/` in the per-user folder), so the change can be undone. There is no limit: the framework
keeps as many as the disk holds and deletes none by itself, on any channel, and no count of them reminds or warns
(DECISIONS, sixth round; round 5 f: deleting always asks the person, with the full list). `llm-cli knowledge
snapshots` and Tools > Knowledge Snapshots... list each one with its full path, file name, size and what it is; to
free space the person deletes the ones they choose or exports them (copies them elsewhere). The trash and the
framework's deletion dialog follow (design WP7).

This module reads the folder only; it never opens the knowledge base itself (no embedding model, no Qdrant).
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

FOLDER = "knowledge_snapshots"
SUFFIX = ".jsonl.gz"
# What the person's listings say about keeping and deleting (the CLI and the GUI show the same words).
KEEPING_NOTE = ("There is no limit: the framework keeps as many snapshots as the disk holds and deletes no snapshot by "
                "itself. To free space, copy the ones you want to keep elsewhere (export) or delete the ones you no "
                "longer need yourself; a trash and a deletion dialog in the framework follow.")

# What a snapshot is, by the prefix the framework gives its name.
_WHAT = (("pre_ingest_", "backup of the knowledge base taken before a Deep Ingest"),
         ("pre_codebase_", "backup of the knowledge base taken before the framework's own code was indexed"),
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


__all__ = ["FOLDER", "KEEPING_NOTE", "SUFFIX", "SnapshotFile", "list_snapshots", "size_text", "snapshot_files",
           "snapshot_folder"]
