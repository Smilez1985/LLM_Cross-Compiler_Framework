#!/usr/bin/env python3
"""
The person's own board steps (MCP design 2.7, W1' of 2026-10-16): the one code path behind `llmcf deploy devices
list|add|remove`, `llmcf deploy devices edit` (Q5), `llmcf device list|settings`, the board contacts of
`llmcf device runtime prune|deploy|verify` and the deploy of a build (`llmcf deploy run`, deploy_build),
and behind the GUI's Boards > Boards and Devices... window and the Deployment tab's Deploy. Both are the person's channels. No tool and no AI reaches
this module (tests/test_board_steps.py keeps it out of the AI-reachable code): it trusts and removes boards, logs in
with a password once, records the sudo consent and removes builds from a board.

Every step returns facts or raises BoardStepError with the sentence the person reads (English, the CLI's words) and a
`kind` the GUI shows its own localized text for; a board that could not be reached or logged in to carries its SSH
reason code (reasons.py), recorded for Mojo when the contact is named (W1). Nothing here prints, asks or exits: the
questions stay with the caller (a terminal prompt, a GUI question with "No" as the default); where both ask, they ask
with the words kept here (SUDO_QUESTION).
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from orchestrator.remote.errors import RemoteFailure
from orchestrator.utils.next_steps import WithSteps

# The question before the systemd unit is allowed (CLI and GUI ask exactly this; "No" is the default).
SUDO_QUESTION = ("The systemd unit is written to /etc/systemd/system and enabled with sudo on the device (sudo -n: it "
                 "must work without a password, the deployment never asks). Allow deployments to this device to run "
                 "these sudo commands? Your answer is written to the audit log.")
SYSTEMD_CONSENT = ("device_runtime_systemd", "deployments may run sudo -n on the device to install the systemd unit")
# The question before a model that is not a catalog model runs in the short benchmark on the board, asked only after
# its sha256 was read there ({path} on the board, {user} the SSH user; CLI and GUI ask exactly this, "No" default).
UNTRUSTED_QUESTION = ("{path} is not a catalog model, so it is untrusted. llama-bench loads it natively on the device "
                      "as {user} (no sandbox). Run the short benchmark with it? Your answer is written to the audit "
                      "log.")
# The question before damaged deploy settings are set again from the defaults (CLI and GUI; "No" is the default).
DAMAGED_QUESTION = ("The deploy settings of device {device_id} cannot be used ({reason}). Set them again, starting "
                    "from the defaults? The damaged file {path} ({size}) is overwritten with the new settings; the "
                    "board's probe, its trust and its runtime stay as they are.")
UNTRUSTED_CONSENT = ("device_runtime_untrusted_model",
                     "an untrusted model may run natively on the device for the short llama-bench run")


class BoardStepError(WithSteps, RemoteFailure):
    """A board step that cannot be done. `kind` names the case (no_paramiko, unreachable, host_key_changed,
    not_trusted, other_device, login_failed, key_install, host_user, label, not_known, no_systemd, sudo_declined,
    not_saved, damaged, no_user, no_address; for a change of a board's address or key: no_board, address, key_text
    with facts["detail"] empty, several, not_a_key or no_paramiko, unchanged, no_key, stale, audit, other_board with
    facts["label"] and facts["device_id"] of the trusted board the new key belongs to, not_moved with facts["what"]
    the kind of the board's data that could not be copied to its new device ID); `reason` is the
    SSH reason code when the board could not be reached or logged in to; `error` the exception behind it."""

    def __init__(self, message: str, kind: str, reason: str = "", error: Optional[BaseException] = None,
                 **facts: Any):
        super().__init__(message, kind, reason, error)
        self.facts = facts


def _note(state_dir: Path, device_id: str, step: str, code: str) -> None:
    """Mojo's record of this contact with the board (W1); never fails the step (trusted.record_reach)."""
    from orchestrator.runtime.device.trusted import record_reach
    record_reach(state_dir, step, code, device_id=device_id)


def unreachable_error(error: BaseException, host: str, port: int, *, state_dir: Optional[Path] = None,
                      device_id: str = "", step: str = "") -> BoardStepError:
    """The refusal after a failed connection: its SSH reason code when it has one (OSError: the board does not
    answer), recorded for Mojo when the contact is named."""
    from orchestrator.remote import reasons
    code = reasons.reach_code(error)
    if code and state_dir is not None and device_id:
        _note(state_dir, device_id, step, code)
    return BoardStepError(f"Could not reach {host}:{port}: {error}", "unreachable", reason=code, error=error)


def _need_paramiko() -> None:
    """BoardStepError no_paramiko when SSH does not work here (remote/transport.py available(): a real import)."""
    from orchestrator.remote import transport
    if not transport.available()[0]:
        raise BoardStepError("Reaching a device needs the 'paramiko' package.", "no_paramiko")


# --- trusted boards (deploy devices list|add|remove) -------------------------------------------------------------

def trusted_rows(state_dir: Path) -> List[Dict[str, Any]]:
    """The trusted boards as `llmcf deploy devices list` shows them: label, address, user, key type and fingerprint,
    paired, the host key's state in known_hosts (known, changed, unknown), when and where the person trusted it and
    the last deploy. For the person only (it names the board's account)."""
    from orchestrator.runtime.device.trusted import TrustedDevices
    store = TrustedDevices(Path(state_dir))
    last = store.last_deploys()
    return [{"device_id": d.device_id, "label": d.label, "address": d.address, "user": d.user,
             "key_type": d.key_type, "fingerprint": d.fingerprint, "paired": d.paired,
             "host_key": store.key_status(d), "trusted": f"{d.trusted_at} ({d.trusted_via})",
             "last_deploy": last.get(d.device_id)} for d in store.list()]


def remove_trust(state_dir: Path, device_id: str, *, audit: Any, actor: str, channel: str) -> bool:
    """Removes the board's trusted record so AI tools can no longer name it (its known_hosts entry is kept) and writes
    the removal to the audit log (actor "cli" or "gui", `channel` framework-cli or framework-gui). `audit` is the audit
    log or a function that opens it (only after a record was removed). False when there is no such record."""
    from orchestrator.runtime.device.trusted import TrustedDevices
    device_id = str(device_id or "").strip()
    if not TrustedDevices(Path(state_dir)).remove(device_id):
        return False
    if callable(audit) and not hasattr(audit, "record"):
        try:
            audit = audit()
        except Exception:  # noqa: BLE001 - the removal itself was the person's
            audit = None
    if audit is not None:
        try:
            audit.record(actor, "device_removed", "external", "ask", "ok", arguments={"device_id": device_id},
                         approved=True, extra={"confirm_channel": channel, "answer": "accepted"})
        except Exception:  # noqa: BLE001 - logged by the audit log itself
            pass
    return True


def check_board_entry(host: str, user: str, label: Optional[str]) -> Optional[str]:
    """The checks of `llmcf deploy devices add HOST --user U [--label L]`: an address or host name, an account name and
    a label of 1-64 printable characters. Returns the cleaned label (None: none given)."""
    from orchestrator.remote.address import valid_host, valid_user
    from orchestrator.runtime.device.trusted import DeviceStoreError, clean_label
    if not valid_host(host) or not valid_user(user, allow_root=True):
        raise BoardStepError("HOST must be an address or a host name (e.g. 192.168.1.50, fe80::1 without brackets or "
                             "myboard.local) and the user an account name", "host_user")
    if label is None:
        return None
    try:
        return clean_label(label)
    except DeviceStoreError as e:
        raise BoardStepError(str(e), "label") from None


def read_host_key(state_dir: Path, host: str, port: int) -> Tuple[Any, str, str]:
    """(key, its state in the framework's known_hosts: known, changed or unknown, its fingerprint) of the board at
    host:port: one key read, no login (remote/trust.py TrustStore.check)."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import TrustStore
    _need_paramiko()
    try:
        check = TrustStore(Path(state_dir)).check(Endpoint(host, port))
    except Exception as e:  # unreachable, no SSH server, a broken known_hosts file
        raise unreachable_error(e, host, port) from None
    return check.key, check.status, check.fingerprint


def not_known_error(host: str, port: int, status: str) -> BoardStepError:
    """The refusal of `deploy devices add` for a board whose key the person did not trust before."""
    return BoardStepError(f"{host}:{port} is not trusted yet (its host key is {status}). Trust it with 'llm-cli deploy "
                          "run' or 'llm-cli scan --device', which show the fingerprint for you to compare.",
                          "not_known", status=status)


def add_trusted_board(state_dir: Path, host: str, port: int, user: str, key: Any, *, label: Optional[str],
                      via: str, audit: Any, actor: str):
    """`llmcf deploy devices add` after the person confirmed the shown fingerprint: tests whether the framework's own
    key logs in (paired; no password is ever asked here), then stores the board's trusted record and audits the
    trust. Returns (TrustedDevice or None when the record could not be written, paired)."""
    from orchestrator.runtime.device.trusted import record_trusted_device
    paired = framework_key_logs_in(state_dir, host, port, user)
    device = record_trusted_device(Path(state_dir), host, port, user, key, via=via, paired=paired, label=label,
                                   audit=audit, actor=actor)
    return device, paired


def framework_key_logs_in(state_dir: Path, host: str, port: int, user: str) -> bool:
    """Whether the framework's own SSH key logs in to `user`@host:port (the board is paired; no password is ever
    asked here). The board's host key must be the trusted one; a board that does not answer is not paired."""
    from orchestrator.utils import ssh_trust
    try:
        client = ssh_trust.connect(host, user, Path(state_dir), port=port, password=None)
        client.close()
        return True
    except Exception:  # noqa: BLE001 - not paired yet: the record says so, and deploy_to_device refuses it
        return False


# --- a trusted board's address and host key (Q5: deploy devices edit) -----------------------------------------------

@dataclass
class BoardChange:
    """A change of a trusted board's address or host key, shown to the person before it is made: `device` the board's
    record now, `plan` the change (remote/pairing.py KeyReplacement: the address, key type and fingerprint now and
    after it, and a key of another board the new address held). After change_board, `data` says where the board's
    data went for a new key (board_move.MovedData; None when the device ID stays)."""
    device: Any
    plan: Any
    data: Any = None


def plan_board_change(state_dir: Path, device_id: str, *, host: Optional[str] = None, port: Optional[int] = None,
                      key_text: Optional[str] = None, read_key: bool = False) -> BoardChange:
    """`llmcf deploy devices edit` and the Boards window's Edit address or key...: the change of the trusted board
    `device_id` to a new host name or IP address (`host`), SSH port (`port`) and host key: pasted (`key_text`, one line
    of the board's /etc/ssh/ssh_host_*_key.pub, of known_hosts or ssh-keyscan) or read from the board at its new
    address (`read_key`: one connection to its SSH port, no login; off the GUI thread). None keeps a value. Nothing is
    written; BoardStepError when the change cannot be made."""
    from orchestrator.remote import pairing
    from orchestrator.remote.address import Endpoint, valid_host, valid_port
    from orchestrator.remote.trust import KeyTextError, TrustStore, parse_public_key
    from orchestrator.runtime.device.trusted import TrustedDevices
    state_dir = Path(state_dir)
    device = TrustedDevices(state_dir).get(str(device_id or "").strip())
    if device is None:
        raise BoardStepError(f"There is no trusted board {device_id}.", "no_board")
    new_host = (host or "").strip() or device.host
    new_port = device.port if port is None else port
    if not valid_host(new_host) or not valid_port(new_port):
        raise BoardStepError("The new address must be an address or a host name (e.g. 192.168.1.50, fe80::1 without "
                             "brackets or myboard.local) and the port a number from 1 to 65535.", "address")
    endpoint = Endpoint(new_host, int(new_port))
    store = TrustStore(state_dir)
    key = None
    if key_text is not None and key_text.strip():
        try:
            key = parse_public_key(key_text)
        except KeyTextError as e:
            raise BoardStepError(str(e), "key_text", detail=e.kind) from None
    elif read_key:
        _need_paramiko()
        try:
            key = store.read_presented_key(endpoint)
        except Exception as e:  # unreachable, no SSH server
            raise unreachable_error(e, endpoint.host, endpoint.port) from None
    try:
        plan = pairing.plan_replacement(store, device, endpoint, key)
    except pairing.KeyReplaceError as e:
        raise BoardStepError(str(e), e.kind) from None
    if plan.key_changed and plan.new_device_id != device.device_id:
        # the new key is another trusted board's: its record would be overwritten with this board's name, user and
        # address without a word, so the change is refused and names that board
        other = TrustedDevices(state_dir).get(plan.new_device_id)
        if other is not None:
            raise BoardStepError(f"The new host key is the key of the trusted board {other.label} "
                                 f"({other.device_id}); nothing was changed. Change or remove that board instead.",
                                 "other_board", label=other.label, device_id=other.device_id)
    return BoardChange(device=device, plan=plan)


def change_lines(change: BoardChange) -> List[str]:
    """The change as the CLI shows it before asking (English; the GUI shows the same facts side by side)."""
    plan, device = change.plan, change.device
    lines = [f"Board: {device.label} ({device.device_id})",
             f"Address now: {plan.old.display}",
             f"Address after the change: {plan.new.display}",
             f"Host key now: {plan.old_key_type} {plan.old_fingerprint}",
             f"Host key after the change: {plan.new_key_type} {plan.new_fingerprint}"]
    if plan.displaced:
        lines.append(f"The framework's known_hosts holds another {plan.new_key_type} key for {plan.new.display} "
                     f"({plan.displaced}); it is replaced too.")
    if plan.key_changed:
        lines.append(f"The board's device ID becomes {plan.new_device_id} (it derives from the host key). Its data "
                     f"moves there from {plan.old_device_id}: its stored probe, deploy settings, deployed runtime, "
                     "deploy record and benchmark results ('tested on'), each copied and checked first; the old "
                     "copies go only after the change.")
    return lines


def moved_lines(change: BoardChange) -> List[str]:
    """Where the board's data went after change_board (English; the GUI shows the same facts in its own texts)."""
    from orchestrator.runtime.device.board_move import kind_words
    data = change.data
    if data is None:
        return []
    lines = []
    if data.moved:
        lines.append(f"Moved to the new device ID {data.new_id}: {kind_words(data.moved)}.")
    if data.kept:
        lines.append(f"Kept under {data.old_id}, because {data.new_id} has its own: {kind_words(data.kept)}.")
    if data.left:
        lines.append(f"Could not be removed under {data.old_id} (the copies under {data.new_id} are complete): "
                     f"{kind_words(data.left)}.")
    return lines


CHANGE_QUESTION = ("Change the board's address and host key in the framework's known_hosts? Compare the new "
                   "fingerprint with 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' on the board. Your answer is "
                   "written to the audit log.")


def change_board(state_dir: Path, change: BoardChange, *, via: str, audit: Any, actor: str,
                 output_dirs: Sequence[Path] = ()):
    """Makes the change the person confirmed (Q5): known_hosts first (remote/pairing.py replace_key, written to the
    audit log before anything changes), then the board's trusted record follows: at its new address, and for a new key
    under the board's new device ID with its label and user (whether the framework's key logs in there is tested once,
    as `deploy devices add` does). For a new address the stored probe's address follows. For a new key the board's
    data moves to its new device ID (P6, board_move.py: its stored probe, deploy settings, deployed runtime, deploy
    record and its "tested on" records in the builds below `output_dirs`): copied and checked before known_hosts
    changes, the old copies removed only after the change; `change.data` says what moved. Returns (TrustedDevice or
    None when the record could not be written, paired); BoardStepError when known_hosts could not be changed or the
    data could not be copied (kind not_moved; nothing was changed then)."""
    from orchestrator.remote import pairing
    from orchestrator.remote.trust import TrustStore
    from orchestrator.runtime.device.trusted import TrustedDevices
    state_dir = Path(state_dir)
    plan, device = change.plan, change.device
    move = None
    if plan.key_changed and plan.new_device_id != device.device_id:
        from orchestrator.runtime.device.board_move import BoardDataMove, BoardMoveError
        move = BoardDataMove(state_dir, device.device_id, plan.new_device_id, output_dirs=output_dirs)
        try:
            move.stage()
        except BoardMoveError as e:
            raise BoardStepError(str(e), "not_moved", what=e.what) from None
    try:
        pairing.replace_key(TrustStore(state_dir), plan, via=via, audit=audit, actor=actor)
    except pairing.KeyReplaceError as e:
        if move is not None:
            move.undo()   # nothing changed: the board's data stays where it was
        raise BoardStepError(str(e), e.kind) from None
    except BaseException:
        if move is not None:
            move.undo()   # known_hosts' lock held elsewhere, a Ctrl+C: no change, so no copies stay either
        raise
    try:
        paired = device.paired
        if plan.key_changed:
            paired = framework_key_logs_in(state_dir, plan.new.host, plan.new.port, device.user)
        try:
            moved = TrustedDevices(state_dir).replace(device.device_id, plan.new.host, plan.new.port, device.user,
                                                      plan.new_key, via=via, paired=paired, label=device.label)
        except Exception:  # noqa: BLE001 - known_hosts holds the change; the record is added again by the person
            moved = None
    finally:
        if move is not None:
            # the change is made (known_hosts holds the new key): only now the old ID's copies go, also when a Ctrl+C
            # ends the login test, so no second copy of the board's data stays
            change.data = move.finish()
    if plan.address_changed:
        from orchestrator.runtime.profile import ProfileStore
        try:
            ProfileStore(state_dir).set_address(plan.new_device_id if move is not None else device.device_id,
                                                plan.new.store_form)
        except (OSError, ValueError):
            pass  # the probe's address is set again by the next probe
    return moved, paired


# --- stored boards (device list) -------------------------------------------------------------------------------------

SYSTEM_KEYS = ("OS", "OS_ID", "OS_VERSION_ID", "ARCH", "LIBC", "LIBC_VERSION", "INIT_SYSTEM", "SOC_MODEL")


def device_rows(state_dir: Path) -> List[Dict[str, Any]]:
    """The stored (probed) boards as `llmcf device list` shows them: label, address, system facts of the probe, deploy
    settings (or the note that they are damaged) and the deployed runtime build."""
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    from orchestrator.runtime.profile import ProfileError, ProfileStore
    store = ProfileStore(Path(state_dir))
    rows = []
    for device_id in store.list_devices():
        info = store.device_info(device_id)
        try:
            profile = store.load_device(device_id)
        except ProfileError:
            profile = None
        try:
            settings: Dict[str, Any] = DeploySettings.from_dict(store.load_deploy_settings(device_id)).as_dict()
        except (SettingsError, ValueError):
            settings = {"problem": "damaged; set them again"}
        record = DeviceRuntimeStore(Path(state_dir), device_id).record()
        system = {k: profile.get(k) for k in SYSTEM_KEYS} if profile is not None else None
        rows.append({"device_id": device_id, "label": info.get("label"), "address": info.get("address"),
                     "system": system, "deploy": settings,
                     "deployed": {k: record.get(k) for k in ("build_id", "deployed_at", "install")} if record
                     else None})
    return rows


def boards_overview(state_dir: Path) -> List[Dict[str, Any]]:
    """Every board the person has, once: the stored boards (device_rows) and the trusted boards (trusted_rows) merged
    by their device ID (both derive it from the board's host key). Each entry: device_id, label, trusted (the trusted
    row or None), stored (the stored row or None); sorted by label."""
    merged: Dict[str, Dict[str, Any]] = {}
    for row in device_rows(state_dir):
        merged[row["device_id"]] = {"device_id": row["device_id"], "label": row.get("label") or row["device_id"],
                                    "trusted": None, "stored": row}
    for row in trusted_rows(state_dir):
        entry = merged.setdefault(row["device_id"], {"device_id": row["device_id"], "label": row["label"],
                                                     "trusted": None, "stored": None})
        entry["trusted"] = row
        entry["label"] = row["label"]   # the person's own name for the board wins
    return sorted(merged.values(), key=lambda e: (str(e["label"]).lower(), e["device_id"]))


# --- deploy settings (device settings) --------------------------------------------------------------------------

# A person's consent in the audit log (actor "user"; RuntimeError when it cannot be written: without its record there
# is no consent, and the caller changes nothing): the one writer of orchestrator/remote/pairing.py.
from orchestrator.remote.pairing import record_consent  # noqa: E402 - kept under this module's name


def load_settings(store: Any, device_id: str):
    """The board's deploy settings (DeploySettings); SettingsError or ValueError when the stored file is damaged."""
    from orchestrator.runtime.device.settings import DeploySettings
    return DeploySettings.from_dict(store.load_deploy_settings(device_id))


def settings_to_change(store: Any, device_id: str, *, may_replace: Callable[[str, str, str], bool]):
    """(settings, replaced) that `llmcf device settings` and the Boards window change: the stored ones; or, when the
    stored file is damaged ("damaged; set them again") and the person said yes to DAMAGED_QUESTION
    (`may_replace(reason, full path, size)`), the defaults with replaced=True: nothing of the damaged file is read as
    a setting, and it is overwritten only when the new settings are saved. BoardStepError ("damaged") otherwise."""
    from orchestrator.Core.deletion import size_text
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    try:
        return load_settings(store, device_id), False
    except (SettingsError, ValueError) as e:
        reason = str(e)
    path = Path(store.deploy_settings_path(device_id))
    try:
        size = size_text(path.stat().st_size)
    except OSError:
        size = "?"
    if not may_replace(reason, str(path), size):
        raise BoardStepError(f"The deploy settings of device {device_id} cannot be used: {reason}", "damaged",
                             detail=reason)
    return DeploySettings(), True


def change_settings(store: Any, device_id: str, profile: Any, current: Any, changes: Dict[str, Any], *,
                    may_use_sudo: Callable[[], bool], record_sudo_consent: Callable[[], None],
                    stamp: Callable[[], str]):
    """`llmcf device settings DEVICE --install/--user/--port/--model/--kv-cache`: applies `changes` (field -> value;
    kv_cache None means automatic) to `current` and saves them. Choosing the systemd unit needs a board whose probe
    reports systemd and the person's yes to SUDO_QUESTION (`may_use_sudo()`), written to the audit log first
    (`record_sudo_consent()`, which raises when it cannot: nothing is changed then). SettingsError for a value that
    does not pass; BoardStepError (no_systemd, sudo_declined, not_saved) otherwise. Returns the saved settings."""
    new = current.changed(**changes)   # SettingsError for a value that does not pass
    if new.install == "systemd" and new.sudo_confirmed_at is None:
        init = profile.get("INIT_SYSTEM") if profile is not None else None
        if init != "systemd":
            raise BoardStepError(f"Device {device_id} does not run systemd (its probe reports "
                                 f"{init or 'no init system'}); the start script is the way to run the runtime "
                                 "there. The settings were not changed.", "no_systemd", init=init or "")
        if not may_use_sudo():
            raise BoardStepError("The systemd unit needs your confirmation that deployments may use sudo on the "
                                 "device; the settings were not changed.", "sudo_declined")
        record_sudo_consent()
        new = replace(new, sudo_confirmed_at=stamp())
    try:
        store.save_deploy_settings(device_id, new.as_dict())
    except (OSError, ValueError) as e:
        raise BoardStepError(f"Could not save the deploy settings: {e}", "not_saved", error=e) from None
    return new


# --- reaching a stored board (device runtime deploy|verify|prune --on-device) ------------------------------------

def board_address(store: Any, device_id: str) -> Tuple[str, int]:
    """(host, port) of a stored board from its stored address; ValueError when it has none or it is not
    host[:port]."""
    from orchestrator.runtime.device.devices import split_address
    return split_address(store.device_info(device_id).get("address"))


def connect_stored(state_dir: Path, device_id: str, host: str, port: int, user: str,
                   password: Optional[str] = None, *, step: str = "",
                   on_client: Optional[Callable[[Any], None]] = None, cancel: Any = None,
                   on_event: Optional[Callable[[Any], None]] = None) -> Tuple[Any, Any]:
    """(board session, host key) of the stored board `device_id` at host:port as `user`: the logged-in RemoteSession
    (orchestrator/remote/session.py; RemoteShell runs the steps over it). Its host key must be the trusted one the
    board was probed with (the device ID derives from it), at this login and at every login after a network drop, so a
    step never reaches another machine at the same address; while the board's network is gone the session waits for
    it (N5; `on_event` gets its events, `cancel`, a session.CancelToken, ends the wait at once). A `password` is the
    person's one-time password (OneTimePassword credentials: used for this session's logins only, never stored) and
    installs the framework's key once. `step` names the contact for Mojo's record of the board (W1). The caller closes
    the session (`on_client` gets it at once, so a Cancel can close it)."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.session import KeyOnly, OneTimePassword, RemoteSession, SessionFailure, Stopped
    from orchestrator.runtime.device import reasons
    from orchestrator.utils import ssh_trust
    state_dir = Path(state_dir)
    _need_paramiko()
    credentials = OneTimePassword(password) if password else KeyOnly()
    # the board this step means is always compared: a missing device ID matches no key (as before)
    session = RemoteSession(Endpoint(host, port), user, state_dir, credentials=credentials,
                            expect_device_id=str(device_id or ""), cancel=cancel, on_event=on_event)
    try:
        session.open()
    except Stopped:
        raise BoardStepError("The contact with the board was cancelled.", "cancelled") from None
    except SessionFailure as failed:
        e = failed.error
        if failed.kind == "key_read":   # unreachable, no SSH server, a broken known_hosts file
            raise unreachable_error(e, host, port, state_dir=state_dir, device_id=device_id, step=step) from None
        if failed.kind == "key_changed":
            _note(state_dir, device_id, step, reasons.HOST_KEY_CHANGED)
            raise BoardStepError(str(ssh_trust.HostKeyChanged(host, port)), "host_key_changed",
                                 reason=reasons.HOST_KEY_CHANGED) from None
        if failed.kind == "not_trusted":
            raise BoardStepError(f"{host}:{port} is not trusted yet: probe it first with llm-cli scan --device {host} "
                                 "--user USER.", "not_trusted") from None
        if failed.kind == "other_device":
            raise BoardStepError(f"{host}:{port} answers with the host key of another device than {device_id}; probe "
                                 "it again before deploying to it.", "other_device") from None
        # the login: authentication, network, a host key that changed meanwhile
        code = reasons.classify(e) if e is not None else None
        if code not in (None, reasons.AUTH_FAILED):
            raise unreachable_error(e, host, port, state_dir=state_dir, device_id=device_id, step=step) from None
        if code == reasons.AUTH_FAILED:
            _note(state_dir, device_id, step, reasons.AUTH_FAILED)
        # none of the four reasons: shown as it is, never relabelled as a refused login
        raise BoardStepError(f"SSH login to {user}@{host} failed: {e if e is not None else failed}", "login_failed",
                             reason=code or "", error=e) from None
    if on_client is not None:
        on_client(session)
    if password:
        from orchestrator.remote import pairing
        try:
            from orchestrator.runtime.device import board_ops
            # the person's writer: the framework's key, installed once, with llmcf's command gate (board_ops.py)
            signer = board_ops.framework_signer(state_dir=state_dir)
            pairing.pair(session.client, state_dir, board_ops.bundle(signer), password=password)
        except ssh_trust.HostKeyError as e:
            session.close()
            raise BoardStepError(str(e), "key_install", error=e) from None
    _note(state_dir, device_id, step, "")   # reached and logged in: a recorded problem ends
    return session, session.key


def remember_board(state_dir: Path, host: str, port: int, user: str, key: Any, *, via: str, audit: Any,
                   actor: str, label: Optional[str] = None):
    """After a login to a board with the framework's key (a deploy; a verify only checks) whose host key `key` the
    person trusted: its trusted record, so AI tools may name it (each deploy is still confirmed by the person). `label`
    None keeps the board's name. Never fails the caller's work."""
    from orchestrator.runtime.device.trusted import record_trusted_device
    return record_trusted_device(Path(state_dir), host, port, user, key, via=via, paired=True, audit=audit,
                                 actor=actor, label=label)


def device_audit(framework: Any, state_dir: Path):
    """The tool audit log for the person's decisions about a board: the framework's registry's log, else the per-user
    audit.db opened with its key, as `llmcf` opens it when the framework is not booted; None when it cannot be opened
    (the person's decision itself still holds)."""
    audit = getattr(getattr(framework, "tool_registry", None), "audit", None)
    if audit is not None:
        return audit
    try:
        from orchestrator.Core.vault_key import audit_key
        from orchestrator.tools.audit import AuditLog
        return AuditLog(Path(state_dir) / "audit.db", key=audit_key(None, Path(state_dir)))
    except Exception:  # noqa: BLE001 - the record is still written; the trust itself was the person's
        return None


def remember_deployed(state_dir: Path, host: str, port: int, user: str, key: Any, *, via: str, actor: str,
                      label: Optional[str] = None, audit: Any = None, framework: Any = None):
    """After a successful deploy of a build to a board whose host key the person trusted in this channel's question
    (`llmcf deploy run`, the Deployment tab): its trusted record (remember_board), audited as the CLI audits it. `audit`
    is the audit log or a function that opens it (called only now); None takes device_audit (the registry's log, else
    the per-user one). `key` None (a deploy started without the key of its question) reads the key the board presents
    now and records it only when the framework's known_hosts knows it. Never fails the deploy: None when nothing was
    written."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import TrustStore
    state_dir = Path(state_dir)
    if key is None:
        try:
            check = TrustStore(state_dir).check(Endpoint(host, int(port)))
        except Exception:  # noqa: BLE001 - unreachable now: the next deployment records it
            return None
        if not check.known:
            return None
        key = check.key
    if audit is None:
        audit = device_audit(framework, state_dir)
    elif callable(audit) and not hasattr(audit, "record"):
        try:
            audit = audit()
        except Exception:  # noqa: BLE001 - the record itself is the person's
            audit = None
    return remember_board(state_dir, host, int(port), user, key, via=via, audit=audit, actor=actor, label=label)


# --- the deploy of a build (deploy run, the Deployment tab's Deploy) -----------------------------------------------

@dataclass
class BuildDeploy:
    """What deploy_build did: `package` the ZIP that went to the board (made now from the build, or the one given;
    None when it could not be made), `package_error` why it could not be made (a DeployError with its reason and next
    step; nothing was deployed then), `result` the deploy's DeployResult (None without a package) and `device` the
    board's trusted record written after a successful deploy (None: not written)."""
    package: Optional[Path] = None
    package_error: Optional[Exception] = None
    result: Any = None
    device: Any = None


def deploy_build(framework: Any, manager: Any, endpoint: Any, user: str, *, source: Any = None,
                 package: Optional[Path] = None, profile: str = "", docker: bool = False,
                 containers: Optional[Sequence[str]] = None, credentials: Any = None, label: Optional[str] = None, kv_cache: Optional[str] = None, wait: Optional[float] = None,
                 on_event: Optional[Callable[[Any], None]] = None, cancel: Any = None, key: Any = None,
                 bind_key: bool = True, via: str, actor: str, state_dir: Any = None,
                 output_dir: Optional[Path] = None, audit: Any = None,
                 remember: Optional[Callable[[Any], Any]] = None,
                 on_step: Callable[[str], None] = lambda _kind: None) -> BuildDeploy:
    """The deploy of a build to a board, one core step for `llmcf deploy run` and the Deployment tab's Deploy and
    Deploy existing package... (the questions, the trust of a new host key, the password and the rendering stay with
    each). The person's step only: no AI-reachable module imports this one (the AI deploys with deploy_to_device).

    1. The package (`on_step("package")`): `package` as it is, else made from `source` (a build folder or a model file)
       for the target `profile` by make_package: `docker` packs the container images (air-gap) under the image name
       of package_docker_config (`containers`: the ones the person chose in the Deployment tab's container table; None:
       every one, as `llmcf deploy package --docker`), `kv_cache` names the KV cache in the start command, and `cancel` stops it between its
       steps; written to `output_dir` (None: the configured output folder, package_folder). A package that cannot be
       made comes back as `package_error`; nothing is deployed then.
    2. The deploy (`on_step("deploy")`): DeploymentManager.deploy_package to `endpoint` as `user` with `credentials`
       (OneTimePassword: the person's one-time password, used for this deploy's logins only and never kept; KeyOnly or
       None: the framework's key), waiting `wait` seconds for a board whose network dropped (None: the deploy's
       default), with `on_event` its events and `cancel` (remote/session.py CancelToken) its cancel, which also holds
       the deploy's SSH session. With `bind_key` and a `key` the board must present exactly that key (the one the
       person was shown); without, the framework's known_hosts decides.
    3. After a successful deploy, the board's trusted record: `remember(key)` when the caller writes it, else
       remember_deployed (`state_dir` the per-user folder or a function that names it, asked only then; None: the
       framework's; `label` None keeps the board's name; `audit` an audit log or a function that opens it, None: the
       CLI's audit log)."""
    from orchestrator.Core.deployment_manager import DeployError, make_package, package_folder
    done = BuildDeploy(package=Path(package) if package is not None else None)
    stop = getattr(cancel, "is_set", None)
    if done.package is None:
        on_step("package")
        try:
            extra = {"containers": containers} if containers is not None else {}
            done.package = make_package(manager, framework, source, profile, docker,
                                        Path(output_dir) if output_dir is not None else package_folder(framework),
                                        kv_cache=kv_cache, cancelled=stop, **extra)
        except DeployError as e:
            done.package_error = e
            return done
    on_step("deploy")
    options: Dict[str, Any] = {"port": int(endpoint.port), "password": getattr(credentials, "password", None),
                               "cancelled": stop, "on_event": on_event}
    if callable(getattr(cancel, "hold", None)):
        options["on_client"] = cancel.hold
    if wait is not None:
        options["wait_seconds"] = wait
    if bind_key and key is not None:
        from orchestrator.utils import ssh_trust
        options["expect_fingerprint"] = ssh_trust.fingerprint(key)
    done.result = manager.deploy_package(done.package, endpoint.host, user, **options)
    if done.result.success:
        if remember is not None:
            done.device = remember(key)
        else:
            folder = state_dir() if callable(state_dir) else state_dir
            if folder is None:
                folder = getattr(getattr(framework, "config", None), "user_state_dir", None)
            if folder:
                done.device = remember_deployed(Path(folder), endpoint.host, endpoint.port, user, key, via=via,
                                                actor=actor, label=label, audit=audit, framework=framework)
    return done


# --- older builds on the board (device runtime prune --on-device) ------------------------------------------------

@dataclass
class BoardPrune:
    """What the board holds besides its active build: the install folder, the person's deletion list (None: nothing
    to list), the builds a running program uses (never listed) and the build that was active before."""
    root: str
    plan: Any
    busy: List[str]
    previous: Optional[str]


def previous_build(state_dir: Path, device_id: str) -> Optional[str]:
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    return str(DeviceRuntimeStore(Path(state_dir), device_id).record().get("previous") or "")[:16] or None


def list_board_builds(shell: Any, state_dir: Path, device_id: str) -> BoardPrune:
    """Reads the older runtime builds on the board (never the active one; one a running program uses is named but not
    listed) as the person's deletion list: full path on the board, folder name, size, what each is."""
    from orchestrator.runtime.device.remote import board_cleanup_plan, older_builds, read_facts
    previous = previous_build(state_dir, device_id)
    root = read_facts(shell).root
    builds = older_builds(shell, root)
    return BoardPrune(root=root, plan=board_cleanup_plan(builds, root, previous),
                      busy=[b.build_id16 for b in builds if b.in_use], previous=previous)


def remove_board_builds(shell: Any, accepted: Any, prune: BoardPrune, device_id: str, audit: Any):
    """Removes exactly the builds the person ticked and confirmed (in the GUI or at a terminal; never an AI) for good,
    a board has no trash. The list is read from the board again first; the removal is audited before anything goes.
    Returns a deletion.DeletionResult; PlanChanged, PermissionError or RuntimeError when nothing was removed."""
    from orchestrator.runtime.device.remote import remove_older_builds
    return remove_older_builds(shell, accepted, root=prune.root, device_id=device_id, previous=prune.previous,
                               audit=audit)


# --- the framework's own OpenSSH settings (DECISIONS round 25 Q4) --------------------------------------------------

SSH_BLOCK_QUESTION = "Write these settings into the framework's own SSH settings file?"
SSH_KNOWINGLY_QUESTION = "These lines connect through another computer or run a command (see above). Add them anyway?"


@dataclass(frozen=True)
class SshBlockPlan:
    """The person's change of one Host block of the framework's own SSH settings file (orchestrator/remote/
    ssh_config.py; every OpenSSH call reads it with -F): `host` the block's Host argument, `old` and `new` its option
    lines (empty: there is none / it goes), `refused` the lines the file may not hold (ssh_config.LineProblem, with
    the reason: nothing is written while there is one), `knowingly` the ProxyJump and ProxyCommand lines the person
    says yes to after reading what they do (ssh_config.Knowingly), `text` the file's text the plan was made from (a
    change made meanwhile is refused as stale)."""
    host: str
    old: Tuple[str, ...]
    new: Tuple[str, ...]
    refused: Tuple[Any, ...]
    knowingly: Tuple[Any, ...]
    text: str
    # The block is already these lines, with a ProxyJump or ProxyCommand no yes of the person covers yet (a hand edit,
    # or a file from before the yes was kept): the person's knowing yes accepts them as they are.
    pending_yes: bool = False

    @property
    def unchanged(self) -> bool:
        return self.old == self.new


def ssh_settings(state_dir: Path) -> Tuple[Path, str]:
    """(the file, its text) of the framework's own SSH settings: `llmcf deploy ssh-config show` and the Boards window's
    SSH settings... (the file is created, private to this account, when it is missing, as every OpenSSH call does)."""
    from orchestrator.remote import ssh_config
    path = ssh_config.ensure(Path(state_dir))
    return path, ssh_config.read(Path(state_dir))


def plan_ssh_block(state_dir: Path, host: str, lines: Sequence[str]) -> SshBlockPlan:
    """The change of `host`'s block to `lines` (option lines such as "ProxyJump jump.example"; none removes the block),
    checked line by line (ssh_config.check_lines; a ProxyJump also against the file's path). BoardStepError kind
    ssh_host for a Host argument OpenSSH would not read."""
    from orchestrator.remote import ssh_config
    host = (host or "").strip()
    if not ssh_config.valid_host(host):
        raise BoardStepError(f"{host!r} is no host name, address or pattern OpenSSH reads (letters, digits, '.', '-', "
                             "':', '_' and the patterns '*', '?', '!').", "ssh_host")
    path = ssh_config.ensure(Path(state_dir))
    text = ssh_config.read(Path(state_dir))
    new = tuple(line.strip() for line in lines if line and line.strip() and not line.strip().startswith("#"))
    check = ssh_config.check_lines(new, jump_path=ssh_config.argument(path))
    old = tuple(ssh_config.block_lines(text, host) or ())
    pending = bool(check.knowingly) and old == new and \
        ssh_config.block_digest(host, new) not in ssh_config.accepted_digests(Path(state_dir))
    return SshBlockPlan(host, old, new, tuple(check.refused), tuple(check.knowingly), text, pending)


def ssh_block_lines(plan: SshBlockPlan) -> List[str]:
    """What the person reads before saying yes (the CLI prints these; the Boards window shows the same facts): the
    block now and after the change, every refused line with its reason, and what each ProxyJump or ProxyCommand line
    does."""
    lines = [f"Host {plan.host}, now: " + ("; ".join(plan.old) if plan.old else "no settings"),
             f"Host {plan.host}, after the change: " + ("; ".join(plan.new) if plan.new else "no settings (the block "
                                                                                           "goes)")]
    lines += [f"Refused: {problem.text()}" for problem in plan.refused]
    lines += [f"{item.line}: {item.what}" for item in plan.knowingly]
    return lines


def save_ssh_block(state_dir: Path, plan: SshBlockPlan, *, via: str, knowingly: bool = False, audit: Any = None,
                   actor: str = "") -> Path:
    """Writes the planned block into the framework's own SSH settings file (atomically, readable by this account
    only) and audits it (the person's channels only: PermissionError for any other `via`; no module an AI can reach
    imports this one). BoardStepError kinds: ssh_refused (a refused line; facts["problems"]), ssh_knowingly (a
    ProxyJump or ProxyCommand without the person's knowing yes), ssh_unchanged, ssh_stale (the file changed since the
    plan was made). The audit row names no host: the number of lines and whether a jump or a command is among them."""
    from orchestrator.remote import ssh_config
    from orchestrator.remote.pairing import PERSON_CHANNELS
    from orchestrator.utils.atomic import write_private
    from orchestrator.utils.file_lock import file_lock
    if via not in PERSON_CHANNELS:
        raise PermissionError("Only the person changes the framework's SSH settings (the GUI or the CLI).")
    if plan.refused:
        raise BoardStepError("The settings hold a line the framework never uses: " + "; ".join(
            p.text() for p in plan.refused), "ssh_refused", problems=[p.text() for p in plan.refused])
    if plan.knowingly and not knowingly:
        raise BoardStepError("A ProxyJump or ProxyCommand is added only with your yes after reading what it does.",
                             "ssh_knowingly")
    state_dir = Path(state_dir)
    # The same lines again, with a ProxyJump or ProxyCommand the person has not said yes to yet (a hand edit, or a
    # file from before the framework kept the yes): the person's knowing yes accepts them as they are.
    accepting = plan.pending_yes
    if plan.unchanged and not accepting:
        raise BoardStepError(f"The settings of {plan.host} are already these; nothing was changed.", "ssh_unchanged")
    path = ssh_config.ensure(state_dir)
    with file_lock(state_dir / ".ssh_config.lock"):
        if ssh_config.read(state_dir) != plan.text:
            raise BoardStepError("The framework's SSH settings file changed since these settings were read; read them "
                                 "again.", "ssh_stale")
        if not accepting:
            write_private(path, ssh_config.with_block(plan.text, plan.host, plan.new))
        if plan.knowingly:   # the person's knowing yes to exactly these lines (prepare() refuses any other)
            ssh_config.record_accepted(state_dir, plan.host, plan.new)
    ssh_config.ensure(state_dir)   # private to this account (an ACL on Windows), as the framework key
    from orchestrator.remote.pairing import _open_audit
    log = _open_audit(audit)
    if log is not None:
        try:
            log.record(actor or via, "ssh_config_changed", "framework", "ask", "ok",
                       arguments={"lines": len(plan.new), "removed": not plan.new,
                                  "jump_or_command": bool(plan.knowingly), "accepted_as_is": accepting},
                       approved=True, extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
        except Exception:  # noqa: BLE001 - the file holds the person's change; the log reports its own failure
            pass
    return path


def import_ssh_block(host: str, home_config: Optional[Path] = None) -> List[str]:
    """The person-only import (round 25 Q4): the option lines of `host`'s Host block in the person's own ~/.ssh/config
    (`home_config`; tests give their own file), read only. The caller shows them and writes them through plan_ssh_block
    and save_ssh_block with the same checks; Include and Match are not followed. BoardStepError kind ssh_import_none
    when the file has no Host block naming `host` exactly, ssh_import_unreadable when it cannot be read."""
    from orchestrator.remote import ssh_config
    path = Path(home_config) if home_config is not None else Path.home() / ".ssh" / "config"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise BoardStepError("There is no ~/.ssh/config on this computer.", "ssh_import_none") from None
    except OSError as e:
        raise BoardStepError(f"~/.ssh/config cannot be read ({e.strerror or e}).", "ssh_import_unreadable",
                             error=e) from None
    lines = ssh_config.in_home_config(text, host)
    if not lines:
        raise BoardStepError(f"~/.ssh/config has no Host block for {host}.", "ssh_import_none")
    return lines


__all__ = ["BoardPrune", "BoardStepError", "DAMAGED_QUESTION", "SUDO_QUESTION", "SYSTEMD_CONSENT",
           "UNTRUSTED_CONSENT", "UNTRUSTED_QUESTION", "add_trusted_board", "board_address", "boards_overview",
           "change_settings", "check_board_entry", "connect_stored", "device_rows", "list_board_builds",
           "load_settings", "not_known_error", "previous_build", "read_host_key", "record_consent", "remember_board",
           "remove_board_builds", "remove_trust", "settings_to_change", "trusted_rows", "unreachable_error",
           "BoardChange", "CHANGE_QUESTION", "change_board", "change_lines", "framework_key_logs_in",
           "moved_lines", "plan_board_change", "BuildDeploy", "deploy_build", "device_audit", "remember_deployed",
           "SSH_BLOCK_QUESTION", "SSH_KNOWINGLY_QUESTION", "SshBlockPlan", "import_ssh_block", "plan_ssh_block",
           "save_ssh_block", "ssh_block_lines", "ssh_settings"]
