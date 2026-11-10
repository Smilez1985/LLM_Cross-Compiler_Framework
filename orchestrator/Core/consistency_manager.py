#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the pre-flight check before a build (DESIGN, "Build pipeline and target modules": a
build that cannot succeed stops before it starts).

`check` compares what the hardware probes found with what the build and the target's SDK need, and returns a
`Preflight`: its findings (each one stops the build or is a warning, with what was found, what is needed and what to
do), what was compared and what could not be compared. It is pure: the build service (Core/build_service.py
`BuildService.preflight_for`) gathers the inputs and runs it at every build start, in the build plan of an AI caller's
question (Core/build_plan.py) and for `llm-cli build preflight` and the Builder's Pre-flight check button.

Two probes, each for what it can tell:
- this computer (only a GPU build): the CUDA version of the target's GPU image (`gpu_image`) against the CUDA version
  the NVIDIA driver reports (NVIDIA_DRIVER_CUDA_MAX: the driver's place in NVIDIA's compatibility matrix). A newer
  CUDA major version than the driver supports stops (the CUDA runtime refuses a driver of an older major version);
  a newer minor version of the same major is a warning (minor version compatibility); no NVIDIA GPU with a working
  driver and a target without a GPU image are warnings (the build runs on the CPU).
- the board's probe (the board the build is for): the format's `sdk:` declaration of the target's capabilities
  (schemas/targets.py SdkNeeds). The NPU driver below its minimum stops, below the recommended version it is a
  warning; the board's runtime older than the toolkit (or of another major version) is a warning.

Only what makes the build or its result fail for certain stops; a value a probe does not have is listed as not checked,
never guessed. Probe values are untrusted: only versions that parse as numbers are compared and shown.

What the build service refuses outright before this check (the NPU missing in the board's probe, RKLLM on another SoC
or on a build computer that is not x86_64, a probe of another board, a GPU build under Podman:
build_formats.format_refusal and BuildService._validated) is not repeated here.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from orchestrator.runtime.catalog import version_tuple

# The closed codes of a finding: the facts catalogue's warning preflight_error names one of them as its `check`
STOP_CODES = ("cuda_driver", "npu_driver")
WARNING_CODES = ("cuda_minor", "no_nvidia_gpu", "no_gpu_image", "npu_driver_old", "npu_runtime")
CODES = STOP_CODES + WARNING_CODES

THIS_COMPUTER = "this computer"
THE_BOARD = "the board"

_CUDA_ARG = re.compile(r"^\s*ARG\s+CUDA_VERSION\s*=\s*\"?(\d{1,2}\.\d{1,3}(?:\.\d{1,3})?)\"?\s*$", re.MULTILINE)
_CUDA_IMAGE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*\S*nvidia/cuda:(\d{1,2}\.\d{1,3}(?:\.\d{1,3})?)", re.MULTILINE |
                         re.IGNORECASE)
_VERSION = re.compile(r"^\d{1,3}(\.\d{1,5}){0,3}$")


@dataclass(frozen=True)
class Finding:
    """One result of the check: `code` (CODES), whether it stops the build, where the fact was found (this computer or
    the board), what was found and what is needed (`message`) and what to do (`next`)."""
    code: str
    stop: bool
    where: str
    message: str
    next: str

    def line(self) -> str:
        kind = "Stops the build" if self.stop else "Warning"
        return f"{kind} ({self.where}): {self.message} {self.next}".strip()

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "stop": self.stop, "where": self.where, "message": self.message, "next": self.next}


@dataclass
class Preflight:
    """The result of the pre-flight check: the findings, what was compared (`checked`) and what could not be compared,
    with how to get it (`unchecked`)."""
    findings: List[Finding] = field(default_factory=list)
    checked: List[str] = field(default_factory=list)
    unchecked: List[str] = field(default_factory=list)

    def stops(self) -> List[Finding]:
        return [f for f in self.findings if f.stop]

    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if not f.stop]

    @property
    def stopped(self) -> bool:
        return bool(self.stops())

    def stop_codes(self) -> List[str]:
        return sorted({f.code for f in self.stops()})

    def verdict(self) -> str:
        """stopped, warnings or passed."""
        return "stopped" if self.stopped else "warnings" if self.findings else "passed"

    def summary(self) -> str:
        if self.stopped:
            n = len(self.stops())
            return (f"Pre-flight check: the build cannot succeed as asked ({n} reason{'s' if n != 1 else ''}); "
                    "nothing was started.")
        if self.findings:
            return "Pre-flight check: passed with warnings."
        if self.checked:
            return "Pre-flight check: passed."
        return "Pre-flight check: nothing to compare for this build."

    def lines(self) -> List[str]:
        """The summary, every finding (stops first), what was compared and what was not."""
        out = [self.summary()]
        out += [f.line() for f in self.stops()] + [f.line() for f in self.warnings()]
        if self.checked:
            out.append("Compared: " + "; ".join(self.checked) + ".")
        out += [f"Not checked: {u}" for u in self.unchecked]
        return out

    def notes(self, overridden: bool = False) -> List[str]:
        """What a started build keeps in its warnings: every warning, and the stops the person overrode."""
        kept = [f"Pre-flight check: {f.message}" for f in self.warnings()]
        if overridden:
            kept = [f"Pre-flight check overridden by the person: {f.message}" for f in self.stops()] + kept
        return kept

    def as_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict(), "summary": self.summary(),
                "findings": [f.as_dict() for f in self.stops() + self.warnings()],
                "checked": list(self.checked), "unchecked": list(self.unchecked)}


@dataclass(frozen=True)
class GpuImage:
    """The target's GPU image: whether it has one (Dockerfile.gpu) and the CUDA version it builds with (None: no CUDA
    found in it, or not readable), with where that version came from."""
    present: bool
    cuda: Optional[str] = None
    source: str = ""


def gpu_image(target_folder: Optional[Path], build_args: Optional[Mapping[str, Any]] = None) -> GpuImage:
    """The CUDA version of a target's GPU image: a CUDA_VERSION in target.yml docker.build_args (what the build passes
    to the image build), else the Dockerfile.gpu default (ARG CUDA_VERSION=...), else the tag of an nvidia/cuda base
    image. GpuImage(False) when the target has no Dockerfile.gpu (the build then uses its CPU Dockerfile)."""
    if target_folder is None:
        return GpuImage(False)
    path = Path(target_folder) / "Dockerfile.gpu"
    if not path.is_file() or path.is_symlink():
        return GpuImage(False)
    arg = str((build_args or {}).get("CUDA_VERSION") or "").strip()
    if re.fullmatch(r"\d{1,2}\.\d{1,3}(\.\d{1,3})?", arg):
        return GpuImage(True, arg, "the target's build_args")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return GpuImage(True)
    match = _CUDA_ARG.search(text)
    if match:
        return GpuImage(True, match.group(1), "Dockerfile.gpu")
    match = _CUDA_IMAGE.search(text)
    if match:
        return GpuImage(True, match.group(1), "the nvidia/cuda base image of Dockerfile.gpu")
    return GpuImage(True)


def _version(value: Any) -> Optional[str]:
    """A probe or declared version that is plainly a number (digits and dots), else None (never shown or compared)."""
    text = str(value or "").strip().lstrip("vV")
    return text if _VERSION.fullmatch(text) else None


def _major_minor(value: str) -> Tuple[int, ...]:
    parts = version_tuple(value) or (0,)
    return tuple(parts[:2]) + (0,) * (2 - len(parts[:2]))


def _get(profile: Any, key: str) -> Optional[str]:
    get = getattr(profile, "get", None)
    return get(key) if callable(get) else None


def _needs_root(profile: Any, key: str) -> bool:
    warnings = getattr(profile, "warnings", None)
    try:
        listed = warnings() if callable(warnings) else (warnings or [])
    except Exception:  # noqa: BLE001 - a profile without warnings
        listed = []
    return f"needs_root:{key}" in list(listed or [])


def _nvidia_gpus(profile: Any) -> List[Dict[str, str]]:
    """The NVIDIA GPUs whose driver answered (seen by nvidia-smi); a GPU seen only on the PCI bus has no driver."""
    gpus = getattr(profile, "gpus", None)
    found = gpus() if callable(gpus) else []
    return [g for g in found if g.get("VENDOR") == "nvidia" and g.get("SOURCE") != "sysfs"]


# --- this computer: a GPU build -----------------------------------------------------------------------------------

def _host_findings(result: Preflight, host: Any, image: GpuImage) -> None:
    if not image.present:
        result.findings.append(Finding(
            "no_gpu_image", False, THIS_COMPUTER,
            "the target has no GPU image (Dockerfile.gpu), so a GPU build runs its CPU image on the CPU.",
            "Build without the GPU option, or add a Dockerfile.gpu to the target."))
        return
    if host is None:
        result.unchecked.append("this computer's GPUs could not be probed, so the driver was not compared with the "
                                "target's CUDA version; probe this computer again to see what the probe finds.")
        return
    nvidia = _nvidia_gpus(host)
    if not nvidia:
        result.findings.append(Finding(
            "no_nvidia_gpu", False, THIS_COMPUTER,
            "the probe finds no NVIDIA GPU with a working driver, so the build runs on the CPU (much slower).",
            "Install or load the NVIDIA driver and the NVIDIA Container Toolkit, or build without the GPU option."))
        return
    if image.cuda is None:
        result.unchecked.append("the CUDA version of the target's GPU image is not named in its Dockerfile.gpu (ARG "
                                "CUDA_VERSION or an nvidia/cuda base image), so the driver was not compared with it.")
        return
    driver_cuda = _version(_get(host, "NVIDIA_DRIVER_CUDA_MAX"))
    if driver_cuda is None:
        result.unchecked.append("the NVIDIA driver's CUDA version is not in this computer's probe, so it was not "
                                "compared with the image's CUDA "
                                f"{image.cuda}; probe this computer again with the driver loaded.")
        return
    result.checked.append(f"the NVIDIA driver of this computer (CUDA {driver_cuda}) with the image's CUDA {image.cuda}")
    need, have = _major_minor(image.cuda), _major_minor(driver_cuda)
    if need[0] > have[0]:
        result.findings.append(Finding(
            "cuda_driver", True, THIS_COMPUTER,
            f"the NVIDIA driver supports CUDA up to {driver_cuda}, and the target's GPU image uses CUDA {image.cuda} "
            f"(from {image.source}): CUDA {need[0]} refuses a driver of CUDA {have[0]}, so the build's GPU work "
            "cannot run.",
            f"Update the NVIDIA driver to one that supports CUDA {need[0]}.{need[1]} or newer, or build without the "
            "GPU option."))
    elif need > have:
        result.findings.append(Finding(
            "cuda_minor", False, THIS_COMPUTER,
            f"the NVIDIA driver supports CUDA up to {driver_cuda}, and the target's GPU image uses CUDA {image.cuda}: "
            "it runs through CUDA's minor version compatibility, but code compiled at run time can fail.",
            f"Update the NVIDIA driver to one that supports CUDA {need[0]}.{need[1]} or newer to be sure."))


# --- the board: what the format's SDK needs -------------------------------------------------------------------------

def _board_findings(result: Preflight, fmt: str, sdk: Any, board: Any, board_name: str) -> None:
    toolkit = str(getattr(sdk, "toolkit", "") or "the toolkit")
    version = _version(getattr(sdk, "version", None))
    label = fmt.upper()
    if board is None:
        what = [k for k in (getattr(sdk, "driver_key", None), getattr(sdk, "runtime_key", None)) if k]
        if what:
            result.unchecked.append(
                f"no hardware probe of {board_name} is stored, so its NPU driver and runtime were not compared with "
                f"what {label} needs; probe the board and store its probe.")
        return
    driver_key = getattr(sdk, "driver_key", None)
    if driver_key:
        driver = _version(_get(board, driver_key))
        minimum = _version(getattr(sdk, "driver_min", None))
        recommended = _version(getattr(sdk, "driver_recommended", None))
        if driver is None:
            why = ("the probe needs root for it: run the probe on the board with sudo" if _needs_root(board, driver_key)
                   else "the probe did not find it")
            result.unchecked.append(f"the NPU driver version of {board_name} ({why}), so it was not compared with "
                                    f"what {label} needs.")
        else:
            result.checked.append(f"the NPU driver {driver} of {board_name} with what {label} needs")
            have = version_tuple(driver) or (0,)
            if minimum and have < (version_tuple(minimum) or (0,)):
                result.findings.append(Finding(
                    "npu_driver", True, THE_BOARD,
                    f"the NPU driver of {board_name} is {driver}, and {label} models need at least {minimum}: the "
                    "board's runtime does not run the converted model on it (an NPU job on an unsupported driver can "
                    "hang the NPU).",
                    f"Update the board's kernel or NPU driver to {minimum} or newer (recommended "
                    f"{recommended or minimum}), probe it again, then build; or build GGUF, which runs on its CPU."))
            elif recommended and have < (version_tuple(recommended) or (0,)):
                result.findings.append(Finding(
                    "npu_driver_old", False, THE_BOARD,
                    f"the NPU driver of {board_name} is {driver}, older than the recommended {recommended}.",
                    f"Update the board's kernel or NPU driver to {recommended} when you can."))
    runtime_key = getattr(sdk, "runtime_key", None)
    if runtime_key and version:
        runtime = _version(_get(board, runtime_key))
        name = str(getattr(sdk, "runtime_name", "") or "runtime")
        if runtime is None:
            result.unchecked.append(f"the {name} version on {board_name} (not in its probe: not installed, or not "
                                    "readable), so it was not compared with "
                                    f"{toolkit} {version}; the board package or bundle brings what it needs.")
            return
        result.checked.append(f"the {name} {runtime} of {board_name} with {toolkit} {version}")
        have, need = version_tuple(runtime) or (0,), version_tuple(version) or (0,)
        if have[0] != need[0] or _major_minor(runtime) < _major_minor(version):
            result.findings.append(Finding(
                "npu_runtime", False, THE_BOARD,
                f"the {name} on {board_name} is {runtime}, and the model is converted with {toolkit} {version}: "
                f"the board's {name} must be at least {'.'.join(str(p) for p in _major_minor(version))} of the same "
                "major version to load it.",
                f"Update {name} on the board (its vendor package), or let the bundle bring its pinned runtime."))


def check(*, fmt: str, board_name: Optional[str] = None, use_gpu: bool = False, sdk: Any = None,
          board_profile: Any = None, host_profile: Any = None, image: Optional[GpuImage] = None) -> Preflight:
    """The pre-flight check of one build. `fmt`: the format; `board_name`: the board's id or label for the texts;
    `use_gpu`: a GPU build (this computer's GPU is passed into the build); `sdk`: the format's SdkNeeds (None: nothing
    of the board is compared); `board_profile`: the board's probe (a HardwareProfile, None: none stored);
    `host_profile`: this computer's probe (None: not probed); `image`: the target's GPU image (gpu_image)."""
    result = Preflight()
    fid = str(fmt or "").strip().lower()
    shown = str(board_name or "the board")
    if use_gpu:
        _host_findings(result, host_profile, image or GpuImage(False))
    if sdk is not None:
        _board_findings(result, fid, sdk, board_profile, shown)
    return result


__all__ = ["CODES", "Finding", "GpuImage", "Preflight", "STOP_CODES", "THE_BOARD", "THIS_COMPUTER",
           "WARNING_CODES", "check", "gpu_image"]
