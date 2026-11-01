#!/usr/bin/env python3
"""
The bundle's two gate operations (orchestrator/remote/gate.py define; runtime/device/board_ops.py imports this module,
so the gate a pairing installs knows them, with the same argument checks as every other operation):

- bundle-run (a job, start-bundle-run FOLDER UPLOAD ACTION BUNDLE): the installer of the upload folder UPLOAD (exactly
  a staging folder staging-create made), ACTION plan, apply or status;
- bundle-kept (a job, start-bundle-kept FOLDER ACTION BUNDLE): the installer the last apply kept on the board, ACTION
  status, rollback, uninstall or plan-uninstall.

Both copy install.sh, steps.tsv and manifest.json with their signatures into a new folder of their own
(~/.local/share/llm-framework/bundle/.run-XXXXXXXX, which no upload operation can write to), check the framework's
signature of all three there (always in the gate, and in an unrestricted login once the board holds the root-owned
signers file: gate.signed_sh, the same check as for runtime tarballs and packages), and only then run the installer;
its report goes into the job's log (install-log reads it). The gate refuses an action or a bundle id the operation does
not take before anything runs (and the signed installer checks them again).
"""

import re
from typing import Tuple

from orchestrator.remote import gate
from orchestrator.remote.gate import define
from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.installer import SIGNED_FILES

RUN_PREFIX = ".run-"
_RUN_ACTIONS = inst.ACTIONS
_KEPT_ACTIONS = inst.KEPT_ACTIONS
_ROOT_ACTIONS = inst.ROOT_ACTIONS
ROOT = inst.ROOT_HELPER_FILE


def _job_body(source: str, files: str, action: str, bundle: str, allowed: Tuple[str, ...]) -> str:
    """The job of bundle-run / bundle-kept with its values in place (checked values, or the gate's "$LLMCF_A..")."""
    names = " ".join(SIGNED_FILES)
    cases = "|".join(allowed)
    return "(" + "\n".join([
        gate.signed_sh(),
        'llmcf_log="$(pwd)/' + gate.JOB_LOG + '"',
        f'case {action} in {cases}) ;; *) echo "llmcf-bundle: result=refused changed=0 not an action of this '
        f'operation" > "$llmcf_log"; exit 3 ;; esac',
        f'case {bundle} in ""|-*|.*|*[!a-z0-9._-]*) echo "llmcf-bundle: result=refused changed=0 not a bundle id" '
        '> "$llmcf_log"; exit 3 ;; esac',
        f'llmcf_b="$HOME"/{inst.BUNDLE_REL}',
        'mkdir -p "$llmcf_b" || exit 3',
        f'llmcf_r=$(mktemp -d "$llmcf_b/{RUN_PREFIX}XXXXXXXX") || exit 3',
        "trap 'cd /; rm -rf \"$llmcf_r\"' EXIT",
        f"llmcf_s={source}",
        f"for llmcf_f in {names}; do",
        '  if [ ! -f "$llmcf_s/$llmcf_f" ]; then echo "llmcf-bundle: result=refused changed=0 the bundle file '
        '$llmcf_f is not on the board" > "$llmcf_log"; exit 3; fi',
        '  cp -- "$llmcf_s/$llmcf_f" "$llmcf_r/$llmcf_f" || exit 3',
        '  [ ! -f "$llmcf_s/$llmcf_f.sig" ] || cp -- "$llmcf_s/$llmcf_f.sig" "$llmcf_r/$llmcf_f.sig" || exit 3',
        "done",
        # the root helper a bundle with root actions carries: copied and checked like the three when it is there
        f'llmcf_x=""; if [ -f "$llmcf_s/{ROOT}" ]; then llmcf_x={ROOT}',
        f'  cp -- "$llmcf_s/{ROOT}" "$llmcf_r/{ROOT}" || exit 3',
        f'  [ ! -f "$llmcf_s/{ROOT}.sig" ] || cp -- "$llmcf_s/{ROOT}.sig" "$llmcf_r/{ROOT}.sig" || exit 3',
        "fi",
        ": > \"$llmcf_log\"",
        f"if llmcf_must_sign; then for llmcf_f in {names} $llmcf_x; do",
        '  llmcf_signed "$llmcf_r/$llmcf_f" "$llmcf_r/$llmcf_f.sig" >> "$llmcf_log" 2>&1 || { echo "llmcf-bundle: '
        'result=refused changed=0 the bundle file $llmcf_f is not signed with the signing key of the framework '
        '(nothing was changed)" >> "$llmcf_log"; exit 3; }',
        "done; fi",
        f'sh "$llmcf_r/install.sh" {action} "$llmcf_r" {files} {bundle} >> "$llmcf_log" 2>&1',
    ]) + "\n)"


def _run_refusal(args) -> str:
    return "" if args[1] in _RUN_ACTIONS and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,47}", args[2]) else \
        "not an action or a bundle id it takes"


def _kept_refusal(args) -> str:
    return "" if args[0] in _KEPT_ACTIONS and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,47}", args[1]) else \
        "not an action or a bundle id it takes"


define("bundle-run", ("staging", "word", "word"),
       lambda upload, action, bundle, rest="": _job_body(upload, upload, action, bundle, _RUN_ACTIONS),
       job=True, refuse=_run_refusal,
       doc="the bundle's installer from its upload folder (plan, apply, status), only when the framework signed it")
define("bundle-kept", ("word", "word"),
       lambda action, bundle, rest="": _job_body(f'"$llmcf_b"/{bundle}/.kept', f'"$llmcf_b"/{bundle}/.kept',
                                                 action, bundle, _KEPT_ACTIONS),
       job=True, refuse=_kept_refusal,
       doc="the installer the last apply kept (status, rollback, uninstall, plan-uninstall), only when signed")


# --- the board's root readiness for llmcf-install (bundle/readiness.py says what each word means) ----------------------

# Where the root readiness puts llmcf-install and its sudo rule on the board: a fixed root-owned path, never the user's
# own bundle folder, and a fixed sudoers drop-in that lets the board account run only that program without a password.
FIXED_HELPER = gate.LIBEXEC_DIR + "/" + ROOT
FIXED_DIR = gate.LIBEXEC_DIR
DROPIN = gate.SUDOERS_DIR + "/llmcf-install"
_HELPER_ACTIONS = inst.ROOT_ACTIONS


def places_sh(helper: str = FIXED_HELPER, dropin: str = DROPIN) -> str:
    """The sh assignments of the root readiness's fixed places: llmcf_fx (llmcf-install), llmcf_fd (its folder),
    llmcf_fp (the folder above), llmcf_di (the sudoers drop-in), llmcf_dd (its folder). `helper` and `dropin` are
    parameters for the tests only (a temporary folder), as gate.signed_sh's are."""
    folder = helper.rsplit("/", 1)[0]
    parent = folder.rsplit("/", 1)[0] or "/"
    return (f"llmcf_fx={helper}; llmcf_fd={folder}; llmcf_fp={parent}; llmcf_di={dropin}; "
            f"llmcf_dd={dropin.rsplit('/', 1)[0] or '/'}")


_FIXED_FNS = "\n".join([
    'llmcf_sha() { sha256sum "$1" 2>/dev/null | cut -d" " -f1; }',
    # llmcf-install at its fixed place is root-owned with its two folders, so the account can never swap it
    'llmcf_fixed_ok() { [ -f "$llmcf_fx" ] && llmcf_rootowned "$llmcf_fx" && llmcf_rootowned "$llmcf_fd" && '
    'llmcf_rootowned "$llmcf_fp"; }',
    # sudo lets the account run llmcf-install at its fixed place without a password (sudo -n -l never prompts)
    'llmcf_dropin_ok() { command -v sudo >/dev/null 2>&1 && sudo -n -l "$llmcf_fx" "$1" "$2" >/dev/null 2>&1; }',
])


def _ready_body(sha: str) -> str:
    """bundle-ready: what llmcf-install needs on this board, read without root and without a password, in fixed
    words: sudo=yes|no, rootway=nopass|password|none (sudo -n true), signers=root|none, account=ok|bad,
    helper=same|other|absent|unsafe (llmcf-install at its fixed root-owned place, its sha256 against `sha`),
    dropin=yes|no, and the board's family/dietpi/manager words (gate.SYSTEM_FACTS)."""
    return "\n".join([
        gate.signed_sh(),
        places_sh(),
        _FIXED_FNS,
        "if command -v sudo >/dev/null 2>&1; then echo sudo=yes; if sudo -n true >/dev/null 2>&1; then "
        "echo rootway=nopass; else echo rootway=password; fi; else echo sudo=no; echo rootway=none; fi",
        "if command -v su >/dev/null 2>&1; then echo su=yes; else echo su=no; fi",
        "LLMCF_HOME_GATE=; if llmcf_trusted_signers; then echo signers=root; else echo signers=none; fi",
        'llmcf_u=$(id -un 2>/dev/null) || llmcf_u=""',
        'case $llmcf_u in ""|root|-*|*[!A-Za-z0-9._-]*) echo account=bad ;; *) echo account=ok ;; esac',
        'if [ ! -e "$llmcf_fx" ]; then echo helper=absent; elif ! llmcf_fixed_ok; then echo helper=unsafe; '
        f'elif [ "$(llmcf_sha "$llmcf_fx")" = {sha} ]; then echo helper=same; else echo helper=other; fi',
        "if llmcf_dropin_ok status llmcf-check; then echo dropin=yes; else echo dropin=no; fi",
        *gate.SYSTEM_FACTS,
    ])


def _install_lines(manager: str) -> list:
    """The sh lines that install sudo with the board's package manager `manager` as root (asroot): openssh_switch's
    known commands per manager (install.sh's way: the lists read first where the manager needs it, pacman never -Sy),
    as a case over the manager word (the gate gets the word as its argument)."""
    from orchestrator.remote import openssh_switch as sw
    lines = [f"case {manager} in"]
    for name, command in sw.INSTALL_COMMANDS.items():
        steps = []
        if name == "pacman":
            steps.append(f"ls {sw.PACMAN_SYNC_DIR}/*.db >/dev/null 2>&1 || no no-package-db")
        prepare = sw.PREPARE_COMMANDS.get(name, "")
        if prepare:
            steps.append(f"asroot sh -c '{prepare}' >/dev/null 2>&1 || no lists")
        steps.append(f"asroot sh -c '{command} sudo' >/dev/null 2>&1 || no install")
        lines.append(f"  {name}) " + "; ".join(steps) + " ;;")
    lines += ["  *) no no-manager ;;", "esac"]
    return lines


def _setup_body(via: str, manager: str, bundle: str, sha: str) -> str:
    """bundle-ready-setup: sets up what llmcf-install needs, only what is missing, after the person's yes: stdin's
    first line is the person's one-time password (the account's for sudo -S, root's for su; empty: sudo -n). sudo
    through the board's package manager (only `via` su, root's password: a board without sudo), then, only with the
    root-owned signer files there (they come from pairing's root step, never from here), llmcf-install copied from
    the bundle the last apply kept, its signature checked against those signers and its sha256 against `sha`,
    installed root-owned at its fixed place and checked again there, and the sudoers drop-in (visudo -cf before it is
    moved in) that lets this account run only that program without a password. Answers ready=done changed=WORDS, or
    ready=unavailable reason=WORD changed=WORDS (what was done before it stopped)."""
    actions = ", ".join(f"$llmcf_fx {a} *" for a in _HELPER_ACTIONS)
    return "\n".join([
        gate.signed_sh(),
        places_sh(),
        _FIXED_FNS,
        "set -u",
        "umask 077",
        'IFS= read -r llmcf_pw || llmcf_pw=""',
        "llmcf_ch=",
        'did() { llmcf_ch="${llmcf_ch:+$llmcf_ch,}$1"; }',
        'no() { echo "ready=unavailable reason=$1 changed=${llmcf_ch:-none}"; exit 0; }',
        f"case {via} in sudo|su) ;; *) no via ;; esac",
        f'case {bundle} in ""|-*|.*|*[!a-z0-9._-]*) no bundle ;; esac',
        'llmcf_u=$(id -un 2>/dev/null) || no account',
        'case $llmcf_u in ""|root|-*|*[!A-Za-z0-9._-]*) no account ;; esac',
        'case $HOME in /*) ;; *) no home ;; esac',
        'case $HOME in *[!A-Za-z0-9._/-]*) no home ;; esac',
        # su takes one command line: every word quoted on its own
        "llmcf_q() { for llmcf_a in \"$@\"; do printf \"'%s' \" \"$(printf '%s' \"$llmcf_a\" | sed \"s/'/'\\\\\\\\''/g\")\"; "
        "done; }",
        f'asroot() {{ if [ {via} = su ]; then printf "%s\\n" "$llmcf_pw" | su root -c "$(llmcf_q "$@")"; '
        'elif [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; else sudo -n "$@"; fi; }',
        # 1. sudo, like any other dependency: through the board's package manager, as root once with su
        "if ! command -v sudo >/dev/null 2>&1; then",
        f"  [ {via} = su ] || no no-sudo",
        "  command -v su >/dev/null 2>&1 || no no-su",
        "  asroot true >/dev/null 2>&1 || no su-refused",
        *("  " + line for line in _install_lines(manager)),
        "  command -v sudo >/dev/null 2>&1 || no install",
        "  did sudo",
        "fi",
        # 2. the root-owned signer files: pairing's root step puts them there, never this operation
        "LLMCF_HOME_GATE=; llmcf_trusted_signers || no no-signer",
        # root is asked for only when something is missing (a second setup of a ready board needs no password)
        f'if ! {{ llmcf_fixed_ok && [ "$(llmcf_sha "$llmcf_fx")" = {sha} ] && llmcf_dropin_ok status {bundle}; }}; then',
        "  asroot true >/dev/null 2>&1 || { if [ -n \"$llmcf_pw\" ]; then no sudo-refused; else no needs-password; fi; }",
        "fi",
        f'llmcf_b="$HOME"/{inst.BUNDLE_REL}; llmcf_k="$llmcf_b"/{bundle}/.kept',
        f'[ -f "$llmcf_k/{ROOT}" ] && [ -f "$llmcf_k/{ROOT}.sig" ] || no no-bundle',
        f'llmcf_r=$(mktemp -d "$llmcf_b/{RUN_PREFIX}XXXXXXXX") || no tmp',
        "trap 'cd /; rm -rf \"$llmcf_r\"' EXIT",
        # 3. llmcf-install at its fixed root-owned place (only when it is not this bundle's already)
        f'if ! {{ llmcf_fixed_ok && [ "$(llmcf_sha "$llmcf_fx")" = {sha} ]; }}; then',
        f'  cp -- "$llmcf_k/{ROOT}" "$llmcf_r/{ROOT}" && cp -- "$llmcf_k/{ROOT}.sig" "$llmcf_r/{ROOT}.sig" || no tmp',
        f'  llmcf_signed "$llmcf_r/{ROOT}" "$llmcf_r/{ROOT}.sig" >/dev/null 2>&1 || no not-signed',
        f'  [ "$(llmcf_sha "$llmcf_r/{ROOT}")" = {sha} ] || no helper-sha',
        '  asroot install -d -m 0755 -o root -g root "$llmcf_fd" >/dev/null 2>&1 || no sudo',
        f'  asroot install -m 0755 -o root -g root "$llmcf_r/{ROOT}" "$llmcf_fx.llmcf-new" >/dev/null 2>&1 || no sudo',
        '  asroot mv -f "$llmcf_fx.llmcf-new" "$llmcf_fx" >/dev/null 2>&1 || no sudo',
        f'  if ! {{ llmcf_fixed_ok && [ "$(llmcf_sha "$llmcf_fx")" = {sha} ]; }}; then '
        'asroot rm -f "$llmcf_fx" >/dev/null 2>&1; no helper-changed; fi',
        "  did helper",
        "fi",
        # 4. the sudoers drop-in: only llmcf-install at its fixed place, only its actions, no password
        f"if ! llmcf_dropin_ok status {bundle}; then",
        '  llmcf_v=$(command -v visudo 2>/dev/null || echo /usr/sbin/visudo)',
        '  [ -x "$llmcf_v" ] || no no-visudo',
        '  printf "%s\\n" "# llmcf-install: the board account may run only the framework\'s root helper at its fixed '
        'root-owned place, without a password (generated by llmcf)" '
        f'"$llmcf_u ALL=(root) NOPASSWD: {actions}" > "$llmcf_r/sudoers" || no tmp',
        '  "$llmcf_v" -cf "$llmcf_r/sudoers" >/dev/null 2>&1 || no sudoers',
        '  [ -d "$llmcf_dd" ] || asroot install -d -m 0755 -o root -g root "$llmcf_dd" >/dev/null 2>&1 || no sudo',
        # a name with a '.' is skipped by sudo's includedir: the new file is not read before it is checked and moved
        '  asroot install -m 0440 -o root -g root "$llmcf_r/sudoers" "$llmcf_di.llmcf-new" >/dev/null 2>&1 || no sudo',
        '  if ! asroot "$llmcf_v" -cf "$llmcf_di.llmcf-new" >/dev/null 2>&1; then '
        'asroot rm -f "$llmcf_di.llmcf-new" >/dev/null 2>&1; no sudoers; fi',
        '  asroot mv -f "$llmcf_di.llmcf-new" "$llmcf_di" >/dev/null 2>&1 || no sudo',
        f"  llmcf_dropin_ok status {bundle} || no dropin",
        "  did dropin",
        "fi",
        'echo "ready=done changed=${llmcf_ch:-none}"',
    ])


def _remove_body(via: str) -> str:
    """bundle-ready-remove: after the person's separate yes, removes exactly the sudoers drop-in and llmcf-install at
    its fixed place (never their folders, never anything else), as root once (stdin: the person's one-time
    password). Answers removed=WORDS or removed=unavailable reason=WORD."""
    return "\n".join([
        places_sh(),
        "set -u",
        'IFS= read -r llmcf_pw || llmcf_pw=""',
        'no() { echo "removed=unavailable reason=$1"; exit 0; }',
        f"case {via} in sudo|su) ;; *) no via ;; esac",
        "llmcf_q() { for llmcf_a in \"$@\"; do printf \"'%s' \" \"$llmcf_a\"; done; }",
        f'asroot() {{ if [ {via} = su ]; then printf "%s\\n" "$llmcf_pw" | su root -c "$(llmcf_q "$@")"; '
        'elif [ -n "$llmcf_pw" ]; then printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$@"; else sudo -n "$@"; fi; }',
        "llmcf_gone=",
        'if [ -e "$llmcf_di" ]; then asroot rm -f "$llmcf_di" >/dev/null 2>&1 || no sudo; llmcf_gone=dropin; fi',
        'if [ -e "$llmcf_fx" ]; then asroot rm -f "$llmcf_fx" >/dev/null 2>&1 || no sudo; '
        'llmcf_gone="${llmcf_gone:+$llmcf_gone,}helper"; fi',
        'echo "removed=${llmcf_gone:-none}"',
    ])


def _root_body(action: str, bundle: str, sha: str) -> str:
    """bundle-root with its values in place: stdin's first line is the person's one-time password for sudo -S (empty:
    sudo -n only; the framework sends one only in private mode, after the person typed it for this run). It runs
    llmcf-install from its fixed root-owned place (the root readiness put it there, checked against the framework's
    signature then), never from the account's own folders: only when that program is root-owned with its folders and
    its sha256 is `sha` (this bundle's llmcf-install), with the action and the bundle id, through the sudoers drop-in
    (sudo -n) or the person's password; llmcf-install checks the kept manifest's signature itself against the
    ROOT-OWNED signer files (never a home gate's: LLMCF_HOME_GATE is cleared for the check)."""
    cases = "|".join(_ROOT_ACTIONS)
    refuse = 'echo "llmcf-bundle: result={0} changed=0 {1}"; exit 3'
    return "\n".join([
        gate.signed_sh(),
        places_sh(),
        _FIXED_FNS,
        'IFS= read -r llmcf_pw || llmcf_pw=""',
        f'case {action} in {cases}) ;; *) {refuse.format("refused", "not an action of this operation")} ;; esac',
        f'case {bundle} in ""|-*|.*|*[!a-z0-9._-]*) {refuse.format("refused", "not a bundle id")} ;; esac',
        f'llmcf_b="$HOME"/{inst.BUNDLE_REL}; llmcf_k="$llmcf_b"/{bundle}/.kept',
        # no kept helper: nothing to plan or apply (deploy first); for the other actions nothing it could have made
        f'if [ ! -f "$llmcf_k/{ROOT}" ] || [ ! -f "$llmcf_k/{ROOT}.sig" ]; then case {action} in plan|apply) '
        + refuse.format("refused", f"{ROOT} of this bundle is not on the board (deploy the bundle first)") + " ;; "
        f'*) echo "llmcf-bundle: result=absent changed=0 {ROOT} of this bundle is not on the board"; exit 0 ;; '
        "esac; fi",
        # where llmcf-install cannot run as root at all (no root-owned signer, no sudo), it made nothing of the
        # bundle: plan and apply are refused, the other actions say absent, so the installer's own status, rollback
        # and uninstall stay possible there, as before llmcf-install
        "LLMCF_HOME_GATE=; llmcf_trusted_signers || { "
        + f"case {action} in plan|apply) "
        + refuse.format("refused", "this board holds no root-owned signing key of the framework, so no root action "
                                   "runs (pair it again with the board password)") + " ;; "
        f'*) echo "llmcf-bundle: result=absent changed=0 this board holds no root-owned signing key of the framework, '
        f'so {ROOT} never ran here"; exit 0 ;; esac; }}',
        f"case {action} in plan|apply) ;; *) command -v sudo >/dev/null 2>&1 || {{ echo \"llmcf-bundle: "
        f'result=absent changed=0 the board has no sudo, so {ROOT} never ran here"; exit 0; }} ;; esac',
        "command -v sudo >/dev/null 2>&1 || { " + refuse.format(
            "not-ready", "the board has no sudo yet (set up the board's root readiness; nothing was changed)") + "; }",
        "llmcf_fixed_ok || { " + refuse.format(
            "not-ready", f"{ROOT} is not at its root-owned place on the board (set up the board's root readiness; "
                         "nothing was changed)") + "; }",
        f'[ "$(llmcf_sha "$llmcf_fx")" = {sha} ] || {{ '
        + refuse.format("not-ready", f"{ROOT} at its root-owned place is not this bundle's (update it through the "
                                     "board's root readiness; nothing was changed)") + "; }",
        f"if llmcf_dropin_ok {action} {bundle}; then",
        f'  sudo -n "$llmcf_fx" {action} {bundle} 2>&1',
        'elif [ -n "$llmcf_pw" ]; then',
        '  printf "%s\\n" "$llmcf_pw" | sudo -S -p "" true >/dev/null 2>&1 || { '
        + refuse.format("sudo-refused", "sudo did not take the password (nothing was changed)") + "; }",
        f'  printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$llmcf_fx" {action} {bundle} 2>&1',
        "else",
        "  " + refuse.format("needs-password", "sudo asks for a password on this board (sudo -n refused; nothing was "
                                               "changed)"),
        "fi",
    ])


_BUNDLE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}")


def _root_refusal(args) -> str:
    return "" if args[0] in _ROOT_ACTIONS and _BUNDLE_ID.fullmatch(args[1]) else \
        "not an action or a bundle id it takes"


def _setup_refusal(args) -> str:
    from orchestrator.remote import openssh_switch as sw
    if args[0] not in ("sudo", "su"):
        return "not a way to reach root (sudo or su)"
    if args[1] != "-" and args[1] not in sw.INSTALL_COMMANDS:
        return "not a package manager the framework knows"
    return "" if _BUNDLE_ID.fullmatch(args[2]) else "not a bundle id"


# The one operation that runs anything as root for a bundle: llmcf-install, signed by the framework, at its fixed
# root-owned place, through sudo, only for an action the person said yes to in the CLI or the Board bundle window (an AI
# never calls it: the MCP tool bundle_deploy runs the unprivileged installer only).
define("bundle-root", ("word", "word", "sha256"),
       lambda action, bundle, sha, rest="": _root_body(action, bundle, sha), refuse=_root_refusal,
       doc="llmcf-install at its fixed root-owned place, as root through its sudoers drop-in (sudo -n) or the "
           "person's one-time password on stdin for sudo -S, only when it is this bundle's and the signers are "
           "root-owned")
# The board's root readiness (bundle/readiness.py): what llmcf-install needs, read without root; set up after the
# person's yes (only what is missing); removed after a separate yes. Only the person's channels call the last two.
define("bundle-ready", ("sha256",), lambda sha, rest="": _ready_body(sha),
       doc="what llmcf-install needs on this board (sudo, root-owned signers, the program at its fixed place, its "
           "sudo rule), read without root and without a password")
define("bundle-ready-setup", ("word", "word", "word", "sha256"),
       lambda via, manager, bundle, sha, rest="": _setup_body(via, manager, bundle, sha), refuse=_setup_refusal,
       doc="after the person's yes: sudo through the package manager (root once with su), llmcf-install of the kept "
           "bundle at its fixed root-owned place (signature and sha256 checked), its sudoers drop-in (visudo -cf)")
define("bundle-ready-remove", ("word",), lambda via, rest="": _remove_body(via),
       refuse=lambda args: "" if args[0] in ("sudo", "su") else "not a way to reach root (sudo or su)",
       doc="after the person's separate yes: removes exactly the sudoers drop-in and llmcf-install at its fixed place")


__all__ = ["DROPIN", "FIXED_DIR", "FIXED_HELPER", "RUN_PREFIX", "places_sh"]
