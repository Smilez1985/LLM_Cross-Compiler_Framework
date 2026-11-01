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


def _root_body(action: str, bundle: str) -> str:
    """bundle-root with its values in place: stdin's first line is the person's one-time password for sudo -S (empty:
    sudo -n only; the framework sends one only in private mode, after the person typed it for this run). The kept
    llmcf-install and its signature are copied into a folder of their own, checked against the ROOT-OWNED signer
    files only (never a home gate's: LLMCF_HOME_GATE is cleared for the check), and only then run as root with the
    action and the bundle id; llmcf-install checks the kept manifest's signature itself again."""
    cases = "|".join(_ROOT_ACTIONS)
    refuse = 'echo "llmcf-bundle: result={0} changed=0 {1}"; exit 3'
    return "\n".join([
        gate.signed_sh(),
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
        f'llmcf_r=$(mktemp -d "$llmcf_b/{RUN_PREFIX}XXXXXXXX") || exit 3',
        "trap 'cd /; rm -rf \"$llmcf_r\"' EXIT",
        f'cp -- "$llmcf_k/{ROOT}" "$llmcf_r/{ROOT}" && cp -- "$llmcf_k/{ROOT}.sig" "$llmcf_r/{ROOT}.sig" || exit 3',
        f'llmcf_signed "$llmcf_r/{ROOT}" "$llmcf_r/{ROOT}.sig" 2>&1 || {{ '
        + refuse.format("refused", f"{ROOT} is not signed with the signing key of the framework (nothing was "
                                   "changed)") + "; }",
        f'chmod 0700 "$llmcf_r/{ROOT}" || exit 3',
        "command -v sudo >/dev/null 2>&1 || { " + refuse.format("no-sudo", "the board has no sudo") + "; }",
        'if [ -n "$llmcf_pw" ]; then',
        '  printf "%s\\n" "$llmcf_pw" | sudo -S -p "" true >/dev/null 2>&1 || { '
        + refuse.format("sudo-refused", "sudo did not take the password (nothing was changed)") + "; }",
        f'  printf "%s\\n" "$llmcf_pw" | sudo -S -p "" "$llmcf_r/{ROOT}" {action} {bundle} 2>&1',
        "else",
        "  sudo -n true >/dev/null 2>&1 || { "
        + refuse.format("needs-password", "sudo asks for a password on this board (sudo -n refused; nothing was "
                                          "changed)") + "; }",
        f'  sudo -n "$llmcf_r/{ROOT}" {action} {bundle} 2>&1',
        "fi",
    ])


def _root_refusal(args) -> str:
    return "" if args[0] in _ROOT_ACTIONS and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,47}", args[1]) else \
        "not an action or a bundle id it takes"


# The one operation that runs anything as root for a bundle: llmcf-install, signed by the framework, through sudo, only
# for an action the person said yes to in the CLI or the Board bundle window (an AI never calls it: the MCP tool
# bundle_deploy runs the unprivileged installer only).
define("bundle-root", ("word", "word"), lambda action, bundle, rest="": _root_body(action, bundle),
       refuse=_root_refusal,
       doc="llmcf-install of the bundle the last apply kept, as root through sudo -n (or the person's one-time "
           "password on stdin for sudo -S), only when the framework signed it and the signers are root-owned")


__all__ = ["RUN_PREFIX"]
