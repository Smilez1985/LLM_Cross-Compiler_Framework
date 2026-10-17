#!/usr/bin/env python3
"""
SSH trust for device probing and deployment.

- Host keys: trust on first use, but never silently. An unknown key is shown to the user with its
  SHA256 fingerprint (the format of `ssh-keygen -lf`); only a confirmed key is stored, in the framework's
  own known_hosts file. A changed key always fails.
- Authentication: a framework Ed25519 key, installed on the device once ("pairing") with a password that
  is used for that single connection and never stored.
"""

import base64
import contextlib
import hashlib
import logging
import os
import re
import shlex
import socket
import tempfile
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from orchestrator.remote.address import Endpoint

try:
    import paramiko
except ImportError:  # deployment and remote probing are optional features
    paramiko = None

KEY_NAME = "id_ed25519_llm_framework"
# A public key line as client_key writes it: key type, base64 key, an optional plain comment.
PUBLIC_LINE = re.compile(r"^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]{16,8192}={0,3}"
                         r"(?: [A-Za-z0-9._@-]{1,64})?$", re.ASCII)


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


def _require_paramiko():
    if paramiko is None:
        raise HostKeyError("SSH needs the 'paramiko' package.")


def fingerprint(key) -> str:
    """SHA256 fingerprint as printed by `ssh-keygen -lf`."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def host_entry(host: str, port: int = 22) -> str:
    """The host as known_hosts keeps it (Endpoint.known_hosts_entry)."""
    return Endpoint(host, port).known_hosts_entry


def known_hosts_path(state_dir: Path) -> Path:
    return Path(state_dir) / "known_hosts"


def _host_keys(state_dir: Path):
    _require_paramiko()
    keys = paramiko.HostKeys()
    path = known_hosts_path(state_dir)
    if path.exists():
        keys.load(str(path))
    return keys


def check_host_key(state_dir: Path, host: str, port: int, key) -> str:
    """'known', 'unknown' or 'changed'."""
    entries = _host_keys(state_dir).lookup(host_entry(host, port))
    if not entries:
        return "unknown"
    stored = entries.get(key.get_name())
    if stored is None:
        # A known device that now offers a different key type: treat like a change, never as new.
        return "changed"
    return "known" if stored.asbytes() == key.asbytes() else "changed"


def trust_host_key(state_dir: Path, host: str, port: int, key) -> None:
    """Stores a key the user confirmed (never called without that confirmation)."""
    keys = _host_keys(state_dir)
    keys.add(host_entry(host, port), key.get_name(), key)
    path = known_hosts_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".known_hosts.")
    os.close(fd)
    keys.save(tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def fetch_host_key(host: str, port: int = 22, timeout: float = 10.0):
    """The key the device presents, without authenticating (to show its fingerprint before trusting it)."""
    _require_paramiko()
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.banner_timeout = timeout
        transport.start_client(timeout=timeout)
        return transport.get_remote_server_key()
    finally:
        transport.close()


def client_key(state_dir: Path) -> Tuple[Path, str]:
    """The framework's own SSH key (created on first use, private part mode 0600) and its public line."""
    private = Path(state_dir) / KEY_NAME
    public = private.with_suffix(".pub")
    if not private.exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        private.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                      serialization.NoEncryption()))
        line = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        public.write_text(line.decode("ascii") + " llm-cross-compiler-framework\n", encoding="ascii")
    return private, public.read_text(encoding="ascii").strip()


if paramiko is not None:
    class TofuPolicy(paramiko.MissingHostKeyPolicy):
        """Refuses unknown host keys; the caller shows the fingerprint and asks the user first."""

        def missing_host_key(self, client, hostname, key):
            host, _, port = hostname.rpartition(":") if hostname.startswith("[") else (hostname, "", "22")
            raise UnknownHostKey(host.strip("[]"), int(port or 22), key)


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


def connect(host: str, user: str, state_dir: Path, port: int = 22, password: Optional[str] = None,
            timeout: float = 10.0):
    """An authenticated SSHClient for a device whose host key is already trusted.
    Uses the framework key; a password (pairing) is used for this connection only."""
    _require_paramiko()
    private, _ = client_key(state_dir)
    client = paramiko.SSHClient()
    path = known_hosts_path(state_dir)
    if path.exists():
        client.load_host_keys(str(path))
    client.set_missing_host_key_policy(TofuPolicy())
    try:
        with paramiko_quiet():
            client.connect(host, port=port, username=user, key_filename=str(private), password=password,
                           look_for_keys=False, allow_agent=False, timeout=timeout, banner_timeout=timeout,
                           auth_timeout=timeout)
    except paramiko.BadHostKeyException:
        client.close()
        raise HostKeyChanged(host, port) from None
    except Exception:
        client.close()
        raise
    return client


def install_public_key(client, public_line: str) -> None:
    """Adds the framework key to the device user's authorized_keys (idempotent). The command is fixed; its only value
    is the framework's own public key line (client_key), checked against PUBLIC_LINE and shell-quoted, so nothing from
    a file or a device reaches the device's shell as code."""
    if not isinstance(public_line, str) or not PUBLIC_LINE.fullmatch(public_line):
        raise HostKeyError("The framework's public key line is not a plain SSH public key; nothing was installed.")
    q = shlex.quote(public_line)
    command = (f"umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qxF {q} ~/.ssh/authorized_keys || printf '%s\\n' {q} >> ~/.ssh/authorized_keys)")
    # B601: a fixed command whose one value is the checked and quoted public key line, never outside input.
    _, stdout, stderr = client.exec_command(command)  # nosec B601
    if stdout.channel.recv_exit_status() != 0:
        raise HostKeyError(f"Could not install the framework key: {stderr.read().decode(errors='replace').strip()}")


def system_ssh_options(state_dir: Path) -> List[str]:
    """Options that make OpenSSH use the framework key and known_hosts and never ask or auto-accept."""
    private, _ = client_key(state_dir)
    return ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts_path(state_dir)}", "-o", "IdentitiesOnly=yes",
            "-i", str(private)]
