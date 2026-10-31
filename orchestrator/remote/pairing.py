#!/usr/bin/env python3
"""
The person's writers of a board's trust (consolidation stage C, c2-remote-trust; MCP design 2.7, decision 2; DECISIONS
round 10 Q5). Only the person's own channels call these: the GUI and the CLI, and the person's board and scan steps
behind them (orchestrator/runtime/device/person_steps.py, orchestrator/runtime/scan_steps.py). No module an AI can
reach imports this one: tests/test_remote_trust.py follows every import of the tool modules, the MCP server, Ditto and
the device service and fails when one reaches it, and tests/test_deploy_tools.py (WRITERS) and
tests/test_board_steps.py check the calls and the module name in that code.

- trust_presented_key(store, endpoint, key, *, via, audit, actor=""): stores a key the person confirmed in the
  framework's known_hosts (atomically, readable only by this user) and writes its audit row in the same step, so a key
  trusted before a login that then fails is audited too. PersonChannel(via, audit, actor) carries it for the
  deprecated DeploymentManager.trust_host, a thin delegate that trusts only with the person's channel (P5).
- first_use(store, check, *, ask, preset_fingerprint=None, via, audit, actor=""): the one decision of trust on first
  use behind `llmcf deploy run`, `llmcf scan --device` and the GUI's host-key question. A known key goes on, a changed
  key is refused, an unknown key is trusted only when it matches --trust-fingerprint or the person said yes (each
  channel asks in its own way, always with "No" as the default). Returns FirstUse(verdict, check).
- pair(client, state_dir): installs the framework's own SSH key on a board over a session the person logged in to with
  a one-time password (the fixed command of ssh_trust.install_public_key; its one value is the framework's own public
  key line).
- plan_replacement(store, record, new_endpoint=None, new_key=None) -> KeyReplacement and replace_key(store, plan, *,
  via, audit, actor=""): Q5, the person's change of a trusted board's mapping in the framework's known_hosts: a new
  host name or IP address (and port) and/or a new host key (pasted or read from the board, never typed). The plan holds
  the old and the new fingerprint for the person to compare; the change is written to the audit log before
  known_hosts changes (no record, no change), and it is refused when known_hosts changed since the plan was made.
- record_consent(audit, tool, arguments, detail, *, channel): a person's consent in the audit log (RuntimeError when
  it cannot be written: without its record there is no consent).

The primitives that write known_hosts and the board's authorized_keys (ssh_trust.trust_host_key, save_host_keys and
install_public_key) are looked up through orchestrator/utils/ssh_trust.py when they are called (tests replace them
there). Audit rows name a board by its device ID and fingerprint, never by its address (D0).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

from orchestrator.remote.address import Endpoint, valid_host, valid_port
from orchestrator.remote.trust import KeyCheck, TrustStore, device_id_for, host_entry, load_host_keys

_LOCK = ".known_hosts.lock"


def _open_audit(audit: Any) -> Any:
    """The audit log, or None: `audit` is the log itself or a function that opens it (called only when a row is
    written, so a step that writes nothing never opens it)."""
    if callable(audit) and not hasattr(audit, "record"):
        try:
            return audit()
        except Exception:  # noqa: BLE001 - the step itself is the person's; its row is written where possible
            return None
    return audit


def _lock(store: TrustStore):
    from orchestrator.utils.file_lock import file_lock
    store.state_dir.mkdir(parents=True, exist_ok=True)
    return file_lock(store.state_dir / _LOCK)


# --- trust on first use ------------------------------------------------------------------------------------------

def trust_presented_key(store: TrustStore, endpoint: Endpoint, key: Any, *, via: str, audit: Any,
                        actor: str = "") -> None:
    """Stores a host key the person confirmed (compared with the fingerprint shown, or given as --trust-fingerprint)
    for `endpoint` in the framework's known_hosts and writes the trust to the audit log in the same step (actor `actor`
    or `via`, confirm channel framework-<via>). The row is written whenever an audit log can be opened (`audit`: the log
    or a function that opens it); the trust itself never fails on its bookkeeping, as the board's record never did."""
    from orchestrator.utils import ssh_trust   # looked up now: tests replace trust_host_key there
    with _lock(store):
        ssh_trust.trust_host_key(store.state_dir, endpoint.host, endpoint.port, key)
    log = _open_audit(audit)
    if log is None:
        return
    fp = ssh_trust.fingerprint(key)
    try:
        log.record(actor or via, "host_key_trusted", "external", "ask", "ok",
                   arguments={"device_id": device_id_for(fp), "fingerprint": fp, "key_type": key.get_name()},
                   approved=True, extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
    except Exception:  # noqa: BLE001 - logged by the audit log itself
        pass


# The person's own channels: the only `via` a PersonChannel takes.
PERSON_CHANNELS = ("cli", "gui")


@dataclass(frozen=True)
class PersonChannel:
    """The person's channel as the deprecated DeploymentManager.trust_host takes it (DECISIONS round 22, P5): `via`
    (cli or gui), the audit log or a function that opens it, and the actor of its rows. Only the person's channels
    make one: no module an AI can reach imports this module (tests/test_remote_trust.py). trust_presented_key() is the
    one implementation above, with its audit row."""
    via: str
    audit: Any = None
    actor: str = ""

    def __post_init__(self) -> None:
        if self.via not in PERSON_CHANNELS:
            raise ValueError(f"a person's channel is one of {', '.join(PERSON_CHANNELS)}")

    def trust_presented_key(self, store: TrustStore, endpoint: Endpoint, key: Any) -> None:
        trust_presented_key(store, endpoint, key, via=self.via, audit=self.audit, actor=self.actor)


@dataclass(frozen=True)
class FirstUse:
    """What trust on first use decided: `verdict` known (the key was trusted before), trusted (the person trusted it
    now), changed (another key than the trusted one: refused), mismatch (not the --trust-fingerprint given) or declined
    (the person said no, or there was nobody to ask)."""
    verdict: str
    check: KeyCheck

    @property
    def trusted(self) -> bool:
        return self.verdict in ("known", "trusted")


def first_use(store: TrustStore, check: KeyCheck, *, ask: Optional[Callable[[KeyCheck], bool]],
              preset_fingerprint: Optional[str] = None, via: str, audit: Any = None, actor: str = "") -> FirstUse:
    """The one decision of trust on first use (CLI and GUI): known goes on, changed is refused; an unknown key is
    trusted (trust_presented_key, audited) only when it has `preset_fingerprint` or, without one, when `ask(check)`
    returns True. `ask` None: nobody can be asked (no terminal), so an unknown key is declined."""
    if check.status == "known":
        return FirstUse("known", check)
    if check.status != "unknown":
        return FirstUse("changed", check)
    if preset_fingerprint:
        if preset_fingerprint != check.fingerprint:
            return FirstUse("mismatch", check)
    elif ask is None or not ask(check):
        return FirstUse("declined", check)
    trust_presented_key(store, check.endpoint, check.key, via=via, audit=audit, actor=actor)
    return FirstUse("trusted", check)


# --- pairing ----------------------------------------------------------------------------------------------------

def pair(client: Any, state_dir: Path, bundle: Any = None, *, allow_unrestricted: bool = False) -> str:
    """Installs the framework's own SSH key on the board over `client`, a session the person logged in to with a
    one-time password (used for that connection only, never stored): ssh_trust.install_public_key, with `bundle`
    (runtime/device/board_ops.bundle()) as llmcf's command gate and the key's restricted line. Returns "restricted" or
    "unrestricted"; GateUnavailable (a HostKeyError) when the board cannot take the gate and the person did not allow
    an unrestricted key, HostKeyError when the key cannot be installed."""
    from orchestrator.utils import ssh_trust   # looked up now: tests replace install_public_key and client_key there
    line = ssh_trust.client_key(Path(state_dir))[1]
    if bundle is None:
        ssh_trust.install_public_key(client, line)
        return "unrestricted"
    return ssh_trust.install_public_key(client, line, bundle, allow_unrestricted=allow_unrestricted)


# --- Q5: the person's change of a board's address and host key -------------------------------------------------

class KeyReplaceError(ValueError):
    """A change of a board's mapping that cannot be made; `kind`: address (not a host name or IP address, or no port),
    unchanged (nothing would change), no_key (known_hosts no longer holds the board's key: give the new one), stale
    (known_hosts changed since the change was shown: look again), audit (the audit log could not be written: nothing was
    changed), not_saved (known_hosts could not be written)."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class KeyReplacement:
    """A change of one trusted board's mapping, shown to the person before it is made: the address and the host key
    now (`old`, `old_key_type`, `old_fingerprint`) and after the change (`new`, `new_key`, `new_fingerprint`).
    `displaced` is the fingerprint of another key of that type that known_hosts holds for the new address ("" when
    none): it is replaced too."""
    old: Endpoint
    new: Endpoint
    old_key_type: str
    old_fingerprint: str
    new_key: Any
    new_fingerprint: str
    displaced: str = ""

    @property
    def new_key_type(self) -> str:
        return self.new_key.get_name()

    @property
    def address_changed(self) -> bool:
        return (self.old.host, self.old.port) != (self.new.host, self.new.port)

    @property
    def key_changed(self) -> bool:
        return self.old_fingerprint != self.new_fingerprint

    @property
    def old_device_id(self) -> str:
        return device_id_for(self.old_fingerprint)

    @property
    def new_device_id(self) -> str:
        return device_id_for(self.new_fingerprint)


def plan_replacement(store: TrustStore, record: Any, new_endpoint: Optional[Endpoint] = None,
                     new_key: Any = None) -> KeyReplacement:
    """The change of the trusted board `record` (host, port, key_type, fingerprint) to `new_endpoint` (None: its
    address stays) and `new_key` (None: its key stays, taken from known_hosts). Reads only the file; KeyReplaceError
    when it cannot be made."""
    from orchestrator.utils import ssh_trust
    old = Endpoint(record.host, int(record.port))
    new = new_endpoint if new_endpoint is not None else old
    if not valid_host(new.host) or not valid_port(new.port):
        raise KeyReplaceError("The new address is not a host name or an IP address with a port from 1 to 65535.",
                              "address")
    if new_key is None:
        stored = store.keys_at(old).get(record.key_type)
        if stored is None or ssh_trust.fingerprint(stored) != record.fingerprint:
            raise KeyReplaceError("The framework's known_hosts no longer holds the board's trusted key for its "
                                  "address; paste the board's host key or read it from the board.", "no_key")
        new_key = stored
    new_fp = ssh_trust.fingerprint(new_key)
    held = store.keys_at(new).get(new_key.get_name())
    displaced = ""
    if held is not None:
        held_fp = ssh_trust.fingerprint(held)
        if held_fp != new_fp and not (new == old and held_fp == record.fingerprint):
            displaced = held_fp
    plan = KeyReplacement(old=old, new=new, old_key_type=record.key_type, old_fingerprint=record.fingerprint,
                          new_key=new_key, new_fingerprint=new_fp, displaced=displaced)
    if not plan.address_changed and not plan.key_changed:
        raise KeyReplaceError("Nothing would change: the board already has this address and this host key.",
                              "unchanged")
    return plan


def _rebuilt(keys: Any, drop: Set[Tuple[str, str]]) -> Any:
    """A copy of `keys` (paramiko HostKeys) without the keys `drop` names as (known_hosts entry, key type)."""
    from orchestrator.utils import ssh_trust
    paramiko = ssh_trust._require_paramiko()
    copy = paramiko.HostKeys()
    for name in keys.keys():
        for key_type, key in keys[name].items():
            if (name, key_type) not in drop:
                copy.add(name, key_type, key)
    return copy


def replace_key(store: TrustStore, plan: KeyReplacement, *, via: str, audit: Any, actor: str = "") -> KeyReplacement:
    """Makes the change the person confirmed (Q5): the board's old key leaves its old known_hosts entry, and the new
    key (the same one for a new address) is stored for the new address, replacing a key of that type held there before
    (plan.displaced). Written to the audit log first: KeyReplaceError(audit) and nothing changes when it cannot be
    written. Refused (stale) when known_hosts changed since the plan was made. Returns the plan."""
    from orchestrator.utils import ssh_trust   # looked up now: tests replace save_host_keys and fingerprint there
    log = _open_audit(audit)
    if log is None or not callable(getattr(log, "record", None)):
        raise KeyReplaceError("The change could not be written to the audit log; nothing was changed.", "audit")
    old_entry, new_entry = host_entry(plan.old.host, plan.old.port), host_entry(plan.new.host, plan.new.port)
    arguments = {"old_device_id": plan.old_device_id, "new_device_id": plan.new_device_id,
                 "old_fingerprint": plan.old_fingerprint, "new_fingerprint": plan.new_fingerprint,
                 "new_key_type": plan.new_key_type, "address_changed": plan.address_changed,
                 "replaced_other_key": plan.displaced}
    with _lock(store):
        keys = load_host_keys(store.state_dir)
        old_now = (keys.lookup(old_entry) or {}).get(plan.old_key_type)
        new_now = (keys.lookup(new_entry) or {}).get(plan.new_key_type)
        stale = old_now is not None and ssh_trust.fingerprint(old_now) != plan.old_fingerprint
        if new_now is not None and plan.address_changed:
            stale = stale or ssh_trust.fingerprint(new_now) not in (plan.displaced, plan.new_fingerprint)
        if stale:
            raise KeyReplaceError("The framework's known_hosts changed since the change was shown; nothing was "
                                  "changed. Look at the board again.", "stale")
        try:
            log.record(actor or via, "host_key_replaced", "external", "ask", "ok", arguments=arguments,
                       approved=True, extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
        except Exception as e:  # noqa: BLE001 - without its record there is no change
            raise KeyReplaceError(f"The change could not be written to the audit log ({e.__class__.__name__}); "
                                  "nothing was changed.", "audit") from None
        # the board's old key leaves its old entry; the new address keeps no other key of the new key's type
        changed = _rebuilt(keys, {(old_entry, plan.old_key_type), (new_entry, plan.new_key_type)})
        changed.add(new_entry, plan.new_key_type, plan.new_key)
        try:
            ssh_trust.save_host_keys(store.state_dir, changed)
        except OSError as e:
            try:
                log.record(actor or via, "host_key_replaced", "external", "ask", "error", arguments=arguments,
                           approved=True, detail=f"known_hosts could not be written ({e.__class__.__name__})",
                           extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
            except Exception:  # noqa: BLE001 - logged by the audit log itself
                pass
            raise KeyReplaceError(f"The framework's known_hosts could not be written ({e.__class__.__name__}).",
                                  "not_saved") from None
    return plan


# --- consents ----------------------------------------------------------------------------------------------------

def record_consent(audit: Any, tool: str, arguments: Dict[str, Any], detail: str, *, channel: str) -> None:
    """Writes a person's consent to the audit log (actor "user"); RuntimeError when it cannot be written: without its
    record there is no consent, and the caller changes nothing."""
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("no audit log")
    audit.record("user", tool, "external", "ask", "ok", arguments=arguments, approved=True, detail=detail,
                 extra={"answer": "accepted", "confirm_channel": channel})


__all__ = ["FirstUse", "KeyReplaceError", "KeyReplacement", "PERSON_CHANNELS", "PersonChannel", "first_use", "pair",
           "plan_replacement", "record_consent", "replace_key", "trust_presented_key"]
