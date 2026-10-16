#!/usr/bin/env python3
"""
The per-user bridge from AI apps to the open framework window (W4 of 2026-10-16).

An MCP client runs the framework's MCP server in its own process (stdio, or the loopback HTTP server). While the
person has the framework's window open, that server forwards the GUI tool's calls over this bridge to the GUI
process, where the same code that serves Ditto reads and fills the forms (orchestrator/tools/tools_gui.py). The
caller's own process keeps the tool registry's part: the person's permissions for that caller, the limits, the
questions (also pending approvals) and the audit entry.

Who can reach it: only this person.

- POSIX: an AF_UNIX socket `gui.sock` in the private folder `<per-user folder>/gui_bridge` (mode 0700, owned by this
  account; the socket itself 0600). When that path is too long for a socket, the socket lives in a new 0700 folder in
  the temporary folder instead. On Linux the GUI also checks the user id of every process that connects
  (SO_PEERCRED) and closes the connection of any other account at once.
- Windows: a named pipe with a random name (`\\\\.\\pipe\\llmcf-gui-<32 hex>`, new for every start). Its default
  security lets only this account (and SYSTEM and the administrators) open it for writing, and the first instance is
  created exclusively, so nobody can take the name over while the window runs.
- A random key (32 bytes, new for every start) is stored with the address in `bridge.json` (mode 0600) in the
  private folder. Before anything else is sent, both sides prove they know it (the documented challenge of
  multiprocessing.connection, in both directions), so a client never talks to an endpoint someone else put there.
- Messages are JSON (never pickle), one request per connection, at most MAX_MESSAGE bytes. The key, the address and
  the private folder never appear in a message, an error, the log or the audit log.

The boundary is the person's account: the window serves a framework process of the same account that proves the key,
and that process's registry has already decided (the person's permissions, the question) and audited the call. A
program of the same account could read the key file, but it could also change that account's files and settings
anyway; every other account is kept out.

The window that opens first serves AI apps; another window of the same person opened while it runs does not start
a second bridge. When the window closes, the endpoint and `bridge.json` are removed; a process that finds no live
window gets GuiNotOpen, which the GUI tool turns into the blocker gui_not_open ("open the framework window").
"""

import json
import os
import secrets
import socket
import stat
import struct
import tempfile
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import AuthenticationError, Client, Listener, answer_challenge, deliver_challenge
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from orchestrator.utils.logging import get_logger

BRIDGE_FOLDER = "gui_bridge"
INFO_FILE = "bridge.json"
LOCK_FILE = "bridge.lock"
SOCKET_NAME = "gui.sock"
PIPE_PREFIX = "\\\\.\\pipe\\llmcf-gui-"
TEMP_PREFIX = "llmcf-gui-"
SOCKET_PATH_MAX = 100        # bytes of an AF_UNIX path (Linux allows 107, macOS 103)
MAX_MESSAGE = 1024 * 1024    # one request or answer
MAX_INFO = 4096              # bytes of bridge.json
KEY_BYTES = 32
HANDSHAKE_SECONDS = 5.0      # to prove the key, both ways
REQUEST_SECONDS = 10.0       # for the request after the handshake
ANSWER_SECONDS = 60.0        # for the window's answer (each step on the GUI thread has its own 15 s limit)
PING_SECONDS = 2.0
MAX_CONNECTIONS = 8          # calls served at the same time
PROTOCOL = 1

logger = get_logger("GuiBridge")


class GuiNotOpen(Exception):
    """No framework window of this person answers (none is open, or it closed)."""


class BridgeError(Exception):
    """The bridge could not serve a call (a time limit, a broken connection, an answer that is no answer). The text
    names no address, key or folder."""


@dataclass(frozen=True)
class BridgeInfo:
    """What `bridge.json` says: where the window listens and the key both sides prove."""
    family: str      # "AF_UNIX" or "AF_PIPE"
    address: str
    key: bytes
    pid: int
    instance: str

    def to_json(self) -> str:
        return json.dumps({"protocol": PROTOCOL, "family": self.family, "address": self.address,
                           "key": self.key.hex(), "pid": self.pid, "instance": self.instance})


def windows_host(windows: Optional[bool] = None) -> bool:
    return os.name == "nt" if windows is None else bool(windows)


def bridge_folder(state_dir: Path) -> Path:
    """The private folder of the bridge (<per-user folder>/gui_bridge): created with mode 0700; a link, a file of that
    name or a folder of another account is refused (BridgeError). The framework keeps its own runtime folder private
    (0700) on POSIX."""
    folder = Path(state_dir) / BRIDGE_FOLDER
    Path(state_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = os.lstat(folder)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BridgeError("the bridge's private folder is not a folder")
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            raise BridgeError("the bridge's private folder belongs to another account")
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(folder, 0o700)
    return folder


def new_endpoint(folder: Path, windows: Optional[bool] = None) -> Tuple[str, str]:
    """(family, address) for a new start: a named pipe with a random name on Windows; on POSIX the socket in the
    private folder, or in a new 0700 folder in the temporary folder when that path is too long for a socket."""
    if windows_host(windows):
        return "AF_PIPE", PIPE_PREFIX + secrets.token_hex(16)
    path = Path(folder) / SOCKET_NAME
    if len(os.fsencode(str(path))) > SOCKET_PATH_MAX:
        path = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)) / SOCKET_NAME
    return "AF_UNIX", str(path)


def _private_enough(path: Path) -> bool:
    """Whether a file of the bridge is this account's and nobody else's (POSIX; Windows keeps the per-user folder
    private by its own permissions)."""
    if os.name != "posix":
        return True
    info = os.lstat(path)
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and not stat.S_IMODE(info.st_mode) & 0o077)


def read_info(state_dir: Path) -> Optional[BridgeInfo]:
    """What bridge.json says, or None when there is none, or when it cannot be used (damaged, or readable by other
    accounts: a key others could read is never used)."""
    path = Path(state_dir) / BRIDGE_FOLDER / INFO_FILE
    try:
        if not _private_enough(path):
            logger.warning("The GUI bridge's key file is readable by other accounts; it is not used. Close and "
                           "reopen the framework window to make a new one.")
            return None
        with open(path, "rb") as f:
            raw = f.read(MAX_INFO + 1)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        logger.warning(f"The GUI bridge's key file cannot be read ({type(e).__name__}).")
        return None
    try:
        if len(raw) > MAX_INFO:
            raise ValueError("too large")
        data = json.loads(raw.decode("utf-8"))
        family, address, key = data["family"], data["address"], bytes.fromhex(data["key"])
        if (data.get("protocol") != PROTOCOL or family not in ("AF_UNIX", "AF_PIPE") or not isinstance(address, str)
                or not address or len(address) > 400 or len(key) != KEY_BYTES):
            raise ValueError("unexpected content")
        if family == "AF_PIPE" and not address.startswith(PIPE_PREFIX):
            raise ValueError("not the framework's pipe")
        return BridgeInfo(family, address, key, int(data.get("pid", 0)), str(data.get("instance", ""))[:32])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("The GUI bridge's key file is damaged; it is not used.")
        return None


# --- the window's side ------------------------------------------------------------------------------------------------

def _peer_uid(conn: Any) -> Optional[int]:
    """The user id of the process at the other end of an AF_UNIX connection (Linux: SO_PEERCRED); None where the
    system does not tell."""
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None or os.name != "posix":
        return None
    peer = socket.socket(fileno=os.dup(conn.fileno()))
    try:
        _pid, uid, _gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i")))
        return uid
    finally:
        peer.close()


def _abort(conn: Any) -> None:
    """Ends a connection another thread may be blocked reading (a peer that never finishes the handshake)."""
    try:
        if os.name == "posix":
            peer = socket.socket(fileno=os.dup(conn.fileno()))
            try:
                peer.shutdown(socket.SHUT_RDWR)
            finally:
                peer.close()
        else:
            conn.close()
    except (OSError, ValueError):
        pass


class BridgeServer:
    """The window's end of the bridge. `handler(request)` gets every request (a dict, already JSON) after the key was
    proved both ways and returns the answer (a dict that is JSON). It runs in a connection thread; GUI work goes
    through the surface's invoker."""

    def __init__(self, state_dir: Path, handler: Callable[[Dict[str, Any]], Dict[str, Any]], *,
                 windows: Optional[bool] = None):
        self.state_dir = Path(state_dir)
        self.handler = handler
        self.windows = windows
        self.note = ""                    # why the bridge did not start (for the log; no path, no key)
        self._listener: Optional[Listener] = None
        self._info: Optional[BridgeInfo] = None
        self._temp_folder: Optional[Path] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        # The account this window runs as: a process of any other account is refused (POSIX).
        self._owner_uid = os.geteuid() if hasattr(os, "geteuid") else None

    @property
    def running(self) -> bool:
        return self._listener is not None and not self._stop.is_set()

    def start(self) -> bool:
        """Starts listening and writes bridge.json; False (with `note`) when another window of this person already
        serves AI apps or the bridge cannot start here. Never raises."""
        from orchestrator.utils.file_lock import file_lock
        try:
            folder = bridge_folder(self.state_dir)
            with file_lock(folder / LOCK_FILE, timeout=10.0):
                existing = read_info(self.state_dir)
                if existing is not None and _alive(existing):
                    self.note = "another framework window of this person already serves AI apps"
                    return False
                if existing is not None:
                    _remove_stale_endpoint(existing, folder)
                family, address = new_endpoint(folder, self.windows)
                if family == "AF_UNIX":
                    if Path(address).parent != folder:
                        self._temp_folder = Path(address).parent
                    if not _clear_socket_path(Path(address)):
                        self.note = "the place of the bridge's socket holds something else"
                        self._remove_temp_folder()
                        return False
                listener = Listener(address, family=family, backlog=MAX_CONNECTIONS)
                if family == "AF_UNIX":
                    os.chmod(address, 0o600)
                info = BridgeInfo(family, address, secrets.token_bytes(KEY_BYTES), os.getpid(), secrets.token_hex(8))
                from orchestrator.Core.vault_key import write_private
                try:
                    write_private(folder / INFO_FILE, info.to_json())
                except BaseException:
                    listener.close()
                    raise
                self._listener, self._info = listener, info
        except Exception as e:  # noqa: BLE001 - the window works without the bridge; AI apps are told it is closed
            self.note = f"the bridge could not start ({type(e).__name__})"
            logger.warning(f"The GUI bridge for AI apps did not start: {self.note}.")
            self._remove_temp_folder()
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="gui-bridge", daemon=True)
        self._thread.start()
        logger.info("AI apps can reach this framework window (GUI bridge started).")
        return True

    def stop(self) -> None:
        """Stops listening and removes the endpoint and bridge.json (when it is still this window's)."""
        listener, info = self._listener, self._info
        if listener is None or info is None:
            return
        self._stop.set()
        try:   # wake the accept() of the serving thread (closing alone does not on every system)
            Client(info.address, family=info.family).close()
        except (OSError, EOFError):
            pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        try:
            listener.close()   # an AF_UNIX listener removes its socket file
        except OSError:
            pass
        self._listener = None
        folder = self.state_dir / BRIDGE_FOLDER
        try:
            from orchestrator.utils.file_lock import file_lock
            with file_lock(folder / LOCK_FILE, timeout=10.0):
                now = read_info(self.state_dir)
                if now is not None and now.instance == info.instance:
                    os.unlink(folder / INFO_FILE)
        except (OSError, TimeoutError) as e:
            logger.warning(f"The GUI bridge's key file was not removed ({type(e).__name__}).")
        self._remove_temp_folder()
        self._info = None
        logger.info("The GUI bridge for AI apps stopped.")

    def _remove_temp_folder(self) -> None:
        folder, self._temp_folder = self._temp_folder, None
        if folder is None:
            return
        try:
            leftover = folder / SOCKET_NAME
            if leftover.is_socket():
                leftover.unlink()
            folder.rmdir()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn = self._listener.accept()
            except (OSError, EOFError):
                if self._stop.is_set():
                    break
                time.sleep(0.2)
                continue
            except Exception:  # noqa: BLE001 - the listener was closed under it
                break
            info = self._info
            if self._stop.is_set() or info is None:
                conn.close()
                break
            if not self._slots.acquire(blocking=False):
                conn.close()   # too many calls at once: the caller is told the window did not answer
                continue
            key = info.key   # this start's key, also when the window stops while the call runs
            threading.Thread(target=self._handle, args=(conn, key), name="gui-bridge-call", daemon=True).start()

    def _handle(self, conn: Any, key: bytes) -> None:
        try:
            uid = _peer_uid(conn)
            if uid is not None and self._owner_uid is not None and uid != self._owner_uid:
                logger.warning("A process of another account tried to reach the GUI bridge; it was refused.")
                return
            timer = threading.Timer(HANDSHAKE_SECONDS, _abort, (conn,))
            timer.daemon = True
            timer.start()
            try:
                deliver_challenge(conn, key)
                answer_challenge(conn, key)
            finally:
                timer.cancel()
            if not conn.poll(REQUEST_SECONDS):
                return
            request = json.loads(conn.recv_bytes(MAX_MESSAGE).decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("a request is an object")
            if request.get("op") == "ping":
                answer: Dict[str, Any] = {"ok": True, "pong": True}
            else:
                answer = self.handler(request)
            conn.send_bytes(json.dumps(answer, ensure_ascii=False).encode("utf-8"))
        except AuthenticationError:
            logger.warning("A process without the GUI bridge's key tried to reach it; it was refused.")
        except (EOFError, OSError, ValueError, UnicodeDecodeError) as e:
            logger.info(f"A GUI bridge call ended early ({type(e).__name__}).")
        except Exception as e:  # noqa: BLE001 - a broken handler never takes the bridge down
            logger.error(f"A GUI bridge call failed ({type(e).__name__}).")
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._slots.release()


def _clear_socket_path(path: Path) -> bool:
    """Makes room for the socket: a stale socket of this account there is removed (its window is gone); anything else
    (a file, a link, another account's socket) is left alone and the bridge does not start."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid():
        os.unlink(path)
        return True
    return False


def _remove_stale_endpoint(info: BridgeInfo, folder: Path) -> None:
    """The endpoint of a window that ended without stopping its bridge (a crash): its socket (in the private folder, or
    in its own folder in the temporary folder) is removed. A named pipe ends with its process."""
    if info.family != "AF_UNIX":
        return
    path = Path(info.address)
    in_temp = (path.parent.parent == Path(tempfile.gettempdir()) and path.parent.name.startswith(TEMP_PREFIX)
               and path.name == SOCKET_NAME)
    if path.parent != folder and not in_temp:
        return   # not a place the bridge uses: never touched
    try:
        _clear_socket_path(path)
        if in_temp and os.lstat(path.parent).st_uid == os.geteuid():
            path.parent.rmdir()
    except OSError:
        pass


# --- the caller's side --------------------------------------------------------------------------------------------------

def _connect(info: BridgeInfo, handshake: float) -> Any:
    try:
        conn = Client(info.address, family=info.family)
    except (OSError, EOFError) as e:   # no socket or pipe, nobody listening, or not this account's
        raise GuiNotOpen(f"no framework window answers ({type(e).__name__})") from None
    try:
        if not conn.poll(handshake):
            raise GuiNotOpen("the framework window did not answer")
        answer_challenge(conn, info.key)
        deliver_challenge(conn, info.key)
    except AuthenticationError:
        conn.close()
        logger.warning("The endpoint of the GUI bridge did not prove the key; it is not the framework's window.")
        raise GuiNotOpen("the endpoint is not the framework's window") from None
    except (OSError, EOFError) as e:
        conn.close()
        raise GuiNotOpen(f"the framework window closed the connection ({type(e).__name__})") from None
    except GuiNotOpen:
        conn.close()
        raise
    return conn


def _alive(info: BridgeInfo) -> bool:
    try:
        return bool(_exchange(info, {"op": "ping"}, PING_SECONDS).get("pong"))
    except (GuiNotOpen, BridgeError):
        return False


def _exchange(info: BridgeInfo, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    conn = _connect(info, min(HANDSHAKE_SECONDS, timeout))
    try:
        data = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_MESSAGE:
            raise BridgeError("the call is too large for the window's bridge")
        conn.send_bytes(data)
        if not conn.poll(timeout):
            raise BridgeError("the framework window did not answer in time")
        answer = json.loads(conn.recv_bytes(MAX_MESSAGE).decode("utf-8"))
    except (EOFError, OSError) as e:
        raise BridgeError(f"the connection to the framework window broke ({type(e).__name__})") from None
    except (ValueError, UnicodeDecodeError):
        raise BridgeError("the framework window sent no readable answer") from None
    finally:
        conn.close()
    if not isinstance(answer, dict):
        raise BridgeError("the framework window sent no readable answer")
    return answer


def call(state_dir: Path, request: Dict[str, Any], timeout: float = ANSWER_SECONDS) -> Dict[str, Any]:
    """Sends one request to this person's open framework window and returns its answer. GuiNotOpen when no window
    answers; BridgeError when the call broke off or timed out (the window may have done part of it)."""
    info = read_info(Path(state_dir))
    if info is None:
        raise GuiNotOpen("no framework window is open")
    return _exchange(info, request, timeout)


def gui_open(state_dir: Path) -> bool:
    """Whether a framework window of this person answers now."""
    info = read_info(Path(state_dir))
    return info is not None and _alive(info)


__all__ = ["ANSWER_SECONDS", "BRIDGE_FOLDER", "BridgeError", "BridgeInfo", "BridgeServer", "GuiNotOpen", "INFO_FILE",
           "MAX_MESSAGE", "PIPE_PREFIX", "SOCKET_NAME", "bridge_folder", "call", "gui_open", "new_endpoint",
           "read_info", "windows_host"]
