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
(DeploymentManager.trust_host, deprecated, names no writer: it delegates to pairing.trust_presented_key through the
person's PersonChannel, and nothing in the framework calls it).

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


class GateUnavailable(HostKeyError):
    """The board cannot take llmcf's command gate (`reason`, a word of gate.SETUP_REASONS): the framework's key was
    not installed, nothing of the board's authorized_keys changed."""

    def __init__(self, reason: str, offer=None):
        from orchestrator.remote.gate import SETUP_REASONS
        self.reason = reason
        self.offer = offer   # remote/key_tiers.py TierOffer: the ways the person may choose instead (None: none read)
        ways = ""
        if offer is not None and len(offer.choices) > 1:
            ways = (" You can choose another way when you pair the board again with its password: "
                    + "; ".join(line.split(". ", 1)[1] for line in offer.lines() if line[:1].isdigit()) + ".")
        super().__init__(f"This board cannot take llmcf's command gate with the root-owned key line "
                         f"({SETUP_REASONS.get(reason, reason)}), so the framework's key was not installed and nothing "
                         "on the board changed. The framework never installs a weaker key on its own: fix that on the "
                         "board and pair again, or choose another way when you are asked." + ways)


def _tier_offer(client, bundle, password: str, reason: str, mode: str):
    """The person's choices for a board whose root step failed with `reason` (remote/key_tiers.py offer), read with
    the gate's tier-facts over the pairing's login; None when nothing could be read."""
    from orchestrator.remote import gate, key_tiers
    try:
        text = gate.read_tier_facts(transport.ParamikoTransport(client).run, bundle, password)
        banner = getattr(client.get_transport(), "remote_version", "") if hasattr(client, "get_transport") else ""
    except Exception:  # noqa: BLE001 - no facts: the person reads the plain reason
        return None
    return key_tiers.offer(reason, key_tiers.TierFacts.parse(text, banner if isinstance(banner, str) else ""), mode)


def install_public_key(client, public_line: str, bundle=None, *, allow_unrestricted: bool = False,
                       password: str = "", tier: str = "", mode: str = "private") -> str:
    """Installs the framework key on the board over `client` (a session the person logged in to with a one-time
    password). With `bundle` (orchestrator/remote/gate.py GateBundle, from runtime/device/board_ops.bundle()): llmcf's
    command gate and the key's restricted line (command= the gate, no-pty, no-port-forwarding, no-agent-forwarding,
    no-X11-forwarding), the person's other lines unchanged.

    `tier` is the person's choice (remote/key_tiers.py): "" or "root" the root step (root-owned key line;
    "restricted"); "dropbear" the Dropbear tier (gate.install_dropbear_board; "dropbear"); "home" the home gate of a
    board without sudo (gate.install_gate with the signing key; "home"). The weaker two are refused in enterprise
    `mode` (GateUnavailable "enterprise"). A board whose root step fails raises GateUnavailable with the person's
    choices (its `offer`); the framework never picks a weaker tier itself. `allow_unrestricted` (deprecated: nothing in
    the framework passes it, the tier choice replaced it) still installs the plain line instead; "unrestricted".
    Without a bundle the plain line (the earlier behaviour, for a caller that gives none). The command is fixed; its
    only values are the framework's own public key line (checked against PUBLIC_LINE), the signing key and the gate
    program on stdin, so nothing from a file or a device reaches the device's shell as code."""
    if not isinstance(public_line, str) or not PUBLIC_LINE.fullmatch(public_line):
        raise HostKeyError("The framework's public key line is not a plain SSH public key; nothing was installed.")
    if bundle is not None and tier in ("dropbear", "home"):
        from orchestrator.remote import gate, key_tiers
        if not key_tiers.allowed(tier, mode):
            raise GateUnavailable("enterprise")
        run = transport.ParamikoTransport(client).run
        if tier == "dropbear":
            ok, reason = gate.install_dropbear_board(run, public_line, bundle, password)
        else:
            ok, reason = gate.install_gate(run, public_line, bundle.gate, bundle)
        if ok:
            return tier
        if reason == "gated":
            return "restricted"
        raise GateUnavailable(reason)
    if tier not in ("", "root"):
        raise HostKeyError(f"{tier!r} is not a tier the framework installs; nothing was installed.")
    if bundle is not None:
        # the root step (DECISIONS 2026-10-31): the gate, the key's restricted line and the framework's signing key in
        # root-owned places the board account cannot change, with the person's one-time `password` for sudo -S (or
        # sudo -n); a board that cannot take it gets no key (GateUnavailable says why), unless the person allowed an
        # unrestricted one
        from orchestrator.remote import gate
        ok, reason, _home = gate.install_root_board(transport.ParamikoTransport(client).run, public_line, bundle,
                                                     password)
        if ok or reason == "gated":   # "gated": the login was with the framework key, which runs only the gate
            return "restricted"
        if not allow_unrestricted:
            raise GateUnavailable(reason, _tier_offer(client, bundle, password, reason, mode)
                                  if reason not in ("no-signer", "no-signer-store", "account") else None)
    _install_plain(client, public_line)
    return "unrestricted"


def _install_plain(client, public_line: str) -> None:
    """The framework key's plain line in authorized_keys (idempotent): the earlier pairing."""
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
