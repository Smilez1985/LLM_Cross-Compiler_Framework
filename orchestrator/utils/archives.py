#!/usr/bin/env python3
"""
An archive's format by its content, never by its name (`sniff`), and 7-Zip for the formats Python's standard library
cannot read: rar, 7z, iso, cpio, zstd (where this Python has no compression.zstd), SquashFS and disk images (raw .img
or .bin with MBR or GPT partitions holding ext2/3/4, FAT or NTFS file systems, or one such file system alone).

7-Zip is found as `7zz`, `7z` or `7za` on PATH, on Windows also in its usual install folder; llmcf's installers set it
up. It is run with an argument list only (never a shell, never archive text as an option: `--` before the archive and
`-spd` so a name is never a wildcard), with stdin closed and a time limit. An archive is listed first (`l -slt -ba`) and
every entry checked by the same rules as safe_extract_tar (utils/helpers.py): no absolute path, no "..", no link out of
the folder, no hard link, no device, FIFO, socket or other special file, the caller's caps on files and bytes; only
then is it extracted, into a fresh folder of the caller's (a quarantine, never a store), and what was written is
compared with the listing again. A block of the listing with a field twice (a name with a line break in it) is
refused, so a crafted name cannot forge an entry.
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from orchestrator.utils.helpers import UnsafeArchive, execute_command

SEVEN_ZIP_NAMES = ("7zz", "7z", "7za")
LIST_TIMEOUT = 300
EXTRACT_TIMEOUT = 1800
HEAD = 40 * 1024

# What 7-Zip reads for llmcf (sniff's names) and how a message names each.
SEVEN_ZIP_KINDS = {
    "7z": "a 7z archive", "rar": "a RAR archive", "iso": "an ISO image", "cpio": "a cpio archive",
    "disk": "a disk image", "squashfs": "a SquashFS image", "zstd": "a zstd-compressed file",
}
COMPRESSED = ("gzip", "bzip2", "xz", "zstd")
PROGRAMS = ("script", "elf", "macho")
# A program file's first bytes: a script ("#!"), ELF, Mach-O (32/64 bit, either byte order). The content rule of a
# host without an executable bit (harness.py) marks such a file executable.
PROGRAM_MAGIC = {b"#!": "script", b"\x7fELF": "elf", b"\xfe\xed\xfa\xce": "macho", b"\xfe\xed\xfa\xcf": "macho",
                 b"\xce\xfa\xed\xfe": "macho", b"\xcf\xfa\xed\xfe": "macho"}

try:   # Python 3.14 reads zstd itself (tarfile too)
    from compression import zstd as _zstd  # type: ignore[import-not-found]  # noqa: F401
    ZSTD_STDLIB = True
except ImportError:
    ZSTD_STDLIB = False


class SevenZipMissing(Exception):
    """7-Zip is needed for this format and is not on this computer; the message says which format and the repair."""


def missing_text(what: str) -> str:
    return (f"{what} is read with 7-Zip (7zz, 7z or 7za), which is not on this computer: run llmcf's installer "
            "again (it sets up 7-Zip), or install 7-Zip yourself (Linux: the package 7zip or p7zip-full; Windows: "
            "winget install 7zip.7zip; macOS: brew install sevenzip), then take it in again")


def program_kind(head: bytes) -> Optional[str]:
    """"script", "elf" or "macho" by the first bytes, else None."""
    for magic, kind in PROGRAM_MAGIC.items():
        if head.startswith(magic):
            return kind
    return None


def is_program(path: Path) -> bool:
    """Whether the file starts like a script or a program (the content rule)."""
    try:
        with open(path, "rb") as fh:
            return program_kind(fh.read(4)) is not None
    except OSError:
        return False


def sniff(path: Path) -> Optional[str]:
    """The format of the file by its first bytes: zip, tar, gzip, bzip2, xz, zstd, 7z, rar, cpio, iso, disk,
    squashfs, script, elf, macho, or None for none of them. The name is never looked at."""
    with open(path, "rb") as fh:
        head = fh.read(HEAD)
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:3] == b"BZh" and head[3:4].isdigit():
        return "bzip2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:6] == b"Rar!\x1a\x07":
        return "rar"
    if head[257:262] == b"ustar":
        return "tar"
    if head[:6] in (b"070701", b"070702", b"070707") or head[:2] in (b"\xc7\x71", b"\x71\xc7"):
        return "cpio"
    program = program_kind(head)
    if program:
        return program
    if any(head[o:o + 5] == b"CD001" for o in (0x8001, 0x8801, 0x9001)):
        return "iso"
    if head[:4] in (b"hsqs", b"sqsh"):
        return "squashfs"
    if head[512:520] == b"EFI PART" or head[510:512] == b"\x55\xaa" or head[3:11] == b"NTFS    " \
            or head[1080:1082] == b"\x53\xef":
        return "disk"
    if len(head) >= 512 and _v7_tar(head[:512]):
        return "tar"
    return None


def _v7_tar(block: bytes) -> bool:
    """An old tar header without the ustar mark: its checksum field matches the block."""
    try:
        stored = int(block[148:156].replace(b"\0", b" ").strip() or b"-1", 8)
    except ValueError:
        return False
    return stored == sum(block[:148]) + 8 * 32 + sum(block[156:]) and block[0:1] not in (b"\0", b"")


def find_7zip() -> Optional[str]:
    """7-Zip's program: 7zz, 7z or 7za on PATH, on Windows also its usual install folder; None when there is none."""
    for name in SEVEN_ZIP_NAMES:
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
                return str(Path(base) / "7-Zip" / "7z.exe")
    return None


@dataclass(frozen=True)
class Entry:
    """One entry of 7-Zip's listing: `path` relative with "/", `raw` as 7-Zip names it (for extracting it alone),
    `kind` file, dir, link, hardlink or special, `mode` the unix permission bits where the archive has them, `link`
    a link's target where the listing names it, `fields` every field of its block."""
    path: str
    raw: str
    kind: str
    size: int
    mode: Optional[int] = None
    link: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)


def parse_blocks(text: str) -> List[Dict[str, str]]:
    """The blocks of `7z l -slt -ba` (Key = Value lines, a blank line between blocks). UnsafeArchive for a block with
    a field twice or without a Path: a name with a line break cannot forge an entry."""
    blocks: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if current:
                if "Path" not in current:
                    raise UnsafeArchive("Security: an entry of the archive's listing has no path")
                blocks.append(current)
                current = {}
            continue
        key, sep, value = line.partition(" = ")
        if not sep:
            key, sep, value = line.rstrip().partition(" =")
        if not sep:
            raise UnsafeArchive(f"Security: a line of the archive's listing that is no field: {line[:80]!r}")
        if key in current:
            raise UnsafeArchive(f"Security: an entry of the archive's listing names {key} twice (a name with a line "
                                "break?)")
        current[key] = value
    return blocks


RE_UNIX = re.compile(r"(?:^|\s)([-dlcbps])([-r][-w][-xsS][-r][-w][-xsS][-r][-w][-xtT])(?:\s|$)")


def _unix(fields: Dict[str, str]):
    """(type letter, permission bits) from Mode or Attributes ("A -rwxr-xr-x", "-rw-r--r--", 0x81ED8020), else
    (None, None)."""
    for key in ("Mode", "Attributes"):
        value = fields.get(key, "")
        found = RE_UNIX.search(value)
        if found:
            perm = found.group(2)
            bits = 0
            for i, ch in enumerate(perm):
                if ch not in "-ST":
                    bits |= 1 << (8 - i)
            return found.group(1), bits
        hexed = re.fullmatch(r"\s*0x([0-9A-Fa-f]{1,8})\s*", value)
        if hexed:
            number = int(hexed.group(1), 16)
            if number & 0x8000:   # FILE_ATTRIBUTE_UNIX_EXTENSION: the unix mode in the high 16 bits
                umode = number >> 16
                kind = {0o040000: "d", 0o120000: "l", 0o100000: "-", 0o020000: "c", 0o060000: "b",
                        0o010000: "p", 0o140000: "s"}.get(umode & 0o170000, "-")
                return kind, umode & 0o777
    return None, None


def relative(path: str) -> str:
    """A listed path as a relative path with "/" ("" for the archive's top); UnsafeArchive for an absolute path, a
    drive, ".." or a NUL."""
    p = path.replace("\\", "/")
    if p.startswith("/") or re.match(r"[A-Za-z]:", p) or "\0" in p:
        raise UnsafeArchive(f"Security: an absolute path in the archive: {path}")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if ".." in parts:
        raise UnsafeArchive(f"Security: a path out of the folder in the archive: {path}")
    return "/".join(parts)


def link_inside(rel: str, target: str) -> bool:
    """A link at `rel` with `target` stays inside the folder by its words (llmcf-install's stays_inside)."""
    if not target or target.startswith("/") or "\0" in target or "\\" in target or re.match(r"[A-Za-z]:", target):
        return False
    parts = rel.split("/")[:-1]
    for p in target.split("/"):
        if p == "..":
            if not parts:
                return False
            parts.pop()
        elif p not in ("", "."):
            parts.append(p)
    return True


def entries(blocks: Sequence[Dict[str, str]]) -> List[Entry]:
    """The checked entries of a listing (the archive's top itself left out). UnsafeArchive for an absolute path,
    "..", a link out of the folder, a hard link, a device, FIFO, socket or other special file."""
    found: List[Entry] = []
    for block in blocks:
        raw = block["Path"]
        rel = relative(raw)
        if not rel:
            continue
        letter, mode = _unix(block)
        attrs = block.get("Attributes", "").split(" ", 1)[0]
        target = block.get("Symbolic Link", "") or None
        if block.get("Hard Link"):
            raise UnsafeArchive(f"Security: a hard link in the archive: {rel}")
        if letter in ("c", "b", "p", "s"):
            raise UnsafeArchive(f"Security: a device, FIFO or socket in the archive: {rel}")
        if letter == "l" or target:
            kind = "link"
            if target is not None and not link_inside(rel, target.replace("\\", "/") if os.name == "nt" else target):
                raise UnsafeArchive(f"Security: a link out of the folder in the archive: {rel} -> {target}")
        elif letter == "d" or block.get("Folder") == "+" or "D" in attrs:
            kind = "dir"
        else:
            kind = "file"
        try:
            size = int(block.get("Size") or 0)
        except ValueError:
            size = 0
        found.append(Entry(rel, raw, kind, max(0, size), mode, target, dict(block)))
    return found


def check_caps(found: Sequence[Entry], *, max_files: int, max_bytes: int) -> int:
    """The bytes the entries unpack to; UnsafeArchive above the caller's caps."""
    files = [e for e in found if e.kind != "dir"]
    if len(files) > max_files:
        raise UnsafeArchive(f"the archive has more than {max_files} files")
    total = sum(e.size for e in files)
    if total > max_bytes:
        raise UnsafeArchive(f"the archive unpacks to more than {max_bytes // (1024 * 1024)} MB")
    return total


def is_partition_listing(found: Sequence[Entry]) -> bool:
    """A disk image that 7-Zip lists as its partitions (each with an offset), not as one file system's files."""
    return bool(found) and all(e.kind == "file" and "Offset" in e.fields and "/" not in e.path for e in found)


def list_archive(exe: str, archive: Path) -> List[Entry]:
    """The checked listing of `archive`; UnsafeArchive when 7-Zip cannot read it or an entry is refused."""
    code, out, err = execute_command([exe, "l", "-slt", "-ba", "-sccUTF-8", "-spd", "-p", "--", str(archive)],
                                     timeout=LIST_TIMEOUT)
    if code != 0:
        reason = (err or out).strip().splitlines()
        raise UnsafeArchive(f"7-Zip cannot read it: {reason[-1] if reason else f'exit code {code}'}")
    return entries(parse_blocks(out))


def extract(exe: str, archive: Path, dest: Path, listed: Sequence[Entry], *, only: Sequence[str] = ()) -> None:
    """Extracts the listed (and checked) entries of `archive` into the fresh folder `dest` (`only`: these names of
    7-Zip's listing alone) and compares what was written with the listing: a path that was not listed is refused."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    code, out, err = execute_command([exe, "x", "-y", "-bd", "-sccUTF-8", "-spd", "-p", f"-o{dest}", "--",
                                      str(archive), *only], timeout=EXTRACT_TIMEOUT)
    if code != 0:
        reason = (err or out).strip().splitlines()
        raise UnsafeArchive(f"7-Zip could not extract it: {reason[-1] if reason else f'exit code {code}'}")
    wanted = {e.path for e in listed if not only or e.raw in only}
    allowed = set(wanted)
    for p in wanted:
        parts = p.split("/")
        allowed.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for dirpath, dirnames, filenames in os.walk(dest):
        for name in filenames + dirnames:
            rel = (Path(dirpath) / name).relative_to(dest).as_posix()
            if rel not in allowed:
                raise UnsafeArchive(f"Security: 7-Zip wrote what the listing did not name: {rel}")


__all__ = ["COMPRESSED", "Entry", "PROGRAMS", "SEVEN_ZIP_KINDS", "SevenZipMissing", "ZSTD_STDLIB", "check_caps",
           "entries", "extract", "find_7zip", "is_partition_listing", "is_program", "link_inside", "list_archive",
           "missing_text", "parse_blocks", "program_kind", "relative", "sniff"]
