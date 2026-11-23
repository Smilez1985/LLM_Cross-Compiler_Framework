#!/usr/bin/env python3
"""
Every pinned version of the repository in one list, offline (ROADMAP "Step 7", DESIGN "Installer, platforms and
dependencies": the freshness check is offline by default, online only on request).

The places, read from the files that pin them, never from a list kept beside them:
- base-image: every FROM of every Dockerfile (an ARG default when the FROM names one; a FROM of another stage is
  skipped; a FROM whose ARG has no default is set by the framework at build time, from the catalogue's build_bases);
- action: every `uses: owner/repo@<sha> # vX.Y.Z` of .github/workflows (pinned when the ref is a full commit SHA);
- tool: every `<NAME>_VERSION: "x.y.z"` and `cargo install ... --version =x.y.z` of .github/workflows;
- rust-toolchain: the channel of rust/rust-toolchain.toml;
- source-pin: the llama.cpp and whisper.cpp pins of configs/llama_runtime.yml;
- cuda-toolkit: the newest CUDA toolkit of the catalogue;
- build-base: every builder base of the catalogue.

Python's dependencies are not listed here: scripts/fresh_deps.py and .github/workflows/deps-update.yml keep them on the
newest compatible versions already, and uv.lock is their list.

What can be said offline is what the repository itself knows: a Debian or Ubuntu base image older than the newest base
of the same distribution in the catalogue's build_bases (`newer_in_catalogue`). Whether a newer release exists upstream
needs the network and is a later step (ROADMAP "Step 7"). scripts/versions_report.py prints the list; Mojo's check
`versions` reports the bases of the person's own targets that the catalogue knows a newer one of.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog, version_tuple

REPO_ROOT = Path(__file__).resolve().parents[3]

KINDS = ("base-image", "action", "tool", "rust-toolchain", "source-pin", "cuda-toolkit", "build-base")

_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.IGNORECASE | re.MULTILINE)
_ARG = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", re.MULTILINE)
_ARG_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_USES = re.compile(r"^\s*-?\s*uses:\s*([^@\s]+)@(\S+)(?:\s*#\s*(\S+))?", re.MULTILINE)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TOOL_ENV = re.compile(r"^\s*([A-Z][A-Z0-9_]*)_VERSION:\s*\"?([0-9][\w.+-]*)\"?\s*$", re.MULTILINE)
_CARGO_INSTALL = re.compile(r"cargo install\b[^\n]*?\s([a-z][a-z0-9_-]+)\s+--version\s+=?([0-9][\w.+-]*)")
_CHANNEL = re.compile(r"^\s*channel\s*=\s*\"([^\"]+)\"", re.MULTILINE)
_DEBIAN_CODENAMES = {"buster": 10, "bullseye": 11, "bookworm": 12, "trixie": 13, "forky": 14, "duke": 15}
_SKIP_DIRS = {".git", "tests", "node_modules", "__pycache__", ".venv", "venv"}


@dataclass(frozen=True)
class PinnedVersion:
    path: str               # relative to the root, with / separators
    what: str               # the place in the file (an image, an action, a tool name)
    kind: str               # one of KINDS
    version: str            # the version the place names (a tag, a version, a channel)
    pinned: bool            # pinned exactly: a digest, a full commit SHA or an exact version
    newer_in_catalogue: Optional[str] = None   # the id of a newer base of the same distribution in build_bases
    note: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def dockerfiles(root: Path) -> List[Path]:
    """Every Dockerfile under `root` (Dockerfile, dockerfile, Dockerfile.<variant>, <name>.Dockerfile), the tests'
    fixtures and hidden folders left out; sorted, so the list is the same on every system."""
    found = []
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if any(p in _SKIP_DIRS or p.startswith(".") for p in parts[:-1]) or not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith(".dockerignore"):
            continue
        if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
            found.append(path)
    return sorted(found, key=lambda p: _rel(p, root))


def split_image(ref: str) -> Tuple[str, str, str]:
    """'debian:13-slim@sha256:ab..' -> ('debian', '13-slim', 'ab..'); a missing tag is 'latest', a missing digest ''."""
    name, _, digest = ref.partition("@sha256:")
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = name.rsplit(":", 1)
    else:
        repo, tag = name, "latest"
    return repo, tag, digest


def distribution_release(ref: str) -> Optional[Tuple[str, Tuple[int, ...]]]:
    """The distribution and release of a Debian or Ubuntu image ('debian', (12,)) from its tag; None for another
    image or a tag without a release (latest, stable)."""
    repo, tag, _ = split_image(ref)
    base = repo.rsplit("/", 1)[-1]
    if repo not in ("debian", "ubuntu", "library/debian", "library/ubuntu", "docker.io/library/debian",
                    "docker.io/library/ubuntu"):
        return None
    head = tag.split("-", 1)[0]
    if base == "debian":
        if head in _DEBIAN_CODENAMES:
            return "debian", (_DEBIAN_CODENAMES[head],)
        if re.fullmatch(r"\d{1,2}(\.\d+)?", head):
            return "debian", (int(head.split(".")[0]),)
        return None
    if re.fullmatch(r"\d{2}\.\d{2}", head):
        return "ubuntu", version_tuple(head)
    return None


def _catalogue_releases(catalog: RuntimeCatalog) -> Dict[str, Tuple[Tuple[int, ...], str]]:
    """The newest base of each distribution in build_bases: {'debian': ((13,), 'debian-13'), ...}."""
    newest: Dict[str, Tuple[Tuple[int, ...], str]] = {}
    for base in catalog.build_bases:
        found = distribution_release(base.image)
        if found is None:
            continue
        dist, release = found
        if dist not in newest or release > newest[dist][0]:
            newest[dist] = (release, base.id)
    return newest


def newer_base(ref: str, catalog: RuntimeCatalog) -> Optional[str]:
    """The id of the catalogue's newest base of the same distribution when it is newer than `ref`, else None."""
    found = distribution_release(ref)
    if found is None:
        return None
    dist, release = found
    newest = _catalogue_releases(catalog).get(dist)
    return newest[1] if newest and newest[0] > release else None


def _base_images(root: Path, catalog: RuntimeCatalog) -> Iterable[PinnedVersion]:
    for path in dockerfiles(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        args = dict(_ARG.findall(text))
        stages = set()
        for ref, stage in _FROM.findall(text):
            known_stage = ref in stages
            if stage:
                stages.add(stage)
            if known_stage:   # FROM another stage of the same file: no image of its own
                continue
            arg = _ARG_REF.fullmatch(ref)
            note = ""
            if arg:
                default = args.get(arg.group(1))
                if default is None:
                    yield PinnedVersion(_rel(path, root), ref, "base-image", "", False,
                                        note="set at build time by the framework (the catalogue's build_bases)")
                    continue
                ref, note = default, f"the default of ARG {arg.group(1)}"
            repo, tag, digest = split_image(ref)
            yield PinnedVersion(_rel(path, root), f"{repo}:{tag}", "base-image", tag, bool(digest),
                                newer_base(ref, catalog), note)


def _workflow_files(root: Path) -> List[Path]:
    folder = root / ".github" / "workflows"
    return sorted(folder.glob("*.yml")) + sorted(folder.glob("*.yaml")) if folder.is_dir() else []


def _actions_and_tools(root: Path) -> Iterable[PinnedVersion]:
    for path in _workflow_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = _rel(path, root)
        seen = set()
        for action, ref, comment in _USES.findall(text):
            if action.startswith("./") or (action, ref) in seen:
                continue
            seen.add((action, ref))
            yield PinnedVersion(rel, action, "action", comment or ref, bool(_SHA.fullmatch(ref)),
                                note="" if _SHA.fullmatch(ref) else "not pinned by a full commit SHA")
        for name, version in _TOOL_ENV.findall(text):
            yield PinnedVersion(rel, name.lower(), "tool", version, True)
        for name, version in _CARGO_INSTALL.findall(text):
            yield PinnedVersion(rel, name, "tool", version, True)


def _rust_toolchain(root: Path) -> Iterable[PinnedVersion]:
    path = root / "rust" / "rust-toolchain.toml"
    if path.is_file():
        found = _CHANNEL.search(path.read_text(encoding="utf-8"))
        if found:
            channel = found.group(1)
            yield PinnedVersion(_rel(path, root), "channel", "rust-toolchain", channel,
                                bool(re.fullmatch(r"\d+\.\d+\.\d+", channel)))


def _catalogue(catalog: RuntimeCatalog) -> Iterable[PinnedVersion]:
    path = "configs/llama_runtime.yml"
    source = catalog.llama_cpp.source
    if source is not None:
        yield PinnedVersion(path, "llama_cpp.source", "source-pin", source.tag, True, note=f"commit {source.commit}")
    if catalog.whisper_cpp is not None:
        yield PinnedVersion(path, "whisper_cpp.source", "source-pin", catalog.whisper_cpp.source.tag, True,
                            note=f"commit {catalog.whisper_cpp.source.commit}")
    if catalog.cuda is not None and catalog.cuda.toolkits:
        newest = max(catalog.cuda.toolkits, key=lambda t: version_tuple(t.version))
        yield PinnedVersion(path, "cuda.toolkits (the newest)", "cuda-toolkit", newest.version, True,
                            note=f"manifest {newest.manifest.name}")
    for base in catalog.build_bases:
        _, tag, digest = split_image(base.image)
        # The bases are a set on purpose (one per C library version a machine may have): none is "behind" another.
        yield PinnedVersion(path, f"build_bases.{base.id}", "build-base", tag, bool(digest),
                            note="opt-in" if base.opt_in else "")


def inventory(root: Path = REPO_ROOT, catalog: Optional[RuntimeCatalog] = None) -> List[PinnedVersion]:
    """Every pinned version under `root`, offline, in the order of KINDS, then by file, and within a file in the
    order the file names them (a stable sort)."""
    catalog = catalog or load_catalog(root / "configs" / "llama_runtime.yml")
    places = [*_base_images(root, catalog), *_actions_and_tools(root), *_rust_toolchain(root), *_catalogue(catalog)]
    return sorted(places, key=lambda p: (KINDS.index(p.kind), p.path))
