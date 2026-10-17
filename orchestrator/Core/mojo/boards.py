#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo and the person's boards (W1 and W1' of 2026-10-16)

A default Mojo Oracle run contacts no board (checks/devices.py). This module is what the person's command
(`llmcf mojo`, `llmcf mojo --oracle --board NAME`) and the GUI (Tools > Mojo Health Check..., the Deployment tab's
"Check this board") share, so both offer, find and check a board the same way:

- boards_to_offer: the boards a recorded fact points to (Finding.board_check), once each, by the label the person gave
  them, with the name `--board` takes for each;
- find_board: the one trusted board a name means (its label, device ID or address), or the boards to choose from;
- check_board: one board checked now (one TCP connection to its SSH port, no login, no password, no key) and its
  finding (`devices.<device_id>`).

An AI names a board by its label or device ID only (tools_mojo.run_mojo_oracle); nothing here goes to an AI.
"""

from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

from orchestrator.Core.mojo.findings import Finding, MojoReport


class BoardOffer(NamedTuple):
    """A board a recorded fact points to and that Mojo did not contact."""
    device_id: str
    label: str   # the person's name for it (the trusted board's label)
    name: str    # the name `llmcf mojo --oracle --board` takes for it (Finding.board_name: the label or device ID)


@dataclass(frozen=True)
class BoardLookup:
    """What a board name means: the one board (`device_id`), or why not (`problem`) with the boards to choose from
    instead (`boards`: those that match, or every trusted board when none does)."""
    device_id: str
    problem: str
    boards: Tuple[object, ...]   # runtime.device.trusted.TrustedDevice


def trusted_boards(framework) -> List[object]:
    """The trusted boards of this user (label, device ID, address), by label; [] when none can be read."""
    from orchestrator.runtime.device.trusted import TrustedDevices
    try:
        return TrustedDevices.for_framework(framework).list()
    except Exception:  # noqa: BLE001 - a store that cannot be read: no board to offer
        return []


def board_labels(framework) -> Dict[str, str]:
    """The label of each trusted board by its device ID."""
    return {d.device_id: d.label for d in trusted_boards(framework)}


def boards_to_offer(framework, report: MojoReport) -> List[BoardOffer]:
    """The boards the findings of `report` point to (Finding.board_check), each once and in the report's order, with
    the person's label (the finding's board name when the board is no longer listed)."""
    labels = board_labels(framework)
    boards: Dict[str, BoardOffer] = {}
    for finding in report.findings:
        if finding.board_check and finding.board_check not in boards:
            name = finding.board_name or finding.board_check
            boards[finding.board_check] = BoardOffer(finding.board_check, labels.get(finding.board_check) or name,
                                                     name)
    return list(boards.values())


def find_board(framework, name: str) -> BoardLookup:
    """The trusted board the person named by its label, device ID or address (find_boards with by_address); when the
    name means none or several, why, with the boards to choose from."""
    from orchestrator.runtime.device.trusted import TrustedDevices, find_boards
    store = TrustedDevices.for_framework(framework)
    found = find_boards(store, name, by_address=True)
    if len(found) == 1:
        return BoardLookup(found[0].device_id, "", tuple(found))
    problem = (f"{len(found)} trusted boards match '{name}'" if found else f"No trusted board is named '{name}'")
    return BoardLookup("", problem, tuple(found or store.list()))


def check_board(framework, device_id: str) -> Optional[Finding]:
    """Checks one trusted board now: one TCP connection to its SSH port (no login) and its host key in the framework's
    known_hosts. Returns its finding (`devices.<device_id>`), or None when no trusted board has this device ID.
    Changes nothing; may take a few seconds (run it off the GUI thread)."""
    from orchestrator.Core.mojo.checks.devices import check_devices
    code = f"devices.{device_id}"
    return next((f for f in check_devices(framework, contact=(device_id,)) if f.code == code), None)


__all__ = ["BoardLookup", "BoardOffer", "board_labels", "boards_to_offer", "check_board", "find_board",
           "trusted_boards"]
