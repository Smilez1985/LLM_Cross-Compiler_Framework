#!/usr/bin/env python3
"""
What a pack of the person's own work must not carry out of this computer, found by its shape (docs/DESIGN.md, Community
packs: the release check).

Two classes, both for the text files of a target, recipe or block pack (never for crawled pages, which are other
people's public text and name system folders and domains of their own):

- certain: personal by its kind, replaced without asking: the serial number, the machine id and the UUID of a probe,
  the host name line of a probe, a known_hosts entry;
- to review: private to this computer, but written into a script or a file that a replacement can change: a path on
  a network drive or an external volume, a Windows drive or share path, an internal domain, an SSH configuration line,
  the key file of `ssh -i`. The export stops at these, shows the place with a proposed replacement (an example), and
  goes on only when the person accepts the proposals or has edited the lines.

What a path or a name is cannot be told by its shape alone (a folder below /opt may be a vendor's or a company's), so
only the shapes that are private by their nature are here; the rest is the wizard's local layer (the design's row
"The wizard's local layer for private values"). Standard library only, no import from orchestrator.
"""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# The text a proposal puts in must never be found again (an export that accepted its proposals passes the check), and it
# must be safe in a shell script and a YAML file: no < or >, which a script reads as a redirection (the first version's
# "<serial>" was a syntax error in a build script).
EXAMPLE_PATH = "/path/to/example"
EXAMPLE_WINDOWS_PATH = "C:\\path\\to\\example"
EXAMPLE_SHARE = "\\\\server\\share"
EXAMPLE_HOST = "host.example.com"
EXAMPLE_KEY = "~/.ssh/id_example"


@dataclass(frozen=True)
class Finding:
    kind: str        # what it is, in words
    start: int       # offset in the text
    end: int
    line: int        # 1-based
    original: str
    proposal: str
    certain: bool    # replaced without asking (the others stop the export)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _find(pattern: "re.Pattern[str]", text: str, kind: str, proposal: "str | Callable[[re.Match], str]", *,
          group: int = 0, certain: bool = False, accept: Optional[Callable[[re.Match], bool]] = None) -> List[Finding]:
    found = []
    for m in pattern.finditer(text):
        original = m.group(group)
        if not original or (accept is not None and not accept(m)):
            continue
        new = proposal(m) if callable(proposal) else proposal
        if new == original:
            continue
        found.append(Finding(kind, m.start(group), m.end(group), _line_of(text, m.start(group)), original, new, certain))
    return found


# --- to review ------------------------------------------------------------------------------------------------------

_SEG = r"[\w.@+\-]+"
_MOUNT = re.compile(r"(?<![\w.:/~<>$\-])(?:/mnt/|/media/|/Volumes/|/run/media/|/net/|/nfs/)" + _SEG + r"(?:/" + _SEG + r")+")
_WIN_PATH = re.compile(r"(?<![\w])[A-Za-z]:\\(?:[^\\/:*?\"<>|\s]+\\)*[^\\/:*?\"<>|\s]+")
_WIN_SYSTEM = ("c:\\windows", "c:\\program files", "c:\\programdata", "c:\\users", "c:\\path\\to\\example")
_UNC = re.compile(r"(?<![\w\\])\\\\[\w.\-]+\\[\w.$\-]+(?:\\[^\\\s]+)*")
# Internal domains: one label before lan, localdomain, intranet or corp (nas.lan), two before internal, local, home or
# private (git.firma.internal); the first label is no object of a program (self.cfg.internal).
_HOST = re.compile(
    r"(?<![\w.\-])(?:(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+(?:lan|localdomain|intranet|corp)"
    r"|(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.){2,}(?:internal|local|home|private|priv))(?![\w.\-])(?::\d+)?")
_NOT_A_HOST = {"self", "cls", "os", "sys", "this", "config", "cfg", "settings", "args", "ctx", "app"}
_SSH_CONFIG = re.compile(r"(?im)^([ \t]*(HostName|User|IdentityFile|CertificateFile|ProxyJump)[ \t]+)(\S.*?)[ \t]*$")
_SSH_CONFIG_PROPOSAL = {"hostname": EXAMPLE_HOST, "user": "user", "identityfile": EXAMPLE_KEY,
                        "certificatefile": EXAMPLE_KEY + "-cert.pub", "proxyjump": "jump.example.com"}
_COMMON_USERS = {"root", "pi", "ubuntu", "debian", "user", "dietpi", "runner", "llmcf"}
_SSH_KEY_OPTION = re.compile(r"(?i)\b(?:ssh|scp|sftp|rsync)\b[^\n|;&]{0,200}?[ \t]-i[ \t]+['\"]?([^\s'\"]+)")


def _host_ok(m: "re.Match") -> bool:
    first = m.group(0).split(".", 1)[0].lower()
    return first not in _NOT_A_HOST


def _host_proposal(m: "re.Match") -> str:
    port = re.search(r":\d+$", m.group(0))
    return EXAMPLE_HOST + (port.group(0) if port else "")


def _ssh_config_ok(m: "re.Match") -> bool:
    key, value = m.group(2).lower(), m.group(3).strip().strip("\"'")
    if "example" in value or value.startswith(("%", "$", "<")):
        return False
    return not (key == "user" and value.lower() in _COMMON_USERS)


def _ssh_key_ok(m: "re.Match") -> bool:
    path = m.group(1)
    return not (path.startswith(("$", "<", "{")) or "example" in path)


# --- certain --------------------------------------------------------------------------------------------------------

_SERIAL = re.compile(r"(?i)\b(?:serial(?:[ _-]?(?:number|no\.?|num))?|serialno|ro\.serialno|ro\.boot\.serialno|"
                     r"board[-_]serial|product[-_]serial|chassis[-_]serial)\b[ \t]*[=:][ \t]*[\"']?"
                     r"([A-Za-z0-9][A-Za-z0-9\-]{3,63})")
_MACHINE_ID = re.compile(r"(?i)\b[\w\-]*(?:uuid|machine[-_]?id)[\w\-]*[ \t]*[=:][ \t]*[\"']?([0-9A-Fa-f][0-9A-Fa-f\-]{15,63})")
_HOST_DECL = re.compile(r"(?m)^[ \t]*(?:export[ \t]+)?(?:HOSTNAME|hostname|Hostname|NODENAME|nodename|Static hostname|"
                        r"Transient hostname)[ \t]*[=:][ \t]*[\"']?([A-Za-z0-9][A-Za-z0-9.\-]{0,252})")
_KNOWN_HOSTS = re.compile(r"(?m)^[\w.\-,\[\]:*?]+[ \t]+(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-\S+|sk-\S+)[ \t]+AAAA\S+.*$")


def findings(text: str) -> List[Finding]:
    """Every private datum in `text` that the replacements know, without overlaps, in order."""
    found: List[Finding] = []
    found += _find(_MOUNT, text, "a path on a network drive or an external volume", EXAMPLE_PATH)
    found += _find(_WIN_PATH, text, "a Windows drive path", EXAMPLE_WINDOWS_PATH,
                   accept=lambda m: not m.string[m.start():m.start() + 40].lower().startswith(_WIN_SYSTEM))
    found += _find(_UNC, text, "a Windows network share", EXAMPLE_SHARE,
                   accept=lambda m: m.group(0) != EXAMPLE_SHARE)
    found += _find(_HOST, text, "an internal domain", _host_proposal, accept=_host_ok)
    found += _find(_SSH_CONFIG, text, "a line of an SSH configuration",
                   lambda m: _SSH_CONFIG_PROPOSAL[m.group(2).lower()], group=3, accept=_ssh_config_ok)
    found += _find(_SSH_KEY_OPTION, text, "the key file of an ssh command", EXAMPLE_KEY, group=1, accept=_ssh_key_ok)
    found += _find(_SERIAL, text, "a serial number", "serial-removed", group=1, certain=True)
    found += _find(_MACHINE_ID, text, "a machine id or UUID", "machine-id-removed", group=1, certain=True)
    found += _find(_HOST_DECL, text, "a host name", "host-removed", group=1, certain=True,
                   accept=lambda m: m.group(1).lower() not in ("localhost", "localhost.localdomain"))
    found += _find(_KNOWN_HOSTS, text, "a known_hosts entry", "# known_hosts entry removed", certain=True)
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    out: List[Finding] = []
    for f in found:
        if out and f.start < out[-1].end:
            continue   # inside the one before (a host name inside an ssh line): the longer one stands
        out.append(f)
    return out


def apply(text: str, chosen: List[Finding]) -> str:
    """`text` with the proposals of `chosen` put in."""
    out = text
    for f in sorted(chosen, key=lambda f: f.start, reverse=True):
        out = out[:f.start] + f.proposal + out[f.end:]
    return out


def certain(text: str) -> str:
    """`text` with the certainly personal data replaced (a probe's serial number, machine id and host name)."""
    return apply(text, [f for f in findings(text) if f.certain])


def to_review(text: str) -> List[Finding]:
    """What stops an export until the person accepts the proposals."""
    return [f for f in findings(text) if not f.certain]


def describe(f: Finding, where: str, *, with_original: bool) -> str:
    """One line for the person; the original only where the person is the reader (never for an AI)."""
    shown = f"`{f.original}` -> " if with_original else ""
    return f"{where}:{f.line}: {f.kind}: {shown}`{f.proposal}`"


def kinds(items: List[Finding]) -> Tuple[str, ...]:
    return tuple(sorted({f.kind for f in items}))
