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


# --- formats and the board's probe (DECISIONS round 23 item 15) ----------------------------------------------------
#
# A format that runs on an NPU is offered and chosen only when the board's probe shows that NPU; otherwise GGUF (the
# board's CPU). The conversion runs on the build computer: a vendor toolkit without wheels for its architecture is
# refused there with the reason. One function decides (format_refusal); probe_formats, the Builder tab and the build
# service read it. Facts, checked against the airockchip repositories on 2026-10-23 and again for this rule:
# rkllm-toolkit 1.3.1 ships rkllm_toolkit-1.3.1-cp310/cp311/cp312-linux_x86_64 wheels only (rknn-llm, release-v1.3.1,
# rkllm-toolkit/packages); its README's "Support Platform" lists the RK3588, RK3576, RK3562 and RV1126B series, not
# the RK3566 or RK3568. rknn-toolkit2 ships x86_64 and arm64 wheels (rknn-toolkit2/packages/x86_64 and /arm64; on
# PyPI, 2.3.2 has manylinux x86_64 and aarch64 wheels), so RKNN converts natively on an ARM build computer.

# Format -> the NPU vendor whose NPU runs it (NPU_VENDOR of the probe schema)
NPU_FORMATS = {"rkllm": "rockchip", "rknn": "rockchip"}
# The SoCs RKLLM-Toolkit 1.3.1 converts for
RKLLM_SOCS = ("rk3588", "rk3576", "rk3562", "rv1126b")
# Format -> (the one build-computer architecture its toolkit has wheels for, the toolkit in words)
SINGLE_ARCH_FORMATS = {"rkllm": ("x86_64", "RKLLM-Toolkit 1.3.1 ships Linux x86_64 wheels only")}
_MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}


def build_host_arch(machine: Optional[str] = None) -> str:
    """The architecture of the build computer (this one, or `machine` as platform.machine() names it): x86_64, arm64,
    or the machine's own name in lower case for another one."""
    if machine is None:
        import platform
        machine = platform.machine()
    text = str(machine or "").strip().lower()
    return _MACHINES.get(text, text)


def probe_npu(profile: Any) -> Optional[str]:
    """The vendor of the NPU the board's probe shows, or None. A Rockchip NPU counts when the probe found the SoC's NPU
    node (SUPPORTS_RKNN=ON); a Rockchip board attached over USB is no NPU of the probed machine. Another vendor's NPU
    counts with its NPU_VENDOR and NPU_DEVICE."""
    if profile is None:
        return None
    get = getattr(profile, "get", None)
    flag = getattr(profile, "flag", None)
    if callable(flag) and flag("SUPPORTS_RKNN") == "ON":
        return "rockchip"
    vendor = str(get("NPU_VENDOR") or "").lower() if callable(get) else ""
    if vendor and vendor != "rockchip" and get("NPU_DEVICE"):
        return vendor
    return None


def _soc_of(profile: Any, board: Optional[str]) -> str:
    get = getattr(profile, "get", None) if profile is not None else None
    soc = board or (get("SOC_MODEL") if callable(get) else None)
    return str(soc or "").strip().lower()


def format_refusal(fmt: Optional[str], *, profile: Any = None, board: Optional[str] = None,
                   host_arch: Optional[str] = None) -> Optional[str]:
    """Why `fmt` is not built for this board on this build computer, as one plain sentence; None when nothing stands
    against it. `profile`: the board's probe (None: not known, no NPU check); `board`: the board id of the request
    (else the probe's SoC); `host_arch`: the build computer (build_host_arch(); None: not checked). The one verdict of
    DECISIONS round 23 item 15: an NPU format only with that NPU in the probe; RKLLM only for its SoCs and only on an
    x86_64 build computer; GGUF always."""
    fid = str(fmt or "").strip().lower()
    label = fid.upper()
    vendor = NPU_FORMATS.get(fid)
    if vendor and profile is not None and probe_npu(profile) != vendor:
        return (f"The board's hardware probe shows no {vendor.title()} NPU, and {label} runs on that NPU only: build "
                "GGUF, which runs on the board's CPU.")
    if fid == "rkllm":
        soc = _soc_of(profile, board)
        if soc and soc not in RKLLM_SOCS:
            return (f"RKLLM-Toolkit 1.3.1 converts for the RK3588, RK3576, RK3562 and RV1126B only, not for the "
                    f"{soc.upper()}: an LLM runs on its CPU there, so build GGUF.")
    single = SINGLE_ARCH_FORMATS.get(fid)
    if single and host_arch and host_arch != single[0]:
        return (f"{single[1]}, and this build computer is {host_arch}: build GGUF here (it converts natively), or "
                f"build {label} on an {single[0]} computer.")
    return None


@dataclass(frozen=True)
class FormatVerdict:
    """A format of a target for one board and build computer: offered, or not with the reason (format_refusal)."""
    format: str
    label: str
    offered: bool
    reason: str = ""


def format_verdicts(caps: Any, *, profile: Any = None, board: Optional[str] = None,
                    host_arch: Optional[str] = None) -> List[FormatVerdict]:
    """Every format of offered_formats(caps) with its verdict for the board's probe and the build computer."""
    out = []
    for label, fid in offered_formats(caps):
        reason = format_refusal(fid, profile=profile, board=board, host_arch=host_arch)
        out.append(FormatVerdict(fid, label, reason is None, reason or ""))
    return out


def probe_formats(caps: Any, *, profile: Any = None, board: Optional[str] = None,
                  host_arch: Optional[str] = None) -> List[Tuple[str, str]]:
    """(label, format id) offered for the board's probe on this build computer: offered_formats(caps) without what
    format_refusal refuses, in the target's order (GGUF, the CPU format, stays, so it is chosen without an NPU)."""
    return [(v.label, v.format) for v in format_verdicts(caps, profile=profile, board=board, host_arch=host_arch)
            if v.offered]
