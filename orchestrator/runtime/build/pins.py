#!/usr/bin/env python3
"""
Every place in the repository that names the llama.cpp version, checked against the one pin
(configs/llama_runtime.yml, llama_cpp.source).

- derived: must equal the pin. The settings default llama_cpp_version is derived in code
  (orchestrator/utils/llama_pin.py); the Dockerfile ARG and the target.yml build arguments are fallbacks for builds
  outside the framework and are compared here.
- reference: the catalogue of upstream projects (not read while the framework runs); compared as well.
- legacy: a target whose modules/source_module.sh still has the LLAMA_CPP_COMMIT default of version 1.x (an older
  community target; decision D20): reported, never a failure. In a build the shipped stage modules use the pin of the
  image (DESIGN U9); their LLAMA_CPP_PIN_TAG and LLAMA_CPP_PIN_COMMIT, the default of
  `source_module.sh --clone-llama-cpp`, are derived places.

The GPU template (targets/_template/Dockerfile.gpu) follows the newest versions of the same catalogue that fit, so a
newer Ubuntu base or CUDA toolkit there is a mismatch until the template is moved on (derived places, gpu_template_places):
- both FROM lines: the newest pinned Ubuntu base of build_bases (opt_in included: only what pins a base names it);
- ARG CUDA_VERSION, CUDA_REDIST_MANIFEST_SHA256, CUDA_COMPONENTS: the newest toolkit of cuda.toolkits for linux-x86_64
  whose host_compilers.gcc takes the major version of that base's gcc (the toolkit nvcc accepts on the base);
- ARG CUDA_REDIST_BASE_URL: cuda.redist_base_url;
- the commented example of the prebuilt opt-in: the newest image of cuda.prebuilt_images.

tests/test_build_recipe.py fails on any derived or reference mismatch; scripts/llama_cpp_pin.py prints the table.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from orchestrator.runtime.catalog import BuildBase, CudaToolkit, LlamaCppSource, RuntimeCatalog, load_catalog, version_tuple

REPO_ROOT = Path(__file__).resolve().parents[3]

_DOCKER_ARG = re.compile(r"^\s*ARG\s+LLAMA_CPP_TAG=(\S+)\s*$", re.MULTILINE)
_DOCKER_COMMIT = re.compile(r"^\s*ARG\s+LLAMA_CPP_COMMIT=(\S+)\s*$", re.MULTILINE)
_LLAMA_REPO = re.compile(r"github\.com/(ggml-org|ggerganov)/llama\.cpp", re.IGNORECASE)
_LEGACY_DEFAULT =re.compile(r'LLAMA_CPP_COMMIT="\$\{LLAMA_CPP_COMMIT:-([^}"]*)\}"')
# The default of source_module.sh --clone-llama-cpp in the shipped stage modules (the pin, written out)
_SOURCE_PIN_TAG = re.compile(r'^LLAMA_CPP_PIN_TAG="([^"]*)"$', re.MULTILINE)
_SOURCE_PIN_COMMIT = re.compile(r'^LLAMA_CPP_PIN_COMMIT="([^"]*)"$', re.MULTILINE)
# The GPU template's places that follow the catalogue
GPU_TEMPLATE = "targets/_template/Dockerfile.gpu"
_STAGE_FROM = r"^FROM\s+(\S+)\s+AS\s+{stage}\s*$"
_GPU_ARG = r"^ARG\s+{name}=(.*?)\s*$"
_OPT_IN_EXAMPLE = re.compile(r"^#\s+FROM\s+(nvidia/cuda:\S+)\s+AS\s+builder\s*$", re.MULTILINE)
_UBUNTU_BASE = re.compile(r"^ubuntu-(\d{2}\.\d{2})$")
NO_FITTING = "none fits"   # what a place expects when the catalogue has nothing that fits


@dataclass(frozen=True)
class PinPlace:
    path: str          # relative to the repository root
    what: str
    kind: str          # derived | reference | legacy
    found: Optional[str]
    expected: str

    @property
    def ok(self) -> bool:
        return self.found == self.expected


def _yaml(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pin_places(source: LlamaCppSource, root: Path = REPO_ROOT) -> List[PinPlace]:
    """The pin places found under `root`, each with what it says and what the pin expects."""
    places: List[PinPlace] = []
    tag = source.tag

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    catalog = _yaml(root / "configs" / "llama_runtime.yml")["llama_cpp"]
    places.append(PinPlace("configs/llama_runtime.yml", "llama_cpp.build (label of the prebuilt archives)",
                           "derived", catalog.get("build"), tag))
    base_url = str(catalog.get("base_url", ""))
    m = re.search(r"/releases/download/([^/]+)/$", base_url)
    places.append(PinPlace("configs/llama_runtime.yml", "llama_cpp.base_url (release of the prebuilt archives)",
                           "derived", m.group(1) if m else base_url, tag))

    for target_dir in sorted(p for p in (root / "targets").iterdir() if p.is_dir()):
        # dockerfile, Dockerfile and variants such as Dockerfile.gpu (listed by their real names, so a
        # case-insensitive file system does not report one file twice)
        dockerfiles = sorted(p for p in target_dir.iterdir() if p.is_file() and (
            p.name.lower() == "dockerfile" or p.name.lower().startswith("dockerfile.")))
        for dockerfile in dockerfiles:
            text = dockerfile.read_text(encoding="utf-8")
            found = _DOCKER_ARG.findall(text)
            commit = _DOCKER_COMMIT.findall(text)
            if found or commit or _LLAMA_REPO.search(text):  # the ARGs, or a llama.cpp clone without them
                places.append(PinPlace(rel(dockerfile), "ARG LLAMA_CPP_TAG", "derived",
                                       found[0] if found else None, tag))
                # The image fetches the commit and checks it; a Dockerfile without the ARG builds a moving tag.
                places.append(PinPlace(rel(dockerfile), "ARG LLAMA_CPP_COMMIT", "derived",
                                       commit[0] if commit else None, source.commit))
        target_yml = target_dir / "target.yml"
        if target_yml.is_file():
            data = _yaml(target_yml) or {}
            args = ((data.get("docker") or {}).get("build_args") or {})
            if "LLAMA_CPP_TAG" in args:
                places.append(PinPlace(rel(target_yml), "docker.build_args.LLAMA_CPP_TAG", "derived",
                                       str(args["LLAMA_CPP_TAG"]), tag))
            if "LLAMA_CPP_COMMIT" in args:
                places.append(PinPlace(rel(target_yml), "docker.build_args.LLAMA_CPP_COMMIT", "derived",
                                       str(args["LLAMA_CPP_COMMIT"]), source.commit))
        legacy = target_dir / "modules" / "source_module.sh"
        if legacy.is_file():
            text = legacy.read_text(encoding="utf-8")
            found = _LEGACY_DEFAULT.findall(text)
            if found:
                places.append(PinPlace(rel(legacy), "LLAMA_CPP_COMMIT default (source_module.sh of version 1.x, D20)",
                                       "legacy", found[0], tag))
            pin_tag, pin_commit = _SOURCE_PIN_TAG.findall(text), _SOURCE_PIN_COMMIT.findall(text)
            if pin_tag or pin_commit:  # the default of --clone-llama-cpp: both or neither
                places.append(PinPlace(rel(legacy), "LLAMA_CPP_PIN_TAG (default of source_module.sh --clone-llama-cpp)",
                                       "derived", pin_tag[0] if pin_tag else None, tag))
                places.append(PinPlace(rel(legacy),
                                       "LLAMA_CPP_PIN_COMMIT (default of source_module.sh --clone-llama-cpp)",
                                       "derived", pin_commit[0] if pin_commit else None, source.commit))

    for name in ("project_sources.yml", "detailed_project_sources.yml"):
        path = root / "configs" / name
        if path.is_file():
            entry = ((_yaml(path) or {}).get("inference_backends") or {}).get("llama_cpp") or {}
            places.append(PinPlace(rel(path), "inference_backends.llama_cpp.commit", "reference",
                                   str(entry.get("commit")) if entry.get("commit") is not None else None, tag))
    places.extend(gpu_template_places(load_catalog(root / "configs" / "llama_runtime.yml"), root))
    return places


def newest_ubuntu_base(catalog: RuntimeCatalog) -> Optional[BuildBase]:
    """The newest Ubuntu base of the catalogue's build_bases (id ubuntu-YY.MM), None when it pins none."""
    bases = [b for b in catalog.build_bases if _UBUNTU_BASE.fullmatch(b.id)]
    return max(bases, key=lambda b: version_tuple(_UBUNTU_BASE.fullmatch(b.id).group(1)), default=None)


def newest_fitting_toolkit(catalog: RuntimeCatalog, base: Optional[BuildBase]) -> Optional[CudaToolkit]:
    """The newest CUDA toolkit of the catalogue that builds on `base` for x86-64: redist archives for linux-x86_64,
    and the major version of the base's gcc within the toolkit's host_compilers.gcc (nvcc checks the major version)."""
    if base is None or catalog.cuda is None:
        return None
    gcc = version_tuple(base.gcc)[:1]
    fitting = [t for t in catalog.cuda.toolkits
               if "linux-x86_64" in t.platforms and t.host_compilers.gcc is not None
               and version_tuple(t.host_compilers.gcc[0])[:1] <= gcc <= version_tuple(t.host_compilers.gcc[1])[:1]]
    return max(fitting, key=lambda t: version_tuple(t.version), default=None)


def gpu_template_places(catalog: RuntimeCatalog, root: Path = REPO_ROOT) -> List[PinPlace]:
    """The GPU template's places, each against the newest value of the catalogue that fits ([] without the template)."""
    path = root / GPU_TEMPLATE
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")

    def one(pattern: str) -> Optional[str]:
        found = re.findall(pattern, text, re.MULTILINE)
        return found[0].strip('"') if len(found) == 1 else None

    base = newest_ubuntu_base(catalog)
    toolkit = newest_fitting_toolkit(catalog, base)
    image = base.image if base else NO_FITTING
    cuda = catalog.cuda
    newest_image = max(cuda.prebuilt_images, key=lambda i: version_tuple(i.cuda), default=None) if cuda else None
    example = _OPT_IN_EXAMPLE.findall(text)
    return [
        PinPlace(GPU_TEMPLATE, "FROM ... AS builder (the newest Ubuntu base of build_bases)", "derived",
                 one(_STAGE_FROM.format(stage="builder")), image),
        PinPlace(GPU_TEMPLATE, "FROM ... AS runtime (the newest Ubuntu base of build_bases)", "derived",
                 one(_STAGE_FROM.format(stage="runtime")), image),
        PinPlace(GPU_TEMPLATE, "ARG CUDA_VERSION (the newest toolkit whose host compilers take the base's gcc)",
                 "derived", one(_GPU_ARG.format(name="CUDA_VERSION")), toolkit.version if toolkit else NO_FITTING),
        PinPlace(GPU_TEMPLATE, "ARG CUDA_REDIST_MANIFEST_SHA256 (that toolkit's manifest)", "derived",
                 one(_GPU_ARG.format(name="CUDA_REDIST_MANIFEST_SHA256")),
                 toolkit.manifest.sha256 if toolkit else NO_FITTING),
        PinPlace(GPU_TEMPLATE, "ARG CUDA_COMPONENTS (that toolkit's components)", "derived",
                 one(_GPU_ARG.format(name="CUDA_COMPONENTS")), " ".join(toolkit.components) if toolkit else NO_FITTING),
        PinPlace(GPU_TEMPLATE, "ARG CUDA_REDIST_BASE_URL (cuda.redist_base_url)", "derived",
                 one(_GPU_ARG.format(name="CUDA_REDIST_BASE_URL")), cuda.redist_base_url if cuda else NO_FITTING),
        PinPlace(GPU_TEMPLATE, "the example of the prebuilt opt-in (the newest image of cuda.prebuilt_images)",
                 "derived", example[0] if len(example) == 1 else None,
                 newest_image.image if newest_image else NO_FITTING),
    ]


def mismatches(places: List[PinPlace]) -> List[PinPlace]:
    """Derived and reference places that disagree with the pin (legacy places are only reported)."""
    return [p for p in places if p.kind != "legacy" and not p.ok]
