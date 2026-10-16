#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the boards the person trusted (eighth round N6)

For every trusted board: whether the framework's known_hosts still holds the host key the person confirmed (read
from the file, no network), and one TCP connection to its SSH port (runtime/device/reasons.check_port: no login, no
password, no key, no ICMP). A board that cannot be used is named with its reason code (device_host_down,
device_port_closed, device_host_key_changed) and the next step. Mojo never logs in to a board, so a wrong password or
a framework key that is gone (device_auth_failed) shows at the next deploy or benchmark only. The device account is
never named.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

MAX_BOARDS = 16   # boards checked in one run, the most recently trusted first
BOARD_WHY = ("Deploys, benchmarks on the board and the AI's board tools reach a board only on its SSH port and with "
             "the host key you trusted.")


def _reason(device, code: str) -> Tuple[str, str]:
    """The finding's message and fix hint for a reason code: what happened and the next step (address and port, never
    the account)."""
    from orchestrator.runtime.device import reasons
    title = reasons.person_title(code, "en", address=device.host, port=device.port)
    return (f"Board {device.label}: {title} ({code})",
            reasons.person_next(code, "en", address=device.host, port=device.port))


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


def check_devices(framework) -> List[Finding]:
    """The trusted boards: host key still trusted, SSH port answering. Changes nothing, logs in nowhere."""
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    state_dir = getattr(getattr(framework, "config", None), "user_state_dir", None)
    if state_dir is None:
        return []
    store = TrustedDevices(Path(state_dir))
    boards = sorted(store.list(), key=lambda d: d.trusted_at, reverse=True)
    if not boards:
        return []
    if ssh_trust.paramiko is None:
        return [Finding("devices", "devices.unchecked", Severity.INFO,
                        f"{len(boards)} trusted boards were not checked: checking a board needs the 'paramiko' "
                        "package.")]
    checked = boards[:MAX_BOARDS]
    with ThreadPoolExecutor(max_workers=min(8, len(checked))) as pool:
        answers = list(pool.map(lambda d: reasons.check_port(d.host, d.port), checked))
    findings: List[Finding] = []
    for device, answer in zip(checked, answers):
        code = f"devices.{device.device_id}"
        key = store.key_status(device)
        if key == "changed":
            message, hint = _reason(device, reasons.HOST_KEY_CHANGED)
            findings.append(Finding("devices", code, Severity.WARN, message, hint, where=device.address,
                                    why=BOARD_WHY, cannot="person"))
        elif key != "known":
            findings.append(Finding("devices", code, Severity.WARN,
                                    f"Board {device.label} ({device.address}): the host key you trusted is no longer "
                                    "in the framework's known_hosts.",
                                    "Trust the board again: the Deployment window in the GUI, or 'llm-cli deploy "
                                    "run' or 'llm-cli scan --device' in a terminal (only you compare the "
                                    "fingerprint).", where=device.address, why=BOARD_WHY, cannot="person"))
        elif answer == reasons.PORT_CLOSED:
            message, hint = _reason(device, answer)
            findings.append(Finding("devices", code, Severity.WARN, message, hint, where=device.address,
                                    why=BOARD_WHY, cannot="person"))
        elif answer is not None:
            # A board that is switched off is no fault of this computer: information, with the next step.
            message, hint = _reason(device, answer)
            findings.append(Finding("devices", code, Severity.INFO, message, hint, where=device.address,
                                    why=BOARD_WHY))
        else:
            findings.append(_ok("devices", code, f"Board {device.label} ({device.address}) answers on its SSH port; "
                                                 "its host key is the one you trusted."))
    findings += _old_sidecars(store, checked)
    if len(boards) > MAX_BOARDS:
        findings.append(Finding("devices", "devices.more", Severity.INFO,
                                f"{len(boards) - MAX_BOARDS} older trusted boards were not checked (Mojo checks the "
                                f"{MAX_BOARDS} most recently trusted)."))
    return findings
