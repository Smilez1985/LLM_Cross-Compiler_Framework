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
- The pairing during the hardware probe, one core for both ways (the probe kit's result file and the short-code PAKE):
  create_offer(state_dir, *, public_line, signers, pem, via, audit, way) -> PairingOffer (a one-time nonce, 24 hours),
  offer_problem(state_dir, nonce, way=), PairingResult (from_profile of a probe file's PAIR_* block, or built by the
  PAKE session) and complete_pairing(state_dir, result, *, via, audit, actor, probe_id, label) -> PairingDone, which
  pins the board's host keys (remote/trust.py PAIRED_FILE; first_use then trusts them without the question) and marks
  the board paired with its key tier. PairingRefused(kind) for everything it does not do; only cli and gui pair.

The primitives that write known_hosts and the board's authorized_keys (ssh_trust.trust_host_key, save_host_keys and
install_public_key) are looked up through orchestrator/utils/ssh_trust.py when they are called (tests replace them
there). Audit rows name a board by its device ID and fingerprint, never by its address (D0).
"""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
                        actor: str = "", pinned: bool = False) -> None:
    """Stores a host key the person confirmed (compared with the fingerprint shown, or given as --trust-fingerprint)
    for `endpoint` in the framework's known_hosts and writes the trust to the audit log in the same step (actor `actor`
    or `via`, confirm channel framework-<via>). The row is written whenever an audit log can be opened (`audit`: the log
    or a function that opens it); the trust itself never fails on its bookkeeping, as the board's record never did.
    `pinned`: a completed pairing pinned the key (the row's answer is "pinned", not the person's "accepted")."""
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
                   approved=True, extra={"confirm_channel": f"framework-{via}",
                                         "answer": "pinned" if pinned else "accepted"})
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
    now), paired (a completed pairing during the hardware probe pinned this key: trusted without a question), changed
    (another key than the trusted one: refused), mismatch (not the --trust-fingerprint given) or declined (the person
    said no, or there was nobody to ask)."""
    verdict: str
    check: KeyCheck

    @property
    def trusted(self) -> bool:
        return self.verdict in ("known", "trusted", "paired")


def first_use(store: TrustStore, check: KeyCheck, *, ask: Optional[Callable[[KeyCheck], bool]],
              preset_fingerprint: Optional[str] = None, via: str, audit: Any = None, actor: str = "") -> FirstUse:
    """The one decision of trust on first use (CLI and GUI): known goes on, changed is refused; an unknown key is
    trusted (trust_presented_key, audited) when a completed pairing pinned it (TrustStore.pinned: the person carried
    the board's fingerprints home in the probe's result file, or the PAKE session exchanged them; no question then,
    verdict "paired"), when it has `preset_fingerprint` or, without either, when `ask(check)` returns True. A
    `preset_fingerprint` that differs is refused even for a pinned key. `ask` None: nobody can be asked (no terminal),
    so an unknown key that is not pinned is declined."""
    if check.status == "known":
        return FirstUse("known", check)
    if check.status != "unknown":
        return FirstUse("changed", check)
    if preset_fingerprint and preset_fingerprint != check.fingerprint:
        return FirstUse("mismatch", check)
    pinned = store is not None and store.pinned(check.fingerprint, check.key_type) is not None
    if not pinned and not preset_fingerprint and (ask is None or not ask(check)):
        return FirstUse("declined", check)
    trust_presented_key(store, check.endpoint, check.key, via=via, audit=audit, actor=actor, pinned=pinned)
    return FirstUse("paired" if pinned else "trusted", check)


# --- pairing ----------------------------------------------------------------------------------------------------

class PairOutcome(str):
    """What pair() did: the word install_public_key answers ("restricted", "dropbear", "home", "unrestricted") or
    "password" (no key, the person declined every key tier), with `tier` (remote/key_tiers.py) and, after the switch
    to OpenSSH, the board's new `endpoint` and host `key` (its sshd answers on another port with keys of its own)."""
    tier: str
    endpoint: Any
    key: Any

    def __new__(cls, word: str, tier: str = "", endpoint: Any = None, key: Any = None):
        obj = super().__new__(cls, word)
        obj.tier = tier or {"restricted": "root"}.get(word, word)
        obj.endpoint, obj.key = endpoint, key
        return obj


class TierChooser:
    """The person's channel for a board that cannot take the root-owned key line (the CLI's prompts, the GUI's
    questions; never an AI: no module an AI can reach imports this one). Every answer defaults to "No"."""

    def choose(self, offer: Any) -> str:
        """One of offer.choices (key_tiers.CHOICES); "password" when the person takes none."""
        return "password"

    def confirm(self, title: str, lines: Any) -> bool:
        """The person's yes to the plan in `lines` (the switch to OpenSSH, the removal of Dropbear)."""
        return False

    def trust_key(self, check: Any) -> bool:
        """The person's yes to the new sshd's host key (trust on first use, as for a new board)."""
        return False

    def tell(self, lines: Any) -> None:
        """A plain report for the person."""


def _reason_text(word: str) -> str:
    from orchestrator.remote.gate import SETUP_REASONS
    text = SETUP_REASONS.get(word, word)
    return text[:1].upper() + text[1:] + "."


def _step(audit: Any, via: str, tool: str, arguments: Dict[str, Any], status: str = "ok", detail: str = "") -> None:
    """One step of the key tiers in the audit log (actor `via`, fixed words only: no host, no account, no path)."""
    log = _open_audit(audit)
    if log is None or not callable(getattr(log, "record", None)):
        return
    try:
        log.record(via or "user", tool, "external", "ask", status, arguments=arguments, approved=status == "ok",
                   detail=detail, extra={"confirm_channel": f"framework-{via or 'user'}"})
    except Exception:  # noqa: BLE001 - logged by the audit log itself
        pass


def pair(client: Any, state_dir: Path, bundle: Any = None, *, allow_unrestricted: bool = False,
         password: str = "", chooser: Optional[TierChooser] = None, mode: str = "private", audit: Any = None,
         via: str = "", host: str = "", port: int = 22, user: str = "", switch: bool = True,
         access: Any = None) -> str:
    """Installs the framework's own SSH key on the board over `client`, a session the person logged in to with a
    one-time password (used for that connection only, never stored): ssh_trust.install_public_key, with `bundle`
    (runtime/device/board_ops.bundle()) as llmcf's command gate and the key's restricted line. `password`: the person's
    one-time password, which a root step gives to sudo -S once (never stored); without it the account's sudo -n.

    A board that cannot take the root-owned key line (Dropbear, no sudo, an old ssh-keygen without openssl) gets the
    person's choice through `chooser` (key_tiers.offer: the switch to OpenSSH, the Dropbear tier, the home gate, or
    password login; the weaker tiers only in private `mode`): each answer is written to `audit` (the log or a function
    that opens it) as the person's (`via`), and the framework never takes a weaker tier without that yes. The switch
    needs the board's `host`, SSH `port` and `user` (the new sshd is reached on its own port). Without a chooser, or
    when the board offers nothing but the password, GateUnavailable (a HostKeyError) with the choices in its text.
    `switch` False leaves the switch out of the choices (a stored board keeps its address and host key there).
    `access` is the board's stored probe's access facts (runtime/profile.py HardwareProfile.access(), None without a
    stored probe): its proposal (key_tiers.proposal) is marked among the choices only when what the board says now
    offers it (key_tiers.with_proposal); the root step on the board and its facts read now decide, never the probe
    alone. Returns a PairOutcome (a str: "restricted", "dropbear", "home", "unrestricted" or "password")."""
    from orchestrator.remote import key_tiers
    from orchestrator.utils import ssh_trust   # looked up now: tests replace install_public_key and client_key there
    line = ssh_trust.client_key(Path(state_dir))[1]
    if bundle is None:
        ssh_trust.install_public_key(client, line)
        return PairOutcome("unrestricted")
    mode = key_tiers.normal_mode(mode)
    try:
        return PairOutcome(ssh_trust.install_public_key(client, line, bundle, allow_unrestricted=allow_unrestricted,
                                                        password=password, mode=mode))
    except ssh_trust.GateUnavailable as e:
        offer = e.offer
        if chooser is None or offer is None:
            raise
    if not switch and key_tiers.CHOICE_OPENSSH in offer.choices:
        from dataclasses import replace
        offer = replace(offer, choices=tuple(c for c in offer.choices if c != key_tiers.CHOICE_OPENSSH))
    offer = key_tiers.with_proposal(offer, access)   # the stored probe proposes; the board's facts now decide
    facts = offer.facts.as_dict()
    _step(audit, via, "board_key_tier_offered", {"reason": offer.reason, "mode": mode, "choices": list(offer.choices),
                                                 "refused": list(offer.refused), "proposed": offer.proposed,
                                                 "probe_disagrees": bool(offer.note), **facts})
    if offer.refused:
        chooser.tell([_reason_text("enterprise")])
    if offer.note:
        chooser.tell([offer.note])
    choice = chooser.choose(offer)
    problem = key_tiers.choice_problem(choice, offer, mode)
    _step(audit, via, "board_key_tier_chosen", {"choice": choice, "mode": mode}, "error" if problem else "ok",
          problem)
    if problem:
        raise ssh_trust.GateUnavailable("enterprise" if not key_tiers.allowed(choice, mode) else "declined", offer)
    if choice == key_tiers.TIER_PASSWORD:
        chooser.tell([_reason_text("declined")])
        return PairOutcome("password")
    if choice in (key_tiers.TIER_DROPBEAR, key_tiers.TIER_HOME):
        try:
            got = ssh_trust.install_public_key(client, line, bundle, password=password, tier=choice, mode=mode)
        except ssh_trust.GateUnavailable as failed:
            _step(audit, via, "board_key_tier_installed", {"tier": choice}, "error", failed.reason)
            raise
        _step(audit, via, "board_key_tier_installed", {"tier": choice})
        chooser.tell([key_tiers.TIER_TEXTS[choice] + ".", key_tiers.UPGRADE_TEXT])
        return PairOutcome(got, tier=choice)
    return _switch_to_openssh(client, state_dir, bundle, line, offer, chooser, password=password, mode=mode,
                              audit=audit, via=via, host=host, port=port, user=user)


def _switch_to_openssh(client: Any, state_dir: Path, bundle: Any, line: str, offer: Any, chooser: TierChooser, *,
                       password: str, mode: str, audit: Any, via: str, host: str, port: int, user: str) -> str:
    """The switch to OpenSSH the person chose (remote/openssh_switch.py): its plan first and the person's yes to it,
    then OpenSSH installed if it is not there (sudo once), sshd checked and started on its own port, the current
    session kept until the new sshd answers, its host key trusted by the person, the root-owned key line over a new
    login there, and only after a separate yes Dropbear off. Every step in the audit log; GateUnavailable when a step
    fails (nothing of Dropbear changed then)."""
    from orchestrator.remote import openssh_switch, transport
    from orchestrator.remote.address import Endpoint, valid_host
    from orchestrator.remote.trust import TrustStore
    from orchestrator.utils import ssh_trust
    try:
        plan = openssh_switch.plan(offer.facts)
    except openssh_switch.SwitchUnavailable as e:
        _step(audit, via, "board_openssh_plan", {"facts": offer.facts.as_dict()}, "error", e.reason)
        raise ssh_trust.GateUnavailable(e.reason, offer, switch=True) from None
    if not valid_host(host) or not user:
        raise ssh_trust.GateUnavailable("unknown", offer)
    if not chooser.confirm("switch", plan.lines):
        _step(audit, via, "board_openssh_plan", plan.as_dict(), "error", "declined")
        raise ssh_trust.GateUnavailable("declined", offer)
    _step(audit, via, "board_openssh_plan", plan.as_dict())
    ok, reason, state = openssh_switch.install(transport.ParamikoTransport(client).run, plan, password)
    _step(audit, via, "board_openssh_installed", {**plan.as_dict(), "state": state}, "ok" if ok else "error", reason)
    if not ok:
        raise ssh_trust.GateUnavailable(reason, offer, switch=True)
    # the current session (`client`) stays open until the new sshd answers on its port
    if not openssh_switch.wait_until_answers(host, plan.port):
        _step(audit, via, "board_openssh_answers", {"port": plan.port}, "error", "no-answer")
        raise ssh_trust.GateUnavailable("no-answer", offer, switch=True)
    endpoint = Endpoint(host, plan.port)
    store = TrustStore(Path(state_dir))
    key = ssh_trust.fetch_host_key(host, plan.port)
    outcome = first_use(store, store.check(endpoint, key), ask=chooser.trust_key, via=via or "cli", audit=audit,
                        actor=via)
    _step(audit, via, "board_openssh_host_key", {"port": plan.port, "verdict": outcome.verdict},
          "ok" if outcome.trusted else "error")
    if not outcome.trusted:
        raise ssh_trust.GateUnavailable("host-key", offer, switch=True)
    fresh = ssh_trust.connect(host, user, Path(state_dir), port=plan.port, password=password)
    try:
        try:
            got = ssh_trust.install_public_key(fresh, line, bundle, password=password, mode=mode)
        except ssh_trust.GateUnavailable as failed:   # OpenSSH runs already: the person reads the switch's text
            _step(audit, via, "board_key_tier_installed", {"tier": "root", "port": plan.port}, "error", failed.reason)
            raise ssh_trust.GateUnavailable(failed.reason, offer, switch=True) from None
        _step(audit, via, "board_key_tier_installed", {"tier": "root", "port": plan.port})
        report = [f"OpenSSH answers on port {plan.port} and the framework's key is paired there with the root-owned "
                  "key line."]
        going = plan.removable()
        how = ("Its service is stopped and switched off; " + openssh_switch.ALPINE_NOTE + "."
               if plan.manager == "apk" else
               "Its service is stopped and disabled first, then the board's package manager removes only the "
               "packages that provide it" + (f" ({', '.join(going)})" if going else "")
               + "; nothing is purged or autoremoved.")
        if chooser.confirm("dropbear", [
                ("Turn Dropbear off now?" if plan.manager == "apk" else "Remove Dropbear now?")
                + f" The board is then reached only through OpenSSH on port {plan.port}; your current session ends "
                "when Dropbear stops.", how]):
            done = openssh_switch.remove_dropbear(transport.ParamikoTransport(fresh).run, password)
            _step(audit, via, "board_dropbear_disabled", {"dietpi": plan.dietpi, **done.as_dict()},
                  "ok" if done.ok else "error", done.reason)
            report.append(openssh_switch.removal_text(done))
        else:
            _step(audit, via, "board_dropbear_disabled", {"dietpi": plan.dietpi}, "error", "declined")
            report.append("Dropbear keeps running next to OpenSSH; you can remove it later.")
        chooser.tell(report)
    finally:
        try:
            fresh.close()
        except Exception:  # noqa: BLE001 - the pairing is done
            pass
    return PairOutcome(got, tier="root", endpoint=endpoint, key=key)


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


# --- the pairing during the hardware probe: one core, two ways ---------------------------------------------------
#
# Finding a board is not trusting it. A pairing offer (create_offer) is made only by the person (CLI or GUI): a random
# one-time nonce with the framework's PUBLIC key line and signer file, kept in the per-user folder (OFFERS_FILE,
# readable only by this user; the nonce itself only as its sha256). Way 1 carries it to the board in the probe kit
# (runtime/scan_steps.py export_probe_kit): the probe, run once with sudo, installs the root-owned key line and the
# signer files and writes the board's host key fingerprints and the nonce into its result file. Way 2 (the PAKE short
# code, the board's Rust tools) exchanges the same facts in an authenticated session. Both hand a PairingResult to
# complete_pairing, which checks the nonce against an open offer (single use, expires), writes the audit row, pins the
# host keys (PAIRED_FILE, which TrustStore.pinned reads: first_use then trusts that key without the question) and
# marks the board paired with its key tier. No secret travels: the result holds fingerprints and the nonce only.

OFFER_TTL_HOURS = 24
NONCE_RE = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
PAIR_STATES = ("installed", "skipped", "failed")
_REASON_RE = re.compile(r"^[a-z][a-z-]{0,23}$", re.ASCII)
_PAIR_LOCK = ".pairing.lock"
# The probe file keys of the pairing block (configs/hardware_probe_schema.yml, group pairing, never stored).
PAIR_KEYS = {"ed25519": "PAIR_HOSTKEY_ED25519", "ecdsa": "PAIR_HOSTKEY_ECDSA", "rsa": "PAIR_HOSTKEY_RSA"}
# What the board's own word for a pairing it did not do means, in the person's words.
BOARD_REASONS = {
    "not-root": "the probe did not run with sudo, so pairing was skipped (the hardware data is complete)",
    "no-kit": "the probe found no pairing files next to it",
    "opted-out": "pairing was switched off for this probe",
    "windows": "pairing of a Windows board is not there yet (the hardware data is complete)",
    "account": "the probe could not tell which board account to pair (run it with sudo from that account)",
    "no-host-keys": "the board has no SSH host key the probe could read",
}


def _now_stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(("llmcf-pairing-nonce:" + nonce).encode("ascii")).hexdigest()


class PairingRefused(ValueError):
    """A pairing that was not made; the message is for the person. `kind`: channel (not the person's channel),
    invalid (the result's pairing block is damaged), skipped / failed (the board did not pair: `reason` is its word),
    unknown (no offer with this nonce), expired, used (the offer was used already), audit (the audit log could not be
    written: nothing was pinned), no-key (the framework has no public key or signer to offer)."""

    def __init__(self, message: str, kind: str, reason: str = ""):
        super().__init__(message)
        self.kind, self.reason = kind, reason


@dataclass(frozen=True)
class PairingOffer:
    """A pairing offer: `nonce` (128 random bits as 32 hex characters, single use), the framework's public key line,
    its board signer file (allowed_signers line) and its PEM twin, when it was made and when it expires, and the way
    it is for (file: the probe kit; pake: the short code)."""
    nonce: str
    public_line: str
    signers: str
    pem: str
    created_at: str
    expires_at: str
    way: str = "file"


@dataclass(frozen=True)
class PairingResult:
    """What a board sends back for a pairing (the probe's result file, or the PAKE session): its state (installed,
    skipped, failed) and the board's own reason word, the offer's nonce, the board's SSH host key fingerprints by
    family (ed25519, ecdsa, rsa: SHA256 as ssh-keygen -lf prints them), a few facts of the board from the probe
    (`board`: BOARD_MODEL, SOC_MODEL, ARCH, OS_ID when known) and the way and the key tier."""
    state: str
    nonce: str = ""
    host_keys: Tuple[Tuple[str, str], ...] = ()
    reason: str = ""
    board: Tuple[Tuple[str, str], ...] = ()
    way: str = "file"
    tier: str = "root"

    @classmethod
    def from_profile(cls, profile: Any) -> Optional["PairingResult"]:
        """The pairing block of a parsed probe file (HardwareProfile: its PAIR_* keys, already checked by the probe
        schema), or None when the file has none (pairing off, or an older probe)."""
        state = profile.get("PAIR_STATE")
        if not state:
            return None
        keys = tuple((family, profile.get(key)) for family, key in PAIR_KEYS.items() if profile.get(key))
        board = tuple((k, profile.get(k)) for k in ("BOARD_MODEL", "SOC_MODEL", "ARCH", "OS_ID") if profile.get(k))
        return cls(state=state, nonce=profile.get("PAIR_NONCE") or "", host_keys=keys,
                   reason=profile.get("PAIR_REASON") or "", board=board)

    @property
    def fingerprints(self) -> Dict[str, str]:
        return dict(self.host_keys)

    @property
    def primary_fingerprint(self) -> str:
        """The fingerprint the board is known by (the family an SSH client prefers first)."""
        from orchestrator.remote.trust import FAMILY_ORDER
        fps = self.fingerprints
        return next((fps[f] for f in FAMILY_ORDER if f in fps), "")

    @property
    def device_id(self) -> str:
        """The board's device ID from its preferred host key ("" without one)."""
        fp = self.primary_fingerprint
        return device_id_for(fp) if fp else ""

    def problem(self) -> str:
        """Why this result cannot complete a pairing ("" when it can)."""
        from orchestrator.remote.key_tiers import TIERS
        from orchestrator.remote.trust import FINGERPRINT, HOST_KEY_FAMILIES, PAIRING_WAYS
        if self.state not in PAIR_STATES:
            return "the pairing state is not one the framework knows"
        if self.reason and not _REASON_RE.fullmatch(self.reason):
            return "the board's reason is not a plain word"
        if self.state != "installed":
            return ""
        if not NONCE_RE.fullmatch(self.nonce or ""):
            return "the pairing nonce is missing or not 32 hex characters"
        if not self.host_keys:
            return "the board sent no host key fingerprint"
        families = [f for f, _ in self.host_keys]
        if len(set(families)) != len(families) or any(f not in HOST_KEY_FAMILIES for f in families):
            return "the host key fingerprints are not one per key family"
        if any(not FINGERPRINT.fullmatch(fp or "") for _, fp in self.host_keys):
            return "a host key fingerprint is not an SHA256 fingerprint"
        if self.way not in PAIRING_WAYS or self.tier not in TIERS or self.tier in ("password", "unrestricted"):
            return "the way or the key tier of the pairing is not one the framework pairs with"
        return ""


@dataclass(frozen=True)
class PairingDone:
    """A completed pairing: the board's device ID (from its preferred host key), its pinned fingerprints by family,
    the key tier, the way, the stored probe it came with ("" for none) and when."""
    device_id: str
    fingerprints: Dict[str, str]
    tier: str
    way: str
    probe_id: str
    paired_at: str


def _person_channel(via: str) -> None:
    if via not in PERSON_CHANNELS:
        raise PairingRefused("Only you can pair a board, in the CLI or the GUI; an AI cannot.", "channel")


def _load(path: Path, key: str) -> list:
    from orchestrator.remote.trust import _read_json
    data = _read_json(path)
    rows = data.get(key) if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save(path: Path, key: str, rows: list) -> None:
    from orchestrator.utils.atomic import write_private
    write_private(path, json.dumps({"version": 1, key: rows}, indent=2, sort_keys=True) + "\n")


def _pair_lock(state_dir: Path):
    from orchestrator.utils.file_lock import file_lock
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    return file_lock(Path(state_dir) / _PAIR_LOCK)


def create_offer(state_dir: Path, *, public_line: str, signers: str, pem: str = "", via: str, audit: Any = None,
                 actor: str = "", way: str = "file", ttl_hours: int = OFFER_TTL_HOURS,
                 now: Optional[datetime] = None) -> PairingOffer:
    """A new pairing offer, made only by the person (`via` cli or gui; PairingRefused channel otherwise): 128 random
    bits as the nonce, the framework's public key line and signer file, valid for `ttl_hours` (24 by default). It is
    kept in OFFERS_FILE (owner-only, the nonce as its sha256 only; expired and used offers older than a week leave),
    and the offer is written to the audit log when one can be opened (`audit`: the log or a function that opens it).
    PairingRefused no-key without a public key line or a signer file."""
    from orchestrator.remote.trust import OFFERS_FILE, PAIRING_WAYS
    from orchestrator.utils import ssh_trust
    _person_channel(via)
    if way not in PAIRING_WAYS:
        raise ValueError(f"a pairing way is one of {', '.join(PAIRING_WAYS)}")
    if not isinstance(public_line, str) or not ssh_trust.PUBLIC_LINE.fullmatch(public_line) or not signers:
        raise PairingRefused("The framework has no public key or no signing key to offer the board; nothing was made.",
                             "no-key")
    created = now or datetime.now(timezone.utc)
    offer = PairingOffer(nonce=secrets.token_hex(16), public_line=public_line, signers=signers.strip(), pem=pem or "",
                         created_at=_now_stamp(created),
                         expires_at=_now_stamp(created + timedelta(hours=max(1, int(ttl_hours)))), way=way)
    path = Path(state_dir) / OFFERS_FILE
    week = _now_stamp(created - timedelta(days=7))
    with _pair_lock(state_dir):
        rows = [r for r in _load(path, "offers") if str(r.get("expires_at", "")) > week]
        rows.append({"nonce_sha256": _nonce_hash(offer.nonce), "created_at": offer.created_at,
                     "expires_at": offer.expires_at, "way": way, "used_at": "", "via": via})
        _save(path, "offers", rows)
    _step(audit, actor or via, "board_pairing_offered", {"way": way, "expires_at": offer.expires_at})
    return offer


def offer_problem(state_dir: Path, nonce: str, *, way: str = "file", now: Optional[datetime] = None) -> str:
    """Why `nonce` matches no open offer of `way` ("" when it does): unknown, expired or used. Reads only the file."""
    from orchestrator.remote.trust import OFFERS_FILE
    if not NONCE_RE.fullmatch(nonce or ""):
        return "unknown"
    want = _nonce_hash(nonce)
    row = next((r for r in _load(Path(state_dir) / OFFERS_FILE, "offers")
                if r.get("nonce_sha256") == want and r.get("way") == way), None)
    if row is None:
        return "unknown"
    if row.get("used_at"):
        return "used"
    if str(row.get("expires_at", "")) <= _now_stamp(now):
        return "expired"
    return ""


REFUSAL_TEXTS = {
    "unknown": "this result file does not belong to an open pairing of this framework (made for another framework, "
               "or the offer was withdrawn)",
    "expired": "the pairing offer in this probe kit has expired (it is valid for 24 hours); make a new probe kit",
    "used": "this pairing was already completed once; a probe kit pairs one board once",
}


def complete_pairing(state_dir: Path, result: PairingResult, *, via: str, audit: Any, actor: str = "",
                     probe_id: str = "", label: str = "", now: Optional[datetime] = None) -> PairingDone:
    """The one end of both pairing ways (the probe's result file, the PAKE session): checks `result` (its state, its
    fingerprints, its nonce against an open offer of its way: single use, not expired), writes the pairing to the audit
    log first (no record, no pairing), pins the board's host keys (PAIRED_FILE: first_use then trusts them without the
    TOFU question; an earlier pin of the same board is replaced), marks the offer used and the board paired with
    result.tier, and returns a PairingDone. Only the person's channels (`via` cli or gui) pair; PairingRefused with
    its kind otherwise (nothing is changed then). `probe_id` names the stored probe the result came with, `label` the
    person's name for the board."""
    from orchestrator.remote.trust import OFFERS_FILE, PAIRED_FILE
    _person_channel(via)
    problem = result.problem()
    if problem:
        raise PairingRefused(f"The pairing in this result cannot be used: {problem}.", "invalid")
    if result.state != "installed":
        why = BOARD_REASONS.get(result.reason) or _reason_text(result.reason or "unknown")
        raise PairingRefused(f"The board did not pair: {why.rstrip('.')}.", result.state, result.reason)
    stamp = _now_stamp(now)
    want = _nonce_hash(result.nonce)
    offers_path, pins_path = Path(state_dir) / OFFERS_FILE, Path(state_dir) / PAIRED_FILE
    with _pair_lock(state_dir):
        offers = _load(offers_path, "offers")
        row = next((r for r in offers if r.get("nonce_sha256") == want and r.get("way") == result.way), None)
        kind = ("unknown" if row is None else "used" if row.get("used_at")
                else "expired" if str(row.get("expires_at", "")) <= stamp else "")
        if kind:
            raise PairingRefused(f"Pairing refused: {REFUSAL_TEXTS[kind]}. The hardware data is kept.", kind)
        log = _open_audit(audit)
        arguments = {"device_id": result.device_id, "fingerprints": result.fingerprints, "tier": result.tier,
                     "way": result.way, "board": dict(result.board)}
        try:
            record_consent(log, "board_paired", arguments, "pairing during the hardware probe",
                           channel=f"framework-{via}")
        except Exception as e:  # noqa: BLE001 - without its record there is no pairing
            raise PairingRefused(f"The pairing could not be written to the audit log ({e.__class__.__name__}); "
                                 "nothing was paired.", "audit") from None
        pins = [p for p in _load(pins_path, "boards")
                if p.get("device_id") != result.device_id
                and not set((p.get("fingerprints") or {}).values()) & set(result.fingerprints.values())]
        clean_label = " ".join(str(label or "").split())[:64]
        pins.append({"device_id": result.device_id, "fingerprints": result.fingerprints, "tier": result.tier,
                     "way": result.way, "paired_at": stamp, "label": clean_label,
                     "probe_id": probe_id if re.fullmatch(r"[0-9a-f]{16}", probe_id or "") else "", "via": via})
        _save(pins_path, "boards", pins)
        row["used_at"], row["device_id"] = stamp, result.device_id
        _save(offers_path, "offers", offers)
    return PairingDone(device_id=result.device_id, fingerprints=result.fingerprints, tier=result.tier,
                       way=result.way, probe_id=probe_id, paired_at=stamp)


# --- consents ----------------------------------------------------------------------------------------------------

def record_consent(audit: Any, tool: str, arguments: Dict[str, Any], detail: str, *, channel: str) -> None:
    """Writes a person's consent to the audit log (actor "user"); RuntimeError when it cannot be written: without its
    record there is no consent, and the caller changes nothing."""
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("no audit log")
    audit.record("user", tool, "external", "ask", "ok", arguments=arguments, approved=True, detail=detail,
                 extra={"answer": "accepted", "confirm_channel": channel})


__all__ = ["PairOutcome", "TierChooser", "FirstUse", "KeyReplaceError", "KeyReplacement", "PERSON_CHANNELS",
           "BOARD_REASONS", "NONCE_RE", "OFFER_TTL_HOURS", "PAIR_KEYS", "PAIR_STATES", "PairingDone", "PairingOffer",
           "PairingRefused", "PairingResult", "REFUSAL_TEXTS", "complete_pairing", "create_offer", "offer_problem",
           "PersonChannel", "first_use", "pair",
           "plan_replacement", "record_consent", "replace_key", "trust_presented_key"]
