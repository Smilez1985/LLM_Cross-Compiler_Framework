#!/usr/bin/env python3
"""
The llama.cpp pin for places that cannot load the runtime catalog.

configs/llama_runtime.yml (`llama_cpp.source`) holds the one llama.cpp pin; orchestrator.runtime.catalog validates it
for the runtime and the build recipe. The settings default `llama_cpp_version` and the image build of target images
derive their tag from the same entry, but they are imported long before the runtime package (and must not import
it), so this module reads just that entry with nothing but PyYAML. tests/test_build_recipe.py checks that every
other pin place agrees with it.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

CATALOG_PATH = Path(__file__).resolve().parents[2] / "configs" / "llama_runtime.yml"
_TAG = re.compile(r"^b\d{1,7}$", re.ASCII)
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)


@lru_cache(maxsize=4)
def _read(path: str, mtime_ns: int) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        # libyaml's safe loader (read at every start through the settings' default; the pure-Python loader takes
        # about ten times longer for this file), else the same rules in Python when PyYAML was built without libyaml
        try:
            data = yaml.load(f, Loader=yaml.CSafeLoader)
        except AttributeError:
            data = yaml.safe_load(f)
    source = data["llama_cpp"]["source"]
    tag, commit = source["tag"], source["commit"]
    if not (isinstance(tag, str) and _TAG.fullmatch(tag) and isinstance(commit, str) and _COMMIT.fullmatch(commit)):
        raise ValueError("llama_cpp.source.tag or .commit is not valid")
    return {"tag": tag, "commit": commit}


def pinned_source(path: Optional[Path] = None) -> Optional[dict]:
    """{"tag": "b11429", "commit": "<40 hex>"} of the pin, or None when the catalog is missing or broken (the
    callers then fail closed: an image build without a tag stops with an error)."""
    p = Path(path or CATALOG_PATH)
    try:
        return dict(_read(str(p), p.stat().st_mtime_ns))
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
        return None


def pinned_tag(path: Optional[Path] = None) -> str:
    """The llama.cpp tag target images clone (the pin's label, `b<build number>`); "" when the pin is unreadable."""
    source = pinned_source(path)
    return source["tag"] if source else ""
