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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.remote import gate
from orchestrator.runtime.bundle import gate_ops  # noqa: F401 - defines bundle-run and bundle-kept
from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.generator import BuiltBundle, BundleError, BundleStore
from orchestrator.runtime.bundle.installer import SIGNED_FILES

JOB_TIMEOUT = 4 * 3600      # copying and checking several GB on a slow board
JOB_POLL = 3.0
LOG_LINES = 2000
GATEWAY_TIMEOUT = 300       # a short bench of three tiny completions on a slow board
_PROBE_WORD = re.compile(r"^([a-z_]{1,24})=([A-Za-z0-9._-]{1,80})$", re.ASCII)


@dataclass
class BoardRun:
    """What one run of the installer on a board gave: its action, its exit status, its report, the upload folder
    (kept while an apply has not finished, so the next deploy continues there) and the notes on the way."""
    action: str
    status: int
    report: inst.Report
    staging: str = ""
    notes: List[str] = field(default_factory=list)
    gateway: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (inst.EXIT_OK, inst.EXIT_ROOT) and self.report.ok

    def lines(self) -> List[str]:
        out = [f"{line['state']} {line['step']} {line['what']}".strip() for line in self.report.lines]
        out.append(f"result: {self.report.result or 'no answer'} ({self.report.changed} changed)"
                   + (f" - {self.report.detail}" if self.report.detail else ""))
        for comp, facts in sorted(self.gateway.items()):
            out.append(f"gateway {comp}: " + " ".join(f"{k}={v}" for k, v in sorted(facts.items())
                                                      if k not in ("fingerprint",)))
        return out + self.notes


def parse_probe(text: str) -> Dict[str, str]:
    """The key=value words of `llmcf-gateway probe` (its `llmcf-gateway: ` lines; anything else is ignored)."""
    facts: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.startswith("llmcf-gateway: "):
            continue
        for word in line[len("llmcf-gateway: "):].split():
            m = _PROBE_WORD.fullmatch(word)
            if m:
                facts.setdefault(m.group(1), m.group(2))
    return facts


def gateway_components(built: BuiltBundle) -> List[Tuple[str, int, bool]]:
    """(component, port, TLS) of every gateway component of the bundle."""
    return [(c.id, int(c.variables.get("port", 8088)), c.variables.get("bind") == "lan")
            for c in built.manifest.components if c.kind == "gateway"]


def check_gateway(shell: Any, built: BuiltBundle, mode: str = "complete", *, store: Optional[BundleStore] = None,
                  device_id: str = "") -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """The gateway components of the bundle checked on the board (gate operation bundle-gateway: health, one tiny
    completion with the bundled model, or a short bench). In TLS mode the certificate's fingerprint is pinned in the
    store's boards.json at the first check (over the board's own SSH session, whose host key is pinned) and compared
    at every later one: a changed certificate is reported, never pinned again. ({component: facts}, notes)."""
    found: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    pins: Dict[str, str] = {}
    if store is not None and device_id:
        pins = dict((store.boards(built.bundle_id).get(device_id) or {}).get("gateway_pins") or {})
    for comp, port, tls in gateway_components(built):
        res = shell.run(gate.script("bundle-gateway", built.bundle_id, comp, str(port), "yes" if tls else "no", mode),
                        timeout=GATEWAY_TIMEOUT, again=True)
        facts = parse_probe(getattr(res, "out", ""))
        health = facts.get("health", "failed")
        state = {"ok": "ok", "absent": "not installed"}.get(health, "not running" if health == "failed" else health)
        if facts.get("complete") == "failed" or facts.get("bench") == "failed":
            state = "no answer"
        entry: Dict[str, Any] = {"state": state, "tls": "yes" if tls else "no"}
        for key in ("upstream", "budget", "control", "version", "tokens_per_s", "avg_ms"):
            if key in facts:
                entry[key] = facts[key]
        fp = facts.get("fingerprint", "")
        if tls and re.fullmatch(r"[0-9a-f]{64}", fp):
            known = pins.get(comp)
            if known and known != fp:
                entry["state"] = "certificate changed"
                notes.append(f"gateway {comp}: its TLS certificate is not the one pinned at the first check; the pin "
                             "is kept (a board made again needs its pin removed by removing the bundle's record)")
            else:
                pins[comp] = fp
                entry["pinned"] = True
                entry["fingerprint"] = fp
        found[comp] = entry
    if store is not None and device_id and found:
        store.record_board(built.bundle_id, device_id, gateway={c: {k: v for k, v in e.items() if k != "fingerprint"}
                                                                for c, e in found.items()}, gateway_pins=pins)
    return found, notes


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
               device_id: str = "", say: Callable[[str], None] = lambda _m: None,
               gateway: str = "complete") -> BoardRun:
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
        run = BoardRun(action, status, inst.report(log))
        if action == "status" and gateway_components(built):
            say("checking the gateway on the board ...")
            run.gateway, notes = check_gateway(shell, built, gateway, store=store, device_id=device_id)
            run.notes.extend(notes)
        return run
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
    names = SIGNED_FILES + ((inst.ROOT_HELPER_FILE,) if built.file(inst.ROOT_HELPER_FILE).is_file() else ())
    signed: List[Tuple[str, Path, str]] = [(name, built.file(name), sha256_file(built.file(name))) for name in names]
    _send(shell, staging, signed, say)
    for name in names:   # the signatures are small: written on stdin, again after a drop
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


def run_root(shell: Any, built: BuiltBundle, action: str, *, password: str = "",
             say: Callable[[str], None] = lambda _m: None) -> BoardRun:
    """llmcf-install `action` (installer.ROOT_ACTIONS) of the bundle on the board behind `shell`, through the gate
    operation bundle-root: the helper the last apply kept, checked against the framework's signature with the
    root-owned signers, run as root with `sudo -n`, or with the person's one-time `password` for `sudo -S` (sent on
    stdin, never kept; the person's channels pass one only in private mode, after the person typed it). Only the
    person's channels call this, after the person's yes for this run (never an AI: the MCP tool bundle_deploy runs the
    unprivileged installer only). Its report is read by installer.report; result needs-password means sudo asked
    for a password and nothing ran."""
    if action not in inst.ROOT_ACTIONS:
        raise BundleError(f"not an action of llmcf-install: {action}")
    if not built.needs_root:
        raise BundleError(f"bundle {built.bundle_id} has no root actions (llmcf-install is not part of it)")
    if any(ch in password for ch in "\r\n\0"):
        raise BundleError("the password holds a character sudo never takes")
    _facts(shell, built)
    say(f"running llmcf-install on the board as root ({action}) ...")
    res = shell.run(gate.script("bundle-root", action, built.bundle_id), stdin=[(password + "\n").encode("utf-8")],
                    timeout=JOB_TIMEOUT, step="bundle")
    return BoardRun("root-" + action, int(getattr(res, "status", 3)), inst.report(getattr(res, "out", "")))


__all__ = ["BoardRun", "GATEWAY_TIMEOUT", "JOB_TIMEOUT", "check_gateway", "gateway_components", "parse_probe",
           "run_bundle", "run_root"]
