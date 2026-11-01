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
- the setup operation dropbear-disable: only after a separate yes of the person (DietPi through dietpi-software,
  else the init system's own commands); the framework never removes Dropbear on its own.

DietPi: dietpi-software keeps one SSH server and replaces Dropbear in the same step it installs OpenSSH, which would
end Dropbear before the person's separate yes and could cut the running session. So the install uses DietPi's own
package manager underneath (apt), and dietpi-software does the removal of Dropbear when the person says yes to it.

Every step is written to the audit log by the caller (remote/pairing.py), with the plan's fixed words only.
"""

import socket
import time
from dataclasses import dataclass
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
# dietpi-software's software ID of Dropbear, used only for its removal after the person's separate yes. To be
# confirmed on a real DietPi board (dietpi-software list): when the ID is wrong or dietpi-software refuses, the removal
# falls back to the init system (systemctl disable --now dropbear), so Dropbear is never left half removed.
DIETPI_DROPBEAR_ID = "104"


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
    there already), the `port` of the new sshd and the person's `lines`."""
    manager: str
    packages: Tuple[str, ...]
    port: int
    dietpi: bool
    lines: Tuple[str, ...]

    def as_dict(self) -> dict:
        return {"manager": self.manager, "packages": list(self.packages), "port": self.port, "dietpi": self.dietpi}


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
    if dietpi:
        lines.append("On DietPi, OpenSSH is installed with apt (DietPi's own package manager underneath), because "
                     "dietpi-software would replace Dropbear in the same step; dietpi-software removes Dropbear "
                     "only when you say yes to that question.")
    return SwitchPlan(manager=manager, packages=packages, port=int(port), dietpi=dietpi, lines=tuple(lines))


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
    "set -u",
    'IFS= read -r llmcf_pw || llmcf_pw=""',
    'no() { echo "dropbear=unavailable reason=$1"; exit 0; }',
    'asroot() { if [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; '
    'else sudo -n "$@"; fi; }',
    "command -v sudo >/dev/null 2>&1 || no no-sudo",
    "llmcf_done=no",
    # DietPi's own tool first (it keeps its software list right); its ID of Dropbear: DIETPI_DROPBEAR_ID
    "if [ -x /boot/dietpi/dietpi-software ] || command -v dietpi-software >/dev/null 2>&1; then",
    "  llmcf_ds=$(command -v dietpi-software 2>/dev/null || echo /boot/dietpi/dietpi-software)",
    f'  asroot "$llmcf_ds" uninstall {DIETPI_DROPBEAR_ID} >/dev/null 2>&1 && llmcf_done=yes',
    "fi",
    # without dietpi-software, or when it refused: the init system stops and disables the dropbear service
    'if [ "$llmcf_done" = no ]; then',
    "  if [ -d /run/systemd/system ]; then",
    "    asroot systemctl disable --now dropbear >/dev/null 2>&1 || no start",
    "  elif command -v rc-service >/dev/null 2>&1; then",
    "    asroot rc-update del dropbear default >/dev/null 2>&1; asroot rc-service dropbear stop >/dev/null 2>&1 || "
    "no start",
    "  else no start; fi",
    "fi",
    'echo "dropbear=disabled"',
])
define("dropbear-disable", (), lambda rest="": DROPBEAR_DISABLE, gate=False,
       doc="after the person's separate yes: Dropbear off (DietPi: dietpi-software, else or when it refuses the init "
           "system)")


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


def disable_dropbear(run: Callable[..., object], password: str = "") -> Tuple[bool, str]:
    """dropbear-disable over `run`, only after the person's separate yes. (ok, reason)."""
    if "\n" in password or "\r" in password:
        return False, "sudo"
    result = run(gate.script("dropbear-disable"), stdin=[(password + "\n").encode("utf-8")])
    ok, reason, _state = _answer(getattr(result, "out", ""), "dropbear")
    return ok, reason


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


__all__ = ["DIETPI_DROPBEAR_ID", "DROPIN", "FAMILY_MANAGER", "SSH_SOCKET", "INSTALL_COMMANDS", "INSTALL_TIMEOUT", "PACKAGES", "PACMAN_SYNC_DIR",
           "PREPARE_COMMANDS", "SWITCH_PORT", "SWITCH_REASONS", "SwitchPlan",
           "SwitchUnavailable", "banner_of", "disable_dropbear", "install", "manager_for", "plan",
           "wait_until_answers"]
