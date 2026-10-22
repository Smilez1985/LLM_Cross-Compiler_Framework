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
install_public_key through this module (tests replace them here). Of the modules an AI can reach, only
orchestrator/remote/session.py RemoteSession.pair calls install_public_key, and only with the person's
OneTimePassword credentials (every AI path logs in with KeyOnly); none calls trust_host_key on an AI's behalf
(DeploymentManager.trust_host, the old primitive without an audit row, has no caller).

paramiko is imported on first use (orchestrator/remote/transport.py, decision RG1): importing this module, the deploy or
the probe code loads no SSH library. The login (connect) and paramiko_quiet live in orchestrator/remote/transport.py;
the names here delegate, so every caller and every test that patches ssh_trust.connect reaches the one login.
`ssh_trust.paramiko` (None without it: what the person steps, the CLI's `scan --device` and Mojo ask) and
`ssh_trust.TofuPolicy` stay readable under their old names; a test that sets `ssh_trust.paramiko = None` makes this
module behave as without paramiko, as before.
"""

import logging
import os
import re
import shlex
import socket
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from orchestrator.remote import transport
from orchestrator.remote.trust import (HostKeyChanged, HostKeyError, UnknownHostKey, check_host_key,  # noqa: F401
                                       fingerprint, host_entry, known_hosts_path)
from orchestrator.remote.trust import load_host_keys as _host_keys

_log = logging.getLogger(__name__)

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
    """The framework's own SSH key (created on first use, private part mode 0600; on Windows, where a mode is only a
    read-only flag, an ACL for this account alone: utils/atomic.py owner_only) and its public line."""
    private = Path(state_dir) / KEY_NAME
    public = private.with_suffix(".pub")
    if not private.exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from orchestrator.utils.atomic import owner_only
        key = Ed25519PrivateKey.generate()
        private.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            owner_only(private)   # before the key is in it (the mode above is all POSIX needs)
        except OSError as e:      # the key still works over paramiko; OpenSSH says what it refuses
            _log.warning(f"The framework's SSH key could not be made private to this account ({e.strerror or e}).")
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                      serialization.NoEncryption()))
        line = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        public.write_text(line.decode("ascii") + " llm-cross-compiler-framework\n", encoding="ascii")
    return private, public.read_text(encoding="ascii").strip()


# The quiet logging while a login authenticates and the login itself moved to orchestrator/remote/transport.py
# (consolidation chain c2-remote-session); these are their old names. Callers and tests reach connect through this
# module (they patch ssh_trust.connect), so it stays a function here.
paramiko_quiet = transport.paramiko_quiet


def connect(host: str, user: str, state_dir: Path, port: int = 22, password: Optional[str] = None,
            timeout: float = 10.0):
    """An authenticated SSHClient for a device whose host key is already trusted.
    Uses the framework key; a password (pairing) is used for this connection only (transport.open_client)."""
    return transport.open_client(host, user, state_dir, port=port, password=password, timeout=timeout)


def install_public_key(client, public_line: str) -> None:
    """Adds the framework key to the device user's authorized_keys (idempotent). The command is fixed; its only value
    is the framework's own public key line (client_key), checked against PUBLIC_LINE and shell-quoted, so nothing from
    a file or a device reaches the device's shell as code."""
    if not isinstance(public_line, str) or not PUBLIC_LINE.fullmatch(public_line):
        raise HostKeyError("The framework's public key line is not a plain SSH public key; nothing was installed.")
    q = shlex.quote(public_line)
    command = (f"umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qxF {q} ~/.ssh/authorized_keys || printf '%s\\n' {q} >> ~/.ssh/authorized_keys)")
    # B601: a fixed command whose one value is the checked and quoted public key line, never outside input. It goes
    # to the board as one `sh -c '...'`, so the account's login shell (fish, csh) does not read its && and ||.
    _, stdout, stderr = client.exec_command(transport.sh_command(command))  # nosec B601
    if stdout.channel.recv_exit_status() != 0:
        raise HostKeyError(f"Could not install the framework key: {stderr.read().decode(errors='replace').strip()}")


def system_ssh_options(state_dir: Path) -> List[str]:
    """Options that make OpenSSH use the framework key and known_hosts and never ask or auto-accept, with a login
    time limit and keepalive: the one list of orchestrator/remote/transport.py openssh_options (its old name)."""
    return transport.openssh_options(state_dir)
