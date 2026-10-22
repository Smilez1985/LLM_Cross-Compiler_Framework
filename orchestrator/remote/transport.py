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

A failure of the network (OSError, EOFError, socket.timeout) leaves run() as it is: the caller decides whether it is a
drop to wait through (alive() is false) or the end. Only more output than the cap is the transport's own failure,
TooMuchOutput. The OpenSSH fallback becomes a second Transport in consolidation chain c2-remote-system.
"""

import contextlib
import logging
import shlex
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Tuple

from orchestrator.remote.errors import RemoteFailure

_lock = threading.Lock()
_loaded: Optional[Tuple[Any, str]] = None   # (the module or None, why not) once it was looked up

COMMAND_TIMEOUT = 120          # seconds a command on the board may stay silent
HASH_TIMEOUT = 900             # ... while it hashes a large file (the board prints nothing until it is done)
MAX_OUTPUT = 1024 * 1024       # bytes of a command's stdout (and of its stderr) the framework reads at most
LOGIN_TIMEOUT = 10.0
KEEPALIVE_SECONDS = 15         # an idle session sends a keepalive this often, so a dead network shows up


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


def available() -> Tuple[bool, str]:
    """(True, "") when paramiko imports here, else (False, why not)."""
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


# --- paramiko -----------------------------------------------------------------------------------------------------------

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


__all__ = ["COMMAND_TIMEOUT", "HASH_TIMEOUT", "KEEPALIVE_SECONDS", "LOGIN_TIMEOUT", "MAX_OUTPUT", "PARAMIKO_AVAILABLE",
           "ParamikoTransport", "Result", "TooMuchOutput", "Transport", "available", "imported_paramiko", "open_client",
           "paramiko_module", "paramiko_quiet", "read_limited", "sh_command"]
