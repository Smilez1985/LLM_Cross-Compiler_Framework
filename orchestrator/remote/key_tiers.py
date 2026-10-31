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
from typing import Dict, Optional, Tuple

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
# GUI shows its own words, deploy.tier.refused_use of utils/l10n_deploy.py).
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
MANAGERS = ("apt-get", "dnf", "zypper", "pacman", "apk", "dietpi-software")


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

    @classmethod
    def parse(cls, text: str, banner: str = "") -> "TierFacts":
        words: Dict[str, str] = {}
        managers = []
        port = 0
        for line in (text or "").splitlines():
            key, _, value = line.strip().partition("=")
            if key == "manager" and value in MANAGERS:
                managers.append(value)
            elif key == "port" and re.fullmatch(r"[0-9]{1,5}", value) and 0 < int(value) < 65536:
                port = int(value)
            elif key in _WORDS and value in _WORDS[key]:
                words[key] = value
        server = server_of_banner(banner)
        if server == "unknown":
            server = {"openssh": "openssh", "dropbear": "dropbear"}.get(words.get("running", ""), "unknown")
        return cls(server=server, managers=tuple(dict.fromkeys(managers)), port=port,
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
                "openssh_installed": self.openssh, "managers": list(self.managers)}


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

    def lines(self) -> Tuple[str, ...]:
        """The offer as the CLI shows it (English; the GUI asks the same choices in its own texts)."""
        out = []
        if self.refused:
            out.append("Enterprise mode refuses " + " and ".join(TIER_TEXTS[t].split(":")[0] for t in self.refused)
                       + ": they count as too insecure there; enterprise mode needs OpenSSH with sudo.")
        for n, choice in enumerate(self.choices, 1):
            out.append(f"{n}. {CHOICE_TEXTS[choice]}")
        return tuple(out)


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
           "normal_mode", "offer", "server_of_banner", "tier_of_status", "use_problem"]
