#!/usr/bin/env python3
"""
The framework's trust in a board's SSH host key, read-only (consolidation stage C, c2-remote-trust).

The framework keeps its own known_hosts file in the per-user folder: it maps a board's address (a host name or an IP
address with its port) to the host key the person confirmed. This module reads it and compares keys; it never writes.
Trusting a key, pairing a board and replacing a board's key or address are the person's (orchestrator/remote/
pairing.py), and no module an AI can reach imports that one.

- fingerprint(key): the SHA256 fingerprint as `ssh-keygen -lf` prints it; device_id_for(fingerprint): the board's
  device ID (the first 16 hex characters of the sha256 of its fingerprint: the same after an address change, it names
  no host); host_entry, known_hosts_path, load_host_keys and check_host_key: the store itself.
- TrustStore(state_dir): status(endpoint, key), status_of(record), keys_at(endpoint), read_presented_key(endpoint)
  and check(endpoint, key=None, expect_fingerprint=None, expect_device_id=None) -> KeyCheck. Every caller keeps only
  its own policy on top of a KeyCheck: the person may trust an unknown key, an AI is refused, and D0 holds (what an
  AI is told names the board by its label, never by its address).
- parse_public_key(text): a host key the person pasted (one line of the board's /etc/ssh/ssh_host_*_key.pub, a
  known_hosts line or a line of ssh-keyscan) as a key object, or KeyTextError; nobody types a key (Q5).
- HostKeyError, UnknownHostKey, HostKeyChanged: the host-key failures; the message is meant for the person.

orchestrator/utils/ssh_trust.py re-exports all of this under its old names. The two network and file primitives that
tests replace there (fetch_host_key, check_host_key) are looked up through that module when a TrustStore calls them,
so a test that patches them sees every caller (fingerprint too, for a KeyCheck). paramiko is imported on first use
only (remote/transport.py).
"""

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from orchestrator.remote.address import Endpoint

# A host key fingerprint as fingerprint() prints it (the device ID derives from it).
FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$", re.ASCII)
# The host key types a board's SSH server offers (paramiko reads each of them).
KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa")
# One pasted key line: an optional host field (known_hosts, ssh-keyscan), the key type, the base64 key, a comment.
_KEY_LINE = re.compile(r"(?:(?P<hosts>[^\s@#][^\s]{0,1023})\s+)?(?P<type>ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521)"
                       r"|ssh-rsa)\s+(?P<blob>[A-Za-z0-9+/]{16,16384}={0,3})(?:\s+[^\r\n]{0,256})?", re.ASCII)
MAX_KEY_TEXT = 32 * 1024


class HostKeyError(Exception):
    """Base class for host key problems; the message is meant for the user."""


class UnknownHostKey(HostKeyError):
    def __init__(self, host: str, port: int, key):
        self.host, self.port, self.key = host, port, key
        self.key_type = key.get_name()
        self.fingerprint = fingerprint(key)
        super().__init__(f"The device {Endpoint(host, port).display} is not known yet ({self.key_type} "
                         f"{self.fingerprint}).")


class HostKeyChanged(HostKeyError):
    def __init__(self, host: str, port: int):
        super().__init__(
            f"The host key of {Endpoint(host, port).display} has CHANGED. This can be a reinstalled device - or "
            f"someone in between. Check the device, then remove its line from the framework's known_hosts file to "
            f"trust it again.")


class KeyTextError(ValueError):
    """A pasted host key that cannot be used; `kind`: empty, several, not_a_key or no_paramiko."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


def _require_paramiko():
    """paramiko for the store (HostKeyError without it); honours a test's `ssh_trust.paramiko = None`."""
    from orchestrator.utils import ssh_trust
    return ssh_trust._require_paramiko()


def fingerprint(key) -> str:
    """SHA256 fingerprint as printed by `ssh-keygen -lf`."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def device_id_for(fingerprint_text: str) -> str:
    """Device ID: the first 16 hex characters of the sha256 of the board's host key fingerprint ("SHA256:..." as
    fingerprint() prints it). Stable across address changes, no host name."""
    if not isinstance(fingerprint_text, str) or not FINGERPRINT.fullmatch(fingerprint_text):
        raise ValueError("not an SSH host key fingerprint (SHA256:<43 base64 characters>)")
    return hashlib.sha256(fingerprint_text.encode("ascii")).hexdigest()[:16]


def host_entry(host: str, port: int = 22) -> str:
    """The host as known_hosts keeps it (Endpoint.known_hosts_entry)."""
    return Endpoint(host, port).known_hosts_entry


def known_hosts_path(state_dir: Path) -> Path:
    return Path(state_dir) / "known_hosts"


def load_host_keys(state_dir: Path):
    """The framework's known_hosts as paramiko HostKeys (empty when there is none yet)."""
    paramiko = _require_paramiko()
    keys = paramiko.HostKeys()
    path = known_hosts_path(state_dir)
    if path.exists():
        keys.load(str(path))
    return keys


def check_host_key(state_dir: Path, host: str, port: int, key) -> str:
    """'known', 'unknown' or 'changed'."""
    entries = load_host_keys(state_dir).lookup(host_entry(host, port))
    if not entries:
        return "unknown"
    stored = entries.get(key.get_name())
    if stored is None:
        # A known device that now offers a different key type: treat like a change, never as new.
        return "changed"
    return "known" if stored.asbytes() == key.asbytes() else "changed"


@dataclass(frozen=True)
class KeyCheck:
    """What the board at `endpoint` presents (`key`) against the framework's known_hosts: `status` known, unknown or
    changed. `expected` is the fingerprint the caller was shown (or the record holds), `expected_device` the device ID
    the caller means (None when it means none; a device ID that was given is always compared, so an empty one never
    matches, as the person's board steps compared it before)."""
    endpoint: Endpoint
    key: Any
    status: str
    fingerprint: str
    expected: str = ""
    expected_device: Optional[str] = None

    @classmethod
    def of(cls, endpoint: Endpoint, key: Any, status: str, *, expect_fingerprint: Optional[str] = None,
           expect_device_id: Optional[str] = None) -> "KeyCheck":
        from orchestrator.utils import ssh_trust   # looked up now: tests replace fingerprint there
        return cls(endpoint=endpoint, key=key, status=status, fingerprint=ssh_trust.fingerprint(key),
                   expected=expect_fingerprint or "",
                   expected_device=None if expect_device_id is None else str(expect_device_id))

    @property
    def key_type(self) -> str:
        return self.key.get_name()

    @property
    def device_id(self) -> str:
        return device_id_for(self.fingerprint)

    @property
    def known(self) -> bool:
        return self.status == "known"

    @property
    def fingerprint_differs(self) -> bool:
        """The board presents another key than the one the caller expected."""
        return bool(self.expected) and self.fingerprint != self.expected

    @property
    def other_device(self) -> bool:
        """The key belongs to another device than the one the caller means (always when it means "" by mistake)."""
        return self.expected_device is not None and self.device_id != self.expected_device


class TrustStore:
    """The framework's known_hosts in the per-user folder `state_dir`, read-only."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)

    @property
    def path(self) -> Path:
        return known_hosts_path(self.state_dir)

    def status(self, endpoint: Endpoint, key: Any) -> str:
        """'known', 'unknown' or 'changed': `key` against what known_hosts holds for `endpoint` (no network)."""
        from orchestrator.utils import ssh_trust   # looked up now: tests replace check_host_key there
        return ssh_trust.check_host_key(self.state_dir, endpoint.host, endpoint.port, key)

    def status_of(self, record: Any) -> str:
        """For a trusted board's record (host, port, key_type, fingerprint): 'known' while known_hosts holds exactly
        its key for its address, 'changed' when it holds another key there, 'unknown' when it holds none (or the file
        cannot be read: nothing is known then). Reads no network."""
        try:
            entries = load_host_keys(self.state_dir).lookup(host_entry(record.host, record.port))
        except Exception:  # noqa: BLE001 - no paramiko or a damaged known_hosts: nothing is known
            return "unknown"
        if not entries:
            return "unknown"
        stored = entries.get(record.key_type)
        if stored is None:
            return "changed"
        return "known" if fingerprint(stored) == record.fingerprint else "changed"

    def keys_at(self, endpoint: Endpoint) -> Dict[str, Any]:
        """The keys known_hosts holds for `endpoint`, by key type ({} when none or it cannot be read)."""
        try:
            entries = load_host_keys(self.state_dir).lookup(host_entry(endpoint.host, endpoint.port))
        except Exception:  # noqa: BLE001 - no paramiko or a damaged known_hosts
            return {}
        return dict(entries.items()) if entries else {}

    def read_presented_key(self, endpoint: Endpoint) -> Any:
        """The key the board presents now: one connection to its SSH port, no login. Network: off the GUI thread."""
        from orchestrator.utils import ssh_trust   # looked up now: tests replace fetch_host_key there
        return ssh_trust.fetch_host_key(endpoint.host, endpoint.port)

    def check(self, endpoint: Endpoint, key: Any = None, *, expect_fingerprint: Optional[str] = None,
              expect_device_id: Optional[str] = None) -> KeyCheck:
        """The key the board presents (read now when `key` is None) against known_hosts, and against the fingerprint
        and the device ID the caller expects. Raises what reading the key or the file raised (the caller gives it its
        SSH reason code)."""
        if key is None:
            key = self.read_presented_key(endpoint)
        return KeyCheck.of(endpoint, key, self.status(endpoint, key), expect_fingerprint=expect_fingerprint,
                           expect_device_id=expect_device_id)


def parse_public_key(text: str) -> Any:
    """The host key in a text the person pasted: exactly one key line (blank lines and # comments aside) of a public key
    file, known_hosts or ssh-keyscan, of one of KEY_TYPES, whose key data says the same type. KeyTextError otherwise."""
    text = text if isinstance(text, str) else ""
    if len(text) > MAX_KEY_TEXT:
        raise KeyTextError("The pasted text is too long for a host key.", "not_a_key")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines:
        raise KeyTextError("No host key was pasted.", "empty")
    if len(lines) > 1:
        raise KeyTextError("The pasted text holds more than one line; paste the one key line.", "several")
    found = _KEY_LINE.fullmatch(lines[0])
    if found is None:
        raise KeyTextError("The pasted text is not an SSH host key line (for example the line of the board's "
                           "/etc/ssh/ssh_host_ed25519_key.pub).", "not_a_key")
    from orchestrator.remote import transport
    paramiko = transport.paramiko_module()
    if paramiko is None:
        raise KeyTextError("Reading a host key needs the 'paramiko' package.", "no_paramiko")
    try:
        key = paramiko.PKey.from_type_string(found["type"], base64.b64decode(found["blob"], validate=True))
    except (binascii.Error, Exception) as e:  # noqa: BLE001 - bad base64, or whatever the key parser raises
        raise KeyTextError(f"The pasted host key cannot be read ({e.__class__.__name__}).", "not_a_key") from None
    if key.get_name() != found["type"]:
        raise KeyTextError("The pasted key data is of another type than its line says.", "not_a_key")
    return key


__all__ = ["FINGERPRINT", "HostKeyChanged", "HostKeyError", "KEY_TYPES", "KeyCheck", "KeyTextError", "TrustStore",
           "UnknownHostKey", "check_host_key", "device_id_for", "fingerprint", "host_entry", "known_hosts_path",
           "load_host_keys", "parse_public_key"]
