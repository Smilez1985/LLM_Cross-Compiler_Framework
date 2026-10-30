#!/usr/bin/env python3
"""
The connection to a board: whether SSH works here, and one Transport interface that RemoteSession
(orchestrator/remote/session.py) works through. paramiko is imported on first use only (decision RG1: the CLI, the
MCP server and the GUI start without it).

- paramiko_module(): the paramiko module, imported the first time something needs it; None when it cannot be
  imported (not installed, or an installation whose import fails, for example a missing compiled dependency).
  imported_paramiko(): paramiko only when something imported it already (reasons.classify reads its error types).
- available() -> (bool, reason): whether paramiko imports here, and why not ("" when it does). This is a real import,
  not a look for the package on disk: a broken installation counts as missing everywhere, so the deploy falls back to
  OpenSSH instead of failing on the first login.
- PARAMIKO_AVAILABLE: true while paramiko imports, asked only when it is first tested. DeploymentManager and the
  Builder tab keep it under their old module attribute PARAMIKO_AVAILABLE (tests set that to True or False).
- sh_command(script): a script as the one command the board's login shell gets, `sh -c '<script>'` (every command
  the framework sends to a board has this form).
- Transport: what a session needs from a connection. run(script, stdin, timeout, limit) sends one `sh -c` and reads
  what the board answers with one bounded reader (read_limited): a command that stays silent longer than `timeout`
  seconds or sends more than `limit` bytes ends; stat_size(path) and open_write(path, offset) reach the board's files
  over SFTP; alive() tells a dropped network from a failure on the board; host_key is the key of the authenticated
  connection; close().
- ParamikoTransport: the Transport over an authenticated paramiko SSHClient, with keepalive on. open_client() is that
  login (orchestrator/utils/ssh_trust.connect is its old name and delegates here): the framework key, a one-time
  password for this connection only, the host key checked against the framework's known_hosts, and paramiko_quiet
  around it so no password or key material reaches a log.

- SystemSshTransport: the second Transport, over this computer's OpenSSH client for an installation without paramiko
  (consolidation chain c2-remote-system, decision A5). The deploy's OpenSSH fallback is a RemoteSession over it, so
  it waits for the board, resumes the upload and follows the detached installation like the paramiko deploy.
  openssh_options(state_dir) are the options of every OpenSSH call: the framework key and known_hosts only, never a
  question or a new key, a login time limit and keepalive. openssh_argv() is the one place an ssh, scp or sftp
  command line is built; config_value() quotes a path for OpenSSH's own option parser (a folder with a space).

A failure of the network (OSError, EOFError, socket.timeout) leaves run() as it is: the caller decides whether it is a
drop to wait through (alive() is false, or timed_out(error): no answer within the time limit) or the end. Only more
output than the cap is the transport's own failure, TooMuchOutput. Over OpenSSH a failure of ssh itself (its exit
status 255) is SshFailed, an OSError with the reason code OpenSSH's words name.
"""

import contextlib
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from orchestrator.remote.errors import RemoteFailure

_lock = threading.Lock()
_loaded: Optional[Tuple[Any, str]] = None   # (the module or None, why not) once it was looked up

COMMAND_TIMEOUT = 120          # seconds a command on the board may stay silent
HASH_TIMEOUT = 900             # ... while it hashes a large file (the board prints nothing until it is done)
MAX_OUTPUT = 1024 * 1024       # bytes of a command's stdout (and of its stderr) the framework reads at most
LOGIN_TIMEOUT = 10.0
KEEPALIVE_SECONDS = 15         # an idle session sends a keepalive this often, so a dead network shows up
KEEPALIVE_COUNT = 3            # OpenSSH ends a session after this many keepalives without an answer
OPENSSH_FAILED = 255           # OpenSSH's own exit status: ssh itself failed, not the command on the board

_log = logging.getLogger(__name__)


def paramiko_module() -> Any:
    """The paramiko module, imported on the first call; None when it cannot be imported."""
    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                try:
                    import paramiko
                except ModuleNotFoundError as e:
                    _loaded = (None, "the 'paramiko' package is not installed" if e.name == "paramiko" else
                               f"the 'paramiko' package cannot be loaded: {e.name} is missing")
                except Exception as e:  # noqa: BLE001 - an installation whose import fails is a missing one
                    _loaded = (None, f"the 'paramiko' package cannot be loaded ({e.__class__.__name__})")
                else:
                    _loaded = (paramiko, "")
    return _loaded[0]


def imported_paramiko() -> Any:
    """paramiko when something imported it already, else None; never the first import (RG1: classify reads its error
    types only once they can exist). The import statement, not the sys.modules entry itself, waits for an import
    another thread started and has not finished, so a half-loaded module is never read."""
    if sys.modules.get("paramiko") is None:
        return None
    try:
        import paramiko
    except Exception:  # noqa: BLE001 - that other import failed: no paramiko error can exist
        return None
    return paramiko


_UNSET = object()


def _ssh_trust_seam() -> Any:
    """What orchestrator.utils.ssh_trust.paramiko was set to (its old seam: a test that sets it to None makes that
    module behave as without paramiko), else _UNSET; read without importing anything."""
    trust = sys.modules.get("orchestrator.utils.ssh_trust")
    return vars(trust).get("paramiko", _UNSET) if trust is not None else _UNSET


def available() -> Tuple[bool, str]:
    """(True, "") when paramiko imports here, else (False, why not). ssh_trust.paramiko set to None counts as
    missing here too, so every reader (the person's board steps, `scan --device`, Mojo) agrees with that module."""
    if _ssh_trust_seam() is None:
        return False, "the 'paramiko' package is not installed"
    module = paramiko_module()
    return module is not None, ("" if module is not None else _loaded[1])


class _Availability:
    """True while paramiko imports; the import happens when the value is first tested, never at import time."""

    def __bool__(self) -> bool:
        return available()[0]

    def __repr__(self) -> str:
        return repr(bool(self))


PARAMIKO_AVAILABLE = _Availability()


def sh_command(script: str) -> str:
    """`script` as the one command sent to the board: `sh -c '<script>'`. sshd hands every command to the account's
    login shell; as one quoted word for sh, the redirections, && and $? are read by sh, so a login shell such as fish
    or csh (where `2>/dev/null` means something else or nothing) does not matter."""
    return "sh -c " + shlex.quote(script)


# --- what a command on the board answered ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    """A command's exit status and what it printed (the board's words: untrusted text)."""
    status: int
    out: str
    err: str


class TooMuchOutput(RemoteFailure):
    """A command printed more than the framework reads (`limit` bytes); the command's channel is closed."""


def read_limited(stream: Any, limit: int) -> bytes:
    """What `stream` gives, read in pieces until its end or until one byte more than `limit` (the caller then knows
    the board sent too much); never more."""
    data = b""
    while len(data) <= limit:
        chunk = stream.read(min(65536, limit + 1 - len(data)))
        if not chunk:
            break
        data += chunk
    return data


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def timed_out(error: BaseException) -> bool:
    """Whether `error` says the board gave no answer within a command's time limit: a read that stayed silent
    (socket.timeout) or a command channel paramiko could not open in time. While the network is gone, paramiko's
    connection stands until TCP gives up (on Linux after about 15 minutes), so this is what a drop looks like first;
    RemoteSession waits through it as through a broken connection."""
    if isinstance(error, (socket.timeout, TimeoutError)):
        return True
    paramiko = imported_paramiko()
    return paramiko is not None and isinstance(error, paramiko.SSHException) and \
        str(error).startswith("Timeout opening channel")


# --- the interface ----------------------------------------------------------------------------------------------------

class Transport:
    """What RemoteSession needs from a connection to a board (see the module docstring)."""

    def run(self, script: str, *, stdin: Optional[Iterable[bytes]] = None, timeout: float = COMMAND_TIMEOUT,
            limit: int = MAX_OUTPUT) -> Result:
        raise NotImplementedError

    def stat_size(self, path: str) -> Optional[int]:
        """The size of a file on the board, None when it does not exist."""
        raise NotImplementedError

    def open_write(self, path: str, offset: int = 0):
        """A context manager giving a file on the board to write: new (offset 0) or continued at `offset`."""
        raise NotImplementedError

    def alive(self) -> bool:
        """Whether the connection itself still stands (False: the network dropped)."""
        raise NotImplementedError

    @property
    def host_key(self) -> Any:
        """The host key of the authenticated connection."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


# --- paramiko ---------------------------------------------------------------------------------------------------

_quiet_lock = threading.Lock()
_quiet_users = 0
_quiet_saved: List[Tuple[logging.Logger, int]] = []


@contextlib.contextmanager
def paramiko_quiet():
    """Holds paramiko's loggers at INFO or higher while a connection authenticates, also when the framework logs at
    DEBUG: a password or key material must never reach a log file. Levels come back when the last login is done."""
    global _quiet_users, _quiet_saved
    with _quiet_lock:
        if _quiet_users == 0:
            known = list(logging.Logger.manager.loggerDict)  # a copy: another thread may add a logger meanwhile
            names = ["paramiko"] + sorted(n for n in known if n.startswith("paramiko."))
            for name in names:
                logger = logging.getLogger(name)
                # A child without its own level follows "paramiko"; only explicit levels below INFO are raised.
                level = logger.getEffectiveLevel() if name == "paramiko" else logger.level or logging.INFO
                if level < logging.INFO:
                    _quiet_saved.append((logger, logger.level))
                    logger.setLevel(logging.INFO)
        _quiet_users += 1
    try:
        yield
    finally:
        with _quiet_lock:
            _quiet_users -= 1
            if _quiet_users == 0:
                for logger, level in _quiet_saved:
                    logger.setLevel(level)
                _quiet_saved = []


def open_client(host: str, user: str, state_dir: Path, port: int = 22, password: Optional[str] = None,
                timeout: float = LOGIN_TIMEOUT):
    """An authenticated paramiko SSHClient for a board whose host key is already trusted (the old
    ssh_trust.connect, which delegates here). Uses the framework key; a password (pairing) is used for this connection
    only. The trust store, the framework key and the trust-on-first-use policy are read through
    orchestrator.utils.ssh_trust at call time, so a test that sets ssh_trust.paramiko = None sees no paramiko here."""
    from orchestrator.utils import ssh_trust
    paramiko = ssh_trust._require_paramiko()
    private, _ = ssh_trust.client_key(state_dir)
    client = paramiko.SSHClient()
    path = ssh_trust.known_hosts_path(state_dir)
    if path.exists():
        client.load_host_keys(str(path))
    client.set_missing_host_key_policy(ssh_trust._tofu_policy()())
    try:
        with paramiko_quiet():
            client.connect(host, port=port, username=user, key_filename=str(private), password=password,
                           look_for_keys=False, allow_agent=False, timeout=timeout, banner_timeout=timeout,
                           auth_timeout=timeout)
    except paramiko.BadHostKeyException:
        client.close()
        raise ssh_trust.HostKeyChanged(host, port) from None
    except Exception:
        client.close()
        raise
    return client


class ParamikoTransport(Transport):
    """The Transport over an authenticated paramiko SSHClient (or a stand-in with exec_command, open_sftp,
    get_transport and close). With `keepalive` seconds the idle session sends keepalives."""

    def __init__(self, client: Any, keepalive: Optional[float] = None):
        self.client = client
        if keepalive:
            try:
                self.client.get_transport().set_keepalive(int(keepalive))
            except Exception:  # noqa: BLE001 - a client that cannot keep alive still works
                pass

    def run(self, script: str, *, stdin: Optional[Iterable[bytes]] = None, timeout: float = COMMAND_TIMEOUT,
            limit: int = MAX_OUTPUT) -> Result:
        """One `sh -c '<script>'` on the board: `stdin` is written and closed, stdout and stderr are each read up to
        `limit` bytes. A silent board raises socket.timeout after `timeout` seconds, a lost connection its OSError or
        EOFError; more output than `limit` raises TooMuchOutput."""
        ch_in, ch_out, ch_err = self.client.exec_command(sh_command(script), timeout=timeout)
        if stdin is not None:
            for chunk in stdin:
                ch_in.write(chunk)
            ch_in.flush()
        ch_in.channel.shutdown_write()
        out = read_limited(ch_out, limit)
        err = read_limited(ch_err, limit)
        if len(out) > limit or len(err) > limit:
            ch_out.channel.close()
            raise TooMuchOutput(f"the device sent more than {limit} bytes")
        return Result(ch_out.channel.recv_exit_status(), _text(out), _text(err))

    def stat_size(self, path: str) -> Optional[int]:
        sftp = self.client.open_sftp()
        try:
            try:
                return int(sftp.stat(path).st_size or 0)
            except FileNotFoundError:
                return None
        finally:
            sftp.close()

    @contextlib.contextmanager
    def open_write(self, path: str, offset: int = 0) -> Iterator[Any]:
        """The file at `path` on the board, pipelined (paramiko does not wait for each write's answer): created anew,
        or opened at `offset` to continue it."""
        sftp = self.client.open_sftp()
        try:
            with sftp.open(path, "r+b" if offset else "wb") as remote:
                remote.set_pipelined(True)
                if offset:
                    remote.seek(offset)
                yield remote
        finally:
            sftp.close()

    def alive(self) -> bool:
        try:
            transport = self.client.get_transport()
        except Exception:  # noqa: BLE001 - a session that cannot say: treated as broken
            return False
        return transport is not None and transport.is_active()

    @property
    def host_key(self) -> Any:
        return self.client.get_transport().get_remote_server_key()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass


# --- OpenSSH ------------------------------------------------------------------------------------------------------

_PORT_FLAG = {"ssh": "-p", "scp": "-P", "sftp": "-P"}
# What this computer's OpenSSH prints (English whatever the locale) when the board's host key is not in the
# framework's known_hosts yet (the first contact; a changed key is reasons.classify_ssh_output's), and when it refuses
# the framework's own key file (its permissions, a damaged file): a problem on this computer, not the board.
_UNKNOWN_KEY_OUTPUT = ("Host key verification failed", "host key is known for")
_KEY_FILE_OUTPUT = ("UNPROTECTED PRIVATE KEY FILE", "Load key", "Bad owner or permissions")
_KEY_TOO_OPEN = ("are too open",)   # "Permissions 0644 for '<key>' are too open." (the line that names the key)


def config_value(path: Any) -> str:
    """`path` as one value of an OpenSSH -o option: in double quotes, so a space (a Windows account name with one, in
    the profile folder) does not split it into two files. A backslash or a double quote inside is escaped as OpenSSH's
    option parser reads it (misc.c argv_split). A Windows path is written with forward slashes, which OpenSSH for
    Windows reads as the same path, so it holds no backslash at all."""
    text = path.as_posix() if isinstance(path, PureWindowsPath) else str(path)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def openssh_file(path: Any) -> str:
    """`path` as the value of an OpenSSH option that expands %-tokens (IdentityFile, UserKnownHostsFile, ...): quoted
    as config_value does, with each '%' written as '%%' (DECISIONS round 25 Q5), so a '%' in the framework's own
    folder names that very file. A Windows path is written with forward slashes."""
    from orchestrator.remote import ssh_config
    text = path.as_posix() if isinstance(path, PureWindowsPath) else str(path)
    return config_value(ssh_config.tokens(text))


def key_file_of(options: Sequence[str]) -> str:
    """The framework key's file in `options` (openssh_options' IdentityFile option, or an older list's -i), as
    OpenSSH names it in what it prints: unquoted, each '%%' read back as '%'."""
    options = list(options)
    for i, word in enumerate(options[:-1]):
        if word == "-i":
            return options[i + 1]
        if word == "-o" and options[i + 1].startswith("IdentityFile="):
            value = options[i + 1][len("IdentityFile="):]
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return value.replace("%%", "%")
    return ""


# The null device as OpenSSH names a file: GlobalKnownHostsFile points here, so this computer's system-wide known_hosts
# (/etc/ssh/ssh_known_hosts, on Windows %ProgramData%\ssh\ssh_known_hosts) never decides about a board's host key.
NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"


def openssh_options(state_dir: Path, *, known_hosts: Optional[Path] = None, accept_new: bool = False) -> List[str]:
    """The options of every OpenSSH call of the framework, all on the command line, which OpenSSH reads first (the
    first value it obtains for an option wins):

    - -F with the framework's own configuration file (orchestrator/remote/ssh_config.py, DECISIONS round 25 Q4), so
      OpenSSH reads neither ~/.ssh/config nor /etc/ssh/ssh_config; it holds only what the person added (a ProxyJump,
      for example) and is checked before each use (SshConfigRefused, a local problem, when a hand edit put a line in
      it the framework never uses);
    - the framework's known_hosts only (UserKnownHostsFile; GlobalKnownHostsFile is the null device, so a key in this
      computer's system-wide known_hosts is never taken either), never a question or an accepted new key (BatchMode,
      StrictHostKeyChecking), and OpenSSH never writes to it or trusts a key another way (CheckHostIP, UpdateHostKeys
      and VerifyHostKeyDNS off);
    - the framework key only (IdentitiesOnly, IdentityFile);
    - a connection that gives up after LOGIN_TIMEOUT seconds (ConnectTimeout) and a keepalive every KEEPALIVE_SECONDS
      that ends a session after KEEPALIVE_COUNT unanswered ones (ServerAliveInterval, ServerAliveCountMax), so a
      dropped network ends the call instead of holding it.

    The framework's own paths in options that expand %-tokens are written with '%%' for a '%' (Q5, openssh_file); -F's
    path is read literally. The key file is made private to this account first (OpenSSH refuses a key others can read:
    utils/atomic.py owner_only, an ACL on Windows). The framework key and known_hosts are read through
    orchestrator/utils/ssh_trust at call time (ssh_trust.system_ssh_options is this function's old name).

    `known_hosts` and `accept_new` serve one call only: reading the key a board behind a jump host presents
    (remote/trust.presented_key_through_openssh): a temporary known_hosts instead of the framework's, into which
    OpenSSH may write the board's new key (StrictHostKeyChecking=accept-new); the framework's own known_hosts is never
    written that way, the person still says yes to the fingerprint first."""
    from orchestrator.remote import ssh_config
    from orchestrator.utils import ssh_trust
    from orchestrator.utils.atomic import owner_only
    config = ssh_config.prepare(state_dir)
    private, _ = ssh_trust.client_key(state_dir)
    try:
        owner_only(private)
    except OSError as e:   # OpenSSH then says what it refuses; the login fails with local_problem
        _log.warning(f"The framework's SSH key could not be made private to this account ({e.strerror or e}).")
    return ["-F", ssh_config.argument(config),
            "-o", "BatchMode=yes", "-o", f"StrictHostKeyChecking={'accept-new' if accept_new else 'yes'}",
            "-o", f"UserKnownHostsFile={openssh_file(known_hosts or ssh_trust.known_hosts_path(state_dir))}",
            "-o", f"GlobalKnownHostsFile={NULL_DEVICE}",
            "-o", "CheckHostIP=no", "-o", "UpdateHostKeys=no", "-o", "VerifyHostKeyDNS=no",
            "-o", "IdentitiesOnly=yes", "-o", f"ConnectTimeout={int(LOGIN_TIMEOUT)}",
            "-o", f"ServerAliveInterval={KEEPALIVE_SECONDS}", "-o", f"ServerAliveCountMax={KEEPALIVE_COUNT}",
            "-o", f"IdentityFile={openssh_file(private)}"]


def remote_location(endpoint: Any, user: str, path: Optional[str] = None) -> str:
    """The board as scp and sftp name it: user@host, with `path` user@host:path; an IPv6 address in brackets, since
    both read a path after the first colon."""
    host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
    return f"{user}@{host}" + (f":{path}" if path is not None else "")


def openssh_argv(program: str, endpoint: Any, user: str, options: Sequence[str], *rest: str) -> List[str]:
    """The one place the framework builds an OpenSSH command line (a list: no shell reads it):
    ssh OPTIONS -T -p PORT user@host COMMAND, scp OPTIONS -P PORT SOURCE... TARGET, sftp OPTIONS -P PORT DESTINATION.
    `program` is ssh, scp or sftp, or the path of one (its file name says which). For scp and sftp a board's place is
    remote_location(...). `endpoint` and `user` are checked ones (valid_host, valid_user: neither starts with '-', so
    OpenSSH never reads them as an option)."""
    kind = str(program).replace("\\", "/").rpartition("/")[2].lower()
    kind = kind[:-4] if kind.endswith(".exe") else kind
    if kind not in _PORT_FLAG:
        raise ValueError(f"no OpenSSH program: {program!r}")
    argv = [str(program), *options]
    if kind == "ssh":
        return argv + ["-T", _PORT_FLAG[kind], str(int(endpoint.port)), f"{user}@{endpoint.host}", *rest]
    return argv + [_PORT_FLAG[kind], str(int(endpoint.port)), *rest]


class SshFailed(OSError):
    """OpenSSH itself failed (its exit status 255, or it was stopped), not the command on the board: it could not
    connect or log in, the host key is unknown or changed, the framework's key file was refused, or the connection
    broke. `reason`: the SSH reason code OpenSSH's words name (reasons.classify_ssh_output; "" when they name none),
    `unknown_key`: the board's key is not in the framework's known_hosts yet, `output`: what OpenSSH printed (this
    computer's words, which can quote the board's banner: kept apart, never in the message). A refused key file is a
    problem on this computer: errno EACCES and the key's file name (reasons.local_problem)."""

    def __init__(self, message: str, *, reason: str = "", unknown_key: bool = False, key_file: str = "",
                 output: Sequence[str] = ()):
        if key_file:
            import errno
            super().__init__(errno.EACCES, message, key_file)
        else:
            super().__init__(message)
        self.reason, self.unknown_key, self.output = reason, unknown_key, list(output)

    @classmethod
    def of(cls, text: str, key_file: str = "") -> "SshFailed":
        """The failure OpenSSH's words `text` describe. OpenSSH ignores a key file it refuses ("This private key will
        be ignored") and goes on without it, so the board then refuses the login and OpenSSH's last words are
        "Permission denied": when the framework's key file (`key_file`, named by its file name) is the one refused,
        the cause is still this computer, not the board."""
        from orchestrator.remote import reasons
        output = [line for line in (text or "").splitlines() if line.strip()]
        reason = reasons.classify_ssh_output(text) or ""
        name = str(key_file).replace("\\", "/").rpartition("/")[2]
        if reason == reasons.AUTH_FAILED and name and any(
                name in line and any(needle in line for needle in _KEY_FILE_OUTPUT + _KEY_TOO_OPEN)
                for line in output):
            reason = ""   # the board refused the login because OpenSSH did not offer the framework's key
        if not reason and any(needle in (text or "") for needle in _KEY_FILE_OUTPUT):
            return cls("OpenSSH refuses the framework's SSH key file (other accounts may read it, or it is damaged)",
                       key_file=key_file or "the framework's SSH key", output=output)
        if not reason and any(needle in (text or "") for needle in _UNKNOWN_KEY_OUTPUT):
            return cls("the board's host key is not in the framework's known_hosts", unknown_key=True, output=output)
        if reason:
            return cls(f"OpenSSH could not reach or log in to the board ({reason})", reason=reason, output=output)
        return cls("OpenSSH lost the connection to the board or could not make it (its words are in the device "
                   "output)", output=output)


def _no_window() -> Dict[str, Any]:
    """On Windows, OpenSSH (a console program) opens no console window when the GUI starts it."""
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if os.name == "nt" else {}


class _Readers:
    """Reads a child's stdout and stderr in the background, each up to `limit` bytes plus one (more is too much)."""

    def __init__(self, process: Any, limit: int):
        self.limit, self.box = limit, {}
        self.threads = [threading.Thread(target=self._read, args=(name, stream), daemon=True)
                        for name, stream in (("out", process.stdout), ("err", process.stderr)) if stream is not None]
        for thread in self.threads:
            thread.start()

    def _read(self, name: str, stream: Any) -> None:
        try:
            self.box[name] = read_limited(stream, self.limit)
        except (OSError, ValueError):   # the pipe was closed under the reader (a kill)
            self.box.setdefault(name, b"")

    def too_much(self) -> bool:
        return any(len(data) > self.limit for data in self.box.values())

    def wait(self, deadline: float) -> bool:
        """True when both are read before `deadline` (time.monotonic) and none read too much."""
        while True:
            alive = [t for t in self.threads if t.is_alive()]
            if self.too_much() or not alive:
                return not self.too_much() and not alive
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            alive[0].join(min(0.2, left))

    @property
    def out(self) -> bytes:
        return self.box.get("out", b"")

    @property
    def err(self) -> bytes:
        return self.box.get("err", b"")


class _PipeWriter:
    """The board's file as SystemSshTransport.open_write gives it: write() sends into `cat` on the board."""

    def __init__(self, transport: "SystemSshTransport", process: Any, readers: _Readers):
        self.transport, self.process, self.readers = transport, process, readers

    def write(self, data: bytes) -> int:
        try:
            self.process.stdin.write(data)
        except (OSError, ValueError):   # OpenSSH or the board's cat ended: its exit status says which
            raise self.transport._write_failed(self.process, self.readers) from None
        return len(data)

    def finish(self) -> None:
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            raise self.transport._write_failed(self.process, self.readers) from None
        # what is still in OpenSSH's buffers goes out first (on a dead network keepalive ends it within ~45 s)
        status = self.transport._wait(self.process, self.readers, HASH_TIMEOUT)
        failure = self.transport._failed(self.process, self.readers, status)
        if failure is not None:
            raise failure
        if status != 0:
            raise OSError(f"the device could not write the file (exit {status})")


class SystemSshTransport(Transport):
    """The Transport over this computer's OpenSSH client, for an installation without paramiko (decision A5). There
    is no standing connection: every run() is one `ssh ... sh -c '<script>'` with openssh_options and a time limit,
    stdin a pipe of ours or nothing (never this process's stdin: over MCP stdio that is the protocol stream).
    open_write() streams a file into `cat` on the board (continued with `cat >>` after what arrived), stat_size() asks
    the board for a file's size, alive() says whether the last call reached the board (False once OpenSSH itself
    failed: the network dropped), close() ends every OpenSSH process still running (a cancel). OpenSSH checks the host
    key against the framework's known_hosts on every call (StrictHostKeyChecking=yes), so host_key is None: OpenSSH does
    not hand the key out, and a fingerprint the person was shown cannot be compared (RemoteSession refuses that)."""

    def __init__(self, endpoint: Any, user: str, state_dir: Path, *, program: str = "ssh"):
        self.endpoint, self.user, self.state_dir, self.program = endpoint, user, Path(state_dir), program
        self._options: Optional[List[str]] = None
        self._alive = False
        self._closed = False
        self._lock = threading.Lock()
        self._running: List[Any] = []

    # --- the OpenSSH processes ---

    def options(self) -> List[str]:
        if self._options is None:
            self._options = openssh_options(self.state_dir)
        return self._options

    def argv(self, script: str) -> List[str]:
        """The ssh command line of one script on the board (one `sh -c`)."""
        return openssh_argv(self.program, self.endpoint, self.user, self.options(), sh_command(script))

    def _start(self, argv: List[str], stdin: bool) -> Any:
        with self._lock:
            if self._closed:
                raise SshFailed("the session with the board was closed")
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_no_window())
        with self._lock:
            self._running.append(process)
            closed = self._closed
        if closed:
            self._kill(process)
        return process

    def _forget(self, process: Any, readers: Optional[_Readers] = None) -> None:
        """The call is over: it is no longer one a close() ends, and the pipes of an ended process are closed once
        its readers are done (a pipe a reader still reads stays open; it goes with the process object)."""
        with self._lock:
            self._running = [p for p in self._running if p is not process]
        for thread in (readers.threads if readers is not None else []):
            thread.join(2)
        if process.poll() is None or (readers is not None and any(t.is_alive() for t in readers.threads)):
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()

    @staticmethod
    def _kill(process: Any) -> None:
        try:
            process.kill()
        except Exception:  # noqa: BLE001 - it ended already
            pass

    def _wait(self, process: Any, readers: _Readers, timeout: float, deadline: Optional[float] = None) -> int:
        """The exit status of `process` once its output is read, within `timeout` seconds (or by `deadline`, a
        time.monotonic() of a call whose limit began earlier; TimeoutError after, the process ended); TooMuchOutput
        when it printed more than the readers take."""
        deadline = time.monotonic() + timeout if deadline is None else deadline
        if not readers.wait(deadline):
            self._kill(process)
            if readers.too_much():
                raise TooMuchOutput(f"the device sent more than {readers.limit} bytes")
            raise TimeoutError(f"no answer from the device within {int(timeout)} s")
        try:
            return process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            self._kill(process)
            raise TimeoutError(f"no answer from the device within {int(timeout)} s") from None

    def _feed(self, process: Any, stdin: Iterable[bytes], timeout: float, deadline: float) -> None:
        """Writes `stdin` into the pipe of `process` and closes it. A board that does not read it (the pipe stays
        full, write() waits) ends the call at its time limit (`deadline`, time.monotonic()): OpenSSH is stopped and
        TimeoutError raised, as paramiko's channel time limit ends a write."""
        expired = threading.Event()

        def stop() -> None:
            expired.set()
            self._kill(process)
        watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), stop)
        watchdog.daemon = True
        watchdog.start()
        try:
            for chunk in stdin:
                process.stdin.write(chunk)
            process.stdin.flush()
        except (OSError, ValueError):   # OpenSSH ended early (or at the time limit): its exit status says why
            pass
        finally:
            watchdog.cancel()
            with contextlib.suppress(OSError, ValueError):
                process.stdin.close()
        if expired.is_set():
            raise TimeoutError(f"no answer from the device within {int(timeout)} s")

    def _failed(self, process: Any, readers: _Readers, status: Optional[int]) -> Optional[SshFailed]:
        """SshFailed when OpenSSH itself failed or was stopped (then the session counts as dropped), else None."""
        if self._closed:
            self._alive = False
            return SshFailed("the session with the board was closed")
        if status is None or status < 0 or status == OPENSSH_FAILED:
            self._alive = False
            if status is None or status < 0:
                return SshFailed("OpenSSH was stopped before the board answered")
            return SshFailed.of(_text(readers.err), key_file=self._key_file())
        self._alive = True
        return None

    def _key_file(self) -> str:
        return key_file_of(self._options or [])

    def _write_failed(self, process: Any, readers: _Readers) -> OSError:
        """Why writing into the board's `cat` stopped: OpenSSH failed (SshFailed, the network dropped), or the board's
        `cat` ended with an error (a full disk: the connection is fine). The board's words stay out of the message."""
        try:
            status = self._wait(process, readers, 10)
        except (TimeoutError, TooMuchOutput):
            status = None
        failure = self._failed(process, readers, status)
        if failure is not None:
            return failure
        return OSError(f"the device could not write the file (exit {status})")

    # --- the interface ---

    def login(self) -> None:
        """One `true` on the board: OpenSSH connects, checks the host key against the framework's known_hosts and logs
        in with the framework key (SshFailed says why not)."""
        self.run("true")

    def run(self, script: str, *, stdin: Optional[Iterable[bytes]] = None, timeout: float = COMMAND_TIMEOUT,
            limit: int = MAX_OUTPUT) -> Result:
        """One `ssh ... sh -c '<script>'`: `stdin` is written into a pipe and closed, stdout and stderr are each read
        up to `limit` bytes. The whole call ends after `timeout` seconds (TimeoutError); more output than `limit`
        raises TooMuchOutput; a failure of OpenSSH itself (exit status 255) raises SshFailed."""
        deadline = time.monotonic() + timeout
        process = self._start(self.argv(script), stdin is not None)
        readers = None
        try:
            readers = _Readers(process, limit)
            if stdin is not None:
                self._feed(process, stdin, timeout, deadline)
            status = self._wait(process, readers, timeout, deadline)   # the limit covers the writing too
        except BaseException:
            self._kill(process)
            raise
        finally:
            self._forget(process, readers)
        failure = self._failed(process, readers, status)
        if failure is not None:
            raise failure
        return Result(status, _text(readers.out), _text(readers.err))

    def stat_size(self, path: str) -> Optional[int]:
        q = shlex.quote(path)
        result = self.run(f"if [ -f {q} ]; then wc -c < {q}; else echo none; fi")
        answer = result.out.strip()
        if result.status == 0 and answer == "none":
            return None
        if result.status == 0 and answer.isdigit():
            return int(answer)
        raise OSError(f"the device did not say the size of the file (exit {result.status})")

    @contextlib.contextmanager
    def open_write(self, path: str, offset: int = 0) -> Iterator[Any]:
        """The file at `path` on the board, written through `cat` from a pipe: created anew, or continued after its
        first `offset` bytes, which RemoteSession has just found on the board and checked (`cat >>`)."""
        q = shlex.quote(path)
        process = self._start(self.argv(f"cat > {q}" if not offset else f"cat >> {q}"), True)
        readers = None
        try:
            readers = _Readers(process, MAX_OUTPUT)
            writer = _PipeWriter(self, process, readers)
            yield writer
            writer.finish()
        except BaseException:
            self._kill(process)
            raise
        finally:
            self._forget(process, readers)

    def alive(self) -> bool:
        return self._alive and not self._closed

    @property
    def host_key(self) -> Any:
        return None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            running, self._running = self._running, []
        for process in running:
            self._kill(process)


__all__ = ["COMMAND_TIMEOUT", "HASH_TIMEOUT", "KEEPALIVE_COUNT", "KEEPALIVE_SECONDS", "LOGIN_TIMEOUT", "MAX_OUTPUT",
           "NULL_DEVICE", "OPENSSH_FAILED", "PARAMIKO_AVAILABLE", "ParamikoTransport", "Result", "SshFailed",
           "SystemSshTransport", "TooMuchOutput", "Transport", "available", "config_value", "imported_paramiko",
           "key_file_of", "open_client", "openssh_argv", "openssh_file", "openssh_options", "paramiko_module",
           "paramiko_quiet", "read_limited", "remote_location", "sh_command", "timed_out"]
