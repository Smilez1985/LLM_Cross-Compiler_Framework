#!/usr/bin/env python3
"""
The dependencies of a target as data (docs/DESIGN.md, "Dependencies are data in the wizard").

A module's target.yml lists what its SDK and toolchain need under `template_variables.dependencies`
(schemas/target_template.py `Dependency`). This module reads that list for the person and for the module generator:

- `statuses` answers "is it there?" for each entry: the entry's own check, a program and its arguments run without a
  shell (`HostRunner` on this computer, `ImageRunner` in a build image), present when it exits with 0; a vendor file
  waits for the person's own yes (the guided fetch, Core/vendor_files.py) unless the vendor store has it; a driver or
  firmware of the board is shown and left to the pre-flight check, never installed; an entry without a check is
  unchecked, and an entry without a source is a named gap;
- `install_plan` says what the module's build image installs: a system package as a pinned apt package, a pip package
  as a hash-checked wheel (`pip install --require-hashes --no-deps`, so every package of a tree is its own entry); a
  vendor file, a base image and a driver never become a step. An apt or pip step that finds the package already there
  does nothing, which is the "if it is not there" of the design.

`dockerfile_gaps` serves Mojo: the packages of the list that a module's Dockerfile does not name pinned (the steps
themselves are written by Mojo Miracle's repair, Core/mojo/repairs/dependencies.py).

Nothing here installs anything on the person's computer: the steps are lines of the build image's Dockerfile.
"""

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from orchestrator.schemas.target_template import CHECK_PROGRAMS, Dependency

STATES = ("present", "missing", "waiting", "board", "unchecked")
STATE_WORDS = {"present": "there", "missing": "missing", "waiting": "waits for your yes",
               "board": "on the board (checked by the pre-flight check)", "unchecked": "no check given"}
CHECK_TIMEOUT = 15   # seconds a check may run
def check_program(word: str) -> str:
    """The program of a check's first word (its last path part)."""
    return word.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Status:
    """One entry of the list as the person reads it."""
    entry: Dependency
    state: str          # one of STATES
    why: str            # a short reason in plain words
    gap: bool           # no source: nobody read this entry

    @property
    def line(self) -> str:
        e = self.entry
        return (f"{e.name} {e.version} ({e.kind.replace('_', ' ')}): {STATE_WORDS[self.state]}"
                + (f"; {self.why}" if self.why else "") + ("; a gap, it has no source" if self.gap else ""))


@dataclass
class InstallPlan:
    """What the build image installs for a module's dependencies, and what is left to the person and the board."""
    packages: List[str] = field(default_factory=list)          # name=version, as the Dockerfile's apt step takes them
    pip_packages: List[str] = field(default_factory=list)      # name==version
    pip_hashes: Dict[str, str] = field(default_factory=dict)   # name -> the wheel's sha256
    waiting: List[Dependency] = field(default_factory=list)    # vendor files: the person's own yes
    board: List[Dependency] = field(default_factory=list)      # drivers and firmware: shown, never installed
    images: List[Dependency] = field(default_factory=list)     # base images: named by the module's base image
    gaps: List[Dependency] = field(default_factory=list)       # entries without a source


class HostRunner:
    """Runs a check on this computer: the program and its arguments, no shell, no input, a time limit. 127 when the
    program is not there, 124 when it ran too long."""

    def __call__(self, argv: Sequence[str]) -> int:
        program = shutil.which(argv[0])
        if program is None:
            return 127
        try:
            return subprocess.run([program, *argv[1:]], shell=False, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=CHECK_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            return 124
        except OSError:
            return 127


class ImageRunner:
    """Runs a check in a build image: `docker run --rm --network none IMAGE argv`, so the image's own packages are
    what it sees. 127 when the container engine is not there."""

    def __init__(self, image: str, engine: str = "docker"):
        self.image, self.engine = image, engine

    def __call__(self, argv: Sequence[str]) -> int:
        program = shutil.which(self.engine)
        if program is None:
            return 127
        try:
            return subprocess.run([program, "run", "--rm", "--network", "none", "--entrypoint", argv[0], self.image,
                                   *argv[1:]], shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=CHECK_TIMEOUT * 4).returncode
        except subprocess.TimeoutExpired:
            return 124
        except OSError:
            return 127


def entries_of(record: Any) -> List[Dependency]:
    """The entries of a target.yml's template_variables (the whole block or its `dependencies` list); ValueError
    with the first problem when one is not valid."""
    items = record.get("dependencies") if isinstance(record, dict) else record
    entries = []
    for number, item in enumerate(items or [], 1):
        try:
            entries.append(item if isinstance(item, Dependency) else Dependency.model_validate(item))
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            reason = str(first["msg"]).removeprefix("Value error, ")
            raise ValueError(f"dependencies[{number}]{'.' + where if where else ''}: {reason}"[:300]) from None
    return entries


def _allowed(entry: Dependency) -> bool:
    return bool(entry.check) and check_program(entry.check[0]) in CHECK_PROGRAMS


def statuses(entries: Iterable[Dependency], run: Optional[Callable[[Sequence[str]], int]] = None,
             have_vendor_file: Optional[Callable[[Dependency], bool]] = None) -> List[Status]:
    """The state of each entry. `run` runs a check (HostRunner by default); `have_vendor_file(entry)` says whether the
    person's vendor store holds a vendor file (without it a vendor file waits for the person)."""
    run = run or HostRunner()
    out: List[Status] = []
    for e in entries:
        gap = e.is_gap
        if e.kind == "board_driver":
            out.append(Status(e, "board", e.note, gap))
        elif e.kind == "vendor_file":
            if have_vendor_file and have_vendor_file(e):
                out.append(Status(e, "present", "in your vendor store", gap))
            else:
                out.append(Status(e, "waiting", f"fetch it under your own licence acceptance from {e.url}", gap))
        elif not e.check:
            out.append(Status(e, "unchecked", "", gap))
        elif not _allowed(e):
            out.append(Status(e, "unchecked", f"{check_program(e.check[0])} is not a program a check may start", gap))
        else:
            code = run(e.check)
            out.append(Status(e, "present" if code == 0 else "missing", "" if code == 0 else f"the check exited {code}",
                              gap))
    return out


def vendor_checker(state_dir: Any, folder: Path) -> Callable[[Dependency], bool]:
    """`have_vendor_file` for `statuses`: whether the person's vendor store (Core/vendor_files.py) holds the file that
    a vendor_file entry names. The store keeps a file only after the person accepted its licence (the guided fetch), so
    its presence is the person's yes."""
    from orchestrator.Core import vendor_files
    known = {e.file.name: e for e in vendor_files.entries(Path(folder))}

    def have(entry: Dependency) -> bool:
        found = known.get(entry.name)
        return found is not None and vendor_files.stored(state_dir, found) is not None
    return have


def install_plan(entries: Iterable[Dependency]) -> InstallPlan:
    """What the module's build image installs, from the entries (the person's yes to a licence is a separate step)."""
    plan = InstallPlan()
    for e in entries:
        if e.is_gap:
            plan.gaps.append(e)
        if e.kind == "system_package":
            plan.packages.append(f"{e.name}={e.version}")
        elif e.kind == "pip_package":
            plan.pip_packages.append(f"{e.name}=={e.version}")
            plan.pip_hashes[e.name] = e.sha256 or ""
        elif e.kind == "vendor_file":
            plan.waiting.append(e)
        elif e.kind == "board_driver":
            plan.board.append(e)
        elif e.kind == "base_image":
            plan.images.append(e)
    return plan


# The comment line the module generator leaves in a Dockerfile above the place of the module's own pip packages (its
# marker line is replaced by their steps, or by nothing): the place the steps of the list go to later.
STEPS_ANCHOR = "# Vendor Python packages of the module"


@dataclass(frozen=True)
class DockerfileGap:
    """The packages of the list one Dockerfile does not name pinned, and whether it has the place for them."""
    file: Path
    sha256: str                                   # of the file as it was read
    apt: Tuple[str, ...] = ()                     # name=version
    pip: Tuple[str, ...] = ()                     # name==version
    hashes: Tuple[Tuple[str, str], ...] = ()      # pip name -> the wheel's sha256
    has_place: bool = True

    @property
    def names(self) -> List[str]:
        return [p.split("=", 1)[0] for p in (*self.apt, *self.pip)]


def _code_lines(text: str) -> str:
    """The Dockerfile's text without its comment lines (a mention in a comment installs nothing)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def dockerfile_gaps(folder: Path, entries: Iterable[Dependency]) -> List[DockerfileGap]:
    """For each Dockerfile of a target folder (`Dockerfile*`), the system and pip packages of the list it does not name
    pinned; only files that miss something are returned. A vendor file, a base image and a board driver never count."""
    plan = install_plan(entries)
    gaps: List[DockerfileGap] = []
    for path in sorted(Path(folder).glob("Dockerfile*")):
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code = _code_lines(text).lower()
        apt = tuple(p for p in plan.packages if p.lower() not in code)
        pip = tuple(p for p in plan.pip_packages if p.lower() not in code)
        if not apt and not pip:
            continue
        hashes = tuple((p.split("==", 1)[0], plan.pip_hashes.get(p.split("==", 1)[0], "")) for p in pip)
        gaps.append(DockerfileGap(path, hashlib.sha256(raw).hexdigest(), apt, pip, hashes,
                                  STEPS_ANCHOR in text))
    return gaps


def merge_into(data: Dict[str, Any], plan: InstallPlan) -> Dict[str, Any]:
    """`data` (a module's validated values) with the plan's packages added to its apt packages and its pip packages,
    each once; the pip hashes go along (`pip_hashes`), so the Dockerfile's pip step is hash-checked."""
    out = dict(data)
    packages = list(out.get("packages") or [])
    names = {p.split("=", 1)[0] for p in packages}
    out["packages"] = packages + [p for p in plan.packages if p.split("=", 1)[0] not in names]
    pips = list(out.get("pip_packages") or [])
    have = {p.split("==", 1)[0].lower() for p in pips}
    out["pip_packages"] = pips + [p for p in plan.pip_packages if p.split("==", 1)[0].lower() not in have]
    hashes = dict(out.get("pip_hashes") or {})
    hashes.update({n.lower(): h for n, h in plan.pip_hashes.items() if h})
    if hashes:
        out["pip_hashes"] = hashes
    return out


def table(rows: List[Status]) -> List[str]:
    """The list as lines for the command line and the wizard: one per entry, then what is left to the person."""
    lines = [s.line for s in rows]
    waiting = sum(1 for s in rows if s.state == "waiting")
    missing = sum(1 for s in rows if s.state == "missing")
    if lines:
        lines.append(f"{len(rows)} entries: {sum(1 for s in rows if s.state == 'present')} there, {missing} missing, "
                     f"{waiting} waiting for your yes, {sum(1 for s in rows if s.gap)} without a source.")
    return lines


def load(target_folder: Path) -> List[Dependency]:
    """The entries of a target folder's target.yml (an empty list when it has none); ValueError when one is invalid."""
    import yaml
    try:
        meta = yaml.safe_load((Path(target_folder) / "target.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    record = meta.get("template_variables") if isinstance(meta, dict) else None
    return entries_of(record) if isinstance(record, dict) else []


__all__ = ["CHECK_PROGRAMS", "DockerfileGap", "HostRunner", "ImageRunner", "InstallPlan", "STATES", "STEPS_ANCHOR",
           "Status", "check_program", "dockerfile_gaps", "entries_of", "install_plan", "load", "merge_into",
           "statuses", "table"]
