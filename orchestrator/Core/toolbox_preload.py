#!/usr/bin/env python3
"""
The runtime layer's tools from the build's preload (docs/DESIGN.md, Build pipeline and target modules): a computer that
never had a network makes the runtime layer's static busybox and setpriv the way "build" (Core/runtime_layer.py) from
sources the build's preload brought, made on the computer with the network by downloads alone.

- section(): the recipe's section `toolbox` when this computer has no toolbox and no checked sources for one: the
  builder's base image by its pinned digest (runtime_layer.BUILDER_BASE, carried under a local name, BASE_NAME), the
  three source tarballs of runtime_layer.BUILD_SOURCES and the builder's distribution packages read from its
  Dockerfile (build_preload._apt_section). scripts/preload_lock.py pack_build fetches them into llmcf-preload/toolbox/.
- check(): what an archive brought, against the pins of THIS installation (never the archive's own words): the base
  image by scripts/image_files.check_pinned, the tarballs by their sha256, the packages as a target's are checked
  (signed lists, pinned archive keys), and of the release the checked base image is. keep() keeps it in
  build-preload/runtime-toolbox/<arch>/ of the person's state folder with a note of the pins (kept() reads it back
  only while the pins are the same).
- context() and ready_base(): the offline build's builder context (each tarball checked again, the packages as the
  signed file repository) and the base image in the container engine (checked again, loaded, its layers compared).

Qt-free; no text here names a host path (they may reach an AI).
"""

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.Core import runtime_layer

STORE = "runtime-toolbox"          # build-preload/runtime-toolbox/<arch>/
FOLDER = "toolbox"                 # llmcf-preload/toolbox/ in the archive
BASE = "base"                      # the base image: image_files.PINNED_DOCUMENT, PLATFORM_MANIFEST, SAVED_IMAGE
SOURCES = "sources"                # the pinned source tarballs
PACKAGES = "packages"              # the builder's distribution packages (deb_preload.FOLDER)
KEPT = "kept.json"
MANIFEST_PREFIX = "toolbox_"       # the archive's manifest lines of the builder's packages: toolbox_packages_*
# The builder's base image under a local name (a loaded image cannot carry a reference pinned by digest); the name
# carries the pinned digest, so a base of another pin is never taken for it
BASE_NAME = "llm-framework/runtime-toolbox-base:" + runtime_layer.BUILDER_BASE.rsplit(":", 1)[-1][:16]
LABEL = "the runtime layer's tools"


def _reader():
    from orchestrator.Core.build_preload import reader
    return reader()


def _problem(text: str, code: str = "mismatch", next: str = ""):
    from orchestrator.Core.build_preload import PreloadProblem
    return PreloadProblem(text, code, next or "Make the preload anew with the scripts the framework wrote.")


def sources() -> List[Dict[str, str]]:
    """The pinned source tarballs of the way "build" (runtime_layer.BUILD_SOURCES), by file name."""
    return sorted(({"file": p["file"], "url": p["url"], "sha256": p["sha256"]}
                   for p in runtime_layer.BUILD_SOURCES.values()), key=lambda p: p["file"])


def builder_text() -> str:
    return runtime_layer.BUILDER_DOCKERFILE.read_text(encoding="utf-8")


def pins(platform: str = "") -> Dict[str, Any]:
    """What a kept preload of the tools was checked for: the base, its local name, the tarballs and the builder's
    package need (its key: the Dockerfile's stages and packages for this architecture)."""
    from orchestrator.Core.image_packages import dpkg_arch, need, need_key
    arch = dpkg_arch(platform or _reader().host_platform())
    found, _problems = need(builder_text(), arch) if arch else ([], [])
    return {"base": runtime_layer.BUILDER_BASE, "base_name": BASE_NAME, "sources": sources(),
            "packages": need_key(found, arch) if found else ""}


def pins_key(platform: str = "") -> str:
    return hashlib.sha256(json.dumps(pins(platform), sort_keys=True).encode("utf-8")).hexdigest()


def store(state_dir: Any, arch: Optional[str] = None) -> Path:
    from orchestrator.Core.build_preload import store_root
    return store_root(state_dir) / STORE / (arch or runtime_layer.machine())


def kept(state_dir: Any) -> Optional[Path]:
    """The kept sources of the tools for this computer while they were checked for this installation's pins (None
    otherwise: none taken in, or a framework update changed a pin)."""
    if state_dir is None:
        return None
    folder = store(state_dir)
    try:
        note = json.loads((folder / KEPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or note.get("pins") != pins_key():
        return None
    files = [folder / BASE / "image.tar", *(folder / SOURCES / p["file"] for p in sources())]
    return folder if all(path.is_file() for path in files) else None


def missing(cache_dir: Any, state_dir: Any) -> bool:
    """Whether this computer has neither the runtime layer's tools nor checked sources for them."""
    if cache_dir is None:
        return False
    if runtime_layer.read_toolbox(runtime_layer.toolbox_dir(Path(cache_dir))) is not None:
        return False
    return kept(state_dir) is None


def missing_for(framework: Any) -> bool:
    """missing() for the framework's build engine and the person's state folder (False when it cannot be told)."""
    try:
        from orchestrator.Core.build_image import _engine
        engine = _engine(framework)
        config = getattr(framework, "config", None) or getattr(engine, "config", None)
        return missing(getattr(engine, "cache_dir", None), getattr(config, "user_state_dir", None))
    except Exception:  # noqa: BLE001 - no engine: the build's own check says it
        return False


# --- the recipe -------------------------------------------------------------------------------------------------------

def section(platform: str, client: Any = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """(the recipe's section `toolbox`, "") or (None, why a preload cannot carry the tools' sources)."""
    from orchestrator.Core.build_preload import _apt_section
    apt, why = _apt_section(builder_text(), platform, client, [runtime_layer.BUILDER_BASE])
    if apt is None:
        return None, f"{LABEL}: {why}"
    return {"base": runtime_layer.BUILDER_BASE, "base_name": BASE_NAME, "sources": sources(), "apt": apt}, ""


def describe(data: Dict[str, Any]) -> str:
    words = sorted({p for s in (data.get("apt") or {}).get("stages") or [] for p in s["packages"]})
    packages = (f" and the builder's distribution packages {', '.join(words)} with their dependencies" if words
                else "")
    return (f"{LABEL}' sources (the builder's base image {data['base'].split('@')[0]} by its pinned digest, "
            f"{', '.join(p['file'] for p in data['sources'])}{packages}), from which this computer builds busybox "
            "and setpriv without a network")


# --- taking in ----------------------------------------------------------------------------------------------------

@dataclass
class Checked:
    """What check() found: the builder's packages (NAME=VERSION), for which release, whether gpgv checked them too."""
    packages: List[str] = field(default_factory=list)
    packages_for: str = ""
    gpgv: bool = False


def check(top: Path, manifest: Dict[str, Any], data: Optional[Dict[str, Any]]) -> Optional[Checked]:
    """Checks llmcf-preload/toolbox/ of an unpacked archive (`top`) against this installation's pins. None when the
    recipe has no section `toolbox` (and the archive must then hold no such folder)."""
    folder = Path(top) / FOLDER
    if not data:
        if folder.exists():
            raise _problem(f"The preload holds {LABEL}' sources, which its recipe does not name.")
        return None
    if not folder.is_dir():
        raise _problem(f"The preload lacks {LABEL}' sources, which its recipe names.")
    if data.get("base") != runtime_layer.BUILDER_BASE or data.get("base_name") != BASE_NAME \
            or data.get("sources") != sources():
        raise _problem(f"The preload's recipe names other sources of {LABEL} than this installation pins.",
                       next="Write the preload scripts on this computer again and run them.")
    images = _reader()
    try:
        images.check_pinned(folder / BASE, runtime_layer.BUILDER_BASE, BASE_NAME, images.host_platform())
        read = images.read_saved(folder / BASE / images.SAVED_IMAGE, ["usr/lib/os-release", "etc/os-release"])
    except images.ImageFilesError as e:
        raise _problem(f"The builder's base image in the preload was refused: {e}.") from None
    for pin in sources():
        path = folder / SOURCES / pin["file"]
        if not path.is_file() or _sha256(path) != pin["sha256"]:
            raise _problem(f"{pin['file']} in the preload is missing or not the pinned file.")
    extra = [p.name for p in (folder / SOURCES).iterdir() if p.name not in {s["file"] for s in sources()}]
    extra += [p.name for p in (folder / BASE).iterdir()
              if p.name not in (images.PINNED_DOCUMENT, images.PLATFORM_MANIFEST, images.SAVED_IMAGE)]
    if extra or sorted(p.name for p in folder.iterdir()) != sorted([BASE, SOURCES] + ([PACKAGES] if data.get("apt")
                                                                                     else [])):
        raise _problem(f"The preload's folder of {LABEL} holds files its recipe does not name.")
    apt = data.get("apt")
    if apt:
        from orchestrator.Core.build_preload import _check_packages, deb_preload
        facts = deb_preload().parse_os_release((read["usr/lib/os-release"] or read["etc/os-release"]
                                                or b"").decode("utf-8", "replace"))
        # the packages are checked for the release the checked base image is, never the one the archive names
        apt = dict(apt, distribution=facts.get("ID", ""), release=facts.get("VERSION_CODENAME", ""))
        lines = {k[len(MANIFEST_PREFIX):]: v for k, v in manifest.items() if str(k).startswith(MANIFEST_PREFIX)}
        packages, packages_for, gpgv = _check_packages(folder, lines, apt)
        return Checked(packages, packages_for, gpgv)
    return Checked()


def keep(state_dir: Any, top: Path, archive_sha256: str, checked: Checked) -> Path:
    """Keeps the checked llmcf-preload/toolbox/ of `top` for this computer (a copy first, then put in place) with the
    note of the pins it was checked for."""
    from orchestrator.utils.atomic import remove_tree
    dest = store(state_dir)
    part = dest.with_name(dest.name + ".part")
    remove_tree(part, label="the partial copy of the runtime layer's tools' sources")
    shutil.copytree(Path(top) / FOLDER, part, copy_function=shutil.copyfile)
    note = {"pins": pins_key(), "archive_sha256": archive_sha256, "packages_for": checked.packages_for,
            "packages": len(checked.packages), "taken": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (part / KEPT).write_bytes((json.dumps(note, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    remove_tree(dest, label="the runtime layer's tools' sources taken in before")
    os.replace(part, dest)
    return dest


# --- the offline build ----------------------------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    from orchestrator.utils.helpers import sha256_file
    return sha256_file(Path(path))


def context(work: Path, folder: Path) -> Path:
    """The builder's build context from the kept sources `folder`: its Dockerfile, each tarball checked against its
    pin again, the packages as the file repository signed by the pinned archive keys (sources/apt)."""
    from orchestrator.Core.build_preload import file_repository
    out = Path(work) / "context"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(runtime_layer.BUILDER_DOCKERFILE, out / "Dockerfile")
    for pin in sources():
        path = Path(folder) / SOURCES / pin["file"]
        if not path.is_file() or _sha256(path) != pin["sha256"]:
            raise ValueError(f"the kept {pin['file']} is not the pinned file; take the build's preload in again")
        shutil.copyfile(path, out / pin["file"])
    apt = out / "sources" / "apt"
    if (Path(folder) / PACKAGES).is_dir():
        file_repository(Path(folder) / PACKAGES, apt)
    else:
        apt.mkdir(parents=True, exist_ok=True)
    return out


def _layers(client: Any) -> Optional[List[str]]:
    try:
        image = client.images.get(BASE_NAME)
    except Exception:  # noqa: BLE001 - not here, or no answer
        return None
    return list(((getattr(image, "attrs", None) or {}).get("RootFS") or {}).get("Layers") or [])


def ready_base(client: Any, folder: Path, log: Callable[[str], None] = lambda _t: None) -> str:
    """The builder's base image in the container engine under BASE_NAME, from the kept sources `folder`: the kept
    archive checked against the pin again, loaded unless an image of that name with exactly its layers is there, and
    taken only when its layers are the checked config's. Returns the name the build passes as BUILDER_BASE."""
    images = _reader()
    try:
        facts = images.check_pinned(Path(folder) / BASE, runtime_layer.BUILDER_BASE, BASE_NAME, images.host_platform())
    except images.ImageFilesError as e:
        raise ValueError(f"the kept base image of the builder was refused ({e}); take the build's preload in "
                         "again") from None
    if _layers(client) != facts["diff_ids"]:
        log("Runtime layer: loading the builder's base image from the build's preload (checked against its pin) ...")
        with open(Path(folder) / BASE / images.SAVED_IMAGE, "rb") as handle:
            client.images.load(handle)
        if _layers(client) != facts["diff_ids"]:
            raise ValueError("the loaded base image of the builder does not have the layers its pinned config names")
    return BASE_NAME


def prepare(work: Path, folder: Path, client: Any, log: Callable[[str], None] = lambda _t: None) -> Tuple[Path, str]:
    """The offline builder's context and base image from the kept sources `folder` (runtime_layer.engine_builder's
    `prepare`): context() and ready_base()."""
    out = context(work, folder)
    return out, ready_base(client, folder, log)


__all__ = ["BASE_NAME", "Checked", "check", "context", "describe", "keep", "kept", "missing", "missing_for", "pins",
           "pins_key", "prepare", "ready_base", "section", "sources", "store"]
