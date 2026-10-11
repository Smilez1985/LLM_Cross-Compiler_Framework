#!/usr/bin/env python3
"""
One handle model for every long operation except builds (design section 5).

A job has a kind, an owner (the caller that started it: actor and session), a state, progress, a result or an error,
and times. Tools that take longer than an inline call may (a crawl, a deploy, a download, a pending approval) return
a job id at once; the client polls with `get_job_status(wait_seconds)` and stops it with `cancel_job`. Waiting holds
no worker thread: an async waiter is woken through its event loop when the job changes. A summary of each job (never
its result) is kept in the per-user folder so the GUI and the CLI can show it.
"""

import asyncio
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

JOB_KINDS = ("crawl", "deploy", "package", "probe", "pack_import", "pack_export", "snapshot_restore", "hf_download",
             "runtime_build", "runtime_start", "benchmark", "approval")
JOB_STATES = ("queued", "running", "waiting_for_approval", "done", "failed", "cancelled", "expired")
TERMINAL_STATES = frozenset({"done", "failed", "cancelled", "expired"})
JOB_ID_RE = re.compile(r"^job_[a-z0-9]{16}$")
MAX_WAIT_SECONDS = 50       # below Codex's 60 s default per tool call
PROGRESS_EVERY = 5.0        # seconds between progress notifications while a client waits
KEEP_SUMMARIES = 200

logger = get_logger("JobService")


class JobError(ValueError):
    """An unknown job, or a change a job does not allow; the message says what to do next."""

    def __init__(self, message: str, code: str = "unknown_id"):
        self.code = code
        super().__init__(message)


class JobCancelled(Exception):
    """Raised inside a job's work when it notices its cancellation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def owner_actor(owner: str) -> str:
    """The caller class of an owner key (`mcp#<session>` -> `mcp`), the only part ever shown."""
    return (owner or "").split("#", 1)[0]


@dataclass
class Job:
    job_id: str
    kind: str
    owner: str
    state: str
    created: str
    updated: str
    done: Optional[float] = None
    total: Optional[float] = None
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, str]] = None   # code, message, next
    note: str = ""
    version: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    on_cancel: Optional[Callable[[], None]] = field(default=None, repr=False)
    _waiters: List[Callable[[], None]] = field(default_factory=list, repr=False)

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES

    def summary(self) -> Dict[str, Any]:
        """What is persisted and shown in lists: no result, no session id."""
        return {"job_id": self.job_id, "kind": self.kind, "owner": owner_actor(self.owner), "state": self.state,
                "created": self.created, "updated": self.updated,
                "error": (self.error or {}).get("code")}


class JobHandle:
    """What a job's work function gets: its cancel state and a way to report progress."""

    def __init__(self, service: "JobService", job: Job):
        self._service, self.job = service, job

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def progress(self, done: Optional[float], total: Optional[float] = None, message: str = "") -> None:
        self._service.update(self.job.job_id, progress=(done, total, message))


class JobService:
    def __init__(self, folder: Optional[Path] = None, keep: int = KEEP_SUMMARIES):
        self.folder = Path(folder) if folder is not None else None
        self.keep = keep
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)

    # --- creating and changing jobs --------------------------------------------------------------------------

    def create(self, kind: str, owner: str, *, state: str = "queued",
               on_cancel: Optional[Callable[[], None]] = None) -> Job:
        if kind not in JOB_KINDS:
            raise ValueError(f"Unknown job kind {kind!r}.")
        if state not in JOB_STATES or state in TERMINAL_STATES:
            raise ValueError(f"A job cannot start in state {state!r}.")
        now = _now()
        job = Job(f"job_{secrets.token_hex(8)}", kind, owner, state, now, now, on_cancel=on_cancel)
        with self._lock:
            self._jobs[job.job_id] = job
        self._persist(job)
        return job

    def start(self, kind: str, owner: str, work: Callable[[JobHandle], Optional[Dict[str, Any]]], *,
              on_cancel: Optional[Callable[[], None]] = None) -> Job:
        """Runs `work(handle)` in a thread; its returned dict becomes the result. An error that is a ValueError (a
        refusal meant for the user) keeps its message; anything else is reported without details."""
        job = self.create(kind, owner, state="running", on_cancel=on_cancel)

        def run():
            handle = JobHandle(self, job)
            try:
                result = work(handle)
            except JobCancelled:
                self._finish(job.job_id, "cancelled")
                return
            except ValueError as e:
                self._finish(job.job_id, "failed", error={"code": getattr(e, "code", "failed"),
                                                          "message": redact_secrets(str(e))})
                return
            except Exception as e:  # never hand an unexpected exception's text to an AI client
                logger.error(f"Job {job.job_id} ({kind}) failed: {e!r}", exc_info=True)
                self._finish(job.job_id, "failed", error={"code": "failed",
                                                          "message": f"The {kind} job failed (details in the log)."})
                return
            if handle.cancelled:
                return  # cancelled while the last step ran: its result is not used
            self._finish(job.job_id, "done", result=result or {})

        threading.Thread(target=run, name=f"job-{job.job_id}", daemon=True).start()
        return job

    def update(self, job_id: str, *, state: Optional[str] = None, progress=None,
               result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, str]] = None,
               note: Optional[str] = None) -> Job:
        with self._lock:
            job = self._get(job_id)
            if job.finished:
                return job  # a finished job never changes again (a late result of a cancelled job is dropped)
            persist = False
            if state is not None:
                if state not in JOB_STATES:
                    raise ValueError(f"Unknown job state {state!r}.")
                persist = state != job.state
                job.state = state
            if progress is not None:
                job.done, job.total, message = progress
                job.message = redact_secrets(str(message or ""))[:500]
            if result is not None:
                job.result = result
            if error is not None:
                job.error = {k: redact_secrets(str(v))[:2000] for k, v in error.items()}
            if note is not None:
                job.note = note
            self._touch(job)
        if persist:
            self._persist(job)
        return job

    def _finish(self, job_id: str, state: str, **changes) -> Job:
        return self.update(job_id, state=state, **changes)

    def _touch(self, job: Job) -> None:
        """Called with the lock held: a new version, and every waiter woken."""
        job.version += 1
        job.updated = _now()
        waiters, job._waiters = job._waiters, []
        self._changed.notify_all()
        for wake in waiters:
            try:
                wake()
            except Exception:  # the waiter's event loop is gone (client disconnected)
                pass

    def cancel(self, job_id: str, reason: str = "") -> Job:
        """Stops a job: its cancel event is set, its own stop hook runs (crawl stop event, download stop, SSH close,
        approval withdrawal), and it is marked cancelled at once; a result that arrives later is dropped."""
        with self._lock:
            job = self._get(job_id)
            if job.finished:
                return job
            job.cancel_event.set()
            hook = job.on_cancel
        if hook is not None:
            try:
                hook()
            except Exception as e:
                logger.warning(f"Stopping job {job_id} failed: {e}")
        return self.update(job_id, state="cancelled", note=reason or None)

    def cancel_owned(self, owner: str, reason: str = "") -> List[Job]:
        """Cancels every unfinished job of `owner` (a disconnected client's)."""
        with self._lock:
            mine = [j.job_id for j in self._jobs.values() if j.owner == owner and not j.finished]
        return [self.cancel(job_id, reason) for job_id in mine]

    # --- reading and waiting ---------------------------------------------------------------------------------

    def _get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobError(f"Unknown job '{job_id}'. Job ids come from the tool that started the job; do not guess "
                           "them.")
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._get(job_id)

    def owner_of(self, job_id: str) -> Optional[str]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.owner if job is not None else None

    def list(self, owner: Optional[str] = None) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if owner is None or j.owner == owner]

    def wait(self, job_id: str, timeout: float) -> Job:
        """Blocks until the job changes or ends, at most `timeout` seconds (capped at MAX_WAIT_SECONDS). For callers
        in their own thread (Ditto); MCP waits use wait_async."""
        deadline = time.monotonic() + max(0.0, min(float(timeout), MAX_WAIT_SECONDS))
        with self._changed:
            job = self._get(job_id)
            version = job.version
            while not job.finished and job.version == version:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
            return job

    async def wait_async(self, job_id: str, timeout: float,
                         report: Optional[Callable[..., Any]] = None) -> Job:
        """Waits on the event loop (no worker thread) until the job changes or ends, at most `timeout` seconds
        (capped at MAX_WAIT_SECONDS), and reports progress every PROGRESS_EVERY seconds through `report` (the MCP
        context's report_progress, which sends nothing without a progress token)."""
        import anyio
        deadline = time.monotonic() + max(0.0, min(float(timeout), MAX_WAIT_SECONDS))
        event = anyio.Event()
        loop = asyncio.get_running_loop()

        def wake():
            loop.call_soon_threadsafe(event.set)

        with self._lock:
            job = self._get(job_id)
            if job.finished:
                return job
            job._waiters.append(wake)
        try:
            while not event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                with anyio.move_on_after(min(PROGRESS_EVERY, remaining)):
                    await event.wait()
                if not event.is_set() and report is not None and deadline - time.monotonic() > 0:
                    try:
                        await report(job.done or 0, job.total, f"{job.kind}: {job.state}")
                    except Exception:  # the client may be gone; the wait itself goes on
                        pass
        finally:
            with self._lock:
                if wake in job._waiters:
                    job._waiters.remove(wake)
        return job

    # --- summaries in the per-user folder ----------------------------------------------------------------------

    def _persist(self, job: Job) -> None:
        if self.folder is None:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=f".{job.job_id}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(job.summary(), f)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.folder / f"{job.job_id}.json")
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._prune()
        except OSError as e:
            logger.warning(f"Could not keep the summary of job {job.job_id}: {e}")

    def _prune(self) -> None:
        files = sorted(self.folder.glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[self.keep:]:
            try:
                old.unlink()
            except OSError:
                pass

    def summaries(self) -> List[Dict[str, Any]]:
        """Kept summaries of every process's jobs, newest first (for the GUI and the CLI)."""
        if self.folder is None or not self.folder.is_dir():
            return []
        found = []
        for path in sorted(self.folder.glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and JOB_ID_RE.fullmatch(str(data.get("job_id", ""))):
                found.append(data)
        return found
