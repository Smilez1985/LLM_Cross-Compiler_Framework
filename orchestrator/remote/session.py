#!/usr/bin/env python3
"""
RemoteSession: one working session with a board over a Transport (consolidation chain c2-remote-session, finding 2 of
the architecture audit). It is what DeploymentManager's board session could do alone, taken out so the runtime deploy,
the probe and the on-board benchmark can use the same steps (chain c2-remote-callers):

- open(): reads the host key the board presents, checks it against the framework's known_hosts (and against the
  fingerprint the person was shown, `expect_fingerprint`, and the board the caller means, `expect_device_id`), then
  logs in; `last_check` keeps that KeyCheck (also of a refused login) and `key` the key that passed. Every login
  after a network drop checks the key again the same way. adopt(client, key) takes over a login the caller made
  itself (the person's scan), so later logins are the session's own. The credentials are typed: KeyOnly() logs
  in with the framework's key only (every AI path), OneTimePassword(password) also sends a password the person
  typed for these logins, and only with it can pair() install the framework's key on the board. An AI path that
  holds no OneTimePassword cannot pair, not even indirectly.
- run(script): one `sh -c` on the board with a time limit and an output cap (transport.ParamikoTransport.run).
- private_dir(parent, prefix): a private, unpredictable folder made by mktemp, its answer checked strictly.
- upload(fh, remote_path, size, sha256): sends an open file as <remote_path>.part in pipelined chunks of
  UPLOAD_CHUNK, checks its sha256 on the board and only then renames it. A damaged arrival is sent once more. After
  a network drop it waits for the board and continues at what arrived, once the board's hash of those bytes matches
  the same bytes here (utils/helpers.sha256_fileobj on the same open file); else it starts again. With `private`
  every copy it starts on the board is made empty under umask 077 first and then written into (an SFTP server makes a
  new file with its own umask), so it is the user's only from the start (the runtime deploy's tarball).
- wait_for_host(step): after a drop, waits up to `wait_seconds` until the board's SSH port answers and the login
  works again (N5), with a "waiting" event about every WAIT_INTERVAL seconds; a connection that opens and drops
  again while the SSH server starts counts as not back yet (reasons.reach_code(..., while_waiting=True)).
- run_detached(folder, job) and follow(folder): starts `job` once on the board on its own (nohup, behind a marker, so
  the start is never repeated even when its command arrives twice) and follows it to its exit status, again after a
  drop; follow() alone follows a job started before (the work keeps running on the board while nobody watches, round
  16 G4). The deploy's deploy.sh and the on-board benchmark (runtime/device/remote.py verify_on_device) run this way.
- run_resumable(script, again=...) and resumable(action): one command (or any action on the board) through a network
  drop: the session waits for the board, logs in again and, for a command that may run twice (it only reads, or does
  the same again), runs it once more; for one that may not, it raises SessionFailure "dropped" once the board answers
  again (so the caller can still clean up). A command that stays silent while its connection still answers (an
  `exit 0` on it, answers()) is busy on the board, no drop: its timeout is the end at once, as before; one whose
  connection is silent too waits for the board and is tried once more, then its timeout is the end. The runtime
  deploy, the probe and the checks on the board run their commands this way (runtime/device/remote.py RemoteShell is
  their facade).
- attached(client): a session over a client someone else logged in (a stand-in in tests): it runs, uploads and follows
  jobs like any session, but cannot log in again, so a network drop ends it as it always did.
- CancelToken: one cancel for the whole session; every wait ends at once on it. close() ends the session the same
  way for whoever holds it (the Cancel of a window or a tool job that holds the session with CancelToken.hold).
- RemoteEvent: what a running session reports (uploading, waiting, back); DeploymentManager.DeployEvent is this
  class. remote/text.py event_text renders it for the person or an AI.
- openssh: the same session over this computer's OpenSSH client instead of paramiko (transport.SystemSshTransport,
  chain c2-remote-system, decision A5): every step above works the same way. The login is OpenSSH's own: it checks
  the host key against the framework's known_hosts on every call (StrictHostKeyChecking=yes), so the key is not read
  here, neither a fingerprint the person was shown nor the board the caller means (expect_device_id) can be compared
  (open() refuses with "needs_paramiko"), and the credentials are KeyOnly (BatchMode never asks for a password, so a
  OneTimePassword is refused when the session is made). Every step after the login (run_resumable, upload with
  `private`, run_detached and follow, a wait after a drop, close() as the end) is the same over both transports.

A step that cannot be done raises SessionFailure (a RemoteFailure) with its `kind`, the SSH reason code when the
board could not be reached or logged in to, the board's own words apart in `output` (never in the message), and
`state`, what is known about the board afterwards ("ok": nothing was started there; "unknown": a job may run). A
cancel raises Stopped. The messages are the framework's sentences; DeploymentManager turns them into its results.

The session imports nothing above the remote layer: the board's host key is read and checked by the one read-only
trust store (orchestrator/remote/trust.py TrustStore.check, which looks up ssh_trust.fetch_host_key, check_host_key and
fingerprint at call time), the login and the framework's key go through orchestrator/utils/ssh_trust (looked up at
call time, so tests that patch ssh_trust.fetch_host_key, check_host_key and connect reach every login), the reason
codes through orchestrator/remote/reasons. pair() calls the same primitive as remote/pairing.py pair
(ssh_trust.install_public_key) without importing that module: the session is reached from AI paths, which may never
import the person's writers (tests/test_remote_trust.py), and only OneTimePassword credentials reach it.
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

from orchestrator.remote import gate
from orchestrator.remote.address import Endpoint
from orchestrator.remote.errors import RemoteFailure
from orchestrator.remote.text import OUTPUT_LINES
from orchestrator.remote.transport import (COMMAND_TIMEOUT, HASH_TIMEOUT, KEEPALIVE_SECONDS, MAX_OUTPUT,
                                           ParamikoTransport, Result, Transport, timed_out)
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
# used since N5; defined with the job's operations in orchestrator/remote/gate.py).
JOB_STARTED = gate.JOB_STARTED
JOB_STATUS = gate.JOB_STATUS
JOB_LOG = gate.JOB_LOG
PRIVATE_PARENT = "/tmp"        # nosec B108 - a folder on the board, made by mktemp with a random name
PRIVATE_PREFIX = "llm_deploy"
# How often a command that stays silent while its connection stands is tried again (resumable): once, then the board
# is taken to be busy, not gone, and the time limit is the end.
SILENT_TRIES = 1
# How long a standing connection may take to answer ANSWER_COMMAND when a command on it stayed silent (resumable): an
# answer means the board is busy with that command and the network did not drop (N5 waits for a network loss only).
ANSWER_TIMEOUT = 10.0
ANSWER_COMMAND = "exit 0"
# The steps whose work runs on the board on its own (a job): a wait that fails leaves the board's state unknown.
JOB_STEPS = ("install", "bench")
# What a step is called in the log line of a drop.
_DOING = {"install": "the installation", "bench": "the benchmark", "upload": "the upload"}


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

    def is_set(self) -> bool:
        """The same as `cancelled`, so the token stands where a threading.Event was the cancel flag (the Boards
        window, the Deployment tab's worker, the board tools' jobs)."""
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
    """A step on the board that cannot be done. `kind`: key_read, not_trusted, key_changed, other_key, needs_paramiko, needs_openssh,
    login (the login), wait (the board did not come back), private_dir, upload_failed, upload_damaged, rename,
    folder_gone (the upload), not_started, cannot_follow, job_gone, job_timeout, job_lost (a detached job).
    `reason`: the SSH reason code; `output`: the board's words (over OpenSSH also what OpenSSH printed), kept apart;
    `state`: "ok" or "unknown" (what may run on the board); `step`: what a wait waited for ("upload" or "install")."""

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
    `on_event` each RemoteEvent, `transport_factory(client)` makes the Transport of a client. With `openssh`, a
    factory `openssh(endpoint, user, state_dir)` of a not yet logged-in transport.SystemSshTransport (or a stand-in
    with login()), the session works over this computer's OpenSSH client instead (see the module docstring)."""

    def __init__(self, endpoint: Endpoint, user: str, state_dir: Path, *, credentials: Any = None,
                 expect_fingerprint: Optional[str] = None, expect_device_id: Optional[str] = None,
                 cancel: Optional[CancelToken] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep,
                 reachable: Optional[Callable[[str, int], bool]] = None,
                 on_event: Optional[Callable[[RemoteEvent], Any]] = None,
                 on_client: Optional[Callable[[Any], Any]] = None, wait_seconds: float = WAIT_SECONDS,
                 transport_factory: Optional[Callable[[Any], Transport]] = None,
                 logger: Optional[logging.Logger] = None,
                 openssh: Optional[Callable[[Endpoint, str, Path], Any]] = None):
        self.endpoint, self.user, self.state_dir = endpoint, user, Path(state_dir)
        self.credentials = credentials if credentials is not None else KeyOnly()
        if not isinstance(self.credentials, (KeyOnly, OneTimePassword)):
            raise TypeError("credentials are KeyOnly() or OneTimePassword(password)")
        if openssh is not None and isinstance(self.credentials, OneTimePassword):
            raise ValueError("OpenSSH runs in BatchMode and never asks for a password: a one-time password (and "
                             "pairing with it) needs the 'paramiko' package")
        self.openssh = openssh
        self.expect_fingerprint = expect_fingerprint
        self.expect_device_id = expect_device_id   # the board the caller means (its device ID); None: any board
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
        self.last_check: Any = None  # the KeyCheck of the last login (also of one that was refused)
        self.key: Any = None         # the host key that passed the check at the last login
        self.can_relogin = True      # False for attached(): a drop is the end
        self.ended = False           # close() ended it: a step that runs or waits never logs in again
        # the framework's key on this board runs only llmcf's gate (orchestrator/remote/gate.py): every command goes as
        # `llmcf-gate VERB ARG...`, uploads through the gate's file operations. Learnt from the gate's refusal of the
        # first command in script form (run()), kept across the logins of this session.
        self.gated = False

    @classmethod
    def attached(cls, client: Any, *, host: str = "the device", cancel: Optional[CancelToken] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep,
                 on_event: Optional[Callable[[RemoteEvent], Any]] = None,
                 transport_factory: Optional[Callable[[Any], Transport]] = None,
                 logger: Optional[logging.Logger] = None) -> "RemoteSession":
        """A session over `client`, which someone else logged in (an authenticated paramiko client or a stand-in with
        exec_command): every step works as in any session, but it cannot log in again, so a network drop ends a step
        with the connection's own error, as before. Its transport has no keepalive of its own (the client's owner
        decides)."""
        session = cls(Endpoint(host), "", Path("."), cancel=cancel, clock=clock, sleep=sleep, on_event=on_event,
                      transport_factory=transport_factory or ParamikoTransport, logger=logger)
        session.client, session.transport = client, session._factory(client)
        session.can_relogin = False
        return session

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
        if not waiting:
            self.ended = False   # the owner opens it (again)
        if self.openssh is None:
            self._proxy_through_openssh()
        if self.openssh is not None:
            self._open_openssh(waiting)
            return
        from orchestrator.remote import reasons
        from orchestrator.remote.trust import HostKeyChanged, TrustStore
        from orchestrator.utils import ssh_trust
        try:
            # The one host-key read and check (remote/trust.py): the key the board presents against the framework's
            # known_hosts and against the fingerprint the person was shown.
            expect = {"expect_fingerprint": self.expect_fingerprint}
            if self.expect_device_id is not None:   # the board the caller means; never "" by mistake for none
                expect["expect_device_id"] = self.expect_device_id
            check = TrustStore(self.state_dir).check(self.endpoint, **expect)
        except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, no paramiko, a damaged known_hosts
            code = reasons.reach_code(e, while_waiting=waiting)
            raise SessionFailure(f"Could not read the host key of {self.host}" + failed_because(code, e), "key_read",
                                 code, e) from None
        self.last_check = check
        if check.status == "unknown":
            raise SessionFailure(f"The device {self.host} is not trusted yet: confirm its host key fingerprint first.",
                                 "not_trusted")
        if check.status == "changed":
            raise SessionFailure(str(HostKeyChanged(self.host, self.port)), "key_changed",
                                 reasons.HOST_KEY_CHANGED)
        if check.other_device:
            raise SessionFailure(f"{self.endpoint.display} answers with the host key of another device than "
                                 f"{self.expect_device_id}.", "other_device")
        fp = check.fingerprint
        if check.fingerprint_differs:
            raise SessionFailure(f"The device {self.host} now presents another host key than the one shown ({fp}, "
                                 f"not {self.expect_fingerprint})", "other_key", reasons.HOST_KEY_CHANGED)
        self.fingerprint, self.key = fp, check.key
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

    def _proxy_through_openssh(self) -> None:
        """paramiko reads no configuration file: a board the framework's own SSH settings reach through a ProxyJump or
        ProxyCommand (ssh_config.needs_proxy) goes through this computer's OpenSSH client, which reads that file. A
        plain refusal where that cannot be: no OpenSSH client here, a one-time password, or a fingerprint or board the
        caller binds the login to (OpenSSH checks the host key against known_hosts only)."""
        import shutil
        from orchestrator.remote import ssh_config
        if not ssh_config.needs_proxy(self.state_dir, self.host):
            return
        why = ""
        if shutil.which("ssh") is None:
            why = "this computer has no OpenSSH client (ssh), and paramiko reads no SSH settings file"
        elif isinstance(self.credentials, OneTimePassword):
            why = "OpenSSH never asks for a password: log in with the framework key (pair the board first)"
        elif self.expect_fingerprint or self.expect_device_id is not None:
            why = ("OpenSSH checks the host key against the framework's known_hosts only, so this step, which is "
                   "bound to the fingerprint or the board shown, cannot go through it")
        if why:
            raise SessionFailure(f"The framework's SSH settings reach {self.host} through a jump host or a proxy "
                                 f"command, which only the OpenSSH client can do, and {why}.", "needs_openssh")
        from orchestrator.remote.transport import SystemSshTransport
        self.openssh = SystemSshTransport

    def _open_openssh(self, waiting: bool) -> None:
        """open() over OpenSSH: one `true` on the board (SystemSshTransport.login), in which OpenSSH checks the host key
        against the framework's known_hosts and logs in with the framework key. Its failures in the words of the
        paramiko login: a changed key (key_changed), a key not trusted yet (not_trusted), the login (login, with the
        reason code OpenSSH's words name; while waiting for the board, a connection that opens and drops again is the
        board not being back yet)."""
        from orchestrator.remote import reasons
        from orchestrator.remote.trust import HostKeyChanged
        from orchestrator.remote.transport import SshFailed
        if self.expect_fingerprint:
            raise SessionFailure(f"Checking the host key of {self.host} against the fingerprint shown needs the "
                                 "'paramiko' package (OpenSSH checks it against known_hosts only).", "needs_paramiko")
        if self.expect_device_id is not None:   # the board the caller means: OpenSSH cannot tell another board's key
            raise SessionFailure(f"Checking that {self.host} is the board meant needs the 'paramiko' package (OpenSSH "
                                 "checks the host key against known_hosts only).", "needs_paramiko")
        if self.cancel.cancelled:
            raise Stopped()
        transport = self.openssh(self.endpoint, self.user, self.state_dir)
        try:
            transport.login()
        except SshFailed as e:
            transport.close()
            output = e.output[-OUTPUT_LINES:]
            if e.reason == reasons.HOST_KEY_CHANGED:
                raise SessionFailure(str(HostKeyChanged(self.host, self.port)), "key_changed", e.reason, e,
                                     output=output) from None
            if e.unknown_key:
                raise SessionFailure(f"The device {self.host} is not trusted yet: confirm its host key fingerprint "
                                     "first.", "not_trusted", output=output) from None
            code = reasons.reach_code(e, while_waiting=waiting)
            raise SessionFailure(f"SSH login to {self.user}@{self.host} failed" + failed_because(code, e), "login",
                                 code, e, output=output) from None
        except Exception as e:  # noqa: BLE001 - no OpenSSH here, a login that gave no answer in time
            transport.close()
            code = reasons.reach_code(e, while_waiting=waiting)
            raise SessionFailure(f"SSH login to {self.user}@{self.host} failed" + failed_because(code, e), "login",
                                 code, e) from None
        self.transport, self.client = transport, None
        if self.on_client is not None:
            self.on_client(transport)

    def adopt(self, client: Any, key: Any = None) -> None:
        """Takes over `client`, which the caller logged in to this session's board with the host key checked by that
        login (ssh_trust.connect checks it against the framework's known_hosts): the session's steps run over it, and
        after a network drop the session logs in again itself, the key checked as at open() and against `key`'s
        fingerprint (the key of that first login)."""
        from orchestrator.utils import ssh_trust
        self.client, self.transport = client, self._factory(client)
        if key is not None:
            self.key, self.fingerprint = key, ssh_trust.fingerprint(key)
            if self.expect_fingerprint is None:
                self.expect_fingerprint = self.fingerprint

    def pair(self, bundle: Any = None, *, mode: str = "private", pairer: Any = None) -> Any:
        """Installs the framework's key on the board (its authorized_keys), only with OneTimePassword credentials
        that ask for it; KeyOnly credentials never reach the key installation (False). Returns what was installed: the
        word of ssh_trust.install_public_key ("restricted", "unrestricted"), or with `pairer` its answer.

        `pairer(client, bundle, password)` is the person's pairing (remote/pairing.py pair with the person's
        TierChooser, given by the person's own channels only; no module an AI can reach makes one): a board that
        cannot take the root-owned key line then gets the person's choice of a key tier (a PairOutcome; "password"
        when the person took none). Without it a board that cannot take the root-owned key line raises
        ssh_trust.GateUnavailable with the ways in its text. `mode` (key_tiers.MODES) is the framework mode the
        root step follows."""
        if not isinstance(self.credentials, OneTimePassword) or not self.credentials.install_key:
            return False
        if pairer is not None and bundle is not None:
            return pairer(self.client, bundle, self.credentials.password)
        from orchestrator.utils import ssh_trust
        line = ssh_trust.client_key(self.state_dir)[1]
        if bundle is None:
            ssh_trust.install_public_key(self.client, line)
            return "unrestricted"
        # llmcf's command gate and the key's restricted line (orchestrator/remote/gate.py)
        return ssh_trust.install_public_key(self.client, line, bundle, password=self.credentials.password,
                                            mode=mode)

    def for_cleanup(self) -> Optional["RemoteSession"]:
        """A new, logged-in session to this session's board for one clean-up command after a Cancel closed this one
        (the stop of the benchmark the framework started there: runtime/device/remote.py): the same board, account
        and host-key checks (the fingerprint of the last login), the framework key only (a one-time password is never
        kept), no wait for the board and a cancel of its own. None when this session cannot log in again (attached())
        or the login fails; the caller then leaves the board as it is."""
        if not self.can_relogin:
            return None
        expect = (self.fingerprint or self.expect_fingerprint) if self.openssh is None else None
        fresh = RemoteSession(self.endpoint, self.user, self.state_dir, credentials=KeyOnly(), expect_fingerprint=expect,
                              expect_device_id=self.expect_device_id, cancel=CancelToken(), clock=self._clock,
                              sleep=self._sleep, reachable=self._reachable, wait_seconds=0.0,
                              transport_factory=self._factory, logger=self.logger, openssh=self.openssh)
        fresh.gated = self.gated   # the same board's key: only the gate, as for this session
        try:
            fresh.open()
        except Exception:  # noqa: BLE001 - unreachable now: the board's own time limit ends the job
            fresh.close()
            return None
        return fresh

    def close(self) -> None:
        """Ends the session: its connection closes, and a step that runs or waits for the board ends (Stopped)
        instead of logging in again. A Cancel that holds the session (CancelToken.hold) ends it this way."""
        self.ended = True
        self._disconnect()

    def _disconnect(self) -> None:
        if self.transport is not None:
            self.transport.close()
        elif self.client is not None:
            _close_quietly(self.client)
        self.client, self.transport = None, None

    def alive(self) -> bool:
        return self.transport is not None and self.transport.alive()

    # --- commands ---

    def wire(self, script: str) -> str:
        """What goes to the board for `script`: the script itself (the transport sends it as one `sh -c`), or on a
        board whose key runs only llmcf's gate the operation's gate call. A script that is no operation cannot run
        there (SessionFailure "not_an_operation", nothing is sent)."""
        if not self.gated:
            return script
        try:
            return gate.gate_call(script)
        except gate.GateRefused as e:
            raise SessionFailure(f"The framework's key on this board runs only llmcf's operations; {e}.",
                                 "not_an_operation") from None

    def refused(self, script: str, status: int, err: str) -> bool:
        """Whether the board's gate refused `script` in script form (nothing ran): the session is gated from now on
        and the caller sends the same operation again as a gate call. A refusal of a gate call is the board's answer
        and stays one (an older gate, an argument the board's folders do not allow)."""
        if self.gated or not isinstance(script, gate.GateScript) or not gate.is_refusal(status, err):
            return False
        self.gated = True
        self.logger.info("The framework's key on this board runs only llmcf's operations (its command gate).")
        return True

    def run(self, script: str, *, timeout: float = COMMAND_TIMEOUT, limit: int = MAX_OUTPUT,
            stdin: Any = None) -> Result:
        """One command on the board: `sh -c '<script>'`, or on a gated board the operation's gate call (wire). The
        first refusal by the board's gate makes the session gated and sends the operation again as a gate call; a
        one-time stdin iterator cannot be sent again, so that step fails plainly instead (the gate read none of it).
        The connection's own errors are raised as they are."""
        result = self.transport.run(self.wire(script), stdin=stdin, timeout=timeout, limit=limit)
        if self.refused(script, result.status, result.err):
            if stdin is not None and not isinstance(stdin, (list, tuple)):
                raise SessionFailure("The framework's key on this board runs only llmcf's operations; this step "
                                     "could not be sent again. Try it once more.", "gated_retry")
            result = self.transport.run(self.wire(script), stdin=stdin, timeout=timeout, limit=limit)
        return result

    def private_dir(self, parent: str = PRIVATE_PARENT, prefix: str = PRIVATE_PREFIX) -> str:
        """A private folder on the board, made by `mktemp -d <parent>/<prefix>.XXXXXXXX` (a fixed path could be made by
        another user first); its answer must be exactly such a folder, else SessionFailure with the board's words in
        `output`."""
        result = self.run(gate.script("mkprivate", parent, prefix))
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
        while not self.cancel.cancelled and not self.ended:
            left = end - self._clock()
            if left <= 0:
                return
            self._sleep(min(0.25, left))

    def _port_open(self) -> bool:
        from orchestrator.remote import ssh_config
        if ssh_config.needs_proxy(self.state_dir, self.host):
            # Behind a jump host the board's port is not reachable from here: only the login through the jump tells.
            return True
        if self._reachable is not None:
            return bool(self._reachable(self.host, self.port))
        from orchestrator.remote import reasons
        return reasons.check_port(self.host, self.port, timeout=2) is None

    def wait_for_host(self, step: str) -> None:
        """After a network drop: waits until the board answers on its SSH port again and logs in again, or raises
        SessionFailure "wait" (with the reason code) after wait_seconds. A login refused for another reason (a changed
        key, a refused password) ends the wait with its own failure; a cancel ends it at once (Stopped)."""
        from orchestrator.remote import reasons
        self._disconnect()
        started = self._clock()
        code: Optional[str] = None
        while True:
            if self.cancel.cancelled or self.ended:
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
                    if self.ended:   # closed while it logged in again
                        self._disconnect()
                        raise Stopped()
                    return
            elapsed = self._clock() - started
            if elapsed >= self.wait_seconds:
                self.waited += elapsed
                code = code or reasons.check_port(self.host, self.port) or reasons.HOST_DOWN
                raise SessionFailure(f"The board did not answer again within {int(self.wait_seconds)} s ({code})",
                                     "wait", code, state="unknown" if step in JOB_STEPS else "ok", step=step)
            self.pause(min(WAIT_INTERVAL, max(0.1, self.wait_seconds - elapsed)))

    def dropped(self, error: BaseException) -> bool:
        """Whether `error` is a network drop to wait through, within MAX_RESUMES: the session itself broke, or the
        board gave no answer within the command's time limit (transport.timed_out: while the network is gone the
        connection still stands until TCP gives up, so the time limit notices first). A board that answers with an
        error (a full disk) is no drop, and an attached() session never waits."""
        if self.ended or not self.can_relogin or self.transport is None or self.resumed >= MAX_RESUMES:
            return False
        return not self.transport.alive() or timed_out(error)

    _dropped = dropped   # its first name

    def _say_dropped(self, step: str) -> None:
        self.logger.warning(f"The connection to {self.host} dropped during {_DOING.get(step, 'a step on the board')}; "
                            "waiting for the board.")

    # --- a step through a network drop ---

    def resumable(self, action: Callable[[], Any], *, step: str = "command", again: bool = True) -> Any:
        """`action()` (a command or a file operation on the board through self.transport) through a network drop:
        the session waits for the board (wait_for_host(step)), logs in again and, with `again`, does it once more (a
        "back" event with `step` first). Without `again` (a step that must not run twice) it raises SessionFailure
        "dropped" (state unknown) once the board answers again, so the caller can still clean up. A command that stays
        silent while its connection stands is no drop when that connection still answers (answers()): the board is
        busy with it, so its timeout is raised at once, as before the board session; when the connection is silent
        too (the network is gone, while paramiko's connection stands until TCP gives up), the session waits, and such
        a command is tried SILENT_TRIES more times, then its timeout is raised. Anything else (the board's own error,
        too much output) is raised as it is; a cancel raises Stopped."""
        silent = 0
        while True:
            try:
                return action()
            except (Stopped, SessionFailure):
                raise
            except Exception as e:  # noqa: BLE001 - a network drop, or the board's or the connection's own error
                if self.cancel.cancelled or self.ended:
                    raise Stopped() from None
                if not self.dropped(e):
                    raise
                if timed_out(e) and self.transport.alive():
                    silent += 1
                    if silent > SILENT_TRIES or self.answers():
                        raise   # the board keeps the connection but does not answer this command: no drop
                self._say_dropped(step)
                self.wait_for_host(step)
                self.resumed += 1
                if not again:
                    raise SessionFailure("The connection to the board dropped during this step. The board answers "
                                         "again, but whether the step ran there is unknown, so it is not repeated.",
                                         "dropped", error=e, state="unknown", step=step) from None
                self.on_event(RemoteEvent("back", step=step))

    def answers(self) -> bool:
        """Whether the standing connection still answers `exit 0` within ANSWER_TIMEOUT seconds: then a command on it
        that stayed silent is busy on the board, not cut off by a network drop (it is neither waited for nor run
        again)."""
        if self.transport is None:
            return False
        try:
            self.transport.run(self.wire(gate.script("ping")), timeout=ANSWER_TIMEOUT, limit=64)
        except Exception:  # noqa: BLE001 - no answer either: the network is gone
            return False
        return True

    def run_resumable(self, script: str, *, again: bool = True, step: str = "command",
                      timeout: float = COMMAND_TIMEOUT, limit: int = MAX_OUTPUT, stdin: Any = None) -> Result:
        """run() through a network drop (resumable): `again` for a command that may run twice (it only reads, or does
        the same again); `stdin` is sent again then, so it must be a list, not a one-time iterator."""
        return self.resumable(lambda: self.run(script, timeout=timeout, limit=limit, stdin=stdin), step=step,
                              again=again)

    # --- the upload ---

    def upload(self, fh: Any, remote_path: str, size: int, sha256: str, *, private: bool = False) -> None:
        """Uploads the open file `fh` (`size` bytes, sha256 `sha256`) as <remote_path>.part and renames it when its
        sha256 on the board is the same; resumes after a network drop (see the module docstring). `private`: every
        new copy on the board is made under umask 077 (`umask 077 && : >`) before it is written into, also the one
        sent again after a damaged arrival, so the file is the user's only as `umask 077 && cat >` made it."""
        part = f"{remote_path}.part"
        folder = posixpath.dirname(remote_path)
        restarted = False
        while True:
            try:
                offset = self._resume_offset(fh, part, size)
                # the deploy's words since N5 (they reach the console of `llmcf deploy run`)
                self.logger.info("Uploading package..." if not offset else f"Resuming the upload at {offset} bytes...")
                self._send(fh, part, offset, size, private)
                result = self.run(gate.script("file-sha256", part), timeout=hash_timeout(size))
                if result.status == 0 and result.out.split()[:1] == [sha256]:
                    result = self.run(gate.script("file-move", part, remote_path))
                    if result.status == 0:
                        return
                    raise SessionFailure("The file arrived, but could not be renamed on the device.", "rename")
                if restarted:
                    raise SessionFailure("The file arrived damaged twice (its sha256 on the device differs).",
                                         "upload_damaged")
                restarted = True
                self.run(gate.script("file-remove", part))
            except (Stopped, SessionFailure):
                raise
            except Exception as e:  # noqa: BLE001 - a network drop, or a failure on the board (disk full, ...)
                if self.cancel.cancelled or self.ended:
                    raise Stopped() from None
                if not self.dropped(e):
                    raise SessionFailure(f"The upload to {self.host} failed: {e}", "upload_failed", error=e) from None
                self._say_dropped("upload")
                self.wait_for_host("upload")
                self.resumed += 1
                if self.run(gate.script("dir-exists", folder)).status != 0:
                    # the board restarted and its /tmp is empty: nothing of the file is left there
                    raise SessionFailure("The board answers again, but the upload folder is gone (the board "
                                         "restarted).", "folder_gone")

    def _resume_offset(self, fh: Any, part: str, size: int) -> int:
        """How many bytes of the file are on the board and match it (0: start from the beginning)."""
        offset = self._stat_size(part) or 0
        if offset <= 0 or offset > size:
            return 0
        result = self.run(gate.script("file-prefix-sha256", str(offset), part), timeout=hash_timeout(offset))
        fh.seek(0)
        prefix = sha256_fileobj(fh, limit=offset, chunk=UPLOAD_CHUNK)  # the first `offset` bytes of the file
        if result.status != 0 or result.out.split()[:1] != [prefix]:
            self.logger.warning("What arrived on the board does not match the package; the upload starts again.")
            return 0
        self.on_event(RemoteEvent("back", done=offset, total=size))
        return offset

    def _stat_size(self, part: str) -> Optional[int]:
        """The size of `part` on the board (None: none there): over SFTP, or on a gated board (which refuses SFTP)
        with the gate's file-size."""
        if not self.gated:
            return self.transport.stat_size(part)
        result = self.run(gate.script("file-size", part))
        answer = result.out.strip()
        if result.status == 0 and answer == "none":
            return None
        if result.status == 0 and answer.isdigit():
            return int(answer)
        raise OSError(f"the device did not say the size of the file (exit {result.status})")

    def _send(self, fh: Any, part: str, offset: int, size: int, private: bool = False) -> None:
        if private and not offset:
            # a new copy: made empty by the user's shell under umask 077 first (writing into it keeps its mode); when
            # that fails, the SFTP open below says why
            self.run(gate.script("file-create", part))
        fh.seek(offset)
        if self.gated:
            self._send_through_gate(fh, part, offset, size)
            return
        with self.transport.open_write(part, offset) as remote:
            done = offset
            self.on_event(RemoteEvent("uploading", done=done, total=size))
            for chunk in iter(lambda: fh.read(UPLOAD_CHUNK), b""):
                if self.cancel.cancelled or self.ended:
                    raise Stopped()
                remote.write(chunk)
                done += len(chunk)
                self.on_event(RemoteEvent("uploading", done=done, total=size))

    def _send_through_gate(self, fh: Any, part: str, offset: int, size: int) -> None:
        """The upload on a gated board: the file's bytes from `offset` on the stdin of the gate's file-write (a new
        copy) or file-append (after what arrived), with the same events as over SFTP. A drop raises the connection's
        error; upload() then waits for the board and continues at what arrived, as over SFTP."""
        done = [offset]

        def chunks():
            self.on_event(RemoteEvent("uploading", done=done[0], total=size))
            for chunk in iter(lambda: fh.read(UPLOAD_CHUNK), b""):
                if self.cancel.cancelled or self.ended:
                    raise Stopped()
                yield chunk
                done[0] += len(chunk)
                self.on_event(RemoteEvent("uploading", done=done[0], total=size))
        verb = "file-append" if offset else "file-write"
        result = self.transport.run(self.wire(gate.script(verb, part)), stdin=chunks(),
                                    timeout=max(COMMAND_TIMEOUT, hash_timeout(size - offset)))
        if result.status != 0:
            raise OSError(f"the device could not write the file (exit {result.status})")

    # --- a job that runs on the board on its own ---

    @staticmethod
    def detached_command(folder: str, job: str) -> str:
        """The command that starts `job` in `folder` on the board on its own (nohup, nothing attached to the SSH
        session): its exit status goes into JOB_STATUS. The marker JOB_STARTED is made first, and a folder that has it
        is never started again, also when this command reaches the board twice."""
        return gate.detached_command(shlex.quote(folder), job)

    @staticmethod
    def poll_command(folder: str) -> str:
        return gate.poll_command(shlex.quote(folder))

    def run_detached(self, folder: str, job: str, *, timeout: float = JOB_TIMEOUT, poll: float = JOB_POLL,
                     step: str = "install") -> int:
        """Starts `job` once (detached_command) and waits for its exit status; after a network drop it waits for the
        board, logs in again and keeps following the same job. Once the start command is on its way, the job may run
        on the board even when its answer is lost (detached_sent): from then on a failure says the state is unknown.
        `step` names the job in the wait's events ("install": deploy.sh; "bench": the benchmark on the board)."""
        if isinstance(job, gate.GateScript):   # one of llmcf's jobs: its start is an operation of its own
            start = gate.script("start-" + job.verb, folder, *job.args)
        else:
            start = self.detached_command(folder, job)
        return self._follow(folder, start, timeout, poll, step)

    def follow(self, folder: str, *, timeout: float = JOB_TIMEOUT, poll: float = JOB_POLL,
               step: str = "install") -> int:
        """Follows a job that run_detached started in `folder` before (maybe in another session: the job ran on while
        nobody watched) to its exit status, again after a network drop; it is never started here."""
        return self._follow(folder, None, timeout, poll, step)

    @classmethod
    def _poll(cls, folder: str) -> str:
        """The job's poll as llmcf's operation (job-poll), or for a folder that is none of llmcf's places (a
        stand-in's) the same command as a script."""
        try:
            return gate.script("job-poll", folder)
        except gate.GateRefused:
            return cls.poll_command(folder)

    def _follow(self, folder: str, start: Optional[str], timeout: float, poll: float, step: str) -> int:
        started_at = self._clock()
        sent = start is None
        if sent:
            self.detached_sent = True   # a job that was started before may run on the board
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
                result = self.run(self._poll(folder))
                answer = result.out.strip()
                if result.status == 0 and answer.isdigit():
                    return int(answer)
                if answer == "gone":
                    raise SessionFailure("The board restarted during the job (its folder is gone).", "job_gone",
                                         state="unknown")
                if self._clock() - started_at > timeout:
                    raise SessionFailure(f"The job did not finish on the device within {int(timeout)} s.",
                                         "job_timeout", state="unknown")
                if self.cancel.cancelled or self.ended:
                    raise Stopped()
                self.pause(poll)
            except (Stopped, SessionFailure):
                raise
            except Exception as e:  # noqa: BLE001 - a network drop while the board works on its own
                if self.cancel.cancelled or self.ended:
                    raise Stopped() from None
                if not self.dropped(e):
                    raise SessionFailure(f"The job on {self.host} could not be followed: {e}", "job_lost", error=e,
                                         state="unknown" if self.detached_sent else "ok") from None
                self._say_dropped(step)
                self.wait_for_host(step)
                self.resumed += 1
                first = False
                # the start command is sent again only when its answer never came: the marker on the board makes it
                # answer started=already instead of starting the job a second time
                self.on_event(RemoteEvent("back", step=step))


__all__ = ["ANSWER_COMMAND", "ANSWER_TIMEOUT", "CancelToken", "HASH_RATE", "JOB_LOG", "JOB_POLL", "JOB_STARTED",
           "JOB_STATUS", "JOB_STEPS", "JOB_TIMEOUT", "KeyOnly", "MAX_RESUMES", "OneTimePassword", "PRIVATE_PARENT",
           "PRIVATE_PREFIX", "RemoteEvent", "RemoteSession", "SILENT_TRIES", "SessionFailure", "Stopped",
           "UPLOAD_CHUNK", "WAIT_INTERVAL", "WAIT_SECONDS", "failed_because", "hash_timeout"]
