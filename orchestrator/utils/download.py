#!/usr/bin/env python3
"""
Pinned HTTPS downloads (moved here from orchestrator/runtime/llama_server.py, shared by the local runtime and
the community zoo).

PinnedDownloader.fetch resumes into <dest>.part; the file gets its final name only after its size and sha256
matched the pin. get_bytes reads a small file that is verified another way (the signed community index) with a
size cap. Redirects away from HTTPS are refused.
"""

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Optional

import psutil
import requests

from orchestrator.utils.atomic import remove_file
from orchestrator.utils.helpers import digest_matches
from orchestrator.utils.helpers import sha256_file  # download.sha256_file: community zoo, Hugging Face

MIB = 1024 ** 2
ProgressFn = Callable[[str, int, Optional[int]], None]


class DownloadError(Exception):
    """A download problem; the message is safe to show to the user."""


class IntegrityError(DownloadError):
    """A file that does not match its pin (or has none)."""


class DownloadStopped(DownloadError):
    """The download was stopped (its stop event was set); the `.part` file is kept for a later resume."""


class _Retry(Exception):
    """A transient problem; the download is resumed."""


def _gib(size: float) -> str:
    return f"{size / 1024 ** 3:.1f} GiB"


class FileLock:
    """Cross-process lock: an O_EXCL lock file holding the owner's pid and start time. A lock left behind by a
    process that no longer exists is taken over. The lock file is deleted with atomic.remove_file: on Windows a waiting
    process reading it at that moment makes the delete fail for a moment."""

    error = DownloadError
    busy = "{path} is in use by another llm-cli or GUI; try again when it has finished."

    def __init__(self, path: Path, timeout: float = 900, sleep: Callable[[float], None] = time.sleep):
        self.path, self.timeout, self.sleep = Path(path), timeout, sleep

    def _owner_alive(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return False  # released in the meantime
        try:
            pid, created = text.split()[:2]
            pid, created = int(pid), float(created)
        except ValueError:
            try:  # being written this very moment, or garbage that is old enough to ignore
                return time.time() - self.path.stat().st_mtime < 10
            except OSError:
                return False
        try:
            return abs(psutil.Process(pid).create_time() - created) < 0.01
        except psutil.NoSuchProcess:
            return False
        except psutil.Error:
            return True  # exists but is not inspectable: assume it is the owner

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        me = f"{os.getpid()} {psutil.Process().create_time()}"
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not self._owner_alive():
                    remove_file(self.path)
                    continue
                if time.monotonic() > deadline:
                    raise self.error(self.busy.format(path=self.path)) from None
                self.sleep(0.2)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(me)
            return self

    def __exit__(self, *exc) -> None:
        remove_file(self.path)


SPACE_MARGIN = 256 * MIB   # what a download leaves free when its caller gives no disk rule (`space`)
# The caller's disk rule: space(folder, needed, what) returns None when `needed` more bytes fit in `folder`, else the
# refusal (an exception; the domain passes Core/disk_plan's, this bottom layer imports nothing above it).
SpaceRule = Callable[[Path, int, str], Optional[Exception]]
# What a disk rule's refusal carries beside its message: the reason code, the AI's next step, the person's next steps
# and the disk checks a window shows in the person's language (Core/disk_plan.person_reason).
REASON_FIELDS = ("code", "next", "steps", "checks")


def with_reason(error: Exception, source: Any) -> Exception:
    """`error` (a download error, or the caller's own error class that wraps one) with the reason fields of `source`
    (REASON_FIELDS, those it has); returns `error`, so ``raise with_reason(PackError(str(e)), e)`` keeps them."""
    for name in REASON_FIELDS:
        value = getattr(source, name, None)
        if value:
            setattr(error, name, value)
    return error


class PinnedDownloader:
    """Downloads over HTTPS only. Subclasses may raise their own error classes (error, integrity_error). `space` is
    the caller's disk rule (SpaceRule); without one a download leaves SPACE_MARGIN free."""

    error = DownloadError
    integrity_error = IntegrityError
    lock_class = FileLock

    def __init__(self, session: Optional[requests.Session] = None, sleep: Callable[[float], None] = time.sleep,
                 logger=None, space: Optional[SpaceRule] = None):
        self._session = session
        self._sleep = sleep
        self.logger = logger
        self.space = space

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def ensure_space(self, folder: Path, needed: int, what: str = "the download") -> None:
        """Makes `folder` and raises self.error when `needed` more bytes do not fit there: by the caller's disk rule
        (`space`; the refusal's reason code, next step, steps and disk checks go with the error), else SPACE_MARGIN."""
        folder.mkdir(parents=True, exist_ok=True)
        if self.space is not None:
            refusal = self.space(folder, int(needed), what)
            if refusal is not None:
                raise with_reason(self.error(str(refusal)), refusal)
            return
        free = shutil.disk_usage(folder).free
        if free < needed + SPACE_MARGIN:
            raise self.error(f"Not enough free disk space in {folder} for {what}: {_gib(needed)} needed, "
                             f"{_gib(free)} free.")

    def fetch(self, url: str, dest: Path, size: Optional[int], sha256: Optional[str],
              progress: Optional[ProgressFn] = None, attempts: int = 5, stop: Optional[Any] = None) -> Path:
        """Resumable download into <dest>.part; the file only gets its final name after size and hash matched. With a
        `stop` event (threading.Event) that is set, the download ends between two chunks with DownloadStopped and the
        `.part` file is kept, so a later call resumes it."""
        if not (sha256 and size):
            raise self.integrity_error(f"No sha256 and size pinned for {dest.name}; refusing to download it.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_class(dest.with_name(f".{dest.name}.lock"), sleep=self._sleep):
            if dest.is_file():
                if dest.stat().st_size == size and digest_matches(sha256_file(dest), sha256):
                    return dest  # complete and verified by an earlier, interrupted run
                dest.unlink()
            part = dest.with_name(dest.name + ".part")
            have = part.stat().st_size if part.exists() else 0
            self.ensure_space(dest.parent, max(size - have, 0))
            for attempt in range(1, attempts + 1):
                have = part.stat().st_size if part.exists() else 0
                if have > size:
                    part.unlink()
                    have = 0
                if have == size:
                    break
                if stop is not None and stop.is_set():
                    raise DownloadStopped(f"The download of {dest.name} was stopped; the part is kept for a resume.")
                try:
                    self._download_range(url, part, have, size, dest.name, progress, stop)
                    if part.stat().st_size >= size:
                        break
                except _Retry as e:
                    if self.logger:
                        self.logger.warning(f"Download of {dest.name} interrupted ({e}), attempt {attempt}/{attempts}")
                if attempt == attempts:
                    raise self.error(f"Download of {dest.name} did not complete after {attempts} attempts; "
                                     "run the command again to resume.")
                self._sleep(min(2 ** attempt, 15))
            self.verify(part, size, sha256, dest.name)
            os.replace(part, dest)
            return dest

    def _download_range(self, url: str, part: Path, have: int, size: int, name: str,
                        progress: Optional[ProgressFn], stop: Optional[Any] = None) -> None:
        # identity: a compressed transfer would make the byte counts meaningless
        headers = {"Accept-Encoding": "identity", **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with self.session.get(url, headers=headers, stream=True, timeout=(15, 60), allow_redirects=True) as r:
                if not str(r.url).startswith("https://"):
                    raise self.error(f"Refusing a download that was redirected away from HTTPS: {r.url}")
                if r.status_code == 416:
                    part.unlink(missing_ok=True)  # our range does not exist on the server: start over
                    raise _Retry("HTTP 416")
                if r.status_code == 429 or r.status_code >= 500:
                    raise _Retry(f"HTTP {r.status_code}")
                if r.status_code not in (200, 206):
                    raise self.error(f"Download of {name} failed: HTTP {r.status_code}")
                resumed = bool(have) and r.status_code == 206
                if resumed:
                    match = re.match(r"bytes (\d+)-\d+/(\d+|\*)", str(r.headers.get("Content-Range", "")))
                    if not match or int(match.group(1)) != have:
                        part.unlink(missing_ok=True)  # a range we did not ask for
                        raise _Retry("unexpected Content-Range")
                    total = match.group(2)
                    remote = int(total) if total != "*" else None
                else:
                    have = 0
                    length = r.headers.get("Content-Length")
                    remote = int(length) if length and length.isdigit() else None
                if remote is not None and remote != size:
                    raise self.integrity_error(f"{name} has {remote} bytes on the server, but {size} are pinned; "
                                               "refusing it.")
                with open(part, "ab" if resumed else "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        f.write(chunk)
                        have += len(chunk)
                        if stop is not None and stop.is_set():
                            raise DownloadStopped(f"The download of {name} was stopped; the part is kept for a "
                                                  "resume.")
                        if have > size:
                            break  # more than pinned: the hash check refuses it
                        if progress:
                            progress(name, have, size)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            raise _Retry(e.__class__.__name__) from e

    def verify(self, path: Path, size: int, sha256: str, name: str) -> None:
        actual_size = path.stat().st_size
        digest = sha256_file(path)
        if actual_size != size or not digest_matches(digest, sha256):
            path.unlink(missing_ok=True)
            raise self.integrity_error(f"{name} does not match its pin (expected {size} bytes, sha256 {sha256}; got "
                                       f"{actual_size} bytes, {digest}). The file was deleted and will not be used.")

    def get_bytes(self, url: str, max_bytes: int, name: str = "") -> bytes:
        """A small file without a pin, for content that is verified separately (e.g. by a signature)."""
        name = name or url.rsplit("/", 1)[-1]
        if not url.startswith("https://"):
            raise self.error(f"Refusing to download {name} without HTTPS: {url}")
        try:
            with self.session.get(url, headers={"Accept-Encoding": "identity"}, stream=True, timeout=(15, 60),
                                  allow_redirects=True) as r:
                if not str(r.url).startswith("https://"):
                    raise self.error(f"Refusing a download that was redirected away from HTTPS: {r.url}")
                if r.status_code != 200:
                    raise self.error(f"Download of {name} failed: HTTP {r.status_code}")
                data = bytearray()
                for chunk in r.iter_content(chunk_size=1 << 16):
                    data += chunk
                    if len(data) > max_bytes:
                        raise self.integrity_error(f"{name} is larger than {max_bytes} bytes; refusing it.")
                return bytes(data)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            raise self.error(f"Download of {name} failed: {e.__class__.__name__}") from e
