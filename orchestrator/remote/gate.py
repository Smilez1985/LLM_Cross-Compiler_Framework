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

GATE_NAME = "llmcf-gate"
# Where the gate lives on the board, relative to the account's home folder (next to the runtime's API key folder).
GATE_REL = ".config/llm-framework/llmcf-gate"
# What the gate answers when it refuses a line (stderr), and its exit status then.
REFUSAL_PREFIX = "llmcf-gate: refused: "
REFUSED_STATUS = 126
# The options of the framework key's authorized_keys line besides command= (OpenSSH and Dropbear both read them; none
# of llmcf's operations needs a terminal or a forwarding).
KEY_OPTIONS = ("no-pty", "no-port-forwarding", "no-agent-forwarding", "no-X11-forwarding")
# llmcf's own folders on a board, the only places the gate lets an operation write to or remove: the runtime's install
# folder in the account's home and the private upload folders of a package deploy (mktemp's /tmp/llm_deploy.XXXXXXXX).
PLACE_HOME_REL = ".local/share/llm-framework"
PLACE_TMP_PREFIX = "/tmp/llm_deploy."   # nosec B108 - a folder on the board, made by mktemp with a random name
PATH_MAX = 1536
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
_SH_PLACE = ('llmcf_k_path "$1" || return 1; '
             f'case $1 in "$HOME"/{PLACE_HOME_REL}|"$HOME"/{PLACE_HOME_REL}/*) return 0 ;; esac; '
             f'case $1 in {PLACE_TMP_PREFIX}????????|{PLACE_TMP_PREFIX}????????/*) return 0 ;; esac; return 1')
KINDS: Dict[str, Kind] = {k.name: k for k in (
    # an absolute path without `.` or `..` parts and without characters that would need quoting (read-only operations)
    Kind("path", _plain_path, _SH_PATH),
    # a path inside llmcf's own folders on the board (the gate checks where; here only that it is a plain path)
    Kind("place", _plain_path, _SH_PLACE),
    # the parent of a private folder: /tmp or one of llmcf's places
    Kind("parent", _plain_path, '[ "$1" = /tmp ] || llmcf_k_place "$1"'),
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
    (start-<verb>, RemoteSession.run_detached)."""
    verb: str
    kinds: Tuple[str, ...]
    render: Callable[..., str]
    rest: Optional[str] = None
    rest_max: int = REST_MAX
    gate: bool = True
    gate_render: Optional[Callable[..., str]] = None
    job: bool = False
    doc: str = ""

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
        return args


OPERATIONS: Dict[str, Operation] = {}


def define(verb: str, kinds: Sequence[str] = (), render: "str | Callable[..., str]" = "", *, rest: Optional[str] = None,
           rest_max: int = REST_MAX, gate: bool = True, gate_render: Optional[Callable[..., str]] = None,
           job: bool = False, doc: str = "") -> Operation:
    """Defines (or redefines, for a module loaded again) one operation. A str `render` is a format template: {0}, {1},
    ... are the arguments, {rest} the rest of them (sh braces are written twice, as in an f-string)."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", verb, re.ASCII):
        raise ValueError(f"not an operation verb: {verb!r}")
    for kind in list(kinds) + ([rest] if rest else []):
        if kind not in KINDS:
            raise ValueError(f"unknown argument kind {kind!r}")
    fn = (lambda *v, rest="", _t=render: _t.format(*v, rest=rest)) if isinstance(render, str) else render
    op = Operation(verb, tuple(kinds), fn, rest, rest_max, gate, gate_render, job, doc)
    OPERATIONS[verb] = op
    if job:   # its start: the detached wrapper around the job's body, a gate operation of its own
        def start(folder: str, *values: str, rest: str = "", _op=op) -> str:
            return detached_command(folder, _op.render(*values, rest=rest))
        OPERATIONS["start-" + verb] = Operation("start-" + verb, ("place",) + op.kinds, start, rest, rest_max,
                                                gate, None, False, f"starts the job {verb} on its own")
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
    head = _GATE_HEAD
    version = hashlib.sha256((head + body).encode("utf-8")).hexdigest()[:16]
    return head.replace("@VERSION@", version) + body


def gate_version(text: str) -> str:
    """The version a gate program carries ("" when it carries none)."""
    m = re.search(r"^LLMCF_GATE_VERSION=([0-9a-f]{16})$", text or "", re.M | re.ASCII)
    return m.group(1) if m else ""


_GATE_HEAD = """#!/bin/sh
# llmcf-gate: the command gate of the LLM Cross-Compiler Framework on this board (generated by the framework; pairing
# writes it again). The framework's SSH key runs only this program (command= in ~/.ssh/authorized_keys): it runs one of
# llmcf's operations, each a fixed verb with checked arguments, and refuses everything else with one plain line.
LLMCF_GATE_VERSION=@VERSION@
LLMCF_GATE=1; export LLMCF_GATE LLMCF_GATE_VERSION
LC_ALL=C; export LC_ALL
set -f
llmcf_refuse() { printf 'llmcf-gate: refused: %s\\n' "$1" >&2; exit 126; }
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

def key_options(gate_path: str) -> str:
    """The options before the framework's public key: the gate as forced command and the no-* options."""
    if not _plain_path(gate_path):
        raise GateRefused("the gate's path on the board is not a plain absolute path")
    return f'command="/bin/sh {gate_path}",' + ",".join(KEY_OPTIONS)


def restricted_line(public_line: str, gate_path: str) -> str:
    return f"{key_options(gate_path)} {public_line}"


def is_restricted_line(line: str, gate_path: str) -> bool:
    return (line or "").strip().startswith(key_options(gate_path) + " ")


@dataclass(frozen=True)
class GateBundle:
    """What pairing installs on a board: the gate program (render_gate, with every operation of the framework
    defined), llmcf's root helper (None: no root actions) and its version."""
    gate: str
    root_helper: Optional[str] = None
    notes: Tuple[str, ...] = field(default_factory=tuple)

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
define("status", (), 'echo "restricted=no gate=none"',
       gate_render=lambda *v, rest="": 'echo "restricted=yes gate=$LLMCF_GATE_VERSION"',
       doc="whether the framework's key on the board runs only the gate, and its version")


__all__ = ["GATE_NAME", "GATE_REL", "GateBundle", "GateCall", "GateRefused", "GateScript", "JOB_LOG", "JOB_STARTED",
           "JOB_STATUS", "KEY_OPTIONS", "KINDS",
           "Kind", "OPERATIONS", "Operation", "PLACE_HOME_REL", "PLACE_TMP_PREFIX", "REFUSAL_PREFIX", "REFUSED_STATUS",
           "define", "detached_command", "gate_call", "gate_operations", "gate_version", "is_refusal", "is_restricted_line", "job",
           "key_options", "operation", "poll_command", "refusal_text", "render_gate", "restricted_line", "script"]
