#!/usr/bin/env python3
"""
llmcf's own OpenSSH configuration file (DECISIONS round 25 Q4 and Q5; consolidation stage C wave 3c-2, chain boards).

Every OpenSSH call of the framework passes this file with -F (orchestrator/remote/transport.py openssh_options, the one
argv builder), so OpenSSH reads neither ~/.ssh/config nor /etc/ssh/ssh_config: the framework is sealed off from this
computer's SSH setup, as it is with its own known_hosts. The file lies in the framework's state folder
(<state>/ssh_config), is created on first use readable by this account only (as the framework's key) and holds only
what the person adds there, for example a ProxyJump for a board behind a jump host. paramiko reads no configuration
file at all (transport.open_client: no SSHConfig, look_for_keys and allow_agent off).

OpenSSH takes the first value it obtains for an option, and the command line comes first: the trust options of
openssh_options (StrictHostKeyChecking, UserKnownHostsFile, GlobalKnownHostsFile, CheckHostIP, UpdateHostKeys,
VerifyHostKeyDNS, BatchMode, IdentitiesOnly, the framework key) always win over the file. What the file may not hold
is refused when the person writes it, and again before each use (a hand edit): every option that would change how a
board's host key is trusted or which machine is reached, run a command or load code on this computer, read another
configuration file, or reuse or forward the connection (REFUSED, each with its reason). ProxyCommand and ProxyJump are
the exception: they connect through another computer or run a command, and the person adds them knowingly after
reading what they do (KNOWINGLY, shown plainly).

- config_path(state_dir) -> Path: the file; ensure(state_dir) creates it (owner-only) when it is missing.
- argument(path) -> str: the file as -F's argument (OpenSSH reads it literally, without '%' tokens; a Windows path with
  forward slashes, the argv quoting is subprocess's).
- tokens(text) -> str: a '%' written as '%%' (Q5) for the options OpenSSH expands tokens in (IdentityFile and -i,
  UserKnownHostsFile, ControlPath, ...); transport.openssh_file() quotes such a path for -o.
- check_lines(lines) -> LineCheck: what the person wants to write, line by line: refused lines with their reason, the
  lines that need the person's knowing yes, and lines that are no option line at all.
- blocks(text), block_lines(text, host), with_block(text, host, lines): the file's Host blocks (one per host the person
  set, by its exact Host argument), read and replaced as text.
- prepare(state_dir) -> Path: before each OpenSSH call: created when missing, made private to this account, and
  checked; SshConfigRefused (an OSError naming the file: reasons.local_problem) when it holds a refused line.
- in_home_config(text, host): the lines of one Host block of the person's ~/.ssh/config text (for the person-only
  import: person_steps.import_ssh_block shows it before anything is written).

The person's writer of the file is runtime/device/person_steps.py save_ssh_block (person-only: no module an AI can
reach imports it; tests/test_remote_trust.py follows the imports). This module writes nothing but the empty file.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Sequence, Tuple

CONFIG_NAME = "ssh_config"
MAX_BYTES = 64 * 1024
HEADER = ("# llmcf's own OpenSSH settings: the framework passes this file to OpenSSH with -F, so OpenSSH reads\n"
          "# neither ~/.ssh/config nor /etc/ssh/ssh_config. Add a board's settings in the Boards window\n"
          "# (SSH settings...) or with the command line's deploy ssh-config edit. The framework's trust options\n"
          "# stay on OpenSSH's command line and win over this file.\n")

# The reason kinds of a refused line, in plain words (the person's texts in l10n_boards carry the same kinds).
TRUST = "trust"        # how a board's host key is trusted, or which machine is reached
COMMAND = "command"    # runs a command or loads code on this computer (or on the board)
FILES = "files"        # reads another configuration file
REUSE = "reuse"        # reuses or forwards the connection
SESSION = "session"    # changes how the framework's commands run on the board
REASONS = {
    TRUST: "it would change how a board's host key is checked or which machine is reached; the framework's own "
           "known_hosts decides that",
    COMMAND: "it would run a command or load code on this computer or the board",
    FILES: "it would read another configuration file (such as ~/.ssh/config), which this file keeps out",
    REUSE: "it would reuse or forward the connection; the framework opens its own connection each time",
    SESSION: "it would change how the framework's commands run on the board (each is one command with its own input "
             "and output)",
}
# The options a line may not set (lower case, as OpenSSH reads keywords), each with its kind.
REFUSED: Dict[str, str] = {
    **{name: TRUST for name in ("stricthostkeychecking", "userknownhostsfile", "globalknownhostsfile",
                                "knownhostscommand", "hostkeyalias", "verifyhostkeydns", "updatehostkeys",
                                "checkhostip", "hostname", "canonicalizehostname", "canonicaldomains",
                                "canonicalizefallbacklocal", "canonicalizepermittedcnames", "canonicalizemaxdots",
                                "nohostauthenticationforlocalhost")},
    **{name: COMMAND for name in ("localcommand", "permitlocalcommand", "remotecommand", "pkcs11provider",
                                  "securitykeyprovider")},
    **{name: SESSION for name in ("sessiontype", "forkafterauthentication", "stdinnull", "requesttty")},
    "include": FILES,
    **{name: REUSE for name in ("controlmaster", "controlpath", "controlpersist", "forwardagent", "forwardx11",
                                "forwardx11trusted", "forwardx11timeout", "localforward", "remoteforward",
                                "dynamicforward", "tunnel", "tunneldevice", "gatewayports", "exitonforwardfailure",
                                "clearallforwardings", "permitremoteopen", "streamlocalbindmask",
                                "streamlocalbindunlink")},
}
# What the person adds knowingly, after reading what it does.
KNOWINGLY = {
    "proxyjump": "connects to the board through another computer (the jump host): OpenSSH first logs in there with "
                 "this file's settings for the jump host and OpenSSH's defaults, so the jump host's key is checked "
                 "against your own ~/.ssh/known_hosts and your own SSH keys log in there; the board's own host key "
                 "is still checked against the framework's known_hosts",
    "proxycommand": "runs this command on this computer for every connection to the board, and the connection goes "
                    "through it; the board's own host key is still checked against the framework's known_hosts",
}
# A Host argument: host names, addresses and OpenSSH's patterns (*, ?, !), several separated by spaces.
_HOST_ARGUMENT = re.compile(r"^[A-Za-z0-9.:_*?!\[\]%-]+( [A-Za-z0-9.:_*?!\[\]%-]+){0,15}$", re.ASCII)
_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)(?:\s*=\s*|\s+)(\S.*?)\s*$", re.ASCII)
# A path a shell reads as one word and OpenSSH's token expansion leaves alone (ProxyJump hands -F's path to the jump
# host's ssh unquoted, inside a command line that is %-expanded and run by the shell).
_SHELL_WORD = re.compile(r"^[A-Za-z0-9/_.:+@,=-]+$", re.ASCII)


class SshConfigRefused(OSError):
    """llmcf's own SSH settings file holds a line it never uses (a hand edit after it was written): the OpenSSH call
    is not made. An OSError naming the file, so the reason code is local_problem (something on this computer)."""

    def __init__(self, message: str, path: Path, problems: Sequence["LineProblem"] = ()):
        import errno
        super().__init__(errno.EPERM, message, str(path))
        self.problems = list(problems)


@dataclass(frozen=True)
class LineProblem:
    """One line the file may not hold: its number (1 = first line of what was checked), the option, the kind
    (TRUST, COMMAND, FILES, REUSE, "syntax" or "jump_path") and the reason in plain words."""
    number: int
    keyword: str
    kind: str
    reason: str

    def text(self) -> str:
        return f"line {self.number} ({self.keyword or 'no option'}): {self.reason}"


@dataclass(frozen=True)
class Knowingly:
    """A line the person adds knowingly (ProxyJump, ProxyCommand), with what it does in plain words."""
    number: int
    keyword: str
    line: str
    what: str


@dataclass
class LineCheck:
    refused: List[LineProblem] = field(default_factory=list)
    knowingly: List[Knowingly] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused


@dataclass(frozen=True)
class HostBlock:
    """A Host block of the file: its exact Host argument and its option lines (as written, without the Host line)."""
    host: str
    lines: Tuple[str, ...]


def config_path(state_dir: Path) -> Path:
    return Path(state_dir) / CONFIG_NAME


def tokens(text: str) -> str:
    """`text` with each '%' written as '%%' (DECISIONS round 25 Q5): OpenSSH expands %-tokens in IdentityFile (also
    -i's path), UserKnownHostsFile, ControlPath and a few more, so a '%' in one of the framework's own paths would
    otherwise name another file or stop OpenSSH ("unknown key")."""
    return str(text).replace("%", "%%")


def argument(path: Path) -> str:
    """The file as the argument of -F: OpenSSH opens it as written (no token expansion, no '~'); a Windows path is
    written with forward slashes, which OpenSSH for Windows reads as the same path (subprocess quotes a space)."""
    return path.as_posix() if isinstance(path, PureWindowsPath) or os.name == "nt" else str(path)


def ensure(state_dir: Path) -> Path:
    """The file, created with the header when it is missing (readable by this account only, as the framework key)."""
    from orchestrator.utils.atomic import owner_only, write_private
    path = config_path(state_dir)
    if not os.path.lexists(path):
        write_private(path, HEADER)
    try:
        owner_only(path)
    except OSError as e:   # as with the framework key: OpenSSH then says what it refuses (a local problem)
        logging.getLogger(__name__).warning("The framework's SSH settings file could not be made private to this "
                                            f"account ({e.strerror or e.__class__.__name__}).")
    return path


def read(state_dir: Path) -> str:
    """The file's text ("" when it does not exist yet); OSError when it cannot be read or is too large."""
    path = config_path(state_dir)
    if not os.path.lexists(path):
        return ""
    if path.is_symlink() or not path.is_file():
        raise OSError(f"{CONFIG_NAME} is not a plain file", str(path))
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise OSError(f"{CONFIG_NAME} is larger than {MAX_BYTES} bytes", str(path))
    return data.decode("utf-8", errors="replace")


def valid_host(host: str) -> bool:
    """Whether `host` can be a Host argument of the file (names, addresses, OpenSSH patterns; no quotes, no option)."""
    return bool(host) and not host.startswith("-") and bool(_HOST_ARGUMENT.fullmatch(host))


def check_lines(lines: Sequence[str], *, jump_path: Optional[str] = None, whole_file: bool = False) -> LineCheck:
    """The option lines of one Host block as the person wants to write them (`whole_file`: every line of the file,
    its Host lines included): each refused line with its reason, the lines that need the person's knowing yes. In a
    block a Host or Match line is refused (a block's lines are its options); in the whole file a Host line needs a
    valid argument and a Match line no `exec`. `jump_path`: the file's -F argument, which a ProxyJump hands to the
    jump host's ssh unquoted: a path a shell would split (a space, a '%', a quote) makes a ProxyJump refused with the
    way out (ProxyCommand)."""
    check = LineCheck()
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\n" in raw or "\r" in raw or "\0" in raw:
            check.refused.append(LineProblem(number, "", "syntax", "a line may not hold a line break"))
            continue
        match = _LINE.match(line)
        if match is None:
            check.refused.append(LineProblem(number, "", "syntax", "this is no 'Option value' line"))
            continue
        keyword, value = match.group(1), match.group(2)
        name = keyword.lower()
        if name in ("host", "match"):
            if name == "match" and any(w.lstrip("!").lower() == "exec" for w in value.split()):
                check.refused.append(LineProblem(number, keyword, COMMAND, "Match exec: " + REASONS[COMMAND]))
            elif not whole_file:
                check.refused.append(LineProblem(number, keyword, "syntax",
                                                 "a block's lines are its options; the Host is given on its own"))
            elif name == "host" and not valid_host(value):
                check.refused.append(LineProblem(number, keyword, "syntax",
                                                 "this is no host name, address or pattern OpenSSH reads"))
            continue
        if name in REFUSED:
            check.refused.append(LineProblem(number, keyword, REFUSED[name], REASONS[REFUSED[name]]))
            continue
        if name in KNOWINGLY:
            if name == "proxyjump" and jump_path is not None and not _SHELL_WORD.fullmatch(jump_path) \
                    and value.lower() != "none":
                check.refused.append(LineProblem(
                    number, keyword, "jump_path",
                    "OpenSSH hands the path of this file to the jump host's ssh without quotes, and the framework's "
                    "folder has a space, a '%' or another sign a shell reads in its path, so a ProxyJump cannot work "
                    "from there; write the jump as a ProxyCommand instead, for example: ProxyCommand ssh -W "
                    "[%h]:%p jump.example (that ssh uses your own settings for the jump host)"))
                continue
            check.knowingly.append(Knowingly(number, keyword, line, KNOWINGLY[name]))
    return check


def blocks(text: str) -> Tuple[List[str], List[HostBlock]]:
    """(the lines before the first Host or Match line, the Host blocks in order). A Match block is kept as text in the
    block before it; the person's blocks are Host blocks."""
    head: List[str] = []
    found: List[HostBlock] = []
    current: Optional[Tuple[str, List[str]]] = None
    for raw in (text or "").splitlines():
        match = _LINE.match(raw.strip())
        if match and match.group(1).lower() == "host":
            if current is not None:
                found.append(HostBlock(current[0], tuple(current[1])))
            current = (match.group(2).strip(), [])
            continue
        if current is None:
            head.append(raw)
        else:
            current[1].append(raw)
    if current is not None:
        found.append(HostBlock(current[0], tuple(current[1])))
    return head, found


def _options(lines: Sequence[str]) -> List[str]:
    """The option lines of a block as the file holds them (indented), without blank lines and comments at its end."""
    kept = [line.strip() for line in lines if line.strip()]
    return [f"    {line}" for line in kept]


def block_lines(text: str, host: str) -> Optional[List[str]]:
    """The option lines of the Host block whose Host argument is exactly `host` (None when there is none)."""
    for block in blocks(text)[1]:
        if block.host == host:
            return [line.strip() for line in block.lines if line.strip() and not line.strip().startswith("#")]
    return None


def with_block(text: str, host: str, lines: Sequence[str]) -> str:
    """The file's text with the Host block of `host` set to `lines` (added at the end when it is new; removed when
    `lines` holds no option); every other block and the head stay as they are."""
    head, found = blocks(text if text else HEADER)
    wanted = [line for line in lines if line.strip()]
    out: List[str] = list(head)
    done = False
    for block in found:
        if block.host == host:
            done = True
            if wanted:
                out += [f"Host {host}", *_options(wanted), ""]
            continue
        out += [f"Host {block.host}", *[line for line in block.lines]]
    if not done and wanted:
        if out and out[-1].strip():
            out.append("")
        out += [f"Host {host}", *_options(wanted), ""]
    result = "\n".join(out).rstrip("\n") + "\n"
    return result


def prepare(state_dir: Path) -> Path:
    """The file before an OpenSSH call: created when missing, private to this account, and checked; SshConfigRefused
    when it holds a line the framework never uses (a hand edit since it was written), OSError when it cannot be read
    or made private. Every OpenSSH call then reads only this file."""
    path = ensure(state_dir)
    text = read(state_dir)
    check = check_lines(text.splitlines(), jump_path=argument(path), whole_file=True)
    if not check.ok:
        raise SshConfigRefused(f"the framework's own SSH settings file holds a line it never uses: "
                               f"{check.refused[0].text()}", path, check.refused)
    return path


def in_home_config(text: str, host: str) -> Optional[List[str]]:
    """The option lines of the person's own ~/.ssh/config text for `host`: the first Host block whose Host argument
    names `host` exactly (one of its words; patterns are not matched), as written there. Include and Match blocks are
    not followed. None when there is none."""
    for block in blocks(text)[1]:
        if host in block.host.split():
            return [line.strip() for line in block.lines if line.strip() and not line.strip().startswith("#")]
    return None


__all__ = ["COMMAND", "CONFIG_NAME", "FILES", "HEADER", "HostBlock", "KNOWINGLY", "Knowingly", "LineCheck",
           "LineProblem", "REASONS", "REFUSED", "REUSE", "SESSION", "SshConfigRefused", "TRUST", "argument",
           "block_lines",
           "blocks", "check_lines", "config_path", "ensure", "in_home_config", "prepare", "read", "tokens",
           "valid_host", "with_block"]
