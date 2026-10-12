#!/usr/bin/env python3
"""
Knowledge-base snapshots as the person sees them, and the reminder when there are more than usual.

Before every Deep Ingest, every index of the framework's own code and every restore, the knowledge base is saved as a
snapshot (`knowledge_snapshots/` in the per-user folder), so the change can be undone. Nothing deletes a snapshot by
itself, on any channel (round 5 f: deleting always asks the person, with the list): above MAX_SNAPSHOTS the doctor
and the chat remind the person, and `llm-cli knowledge snapshots` lists each one with its full path, file name, size
and what it is. Deleting them is the person's decision; the trash and the framework's deletion dialog follow (design
WP7).

This module reads the folder only; it never opens the knowledge base itself (no embedding model, no Qdrant).
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

FOLDER = "knowledge_snapshots"
SUFFIX = ".jsonl.gz"
MAX_SNAPSHOTS = 5   # the usual number; more only bring a reminder, never a deletion
REMINDER_KEY = "chat.notice.snapshots_over_cap"

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


def over_cap(snapshots: List[SnapshotFile]) -> bool:
    return len(snapshots) > MAX_SNAPSHOTS


# Which programs may write the chat reminder: the ones the chat feed knows (request_store.set_program names the
# process at its entry point; an unnamed process writes no reminder, the doctor still reports it).
_FEED_PROGRAMS = {"gui": "gui", "cli": "cli", "mcp": "mcp"}


def remind_over_cap(framework: Any, snapshots: List[SnapshotFile]) -> Optional[Any]:
    """Posts the chat reminder that the knowledge base keeps more snapshots than usual (a framework notice; the full
    folder only in what the person sees). Returns the stored row, or None when nothing was written: not over the cap,
    no chat feed, a process without a known program, or the feed could not be written (it fails open; the doctor
    reports the same)."""
    if not over_cap(snapshots):
        return None
    store = getattr(framework, "chat_feed", None)
    if store is None:
        return None
    from orchestrator.Core.request_store import program
    kind = _FEED_PROGRAMS.get(program())
    if kind is None:
        return None
    from orchestrator.Core.chat_feed import FeedSender, FeedWriter
    total = sum(s.size_bytes for s in snapshots)
    folder = os.path.dirname(str(snapshots[-1].path))
    try:
        return FeedWriter(store, kind).write(
            "notice", FeedSender(role="framework"), text_key=REMINDER_KEY,
            params={"n": str(len(snapshots)), "size": size_text(total), "cap": str(MAX_SNAPSHOTS)},
            facts_person=[{"label": "Folder", "value": folder, "person_only": True}], level="warning")
    except Exception:  # noqa: BLE001 - a reminder never stops the snapshot or the ingest
        return None


__all__ = ["FOLDER", "MAX_SNAPSHOTS", "REMINDER_KEY", "SUFFIX", "SnapshotFile", "list_snapshots", "over_cap",
           "remind_over_cap", "size_text", "snapshot_files", "snapshot_folder"]
