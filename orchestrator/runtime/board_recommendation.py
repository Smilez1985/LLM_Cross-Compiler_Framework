#!/usr/bin/env python3
"""
The board a hardware probe points to, as a recommendation only (DECISIONS round 8, N9, N9', N9'').

The board comes from the probe: the probe's SoC named like one of the target's boards (source "probe"), or the
probe's board name found in the target's list of known boards (`examples` in target.yml capabilities, or
`typical_boards`; source "list"). Only an exact match is "sure". Anything weaker is "unsure": then nothing is
preselected, the candidates are shown with why it is unsure, and the person picks. The person can always change the
board (they know which board is in their hand), and a board the person chose is never overwritten by a later
detection or by an AI (the builder checks that before it applies a recommendation).

An online look-up of the board name happens only with the person's consent (the GUI opens their web browser after
asking), and Ditto may suggest a board through the GUI tools, which marks it as Ditto's. Ditto probing the board itself
over SSH needs the later tool probe_remote_device; today the person runs the remote scan in the Builder.

Probe values are untrusted: the board name is free text for display, compared here only for an exact match against a
fixed list, and the result is never more than a recommendation the person sees.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

SURE, UNSURE, NONE = "sure", "unsure", "none"


def _canon(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


@dataclass(frozen=True)
class BoardCandidate:
    board: str          # the board id the build form takes
    label: str          # its label in target.yml
    why: str            # English: why it is a candidate


@dataclass(frozen=True)
class BoardRecommendation:
    """`board` is set only when the match is sure; `candidates` lists what the person picks from when it is not.
    `source`: probe (the probe's SoC), list (the probe's board name in the target's board list) or "" (none)."""
    target: str
    board: Optional[str]
    source: str
    confidence: str
    candidates: Tuple[BoardCandidate, ...] = ()
    reason: str = ""
    soc: str = ""
    board_model: str = ""

    @property
    def sure(self) -> bool:
        return self.confidence == SURE and bool(self.board)


def _boards(target_manager: Any, target: str) -> Dict[str, Tuple[str, List[str]]]:
    """board id -> (label, known board names) of a target, from its capabilities or its supported_boards."""
    found: Dict[str, Tuple[str, List[str]]] = {}
    try:
        caps = target_manager.get_capabilities(target) if hasattr(target_manager, "get_capabilities") else None
    except Exception:  # noqa: BLE001 - a target without capabilities
        caps = None
    if caps is not None and getattr(caps, "boards", None):
        for board_id, spec in caps.boards.items():
            found[str(board_id)] = (str(getattr(spec, "label", "") or board_id),
                                    [str(e) for e in (getattr(spec, "examples", None) or [])])
        return found
    try:
        data = target_manager.get_target(target) or {}
    except Exception:  # noqa: BLE001
        data = {}
    for entry in data.get("supported_boards") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        specs = entry.get("specifications") if isinstance(entry.get("specifications"), dict) else {}
        names = specs.get("typical_boards") if isinstance(specs.get("typical_boards"), list) else []
        found[str(entry["name"])] = (str(entry.get("description") or entry["name"]), [str(n) for n in names])
    return found


def recommend_board(profile: Any, target_manager: Any, target: str) -> BoardRecommendation:
    """The board of `target` the probe `profile` points to (see the module docstring)."""
    get = getattr(profile, "get", None)
    soc = str(get("SOC_MODEL") or "") if callable(get) else ""
    board_model = str(get("BOARD_MODEL") or "") if callable(get) else ""
    boards = _boards(target_manager, target) if target_manager is not None and target else {}
    if not boards:
        return BoardRecommendation(target, None, "", NONE, reason="the target lists no boards", soc=soc,
                                   board_model=board_model)
    by_soc = [b for b in boards if soc and _canon(b) == _canon(soc)]
    if len(by_soc) == 1:
        board = by_soc[0]
        return BoardRecommendation(target, board, "probe", SURE,
                                   (BoardCandidate(board, boards[board][0], f"the probe's SoC is {soc}"),),
                                   f"the probe's SoC {soc} is the board {board} of this target", soc, board_model)
    name = _canon(board_model)
    exact = [b for b, (_label, names) in boards.items() if name and any(_canon(n) == name for n in names)]
    if len(exact) == 1:
        board = exact[0]
        return BoardRecommendation(target, board, "list", SURE,
                                   (BoardCandidate(board, boards[board][0],
                                                   f"'{board_model}' is in this board's list of known boards"),),
                                   f"the probe's board name is in the known board list of {board}", soc,
                                   board_model)
    partial = [b for b, (_label, names) in boards.items()
               if name and any(_canon(n) and (_canon(n) in name or name in _canon(n)) for n in names)]
    if exact or partial:
        chosen = exact or partial
        why = ("the probe's board name matches more than one board of this target" if len(chosen) > 1 else
               "the probe's board name only partly matches a known board")
        candidates = tuple(BoardCandidate(b, boards[b][0], why) for b in chosen)
        return BoardRecommendation(target, None, "list", UNSURE, candidates, why, soc, board_model)
    why = ("the probe names no SoC this target knows" if soc else "the probe names no SoC") + \
          (" and its board name is not in the target's list" if board_model else "")
    candidates = tuple(BoardCandidate(b, label, why) for b, (label, _names) in boards.items())
    return BoardRecommendation(target, None, "", UNSURE, candidates, why, soc, board_model)


__all__ = ["BoardCandidate", "BoardRecommendation", "NONE", "SURE", "UNSURE", "recommend_board"]
