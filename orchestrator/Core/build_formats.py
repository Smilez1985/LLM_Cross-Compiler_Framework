"""
Model formats and optimization levels of a build, and the formats the installed targets build.

They live in this small module so the CLI can build its --format choices without importing the build engine and
the Docker SDK (lazy loading). orchestrator.Core.builder re-exports both: builder.ModelFormat is this class.

What a target offers (DECISIONS round 23, item 7): exactly the formats its target.yml capabilities declare; a target
without a (valid) capabilities block is built unchecked and offered every format but hf (unchecked_formats). The
Builder tab's format list (offered_formats) and the CLI's `build start --format` (its help text, its completion and
its refusal, format_problem) read the same lists; nothing here imports the schema before a list is asked for.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple


class ModelFormat(Enum):
    HUGGINGFACE = "hf"
    GGUF = "gguf"
    RKLLM = "rkllm"          # Rockchip NPU LLM (RK3588/RK3576)
    ONNX = "onnx"
    TENSORFLOW_LITE = "tflite"
    PYTORCH_MOBILE = "pytorch_mobile"
    # Hardware-Specific Formats
    RKNN = "rknn"           # Rockchip NPU
    TENSORRT = "tensorrt"   # NVIDIA GPU
    OPENVINO = "openvino"   # Intel NPU/CPU
    COREML = "coreml"       # Apple Silicon
    NCNN = "ncnn"           # Mobile High-Performance
    MNN = "mnn"             # Alibaba Mobile


class OptimizationLevel(Enum):
    FAST = "fast"
    BALANCED = "balanced"
    SIZE = "size"
    SPEED = "speed"
    AGGRESSIVE = "aggressive"


# Formats with a working pipeline and their labels: what a target without capabilities is offered first (the Builder
# tab's BUILD_FORMATS, gui/builder_tab.py re-exports them).
PIPELINE_FORMATS: Tuple[Tuple[str, str], ...] = (
    ("GGUF (llama.cpp, CPU/GPU)", "gguf"),
    ("RKLLM (Rockchip NPU, LLM)", "rkllm"),
    ("RKNN (Rockchip NPU, ONNX models)", "rknn"),
)


def unchecked_formats() -> List[Tuple[str, str]]:
    """(label, format id) a target without (valid) capabilities is offered: every ModelFormat but hf, the three with
    a working pipeline first. Nothing is checked for it; its own scripts decide (the build service builds it
    unchecked, for the person only)."""
    known = {value for _, value in PIPELINE_FORMATS}
    return list(PIPELINE_FORMATS) + [(f.value.upper(), f.value) for f in ModelFormat
                                     if f is not ModelFormat.HUGGINGFACE and f.value not in known]


def offered_formats(caps: Any) -> List[Tuple[str, str]]:
    """(label, format id) a target offers: exactly the formats its capabilities declare (their labels), or for a
    target without (valid) capabilities unchecked_formats(). The Builder tab's format list and the CLI's --format."""
    if caps is None:
        return unchecked_formats()
    return [(getattr(cap, "label", None) or fid, fid) for fid, cap in caps.formats.items()]


@dataclass(frozen=True)
class TargetFormats:
    """What one installed target builds: its id, the format ids it offers (offered_formats) and whether its
    capabilities declare them (False: no or no valid capabilities block, every format offered, nothing checked)."""
    target: str
    formats: Tuple[str, ...]
    declared: bool

    def builds(self, fmt: Optional[str]) -> bool:
        wanted = str(fmt or "").strip().casefold()
        return bool(wanted) and any(f.casefold() == wanted for f in self.formats)

    def text(self) -> str:
        """"Rockchip: gguf, rkllm, rknn", or for an unchecked target that it takes any format unchecked."""
        if not self.declared:
            return f"{self.target}: any format (it declares no capabilities, nothing is checked)"
        return f"{self.target}: {', '.join(self.formats)}"


def _formats(target: str, caps: Any) -> TargetFormats:
    return TargetFormats(str(target), tuple(fid for _, fid in offered_formats(caps)), caps is not None)


def formats_of(target_manager: Any) -> List[TargetFormats]:
    """What each target of a loaded TargetManager builds, in its order (its validated capabilities)."""
    found = []
    for meta in target_manager.list_targets() if target_manager is not None else []:
        tid = meta.get("id") if isinstance(meta, dict) else None
        if tid:
            found.append(_formats(tid, target_manager.get_capabilities(tid)))
    return found


def formats_in(targets_dir: Path) -> List[TargetFormats]:
    """What each target in `targets_dir` builds, read from the target.yml files without booting the framework (the
    CLI's help text and completion); a capabilities block that fails the schema counts as none, as on the build
    path."""
    from orchestrator.Core.target_manager import read_target_folders
    from orchestrator.schemas.targets import TargetCapabilities
    found = []
    for tid, (_folder, data) in read_target_folders(Path(targets_dir)).items():
        caps = None
        if isinstance(data.get("capabilities"), dict):
            try:
                caps = TargetCapabilities.model_validate(data["capabilities"])
            except ValueError:   # pydantic's ValidationError is a ValueError: invalid counts as none
                caps = None
        found.append(_formats(tid, caps))
    return found


def formats_text(found: Iterable[TargetFormats]) -> str:
    """What each installed target builds, in one line: "Rockchip: gguf, rkllm, rknn; MyJetson: any format (...)"."""
    return "; ".join(t.text() for t in found)


def offered_values(found: Iterable[TargetFormats]) -> List[str]:
    """Every format id some installed target offers, each once, in the targets' order (the CLI's completion)."""
    values: List[str] = []
    for entry in found:
        for fid in entry.formats:
            if fid not in values:
                values.append(fid)
    return values


def format_problem(fmt: Optional[str], found: Iterable[TargetFormats], target: str) -> Optional[str]:
    """Why `build start --format fmt` for the installed target `target` is refused, in plain words naming what each
    installed target builds; None when the target offers that format (a target without capabilities offers every
    format but hf) or when `target` is none of `found` (the target check says so)."""
    found = list(found)
    entry = next((t for t in found if t.target == target), None)
    if entry is None or entry.builds(fmt):
        return None
    shown = str(fmt or "").strip() or "(none)"
    listing = formats_text(found)
    others = [t.target for t in found if t.declared and t.builds(fmt)]
    if not entry.declared and not others:   # no target takes it (hf, or no format at all)
        return f"{shown} is no format a target builds. What each installed target builds: {listing}."
    if others:
        why = f"; {', '.join(others)} {'builds' if len(others) == 1 else 'build'} it"
    elif not any(not t.declared and t.builds(fmt) for t in found):
        why = "; no installed target builds it"
    else:
        why = ""
    return f"The target {target} does not build the format {shown}{why}. What each installed target builds: {listing}."
