#!/usr/bin/env python3
"""
The person's hardware scans (W1' of 2026-10-16: every action of the CLI also by mouse): the one code path behind
`llmcf scan` (this computer, `--device` over SSH, `--import FILE`) and the GUI's Builder tab > Scan / Import... menu.
Both are the person's channels; no tool and no AI reaches this module (it logs in to a board with a password once and
writes the trusted record of a board).

Every step returns facts or raises: ProbeError (orchestrator/runtime/probe_service.py) for a probe that cannot run,
ProfileWriteError for a profile file that cannot be written, DeviceScanError for a board that cannot be logged in to,
probed or stored. Nothing here prints, asks or exits: the host-key question, the password and the label stay with
the caller (a terminal prompt, a GUI dialog with "No" as the default). A board's own words are untrusted: they reach
the caller as data (the profile went through the probe schema; notes are the framework's own sentences).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from orchestrator.remote.errors import RemoteFailure
from orchestrator.remote.reasons import reach_code  # noqa: F401 - the SSH reason code, also under this module's name
from orchestrator.utils.next_steps import WithSteps

LABEL_HINT = "1 to 64 printable characters"
# The refusal of an SSH user that is not an account name (the CLI's words; the GUI shows deploy.bad_user).
USER_HINT = ("The SSH user must be an account name on the board (letters, digits, '.', '_' or '-'; it starts with a "
             "letter or '_').")


class ProfileWriteError(OSError):
    """The profile file (`llmcf scan --output`) could not be written; `error` is the OSError behind it."""

    def __init__(self, path: Path, error: OSError):
        super().__init__(error.errno, error.strerror or str(error))
        self.path, self.error = Path(path), error

    def __str__(self) -> str:  # the CLI prints the reason as it did before (str of the OSError)
        return str(self.error)


def label_problem(label: Optional[str]) -> bool:
    """Whether a person's name for a board is refused (`--label` of `scan --device` and `scan --import`): it must have
    1 to 64 printable characters once its spaces are collapsed. None (no label given) is fine."""
    return label is not None and not (1 <= len(" ".join(label.split())) <= 64 and label.isprintable())


def write_profile_file(profile: Any, output_file: Path, as_json: bool = False) -> None:
    """Writes a probe's profile file (KEY=VALUE lines, or the JSON view) readable only by this user."""
    from orchestrator.utils.atomic import write_private
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_private(output_file, profile.to_json() + "\n" if as_json else profile.to_text())


# --- this computer (`llmcf scan [--script] [--output FILE] [--json]`) ---------------------------------------------

@dataclass
class LocalScan:
    """What a scan of this computer found and where it went: `how` is "Python probe" or "fallback script", `notes`
    what it had to fall back from, `output_file` the profile file, `local_path` this computer's stored probe (probe
    id local)."""
    profile: Any
    how: str
    notes: List[str]
    output_file: Path
    local_path: Path

    @property
    def needs_reprobe(self) -> bool:
        return bool(getattr(self.profile, "needs_reprobe", False))


def scan_local(probes: Any, output_file: Path, *, as_json: bool = False, use_script: bool = False) -> LocalScan:
    """Probes this computer (HardwareProbes.run_local: the Python probe, or the fallback script with `use_script` or
    when the Python probe fails), stores it as this computer's probe (id local) and writes the profile file: the
    fallback script writes it itself, the Python probe's result (and with `as_json` the JSON view) is written here.
    Raises ProbeError when the probe fails, ProfileWriteError when the file cannot be written."""
    output_file = Path(output_file)
    profile, how = probes.run_local(use_script=use_script, output=output_file)
    if how != "fallback script" or as_json:
        try:
            write_profile_file(profile, output_file, as_json)
        except OSError as e:
            raise ProfileWriteError(output_file, e) from None
    return LocalScan(profile=profile, how=how, notes=list(probes.notes), output_file=output_file,
                     local_path=probes.store.local_path())


# --- a board's probe file (`llmcf scan --import FILE [--label]`) ------------------------------------------------

@dataclass
class ImportedProbe:
    """A board's probe file in the probe store: its id (what AI clients and Ditto name it by), the profile, its list
    entry (label) and the target that fits it (None when none does, or the targets could not be read)."""
    probe_id: str
    profile: Any
    info: Any
    match: Any = None


def import_probe(probes: Any, path: Path, label: Optional[str] = None, target_manager: Any = None) -> ImportedProbe:
    """Copies a probe file the person chose, validated and without its host name, into the probe store
    (HardwareProbes.import_file) and matches it against the targets. ValueError for a label that is refused
    (label_problem), ProbeError when the file is not a probe file the framework can read."""
    from orchestrator.runtime.probe_service import match_target
    if label_problem(label):
        raise ValueError(f"A board's label has {LABEL_HINT}.")
    probe_id = probes.import_file(path, label)
    profile = probes.get(probe_id)
    info = probes.info(probe_id, profile)
    return ImportedProbe(probe_id=probe_id, profile=profile, info=info, match=match_target(profile, target_manager))


# --- a board over SSH (`llmcf scan --device HOST --user U [--port] [--password] [--label]`) ---------------------------

class DeviceScanError(WithSteps, RemoteFailure):
    """A board scan that could not be done. `kind`: user (the SSH user is not an account name: refused before any
    contact, as on every other board path), login (the board could not be reached or logged in to; `reason` is its SSH
    reason code when it has one), probe (the probe on the board failed), cancelled (the caller's Cancel ended it) or
    save (the profile could not be stored or written). `error` is the exception behind it."""

    def __init__(self, message: str, kind: str, reason: str = "", error: Optional[BaseException] = None):
        super().__init__(message, kind, reason, error)


@dataclass
class DeviceScan:
    """What a board scan did: the board's device id (from its host key), its profile, the probe that ran there
    ("python" or "sh") and why it fell back, the stored profile file, the profile file written for the person (None
    when none was asked for), and the trusted, paired record of the board (None when it was not written)."""
    device_id: str
    profile: Any
    tool: str
    notes: List[str] = field(default_factory=list)
    stored: Optional[Path] = None
    output_file: Optional[Path] = None
    trusted: Any = None


def _note_reach(state_dir: Path, host: str, port: int, code: str) -> None:
    """Mojo's record of this contact with the board at host:port (W1); never fails the scan (trusted.record_reach)."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime.device.trusted import record_reach
    record_reach(state_dir, "probe", code, endpoint=Endpoint(host, port))


def scan_device(state_dir: Path, host: str, port: int, user: str, key: Any = None, *, password: Optional[str] = None,
                label: Optional[str] = None, output_file: Optional[Path] = None, as_json: bool = False,
                store: Any = None, record: bool = True, via: str = "cli", actor: str = "", audit: Any = None,
                audit_factory: Optional[Callable[[], Any]] = None,
                on_client: Optional[Callable[[Any], None]] = None, cancel: Any = None,
                on_event: Optional[Callable[[Any], None]] = None, signer: Any = None, chooser: Any = None,
                mode: str = "private") -> DeviceScan:
    """Probes a board whose host key the person trusted (the caller asked; `key` is that key, or None to take the key
    of the session itself): logs in (a password only the first time, to install the framework's SSH key; never
    stored), runs the hardware probe there (the Python probe with python3 -I, else the shell probe; nothing is
    written on the board), stores the profile under the board's device id with its label and address, keeps the SSH
    user as the board's deploy user (never root) and, with `record`, writes the board's trusted, paired record (and
    audits it as the person's decision through `via`). A failed login or a reached board is recorded for Mojo (W1).
    `audit_factory` (instead of `audit`) opens the audit log only when that record is written, so a scan that fails
    never touches it (the CLI's audit log may create the OS keyring's audit key on first use). `on_client` gets the
    SSH session so a Cancel can close it. While the board's network is gone the probe's session waits for it (N5):
    `on_event` gets its events (remote/text.py wait_lines makes the person's lines of them), and `cancel`, a
    session.CancelToken, ends the wait and the probe at once (it holds the session); a scan it ended raises
    DeviceScanError "cancelled". `signer`: the caller's framework signer for the pairing (runtime/device/board_ops.py
    resolve_signer: a BoardSigner, or `lambda: board_ops.framework_signer(framework)`, looked up only when a password
    pairs the board); None only for a caller without any framework (then the state folder's store, and the pairing
    says plainly when that cannot give the signing key). `chooser` (remote/pairing.py TierChooser, the person's
    channel) and `mode` (Core/config_manager.py framework_mode): a board that cannot take the root-owned key line
    offers the person the switch to OpenSSH, the weaker tiers (private mode only) or password login; after the switch
    the board is recorded at its new sshd's port and host key."""
    from orchestrator.remote.address import Endpoint, valid_user
    from orchestrator.remote.session import KeyOnly, OneTimePassword, RemoteSession, SessionFailure, Stopped
    from orchestrator.runtime.device.settings import remember_ssh_user
    from orchestrator.runtime.device.trusted import record_trusted_device
    from orchestrator.runtime.profile import ProfileStore, device_id_for_host_key
    from orchestrator.runtime.remote_probe import RemoteProbeError, probe_over_ssh, store_address
    from orchestrator.utils import ssh_trust
    if not valid_user(user, allow_root=True):   # the account check of every board path (remote/address.py)
        raise DeviceScanError(USER_HINT, "user")
    state_dir = Path(state_dir)
    store = store if store is not None else ProfileStore(state_dir)
    try:
        # the login checks the host key against the framework's known_hosts (TofuPolicy refuses an unknown one)
        client = ssh_trust.connect(host, user, state_dir, port=port, password=password)
    except Exception as e:  # authentication, network, a host key that changed meanwhile
        code = reach_code(e)
        if code:
            _note_reach(state_dir, host, port, code)
        raise DeviceScanError(f"Could not log in to {user}@{host}:{port}: {e}", "login", reason=code,
                              error=e) from None
    _note_reach(state_dir, host, port, "")
    if on_client is not None:
        on_client(client)
    # the one board session takes the login over for the probe (orchestrator/remote/session.py): when the board's
    # network drops, it waits for the board and logs in again with the host key checked as at the first login, the
    # one-time password for this session's logins only (N5)
    session = RemoteSession(Endpoint(host, port), user, state_dir,
                            credentials=OneTimePassword(password) if password else KeyOnly(), cancel=cancel,
                            on_event=on_event)
    paired = None
    try:
        if cancel is not None:
            cancel.hold(session)   # a Cancel closes the session: a running probe and a wait for the board end
        if session.cancel.cancelled:
            raise Stopped()
        if key is None:
            key = client.get_transport().get_remote_server_key()   # the key of the session itself
        session.adopt(client, key)
        if password:
            from orchestrator.remote import pairing
            from orchestrator.runtime.device import board_ops
            # the framework's key, over the one-time password session, with llmcf's command gate (board_ops.py)
            from orchestrator.runtime.profile import stored_access
            # the board's stored probe (an earlier scan) proposes a tier; the board's facts read now decide
            access = stored_access(state_dir, device_id_for_host_key(ssh_trust.fingerprint(key)))
            paired = pairing.pair(client, state_dir, board_ops.pairing_bundle(signer, state_dir), password=password,
                                  chooser=chooser, mode=mode, audit=audit if audit is not None else audit_factory,
                                  via=via, host=host, port=port, user=user, access=access)
        result = probe_over_ssh(session)
    except (RemoteProbeError, ssh_trust.HostKeyError) as e:
        if session.cancel.cancelled:   # the Cancel closed the session under the running probe
            raise DeviceScanError(f"The scan of {host} was cancelled.", "cancelled", error=e) from None
        raise DeviceScanError(f"The probe on {host} failed: {e}", "probe", error=e) from None
    except (SessionFailure, Stopped) as e:   # the board did not answer again after a drop, or a cancel
        if session.cancel.cancelled or isinstance(e, Stopped):
            raise DeviceScanError(f"The scan of {host} was cancelled.", "cancelled", error=e) from None
        if getattr(e, "reason", ""):
            _note_reach(state_dir, host, port, e.reason)
        raise DeviceScanError(f"The probe on {host} failed: {e}", "probe", reason=getattr(e, "reason", ""),
                              error=e) from None
    finally:
        if cancel is not None:
            cancel.release(session)
        if session.client is not None:
            session.close()   # the client of its current login
        else:
            client.close()
    switched = getattr(paired, "endpoint", None) is not None and getattr(paired, "key", None) is not None
    old_id = device_id_for_host_key(ssh_trust.fingerprint(key))
    if switched:
        # the switch to OpenSSH: the board is reached at its new sshd from now on (its own port and host key)
        port, key = paired.endpoint.port, paired.key
    device_id = device_id_for_host_key(ssh_trust.fingerprint(key))
    trusted = None
    if switched and record:
        # the board's earlier data (a probe, deploy settings, a runtime) moves to its new device ID, its record follows
        from orchestrator.runtime.device.person_steps import BoardStepError, adopt_switched_board
        if audit is None and audit_factory is not None:
            audit = audit_factory()
        try:
            trusted = adopt_switched_board(state_dir, old_id, host, user, paired, via=via, audit=audit,
                                           actor=actor or via, label=label).device
        except BoardStepError as e:
            raise DeviceScanError(str(e), "save", error=e) from None
    try:
        if output_file is not None:
            write_profile_file(result.profile, Path(output_file), as_json)
        stored = store.save_device(device_id, result.profile, label=label, address=store_address(host, port))
    except (OSError, ValueError) as e:
        raise DeviceScanError(f"Could not save the profile: {e}", "save", error=e) from None
    remember_ssh_user(store, device_id, user)  # the deploy user for `device runtime deploy` (never root)
    if record and not switched:
        if audit is None and audit_factory is not None:
            audit = audit_factory()
        trusted = record_trusted_device(state_dir, host, port, user, key, via=via,
                                        paired=str(paired or "") != "password", label=label, audit=audit,
                                        actor=actor or via)
    if paired is not None:
        from orchestrator.runtime.device.trusted import record_key_tier
        record_key_tier(state_dir, device_id, getattr(paired, "tier", ""))
    return DeviceScan(device_id=device_id, profile=result.profile, tool=result.tool, notes=list(result.notes),
                      stored=Path(stored) if stored is not None else None,
                      output_file=Path(output_file) if output_file is not None else None, trusted=trusted)


__all__ = ["DeviceScan", "DeviceScanError", "ImportedProbe", "LABEL_HINT", "LocalScan", "ProfileWriteError", "USER_HINT",
           "import_probe", "label_problem", "reach_code", "scan_device", "scan_local", "write_profile_file"]
