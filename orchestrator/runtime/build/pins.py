#!/usr/bin/env python3
"""
Every place in the repository that names the llama.cpp version, checked against the one pin
(configs/llama_runtime.yml, llama_cpp.source).

- derived: must equal the pin. The settings default llama_cpp_version is derived in code
  (orchestrator/utils/llama_pin.py); the Dockerfile ARG and the target.yml build arguments are fallbacks for builds
  outside the framework and are compared here.
- reference: the catalogue of upstream projects (not read while the framework runs); compared as well.
- legacy: the four-module chain (targets/*/modules/source_module.sh) stays untouched (decision D20); its default is
  reported, never a failure.

tests/test_build_recipe.py fails on any derived or reference mismatch; scripts/llama_cpp_pin.py prints the table.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from orchestrator.runtime.catalog import LlamaCppSource

REPO_ROOT = Path(__file__).resolve().parents[3]

_DOCKER_ARG = re.compile(r"^\s*ARG\s+LLAMA_CPP_TAG=(\S+)\s*$", re.MULTILINE)
_LLAMA_REPO = re.compile(r"github\.com/(ggml-org|ggerganov)/llama\.cpp", re.IGNORECASE)
_LEGACY_DEFAULT =re.compile(r'LLAMA_CPP_COMMIT="\$\{LLAMA_CPP_COMMIT:-([^}"]*)\}"')


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
        for name in ("dockerfile", "Dockerfile"):
            dockerfile = target_dir / name
            if dockerfile.is_file():
                text = dockerfile.read_text(encoding="utf-8")
                found = _DOCKER_ARG.findall(text)
                if found or _LLAMA_REPO.search(text):  # the ARG, or a llama.cpp clone without it
                    places.append(PinPlace(rel(dockerfile), "ARG LLAMA_CPP_TAG", "derived",
                                           found[0] if found else None, tag))
        target_yml = target_dir / "target.yml"
        if target_yml.is_file():
            data = _yaml(target_yml) or {}
            args = ((data.get("docker") or {}).get("build_args") or {})
            if "LLAMA_CPP_TAG" in args:
                places.append(PinPlace(rel(target_yml), "docker.build_args.LLAMA_CPP_TAG", "derived",
                                       str(args["LLAMA_CPP_TAG"]), tag))
        legacy = target_dir / "modules" / "source_module.sh"
        if legacy.is_file():
            found = _LEGACY_DEFAULT.findall(legacy.read_text(encoding="utf-8"))
            if found:
                places.append(PinPlace(rel(legacy), "LLAMA_CPP_COMMIT default (legacy four-module chain, D20)",
                                       "legacy", found[0], tag))

    for name in ("project_sources.yml", "detailed_project_sources.yml"):
        path = root / "configs" / name
        if path.is_file():
            entry = ((_yaml(path) or {}).get("inference_backends") or {}).get("llama_cpp") or {}
            places.append(PinPlace(rel(path), "inference_backends.llama_cpp.commit", "reference",
                                   str(entry.get("commit")) if entry.get("commit") is not None else None, tag))
    return places


def mismatches(places: List[PinPlace]) -> List[PinPlace]:
    """Derived and reference places that disagree with the pin (legacy places are only reported)."""
    return [p for p in places if p.kind != "legacy" and not p.ok]
