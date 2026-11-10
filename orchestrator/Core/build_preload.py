#!/usr/bin/env python3
"""
The build's preload (docs/DESIGN.md, Installer, platforms and dependencies): an offline build whose build container
image is not on this computer and whose sources are not all local (Core/build_image.offline_plan) writes a preload
script for exactly those sources, the person runs it on a computer with a network, and the archive it writes is taken
in here as local sources under the one locality rule. The format is the installer's preload (scripts/preload_lock.py:
one llmcf-preload/ folder, manifest.txt, SHA256SUMS), of kind build.

- recipe(): what the preload fetches, from the plan's missing sources: base images, the pinned llama.cpp commit, the
  build image's Python packages (target.yml build_preload), pinned downloads of the Dockerfile, the files taken from
  source images (target.yml template_variables.source_images); and what it cannot carry (not_covered).
- write(): preload.sh and preload.ps1 for that recipe into the person's folder, the recipe's sha256 recorded.
- take(): checks an archive (the installer's checks, then kind, target, platform, a recipe written here, the image
  names, the llama.cpp commit and every pinned sha256) and takes it in: images loaded into the container engine,
  llama.cpp into the source store, wheels, downloads and taken files into the person's state folder.
- the build engine asks preload_sources() for the plan and stage() for the build context; image_files() takes the
  files of source images (online through the registry, offline from the person's cache).

Taking from source images reads the image without running it (orchestrator/utils/image_files.py); a file whose
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

from orchestrator.utils.next_steps import WithSteps

STORE = "build-preload"
SCRIPTS = "llmcf-preload-scripts"
RECIPES = "recipes.txt"
IMAGE_FILES = "image-files"
CONTEXT_WHEELS = "wheels"          # sources/wheels in the build context (PIP_FIND_LINKS)
CONTEXT_FILES = "files"            # sources/files/NAME: pinned downloads the Dockerfile takes from the context
CONTEXT_IMAGE_FILES = "image-files"  # sources/image-files/DEST: copied into the image root by the Dockerfile
IMAGE_SOURCES = "/tmp/llmcf-sources"   # where the target Dockerfiles COPY sources/
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


def _preload_lock():
    """scripts/preload_lock.py of this installation (the installer's preload; standard library only)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "preload_lock.py"
    spec = importlib.util.spec_from_file_location("llmcf_preload_lock", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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
    current pip declaration (or None), the pinned downloads present (url -> file) and the source images whose files
    are all in the cache."""
    wheels: Optional[Path] = None
    downloads: Dict[str, Path] = field(default_factory=dict)
    images_missing: List[str] = field(default_factory=list)


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
    found.images_missing = [image.image for image in missing_image_files(state_dir, source_images(target_dir))]
    return found


def stage(context: Path, plan: Any) -> None:
    """Puts what the plan takes from the build's preload into the build context's sources/ (wheels/ and files/)."""
    sources = Path(context) / "sources"
    for kind, path in getattr(plan, "preload_stage", None) or []:
        if kind == CONTEXT_WHEELS:
            shutil.copytree(path, sources / CONTEXT_WHEELS, dirs_exist_ok=True, copy_function=shutil.copyfile)
        else:
            (sources / CONTEXT_FILES).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, sources / CONTEXT_FILES / Path(path).name)


# --- the files of source images --------------------------------------------------------------------------------------

def image_files(state_dir: Any, target_dir: Path, context: Path, *, online: bool, log=None) -> List[str]:
    """Takes the files the target names from its source images into the build context (sources/image-files/DEST),
    from the person's cache, fetching what is missing through the registry when `online` (the image read, never run;
    the rest discarded). Returns the images whose files are missing offline (the plan names them)."""
    from orchestrator.utils import image_files as reader
    images = source_images(target_dir)
    if not images:
        return []
    cache = image_cache(state_dir)
    missing = []
    for image in images:
        absent = {f.path: f.sha256 for f in image.files if not (cache / f.sha256).is_file()}
        if absent and not online:
            missing.append(image.image)
            continue
        if absent:
            if log:
                log(f"Taking {len(absent)} file(s) from {image.image} (its layers read, nothing run; the rest of the "
                    "image is discarded)")
            try:
                reader.take_files(image.image, absent, cache, image.platform or "")
            except reader.ImageFilesError as e:
                raise PreloadProblem(f"The files of the source image could not be taken: {e}", "failed",
                                     "Check the image's digest and the files' paths and sha256 in the target's "
                                     "template variables.") from None
        for item in image.files:
            dest = Path(context) / "sources" / CONTEXT_IMAGE_FILES / item.dest.lstrip("/")
            reader.copy_file(cache / item.sha256, dest)
            os.chmod(dest, 0o755 if item.executable else 0o644)  # nosec B103 - a program of the image, no secret
    return missing


# --- the recipe ------------------------------------------------------------------------------------------------------

def _ref_of(source: Any) -> str:
    return str(getattr(source, "ref", "") or "")


def recipe(plan: Any, target: str, target_dir: Path, dockerfile_text: str, *, gpu: bool = False,
           platform: str = "") -> Dict[str, Any]:
    """The build's preload recipe (sources.json) for the plan's missing sources."""
    from orchestrator.utils import image_files as reader
    from orchestrator.utils.locality import url_host
    missing = [s for s in plan.sources if not s.local]
    kinds = {s.kind for s in missing}
    out: Dict[str, Any] = {"format": "1", "target": target, "platform": platform or reader.host_platform(),
                           "images": [], "llama_cpp": None, "llama_cpp_bundle": False, "pip": None, "downloads": [],
                           "source_images": [], "not_covered": []}
    for source in missing:
        if source.kind != "base_image":
            continue
        ref = _ref_of(source)
        try:
            reader.load_name(ref)
            out["images"].append(ref)
        except reader.ImageFilesError as e:
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
                                      "pip index in your own network (llm-cli build image sources --pip-index)")
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
        elif source.kind in ("apt", "registry_mirror", "download"):
            out["not_covered"].append(f"{source.label}: {source.how}")
    for source in missing:
        if source.kind == "image_files":
            image = next((i for i in source_images(target_dir) if i.image == _ref_of(source)), None)
            if image is not None:
                out["source_images"].append({
                    "image": image.image, "platform": image.platform or out["platform"],
                    "files": [{"path": f.path, "sha256": f.sha256, "redistributable": f.redistributable}
                              for f in image.files]})
    return out


def recipe_bytes(data: Dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8")


def fetches(data: Dict[str, Any]) -> bool:
    return bool(data["images"] or data["llama_cpp_bundle"] or data["pip"] or data["downloads"]
                or data["source_images"])


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
        parts.append("files taken from " + ", ".join(i["image"].split("@")[0] for i in data["source_images"]))
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


def steps(written: Written) -> List[str]:
    """The person's steps after the scripts were written (the CLI and a run without a terminal print them)."""
    return [f"On a computer with a network (Python 3 with pip; git for llama.cpp), run {written.scripts[0].name} "
            f"(Linux, macOS) or {written.scripts[1].name} (Windows) from {written.folder}; they need nothing else of "
            "llmcf.",
            "Carry the archive it writes (llmcf-preload-build-...tar.gz) to this computer.",
            f"Give its path when the build asks, or: llm-cli build start ... --preload FILE, or llm-cli build preload "
            f"take FILE --target {written.folder.name[len('build-'):]}."]


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
        if self.personal:
            out.append("It holds files whose licence does not allow passing them on: they stay with you (never share "
                       "the archive).")
        out += [f"Not in the preload (provide it otherwise): {item}" for item in self.not_covered]
        return out


def _check_image_archive(file: Path, name: str) -> None:
    from orchestrator.Core.build_image import ImageTransferError, _archive
    try:
        _labels, names = _archive(file)
    except ImageTransferError as e:
        raise PreloadProblem(f"An image of the preload is not a single image archive: {e}", "mismatch") from None
    wanted = {name, f"docker.io/{name}", f"docker.io/library/{name}"}
    if not names or any(n not in wanted for n in names):
        raise PreloadProblem(f"An image of the preload would be named {', '.join(names) or 'nothing'}, not {name}; "
                             "nothing was loaded.", "mismatch", "Make the preload again with the scripts llmcf wrote.")


def take(state_dir: Any, target: str, archive: Path, *, client: Any, sha256: str = "", actor: str = "cli",
         log=None) -> Taken:
    """Checks the build's preload `archive` for `target` and takes it in. Every check runs before anything is
    loaded or copied; an AI caller (actor other than cli and gui) may name only an archive of a recipe written here,
    which every caller must anyway."""
    from orchestrator.utils import image_files as reader
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
                                 "Copy the archive again, or make it anew with the scripts llmcf wrote.") from None
        top = Path(manifest["folder"])
        if str(manifest.get("target", "")).casefold() != str(target).casefold():
            raise PreloadProblem(f"The preload is for the target {manifest.get('target')}, not {target}.", "mismatch",
                                 "Give the preload of this target.")
        if manifest.get("platform") != reader.host_platform():
            raise PreloadProblem(f"The preload is for {manifest.get('platform')}, this computer is "
                                 f"{reader.host_platform()}.", "mismatch",
                                 "Write the preload scripts on this computer and run them again.")
        digest = str(manifest.get("recipe_sha256") or "")
        if _recorded(state_dir).get(digest, "").casefold() != str(target).casefold():
            raise PreloadProblem("The preload was not made from a recipe llmcf wrote on this computer for you; nothing "
                                 "was taken in.", "needs_person" if actor not in ("cli", "gui") else "mismatch",
                                 "Run the preload scripts llmcf wrote here (llm-cli build preload write TARGET).")
        body = (top / "sources.json").read_bytes() if (top / "sources.json").is_file() else b""
        if hashlib.sha256(body).hexdigest() != digest:
            raise PreloadProblem("The preload's recipe is not the one its manifest names.", "mismatch")
        data = json.loads(body.decode("utf-8"))
        # every image named as asked
        images = []
        listing = (top / "images.txt").read_text(encoding="utf-8").splitlines() if (top / "images.txt").is_file() \
            else []
        wanted = {reader.load_name(ref) for ref in data.get("images") or []}
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
                    raise PreloadProblem(f"A file taken from {image['image']} ({item['path']}) is missing or not its "
                                         "pinned sha256.", "mismatch")
                taken_files.append(path)
        bundle = top / "llama.cpp" / "llama.cpp.bundle"
        if data.get("llama_cpp_bundle") and not bundle.is_file():
            raise PreloadProblem("The preload lacks llama.cpp, which its recipe names.", "mismatch")
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
                raise PreloadProblem("The preload's llama.cpp is another commit than this llmcf pins.", "mismatch")
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
    return Taken(str(manifest.get("target")), str(manifest.get("archive_sha256")), [n for _, n in images],
                 bool(data.get("llama_cpp_bundle")), wheel_count, [d["name"] for d in data.get("downloads") or []],
                 len(taken_files), manifest.get("personal") == "yes", list(data.get("not_covered") or []))


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
    data = recipe(plan, str(name or target), folder, text, gpu=gpu)
    return write_scripts(state_dir_of(framework), data, _version())


def take_in(framework: Any, target: str, archive: Path, *, actor: str, sha256: str = "", log=None) -> Taken:
    from orchestrator.Core import build_image
    engine = build_image._engine(framework)
    name = getattr(engine.target_manager, "resolve", lambda t: t)(target) if getattr(engine, "target_manager", None) \
        else target
    return take(state_dir_of(framework), str(name or target), archive,
                client=build_image.docker_client(framework, engine), sha256=sha256, actor=actor, log=log)


__all__ = ["PreloadProblem", "PreloadSources", "Taken", "Written", "declared_pip", "describe", "fetches",
           "image_cache", "image_files", "missing_image_files", "needed", "pinned_downloads", "preload_sources",
           "recipe", "recipe_bytes", "scripts_folder", "source_images", "stage", "steps", "store_root", "take",
           "take_in", "target_store", "write", "write_scripts"]
