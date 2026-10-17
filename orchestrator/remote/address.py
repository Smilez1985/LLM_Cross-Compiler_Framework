#!/usr/bin/env python3
"""
A board's address and SSH account: the one host check, the one port check, the one account check and the host:port
forms (moved here from orchestrator/runtime/remote_probe.py, the deploy, the trusted-board records, the deploy settings,
the launcher, orchestrator/utils/ssh_trust.py, orchestrator/runtime/device/devices.py and the Boards window, which each
had a copy).

- valid_host(host): an address or a host name as the person entered it (eighth round N4); host_shaped(host) is only
  its shape (what a stored trusted-board record is checked against, so a record the person trusted once is never
  dropped by a stricter rule later).
- valid_port(port): a TCP port, 1 to 65535 (an int, not a bool).
- valid_user(user, allow_root=False): an account name on the board, ASCII only: with re.IGNORECASE alone, Python's
  case folding lets a long s (U+017F) and the Kelvin sign (U+212A) pass as 's' and 'k'.
- Endpoint(host, port): display ("host:port", the framework's messages), known_hosts_entry (OpenSSH's form: [host]:port
  for another port than 22, so IPv6 at port 22 stays without brackets), store_form (how the profile store and the
  trusted-board records keep it: host, host:port or [IPv6]:port) and Endpoint.parse(stored form).
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_PORT = 22
# A board's address or host name (192.168.1.50, myboard.local, an IPv6 address); the person enters what they know.
# An IPv6 address is written without brackets: the port is a field of its own.
HOST_PATTERN = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]{2,45})$", re.ASCII)
# An account name on a board: no option, no shell character, at most 32 characters.
USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.ASCII | re.IGNORECASE)
_PORT_TEXT = re.compile(r"^\d{1,5}$", re.ASCII)


def host_shaped(host: Any) -> bool:
    """Whether `host` has the shape of an address or a host name (HOST_PATTERN, never a leading '-' that ssh would read
    as an option). valid_host checks more."""
    return isinstance(host, str) and bool(HOST_PATTERN.fullmatch(host)) and not host.startswith("-")


def valid_host(host: Any) -> bool:
    """A board's address or host name as the person entered it (eighth round N4): an IPv4 or IPv6 address, or a host
    name such as myboard.local; never an option (a leading '-' would read as one) or a shell character. Digits and
    dots alone must be a valid IPv4 address (192.168.1.300 is a typo, not a name), and a name has no empty part. An
    IPv6 address is written without brackets (fe80::1, never [fe80::1]): the port is a field of its own, and the deploy
    refuses brackets too, so a CLI or GUI check never passes an address the deploy then refuses."""
    if not host_shaped(host):
        return False
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
        return True
    if re.fullmatch(r"[0-9.]+", host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return False
        return True
    return ".." not in host


def valid_port(port: Any) -> bool:
    """A TCP port: an int (not a bool) from 1 to 65535."""
    return isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535


def valid_user(user: Any, *, allow_root: bool = False) -> bool:
    """An account name on a board (USER_PATTERN, the whole text, ASCII only); root only with `allow_root` (the device
    runtime never runs as root, a deploy and a trusted board may use it)."""
    if not isinstance(user, str) or not USER_PATTERN.fullmatch(user):
        return False
    return allow_root or user != "root"


@dataclass(frozen=True)
class Endpoint:
    """A board's SSH address: its host (an address or a host name, IPv6 without brackets) and port."""
    host: str
    port: int = DEFAULT_PORT

    @property
    def display(self) -> str:
        """How the framework's messages name it: host:port."""
        return f"{self.host}:{self.port}"

    @property
    def known_hosts_entry(self) -> str:
        """The host as OpenSSH's known_hosts keeps it: the host at port 22, else [host]:port."""
        return self.host if self.port == DEFAULT_PORT else f"[{self.host}]:{self.port}"

    @property
    def store_form(self) -> str:
        """The address as the profile store and the trusted-board records keep it: host, host:port, or [IPv6]:port
        (brackets someone typed around an IPv6 address are not doubled)."""
        if self.port == DEFAULT_PORT:
            return self.host
        return f"[{self.host.strip('[]')}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

    @classmethod
    def parse(cls, address: Any) -> "Endpoint":
        """The endpoint of a stored address (store_form: host, host:port or [IPv6]:port). ValueError when there is
        none or it is not host[:port] with a port from 1 to 65535; the host itself is taken as stored."""
        if not address or not isinstance(address, str):
            raise ValueError("there is no stored address")
        if address.startswith("["):
            host, _, rest = address[1:].partition("]")
            port = rest[1:] if rest.startswith(":") else str(DEFAULT_PORT)
        elif address.count(":") == 1:
            host, _, port = address.partition(":")
        else:
            host, port = address, str(DEFAULT_PORT)
        if not _PORT_TEXT.fullmatch(port) or not 1 <= int(port) <= 65535 or not host:
            raise ValueError(f"the stored address {address[:64]!r} is not host[:port]")
        return cls(host, int(port))


__all__ = ["DEFAULT_PORT", "Endpoint", "HOST_PATTERN", "USER_PATTERN", "host_shaped", "valid_host", "valid_port",
           "valid_user"]
