#!/usr/bin/env python3
"""
Why a board cannot be reached over SSH (eighth round, N6): four reason codes, each with the next step for the person
in their language and a plain code for AIs.

- device_host_down: nothing answers at the address. The board is off or on another network, or the host name (for
  example myboard.local) does not resolve. A firewall that drops the connection looks the same.
- device_port_closed: the board answers, but its SSH port refuses the connection (no SSH server running, another
  port, a firewall that rejects it), or something other than an SSH server answers there.
- device_auth_failed: the SSH server refused the login: a wrong user name or password, or the framework's key is no
  longer on the board.
- device_host_key_changed: the board presents another host key than the one the person trusted.

And one code that is not about the board (LOCAL_PROBLEM, local_problem): something on this computer stopped the contact.
One of the framework's own files or folders (its SSH key, known_hosts) could not be read or written (no space left,
no permission, a read-only disk), or this computer does not allow the connection (a PermissionError). The board is
not to blame, so it is never recorded with the board for Mojo (REASONS are the board's four codes; CODES all five).

classify() reads an exception of socket, paramiko or orchestrator.utils.ssh_trust; classify_ssh_output() reads what
OpenSSH printed. reach_code() is the code every caller records and shows for a failed contact: classify's, local_problem
for an error about this computer (is_local), device_host_down for any other OSError (the board does not answer), "" when
it is none of these. check_port() tells host_down from port_closed with one TCP connection to the SSH port (no ICMP,
which many networks block and which needs extra rights). The codes carry no account, address or path, so they reach an
AI as they are (study 6.2 D0). The person's texts (person_text) may name the address and the port, never the account; an
AI gets the code with AI_NEXT, English and without the address.

This module moved here from orchestrator/runtime/device/reasons.py (consolidation chain c2-remote-base); that name
is this module itself, so every old import and every test that patches it reaches the same functions.
"""

import errno
import socket
from typing import Optional

HOST_DOWN = "device_host_down"
PORT_CLOSED = "device_port_closed"
AUTH_FAILED = "device_auth_failed"
HOST_KEY_CHANGED = "device_host_key_changed"
REASONS = (HOST_DOWN, PORT_CLOSED, AUTH_FAILED, HOST_KEY_CHANGED)   # why the board cannot be reached (Mojo keeps it)
LOCAL_PROBLEM = "local_problem"   # something on this computer, not the board (never recorded with a board)
CODES = REASONS + (LOCAL_PROBLEM,)  # every code reach_code gives, each with the person's text and the AI's next step
# The next step of a local problem in the catalogue (orchestrator/utils/next_steps): Mojo's check of the installation
# looks at this computer's disks and the state folder. The board's four codes take theirs from their blocker
# (tools/facts_catalog.py); local_problem is no blocker of the delegation rules, it names no fact about the call.
LOCAL_STEPS = ("mojo.oracle",)
CONNECT_TIMEOUT = 3.0

# The tool reason code (normalize.REASON_CODES) of each: only the person can open a port, enter a password or check
# a changed key; a board that is off may come back by itself.
TOOL_CODES = {HOST_DOWN: "hardware_unavailable", PORT_CLOSED: "needs_person", AUTH_FAILED: "needs_person",
              HOST_KEY_CHANGED: "needs_person", LOCAL_PROBLEM: "needs_person"}

# The next step an AI caller gets: no address, no account, no path.
AI_NEXT = {
    HOST_DOWN: "Ask the person to check that the board is switched on and on the same network as this computer; if "
               "they entered a host name such as myboard.local, the IP address may work instead.",
    PORT_CLOSED: "Ask the person to check that the board's SSH server runs, that the SSH port is right and that no "
                 "firewall blocks it.",
    AUTH_FAILED: "Ask the person to check the user name and password and to pair the board again in the GUI or the "
                 "CLI (only the person enters a password).",
    HOST_KEY_CHANGED: "Tell the person: the board's host key changed (a reinstalled board, or another device in its "
                      "place). They must check the board and trust it again in the GUI or the CLI; no tool can "
                      "accept a changed key.",
    LOCAL_PROBLEM: "Tell the person: something on this computer stopped the contact, not the board. One of the "
                   "framework's own files could not be read or written (no space left, no permission or a read-only "
                   "disk), or this computer does not allow the connection. They free disk space or fix the "
                   "framework's state folder, then try again; Mojo's check of the installation shows what is wrong.",
}

_DOWN_ERRNOS = {getattr(errno, name) for name in ("EHOSTUNREACH", "ENETUNREACH", "EHOSTDOWN", "ENETDOWN",
                                                   "ETIMEDOUT", "EADDRNOTAVAIL") if hasattr(errno, name)}
_REFUSED_ERRNOS = {getattr(errno, name) for name in ("ECONNREFUSED", "WSAECONNREFUSED") if hasattr(errno, name)}
_REFUSED_ERRNOS.add(10061)  # Windows: the connection was refused

# Errors about this computer, not the board (is_local): a full or read-only disk, no permission, too many open files,
# or a path of this computer that is no folder or no file where one was expected.
_LOCAL_ERRNOS = {getattr(errno, name) for name in ("ENOSPC", "EDQUOT", "EROFS", "EACCES", "EPERM", "EMFILE",
                                                    "ENFILE", "EISDIR", "ENOTDIR", "ENAMETOOLONG", "ELOOP")
                 if hasattr(errno, name)}

# What a board does while it restarts its SSH server: the TCP connection opens and drops again (a reset, an abort, a
# broken pipe, or EOF during paramiko's key exchange). None of the four by itself; while a deploy waits for the board
# (reach_code with while_waiting) it means the board is not back yet.
DROPPED = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, EOFError)

# What OpenSSH prints for each (its messages are English whatever the locale).
_SSH_OUTPUT = (
    ("REMOTE HOST IDENTIFICATION HAS CHANGED", HOST_KEY_CHANGED),
    ("Connection refused", PORT_CLOSED),
    ("Permission denied", AUTH_FAILED),
    ("Too many authentication failures", AUTH_FAILED),
    ("Could not resolve hostname", HOST_DOWN),
    ("Name or service not known", HOST_DOWN),
    ("No route to host", HOST_DOWN),
    ("Network is unreachable", HOST_DOWN),
    ("Host is down", HOST_DOWN),
    ("Connection timed out", HOST_DOWN),
    ("Operation timed out", HOST_DOWN),
)


def classify(error: BaseException) -> Optional[str]:
    """The reason code of a failed connection or login (None: none of the four, e.g. a host key that is not trusted
    yet, which the caller handles as the first contact)."""
    from orchestrator.remote import trust
    if isinstance(error, trust.HostKeyChanged):
        return HOST_KEY_CHANGED
    if isinstance(error, trust.HostKeyError):
        return None
    # paramiko only when something imported it already: none of its errors exists before that, so classify never
    # loads it (RG1) and works the same without it (transport.imported_paramiko, which also waits for an import
    # another thread started and has not finished).
    from orchestrator.remote.transport import imported_paramiko
    paramiko = imported_paramiko()
    if paramiko is not None:
        if isinstance(error, paramiko.BadHostKeyException):
            return HOST_KEY_CHANGED
        if isinstance(error, paramiko.AuthenticationException):
            return AUTH_FAILED
        valid = getattr(paramiko.ssh_exception, "NoValidConnectionsError", None)
        if valid is not None and isinstance(error, valid):
            inner = [classify(e) for e in (getattr(error, "errors", None) or {}).values()]
            return PORT_CLOSED if inner and all(code == PORT_CLOSED for code in inner) else HOST_DOWN
        if isinstance(error, paramiko.SSHException):
            text = str(error).lower()
            # something answers on the port, but it does not speak SSH (a web server, another service)
            if "banner" in text or "protocol" in text:
                return PORT_CLOSED
            if "authentication" in text:
                return AUTH_FAILED
            return None
    if isinstance(error, socket.gaierror):
        return HOST_DOWN  # the host name does not resolve (a .local name in a network without mDNS)
    if isinstance(error, ConnectionRefusedError) or getattr(error, "errno", None) in _REFUSED_ERRNOS:
        return PORT_CLOSED
    if isinstance(error, (socket.timeout, TimeoutError)) or getattr(error, "errno", None) in _DOWN_ERRNOS:
        return HOST_DOWN
    return None


def is_local(error: BaseException) -> bool:
    """Whether a failed contact is about this computer, not the board: an OSError about one of its files (it names
    one: the framework's SSH key, known_hosts, the state folder), a full or read-only disk, or no permission (also
    this computer refusing the connection itself, a PermissionError). A socket error about the board names no file."""
    if not isinstance(error, OSError) or isinstance(error, (socket.gaierror, socket.timeout, TimeoutError)):
        return False
    return isinstance(error, PermissionError) or getattr(error, "filename", None) is not None or \
        getattr(error, "errno", None) in _LOCAL_ERRNOS


def _own_accounts() -> set:
    """The names this computer's account goes by (its home folder's name, USER, USERNAME), never shown to an AI."""
    import os
    names = {os.environ.get("USER", ""), os.environ.get("USERNAME", "")}
    try:
        names.add(os.path.basename(os.path.expanduser("~").replace("\\", "/").rstrip("/")))
    except Exception:  # noqa: BLE001 - no home folder known: the environment's names only
        pass
    return {n for n in names if n and n != "~"}


def local_detail(error: BaseException) -> str:
    """A local problem in a few plain words for a reason text: what the system said and the file's name, never its
    folder ("No space left on device: known_hosts"); the log has the whole path. A home folder itself, or a folder
    with this computer's account name, is named by its kind: its name is an account's name, and the reason reaches an
    AI (D0)."""
    what = getattr(error, "strerror", None) or error.__class__.__name__
    name = getattr(error, "filename", None)
    if name is None:
        return str(what)
    parent, _, base = str(name).replace("\\", "/").rstrip("/").rpartition("/")
    if base and parent.rpartition("/")[2].lower() in ("home", "users"):   # /home/<account>, C:\Users\<account>
        base = "a home folder"
    elif base and base in _own_accounts():
        base = "a folder named after the account"
    return f"{what}: {base}" if base else str(what)


def reach_code(error: BaseException, *, while_waiting: bool = False) -> str:
    """The SSH reason code of a failed connection or login as every caller records and shows it: classify's code,
    local_problem for an error about this computer (is_local: a full disk, no permission, the framework's own files),
    or device_host_down for any other OSError (the board does not answer); "" when it is none of these (for example a
    host key that is not trusted yet). `while_waiting`: the deploy waits for a board that dropped off the network
    (N5); a connection that opens and drops again (DROPPED: its SSH server is still starting) means it is not back
    yet, so the wait goes on."""
    code = classify(error)
    if code is None and is_local(error):
        code = LOCAL_PROBLEM
    if code is None and (isinstance(error, OSError) or (while_waiting and isinstance(error, DROPPED))):
        code = HOST_DOWN
    return code or ""


def classify_ssh_output(text: str) -> Optional[str]:
    """The reason code of an OpenSSH failure from what it printed (None when it says none of the four; a host key
    that is not in the framework's known_hosts yet is the first contact, not a changed key)."""
    for needle, code in _SSH_OUTPUT:
        if needle in (text or ""):
            return code
    return None


def check_port(host: str, port: int = 22, timeout: float = CONNECT_TIMEOUT) -> Optional[str]:
    """One TCP connection to the board's SSH port: None when it opens, HOST_DOWN when nothing answers (or the name
    does not resolve), PORT_CLOSED when the board refuses it, LOCAL_PROBLEM when this computer does not allow it."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return None
    except OSError as e:
        return reach_code(e) or HOST_DOWN


def person_title(code: str, lang: Optional[str] = None, *, address: str = "", port: int = 22) -> str:
    """What happened, for the person (names the address and the port, never the account)."""
    from orchestrator.utils.localization import ui_text
    return ui_text(f"device.reason.{code}", lang, address=address or "?", port=int(port))


def person_next(code: str, lang: Optional[str] = None, *, address: str = "", port: int = 22) -> str:
    """What the person can do about it."""
    from orchestrator.utils.localization import ui_text
    return ui_text(f"device.next.{code}", lang, address=address or "?", port=int(port))


def person_text(code: str, lang: Optional[str] = None, *, address: str = "", port: int = 22) -> str:
    """Title and next steps in one text for a message box or the console."""
    return f"{person_title(code, lang, address=address, port=port)} " \
           f"{person_next(code, lang, address=address, port=port)}"


__all__ = ["AI_NEXT", "AUTH_FAILED", "CODES", "CONNECT_TIMEOUT", "DROPPED", "HOST_DOWN", "HOST_KEY_CHANGED",
           "LOCAL_PROBLEM", "LOCAL_STEPS", "PORT_CLOSED", "REASONS", "TOOL_CODES", "check_port", "classify",
           "classify_ssh_output",
           "is_local", "local_detail", "person_next", "person_text", "person_title", "reach_code"]
