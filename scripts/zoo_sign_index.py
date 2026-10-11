#!/usr/bin/env python3
"""
Maintainer tool for the community repository: index entries for reviewed packs, and the signature of index.json.
It is not part of the framework's product code. The private signing key lives only in the OS keyring.

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
import base64
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from orchestrator.Core.community_manager import CommunityIndex, CommunityManager, IndexEntry  # noqa: E402
from orchestrator.Core.community_pack import PackError, key_id, sign_index, verify_index  # noqa: E402

SERVICE = "lcf-community-index"


def _keyring():
    import keyring
    return keyring


def keygen(name: str) -> int:
    kr = _keyring()
    if kr.get_password(SERVICE, name):
        print(f"A key named {name!r} exists already; choose another name (keys are never replaced).", file=sys.stderr)
        return 1
    key = Ed25519PrivateKey.generate()
    kr.set_password(SERVICE, name, base64.b64encode(key.private_bytes_raw()).decode("ascii"))
    public = key.public_key().public_bytes_raw()
    print("# Add to configs/community_keys.yml (keys:) and ship it with a release:")
    print(f"  - key_id: {key_id(key.public_key())}")
    print(f"    pubkey_b64: {base64.b64encode(public).decode('ascii')}")
    print(f"    valid_until: {date.today() + timedelta(days=730)}")
    print(f"    name: {name}")
    return 0


def entry(pack: Path, url: str, trust: str, title: str) -> int:
    manifest, problems = CommunityManager(None).verify_file(pack)
    if problems:
        print("The pack is not acceptable:\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    data = pack.read_bytes()
    item = IndexEntry(kind=manifest.kind, id=manifest.id, version=manifest.version, title=title or manifest.id,
                      author=manifest.author, license=manifest.license, trust=trust, url=url, size=len(data),
                      sha256=hashlib.sha256(data).hexdigest())
    print(item.model_dump_json(indent=2))
    return 0


def sign(index_path: Path, name: str, previous: Path = None) -> int:
    data = index_path.read_bytes()
    index = CommunityIndex.model_validate_json(data)
    if previous is not None:
        old = CommunityIndex.model_validate_json(previous.read_bytes())
        if index.version <= old.version:
            print(f"The version must grow: {index.version} <= {old.version}.", file=sys.stderr)
            return 1
    stored = _keyring().get_password(SERVICE, name)
    if not stored:
        print(f"No key named {name!r} in the keyring (service {SERVICE}).", file=sys.stderr)
        return 1
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(stored))
    signature = sign_index(data, key)
    verify_index(data, signature, [key.public_key()])  # self-check before anything is written
    sig_path = index_path.with_name(index_path.name + ".sig")
    sig_path.write_text(signature.model_dump_json(), encoding="utf-8")
    print(f"Signed index version {index.version} ({len(index.entries)} entries, {len(index.revoked)} revoked) "
          f"with key {signature.key_id}: {sig_path}")
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
