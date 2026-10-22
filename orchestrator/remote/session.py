#!/usr/bin/env python3
"""
RemoteSession: one working session with a board over a Transport (consolidation chain c2-remote-session, finding 2 of
the architecture audit). It is what DeploymentManager's board session could do alone, taken out so the runtime deploy,
the probe and the on-board benchmark can use the same steps (chain c2-remote-callers):

- open(): reads the host key the board presents, checks it against the framework's known_hosts (and against the
  fingerprint the person was shown, `expect_fingerprint`), then logs in. Every login after a network drop checks the
  key again the same way. The credentials are typed: KeyOnly() logs in with the framework's key only (every AI path),
  OneTimePassword(password) also sends a password the person typed for these logins, and only with it can pair()
  install the framework's key on the board. An AI path that holds no OneTimePassword cannot pair, not even
  indirectly.
- run(script): one `sh -c` on the board with a time limit and an output cap (transport.ParamikoTransport.run).
- private_dir(parent, prefix): a private, unpredictable folder made by mktemp, its answer checked strictly.
- upload(fh, remote_path, size, sha256): sends an open file as <remote_path>.part in pipelined chunks of
  UPLOAD_CHUNK, checks its sha256 on the board and only then renames it. A damaged arrival is sent once more. After
  a network drop it waits for the board and continues at what arrived, once the board's hash of those bytes matches
  the same bytes here (utils/helpers.sha256_fileobj on the same open file); else it starts again.
- wait_for_host(step): after a drop, waits up to `wait_seconds` until the board's SSH port answers and the login
  works again (N5), with a "waiting" event about every WAIT_INTERVAL seconds; a connection that opens and drops
  again while the SSH server starts counts as not back yet (reasons.reach_code(..., while_waiting=True)).
- run_detached(folder, job) and its follow: starts `job` once on the board on its own (nohup, behind a marker, so the
  start is never repeated even when its command arrives twice) and follows it to its exit status, again after a drop.
- CancelToken: one cancel for the whole session; every wait ends at once on it.
- RemoteEvent: what a running session reports (uploading, waiting, back); DeploymentManager.DeployEvent is this
  class. remote/text.py event_text renders it for the person or an AI.

A step that cannot be done raises SessionFailure (a RemoteFailure) with its `kind`, the SSH reason code when the
board could not be reached or logged in to, the board's own words apart in `output` (never in the message), and
`state`, what is known about the board afterwards ("ok": nothing was started there; "unknown": a job may run). A
cancel raises Stopped. The messages are the framework's sentences; DeploymentManager turns them into its results.

The session imports nothing above the remote layer: the trust store and the key are read through
orchestrator/utils/ssh_trust (looked up at call time, so tests that patch ssh_trust.fetch_host_key, check_host_key
and connect reach every login), the reason codes through orchestrator/remote/reasons.
"""

import logging
import posixpath
import re
import shlex
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

from orchestrator.remote.address import Endpoint
from orchestrator.remote.errors import RemoteFailure
from orchestrator.remote.text import OUTPUT_LINES
from orchestrator.remote.transport import (COMMAND_TIMEOUT, HASH_TIMEOUT, KEEPALIVE_SECONDS, MAX_OUTPUT,
                                           ParamikoTransport, Result, Transport)
from orchestrator.utils.helpers import sha256_fileobj

# A session whose network drops (a Wi-Fi board) waits this long for the board to answer again (N5), trying every
# WAIT_INTERVAL seconds, and continues at most MAX_RESUMES times.
WAIT_SECONDS = 300
WAIT_INTERVAL = 5.0
MAX_RESUMES = 20
UPLOAD_CHUNK = 1 << 20
HASH_RATE = 2_000_000          # bytes per second a slow board's sha256sum reads at least (its time limit grows with it)
JOB_TIMEOUT = 1800             # seconds a detached job may run on the board
JOB_POLL = 2.0
# Files of a job that runs on the board on its own, in the session's private folder there (the names the deploy has
# used since N5: a folder on a board may hold them from a run before).
JOB_STARTED = ".lcf-install.started"
JOB_STATUS = ".lcf-install.status"
JOB_LOG = ".lcf-install.log"
PRIVATE_PARENT = "/tmp"        # nosec B108 - a folder on the board, made by mktemp with a random name
PRIVATE_PREFIX = "llm_deploy"


@dataclass(frozen=True)
class RemoteEvent:
    """What a running session reports: "uploading" (bytes on the board of the total), "waiting" (the network dropped:
    seconds waited of the limit, and whether the upload or the installation waits), "back" (the board answers again:
    the upload continues at `done`, or with step "install" the job is followed again), "installing"."""
    kind: str
    done: int = 0
    total: int = 0
    elapsed: float = 0.0
    limit: float = 0.0
    step: str = ""


# --- credentials ------------------------------------------------------------------------------------------------------

class KeyOnly:
    """Log in with the framework's key only. Every AI path uses this: it holds no password, so it cannot pair."""

    password = None

    def __repr__(self) -> str:
        return "KeyOnly()"


class OneTimePassword:
    """A password the person typed, used for this session's logins only and never stored; with `install_key` the
    session's pair() installs the framework's key on the board with it (later logins need no password)."""

    def __init__(self, password: str, install_key: bool = True):
        if not isinstance(password, str) or not password:
            raise ValueError("a one-time password is a non-empty text the person typed")
        self.password, self.install_key = password, bool(install_key)

    def __repr__(self) -> str:   # never the password itself
        return f"OneTimePassword(install_key={self.install_key})"


# --- cancel -----------------------------------------------------------------------------------------------------------

class CancelToken:
    """One cancel for a session: cancel() (a Cancel button, Ctrl+C, an AI's cancel_job), or the caller's own check
    (`check`, read each time). What the caller holds with hold() (an SSH client) is closed at once on cancel(), so a
    command that waits for the board ends too."""

    def __init__(self, check: Optional[Callable[[], Any]] = None):
        self._event = threading.Event()
        self._check = check
        self._lock = threading.Lock()
        self._held: List[Any] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or bool(self._check is not None and self._check())

    def __call__(self) -> bool:
        return self.cancelled

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            held, self._held = self._held, []
        for item in held:
            _close_quietly(item)

    def hold(self, closeable: Any) -> None:
        """Closes `closeable` when the session is cancelled (at once when it is already)."""
        with self._lock:
            if not self._event.is_set():
                self._held.append(closeable)
                return
        _close_quietly(closeable)

    def release(self, closeable: Any) -> None:
        with self._lock:
            self._held = [item for item in self._held if item is not closeable]


def _close_quietly(item: Any) -> None:
    try:
        item.close()
    except Exception:  # noqa: BLE001 - closing on a cancel never raises
        pass


# --- failures ---------------------------------------------------------------------------------------------------------

class Stopped(RemoteFailure):
    """The session was cancelled."""

    def __init__(self, message: str = "The session was cancelled."):
        super().__init__(message, "stopped")


class SessionFailure(RemoteFailure):
    """A step on the board that cannot be done. `kind`: key_read, not_trusted, key_changed, other_key, login (the
    login), wait (the board did not come back), private_dir, upload_failed, upload_damaged, rename, folder_gone (the
    upload), not_started, cannot_follow, job_gone, job_timeout, job_lost (a detached job). `reason`: the SSH reason
    code; `output`: the board's words, kept apart; `state`: "ok" or "unknown" (what may run on the board); `step`: what
    a wait waited for ("upload" or "install")."""

    def __init__(self, message: str, kind: str, reason: str = "", error: Optional[BaseException] = None, *,
                 output: Optional[List[str]] = None, state: str = "ok", step: str = ""):
        super().__init__(message, kind, reason, error)
        self.output, self.state, self.step = list(output or []), state, step


def failed_because(code: str, error: BaseException) -> str:
    """The end of a failed contact's sentence: its SSH reason code, a problem on this computer in plain words (the
    file's name, never its folder), or the error itself when it is none of them."""
    from orchestrator.remote import reasons
    if code == reasons.LOCAL_PROBLEM:
        return f": a problem on this computer, not on the board ({reasons.local_detail(error)}; {code})."
    return f" ({code})." if code else f": {error}"


def hash_timeout(size: int) -> float:
    """How long a board may hash `size` bytes without a word: HASH_TIMEOUT, more for a large file on a slow board."""
    return max(float(HASH_TIMEOUT), float(size) / HASH_RATE)


# --- the session ------------------------------------------------------------------------------------------------

class RemoteSession:
    """One session with the board at `endpoint` as `user` (see the module docstring). `clock` and `sleep` are the
    session's time (tests pass a fake one), `reachable(host, port)` the quick look whether the SSH port answers again
    (reasons.check_port by default), `on_client` gets each authenticated client (so a Cancel elsewhere can close it),
    `on_event` each RemoteEvent, `transport_factory(client)` makes the Transport of a client."""

    def __init__(self, endpoint: Endpoint, user: str, state_dir: Path, *, credentials: Any = None,
                 expect_fingerprint: Optional[str] = None, cancel: Optional[CancelToken] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep,
                 reachable: Optional[Callable[[str, int], bool]] = None,
                 on_event: Optional[Callable[[RemoteEvent], Any]] = None,
                 on_client: Optional[Callable[[Any], Any]] = None, wait_seconds: float = WAIT_SECONDS,
                 transport_factory: Optional[Callable[[Any], Transport]] = None,
                 logger: Optional[logging.Logger] = None):
        self.endpoint, self.user, self.state_dir = endpoint, user, Path(state_dir)
        self.credentials = credentials if credentials is not None else KeyOnly()
        if not isinstance(self.credentials, (KeyOnly, OneTimePassword)):
            raise TypeError("credentials are KeyOnly() or OneTimePassword(password)")
        self.expect_fingerprint = expect_fingerprint
        self.cancel = cancel if cancel is not None else CancelToken()
        self._clock, self._sleep = clock, sleep
        self._reachable = reachable
        self.on_event = on_event or (lambda _e: None)
        self.on_client = on_client
        self.wait_seconds = max(0.0, float(wait_seconds))
        self._factory = transport_factory or (lambda client: ParamikoTransport(client, keepalive=KEEPALIVE_SECONDS))
        self.logger = logger or logging.getLogger(__name__)
        self.client: Any = None
        self.transport: Optional[Transport] = None
        self.resumed = 0           # how often the session continued after the network dropped
        self.waited = 0.0          # seconds it waited for the board
        self.fingerprint = ""      # the host key the board presented at the last login
        self.detached_sent = False  # a detached job's start command is on its way: it may run on the board

    @property
    def host(self) -> str:
        return self.endpoint.host

    @property
    def port(self) -> int:
        return self.endpoint.port

    # --- logging in ---

    def open(self, *, waiting: bool = False) -> None:
        """Reads and checks the board's host key, then logs in (SessionFailure says why not). `waiting`: the board
        dropped off the network before (wait_for_host), so a connection that drops again means it is not back yet."""
        from orchestrator.remote import reasons
        from orchestrator.utils import ssh_trust
        try:
            key = ssh_trust.fetch_host_key(self.host, self.port)
            status = ssh_trust.check_host_key(self.state_dir, self.host, self.port, key)
        except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, no paramiko, a damaged known_hosts
            code = reasons.reach_code(e, while_waiting=waiting)
            raise SessionFailure(f"Could not read the host key of {self.host}" + failed_because(code, e), "key_read",
                                 code, e) from None
        if status == "unknown":
            raise SessionFailure(f"The device {self.host} is not trusted yet: confirm its host key fingerprint first.",
                                 "not_trusted")
        if status == "changed":
            raise SessionFailure(str(ssh_trust.HostKeyChanged(self.host, self.port)), "key_changed",
                                 reasons.HOST_KEY_CHANGED)
        fp = ssh_trust.fingerprint(key)
        if self.expect_fingerprint and fp != self.expect_fingerprint:
            raise SessionFailure(f"The device {self.host} now presents another host key than the one shown ({fp}, "
                                 f"not {self.expect_fingerprint})", "other_key", reasons.HOST_KEY_CHANGED)
        self.fingerprint = fp
        if self.cancel.cancelled:
            raise Stopped()
        try:
            self.client = ssh_trust.connect(self.host, self.user, self.state_dir, port=self.port,
                                            password=self.credentials.password)
        except Exception as e:  # noqa: BLE001 - authentication, network, a host key that changed meanwhile
            code = reasons.reach_code(e, while_waiting=waiting)
            raise SessionFailure(f"SSH login to {self.user}@{self.host} failed" + failed_because(code, e), "login",
                                 code, e) from None
        self.transport = self._factory(self.client)
        if self.on_client is not None:
            self.on_client(self.client)

    def pair(self) -> bool:
        """Installs the framework's key on the board (its authorized_keys), only with OneTimePassword credentials
        that ask for it; True when it was installed. KeyOnly credentials never reach the key installation."""
        if not isinstance(self.credentials, OneTimePassword) or not self.credentials.install_key:
            return False
        from orchestrator.utils import ssh_trust
        ssh_trust.install_public_key(self.client, ssh_trust.client_key(self.state_dir)[1])
        return True

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
        elif self.client is not None:
            _close_quietly(self.client)
        self.client, self.transport = None, None

    def alive(self) -> bool:
        return self.transport is not None and self.transport.alive()

    # --- commands ---

    def run(self, script: str, *, timeout: float = COMMAND_TIMEOUT, limit: int = MAX_OUTPUT,
            stdin: Any = None) -> Result:
        """One `sh -c` on the board (the connection's own errors are raised as they are)."""
        return self.transport.run(script, stdin=stdin, timeout=timeout, limit=limit)

    def private_dir(self, parent: str = PRIVATE_PARENT, prefix: str = PRIVATE_PREFIX) -> str:
        """A private folder on the board, made by `mktemp -d <parent>/<prefix>.XXXXXXXX` (a fixed path could be made by
        another user first); its answer must be exactly such a folder, else SessionFailure with the board's words in
        `output`."""
        pattern = shlex.quote(f"{parent}/{prefix}.XXXXXXXX")
        result = self.run(f"mktemp -d {pattern}")
        folder = result.out.strip()
        exact = re.compile(re.escape(f"{parent}/{prefix}.") + r"[A-Za-z0-9]{8}", re.ASCII)
        if result.status != 0 or not exact.fullmatch(folder):
            raise SessionFailure("Could not create a folder on the device (its answer is in the device output).",
                                 "private_dir", output=result.err.splitlines()[-OUTPUT_LINES:])
        return folder

    # --- waiting ---

    def pause(self, seconds: float) -> None:
        """Sleeps, but ends at once on a cancel."""
        end = self._clock() + seconds
        while not self.cancel.cancelled:
            left = end - self._clock()
            if left <= 0:
                return
            self._sleep(min(0.25, left))

    def _port_open(self) -> bool:
        if self._reachable is not None:
            return bool(self._reachable(self.host, self.port))
        from orchestrator.remote import reasons
        return reasons.check_port(self.host, self.port, timeout=2) is None

    def wait_for_host(self, step: str) -> None:
        """After a network drop: waits until the board answers on its SSH port again and logs in again, or raises
        SessionFailure "wait" (with the reason code) after wait_seconds. A login refused for another reason (a changed
        key, a refused password) ends the wait with its own failure; a cancel ends it at once (Stopped)."""
        from orchestrator.remote import reasons
        self.close()
        started = self._clock()
        code: Optional[str] = None
        while True:
            if self.cancel.cancelled:
                raise Stopped()
            elapsed = self._clock() - started
            self.on_event(RemoteEvent("waiting", elapsed=elapsed, limit=self.wait_seconds, step=step))
            if self._port_open():
                try:
                    self.open(waiting=True)
                except SessionFailure as failed:
                    if failed.reason not in (reasons.HOST_DOWN, reasons.PORT_CLOSED):
                        raise   # a changed key or a refused login: waiting does not help
                    code = failed.reason  # the SSH server is not up yet: keep waiting
                else:
                    self.waited += self._clock() - started
                    return
            elapsed = self._clock() - started
            if elapsed >= self.wait_seconds:
                self.waited += elapsed
                code = code or reasons.check_port(self.host, self.port) or reasons.HOST_DOWN
                raise SessionFailure(f"The board did not answer again within {int(self.wait_seconds)} s ({code})",
                                     "wait", code, state="unknown" if step == "install" else "ok", step=step)
            self.pause(min(WAIT_INTERVAL, max(0.1, self.wait_seconds - elapsed)))

    def _dropped(self, error: BaseException) -> bool:
        """Whether `error` is a network drop to wait through (the session itself broke), within MAX_RESUMES."""
        return self.transport is not None and not self.transport.alive() and self.resumed < MAX_RESUMES

    # --- the upload ---

    def upload(self, fh: Any, remote_path: str, size: int, sha256: str) -> None:
        """Uploads the open file `fh` (`size` bytes, sha256 `sha256`) as <remote_path>.part and renames it when its
        sha256 on the board is the same; resumes after a network drop (see the module docstring)."""
        part = f"{remote_path}.part"
        folder = posixpath.dirname(remote_path)
        restarted = False
        while True:
            try:
                offset = self._resume_offset(fh, part, size)
                self.logger.info(f"Uploading {posixpath.basename(remote_path)}..." if not offset else
                                 f"Resuming the upload of {posixpath.basename(remote_path)} at {offset} bytes...")
                self._send(fh, part, offset, size)
                result = self.run(f"sha256sum {shlex.quote(part)}", timeout=hash_timeout(size))
                if result.status == 0 and result.out.split()[:1] == [sha256]:
                    result = self.run(f"mv -f {shlex.quote(part)} {shlex.quote(remote_path)}")
                    if result.status == 0:
                        return
                    raise SessionFailure("The file arrived, but could not be renamed on the device.", "rename")
                if restarted:
                    raise SessionFailure("The file arrived damaged twice (its sha256 on the device differs).",
                                         "upload_damaged")
                restarted = True
                self.run(f"rm -f {shlex.quote(part)}")
            except (Stopped, SessionFailure):
                raise
            except Exception as e:  # noqa: BLE001 - a network drop, or a failure on the board (disk full, ...)
                if self.cancel.cancelled:
                    raise Stopped() from None
                if not self._dropped(e):
                    raise SessionFailure(f"The upload to {self.host} failed: {e}", "upload_failed", error=e) from None
                self.logger.warning(f"The connection to {self.host} dropped during the upload; waiting for the board.")
                self.wait_for_host("upload")
                self.resumed += 1
                if self.run(f"[ -d {shlex.quote(folder)} ]").status != 0:
                    # the board restarted and its /tmp is empty: nothing of the file is left there
                    raise SessionFailure("The board answers again, but the upload folder is gone (the board "
                                         "restarted).", "folder_gone")

    def _resume_offset(self, fh: Any, part: str, size: int) -> int:
        """How many bytes of the file are on the board and match it (0: start from the beginning)."""
        offset = self.transport.stat_size(part) or 0
        if offset <= 0 or offset > size:
            return 0
        result = self.run(f"head -c {offset} {shlex.quote(part)} | sha256sum", timeout=hash_timeout(offset))
        fh.seek(0)
        prefix = sha256_fileobj(fh, limit=offset, chunk=UPLOAD_CHUNK)  # the first `offset` bytes of the file
        if result.status != 0 or result.out.split()[:1] != [prefix]:
            self.logger.warning("What arrived on the board does not match the file here; the upload starts again.")
            return 0
        self.on_event(RemoteEvent("back", done=offset, total=size))
        return offset

    def _send(self, fh: Any, part: str, offset: int, size: int) -> None:
        fh.seek(offset)
        with self.transport.open_write(part, offset) as remote:
            done = offset
            self.on_event(RemoteEvent("uploading", done=done, total=size))
            for chunk in iter(lambda: fh.read(UPLOAD_CHUNK), b""):
                if self.cancel.cancelled:
                    raise Stopped()
                remote.write(chunk)
                done += len(chunk)
                self.on_event(RemoteEvent("uploading", done=done, total=size))

    # --- a job that runs on the board on its own ---

    @staticmethod
    def detached_command(folder: str, job: str) -> str:
        """The command that starts `job` in `folder` on the board on its own (nohup, nothing attached to the SSH
        session): its exit status goes into JOB_STATUS. The marker JOB_STARTED is made first, and a folder that has it
        is never started again, also when this command reaches the board twice."""
        d = shlex.quote(folder)
        job = f"{job}; echo $? > {JOB_STATUS}"
        return (f"cd {d} && if [ -e {JOB_STARTED} ]; then echo started=already; else : > {JOB_STARTED} && "
                f"(nohup sh -c {shlex.quote(job)} </dev/null >/dev/null 2>&1 &) && echo started=now; fi")

    @staticmethod
    def poll_command(folder: str) -> str:
        d = shlex.quote(folder)
        return f"cat {d}/{JOB_STATUS} 2>/dev/null || {{ [ -d {d} ] && echo running || echo gone; }}"

    def run_detached(self, folder: str, job: str, *, timeout: float = JOB_TIMEOUT, poll: float = JOB_POLL) -> int:
        """Starts `job` once (detached_command) and waits for its exit status; after a network drop it waits for the
        board, logs in again and keeps following the same job. Once the start command is on its way, the job may run
        on the board even when its answer is lost (detached_sent): from then on a failure says the state is unknown."""
        start = self.detached_command(folder, job)
        started_at = self._clock()
        sent = False
        first = True
        while True:
            try:
                if not sent:
                    self.detached_sent = True
                    result = self.run(start)
                    if result.status != 0 or "started=" not in result.out:
                        output = result.err.splitlines()[-OUTPUT_LINES:]
                        if first:
                            raise SessionFailure("The job could not be started on the device (its answer is in the "
                                                 "device output).", "not_started", output=output)
                        raise SessionFailure("The board answers again, but the job cannot be followed (its answer is "
                                             "in the device output).", "cannot_follow", output=output, state="unknown")
                    sent = True
                result = self.run(self.poll_command(folder))
                answer = result.out.strip()
                if result.status == 0 and answer.isdigit():
                    return int(answer)
                if answer == "gone":
                    raise SessionFailure("The board restarted during the job (its folder is gone).", "job_gone",
                                         state="unknown")
                if self._clock() - started_at > timeout:
                    raise SessionFailure(f"The job did not finish on the device within {int(timeout)} s.",
                                         "job_timeout", state="unknown")
                if self.cancel.cancelled:
                    raise Stopped()
                self.pause(poll)
            except (Stopped, SessionFailure):
                raise
            except Exception as e:  # noqa: BLE001 - a network drop while the board works on its own
                if self.cancel.cancelled:
                    raise Stopped() from None
                if not self._dropped(e):
                    raise SessionFailure(f"The job on {self.host} could not be followed: {e}", "job_lost", error=e,
                                         state="unknown" if self.detached_sent else "ok") from None
                self.logger.warning(f"The connection to {self.host} dropped during the installation; waiting for "
                                    "the board.")
                self.wait_for_host("install")
                self.resumed += 1
                first = False
                # the start command is sent again only when its answer never came: the marker on the board makes it
                # answer started=already instead of starting the job a second time
                self.on_event(RemoteEvent("back", step="install"))


__all__ = ["CancelToken", "HASH_RATE", "JOB_LOG", "JOB_POLL", "JOB_STARTED", "JOB_STATUS", "JOB_TIMEOUT", "KeyOnly",
           "MAX_RESUMES", "OneTimePassword", "PRIVATE_PARENT", "PRIVATE_PREFIX", "RemoteEvent", "RemoteSession",
           "SessionFailure", "Stopped", "UPLOAD_CHUNK", "WAIT_INTERVAL", "WAIT_SECONDS", "failed_because",
           "hash_timeout"]
