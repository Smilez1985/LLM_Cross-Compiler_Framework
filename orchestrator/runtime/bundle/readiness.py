#!/usr/bin/env python3
"""
The board's root readiness for llmcf-install (the maintainer's decision: a board must be able to run the bundle's root
steps; whatever llmcf-install needs is set up like any other dependency, after the person's yes, with the plan shown
first). One implementation for every channel: `llmcf bundle deploy` and `llmcf bundle prepare-root`, the Board bundle
window (its Prepare root... button and the deploy that stops before the root actions), and the MCP tool bundle_deploy,
which only reads the check (an AI never sets anything up: no tool module calls setup or remove).

What llmcf-install needs, read without root and without a password (gate operation bundle-ready, gate_ops.py):
sudo on the board; the framework's signer files root-owned in /etc/llmcf (pairing's root-owned key line puts them
there, never this module); llmcf-install itself at a fixed root-owned place (gate_ops.FIXED_HELPER) with the sha256 of
the bundle's own llmcf-install; and a sudoers drop-in (gate_ops.DROPIN, mode 0440, checked with visudo -cf before it is
moved in) that lets the board account run only that program, only with its actions, without a password.

setup (gate operation bundle-ready-setup) sets up only what is missing, as root once: through the board account's sudo
(sudo -n, or the person's one-time password on stdin), or, on a board without sudo, through su with root's password on
stdin (only in private mode: enterprise mode keeps its rules, OpenSSH with sudo, never the Dropbear tier or the home
gate), installing sudo with the board's package manager first (openssh_switch's known package managers; an unknown
distribution is refused before anything runs). A password is never in an argv, a log or the audit log. remove (gate
operation bundle-ready-remove) removes exactly the drop-in and the program at their fixed paths, after a separate yes.

Afterwards bundle-root runs llmcf-install from that fixed root-owned place through the drop-in (sudo -n), never from
the account's own folders, which closes the check-then-run gap of the user-owned .run folder; a bundle carrying another
llmcf-install updates the fixed program only through the same plan and yes. Standard library only.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.runtime.bundle import gate_ops

FIXED_HELPER = gate_ops.FIXED_HELPER
DROPIN = gate_ops.DROPIN
# What the separate yes of the readiness's removal removes, exactly (never their folders: llmcf's other root-owned
# files live next to the program).
REMOVE_PATHS = (DROPIN, FIXED_HELPER)
WAYS = ("sudo", "su")

_WORDS = {
    "sudo": ("yes", "no"), "rootway": ("nopass", "password", "none"), "su": ("yes", "no"),
    "signers": ("root", "none"), "account": ("ok", "bad"), "helper": ("same", "other", "absent", "unsafe"),
    "dropin": ("yes", "no"), "family": ("debian", "fedora", "suse", "arch", "alpine", "unknown"),
    "dietpi": ("yes", "no"),
}
_MANAGERS = ("apt-get", "dnf", "zypper", "pacman", "apk")

# What a missing piece means, and how the person sets it up (CLI, GUI and the AI's facts use these words).
MISSING_TEXTS = {
    "sudo": "the board has no sudo",
    "signers": "the board holds no root-owned signing key of the framework",
    "helper": "llmcf-install is not at its root-owned place " + FIXED_HELPER,
    "helper-update": "llmcf-install at " + FIXED_HELPER + " is not this bundle's (it is updated with the same yes)",
    "dropin": "no sudo rule lets the board account run llmcf-install without a password (" + DROPIN + ")",
}
SIGNERS_HOW = ("pair the board again with its password (a scan of the board with its password, in the CLI or the "
               "GUI) and take the root-owned key line; it puts the framework's signing key in root-owned files")
REASONS = {
    "via": "not a way to reach root", "bundle": "not a bundle id",
    "account": "the board account is root or its name is not a plain account name",
    "home": "the board account's home folder is not a plain path",
    "no-sudo": "the board has no sudo, and root's password through su was not chosen",
    "no-su": "the board has neither sudo nor su",
    "su-refused": "su did not take root's password (nothing was changed)",
    "no-manager": "the board has no package manager the framework knows (apt, dnf, zypper, pacman, apk) to install "
                  "sudo with",
    "no-package-db": "pacman has no package database yet: update the board's system first (sudo pacman -Syu)",
    "lists": "the package manager could not read its package lists",
    "install": "the package manager could not install sudo (its output stays on the board)",
    "no-signer": "the board holds no root-owned signing key of the framework: " + SIGNERS_HOW,
    "needs-password": "sudo asks for a password on this board",
    "sudo-refused": "sudo did not take the password (nothing more was changed)",
    "no-bundle": "the bundle's llmcf-install is not on the board yet (deploy the bundle first)",
    "tmp": "the board could not make a private folder",
    "not-signed": "the bundle's llmcf-install on the board is not signed with the framework's signing key",
    "helper-sha": "the bundle's llmcf-install on the board is not the one this framework built",
    "helper-changed": "llmcf-install changed while it was put in place; it was removed again",
    "sudo": "sudo refused a step (nothing more was changed)",
    "no-visudo": "the board has no visudo to check the sudo rule",
    "sudoers": "visudo did not accept the sudo rule; it was not moved in",
    "dropin": "the sudo rule is in place, but sudo does not read it (the board's sudoers has no includedir for "
              "/etc/sudoers.d)",
    "enterprise-su": "enterprise mode reaches root only through the board account's sudo, never through su",
    "unknown": "the board gave no answer the framework knows",
}
QUESTION = ("Set up the root readiness of board {board} now, as listed above? It changes only what is missing, as root "
            "once; nothing else on the board is changed.")
REMOVE_QUESTION = ("Remove the root readiness of board {board}: exactly {paths}? Bundles with root actions cannot run "
                   "them there afterwards until it is set up again.")
DECLINED = ("Nothing was set up. The root actions of bundle {bundle} cannot run on board {board} until its root "
            "readiness is there: {missing}. Set it up when you deploy again (say yes to its question), or on its own "
            "with the root readiness step of the CLI or of the Board bundle window.")
PASSWORD_SUDO = "The board account's password for sudo, for this setup only (never kept)"
PASSWORD_SU = "The board's root password for su, for this setup only (never kept; the board has no sudo yet)"


@dataclass(frozen=True)
class Readiness:
    """What llmcf-install needs on a board, in fixed words (bundle-ready's answer); "unknown" where it did not say."""
    sudo: str = "unknown"
    rootway: str = "unknown"
    su: str = "unknown"
    signers: str = "unknown"
    account: str = "unknown"
    helper: str = "unknown"
    dropin: str = "unknown"
    family: str = "unknown"
    dietpi: str = "unknown"
    managers: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, text: str) -> "Readiness":
        words: Dict[str, str] = {}
        managers: List[str] = []
        for line in (text or "").splitlines():
            key, _, value = line.strip().partition("=")
            if key == "manager" and value in _MANAGERS:
                managers.append(value)
            elif key in _WORDS and value in _WORDS[key]:
                words[key] = value
        return cls(managers=tuple(dict.fromkeys(managers)), **{k: words.get(k, "unknown") for k in _WORDS})

    @property
    def manager(self) -> str:
        """The package manager sudo is installed with ("" when none the framework knows is there)."""
        from orchestrator.remote.openssh_switch import manager_for
        return manager_for(self)

    @property
    def ready(self) -> bool:
        return not self.missing()

    def missing(self) -> List[str]:
        """The missing pieces, words of MISSING_TEXTS, in the order they are set up."""
        out = []
        if self.sudo != "yes":
            out.append("sudo")
        if self.signers != "root":
            out.append("signers")
        if self.helper in ("absent", "unsafe", "unknown"):
            out.append("helper")
        elif self.helper == "other":
            out.append("helper-update")
        if self.dropin != "yes":
            out.append("dropin")
        return out

    def way(self, mode: Any = "private") -> str:
        """How root is reached once for the setup: sudo, su (a board without sudo, private mode only), or ""."""
        from orchestrator.remote.key_tiers import MODE_ENTERPRISE, normal_mode
        if self.sudo == "yes":
            return "sudo"
        if self.su == "yes" and normal_mode(mode) != MODE_ENTERPRISE:
            return "su"
        return ""

    def needs_password(self, mode: Any = "private") -> bool:
        """Whether the setup needs the person's one-time password (root's for su, the account's for sudo)."""
        return self.way(mode) == "su" or self.rootway != "nopass"

    def problem(self, mode: Any = "private") -> str:
        """Why the setup cannot run on this board now ("" when it can): the reason in the person's words."""
        from orchestrator.remote.key_tiers import MODE_ENTERPRISE, normal_mode
        if self.account == "bad":
            return REASONS["account"]
        if self.sudo != "yes":
            if normal_mode(mode) == MODE_ENTERPRISE:
                return ("enterprise mode needs OpenSSH with sudo on the board (the root-owned key line); the board has "
                        "no sudo")
            if self.su != "yes":
                return REASONS["no-su"]
            if not self.manager:
                return REASONS["no-manager"]
        return ""

    def as_dict(self) -> Dict[str, Any]:
        """Fixed words only (what the audit log and an AI may read: no host, no account, no path)."""
        return {"ready": self.ready, "missing": self.missing(), "sudo": self.sudo, "signers": self.signers,
                "helper": self.helper, "dropin": self.dropin, "family": self.family, "managers": list(self.managers)}


def check(run: Callable[..., Any], sha256: str) -> Readiness:
    """bundle-ready over `run` (a board session's run): what llmcf-install needs, read without root."""
    from orchestrator.remote import gate
    result = run(gate.script("bundle-ready", sha256))
    return Readiness.parse(getattr(result, "out", "") or "")


def plan_lines(found: Readiness, *, version: str = "", mode: Any = "private") -> List[str]:
    """What the setup does on this board, in the person's words, before the yes (only what is missing)."""
    out = []
    missing = found.missing()
    if not missing:
        return [f"The board's root readiness is there: sudo, the root-owned signing key, llmcf-install at {FIXED_HELPER} "
                f"and its sudo rule {DROPIN}."]
    way = found.way(mode)
    if "sudo" in missing and way == "su":
        from orchestrator.remote.openssh_switch import INSTALL_COMMANDS, PREPARE_COMMANDS
        manager = found.manager
        prepare = PREPARE_COMMANDS.get(manager, "")
        out.append("Install sudo with the board's package manager, as root once through su (root's password, typed "
                   "by you, sent on stdin only): " + (f"{prepare}, then " if prepare else "")
                   + f"{INSTALL_COMMANDS.get(manager, '?')} sudo.")
    if "signers" in missing:
        out.append("The board holds no root-owned signing key of the framework, which llmcf-install checks the "
                   "bundle against. This setup never writes it: " + SIGNERS_HOW + "; then deploy again.")
    if "helper" in missing or "helper-update" in missing:
        verb = "Replace" if "helper-update" in missing else "Put"
        out.append(f"{verb} llmcf-install{(' ' + version) if version else ''} of this bundle at {FIXED_HELPER}, "
                   "root-owned (0755): copied from the bundle on the board after its signature is checked against "
                   "the root-owned signing key and its sha256 against the one this framework built, and checked "
                   "again in place.")
    if "dropin" in missing:
        out.append(f"Write the sudo rule {DROPIN} (root-owned, 0440, checked with visudo -cf before it is moved in): "
                   f"the board account may run only {FIXED_HELPER} with its actions (plan, apply, status, rollback, "
                   "uninstall, plan-uninstall), without a password; nothing else.")
    if way == "sudo":
        out.append("Root is reached once through the board account's sudo"
                   + (" without a password." if found.rootway == "nopass" else
                      " with your password, typed once and sent on stdin only (never kept)."))
    out.append("Afterwards the root actions run llmcf-install from that root-owned place only (sudo -n through the "
               "rule), never from the account's own folders.")
    return out


@dataclass(frozen=True)
class SetupDone:
    """What bundle-ready-setup did: `ok`, `reason` (a word of REASONS when not), `changed` (sudo, helper, dropin)."""
    ok: bool
    reason: str = ""
    changed: Tuple[str, ...] = ()

    def text(self) -> str:
        done = ", ".join({"sudo": "sudo installed", "helper": f"llmcf-install put at {FIXED_HELPER}",
                          "dropin": f"sudo rule {DROPIN} written"}.get(c, c) for c in self.changed)
        if self.ok:
            return "The board's root readiness is set up" + (f" ({done})." if done else " (nothing was missing).")
        return ("The board's root readiness could not be set up: " + REASONS.get(self.reason, REASONS["unknown"])
                + (f". Done before it stopped: {done}." if done else "."))

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "changed": list(self.changed)}


_CHANGED = ("sudo", "helper", "dropin")


def _words(text: str, key: str) -> Dict[str, str]:
    for line in (text or "").splitlines():
        words = dict(w.split("=", 1) for w in line.split() if "=" in w)
        if key in words:
            return words
    return {}


def setup(run: Callable[..., Any], bundle_id: str, sha256: str, found: Readiness, *, mode: Any = "private",
          password: str = "") -> SetupDone:
    """bundle-ready-setup over `run`, after the person's yes: only what `found` says is missing. `password` is the
    person's one-time password (root's for su, the account's for sudo -S), sent on stdin only."""
    from orchestrator.remote import gate
    from orchestrator.remote.key_tiers import MODE_ENTERPRISE, normal_mode
    if any(ch in password for ch in "\r\n\0"):
        return SetupDone(False, "sudo-refused")
    way = found.way(mode)
    if not way:
        return SetupDone(False, "enterprise-su" if normal_mode(mode) == MODE_ENTERPRISE else "no-su")
    result = run(gate.script("bundle-ready-setup", way, found.manager or "-", bundle_id, sha256),
                 stdin=[(password + "\n").encode("utf-8")], timeout=1800.0)
    words = _words(getattr(result, "out", "") or "", "ready")
    changed = tuple(c for c in words.get("changed", "").split(",") if c in _CHANGED)
    if words.get("ready") == "done":
        return SetupDone(True, "", changed)
    reason = words.get("reason", "unknown")
    return SetupDone(False, reason if reason in REASONS else "unknown", changed)


@dataclass(frozen=True)
class RemoveDone:
    ok: bool
    reason: str = ""
    removed: Tuple[str, ...] = ()

    def text(self) -> str:
        if not self.ok:
            return "The root readiness was not removed: " + REASONS.get(self.reason, REASONS["unknown"]) + "."
        gone = {"dropin": DROPIN, "helper": FIXED_HELPER}
        return ("Removed: " + ", ".join(gone[r] for r in self.removed) + "." if self.removed
                else "Nothing was there to remove.")


def remove(run: Callable[..., Any], found: Readiness, *, mode: Any = "private", password: str = "") -> RemoveDone:
    """bundle-ready-remove over `run`, after the person's separate yes: exactly REMOVE_PATHS."""
    from orchestrator.remote import gate
    if any(ch in password for ch in "\r\n\0"):
        return RemoveDone(False, "sudo-refused")
    way = found.way(mode) or "sudo"
    result = run(gate.script("bundle-ready-remove", way), stdin=[(password + "\n").encode("utf-8")])
    words = _words(getattr(result, "out", "") or "", "removed")
    state = words.get("removed", "")
    if state == "unavailable" or not state:
        reason = words.get("reason", "unknown")
        return RemoveDone(False, reason if reason in REASONS else "unknown")
    return RemoveDone(True, "", tuple(w for w in state.split(",") if w in ("dropin", "helper")))


def declined_text(bundle_id: str, board: str, found: Optional[Readiness]) -> str:
    missing = "; ".join(MISSING_TEXTS[m] for m in (found.missing() if found else ["helper", "dropin"]))
    return DECLINED.format(bundle=bundle_id, board=board, missing=missing)


__all__ = ["DECLINED", "DROPIN", "FIXED_HELPER", "MISSING_TEXTS", "PASSWORD_SU", "PASSWORD_SUDO", "QUESTION",
           "REASONS", "REMOVE_PATHS", "REMOVE_QUESTION", "Readiness", "RemoveDone", "SIGNERS_HOW", "SetupDone",
           "WAYS", "check", "declined_text", "plan_lines", "remove", "setup"]
