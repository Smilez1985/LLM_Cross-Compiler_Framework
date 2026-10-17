#!/usr/bin/env python3
"""
Private files written atomically (consolidation chain c1-imports: moved unchanged from Core/vault_key.py and
runtime/llama_server.py, which keep the old names, so the utils and the kernel no longer import the Core for them).

- replace_file(src, dst): os.replace, tried again for a moment on Windows while another process has the target open
  (REPLACE_RETRIES times there, never elsewhere).
- write_private(path, text): the shared writer (the secrets store, profiles, trusted boards, the notices, the input
  history, the GUI bridge): the link is resolved first (a link onto an encrypted volume keeps pointing there), the
  folder is made 0700, a temporary ".<name>.<random>.tmp" file next to the target is written, flushed and synced,
  made 0600 (not on Windows) and moved over the target with replace_file.
- write_private_exclusive(path, text): the local runtime's state files (llama_server, the build store, the runtime
  mode): a ".<name>.<8 hex>.tmp" file created with O_EXCL and 0600, LF line ends on every system, no fsync, no link
  resolution (a link is replaced by the file), plain os.replace.
Neither leaves its temporary file behind. The other writers (JSON and signed stores) are unified later (chain
c5-io-stores).
"""

import os
import platform
import secrets
import tempfile
import time
from pathlib import Path

REPLACE_RETRIES = 10 if os.name == "nt" else 0


def replace_file(src, dst) -> None:
    """os.replace, tried again for a moment on Windows while another process has the target open."""
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt >= REPLACE_RETRIES:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_private(path: Path, text: str) -> None:
    """Atomic write with mode 0600 (temp file in the same folder, then rename)."""
    path = Path(os.path.realpath(path))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if platform.system() != "Windows":
            os.chmod(tmp, 0o600)
        replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_private_exclusive(path: Path, text: str) -> None:
    """Writes a file only the current user can read (created 0600, replaced atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


__all__ = ["REPLACE_RETRIES", "replace_file", "write_private", "write_private_exclusive"]
