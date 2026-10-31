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
         via: str = "", host: str = "", port: int = 22, user: str = "", switch: bool = True) -> str:
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
    Returns a PairOutcome (a str: "restricted", "dropbear", "home", "unrestricted" or "password")."""
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
    facts = offer.facts.as_dict()
    _step(audit, via, "board_key_tier_offered", {"reason": offer.reason, "mode": mode, "choices": list(offer.choices),
                                                 "refused": list(offer.refused), **facts})
    if offer.refused:
        chooser.tell([_reason_text("enterprise")])
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
        raise ssh_trust.GateUnavailable(e.reason, offer) from None
    if not valid_host(host) or not user:
        raise ssh_trust.GateUnavailable("unknown", offer)
    if not chooser.confirm("switch", plan.lines):
        _step(audit, via, "board_openssh_plan", plan.as_dict(), "error", "declined")
        raise ssh_trust.GateUnavailable("declined", offer)
    _step(audit, via, "board_openssh_plan", plan.as_dict())
    ok, reason, state = openssh_switch.install(transport.ParamikoTransport(client).run, plan, password)
    _step(audit, via, "board_openssh_installed", {**plan.as_dict(), "state": state}, "ok" if ok else "error", reason)
    if not ok:
        raise ssh_trust.GateUnavailable(reason, offer)
    # the current session (`client`) stays open until the new sshd answers on its port
    if not openssh_switch.wait_until_answers(host, plan.port):
        _step(audit, via, "board_openssh_answers", {"port": plan.port}, "error", "no-answer")
        raise ssh_trust.GateUnavailable("no-answer", offer)
    endpoint = Endpoint(host, plan.port)
    store = TrustStore(Path(state_dir))
    key = ssh_trust.fetch_host_key(host, plan.port)
    outcome = first_use(store, store.check(endpoint, key), ask=chooser.trust_key, via=via or "cli", audit=audit,
                        actor=via)
    _step(audit, via, "board_openssh_host_key", {"port": plan.port, "verdict": outcome.verdict},
          "ok" if outcome.trusted else "error")
    if not outcome.trusted:
        raise ssh_trust.GateUnavailable("declined", offer)
    fresh = ssh_trust.connect(host, user, Path(state_dir), port=plan.port, password=password)
    try:
        got = ssh_trust.install_public_key(fresh, line, bundle, password=password, mode=mode)
        _step(audit, via, "board_key_tier_installed", {"tier": "root", "port": plan.port})
        report = [f"OpenSSH answers on port {plan.port} and the framework's key is paired there with the root-owned "
                  "key line."]
        if chooser.confirm("dropbear", ["Remove Dropbear now? The board is then reached only through OpenSSH on port "
                                        f"{plan.port}; your current session ends when Dropbear stops."]):
            done, why = openssh_switch.disable_dropbear(transport.ParamikoTransport(fresh).run, password)
            _step(audit, via, "board_dropbear_disabled", {"dietpi": plan.dietpi}, "ok" if done else "error", why)
            report.append("Dropbear is off." if done else
                          f"Dropbear could not be turned off ({openssh_switch.SWITCH_REASONS.get(why, why)}); it "
                          "keeps running next to OpenSSH.")
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


# --- consents ----------------------------------------------------------------------------------------------------

def record_consent(audit: Any, tool: str, arguments: Dict[str, Any], detail: str, *, channel: str) -> None:
    """Writes a person's consent to the audit log (actor "user"); RuntimeError when it cannot be written: without its
    record there is no consent, and the caller changes nothing."""
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("no audit log")
    audit.record("user", tool, "external", "ask", "ok", arguments=arguments, approved=True, detail=detail,
                 extra={"answer": "accepted", "confirm_channel": channel})


__all__ = ["PairOutcome", "TierChooser", "FirstUse", "KeyReplaceError", "KeyReplacement", "PERSON_CHANNELS",
           "PersonChannel", "first_use", "pair",
           "plan_replacement", "record_consent", "replace_key", "trust_presented_key"]
