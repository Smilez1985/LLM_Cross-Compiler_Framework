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
class PairingImport:
    """What the import did with a probe kit's pairing block: `state` none (the file has none: pairing was off or an
    older probe), paired (complete_pairing pinned the board's host keys: `done` is its PairingDone) or refused (the
    board skipped or failed the pairing, or the nonce matches no open offer, has expired or was used: `kind` is
    PairingRefused's kind). `message` is the person's sentence (the hardware data was imported in every case)."""
    state: str = "none"
    message: str = ""
    kind: str = ""
    done: Any = None


@dataclass
class ImportedProbe:
    """A board's probe file in the probe store: its id (what AI clients and Ditto name it by), the profile, its list
    entry (label), the target that fits it (None when none does, or the targets could not be read) and what became of
    the probe kit's pairing block (PairingImport)."""
    probe_id: str
    profile: Any
    info: Any
    match: Any = None
    pairing: PairingImport = field(default_factory=PairingImport)


def import_probe(probes: Any, path: Path, label: Optional[str] = None, target_manager: Any = None, *,
                 via: str = "cli", audit: Any = None, actor: str = "") -> ImportedProbe:
    """Copies a probe file the person chose, validated and without its host name, into the probe store and matches it
    against the targets. A probe kit's pairing block (probe 3.4.0) is handed to remote/pairing.py complete_pairing
    (the one end of both pairing ways) through the person's channel `via` (cli or gui) and `audit` (the log or a
    function that opens it): when its nonce matches an open offer, the board's host keys are pinned (no TOFU question
    at its first login) and the probe is stored under the board's device ID; otherwise the pairing is refused with a
    plain reason and the hardware data is imported as usual. ValueError for a label that is refused (label_problem),
    ProbeError when the file is not a probe file the framework can read."""
    from orchestrator.remote import pairing
    from orchestrator.runtime.probe_service import match_target
    if label_problem(label):
        raise ValueError(f"A board's label has {LABEL_HINT}.")
    parsed = probes.parse_text(probes.read_file(path))
    result = pairing.PairingResult.from_profile(parsed)
    outcome, device_id = PairingImport(), None
    if result is not None:
        try:
            done = pairing.complete_pairing(probes.store.state_dir, result, via=via, audit=audit, actor=actor,
                                            probe_id=result.device_id, label=label or parsed.get("BOARD_MODEL") or "")
        except pairing.PairingRefused as e:
            outcome = PairingImport("refused", str(e), e.kind)
        else:
            device_id = done.device_id
            outcome = PairingImport("paired", f"Paired: the board's host key is pinned (device {done.device_id}, key "
                                              f"tier {done.tier}); its first login needs no host-key question.",
                                    done=done)
    probe_id = probes.import_text(parsed, label, device_id=device_id)
    profile = probes.get(probe_id)
    info = probes.info(probe_id, profile)
    return ImportedProbe(probe_id=probe_id, profile=profile, info=info, match=match_target(profile, target_manager),
                         pairing=outcome)


# --- the probe kit the person carries to a board (`llmcf scan --kit FOLDER [--no-pair]`) -------------------------

KIT_PAIR_DIR = "llmcf-pairing"
KIT_README = "README.txt"


@dataclass
class ProbeKit:
    """A probe kit written for a board: its folder, the files in it (relative names), the pairing offer it carries
    (None: pairing was switched off or could not be offered) and, when it could not be offered, why (`problem`, the
    person's sentence)."""
    folder: Path
    files: List[str]
    offer: Any = None
    problem: str = ""

    @property
    def pairing(self) -> bool:
        return self.offer is not None


def _kit_readme(pairing_on: bool, expires_at: str = "") -> str:
    lines = [
        "LLM Cross-Compiler Framework - hardware probe kit",
        "",
        "Copy this folder to the board (a USB stick, a card, or a local network share) and run ONE of the probes",
        "there, from this folder:",
        "",
        "  Python 3.8 or newer:  sudo python3 -I hardware_probe.py",
        "  any POSIX shell:      sudo sh hardware_probe.sh",
        "  Windows:              powershell -ExecutionPolicy Bypass -File hardware_probe.ps1",
        "",
        "The probe writes target_hardware_config.txt. Carry that file back and import it in the framework",
        "(Builder tab > Scan / Import... > Import from File, or the CLI's scan --import).",
        "",
    ]
    if pairing_on:
        lines += [
            "Pairing is ON for this kit: run with sudo, the probe also puts the framework's own key line and its",
            "signing key on the board (root-owned, as the framework's pairing over a password login does) for the",
            "account that called sudo, and writes the board's SSH host key fingerprints with this kit's one-time",
            "number into the result file. Importing that file pins the board's host key: its first login asks no",
            f"host-key question. The kit pairs one board once and is valid until {expires_at} (UTC).",
            "Without sudo the probe says that pairing was skipped; the hardware data is complete either way.",
            "The folder llmcf-pairing holds only public data: the framework's public key, its signer file and the",
            "one-time number. No password, no private key. Pairing a Windows board is not there yet.",
        ]
    else:
        lines += ["Pairing is OFF for this kit: the probe only reads the hardware (sudo is not needed)."]
    return "\n".join(lines) + "\n"


# The names a probe kit writes outside its own folder llmcf-pairing, and the words every one of them (the framework's
# probes and the kit's README) carries in its first lines: such a file is an earlier kit's and may be written again.
KIT_TOP_FILES = ("hardware_probe.py", "hardware_probe.sh", "hardware_probe.ps1", KIT_README)
_KIT_MARK = b"llm cross-compiler framework"


def _foreign_files(folder: Path) -> List[str]:
    """The files in `folder` the kit would replace that are not an earlier kit's (sorted names; [] for a new or empty
    folder): a person's own README.txt on a USB stick is never written over."""
    found = []
    for name in KIT_TOP_FILES:
        path = folder / name
        if not path.exists() and not path.is_symlink():
            continue
        try:
            with open(path, "rb") as f:
                head = f.read(1024)
        except OSError:
            head = b""
        if path.is_symlink() or not path.is_file() or _KIT_MARK not in head.lower():
            found.append(name)
    return sorted(found)


def export_probe_kit(state_dir: Path, folder: Path, *, pair: bool = True, signer: Any = None, via: str,
                     audit: Any = None, actor: str = "", scripts_dir: Optional[Path] = None) -> ProbeKit:
    """Writes the probe kit the person carries to a board into `folder`: the three probes (hardware_probe.py from the
    package, hardware_probe.sh and .ps1 from the scripts folder when the installation has it) and a README; with
    `pair` (the default: pairing during the hardware probe is on unless the person switches it off) also the folder
    llmcf-pairing: a new pairing offer's nonce (remote/pairing.py create_offer, only through the person's channel
    `via`), the framework's public key line, its signer file and PEM twin, and the root step the probe runs with sudo
    (gate.probe_kit_root_step: the very text the pairing over a password login installs, in its as-root form) with the
    gate program it installs. `signer`: the caller's framework signer (board_ops.resolve_signer); without a signing
    key the kit is written without pairing and `problem` says why. Files a shell reads are written with LF line ends.
    OSError when the folder cannot be written; FileExistsError, before anything is written or offered, when the folder
    holds a file of the person's own under a name the kit writes (_foreign_files: only an earlier kit's are replaced)."""
    from orchestrator.remote import gate, pairing
    from orchestrator.runtime.device import board_ops
    from orchestrator.runtime.probe_service import SCRIPTS_DIR
    from orchestrator.utils import ssh_trust
    if via not in pairing.PERSON_CHANNELS:
        raise pairing.PairingRefused("Only you can make a probe kit, in the CLI or the GUI; an AI cannot.", "channel")
    folder = Path(folder)
    scripts = Path(scripts_dir) if scripts_dir is not None else SCRIPTS_DIR
    foreign = _foreign_files(folder)
    if foreign:   # a person's own files are never replaced (nothing written, no offer made)
        raise FileExistsError(f"The folder already holds files of yours the probe kit would replace: "
                              f"{', '.join(foreign)}. Choose an empty folder (or the folder of an earlier probe kit); "
                              "nothing was written.")
    folder.mkdir(parents=True, exist_ok=True)
    files: List[str] = []

    def put(name: str, data: bytes, mode: int = 0o644) -> None:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        try:
            target.chmod(mode)
        except OSError:  # a file system without modes (a FAT stick): the probe is started with sh/python anyway
            pass
        files.append(name)

    package_probe = Path(__file__).resolve().with_name("hardware_probe.py")
    put("hardware_probe.py", package_probe.read_bytes(), 0o755)
    for name in ("hardware_probe.sh", "hardware_probe.ps1"):
        if (scripts / name).is_file():
            put(name, (scripts / name).read_bytes(), 0o755 if name.endswith(".sh") else 0o644)
    offer, problem = None, ""
    stale = folder / KIT_PAIR_DIR / "nonce"
    if stale.exists():   # an earlier kit's pairing in this folder never pairs again from here (nothing is deleted)
        stale.write_bytes(b"off\n")
    if pair:
        bundle = board_ops.pairing_bundle(signer, state_dir)
        public_line = ssh_trust.client_key(Path(state_dir))[1]
        try:
            step = gate.probe_kit_root_step(public_line, bundle)
        except ValueError as e:
            problem = ("Pairing is not in this kit: " + gate.SETUP_REASONS.get(str(e), str(e)) + ". The probe kit "
                       "reads the hardware only.")
        else:
            offer = pairing.create_offer(state_dir, public_line=public_line, signers=bundle.signers, pem=bundle.pem,
                                         via=via, audit=audit, actor=actor)
            base = KIT_PAIR_DIR + "/"
            put(base + "root-board.sh", step.encode("utf-8"), 0o755)
            put(base + "llmcf-gate", bundle.gate.encode("utf-8"))
            put(base + "framework_key.pub", (public_line + "\n").encode("utf-8"))
            put(base + "allowed_signers", (bundle.signers.strip() + "\n").encode("utf-8"))
            if bundle.pem:
                put(base + "signing_key.pem", bundle.pem.encode("utf-8"))
            put(base + "nonce", (offer.nonce + "\n").encode("ascii"))
    put(KIT_README, _kit_readme(offer is not None, offer.expires_at if offer else "").encode("utf-8"))
    return ProbeKit(folder=folder, files=files, offer=offer, problem=problem)


# --- a board over SSH (`llmcf scan --device HOST --user U [--port] [--password] [--label]`) ---------------------------

class DeviceScanError(WithSteps, RemoteFailure):
    """A board scan that could not be done. `kind`: user (the SSH user is not an account name: refused before any
    contact, as on every other board path), login (the board could not be reached or logged in to; `reason` is its SSH
    reason code when it has one), probe (the probe on the board failed), weaker_tier (enterprise mode refuses the
    board's key tier; the framework's key logged in, nothing ran), cancelled (the caller's Cancel ended it) or save
    (the profile could not be stored or written). `error` is the exception behind it."""

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
        if not password:
            # enterprise mode refuses a board whose framework key sits on a weaker tier at every use, its probe too
            # (trusted.tier_refusal, as the AI's probe); with a password the pairing below offers the ways on
            from orchestrator.runtime.device.trusted import tier_refusal
            refusal = tier_refusal(state_dir, device_id_for_host_key(ssh_trust.fingerprint(key)), mode, session)
            if refusal:
                raise DeviceScanError(refusal, "weaker_tier")
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


__all__ = ["DeviceScan", "DeviceScanError", "ImportedProbe", "KIT_PAIR_DIR", "LABEL_HINT", "LocalScan", "PairingImport",
           "ProbeKit", "ProfileWriteError", "USER_HINT", "export_probe_kit",
           "import_probe", "label_problem", "reach_code", "scan_device", "scan_local", "write_profile_file"]
