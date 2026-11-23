#!/usr/bin/env python3
"""
Every pinned version of the repository, offline (orchestrator/runtime/build/versions.py, ROADMAP "Step 7").

    python scripts/versions_report.py           # the list, grouped by kind
    python scripts/versions_report.py --json    # the same as JSON

Each line names the file, the place, the version and whether it is pinned exactly (a digest, a full commit SHA, an exact
version). A Debian or Ubuntu base image that is older than the newest base of the same distribution in
configs/llama_runtime.yml (build_bases) says so. Nothing is fetched: whether a newer release exists upstream is a later
step (ROADMAP "Step 7"). Python's dependencies are kept by scripts/fresh_deps.py and are not listed here.
Exit code: 0 (a report, not a check).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.runtime.build.versions import KINDS, inventory  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="List every pinned version of the repository (offline).")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    places = inventory()
    if args.json:
        print(json.dumps([p.to_dict() for p in places], indent=2))
        return 0
    for kind in KINDS:
        group = [p for p in places if p.kind == kind]
        if not group:
            continue
        print(f"{kind} ({len(group)})")
        for p in group:
            mark = "pinned" if p.pinned else "UNPINNED"
            extra = []
            if p.newer_in_catalogue:
                extra.append(f"the catalogue has {p.newer_in_catalogue}")
            if p.note:
                extra.append(p.note)
            tail = f"  ({'; '.join(extra)})" if extra else ""
            print(f"  {mark:8} {p.path}: {p.what} = {p.version or '-'}{tail}")
    behind = sum(1 for p in places if p.newer_in_catalogue)
    unpinned = sum(1 for p in places if not p.pinned and p.version)
    print(f"{len(places)} places; {unpinned} not pinned exactly; {behind} older than a base of the catalogue. "
          "Offline: upstream releases are not checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
