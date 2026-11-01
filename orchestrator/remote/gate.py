#!/usr/bin/env python3
"""
llmcf's command gate on a board (DECISIONS: no standing rights that can do more than needed; the framework's key on a
board may run only what llmcf needs, so a stolen key can do nothing else, also while llmcf is not running).

Every command the framework runs on a board is one of llmcf's OPERATIONS: a fixed verb with validated arguments,
never a free shell string. An operation is defined once, here or in orchestrator/runtime/device/board_ops.py (the
board commands of the domain), as its verb, the kinds of its arguments and the script it runs. That one definition
serves both ways of reaching a board:

- an unrestricted login (the person's one-time password while pairing, or a framework key from before the gate):
  script(verb, *args) is the operation's script with its validated arguments in place (a GateScript, a str that
  remembers its verb and arguments), sent as one `sh -c '...'` as every command was before;
- a restricted login (the framework's key with `command="/bin/sh ~/.config/llm-framework/llmcf-gate"` and no-pty,
  no-port-forwarding, no-agent-forwarding, no-X11-forwarding in authorized_keys): the board runs only its gate, a small
  POSIX sh program that render_gate() writes from the same definitions. The client sends `llmcf-gate VERB ARG...`
  (gate_call); the gate checks the line (only the characters no argument kind needs to be quoted for, the verb in its
  list, the number of arguments, each argument's kind: absolute paths without `.`/`..` parts, places only inside
  llmcf's own folders, hex IDs, numbers, words) and then runs its own copy of the operation with the arguments as
  environment values (LLMCF_A1, LLMCF_A2, ..., LLMCF_REST). Anything else gets one plain line on stderr
  (REFUSAL_PREFIX ...) and exit status REFUSED_STATUS, before anything runs. SFTP is refused too: uploads go through
  the gate's file operations (file-size, file-write, file-append), resumable as over SFTP.

A session does not need to know beforehand which kind of login it has: it sends the script form, and a board whose
key runs only the gate refuses that line with the gate's refusal; the session then knows (RemoteSession.gated) and
sends the same operation as a gate call (refused() below). Nothing ran on the board in between.

Operations with `gate=False` are the few setup steps an unrestricted login runs once (installing the gate and the
restricted key line, installing llmcf's exact sudo rules): the gate never runs them, so a stolen key cannot widen
itself. The arguments of every operation are checked here before a command is made (GateRefused) and again on the
board by the gate; a value that would need shell quoting is never a valid argument, so the script form puts each one
in place as it is (shlex.quote would leave it unchanged).
"""

import hashlib
import re
import shlex
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

from orchestrator.remote.key_tiers import DROPBEAR_PACKAGE as _DROPBEAR_PACKAGE

GATE_NAME = "llmcf-gate"
# Where the gate lives on the board, relative to the account's home folder (next to the runtime's API key folder).
GATE_REL = ".config/llm-framework/llmcf-gate"
# What the gate answers when it refuses a line (stderr), and its exit status then.
REFUSAL_PREFIX = "llmcf-gate: refused: "
REFUSED_STATUS = 126
# The options of the framework key's authorized_keys line besides command= (OpenSSH and Dropbear both read them; none
# of llmcf's operations needs a terminal or a forwarding). The line in the account's own home (gate-install) keeps
# exactly these: Dropbear refuses a key line with an option it does not know (and runs no ~/.ssh/rc).
KEY_OPTIONS = ("no-pty", "no-port-forwarding", "no-agent-forwarding", "no-X11-forwarding")
# The root-owned line (root-board, OpenSSH's sshd only) adds no-user-rc: sshd then never runs the account's
# ~/.ssh/rc, which the account may write, before the forced command (the gate).
ROOT_KEY_OPTIONS = KEY_OPTIONS + ("no-user-rc",)
# llmcf's own folders on a board. Uploads and scratch work may only write into the upload folders ("place": the
# staging and benchmark folders of the install folder, mktemp's .staging-XXXXXXXX and .bench.XXXXXXXX, and the private
# upload folders of a package deploy, /tmp/llm_deploy.XXXXXXXX); nothing the gate runs as a program ever lies there.
# A runtime build reaches the install folder only through runtime-check (a copy of the uploaded tarball in a folder of
# its own, .verify-XXXXXXXX, checked against the framework's signature, then unpacked) and runtime-install.
PLACE_HOME_REL = ".local/share/llm-framework"
INSTALL_ROOT_REL = PLACE_HOME_REL + "/llama.cpp"
PLACE_TMP_PREFIX = "/tmp/llm_deploy."   # nosec B108 - a folder on the board, made by mktemp with a random name
STAGING_PREFIX = ".staging-"
BENCH_PREFIX = ".bench."
VERIFY_PREFIX = ".verify-"
PATH_MAX = 1536
# llmcf's root-owned files on a board: the root helper and its sudo rules (the deploy setting "systemd";
# runtime/device/board_ops.py writes the helper's program), the gate itself, the framework key's authorized_keys line
# (sshd: Match User <account> / AuthorizedKeysFile KEYS_DIR/%u, so the account cannot change it) and the public key the
# board checks every runtime tarball and package against (SIGNERS, an OpenSSH allowed_signers file).
LIBEXEC_DIR = "/usr/local/libexec/llmcf"
ROOT_HELPER = LIBEXEC_DIR + "/llmcf-root"
ROOT_GATE = LIBEXEC_DIR + "/llmcf-gate"
SUDOERS_DIR = "/etc/sudoers.d"
KEYS_DIR = "/etc/ssh/llmcf_keys"
SIGNERS_DIR = "/etc/llmcf"
SIGNERS = SIGNERS_DIR + "/allowed_signers"
# The same key as a PEM public key, for a board whose ssh-keygen cannot check it (older than OpenSSH 8.1, or none, as
# on many Dropbear boards): the gate then checks the very same SSHSIG signature with openssl (1.1.1 or later).
SIGNERS_PEM = SIGNERS_DIR + "/signing_key.pem"
# The signing folder of a board without sudo (the home gate, a weaker tier the person agreed to): owned by the account,
# never written by a gate operation (they write only into llmcf's upload places).
HOME_SIGNING_REL = ".config/llm-framework/signing"
SSHD_CONFIG = "/etc/ssh/sshd_config"
# The namespace and the principal of the framework's board signatures (orchestrator/Core/community_pack.py
# BOARD_NAMESPACE and BOARD_SIGNER; tests/test_board_signing.py checks they are the same).
SIGN_NAMESPACE = "llmcf-board"
SIGN_PRINCIPAL = "llmcf"
# The owner of llmcf's root-owned files; the gate trusts no signer file another user could change.
ROOT_UID = 0
REST_MAX = 64


class GateRefused(ValueError):
    """An operation that is not one of llmcf's, or an argument that is not of its kind: no command is made."""


# --- argument kinds ---------------------------------------------------------------------------------------------------

_RE_PATH = re.compile(r"^/[A-Za-z0-9._+/-]{1,%d}$" % (PATH_MAX - 1), re.ASCII)
_RE_ARG = re.compile(r"^[A-Za-z0-9._+/:=,-]{1,%d}$" % PATH_MAX, re.ASCII)
_RE_WORD = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]{0,63}$", re.ASCII)


def _plain_path(value: str) -> bool:
    if not _RE_PATH.fullmatch(value) or "//" in value or value.endswith("/"):
        return False
    return all(part not in (".", "..") for part in value.split("/")[1:])


def _plain_arg(value: str) -> bool:
    return bool(_RE_ARG.fullmatch(value)) and ".." not in value.split("/")


@dataclass(frozen=True)
class Kind:
    """An argument kind: `check` here, `sh` the body of its check function in the gate (true when "$1" is one)."""
    name: str
    check: Callable[[str], bool]
    sh: str


_SH_PATH = ('case $1 in /*) ;; *) return 1 ;; esac; '
            'case $1 in *[!A-Za-z0-9._+/-]*|*//*|*/|*/./*|*/.|*/../*|*/..) return 1 ;; esac; '
            f'[ ${{#1}} -le {PATH_MAX} ]')
_ROOT = f'"$HOME"/{INSTALL_ROOT_REL}'
_RND = "????????"
_SH_PLACE = ('llmcf_k_path "$1" || return 1; '
             f'case $1 in {_ROOT}/{STAGING_PREFIX}{_RND}|{_ROOT}/{STAGING_PREFIX}{_RND}/*) return 0 ;; esac; '
             f'case $1 in {_ROOT}/{BENCH_PREFIX}{_RND}|{_ROOT}/{BENCH_PREFIX}{_RND}/*) return 0 ;; esac; '
             f'case $1 in {PLACE_TMP_PREFIX}{_RND}|{PLACE_TMP_PREFIX}{_RND}/*) return 0 ;; esac; return 1')
_SH_BUILD = ('llmcf_k_path "$1" || return 1; '
             f'case $1 in {_ROOT}/current|{_ROOT}/{"[0-9a-f]" * 16}) return 0 ;; esac; return 1')
_SH_VERIFIED = (f'llmcf_k_path "$1" || return 1; case $1 in {_ROOT}/{VERIFY_PREFIX}{_RND}) return 0 ;; esac; '
                'return 1')
# exactly a folder mktemp made (staging-create): eight letters or digits, no `/`, nothing below it
_MKTEMP = "[A-Za-z0-9]" * 8
_SH_STAGING = (f'llmcf_k_path "$1" || return 1; case $1 in {_ROOT}/{STAGING_PREFIX}{_MKTEMP}) return 0 ;; esac; '
               'return 1')
_RE_STAGING = re.compile(r"/.+/" + re.escape(INSTALL_ROOT_REL + "/" + STAGING_PREFIX) + r"[A-Za-z0-9]{8}", re.ASCII)


def _staging_folder(value: str) -> bool:
    """A staging folder of the install folder as staging-create makes it (here: its tail; the gate also compares the
    install folder with $HOME)."""
    return _plain_path(value) and bool(_RE_STAGING.fullmatch(value))
KINDS: Dict[str, Kind] = {k.name: k for k in (
    # an absolute path without `.` or `..` parts and without characters that would need quoting (read-only operations)
    Kind("path", _plain_path, _SH_PATH),
    # a path inside llmcf's upload folders on the board, the only places an upload or a scratch file is written to
    # (the gate checks where; here only that it is a plain path)
    Kind("place", _plain_path, _SH_PLACE),
    # the runtime's install folder itself (<home>/.local/share/llm-framework/llama.cpp)
    Kind("root", _plain_path, f'llmcf_k_path "$1" && [ "$1" = {_ROOT} ]'),
    # a runtime build of the install folder (current, or a build folder named by 16 hex digits): only runtime-install
    # puts one there, from the folder runtime-check checked
    Kind("build", _plain_path, _SH_BUILD),
    # the folder runtime-check unpacked a checked tarball into (<install folder>/.verify-XXXXXXXX)
    Kind("verified", _plain_path, _SH_VERIFIED),
    # exactly the staging folder staging-create made (<install folder>/.staging-XXXXXXXX), nothing below it: the only
    # folder runtime-check takes a tarball from (its checked folder is made next to it, in the install folder)
    Kind("staging", _staging_folder, _SH_STAGING),
    # what a clean-up may remove: an upload folder, a checked folder or a build (a removal runs nothing)
    Kind("removable", _plain_path, 'llmcf_k_place "$1" || llmcf_k_verified "$1" || llmcf_k_build "$1"'),
    # the parent of a private folder: /tmp or the install folder
    Kind("parent", _plain_path, '[ "$1" = /tmp ] || llmcf_k_root "$1"'),
    # the account's own home folder (the gate compares it with $HOME)
    Kind("home", _plain_path, 'llmcf_k_path "$1" && [ "$1" = "$HOME" ]'),
    # a program of a runtime build in llmcf's install folder: <home>/.local/share/llm-framework/llama.cpp/<build>/bin/...
    Kind("program", _plain_path,
         'llmcf_k_path "$1" || return 1; '
         f'case $1 in "$HOME"/{PLACE_HOME_REL}/llama.cpp/current/bin/llama-server|'
         f'"$HOME"/{PLACE_HOME_REL}/llama.cpp/current/bin/llama-bench) return 0 ;; esac; '
         f'case $1 in "$HOME"/{PLACE_HOME_REL}/llama.cpp/{"[0-9a-f]" * 16}/bin/llama-server|'
         f'"$HOME"/{PLACE_HOME_REL}/llama.cpp/{"[0-9a-f]" * 16}/bin/llama-bench) return 0 ;; esac; return 1'),
    Kind("id16", lambda v: bool(re.fullmatch(r"[0-9a-f]{16}", v, re.ASCII)),
         'case $1 in *[!0-9a-f]*) return 1 ;; esac; [ ${#1} -eq 16 ]'),
    Kind("sha256", lambda v: bool(re.fullmatch(r"[0-9a-f]{64}", v, re.ASCII)),
         'case $1 in *[!0-9a-f]*) return 1 ;; esac; [ ${#1} -eq 64 ]'),
    Kind("uint", lambda v: bool(re.fullmatch(r"[0-9]{1,10}", v, re.ASCII)),
         'case $1 in ""|*[!0-9]*) return 1 ;; esac; [ ${#1} -le 10 ]'),
    Kind("word", lambda v: bool(_RE_WORD.fullmatch(v)),
         'case $1 in ""|-*|*[!A-Za-z0-9._-]*) return 1 ;; esac; [ ${#1} -le 64 ]'),
    # one argument of a runtime program (an option, a number, a path): no `..` part, nothing that needs quoting
    Kind("arg", _plain_arg,
         'case $1 in ""|*[!A-Za-z0-9._+/:=,-]*|..|../*|*/../*|*/..) return 1 ;; esac; '
         f'[ ${{#1}} -le {PATH_MAX} ]'),
    Kind("yesno", lambda v: v in ("yes", "no"), 'case $1 in yes|no) return 0 ;; esac; return 1'),
    # the base64 part and the comment of a public key line (pairing only: its operation never runs in the gate)
    Kind("b64", lambda v: bool(re.fullmatch(r"[A-Za-z0-9+/]{16,8192}={0,3}", v, re.ASCII)),
         'case $1 in ""|*[!A-Za-z0-9+/=]*) return 1 ;; esac'),
    Kind("comment", lambda v: bool(re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", v, re.ASCII)),
         'case $1 in ""|*[!A-Za-z0-9._@-]*) return 1 ;; esac'),
)}


# --- operations -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Operation:
    """One of llmcf's operations on a board: `verb`, the kinds of its arguments, the kind of up to `rest_max` more
    arguments (`rest`, None: none), and `render(*values, rest=...)`, its script with the values in place: the script
    form gets the checked values themselves, the gate gets "$LLMCF_A1", ... and $LLMCF_REST. `gate_render`, when given,
    is the gate's own body (the probe: the gate checks the script it is sent before it runs it). `gate=False`: a setup
    step of an unrestricted login only, never run by the gate. `job`: the body of a job that only runs detached
    (start-<verb>, RemoteSession.run_detached). `refuse(args)`, when given, says why checked arguments are still not
    ones the operation takes ("" when they are), here before a command is made; the gate makes the same check on the
    board in its `gate_render` (run-program: no option of the shared forbidden list, server_policy.check_command)."""
    verb: str
    kinds: Tuple[str, ...]
    render: Callable[..., str]
    rest: Optional[str] = None
    rest_max: int = REST_MAX
    gate: bool = True
    gate_render: Optional[Callable[..., str]] = None
    job: bool = False
    doc: str = ""
    refuse: Optional[Callable[[Tuple[str, ...]], str]] = None

    def check(self, args: Sequence[str]) -> Tuple[str, ...]:
        args = tuple(args)
        n = len(self.kinds)
        if len(args) < n or (self.rest is None and len(args) > n) or len(args) > n + self.rest_max:
            raise GateRefused(f"{self.verb} takes {n} argument(s)" + (f" and up to {self.rest_max} more"
                                                                       if self.rest else ""))
        for i, value in enumerate(args):
            kind = KINDS[self.kinds[i] if i < n else self.rest]
            if not isinstance(value, str) or not kind.check(value):
                raise GateRefused(f"argument {i + 1} of {self.verb} is not a {kind.name}")
        why = self.refuse(args) if self.refuse is not None else ""
        if why:
            raise GateRefused(f"{self.verb}: {why}")
        return args


OPERATIONS: Dict[str, Operation] = {}


def define(verb: str, kinds: Sequence[str] = (), render: "str | Callable[..., str]" = "", *, rest: Optional[str] = None,
           rest_max: int = REST_MAX, gate: bool = True, gate_render: Optional[Callable[..., str]] = None,
           job: bool = False, doc: str = "", refuse: Optional[Callable[[Tuple[str, ...]], str]] = None) -> Operation:
    """Defines (or redefines, for a module loaded again) one operation. A str `render` is a format template: {0}, {1},
    ... are the arguments, {rest} the rest of them (sh braces are written twice, as in an f-string)."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", verb, re.ASCII):
        raise ValueError(f"not an operation verb: {verb!r}")
    for kind in list(kinds) + ([rest] if rest else []):
        if kind not in KINDS:
            raise ValueError(f"unknown argument kind {kind!r}")
    fn = (lambda *v, rest="", _t=render: _t.format(*v, rest=rest)) if isinstance(render, str) else render
    op = Operation(verb, tuple(kinds), fn, rest, rest_max, gate, gate_render, job, doc, refuse)
    OPERATIONS[verb] = op
    if job:   # its start: the detached wrapper around the job's body, a gate operation of its own
        def start(folder: str, *values: str, rest: str = "", _op=op) -> str:
            return detached_command(folder, _op.render(*values, rest=rest))

        def gate_start(folder: str, *values: str, rest: str = "", _op=op) -> str:
            return detached_command(folder, (_op.gate_render or _op.render)(*values, rest=rest))

        def refuse_start(args: Tuple[str, ...], _op=op) -> str:
            return _op.refuse(args[1:]) if _op.refuse is not None else ""
        OPERATIONS["start-" + verb] = Operation("start-" + verb, ("place",) + op.kinds, start, rest, rest_max,
                                                gate, gate_start if gate_render else None, False,
                                                f"starts the job {verb} on its own", refuse_start)
    return op


class GateScript(str):
    """The script of one operation with its checked arguments in place (what an unrestricted login runs as
    `sh -c '...'`); `verb` and `args` are what a restricted login sends instead (gate_call)."""
    verb: str
    args: Tuple[str, ...]

    def __new__(cls, text: str, verb: str, args: Tuple[str, ...]):
        obj = super().__new__(cls, text)
        obj.verb, obj.args = verb, args
        return obj


class GateCall(str):
    """`llmcf-gate VERB ARG...`: a command line for a board whose key runs only the gate. transport.sh_command sends
    it as it is (no `sh -c`: the gate splits the line itself)."""


def operation(verb: str) -> Operation:
    try:
        return OPERATIONS[verb]
    except KeyError:
        raise GateRefused(f"{verb!r} is not one of llmcf's board operations") from None


def script(verb: str, *args: str) -> GateScript:
    """The operation `verb` with `args` as a script (see the module docstring); GateRefused when it is no operation or
    an argument is not of its kind."""
    op = operation(verb)
    if op.job:
        raise GateRefused(f"{verb} only runs as a job (start-{verb})")
    args = op.check(args)
    fixed, rest = args[:len(op.kinds)], args[len(op.kinds):]
    return GateScript(op.render(*fixed, rest=" ".join(rest)), verb, args)


def job(verb: str, *args: str) -> GateScript:
    """The body of the job `verb` with its arguments (RemoteSession.run_detached starts it as start-<verb>)."""
    op = operation(verb)
    if not op.job:
        raise GateRefused(f"{verb} is not a job")
    args = op.check(args)
    fixed, rest = args[:len(op.kinds)], args[len(op.kinds):]
    return GateScript(op.render(*fixed, rest=" ".join(rest)), verb, args)


def gate_call(call: GateScript) -> GateCall:
    """The gate's form of an operation: `llmcf-gate VERB ARG...` (every argument was checked, none holds a space)."""
    if not isinstance(call, GateScript):
        raise GateRefused("only llmcf's operations reach a board whose key runs only the gate")
    if call.verb not in OPERATIONS or not OPERATIONS[call.verb].gate:
        raise GateRefused(f"{call.verb} is a setup step of an unrestricted login; the gate never runs it")
    return GateCall(" ".join((GATE_NAME, call.verb) + tuple(call.args)))


def is_refusal(status: int, err: str) -> bool:
    """Whether a board's answer is the gate's refusal of a line (nothing ran)."""
    return status == REFUSED_STATUS and (err or "").lstrip().startswith(REFUSAL_PREFIX)


def refusal_text(err: str) -> str:
    """The gate's own plain reason from its refusal line (without the prefix)."""
    for line in (err or "").splitlines():
        line = line.strip()
        if line.startswith(REFUSAL_PREFIX):
            return line[len(REFUSAL_PREFIX):][:200]
    return ""


# --- the gate on the board --------------------------------------------------------------------------------------------

def _fn(verb: str) -> str:
    return "llmcf_op_" + verb.replace("-", "_")


def _gate_values(op: Operation) -> Tuple[Tuple[str, ...], str]:
    return tuple(f'"$LLMCF_A{i + 1}"' for i in range(len(op.kinds))), "$LLMCF_REST"


def gate_operations() -> Iterable[Operation]:
    """The operations the gate runs (sorted by verb): every gate operation but the bodies of jobs."""
    return [OPERATIONS[v] for v in sorted(OPERATIONS) if OPERATIONS[v].gate and not OPERATIONS[v].job]


def render_gate() -> str:
    """The gate as a POSIX sh program (see the module docstring), made from the operations defined now. Its version
    is the sha256 of the program text without the version line; `llmcf-gate status` answers it."""
    ops = gate_operations()
    out = []
    for kind in KINDS.values():
        out.append(f"llmcf_k_{kind.name}() {{ {kind.sh}; }}")
    out.append("")
    for op in ops:
        values, rest = _gate_values(op)
        body = (op.gate_render or op.render)(*values, rest=rest)
        out.append(f"{_fn(op.verb)}() {{\n{body}\n}}")
    out.append("")
    out.append('case $llmcf_verb in')
    for op in ops:
        n = len(op.kinds)
        count = (f'[ $# -eq {n} ]' if op.rest is None else f'[ $# -ge {n} ] && [ $# -le {n + op.rest_max} ]')
        checks = [count] + [f'llmcf_k_{k} "${i + 1}"' for i, k in enumerate(op.kinds)]
        lines = [f'  {op.verb})',
                 f'    {" && ".join(checks)} || llmcf_refuse "the arguments of {op.verb} are not the ones it takes"']
        for i in range(n):
            lines.append(f'    LLMCF_A{i + 1}=${i + 1}; export LLMCF_A{i + 1}')
        if n:
            lines.append(f'    shift {n}')
        if op.rest:
            lines.append(f'    for llmcf_a in "$@"; do llmcf_k_{op.rest} "$llmcf_a" || '
                         f'llmcf_refuse "an argument of {op.verb} is not one it takes"; done')
            lines.append('    LLMCF_REST="$*"; export LLMCF_REST')
        lines.append(f'    set +f; unset IFS; {_fn(op.verb)}; exit $? ;;')
        out.extend(lines)
    out.append('  *) llmcf_refuse "unknown operation (the framework'"'"'s key runs only llmcf'"'"'s operations)" ;;')
    out.append('esac')
    body = "\n".join(out) + "\n"
    head = _GATE_HEAD.replace("@SIGNED@", signed_sh())
    version = hashlib.sha256((head + body).encode("utf-8")).hexdigest()[:16]
    return head.replace("@VERSION@", version) + body


def gate_version(text: str) -> str:
    """The version a gate program carries ("" when it carries none)."""
    m = re.search(r"^LLMCF_GATE_VERSION=([0-9a-f]{16})$", text or "", re.M | re.ASCII)
    return m.group(1) if m else ""


# --- the framework's signature on the board ---------------------------------------------------------------------------

def _octal(data: bytes) -> str:
    """`data` as a printf format of plain letters and octal escapes (POSIX printf \\ddd), for sh."""
    return "".join(chr(b) if chr(b).isascii() and (chr(b).isalnum() or chr(b) in "-_") else "\\%03o" % b
                   for b in data)


def _ssh_string(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def sshsig_prefix() -> bytes:
    """What the framework's SSHSIG signature signs before the sha512 of the file (PROTOCOL.sshsig): the magic, the
    namespace, an empty reserved string, the hash name and the length of the 64-byte digest."""
    return (b"SSHSIG" + _ssh_string(SIGN_NAMESPACE.encode("ascii")) + _ssh_string(b"") + _ssh_string(b"sha512")
            + (64).to_bytes(4, "big"))


# The size of a decoded SSHSIG blob of an Ed25519 key in the framework's namespace (the only form the openssl check
# takes: its last 64 bytes are the raw signature).
SSHSIG_ED25519_SIZE = (6 + 4 + 4 + (4 + 11 + 4 + 32) + 4 + len(SIGN_NAMESPACE) + 4 + 4 + 6
                       + 4 + (4 + 11 + 4 + 64))


def verify_sh() -> str:
    """The sh function llmcf_verify FILE SIG SIGNERS PEM: true when FILE carries the framework's SSHSIG signature SIG,
    checked with `ssh-keygen -Y verify` against the allowed_signers file SIGNERS (OpenSSH 8.1 or later) or, where
    ssh-keygen cannot check it (older, or none, as on many Dropbear boards), with openssl 1.1.1 or later against PEM
    (the same Ed25519 key): the signed message is rebuilt from sshsig_prefix() and the file's sha512, and the raw
    signature is the last 64 bytes of the decoded blob. Otherwise llmcf_why says why: old-ssh (ssh-keygen could not
    check it and openssl did not either) or no-verifier (neither tool is on the board)."""
    return "\n".join([
        "llmcf_verify() {",
        "  llmcf_why=no-verifier",
        "  if command -v ssh-keygen >/dev/null 2>&1; then",
        f'    ssh-keygen -Y verify -f "$3" -I {SIGN_PRINCIPAL} -n {SIGN_NAMESPACE} -s "$2" < "$1" >/dev/null 2>&1 '
        "&& return 0",
        # an ssh-keygen that knows -Y and still says no: the signature is not the framework's (openssl would agree)
        "    if ssh-keygen -Y verify </dev/null 2>&1 | grep -qi 'unknown option\\|illegal option\\|invalid option'; "
        "then llmcf_why=old-ssh; else llmcf_why=bad; return 1; fi",
        "  fi",
        '  [ -s "$4" ] || return 1',
        "  command -v openssl >/dev/null 2>&1 || return 1",
        # openssl older than 1.1.1 has no -rawin (it cannot check an Ed25519 signature of a message)
        "  openssl pkeyutl -help 2>&1 | grep -q rawin || return 1",
        "  llmcf_why=bad",
        # the scratch folder lies next to the file (a private folder of the caller), never in /tmp
        '  case $1 in */*) llmcf_t=${1%/*} ;; *) llmcf_t=. ;; esac',
        '  llmcf_t=$(mktemp -d "$llmcf_t/.llmcf-sig.XXXXXXXX") || return 1',
        "  llmcf_ok=1",
        "  if sed '1d;$d' \"$2\" | tr -d '\\n\\r' | openssl base64 -d -A > \"$llmcf_t/blob\" 2>/dev/null &&",
        f'    [ "$(wc -c < "$llmcf_t/blob" | tr -d " ")" -eq {SSHSIG_ED25519_SIZE} ] &&',
        '    tail -c 64 "$llmcf_t/blob" > "$llmcf_t/raw" &&',
        f"    {{ printf '{_octal(sshsig_prefix())}' && openssl dgst -sha512 -binary < \"$1\"; }} > \"$llmcf_t/msg\" &&",
        '    openssl pkeyutl -verify -pubin -inkey "$4" -rawin -in "$llmcf_t/msg" -sigfile "$llmcf_t/raw" '
        ">/dev/null 2>&1; then llmcf_ok=0; fi",
        '  rm -rf "$llmcf_t"',
        '  return "$llmcf_ok"',
        "}",
    ])


def signed_sh(signers: str = SIGNERS, root_uid: int = ROOT_UID) -> str:
    """The sh functions that check a file against the framework's signature on the board (DECISIONS 2026-10-31: a
    board runs only runtime tarballs and packages signed with the framework's key). Part of the gate's head and put in
    front of every script-form body that checks a signature (runtime-check, the package's install job):

    - llmcf_owned UID PATH, llmcf_rootowned PATH: PATH is a plain file or folder (no link) of that owner (`root_uid`)
      that no group and nobody else may write;
    - llmcf_trusted_signers: the allowed_signers file `signers`, its folder and the folder above are root-owned; or,
      only in a home gate (LLMCF_HOME_GATE: a board without sudo whose gate is not root-owned, the weaker tier the
      person agreed to), the account's own signing folder HOME_SIGNING_REL and every folder up to its home, owned by
      the account. It sets llmcf_sf (the signers file), llmcf_pf (its PEM twin, signing_key.pem next to it) and
      llmcf_po (their owner);
    - llmcf_must_sign: a signature is needed: always in the gate, and in an unrestricted login once the board holds
      the signers file (before its root step an unrestricted login runs anything anyway);
    - llmcf_signed FILE SIG: llmcf_verify (verify_sh: ssh-keygen, else openssl) of FILE against SIG with only the
      framework's key, only in its namespace; one plain line on stderr and status 1 otherwise.

    `signers` and `root_uid` are parameters for the tests only (a temporary folder, the test's own user)."""
    folder = signers.rsplit("/", 1)[0]
    parent = folder.rsplit("/", 1)[0] or "/"
    home = f'"$HOME"/{HOME_SIGNING_REL}'
    return "\n".join([
        "llmcf_owned() {",
        '  ls -ldn "$2" 2>/dev/null | (read -r llmcf_m llmcf_n llmcf_u llmcf_rest || exit 1',
        '    [ "$llmcf_u" = "$1" ] || exit 1',
        "    case $llmcf_m in -*|d*) ;; *) exit 1 ;; esac",
        "    case $llmcf_m in ?????w*|????????w*) exit 1 ;; esac)",
        "}",
        f'llmcf_rootowned() {{ llmcf_owned {int(root_uid)} "$1"; }}',
        "llmcf_trusted_signers() {",
        f"  llmcf_sf={signers}; llmcf_pf={folder}/signing_key.pem; llmcf_po={int(root_uid)}",
        f"  llmcf_rootowned {signers} && llmcf_rootowned {folder} && llmcf_rootowned {parent} && return 0",
        '  [ "${LLMCF_HOME_GATE-}" = 1 ] || return 1',
        "  llmcf_po=$(id -u 2>/dev/null) || return 1",
        f"  llmcf_sf={home}/allowed_signers; llmcf_pf={home}/signing_key.pem",
        f'  llmcf_owned "$llmcf_po" "$llmcf_sf" && llmcf_owned "$llmcf_po" {home} && '
        f'llmcf_owned "$llmcf_po" "$HOME"/{HOME_SIGNING_REL.rsplit("/", 1)[0]} && '
        'llmcf_owned "$llmcf_po" "$HOME"/.config && llmcf_owned "$llmcf_po" "$HOME"',
        "}",
        f'llmcf_must_sign() {{ [ "${{LLMCF_GATE-}}" = 1 ] || [ -e {signers} ]; }}',
        verify_sh(),
        "llmcf_signed() {",
        '  [ -f "$2" ] || { echo "it carries no signature of the framework\'s signing key" >&2; return 1; }',
        "  llmcf_trusted_signers || { echo \"this board holds no root-owned signing key of the framework (pair it "
        "again with the board's password)\" >&2; return 1; }",
        '  llmcf_owned "$llmcf_po" "$llmcf_pf" || llmcf_pf=/nonexistent',
        '  llmcf_verify "$1" "$2" "$llmcf_sf" "$llmcf_pf" && return 0',
        '  if [ "$llmcf_why" != bad ]; then',
        "    echo \"this board can check the framework's signature neither with ssh-keygen (OpenSSH 8.1 or later) nor "
        "with openssl (1.1.1 or later)\" >&2",
        "  else",
        "    echo \"its signature is not one of the framework's signing key (changed, or signed with another key)\" >&2",
        "  fi",
        "  return 1",
        "}",
    ])


_GATE_HEAD = """#!/bin/sh
# llmcf-gate: the command gate of the LLM Cross-Compiler Framework on this board (generated by the framework; pairing
# writes it again). The framework's SSH key runs only this program (command= in ~/.ssh/authorized_keys): it runs one of
# llmcf's operations, each a fixed verb with checked arguments, and refuses everything else with one plain line.
LLMCF_GATE_VERSION=@VERSION@
LLMCF_GATE=1; export LLMCF_GATE LLMCF_GATE_VERSION
LC_ALL=C; export LC_ALL
set -f
llmcf_refuse() { printf 'llmcf-gate: refused: %s\\n' "$1" >&2; exit 126; }
@SIGNED@
# A gate that is not root-owned is the home gate of a board without sudo (the weaker tier the person agreed to): its
# signing key is the account's own (llmcf_trusted_signers). Set here, whatever the login's environment said.
LLMCF_HOME_GATE=; llmcf_rootowned "$0" || LLMCF_HOME_GATE=1; export LLMCF_HOME_GATE
llmcf_line=${SSH_ORIGINAL_COMMAND-}
[ -n "$llmcf_line" ] || llmcf_refuse "no operation given: the framework's key opens no login shell"
case $llmcf_line in
  *[!-A-Za-z0-9._+/:=,\\ ]*) llmcf_refuse "the line holds characters none of llmcf's operations takes" ;;
esac
IFS=' '
# shellcheck disable=SC2086 # the checked line is split into its words on purpose (globbing is off)
set -- $llmcf_line
[ "${1-}" = llmcf-gate ] || llmcf_refuse "not one of llmcf's operations (the framework's key runs only llmcf-gate)"
shift
llmcf_verb=${1-}
[ $# -gt 0 ] && shift
"""


# --- the framework key's line in authorized_keys ----------------------------------------------------------------------

def key_options(gate_path: str, options: Tuple[str, ...] = KEY_OPTIONS) -> str:
    """The options before the framework's public key: the gate as forced command and the no-* `options`."""
    if not _plain_path(gate_path):
        raise GateRefused("the gate's path on the board is not a plain absolute path")
    return f'command="/bin/sh {gate_path}",' + ",".join(options)


def restricted_line(public_line: str, gate_path: str) -> str:
    """The restricted line in the account's own authorized_keys (gate-install; OpenSSH or Dropbear)."""
    return f"{key_options(gate_path)} {public_line}"


def root_key_line(public_line: str, gate_path: str) -> str:
    """The root-owned restricted line (root-board, OpenSSH's sshd only): also no-user-rc (ROOT_KEY_OPTIONS)."""
    return f"{key_options(gate_path, ROOT_KEY_OPTIONS)} {public_line}"


def is_restricted_line(line: str, gate_path: str) -> bool:
    return (line or "").strip().startswith(key_options(gate_path) + " ")


@dataclass(frozen=True)
class GateBundle:
    """What pairing installs on a board: the gate program (render_gate, with every operation of the framework
    defined), llmcf's root helper (None: no root actions) and its version; `signers`, the board's allowed_signers line
    of the framework's signing key ("" when the framework has none), and `selftest`, that key's signature of
    SELFTEST_DATA, which the root step checks with the board's own tools (ssh-keygen, else openssl against `pem`, the
    same key as a PEM public key) before it installs anything."""
    gate: str
    root_helper: Optional[str] = None
    notes: Tuple[str, ...] = field(default_factory=tuple)
    signers: str = ""
    selftest: str = ""
    missing: str = "no-signer"   # the reason word of the root step when there is no signer (board_ops.resolve_signer)
    pem: str = ""

    @property
    def version(self) -> str:
        return gate_version(self.gate)


# --- a job that runs on the board on its own (RemoteSession.run_detached and follow) -----------------------------------

# Files of a job that runs on the board on its own, in its private folder there (the names the deploy has used since
# N5: a folder on a board may hold them from a run before).
JOB_STARTED = ".lcf-install.started"
JOB_STATUS = ".lcf-install.status"
JOB_LOG = ".lcf-install.log"


def detached_command(folder: str, job: str) -> str:
    """The command that starts `job` in `folder` on the board on its own (nohup, nothing attached to the SSH session):
    its exit status goes into JOB_STATUS. The marker JOB_STARTED is made first, and a folder that has it is never
    started again, also when this command reaches the board twice. `folder` stands as it is given (a checked value, or
    the gate's "$LLMCF_A1"); the job is one quoted word for the `sh -c` nohup starts."""
    job = f"{job}; echo $? > {JOB_STATUS}"
    return (f"cd {folder} && if [ -e {JOB_STARTED} ]; then echo started=already; else : > {JOB_STARTED} && "
            f"(nohup sh -c {shlex.quote(job)} </dev/null >/dev/null 2>&1 &) && echo started=now; fi")


def poll_command(folder: str) -> str:
    """The exit status of the job in `folder`, `running`, or `gone` when the folder is gone (the board restarted)."""
    return f"cat {folder}/{JOB_STATUS} 2>/dev/null || {{ [ -d {folder} ] && echo running || echo gone; }}"


# --- the operations of the board session itself (orchestrator/remote/session.py, transport.py) -----------------------

define("ping", (), "exit 0", doc="the connection answers (a silent command is busy, the network did not drop)")
define("login-check", (), "true", doc="OpenSSH's login check")
define("mkprivate", ("parent", "word"), "mktemp -d {0}/{1}.XXXXXXXX", doc="a private folder made by mktemp")
define("file-sha256", ("place",), "sha256sum {0}", doc="the sha256 of an uploaded part")
define("file-prefix-sha256", ("uint", "place"), "head -c {0} {1} | sha256sum",
       doc="the sha256 of what of an upload arrived (resume)")
define("file-move", ("place", "place"), "mv -f {0} {1}", doc="an upload checked on the board takes its name")
define("file-remove", ("place",), "rm -f {0}", doc="a damaged upload goes")
define("file-create", ("place",), "umask 077 && : > {0}", doc="a new private upload file")
define("dir-exists", ("place",), "[ -d {0} ]", doc="the upload folder is still there after a drop")
define("file-size", ("place",), "if [ -f {0} ]; then wc -c < {0}; else echo none; fi",
       doc="how much of an upload arrived")
define("file-write", ("place",), "cat > {0}", doc="an upload on stdin")
define("file-append", ("place",), "cat >> {0}", doc="an upload continued on stdin")
define("job-poll", ("place",), lambda folder, rest="": poll_command(folder), doc="the exit status of a job on its own")
_STATUS_GATE = "\n".join([
    'llmcf_keys=home; llmcf_me=$(id -un 2>/dev/null) || llmcf_me=""',
    f'[ -n "$llmcf_me" ] && llmcf_rootowned "$0" && llmcf_rootowned {KEYS_DIR}/"$llmcf_me" && '
    f'llmcf_rootowned {KEYS_DIR} && llmcf_keys=root',
    "llmcf_sig=no; llmcf_trusted_signers && llmcf_sig=yes",
    # the board's key tier (remote/key_tiers.py): root-owned key line (root), root-owned gate with the line in the
    # account's own authorized_keys (dropbear), or the home gate of a board without sudo (home)
    'llmcf_tier=home; if [ "$llmcf_keys" = root ]; then llmcf_tier=root; elif [ -z "$LLMCF_HOME_GATE" ]; then '
    "llmcf_tier=dropbear; fi",
    'echo "restricted=yes gate=$LLMCF_GATE_VERSION keys=$llmcf_keys signing=$llmcf_sig tier=$llmcf_tier"',
])
define("status", (), 'echo "restricted=no gate=none"', gate_render=lambda *v, rest="": _STATUS_GATE,
       doc="whether the framework's key on the board runs only the gate, its version, whether its key line and the "
           "gate are root-owned (keys=root) and whether the board checks the framework's signature (signing=yes)")


# --- pairing: the gate and the restricted key line --------------------------------------------------------------------

def _home_key_line_sh(key: str) -> list:
    """sh lines that put the framework key's restricted line ($line, the key's blob @BLOB@) into the account's own
    ~/.ssh/authorized_keys in place of an earlier line of that key, every other line as it was; on failure they answer
    `<key>=unavailable reason=read-only` and end (nothing of the file changed)."""
    return [
        'keys="$HOME/.ssh/authorized_keys"; new="$keys.llmcf.$$"',
        f'mkdir -p "$HOME/.ssh" 2>/dev/null || {{ echo "{key}=unavailable reason=read-only"; exit 0; }}',
        'if ! { touch "$keys" && cp -p "$keys" "$new" && '
        "awk -v blob=@BLOB@ -v line=\"$line\" 'BEGIN { done = 0 } { hit = 0; n = split($0, f, \" \"); "
        "for (i = 1; i <= n; i++) if (f[i] == blob) hit = 1; if (hit) { if (!done) print line; done = 1; next } print } "
        "END { if (!done) print line }' \"$keys\" > \"$new\" && mv -f \"$new\" \"$keys\"; } 2>/dev/null; then "
        f'rm -f "$new" 2>/dev/null; echo "{key}=unavailable reason=read-only"; exit 0; fi',
    ]


def _selftest_sh(key: str) -> list:
    """sh lines that write the framework's signing key ($LLMCF_SIGNERS, $LLMCF_PEM) and its self-test signature
    ($LLMCF_SELFTEST of SELFTEST_DATA) into the private folder $llmcf_d and check it with the board's own tools
    (llmcf_verify: ssh-keygen, else openssl), before anything is installed; `<key>=unavailable reason=old-ssh` or
    `no-verifier` when this board can check the framework's signature with neither."""
    return [
        'printf "%s\\n" "$LLMCF_SIGNERS" > "$llmcf_d/signers"',
        'printf "%s" "${LLMCF_PEM-}" > "$llmcf_d/pem"',
        'printf "llmcf-board-selftest\\n" > "$llmcf_d/selftest"',
        'printf "%s" "$LLMCF_SELFTEST" > "$llmcf_d/selftest.sig"',
        'llmcf_verify "$llmcf_d/selftest" "$llmcf_d/selftest.sig" "$llmcf_d/signers" "$llmcf_d/pem" || '
        f'{{ [ "$llmcf_why" != bad ] || llmcf_why=old-ssh; echo "{key}=unavailable reason=$llmcf_why"; exit 0; }}',
    ]


GATE_INSTALL = "\n".join([
    # stdin: the gate program; LLMCF_SIGNERS, LLMCF_PEM, LLMCF_SELFTEST before it (a pairing that gives the
    # framework's signing key: the home gate of a board without sudo, the weaker tier the person agreed to). Answers
    # gate=installed, or gate=unavailable reason=WORD (nothing of the person's authorized_keys changed then).
    "set -u",
    "umask 077",
    signed_sh(),
    'llmcf_text=$(cat; echo x); llmcf_text=${llmcf_text%x}',
    '[ -x /bin/sh ] || { echo "gate=unavailable reason=no-sh"; exit 0; }',
    'case $HOME in /*) ;; *) echo "gate=unavailable reason=home"; exit 0 ;; esac',
    'case $HOME in *[!A-Za-z0-9._/-]*) echo "gate=unavailable reason=home"; exit 0 ;; esac',
    'llmcf_d=$(mktemp -d "${TMPDIR:-/tmp}/llmcf-home.XXXXXXXX") || { echo "gate=unavailable reason=tmp"; exit 0; }',
    "trap 'rm -rf \"$llmcf_d\"' EXIT",
    'if [ -n "${LLMCF_SIGNERS-}" ]; then',
    *("  " + line for line in _selftest_sh("gate")),
    "fi",
    f'cfg="$HOME/{GATE_REL.rsplit("/", 1)[0]}"; g="$HOME/{GATE_REL}"',
    'if ! { mkdir -p "$cfg" "$HOME/.ssh" && printf "%s" "$llmcf_text" > "$g.new" && chmod 0500 "$g.new" && '
    'mv -f "$g.new" "$g"; } 2>/dev/null; then rm -f "$g.new" 2>/dev/null; '
    'echo "gate=unavailable reason=read-only"; exit 0; fi',
    '/bin/sh -n "$g" 2>/dev/null || { echo "gate=unavailable reason=sh"; exit 0; }',
    'if [ -n "${LLMCF_SIGNERS-}" ]; then',
    f'  s="$HOME/{HOME_SIGNING_REL}"',
    '  if ! { mkdir -p "$s" && cp "$llmcf_d/signers" "$s/allowed_signers.new" && cp "$llmcf_d/pem" '
    '"$s/signing_key.pem.new" && chmod 0644 "$s/allowed_signers.new" "$s/signing_key.pem.new" && '
    'mv -f "$s/allowed_signers.new" "$s/allowed_signers" && mv -f "$s/signing_key.pem.new" "$s/signing_key.pem"; } '
    '2>/dev/null; then echo "gate=unavailable reason=read-only"; exit 0; fi',
    # the gate trusts this signing key only while nobody but the account can change it, up to its home folder
    '  LLMCF_HOME_GATE=1 llmcf_trusted_signers || { echo "gate=unavailable reason=home-writable"; exit 0; }',
    "fi",
    'line="command=\\"/bin/sh $g\\",' + ",".join(KEY_OPTIONS) + ' @TYPE@ @BLOB@ @COMMENT@"',
    *_home_key_line_sh("gate"),
    'echo "gate=installed"',
])
define("gate-install", ("word", "b64", "comment"),
       lambda t, b, c, rest="": GATE_INSTALL.replace("@TYPE@", t).replace("@BLOB@", b).replace("@COMMENT@", c),
       gate=False, doc="pairing: the gate program and the framework key's restricted authorized_keys line")

ROOT_SETUP = "\n".join([
    # stdin: a line with the person's one-time password (empty: sudo -n), then the root helper program.
    "set -u",
    "umask 077",
    'IFS= read -r llmcf_pw || llmcf_pw=""',
    'llmcf_d=$(mktemp -d "${TMPDIR:-/tmp}/llmcf-root.XXXXXXXX") || { echo "root=unavailable reason=tmp"; exit 0; }',
    "trap 'rm -rf \"$llmcf_d\"' EXIT",
    'cat > "$llmcf_d/helper"',
    'printf "%s" "$LLMCF_SUDOERS" > "$llmcf_d/sudoers"',
    'asroot() { if [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; '
    'else sudo -n "$@"; fi; }',
    'command -v sudo >/dev/null 2>&1 || { echo "root=unavailable reason=no-sudo"; exit 0; }',
    'command -v visudo >/dev/null 2>&1 || [ -x /usr/sbin/visudo ] || { echo "root=unavailable reason=no-visudo"; '
    'exit 0; }',
    'llmcf_visudo=$(command -v visudo 2>/dev/null || echo /usr/sbin/visudo)',
    '"$llmcf_visudo" -cf "$llmcf_d/sudoers" >/dev/null 2>&1 || { echo "root=unavailable reason=sudoers"; exit 0; }',
    f'asroot install -d -m 0755 -o root -g root {ROOT_HELPER.rsplit("/", 1)[0]} >/dev/null 2>&1 || '
    '{ echo "root=unavailable reason=sudo"; exit 0; }',
    f'asroot install -m 0755 -o root -g root "$llmcf_d/helper" {ROOT_HELPER} >/dev/null 2>&1 || '
    '{ echo "root=unavailable reason=sudo"; exit 0; }',
    f'asroot install -m 0440 -o root -g root "$llmcf_d/sudoers" {SUDOERS_DIR}/@FILE@ >/dev/null 2>&1 || '
    '{ echo "root=unavailable reason=sudo"; exit 0; }',
    'echo "root=installed"',
])


def sudoers_name(user: str) -> str:
    """The name of llmcf's sudoers file for the board account `user` in SUDOERS_DIR: sudo's #includedir skips a
    file whose name holds a '.', so the '.' of an account such as first.last becomes '_'."""
    return "llmcf-" + user.replace(".", "_")


define("root-setup", ("word",), lambda user, rest="": ROOT_SETUP.replace("@FILE@", sudoers_name(user)), gate=False,
       doc="llmcf's root helper and its exact sudo rules (sudo -n, or the person's one-time password for sudo -S)")


def _answer(text: str, key: str) -> Tuple[bool, str]:
    """(True, "") for `key=installed` in a setup step's answer, else (False, its reason word)."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line == f"{key}=installed" or line.startswith(f"{key}=installed "):
            return True, ""
        if line.startswith(f"{key}=unavailable"):
            reason = line.partition("reason=")[2].strip()
            return False, reason if re.fullmatch(r"[a-z-]{1,24}", reason or "") else "unknown"
    return False, "unknown"


# What a reason word of the setup steps means, in the person's words.
SETUP_REASONS = {
    "account": "the board account is root or its name is not a plain account name",
    "no-signer": "the framework has no signing key to give the board (its secrets store is not available)",
    "no-signer-store": "the framework has no signing key to give the board: this step ran without the framework's "
                       "settings, so it could open the secrets store only as a keyring store (secret_storage keyring); "
                       "a store protected with a passphrase or a credential helper (secret_storage passphrase or "
                       "helper) cannot be opened there",
    "no-sshd": "the board's SSH server is not OpenSSH (sshd), which a root-owned key line needs (Match User)",
    "no-keygen": "the board has no ssh-keygen to check the framework's signature",
    "old-ssh": "the board's ssh-keygen cannot check the framework's signature (OpenSSH 8.1 or later needed) and "
               "openssl (1.1.1 or later) could not check it either",
    "no-verifier": "the board can check the framework's signature neither with ssh-keygen (OpenSSH 8.1 or later) nor "
                   "with openssl (1.1.1 or later); install one of them on the board, or switch it to OpenSSH",
    "home-writable": "the account's home folder or its .config folder can be written by others, so a signing key "
                     "kept there could be changed",
    "not-root-owned": "llmcf's files were installed, but a folder on their way is not root-owned or can be written "
                      "by others; the framework's key was not installed",
    "enterprise": "this framework runs in enterprise mode, which takes only OpenSSH with sudo (the root-owned key "
                  "line): the weaker Dropbear tier and the home gate are refused as too insecure there",
    "declined": "you declined every way of putting the framework's key on this board; it keeps using your password",
    "sshd-config": "sshd did not accept its configuration with llmcf's Match block (sshd -t); nothing was changed",
    "sshd-match": "sshd's configuration already names another key file for this account; nothing was changed",
    "reload": "sshd could not be reloaded; its earlier configuration was put back",
    "no-sh": "the board has no /bin/sh",
    "home": "the account's home folder is not a plain path",
    "read-only": "the account's home folder or its ~/.ssh cannot be written (a read-only system)",
    "sh": "the board's /bin/sh cannot read the gate",
    "tmp": "the board cannot make a private temporary folder",
    "no-sudo": "the board has no sudo",
    "no-visudo": "the board has no visudo to check the sudo rules",
    "sudoers": "visudo did not accept the sudo rules",
    "sudo": "sudo refused (it needs a password the framework was not given, or the account may not use it)",
    "gated": "the framework's key on this board already runs only llmcf's gate, which never runs a setup step",
    "unknown": "the board gave no answer the framework knows",
}


def _assignments(bundle: "GateBundle", **more: str) -> str:
    """The plain assignments of the framework's signing key in front of a setup step (shlex-quoted values)."""
    values = {**more, "LLMCF_SIGNERS": bundle.signers, "LLMCF_PEM": bundle.pem, "LLMCF_SELFTEST": bundle.selftest}
    return "".join(f"{k}={shlex.quote(v)}\n" for k, v in values.items())


def install_gate(run: Callable[..., object], public_line: str, gate_text: str,
                 bundle: Optional["GateBundle"] = None) -> Tuple[bool, str]:
    """Pairing's setup step gate-install over `run` (a session's or a transport's run(script, stdin=...)): the gate
    program and the framework key's restricted authorized_keys line in the account's own home. With `bundle` (the
    home gate of a board without sudo, the weaker tier the person agreed to: key_tiers.TIER_HOME) also the framework's
    signing key in HOME_SIGNING_REL, checked first with the board's own tools (ssh-keygen, else openssl), so the gate
    runs only what the framework signed. (True, "") when installed, else (False, the reason word of SETUP_REASONS;
    "gated": the login was with the framework key, which already runs only the gate); the person's other lines in
    authorized_keys stay as they were."""
    parts = (public_line or "").split(" ")
    if len(parts) not in (2, 3):
        raise GateRefused("the framework's public key line is not a plain SSH public key")
    call = script("gate-install", parts[0], parts[1], parts[2] if len(parts) == 3 else GATE_NAME)
    if bundle is not None:   # the home gate of a board without sudo: the gate checks the framework's signature too
        if not bundle.signers or not bundle.selftest:
            return False, bundle.missing or "no-signer"
        call = GateScript(_assignments(bundle) + str(call), call.verb, call.args)
    try:
        result = run(call, stdin=[gate_text.encode("utf-8")])
    except (OSError, EOFError):
        # a gate that refused the line closes the channel without reading the program on stdin: the login was with
        # the framework key, which already runs only the gate (paramiko tries it before a one-time password)
        check = run(script("status"))
        if is_refusal(getattr(check, "status", 0), getattr(check, "err", "")):
            return False, "gated"
        raise
    if is_refusal(getattr(result, "status", 0), getattr(result, "err", "")):
        return False, "gated"
    return _answer(getattr(result, "out", ""), "gate")


def install_root(run: Callable[..., object], user: str, helper_text: str, sudoers_text: str,
                 password: str = "") -> Tuple[bool, str]:
    """The setup step root-setup: llmcf's root helper at ROOT_HELPER and its sudoers file (checked with visudo -cf
    first) for `user`, with `sudo -n`, or with the person's one-time `password` for `sudo -S` (sent on stdin, never on
    a command line). (True, "") when installed, else (False, the reason word)."""
    if "\n" in password or "\r" in password:
        return False, "sudo"
    text = f"LLMCF_SUDOERS={shlex.quote(sudoers_text)}\n" + str(script("root-setup", user))
    call = GateScript(text, "root-setup", (user,))
    result = run(call, stdin=[(password + "\n").encode("utf-8"), helper_text.encode("utf-8")])
    return _answer(getattr(result, "out", ""), "root")


# --- the root step of a board: llmcf's key line and signing key in root-owned places (DECISIONS 2026-10-31) -----------

SELFTEST_DATA = b"llmcf-board-selftest\n"
MATCH_BEGIN = "# llmcf-begin board account"
MATCH_END = "# llmcf-end board account"


@dataclass(frozen=True)
class BoardPaths:
    """Where the root step puts llmcf's root-owned files (the defaults are the board's; the tests give a temporary
    folder)."""
    libexec: str = LIBEXEC_DIR
    gate: str = ROOT_GATE
    keys_dir: str = KEYS_DIR
    signers_dir: str = SIGNERS_DIR
    signers: str = SIGNERS
    sshd_config: str = SSHD_CONFIG
    root_uid: int = ROOT_UID   # the owner the Dropbear step checks its files for (the tests' own user)

    @property
    def signers_pem(self) -> str:
        return self.signers.rsplit("/", 1)[0] + "/signing_key.pem"


def match_block(user: str, keys_dir: str = KEYS_DIR) -> str:
    """llmcf's block at the end of sshd_config for the board account `user`: its key line comes from the root-owned
    KEYS_DIR/<account> first; the account's own ~/.ssh/authorized_keys stays readable after it (the person's own keys
    for this account keep working, and llmcf's line is found first, in the file the account cannot change)."""
    return (f"{MATCH_BEGIN} {user} (generated by llmcf's pairing)\nMatch User {user}\n"
            f"\tAuthorizedKeysFile {keys_dir}/%u .ssh/authorized_keys\n{MATCH_END} {user}\n")


def root_board_script(paths: BoardPaths = BoardPaths()) -> str:
    """The root step's script (the setup operation root-board, run by an unrestricted login of the board account):
    stdin is a line with the person's one-time password (empty: sudo -n), then the gate program; LLMCF_KEYLINE (the
    framework key's restricted line), LLMCF_SIGNERS (the allowed_signers line) and LLMCF_SELFTEST (the framework key's
    signature of SELFTEST_DATA) come before it as plain assignments. It checks first (sudo, OpenSSH's sshd, ssh-keygen
    checks the self-test signature with exactly that signers line, the gate reads, sshd -t accepts the configuration
    with llmcf's Match block and sshd -T shows the root-owned key file for this account), then installs root:root the
    gate (0755), the signers file and the key line (0644, folders 0755) and the configuration (the earlier one kept as
    sshd_config.llmcf-backup), reloads sshd (the earlier configuration put back when that fails) and only then removes
    the framework key's line from the account's own ~/.ssh/authorized_keys (as the account, never as root). Answers
    root-board=installed home=removed|kept|none, or root-board=unavailable reason=WORD (nothing changed then)."""
    q = paths
    block = match_block("@USER@", q.keys_dir).replace("@USER@", "$llmcf_u")
    return "\n".join([
        "set -u",
        "umask 077",
        'IFS= read -r llmcf_pw || llmcf_pw=""',
        'no() { echo "root-board=unavailable reason=$1"; exit 0; }',
        'llmcf_u=$(id -un 2>/dev/null) || no account',
        'case $llmcf_u in ""|root|-*|*[!A-Za-z0-9._-]*) no account ;; esac',
        '[ "$(id -u)" != 0 ] || no account',
        '[ -n "${LLMCF_SIGNERS-}" ] || no no-signer',
        'llmcf_d=$(mktemp -d "${TMPDIR:-/tmp}/llmcf-root.XXXXXXXX") || no tmp',
        "trap 'rm -rf \"$llmcf_d\"' EXIT",
        'cat > "$llmcf_d/gate"',
        'printf "%s\\n" "$LLMCF_KEYLINE" > "$llmcf_d/key"',
        'asroot() { if [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; '
        'else sudo -n "$@"; fi; }',
        "command -v sudo >/dev/null 2>&1 || no no-sudo",
        'llmcf_sshd=$(command -v sshd 2>/dev/null) || llmcf_sshd=/usr/sbin/sshd',
        '[ -x "$llmcf_sshd" ] || no no-sshd',
        verify_sh(),
        *_selftest_sh("root-board"),
        '/bin/sh -n "$llmcf_d/gate" 2>/dev/null || no sh',
        f'asroot cat {q.sshd_config} > "$llmcf_d/sshd_config" 2>/dev/null || no sudo',
        f"awk -v b=\"{MATCH_BEGIN} $llmcf_u \" -v e=\"{MATCH_END} $llmcf_u\" "
        "'index($0, b) == 1 { skip = 1; next } skip && $0 == e { skip = 0; next } !skip { print }' "
        '"$llmcf_d/sshd_config" > "$llmcf_d/sshd_config.new" || no sshd-config',
        f'printf "%s" "{block}" >> "$llmcf_d/sshd_config.new"',
        'asroot "$llmcf_sshd" -t -f "$llmcf_d/sshd_config.new" >/dev/null 2>&1 || no sshd-config',
        'llmcf_akf=$(asroot "$llmcf_sshd" -T -f "$llmcf_d/sshd_config.new" -C "user=$llmcf_u,host=localhost,'
        "addr=127.0.0.1\" 2>/dev/null | awk 'tolower($1) == \"authorizedkeysfile\" { $1 = \"\"; sub(/^ +/, \"\"); "
        "print; exit }')",
        f'[ "$llmcf_akf" = "{q.keys_dir}/%u .ssh/authorized_keys" ] || no sshd-match',
        f"asroot install -d -m 0755 -o root -g root {q.libexec} {q.keys_dir} {q.signers_dir} >/dev/null 2>&1 "
        "|| no sudo",
        f'asroot install -m 0755 -o root -g root "$llmcf_d/gate" {q.gate} >/dev/null 2>&1 || no sudo',
        f'asroot install -m 0644 -o root -g root "$llmcf_d/signers" {q.signers} >/dev/null 2>&1 || no sudo',
        f'asroot install -m 0644 -o root -g root "$llmcf_d/pem" {q.signers_pem} >/dev/null 2>&1 || no sudo',
        f'asroot install -m 0644 -o root -g root "$llmcf_d/key" {q.keys_dir}/"$llmcf_u" >/dev/null 2>&1 || no sudo',
        f"asroot cp -p {q.sshd_config} {q.sshd_config}.llmcf-backup >/dev/null 2>&1 || no sudo",
        f'asroot install -m 0644 -o root -g root "$llmcf_d/sshd_config.new" {q.sshd_config} >/dev/null 2>&1 || no sudo',
        "if ! { asroot systemctl reload ssh || asroot systemctl reload sshd || "
        "asroot kill -HUP \"$(asroot cat /run/sshd.pid)\"; } >/dev/null 2>&1; then",
        f"  asroot cp -p {q.sshd_config}.llmcf-backup {q.sshd_config} >/dev/null 2>&1",
        "  no reload",
        "fi",
        'llmcf_keys="$HOME/.ssh/authorized_keys"; llmcf_home=none',
        'if [ -f "$llmcf_keys" ] && grep -qF "@BLOB@" "$llmcf_keys" 2>/dev/null; then',
        '  llmcf_new="$llmcf_keys.llmcf.$$"; llmcf_home=kept',
        "  if cp -p \"$llmcf_keys\" \"$llmcf_new\" 2>/dev/null && awk -v blob=@BLOB@ '{ hit = 0; "
        "n = split($0, f, \" \"); for (i = 1; i <= n; i++) if (f[i] == blob) hit = 1 } !hit' \"$llmcf_keys\" "
        '> "$llmcf_new" && mv -f "$llmcf_new" "$llmcf_keys"; then llmcf_home=removed; else rm -f "$llmcf_new"; fi',
        "fi",
        'echo "root-board=installed home=$llmcf_home"',
    ])


define("root-board", ("b64",), lambda blob, rest="": root_board_script().replace("@BLOB@", blob), gate=False,
       doc="pairing's root step: the gate, the framework key's line and its signing key in root-owned places, sshd's "
           "Match block for the account (checked with sshd -t), the account's own line of the key removed")


def install_root_board(run: Callable[..., object], public_line: str, bundle: "GateBundle", password: str = "", *,
                       paths: Optional[BoardPaths] = None) -> Tuple[bool, str, str]:
    """The root step root-board over `run` (an unrestricted login of the board account) with `sudo -n`, or with the
    person's one-time `password` for `sudo -S` (on stdin, never on a command line). (True, "", home) when installed
    (home: removed, kept or none, the framework key's earlier line in ~/.ssh/authorized_keys), else (False, the reason
    word of SETUP_REASONS, ""); "gated": the login was with a framework key that already runs only the gate."""
    parts = (public_line or "").split(" ")
    if len(parts) not in (2, 3) or not KINDS["b64"].check(parts[1]):
        raise GateRefused("the framework's public key line is not a plain SSH public key")
    if "\n" in password or "\r" in password:
        return False, "sudo", ""
    if not bundle.signers or not bundle.selftest:
        return False, bundle.missing or "no-signer", ""
    line = root_key_line(" ".join(parts), (paths or BoardPaths()).gate)
    body = root_board_script(paths).replace("@BLOB@", parts[1]) if paths else str(script("root-board", parts[1]))
    text = _assignments(bundle, LLMCF_KEYLINE=line) + body
    call = GateScript(text, "root-board", (parts[1],))
    try:
        result = run(call, stdin=[(password + "\n").encode("utf-8"), bundle.gate.encode("utf-8")])
    except (OSError, EOFError):
        # a gate that refused the line closes the channel without reading stdin: the login ran in the gate
        check = run(script("status"))
        if is_refusal(getattr(check, "status", 0), getattr(check, "err", "")):
            return False, "gated", ""
        raise
    if is_refusal(getattr(result, "status", 0), getattr(result, "err", "")):
        return False, "gated", ""
    out = getattr(result, "out", "") or ""
    ok, reason = _answer(out, "root-board")
    if not ok:
        return False, reason, ""
    home = next((w.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("root-board=installed")
                 for w in ln.split() if w.startswith("home=")), "none")
    return True, "", home if home in ("removed", "kept", "none") else "none"


# --- the Dropbear tier: root-owned gate and signing key, the key line in the account's own authorized_keys -----------

# The key-line options of Dropbear's authorized_keys as its manual (dropbear(8), "Authorized Keys") lists them. Dropbear
# refuses a line that holds an option it does not know, so the line in the account's own authorized_keys keeps to the
# ones every Dropbear release since its first key-option support knows (command= and the four no-* options);
# permitopen=, restrict and no-touch-required came in later releases only. OpenSSH's no-user-rc is not one of them.
DROPBEAR_OPTIONS = ("command", "no-port-forwarding", "no-agent-forwarding", "no-X11-forwarding", "no-pty")
DROPBEAR_LATER_OPTIONS = ("permitopen", "restrict", "no-touch-required")


def dropbear_board_script(paths: BoardPaths = BoardPaths()) -> str:
    """The Dropbear tier's setup step (dropbear-board, run once by an unrestricted login of the board account with
    sudo; DECISIONS 2026-10-31, Dropbear tiers): stdin is a line with the person's one-time password (empty: sudo -n),
    then the gate program; LLMCF_SIGNERS, LLMCF_PEM and LLMCF_SELFTEST come before it as plain assignments. It checks
    first (the account, sudo, the board's own tools check the self-test signature: ssh-keygen, else openssl; the gate
    reads), then installs root:root the gate (0755) at the root-owned place of the OpenSSH tier and the signing key
    (0644, folders 0755), checks them as the gate will (owner root, no group or other write, up the folder chain) and
    only then writes the framework key's line, with the gate as forced command and only options Dropbear knows, into
    the account's own ~/.ssh/authorized_keys (as the account; Dropbear reads no other file). One step weaker than the
    OpenSSH tier: the account itself could edit that line; a stolen key cannot, because no gate operation writes to
    ~/.ssh. Answers dropbear-board=installed, or dropbear-board=unavailable reason=WORD."""
    q = paths
    lib_parent = q.libexec.rsplit("/", 1)[0] or "/"
    sig_parent = q.signers_dir.rsplit("/", 1)[0] or "/"
    return "\n".join([
        "set -u",
        "umask 077",
        'IFS= read -r llmcf_pw || llmcf_pw=""',
        'no() { echo "dropbear-board=unavailable reason=$1"; exit 0; }',
        'llmcf_u=$(id -un 2>/dev/null) || no account',
        'case $llmcf_u in ""|root|-*|*[!A-Za-z0-9._-]*) no account ;; esac',
        '[ "$(id -u)" != 0 ] || no account',
        'case $HOME in /*) ;; *) no home ;; esac',
        'case $HOME in *[!A-Za-z0-9._/-]*) no home ;; esac',
        '[ -n "${LLMCF_SIGNERS-}" ] || no no-signer',
        'llmcf_d=$(mktemp -d "${TMPDIR:-/tmp}/llmcf-root.XXXXXXXX") || no tmp',
        "trap 'rm -rf \"$llmcf_d\"' EXIT",
        'cat > "$llmcf_d/gate"',
        'asroot() { if [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; '
        'else sudo -n "$@"; fi; }',
        "command -v sudo >/dev/null 2>&1 || no no-sudo",
        signed_sh(q.signers, q.root_uid),
        *_selftest_sh("dropbear-board"),
        '/bin/sh -n "$llmcf_d/gate" 2>/dev/null || no sh',
        f"asroot install -d -m 0755 -o root -g root {q.libexec} {q.signers_dir} >/dev/null 2>&1 || no sudo",
        f'asroot install -m 0755 -o root -g root "$llmcf_d/gate" {q.gate} >/dev/null 2>&1 || no sudo',
        f'asroot install -m 0644 -o root -g root "$llmcf_d/signers" {q.signers} >/dev/null 2>&1 || no sudo',
        f'asroot install -m 0644 -o root -g root "$llmcf_d/pem" {q.signers_pem} >/dev/null 2>&1 || no sudo',
        f"for p in {q.gate} {q.libexec} {lib_parent} {q.signers} {q.signers_pem} {q.signers_dir} {sig_parent}; do",
        '  llmcf_rootowned "$p" || no not-root-owned',
        "done",
        'line="command=\\"/bin/sh ' + q.gate + '\\",' + ",".join(KEY_OPTIONS) + ' @TYPE@ @BLOB@ @COMMENT@"',
        *_home_key_line_sh("dropbear-board"),
        'echo "dropbear-board=installed"',
    ])


def _dropbear_render(t: str, b: str, c: str, rest: str = "", paths: Optional[BoardPaths] = None) -> str:
    return dropbear_board_script(paths or BoardPaths()).replace("@TYPE@", t).replace("@BLOB@", b).replace(
        "@COMMENT@", c)


define("dropbear-board", ("word", "b64", "comment"), _dropbear_render, gate=False,
       doc="pairing's Dropbear tier: the gate and the framework's signing key root-owned (sudo once), the key line "
           "with Dropbear's options in the account's own authorized_keys")


def install_dropbear_board(run: Callable[..., object], public_line: str, bundle: "GateBundle", password: str = "", *,
                           paths: Optional[BoardPaths] = None) -> Tuple[bool, str]:
    """The Dropbear tier's setup step over `run` (an unrestricted login of the board account) with `sudo -n`, or with
    the person's one-time `password` for `sudo -S` (on stdin, never on a command line). (True, "") when installed,
    else (False, the reason word of SETUP_REASONS)."""
    parts = (public_line or "").split(" ")
    if len(parts) not in (2, 3) or not KINDS["b64"].check(parts[1]):
        raise GateRefused("the framework's public key line is not a plain SSH public key")
    if "\n" in password or "\r" in password:
        return False, "sudo"
    if not bundle.signers or not bundle.selftest:
        return False, bundle.missing or "no-signer"
    comment = parts[2] if len(parts) == 3 else GATE_NAME
    body = (_dropbear_render(parts[0], parts[1], comment, paths=paths) if paths
            else str(script("dropbear-board", parts[0], parts[1], comment)))
    call = GateScript(_assignments(bundle) + body, "dropbear-board", (parts[0], parts[1], comment))
    try:
        result = run(call, stdin=[(password + "\n").encode("utf-8"), bundle.gate.encode("utf-8")])
    except (OSError, EOFError):
        check = run(script("status"))
        if is_refusal(getattr(check, "status", 0), getattr(check, "err", "")):
            return False, "gated"
        raise
    if is_refusal(getattr(result, "status", 0), getattr(result, "err", "")):
        return False, "gated"
    return _answer(getattr(result, "out", ""), "dropbear-board")


# --- the facts a pairing reads to offer the key tiers (remote/key_tiers.py) -----------------------------------------

# The packages that may provide Dropbear, per query program of the board's package manager, asked without root
# (dpkg-query -W, pacman -Qq, apk info -e, rpm -q). The hardware probe's table tables.dropbear_packages of
# configs/hardware_probe_schema.yml is the same (a test compares them). dropbear-initramfs only unlocks an encrypted
# disk at boot: it is recorded, never removed (DROPBEAR_KEEP), and the dropbear-bin it needs stays with it.
DROPBEAR_PACKAGES = {"dpkg": ("dropbear", "dropbear-bin", "dropbear-run", "dropbear-initramfs"),
                     "pacman": ("dropbear",), "apk": ("dropbear", "dropbear-openrc"), "rpm": ("dropbear",)}
DROPBEAR_KEEP = ("dropbear-initramfs",)
DROPBEAR_PACKAGE_NAMES = tuple(dict.fromkeys(n for names in DROPBEAR_PACKAGES.values() for n in names))
DROPBEAR_PACKAGE = _DROPBEAR_PACKAGE   # a strict name, nothing else (the one pattern: remote/key_tiers.py)


def dropbear_packages_sh() -> str:
    """POSIX sh functions for a board script: llmcf_dbtool prints the query program it asks (dpkg, pacman, apk, rpm;
    nothing without one), llmcf_dbpkgs the installed packages of DROPBEAR_PACKAGES, one per line, each checked
    against the fixed names (nothing of the package manager's own text comes out). Neither needs root."""
    names = "|".join(DROPBEAR_PACKAGE_NAMES)
    return "\n".join([
        "llmcf_dbtool() {",
        "  for llmcf_t in dpkg-query:dpkg pacman:pacman apk:apk rpm:rpm; do",
        '    if command -v "${llmcf_t%%:*}" >/dev/null 2>&1; then echo "${llmcf_t#*:}"; return 0; fi',
        "  done",
        "  return 1",
        "}",
        "llmcf_dbpkgs() {",
        "  case $(llmcf_dbtool) in",
        "    dpkg) dpkg-query -W -f '${Package} ${Status}\\n' " + " ".join(DROPBEAR_PACKAGES["dpkg"])
        + " 2>/dev/null | while read -r llmcf_n llmcf_s; do case \" $llmcf_s\" in *' installed') "
        "echo \"$llmcf_n\" ;; esac; done ;;",
        "    pacman) pacman -Qq " + " ".join(DROPBEAR_PACKAGES["pacman"]) + " 2>/dev/null ;;",
        "    apk) apk info -e " + " ".join(DROPBEAR_PACKAGES["apk"]) + " 2>/dev/null ;;",
        "    rpm) rpm -q --qf '%{NAME}\\n' " + " ".join(DROPBEAR_PACKAGES["rpm"]) + " 2>/dev/null ;;",
        f'  esac | while read -r llmcf_n; do case $llmcf_n in {names}) echo "$llmcf_n" ;; esac; done',
        "}",
    ])


TIER_FACTS = "\n".join([
    # stdin: a line with the person's one-time password (empty: sudo -n only). LLMCF_SIGNERS, LLMCF_PEM and
    # LLMCF_SELFTEST may come before it (then the board's own tools check the framework's self-test signature).
    # Answers fixed words only (key=value), nothing of the board's own text.
    "set -u",
    "umask 077",
    'IFS= read -r llmcf_pw || llmcf_pw=""',
    "llmcf_o=no; llmcf_db=no",
    "for llmcf_p in /proc/[0-9]*; do",
    '  read -r llmcf_c < "$llmcf_p/comm" 2>/dev/null || continue',
    "  case $llmcf_c in sshd) llmcf_o=yes ;; dropbear) llmcf_db=yes ;; esac",
    "done",
    'case $llmcf_o$llmcf_db in yesyes) echo running=both ;; yesno) echo running=openssh ;; '
    "noyes) echo running=dropbear ;; *) echo running=none ;; esac",
    "if command -v sshd >/dev/null 2>&1 || [ -x /usr/sbin/sshd ]; then echo openssh=yes; else echo openssh=no; fi",
    "if command -v dropbear >/dev/null 2>&1 || [ -x /usr/sbin/dropbear ]; then echo dropbear=yes; else echo dropbear=no; fi",
    "if ! command -v sudo >/dev/null 2>&1; then echo sudo=none",
    "elif sudo -n true >/dev/null 2>&1; then echo sudo=nopass",
    'elif [ -n "$llmcf_pw" ] && printf "%s\\n" "$llmcf_pw" | sudo -S -p "" true >/dev/null 2>&1; then echo sudo=password',
    "else echo sudo=refused; fi",
    "if command -v ssh-keygen >/dev/null 2>&1; then echo keygen=yes; else echo keygen=none; fi",
    "if command -v openssl >/dev/null 2>&1; then echo openssl=yes; else echo openssl=none; fi",
    verify_sh(),
    'if [ -n "${LLMCF_SIGNERS-}" ] && llmcf_d=$(mktemp -d "${TMPDIR:-/tmp}/llmcf-tier.XXXXXXXX"); then',
    '  printf "%s\\n" "$LLMCF_SIGNERS" > "$llmcf_d/signers"; printf "%s" "${LLMCF_PEM-}" > "$llmcf_d/pem"',
    '  printf "llmcf-board-selftest\\n" > "$llmcf_d/selftest"; printf "%s" "$LLMCF_SELFTEST" > "$llmcf_d/selftest.sig"',
    '  if llmcf_verify "$llmcf_d/selftest" "$llmcf_d/selftest.sig" "$llmcf_d/signers" "$llmcf_d/pem"; then '
    'echo verifier=yes; elif [ "$llmcf_why" = no-verifier ]; then echo verifier=no-verifier; '
    "else echo verifier=old-ssh; fi",
    '  rm -rf "$llmcf_d"',
    "fi",
    # the system family as install.sh reads it (ID, then ID_LIKE), and DietPi on its own
    "llmcf_id=; llmcf_like=",
    "if [ -r /etc/os-release ]; then",
    "  llmcf_id=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '\"' | tr 'A-Z' 'a-z')",
    "  llmcf_like=$(sed -n 's/^ID_LIKE=//p' /etc/os-release | tr -d '\"' | tr 'A-Z' 'a-z')",
    "fi",
    "llmcf_f=unknown",
    "for llmcf_w in $llmcf_id $llmcf_like; do",
    "  case $llmcf_w in",
    "    debian|ubuntu|raspbian|dietpi|armbian|linuxmint|pop) llmcf_f=debian ;;",
    "    fedora|rhel|centos|rocky|almalinux|amzn|ol) llmcf_f=fedora ;;",
    "    opensuse*|sles|suse) llmcf_f=suse ;;",
    "    arch|manjaro|endeavouros) llmcf_f=arch ;;",
    "    alpine|postmarketos) llmcf_f=alpine ;;",
    "    *) continue ;;",
    "  esac",
    "  break",
    "done",
    'echo "family=$llmcf_f"',
    # DietPi on its own: the switch's plan tells a DietPi board that both of its steps go through apt
    "if [ -d /boot/dietpi ] || [ -f /boot/dietpi/.version ]; then echo dietpi=yes; else echo dietpi=no; fi",
    "for llmcf_m in apt-get dnf zypper pacman apk; do",
    '  if command -v "$llmcf_m" >/dev/null 2>&1; then echo "manager=$llmcf_m"; fi',
    "done",
    # which installed packages provide Dropbear, asked from the package manager without root
    dropbear_packages_sh(),
    'for llmcf_n in $(llmcf_dbpkgs); do echo "dropbear_pkg=$llmcf_n"; done',
    "if [ -d /run/systemd/system ]; then echo init=systemd; elif command -v rc-service >/dev/null 2>&1; then "
    "echo init=openrc; else echo init=other; fi",
    'llmcf_port=${SSH_CONNECTION##* }',
    'case $llmcf_port in ""|*[!0-9]*) ;; *) echo "port=$llmcf_port" ;; esac',
])
define("tier-facts", (), lambda rest="": TIER_FACTS, gate=False,
       doc="pairing: which SSH server runs, sudo, the board's tools for the framework's signature, the system family")


def read_tier_facts(run: Callable[..., object], bundle: Optional["GateBundle"] = None,
                    password: str = "") -> str:
    """The answer of tier-facts over `run` (an unrestricted login; the person's one-time `password` only tests
    `sudo -S true`, on stdin): fixed key=value words, read by key_tiers.TierFacts.parse. "" when it could not run."""
    if "\n" in password or "\r" in password:
        password = ""
    call = script("tier-facts")
    if bundle is not None and bundle.signers and bundle.selftest:
        call = GateScript(_assignments(bundle) + str(call), call.verb, call.args)
    try:
        result = run(call, stdin=[(password + "\n").encode("utf-8")])
    except Exception:  # noqa: BLE001 - no facts: the offer names only what is always possible
        return ""
    if is_refusal(getattr(result, "status", 0), getattr(result, "err", "")):
        return ""
    return getattr(result, "out", "") or ""


__all__ = ["DROPBEAR_KEEP", "DROPBEAR_PACKAGE", "DROPBEAR_PACKAGE_NAMES", "DROPBEAR_PACKAGES",
           "dropbear_packages_sh", "DROPBEAR_OPTIONS", "DROPBEAR_LATER_OPTIONS", "HOME_SIGNING_REL", "SIGNERS_PEM", "SSHSIG_ED25519_SIZE",
           "dropbear_board_script", "install_dropbear_board", "read_tier_facts", "sshsig_prefix", "verify_sh"]
__all__ += ["BoardPaths", "INSTALL_ROOT_REL", "KEYS_DIR", "LIBEXEC_DIR", "ROOT_GATE", "SELFTEST_DATA", "SIGNERS",
           "SIGN_NAMESPACE", "SIGN_PRINCIPAL", "install_root_board", "match_block", "root_board_script", "signed_sh",
           "GATE_NAME", "GATE_REL", "GateBundle", "GateCall", "GateRefused", "GateScript", "JOB_LOG", "JOB_STARTED",
           "JOB_STATUS", "KEY_OPTIONS", "KINDS", "Kind", "OPERATIONS", "Operation", "PLACE_HOME_REL",
           "PLACE_TMP_PREFIX", "REFUSAL_PREFIX", "REFUSED_STATUS", "ROOT_HELPER", "ROOT_KEY_OPTIONS", "SETUP_REASONS",
           "SUDOERS_DIR", "define", "detached_command", "gate_call", "gate_operations", "gate_version", "install_gate",
           "install_root", "is_refusal", "is_restricted_line", "job", "key_options", "operation", "poll_command",
           "refusal_text", "root_key_line", "render_gate", "restricted_line", "script", "sudoers_name"]
