#!/usr/bin/env python3
"""
The bundle on a board: the deploy over the board session, through the gate operations bundle-run and bundle-kept
(gate_ops.py). Uploads go through the gate's own file operations (file-write
and file-append on the board session's stdin, resumable and hash-checked: RemoteSession.upload), never scp or SFTP
writes beyond what the session already does.

run_bundle is the one deploy flow for the CLI, the Board bundle window and the AI's bundle_deploy (after the person
approved it in the framework's own window): the board's facts (no root login, the bundle's architecture), the upload
of what is not on the board yet (a deploy cut off continues with the same upload folder, kept in the store's
boards.json), the installer job, its report, and the upload folder removed after an apply that finished.
"""

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from orchestrator.remote import gate
from orchestrator.runtime.bundle import gate_ops  # noqa: F401 - defines bundle-run and bundle-kept
from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.generator import BuiltBundle, BundleError, BundleStore
from orchestrator.runtime.bundle.installer import SIGNED_FILES

JOB_TIMEOUT = 4 * 3600      # copying and checking several GB on a slow board
JOB_POLL = 3.0
LOG_LINES = 2000


@dataclass
class BoardRun:
    """What one run of the installer on a board gave: its action, its exit status, its report, the upload folder
    (kept while an apply has not finished, so the next deploy continues there) and the notes on the way."""
    action: str
    status: int
    report: inst.Report
    staging: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (inst.EXIT_OK, inst.EXIT_ROOT) and self.report.ok

    def lines(self) -> List[str]:
        out = [f"{line['state']} {line['step']} {line['what']}".strip() for line in self.report.lines]
        out.append(f"result: {self.report.result or 'no answer'} ({self.report.changed} changed)"
                   + (f" - {self.report.detail}" if self.report.detail else ""))
        return out + self.notes


def _facts(shell: Any, built: BuiltBundle):
    from orchestrator.runtime.device.remote import RemoteError, read_facts
    facts = read_facts(shell)
    if facts.uid == 0:
        raise RemoteError("the SSH user on the board is root: the bundle installs as an unprivileged account (root "
                          "actions go through llmcf-install)")
    want = built.manifest.board.arch
    if want != "unknown" and facts.arch and facts.arch != want:
        raise RemoteError(f"the board runs {facts.arch}, but the bundle is for {want}: plan it again for this board")
    return facts


def _new_folder(shell: Any, root: str) -> str:
    from orchestrator.runtime.device.remote import RemoteError
    res = shell.run(gate.script("staging-create", root))
    folder = res.out.strip()
    if res.status != 0 or not folder.startswith(root + "/" + gate.STAGING_PREFIX):
        raise RemoteError(f"could not make a folder for the bundle on the board (exit {res.status})")
    return folder


def _on_board(shell: Any, remote: str, sha256: str) -> bool:
    res = shell.run(gate.script("file-sha256", remote), again=True)
    return res.status == 0 and res.out.split()[:1] == [sha256]


def _job(shell: Any, folder: str, job: Any, step: str) -> Tuple[int, str]:
    status = shell.run_detached(folder, job, timeout=JOB_TIMEOUT, poll=JOB_POLL, step=step)
    log = shell.run(gate.script("install-log", folder, str(LOG_LINES)), again=True)
    shell.run(gate.script("dir-remove", folder), again=True)
    return status, log.out


def _send(shell: Any, staging: str, uploads: List[Tuple[str, Path, str]], say: Callable[[str], None]) -> None:
    for name, path, sha in uploads:
        remote = f"{staging}/{name}"
        if _on_board(shell, remote, sha):
            continue
        say(f"uploading {name} ...")
        shell.upload(path, remote, sha)


def _installed_files(shell: Any, root: str, staging: str, bid: str) -> set:
    """The staged names (<component>--<file>) of the bundle's files the board's installer reports in place with
    their sha256 (its status from the upload folder; an empty set when the bundle is not on the board yet)."""
    folder = _new_folder(shell, root)
    status, log = _job(shell, folder, gate.job("bundle-run", staging, "status", bid), "bundle")
    found = inst.report(log)
    if status != inst.EXIT_OK or found.result not in ("installed", "partial"):
        return set()
    out = set()
    for line in found.lines:
        parts = line["what"].split(" ")
        if line["state"] == "already" and line["step"] in ("file", "fetch") and len(parts) == 2:
            out.add(inst.staged_name(parts[0], parts[1]))
    return out


def run_bundle(shell: Any, built: BuiltBundle, action: str, *, store: Optional[BundleStore] = None,
               device_id: str = "", say: Callable[[str], None] = lambda _m: None) -> BoardRun:
    """Runs the bundle's installer on the board behind `shell` (see the module text): `action` plan or apply (the
    bundle is uploaded first), status, rollback, uninstall or plan-uninstall (the installer the board kept). The
    stored bundle is checked first (every file against its manifest, the three signatures): a changed bundle is never
    sent. BundleError or RemoteError with the reason; the installer's own refusals are in the report."""
    from orchestrator.utils.helpers import sha256_file
    if action not in inst.ACTIONS + inst.KEPT_ACTIONS:
        raise BundleError(f"not an installer action: {action}")
    facts = _facts(shell, built)
    root = facts.root
    bid = built.bundle_id
    if action in inst.KEPT_ACTIONS:
        say("asking the installer the board kept ...")
        folder = _new_folder(shell, root)
        status, log = _job(shell, folder, gate.job("bundle-kept", action, bid), "bundle")
        return BoardRun(action, status, inst.report(log))
    if store is not None:
        problems = store.check(built)
        if problems:
            raise BundleError("the stored bundle is not the one that was built: " + "; ".join(problems[:5]))
    staging = ""
    known = (store.boards(bid).get(device_id) or {}).get("staging", "") if store is not None and device_id else ""
    if known and posixpath.dirname(known) == root and shell.run(gate.script("dir-exists", known), again=True).status == 0:
        staging = known
        say("continuing in the upload folder of the last deploy ...")
    if not staging:
        say("making an upload folder on the board ...")
        staging = _new_folder(shell, root)
    if store is not None and device_id:
        store.record_board(bid, device_id, staging=staging, last="uploading")
    signed: List[Tuple[str, Path, str]] = [(name, built.file(name), sha256_file(built.file(name)))
                                           for name in SIGNED_FILES]
    _send(shell, staging, signed, say)
    for name in SIGNED_FILES:   # the signatures are small: written on stdin, again after a drop
        sig = built.file(name + ".sig").read_bytes()
        res = shell.run(gate.script("file-write-private", f"{staging}/{name}.sig"), stdin=[sig], again=True)
        if res.status != 0:
            from orchestrator.runtime.device.remote import RemoteError
            raise RemoteError(f"the signature of {name} could not be written on the board (exit {res.status})")
    payload = [(name, path, entry.sha256) for name, path, entry in built.payload()]
    if action in ("plan", "apply") and payload:
        # what the board holds in place already (the signed installer's own status, which changes nothing) is not
        # sent again: a second deploy of an installed bundle sends no model a second time
        installed = _installed_files(shell, root, staging, bid)
        payload = [item for item in payload if item[0] not in installed]
    _send(shell, staging, payload, say)
    say(f"running the installer on the board ({action}) ...")
    folder = _new_folder(shell, root)
    status, log = _job(shell, folder, gate.job("bundle-run", staging, action, bid), "bundle")
    run = BoardRun(action, status, inst.report(log), staging=staging)
    finished = action == "apply" and run.report.result in ("ok", "stopped-root")
    if finished:
        shell.run(gate.script("dir-remove", staging), again=True)
        run.staging = ""
    if store is not None and device_id:
        store.record_board(bid, device_id, staging=run.staging, last=f"{action}:{run.report.result or 'none'}")
    return run


__all__ = ["BoardRun", "JOB_TIMEOUT", "run_bundle"]
