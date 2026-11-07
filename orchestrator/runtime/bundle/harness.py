#!/usr/bin/env python3
"""
The person's harness for the board (ROADMAP 3.4.0 "An agent harness on the board as an opt-in bundle component";
DESIGN "A harness on the board is the person's opt-in"): a harness is its own project with its own repository, never
code in llmcf, and llmcf does not build it. The person names it as a git repository link, an archive (zip, tar.gz)
or a folder; llmcf takes it in here, on the host, at a pin (the commit of a repository, the sha256 of an archive)
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
entry's external attributes) or, for a folder, its git index (`git ls-files --stage`); tree_listing and pack use them
there instead of os.stat, so the same harness gives the same tree checksum and the same pack on Windows and Linux. A
folder that is no git checkout has no mode source on Windows and is refused (take it in by its git link or as an
archive). On Linux and macOS the file system's own modes count, as before.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

SCHEMA = "llmcf-harness/1"
KINDS = ("git", "archive", "folder")
OUTCOMES = ("done", "findings")
STORE_DIR = "harnesses"
TREE_DIR = "tree"
SPEC_FILE = "harness.json"
MODES_FILE = "modes.json"
# The host's file system keeps no executable bit (Windows): the source's recorded modes count instead.
NO_EXEC_BIT = os.name == "nt"
FOLDER_NO_MODES = ("a folder taken in on Windows has no executable bits to read (Windows keeps none), and it is not a "
                   "git checkout whose index has them; take the harness in by its git link or as an archive (zip or "
                   "tar keep the bits)")

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
    """A harness taken in: the spec, the resolved pin, the tree's checksum and size, and where it lies."""
    spec: HarnessSpec
    pin: str
    tree_sha256: str
    files: int
    size: int
    path: Path

    def record(self) -> dict:
        spec = asdict(self.spec)
        spec["dry_run_args"] = list(self.spec.dry_run_args)
        spec["exit_codes"] = {str(k): v for k, v in sorted(self.spec.exit_codes.items())}
        return {"schema": SCHEMA, "spec": spec, "pin": self.pin, "tree_sha256": self.tree_sha256,
                "files": self.files, "size": self.size}


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


def _extract(archive: Path, dest: Path) -> Modes:
    """Extracts the archive into `dest` and returns its members' modes (the executable files and the links), keyed by
    their path inside the archive."""
    from orchestrator.utils.helpers import UnsafeArchive, safe_extract_tar
    name = archive.name.lower()
    executable: List[str] = []
    links: Dict[str, str] = {}
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                root = dest.resolve()
                for info in zf.infolist():
                    target = (dest / info.filename).resolve()
                    if info.filename.startswith(("/", "\\")) or not target.is_relative_to(root):
                        raise HarnessError(f"{info.filename}: an archive entry outside the harness")
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise HarnessError(f"{info.filename}: a link in a zip archive")
                zf.extractall(dest)  # nosec B202 - every entry checked above
                for info in zf.infolist():   # zip keeps the executable bit in its attributes
                    mode = (info.external_attr >> 16) & 0o777
                    if mode and not info.is_dir():
                        os.chmod(dest / info.filename, mode & 0o755)
                        if mode & 0o111:
                            executable.append(_member(info.filename))
        elif name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(archive, "r:*") as tf:
                safe_extract_tar(tf, dest)
                for member in tf.getmembers():
                    if member.issym():
                        links[_member(member.name)] = member.linkname
                    elif (member.isfile() or member.islnk()) and member.mode & 0o111:
                        executable.append(_member(member.name))
        else:
            raise HarnessError("archive: a .zip, .tar.gz, .tgz or .tar file")
    except (UnsafeArchive, zipfile.BadZipFile, tarfile.TarError) as e:
        raise HarnessError(f"the archive cannot be taken in: {e}") from None
    return Modes(tuple(executable), links)


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


def take_in(spec: HarnessSpec, state_dir: Path, *, git=None, offline: bool = False) -> TakenIn:
    """Takes the harness in at its pin and stores its tree; HarnessError says why it cannot. A tree with the same
    checksum that is already stored is reused."""
    check_spec(spec)
    root = Path(state_dir) / STORE_DIR / spec.id
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{os.getpid()}-{os.urandom(4).hex()}"
    staging.mkdir()
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
            found = _extract(archive, staging)
            tree = _single_top(staging, spec.installer)
            modes = _below(found, tree.relative_to(staging).as_posix() if tree != staging else "")
        else:
            folder = Path(spec.location)
            if not folder.is_dir():
                raise HarnessError("folder: it is not there")
            modes = None
            if NO_EXEC_BIT:
                if not (folder / ".git").exists():
                    raise HarnessError(FOLDER_NO_MODES)
                if git is None:
                    from orchestrator.runtime.build.source import Git
                    git = Git()
                modes = _git_modes(git, folder, ["ls-files", "--stage", "-z"])
            shutil.copytree(folder, staging / "t", symlinks=True, ignore=shutil.ignore_patterns(".git"))
            tree = staging / "t"
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
        taken = TakenIn(spec=spec, pin=pin, tree_sha256=digest, files=len(rows), size=size, path=final / TREE_DIR)
        (final / SPEC_FILE).write_text(json.dumps(taken.record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return taken
    finally:
        from orchestrator.utils.atomic import remove_tree
        remove_tree(staging, label="the harness's staging folder")


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
