#!/usr/bin/env python3
"""
Reads what harness_watch.sh recorded on the board and reports it (DESIGN "A harness on the board is the person's
opt-in"): the outcome by the exit codes the harness names (done, done with findings, failed), what changed (packages,
accounts, listening ports, files in the system's places) and, where strace ran, what the installer did (programs it
started, network targets, files it wrote). llmcf never blocks the installer; it flags what deserves a look:

  warn    a sudoers file or an authorized_keys file written, a new account with uid 0, a setuid or setgid bit set, a
          program started from /tmp, /var/tmp or /dev/shm, a download piped into a shell, a firewall switched off, a
          port opened on every address
  notice  a kernel module loaded or built, the boot configuration changed, a system unit added, a port opened on
          loopback, a connection to an address outside the local network

It also says whether the installer brought its own account(s) and service unit(s) (the person's decision: llmcf never
nests an account or a service of its own around a harness that brings its own): new rows of the account file, and
unit files it added or wrote in a systemd system folder or /etc/init.d. Only when it brought neither does the report
say so and point to the roadmap (an own account and service for a harness, only as the person's opt-in); the
framework sets none up.

The report holds no file contents, only paths, sizes and names; it is the person's to read (and the same text goes to
the audit log). Codes are fixed words, so GUI, CLI and an AI read the same report. The records are read line by line
(a trace of a long run can be large): only the lines that differ are ever held in memory.
"""

import ipaddress
import re
import socket
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

TMP_PLACES = ("/tmp/", "/var/tmp/", "/dev/shm/")
FIREWALL_OFF = (("ufw", "disable"), ("iptables", "-F"), ("nft", "flush"), ("systemctl", "disable", "nftables"),
                ("systemctl", "stop", "nftables"), ("systemctl", "stop", "ufw"), ("systemctl", "disable", "ufw"))
MODULE_TOOLS = ("insmod", "modprobe", "dkms")

RE_EXECVE = re.compile(r'^(\d+)\s+execve\("([^"]*)", \[(.*?)\]')
RE_CONNECT4 = re.compile(r'connect\(\d+, \{sa_family=AF_INET, sin_port=htons\((\d+)\), sin_addr=inet_addr\("([0-9.]+)"\)')
RE_CONNECT6 = re.compile(r'connect\(\d+, \{sa_family=AF_INET6, sin6_port=htons\((\d+)\),.*?inet_pton\(AF_INET6, "([0-9a-fA-F:.]+)"')
RE_OPEN_W = re.compile(r'(?:openat\(AT_FDCWD, |open\(|creat\()"([^"]+)", (O_[A-Z_|]+)')
RE_RENAME = re.compile(r'rename(?:at2?)?\((?:AT_FDCWD, )?"([^"]+)", (?:AT_FDCWD, )?"([^"]+)"')
RE_UNLINK = re.compile(r'unlink(?:at)?\((?:AT_FDCWD, )?"([^"]+)"')
RE_CHMOD = re.compile(r'(?:fchmodat\(AT_FDCWD, |chmod\()"([^"]+)", (0[0-7]+)\)')
RE_ARG = re.compile(r'"((?:[^"\\]|\\.)*)"')
# A service unit of its own: a unit file directly in a systemd system folder (not a link in a .wants folder), or an
# init script (a board without systemd).
RE_UNIT = re.compile(r"/systemd/system/([^/]+\.(?:service|timer|socket|path|mount|automount|target))$|"
                     r"/etc/init\.d/([^/.][^/]*)$")
# What the installer brought of its own: both, accounts, units or neither (fixed words for the AI's view).
BRINGS = ("both", "accounts", "units", "neither")


@dataclass
class Flag:
    level: str          # warn or notice
    code: str           # a fixed word
    detail: str         # a path, a program, an address or an account name


@dataclass
class HarnessReport:
    mode: str                       # dry or run
    watch: str                      # strace or compare
    exit_code: Optional[int]
    outcome: str                    # done, findings or failed
    seconds: Optional[int]
    packages_added: List[str] = field(default_factory=list)
    packages_removed: List[str] = field(default_factory=list)
    packages_changed: List[str] = field(default_factory=list)
    accounts_added: List[str] = field(default_factory=list)
    ports_added: List[str] = field(default_factory=list)
    files_added: List[str] = field(default_factory=list)
    files_changed: List[str] = field(default_factory=list)
    files_removed: List[str] = field(default_factory=list)
    programs: Dict[str, int] = field(default_factory=dict)
    network: List[str] = field(default_factory=list)
    written: List[str] = field(default_factory=list)
    own_units: List[str] = field(default_factory=list)
    flags: List[Flag] = field(default_factory=list)

    def flag(self, level: str, code: str, detail: str) -> None:
        if not any(f.code == code and f.detail == detail for f in self.flags):
            self.flags.append(Flag(level, code, detail))

    @property
    def brings(self) -> str:
        """Whether the installer brought its own account(s) and service unit(s): both, accounts, units or neither."""
        if self.accounts_added and self.own_units:
            return "both"
        return "accounts" if self.accounts_added else ("units" if self.own_units else "neither")

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["flags"] = [f.__dict__ for f in self.flags]
        return d


def _iter_lines(path: Path) -> Iterator[str]:
    """The non-empty lines of a record, one by one (never the whole file in memory)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for ln in fh:
                ln = ln.rstrip("\r\n")
                if ln.strip():
                    yield ln
    except OSError:
        return


def _first(path: Path) -> Optional[str]:
    return next(_iter_lines(path), None)


def _int(path: Path) -> Optional[int]:
    line = _first(path)
    try:
        return int(line) if line is not None else None
    except ValueError:
        return None


def _packages(lines: Iterator[str]) -> Dict[str, str]:
    out = {}
    for ln in lines:
        name, _, version = ln.partition("\t")
        out[name.strip()] = version.strip()
    return out


def _files(lines: Iterator[str]) -> Dict[str, Tuple[str, str, str]]:
    out = {}
    for ln in lines:
        parts = ln.rsplit("|", 3)
        if len(parts) == 4:
            out[parts[0]] = (parts[1], parts[2], parts[3])
    return out


def port_label(kind: str, hexaddr: str) -> str:
    """'tcp 0100007F:1F90' -> 'tcp 127.0.0.1:8080' (the kernel's /proc/net notation)."""
    addr, _, port = hexaddr.partition(":")
    try:
        number = int(port, 16)
        raw = bytes.fromhex(addr)
        if len(raw) == 4:
            ip = socket.inet_ntop(socket.AF_INET, struct.pack("<I", struct.unpack(">I", raw)[0]))
        else:
            ip = socket.inet_ntop(socket.AF_INET6, b"".join(struct.pack("<I", struct.unpack(">I", raw[i:i + 4])[0])
                                                              for i in range(0, 16, 4)))
    except (ValueError, struct.error, OSError):
        return f"{kind} {hexaddr}"
    return f"{kind.rstrip('6')} [{ip}]:{number}" if ":" in ip else f"{kind} {ip}:{number}"


def _local(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def _outcome(code: Optional[int], exit_codes: Dict[int, str]) -> str:
    if code is None:
        return "failed"
    return exit_codes.get(code, "failed")


def read_report(folder: Path, exit_codes: Optional[Dict[int, str]] = None, mode: str = "run") -> HarnessReport:
    folder = Path(folder)
    exit_codes = exit_codes or {0: "done"}
    code = _int(folder / "exit_code")
    started, ended = _int(folder / "started"), _int(folder / "ended")
    watch = _first(folder / "watch") or "compare"
    rep = HarnessReport(mode=mode, watch=watch, exit_code=code, outcome=_outcome(code, exit_codes),
                        seconds=(ended - started) if started is not None and ended is not None else None)

    before, after = folder / "before", folder / "after"
    pb, pa = _packages(_iter_lines(before / "packages.txt")), _packages(_iter_lines(after / "packages.txt"))
    rep.packages_added = sorted(p for p in pa if p not in pb)
    rep.packages_removed = sorted(p for p in pb if p not in pa)
    rep.packages_changed = sorted(p for p in pa if p in pb and pa[p] != pb[p])

    ab = set(_iter_lines(before / "accounts.txt"))
    for row in sorted(set(_iter_lines(after / "accounts.txt")) - ab):
        name, _, rest = row.partition(":")
        uid = rest.split(":", 1)[0]
        rep.accounts_added.append(name)
        if uid == "0":
            rep.flag("warn", "account_uid0", name)

    for row in sorted(set(_iter_lines(after / "ports.txt")) - set(_iter_lines(before / "ports.txt"))):
        kind, _, hexaddr = row.partition(" ")
        label = port_label(kind, hexaddr)
        rep.ports_added.append(label)
        open_all = hexaddr.startswith("00000000:") or hexaddr.startswith("00000000000000000000000000000000:")
        rep.flag("warn" if open_all else "notice", "port_every_address" if open_all else "port_opened", label)

    fb, fa = _files(_iter_lines(before / "files.txt")), _files(_iter_lines(after / "files.txt"))
    rep.files_added = sorted(p for p in fa if p not in fb)
    rep.files_removed = sorted(p for p in fb if p not in fa)
    rep.files_changed = sorted(p for p in fa if p in fb and fa[p] != fb[p])
    for p in rep.files_added + rep.files_changed:
        _path_flags(rep, p, fa[p][2])

    if watch == "strace":
        _read_trace(rep, _iter_lines(folder / "trace.txt"))
    rep.own_units = own_units(rep.files_added + rep.written)
    return rep


def own_units(paths: List[str]) -> List[str]:
    """The names of the service units the installer brought (files it added or wrote: a unit file in a systemd
    system folder, or an init script), each once, sorted."""
    names = set()
    for path in paths:
        m = RE_UNIT.search(path)
        if m:
            names.add(m.group(1) or m.group(2))
    return sorted(names)


def _path_flags(rep: HarnessReport, path: str, mode: Optional[str] = None) -> None:
    if path.startswith("/etc/sudoers") or "/sudoers.d/" in path:
        rep.flag("warn", "sudoers_written", path)
    elif path.endswith("/.ssh/authorized_keys") or path.endswith("/.ssh/authorized_keys2"):
        rep.flag("warn", "authorized_keys_written", path)
    elif path.startswith("/boot/"):
        rep.flag("notice", "boot_changed", path)
    elif path.startswith("/lib/modules/") or path.startswith("/usr/lib/modules/"):
        rep.flag("notice", "kernel_module", path)
    elif path.startswith(("/etc/systemd/system/", "/lib/systemd/system/", "/usr/lib/systemd/system/")):
        rep.flag("notice", "system_unit", path)
    if mode and _setid(mode):
        rep.flag("warn", "setid_bit", path)


def _setid(mode: str) -> bool:
    """Whether an octal mode ("4755" from stat, "04755" from strace) has the setuid or setgid bit."""
    try:
        return bool(int(mode, 8) & 0o6000)
    except ValueError:
        return False


def _read_trace(rep: HarnessReport, lines: Iterator[str]) -> None:
    downloaded: Set[str] = set()
    written: Set[str] = set(rep.written)   # a long trace writes many paths: a set to look them up
    for ln in lines:
        m = RE_EXECVE.search(ln)
        if m and ln.rstrip().endswith("= 0"):
            program, argv = m.group(2), [a.encode().decode("unicode_escape", "replace") for a in RE_ARG.findall(m.group(3))]
            name = program.rsplit("/", 1)[-1]
            rep.programs[name] = rep.programs.get(name, 0) + 1
            if program.startswith(TMP_PLACES):
                rep.flag("warn", "ran_from_tmp", program)
            if name in MODULE_TOOLS:
                rep.flag("notice", "kernel_module", " ".join(argv[:3]))
            words = (name, *argv[1:])
            if any(words[:len(pattern)] == pattern for pattern in FIREWALL_OFF):
                rep.flag("warn", "firewall_off", " ".join(words[:4]))
            if name in ("sh", "bash", "dash", "ash") and "-c" in argv:
                script = argv[argv.index("-c") + 1] if argv.index("-c") + 1 < len(argv) else ""
                if re.search(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|da|a)?sh\b", script):
                    rep.flag("warn", "download_piped_to_shell", script[:120])
            if name in ("curl", "wget"):
                downloaded.add(name)
            continue
        for rx in (RE_CONNECT4, RE_CONNECT6):
            m = rx.search(ln)
            if m:
                port, ip = m.group(1), m.group(2)
                target = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
                if target not in rep.network:
                    rep.network.append(target)
                if not _local(ip):
                    rep.flag("notice", "outside_network", target)
                break
        m = RE_OPEN_W.search(ln)
        if m and any(f in m.group(2) for f in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")) \
                and "= -1" not in ln:
            path = m.group(1)
            if path not in written and not path.startswith(("/dev/", "/proc/")):
                written.add(path)
                rep.written.append(path)
                _path_flags(rep, path)
            continue
        m = RE_CHMOD.search(ln)
        if m and "= -1" not in ln:
            if _setid(m.group(2)):
                rep.flag("warn", "setid_bit", m.group(1))
            continue
        m = RE_RENAME.search(ln)
        if m and "= -1" not in ln:
            if m.group(2) not in written:
                written.add(m.group(2))
                rep.written.append(m.group(2))
                _path_flags(rep, m.group(2))


TEXT = {
    "en": {
        "done": "finished", "findings": "finished with findings", "failed": "failed",
        "account_uid0": "a new account with root's uid 0", "sudoers_written": "a sudoers file written",
        "authorized_keys_written": "an SSH authorized_keys file written", "setid_bit": "a setuid or setgid bit set",
        "ran_from_tmp": "a program started from a temporary folder", "firewall_off": "a firewall switched off",
        "download_piped_to_shell": "a download piped into a shell", "port_every_address": "a port opened on every address",
        "port_opened": "a port opened on loopback", "kernel_module": "a kernel module loaded or built",
        "boot_changed": "the boot configuration changed", "system_unit": "a system unit added or changed",
        "outside_network": "a connection outside the local network",
        "head": "Harness installer {mode}: {outcome} (exit code {code}), watched by {watch}.",
        "counts": "Packages: {pa} added, {pr} removed, {pc} changed. Accounts added: {acc}. Files: {fa} added, "
                  "{fc} changed, {fr} removed.",
        "net": "Network targets: {net}.", "none": "none",
        "own_both": "The installer brought its own accounts ({accounts}) and its own service units ({units}): the "
                    "framework adds no account and no service of its own for this harness.",
        "own_accounts": "The installer brought its own accounts ({accounts}) and no service unit: the framework adds "
                        "no account and no service of its own for this harness.",
        "own_units": "The installer brought its own service units ({units}) and no account: the framework adds no "
                     "account and no service of its own for this harness.",
        "own_neither": "The installer brought neither an account nor a service unit of its own. The framework sets "
                       "none up now: an own account and service for a harness are a later step of the roadmap (the "
                       "harness row), and only as your opt-in.",
        "own_dry": "A dry run: it brought no account and no service unit of its own (the run shows what the installer "
                   "brings).",
    },
    "de": {
        "done": "fertig", "findings": "fertig mit Befunden", "failed": "fehlgeschlagen",
        "account_uid0": "ein neues Konto mit der uid 0 von root", "sudoers_written": "eine sudoers-Datei geschrieben",
        "authorized_keys_written": "eine SSH-Datei authorized_keys geschrieben",
        "setid_bit": "ein setuid- oder setgid-Bit gesetzt", "ran_from_tmp": "ein Programm aus einem Temp-Ordner gestartet",
        "firewall_off": "eine Firewall abgeschaltet", "download_piped_to_shell": "ein Download direkt in eine Shell geleitet",
        "port_every_address": "ein Port auf allen Adressen geöffnet", "port_opened": "ein Port auf Loopback geöffnet",
        "kernel_module": "ein Kernelmodul geladen oder gebaut", "boot_changed": "die Boot-Konfiguration geändert",
        "system_unit": "eine System-Unit angelegt oder geändert",
        "outside_network": "eine Verbindung außerhalb des lokalen Netzes",
        "head": "Harness-Installer {mode}: {outcome} (Rückgabewert {code}), beobachtet mit {watch}.",
        "counts": "Pakete: {pa} neu, {pr} entfernt, {pc} geändert. Neue Konten: {acc}. Dateien: {fa} neu, "
                  "{fc} geändert, {fr} entfernt.",
        "net": "Netzziele: {net}.", "none": "keine",
        "own_both": "Der Installer hat eigene Konten ({accounts}) und eigene Service-Units ({units}) mitgebracht: "
                    "Das Framework legt für diesen Harness kein eigenes Konto und keinen eigenen Dienst an.",
        "own_accounts": "Der Installer hat eigene Konten ({accounts}) und keine Service-Unit mitgebracht: Das "
                        "Framework legt für diesen Harness kein eigenes Konto und keinen eigenen Dienst an.",
        "own_units": "Der Installer hat eigene Service-Units ({units}) und kein Konto mitgebracht: Das Framework legt "
                     "für diesen Harness kein eigenes Konto und keinen eigenen Dienst an.",
        "own_neither": "Der Installer hat weder ein Konto noch eine Service-Unit mitgebracht. Das Framework legt jetzt "
                       "keines an: Ein eigenes Konto und ein eigener Dienst für einen Harness sind ein späterer "
                       "Schritt der Roadmap (die Harness-Zeile), und nur als dein Opt-in.",
        "own_dry": "Ein Probelauf: Er hat kein eigenes Konto und keine eigene Service-Unit mitgebracht (der Lauf "
                   "zeigt, was der Installer mitbringt).",
    },
}


def render(rep: HarnessReport, lang: str = "en") -> str:
    t = TEXT.get(lang, TEXT["en"])
    watch = "strace" if rep.watch == "strace" else ("Vorher-Nachher-Vergleich" if lang == "de" else "a before and after comparison")
    mode = ("Probelauf" if rep.mode == "dry" else "Lauf") if lang == "de" else ("dry run" if rep.mode == "dry" else "run")
    lines = [t["head"].format(mode=mode, outcome=t[rep.outcome], code=rep.exit_code, watch=watch),
             t["counts"].format(pa=len(rep.packages_added), pr=len(rep.packages_removed), pc=len(rep.packages_changed),
                                acc=", ".join(rep.accounts_added) or t["none"], fa=len(rep.files_added),
                                fc=len(rep.files_changed), fr=len(rep.files_removed))]
    if rep.watch == "strace":
        lines.append(t["net"].format(net=", ".join(rep.network[:20]) or t["none"]))
    brings = rep.brings
    lines.append(t["own_dry"] if brings == "neither" and rep.mode == "dry" else
                 t[f"own_{brings}"].format(accounts=", ".join(rep.accounts_added), units=", ".join(rep.own_units)))
    for level in ("warn", "notice"):
        for f in (f for f in rep.flags if f.level == level):
            lines.append(f"{'!' if level == 'warn' else '-'} {t[f.code]}: {f.detail}")
    return "\n".join(lines)
