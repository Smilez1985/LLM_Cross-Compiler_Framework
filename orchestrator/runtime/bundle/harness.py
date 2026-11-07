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
reused. Nothing here runs the installer: that happens on the board, watched (the next step of the roadmap row).
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


def tree_listing(root: Path) -> List[Tuple[str, str, str]]:
    """(relative path, kind, digest) for every entry below `root`, sorted, `.git` left out: kind "x" an executable
    file, "f" another file, "l" a link (its target as digest). A link that leaves the tree, a device or another
    special file raises HarnessError."""
    root = Path(root)
    rows: List[Tuple[str, str, str]] = []
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
                rows.append((rel, "l", target))
            elif stat.S_ISREG(st.st_mode):
                size += st.st_size
                if size > MAX_BYTES:
                    raise HarnessError(f"the harness is larger than {MAX_BYTES // (1024 * 1024)} MB")
                digest = hashlib.sha256()
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
                rows.append((rel, "x" if st.st_mode & 0o111 else "f", digest.hexdigest()))
            else:
                raise HarnessError(f"{rel}: not a file (a device or another special file)")
            if len(rows) > MAX_FILES:
                raise HarnessError(f"the harness has more than {MAX_FILES} files")
    return sorted(rows)


def tree_sha256(rows: List[Tuple[str, str, str]]) -> str:
    """One checksum over the listing: the same tree gives the same checksum on every computer."""
    text = "".join(f"{kind} {digest} {path}\n" for path, kind, digest in rows)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract(archive: Path, dest: Path) -> None:
    from orchestrator.utils.helpers import UnsafeArchive, safe_extract_tar
    name = archive.name.lower()
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
        elif name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(archive, "r:*") as tf:
                safe_extract_tar(tf, dest)
        else:
            raise HarnessError("archive: a .zip, .tar.gz, .tgz or .tar file")
    except (UnsafeArchive, zipfile.BadZipFile, tarfile.TarError) as e:
        raise HarnessError(f"the archive cannot be taken in: {e}") from None


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
            shutil.rmtree(staging / ".git")
            tree = staging
        elif spec.kind == "archive":
            archive = Path(spec.location)
            if not archive.is_file():
                raise HarnessError("archive: the file is not there")
            pin = _sha256_file(archive)
            if spec.pin and pin != spec.pin:
                raise HarnessError("archive: its sha256 is not the one given")
            _extract(archive, staging)
            tree = _single_top(staging, spec.installer)
        else:
            folder = Path(spec.location)
            if not folder.is_dir():
                raise HarnessError("folder: it is not there")
            shutil.copytree(folder, staging / "t", symlinks=True, ignore=shutil.ignore_patterns(".git"))
            tree = staging / "t"
            pin = ""
        rows = tree_listing(tree)
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
        if tree_sha256(tree_listing(final / TREE_DIR)) != digest:
            raise HarnessError("the stored tree does not match what was taken in")
        taken = TakenIn(spec=spec, pin=pin, tree_sha256=digest, files=len(rows), size=size, path=final / TREE_DIR)
        (final / SPEC_FILE).write_text(json.dumps(taken.record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return taken
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
