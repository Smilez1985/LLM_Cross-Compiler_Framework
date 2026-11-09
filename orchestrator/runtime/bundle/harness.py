#!/usr/bin/env python3
"""
The person's harness for the board (ROADMAP 3.4.0 "An agent harness on the board as an opt-in bundle component";
DESIGN "A harness on the board is the person's opt-in"): a harness is its own project with its own repository, never
code in llmcf, and llmcf does not build it. The person names it as a git repository link, an archive or disk image
(any common format, see below), one installer file, or a folder; llmcf takes it in here, on the host, at a pin (the commit of a repository, the sha256 of an archive)
and records the whole tree with one checksum, so what reaches the board is exactly what the person saw.

What the harness's own installer needs is described by a small spec (`HarnessSpec`, schema llmcf-harness/1): the
installer's path inside the tree, its dry-run arguments (the "before" report), its own "assume yes" switch (an opt-in
only: by default the installer's questions reach the person live and are answered one by one), and what its exit codes
mean (`done` or `findings`; any other code is
a failure). Chimera, the first example, is: installer `installer/chimera-install`, dry run `--dry-run`, assume yes
`CHIMERA_ASSUME_YES=1`, exit 0 done and 1 findings.

The store: <state>/harnesses/<id>/<tree sha256>/tree/ and harness.json beside it; a tree that is already there is
reused. Nothing here runs the installer: that happens on the board, as root through llmcf-install (harness_run.py).

For the bundle the tree is packed into one plain tar file (`pack`: harness.tar beside the tree, USTAR, only the
listing's entries in its order, every owner 0, every time 0, mode 0755 or 0644 by the executable bit, links as links),
so the same tree gives the same bytes; llmcf-install unpacks it on the board and computes the same tree checksum
(rust/llmcf-install/src/harness.rs; tests/data/harness_tree_vectors.json holds the vectors both sides check).

The executable bit and links come from the source's own metadata where the host's file system has none (Windows,
NO_EXEC_BIT): at take-in the modes are recorded beside the tree (modes.json: the executable files and the links with
their targets) from the git tree (`git ls-tree`: 100755, 120000), the archive's members (a tar member's mode, a zip
entry's external attributes, 7-Zip's attributes) or, for a folder, its git index (`git ls-files --stage`);
tree_listing and pack use them there instead of os.stat, so the same harness gives the same tree checksum and the same
pack on Windows and Linux. Where the source records no mode (a plain folder on Windows, a zip made on Windows, a FAT
image) the bit is derived from the content, deterministically: a file that starts with "#!" or an ELF or Mach-O magic,
and the spec's installer, is executable (`content_modes`; TakenIn.derived names them). On Linux and macOS a folder's
own file system modes count, as before.

An archive's format comes from its content, never its name (utils/archives.sniff): zip, tar and tar compressed with
gzip, bzip2 or xz (and zstd where this Python has compression.zstd) through the standard library; one gzip, bzip2, xz
or zstd file that holds no tar is the harness's one file; rar, 7z, iso, cpio, SquashFS and zstd otherwise through
7-Zip, listed and checked before anything is written (utils/archives); a file that is no archive but a script or a
program (a makeself installer .bin) is a harness of one file, its installer the file itself. A hard link inside the
tree becomes a file of its own with its target's content; one out of it is refused.

A disk image (MBR or GPT) is never extracted whole: an ext2/3/4 partition is read in place (utils/ext4_read), its
folders only while the installer is searched, then only the harness's folder (the spec's `image_path`); a FAT or NTFS
partition goes through 7-Zip, one partition at a time under the disk rule. The installer in more than one place needs
the spec's `image_partition` (and `image_path`). An encrypted archive raises PasswordNeeded: the person's password
(asked by the CLI or the window, never by an AI) reaches 7-Zip on its stdin only and is never kept. The pin is always
the sha256 of the file the person named.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.utils.modes import SHARED_READ, SHARED_RUN

SCHEMA = "llmcf-harness/1"
KINDS = ("git", "archive", "folder")
OUTCOMES = ("done", "findings")
STORE_DIR = "harnesses"
TREE_DIR = "tree"
SPEC_FILE = "harness.json"
MODES_FILE = "modes.json"
# The host's file system keeps no executable bit (Windows): the source's recorded modes count instead.
NO_EXEC_BIT = os.name == "nt"
MAX_PARTITIONS = 128

RE_ID = re.compile(r"[a-z][a-z0-9-]{1,39}")
RE_COMMIT = re.compile(r"[0-9a-f]{40}")
RE_SHA256 = re.compile(r"[0-9a-f]{64}")
RE_ENV = re.compile(r"([A-Z][A-Z0-9_]{0,63})=([A-Za-z0-9_.:-]{0,64})")
RE_ARG = re.compile(r"--?[A-Za-z0-9][A-Za-z0-9_.=-]{0,63}")
RE_GIT_URL = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9._~/-]+")
RE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")

MAX_FILES = 20000
MAX_BYTES = 512 * 1024 * 1024
MAX_ARGS = 8


class HarnessError(ValueError):
    """A harness spec or source that cannot be taken in; the message says why, plainly."""


@dataclass(frozen=True)
class HarnessSpec:
    """What the person names. `location` is the repository link, the archive or the folder; `ref` (git only) the
    branch or tag to take, resolved to a commit at intake; `pin` the commit (git) or the archive's sha256 when the
    person gives it, checked; `installer` the installer's path inside the tree."""
    id: str
    title: str
    kind: str
    location: str
    installer: str
    ref: Optional[str] = None
    pin: Optional[str] = None
    dry_run_args: Tuple[str, ...] = ()
    assume_yes: Optional[str] = None
    exit_codes: Dict[int, str] = field(default_factory=lambda: {0: "done"})
    image_partition: Optional[int] = None
    image_path: Optional[str] = None


def check_spec(spec: HarnessSpec) -> HarnessSpec:
    """The spec, or HarnessError naming the field."""
    if not RE_ID.fullmatch(spec.id or ""):
        raise HarnessError("id: 2 to 40 lower-case letters, digits and hyphens, starting with a letter")
    if not spec.title or len(spec.title) > 80 or not spec.title.isprintable():
        raise HarnessError("title: 1 to 80 printable characters")
    if spec.kind not in KINDS:
        raise HarnessError(f"kind is one of {', '.join(KINDS)}")
    if spec.kind == "git":
        if not RE_GIT_URL.fullmatch(spec.location or "") or "@" in spec.location or ".." in spec.location:
            raise HarnessError("location: an https link to the repository, without a user name or password")
        if spec.pin is not None and not RE_COMMIT.fullmatch(spec.pin):
            raise HarnessError("pin: the full 40-character commit")
        if spec.ref is not None and (not RE_REF.fullmatch(spec.ref) or ".." in spec.ref):
            raise HarnessError("ref: a branch or tag name")
    else:
        if spec.ref is not None:
            raise HarnessError("ref is for a git repository only")
        if spec.kind == "archive" and spec.pin is not None and not RE_SHA256.fullmatch(spec.pin):
            raise HarnessError("pin: the archive's sha256 (64 hex characters)")
        if spec.kind == "folder" and spec.pin is not None:
            raise HarnessError("a folder has no pin; its tree checksum is taken at intake")
    if spec.image_partition is not None and (spec.kind != "archive" or isinstance(spec.image_partition, bool)
                                             or not isinstance(spec.image_partition, int)
                                             or not 0 <= spec.image_partition < MAX_PARTITIONS):
        raise HarnessError("image_partition: for a disk image only, the partition's number (0 the first)")
    if spec.image_path is not None:
        if spec.kind != "archive":
            raise HarnessError("image_path: for a disk image only, the harness's folder inside the partition")
        _check_relative(spec.image_path, "image_path")
    _check_relative(spec.installer, "installer")
    if len(spec.dry_run_args) > MAX_ARGS or not all(RE_ARG.fullmatch(a) for a in spec.dry_run_args):
        raise HarnessError("dry_run_args: up to 8 options such as --dry-run")
    if spec.assume_yes is not None and not RE_ENV.fullmatch(spec.assume_yes):
        raise HarnessError("assume_yes: NAME=value, such as CHIMERA_ASSUME_YES=1")
    if not spec.exit_codes or any(not isinstance(c, int) or not 0 <= c <= 255 or o not in OUTCOMES
                                  for c, o in spec.exit_codes.items()):
        raise HarnessError("exit_codes: codes 0 to 255, each meaning done or findings")
    return spec


RE_REL = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")


def _check_relative(path: str, what: str) -> None:
    p = PurePosixPath(path or "")
    if not RE_REL.fullmatch(path or "") or p.is_absolute() or ".." in p.parts:
        raise HarnessError(f"{what}: a path inside the harness, such as installer/install.sh")


@dataclass(frozen=True)
class TakenIn:
    """A harness taken in: the spec, the resolved pin, the tree's checksum and size, and where it lies. `derived`: the
    files marked executable by the content rule where the source recorded no mode (empty when every mode came from
    the source or the file system); `format` the archive's format by its content."""
    spec: HarnessSpec
    pin: str
    tree_sha256: str
    files: int
    size: int
    path: Path
    derived: Tuple[str, ...] = ()
    content_rule: bool = False
    format: str = ""
    image_read: int = 0
    image_size: int = 0

    def record(self) -> dict:
        """harness.json; of the derived modes only their count (the names stay in modes.json and the person's text)."""
        spec = asdict(self.spec)
        spec["dry_run_args"] = list(self.spec.dry_run_args)
        spec["exit_codes"] = {str(k): v for k, v in sorted(self.spec.exit_codes.items())}
        out = {"schema": SCHEMA, "spec": spec, "pin": self.pin, "tree_sha256": self.tree_sha256,
               "files": self.files, "size": self.size}
        if self.format:
            out["format"] = self.format
        if self.content_rule:
            out["modes_derived_from_content"] = len(self.derived)
        if self.image_size:
            out["image_read"], out["image_size"] = self.image_read, self.image_size
        return out


@dataclass(frozen=True)
class Modes:
    """What the source says about its entries (where the host's file system cannot): the executable files and the
    links with their targets, by relative path."""
    executable: Tuple[str, ...] = ()
    links: Dict[str, str] = field(default_factory=dict)

    def record(self) -> dict:
        return {"executable": sorted(self.executable), "links": dict(sorted(self.links.items()))}

    @classmethod
    def load(cls, path: Path) -> Optional["Modes"]:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(tuple(str(p) for p in data.get("executable", [])),
                       {str(k): str(v) for k, v in dict(data.get("links", {})).items()})
        except (OSError, ValueError, TypeError, AttributeError):
            return None


def _link_inside(rel: str, target: str) -> bool:
    """A link `rel` with `target` stays inside the tree by its words (llmcf-install's stays_inside)."""
    from orchestrator.utils.archives import link_inside
    return link_inside(rel, target)


def content_modes(tree: Path, installer: str) -> Tuple[Modes, Tuple[str, ...]]:
    """The content rule where nothing records the executable bit (a plain folder on Windows): a regular file that
    starts with "#!" or an ELF or Mach-O magic, and the installer, is executable. (modes, the executable files)."""
    from orchestrator.utils.archives import is_program
    tree = Path(tree)
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) == tree and d == ".git"))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(tree).as_posix()
            if not full.is_symlink() and full.is_file() and (rel == installer or is_program(full)):
                found.append(rel)
    found.sort()
    return Modes(tuple(found)), tuple(found)


def tree_listing(root: Path, modes: Optional[Modes] = None) -> List[Tuple[str, str, str]]:
    """(relative path, kind, digest) for every entry below `root`, sorted, `.git` left out: kind "x" an executable
    file, "f" another file, "l" a link (its target as digest). With `modes` (a host without executable bits: the
    source's recorded modes) a file's kind comes from them, and a recorded link that the host wrote as a file (or not
    at all) is listed as the link. A link that leaves the tree, a device or another special file raises
    HarnessError."""
    root = Path(root)
    rows: List[Tuple[str, str, str]] = []
    executable = set(modes.executable) if modes is not None else set()
    links = dict(modes.links) if modes is not None else {}
    size = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) == root and d == ".git"))
        for name in sorted(filenames) + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(full)
                if os.path.isabs(target) or not (full.parent / target).resolve().is_relative_to(root.resolve()):
                    raise HarnessError(f"{rel}: a link that leaves the harness")
                rows.append((rel, "l", target.replace(os.sep, "/") if modes is not None else target))
            elif stat.S_ISREG(st.st_mode) and rel in links:
                rows.append((rel, "l", links[rel]))
            elif stat.S_ISREG(st.st_mode):
                size += st.st_size
                if size > MAX_BYTES:
                    raise HarnessError(f"the harness is larger than {MAX_BYTES // (1024 * 1024)} MB")
                kind = ("x" if rel in executable else "f") if modes is not None else \
                    ("x" if st.st_mode & 0o111 else "f")
                rows.append((rel, kind, _sha256_file(full)))
            else:
                raise HarnessError(f"{rel}: not a file (a device or another special file)")
            if len(rows) > MAX_FILES:
                raise HarnessError(f"the harness has more than {MAX_FILES} files")
    seen = {r[0] for r in rows}
    for rel, target in links.items():   # a link the host could not write at all
        if rel not in seen and not os.path.lexists(root / rel):
            rows.append((rel, "l", target))
    for rel, kind, target in rows:
        if kind == "l" and rel in links and not _link_inside(rel, target):
            raise HarnessError(f"{rel}: a link that leaves the harness")
    return sorted(rows)


def modes_of(tree: Path) -> Optional[Modes]:
    """The recorded modes of a stored tree (modes.json beside it) where the host keeps no executable bit; None where
    the file system's own modes count (Linux, macOS)."""
    if not NO_EXEC_BIT:
        return None
    return Modes.load(Path(tree).parent / MODES_FILE) or Modes()


def stored_listing(tree: Path) -> List[Tuple[str, str, str]]:
    """tree_listing of a stored tree with its recorded modes where the host needs them."""
    return tree_listing(tree, modes_of(tree))


def tree_sha256(rows: List[Tuple[str, str, str]]) -> str:
    """One checksum over the listing: the same tree gives the same checksum on every computer."""
    text = "".join(f"{kind} {digest} {path}\n" for path, kind, digest in rows)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PACK_FILE = "harness.tar"
WATCH_FILE = "harness_watch.sh"
WATCH_SH = Path(__file__).resolve().parent / WATCH_FILE


def pack(tree: Path, tree_sha: str, *, make: bool = True) -> Tuple[Path, str, int]:
    """The tree packed for the bundle: (path of harness.tar beside the tree, its sha256, its size). The tree is
    listed again first and must still have `tree_sha`; a pack that is there already is reused when its record
    (harness.tar.sha256) still matches its bytes. HarnessError when a path does not fit a plain tar header, or
    (`make` False: an AI's read-only plan) when no pack is there yet."""
    tree = Path(tree)
    rows = stored_listing(tree)
    if tree_sha256(rows) != tree_sha:
        raise HarnessError("the stored tree does not match what was taken in; take the harness in again")
    out = tree.parent / PACK_FILE
    record = tree.parent / (PACK_FILE + ".sha256")
    if out.is_file() and record.is_file():
        known = record.read_text(encoding="ascii", errors="replace").strip()
        if RE_SHA256.fullmatch(known) and _sha256_file(out) == known:
            return out, known, out.stat().st_size
    if not make:
        raise HarnessError("the harness is not packed for a bundle yet; the person's own plan packs it")
    tmp = tree.parent / f".{PACK_FILE}.{os.getpid()}.tmp"
    try:
        with tarfile.open(tmp, "w", format=tarfile.USTAR_FORMAT) as tf:
            for rel, kind, digest in rows:
                info = tarfile.TarInfo(rel)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if kind == "l":
                    info.type, info.linkname, info.mode = tarfile.SYMTYPE, digest, 0o777
                    tf.addfile(info)
                    continue
                path = tree / rel
                info.size = path.stat().st_size
                info.mode = 0o755 if kind == "x" else 0o644
                with open(path, "rb") as fh:
                    tf.addfile(info, fh)
    except ValueError as e:   # a name or a link target longer than a plain tar header takes
        tmp.unlink(missing_ok=True)
        raise HarnessError(f"the harness cannot be packed for the board: {e}") from None
    sha = _sha256_file(tmp)
    os.replace(tmp, out)
    record.write_text(sha + "\n", encoding="ascii")
    return out, sha, out.stat().st_size


def _sha256_file(path: Path) -> str:
    from orchestrator.utils.helpers import sha256_file
    return sha256_file(Path(path))


def _member(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).as_posix().lstrip("/").removeprefix("./")


@dataclass(frozen=True)
class _Found:
    """What an archive gave: the modes it records (or the content rule decided), the members whose mode no record
    gave (`unknown`, decided by the content rule), its format by content, and for a disk image how many bytes were
    read of how many (`read`, `whole`)."""
    modes: Modes
    unknown: Tuple[str, ...] = ()
    format: str = ""
    read: int = 0
    whole: int = 0


class PasswordNeeded(HarnessError):
    """The archive is encrypted: only the person gives its password (the command line asks without echo, the Board
    harness window in a password field); `wrong`: the one given was not right. Never an AI: the take-in without a
    person to ask refuses with this."""

    def __init__(self, wrong: bool = False):
        super().__init__("archive: the password is not right" if wrong else
                         "archive: it is encrypted and needs its password, which only the person gives (the command "
                         "line asks for it, so does the Board harness window); an AI cannot give it")
        self.wrong = wrong


# Only to name the one file of a compressed single file after its archive ("install.sh.gz" -> "install.sh"); the
# format itself always comes from the content.
COMPRESSION_SUFFIXES = (".gz", ".bz2", ".xz", ".zst", ".zstd")
NOT_READ = ("archive: neither an archive or disk image this framework reads (zip, tar, gz, bz2, xz, zst, 7z, rar, "
            "iso, cpio, img) nor a script or a program; the format is read from the file's content, not its name")
# A search for the installer in a disk image's ext file system reads at most this many directories.
MAX_SEARCH_DIRS = 200000


def _set_mode(path: Path, executable: bool) -> None:
    """On a host with an executable bit the file system follows the decided bit (so its listing is the same)."""
    if not NO_EXEC_BIT and not os.path.islink(path):
        os.chmod(path, SHARED_RUN if executable else SHARED_READ)


def _caps(files: int, size: int) -> None:
    if files > MAX_FILES:
        raise HarnessError(f"the harness has more than {MAX_FILES} files")
    if size > MAX_BYTES:
        raise HarnessError(f"the harness is larger than {MAX_BYTES // (1024 * 1024)} MB")


def _room(folder: Path, need: int, state_dir: Path, what: str = "the harness take-in") -> None:
    """The one disk rule for what an extraction writes; HarnessError with its plain reason when it does not fit."""
    from orchestrator.Core.disk_plan import ReservationLedger, refusal, room
    try:
        check = room(folder, need, label="harness take-in", ledger=ReservationLedger(Path(state_dir)))
    except OSError:
        return
    refused = refusal([check], what)
    if refused is not None:
        raise HarnessError(str(refused))


def _materialise(dest: Path, path: str, target: str) -> None:
    """A hard link inside the tree as a regular file of its own with its target's content (the tree listing, the
    checksum and llmcf-install know no hard links)."""
    full, source = dest / path, dest / target
    if not source.is_file() or source.is_symlink():
        raise HarnessError(f"{path}: a hard link to an entry that is not a file")
    full.parent.mkdir(parents=True, exist_ok=True)
    tmp = full.with_name(f".{full.name}.llmcf-copy")
    shutil.copyfile(source, tmp)
    os.replace(tmp, full)


def _extract(archive: Path, dest: Path, spec: HarnessSpec, state_dir: Path, password: Optional[str] = None) -> _Found:
    """Extracts the archive (its format by content) into the fresh folder `dest` and returns its members' modes,
    keyed by their path inside the archive. PasswordNeeded for an encrypted archive without (the right) password."""
    from orchestrator.utils import archives as ar
    from orchestrator.utils.ext4_read import Ext4Error
    from orchestrator.utils.helpers import UnsafeArchive
    try:
        kind = ar.sniff(archive)
    except OSError as e:
        raise HarnessError(f"archive: it cannot be read: {e}") from None
    try:
        if kind == "zip":
            if _zip_encrypted(archive) and ar.find_7zip() is not None:
                return _extract_7zip(archive, dest, spec, "zip", state_dir, password)
            return _extract_zip(archive, dest, password)
        if kind == "tar" or kind in ("gzip", "bzip2", "xz") or (kind == "zstd" and ar.ZSTD_STDLIB):
            if _holds_tar(archive):
                return _extract_tar(archive, dest, kind)
            return _one_file(archive, dest, spec, kind, decompress=True)
        if kind in ar.PROGRAMS:
            return _one_file(archive, dest, spec, kind)
        if kind == "disk":
            return _extract_disk(archive, dest, spec, state_dir, password)
        if kind in ar.SEVEN_ZIP_KINDS:
            return _extract_7zip(archive, dest, spec, kind, state_dir, password)
    except ar.PasswordNeeded as e:
        raise PasswordNeeded(e.wrong) from None
    except (UnsafeArchive, Ext4Error, zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as e:
        raise HarnessError(f"the archive cannot be taken in: {e}") from None
    raise HarnessError(NOT_READ)


def _holds_tar(archive: Path) -> bool:
    """Whether the (maybe compressed) file is a tar archive with at least one member."""
    try:
        with tarfile.open(archive, "r:*") as tf:
            return tf.next() is not None
    except (tarfile.TarError, OSError, EOFError, ValueError):
        return False


def _extract_tar(archive: Path, dest: Path, kind: str) -> _Found:
    """A tar through safe_extract_tar; a hard link only to a regular member inside the tree, materialised as a file
    of its own with its own mode."""
    from orchestrator.utils.helpers import safe_extract_tar
    executable: List[str] = []
    links: Dict[str, str] = {}
    with tarfile.open(archive, "r:*") as tf:
        members = tf.getmembers()
        regular = {_member(m.name): m.size for m in members if m.isfile()}
        for m in members:
            if m.islnk() and _member(m.linkname) not in regular:
                raise HarnessError(f"{_member(m.name)}: a hard link to an entry the archive does not have")
        _caps(sum(1 for m in members if not m.isdir()),
              sum(m.size if m.isfile() else regular.get(_member(m.linkname), 0) for m in members
                  if m.isfile() or m.islnk()))
        dest.mkdir(parents=True, exist_ok=True)
        safe_extract_tar(tf, dest)
        for member in members:
            name = _member(member.name)
            if member.issym():
                links[name] = member.linkname
                continue
            if member.islnk():
                _materialise(dest, name, _member(member.linkname))
                _set_mode(dest / name, bool(member.mode & 0o111))
            if (member.isfile() or member.islnk()) and member.mode & 0o111:
                executable.append(name)
    return _Found(Modes(tuple(executable), links), format="tar" if kind == "tar" else f"tar+{kind}")


def _zip_encrypted(archive: Path) -> bool:
    try:
        with zipfile.ZipFile(archive) as zf:
            return any(i.flag_bits & 0x1 for i in zf.infolist())
    except (zipfile.BadZipFile, OSError):
        return False


def _extract_zip(archive: Path, dest: Path, password: Optional[str] = None) -> _Found:
    """A zip's members, checked first; the executable bit from its attributes, and by the content rule for a member
    that records no unix mode (a zip made on Windows). An encrypted zip without 7-Zip: ZipCrypto through Python with
    the person's password; AES needs 7-Zip."""
    from orchestrator.utils.archives import PasswordNeeded as NeedsPassword
    from orchestrator.utils.archives import is_program, missing_text
    executable: List[str] = []
    unknown: List[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        root = dest.resolve()
        infos = zf.infolist()
        _caps(sum(1 for i in infos if not i.is_dir()), sum(i.file_size for i in infos if not i.is_dir()))
        for info in infos:
            target = (dest / info.filename).resolve()
            if info.filename.startswith(("/", "\\")) or not target.is_relative_to(root):
                raise HarnessError(f"{info.filename}: an archive entry outside the harness")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise HarnessError(f"{info.filename}: a link in a zip archive")
        if any(i.flag_bits & 0x1 for i in infos):
            if any(i.compress_type == 99 for i in infos):   # WinZip AES
                raise HarnessError(missing_text("An AES-encrypted zip archive"))
            if password is None:
                raise NeedsPassword()
            zf.setpassword(password.encode("utf-8"))
        try:
            zf.extractall(dest)  # nosec B202 - every entry checked above
        except RuntimeError as e:   # zipfile's "Bad password"
            if "password" in str(e).lower():
                raise NeedsPassword(wrong=True) from None
            raise
        except zipfile.BadZipFile:
            if password is not None:   # ZipCrypto's one-byte check passed a wrong password, the CRC did not
                raise NeedsPassword(wrong=True) from None
            raise
        for info in infos:   # zip keeps the executable bit in its attributes
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0o777 if info.create_system == 3 else 0   # 3: made on unix
            if mode:
                os.chmod(dest / info.filename, mode & 0o755)
                if mode & 0o111:
                    executable.append(_member(info.filename))
            else:
                unknown.append(_member(info.filename))
                if is_program(dest / info.filename):
                    executable.append(_member(info.filename))
                    _set_mode(dest / info.filename, True)
                else:
                    _set_mode(dest / info.filename, False)
    return _Found(Modes(tuple(executable)), tuple(unknown), format="zip")


def _one_file(archive: Path, dest: Path, spec: HarnessSpec, kind: str, *, decompress: bool = False) -> _Found:
    """A harness of one file: a script or a program (a makeself .bin), or the one file of a gzip, bzip2, xz or zstd
    file that holds no tar. The tree holds that file, executable; the installer is the file itself."""
    from orchestrator.utils.archives import is_program
    name = _one_file_name(archive) if decompress else archive.name
    if not RE_REL.fullmatch(name) or "/" in name or name in (".", ".."):
        raise HarnessError(f"a harness of one file keeps its file's name, and {name!r} is not one the framework "
                           "takes: rename the file to letters, digits, '.', '_' and '-'")
    if spec.installer != name:
        raise HarnessError(f"installer: a harness of one file is that file itself: give the installer {name}")
    if archive.stat().st_size > MAX_BYTES:
        _caps(1, archive.stat().st_size)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    if decompress:
        _decompress(archive, kind, target)
        if not is_program(target):
            raise HarnessError("archive: a compressed file that holds neither a tar archive nor a script or a program")
    else:
        shutil.copyfile(archive, target)
    _set_mode(target, True)
    return _Found(Modes((name,)), (name,), format=f"{kind}+one-file" if decompress else "one-file")


def _decompress(archive: Path, kind: str, target: Path) -> None:
    """The one file of a gzip, bzip2, xz or zstd file into `target`, at most MAX_BYTES."""
    import bz2
    import gzip
    import lzma
    if kind == "zstd":
        from compression import zstd as opener  # type: ignore[import-not-found]
    else:
        opener = {"gzip": gzip, "bzip2": bz2, "xz": lzma}[kind]
    written = 0
    with opener.open(archive, "rb") as src, open(target, "wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            _caps(1, written)
            out.write(chunk)


def _seven(what: str) -> str:
    from orchestrator.utils import archives as ar
    exe = ar.find_7zip()
    if exe is None:
        raise HarnessError(ar.missing_text(what[0].upper() + what[1:]))
    return exe


def _extract_7zip(archive: Path, dest: Path, spec: HarnessSpec, kind: str, state_dir: Path,
                  password: Optional[str] = None, *, listed=None) -> _Found:
    """Through 7-Zip: listed and checked first (utils/archives), the disk rule, then extracted into `dest`. The
    executable bit from 7-Zip's attributes (the unix mode where the archive keeps one), else by the content rule; a
    hard link inside the tree materialised as a file of its own."""
    from orchestrator.utils import archives as ar
    from orchestrator.utils.helpers import UnsafeArchive
    exe = _seven(ar.SEVEN_ZIP_KINDS.get(kind, "this file"))
    if listed is None:
        try:
            listed = ar.list_archive(exe, archive, password)
        except UnsafeArchive as e:
            if kind == "zstd":
                raise HarnessError(f"a zstd-compressed file: {e} (this Python reads zstd from 3.14 on; a newer 7-Zip "
                                   "reads it too)") from None
            raise
    if ar.is_partition_listing(listed):
        raise HarnessError("archive: a disk image inside this file, which is not read this way")
    if spec.image_partition not in (None, 0) and kind != "disk":
        raise HarnessError("image_partition: this file has one file system and no partitions to choose from")
    total = ar.check_caps(listed, max_files=MAX_FILES, max_bytes=MAX_BYTES)
    _room(dest.parent, total, state_dir)
    if kind == "zstd":   # one stream: a tar inside, or the harness's one file
        stream = dest.parent / "zstd-stream"
        ar.extract(exe, archive, stream, listed, password=password)
        files = [e for e in listed if e.kind == "file"]
        if len(files) != 1:
            raise HarnessError("archive: a zstd file that holds more than one stream")
        inner = stream / files[0].path
        if _holds_tar(inner):
            return replace(_extract_tar(inner, dest, "tar"), format="tar+zstd")
        name = _one_file_name(archive)
        if not RE_REL.fullmatch(name) or "/" in name:
            raise HarnessError(f"a harness of one file keeps its file's name, and {name!r} is not one the "
                               "framework takes: rename the file to letters, digits, '.', '_' and '-'")
        if spec.installer != name:
            raise HarnessError(f"installer: a harness of one file is that file itself: give the installer {name}")
        if not ar.is_program(inner):
            raise HarnessError("archive: a compressed file that holds neither a tar archive nor a script or a program")
        dest.mkdir(parents=True, exist_ok=True)
        os.replace(inner, dest / name)
        _set_mode(dest / name, True)
        return _Found(Modes((name,)), (name,), format="zstd+one-file")
    ar.extract(exe, archive, dest, listed, password=password)
    executable: List[str] = []
    links: Dict[str, str] = {}
    unknown: List[str] = []
    for entry in listed:
        full = dest / entry.path
        if entry.kind == "link":
            target = entry.link
            if target is None:   # 7z keeps a link's target as its content
                if os.path.islink(full):
                    target = os.readlink(full)
                else:
                    with open(full, "rb") as fh:
                        target = fh.read(4096).decode("utf-8", errors="replace")
            if not ar.link_inside(entry.path, target):
                raise HarnessError(f"{entry.path}: a link that leaves the harness")
            links[entry.path] = target
        elif entry.kind in ("file", "hardlink"):
            if entry.kind == "hardlink":
                _materialise(dest, entry.path, entry.link or "")
            if entry.mode is not None:
                bit = bool(entry.mode & 0o111)
            else:
                unknown.append(entry.path)
                bit = ar.is_program(full)
            if bit:
                executable.append(entry.path)
            _set_mode(full, bit)
    return _Found(Modes(tuple(executable), links), tuple(unknown), format=kind)


def _one_file_name(archive: Path) -> str:
    name = archive.name
    low = name.lower()
    for suffix in COMPRESSION_SUFFIXES:
        if low.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


def _holds_installer(listed, installer: str) -> bool:
    return any(e.kind == "file" and (e.path == installer or ("/" in e.path and e.path.split("/", 1)[1] == installer))
               for e in listed)


# --- disk images: read, never extracted whole ------------------------------------------------------------------------

def _extract_disk(archive: Path, dest: Path, spec: HarnessSpec, state_dir: Path, password: Optional[str]) -> _Found:
    """A disk image (MBR or GPT, or one file system alone). An ext2/3/4 partition is read in place
    (utils/ext4_read): the installer searched by directory entries only, then only the harness's folder copied out.
    Another file system (FAT, NTFS: small boot partitions as a rule) through 7-Zip, one partition at a time under the
    disk rule with its exact size, and only when no ext partition holds the installer (or it is the one named)."""
    from orchestrator.utils import ext4_read as e4
    whole = archive.stat().st_size
    parts = e4.partitions(archive)
    if not parts:
        if e4.is_ext(archive, 0):
            parts = [e4.Partition(0, 0, whole, "ext")]
        else:   # one FAT or NTFS file system alone: 7-Zip reads it as it is
            found = _extract_7zip(archive, dest, spec, "disk", state_dir, password)
            return replace(found, read=whole, whole=whole)
    if len(parts) > MAX_PARTITIONS:
        raise HarnessError(f"the disk image has more than {MAX_PARTITIONS} partitions")
    if spec.image_partition is not None:
        if spec.image_partition >= len(parts):
            raise HarnessError(f"image_partition: the image has {len(parts)} partitions (0 to {len(parts) - 1})")
        parts = [parts[spec.image_partition]]
    read = 0
    matches: List[Tuple[Any, str]] = []
    for part in parts:
        if not e4.is_ext(archive, part.offset):
            continue
        with e4.Ext4(archive, part.offset, part.size) as fs:
            for root in _ext_roots(fs, spec, part.number):
                matches.append((part, root))
            read += fs.read_bytes
    if len(matches) > 1:
        where = "; ".join(f"partition {p.number}, folder {r or '/'}" for p, r in matches[:5])
        raise HarnessError(f"the disk image holds {spec.installer} more than once ({where}); name the partition "
                           "(image_partition) and the folder (image_path) to take")
    if matches:
        part, root = matches[0]
        with e4.Ext4(archive, part.offset, part.size) as fs:
            found = _ext_tree(fs, root, dest, state_dir)
            read += fs.read_bytes
        return replace(found, read=read, whole=whole, format="disk:ext")
    others = [p for p in parts if not e4.is_ext(archive, p.offset)]
    if not others:
        raise HarnessError(f"no partition of the disk image holds {spec.installer} (it has "
                           f"{len(e4.partitions(archive)) or 1})")
    return _seven_partition(archive, dest, spec, state_dir, password, others, read, whole)


def _ext_roots(fs, spec: HarnessSpec, number: int) -> List[str]:
    """The folders of the ext file system that hold the installer: the spec's image_path (checked), else found by
    reading directory entries only, never a file's data."""
    from orchestrator.utils.ext4_read import Ext4Error
    if spec.image_path:
        try:
            folder = fs.lookup(spec.image_path)
            inst = fs.lookup(f"{spec.image_path}/{spec.installer}")
        except Ext4Error:
            if spec.image_partition is not None:
                raise HarnessError(f"image_path: {spec.image_path}/{spec.installer} is not a file in partition "
                                   f"{number} of the disk image") from None
            return []
        if folder.kind != "dir" or inst.kind != "file":
            raise HarnessError(f"image_path: {spec.image_path}/{spec.installer} is not a file in partition {number}")
        return [spec.image_path]
    first = spec.installer.split("/", 1)[0]
    found: List[str] = []
    queue: List[Tuple[str, int]] = [("", 2)]
    seen = set()
    while queue and len(found) < 5:
        path, number_ino = queue.pop(0)
        if number_ino in seen:
            continue
        seen.add(number_ino)
        if len(seen) > MAX_SEARCH_DIRS:
            raise HarnessError(f"the search for {spec.installer} read {MAX_SEARCH_DIRS} folders of partition {number}"
                               " without an end; name the folder (image_path)")
        entries = fs.entries(fs.inode(number_ino))
        names = {name for name, _n, _t in entries}
        if first in names:
            try:
                if fs.lookup(f"{path}/{spec.installer}" if path else spec.installer).kind == "file":
                    found.append(path)
            except Ext4Error:
                pass
        for name, n, ftype in sorted(entries):
            if ftype == 2 or (ftype == 0 and fs.inode(n).kind == "dir"):
                queue.append((f"{path}/{name}" if path else name, n))
    return found


def _ext_tree(fs, root: str, dest: Path, state_dir: Path) -> _Found:
    """The folder `root` of the ext file system copied into `dest`: first listed (kinds, sizes, links checked, special
    files refused, the caps), then the disk rule, then only these files' data read. Modes from the inodes."""
    from orchestrator.utils.archives import link_inside
    from orchestrator.utils.ext4_read import Ext4Error
    plan: List[Tuple[str, Any]] = []
    links: Dict[str, str] = {}
    total = 0
    stack: List[Tuple[str, Any]] = [("", fs.lookup(root) if root else fs.inode(2))]
    seen = set()
    while stack:
        rel, ino = stack.pop()
        if ino.number in seen:
            raise HarnessError(f"{rel}: a folder reached twice (a damaged or crafted image)")
        seen.add(ino.number)
        for name, number, _ftype in sorted(fs.entries(ino)):
            if "/" in name or "\\" in name or "\0" in name:
                raise HarnessError(f"{rel}/{name}: a name this framework does not take")
            path = f"{rel}/{name}" if rel else name
            if not rel and name == "lost+found" and not root:
                continue
            child = fs.inode(number)
            if child.kind == "dir":
                stack.append((path, child))
            elif child.kind == "link":
                target = fs.link_target(child)
                if not link_inside(path, target):
                    raise HarnessError(f"{path}: a link that leaves the harness")
                links[path] = target
            elif child.kind == "file":
                plan.append((path, child))
                total += child.size
            else:
                raise HarnessError(f"{path}: not a file (a device or another special file)")
            _caps(len(plan) + len(links), total)
    _room(dest.parent, total, state_dir)
    dest.mkdir(parents=True, exist_ok=True)
    executable: List[str] = []
    try:
        for path, ino in plan:
            full = dest / path
            full.parent.mkdir(parents=True, exist_ok=True)
            with open(full, "wb") as out:
                fs.read_data(ino, out, limit=MAX_BYTES)
            bit = bool(ino.mode & 0o111)
            if bit:
                executable.append(path)
            _set_mode(full, bit)
    except Ext4Error as e:
        raise HarnessError(f"the disk image cannot be read: {e}") from None
    if not NO_EXEC_BIT:
        for path, target in links.items():
            (dest / path).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, dest / path)
    else:   # Windows: the links stay recorded (modes.json), tree_listing lists them
        for path in links:
            (dest / path).parent.mkdir(parents=True, exist_ok=True)
    return _Found(Modes(tuple(executable), links))


def _seven_partition(archive: Path, dest: Path, spec: HarnessSpec, state_dir: Path, password: Optional[str],
                     others, read: int, whole: int) -> _Found:
    """A FAT, NTFS or other partition through 7-Zip: the disk rule with its exact size, then that one partition
    extracted (7-Zip cannot read a partition from a stream) and listed; the one that holds the installer is taken."""
    from orchestrator.utils import archives as ar
    from orchestrator.utils.atomic import remove_tree
    from orchestrator.utils.helpers import UnsafeArchive
    from orchestrator.utils.sizes import format_size
    exe = _seven("a disk image's FAT or NTFS partition")
    listed = ar.list_archive(exe, archive, password)
    by_offset = {e.fields.get("Offset"): e for e in listed} if ar.is_partition_listing(listed) else {}
    chosen = None
    unread: List[str] = []
    for part in others:
        entry = by_offset.get(str(part.offset))
        if entry is None:
            if len(others) == 1 and not by_offset:   # 7-Zip opened the one partition itself
                found = _extract_7zip(archive, dest, spec, "disk", state_dir, password, listed=listed)
                return replace(found, read=read + part.size, whole=whole)
            unread.append(f"{part.number} (7-Zip does not list it)")
            continue
        _room(dest.parent, part.size, state_dir,
              f"reading partition {part.number} of the disk image ({format_size(part.size, 'en')})")
        folder = dest.parent / f"partition-{part.number}"
        ar.extract(exe, archive, folder, listed, only=[entry.raw], password=password)
        read += part.size
        try:
            inner = ar.list_archive(exe, folder / entry.path)
        except UnsafeArchive as e:
            if spec.image_partition is not None:
                raise HarnessError(f"partition {part.number} of the disk image cannot be taken in: {e}") from None
            unread.append(f"{part.number} ({e})")
            inner = None
        if inner is not None and (spec.image_partition is not None or _holds_installer(inner, spec.installer)):
            if chosen is not None:
                raise HarnessError(f"image_partition: partitions {chosen[0]} and {part.number} of the disk image "
                                   f"both hold {spec.installer}; name the one to take (its number, 0 the first)")
            chosen = (part.number, folder / entry.path, inner)
        else:
            remove_tree(folder, label="a partition of the disk image")
    if chosen is None:
        more = f"; partitions that could not be read: {', '.join(unread)}" if unread else ""
        raise HarnessError(f"no partition of the disk image holds {spec.installer}{more}")
    found = _extract_7zip(chosen[1], dest, spec, "disk", state_dir, password, listed=chosen[2])
    return replace(found, read=read, whole=whole, format="disk")


def _below(modes: Modes, top: str) -> Modes:
    """The modes of the entries below `top` ("" the archive's top), keyed relative to it."""
    if not top:
        return modes
    cut = top.rstrip("/") + "/"
    return Modes(tuple(p[len(cut):] for p in modes.executable if p.startswith(cut)),
                 {p[len(cut):]: t for p, t in modes.links.items() if p.startswith(cut)})


def _git_modes(git, folder: Path, args: List[str]) -> Modes:
    """The modes git records for a checkout's files (`ls-tree -r -z HEAD` or `ls-files --stage -z`): 100755 an
    executable file, 120000 a link, its target read from the checkout (a link, or the file Windows wrote instead)."""
    from orchestrator.runtime.build.source import SourceError
    try:
        out = git(args, cwd=folder).stdout
    except SourceError as e:
        raise HarnessError(f"the modes of the harness's files cannot be read from git: {e}") from None
    executable: List[str] = []
    links: Dict[str, str] = {}
    for entry in (out or "").split("\0"):
        head, _, path = entry.partition("\t")
        if not path:
            continue
        mode = head.split(" ", 1)[0]
        if mode == "100755":
            executable.append(path)
        elif mode == "120000":
            full = Path(folder) / path
            try:
                links[path] = os.readlink(full) if os.path.islink(full) else full.read_text(encoding="utf-8")
            except OSError:
                raise HarnessError(f"{path}: a link in the repository that cannot be read") from None
    return Modes(tuple(executable), links)


def _single_top(folder: Path, installer: str) -> Path:
    """An archive that holds one folder around the harness (as GitHub's archives do: `repo-<commit>/`) is taken from
    inside it, but only when the installer is found there and not already at the archive's top."""
    if (folder / installer).is_file():
        return folder
    entries = [p for p in folder.iterdir()]
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink() and (entries[0] / installer).is_file():
        return entries[0]
    return folder


def _fetch_git(spec: HarnessSpec, staging: Path, git, offline: bool) -> str:
    from orchestrator.runtime.build.source import SourceError
    try:
        git(["init", "--quiet", "--template=", str(staging)])
        if spec.pin:
            want = spec.pin
        else:
            ref = spec.ref or "HEAD"
            out = git(["ls-remote", spec.location, ref], timeout=120, offline=offline).stdout.split()
            if not out or not RE_COMMIT.fullmatch(out[0]):
                raise HarnessError(f"{spec.location} has no branch or tag {ref}")
            want = out[0]
        git(["fetch", "--quiet", "--depth", "1", spec.location, want], cwd=staging, timeout=600, offline=offline)
        git(["checkout", "--quiet", "--detach", want], cwd=staging)
        head = git(["rev-parse", "HEAD"], cwd=staging).stdout.strip()
    except SourceError as e:
        raise HarnessError(f"the repository cannot be taken in: {e}") from None
    if head != want:
        raise HarnessError(f"the repository gave commit {head[:12]}, not {want[:12]}")
    return want


def take_in(spec: HarnessSpec, state_dir: Path, *, git=None, offline: bool = False,
            password: Optional[str] = None) -> TakenIn:
    """Takes the harness in at its pin and stores its tree; HarnessError says why it cannot. A tree with the same
    checksum that is already stored is reused. An encrypted archive raises PasswordNeeded until the person's
    `password` is given (the caller asks the person, at most MAX_PASSWORD_TRIES times; it is never kept)."""
    check_spec(spec)
    root = Path(state_dir) / STORE_DIR / spec.id
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{os.getpid()}-{os.urandom(4).hex()}"
    staging.mkdir()
    derived: Tuple[str, ...] = ()
    content_rule = False
    fmt = ""
    image_read = image_size = 0
    try:
        if spec.kind == "git":
            if git is None:
                from orchestrator.runtime.build.source import Git
                git = Git()
            pin = _fetch_git(spec, staging, git, offline)
            modes = _git_modes(git, staging, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"])
            from orchestrator.utils.atomic import remove_tree
            if remove_tree(staging / ".git", label="the harness checkout's git folder"):
                raise HarnessError("the git folder of the harness's checkout could not be removed")
            tree = staging
        elif spec.kind == "archive":
            archive = Path(spec.location)
            if not archive.is_file():
                raise HarnessError("archive: the file is not there")
            pin = _sha256_file(archive)
            if spec.pin and pin != spec.pin:
                raise HarnessError("archive: its sha256 is not the one given")
            quarantine = staging / "q"
            found = _extract(archive, quarantine, spec, Path(state_dir), password)
            image_read, image_size = found.read, found.whole
            fmt = found.format
            tree = _single_top(quarantine, spec.installer)
            top = tree.relative_to(quarantine).as_posix() if tree != quarantine else ""
            modes = _below(found.modes, top)
            unknown = set(_below(Modes(found.unknown), top).executable)
            if spec.installer in unknown and spec.installer not in modes.executable:
                modes = Modes(modes.executable + (spec.installer,), modes.links)
                if (tree / spec.installer).is_file():
                    _set_mode(tree / spec.installer, True)
            content_rule = bool(unknown)
            derived = tuple(sorted(p for p in modes.executable if p in unknown))
        else:
            folder = Path(spec.location)
            if not folder.is_dir():
                raise HarnessError("folder: it is not there")
            shutil.copytree(folder, staging / "t", symlinks=True, ignore=shutil.ignore_patterns(".git"))
            tree = staging / "t"
            modes = None
            if NO_EXEC_BIT:
                if (folder / ".git").exists():
                    if git is None:
                        from orchestrator.runtime.build.source import Git
                        git = Git()
                    modes = _git_modes(git, folder, ["ls-files", "--stage", "-z"])
                else:   # a plain folder: the content rule
                    modes, derived = content_modes(tree, spec.installer)
                    content_rule = True
            pin = ""
        used = modes if NO_EXEC_BIT else None
        rows = tree_listing(tree, used)
        installer = next((r for r in rows if r[0] == spec.installer), None)
        if installer is None or installer[1] == "l":
            raise HarnessError(f"installer: {spec.installer} is not a file in the harness")
        digest = tree_sha256(rows)
        pin = pin or digest
        size = sum((tree / r[0]).stat().st_size for r in rows if r[1] != "l")
        final = root / digest
        if not (final / TREE_DIR).is_dir():
            final.mkdir(exist_ok=True)
            os.replace(tree, final / TREE_DIR)
        if modes is not None:
            (final / MODES_FILE).write_text(json.dumps(modes.record(), indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
        if tree_sha256(tree_listing(final / TREE_DIR, used)) != digest:
            raise HarnessError("the stored tree does not match what was taken in")
        taken = TakenIn(spec=spec, pin=pin, tree_sha256=digest, files=len(rows), size=size, path=final / TREE_DIR,
                        derived=derived, content_rule=content_rule, format=fmt, image_read=image_read,
                        image_size=image_size)
        (final / SPEC_FILE).write_text(json.dumps(taken.record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return taken
    finally:
        from orchestrator.utils.atomic import remove_tree
        remove_tree(staging, label="the harness's staging folder")


DERIVED_SHOWN = 10
MAX_PASSWORD_TRIES = 3


def image_text(taken: TakenIn, lang: Optional[str] = "en") -> str:
    """The person's line for a disk image: how much was read of it ("" for anything else)."""
    if not taken.image_size:
        return ""
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.sizes import format_size
    return ui_text("harness.take.image_read", lang=lang, read=format_size(taken.image_read, lang),
                   size=format_size(taken.image_size, lang))


def derived_text(taken: TakenIn, lang: str = "en") -> str:
    """The person's line when the executable bits were derived from the content: how many files, and their names (at
    most DERIVED_SHOWN). An AI's view (harness.json) has the count only."""
    from orchestrator.utils.localization import ui_text
    names = ", ".join(taken.derived[:DERIVED_SHOWN]) + (", ..." if len(taken.derived) > DERIVED_SHOWN else "")
    return ui_text("harness.take.derived", lang=lang, count=len(taken.derived), names=names or "-")


def stored(state_dir: Path, harness_id: str) -> List[dict]:
    """The records of the harness's stored trees (harness.json), newest first."""
    root = Path(state_dir) / STORE_DIR / harness_id
    if not RE_ID.fullmatch(harness_id or "") or not root.is_dir():
        return []
    found = []
    for spec_file in root.glob(f"*/{SPEC_FILE}"):
        try:
            found.append((spec_file.stat().st_mtime, json.loads(spec_file.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return [record for _, record in sorted(found, key=lambda x: x[0], reverse=True)]


CHIMERA_EXAMPLE = HarnessSpec(
    id="chimera", title="Chimera", kind="git", location="https://github.com/Smilez1985/chimera",
    ref="entwicklung", installer="installer/chimera-install", dry_run_args=("--dry-run",),
    assume_yes="CHIMERA_ASSUME_YES=1", exit_codes={0: "done", 1: "findings"})
