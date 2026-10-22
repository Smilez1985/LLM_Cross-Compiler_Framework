#!/usr/bin/env python3
"""
SSH trust for device probing and deployment.

- Host keys: trust on first use, but never silently. An unknown key is shown to the user with its
  SHA256 fingerprint (the format of `ssh-keygen -lf`); only a confirmed key is stored, in the framework's
  own known_hosts file. A changed key always fails.
- Authentication: a framework Ed25519 key, installed on the device once ("pairing") with a password that
  is used for that single connection and never stored.

The host-key store lives in orchestrator/remote/trust.py (read-only: fingerprint, host_entry, known_hosts_path,
check_host_key and the HostKeyError classes, re-exported here under their old names); the person's writers that trust,
pair and replace keys with their audit rows live in orchestrator/remote/pairing.py and call trust_host_key and
install_public_key through this module (tests replace them here). No module an AI can reach calls those two.

paramiko is imported on first use (orchestrator/remote/transport.py, decision RG1): importing this module, the deploy
or the probe code loads no SSH library. `ssh_trust.paramiko` (None without it: what the person steps, the CLI's
`scan --device` and Mojo ask) and `ssh_trust.TofuPolicy` stay readable under their old names; a test that sets
`ssh_trust.paramiko = None` makes this module behave as without paramiko, as before.
"""

import contextlib
import logging
import os
import re
import shlex
import socket
import tempfile
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from orchestrator.remote import transport
from orchestrator.remote.trust import (HostKeyChanged, HostKeyError, UnknownHostKey, check_host_key,  # noqa: F401
                                       fingerprint, host_entry, known_hosts_path)
from orchestrator.remote.trust import load_host_keys as _host_keys

KEY_NAME = "id_ed25519_llm_framework"
# A public key line as client_key writes it: key type, base64 key, an optional plain comment.
PUBLIC_LINE = re.compile(r"^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]{16,8192}={0,3}"
                         r"(?: [A-Za-z0-9._@-]{1,64})?$", re.ASCII)


def _paramiko():
    """paramiko for this module: imported on first use (transport.paramiko_module), or what ssh_trust.paramiko was
    set to."""
    return globals()["paramiko"] if "paramiko" in globals() else transport.paramiko_module()


def _require_paramiko():
    """The paramiko module (imported on first use); HostKeyError when it cannot be imported."""
    paramiko = _paramiko()
    if paramiko is None:
        raise HostKeyError("SSH needs the 'paramiko' package.")
    return paramiko


_tofu_class = None


def _tofu_policy():
    """The TofuPolicy class, made on first use (it subclasses paramiko's MissingHostKeyPolicy)."""
    global _tofu_class
    if _tofu_class is None:
        paramiko = _require_paramiko()

        class TofuPolicy(paramiko.MissingHostKeyPolicy):
            """Refuses unknown host keys; the caller shows the fingerprint and asks the user first."""

            def missing_host_key(self, client, hostname, key):
                host, _, port = hostname.rpartition(":") if hostname.startswith("[") else (hostname, "", "22")
                raise UnknownHostKey(host.strip("[]"), int(port or 22), key)

        _tofu_class = TofuPolicy
    return _tofu_class


def __getattr__(name):
    """The module's old attributes that need paramiko, loaded on first use: `paramiko` (None without it) and
    `TofuPolicy` (missing without it, as before)."""
    if name == "paramiko":
        return transport.paramiko_module()
    if name == "TofuPolicy":
        if _paramiko() is None:
            raise AttributeError(name)
        return _tofu_policy()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def trust_host_key(state_dir: Path, host: str, port: int, key) -> None:
    """Stores a key the user confirmed (never called without that confirmation)."""
    keys = _host_keys(state_dir)
    keys.add(host_entry(host, port), key.get_name(), key)
    save_host_keys(state_dir, keys)


def save_host_keys(state_dir: Path, keys) -> None:
    """Writes the framework's known_hosts (paramiko HostKeys) atomically, readable only by this user. Only the
    person's writers in orchestrator/remote/pairing.py call it (through trust_host_key or for a replaced key). The
    temporary file goes again when writing fails; the replace is the one of orchestrator/utils/atomic.py (tried again
    on Windows while another program holds the file open)."""
    from orchestrator.utils.atomic import replace_file
    path = known_hosts_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".known_hosts.")
    os.close(fd)
    try:
        keys.save(tmp)
        os.chmod(tmp, 0o600)
        replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_host_key(host: str, port: int = 22, timeout: float = 10.0):
    """The key the device presents, without authenticating (to show its fingerprint before trusting it)."""
    paramiko = _require_paramiko()
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
    paramiko = _require_paramiko()
    private, _ = client_key(state_dir)
    client = paramiko.SSHClient()
    path = known_hosts_path(state_dir)
    if path.exists():
        client.load_host_keys(str(path))
    client.set_missing_host_key_policy(_tofu_policy()())
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
