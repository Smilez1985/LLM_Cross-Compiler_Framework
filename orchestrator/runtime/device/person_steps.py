#!/usr/bin/env python3
"""
The person's own board steps (MCP design 2.7, W1' of 2026-10-16): the one code path behind `llmcf deploy devices
list|add|remove`, `llmcf device list|settings` and the board contacts of `llmcf device runtime prune|deploy|verify`,
and behind the GUI's Boards > Boards and Devices... window. Both are the person's channels. No tool and no AI reaches
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
from typing import Any, Callable, Dict, List, Optional, Tuple

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


class BoardStepError(WithSteps, RuntimeError):
    """A board step that cannot be done. `kind` names the case (no_paramiko, unreachable, host_key_changed,
    not_trusted, other_device, login_failed, key_install, host_user, label, not_known, no_systemd, sudo_declined,
    not_saved, damaged, no_user, no_address); `reason` is the SSH reason code when the board could not be reached or
    logged in to; `error` the exception behind it."""

    def __init__(self, message: str, kind: str, reason: str = "", error: Optional[BaseException] = None,
                 **facts: Any):
        super().__init__(message)
        self.kind, self.reason, self.error, self.facts = kind, reason, error, facts


def _note(state_dir: Path, device_id: str, step: str, code: str) -> None:
    """Mojo's record of this contact with the board (W1); never fails the step."""
    if not step:
        return
    try:
        from orchestrator.runtime.device.trusted import TrustedDevices
        TrustedDevices(Path(state_dir)).note_reach(device_id, step, code)
    except Exception:  # noqa: BLE001 - bookkeeping never changes what the step does
        pass


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
    from orchestrator.utils import ssh_trust
    if ssh_trust.paramiko is None:
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
    host:port: one key read, no login."""
    from orchestrator.utils import ssh_trust
    _need_paramiko()
    try:
        key = ssh_trust.fetch_host_key(host, port)
        status = ssh_trust.check_host_key(Path(state_dir), host, port, key)
    except Exception as e:  # unreachable, no SSH server, a broken known_hosts file
        raise unreachable_error(e, host, port) from None
    return key, status, ssh_trust.fingerprint(key)


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
    from orchestrator.utils import ssh_trust
    try:  # paired: the framework's own key logs in
        client = ssh_trust.connect(host, user, Path(state_dir), port=port, password=None)
        client.close()
        paired = True
    except Exception:  # noqa: BLE001 - not paired yet: the record says so, and deploy_to_device refuses it
        paired = False
    device = record_trusted_device(Path(state_dir), host, port, user, key, via=via, paired=paired, label=label,
                                   audit=audit, actor=actor)
    return device, paired


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

def record_consent(audit: Any, tool: str, arguments: Dict[str, Any], detail: str, *, channel: str) -> None:
    """Writes a person's consent to the audit log (actor "user"); RuntimeError when it cannot be written: without its
    record there is no consent, and the caller changes nothing."""
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("no audit log")
    audit.record("user", tool, "external", "ask", "ok", arguments=arguments, approved=True, detail=detail,
                 extra={"answer": "accepted", "confirm_channel": channel})


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
                   on_client: Optional[Callable[[Any], None]] = None) -> Tuple[Any, Any]:
    """(SSH client, host key) of the stored board `device_id` at host:port as `user`. Its host key must be the trusted
    one the board was probed with (the device ID derives from it), so a step never reaches another machine at the same
    address. A `password` is used once to install the framework's key and is never stored. `step` names the contact
    for Mojo's record of the board (W1). The caller closes the client (`on_client` gets it at once, so a Cancel can
    close it)."""
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    state_dir = Path(state_dir)
    _need_paramiko()
    try:
        key = ssh_trust.fetch_host_key(host, port)
        status = ssh_trust.check_host_key(state_dir, host, port, key)
    except Exception as e:  # unreachable, no SSH server, a broken known_hosts file
        raise unreachable_error(e, host, port, state_dir=state_dir, device_id=device_id, step=step) from None
    if status == "changed":
        _note(state_dir, device_id, step, reasons.HOST_KEY_CHANGED)
        raise BoardStepError(str(ssh_trust.HostKeyChanged(host, port)), "host_key_changed",
                             reason=reasons.HOST_KEY_CHANGED)
    if status != "known":
        raise BoardStepError(f"{host}:{port} is not trusted yet: probe it first with llm-cli scan --device {host} "
                             "--user USER.", "not_trusted")
    if device_id_for_host_key(ssh_trust.fingerprint(key)) != device_id:
        raise BoardStepError(f"{host}:{port} answers with the host key of another device than {device_id}; probe it "
                             "again before deploying to it.", "other_device")
    try:
        client = ssh_trust.connect(host, user, state_dir, port=port, password=password)
    except Exception as e:  # authentication, network, a host key that changed meanwhile
        code = reasons.classify(e)
        if code not in (None, reasons.AUTH_FAILED):
            raise unreachable_error(e, host, port, state_dir=state_dir, device_id=device_id, step=step) from None
        if code == reasons.AUTH_FAILED:
            _note(state_dir, device_id, step, reasons.AUTH_FAILED)
        # none of the four reasons: shown as it is, never relabelled as a refused login
        raise BoardStepError(f"SSH login to {user}@{host} failed: {e}", "login_failed", reason=code or "",
                             error=e) from None
    if on_client is not None:
        on_client(client)
    if password:
        try:
            ssh_trust.install_public_key(client, ssh_trust.client_key(state_dir)[1])
        except ssh_trust.HostKeyError as e:
            client.close()
            raise BoardStepError(str(e), "key_install", error=e) from None
    _note(state_dir, device_id, step, "")   # reached and logged in: a recorded problem ends
    return client, key


def remember_board(state_dir: Path, host: str, port: int, user: str, key: Any, *, via: str, audit: Any,
                   actor: str):
    """After a login to a stored board with the framework's key (a deploy; a verify only checks): its trusted record,
    so AI tools may name it (each deploy is still confirmed by the person). Never fails the caller's work."""
    from orchestrator.runtime.device.trusted import record_trusted_device
    return record_trusted_device(Path(state_dir), host, port, user, key, via=via, paired=True, audit=audit,
                                 actor=actor)


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


__all__ = ["BoardPrune", "BoardStepError", "DAMAGED_QUESTION", "SUDO_QUESTION", "SYSTEMD_CONSENT",
           "UNTRUSTED_CONSENT", "UNTRUSTED_QUESTION", "add_trusted_board", "board_address", "boards_overview",
           "change_settings", "check_board_entry", "connect_stored", "device_rows", "list_board_builds",
           "load_settings", "not_known_error", "previous_build", "read_host_key", "record_consent", "remember_board",
           "remove_board_builds", "remove_trust", "settings_to_change", "trusted_rows", "unreachable_error"]
