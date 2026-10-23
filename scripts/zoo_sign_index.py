#!/usr/bin/env python3
"""
Maintainer tool for the community repository: the index signing key, index entries for reviewed packs, and the
signature of index.json, by hand. The same steps run as a few clicks in the framework (Tools > Open Community Hub >
Maintainer, or `llmcf zoo maintainer key|review|revoke`); this script is a thin wrapper over the same code
(orchestrator/Core/community_maintainer.py). The private signing key lives only in the OS keyring.

    python scripts/zoo_sign_index.py keygen NAME
        new Ed25519 key in the keyring (service "lcf-community-index"); prints the entry for
        configs/community_keys.yml (ship it with a framework release before the first index it signs)
    python scripts/zoo_sign_index.py entry PACK.zip --url HTTPS-URL [--trust community|verified] [--title T]
        checks the pack (format, hashes, validators, privacy scrub) and prints its index entry as JSON
    python scripts/zoo_sign_index.py sign index.json --key NAME [--previous OLD-index.json]
        checks index.json against the schema (the version must be higher than --previous) and writes
        index.json.sig next to it

The signature covers the exact bytes of index.json (with the framework's domain prefix), so publish the file
unchanged. Clients refuse an index whose version is lower than the one they have cached. To revoke a pack, add it
to "revoked" (by sha256, or kind + id and optionally version, with a reason), raise the version and sign again.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.Core import community_maintainer as maintainer  # noqa: E402
from orchestrator.Core.community_pack import PackError  # noqa: E402

SERVICE = maintainer.SERVICE


def keygen(name: str) -> int:
    entry = maintainer.create_signing_key(name)
    print(entry.text(), end="")
    return 0


def entry(pack: Path, url: str, trust: str, title: str) -> int:
    print(maintainer.make_entry(pack, url, trust=trust, title=title).model_dump_json(indent=2))
    return 0


def sign(index_path: Path, name: str, previous: Path = None) -> int:
    index, signer, sig_path = maintainer.sign_index_file(index_path, name, previous)
    print(f"Signed index version {index.version} ({len(index.entries)} entries, {len(index.revoked)} revoked) "
          f"with key {signer}: {sig_path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("name")
    e = sub.add_parser("entry")
    e.add_argument("pack", type=Path)
    e.add_argument("--url", required=True)
    e.add_argument("--trust", choices=["community", "verified"], default="community")
    e.add_argument("--title", default="")
    s = sub.add_parser("sign")
    s.add_argument("index", type=Path)
    s.add_argument("--key", required=True)
    s.add_argument("--previous", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.command == "keygen":
            return keygen(args.name)
        if args.command == "entry":
            return entry(args.pack, args.url, args.trust, args.title)
        return sign(args.index, args.key, args.previous)
    except (PackError, ValueError, OSError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
