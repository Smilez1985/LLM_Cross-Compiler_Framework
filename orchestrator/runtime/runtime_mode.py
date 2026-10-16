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
- After the new mode is verified, leftovers() lists the old mode's objects as a DeletionPlan: every build folder with
  its full path, build ID, folder name and size, every image and container with its name, ID and size, and what each
  is. Nothing is removed here: the person confirms exactly that list in the framework's own window (CLI or GUI, "No"
  by default), and remove_leftovers() removes exactly that list. Without a terminal, and for an AI, nothing is removed
  and the list is reported. Ditto's images are never deleted otherwise.

An AI may read the mode (get_status, local_runtime); switching it and removing the old mode are the person's alone:
no tool calls ensure() or remove_leftovers(), and remove_leftovers() refuses every actor but the person's CLI and GUI.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.runtime import container_server as cs
from orchestrator.runtime.llama_server import PREBUILT_LABEL, LocalRuntimeError

MODES = ("bare-metal", "docker")
# Licences a model may be downloaded under without the person's own yes at a terminal when they chose an unattended run
# (`ensure --yes`, the installers' LCF_DITTO); any other licence is the person's to accept (round 5 a: legal warnings
# stay with the person).
PERMISSIVE_LICENCES = ("apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause")
CHOICES = ("auto", *MODES)
PERSON_ACTORS = ("cli", "gui")
IMAGE_LOG = "ditto-image.log"


class RuntimeModeError(RuntimeError):
    """A mode cannot be made here (the message says why and what to do; safe to show)."""


class LeftoversChanged(RuntimeError):
    def __init__(self):
        super().__init__("The list changed after it was shown (something was built, removed or started meanwhile); "
                         "nothing was removed. Look at the new list and confirm it.")


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
    leftovers: Optional[Any] = None      # DeletionPlan of the old mode's objects (nothing removed yet)
    problems: List[str] = field(default_factory=list)


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
    deleted: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    failed: List[Tuple[str, str]] = field(default_factory=list)


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
    and stay for the next try). Removes nothing: the old mode's leftovers are in the result for the person."""
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
    if target is not None and (mode == "bare-metal" or target.verified):
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
    return EnsureResult(action, mode, list(lines), leftovers=plan)


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
            image = cs.build_runtime_image(docker, build, base, "repackaged", log_file)
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
    runtime_base = cs.build_runtime_base(docker, base, chosen.backend, log_file)
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
    image = cs.build_runtime_image(docker, stored, base, "rebuilt", log_file)
    report = cs.verify_image(docker, image, pin, gpu)
    if not report.ok:
        raise RuntimeModeError(f"Ditto's image {image.name} did not pass its check in a container: {report.summary()}. "
                               "It is not used; it stays until you remove it.")
    rt.store.keep_for_docker(stored.build_id)
    lines.append(f"Built in the builder container: build {stored.build_id[:16]} ({stored.backend}) in the image "
                 f"{image.name} on {base.id}; it passed its checks in a container ({gpu.note}).")
    return _image_record(image)


# --- the old mode's leftovers -----------------------------------------------------------------------------------------

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


def _build_items(rt: Any, mode: str) -> List[Any]:
    """Ditto's llama.cpp build folders that the mode `mode` does not use (never a build of a board's runtime, which
    shares the store, and never the build a running server uses)."""
    from orchestrator.runtime.build.host import PURPOSE
    from orchestrator.tools.spec import DeletionItem
    store = rt.store
    refs = store.references()
    running = store.running_build_ids()
    items = []
    for build in store.list_builds():
        if build.purpose != PURPOSE or build.build_id in running:
            continue
        in_use = build.build_id in (refs.get("active"), refs.get("fallback"))
        if mode == "bare-metal" and (in_use or build.verified_in == "native"):
            continue
        if mode == "bare-metal":
            what = (f"a {build.backend} build of llama-server verified only in Ditto's container; bare metal does not "
                    "use it")
        elif build.build_id == refs.get("docker"):
            what = (f"the {build.backend} build Ditto's image was made from; the image holds its programs, so Docker "
                    "mode does not need this folder (keeping it makes a switch back to bare metal quick)")
        else:
            role = " (the active bare-metal build)" if build.build_id == refs.get("active") else \
                " (the bare-metal CPU fallback)" if build.build_id == refs.get("fallback") else ""
            what = f"Ditto's bare-metal {build.backend} build of llama-server{role}; Docker mode does not use it"
        items.append(DeletionItem(kind="runtime_build", speaking_name=f"llama.cpp build {build.build_id[:16]} "
                                  f"({build.backend})", full_path=str(build.path), file_name=build.path.name,
                                  size_bytes=_folder_size(build.path), what=f"{what}; build ID {build.build_id}"))
    if mode == "docker":
        for manifest in sorted(Path(rt.root).glob("b*-*/runtime.json")):
            folder = manifest.parent
            items.append(DeletionItem(kind="runtime_build", speaking_name=f"official llama.cpp archive {folder.name} "
                                      f"({PREBUILT_LABEL})", full_path=str(folder), file_name=folder.name,
                                      size_bytes=_folder_size(folder),
                                      what="the official prebuilt archive you installed for bare metal; Docker mode "
                                           "does not use it"))
    return items


def _docker_items(rt: Any, mode: str) -> List[Any]:
    """Ditto's images and containers the mode `mode` does not use: in bare metal all of them, in Docker every one but
    the verified image in use and its runtime base."""
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
    if mode == "docker" and saved.get("image_id"):
        keep.add(saved["image_id"])
        used = next((i for i in images if i.image_id == saved["image_id"]), None)
        if used is not None:
            keep |= {i.image_id for i in images if i.role == "base"
                     and i.labels.get(cs.LABEL_BASE) == used.labels.get(cs.LABEL_BASE)
                     and i.labels.get(cs.LABEL_BACKEND) == used.labels.get(cs.LABEL_BACKEND)}
    items = []
    for c in containers:
        if c.status == "running" and c.role == "server":
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
        why = "bare metal does not use it" if mode == "bare-metal" else "not the image in use"
        items.append(DeletionItem(kind="docker_image", speaking_name=f"Ditto image {image.name}",
                                  file_name=image.image_id, size_bytes=image.size, what=f"{what}; {why}"))
    return items


def leftovers(rt: Any, mode: str) -> Optional[Any]:
    """The objects of the other mode when Ditto runs in `mode`, as a DeletionPlan for the person (None: nothing).
    Reads only."""
    from orchestrator.tools.spec import DeletionPlan
    if mode not in MODES:
        raise RuntimeModeError("the mode is bare-metal or docker")
    items = _build_items(rt, mode) + _docker_items(rt, mode)
    if not items:
        return None
    return DeletionPlan(items=items, total_bytes=sum(i.size_bytes for i in items), mode_offered=("permanent",))


def remove_leftovers(rt: Any, plan: Any, *, actor: str, audit: Any) -> Removal:
    """Removes exactly the objects of `plan`, which the person confirmed in the framework's own window (actor "cli" or
    "gui"; never an AI, never under a delegation). The list is looked up again first: if it changed, nothing is
    removed (LeftoversChanged). The removal is audited before anything is removed (nothing is removed when the audit
    cannot be written); each object is looked at again right before its removal and kept when it changed (a build a
    running server uses, an image that lost Ditto's label, a container that runs)."""
    if actor not in PERSON_ACTORS:
        raise PermissionError("Only the person removes the old mode's leftovers, in the GUI or at a terminal.")
    current = leftovers(rt, rt.mode())
    if current is None or current.sha256() != plan.sha256():
        raise LeftoversChanged()
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeModeError("The audit log is not available, so nothing was removed.")
    extra = {"answered_by": "person", "confirm_channel": f"framework-{actor}", "deletion_sha256": plan.sha256()}
    try:
        audit.record(actor, "ditto_runtime_mode", "framework", "person", "started", approved=True,
                     arguments={"mode": rt.mode(), "items": len(plan.items), "bytes": plan.total_bytes},
                     detail=f"removing {len(plan.items)} leftovers of the old runtime mode", extra=extra)
    except Exception:  # noqa: BLE001
        raise RuntimeModeError("The audit log cannot record the removal; nothing was removed.") from None
    result = Removal()
    order = {"docker_container": 0, "docker_image": 1, "runtime_build": 2}
    items = sorted(plan.items, key=lambda i: (order.get(i.kind, 3), cs.BASE_REPO in i.speaking_name))
    for item in items:
        try:
            kept = _remove_item(rt, item)
        except Exception as e:  # noqa: BLE001 - e.g. Docker refused: kept and reported
            from orchestrator.utils.helpers import redact_secrets
            kept = redact_secrets(str(getattr(e, "explanation", "") or e))[:300]
        if kept:
            result.failed.append((item.speaking_name, kept))
            continue
        result.deleted.append(item.speaking_name)
        result.freed_bytes += item.size_bytes
    try:
        audit.record(actor, "ditto_runtime_mode", "framework", "person", "error" if result.failed else "ok",
                     approved=True, arguments={"deleted": len(result.deleted), "kept": len(result.failed)},
                     detail=f"{len(result.deleted)} leftovers removed, {len(result.failed)} kept", extra=extra)
    except Exception:  # noqa: BLE001 - the removal happened and is shown to the person; its start is recorded
        pass
    return result


def _remove_item(rt: Any, item: Any) -> Optional[str]:
    """Removes one listed object; returns why it was kept, or None."""
    from orchestrator.runtime.build.source import remove_tree
    if item.kind == "runtime_build":
        path = Path(item.full_path)
        root = Path(rt.root).resolve()
        if path.is_symlink() or not path.is_dir() or path.resolve().parent not in (root, root / "builds"):
            return "kept: it is not a build folder of Ditto's runtime"
        store = rt.store
        if path.parent.resolve() == (root / "builds") and path.name in {b[:16] for b in store.running_build_ids()}:
            return "kept: a running server uses it"
        remove_tree(path)
        if path.parent.resolve() == (root / "builds"):
            store.forget(path.name)
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
        if info is None or not info.role:
            return "kept: it could not be found again as Ditto's container"
        if info.status == "running":
            return "kept: it runs (stop it first: llm-cli ditto runtime stop)"
        docker.remove_container(item.file_name)
        return None
    return "kept: not an object of Ditto's runtime"


__all__ = ["CHOICES", "Detection", "EnsureResult", "Found", "LeftoversChanged", "MODES", "Removal",
           "RuntimeModeError", "base_for_build", "detect", "docker_problem", "ensure", "image_base", "leftovers",
           "mode_label", "profile_for_base", "remove_leftovers"]
