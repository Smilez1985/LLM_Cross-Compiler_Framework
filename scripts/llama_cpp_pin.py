#!/usr/bin/env python3
"""
Checks the llama.cpp pin (configs/llama_runtime.yml, llama_cpp.source) for a pin bump.

    python scripts/llama_cpp_pin.py check              # upstream's tag points at the pinned commit; pin places agree
    python scripts/llama_cpp_pin.py check --local      # only the pin places (no network)
    python scripts/llama_cpp_pin.py show b11500        # the commit upstream's tag b11500 points at (for a bump)

`check` runs `git ls-remote <repo> refs/tags/<tag>`: GitHub serves commits pushed to any fork through the upstream
URL, so the commit alone fixes the content but not its origin; the tag on upstream does. It then lists every place
that names the llama.cpp version (orchestrator/runtime/build/pins.py): derived and reference places must agree,
legacy places are only reported. Exit code: 0 all good, 1 a mismatch, 2 usage or network error. In offline mode (the
framework's settings) the repository is reached only in the own network, as a mirror is (upstream_tag_commit), so the
upstream check stops with the plain refusal; `check --local` needs no network.
The procedure around it is in CONTRIBUTING.md ("Bumping the llama.cpp pin").
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.runtime.build import pins  # noqa: E402
from orchestrator.runtime.build.source import SourceError, check_pin_upstream, upstream_tag_commit  # noqa: E402
from orchestrator.runtime.catalog import load_catalog  # noqa: E402


def _places(source) -> int:
    places = pins.pin_places(source)
    bad = pins.mismatches(places)
    for p in places:
        mark = "ok" if p.ok else ("legacy" if p.kind == "legacy" else "MISMATCH")
        print(f"  {mark:8} {p.kind:9} {p.path}: {p.what} = {p.found} (pin {p.expected})")
    return 1 if bad else 0


def _settings():
    """The framework's settings (offline mode and the person's opt-outs), None when they cannot be read."""
    try:
        from orchestrator.Core.config_manager import ConfigManager
        return ConfigManager()
    except Exception:  # noqa: BLE001 - no settings: online, as before
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the llama.cpp pin for a pin bump.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="upstream tag = pinned commit, and every pin place agrees")
    check.add_argument("--local", action="store_true", help="skip the upstream check (no network)")
    show = sub.add_parser("show", help="the commit an upstream tag points at")
    show.add_argument("tag", help="for example b11500")
    args = parser.parse_args(argv)

    source = load_catalog().llama_cpp.source
    if source is None:
        print("configs/llama_runtime.yml has no llama_cpp.source", file=sys.stderr)
        return 2
    try:
        if args.command == "show":
            commit = upstream_tag_commit(args.tag, repo=source.repo, settings=_settings())
            print(f"{args.tag} = {commit}")
            print("Put it into configs/llama_runtime.yml: llama_cpp.source.commit, .tag and .build_number, and "
                  "llama_cpp.build; then run 'check'.")
            return 0
        print(f"Pin: {source.tag} = {source.commit} ({source.repo})")
        if not args.local:
            check_pin_upstream(source, settings=_settings())
            print(f"  ok       upstream  refs/tags/{source.tag} points at the pinned commit")
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1 if "points at" in str(e) else 2
    return _places(source)


if __name__ == "__main__":
    sys.exit(main())
