#!/usr/bin/env python3
"""
The cross-process lock of the local knowledge base's storage, and deleting the framework's own storage folders.

Kept apart from the storage engine (orchestrator/rag/edge_store.py): the lock is also taken where Qdrant Edge cannot be
loaded (a computer without a build of it uses the older local storage) and around the conversion of the old storage.
filelock works on Linux and Windows. thread_local=False: the storage may be opened in one thread and released by the
idle timer's thread.
"""

import shutil
import sys
from pathlib import Path

LOCK_SUFFIX = ".lock"


def lock_path(root: Path) -> Path:
    """The lock file of a storage folder: next to it, so it also covers building the folder (the conversion)."""
    root = Path(root)
    return root.with_name(root.name + LOCK_SUFFIX)


def storage_lock(root: Path):
    """The cross-process lock of the storage folder `root` (not acquired yet)."""
    from filelock import FileLock
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path), thread_local=False)


def try_lock(lock) -> bool:
    """Takes `lock` without waiting; False when another process (or another lock object) holds it."""
    from filelock import Timeout
    try:
        lock.acquire(timeout=0)
        return True
    except Timeout:
        return False


def remove_tree(path: Path) -> None:
    """Deletes a folder of the framework's own storage, going on past a file that cannot be deleted (on Windows: one
    that is still open); the caller checks whether the folder is gone."""
    def skip(*_args) -> None:
        return None

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=skip)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=skip)
