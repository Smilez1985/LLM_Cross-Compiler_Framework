#!/usr/bin/env python3
"""
The CI's dead-code step (DESIGN, No dead code and no second system; the second recon, decided by the maintainer on
2026-11-27): vulture over `orchestrator` and `scripts` (the tests are no caller), every unused function, method, class
and property it reports at 60 % confidence or more, minus those a decorator registers (Click commands, pydantic
validators, atexit, property setters), must stand in configs/vulture_allowlist.py with its reason. A new one fails the
step, and so does an entry that is no longer reported (wired or gone: the list keeps no stale name).

    python scripts/dead_code.py            exit 0 when the list and the code agree, else 1 with what to do
"""

import importlib.util
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
PATHS = ("orchestrator", "scripts")
KINDS = ("function", "method", "class", "property")
MIN_CONFIDENCE = 60
# a decorator that registers the function, so nothing calls it by name in this code
REGISTERED = re.compile(r"^@(\w+\.(command|group|setter|deleter)|field_validator|model_validator|atexit\.register)\b")


def allowed() -> Dict[str, str]:
    spec = importlib.util.spec_from_file_location("vulture_allowlist", ROOT / "configs" / "vulture_allowlist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.ALLOWED)


def reported() -> Set[str]:
    """`path::name` of every unused function, method, class and property vulture reports, relative to the root."""
    from vulture import Vulture
    v = Vulture()
    v.scavenge([str(ROOT / p) for p in PATHS])
    found = set()
    for item in v.get_unused_code(min_confidence=MIN_CONFIDENCE):
        if item.typ not in KINDS:
            continue
        path = Path(item.filename).resolve()
        lines = path.read_text(encoding="utf-8").split("\n")
        if REGISTERED.match(lines[item.first_lineno - 1].strip()):
            continue
        found.add(f"{path.relative_to(ROOT).as_posix()}::{item.name}")
    return found


def compare() -> Tuple[List[str], List[str]]:
    """(new: reported but not listed, stale: listed but no longer reported)."""
    listed, found = allowed(), reported()
    return sorted(found - set(listed)), sorted(set(listed) - found)


def main() -> int:
    new, stale = compare()
    for entry in new:
        print(f"::error::{entry} is unused (nothing in orchestrator or scripts calls it): wire it where the design "
              "puts it, or put it in configs/vulture_allowlist.py with its reason (the maintainer decides per case)")
    for entry in stale:
        print(f"::error::{entry} stands in configs/vulture_allowlist.py but is no longer unused: take it out")
    if new or stale:
        return 1
    print(f"No new dead code ({len(allowed())} listed exceptions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
