#!/usr/bin/env python3
"""
The manifest and the checksums of a package for the board (target_module.sh: "Complete Manifest - JSON with all
build details"), and their check. Standard library only: it runs with the image's python3, outside the framework.

    board_package.py write PACKAGE_DIR [--set KEY=VALUE]... [--int KEY=NUMBER]... [--model FILE]
                     [--binary NAME=ELF_MACHINE]...
        writes MANIFEST.json: the given facts (dotted keys make sections: --set build.board=rk3588), "created" (UTC),
        "model_file" (name, size, sha256 of FILE), "binaries" (each program's size, sha256 and ELF machine) and
        "files" (every file of the package with its size and sha256, except MANIFEST.json and SHA256SUMS); then
        SHA256SUMS over every file except itself, MANIFEST.json included, in the format of sha256sum.
    board_package.py check PACKAGE_DIR
        checks SHA256SUMS (every file of the package is listed and has its hash) and that MANIFEST.json is valid JSON
        whose "files" match the package; exit code 0 or 1 with the reason.
    board_package.py room PACKAGE_DIR --what TEXT FILE...
        whether the disk of PACKAGE_DIR (its nearest existing folder) holds FILE... (their sizes) and still keeps the
        reserve free that the framework's disk rule keeps on every disk (2 GiB or 2 % of the disk, whichever is
        larger: reserve_for of orchestrator/Core/disk_plan.py); exit code 0, or 1 with the refusal in the words of
        that rule on stdout ("Not enough disk space for TEXT: output disk: ... needed, ... free, ... must stay free.").

Paths in the manifest are file names inside the package, never paths of the machine that built it.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "MANIFEST.json"
SUMS = "SHA256SUMS"
CHUNK = 1024 * 1024

# The framework's disk rule (orchestrator/Core/disk_plan.py: RESERVE_MIN, RESERVE_SHARE, gb, require and the person's
# next step action.free_disk; tests/test_target_stage.py compares): the same numbers and the same words in the image,
# which has no framework. The per-user reservation ledger is on the computer, not in the build container.
GIB = 1024 ** 3
GB = 1000 ** 3
RESERVE_MIN = 2 * GIB
RESERVE_SHARE = 0.02
FREE_DISK = "Free disk space (old builds, the model cache, the trash), or choose a smaller model."


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def package_files(folder: Path, skip=(MANIFEST, SUMS)):
    """The regular files of the package (sorted relative names), never following a link."""
    names = []
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(root) / name
            rel = path.relative_to(folder).as_posix()
            if rel not in skip and path.is_file() and not path.is_symlink():
                names.append(rel)
    return sorted(names)


def _put(target: dict, key: str, value) -> None:
    parts = key.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
        if not isinstance(target, dict):
            raise SystemExit(f"board_package.py: {key} sets a section inside a value")
    target[parts[-1]] = value


def _pair(text: str):
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise SystemExit(f"board_package.py: expected KEY=VALUE, got {text!r}")
    return key, value


def write(folder: Path, sets, ints, model, binaries) -> dict:
    manifest: dict = {"manifest_version": 1}
    for text in sets:
        _put(manifest, *_pair(text))
    for text in ints:
        key, value = _pair(text)
        _put(manifest, key, int(value))
    manifest["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if model:
        path = folder / model
        manifest["model_file"] = {"name": Path(model).name, "size_bytes": path.stat().st_size, "sha256": sha256(path)}
    if binaries:
        manifest["binaries"] = {}
        for text in binaries:
            name, machine = _pair(text)
            path = folder / name
            manifest["binaries"][name] = {"size_bytes": path.stat().st_size, "sha256": sha256(path),
                                          "elf_machine": int(machine) if machine.isdigit() else machine}
    manifest["files"] = [{"path": rel, "size_bytes": (folder / rel).stat().st_size, "sha256": sha256(folder / rel)}
                         for rel in package_files(folder)]
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    tmp = folder / f".{MANIFEST}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, folder / MANIFEST)
    sums = "".join(f"{sha256(folder / rel)}  {rel}\n" for rel in package_files(folder, skip=(SUMS,)))
    tmp = folder / f".{SUMS}.tmp"
    tmp.write_text(sums, encoding="utf-8")
    os.replace(tmp, folder / SUMS)
    return manifest


def gb(value: float) -> str:
    """Bytes in decimal gigabytes, as people and Hugging Face show sizes (disk_plan.gb)."""
    if value < GB:
        return f"{value / 1000 ** 2:.0f} MB"
    return f"{value / GB:.1f} GB"


def room(folder: Path, files, what: str, disk_usage=None):
    """None when the disk of `folder` (its nearest existing folder) holds `files` and keeps the reserve free (2 GiB or
    2 % of the disk, whichever is larger); else the refusal, as disk_plan.refusal words it for the output disk.
    OSError when the disk cannot be measured. `disk_usage`: the test seam, shutil.disk_usage by default."""
    path = Path(folder)
    while not path.exists() and path.parent != path:
        path = path.parent
    needed = sum(Path(f).stat().st_size for f in files)
    usage = (disk_usage or shutil.disk_usage)(str(path))
    total = int(getattr(usage, "total", 0) or 0)
    reserve = max(RESERVE_MIN, int(total * RESERVE_SHARE))
    if int(usage.free) - needed >= reserve:
        return None
    return (f"Not enough disk space for {what}: output disk: {gb(needed)} needed, {gb(int(usage.free))} free, "
            f"{gb(reserve)} must stay free. {FREE_DISK}")


def check(folder: Path) -> list:
    """The problems of a package; empty when SHA256SUMS and MANIFEST.json match its files."""
    problems = []
    try:
        lines = (folder / SUMS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return [f"{SUMS} is missing"]
    listed = {}
    for line in lines:
        digest, sep, name = line.partition("  ")
        if not sep or len(digest) != 64:
            problems.append(f"{SUMS} has a line that is no checksum: {line[:80]!r}")
            continue
        listed[name] = digest
    present = package_files(folder, skip=(SUMS,))
    for name in present:
        if name not in listed:
            problems.append(f"{name} is not in {SUMS}")
        elif sha256(folder / name) != listed[name]:
            problems.append(f"{name} does not have the checksum {SUMS} lists")
    for name in sorted(set(listed) - set(present)):
        problems.append(f"{name} is listed in {SUMS} but not in the package")
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return problems + [f"{MANIFEST} is not valid JSON: {e}"]
    files = {f.get("path"): f.get("sha256") for f in manifest.get("files", []) if isinstance(f, dict)}
    if files != {name: listed.get(name) for name in package_files(folder)}:
        problems.append(f"the files of {MANIFEST} are not the files of the package")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The manifest and checksums of a package for the board.")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write")
    w.add_argument("folder")
    w.add_argument("--set", dest="sets", action="append", default=[])
    w.add_argument("--int", dest="ints", action="append", default=[])
    w.add_argument("--model")
    w.add_argument("--binary", dest="binaries", action="append", default=[])
    c = sub.add_parser("check")
    c.add_argument("folder")
    r = sub.add_parser("room")
    r.add_argument("folder")
    r.add_argument("--what", required=True)
    r.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    if args.command == "write":
        write(folder, args.sets, args.ints, args.model, args.binaries)
        return 0
    if args.command == "room":
        try:
            problem = room(folder, args.files, args.what)
        except OSError as e:
            problem = f"The free space of the output disk could not be measured for {args.what}: {e.strerror or e}."
        if problem:
            print(problem)
        return 1 if problem else 0
    problems = check(folder)
    for problem in problems:
        print(f"package check: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
