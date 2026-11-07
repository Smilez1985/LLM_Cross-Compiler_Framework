#!/usr/bin/env python3
"""
A minimal read-only reader of an ext2/ext3/ext4 file system inside an image file, at a byte offset, and the partition
table of a disk image (MBR and GPT). It reads only what it is asked for: directory entries while it searches, and file
data only for the files it copies out, so a board's rootfs of many GB is never extracted or copied whole, and it needs
neither root nor a loop device. Nothing is written to the image; it is opened read-only.

What it reads: the superblock, the group descriptors (32 or 64 bytes, the 64-bit feature), inodes, file data through
extents (any depth; an unwritten extent reads as zeros) or the old block map (direct, single, double and triple
indirect; a hole reads as zeros), directories linearly (an htree directory's index blocks look like empty entries and
are skipped that way; the checksum tail too), fast and slow symlinks. A file system with a feature it cannot read
(compression, inline data, encryption, meta_bg, a journal device) is refused by name. Every structure is checked
against the image's size before it is read, so a crafted image raises Ext4Error, never reads elsewhere or loops
(an extent tree deeper than 5 or a block map reached twice is refused).

Python's own `struct` only; no third-party package (the PyPI package `ext4` would bring compiled wheels and two exact
pins of its own).
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, List, Optional, Tuple

EXT_MAGIC = 0xEF53
SECTOR = 512

S_IFMT = 0o170000
S_IFREG = 0o100000
S_IFDIR = 0o040000
S_IFLNK = 0o120000

INCOMPAT_COMPRESSION = 0x1
INCOMPAT_FILETYPE = 0x2
INCOMPAT_RECOVER = 0x4
INCOMPAT_JOURNAL_DEV = 0x8
INCOMPAT_META_BG = 0x10
INCOMPAT_EXTENTS = 0x40
INCOMPAT_64BIT = 0x80
INCOMPAT_MMP = 0x100
INCOMPAT_FLEX_BG = 0x200
INCOMPAT_EA_INODE = 0x400
INCOMPAT_DIRDATA = 0x1000
INCOMPAT_CSUM_SEED = 0x2000
INCOMPAT_LARGEDIR = 0x4000
INCOMPAT_INLINE_DATA = 0x8000
INCOMPAT_ENCRYPT = 0x10000
INCOMPAT_CASEFOLD = 0x20000
READABLE = (INCOMPAT_FILETYPE | INCOMPAT_RECOVER | INCOMPAT_EXTENTS | INCOMPAT_64BIT | INCOMPAT_MMP | INCOMPAT_FLEX_BG
            | INCOMPAT_EA_INODE | INCOMPAT_CSUM_SEED | INCOMPAT_LARGEDIR | INCOMPAT_CASEFOLD)
NOT_READ = {INCOMPAT_COMPRESSION: "compression", INCOMPAT_JOURNAL_DEV: "a journal device", INCOMPAT_META_BG: "meta_bg",
            INCOMPAT_DIRDATA: "dirdata", INCOMPAT_INLINE_DATA: "inline data", INCOMPAT_ENCRYPT: "encryption"}

FL_EXTENTS = 0x80000
FL_INLINE_DATA = 0x10000000
EXTENT_MAGIC = 0xF30A
MAX_EXTENT_DEPTH = 5
ROOT_INO = 2
MAX_DIR_BYTES = 64 * 1024 * 1024


class Ext4Error(ValueError):
    """The image or its file system cannot be read here; the message says why, plainly."""


@dataclass(frozen=True)
class Partition:
    """One partition of a disk image: its number (0 the first in the table's order), byte offset and size, and the
    table's type (an MBR type byte as "0x83", a GPT type GUID)."""
    number: int
    offset: int
    size: int
    type: str


@dataclass(frozen=True)
class Inode:
    number: int
    mode: int
    size: int
    flags: int
    block: bytes

    @property
    def kind(self) -> str:
        fmt = self.mode & S_IFMT
        return {S_IFREG: "file", S_IFDIR: "dir", S_IFLNK: "link"}.get(fmt, "special")


def partitions(image: Path) -> List[Partition]:
    """The partitions of a disk image: GPT where its header is there (a protective MBR names it), else the four
    primary MBR entries (an extended partition's logical ones are not read). [] when there is no table."""
    size = Path(image).stat().st_size
    with open(image, "rb") as fh:
        mbr = fh.read(SECTOR)
        if len(mbr) < SECTOR or mbr[510:512] != b"\x55\xaa":
            return []
        entries = [mbr[446 + 16 * i:462 + 16 * i] for i in range(4)]
        if any(e[4] == 0xEE for e in entries):
            fh.seek(SECTOR)
            head = fh.read(SECTOR)
            if head[:8] == b"EFI PART":
                return _gpt(fh, head, size)
        found: List[Partition] = []
        for e in entries:
            kind = e[4]
            start, count = struct.unpack("<II", e[8:16])
            if kind == 0 or count == 0 or kind in (0x05, 0x0F, 0x85):
                continue
            offset, length = start * SECTOR, count * SECTOR
            if offset + length > size or offset == 0:
                raise Ext4Error("the disk image's partition table names a partition beyond the end of the file")
            found.append(Partition(len(found), offset, length, f"0x{kind:02x}"))
        if found and _boot_sector_only(mbr):
            return []   # a FAT file system alone: its boot sector carries 55 AA too
        return found


def _boot_sector_only(mbr: bytes) -> bool:
    return mbr[54:59] == b"FAT12" or mbr[54:59] == b"FAT16" or mbr[82:87] == b"FAT32" or mbr[3:11] == b"NTFS    "


def _gpt(fh: BinaryIO, head: bytes, size: int) -> List[Partition]:
    lba, number, entry_size = struct.unpack("<QII", head[72:88])
    if not 128 <= entry_size <= 4096 or number > 1024:
        raise Ext4Error("the disk image's GPT header is not one this framework reads")
    fh.seek(lba * SECTOR)
    table = fh.read(number * entry_size)
    found: List[Partition] = []
    for i in range(number):
        e = table[i * entry_size:(i + 1) * entry_size]
        if len(e) < 48 or e[:16] == bytes(16):
            continue
        first, last = struct.unpack("<QQ", e[32:48])
        offset, length = first * SECTOR, (last - first + 1) * SECTOR
        if last < first or offset + length > size:
            raise Ext4Error("the disk image's GPT names a partition beyond the end of the file")
        guid = e[:16]
        text = "%08x-%04x-%04x-%s-%s" % (struct.unpack("<I", guid[:4])[0], struct.unpack("<H", guid[4:6])[0],
                                         struct.unpack("<H", guid[6:8])[0], guid[8:10].hex(), guid[10:16].hex())
        found.append(Partition(len(found), offset, length, text))
    return found


def is_ext(image: Path, offset: int = 0) -> bool:
    try:
        with open(image, "rb") as fh:
            fh.seek(offset + 1024 + 0x38)
            return fh.read(2) == struct.pack("<H", EXT_MAGIC)
    except OSError:
        return False


class Ext4:
    """The file system in `image` at byte `offset` (of at most `length` bytes), opened read-only. `read_bytes` counts
    what was read from the image."""

    def __init__(self, image: Path, offset: int = 0, length: Optional[int] = None):
        self.fh = open(image, "rb")
        self.offset = offset
        total = Path(image).stat().st_size - offset
        self.length = min(total, length) if length else total
        self.read_bytes = 0
        try:
            self._superblock()
        except BaseException:
            self.fh.close()
            raise

    def close(self) -> None:
        self.fh.close()

    def __enter__(self) -> "Ext4":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read(self, at: int, size: int) -> bytes:
        if at < 0 or size < 0 or at + size > self.length:
            raise Ext4Error("the file system points beyond its partition (a damaged or crafted image)")
        self.fh.seek(self.offset + at)
        data = self.fh.read(size)
        if len(data) != size:
            raise Ext4Error("the image ends inside the file system")
        self.read_bytes += size
        return data

    def _superblock(self) -> None:
        sb = self._read(1024, 1024)
        if struct.unpack_from("<H", sb, 0x38)[0] != EXT_MAGIC:
            raise Ext4Error("no ext2/3/4 file system there")
        log = struct.unpack_from("<I", sb, 0x18)[0]
        if log > 6:
            raise Ext4Error("the file system's block size is not one this framework reads")
        self.block_size = 1024 << log
        self.first_data_block = struct.unpack_from("<I", sb, 0x14)[0]
        self.inodes_per_group = struct.unpack_from("<I", sb, 0x28)[0]
        self.inodes_count = struct.unpack_from("<I", sb, 0x00)[0]
        rev = struct.unpack_from("<I", sb, 0x4C)[0]
        self.inode_size = struct.unpack_from("<H", sb, 0x58)[0] if rev >= 1 else 128
        self.incompat = struct.unpack_from("<I", sb, 0x60)[0]
        missing = [name for bit, name in NOT_READ.items() if self.incompat & bit]
        unknown = self.incompat & ~(READABLE | sum(NOT_READ))
        if missing or unknown:
            raise Ext4Error("the ext file system uses " + (", ".join(missing) or f"features 0x{unknown:x}")
                            + ", which this framework does not read")
        self.is64 = bool(self.incompat & INCOMPAT_64BIT)
        self.desc_size = (struct.unpack_from("<H", sb, 0xFE)[0] or 64) if self.is64 else 32
        blocks = struct.unpack_from("<I", sb, 0x04)[0]
        if self.is64:
            blocks |= struct.unpack_from("<I", sb, 0x150)[0] << 32
        self.blocks_count = blocks
        if not self.inodes_per_group or self.inode_size < 128 or self.desc_size < 32 \
                or self.inode_size > self.block_size:
            raise Ext4Error("the ext file system's superblock is damaged")
        self.groups = (self.inodes_count + self.inodes_per_group - 1) // self.inodes_per_group
        self._tables: Dict[int, int] = {}

    def _inode_table(self, group: int) -> int:
        if group not in self._tables:
            gdt = (self.first_data_block + 1) * self.block_size
            d = self._read(gdt + group * self.desc_size, self.desc_size)
            table = struct.unpack_from("<I", d, 0x8)[0]
            if self.is64 and self.desc_size >= 64:
                table |= struct.unpack_from("<I", d, 0x28)[0] << 32
            self._tables[group] = table
        return self._tables[group]

    def inode(self, number: int) -> Inode:
        if not 1 <= number <= self.inodes_count:
            raise Ext4Error(f"an inode number out of range ({number})")
        group, index = divmod(number - 1, self.inodes_per_group)
        raw = self._read(self._inode_table(group) * self.block_size + index * self.inode_size, 128)
        mode = struct.unpack_from("<H", raw, 0)[0]
        size = struct.unpack_from("<I", raw, 0x4)[0] | (struct.unpack_from("<I", raw, 0x6C)[0] << 32)
        flags = struct.unpack_from("<I", raw, 0x20)[0]
        if flags & FL_INLINE_DATA:
            raise Ext4Error("a file stored inline in its inode, which this framework does not read")
        return Inode(number, mode, size, flags, raw[0x28:0x28 + 60])

    # --- the file's blocks --------------------------------------------------------------------------------------------

    def _runs(self, ino: Inode) -> Iterator[Tuple[int, int, int]]:
        """(logical block, physical block or 0 for zeros, count) in logical order."""
        if ino.flags & FL_EXTENTS:
            yield from sorted(self._extents(ino.block, 0))
        else:
            yield from self._block_map(ino.block)

    def _extents(self, node: bytes, depth: int) -> List[Tuple[int, int, int]]:
        if depth > MAX_EXTENT_DEPTH:
            raise Ext4Error("an extent tree deeper than ext4 allows (a damaged or crafted image)")
        magic, entries, _max, tree_depth = struct.unpack_from("<HHHH", node, 0)
        if magic != EXTENT_MAGIC or entries * 12 + 12 > len(node):
            raise Ext4Error("a damaged extent tree")
        out: List[Tuple[int, int, int]] = []
        for i in range(entries):
            at = 12 + 12 * i
            if tree_depth == 0:
                logical, length, hi, lo = struct.unpack_from("<IHHI", node, at)
                zeros = length > 32768
                count = length - 32768 if zeros else length
                out.append((logical, 0 if zeros else (hi << 32) | lo, count))
            else:
                _logical, lo, hi = struct.unpack_from("<IIH", node, at)
                child = self._read(((hi << 32) | lo) * self.block_size, self.block_size)
                out.extend(self._extents(child, depth + 1))
        return out

    def _block_map(self, block: bytes) -> Iterator[Tuple[int, int, int]]:
        pointers = struct.unpack("<15I", block)
        per = self.block_size // 4
        logical = 0
        seen = set()
        for p in pointers[:12]:
            yield logical, p, 1
            logical += 1

        def walk(p: int, level: int, logical: int) -> Iterator[Tuple[int, int, int]]:
            span = per ** level
            if p == 0:
                yield logical, 0, span
                return
            if p in seen:
                raise Ext4Error("a block map that points to itself (a damaged or crafted image)")
            seen.add(p)
            table = struct.unpack(f"<{per}I", self._read(p * self.block_size, self.block_size))
            for i, q in enumerate(table):
                if level == 1:
                    yield logical + i, q, 1
                else:
                    yield from walk(q, level - 1, logical + i * per ** (level - 1))
        for level, p in enumerate(pointers[12:15], start=1):
            yield from walk(p, level, logical)
            logical += per ** level

    def read_data(self, ino: Inode, out: Optional[BinaryIO] = None, limit: int = MAX_DIR_BYTES) -> bytes:
        """The file's bytes (written to `out` when given, else returned; at most `limit`)."""
        if ino.size > limit:
            raise Ext4Error(f"a file larger than {limit // (1024 * 1024)} MB")
        left = ino.size
        pos = 0
        chunks: List[bytes] = []
        bs = self.block_size
        for logical, physical, count in self._runs(ino):
            if left <= 0:
                break
            start = logical * bs
            if start > pos:   # a hole
                gap = min(start - pos, left)
                self._emit(b"\0" * gap, out, chunks)
                pos += gap
                left -= gap
                if left <= 0:
                    break
            if start < pos:
                continue
            take = min(count * bs, left)
            data = b"\0" * take if physical == 0 else self._read(physical * bs, take)
            self._emit(data, out, chunks)
            pos += take
            left -= take
        if left > 0:
            self._emit(b"\0" * left, out, chunks)
        return b"".join(chunks)

    @staticmethod
    def _emit(data: bytes, out: Optional[BinaryIO], chunks: List[bytes]) -> None:
        if out is not None:
            out.write(data)
        else:
            chunks.append(data)

    # --- names --------------------------------------------------------------------------------------------------------

    def entries(self, ino: Inode) -> List[Tuple[str, int, int]]:
        """A directory's (name, inode number, file type) entries, "." and ".." left out; the file type is the
        entry's own (2 a directory, 0 when the file system records none)."""
        if ino.kind != "dir":
            raise Ext4Error("not a directory")
        data = self.read_data(ino)
        found: List[Tuple[str, int, int]] = []
        at = 0
        while at + 8 <= len(data):
            number, rec_len, name_len, ftype = struct.unpack_from("<IHBB", data, at)
            if rec_len < 8 or at + rec_len > len(data) or name_len > rec_len - 8:
                raise Ext4Error("a damaged directory")
            if number:
                name = data[at + 8:at + 8 + name_len].decode("utf-8", errors="surrogateescape")
                if name not in (".", ".."):
                    found.append((name, number, ftype if self.incompat & INCOMPAT_FILETYPE else 0))
            at += rec_len
        return found

    def link_target(self, ino: Inode) -> str:
        if ino.size < 60 and not ino.flags & FL_EXTENTS:
            raw = ino.block[:ino.size]
        else:
            raw = self.read_data(ino, limit=4096)
        return raw.decode("utf-8", errors="surrogateescape")

    def lookup(self, path: str) -> Inode:
        """The inode at a relative path (no link followed). Ext4Error when it is not there."""
        ino = self.inode(ROOT_INO)
        for part in [p for p in path.split("/") if p not in ("", ".")]:
            if ino.kind != "dir":
                raise Ext4Error(f"{path}: not there in the file system")
            match = [n for name, n, _t in self.entries(ino) if name == part]
            if not match:
                raise Ext4Error(f"{path}: not there in the file system")
            ino = self.inode(match[0])
        return ino


__all__ = ["Ext4", "Ext4Error", "Inode", "Partition", "is_ext", "partitions"]
