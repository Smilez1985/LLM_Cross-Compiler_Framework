#!/usr/bin/env python3
"""
The tiers of the framework's key on a board (DECISIONS 2026-10-31, Dropbear tiers; nothing in the repository held
this vocabulary before, so it is a module of its own next to remote/gate.py, which installs each tier).

- TIER_ROOT ("root"): OpenSSH with sudo. The key line lives in a root-owned file sshd reads for the account (Match
  User / AuthorizedKeysFile), the gate and the framework's signing key are root-owned. The account itself cannot change
  any of it. The only tier enterprise mode takes.
- TIER_DROPBEAR ("dropbear"): Dropbear with sudo. The gate and the signing key are root-owned (sudo once at pairing),
  the key line is in the account's own ~/.ssh/authorized_keys with Dropbear's options (forced command). One step
  weaker: the account itself could edit that line; a stolen key cannot, because no gate operation writes to ~/.ssh.
- TIER_HOME ("home"): no sudo. The gate and the signing key live in the account's home ("weaker protection"): a stolen
  key still runs only llmcf's operations, but anything running as the account could change the gate.
- TIER_PASSWORD ("password"): no framework key; the person's password at every contact (the fallback when the person
  declines every key tier).
- TIER_UNRESTRICTED ("unrestricted"): a plain key line from before the gate (only an earlier pairing leaves one).

The weaker tiers (dropbear, home) exist only in private mode and only after the person's yes; enterprise mode refuses
them with the reason and offers the switch to OpenSSH or password login. Only the person's channels (CLI, GUI) choose a
tier; an AI only reads the tier word of a board. TierFacts holds the fixed words a pairing reads on the board
(gate.read_tier_facts); offer() says which choices the person gets. Standard library only.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

TIER_ROOT = "root"
TIER_DROPBEAR = "dropbear"
TIER_HOME = "home"
TIER_PASSWORD = "password"
TIER_UNRESTRICTED = "unrestricted"
TIERS = (TIER_ROOT, TIER_DROPBEAR, TIER_HOME, TIER_PASSWORD, TIER_UNRESTRICTED)
# What the person may choose when the root tier is not possible (in this order); "openssh" is the switch to OpenSSH
CHOICE_OPENSSH = "openssh"
CHOICES = (CHOICE_OPENSSH, TIER_DROPBEAR, TIER_HOME, TIER_PASSWORD)
WEAKER = (TIER_DROPBEAR, TIER_HOME, TIER_UNRESTRICTED)

MODE_PRIVATE = "private"
MODE_ENTERPRISE = "enterprise"
MODES = (MODE_PRIVATE, MODE_ENTERPRISE)

# What each tier means, in the person's words (English; the GUI's texts are in utils/l10n_deploy.py).
TIER_TEXTS = {
    TIER_ROOT: "root-owned key line (OpenSSH with sudo): the board account cannot change the framework's key line, "
               "its gate or its signing key",
    TIER_DROPBEAR: "Dropbear tier, one step weaker: the gate and the signing key are root-owned, but the key line is "
                   "in the account's own authorized_keys, so the account itself could edit it (a stolen key cannot)",
    TIER_HOME: "home gate, weaker protection: no sudo, so the gate and the signing key live in the account's home; a "
               "stolen key still runs only llmcf's operations, but anything running as the account could change them",
    TIER_PASSWORD: "no framework key: the board is reached with your password each time",
    TIER_UNRESTRICTED: "unrestricted key from an earlier pairing: it runs anything the account may run",
}
CHOICE_TEXTS = {
    CHOICE_OPENSSH: "switch the board to OpenSSH (installed with sudo if it is not there; Dropbear stays until you say "
                    "so separately), then pair with the root-owned key line",
    TIER_DROPBEAR: "use the Dropbear tier (one step weaker than OpenSSH)",
    TIER_HOME: "use the home gate (weaker protection, no sudo)",
    TIER_PASSWORD: "use no framework key: keep logging in with the password",
}
UPGRADE_TEXT = ("To reach the root-owned key line, pair the board again with its password (a scan of the board "
                "with its password, in the CLI or the GUI) and choose the switch to OpenSSH; "
                "the board account needs sudo.")


def normal_mode(value: object) -> str:
    """The framework mode as a word of MODES: "enterprise" only when the setting says exactly that; anything else is
    the default private mode."""
    return MODE_ENTERPRISE if str(value or "").strip().lower() == MODE_ENTERPRISE else MODE_PRIVATE


def allowed(tier: str, mode: str) -> bool:
    """Whether `tier` may be set up in `mode`: the weaker tiers only in private mode."""
    return not (normal_mode(mode) == MODE_ENTERPRISE and tier in (TIER_DROPBEAR, TIER_HOME))


def is_weaker(tier: str) -> bool:
    return tier in WEAKER


# The ways on for a board enterprise mode refuses to use (the person's channels and the AI's next step read it; the
# Boards window and the Builder tab's scan show their own words, boards.err.weaker_tier of utils/l10n_boards.py).
USE_WAYS = ("pair the board again with its password and choose the switch to OpenSSH (the board account needs sudo), "
            "which gives it the root-owned key line; or choose password login at that pairing; or switch the "
            "framework to private mode in its settings (only you can)")


def use_problem(tier: str, mode: str) -> str:
    """Why a board whose framework key sits on `tier` may not be used in `mode` ("" when it may): enterprise mode
    refuses the Dropbear tier and the home gate whenever a step uses the board (a deploy, a verify, a probe, a
    benchmark), not only when it is paired, and names the ways on (USE_WAYS). An unrestricted key from before the
    gate is not refused here: a deploy restricts it, and Mojo warns about it."""
    if allowed(tier, mode):
        return ""
    name = TIER_TEXTS[tier].split(":")[0]
    return (f"Enterprise mode refuses to use this board: the framework's key there is the {name}, which enterprise "
            f"mode counts as too insecure (it needs OpenSSH with sudo and the root-owned key line). Nothing was done "
            f"on the board. Ways on: {USE_WAYS}.")


def tier_of_status(words: Dict[str, str]) -> str:
    """The tier of a board from the words of the gate's status operation (restricted, keys, tier)."""
    if words.get("restricted") != "yes":
        return TIER_UNRESTRICTED if words else ""
    tier = words.get("tier", "")
    if tier in (TIER_ROOT, TIER_DROPBEAR, TIER_HOME):
        return tier
    return TIER_ROOT if words.get("keys") == "root" else TIER_HOME


_WORDS = {
    "running": ("openssh", "dropbear", "both", "none"),
    "openssh": ("yes", "no"), "dropbear": ("yes", "no"),
    "sudo": ("nopass", "password", "refused", "none"),
    "keygen": ("yes", "none"), "openssl": ("yes", "none"),
    "verifier": ("yes", "old-ssh", "no-verifier"),
    "family": ("debian", "fedora", "suse", "arch", "alpine", "unknown"),
    "dietpi": ("yes", "no"), "init": ("systemd", "openrc", "other"),
}
# The package managers a pairing reads. An answer of an earlier framework with dietpi-software is ignored: the switch
# goes through the board's real package manager only.
MANAGERS = ("apt-get", "dnf", "zypper", "pacman", "apk")
# The installed packages that provide Dropbear (dropbear_pkg=NAME, gate.dropbear_packages_sh): fixed names only
DROPBEAR_PACKAGE = re.compile(r"^dropbear(-(bin|run|initramfs|openrc))?$", re.ASCII)


@dataclass(frozen=True)
class TierFacts:
    """What a pairing read on the board, in fixed words only (gate.TIER_FACTS); "unknown" where it did not say.
    `server` is the SSH server the pairing talks to: from the login's own banner first ("SSH-2.0-dropbear_..."),
    else from the running processes."""
    server: str = "unknown"
    openssh: str = "unknown"
    dropbear: str = "unknown"
    sudo: str = "unknown"
    keygen: str = "unknown"
    openssl: str = "unknown"
    verifier: str = "unknown"
    family: str = "unknown"
    dietpi: str = "unknown"
    init: str = "unknown"
    managers: Tuple[str, ...] = field(default_factory=tuple)
    port: int = 0
    dropbear_packages: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, text: str, banner: str = "") -> "TierFacts":
        words: Dict[str, str] = {}
        managers, packages = [], []
        port = 0
        for line in (text or "").splitlines():
            key, _, value = line.strip().partition("=")
            if key == "manager" and value in MANAGERS:
                managers.append(value)
            elif key == "dropbear_pkg" and DROPBEAR_PACKAGE.fullmatch(value):
                packages.append(value)
            elif key == "port" and re.fullmatch(r"[0-9]{1,5}", value) and 0 < int(value) < 65536:
                port = int(value)
            elif key in _WORDS and value in _WORDS[key]:
                words[key] = value
        server = server_of_banner(banner)
        if server == "unknown":
            server = {"openssh": "openssh", "dropbear": "dropbear"}.get(words.get("running", ""), "unknown")
        return cls(server=server, managers=tuple(dict.fromkeys(managers)), port=port,
                   dropbear_packages=tuple(dict.fromkeys(packages)),
                   **{k: words.get(k, "unknown") for k in _WORDS if k != "running"})

    @property
    def sudo_ok(self) -> bool:
        return self.sudo in ("nopass", "password")

    @property
    def can_verify(self) -> bool:
        return self.verifier == "yes"

    def as_dict(self) -> Dict[str, object]:
        """The facts as fixed words (what the audit log and an AI may read: no host, no account, no path)."""
        return {"server": self.server, "sudo": self.sudo, "keygen": self.keygen, "openssl": self.openssl,
                "verifier": self.verifier, "family": self.family, "dietpi": self.dietpi, "init": self.init,
                "openssh_installed": self.openssh, "managers": list(self.managers),
                "dropbear_packages": list(self.dropbear_packages)}


def server_of_banner(banner: str) -> str:
    """openssh, dropbear or unknown from an SSH server's identification string."""
    text = (banner or "").lower()
    if "dropbear" in text:
        return "dropbear"
    if "openssh" in text:
        return "openssh"
    return "unknown"


@dataclass(frozen=True)
class TierOffer:
    """What the person is offered when the root tier could not be set up: the root step's `reason`, the board's
    `facts`, the `choices` (words of CHOICES; always ending with "password"), and `refused`: the weaker tiers this
    board could take but the framework's `mode` refuses (enterprise), with the reason."""
    reason: str
    facts: TierFacts
    choices: Tuple[str, ...]
    mode: str = MODE_PRIVATE
    refused: Tuple[str, ...] = ()
    proposed: str = ""   # the choice the board's stored probe proposes, when the board's own facts allow it now
    note: str = ""       # why the stored probe's proposal is not offered (the board says otherwise now)

    def lines(self) -> Tuple[str, ...]:
        """The offer as the CLI shows it (English; the GUI asks the same choices in its own texts)."""
        out = []
        if self.refused:
            out.append("Enterprise mode refuses " + " and ".join(TIER_TEXTS[t].split(":")[0] for t in self.refused)
                       + ": they count as too insecure there; enterprise mode needs OpenSSH with sudo.")
        if self.note:
            out.append(self.note)
        for n, choice in enumerate(self.choices, 1):
            mark = " (proposed from the board's probe)" if choice == self.proposed else ""
            out.append(f"{n}. {CHOICE_TEXTS[choice]}{mark}")
        return tuple(out)

    def in_order(self) -> Tuple[str, ...]:
        """The choices with the proposed one first (the GUI asks them one by one in this order); "password" stays
        last."""
        if not self.proposed or self.proposed == TIER_PASSWORD:
            return self.choices
        return (self.proposed,) + tuple(c for c in self.choices if c != self.proposed)


def offer(reason: str, facts: TierFacts, mode: str = MODE_PRIVATE) -> TierOffer:
    """The choices for a board whose root tier failed with `reason` (a word of gate.SETUP_REASONS):

    - the switch to OpenSSH when the board does not run it (Dropbear, or nothing known) and sudo works there (both
      modes; a package manager the switch knows must be there);
    - the Dropbear tier on a Dropbear board with sudo whose tools can check the framework's signature (private only);
    - the home gate on a board without sudo whose tools can check the signature (private only);
    - password login, always.
    """
    mode = normal_mode(mode)
    choices, refused = [], []
    from orchestrator.remote.openssh_switch import manager_for
    not_openssh = facts.server != "openssh" or reason == "no-sshd"
    if not_openssh and facts.sudo_ok and manager_for(facts):
        choices.append(CHOICE_OPENSSH)
    if facts.server == "dropbear" and facts.sudo_ok and facts.can_verify:
        (choices if allowed(TIER_DROPBEAR, mode) else refused).append(TIER_DROPBEAR)
    if not facts.sudo_ok and facts.can_verify:
        (choices if allowed(TIER_HOME, mode) else refused).append(TIER_HOME)
    choices.append(TIER_PASSWORD)
    return TierOffer(reason=reason, facts=facts, choices=tuple(choices), mode=mode, refused=tuple(refused))


_NAMES = {TIER_ROOT: "root-owned key line", TIER_DROPBEAR: "Dropbear tier", TIER_HOME: "home gate",
          TIER_PASSWORD: "password login", CHOICE_OPENSSH: "switch to OpenSSH"}
_PROBE_SUDO = ("root", "nopasswd", "password")   # AccessFacts.sudo words with which sudo can be used once


def proposal(access: Any, mode: str = MODE_PRIVATE) -> str:
    """The tier the board's stored probe proposes (runtime/profile.py HardwareProfile.access(), the probe's access
    facts), "" without them: OpenSSH with sudo and an ssh-keygen that checks signatures, the root-owned key line;
    Dropbear with sudo, the Dropbear tier (enterprise mode: the switch to OpenSSH); no sudo, the home gate (enterprise
    mode: password login). Only a proposal: the pairing tries the root step on the board itself first and reads the
    board's facts again (tier-facts), and only what the board says now is offered (with_proposal)."""
    if access is None:
        return ""
    server, sudo = getattr(access, "ssh_server", None), getattr(access, "sudo", None)
    if server == "openssh" and sudo in _PROBE_SUDO and getattr(access, "keygen_signatures", None) is not False:
        return TIER_ROOT
    if server == "dropbear" and sudo in _PROBE_SUDO:
        return TIER_DROPBEAR if allowed(TIER_DROPBEAR, mode) else CHOICE_OPENSSH
    if sudo == "absent":
        return TIER_HOME if allowed(TIER_HOME, mode) else TIER_PASSWORD
    return ""


def with_proposal(the_offer: TierOffer, access: Any) -> TierOffer:
    """`the_offer` (built from what the board says now) with the stored probe's proposal marked when the board offers
    it now (`proposed`), or with a `note` when the probe proposed something the board no longer offers: the probe
    facts never decide alone."""
    from dataclasses import replace
    wanted = proposal(access, the_offer.mode)
    if not wanted:
        return the_offer
    if wanted in the_offer.choices:
        return replace(the_offer, proposed=wanted)
    return replace(the_offer, note=f"The board's stored probe proposed the {_NAMES.get(wanted, wanted)}, but the "
                                   "board says otherwise now; the choices follow what the board says now.")


def choice_problem(choice: str, the_offer: Optional[TierOffer], mode: str) -> str:
    """Why `choice` cannot be taken ("" when it can): not a choice, not offered for this board, or refused in this
    mode (the person is told why)."""
    if choice not in CHOICES:
        return f"{choice!r} is not a way to put the framework's key on a board"
    if not allowed(choice, mode):
        from orchestrator.remote.gate import SETUP_REASONS
        return SETUP_REASONS["enterprise"]
    if the_offer is not None and choice not in the_offer.choices:
        return f"this board does not offer {choice!r} ({CHOICE_TEXTS.get(choice, choice)})"
    return ""


__all__ = ["CHOICES", "CHOICE_OPENSSH", "CHOICE_TEXTS", "MANAGERS", "MODES", "MODE_ENTERPRISE", "MODE_PRIVATE",
           "TIERS", "TIER_DROPBEAR", "TIER_HOME", "TIER_PASSWORD", "TIER_ROOT", "TIER_TEXTS", "TIER_UNRESTRICTED",
           "TierFacts", "TierOffer", "UPGRADE_TEXT", "USE_WAYS", "WEAKER", "allowed", "choice_problem", "is_weaker",
           "normal_mode", "offer", "proposal", "server_of_banner", "tier_of_status", "use_problem",
           "with_proposal"]
