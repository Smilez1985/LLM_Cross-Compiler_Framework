#!/usr/bin/env python3
"""
Compares the pinned values of configs/project_sources.yml with configs/detailed_project_sources.yml and copies
them over without rewriting the detailed file.

    python scripts/sync_ssot.py            # list the differences (exit code 1 if there are any)
    python scripts/sync_ssot.py --write    # put the values of project_sources.yml into the detailed file

Synced: `commit`, `version`, `pypi_package` and `docker_tag` at every depth, and metadata.ssot_version and
metadata.last_updated (the latter as metadata.last_synced in the detailed file). Only values that exist in both
files are changed, and only their text: comments, order, quoting style and layout stay as they are. Entries the
detailed file lacks are listed, never added. Works from any folder (--configs selects another folder).
Exit codes: 0 in sync (or written), 1 differences found, 2 a file is missing or not valid YAML.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator, List, Optional, Sequence, Tuple

import yaml

CONFIGS = Path(__file__).resolve().parent.parent / "configs"
PRODUCTION = "project_sources.yml"
DETAILED = "detailed_project_sources.yml"
SYNC_KEYS = ("commit", "version", "pypi_package", "docker_tag")
METADATA = (("ssot_version", "ssot_version"), ("last_updated", "last_synced"))

Path_ = Tuple[str, ...]


def synced_values(prod: Any) -> Iterator[Tuple[Path_, Path_, Any]]:
    """(path in project_sources.yml, path in the detailed file, value) of everything that is synced."""
    def walk(node: Any, path: Path_) -> Iterator[Tuple[Path_, Path_, Any]]:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            here = path + (str(key),)
            if isinstance(value, dict):
                yield from walk(value, here)
            elif str(key) in SYNC_KEYS and isinstance(value, (str, int, float)) and not isinstance(value, bool):
                yield here, here, value
    meta = prod.get("metadata") if isinstance(prod, dict) else None
    if isinstance(meta, dict):
        for source, target in METADATA:
            if isinstance(meta.get(source), (str, int, float)):
                yield ("metadata", source), ("metadata", target), meta[source]
    yield from walk(prod, ())


def find_node(root: Optional[yaml.Node], path: Path_) -> Optional[yaml.Node]:
    node = root
    for key in path:
        if not isinstance(node, yaml.MappingNode):
            return None
        node = next((v for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == key), None)
    return node


def render(value: Any, node: yaml.ScalarNode) -> str:
    """value as YAML scalar text, in the style the detailed file uses at that place."""
    if not isinstance(value, str):
        return yaml.safe_dump(value, default_flow_style=True).splitlines()[0]
    if node.style == "'":
        return "'" + value.replace("'", "''") + "'"
    if node.style is None and value and yaml.safe_load(value) == value and value.strip() == value \
            and not value.startswith(("#", "&", "*", "!", "|", ">", "%", "@", "`")) and ": " not in value \
            and " #" not in value:
        return value
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid double-quoted YAML scalar


def compare(prod_text: str, detailed_text: str) -> Tuple[List[Tuple[int, int, str, str]], List[str]]:
    """Returns (edits as (start, end, new text, description), notes for values that cannot be synced)."""
    prod = yaml.safe_load(prod_text) or {}
    detailed = yaml.safe_load(detailed_text) or {}
    root = yaml.compose(detailed_text)
    edits, notes = [], []
    for source, target, value in synced_values(prod):
        node = find_node(root, target)
        where = ".".join(target)
        if node is None:
            notes.append(f"missing in {DETAILED}: {where} (add it by hand if the entry belongs there)")
            continue
        current = detailed
        for key in target:
            current = current.get(key) if isinstance(current, dict) else None
        if current == value:
            continue
        if not isinstance(node, yaml.ScalarNode) or node.style in ("|", ">"):
            notes.append(f"not changed, not a one-line value: {where}")
            continue
        edits.append((node.start_mark.index, node.end_mark.index, render(value, node),
                      f"{where}: {current!r} -> {value!r}"))
    return edits, notes


def apply(text: str, edits: Sequence[Tuple[int, int, str, str]]) -> str:
    for start, end, new, _ in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Sync pinned values from project_sources.yml to the detailed file.")
    ap.add_argument("--write", action="store_true", help="change the detailed file (default: only list)")
    ap.add_argument("--configs", type=Path, default=CONFIGS, help=f"folder with both files (default: {CONFIGS})")
    args = ap.parse_args(argv)

    prod_file, detailed_file = args.configs / PRODUCTION, args.configs / DETAILED
    for path in (prod_file, detailed_file):
        if not path.is_file():
            print(f"{path} not found.", file=sys.stderr)
            return 2
    try:
        prod_text = prod_file.read_text(encoding="utf-8")
        with open(detailed_file, encoding="utf-8", newline="") as f:
            detailed_text = f.read()
        edits, notes = compare(prod_text, detailed_text)
    except yaml.YAMLError as e:
        print(f"Not valid YAML: {e}", file=sys.stderr)
        return 2

    for note in notes:
        print(f"note: {note}")
    if not edits:
        print(f"{DETAILED} has the values of {PRODUCTION}.")
        return 0
    for *_, description in edits:
        print(("updated: " if args.write else "differs: ") + description)
    if not args.write:
        print(f"{len(edits)} value(s) differ; 'python scripts/sync_ssot.py --write' copies them (only the values).")
        return 1
    new_text = apply(detailed_text, edits)
    yaml.safe_load(new_text)  # still valid YAML, or nothing is written
    write_atomic(detailed_file, new_text)
    print(f"{len(edits)} value(s) written to {detailed_file}; review with: git diff {detailed_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
