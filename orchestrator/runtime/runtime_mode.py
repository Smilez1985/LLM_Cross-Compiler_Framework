#!/usr/bin/env python3
"""
Ditto's runtime mode: bare metal or Docker (DECISIONS round 7 D).

Ditto's local runtime (the self-built llama-server and its model) runs bare metal, the default (no Docker overhead,
runtime decision D23), or in Docker (Ditto's image, container_server.py). ensure() is what `llm-cli ditto runtime
ensure`, the installers' Ditto step and the AI settings run, and it is idempotent:

- detect() finds both modes: a bare-metal build that passed its verification on this computer and still matches its
  manifest (or the official archive the person opted into), and Ditto's image of the pinned commit, verified in a
  container of itself.
- Found in either mode: nothing is done, and the result says what was found and how to switch (force).
- Nothing found: bare metal when it can be built here; Docker when the person chose it, or when bare metal is not
  possible (the result says why).
- force with a mode: that mode is made (or, when it is there already, only recorded) and verified. The models are
  reused: the image mounts the framework's models folder read-only, nothing is downloaded again. Only the runtime is
  made again for the new mode, by repackaging where it can: a bare-metal build goes into an image on a Docker Official
  base of its C library (a native build: the same C library, at least as new), and the build an image was made from
  becomes the bare-metal build once it passed its verification on this computer. Where that is not possible, the
  result says why, and the runtime is built again (the bare-metal path of `ditto runtime install`, or the builder
  container for the image).
- Bare metal and Docker are two alternatives (W2 of 2026-10-16). Before a switch builds the new mode next to the old
  one, space_needed() estimates what the new mode needs while it is built and what it keeps, and checks that against
  the free space of every disk involved (Ditto's runtime folder; Docker's image storage). When it does not fit,
  nothing is built: SpaceShortage carries the sizes and the old mode's list, and the person chooses: free space and
  switch again, or remove the old mode first (old_mode_plan(); Ditto is unavailable until the new mode works, the
  models stay; when the new mode then fails, the person is told so and offered the old mode again). Removing it first
  is offered only while the new mode may fit then: space_after_removal() estimates it again without the old mode's
  build (a bare-metal build that is gone cannot be repackaged) and counts what the old mode's objects free.
- After the new mode is verified, leftovers() lists EVERY object of the old mode as a DeletionPlan: every build folder
  with its full path, build ID, folder name and size, every image and container with its name, ID and size, the old
  mode's records in mode.json and active.json, the log of Docker mode's image builds, and what each is. Nothing is
  removed here: the person confirms exactly that list in the framework's own window (CLI or GUI, "No" by default; one
  confirmation for the whole list, no choice per item, round 7 D), and remove_leftovers() deletes exactly that list for
  good (no trash: the old mode is gone completely), and only while the new mode is installed and verified. Without a
  terminal, and for an AI, nothing is removed and the list is reported. Ditto's images are never deleted otherwise.
  What both modes share stays: the models, the pinned source, the framework's builder images, a board's runtime
  builds.

An AI may read the mode (get_status, local_runtime); switching it and removing the old mode are the person's alone:
no tool calls ensure() or remove_leftovers(), and remove_leftovers() refuses every actor but the person's CLI and GUI.
"""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.runtime import container_server as cs
from orchestrator.runtime.llama_server import PREBUILT_LABEL, LocalRuntimeError
from orchestrator.utils.next_steps import WithSteps

MODES = ("bare-metal", "docker")
# Licences a model may be downloaded under without the person's own yes at a terminal when they chose an unattended run
# (`ensure --yes`, the installers' LCF_DITTO); any other licence is the person's to accept (round 5 a: legal warnings
# stay with the person).
PERMISSIVE_LICENCES = ("apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause")
CHOICES = ("auto", *MODES)
PERSON_ACTORS = ("cli", "gui")
IMAGE_LOG = "ditto-image.log"


class RuntimeModeError(WithSteps, RuntimeError):
    """A mode cannot be made here (the message says why and what to do; safe to show)."""


class LeftoversChanged(RuntimeError):
    def __init__(self):
        super().__init__("The list changed after it was shown (something was built, removed or started meanwhile); "
                         "nothing was removed. Look at the new list and confirm it.")


class SpaceShortage(RuntimeModeError):
    """The new mode does not fit next to the old one (W2): nothing was built. `estimate` has the sizes per disk,
    `old_mode` and `old_plan` the old mode and every object of it (None when nothing of it is left), which the person
    may remove first. `after_removal` is the estimate once every object of the old mode is deleted first: without the
    old mode's build, which can then no longer be reused, and with the space its objects free on each disk (None when
    there is nothing to remove). Removing the old mode first is offered only while it may help (removal_helps is not
    False); otherwise it would only leave Ditto without a local runtime."""

    def __init__(self, estimate: "SpaceEstimate", old_mode: Optional[str], old_plan: Optional[Any],
                 after_removal: Optional["SpaceEstimate"] = None):
        self.estimate, self.old_mode, self.old_plan = estimate, old_mode, old_plan
        self.after_removal = after_removal
        new, old = mode_label(estimate.mode), mode_label(old_mode)
        if old_plan is None:
            way = " Nothing of the old mode is left to remove: free space there and switch again."
        elif self.removal_helps is False:
            way = (f" Even with {old} deleted first, {new} would not fit either ({' '.join(after_removal.lines())}): "
                   "free space there and switch again.")
        else:
            way = (f" Free space there and switch again, or remove {old} first ({len(old_plan.items)} objects, "
                   f"{_size(old_plan.total_bytes)}): Ditto is unavailable until {new} works, and the models stay.")
        super().__init__(f"{new} does not fit next to {old}, so nothing was built. {' '.join(estimate.lines())}{way}")

    @property
    def removal_helps(self) -> Optional[bool]:
        """Whether the new mode fits once the old mode is deleted first (None: not known, so the removal may help)."""
        after = self.after_removal
        if self.old_plan is None or after is None or not after.checks:
            return None
        return after.fits


def mode_label(mode: Optional[str]) -> str:
    return {"bare-metal": "bare metal", "docker": "Docker"}.get(str(mode), "not installed")


@dataclass
class Found:
    mode: str
    summary: str                         # one line for the person
    build_id: Optional[str] = None
    image: Optional[cs.ImageInfo] = None
    verified: bool = True                # Docker: the image is the one verified and recorded in mode.json


@dataclass
class Detection:
    bare_metal: Optional[Found]
    docker: Optional[Found]
    recorded: Optional[str]              # the mode in mode.json (None: nothing recorded yet)
    notes: List[str] = field(default_factory=list)
    docker_problem: Optional[str] = None  # why Docker cannot be used here

    def get(self, mode: str) -> Optional[Found]:
        return self.bare_metal if mode == "bare-metal" else self.docker

    def found(self) -> List[Found]:
        """What is installed, the recorded mode first."""
        found = [f for f in (self.bare_metal, self.docker) if f is not None]
        return sorted(found, key=lambda f: f.mode != self.recorded)

    def lines(self) -> List[str]:
        out = [f"Found: {f.summary}" for f in self.found()] or ["Found: Ditto's runtime is not installed yet."]
        return out + self.notes


@dataclass
class EnsureResult:
    action: str                          # skipped | installed | switched | recorded
    mode: str
    lines: List[str]                     # what was found, done and decided, and why (for the person)
    leftovers: Optional[Any] = None      # DeletionPlan of every object of the old mode (nothing removed yet)
    problems: List[str] = field(default_factory=list)
    space: Optional["SpaceEstimate"] = None   # the space check before the new mode was built (a switch)


class _Told(list):
    """The result's lines; each line is also told (log) as it is added, so a long build shows where it is."""

    def __init__(self, say: Callable[[str], None]):
        super().__init__()
        self._say = say

    def append(self, item: str) -> None:
        super().append(item)
        self._say(item)

    def __iadd__(self, items):
        for item in items:
            self.append(item)
        return self


@dataclass
class Removal:
    deleted: List[str] = field(default_factory=list)       # deleted for good (every object of the old mode)
    freed_bytes: int = 0
    failed: List[Tuple[str, str]] = field(default_factory=list)
    # speaking name -> the full paths that stayed (Core/deletion.note_kept), for the person's own text only
    remaining: Dict[str, List[str]] = field(default_factory=dict)
    stopped: bool = False                                   # Ditto's server was stopped first (the old mode removed
                                                            # before the new one was built)


def container_build_requested() -> bool:
    """Whether this process is the framework's own container build (LCF_CONTAINER_BUILD=1), which has no Docker engine;
    `ensure` is called with it by the CLI and the GUI alike."""
    return os.environ.get("LCF_CONTAINER_BUILD") == "1"


# --- the model offer after ensure (`ditto runtime ensure`; --no-model turns it off) -----------------------------------

@dataclass
class ModelOffer:
    """The model `ensure` offers afterwards: the tier chosen earlier (`start --model`) or the recommendation for this
    computer, why, and whether it is there. The person sees its source, size and licence before any download; a model
    without a pinned size and sha256 is never downloaded."""
    model: Any                           # catalog.ModelEntry
    why: str
    state: str                           # ready | unverified | missing (LocalLLMRuntime.model_state)
    folder: Path                         # the models folder it goes to (shared by both modes)

    @property
    def needed(self) -> bool:
        return self.state == "missing"

    @property
    def permissive(self) -> bool:
        """Whether its licence allows the download without the person's own yes in an unattended run (--yes)."""
        return self.model.license.lower() in PERMISSIVE_LICENCES


def model_offer(rt: Any) -> ModelOffer:
    """What `ensure` offers to download after it ran (CLI and GUI): reads only."""
    preferred = rt.preferred_tier()
    model, why = (rt.catalog.model(preferred), "chosen earlier") if preferred else rt.choose_tier()
    return ModelOffer(model=model, why=why, state=rt.model_state(model), folder=Path(rt.models_dir))


# --- detection --------------------------------------------------------------------------------------------------------

def docker_problem(rt: Any, container_build: bool = False) -> Optional[str]:
    """Why Docker mode cannot be used on this computer, or None."""
    if container_build:
        return ("this is the framework's own container build (LCF_CONTAINER_BUILD=1), which has no Docker engine; "
                "Docker mode is for a computer with Docker")
    os_name = rt.platform()[0]
    if os_name != "linux":
        return (f"Ditto's container is built for Linux computers in this version ({os_name} follows with the "
                "Windows and macOS work package)")
    try:
        reason = rt.docker().unavailable()
    except Exception as e:  # noqa: BLE001 - the docker module is missing
        return f"Docker cannot be used from the framework ({type(e).__name__})"
    return f"{reason}; start Docker (and let your user use it)" if reason else None


def _docker_summary(image: cs.ImageInfo, record: Dict[str, Any], verified: bool) -> str:
    made_by = image.labels.get(cs.LABEL_MADE_BY, "")
    how = {"repackaged": "the bare-metal build repackaged", "rebuilt": "built in the builder container"}.get(made_by,
                                                                                                           "made here")
    state = f"verified in a container of itself at {record.get('verified_at') or '?'}" if verified else \
        "of the pinned commit, not verified yet"
    return (f"Docker: image {image.name} ({image.image_id[7:19]}), build "
            f"{str(image.labels.get(cs.LABEL_BUILD, ''))[:16]} ({image.labels.get(cs.LABEL_BACKEND, '?')}, {how}, on "
            f"{image.labels.get(cs.LABEL_BASE, '?')}), {state}")


def detect(rt: Any, container_build: bool = False) -> Detection:
    """Both modes on this computer: the bare-metal build start() would use (verified here, intact, of the pin) and
    Ditto's image of the pinned commit (verified and recorded, or there but not verified yet)."""
    notes: List[str] = []
    inst, problems = rt.resolve_installed()
    bare = None
    if inst is not None:
        if inst.origin == "prebuilt":
            summary = f"bare metal: the official archive {inst.asset_id} you chose ({PREBUILT_LABEL})"
        else:
            summary = (f"bare metal: {inst.label} build {str(inst.build_id)[:16]} ({inst.backend}), verified on this "
                       "computer")
        bare = Found("bare-metal", summary, build_id=inst.build_id)
    else:
        notes += [f"Bare metal: {p}" for p in problems]
    record = rt.mode_record()
    docker = None
    problem = docker_problem(rt, container_build)
    if problem is None:
        pin = rt.catalog.llama_cpp.source
        try:
            images = rt.docker().images()
        except Exception:  # noqa: BLE001 - the daemon went away meanwhile
            images = []
            problem = "the Docker daemon did not answer"
        current = [i for i in images if i.role == "image" and pin is not None and not i.pulled
                   and i.labels.get(cs.LABEL_COMMIT) == pin.commit]
        notes += [f"Docker: the image {i.name} has Ditto's labels but was pulled from a registry, so it is not used "
                  "(Ditto's image is only ever built on this computer)" for i in images if i.role == "image" and i.pulled]
        saved = record.get("docker") or {}
        verified = next((i for i in current if i.image_id == saved.get("image_id")
                         and i.labels.get(cs.LABEL_BUILD) == saved.get("build_id")), None)
        if verified is not None:
            docker = Found("docker", _docker_summary(verified, saved, True),
                           build_id=verified.labels.get(cs.LABEL_BUILD), image=verified)
        elif current:
            newest = max(current, key=lambda i: i.created)
            docker = Found("docker", _docker_summary(newest, saved, False), build_id=newest.labels.get(cs.LABEL_BUILD),
                           image=newest, verified=False)
        for image in images:
            if image.role == "image" and image not in current and not image.pulled:
                notes.append(f"Docker: the image {image.name} is of another llama.cpp commit and is not used")
        if record.get("mode") == "docker" and saved.get("image_id") and verified is None:
            gone = not any(i.image_id == saved.get("image_id") for i in images)
            notes.append(f"Docker: the recorded image {saved.get('tag') or str(saved['image_id'])[7:19]} "
                         + ("is no longer there" if gone else "is no longer Ditto's image of the pinned commit"))
    elif record.get("mode") == "docker":
        notes.append(f"Docker: {problem}")
    return Detection(bare, docker, record.get("mode"), notes, problem)


# --- ensure -----------------------------------------------------------------------------------------------------------

def _noop(_msg: str) -> None:
    pass


def ensure(rt: Any, *, mode: str = "auto", force: bool = False, placement: str = "auto", backend: str = "auto",
           jobs: Optional[int] = None, log: Optional[Callable[[str], None]] = None,
           fetch_source: Optional[Callable[[], bool]] = None, cancelled: Optional[Callable[[], bool]] = None,
           container_build: bool = False) -> EnsureResult:
    """Makes sure Ditto's runtime is installed (see the module docstring); returns what was found, done and decided.
    Raises RuntimeModeError when the mode cannot be made here (nothing was changed then, except builds that were made
    and stay for the next try), and SpaceShortage before anything is built when a switch's new mode does not fit next
    to the old one. Removes nothing: every object of the old mode is in the result for the person."""
    if mode not in CHOICES:
        raise RuntimeModeError(f"the mode is one of {', '.join(CHOICES)}")
    if force and mode == "auto":
        raise RuntimeModeError("force needs a mode to force: bare-metal or docker")
    say = log or _noop
    det = detect(rt, container_build)
    lines = _Told(say)
    lines += det.lines()
    if not force:
        if det.recorded == "docker" and det.docker is None and det.docker_problem:
            # The person chose Docker mode and Docker cannot be asked now (not started yet, for example): that is no
            # reason to change the mode behind the person's back.
            lines.append(f"Ditto's runtime mode is Docker, but Docker cannot be used now ({det.docker_problem}); "
                         "nothing was changed. Start Docker, or switch with: llm-cli ditto runtime ensure --mode "
                         "bare-metal --force")
            return EnsureResult("skipped", "docker", list(lines))
        for found in det.found():
            if found.mode == "docker" and not found.verified:
                checked = _verify_found_image(rt, found, lines, say)
                if checked is None:
                    continue
            if det.recorded != found.mode:
                if det.recorded:
                    lines.append(f"The recorded mode was {mode_label(det.recorded)}, and it is not installed any "
                                 f"more (see above); Ditto's runtime mode is now {mode_label(found.mode)}.")
                rt.record_mode(found.mode, _docker_record_of(rt, found) if found.mode == "docker" else None)
            lines.append(f"Nothing to do: Ditto's runtime is installed ({mode_label(found.mode)}).")
            if mode not in ("auto", found.mode):
                lines.append(f"You asked for {mode_label(mode)}; Ditto is installed {mode_label(found.mode)}, so "
                             f"nothing was changed. Switch with: llm-cli ditto runtime ensure --mode {mode} --force "
                             "(the models are reused).")
            return EnsureResult("skipped", found.mode, list(lines))
        if mode == "auto":
            mode, why = _default_mode(rt, placement, backend, container_build)
            lines.append(why)
    old = det.recorded or next((f.mode for f in det.found()), None)
    target = det.get(mode)
    space = None
    built = target is not None and (mode == "bare-metal" or target.verified)
    if not built and old is not None and old != mode:
        # W2: the new mode is built next to the old one only when it fits there; otherwise nothing is built and the
        # person decides (free space, or remove the old mode first while the new mode then fits)
        space = space_needed(rt, det, mode, placement, backend, container_build)
        lines += space.lines()
        if not space.fits:
            old_plan = old_mode_plan(rt, old)
            after = (space_after_removal(rt, det, mode, old_plan, placement, backend, container_build)
                     if old_plan is not None else None)
            raise SpaceShortage(space, old, old_plan, after)
    if built:
        lines.append(f"{mode_label(mode)} is installed already: nothing was built.")
        record = _docker_record_of(rt, target) if mode == "docker" else None
        action = "recorded"
    elif mode == "bare-metal":
        _make_bare_metal(rt, placement, backend, jobs, lines, say, fetch_source, cancelled)
        record, action = None, "installed"
    else:
        record = _make_docker(rt, det, placement, backend, jobs, lines, say, fetch_source, cancelled, container_build)
        action = "installed"
    switched = old is not None and old != mode
    if switched:
        stopped = _stop_server(rt)
        if stopped:
            lines.append(f"The server of the {mode_label(old)} mode was stopped; start Ditto's server again with: "
                         "llm-cli ditto runtime start")
        action = "switched"
    rt.record_mode(mode, record)
    lines.append(f"Ditto's runtime mode is now {mode_label(mode)}.")
    plan = leftovers(rt, mode) if (force or switched) else None
    if (force or switched) and mode == "bare-metal" and (old == "docker" or rt.mode_record().get("docker")):
        unlisted = _docker_unlisted(rt)
        if unlisted:
            lines.append(f"Ditto's images and containers could not be listed ({unlisted}), so none of them is in the "
                         "list. To see them, start Docker and run: llm-cli ditto runtime ensure --mode bare-metal "
                         "--force (nothing is built again then).")
    return EnsureResult(action, mode, list(lines), leftovers=plan, space=space)


def _docker_unlisted(rt: Any) -> Optional[str]:
    """Why Ditto's images and containers cannot be listed now (Docker cannot be asked), or None."""
    try:
        return rt.docker().unavailable()
    except Exception as e:  # noqa: BLE001 - the docker module is missing
        return f"Docker cannot be used from the framework ({type(e).__name__})"


def _stop_server(rt: Any) -> bool:
    try:
        return rt.stop() > 0
    except LocalRuntimeError:
        return False


def _default_mode(rt: Any, placement: str, backend: str, container_build: bool) -> Tuple[str, str]:
    """Nothing is installed: bare metal when it can be built here (no Docker overhead), else Docker, with the reason."""
    try:
        rt.plan_install(backend=backend, placement=placement)
        return "bare-metal", ("Nothing is installed: bare metal is the default (the server runs without Docker's "
                              "overhead).")
    except LocalRuntimeError as e:
        bare = str(e)
    problem = docker_problem(rt, container_build)
    if problem is not None:
        raise RuntimeModeError(f"Ditto's runtime cannot be installed on this computer. Bare metal: {bare} Docker: "
                               f"{problem}.")
    return "docker", f"Bare metal is not possible here ({bare}); Docker mode instead."


def _docker_record_of(rt: Any, found: Found) -> Dict[str, Any]:
    old = rt.mode_record().get("docker") or {}
    image = found.image
    if image is None:
        return old
    return {"image_id": image.image_id, "tag": image.name, "build_id": image.labels.get(cs.LABEL_BUILD),
            "base": image.labels.get(cs.LABEL_BASE), "backend": image.labels.get(cs.LABEL_BACKEND),
            "made_by": image.labels.get(cs.LABEL_MADE_BY),
            "verified_at": old.get("verified_at") if old.get("image_id") == image.image_id else _now()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _verify_found_image(rt: Any, found: Found, lines: List[str], say: Callable[[str], None]) -> Optional[Found]:
    """An image of the pinned commit that is not recorded as verified: checked now (no build). None when it fails."""
    image = found.image
    gpu = cs.gpu_passthrough(rt.docker(), rt.hardware(), str(image.labels.get(cs.LABEL_BACKEND, "cpu")))
    say(f"Checking the image {image.name} in a container ...")
    report = cs.verify_image(rt.docker(), image, rt.catalog.llama_cpp.source, gpu)
    if not report.ok:
        lines.append(f"The image {image.name} did not pass its check in a container ({report.summary()}); it is not "
                     "used.")
        return None
    found.verified = True
    lines.append(f"The image {image.name} passed its check in a container: it is used.")
    return found


# --- bare metal -------------------------------------------------------------------------------------------------------

def _make_bare_metal(rt: Any, placement: str, backend: str, jobs: Optional[int], lines: List[str],
                     say: Callable[[str], None], fetch_source: Optional[Callable[[], bool]],
                     cancelled: Optional[Callable[[], bool]]) -> None:
    """Bare metal: the build Ditto's image was made from, verified on this computer (repackaged the other way round),
    or a new build of `ditto runtime install`."""
    from orchestrator.runtime.build.store import BuildFailed, StoreError
    saved = rt.mode_record().get("docker") or {}
    build_id = saved.get("build_id") or rt.store.references().get("docker")
    if build_id:
        build = rt.store.get(build_id)
        if build is None:
            lines.append(f"The build {str(build_id)[:16]} Ditto's image was made from is no longer in the build store, "
                         "so llama-server is built again for bare metal.")
        elif backend not in ("auto", build.backend):
            lines.append(f"The build Ditto's image was made from is a {build.backend} build; you asked for {backend}, "
                         "so llama-server is built again.")
        else:
            try:
                if not build.verified:
                    say(f"Verifying the build {build.build_id[:16]} of Ditto's image on this computer ...")
                    build = rt.store.verify_here(build.build_id, rt.verifier(), host=rt.platform())
                rt.use_store_build(build.build_id)
                lines.append(f"Repackaged: the build {build.build_id[:16]} ({build.backend}) Ditto's image was made "
                             "from passed its verification on this computer and is the bare-metal build now; nothing "
                             "was built.")
                return
            except (StoreError, BuildFailed, LocalRuntimeError) as e:
                lines.append(f"The build Ditto's image was made from cannot run bare metal here ({e}), so llama-server "
                             "is built again for bare metal.")
    plan = rt.plan_install(backend=backend, placement=placement)
    for line in plan.describe():
        say(line)
    # install() deletes no old build (round 5 f); what the person may remove is listed after the switch (leftovers)
    result = rt.install(plan, jobs=jobs, log=say, fetch_source=fetch_source, cancelled=cancelled)
    lines.append(f"Built for bare metal ({plan.placement} build): active build {result.active.build_id[:16]} "
                 f"({result.active.backend}), verified on this computer"
                 + (f"; CPU fallback {result.fallback.build_id[:16]}" if result.fallback.build_id !=
                    result.active.build_id else "") + ".")
    lines += list(result.problems)


# --- Docker -----------------------------------------------------------------------------------------------------------

def _base_by_id(catalog: Any, base_id: Any, image: Any = None) -> Optional[Any]:
    return next((b for b in catalog.build_bases if b.id == base_id and (image is None or b.image == image)), None)


def base_for_build(catalog: Any, build: Any, profile: Any) -> Tuple[Optional[Any], str]:
    """(the Docker Official base a stored build can be repackaged on, why); None with the reason when there is none. A
    build of the builder container goes on the base it was compiled on; a native build on the oldest base with this
    computer's C library that is at least as new (a binary needs the C library it was linked against or a newer
    one)."""
    from orchestrator.runtime.build.recipe import version_tuple
    builder = build.plan.get("builder") or {}
    if builder.get("kind") == "container":
        base = _base_by_id(catalog, builder.get("base"), builder.get("image"))
        if base is None:
            return None, f"its builder base {builder.get('base')} is no longer in the catalog"
        return base, f"it was built in the builder container on {base.id}"
    libc, version = getattr(profile, "libc", None), getattr(profile, "libc_version", None)
    if not libc or not version:
        return None, "this computer's C library is not known (probe it again: llm-cli scan)"
    try:
        fitting = sorted((b for b in catalog.build_bases
                          if b.libc == libc and version_tuple(b.libc_version) >= version_tuple(version)),
                         key=lambda b: version_tuple(b.libc_version))
    except (TypeError, ValueError):
        fitting = []
    if not fitting:
        return None, (f"it was built natively against {libc} {version}, and no Docker Official base in the catalog has "
                      f"{libc} {version} or newer")
    return fitting[0], f"it was built natively against {libc} {version}; {fitting[0].id} has {libc} " \
                       f"{fitting[0].libc_version}"


def image_base(catalog: Any, profile: Any) -> Tuple[Any, str]:
    """The base of a new build for Ditto's image: the one a builder-container build for this computer uses (its C
    library is not newer than this computer's, so the build also runs bare metal); when this computer's C library fits
    no base, the newest glibc base (the build then runs in the container only)."""
    from orchestrator.runtime.build.recipe import PlanError, choose_build_base, version_tuple
    try:
        base = choose_build_base(catalog, profile.libc, profile.libc_version)
        return base, (f"{base.id}, the newest base whose C library is not newer than this computer's: the build also "
                      "runs bare metal")
    except PlanError as e:
        glibc = sorted((b for b in catalog.build_bases if b.libc == "glibc"),
                       key=lambda b: version_tuple(b.libc_version))
        if not glibc:
            raise RuntimeModeError("the catalog has no glibc builder base for Ditto's image") from None
        return glibc[-1], (f"{glibc[-1].id}, because {e}: the build runs in the container only")


def profile_for_base(profile: Any, base: Any) -> Any:
    """This computer's profile with the base's C library: the recipe plans the image's build for the base it runs on
    (the CPU, the GPUs and everything else stay this computer's)."""
    from orchestrator.runtime.profile import HardwareProfile
    values = {**dict(profile.values), "LIBC": base.libc, "LIBC_VERSION": base.libc_version}
    return HardwareProfile(values, schema=profile.schema, source_schema=profile.source_schema, notes=profile.notes)


def _image_record(image: cs.ImageInfo) -> Dict[str, Any]:
    return {"image_id": image.image_id, "tag": image.name, "build_id": image.labels.get(cs.LABEL_BUILD),
            "base": image.labels.get(cs.LABEL_BASE), "backend": image.labels.get(cs.LABEL_BACKEND),
            "made_by": image.labels.get(cs.LABEL_MADE_BY), "verified_at": _now()}


def _repackage_candidate(rt: Any, backend: str, docker: Any, hardware: Any,
                         lines: List[str]) -> Tuple[Optional[Any], Optional[Any], str]:
    """(stored build, base, why) of the bare-metal build to put into the image; (None, None, why not) when none fits."""
    inst = rt.installed()
    if inst is None:
        return None, None, "there is no bare-metal build to repackage"
    if inst.build_id is None:
        return None, None, "the official prebuilt archive is never put into Ditto's image (it is not self-built)"
    refs = rt.store.references()
    builds = [rt.store.get(refs[r]) for r in ("active", "fallback") if refs.get(r)]
    builds = [b for b in builds if b is not None and b.verified and b.backend in cs.BACKENDS]
    want = backend if backend != "auto" else (builds[0].backend if builds else "cpu")
    if want != "cpu" and not cs.gpu_passthrough(docker, hardware, want).used:
        lines.append(f"The {want} build is not used in the container: "
                     f"{cs.gpu_passthrough(docker, hardware, want).note}.")
        want = "cpu"
    build = next((b for b in builds if b.backend == want), None)
    if build is None:
        return None, None, f"there is no verified {want} bare-metal build to repackage"
    base, why = base_for_build(rt.catalog, build, rt.profile())
    return (build, base, why) if base is not None else (None, None, why)


def _make_docker(rt: Any, det: Detection, placement: str, backend: str, jobs: Optional[int], lines: List[str],
                 say: Callable[[str], None], fetch_source: Optional[Callable[[], bool]],
                 cancelled: Optional[Callable[[], bool]], container_build: bool) -> Dict[str, Any]:
    """Docker: an image of the pinned commit that is there and passes its check, else the bare-metal build repackaged,
    else a new build in the builder container; every image is verified in a container of itself. Returns the record of
    the verified image."""
    problem = docker_problem(rt, container_build)
    if problem is not None:
        raise RuntimeModeError(f"Docker mode is not possible here: {problem}.")
    docker = rt.docker()
    pin = rt.catalog.llama_cpp.source
    log_file = Path(rt.data_dir) / "logs" / IMAGE_LOG
    log_file.parent.mkdir(parents=True, exist_ok=True)
    hardware = rt.hardware()
    if det.docker is not None and not det.docker.verified and _verify_found_image(rt, det.docker, lines, say):
        return _image_record(det.docker.image)
    if placement in ("auto", "native"):
        build, base, why = _repackage_candidate(rt, backend, docker, hardware, lines)
        if build is None and placement == "native":
            raise RuntimeModeError(f"--placement native for Docker mode repackages a native build, but {why}; build "
                                   "one first (llm-cli ditto runtime install --placement native) or use --placement "
                                   "auto")
        if build is not None:
            say(f"Repackaging the bare-metal build {build.build_id[:16]} ({build.backend}) on {base.id}: {why} ...")
            image = cs.build_runtime_image(docker, build, base, "repackaged", log_file, offline=_offline(rt))
            gpu = cs.gpu_passthrough(docker, hardware, build.backend)
            report = cs.verify_image(docker, image, pin, gpu)
            if report.ok:
                rt.store.keep_for_docker(build.build_id)
                lines.append(f"Repackaged: the bare-metal build {build.build_id[:16]} ({build.backend}) is in the "
                             f"image {image.name} on {base.id} ({why}); it passed its check in a container "
                             f"({gpu.note}). Nothing was built.")
                return _image_record(image)
            lines.append(f"Repackaging did not pass its check in a container ({report.summary()}), so llama-server is "
                         f"built again in the builder container. The image {image.name} stays until you remove it.")
        else:
            lines.append(f"Repackaging is not possible ({why}), so llama-server is built in the builder container.")
    return _rebuild_for_docker(rt, backend, jobs, lines, say, fetch_source, cancelled, docker, hardware, log_file)


def _offline(rt: Any) -> bool:
    """The runtime's offline mode by the framework's one offline check (utils/locality.offline_mode)."""
    from orchestrator.utils.locality import offline_mode
    return offline_mode(getattr(rt, "settings", None))


def container_verifier(runner: Any) -> Any:
    """The verification of a build for Ditto's image: the checks of every build (build/verify.py), with its programs run
    by `runner` in a container of the runtime base."""
    from orchestrator.runtime.build.verify import Verifier
    return Verifier(runner=runner)


def _rebuild_for_docker(rt: Any, backend: str, jobs: Optional[int], lines: List[str], say: Callable[[str], None],
                        fetch_source: Optional[Callable[[], bool]], cancelled: Optional[Callable[[], bool]],
                        docker: Any, hardware: Any, log_file: Path) -> Dict[str, Any]:
    """A new build of the pinned recipe in the builder container (no network), planned for the image's base, verified
    in a container of the runtime-base stage, then put into Ditto's image and checked there."""
    from orchestrator.runtime.build.host import HostBuildError, plan_host_runtime
    from orchestrator.runtime.build.store import StoreError, smoke_model_file
    pin = rt.catalog.llama_cpp.source
    profile = rt.profile()
    base, why = image_base(rt.catalog, profile)
    say(f"Base of Ditto's image: {why}.")
    for_base = profile_for_base(profile, base)
    try:
        plan = plan_host_runtime(for_base, rt.toolchain(full=False), rt.catalog, rt.store, backend=backend,
                                 placement="container", engine=rt.engine())
    except HostBuildError as e:
        raise RuntimeModeError(f"llama.cpp cannot be built for Ditto's image: {e}") from None
    chosen = plan.cpu
    gpu_build = plan.gpu
    if gpu_build is not None and gpu_build.refusal is None and gpu_build.backend in cs.BACKENDS:
        gpu = cs.gpu_passthrough(docker, hardware, gpu_build.backend)
        if gpu.used:
            chosen = gpu_build
        else:
            lines.append(f"The {gpu_build.backend} build is not made for the container: {gpu.note}.")
    elif gpu_build is not None and gpu_build.refusal:
        lines.append(f"{gpu_build.backend}: {gpu_build.refusal}; the image gets the CPU build.")
    if chosen.refusal:
        raise RuntimeModeError(f"llama.cpp cannot be built for Ditto's image: {chosen.refusal}")
    if cancelled is not None and cancelled():
        raise RuntimeModeError("cancelled; nothing was built")
    gpu = cs.gpu_passthrough(docker, hardware, chosen.backend)
    say(f"Building the runtime base of Ditto's image on {base.id} ...")
    runtime_base = cs.build_runtime_base(docker, base, chosen.backend, log_file, offline=_offline(rt))
    # A build of this plan that is stored already (also one checked only in the container) is reused: no source.
    source = rt.pinned_source(rt.store.find(chosen.plan) is None, fetch_source=fetch_source, log=say)
    say(f"Building the {chosen.backend} build {chosen.plan.build_id[:16]} in the builder container ...")
    try:
        verifier = container_verifier(cs.ContainerRunner(docker, runtime_base.image_id, gpu))
        stored = rt.store.build(chosen.plan, source=source, executor=rt.executor(log=say), verifier=verifier,
                                jobs=jobs, toolchain=plan.toolchain,
                                smoke_model=smoke_model_file(rt.catalog.smoke_model, rt.models_dir.parent / "smoke"),
                                perf_key=for_base.perf_key(), host=rt.platform(), verified_in="container")
    except StoreError as e:
        where = f" (log: {e.log})" if getattr(e, "log", None) else ""
        raise RuntimeModeError(f"the build for Ditto's image failed: {e}{where}") from None
    if cancelled is not None and cancelled():
        raise RuntimeModeError("cancelled; the build is kept in the build store, no image was made")
    say(f"Putting the build into Ditto's image on {base.id} ...")
    image = cs.build_runtime_image(docker, stored, base, "rebuilt", log_file, offline=_offline(rt))
    report = cs.verify_image(docker, image, pin, gpu)
    if not report.ok:
        raise RuntimeModeError(f"Ditto's image {image.name} did not pass its check in a container: {report.summary()}. "
                               "It is not used; it stays until you remove it.")
    rt.store.keep_for_docker(stored.build_id)
    lines.append(f"Built in the builder container: build {stored.build_id[:16]} ({stored.backend}) in the image "
                 f"{image.name} on {base.id}; it passed its checks in a container ({gpu.note}).")
    return _image_record(image)




# --- the space a switch needs (W2) -----------------------------------------------------------------------------------

MB = 1000 ** 2
GB = 1000 ** 3
# Estimates of what a new llama.cpp build of each backend needs while it is built (its build tree in the store's
# staging folder) and keeps (its build folder); a build that is there already is measured instead.
BUILD_TREE = {"cpu": 1500 * MB, "vulkan": 2 * GB, "cuda": 8 * GB, "rocm": 8 * GB, "metal": 1500 * MB}
BUILD_KEPT = {"cpu": 200 * MB, "vulkan": 300 * MB, "cuda": 2 * GB, "rocm": 2 * GB, "metal": 200 * MB}
SOURCE_BYTES = 215 * MB          # the pinned llama.cpp source when it is not cached yet
# Docker's side: the runtime base of Ditto's image (a Docker Official base with the runtime libraries) when it is not
# there yet, and the framework's builder image a build in the builder container runs in.
RUNTIME_BASE = {"cpu": 300 * MB, "vulkan": 700 * MB, "cuda": 5 * GB, "rocm": 8 * GB, "metal": 300 * MB}
BUILDER_IMAGE = 3 * GB
RUNTIME_FOLDER = "Ditto runtime folder"
DOCKER_STORAGE = "Docker image storage"
TEMP_FOLDER = "temporary folder"   # where the build context of Ditto's image is made (a copy of the build's programs)


def _disk_usage(path: str):
    """shutil.disk_usage (tests replace it)."""
    return shutil.disk_usage(path)


def _size(n: int) -> str:
    from orchestrator.Core.disk_plan import gb
    return gb(n)


@dataclass
class SpaceEstimate:
    """What making `mode` needs on each disk while it is made (the peak), what it keeps afterwards, and whether that
    fits there (disk_plan.DiskCheck: the free space minus a reserve, the trash never counted as free)."""
    mode: str
    how: str                                               # what making it means here, for the person
    needs: List[Any] = field(default_factory=list)         # disk_plan.DiskNeed
    keeps: int = 0
    checks: List[Any] = field(default_factory=list)        # disk_plan.DiskCheck per disk
    unmeasured: List[str] = field(default_factory=list)    # what could not be checked, and why
    freed: int = 0     # the estimate after the old mode is deleted first: what its objects free, counted as free

    @property
    def fits(self) -> bool:
        return all(c.fits for c in self.checks)

    @property
    def total(self) -> int:
        return sum(n.bytes for n in self.needs)

    def lines(self) -> List[str]:
        out = [f"Space for {mode_label(self.mode)} ({self.how}): about {_size(self.total)} while it is made, "
               f"{_size(self.keeps)} kept afterwards (estimates)."]
        if self.freed:
            out.append(f"With the old mode deleted first: its objects free about {_size(self.freed)} on their disks, "
                       "counted as free here (estimate).")
        for check in self.checks:
            text = check.sentence()
            out.append(text[0].upper() + text[1:] + ".")
        return out + self.unmeasured


def _docker_root(rt: Any) -> Tuple[Optional[Path], str]:
    """The folder Docker keeps its images in, when it is a folder of this computer (else None and why)."""
    try:
        root = str((rt.docker().info() or {}).get("DockerRootDir") or "")
    except Exception as e:  # noqa: BLE001 - Docker did not answer
        return None, f"Docker did not answer ({type(e).__name__})"
    if not root:
        return None, "Docker does not say where it keeps its images"
    path = Path(root)
    if not path.is_dir():
        return None, ("that folder is not on this computer (Docker Desktop keeps its images in its own virtual "
                      "disk)")
    return path, ""


def _source_cached(rt: Any) -> bool:
    from orchestrator.runtime.build.source import SourceError, source_dir
    pin = rt.catalog.llama_cpp.source
    try:
        return pin is not None and source_dir(rt.data_dir, pin.commit).is_dir()
    except SourceError:
        return False


def _base_there(rt: Any, base_id: str, backend: str) -> bool:
    try:
        return any(i.role == "base" and i.labels.get(cs.LABEL_BASE) == base_id
                   and i.labels.get(cs.LABEL_BACKEND) == backend for i in rt.docker().images())
    except Exception:  # noqa: BLE001 - not known: counted as missing
        return False


def _builds_need(rt: Any, backends: List[str], state: Path, needs: List[Any]) -> int:
    """The runtime folder's part of new builds of `backends`, one after the other: the source when it is not cached,
    the largest build tree and every build kept. Returns what is kept."""
    from orchestrator.Core.disk_plan import DiskNeed
    if not backends:
        return 0
    if not _source_cached(rt):
        needs.append(DiskNeed(RUNTIME_FOLDER, state, SOURCE_BYTES,
                              f"the pinned llama.cpp source {_size(SOURCE_BYTES)}"))
    tree = max(BUILD_TREE.get(b, BUILD_TREE["cpu"]) for b in backends)
    kept = sum(BUILD_KEPT.get(b, BUILD_KEPT["cpu"]) for b in backends)
    plural = "s" if len(backends) > 1 else ""
    needs.append(DiskNeed(RUNTIME_FOLDER, state, tree, f"the build tree while it is built {_size(tree)}"))
    needs.append(DiskNeed(RUNTIME_FOLDER, state, kept, f"the {', '.join(backends)} build{plural} kept {_size(kept)}"))
    return kept


def _bare_metal_need(rt: Any, placement: str, backend: str, state: Path, needs: List[Any],
                     docker_needs: List[Tuple[int, str]], gone: frozenset = frozenset()) -> Tuple[str, int]:
    """(how, kept) of making bare metal: the build of Ditto's image verified here, or new builds. A build whose folder
    is in `gone` (deleted with the old mode first) is not reused."""
    saved = rt.mode_record().get("docker") or {}
    build_id = saved.get("build_id") or rt.store.references().get("docker")
    build = rt.store.get(build_id) if build_id else None
    if build is not None and backend in ("auto", build.backend) and str(build.path) not in gone:
        return f"the build {build.build_id[:16]} of Ditto's image is verified here, nothing is built", 0
    try:
        plan = rt.plan_install(backend=backend, placement=placement)
    except LocalRuntimeError:
        return "a new CPU build (its plan could not be made yet)", _builds_need(rt, ["cpu"], state, needs)
    backends = [b.backend for b in plan.builds if b.to_build]
    kept = _builds_need(rt, backends, state, needs)
    if backends and plan.placement == "container":
        docker_needs.append((BUILDER_IMAGE, f"the framework's builder image {_size(BUILDER_IMAGE)} (when it is not "
                                            "there yet)"))
    where = "natively" if plan.placement == "native" else "in the builder container"
    return (f"new {', '.join(backends)} build{'s' if len(backends) > 1 else ''} {where}" if backends
            else "builds that are stored already, nothing is built"), kept


def _context_need(size: int, needs: List[Any]) -> None:
    """The build context of Ditto's image: a copy of the build's programs in the temporary folder while the image is
    built (container_server.build_runtime_image)."""
    from orchestrator.Core.disk_plan import DiskNeed
    needs.append(DiskNeed(TEMP_FOLDER, Path(tempfile.gettempdir()), size,
                          f"a copy of the build's programs for the image's build {_size(size)}"))


def _docker_need(rt: Any, det: Detection, placement: str, backend: str, state: Path, needs: List[Any],
                 docker_needs: List[Tuple[int, str]], gone: frozenset = frozenset()) -> Tuple[str, int]:
    """(how, kept) of making Docker mode: an image that is there checked, the bare-metal build repackaged, or a new
    build in the builder container put into an image. A bare-metal build whose folder is in `gone` (deleted with the
    old mode first) cannot be repackaged."""
    if det.docker is not None and not det.docker.verified:
        return f"the image {det.docker.image.name} is checked, nothing is built", 0
    if placement in ("auto", "native"):
        try:
            build, base, _why = _repackage_candidate(rt, backend, rt.docker(), rt.hardware(), [])
        except Exception:  # noqa: BLE001 - estimated as a new build below
            build = base = None
        if build is not None and str(build.path) not in gone:
            size = _folder_size(build.path)
            base_bytes = 0 if _base_there(rt, base.id, build.backend) else RUNTIME_BASE.get(build.backend, 0)
            if base_bytes:
                docker_needs.append((base_bytes, f"the runtime base on {base.id} {_size(base_bytes)}"))
            docker_needs.append((size, f"Ditto's image with the build's programs {_size(size)}"))
            _context_need(size, needs)
            return f"the bare-metal build {build.build_id[:16]} repackaged on {base.id}", base_bytes + size
    want = backend if backend in BUILD_TREE else "cpu"
    kept = _builds_need(rt, [want], state, needs)
    docker_needs.append((BUILDER_IMAGE, f"the framework's builder image {_size(BUILDER_IMAGE)} (when it is not there "
                                        "yet)"))
    base_bytes = RUNTIME_BASE.get(want, RUNTIME_BASE["cpu"])
    try:
        base, _why = image_base(rt.catalog, rt.profile())
        if _base_there(rt, base.id, want):
            base_bytes = 0
    except Exception:  # noqa: BLE001 - counted as missing
        pass
    if base_bytes:
        docker_needs.append((base_bytes, f"the runtime base {_size(base_bytes)}"))
    image = BUILD_KEPT.get(want, BUILD_KEPT["cpu"])
    docker_needs.append((image, f"Ditto's image with the build's programs {_size(image)}"))
    _context_need(image, needs)
    return f"a new {want} build in the builder container", kept + base_bytes + image


def space_needed(rt: Any, det: Detection, mode: str, placement: str = "auto", backend: str = "auto",
                 container_build: bool = False, gone: frozenset = frozenset()) -> SpaceEstimate:
    """What making `mode` next to the other mode needs on each disk (estimates; a build that is there is measured),
    checked against the free space there. Reads only. A disk whose free space cannot be read is named and not
    checked; an estimate that cannot be made at all is named and blocks nothing. `gone`: the folders deleted with the
    old mode first, whose builds are not reused."""
    from orchestrator.Core.disk_plan import DiskNeed, ReservationLedger, check_disk
    state = Path(rt.root)
    needs: List[Any] = []
    docker_needs: List[Tuple[int, str]] = []
    try:
        if mode == "bare-metal":
            how, keeps = _bare_metal_need(rt, placement, backend, state, needs, docker_needs, gone)
        else:
            how, keeps = _docker_need(rt, det, placement, backend, state, needs, docker_needs, gone)
    except Exception as e:  # noqa: BLE001 - the build itself says what is wrong
        return SpaceEstimate(mode, "not estimated", unmeasured=[f"The space for {mode_label(mode)} could not be "
                                                                f"estimated ({type(e).__name__}); it is not checked."])
    unmeasured = []
    if docker_needs:
        root, why = _docker_root(rt)
        total = sum(n for n, _ in docker_needs)
        if root is None:
            unmeasured.append(f"{DOCKER_STORAGE}: about {_size(total)} (estimate); its free space could not be read "
                              f"({why}), so it is not checked.")
        else:
            needs += [DiskNeed(DOCKER_STORAGE, root, n, what) for n, what in docker_needs]
    checks = check_disk(needs, ledger=ReservationLedger(Path(rt.data_dir)), disk_usage=_disk_usage) if needs else []
    return SpaceEstimate(mode, how, needs, keeps, checks, unmeasured)


def _freed_by(rt: Any, plan: Any) -> Dict[str, int]:
    """What deleting the objects of `plan` frees on each disk (by device): folders and records where they are,
    Docker's objects in its image storage when that is a folder of this computer (image sizes include layers other
    images share, so this can be more than is really freed: the removal is then rather offered than withheld)."""
    from orchestrator.Core.disk_plan import device_of
    docker_root = None
    if any(not item.full_path for item in plan.items):
        docker_root, _why = _docker_root(rt)
    freed: Dict[str, int] = {}
    for item in plan.items:
        where = Path(item.full_path) if item.full_path else docker_root
        if where is not None:
            device = device_of(where)
            freed[device] = freed.get(device, 0) + int(item.size_bytes)
    return freed


def space_after_removal(rt: Any, det: Detection, mode: str, plan: Any, placement: str = "auto",
                        backend: str = "auto", container_build: bool = False) -> SpaceEstimate:
    """What making `mode` needs once every object of the old mode (`plan`, old_mode_plan()) is deleted first: the old
    mode's builds are not reused (a bare-metal build that is gone cannot be repackaged), and what its objects use on
    each disk counts as free there. Reads only (W2: removing the old mode first is offered only while it may help)."""
    gone = frozenset(item.full_path for item in plan.items if item.full_path)
    estimate = space_needed(rt, det, mode, placement, backend, container_build, gone=gone)
    freed = _freed_by(rt, plan)
    estimate.checks = [replace(c, free=c.free + freed.get(c.device, 0)) for c in estimate.checks]
    estimate.freed = sum(freed.values())
    return estimate


# --- every object of a mode ------------------------------------------------------------------------------------------

def _folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += st.st_size
    return total


def other_mode(mode: str) -> str:
    return "docker" if mode == "bare-metal" else "bare-metal"


def _build_items(rt: Any, of: str, include_running: bool) -> List[Any]:
    """Ditto's llama.cpp build folders that belong to the mode `of` (never a build of a board's runtime, which shares
    the store): bare metal's are the builds verified on this computer and the ones it uses, and the official archives;
    Docker mode's are the builds verified only in Ditto's container. A build a running server uses is left out unless
    the server is stopped first (`include_running`)."""
    from orchestrator.runtime.build.host import PURPOSE
    from orchestrator.tools.spec import DeletionItem
    store = rt.store
    refs = store.references()
    running = set() if include_running else store.running_build_ids()
    items = []
    for build in store.list_builds():
        if build.purpose != PURPOSE or build.build_id in running:
            continue
        role = (" (the active bare-metal build)" if build.build_id == refs.get("active") else
                " (the bare-metal CPU fallback)" if build.build_id == refs.get("fallback") else "")
        bare = bool(role) or build.verified_in == "native"
        if (of == "bare-metal") != bare:
            continue
        if of == "bare-metal":
            image = "; Ditto's image holds its programs" if build.build_id == refs.get("docker") else ""
            what = f"Ditto's bare-metal {build.backend} build of llama-server{role}{image}"
        else:
            what = (f"a {build.backend} build of llama-server verified only in Ditto's container"
                    + ("; Ditto's image was made from it" if build.build_id == refs.get("docker") else ""))
        items.append(DeletionItem(kind="runtime_build", speaking_name=f"llama.cpp build {build.build_id[:16]} "
                                  f"({build.backend})", full_path=str(build.path), file_name=build.path.name,
                                  size_bytes=_folder_size(build.path), what=f"{what}; build ID {build.build_id}"))
    if of == "bare-metal":
        for manifest in sorted(Path(rt.root).glob("b*-*/runtime.json")):
            folder = manifest.parent
            items.append(DeletionItem(kind="runtime_build", speaking_name=f"official llama.cpp archive {folder.name} "
                                      f"({PREBUILT_LABEL})", full_path=str(folder), file_name=folder.name,
                                      size_bytes=_folder_size(folder),
                                      what="the official prebuilt archive installed for bare metal"))
    return items


def _docker_items(rt: Any, keep_in_use: bool, include_running: bool) -> List[Any]:
    """Ditto's images and containers: all of them, or (`keep_in_use`, Docker mode runs) every one but the verified
    image in use and its runtime base. A running server container is left out unless the server is stopped first."""
    from orchestrator.tools.spec import DeletionItem
    try:
        docker = rt.docker()
        if docker.unavailable():
            return []
        images, containers = docker.images(), docker.containers()
    except Exception:  # noqa: BLE001 - no Docker: nothing of it to list
        return []
    keep: set = set()
    saved = rt.mode_record().get("docker") or {}
    if keep_in_use and saved.get("image_id"):
        keep.add(saved["image_id"])
        used = next((i for i in images if i.image_id == saved["image_id"]), None)
        if used is not None:
            keep |= {i.image_id for i in images if i.role == "base"
                     and i.labels.get(cs.LABEL_BASE) == used.labels.get(cs.LABEL_BASE)
                     and i.labels.get(cs.LABEL_BACKEND) == used.labels.get(cs.LABEL_BACKEND)}
    items = []
    for c in containers:
        if c.status == "running" and c.role == "server" and not include_running:
            continue  # a running server is stopped by the switch itself; one that still runs is not listed
        items.append(DeletionItem(kind="docker_container",
                                  speaking_name=f"Ditto container {c.name or c.container_id[:12]}",
                                  file_name=c.container_id, size_bytes=c.size,
                                  what=f"a {c.status or 'stopped'} container of Ditto's {c.role or 'runtime'}"))
    for image in sorted(images, key=lambda i: (i.role != "image", i.name)):
        if image.image_id in keep:
            continue
        if image.role == "image":
            what = (f"Ditto's image with build {str(image.labels.get(cs.LABEL_BUILD, ''))[:16]} "
                    f"({image.labels.get(cs.LABEL_BACKEND, '?')}, {image.labels.get(cs.LABEL_MADE_BY, '?')}, on "
                    f"{image.labels.get(cs.LABEL_BASE, '?')})")
        else:
            what = (f"the runtime base Ditto's images are built on ({image.labels.get(cs.LABEL_BASE, '?')} with the "
                    "runtime libraries)")
        items.append(DeletionItem(kind="docker_image", speaking_name=f"Ditto image {image.name}",
                                  file_name=image.image_id, size_bytes=image.size, what=what))
    return items


# The old mode's records (W2: "A's recorded runtime entry"): the keys of active.json and mode.json that name it. The
# files stay: they also hold the new mode and the model tier.
BARE_METAL_KEYS = ("build_id", "fallback_build_id", "use", "variant", "asset_id")
DOCKER_ACTIVE_KEYS = ("docker_build_id",)
DOCKER_MODE_KEYS = ("docker",)


def _entries_size(data: Dict[str, Any], keys: Tuple[str, ...]) -> int:
    present = {k: data[k] for k in keys if k in data}
    return len(json.dumps(present, sort_keys=True).encode("utf-8")) if present else 0


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_items(rt: Any, of: str) -> List[Any]:
    """The old mode's records: bare metal's entries in active.json (active build, CPU fallback, the official archive
    chosen); Docker mode's entry in mode.json (the image it used) and its build reference in active.json."""
    from orchestrator.tools.spec import DeletionItem
    active, mode_file = Path(rt.root) / "active.json", Path(rt.root) / "mode.json"
    items = []
    wanted = ([(active, BARE_METAL_KEYS, "the bare-metal entries of active.json (active build, CPU fallback, the "
                                         "official archive chosen); the file stays")]
              if of == "bare-metal" else
              [(mode_file, DOCKER_MODE_KEYS, "the Docker-mode entry of mode.json (the image Ditto's server used); the "
                                             "file stays"),
               (active, DOCKER_ACTIVE_KEYS, "the entry of active.json that names the build of Ditto's image; the "
                                            "file stays")])
    for path, keys, what in wanted:
        size = _entries_size(_read(path), keys)
        if size:
            items.append(DeletionItem(kind="runtime_record", speaking_name=f"{mode_label(of)} record in {path.name}",
                                      full_path=str(path), file_name=path.name, size_bytes=size,
                                      what=f"{what}; keys {', '.join(k for k in keys if k in _read(path))}"))
    return items


def _log_items(rt: Any, of: str) -> List[Any]:
    """The log of the builds of Ditto's image (Docker mode only)."""
    from orchestrator.tools.spec import DeletionItem
    log = Path(rt.data_dir) / "logs" / IMAGE_LOG
    if of != "docker" or not log.is_file():
        return []
    return [DeletionItem(kind="runtime_record", speaking_name=f"log of Ditto's image builds ({IMAGE_LOG})",
                         full_path=str(log), file_name=log.name, size_bytes=log.stat().st_size,
                         what="the log of building Ditto's Docker images")]


def mode_objects(rt: Any, of: str, *, in_use: bool) -> List[Any]:
    """Every object of the mode `of` on this computer (W2: no leftovers): its build folders, Ditto's images and
    containers, its records and logs. `in_use`: the other mode runs now, so what that mode uses is left out (the
    image in use, the bare-metal build in use, a running server); else (`of` is the mode Ditto is recorded in, removed
    before the new one is built) everything of it is listed and its server is stopped before the removal."""
    if of not in MODES:
        raise RuntimeModeError("the mode is bare-metal or docker")
    whole = not in_use
    items = _build_items(rt, of, include_running=whole)
    if of == "docker":
        items += _docker_items(rt, keep_in_use=False, include_running=whole)
    return items + _record_items(rt, of) + _log_items(rt, of)


def _plan(items: List[Any]) -> Optional[Any]:
    from orchestrator.tools.spec import DeletionPlan
    if not items:
        return None
    return DeletionPlan(items=items, total_bytes=sum(i.size_bytes for i in items), mode_offered=("permanent",))


def leftovers(rt: Any, mode: str) -> Optional[Any]:
    """Every object of the other mode while Ditto runs in `mode`, as a DeletionPlan for the person (None: nothing
    left). Deleted for good, never into the trash (W2). Reads only."""
    if mode not in MODES:
        raise RuntimeModeError("the mode is bare-metal or docker")
    items = mode_objects(rt, other_mode(mode), in_use=True)
    if mode == "docker":   # Docker mode runs: the images it does not use belong to no mode any more
        items += _docker_items(rt, keep_in_use=True, include_running=False)
    return _plan(items)


def old_mode_plan(rt: Any, old: Optional[str]) -> Optional[Any]:
    """Every object of the mode Ditto is recorded in, for the person who removes it before the new mode is built (a
    switch that does not fit next to it): its server is stopped first. None when nothing of it is left."""
    if old not in MODES:
        return None
    return _plan(mode_objects(rt, old, in_use=False))


def _new_mode_works(rt: Any) -> Optional[str]:
    """Why the mode Ditto is recorded in does not work (None: installed and verified)."""
    det = detect(rt)
    found = det.get(rt.mode())
    if found is None:
        return f"{mode_label(rt.mode())} is not installed"
    if not found.verified:
        return f"{mode_label(rt.mode())} has not passed its check yet"
    return None


def remove_leftovers(rt: Any, plan: Any, *, actor: str, audit: Any, before_switch: bool = False,
                     removes: Optional[str] = None) -> Removal:
    """Deletes exactly the objects of `plan` for good, which the person confirmed once in the framework's own window
    (actor "cli" or "gui"; never an AI, never under a delegation). The list is looked up again first: if it changed,
    nothing is removed (LeftoversChanged).

    Normally `plan` is leftovers(): every object of the old mode, removed only while the new mode is installed and
    verified (W2: A is removed after B works). With `before_switch` it is old_mode_plan() of `removes` (default the
    recorded mode): the person chose to remove the old mode before the new one is built (it did not fit next to it);
    Ditto's server is stopped first, and Ditto is unavailable until the new mode works.

    The removal is audited before anything is removed (nothing is removed when the audit cannot be written); each
    object is looked at again right before its removal and kept when it changed (a build a running server uses, an
    image that lost Ditto's label, a container that runs)."""
    from orchestrator.Core.deletion import AcceptedDeletion
    if actor not in PERSON_ACTORS:
        raise PermissionError("Only the person removes the old mode, in the GUI or at a terminal.")
    removes = (removes or rt.mode()) if before_switch else other_mode(rt.mode())
    if not before_switch:
        problem = _new_mode_works(rt)
        if problem:
            raise RuntimeModeError(f"The old mode is removed only after the new one works, and {problem}; nothing was "
                                   "removed.")
    current = old_mode_plan(rt, removes) if before_switch else leftovers(rt, rt.mode())
    if current is None or current.sha256() != plan.sha256():
        raise LeftoversChanged()
    accepted = AcceptedDeletion(plan, "permanent", actor)
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeModeError("The audit log is not available, so nothing was removed.")
    extra = accepted.audit_extra()
    try:
        audit.record(actor, "ditto_runtime_mode", "framework", "person", "started", approved=True,
                     arguments={"mode": rt.mode(), "removes": removes, "items": len(plan.items),
                                "bytes": plan.total_bytes, "deletion_mode": "permanent",
                                "before_switch": bool(before_switch)},
                     detail=f"removing every object of the {mode_label(removes)} mode ({len(plan.items)})"
                            + (" before the new mode is built" if before_switch else " after the new mode works"),
                     extra=extra)
    except Exception:  # noqa: BLE001
        raise RuntimeModeError("The audit log cannot record the removal; nothing was removed.") from None
    result = Removal()
    if before_switch:
        result.stopped = _stop_server(rt)
    order = {"docker_container": 0, "docker_image": 1, "runtime_build": 2, "runtime_record": 3}
    items = sorted(plan.items, key=lambda i: (order.get(i.kind, 4), cs.BASE_REPO in i.speaking_name))
    for item in items:
        try:
            kept = _remove_item(rt, item, removes, result.remaining)
        except Exception as e:  # noqa: BLE001 - e.g. Docker refused: kept and reported
            from orchestrator.utils.helpers import redact_secrets
            kept = redact_secrets(str(getattr(e, "explanation", "") or e))[:300]
        if kept:
            result.failed.append((item.speaking_name, kept))
        else:
            result.deleted.append(item.speaking_name)
            result.freed_bytes += item.size_bytes
    try:
        audit.record(actor, "ditto_runtime_mode", "framework", "person", "error" if result.failed else "ok",
                     approved=True, arguments={"deleted": len(result.deleted), "kept": len(result.failed)},
                     detail=f"{len(result.deleted)} objects of the {mode_label(removes)} mode deleted, "
                            f"{len(result.failed)} kept", extra=extra)
    except Exception:  # noqa: BLE001 - the removal happened and is shown to the person; its start is recorded
        pass
    return result


def _forget_keys(rt: Any, path: Path, keys: Tuple[str, ...]) -> None:
    """Drops the old mode's keys from one of Ditto's records (the file stays)."""
    from orchestrator.utils.atomic import write_private_exclusive as _write_private
    with rt._lock():
        data = _read(path)
        kept = {k: v for k, v in data.items() if k not in keys}
        if kept != data:
            _write_private(path, json.dumps(kept, sort_keys=True))


def _remove_item(rt: Any, item: Any, removes: str,
                 remaining: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    """Deletes one listed object for good; returns why it was kept, or None only when it is really gone. A build folder
    of which anything stays is kept ("kept: could not remove N items", relative names: Core/deletion.kept_reason), and
    its remaining full paths go into `remaining` for the person's own text."""
    from orchestrator.Core.deletion import kept_reason
    from orchestrator.runtime.build.source import remove_tree
    root = Path(rt.root).resolve()
    if item.kind == "runtime_build":
        path = Path(item.full_path)
        if path.is_symlink() or not path.is_dir() or path.resolve().parent not in (root, root / "builds"):
            return "kept: it is not a build folder of Ditto's runtime"
        store = rt.store
        if path.parent.resolve() == (root / "builds") and path.name in {b[:16] for b in store.running_build_ids()}:
            return "kept: a running server uses it"
        left = remove_tree(path)
        if left:
            if remaining is not None:
                remaining.setdefault(item.speaking_name, []).extend(str(p) for p in left)
            return kept_reason(path, left)
        if path.parent.resolve() == (root / "builds"):
            store.forget(path.name)
        return None
    if item.kind == "runtime_record":
        path = Path(item.full_path)
        if path.name == IMAGE_LOG and path.parent.resolve() == (Path(rt.data_dir) / "logs").resolve():
            left = remove_tree(path)
            if left:
                if remaining is not None:
                    remaining.setdefault(item.speaking_name, []).extend(str(p) for p in left)
                return kept_reason(path, left)
            return None
        if path.resolve().parent != root or path.name not in ("active.json", "mode.json"):
            return "kept: it is not one of Ditto's records"
        if path.name == "mode.json":
            _forget_keys(rt, path, DOCKER_MODE_KEYS)
        else:
            _forget_keys(rt, path, BARE_METAL_KEYS if removes == "bare-metal" else DOCKER_ACTIVE_KEYS)
        return None
    docker = rt.docker()
    if item.kind == "docker_image":
        image = docker.image(item.file_name)
        if image is None or image.image_id != item.file_name or image.role not in ("base", "image"):
            return "kept: it could not be found again as Ditto's image, so it was not removed"
        docker.remove_image(item.file_name)
        return None
    if item.kind == "docker_container":
        info = docker.container(item.file_name)
        if info is None and not docker.unavailable():
            return None   # gone already (a server container is removed when it stops)
        if info is None or not info.role:
            return "kept: it could not be found again as Ditto's container"
        if info.status == "running":
            return "kept: it runs (stop it first: llm-cli ditto runtime stop)"
        docker.remove_container(item.file_name)
        return None
    return "kept: not an object of Ditto's runtime"


__all__ = ["CHOICES", "Detection", "EnsureResult", "Found", "LeftoversChanged", "MODES", "Removal",
           "RuntimeModeError", "SpaceEstimate", "SpaceShortage", "base_for_build", "detect", "docker_problem",
           "ensure", "image_base", "leftovers", "mode_label", "mode_objects", "old_mode_plan", "other_mode",
           "profile_for_base", "remove_leftovers", "space_after_removal", "space_needed"]
