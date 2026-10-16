#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the boards the person trusted (eighth round N6, W1 of 2026-10-16)

A default Mojo Oracle run makes NO network contact with any board ("why would Mojo connect to the boards? only when
there is a problem to find there"). It reports what the framework recorded, from files only:

- the host key in the framework's known_hosts: a key that changed (device_host_key_changed) or that is gone;
- the last contact with a board that failed with an SSH reason code (device_host_down, device_port_closed,
  device_auth_failed, device_host_key_changed): a deploy, runtime deploy, verify, benchmark or probe, recorded by
  TrustedDevices.note_reach;
- what the last deploy saw on the board (an old Qdrant sidecar).

A board with such a recorded problem is offered for a check (Finding.board_check): at a terminal "Check <board> now?
[y/N]", for an AI the request it can make. A board is contacted only on request, for that one board (`contact`:
`llmcf mojo --oracle --board NAME`, run_mojo_oracle {"board": "NAME"}): one TCP connection to its SSH port
(runtime/device/reasons.check_port: no login, no password, no key, no ICMP). Mojo never logs in to a board, so a
wrong password or a framework key that is gone (device_auth_failed) shows at the next deploy or benchmark only. The
device account is never named.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

MAX_CONTACT = 4   # boards one request may check at once (the CLI and the tool name one)
BOARD_WHY = ("Deploys, benchmarks on the board and the AI's board tools reach a board only on its SSH port and with "
             "the host key you trusted.")
NO_CONTACT = "Mojo did not contact the board."


def _reason(device, code: str) -> Tuple[str, str]:
    """The person's title and next step for a reason code (address and port, never the account)."""
    from orchestrator.runtime.device import reasons
    return (reasons.person_title(code, "en", address=device.host, port=device.port),
            reasons.person_next(code, "en", address=device.host, port=device.port))


def _recorded(problem: Dict[str, str]) -> str:
    """'the last deploy (2026-10-16T09:12:00Z) failed with device_host_down' for a recorded problem."""
    from orchestrator.runtime.device.trusted import REACH_STEPS
    return f"the last {REACH_STEPS[problem['step']]} ({problem['at']}) failed with {problem['code']}"


def _old_sidecars(store, boards) -> List[Finding]:
    """Boards where the last deploy still saw the Qdrant sidecar of an earlier package with Docker images (the deploy
    looked, Mojo does not log in): named with the next step; nothing is stopped or removed."""
    from orchestrator.Core.deployment_manager import OLD_SIDECAR
    last = store.last_deploys()
    findings = []
    for device in boards:
        seen = last.get(device.device_id) or {}
        state = seen.get("old_sidecar")
        if state not in ("running", "stopped"):
            continue
        when = str(seen.get("board_checked_at") or "?")
        findings.append(Finding(
            "devices", f"devices.{device.device_id}.old_sidecar", Severity.INFO,
            f"Board {device.label}: the container {OLD_SIDECAR} from an earlier package with Docker images was still "
            f"there ({state}) at the last deploy ({when}). Nothing uses it any more; Mojo does not remove it.",
            f"If you do not need it, remove it on the board: docker rm -f {OLD_SIDECAR} (the data folder data/qdrant "
            "of that old package stays until you delete it).",
            where=device.address, why="A container nothing uses any more takes space on the board, and memory while "
                                      "it runs.", cannot="person"))
    return findings


def _from_records(store, device, key: str, name: str) -> Optional[Finding]:
    """The finding of one board from the records only (no network), or None when nothing points to a problem."""
    from orchestrator.runtime.device import reasons
    code = f"devices.{device.device_id}"
    if key == "changed":
        title, hint = _reason(device, reasons.HOST_KEY_CHANGED)
        return Finding("devices", code, Severity.WARN,
                       f"Board {device.label}: {title} ({reasons.HOST_KEY_CHANGED}), as the framework's known_hosts "
                       f"records it. {NO_CONTACT}", hint, where=device.address, why=BOARD_WHY, cannot="person",
                       board_check=device.device_id, board_name=name)
    if key == "missing":
        return Finding("devices", code, Severity.WARN,
                       f"Board {device.label} ({device.address}): the host key you trusted is no longer in the "
                       "framework's known_hosts.",
                       "Trust the board again: the Deployment window in the GUI, or 'llm-cli deploy run' or 'llm-cli "
                       "scan --device' in a terminal (only you compare the fingerprint).", where=device.address,
                       why=BOARD_WHY, cannot="person")
    problem = store.reach_problem(device)
    if problem is None:
        return None
    title, hint = _reason(device, problem["code"])
    # A board that was switched off is no fault of this computer: information; the other codes are warnings.
    severity = Severity.INFO if problem["code"] == reasons.HOST_DOWN else Severity.WARN
    return Finding("devices", code, severity,
                   f"Board {device.label}: {_recorded(problem)}: {title} {NO_CONTACT}", hint, where=device.address,
                   why=BOARD_WHY, cannot="" if severity == Severity.INFO else "person",
                   board_check=device.device_id, board_name=name)


def _checked_now(store, device, key: str, answer: Optional[str]) -> Finding:
    """The finding of one board checked on request: its host key (file) and one connection to its SSH port."""
    from orchestrator.runtime.device import reasons
    code = f"devices.{device.device_id}"
    problem = store.reach_problem(device)
    recorded = (f" Recorded: {_recorded(problem)}; that record stays until a deploy, verify or benchmark reaches the "
                "board again." if problem else "")
    if key == "changed":
        title, hint = _reason(device, reasons.HOST_KEY_CHANGED)
        return Finding("devices", code, Severity.WARN, f"Board {device.label}: {title} "
                       f"({reasons.HOST_KEY_CHANGED}).{recorded}", hint, where=device.address, why=BOARD_WHY,
                       cannot="person")
    if key == "missing":
        found = _from_records(store, device, key, "")
        found.message += recorded
        return found
    if answer is not None:
        title, hint = _reason(device, answer)
        severity = Severity.WARN if answer == reasons.PORT_CLOSED else Severity.INFO
        return Finding("devices", code, severity, f"Board {device.label}, checked now: {title} ({answer}).{recorded}",
                       hint, where=device.address, why=BOARD_WHY, cannot="person" if severity == Severity.WARN else "")
    if problem and problem["code"] == reasons.AUTH_FAILED:
        title, hint = _reason(device, reasons.AUTH_FAILED)
        return Finding("devices", code, Severity.WARN,
                       f"Board {device.label}, checked now: it answers on its SSH port and its host key is the one "
                       f"you trusted, but {_recorded(problem)}: {title} Mojo does not log in, so the next deploy or "
                       "benchmark shows whether the login works.", hint, where=device.address, why=BOARD_WHY,
                       cannot="person")
    return _ok("devices", code, f"Board {device.label} ({device.address}), checked now: it answers on its SSH port; "
                                f"its host key is the one you trusted.{recorded}")


def check_devices(framework, contact: Sequence[str] = ()) -> List[Finding]:
    """The trusted boards as the framework recorded them (host keys, failed contacts, the last deploy's look at the
    board): no network contact. `contact`: the device IDs of the boards the person or an AI asked to check now (one
    TCP connection to each one's SSH port). Changes nothing, logs in nowhere."""
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device.trusted import TrustedDevices, board_name
    from orchestrator.utils import ssh_trust
    state_dir = getattr(getattr(framework, "config", None), "user_state_dir", None)
    if state_dir is None:
        return []
    store = TrustedDevices(Path(state_dir))
    boards = sorted(store.list(), key=lambda d: d.trusted_at, reverse=True)
    if not boards:
        return []
    findings: List[Finding] = []
    keys_known = ssh_trust.paramiko is not None
    if not keys_known:
        findings.append(Finding("devices", "devices.unchecked", Severity.INFO,
                                f"The host keys of {len(boards)} trusted boards were not read: reading them needs the "
                                "'paramiko' package."))
    wanted = [d for d in boards if d.device_id in set(contact)][:MAX_CONTACT]
    answers: Dict[str, Optional[str]] = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            answers = dict(zip((d.device_id for d in wanted),
                               pool.map(lambda d: reasons.check_port(d.host, d.port), wanted)))
    quiet = 0
    for device in boards:
        status = store.key_status(device) if keys_known else "known"
        key = {"known": "known", "changed": "changed"}.get(status, "missing")
        if device.device_id in answers:
            findings.append(_checked_now(store, device, key, answers[device.device_id]))
            continue
        found = _from_records(store, device, key, board_name(store, device))
        if found is None:
            quiet += 1
        else:
            findings.append(found)
    if quiet:
        findings.append(_ok("devices", "devices.recorded",
                            f"{quiet} trusted board{'' if quiet == 1 else 's'}: no recorded problem (the host key is "
                            "the one you trusted, and no deploy, verify or benchmark failed to reach "
                            f"{'it' if quiet == 1 else 'them'}). Mojo contacts a board only when you ask it to "
                            "check that one."))
    return findings + _old_sidecars(store, boards)
