#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Helper Utilities (v2.3.0)
DIREKTIVE: Goldstandard. Secure file operations.

Zweck:
Allgemeine Hilfsfunktionen für Dateioperationen, Sicherheit und System-Checks.
Vermeidet zirkuläre Abhängigkeiten durch strikte Trennung von Business-Logik.
"""

import os
import json
import subprocess
import errno
import hashlib
import hmac
import stat
import threading
import zipfile
import tarfile
import re
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from orchestrator.security import secret_patterns

def ensure_directory(path: Union[str, Path], mode: int = 0o755) -> Path:
    """Creates directory if not exists."""
    p = Path(path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        try:
            p.chmod(mode)
        except Exception: pass
    return p

def safe_json_load(path: Path, default: Any = None, kind: Optional[type] = None) -> Any:
    """The JSON in the file `path`, or `default` when it is missing, cannot be read or is no JSON; with `kind` (for
    example dict) also when the JSON is of another type. Never raises for the file's state."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return default
    return data if kind is None or isinstance(data, kind) else default

def windowless_flags() -> int:
    """The creation flags of a console program the framework runs and reads: CREATE_NO_WINDOW on Windows, so no
    console window flashes up when the GUI (or another program without a console) starts it; 0 elsewhere. The one
    value execute_command and the pinned source's git (runtime/build/source.Git) use."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0


def execute_command(command: List[str], cwd=None, timeout=None, env=None):
    """Runs a command without a shell and returns (returncode, stdout, stderr); never raises for the command: a
    missing program, a hang past `timeout` or another failure to start gives (-1, "", the reason). stdin is closed (a
    command that asks for input ends instead of waiting), output that is not UTF-8 is decoded with replacement
    characters, and on Windows no console window opens (windowless_flags)."""
    flags = windowless_flags()
    try:
        res = subprocess.run(list(command), cwd=cwd, timeout=timeout, env=env, capture_output=True, text=True,
                             errors="replace", check=False, stdin=subprocess.DEVNULL, creationflags=flags)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return -1, "", str(e)
    return res.returncode, res.stdout or "", res.stderr or ""

def safe_extract_archive(archive_path: Path, dest_dir: Path):
    """
    Secure extraction preventing Zip Slip/Tar Slip vulnerabilities.
    """
    dest_dir = Path(dest_dir).resolve()

    def inside(path: Path) -> bool:
        # Path containment, not string prefixes ("/x/out2" starts with "/x/out").
        return path.resolve().is_relative_to(dest_dir)

    if str(archive_path).endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as zf:
            for member in zf.infolist():
                if not inside(dest_dir / member.filename):
                    raise Exception(f"Security: Zip Slip attempt detected: {member.filename}")
            # Every member path was checked above.
            zf.extractall(dest_dir)  # nosec B202

    elif str(archive_path).endswith(('.tar.gz', '.tgz', '.tar')):
        with tarfile.open(archive_path, 'r:*') as tf:
            members = tf.getmembers()
            for member in members:
                target = dest_dir / member.name
                if not inside(target):
                    raise Exception(f"Security: Tar Slip attempt detected: {member.name}")
                if member.issym() and not inside(target.parent / member.linkname):
                    raise Exception(f"Security: Tar Slip via symlink detected: {member.name} -> {member.linkname}")
                if member.islnk() and not inside(dest_dir / member.linkname):
                    raise Exception(f"Security: Tar Slip via hardlink detected: {member.name} -> {member.linkname}")
                if member.isdev():
                    raise Exception(f"Security: Device file in archive: {member.name}")

            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest_dir, members=members, filter="data")
            else:
                # Without extraction filters, chained links could still escape; only plain files and dirs are allowed.
                links = [m.name for m in members if m.issym() or m.islnk()]
                if links:
                    raise Exception(f"Security: links in archive need Python >= 3.10.12 / 3.11.4: {links[:3]}")
                tf.extractall(dest_dir, members=members)  # nosec B202

def calculate_file_checksum(path: Path, algorithm="sha256") -> str:
    """Calculates hash of a file ('' when the file is missing). The original of the file-hash helpers below."""
    if not path.exists(): return ""
    with open(path, 'rb') as f:
        return _digest(lambda: f.read(HASH_CHUNK), hashlib.new(algorithm))


# --- File hashing: the one implementation (consolidation c1-hashing, maintainer rule R0) -----------------------------
# Every file sha256 of the framework runs through _digest: whole files (sha256_file, try_sha256_file), open handles
# and their first N bytes (sha256_fileobj, sha256_fd), and copies hashed while they are written (copy_hashed), so the
# bytes that are hashed are the bytes that are used. Old names elsewhere are one-line delegates. Standard library only.
# Not here on purpose: runtime/hardware_probe.py stays standalone (it runs on boards without the package), the
# sha256sum calls that run on a device, and digests of small byte strings already in memory (sha256_bytes covers
# those that want a name).

HASH_CHUNK = 1 << 20


class HashStopped(Exception):
    """A hash or hashed copy ended early because its `cancel` callback returned a true value."""


def _digest(read: Callable[[], bytes], hasher, *, sink: Optional[Callable[[bytes], Any]] = None,
            cancel: Optional[Callable[[], Any]] = None, progress: Optional[Callable[[int], Any]] = None) -> str:
    """The one hashing loop: `cancel` is asked before every read (it stops by raising its own exception, or by
    returning a true value: HashStopped); every chunk read goes to the hasher and then to `sink`; `progress` gets the
    bytes done so far."""
    done = 0
    while True:
        if cancel is not None and cancel():
            raise HashStopped("stopped")
        chunk = read()
        if not chunk:
            return hasher.hexdigest()
        hasher.update(chunk)
        if sink is not None:
            sink(chunk)
        done += len(chunk)
        if progress is not None:
            progress(done)


def _open_flags(follow_links: bool) -> int:
    return os.O_RDONLY | getattr(os, "O_BINARY", 0) | (0 if follow_links else getattr(os, "O_NOFOLLOW", 0))


def _open_read(path: Union[str, Path], follow_links: bool):
    """A binary reader of `path`; without follow_links never through a link (refused before the open, and by the open
    itself where the system has O_NOFOLLOW)."""
    if follow_links:
        return open(path, "rb")
    if os.path.islink(path):
        raise OSError(errno.ELOOP, "a link is not followed", str(path))
    fd = os.open(str(path), _open_flags(False))
    try:
        return open(fd, "rb")
    except BaseException:  # a folder: the reader refuses the descriptor but leaves it open
        os.close(fd)
        raise


def sha256_file(path: Union[str, Path], *, chunk: int = HASH_CHUNK, cancel: Optional[Callable[[], Any]] = None,
                progress: Optional[Callable[[int], Any]] = None, follow_links: bool = True) -> str:
    """sha256 (lower-case hex) of a file; OSError when it cannot be read (with follow_links=False also for a link)."""
    with _open_read(path, follow_links) as f:
        return _digest(lambda: f.read(chunk), hashlib.sha256(), cancel=cancel, progress=progress)


def try_sha256_file(path: Union[str, Path]) -> Optional[str]:
    """sha256 of a regular file, or None for a link, a folder, a missing or unreadable file."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        return sha256_file(path, follow_links=False)
    except OSError:
        return None


def git_blob_id(path: Union[str, Path], *, follow_links: bool = True) -> str:
    """The id git gives a file's bytes: sha1 of "blob <size>\\0" and the bytes. It is the Hugging Face Hub's ETag of a
    file stored without LFS, and huggingface_hub's cache names that file's blob by it. A check against a damaged or
    other file, not a security digest. OSError when the file cannot be read."""
    with _open_read(path, follow_links) as f:
        size = os.fstat(f.fileno()).st_size
        return _digest(lambda: f.read(HASH_CHUNK), hashlib.sha1(b"blob %d\0" % size, usedforsecurity=False))


def sha256_fileobj(fh, *, limit: Optional[int] = None, cancel: Optional[Callable[[], Any]] = None,
                   chunk: int = HASH_CHUNK) -> str:
    """sha256 of what an open binary file gives from its current position: to its end, or only its next `limit` bytes
    (fewer at its end). The handle is left after the bytes read, so the same handle can be used (or measured) next."""
    if limit is None:
        return _digest(lambda: fh.read(chunk), hashlib.sha256(), cancel=cancel)
    left = [max(0, int(limit))]

    def read() -> bytes:
        data = fh.read(min(chunk, left[0])) if left[0] else b""
        left[0] -= len(data)
        return data
    return _digest(read, hashlib.sha256(), cancel=cancel)


def _read_at(fd: int, offset: int, size: int) -> bytes:
    """Reads at an offset of a descriptor: pread where the system has it (the position stays), else seek and read."""
    if hasattr(os, "pread"):
        return os.pread(fd, size, offset)
    os.lseek(fd, offset, os.SEEK_SET)  # pragma: no cover - systems without pread (Windows)
    return os.read(fd, size)  # pragma: no cover


def _fd_reader(fd: int, chunk: int) -> Callable[[], bytes]:
    offset = [0]

    def read() -> bytes:
        data = _read_at(fd, offset[0], chunk)
        offset[0] += len(data)
        return data
    return read


def sha256_fd(fd: int, *, cancel: Optional[Callable[[], Any]] = None, chunk: int = HASH_CHUNK) -> str:
    """sha256 of a whole open file descriptor, read from its start by offset; the descriptor stays open."""
    return _digest(_fd_reader(fd, chunk), hashlib.sha256(), cancel=cancel)


def copy_hashed(source: Union[int, str, Path], target: Union[str, Path], *, cancel: Optional[Callable[[], Any]] = None,
                follow_links: bool = False, fsync: bool = False, mode: int = 0o666,
                chunk: int = HASH_CHUNK) -> str:
    """Copies `source` (an open descriptor, read from its start, or a path opened once and, unless follow_links, never
    through a link) into the new file `target` (refused when it exists: FileExistsError) and returns the sha256 of the
    bytes read: the copy holds exactly what was hashed. Nothing is removed here: on an error or a cancel the caller
    removes what it made."""
    owned = not isinstance(source, int)
    fd = os.open(str(source), _open_flags(follow_links)) if owned else source
    try:
        if stat.S_ISDIR(os.fstat(fd).st_mode):  # refused before the target exists
            raise IsADirectoryError(errno.EISDIR, "a folder is not copied", str(source))
        out = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
        with open(out, "wb") as dst:
            digest = _digest(_fd_reader(fd, chunk), hashlib.sha256(), sink=dst.write, cancel=cancel)
            if fsync:
                dst.flush()
                os.fsync(dst.fileno())
        return digest
    finally:
        if owned:
            os.close(fd)


def hash_stream(chunks, *, sink: Optional[Callable[[bytes], Any]] = None,
                cancel: Optional[Callable[[], Any]] = None) -> str:
    """sha256 of an iterable of byte chunks (a download), each chunk passed on to `sink` (empty chunks are skipped)."""
    it = (c for c in chunks if c)
    return _digest(lambda: next(it, b""), hashlib.sha256(), sink=sink, cancel=cancel)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_matches(actual: Any, expected: Any) -> bool:
    """Constant-time comparison of two hex digests, case-insensitive; False when either is empty or not ASCII text."""
    if not actual or not expected:
        return False
    a, e = str(actual).lower(), str(expected).lower()
    if not (a.isascii() and e.isascii()):
        return False
    return hmac.compare_digest(a, e)


class HashCache:
    """sha256 of files remembered by (path, size, modification time in ns): a retried question hashes a large file
    once. Thread-safe; the hash itself runs outside the lock. More than `cap` entries start the cache over."""

    def __init__(self, cap: int = 64, hasher: Optional[Callable[[Path], str]] = None):
        self.cap = cap
        self._hasher = hasher  # None: sha256_file, looked up at each hash
        self._items: dict = {}
        self._lock = threading.Lock()

    def sha256(self, path: Union[str, Path]) -> str:
        st = os.stat(path)
        key = (str(path), st.st_size, st.st_mtime_ns)
        with self._lock:
            if key in self._items:
                return self._items[key]
        digest = (self._hasher or sha256_file)(Path(path))
        with self._lock:
            if len(self._items) > self.cap:
                self._items.clear()
            self._items[key] = digest
        return digest


def sha256sums_line(digest: str, name: Union[str, Path]) -> str:
    """One line of sha256sum's format: two spaces, POSIX separators (the file is checked on a Linux board, also when
    it was made on Windows) and LF (sha256sum before coreutils 9.0 reads a CR as part of the name)."""
    return f"{digest}  {Path(name).as_posix() if isinstance(name, Path) else str(name).replace(os.sep, '/')}\n"


def sha256sums_text(entries) -> str:
    """The text of a SHA256SUMS file from (digest, name) pairs."""
    return "".join(sha256sums_line(digest, name) for digest, name in entries)


def write_sha256sums(entries, out: Union[str, Path]) -> None:
    """Writes a SHA256SUMS file from (digest, name) pairs; `entries` may be a generator that hashes as it goes."""
    with open(out, "w", newline="\n", encoding="utf-8") as f:
        for digest, name in entries:
            f.write(sha256sums_line(digest, name))


def write_sha256_sidecar(path: Union[str, Path], digest: str) -> Path:
    """<file>.sha256 beside a file, in sha256sum's format (LF on every host)."""
    sidecar = Path(path).with_name(Path(path).name + ".sha256")
    sidecar.write_text(sha256sums_line(digest, Path(path).name), encoding="ascii", newline="\n")
    return sidecar


def read_sha256_sidecar(path: Union[str, Path]) -> str:
    """The digest a <file>.sha256 beside `path` names (OSError, IndexError or UnicodeDecodeError when it cannot)."""
    return Path(path).with_name(Path(path).name + ".sha256").read_text(encoding="ascii").split()[0]

# The one table of secret formats, shared with scripts/secret_scan.py.
SECRET_PATTERNS = secret_patterns.PATTERNS

def redact_secrets(text: str) -> str:
    """Replaces known API key/token formats, credential headers and query parameters before text leaves the
    machine or reaches a log."""
    if not text:
        return ""
    return secret_patterns.redact(text)

def sanitize_filename(name: str) -> str:
    """Removes illegal characters for filenames."""
    name = str(name).strip().replace(" ", "_")
    return re.sub(r'(?u)[^-\w.]', '', name)
