#!/usr/bin/env python3
"""Cross-process lock for the per-user files that the GUI, the CLI and MCP servers change at the same time.

Built on `filelock` (an OS lock on the lock file, released by the OS when the holder dies, so no stale
lock is ever left behind). Not reentrant: take it once per read-modify-write.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

from filelock import FileLock, Timeout


class LockTimeout(TimeoutError):
    """Another process (or thread) held the lock longer than the timeout."""


@contextmanager
def file_lock(path: Union[str, Path], timeout: float = 30.0) -> Iterator[None]:
    """Holds the lock file `path` (created with mode 0600) for the duration of the block."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = FileLock(str(path), timeout=timeout, mode=0o600)
    try:
        lock.acquire()
    except Timeout:
        raise LockTimeout(f"{path} is held by another process (GUI, CLI or MCP server); "
                          "try again when it has finished.") from None
    try:
        yield
    finally:
        lock.release()
