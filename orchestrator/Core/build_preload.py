#!/usr/bin/env python3
"""
The build's preload (docs/DESIGN.md, Installer, platforms and dependencies): an offline build whose build container
image is not on this computer and whose sources are not all local (Core/build_image.offline_plan) writes a preload
script for exactly those sources, the person runs it on a computer with a network, and the archive it writes is taken
in here as local sources under the one locality rule. The format is the installer's preload (scripts/preload_lock.py:
one llmcf-preload/ folder, manifest.txt, SHA256SUMS), of kind build.

- recipe(): what the preload fetches, from the plan's missing sources: base images, the pinned llama.cpp commit, the
  build image's Python packages (target.yml build_preload), pinned downloads of the Dockerfile, the files taken from
  source images (target.yml template_variables.source_images), the image's distribution packages (read from its
  Dockerfile by Core/image_packages.py, each stage against what its base image has, in the board preload's packages/
  folder, scripts/deb_preload.py); and what it cannot carry (not_covered).
- write(): preload.sh and preload.ps1 for that recipe into the person's folder, the recipe's sha256 recorded.
- take(): checks an archive (the installer's checks, then kind, target, platform, a recipe written here, the image
  names, the llama.cpp commit, every pinned sha256, and the distribution packages as a board preload is checked:
  pinned keys, gpgv where it is, lists and .deb files) and takes it in: images loaded into the container engine,
  llama.cpp into the source store, wheels, downloads, taken files and packages into the person's state folder.
- stage() puts the packages into the build context as a file repository signed by the pinned keys (sources/apt), which
  the target Dockerfiles use with the build argument APT_PRELOAD instead of the image's own package sources.
- the build engine asks preload_sources() for the plan and stage() for the build context; image_files() takes the
  files of source images (online through the registry, offline from the person's cache).
- take_image_file(): the files of a source image published only as a file (a docker save or OCI image layout archive
  pinned by its sha256), from the file the person gives, read by the same reader with the same checks.

Taking from source images reads the image without running it (scripts/image_files.py); a file whose
licence forbids passing it on stays with the person (their cache, their image, their own preload).
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from orchestrator.utils.next_steps import NextStep, WithSteps

STORE = "build-preload"
SCRIPTS = "llmcf-preload-scripts"
RECIPES = "recipes.txt"
IMAGE_FILES = "image-files"
CONTEXT_WHEELS = "wheels"          # sources/wheels in the build context (PIP_FIND_LINKS)
CONTEXT_FILES = "files"            # sources/files/NAME: pinned downloads the Dockerfile takes from the context
CONTEXT_IMAGE_FILES = "image-files"  # sources/image-files/DEST: copied into the image root by the Dockerfile
IMAGE_SOURCES = "/tmp/llmcf-sources"   # where the target Dockerfiles COPY sources/
CONTEXT_APT = "apt"                # sources/apt: the file repository of the distribution packages
APT_REPOSITORY = "/tmp/llmcf-apt"  # where the target Dockerfiles COPY sources/apt/ (scripts/build_apt_preload.sh)
APT_STORE = "apt-"                 # build-preload/TARGET/apt-KEY/ARCHIVE: the packages taken in, KEY of the need
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The pinned downloads the module generator writes (module_generator.vendor_blocks): the file, its address, its size
# and its sha256.
_CURL = re.compile(r"-o '(?P<dest>/[^']+)\.part' '(?P<url>https://[^']+)'")
_SIZE = r"wc -c < '{dest}\.part'\)\" -eq (\d+)"
_SUM = r"echo '([0-9a-f]{{64}})  {dest}\.part'"


class PreloadProblem(WithSteps, ValueError):
    """A preload that cannot be written or taken in; `code`: needs_person, invalid_value, mismatch, failed. `next` is
    the plain next step."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_LOADED: Dict[str, Any] = {}


def _script(name: str):
    """A standard-library program of scripts/ (the preload scripts carry them), loaded once."""
    if name not in _LOADED:
        spec = importlib.util.spec_from_file_location(f"llmcf_{name}", _SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED[name] = module
    return _LOADED[name]


def _preload_lock():
    """scripts/preload_lock.py of this installation (the installer's preload; standard library only)."""
    return _script("preload_lock")


def reader():
    """scripts/image_files.py: reads images through the registry without running them (standard library only)."""
    return _script("image_files")


def deb_preload():
    """scripts/deb_preload.py: the board preload's distribution packages (standard library only)."""
    return _script("deb_preload")


def _sha256_file(path: Path) -> str:
    from orchestrator.utils.helpers import sha256_file
    return sha256_file(path)


def _key(section: Any) -> str:
    return hashlib.sha256(json.dumps(section, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# --- where things are ------------------------------------------------------------------------------------------------

def store_root(state_dir: Any) -> Path:
    return Path(state_dir) / STORE


def target_store(state_dir: Any, target: str) -> Path:
    return store_root(state_dir) / re.sub(r"[^a-z0-9._-]", "_", str(target).lower())


def image_cache(state_dir: Any) -> Path:
    return store_root(state_dir) / IMAGE_FILES


def scripts_folder(state_dir: Any, target: str) -> Path:
    return Path(state_dir) / SCRIPTS / f"build-{re.sub(r'[^A-Za-z0-9._-]', '_', str(target))}"


# --- what a target declares ------------------------------------------------------------------------------------------

def _meta(target_dir: Path) -> Dict[str, Any]:
    try:
        meta = yaml.safe_load((Path(target_dir) / "target.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return meta if isinstance(meta, dict) else {}


def declared_pip(target_dir: Path, gpu: bool = False) -> Optional[Dict[str, Any]]:
    """target.yml build_preload: the Python packages of the build image as the recipe holds them (None: none or
    unreadable)."""
    from orchestrator.schemas.target_template import BuildPreload
    block = _meta(target_dir).get("build_preload")
    if not isinstance(block, dict):
        return None
    try:
        return BuildPreload.model_validate(block).section(gpu)
    except ValueError:
        return None


def source_images(target_dir: Path) -> List[Any]:
    """template_variables.source_images of the target (schemas/target_template.SourceImage); [] when none."""
    from orchestrator.schemas.target_template import SourceImage
    record = _meta(target_dir).get("template_variables")
    images = record.get("source_images") if isinstance(record, dict) else None
    out = []
    for entry in images or []:
        try:
            out.append(SourceImage.model_validate(entry))
        except ValueError:
            continue   # the folder check names it (module_generator.template_variable_problems)
    return out


def pinned_downloads(dockerfile_text: str) -> List[Dict[str, Any]]:
    """The pinned downloads of a Dockerfile in the form the module generator writes: name, url, size and sha256."""
    out = []
    for match in _CURL.finditer(dockerfile_text or ""):
        dest, url = match.group("dest"), match.group("url")
        size = re.search(_SIZE.format(dest=re.escape(dest)), dockerfile_text)
        digest = re.search(_SUM.format(dest=re.escape(dest)), dockerfile_text)
        if size and digest:
            out.append({"name": dest.rsplit("/", 1)[-1], "url": url, "size": int(size.group(1)),
                        "sha256": digest.group(1)})
    return out


def missing_image_files(state_dir: Any, images: List[Any]) -> List[Any]:
    """The source images with a named file that is not in the person's cache."""
    cache = image_cache(state_dir)
    return [image for image in images if any(not (cache / f.sha256).is_file() for f in image.files)]


# --- what the plan takes from the preload -----------------------------------------------------------------------------

@dataclass
class PreloadSources:
    """What the person's taken-in preloads give an offline build of one target: the wheels folder of the target's
    current pip declaration (or None), the pinned downloads present (url -> file), the source images whose files
    are all in the cache, and the distribution packages of the Dockerfile's current package need (or None)."""
    wheels: Optional[Path] = None
    downloads: Dict[str, Path] = field(default_factory=dict)
    images_missing: List[str] = field(default_factory=list)
    apt: Optional[Path] = None
    image_labels: Dict[str, str] = field(default_factory=dict)   # key of a missing image -> how the person reads it


def apt_store(state_dir: Any, target: str, dockerfile_text: str, platform: str = "") -> Optional[Path]:
    """The folder the distribution packages of the Dockerfile's current need are kept in (None: no need this computer
    can read)."""
    from orchestrator.Core.image_packages import dpkg_arch, need, need_key
    arch = dpkg_arch(platform or reader().host_platform())
    if not arch:
        return None
    found, problems = need(dockerfile_text or "", arch)
    if problems or not found:
        return None
    return target_store(state_dir, target) / f"{APT_STORE}{need_key(found, arch)}"


def _newest_apt(folder: Optional[Path]) -> Optional[Path]:
    if folder is None or not folder.is_dir():
        return None
    taken = [p for p in folder.iterdir() if p.is_dir() and (p / "sources.txt").is_file()]
    return max(taken, key=lambda p: p.stat().st_mtime) if taken else None


def preload_sources(state_dir: Any, target: str, target_dir: Path, dockerfile_text: str,
                    gpu: bool = False) -> PreloadSources:
    found = PreloadSources()
    if state_dir is None:
        return found
    store = target_store(state_dir, target)
    pip = declared_pip(target_dir, gpu)
    if pip is not None:
        folder = store / f"wheels-{_key(pip)[:16]}"
        if folder.is_dir() and any(folder.iterdir()):
            found.wheels = folder
    for item in pinned_downloads(dockerfile_text):
        path = store / CONTEXT_FILES / item["name"]
        if path.is_file() and path.stat().st_size == item["size"]:
            found.downloads[item["url"]] = path
    for image in missing_image_files(state_dir, source_images(target_dir)):
        found.images_missing.append(image.key)
        found.image_labels[image.key] = image.label
    found.apt = _newest_apt(apt_store(state_dir, target, dockerfile_text))
    return found


def file_repository(folder: Path, dest: Path) -> List[str]:
    """The packages/ folder of a build's preload (checked at take-in) as an apt file repository in `dest`: each suite's
    Release and Release.gpg at N/dists/SUITE/, its lists below them and its pool at N/pool/ unchanged (the signed
    hashes still hold), keyring.gpg with the pinned archive keys only, llmcf-preload.sources (deb822, file: addresses,
    Signed-By that keyring, never trusted=yes) and the helper llmcf-apt.sh the Dockerfiles run. Returns the suites."""
    deb = deb_preload()
    folder, dest = Path(folder), Path(dest)
    lines = (folder / "sources.txt").read_text(encoding="utf-8").splitlines()
    ring = deb.pinned_keyring(p.read_bytes() for p in sorted((folder / "keys").glob("*")))
    keys = deb.folder_keys(folder)
    if not ring or not keys:
        raise PreloadProblem("The distribution packages of the preload hold no pinned archive key.", "mismatch")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "keyring.gpg").write_bytes(ring)
    stanzas, suites = [], []
    for line in lines:
        parts = line.split()
        if len(parts) < 4 or not parts[0].isdigit() or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", parts[2]):
            raise PreloadProblem(f"sources.txt of the preload has a line that is not a suite: {line[:80]}", "mismatch")
        number, suite = parts[0], parts[2]
        top = folder / number
        if not top.is_dir():
            continue
        dists = dest / number / "dists" / suite
        dists.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(top / "Release", dists / "Release")
        # only the signatures of the pinned keys: gpgv in the build fails on one whose key it does not have
        signature = deb.pinned_signature((top / "Release").read_bytes(), (top / "Release.gpg").read_bytes(), keys)
        if not signature:
            raise PreloadProblem(f"Suite {number} of the preload is not signed by a pinned archive key.",
                                 "mismatch")
        (dists / "Release.gpg").write_bytes(signature)
        components = []
        for packages in sorted(top.glob("*/binary-*/Packages")):
            component = packages.parent.parent.name
            (dists / component / packages.parent.name).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(packages, dists / component / packages.parent.name / "Packages")
            components.append(component)
        if (top / "pool").is_dir():
            shutil.copytree(top / "pool", dest / number / "pool", dirs_exist_ok=True, copy_function=shutil.copyfile)
        stanzas.append(f"Types: deb\nURIs: file:{APT_REPOSITORY}/{number}\nSuites: {suite}\n"
                       f"Components: {' '.join(dict.fromkeys(components))}\nSigned-By: {APT_REPOSITORY}/keyring.gpg\n"
                       "Check-Valid-Until: no\nCheck-Date: no\nBy-Hash: no\n")
        suites.append(suite)
    if not stanzas:
        raise PreloadProblem("The distribution packages of the preload name no suite.", "mismatch")
    (dest / "llmcf-preload.sources").write_bytes("\n".join(stanzas).encode("utf-8"))
    (dest / "llmcf-apt.sh").write_bytes((_SCRIPTS / "build_apt_preload.sh").read_bytes())
    return suites


def stage(context: Path, plan: Any) -> None:
    """Puts what the plan takes from the build's preload into the build context's sources/ (wheels/, files/, and the
    distribution packages as a file repository in apt/)."""
    sources = Path(context) / "sources"
    for kind, path in getattr(plan, "preload_stage", None) or []:
        if kind == CONTEXT_WHEELS:
            shutil.copytree(path, sources / CONTEXT_WHEELS, dirs_exist_ok=True, copy_function=shutil.copyfile)
        elif kind == CONTEXT_APT:
            file_repository(path, sources / CONTEXT_APT)
        else:
            (sources / CONTEXT_FILES).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, sources / CONTEXT_FILES / Path(path).name)


# --- the files of source images --------------------------------------------------------------------------------------

def image_files(state_dir: Any, target_dir: Path, context: Path, *, online: bool, log=None) -> List[str]:
    """Takes the files the target names from its source images into the build context (sources/image-files/DEST),
    from the person's cache, fetching what is missing through the registry when `online` (the image read, never run;
    the rest discarded). Returns the images whose files are missing offline (the plan names them)."""
    images_reader = reader()
    images = source_images(target_dir)
    if not images:
        return []
    cache = image_cache(state_dir)
    missing = []
    for image in images:
        absent = {f.path: f.sha256 for f in image.files if not (cache / f.sha256).is_file()}
        if absent and not online:
            missing.append(image.key)
            continue
        if absent and image.archive is not None:
            raise image_file_needed(image, Path(target_dir).name)
        if absent:
            if log:
                log(f"Taking {len(absent)} file(s) from {image.image} (its layers read, nothing run; the rest of the "
                    "image is discarded)")
            try:
                images_reader.take_files(image.image, absent, cache, image.platform or "")
            except images_reader.ImageFilesError as e:
                raise PreloadProblem(f"The files of the source image could not be taken: {e}", "failed",
                                     "Check the image's digest and the files' paths and sha256 in the target's "
                                     "template variables.") from None
        for item in image.files:
            dest = Path(context) / "sources" / CONTEXT_IMAGE_FILES / item.dest.lstrip("/")
            images_reader.copy_file(cache / item.sha256, dest)
            os.chmod(dest, 0o755 if item.executable else 0o644)  # nosec B103 - a program of the image, no secret
    return missing


def image_file_needed(image: Any, target: str) -> PreloadProblem:
    """The stop of a build whose source image is published only as a file and whose files are not in the cache: no
    build can fetch that file, the person gives it."""
    return PreloadProblem(f"The files of {image.label} are not taken yet. It is published only as a file, which no "
                          "build can fetch; nothing was started.", "needs_person",
                          "Give that file: Take from an image file... in Build Container Images, or bring it with "
                          "the build's preload (its script's --image-file).").with_steps(
        NextStep("build.source_image_take", {"target": target}))


@dataclass
class TakenImageFile:
    """What take_image_file() took: the file's sha256, the source images it is, the files taken into the cache."""
    file: str
    sha256: str
    images: List[str]
    files: int
    personal: bool

    def lines(self) -> List[str]:
        out = [f"{self.file} (sha256 {self.sha256}) checked; {self.files} file(s) taken from "
               f"{', '.join(self.images)} into your cache (its layers read, nothing run or loaded; the rest of the "
               "image was discarded, the file itself is not kept)."]
        if self.personal:
            out.append("They include files whose licence does not allow passing them on: they stay with you.")
        return out


def take_image_file(state_dir: Any, target_dir: Path, archive: Path, *, log=None) -> TakenImageFile:
    """Takes the named files of every source image of the target that the file `archive` is (a docker save or OCI image
    layout archive pinned by its sha256) into the person's cache: the file's sha256 matched first, then read by the
    image reader with every check (scripts/image_files.take_archive_files); nothing loaded, run or kept but the
    named files."""
    images_reader = reader()
    archive = Path(archive).expanduser()
    if not archive.is_file():
        raise PreloadProblem("The image file does not exist.", "invalid_value", "Give the path of the file.")
    named = [image for image in source_images(target_dir) if image.archive is not None]
    if not named:
        raise PreloadProblem(f"The target {Path(target_dir).name} names no source image published as a file.",
                             "invalid_value")
    if log:
        log(f"Checking the sha256 of {archive.name}...")
    digest = images_reader.file_sha256(archive)
    matching = [image for image in named if image.archive.sha256 == digest]
    if not matching:
        raise PreloadProblem(f"{archive.name} has the sha256 {digest}, which is none of the image files the target "
                             f"names ({', '.join(image.label for image in named)}); nothing in it was read.",
                             "mismatch", "Give the file exactly as the vendor publishes it.")
    cache = image_cache(state_dir)
    count = 0
    for image in matching:
        if log:
            log(f"Taking {len(image.files)} file(s) from {image.label} (its layers read, nothing run; the rest of the "
                "image is discarded)")
        try:
            images_reader.take_archive_files(archive, image.archive.sha256, {f.path: f.sha256 for f in image.files},
                                             cache, image.platform or "", image.archive.config or "")
        except images_reader.ImageFilesError as e:
            raise PreloadProblem(f"The files of the source image could not be taken: {e}", "mismatch",
                                 "Check the file and the files' paths and sha256 in the target's template "
                                 "variables.") from None
        count += len(image.files)
    return TakenImageFile(archive.name, digest, [image.label for image in matching], count,
                          any(not f.redistributable for image in matching for f in image.files))


def source_image_rows(state_dir: Any, target_dir: Path) -> List[Dict[str, Any]]:
    """Each source image of the target as the person reads it: how it is named (registry or file), its platform, its
    licence and how many of its files are in the cache."""
    cache = image_cache(state_dir)
    rows = []
    for image in source_images(target_dir):
        rows.append({"label": image.label, "kind": "file" if image.archive is not None else "registry",
                     "pin": image.image or image.archive.sha256, "name": image.archive.name if image.archive else "",
                     "platform": image.platform or reader().host_platform(), "licence_url": image.licence_url or "",
                     "files": len(image.files), "cached": sum((cache / f.sha256).is_file() for f in image.files)})
    return rows


# --- the recipe ------------------------------------------------------------------------------------------------------

def _ref_of(source: Any) -> str:
    return str(getattr(source, "ref", "") or "")


def _apt_section(text: str, platform: str, client: Any, fetched: List[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """(the recipe's distribution packages, "") or (None, why a preload cannot carry them)."""
    from orchestrator.Core.image_packages import base_facts, dpkg_arch, need, need_key
    arch = dpkg_arch(platform)
    if not arch:
        return None, f"a build image for {platform} is not one whose packages the preload knows"
    if not re.search(r"^\s*ARG\s+APT_PRELOAD\b", text or "", re.MULTILINE):
        return None, ("the target's Dockerfile takes no ARG APT_PRELOAD (the file repository of the build's preload); "
                      "use an apt mirror or cache in your own network")
    found, problems = need(text, arch)
    if problems:
        return None, ("the Dockerfile's apt installs cannot be read for certain (" + "; ".join(problems[:3]) + "); use "
                      "an apt mirror or cache in your own network")
    if not found:
        return None, "the Dockerfile names no package its apt installs would fetch"
    distribution = release = None
    stages = []
    for stage in found:
        installed = None
        if stage["image"] not in fetched:
            facts = base_facts(client, stage["image"], arch)
            if facts is None:
                return None, (f"the base image {stage['image']} could not be read on this computer (what it is and has "
                              "installed); bring it onto this computer first")
            if (distribution or release) and (facts["distribution"], facts["release"]) != (distribution, release):
                return None, "the stages' base images are of different releases"
            distribution, release, installed = facts["distribution"], facts["release"], facts["installed"]
        stages.append(dict(stage, installed=installed))
    if distribution is not None and distribution not in ("debian", "ubuntu"):
        return None, (f"the base image is {distribution}, whose archive keys are not pinned; use an apt mirror or "
                      "cache in your own network")
    return {"distribution": distribution, "release": release, "arch": arch, "key": need_key(found, arch),
            "stages": stages}, ""


def recipe(plan: Any, target: str, target_dir: Path, dockerfile_text: str, *, gpu: bool = False,
           platform: str = "", client: Any = None) -> Dict[str, Any]:
    """The build's preload recipe (sources.json) for the plan's missing sources. `client`: the container engine, which
    saves a base image on this computer so its os-release and dpkg status are read (never run)."""
    from orchestrator.utils.locality import url_host
    images_reader = reader()
    missing = [s for s in plan.sources if not s.local]
    kinds = {s.kind for s in missing}
    out: Dict[str, Any] = {"format": "1", "target": target, "platform": platform or images_reader.host_platform(),
                           "images": [], "llama_cpp": None, "llama_cpp_bundle": False, "pip": None, "downloads": [],
                           "source_images": [], "apt": None, "not_covered": []}
    for source in missing:
        if source.kind != "base_image":
            continue
        ref = _ref_of(source)
        try:
            images_reader.load_name(ref)
            out["images"].append(ref)
        except images_reader.ImageFilesError as e:
            out["not_covered"].append(f"{source.label}: {e}")
    pip = declared_pip(target_dir, gpu)
    needs_pin = "llama_cpp" in kinds or bool(pip and any(g.get("llama_cpp_requirements") for g in pip["groups"]))
    if needs_pin:
        from orchestrator.runtime.build.source import catalog_pin
        pin = catalog_pin()
        out["llama_cpp"] = {"repo": pin.repo, "commit": pin.commit, "tag": pin.tag}
        out["llama_cpp_bundle"] = "llama_cpp" in kinds
    if "pip" in kinds:
        if pip is not None:
            out["pip"] = pip
        else:
            out["not_covered"].append("pip packages: the target declares no build_preload in its target.yml; use a "
                                      "pip index in your own network (the build source setting build_pip_index)")
    downloads = pinned_downloads(dockerfile_text)
    for source in missing:
        if source.kind == "download" and source.label.startswith("downloads from "):
            host = source.label[len("downloads from "):]
            mine = [d for d in downloads if url_host(d["url"])[0] == host]
            if mine:
                out["downloads"] += mine
            else:
                out["not_covered"].append(f"{source.label}: the Dockerfile fetches it without a pinned size and "
                                          "sha256, so no preload carries it")
        elif source.kind == "apt":
            out["apt"], why = _apt_section(dockerfile_text, out["platform"], client, out["images"])
            if out["apt"] is None:
                out["not_covered"].append(f"{source.label}: {why}")
        elif source.kind in ("registry_mirror", "download"):
            out["not_covered"].append(f"{source.label}: {source.how}")
    for source in missing:
        if source.kind == "image_files":
            image = next((i for i in source_images(target_dir) if i.key == _ref_of(source)), None)
            if image is not None:
                entry = {"platform": image.platform or out["platform"],
                         "files": [{"path": f.path, "sha256": f.sha256, "redistributable": f.redistributable}
                                   for f in image.files]}
                if image.archive is not None:
                    entry["archive"] = image.archive.model_dump(exclude_none=True)
                else:
                    entry["image"] = image.image
                out["source_images"].append(entry)
    # A vendor file that may not be passed on is never in a preload: the build stages it from the person's own vendor
    # store (Core/vendor_files.py), where the person put it under their own licence acceptance.
    from orchestrator.Core.vendor_files import entries as vendor_entries
    for vendor in vendor_entries(target_dir):
        if vendor.file.dest:
            out["not_covered"].append(f"vendor file {vendor.file.name}: it may not be passed on, so no preload carries "
                                      "it; the build takes it from your own vendor store, where you put it under its "
                                      "licence")
    return out


def recipe_bytes(data: Dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8")


def fetches(data: Dict[str, Any]) -> bool:
    return bool(data["images"] or data["llama_cpp_bundle"] or data["pip"] or data["downloads"]
                or data["source_images"] or data.get("apt"))


def recipe_image_label(entry: Dict[str, Any]) -> str:
    """A source image of a recipe as the person reads it; an image file says that the script needs it."""
    archive = entry.get("archive")
    if isinstance(archive, dict):
        from orchestrator.schemas.target_template import archive_label
        return (archive_label(str(archive.get("name") or ""), str(archive.get("sha256") or "")) + ", which the script "
                "takes only from the file you give it with --image-file")
    return str(entry.get("image") or "").split("@")[0]


def describe(data: Dict[str, Any]) -> str:
    """What the preload fetches, in one line (labels and names; no address of the person's network)."""
    parts = []
    if data["images"]:
        parts.append("base images " + ", ".join(data["images"]))
    if data["llama_cpp_bundle"]:
        parts.append(f"llama.cpp at the pinned commit {data['llama_cpp']['commit'][:12]}")
    if data["pip"]:
        parts.append(f"the build image's Python packages ({len(data['pip']['groups'])} group(s), Python "
                     f"{data['pip']['python']})")
    if data["downloads"]:
        parts.append("pinned downloads " + ", ".join(d["name"] for d in data["downloads"]))
    if data["source_images"]:
        parts.append("files taken from " + ", ".join(recipe_image_label(i) for i in data["source_images"]))
    apt = data.get("apt")
    if apt:
        words = sorted({p for s in apt["stages"] for p in s["packages"]})
        base = f"{apt['distribution']} {apt['release']}" if apt.get("distribution") else "its base image"
        parts.append(f"the build image's distribution packages with their dependencies ({', '.join(words)}; for "
                     f"{base} on {apt['arch']}, checked against the signed package lists)")
    return "; ".join(parts) + f" (for {data['platform']})"


# --- writing the scripts ---------------------------------------------------------------------------------------------

@dataclass
class Written:
    folder: Path
    scripts: Tuple[Path, Path]
    what: str
    recipe_sha256: str
    not_covered: List[str]


def _recorded(state_dir: Any) -> Dict[str, str]:
    """{recipe sha256: target} of the recipes written on this computer for the person."""
    path = store_root(state_dir) / RECIPES
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and SHA256.fullmatch(parts[0]):
            out[parts[0]] = parts[1]
    return out


def write_scripts(state_dir: Any, data: Dict[str, Any], version: str) -> Written:
    """Writes preload.sh and preload.ps1 for the recipe `data` into the person's folder and records the recipe."""
    if not fetches(data):
        raise PreloadProblem("Nothing a preload can bring is missing for this build: "
                             + ("; ".join(data["not_covered"]) or "every source is local") + ".", "invalid_value",
                             "Provide what is named in your own network, or import a build container image exported "
                             "on a computer with a network.")
    lock = _preload_lock()
    body = recipe_bytes(data)
    digest = hashlib.sha256(body).hexdigest()
    folder = scripts_folder(state_dir, data["target"])
    what = describe(data)
    with tempfile.TemporaryDirectory(prefix="llmcf-preload-spec-") as spec:
        spec = Path(spec)
        (spec / "sources.json").write_bytes(body)
        manifest = {"format": lock.FORMAT, "kind": "build", "version": version, "target": data["target"],
                    "platform": data["platform"], "recipe_sha256": digest}
        (spec / "manifest.base").write_text("".join(f"{k}={v}\n" for k, v in manifest.items()), encoding="utf-8")
        try:
            sh, ps = lock.write_build_scripts(spec, folder, what)
        except lock.PreloadError as e:
            raise PreloadProblem(f"The preload scripts could not be written: {e}") from None
    store_root(state_dir).mkdir(parents=True, exist_ok=True)
    if digest not in _recorded(state_dir):
        with open(store_root(state_dir) / RECIPES, "a", encoding="utf-8") as handle:
            handle.write(f"{digest} {data['target']} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    return Written(folder, (sh, ps), what, digest, list(data["not_covered"]))


# --- taking an archive in --------------------------------------------------------------------------------------------

@dataclass
class Taken:
    target: str
    archive_sha256: str
    images: List[str]
    llama_cpp: bool
    wheels: int
    downloads: List[str]
    image_files: int
    personal: bool
    not_covered: List[str]
    packages: List[str] = field(default_factory=list)   # NAME=VERSION of the build image's distribution packages
    packages_for: str = ""                              # "debian bookworm amd64"
    packages_gpgv: bool = False                         # their signatures checked with gpgv too

    def lines(self) -> List[str]:
        out = [f"Preload of {self.target} checked (sha256 {self.archive_sha256}) and taken in:"]
        if self.images:
            out.append("base images loaded: " + ", ".join(self.images))
        if self.llama_cpp:
            out.append("llama.cpp at the pinned commit in the source store")
        if self.wheels:
            out.append(f"{self.wheels} Python packages for the build image")
        if self.downloads:
            out.append("pinned downloads: " + ", ".join(self.downloads))
        if self.image_files:
            out.append(f"{self.image_files} file(s) taken from source images")
        if self.packages:
            out.append(f"{len(self.packages)} distribution packages for the build image ({self.packages_for}), checked "
                       "against the distribution's signed package lists with the pinned archive keys"
                       + (" (and with gpgv)" if self.packages_gpgv else ""))
        if self.personal:
            out.append("It holds files whose licence does not allow passing them on: they stay with you (never share "
                       "the archive).")
        out += [f"Not in the preload (provide it otherwise): {item}" for item in self.not_covered]
        return out


def _check_packages(top: Path, manifest: Dict[str, Any], apt: Optional[Dict[str, Any]]) -> Tuple[List[str], str, bool]:
    """The distribution packages of a build's preload checked as a board preload is (deb_preload.verify_folder: the
    archive keys only with a pinned fingerprint, each Release signed by one, of the release and architecture, each list
    against its Release, every .deb against its list, no other file; then gpgv against the pinned keys when this
    computer has it), and every package a stage asks for in it or recorded as in its base image. ([NAME=VERSION],
    "distribution release arch", whether gpgv checked); nothing when the recipe has none."""
    folder = top / "packages"
    if not apt:
        if folder.exists():
            raise PreloadProblem("The preload holds distribution packages its recipe does not name.", "mismatch")
        return [], "", False
    deb = deb_preload()
    distribution, release = manifest.get("packages_distribution", ""), manifest.get("packages_release", "")
    arch = manifest.get("packages_arch", "")
    if arch != apt.get("arch") or (apt.get("distribution") and (distribution, release) != (apt["distribution"],
                                                                                             apt["release"])):
        raise PreloadProblem(f"The preload's distribution packages are for {distribution} {release} {arch}, not for the "
                             "build image's base.", "mismatch")
    if not folder.is_dir():
        raise PreloadProblem("The preload lacks the distribution packages its recipe names.", "mismatch")
    try:
        debs = deb.verify_folder(folder, release, arch)
        gpgv = deb.gpgv_check(folder)
    except deb.PreloadError as e:
        raise PreloadProblem(f"The preload's distribution packages were refused: {e}", "mismatch",
                             "Make the preload anew with the scripts the framework wrote.") from None
    names = {word.split("=", 1)[0]: word.split("=", 1)[1] for word in debs}
    in_base = set(str(manifest.get("packages_in_base") or "").split(","))
    for stage in apt.get("stages") or []:
        for word in stage["packages"]:
            name, _, version = word.partition("=")
            if word in in_base or (name in names and (not version or names[name] == version)):
                continue
            raise PreloadProblem(f"The preload's distribution packages lack {word}, which the Dockerfile installs.",
                                 "mismatch", "Make the preload anew with the scripts the framework wrote.")
    return sorted(debs), f"{distribution} {release} {arch}", gpgv


def _check_image_archive(file: Path, name: str) -> None:
    from orchestrator.Core.build_image import ImageTransferError, _archive
    try:
        _labels, names = _archive(file)
    except ImageTransferError as e:
        raise PreloadProblem(f"An image of the preload is not a single image archive: {e}", "mismatch") from None
    wanted = {name, f"docker.io/{name}", f"docker.io/library/{name}"}
    if not names or any(n not in wanted for n in names):
        raise PreloadProblem(f"An image of the preload would be named {', '.join(names) or 'nothing'}, not {name}; "
                             "nothing was loaded.", "mismatch", "Make the preload again with the scripts the framework wrote.")


def take(state_dir: Any, target: str, archive: Path, *, client: Any, sha256: str = "", actor: str = "cli",
         log=None) -> Taken:
    """Checks the build's preload `archive` for `target` and takes it in. Every check runs before anything is
    loaded or copied; an AI caller (actor other than cli and gui) may name only an archive of a recipe written here,
    which every caller must anyway."""
    images_reader = reader()
    say = log or (lambda _text: None)
    archive = Path(archive).expanduser()
    if not archive.is_file():
        raise PreloadProblem("The preload archive does not exist.", "invalid_value",
                             "Give the path of the archive the preload script wrote.")
    lock = _preload_lock()
    with tempfile.TemporaryDirectory(prefix="llmcf-build-preload-") as work:
        work = Path(work)
        try:
            manifest = lock.open_archive(archive, work, sha256.strip().lower(), kind="build")
        except lock.PreloadError as e:
            raise PreloadProblem(f"The preload was refused: {e}", "mismatch",
                                 "Copy the archive again, or make it anew with the scripts the framework wrote.") from None
        top = Path(manifest["folder"])
        if str(manifest.get("target", "")).casefold() != str(target).casefold():
            raise PreloadProblem(f"The preload is for the target {manifest.get('target')}, not {target}.", "mismatch",
                                 "Give the preload of this target.")
        if manifest.get("platform") != images_reader.host_platform():
            raise PreloadProblem(f"The preload is for {manifest.get('platform')}, this computer is "
                                 f"{images_reader.host_platform()}.", "mismatch",
                                 "Write the preload scripts on this computer and run them again.")
        digest = str(manifest.get("recipe_sha256") or "")
        if _recorded(state_dir).get(digest, "").casefold() != str(target).casefold():
            raise PreloadProblem("The preload was not made from a recipe the framework wrote on this computer for you; "
                                 "nothing "
                                 "was taken in.", "needs_person" if actor not in ("cli", "gui") else "mismatch",
                                 "Write the preload scripts on this computer again and run them.")
        body = (top / "sources.json").read_bytes() if (top / "sources.json").is_file() else b""
        if hashlib.sha256(body).hexdigest() != digest:
            raise PreloadProblem("The preload's recipe is not the one its manifest names.", "mismatch")
        data = json.loads(body.decode("utf-8"))
        # every image named as asked
        images = []
        listing = (top / "images.txt").read_text(encoding="utf-8").splitlines() if (top / "images.txt").is_file() \
            else []
        wanted = {images_reader.load_name(ref) for ref in data.get("images") or []}
        for line in listing:
            parts = line.split()
            if len(parts) != 4 or not re.fullmatch(r"images/\d{2}\.tar", parts[0]) or parts[1] not in wanted:
                raise PreloadProblem(f"images.txt names an image the recipe does not: {line[:120]}", "mismatch")
            _check_image_archive(top / parts[0], parts[1])
            images.append((top / parts[0], parts[1]))
        if {name for _, name in images} != wanted:
            raise PreloadProblem("The preload lacks a base image its recipe names.", "mismatch")
        for item in data.get("downloads") or []:
            path = top / "files" / item["name"]
            if not path.is_file() or path.stat().st_size != int(item["size"]) or _sha256_file(path) != item["sha256"]:
                raise PreloadProblem(f"{item['name']} in the preload is not the pinned file.", "mismatch")
        taken_files = []
        for image in data.get("source_images") or []:
            for item in image["files"]:
                path = top / IMAGE_FILES / item["sha256"]
                if not path.is_file() or _sha256_file(path) != item["sha256"]:
                    raise PreloadProblem(f"A file taken from {recipe_image_label(image).split(', which')[0]} "
                                         f"({item['path']}) is missing or not its pinned sha256.", "mismatch")
                taken_files.append(path)
        bundle = top / "llama.cpp" / "llama.cpp.bundle"
        if data.get("llama_cpp_bundle") and not bundle.is_file():
            raise PreloadProblem("The preload lacks llama.cpp, which its recipe names.", "mismatch")
        packages, packages_for, gpgv = _check_packages(top, manifest, data.get("apt"))
        # taken in
        if images and client is None:
            raise PreloadProblem("The container engine is not available, so the base images cannot be loaded.",
                                 "failed", "Start Docker or Podman and take the preload in again.")
        for path, name in images:
            say(f"Loading the base image {name}...")
            with open(path, "rb") as handle:
                client.images.load(handle)
        if data.get("llama_cpp_bundle"):
            from orchestrator.runtime.build.source import SourceError, catalog_pin, import_source
            pin = catalog_pin()
            if data["llama_cpp"]["commit"] != pin.commit:
                raise PreloadProblem("The preload's llama.cpp is another commit than this framework pins.", "mismatch")
            say("Taking llama.cpp into the source store (checked by its commit)...")
            try:
                import_source(pin, state_dir, bundle)
            except SourceError as e:
                raise PreloadProblem(f"llama.cpp of the preload was refused: {e}", "mismatch") from None
        store = target_store(state_dir, target)
        wheel_count = 0
        if data.get("pip") and (top / "wheels").is_dir():
            folder = store / f"wheels-{_key(data['pip'])[:16]}"
            folder.mkdir(parents=True, exist_ok=True)
            for path in (top / "wheels").iterdir():
                if path.is_file():
                    shutil.copyfile(path, folder / path.name)
                    wheel_count += 1
        for item in data.get("downloads") or []:
            (store / CONTEXT_FILES).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(top / "files" / item["name"], store / CONTEXT_FILES / item["name"])
        cache = image_cache(state_dir)
        for path in taken_files:
            cache.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, cache / path.name)
        if packages:
            say("Keeping the build image's distribution packages (checked)...")
            kept = store / f"{APT_STORE}{data['apt']['key']}" / str(manifest.get("archive_sha256"))[:16]
            if not (kept / "sources.txt").is_file():
                part = kept.with_name(kept.name + ".part")
                shutil.copytree(top / "packages", part, dirs_exist_ok=True, copy_function=shutil.copyfile)
                os.replace(part, kept)
            os.utime(kept)   # the newest taken in is the one a build uses
    return Taken(str(manifest.get("target")), str(manifest.get("archive_sha256")), [n for _, n in images],
                 bool(data.get("llama_cpp_bundle")), wheel_count, [d["name"] for d in data.get("downloads") or []],
                 len(taken_files), manifest.get("personal") == "yes", list(data.get("not_covered") or []),
                 packages, packages_for, gpgv)


# --- the framework's view (CLI, GUI, the build service) --------------------------------------------------------------

def _version() -> str:
    try:
        from orchestrator import __version__
        return str(__version__)
    except Exception:  # noqa: BLE001
        return "unknown"


def state_dir_of(framework: Any) -> Path:
    from orchestrator.Core.build_image import _engine
    config = getattr(framework, "config", None) or getattr(_engine(framework), "config", None)
    state = getattr(config, "user_state_dir", None)
    if state is None:
        raise PreloadProblem("The person's state folder is not known.", "failed")
    return Path(state)


def needed(framework: Any, target: str, *, gpu: bool = False):
    """The offline plan of `target` when an offline build would need a source that is not local and no build
    container image for these settings is on this computer; None otherwise (offline mode off, an image here, or every
    source local)."""
    from orchestrator.Core import build_image
    from orchestrator.Core.builder import INPUTS_LABEL
    from orchestrator.utils.locality import offline_mode
    engine = build_image._engine(framework)
    config = getattr(framework, "config", None) or getattr(engine, "config", None)
    if not offline_mode(config):
        return None
    client = build_image.docker_client(framework, engine)
    try:
        wanted = build_image.expected_labels(framework, target, gpu=gpu).get(INPUTS_LABEL)
    except Exception:  # noqa: BLE001 - the labels cannot be computed: the plan decides
        wanted = None
    if wanted and any(i.inputs == wanted for i in build_image.target_images(client, target)):
        return None
    plan = build_image.target_plan(framework, target, gpu=gpu)
    return None if plan.ready else plan


def write(framework: Any, target: str, *, gpu: bool = False, plan: Any = None) -> Written:
    """Writes the preload scripts for what an offline build of `target` lacks (the plan's missing sources)."""
    from orchestrator.Core import build_image
    engine = build_image._engine(framework)
    folder, dockerfile = build_image.target_dockerfile(engine, target, gpu)
    plan = plan if plan is not None else build_image.target_plan(framework, target, gpu=gpu)
    text = dockerfile.read_text(encoding="utf-8", errors="replace")
    name = getattr(engine.target_manager, "resolve", lambda t: t)(target) if getattr(engine, "target_manager", None) \
        else target
    data = recipe(plan, str(name or target), folder, text, gpu=gpu,
                  client=build_image.docker_client(framework, engine))
    return write_scripts(state_dir_of(framework), data, _version())


def take_in(framework: Any, target: str, archive: Path, *, actor: str, sha256: str = "", log=None) -> Taken:
    from orchestrator.Core import build_image
    engine = build_image._engine(framework)
    name = getattr(engine.target_manager, "resolve", lambda t: t)(target) if getattr(engine, "target_manager", None) \
        else target
    return take(state_dir_of(framework), str(name or target), archive,
                client=build_image.docker_client(framework, engine), sha256=sha256, actor=actor, log=log)


def _target_folder(framework: Any, target: str) -> Path:
    from orchestrator.Core import build_image
    engine = build_image._engine(framework)
    folder = engine.resolve_target_dir(target) if engine is not None and hasattr(engine, "resolve_target_dir") \
        else None
    if folder is None:
        raise PreloadProblem(f"There is no target {target}.", "invalid_value", "List the targets and pick one.")
    return Path(folder)


def image_file_in(framework: Any, target: str, archive: Path, *, log=None) -> TakenImageFile:
    """take_image_file() for an installed target (the CLI's source-image take, the window's Take from an image
    file...)."""
    return take_image_file(state_dir_of(framework), _target_folder(framework, target), archive, log=log)


def source_images_of(framework: Any, target: str) -> List[Dict[str, Any]]:
    """source_image_rows() for an installed target."""
    return source_image_rows(state_dir_of(framework), _target_folder(framework, target))


__all__ = ["PreloadProblem", "PreloadSources", "Taken", "TakenImageFile", "Written", "apt_store", "deb_preload",
           "declared_pip", "describe", "fetches", "file_repository", "image_cache", "image_file_in",
           "image_file_needed", "image_files", "missing_image_files", "needed", "pinned_downloads", "preload_sources",
           "reader", "recipe", "recipe_bytes", "recipe_image_label", "scripts_folder", "source_image_rows",
           "source_images", "source_images_of", "stage", "store_root", "take", "take_image_file", "take_in",
           "target_store", "write", "write_scripts"]
