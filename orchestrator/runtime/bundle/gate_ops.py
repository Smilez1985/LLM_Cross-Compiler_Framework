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
        ": > \"$llmcf_log\"",
        f"if llmcf_must_sign; then for llmcf_f in {names}; do",
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


__all__ = ["RUN_PREFIX"]
