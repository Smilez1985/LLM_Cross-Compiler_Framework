#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Identical builds (DECISIONS, eighth round, N11)

An earlier build counts as identical only when 100 % of the chosen options are the same: the model and its exact
revision (the Hugging Face commit; for a local model every weights file by name, size and modification time), the
target and the sha256 of its definition (every file of its folder), the board, the format, the quantization, the
importance matrix and the sha256 of the calibration data, the optimization level, the context length, GPU use and the
model's task. Other weights or another quantization is never identical.

The build engine records these options in every build's manifest (`build_options`, with their sha256). Before a build,
check_build_request and the GUI's builder form look for a finished build with the same options and say so; the person
(or the AI) then uses that build or builds again. Nothing is ever skipped by itself.

When the commit the build would fetch is not known yet (the default branch, Hugging Face not asked), a build with the
same options apart from that is reported as "the same settings, commit not checked", never as identical. Builds made
before the options were recorded are never reported: their manifests do not say every option.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

OPTIONS_VERSION = 1
MANIFEST_KEY = "build_options"
DIGEST_KEY = "build_options_sha256"
# Every option an identical build shares (the manifest's build_options holds exactly these plus "version").
OPTION_KEYS = ("model", "revision", "weights", "target", "target_sha256", "board", "format", "quantization",
               "imatrix", "dataset_sha256", "optimization", "max_context_length", "use_gpu", "model_task")
# Compared without letter case (a repo id, a board or a quantization type in another case is the same choice).
CASELESS = ("model", "target", "board", "format", "quantization", "optimization", "model_task")
MAX_WEIGHT_FILES = 10000


def local_weights(path: Any) -> Optional[List[Dict[str, Any]]]:
    """A local model as its weights files: name (relative to the model folder), size and modification time of every
    regular file, sorted; links are left out. None when the path is not there or holds too many files to compare."""
    path = Path(path)
    try:
        if path.is_file():
            st = path.stat()
            return [{"file": path.name, "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}]
        if not path.is_dir():
            return None
        found = []
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in files:
                item = Path(root) / name
                if item.is_symlink():
                    continue
                st = item.stat()
                found.append({"file": item.relative_to(path).as_posix(), "bytes": st.st_size,
                              "mtime_ns": st.st_mtime_ns})
                if len(found) > MAX_WEIGHT_FILES:
                    return None
        return sorted(found, key=lambda f: f["file"])
    except OSError:
        return None


def file_sha256(path: Any) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def shipped_calibration_sha256(language: str) -> Optional[str]:
    """The sha256 of the shipped calibration text as a build writes it (orchestrator writes it with the system's line
    ends before the build hashes the file)."""
    try:
        from orchestrator.utils.calibration import build_calibration_text
        text = build_calibration_text(language)
    except Exception:  # noqa: BLE001 - an unknown language: no shipped text
        return None
    return hashlib.sha256(text.replace("\n", os.linesep).encode("utf-8")).hexdigest()


def build_options(*, model: Optional[str], revision: Optional[str], weights: Optional[List[Dict[str, Any]]],
                  target: str, target_sha256: Optional[str], board: Optional[str], fmt: str,
                  quantization: Optional[str], imatrix: bool, dataset_sha256: Optional[str], optimization: str,
                  max_context_length: Optional[int], use_gpu: bool, model_task: str = "LLM") -> Dict[str, Any]:
    """The chosen options of one build, as the manifest records them. `model`: the Hugging Face repo id (None for a
    local model, which is named by its `weights` instead: never its path)."""
    return {"version": OPTIONS_VERSION, "model": model, "revision": revision, "weights": weights, "target": target,
            "target_sha256": target_sha256, "board": board, "format": fmt, "quantization": quantization,
            "imatrix": bool(imatrix), "dataset_sha256": dataset_sha256, "optimization": optimization,
            "max_context_length": max_context_length, "use_gpu": bool(use_gpu), "model_task": model_task or "LLM"}


def options_sha256(options: Dict[str, Any]) -> str:
    text = json.dumps({k: options.get(k) for k in ("version", *OPTION_KEYS)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _same(key: str, a: Any, b: Any) -> bool:
    if key in CASELESS and isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def compare(wanted: Dict[str, Any], earlier: Dict[str, Any]) -> Tuple[str, List[str]]:
    """("identical" | "commit_unchecked" | "different", the options that differ). commit_unchecked: everything is the
    same, but the commit the new build would fetch is not known (it may differ). A target whose definition could not be
    hashed is never the same."""
    if not isinstance(earlier, dict) or earlier.get("version") != OPTIONS_VERSION:
        return "different", ["version"]
    differ = [k for k in OPTION_KEYS if k != "revision" and not _same(k, wanted.get(k), earlier.get(k))]
    if not wanted.get("target_sha256") or not earlier.get("target_sha256"):
        differ.append("target_sha256")
    if wanted.get("model") is None and not wanted.get("weights"):
        differ.append("weights")  # a local model that could not be read is never the same
    if differ:
        return "different", sorted(set(differ))
    if wanted.get("model") is None:  # a local model: its weights decide, there is no commit
        return "identical", []
    if not earlier.get("revision"):
        return "different", ["revision"]
    if not wanted.get("revision"):
        return "commit_unchecked", []
    return ("identical", []) if str(wanted["revision"]).lower() == str(earlier["revision"]).lower() \
        else ("different", ["revision"])


@dataclass(frozen=True)
class EarlierBuild:
    """A finished build with the same options: its artifact id, label, manifest and whether it is identical (the
    commit is known and the same) or only the same settings with the commit not checked."""
    artifact_id: str
    label: str
    info: Dict[str, Any]
    match: str            # identical | commit_unchecked

    @property
    def created(self) -> str:
        return str(self.info.get("created") or "")

    @property
    def revision(self) -> str:
        options = self.info.get(MANIFEST_KEY) if isinstance(self.info.get(MANIFEST_KEY), dict) else {}
        return str(options.get("revision") or "")


def find_earlier(entries: Iterable[Tuple[str, str, Dict[str, Any]]], wanted: Dict[str, Any]) -> List[EarlierBuild]:
    """The finished builds among `entries` ((artifact id, label, manifest) as BuildService lists them) whose recorded
    options are the same as `wanted`, identical ones first, newest first."""
    found = []
    for aid, label, info in entries:
        if not isinstance(info, dict) or not info.get("primary"):
            continue
        match, _ = compare(wanted, info.get(MANIFEST_KEY))
        if match != "different":
            found.append(EarlierBuild(aid, label, info, match))
    found.sort(key=lambda b: b.created, reverse=True)
    found.sort(key=lambda b: b.match != "identical")  # stable: identical first, each group newest first
    return found


__all__ = ["CASELESS", "DIGEST_KEY", "EarlierBuild", "MANIFEST_KEY", "OPTION_KEYS", "OPTIONS_VERSION",
           "build_options", "compare", "file_sha256", "find_earlier", "local_weights", "options_sha256",
           "shipped_calibration_sha256"]
