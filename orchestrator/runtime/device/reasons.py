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

classify() reads an exception of socket, paramiko or orchestrator.utils.ssh_trust; classify_ssh_output() reads what
OpenSSH printed. check_port() tells host_down from port_closed with one TCP connection to the SSH port (no ICMP, which
many networks block and which needs extra rights). The codes carry no account, address or path, so they reach an AI
as they are (study 6.2 D0). The person's texts (person_text) may name the address and the port, never the account;
an AI gets the code with AI_NEXT, English and without the address.
"""

import errno
import socket
from typing import Optional

HOST_DOWN = "device_host_down"
PORT_CLOSED = "device_port_closed"
AUTH_FAILED = "device_auth_failed"
HOST_KEY_CHANGED = "device_host_key_changed"
REASONS = (HOST_DOWN, PORT_CLOSED, AUTH_FAILED, HOST_KEY_CHANGED)
CONNECT_TIMEOUT = 3.0

# The tool reason code (normalize.REASON_CODES) of each: only the person can open a port, enter a password or check
# a changed key; a board that is off may come back by itself.
TOOL_CODES = {HOST_DOWN: "hardware_unavailable", PORT_CLOSED: "needs_person", AUTH_FAILED: "needs_person",
              HOST_KEY_CHANGED: "needs_person"}

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
}

_DOWN_ERRNOS = {getattr(errno, name) for name in ("EHOSTUNREACH", "ENETUNREACH", "EHOSTDOWN", "ENETDOWN",
                                                   "ETIMEDOUT", "EADDRNOTAVAIL") if hasattr(errno, name)}
_REFUSED_ERRNOS = {getattr(errno, name) for name in ("ECONNREFUSED", "WSAECONNREFUSED") if hasattr(errno, name)}
_REFUSED_ERRNOS.add(10061)  # Windows: the connection was refused

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


def _paramiko_errors():
    try:
        import paramiko
    except ImportError:  # deployment and remote probing are optional features
        return None
    return paramiko


def classify(error: BaseException) -> Optional[str]:
    """The reason code of a failed connection or login (None: none of the four, e.g. a host key that is not trusted
    yet, which the caller handles as the first contact)."""
    from orchestrator.utils import ssh_trust
    if isinstance(error, ssh_trust.HostKeyChanged):
        return HOST_KEY_CHANGED
    if isinstance(error, ssh_trust.HostKeyError):
        return None
    paramiko = _paramiko_errors()
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


def classify_ssh_output(text: str) -> Optional[str]:
    """The reason code of an OpenSSH failure from what it printed (None when it says none of the four; a host key
    that is not in the framework's known_hosts yet is the first contact, not a changed key)."""
    for needle, code in _SSH_OUTPUT:
        if needle in (text or ""):
            return code
    return None


def check_port(host: str, port: int = 22, timeout: float = CONNECT_TIMEOUT) -> Optional[str]:
    """One TCP connection to the board's SSH port: None when it opens, HOST_DOWN when nothing answers (or the name
    does not resolve), PORT_CLOSED when the board refuses it."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return None
    except OSError as e:
        return classify(e) or HOST_DOWN


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


__all__ = ["AI_NEXT", "AUTH_FAILED", "CONNECT_TIMEOUT", "HOST_DOWN", "HOST_KEY_CHANGED", "PORT_CLOSED", "REASONS",
           "TOOL_CODES", "check_port", "classify", "classify_ssh_output", "person_next", "person_text",
           "person_title"]
