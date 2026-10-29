#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo closes the circle: the crystal ball after Mojo Miracle (DECISIONS round 27)

When Mojo Miracle ends after Mojo Oracle at a terminal, the console shows Mojo's crystal ball and one line under it:
MANAGED_LINE when Mojo Miracle repaired at least one problem, left none and the check afterwards found nothing new;
NOT_FOUND_LINE when there was nothing to repair (no problem, or every one already gone) and nothing new. Anything else
(a problem left, one the person declined, a new one) shows no logo. "After Mojo Oracle" is the Oracle run's "Run Mojo
Miracle now?" answered yes, or `llmcf mojo --miracle` while an earlier Oracle run at a terminal is still open (the
record below). Only the CLI shows it, at a terminal: never with --json, never through an AI tool, not in the GUI.

- ART and MASK: the drawing (pure ASCII, 60 columns, so it fits an 80-column terminal) and its colour mask of the
  same shape, one letter per character of the drawing (STYLES; a space has none). The drawing is swapped here, in one
  place; a test checks that both have the same shape.
- banner_lines(line, styled=...): the lines to print, plain or as (text, style) runs. The CLI makes rich Text of the
  runs (rich lives in the CLI only) and prints them through its console, which shows colour only where it can.
- circle_line(result): the line a MiracleReport earns, or None for no logo.
- record_oracle / oracle_open / close_oracle: the open Oracle run, one small private file next to Mojo Miracle's
  lock in the per-user state folder (repairs/engine.lock_path), written with utils/atomic.write_private. It holds no
  path and no account name and is never shown to an AI; a Miracle run at a terminal removes it when it ends, whatever
  its outcome. Neither of these raises: the greeting never stops Mojo.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Union

from orchestrator.utils.atomic import remove_file, write_private

logger = logging.getLogger(__name__)

ART = r"""
      |                                            *
    --*--              _.--~~~~~~~~~--._                |
      |            _.-'                 '-._          --@--
                .-' *####                   '-.         |
              .' @@#                    -*-    '.
  **         / @#         \    |    /            \       * *
            / ##           \   |   /          ,-  \       |
           / #          .-~~'~~~~~'~~-.         '  \
          | #        .-'    .-----.    '-.          |   .
          |         (*    .'  ___  '.    *)         |
  |      (         (      (  (@@@)  )      )         )
-+-      (          (*    '.  ~~~  .'    *)          )
  |       |          '-.    '-----'    .-'          |
          | .           '-~~.~~~~~.~~-'       #     |
    .      \ \            /    |    \        ##    /
            \ *.         /     |     \     @#     /
             \                           ##      /        |
     |        '.                       #*      .'       -+-
   -@-          '-.                         .-'           |
     |             '-._                 _.-'
                 ]#***._________________.***#[
                /*#@#######################@#*\      *
     *       ('*----_______________________----*')
               **----_____________________----**
"""[1:-1]

# s sparkle, b ball outline, h shading and glint, e eye, i pupil, r rays, p stand
MASK = r"""
      s                                            s
    sssss              bbbbbbbbbbbbbbbbb                s
      s            bbbb                 bbbb          sssss
                bbb hhhhh                   bbb         s
              bb hhh                    sss    bb
  ss         b hh         r    r    r            b       s s
            b hh           r   r   r          hh  b       s
           b h          eeeeeeeeeeeeeee         h  b
          b h        eee    eeeeeee    eee          b   s
          b         ee    ee  eee  ee    ee         b
  s      b         e      e  iiiii  e      e         b
sss      b          ee    ee  eee  ee    ee          b
  s       b          eee    eeeeeee    eee          b
          b h           eeeeeeeeeeeeeee       h     b
    s      b h            r    r    r        hh    b
            b hh         r     r     r     hh     b
             b                           hh      b        s
     s        bb                       hh      bb       sss
   sss          bbb                         bbb           s
     s             bbbb                 bbbb
                 ppppppppppppppppppppppppppppp
                ppppppppppppppppppppppppppppppp      s
     s       ppppppppppppppppppppppppppppppppppppp
               ppppppppppppppppppppppppppppppppp
"""[1:-1]

# rich style names (the CLI's console turns them into colour where the console can show colour)
STYLES = {"s": "bold bright_yellow", "b": "bright_cyan", "h": "magenta", "e": "bright_green", "i": "bold bright_white",
          "r": "yellow", "p": "bright_blue"}
LINE_STYLE = "bold bright_green"

MANAGED_LINE = "[Mojo] Miracle managed. The oracle is pleased."
NOT_FOUND_LINE = "[Mojo] 404 - miracle not found"

ART_LINES: Tuple[str, ...] = tuple(ART.split("\n"))
MASK_LINES: Tuple[str, ...] = tuple(MASK.split("\n"))
WIDTH = max(len(line) for line in ART_LINES)

Run = Tuple[str, str]   # (text, rich style; "" for none)


def _indent(line: str) -> str:
    return " " * max(0, (WIDTH - len(line)) // 2)


def centred(line: str) -> str:
    """`line` centred under the drawing (spaces before it, none after)."""
    return _indent(line) + line


def _runs(art: str, mask: str) -> Tuple[Run, ...]:
    """One line of the drawing as runs of characters that share a mask letter, each with its style."""
    runs: List[Run] = []
    for char, letter in zip(art, mask.ljust(len(art))):
        style = STYLES.get(letter, "") if char != " " else ""
        if runs and runs[-1][1] == style:
            runs[-1] = (runs[-1][0] + char, style)
        else:
            runs.append((char, style))
    return tuple(runs)


def banner_lines(line: str, *, styled: bool = False) -> Union[List[str], List[Tuple[Run, ...]]]:
    """The drawing, one empty line and `line` centred under it. Plain: one string per line. styled=True: each line as
    a tuple of (text, style) runs (style "" for none; the line in LINE_STYLE), for the CLI to print as rich Text."""
    if not styled:
        return [*ART_LINES, "", centred(line)]
    pad = _indent(line)
    last: Tuple[Run, ...] = ((pad, ""), (line, LINE_STYLE)) if pad else ((line, LINE_STYLE),)
    return [*(_runs(art, mask) for art, mask in zip(ART_LINES, MASK_LINES)), (), last]


def circle_line(result) -> Optional[str]:
    """The line a MiracleReport earns (round 27): MANAGED_LINE when at least one problem was repaired, none is left not
    repaired and nothing new was found; NOT_FOUND_LINE when there was nothing to repair (no problem, or every one
    already gone) and nothing new; None (no logo) for anything else."""
    if result.new_problems or result.not_repaired:
        return None
    return MANAGED_LINE if result.repaired else NOT_FOUND_LINE


# --- the open Oracle run -----------------------------------------------------------------------------------------------

RECORD_NAME = "oracle_open.json"


def record_path(framework) -> Path:
    """Where the open Oracle run is noted: next to Mojo Miracle's lock in the per-user state folder (of this
    LLM_FRAMEWORK_HOME)."""
    from orchestrator.Core.mojo.repairs.engine import lock_path
    return lock_path(framework).with_name(RECORD_NAME)


def record_oracle(framework) -> None:
    """Notes that Mojo Oracle ran for the person at a terminal (the CLI); a later `llmcf mojo --miracle` at a terminal
    closes the circle. Only "open", "cli" and the time: no path, no account name."""
    try:
        write_private(record_path(framework), json.dumps(
            {"mojo_oracle": "open", "via": "cli",
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    except (OSError, ValueError) as e:   # e.g. a read-only state folder: no greeting later, Mojo itself unchanged
        logger.debug("The open Mojo Oracle run could not be noted: %s", type(e).__name__)


def oracle_open(framework) -> bool:
    """Whether an Oracle run of the person at a terminal is still open (noted and not closed by a Miracle run)."""
    try:
        data = json.loads(record_path(framework).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("mojo_oracle") == "open"


def close_oracle(framework) -> None:
    """Removes the note of the open Oracle run (a Miracle run at a terminal ended, whatever its outcome)."""
    try:
        remove_file(record_path(framework))
    except OSError as e:
        logger.debug("The note of the open Mojo Oracle run could not be removed: %s", type(e).__name__)


__all__ = ["ART", "ART_LINES", "LINE_STYLE", "MANAGED_LINE", "MASK", "MASK_LINES", "NOT_FOUND_LINE", "RECORD_NAME",
           "STYLES", "WIDTH", "banner_lines", "centred", "circle_line", "close_oracle", "oracle_open",
           "record_oracle", "record_path"]
