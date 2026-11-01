#!/usr/bin/env python3
"""
The switch of a board to OpenSSH (DECISIONS 2026-10-31, Dropbear tiers, addendum 2): when the person says yes, the
framework's board setup installs OpenSSH if it is not there, like any other dependency of the installer, then pairs
with the root-owned key line. It follows install.sh's way (one system family from ID and ID_LIKE in /etc/os-release,
the distribution's own package manager, known package names per family, a plan shown first and a plain report after):

- plan(facts): what will happen, as lines for the person, from the board's TierFacts (key_tiers.py): the package
  manager and the packages, the port the new sshd answers on (SWITCH_PORT, next to Dropbear, which keeps port 22 and
  the current session until the person removes it), the checks (sshd -t), the start. SwitchUnavailable when the board
  cannot take it (no sudo, no package manager the switch knows).
- the setup operation openssh-install (run once with sudo by the unrestricted login of the pairing): installs the
  packages only when sshd is missing, writes llmcf's port drop-in first (so a package that starts sshd at once never
  fights Dropbear for port 22), makes missing host keys (ssh-keygen -A), checks the configuration (sshd -t) and starts
  sshd; answers openssh=installed|present port=N, or openssh=unavailable reason=WORD.
- wait_until_answers(): the current session stays open until the new sshd answers on its port.
- the setup operation dropbear-disable (remove_dropbear): only after a separate yes of the person, over the new
  sshd's login (the way in stays open): the init system stops and disables the dropbear service first, then the
  board's own package manager removes only the packages that provide the running SSH server (Debian: dropbear,
  dropbear-bin, dropbear-run as installed; Arch, Fedora, openSUSE: dropbear; gate.DROPBEAR_PACKAGES), with remove,
  never purge and never autoremove, so its configuration stays. Alpine is disable-only: apk del would also remove
  the libraries only Dropbear pulled in, so Dropbear is stopped and switched off there and its packages (dropbear,
  dropbear-openrc) stay, refused with the reason dependents and a plain note (ALPINE_NOTE).
  dropbear-initramfs (the disk unlock at boot) is never removed, and the dropbear-bin it needs stays with it. The
  removal is refused, with nothing removed, when the package manager would take any other package with them (apt's
  simulation, rpm's test) or when it fails; a board without a package manager the switch knows gets the init
  system's disable only, with a plain note. The framework never removes Dropbear on its own.

DietPi: OpenSSH is installed and Dropbear removed with apt, DietPi's package manager underneath, like on any Debian
system; DietPi's own software list is not used or changed.

Every step is written to the audit log by the caller (remote/pairing.py), with the plan's fixed words only.
"""

import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Tuple

from orchestrator.remote import gate
from orchestrator.remote.gate import define

SWITCH_PORT = 2222
WAIT_SECONDS = 60.0
# The packages of OpenSSH's server and of ssh-keygen per package manager (the names each distribution ships).
PACKAGES = {
    "apt-get": ("openssh-server", "openssh-client"),
    "dnf": ("openssh-server", "openssh-clients"),
    "zypper": ("openssh-server", "openssh-clients"),
    "pacman": ("openssh",),
    "apk": ("openssh-server", "openssh-keygen"),
}
# The package manager of each system family (install.sh's families).
FAMILY_MANAGER = {"debian": "apt-get", "fedora": "dnf", "suse": "zypper", "arch": "pacman", "alpine": "apk"}
INSTALL_COMMANDS = {
    "apt-get": "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends",
    "dnf": "dnf install -y",
    "zypper": "zypper --non-interactive install",
    "pacman": "pacman -S --noconfirm --needed",
    "apk": "apk add --no-cache",
}
# What runs before the first install, as install.sh's pkg_install does it: apt and apk read their package lists anew
# (a fresh image, DietPi's among them, often ships without them, and then no package is found); dnf and zypper do it
# themselves; pacman never (pacman -Sy alone is a partial upgrade, which Arch does not support), so a board without
# pacman's package database is told to update its system first (PACMAN_SYNC_DIR, as install.sh checks it).
PREPARE_COMMANDS = {"apt-get": "apt-get update", "apk": "apk update"}
PACMAN_SYNC_DIR = "/var/lib/pacman/sync"
# How long the install may stay silent (its output stays on the board): reading the package lists and installing
# OpenSSH on a small board with a slow card or mirror takes minutes, and a cut connection must never stop the package
# manager halfway (the transport's default is 120 seconds).
INSTALL_TIMEOUT = 1800.0
DROPIN = "/etc/ssh/sshd_config.d/llmcf-port.conf"
# Socket-activated OpenSSH (Ubuntu 22.10 and later: ssh.socket listens, sshd starts per connection): systemd's
# sshd-socket-generator turns the Port lines of sshd_config into ssh.socket's ListenStream only at `systemctl
# daemon-reload`, and only a restart of ssh.socket makes it listen there; restarting ssh.service alone keeps port 22.
SSH_SOCKET = "ssh.socket"
# How Dropbear's packages are removed after the person's separate yes, per query program of gate.DROPBEAR_PACKAGES:
# remove (never purge: the configuration stays) and never autoremove (no other package goes with them).
REMOVE_COMMANDS = {
    "dpkg": "env DEBIAN_FRONTEND=noninteractive apt-get remove -y -o APT::Get::AutomaticRemove=false",
    "pacman": "pacman -R --noconfirm",
    "dnf": "dnf remove -y --noautoremove",
    "zypper": "zypper --non-interactive remove --no-clean-deps",
}


class SwitchUnavailable(ValueError):
    """The board cannot be switched to OpenSSH by the framework (`reason`, a word of gate.SETUP_REASONS or below)."""

    def __init__(self, reason: str, text: str):
        super().__init__(text)
        self.reason = reason


SWITCH_REASONS = {
    "no-manager": "the board has no package manager the switch knows (apt, dnf, zypper, pacman, apk)",
    "install": "the package manager could not install OpenSSH (its output stays on the board)",
    "lists": "the package manager could not read its package lists (apt-get update or apk update failed; its output "
             "stays on the board)",
    "no-package-db": "pacman has no package database yet: update the board's system first (sudo pacman -Syu), then "
                     "pair again; the framework never runs pacman -Sy itself (a partial upgrade, which Arch does not "
                     "support)",
    "install-timeout": "the installation of OpenSSH gave no answer in time; it may still be running on the board, so "
                       "wait for it to end, then pair the board again",
    "host-key": "you did not trust the new sshd's host key; it keeps running on its port next to Dropbear",
    "selinux": "SELinux keeps sshd from the switch's port, and semanage (package policycoreutils-python-utils) could "
               "not allow it",
    "no-sshd": "OpenSSH's sshd is not there after the installation",
    "sshd-config": "sshd did not accept its configuration (sshd -t); it was not started",
    "start": "sshd could not be started",
    "no-answer": "the new sshd did not answer on its port in time; the current session stays as it is",
    # the removal of Dropbear after the person's separate yes (remove_dropbear; no-manager above as well): Dropbear
    # is stopped and disabled then, only its packages stay
    "no-packages": "no package of the board's package manager provides Dropbear (it may have been installed by "
                   "hand), so nothing was removed",
    "remove": "the package manager did not remove Dropbear's packages (its output stays on the board); they stay "
              "installed",
    "dependents": "removing Dropbear's packages would also remove other packages, so nothing was removed",
    "remove-timeout": "the removal of Dropbear gave no answer in time; it may still be running on the board",
}
# Alpine is disable-only (the maintainer's decision): `apk del` removes every package that only the removed ones
# pulled in, so it would also take libraries Dropbear brought; the framework never runs it, Dropbear is stopped and
# switched off, and the person reads why in plain words.
ALPINE_NOTE = ("on Alpine, Dropbear is stopped and switched off but not removed: apk would also remove the libraries "
               "that only Dropbear pulled in, so its packages stay installed")
# What the person reads about a removal that stopped and disabled Dropbear but did not remove its packages
REMOVAL_NOTES = {
    "no-manager": "the board has no package manager the framework knows, so Dropbear's program stays installed",
    "no-packages": SWITCH_REASONS["no-packages"],
    "remove": SWITCH_REASONS["remove"],
    "dependents": SWITCH_REASONS["dependents"],
}


def manager_for(facts: Any) -> str:
    """The package manager the switch uses on this board ("" when none it knows is there): the family's own, else the
    first one the board has (DietPi installs through apt underneath)."""
    have = tuple(getattr(facts, "managers", ()) or ())
    family = FAMILY_MANAGER.get(getattr(facts, "family", ""), "")
    if family and family in have:
        return family
    return next((m for m in have if m in PACKAGES), "")


@dataclass(frozen=True)
class SwitchPlan:
    """What the switch does, shown before the person's yes: the package `manager`, the `packages` (none when OpenSSH is
    there already), the `port` of the new sshd and the person's `lines`; `dropbear_packages`: the installed packages
    that provide Dropbear as the board named them (gate.DROPBEAR_PACKAGES), for the later separate question."""
    manager: str
    packages: Tuple[str, ...]
    port: int
    dietpi: bool
    lines: Tuple[str, ...]
    dropbear_packages: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"manager": self.manager, "packages": list(self.packages), "port": self.port, "dietpi": self.dietpi,
                "dropbear_packages": list(self.dropbear_packages)}

    def removable(self) -> Tuple[str, ...]:
        """The packages the separate yes removes (what the board's dropbear-disable decides again on the board):
        every Dropbear package but dropbear-initramfs, and without dropbear-bin while dropbear-initramfs needs it."""
        return removable_packages(self.dropbear_packages)


def removable_packages(installed) -> Tuple[str, ...]:
    """Of the installed Dropbear packages, the ones that provide the SSH server and may go (gate.DROPBEAR_KEEP stays,
    and dropbear-bin with dropbear-initramfs)."""
    have = tuple(installed or ())
    keep = set(gate.DROPBEAR_KEEP)
    if keep & set(have):
        keep.add("dropbear-bin")
    return tuple(p for p in have if p not in keep)


def plan(facts: Any, port: int = SWITCH_PORT) -> SwitchPlan:
    """The plan of the switch for a board with `facts` (key_tiers.TierFacts); SwitchUnavailable when it cannot run."""
    if not getattr(facts, "sudo_ok", False):
        raise SwitchUnavailable("no-sudo", gate.SETUP_REASONS["no-sudo"] + " (the switch needs sudo once)")
    manager = manager_for(facts)
    if not manager:
        raise SwitchUnavailable("no-manager", SWITCH_REASONS["no-manager"])
    present = getattr(facts, "openssh", "") == "yes"
    packages = () if present else PACKAGES[manager]
    dietpi = getattr(facts, "dietpi", "") == "yes"
    lines = []
    if packages:
        prepare = PREPARE_COMMANDS.get(manager, "")
        lines.append("Install OpenSSH with the board's package manager, once with sudo: "
                     + (f"{prepare}, then " if prepare else "") + f"{INSTALL_COMMANDS[manager]} "
                     + " ".join(packages) + ".")
    else:
        lines.append("OpenSSH's sshd is installed on the board already: nothing is installed.")
    lines += [f"Let sshd answer on port {port} next to Dropbear (llmcf's file {DROPIN}), make missing host keys "
              "(ssh-keygen -A), check the configuration (sshd -t) and start sshd.",
              "Your current session stays open until the new sshd answers on its port.",
              f"The new sshd has host keys of its own: you confirm its fingerprint like a new board's, then the board "
              f"is paired there with the root-owned key line, and it is reached on port {port} from then on.",
              "Dropbear stays as it is; removing it is a separate question afterwards."]
    installed = tuple(getattr(facts, "dropbear_packages", ()) or ())
    going = removable_packages(installed)
    if manager == "apk":
        lines.append("Only if you say yes to that question: the init system stops and switches off Dropbear. On Alpine "
                     "it is not removed: apk would also remove the libraries that only Dropbear pulled in, so its "
                     "packages stay installed.")
        return SwitchPlan(manager=manager, packages=packages, port=int(port), dietpi=dietpi, lines=tuple(lines),
                          dropbear_packages=installed)
    lines.append("Only if you say yes to that question: the init system stops and disables Dropbear, then the board's "
                 "package manager removes only the packages that provide it"
                 + (f" ({', '.join(going)})" if going else "")
                 + " with remove, never purge or autoremove, so its configuration stays.")
    if set(gate.DROPBEAR_KEEP) & set(installed):
        lines.append("dropbear-initramfs stays installed (it unlocks an encrypted disk at boot), and with it "
                     "dropbear-bin, which it needs.")
    if dietpi:
        lines.append("On DietPi, both steps use apt, DietPi's package manager underneath, like on any Debian system; "
                     "DietPi's own software list is not used or changed.")
    return SwitchPlan(manager=manager, packages=packages, port=int(port), dietpi=dietpi, lines=tuple(lines),
                      dropbear_packages=installed)


def _install_script(manager: str, port: str) -> str:
    install = INSTALL_COMMANDS[manager] + " " + " ".join(PACKAGES[manager])
    prepare = PREPARE_COMMANDS.get(manager, "")
    return "\n".join([
        "set -u",
        "umask 022",
        'IFS= read -r llmcf_pw || llmcf_pw=""',
        'no() { echo "openssh=unavailable reason=$1"; exit 0; }',
        'asroot() { if [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; '
        'else sudo -n "$@"; fi; }',
        "command -v sudo >/dev/null 2>&1 || no no-sudo",
        "asroot true >/dev/null 2>&1 || no sudo",
        "llmcf_was=present",
        # llmcf's port first: a package that starts sshd at once (Debian) then never fights Dropbear for port 22
        "asroot mkdir -p /etc/ssh/sshd_config.d >/dev/null 2>&1 || no sudo",
        f"printf 'Port {port}\\n' | asroot tee {DROPIN} >/dev/null 2>&1 || no sudo",
        "llmcf_sshd() { command -v sshd 2>/dev/null || { [ -x /usr/sbin/sshd ] && echo /usr/sbin/sshd; }; }",
        'if [ -z "$(llmcf_sshd)" ]; then',
        *([f"  ls {PACMAN_SYNC_DIR}/*.db >/dev/null 2>&1 || no no-package-db"] if manager == "pacman" else []),
        *([f"  asroot sh -c '{prepare}' >/dev/null 2>&1 || no lists"] if prepare else []),
        f"  asroot sh -c '{install}' >/dev/null 2>&1 || no install",
        "  llmcf_was=installed",
        "fi",
        'llmcf_s=$(llmcf_sshd) || no no-sshd',
        '[ -n "$llmcf_s" ] || no no-sshd',
        # a configuration that does not read the drop-in folder gets the port line itself (the earlier file kept)
        "if ! asroot grep -qi '^[[:space:]]*Include[[:space:]].*sshd_config.d' /etc/ssh/sshd_config 2>/dev/null; then",
        f"  asroot grep -qx 'Port {port}' /etc/ssh/sshd_config 2>/dev/null || {{ asroot cp -p /etc/ssh/sshd_config "
        f"/etc/ssh/sshd_config.llmcf-backup && printf '\\nPort {port}\\n' | asroot tee -a /etc/ssh/sshd_config "
        ">/dev/null; } >/dev/null 2>&1 || no sudo",
        "fi",
        "asroot ssh-keygen -A >/dev/null 2>&1 || true",
        'asroot "$llmcf_s" -t >/dev/null 2>&1 || no sshd-config',
        # SELinux (Fedora, RHEL) lets sshd bind only ports labelled for it: the switch's port gets the ssh label
        'if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null)" = Enforcing ]; then',
        "  command -v semanage >/dev/null 2>&1 || no selinux",
        f"  {{ asroot semanage port -a -t ssh_port_t -p tcp {port} || asroot semanage port -m -t ssh_port_t -p tcp "
        f"{port}; }} >/dev/null 2>&1 || no selinux",
        "fi",
        "if [ -d /run/systemd/system ]; then",
        # the port drop-in is read by systemd's generators only after a daemon-reload (ssh.socket's ListenStream)
        "  asroot systemctl daemon-reload >/dev/null 2>&1 || true",
        f"  if systemctl is-enabled --quiet {SSH_SOCKET} 2>/dev/null || systemctl is-active --quiet {SSH_SOCKET} "
        "2>/dev/null; then",
        f"    asroot systemctl restart {SSH_SOCKET} >/dev/null 2>&1 || no start",
        "  else",
        "    { asroot systemctl enable ssh || asroot systemctl enable sshd; } >/dev/null 2>&1",
        "    { asroot systemctl restart ssh || asroot systemctl restart sshd; } >/dev/null 2>&1 || no start",
        "  fi",
        "elif command -v rc-service >/dev/null 2>&1; then",
        "  asroot rc-update add sshd default >/dev/null 2>&1",
        "  asroot rc-service sshd restart >/dev/null 2>&1 || no start",
        "else",
        '  asroot "$llmcf_s" >/dev/null 2>&1 || no start',
        "fi",
        f'echo "openssh=$llmcf_was port={port}"',
    ])


define("openssh-install", ("word", "uint"), lambda manager, port, rest="": _install_script(manager, port), gate=False,
       refuse=lambda args: "" if args[0] in PACKAGES and 0 < int(args[1]) < 65536 else
       "not a package manager or port of the switch",
       doc="the switch to OpenSSH: install it if it is not there (sudo once), its port next to Dropbear, sshd -t, start")

DROPBEAR_DISABLE = "\n".join([
    # stdin: a line with the person's one-time password (empty: sudo -n only). Runs over the new sshd's login, so
    # stopping Dropbear never cuts this session. Answers fixed words only.
    "set -u",
    'IFS= read -r llmcf_pw || llmcf_pw=""',
    'no() { echo "dropbear=unavailable reason=$1"; exit 0; }',
    # Dropbear is stopped and disabled from here on: how its packages went (removal=removed|none|refused, reason=WORD
    # or -, packages=the ones removed, or kept by a refusal, comma-separated, or none; tool=the query program asked)
    "llmcf_tool=",
    'off() { echo "dropbear=$1 removal=$2 reason=$3 packages=${4:-none} tool=${llmcf_tool:-none}"; exit 0; }',
    'asroot() { if [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; '
    'else sudo -n "$@"; fi; }',
    "command -v sudo >/dev/null 2>&1 || no no-sudo",
    gate.dropbear_packages_sh(),
    # 1. the init system stops and disables the dropbear service
    "if [ -d /run/systemd/system ]; then",
    "  asroot systemctl disable --now dropbear >/dev/null 2>&1 || no start",
    "elif command -v rc-service >/dev/null 2>&1; then",
    "  asroot rc-update del dropbear default >/dev/null 2>&1; asroot rc-service dropbear stop >/dev/null 2>&1 || "
    "no start",
    "else no start; fi",
    # 2. only the packages that provide the SSH server: never dropbear-initramfs, and its dropbear-bin stays with it
    "llmcf_all=$(llmcf_dbpkgs | tr '\\n' ' ')",
    "llmcf_rm=",
    "for llmcf_p in $llmcf_all; do",
    "  case $llmcf_p in",
    f"    {'|'.join(gate.DROPBEAR_KEEP)}) ;;",
    '    dropbear-bin) case " $llmcf_all " in *" dropbear-initramfs "*) ;; *) llmcf_rm="$llmcf_rm $llmcf_p" ;; '
    "esac ;;",
    '    *) llmcf_rm="$llmcf_rm $llmcf_p" ;;',
    "  esac",
    "done",
    "llmcf_tool=$(llmcf_dbtool) || off disabled none no-manager",
    '[ -n "$llmcf_rm" ] || off disabled none no-packages',
    "llmcf_list=$(echo $llmcf_rm | tr ' ' ',')",
    "case $llmcf_tool in",
    "  dpkg)",
    "    command -v apt-get >/dev/null 2>&1 || off disabled none no-manager",
    # apt's simulation names every package the removal would take: only these, else nothing is removed
    '    llmcf_sim=$(apt-get -s remove $llmcf_rm 2>/dev/null) || off disabled refused remove "$llmcf_list"',
    "    for llmcf_n in $(printf '%s\\n' \"$llmcf_sim\" | sed -n -e 's/^Remv \\([^ ]*\\).*/\\1/p' "
    "-e 's/^Purg \\([^ ]*\\).*/\\1/p'); do",
    '      case " $llmcf_rm " in *" $llmcf_n "*) ;; *) off disabled refused dependents "$llmcf_list" ;; esac',
    "    done",
    f"    asroot {REMOVE_COMMANDS['dpkg']} $llmcf_rm >/dev/null 2>&1 || off disabled refused remove "
    '"$llmcf_list" ;;',
    # pacman -R (without -c or -dd) refuses by itself when another package needs one of them
    "  pacman)",
    f"    asroot {REMOVE_COMMANDS['pacman']} $llmcf_rm >/dev/null 2>&1 || off disabled refused remove "
    '"$llmcf_list" ;;',
    # Alpine is disable-only: apk del would also remove the libraries only Dropbear pulled in (ALPINE_NOTE)
    '  apk) off disabled refused dependents "$llmcf_list" ;;',
    # rpm's test removal fails when another package needs one of them; dnf or zypper then remove only these
    "  rpm)",
    '    asroot rpm -e --test $llmcf_rm >/dev/null 2>&1 || off disabled refused dependents "$llmcf_list"',
    "    if command -v dnf >/dev/null 2>&1; then",
    f"      asroot {REMOVE_COMMANDS['dnf']} $llmcf_rm >/dev/null 2>&1 || off disabled refused remove "
    '"$llmcf_list"',
    "    elif command -v zypper >/dev/null 2>&1; then",
    f"      asroot {REMOVE_COMMANDS['zypper']} $llmcf_rm >/dev/null 2>&1 || off disabled refused remove "
    '"$llmcf_list"',
    "    else off disabled none no-manager; fi ;;",
    "esac",
    'off removed removed - "$llmcf_list"',
])
define("dropbear-disable", (), lambda rest="": DROPBEAR_DISABLE, gate=False,
       doc="after the person's separate yes: Dropbear stopped and disabled by the init system, then only its packages "
           "removed with the board's package manager (never dropbear-initramfs, never purge or autoremove)")


@dataclass(frozen=True)
class DropbearRemoval:
    """What dropbear-disable did: `ok` when Dropbear is stopped and disabled; `removal` removed (its packages are
    gone), none (nothing to remove, or no package manager the switch knows) or refused (the package manager would
    have taken other packages, or failed: nothing was removed); `reason` a word of SWITCH_REASONS ("" when its
    packages were removed); `packages` the ones removed, or kept by a refusal (fixed names only); `tool` the board's package query program
    (dpkg, pacman, apk, rpm; "" when it said none)."""
    ok: bool
    removal: str = ""
    reason: str = ""
    packages: Tuple[str, ...] = ()
    tool: str = field(default="", compare=False)   # a fact for the text, not part of what happened

    @property
    def removed(self) -> bool:
        return self.ok and self.removal == "removed"

    def as_dict(self) -> dict:
        """Fixed words only, for the audit log."""
        return {"removal": self.removal or "none", "reason": self.reason, "packages": list(self.packages),
                "tool": self.tool}


_REMOVALS = ("removed", "none", "refused")
_TOOLS = ("dpkg", "pacman", "apk", "rpm")
_WORD = re.compile(r"^[a-z][a-z-]{0,23}$", re.ASCII)


def _removal(text: str) -> DropbearRemoval:
    """The answer of dropbear-disable, fixed words only (anything else counts as unknown)."""
    for line in (text or "").splitlines():
        words = dict(w.split("=", 1) for w in line.split() if "=" in w)
        state = words.get("dropbear")
        if state is None:
            continue
        reason = words.get("reason", "-")
        reason = reason if _WORD.fullmatch(reason) else ("" if reason == "-" else "unknown")
        if state not in ("removed", "disabled"):
            return DropbearRemoval(False, reason=reason or "unknown")
        removal = words.get("removal", "none")
        packages = tuple(p for p in words.get("packages", "none").split(",") if gate.DROPBEAR_PACKAGE.fullmatch(p))
        tool = words.get("tool", "")
        return DropbearRemoval(True, removal=removal if removal in _REMOVALS else "none", reason=reason,
                               packages=packages, tool=tool if tool in _TOOLS else "")
    return DropbearRemoval(False, reason="unknown")


def remove_dropbear(run: Callable[..., object], password: str = "") -> DropbearRemoval:
    """dropbear-disable over `run` (the new sshd's login), only after the person's separate yes: Dropbear stopped and
    disabled, then its packages removed with the board's package manager (DropbearRemoval says what happened)."""
    if "\n" in password or "\r" in password:
        return DropbearRemoval(False, reason="sudo")
    try:
        result = run(gate.script("dropbear-disable"), stdin=[(password + "\n").encode("utf-8")],
                     timeout=INSTALL_TIMEOUT)
    except (socket.timeout, TimeoutError):
        return DropbearRemoval(False, reason="remove-timeout")
    return _removal(getattr(result, "out", ""))


def removal_text(done: DropbearRemoval) -> str:
    """What the person reads after the separate yes (the CLI's and the GUI's report of the switch)."""
    if done.reason == "remove-timeout":   # its disable comes first, so Dropbear may already be stopped
        return ("The removal of Dropbear gave no answer in time; it may still be running on the board, so Dropbear "
                "may already be stopped and its packages removed. OpenSSH keeps running.")
    if not done.ok:
        return (f"Dropbear could not be turned off ({SWITCH_REASONS.get(done.reason, done.reason)}); it keeps "
                "running next to OpenSSH.")
    if done.removed:
        return (f"Dropbear is off: stopped, disabled, and its packages ({', '.join(done.packages)}) removed with the "
                "board's package manager; their configuration files stay on the board.")
    if done.tool == "apk":
        return f"Dropbear is off: {ALPINE_NOTE}."
    note = REMOVAL_NOTES.get(done.reason, SWITCH_REASONS.get(done.reason, done.reason or "nothing was removed"))
    return f"Dropbear is off (stopped and disabled by the init system); {note}."


def disable_dropbear(run: Callable[..., object], password: str = "") -> Tuple[bool, str]:
    """Deprecated: remove_dropbear (which also says what happened to Dropbear's packages). (ok, reason)."""
    done = remove_dropbear(run, password)
    return done.ok, ("" if done.ok else done.reason)   # as before: no reason when Dropbear is off


def _answer(text: str, key: str) -> Tuple[bool, str, str]:
    """(ok, reason, state) of an answer key=STATE or key=unavailable reason=WORD."""
    for line in (text or "").splitlines():
        words = dict(w.split("=", 1) for w in line.split() if "=" in w)
        if key in words:
            if words[key] == "unavailable":
                return False, words.get("reason", "unknown"), ""
            return True, "", words[key]
    return False, "unknown", ""


def install(run: Callable[..., object], the_plan: SwitchPlan, password: str = "") -> Tuple[bool, str, str]:
    """openssh-install over `run` (the pairing's unrestricted login), sudo -n or the person's one-time `password`
    (on stdin). (ok, reason, "installed"|"present")."""
    if "\n" in password or "\r" in password:
        return False, "sudo", ""
    try:
        result = run(gate.script("openssh-install", the_plan.manager, str(the_plan.port)),
                     stdin=[(password + "\n").encode("utf-8")], timeout=INSTALL_TIMEOUT)
    except (socket.timeout, TimeoutError):
        return False, "install-timeout", ""
    return _answer(getattr(result, "out", ""), "openssh")


def banner_of(host: str, port: int, timeout: float = 5.0) -> str:
    """The SSH identification line the server at host:port sends first ("" when nothing answers)."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            data = sock.recv(256)
    except OSError:
        return ""
    line = data.split(b"\n", 1)[0].decode("ascii", errors="replace").strip()
    return line if line.startswith("SSH-") else ""


def wait_until_answers(host: str, port: int, *, seconds: float = WAIT_SECONDS, banner: Callable[..., str] = banner_of,
                       clock: Callable[[], float] = time.monotonic,
                       sleep: Callable[[float], None] = time.sleep) -> str:
    """Waits (the current session stays open meanwhile) until an OpenSSH server answers at host:port; its banner, or
    "" when none did within `seconds`."""
    end = clock() + seconds
    while True:
        seen = banner(host, port)
        if "openssh" in seen.lower():
            return seen
        if clock() >= end:
            return ""
        sleep(1.0)


__all__ = ["ALPINE_NOTE", "DROPIN", "DropbearRemoval", "REMOVAL_NOTES", "REMOVE_COMMANDS", "FAMILY_MANAGER", "SSH_SOCKET",
           "INSTALL_COMMANDS", "INSTALL_TIMEOUT", "PACKAGES", "PACMAN_SYNC_DIR",
           "PREPARE_COMMANDS", "SWITCH_PORT", "SWITCH_REASONS", "SwitchPlan",
           "SwitchUnavailable", "banner_of", "disable_dropbear", "install", "manager_for", "plan",
           "remove_dropbear", "removable_packages", "removal_text",
           "wait_until_answers"]
