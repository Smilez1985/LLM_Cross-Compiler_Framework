#!/usr/bin/env python3
"""
Hardware detection for the local runtime: which Ditto model tier fits this machine, how much context fits next to
the model, and which official prebuilt llama.cpp archive it could run (select_backend, only for the explicit opt-in
`ditto runtime install --prebuilt`, decision D1; the self-built server's backend comes from the build recipe,
orchestrator/runtime/build/recipe.py choose_backend, which also follows the C library). Every decision carries a
human-readable reason.

detect_hardware() runs the hardware probe in-process (orchestrator/runtime/hardware_probe.py, a byte-identical copy of
scripts/hardware_probe.py, the file that probes boards) for its memory and GPU parts, checks the result with the
probe file parser (orchestrator/runtime/profile.py) and builds HardwareInfo from that profile. Free RAM and free GPU
memory are volatile: they are read again at every call and never taken from a stored profile.
hardware_from_profile() builds HardwareInfo from any profile, for example a stored one or a device's.

The probe's GPU sources (each optional; a missing tool contributes nothing): nvidia-smi for NVIDIA GPUs (driver,
compute capability, VRAM), vulkaninfo for any Vulkan device, amdgpu's sysfs and the KFD topology on Linux (VRAM, gfx
target), WMI and the display-class registry on Windows (adapter names, dedicated VRAM). Its parsers are wrapped below
into GpuInfo for existing callers.
"""

import importlib
import logging
import os
import platform
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel

from orchestrator.runtime.catalog import (GPU_BACKENDS, ModelEntry, RuntimeAsset, RuntimeCatalog, ServerDefaults,
                                          version_tuple)
from orchestrator.runtime.profile import HardwareProfile, ProfileError, load_schema, parse_profile

GIB = 1024 ** 3
MIB = 1024 ** 2
SYSTEM_RESERVE = int(1.5 * GIB)  # RAM left to the OS, the GUI and the knowledge base when a model is chosen
# Less RAM than this left next to a board's model and the system reserve: its server gets a quantized KV cache unless
# the person chose otherwise (eighth round N7).
KV_LITTLE_RAM = 2 * GIB
# RAM left to the operating system and the framework next to the model, its KV cache and the prompt cache when the
# context is computed (compute_context, local_budget): the one OS margin. A dedicated GPU keeps half of it free of its
# own memory (driver and compute buffers); a partly offloaded model needs both.
OS_MARGIN = GIB
# The memory of a KV-cache type against f16, from ggml's block sizes (ggml-common.h): q8_0 stores 32 values in 34 bytes
# (1.0625 bytes each instead of 2), q4_0 in 18 bytes (0.5625 bytes each).
KV_TYPE_FACTORS = {"f16": 1.0, "q8_0": 1.0625 / 2, "q4_0": 0.5625 / 2}
# The package copy of scripts/hardware_probe.py (byte-identical, tests/test_hardware_probe.py compares them): a wheel
# install has no scripts/ folder.
PROBE_PATH = Path(__file__).resolve().with_name("hardware_probe.py")

# (command, timeout) -> (returncode, stdout, stderr); returncode -1 means "not found / not runnable".
Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]
LibraryFinder = Callable[[str], Optional[str]]

_GPU_VENDORS = ("nvidia", "amd", "intel", "apple")
logger = logging.getLogger(__name__)
_probe_module = None
_probe_lock = threading.Lock()


class GpuInfo(BaseModel):
    vendor: Literal["nvidia", "amd", "intel", "apple", "other"]
    name: str
    discrete: Optional[bool] = None
    unified: bool = False                # shares the system RAM (Apple Silicon, integrated AMD/Intel GPUs)
    vram_mib: Optional[int] = None
    vram_free_mib: Optional[int] = None
    driver_version: Optional[str] = None
    compute_cap: Optional[float] = None
    gfx_target: Optional[str] = None     # AMD, e.g. gfx1103 (Radeon 780M, Ryzen Z1 Extreme)
    source: str = ""

    def label(self) -> str:
        if self.unified:
            return f"{self.name} (shares the RAM)"
        vram = f", {self.vram_mib / 1024:.1f} GiB" if self.vram_mib else ""
        return f"{self.name}{vram}"


class HardwareInfo(BaseModel):
    os: str
    arch: str
    ram_total: int
    ram_available: int
    gpus: List[GpuInfo] = []
    vulkan: bool = False                 # a Vulkan loader or Vulkan device was seen
    hip_runtime: Optional[bool] = None   # Linux: libamdhip64 found (ROCm builds need it); None elsewhere
    libc: Optional[str] = None           # glibc, musl, ... (LIBC of the probe); prebuilt Linux archives need glibc
    libc_version: Optional[str] = None

    def summary(self) -> str:
        gpus = "; ".join(g.label() for g in self.gpus) or "no GPU found"
        return f"{self.os}/{self.arch}, {self.ram_total / GIB:.1f} GiB RAM, {gpus}"


class BackendChoice(BaseModel):
    asset: Optional[RuntimeAsset] = None
    backend: Optional[str] = None
    gpu: Optional[GpuInfo] = None
    reason: str


def _default_runner(cmd: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    """Runs a probe command; a missing tool or a hang counts as "not available" (utils/helpers.execute_command)."""
    from orchestrator.utils.helpers import execute_command
    return execute_command(cmd, timeout=timeout)


def normalize_platform(system: Optional[str] = None, machine: Optional[str] = None) -> Tuple[str, str]:
    system = (system or platform.system()).lower()
    os_name = {"darwin": "macos"}.get(system, system)
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "arm64e": "arm64"}.get(machine, machine)
    return os_name, arch


# --- the probe ---------------------------------------------------------------------------------------------

def load_probe():
    """The hardware probe as a module: orchestrator.runtime.hardware_probe, the package copy of the standalone
    scripts/hardware_probe.py that is sent to devices. RuntimeError when it is missing from this installation."""
    global _probe_module
    with _probe_lock:
        if _probe_module is None:
            try:
                _probe_module = importlib.import_module("orchestrator.runtime.hardware_probe")
            except ImportError as e:
                raise RuntimeError(f"The hardware probe {PROBE_PATH} is missing from this installation.") from e
        return _probe_module


def _gpu_info(gpu: Dict[str, str]) -> GpuInfo:
    """A probe or profile GPU (GPU<n>_ suffixes -> string values) as GpuInfo."""
    def number(key: str) -> Optional[int]:
        value = gpu.get(key)
        return int(value) if value is not None and value.isdigit() else None

    vendor = gpu.get("VENDOR", "other")
    vram = number("VRAM_MB")
    cap = gpu.get("COMPUTE_CAP")
    return GpuInfo(vendor=vendor if vendor in _GPU_VENDORS else "other",
                   name=gpu.get("NAME") or gpu.get("PCI_ID") or f"{vendor} GPU",
                   discrete={"ON": True, "OFF": False}.get(gpu.get("DISCRETE", "UNKNOWN")),
                   unified=gpu.get("UNIFIED") == "ON", vram_mib=vram or None, vram_free_mib=number("VRAM_FREE_MB"),
                   driver_version=gpu.get("DRIVER_VERSION"), compute_cap=float(cap) if cap else None,
                   gfx_target=gpu.get("GFX_TARGET"), source=gpu.get("SOURCE", ""))


def parse_nvidia_smi(output: str) -> List[GpuInfo]:
    """Lines of `--query-gpu=driver_version,compute_cap,memory.total,memory.free,name --format=csv,noheader,nounits`
    (the probe's longer query with the PCI fields is read as well)."""
    return [_gpu_info(g) for g in load_probe().parse_nvidia_csv(output)]


def probe_nvidia(run: Runner, os_name: str = "linux") -> List[GpuInfo]:
    gpus, _exe = load_probe().probe_nvidia(run, os_name)
    return [_gpu_info(g) for g in gpus]


def parse_vulkaninfo_summary(output: str) -> List[GpuInfo]:
    """The "Devices:" section of `vulkaninfo --summary` (GPU0: ... key = value lines)."""
    return [_gpu_info(g) for g in load_probe().parse_vulkaninfo_summary(output)]


def probe_vulkaninfo(run: Runner) -> Tuple[bool, List[GpuInfo]]:
    ran, gpus = load_probe().probe_vulkaninfo(run)
    return ran, [_gpu_info(g) for g in gpus]


def guess_discrete(vendor: str, name: str) -> Optional[bool]:
    """From a Windows adapter name: dedicated Radeon RX/PRO and Arc A/B cards; APU graphics are integrated."""
    return load_probe().guess_discrete(vendor, name)


def probe_windows_adapters(run: Runner) -> List[GpuInfo]:
    """Display adapters. Every current AMD/Intel/NVIDIA driver on Windows ships the Vulkan runtime."""
    return [_gpu_info(g) for g in load_probe().probe_windows_adapters(run)]


def probe_linux_sysfs(root: Path) -> List[GpuInfo]:
    """AMD GPUs from /sys/class/drm: dedicated VRAM, APU detection and the gfx target."""
    return [_gpu_info(g) for g in load_probe().probe_linux_sysfs(str(root)) if g["VENDOR"] == "amd"]


def hardware_from_profile(profile: HardwareProfile, memory: Optional[Tuple[int, int]] = None) -> HardwareInfo:
    """HardwareInfo from a hardware profile. `memory` (total, available bytes) overrides the profile's RAM; a profile
    without RAM_AVAILABLE_MB (a stored one, a device's) counts all of its RAM as available."""
    if memory is not None:
        ram_total, ram_available = memory
    else:
        ram_total = (profile.int_value("RAM_TOTAL_MB") or 0) * MIB
        free = profile.int_value("RAM_AVAILABLE_MB")
        ram_available = free * MIB if free is not None else ram_total
    gpus = []
    for gpu in profile.gpus():
        if gpu.get("VENDOR") == "nvidia" and gpu.get("SOURCE") == "sysfs":
            continue  # seen only on the PCI bus: without a working NVIDIA driver no GPU build runs on it
        info = _gpu_info(gpu)
        if info.vendor == "apple" and info.unified and info.vram_mib is None:
            # Unified memory: Metal may use roughly two thirds of the RAM for buffers.
            info.vram_mib = int(ram_total * 2 / 3) // MIB
        gpus.append(info)
    os_name = profile.os or "unknown"
    return HardwareInfo(os=os_name, arch=profile.arch or "other", ram_total=ram_total, ram_available=ram_available,
                        gpus=gpus, vulkan=profile.is_on("VULKAN"),
                        hip_runtime=profile.is_on("HIP_RUNTIME") if os_name == "linux" else None,
                        libc=profile.libc, libc_version=profile.libc_version)


def detect_hardware(run: Optional[Runner] = None, system: Optional[str] = None, machine: Optional[str] = None,
                    memory: Optional[Tuple[int, int]] = None, sysfs_root: Path = Path("/sys"),
                    find_library: Optional[LibraryFinder] = None,
                    rocm_globs: Sequence[str] = ("/opt/rocm*/lib*/libamdhip64.so*",)) -> HardwareInfo:
    """Probes RAM and GPUs now (free RAM and VRAM are always current). Probe tools that are missing or fail simply
    contribute nothing."""
    probe = load_probe()
    given_sys = Path(sysfs_root) != Path("/sys")
    values = probe.collect(root=os.sep, sys_dir=str(sysfs_root) if given_sys else None, run=run or _default_runner,
                           find_library=find_library, system=system, machine=machine, memory=memory,
                           rocm_globs=tuple(rocm_globs), sections=("memory", "gpu"))
    try:
        profile = parse_profile(probe.render_text(values))
    except ProfileError as e:
        # A required key the probe could not read on this system (an unusual kernel string, no memory size): every
        # value it did write passed the same validation, so the runtime still decides on them.
        logger.warning(f"Hardware probe result incomplete ({e}); deciding on the values it found")
        profile = HardwareProfile(values, schema=load_schema())
    return hardware_from_profile(profile, memory=memory)


def local_probe_values(**options) -> Dict[str, str]:
    """A full probe of this machine (every section); the options are those of hardware_probe.collect()."""
    options.setdefault("root", os.sep)
    return load_probe().collect(**options)


def local_profile(**options) -> HardwareProfile:
    """A full probe of this machine as a validated HardwareProfile."""
    return parse_profile(load_probe().render_text(local_probe_values(**options)))



# --- decisions ---------------------------------------------------------------------------------------------

def _best(gpus: List[GpuInfo]) -> Optional[GpuInfo]:
    return max(gpus, key=lambda g: (g.discrete is True, g.vram_mib or 0), default=None)


def _named(gpu: GpuInfo) -> str:
    """The GPU's label, prefixed with the vendor unless the name already starts with it."""
    label = gpu.label()
    return label if label.lower().startswith(gpu.vendor) else f"{gpu.vendor.upper()} {label}"


def vulkan_worthy(gpu: GpuInfo) -> bool:
    """GPUs on which llama.cpp's Vulkan build beats the CPU: every dedicated GPU, AMD Radeon APUs (Vega, RDNA,
    e.g. Ryzen Z1 Extreme) and Intel Arc. Older Intel UHD/Iris graphics are usually slower than the CPU."""
    if gpu.discrete or gpu.vendor == "amd":
        return True
    return gpu.vendor == "intel" and "arc" in gpu.name.lower()


def cuda_problem(asset: RuntimeAsset, gpu: GpuInfo) -> Optional[str]:
    """Why this CUDA build would not run on the GPU with its driver, or None if it does."""
    cap = gpu.compute_cap
    if asset.min_compute_cap and cap is not None and cap < asset.min_compute_cap - 1e-6:
        return f"CUDA {asset.cuda} needs compute capability >= {asset.min_compute_cap:g} ({gpu.name}: {cap:g})"
    native = asset.has_native_code(cap)
    need = asset.min_driver if native else (asset.jit_min_driver or asset.min_driver)
    have = version_tuple(gpu.driver_version)
    if need and (have is None or have < version_tuple(need)):
        jit = ""
        if not native and asset.jit_min_driver:
            what = f"compute {cap:g}" if cap is not None else "this GPU"
            jit = f" for {what}, whose code is JIT-compiled from PTX"
        return f"CUDA {asset.cuda} needs driver >= {need}{jit}"
    return None


def prebuilt_libc_problem(asset: RuntimeAsset, hw: HardwareInfo) -> Optional[str]:
    """Why an official prebuilt Linux archive cannot start with this machine's C library, or None. The archives are
    built against the glibc of an Ubuntu release (RuntimeAsset.glibc_min): musl (Alpine), an unknown C library or an
    older glibc cannot run them."""
    if asset.os != "linux":
        return None
    if hw.libc != "glibc":
        what = f"musl ({hw.libc})" if hw.libc == "musl" else (hw.libc or "a C library the probe could not read")
        return (f"the official Linux archives are built against glibc, and this machine has {what}: only the "
                "self-built server runs here")
    have = version_tuple(hw.libc_version)
    if asset.glibc_min and (have is None or have < version_tuple(asset.glibc_min)):
        return (f"the {asset.id} archive needs glibc {asset.glibc_min} or newer, this machine has "
                f"{hw.libc_version or 'an unknown version'}")
    return None


def select_backend(hw: HardwareInfo, catalog: RuntimeCatalog, prefer: Optional[str] = None) -> BackendChoice:
    """Picks the official prebuilt llama.cpp archive for this machine: only for the explicit opt-in of
    `ditto runtime install --prebuilt` (decision D1); the self-built server follows recipe.choose_backend. Archives
    whose C library does not fit the machine are never offered (prebuilt_libc_problem), and archives marked opt_in
    (ROCm) only when their backend is asked for (prefer="rocm")."""
    assets = catalog.assets_for(hw.os, hw.arch)
    if not assets:
        return BackendChoice(reason=f"No llama.cpp build for {hw.os}/{hw.arch} in the catalog.")
    refused: Dict[str, str] = {}
    by_backend: Dict[str, List[RuntimeAsset]] = {}
    for a in assets:
        problem = prebuilt_libc_problem(a, hw)
        if problem:
            refused.setdefault(a.backend, problem)
            continue
        if a.opt_in and a.backend != prefer:
            continue
        by_backend.setdefault(a.backend, []).append(a)
    if not by_backend:
        why = refused.get(prefer or "") or next(iter(refused.values()), "no archive fits this machine")
        return BackendChoice(reason=f"No official prebuilt llama.cpp archive starts on this machine: {why}. Build it "
                                    "instead: llm-cli ditto runtime install")
    for group in by_backend.values():
        group.sort(key=lambda a: version_tuple(a.cuda) or (0,), reverse=True)

    nvidias = [g for g in hw.gpus if g.vendor == "nvidia"]
    nvidia = _best(nvidias)

    def cuda_asset() -> Tuple[Optional[RuntimeAsset], str]:
        if not nvidia:
            return None, "no NVIDIA GPU found (nvidia-smi)"
        cap = f", compute {nvidia.compute_cap:g}" if nvidia.compute_cap is not None else ""
        notes = []
        for asset in by_backend.get("cuda", []):
            # llama.cpp uses every visible NVIDIA GPU, so the build has to suit all of them.
            problems = [p for p in (cuda_problem(asset, g) for g in nvidias) if p]
            if problems:
                notes.append(problems[0])
                continue
            skipped = f" ({'; '.join(notes)})" if notes else ""
            return asset, (f"{_named(nvidia)} (driver {nvidia.driver_version}{cap}) -> CUDA {asset.cuda} "
                           f"build{skipped}")
        why = "; ".join(notes) or refused.get("cuda") or f"no CUDA build for {hw.os}/{hw.arch}"
        hint = "; a newer NVIDIA driver enables the CUDA build" if any("driver" in n for n in notes) else ""
        return None, f"{_named(nvidia)} (driver {nvidia.driver_version}{cap}): {why}{hint}"

    def rocm_asset() -> Tuple[Optional[RuntimeAsset], Optional[GpuInfo], str]:
        if "rocm" not in by_backend:
            return None, None, refused.get("rocm") or f"no ROCm build for {hw.os}/{hw.arch}"
        amd = _best([g for g in hw.gpus if g.vendor == "amd"])
        if amd is None:
            return None, None, "no AMD GPU found"
        asset = by_backend["rocm"][0]
        if amd.gfx_target and asset.gpu_targets and amd.gfx_target not in asset.gpu_targets:
            return None, amd, (f"the ROCm build has no code for {amd.name} ({amd.gfx_target}); "
                               "the Vulkan build supports it")
        if hw.os == "linux" and not hw.hip_runtime:
            return None, amd, "no HIP runtime (libamdhip64) found; install ROCm or use the Vulkan build"
        unknown = "" if amd.gfx_target else " (GPU target unknown, the build may not support it)"
        return asset, amd, f"{_named(amd)} -> ROCm build{unknown}"

    def pick(backend: str, gpu: Optional[GpuInfo], reason: str) -> BackendChoice:
        return BackendChoice(asset=by_backend[backend][0], backend=backend, gpu=gpu, reason=reason)

    if prefer and prefer != "auto":
        if prefer == "cuda":
            asset, reason = cuda_asset()
            if asset:
                return BackendChoice(asset=asset, backend="cuda", gpu=nvidia, reason=reason)
            return BackendChoice(reason=f"CUDA requested, but {reason}.")
        if prefer == "rocm":
            asset, amd, reason = rocm_asset()
            if asset:
                return BackendChoice(asset=asset, backend="rocm", gpu=amd, reason=reason)
            return BackendChoice(reason=f"ROCm requested, but {reason}.")
        if prefer not in by_backend:
            if prefer in refused:
                return BackendChoice(reason=f"No '{prefer}' build: {refused[prefer]}.")
            have = ", ".join(sorted(by_backend))
            return BackendChoice(reason=f"No '{prefer}' build for {hw.os}/{hw.arch} (available: {have}).")
        gpu = None
        if prefer == "vulkan":
            gpu = _best([g for g in hw.gpus if g.vendor != "apple"])
        elif prefer == "metal":
            gpu = _best([g for g in hw.gpus if g.vendor == "apple"])
        return pick(prefer, gpu, f"{prefer} build requested")

    if "metal" in by_backend:
        apple = _best([g for g in hw.gpus if g.vendor == "apple"])
        return pick("metal", apple, "Apple Silicon -> Metal build (GPU with unified memory)")

    note = ""
    if nvidia:
        asset, reason = cuda_asset()
        if asset:
            return BackendChoice(asset=asset, backend="cuda", gpu=nvidia, reason=reason)
        note = reason + "; "

    if "vulkan" in by_backend and hw.vulkan:
        best = _best([g for g in hw.gpus if vulkan_worthy(g)])
        if best:
            shared = " (integrated GPU; model and context live in the shared RAM)" if best.unified else ""
            return pick("vulkan", best, f"{note}{_named(best)} -> Vulkan build{shared}")
        weak = _best(hw.gpus)
        if weak:
            note += (f"only an integrated GPU ({weak.name}), usually slower than the CPU; "
                     "'--backend vulkan' uses it anyway; ")
    elif "vulkan" in by_backend and [g for g in hw.gpus if g.vendor != "nvidia"]:
        note += "no Vulkan driver found for the GPU; "

    if "cpu" in by_backend:
        return pick("cpu", None, f"{note}CPU build (no usable GPU backend)")
    return BackendChoice(reason=f"{note}No CPU build for {hw.os}/{hw.arch} in the catalog.")


def backend_choice(hw: HardwareInfo, backend: Optional[str], reason: str = "") -> BackendChoice:
    """The GPU a llama.cpp build of `backend` uses on this machine, for a build that is no catalog archive (a self-built
    or imported one): CUDA the best NVIDIA GPU, ROCm the best AMD GPU, Metal Apple's GPU, Vulkan the best GPU on which
    Vulkan beats the CPU (else the best non-Apple GPU); the CPU build none. Used for the model tier and the context."""
    gpu: Optional[GpuInfo] = None
    if backend == "cuda":
        gpu = _best([g for g in hw.gpus if g.vendor == "nvidia"])
    elif backend == "rocm":
        gpu = _best([g for g in hw.gpus if g.vendor == "amd"])
    elif backend == "metal":
        gpu = _best([g for g in hw.gpus if g.vendor == "apple"])
    elif backend == "vulkan":
        gpu = _best([g for g in hw.gpus if vulkan_worthy(g)]) or _best([g for g in hw.gpus if g.vendor != "apple"])
    return BackendChoice(backend=backend, gpu=gpu, reason=reason or f"{backend or 'no'} build")


def usable_vram_gib(choice: Optional[BackendChoice]) -> Optional[float]:
    """GPU memory that counts for the model choice: a dedicated GPU used by the build, or Apple's Metal share.
    Integrated AMD/Intel GPUs share the RAM, so the RAM rules apply to them."""
    if not choice or choice.backend not in GPU_BACKENDS or not choice.gpu or not choice.gpu.vram_mib:
        return None
    if choice.gpu.unified and choice.gpu.vendor != "apple":
        return None
    return choice.gpu.vram_mib / 1024


def model_fits(model: ModelEntry, hw: HardwareInfo, choice: Optional[BackendChoice],
               defaults: ServerDefaults) -> Tuple[bool, int, int]:
    """(fits, bytes needed, bytes usable): weights plus the minimum context, in RAM plus dedicated VRAM
    (layers that do not fit on the GPU stay in RAM)."""
    need = int((model.size or 0) * 1.05) + defaults.ctx_min * model.kv_bytes_per_token
    usable = max(0, hw.ram_total - SYSTEM_RESERVE)
    vram = usable_vram_gib(choice)
    if vram is not None and choice is not None and choice.gpu is not None and not choice.gpu.unified:
        usable += int(vram * GIB * 0.95)
    return need <= usable, need, usable


def choose_model_tier(hw: HardwareInfo, catalog: RuntimeCatalog,
                      choice: Optional[BackendChoice] = None) -> Tuple[ModelEntry, str]:
    """First tier rule whose conditions hold and whose model fits, with the reason. The last rule (validated to
    have no conditions) is the answer of last resort."""
    ram = hw.ram_total / GIB
    vram = usable_vram_gib(choice)
    skipped: List[str] = []
    last = len(catalog.tier_rules) - 1
    for index, rule in enumerate(catalog.tier_rules):
        why = []
        if rule.min_vram_gib is not None:
            if vram is None or vram < rule.min_vram_gib:
                continue
            why.append(f"{vram:.1f} GiB GPU memory (>= {rule.min_vram_gib:g})")
        if rule.min_ram_gib is not None:
            if ram < rule.min_ram_gib:
                continue
            why.append(f"{ram:.1f} GiB RAM (>= {rule.min_ram_gib:g})")
        if rule.max_ram_gib is not None:
            if ram > rule.max_ram_gib:
                continue
            why.append(f"{ram:.1f} GiB RAM (<= {rule.max_ram_gib:g})")
        model = catalog.model(rule.tier)
        fits, need, usable = model_fits(model, hw, choice, catalog.server)
        if not fits and index < last:
            note = f"{model.tier} needs {need / GIB:.1f} GiB, {usable / GIB:.1f} GiB usable"
            if note not in skipped:
                skipped.append(note)
            continue
        if not why:
            gpu = f"{vram:.1f} GiB GPU memory" if vram is not None else "no dedicated GPU memory"
            why.append(f"{ram:.1f} GiB RAM, {gpu}")
        reason = f"{model.tier}: {', '.join(why)}"
        if skipped:
            reason += f" ({'; '.join(skipped)})"
        if not fits:
            reason += (f"; even this model may not fit ({need / GIB:.1f} GiB needed, {usable / GIB:.1f} GiB "
                       "usable): close other programs")
        return model, reason
    raise ValueError("No tier rule matched; the catalog needs a final rule without conditions.")


def kv_cache_default(ram_total: int, model_bytes: Optional[int] = None) -> Tuple[str, float]:
    """(KV-cache type, GiB left) for a server with `ram_total` bytes of RAM and a model of `model_bytes`: q8_0 (about
    half the memory of f16) when less than KV_LITTLE_RAM is left next to the weights and the system reserve, else f16
    (llama.cpp's default). A model of unknown size is estimated at half of the RAM left after the reserve."""
    usable = max(0, int(ram_total) - SYSTEM_RESERVE)
    weights = int(model_bytes * 1.05) if model_bytes else usable // 2
    left = max(0, usable - weights)
    return ("q8_0" if left < KV_LITTLE_RAM else "f16"), round(left / GIB, 1)


def kv_bytes_for(model: Any, kind: Optional[str] = "f16") -> int:
    """KV-cache bytes per token of `model` (a catalog ModelEntry, or its f16 bytes per token as a number) with the
    KV-cache type `kind` (KV_TYPE_FACTORS; None is f16), rounded up. ValueError for another type."""
    base = int(model if isinstance(model, (int, float)) else model.kv_bytes_per_token)
    factor = KV_TYPE_FACTORS.get(kind or "f16")
    if factor is None:
        raise ValueError(f"the KV-cache type is one of {', '.join(KV_TYPE_FACTORS)}, not '{kind}'")
    return max(1, -(-int(base * factor * 1024) // 1024))


# --- one memory budget for Ditto's server (chain ctx-local-mem) ----------------------------------------------------
# The prompt cache (--cache-ram): RAM outside the KV cache where llama-server keeps the states of earlier prompts. Its
# automatic size is the rule of 3.1.0 (an eighth of the RAM, 256 to 8192 MiB), but never more than half of the free RAM
# left next to the model, the KV cache of the context that must keep fitting and OS_MARGIN; below CACHE_RAM_MIN_MIB it
# is off (0). The person may set more, up to the hard limit (the RAM physically there next to the model, that context
# and SYSTEM_RESERVE); above the soft cap (the automatic value) an AI is refused and the person is warned once.
CACHE_RAM_RULE_MIB = (256, 8192)
CACHE_RAM_MIN_MIB = 64
# How long the server keeps the model loaded without requests (--sleep-idle-seconds, in minutes): above the soft cap
# the model holds its RAM for long; the hard limit is one day. 0 = never unload (only the person sets it).
KEEP_MINUTES_SOFT_CAP = 60
KEEP_MINUTES_HARD_LIMIT = 1440
CACHE_SETTINGS = ("local_cache_ram_mib", "local_cache_keep_minutes", "local_kv_cache")
NEVER = float("inf")


class CacheBudget(BaseModel):
    """What Ditto's server may hold on this computer (local_budget): the values the launcher uses (ram_mib,
    keep_minutes, disk_mib, kv_cache), the probe's automatic values (auto, by setting name), the soft caps and hard
    limits by setting name, a reason per setting, and the saved settings above their soft cap (over_soft_cap)."""
    ram_mib: int
    keep_minutes: int
    disk_mib: int = 0                   # slot files on disk: not used (ctx-slot-cache, if the measurement asks)
    kv_cache: str = "f16"
    auto: Dict[str, Any] = {}
    soft_caps: Dict[str, int] = {}
    hard_limits: Dict[str, int] = {}
    reasons: Dict[str, str] = {}
    over_soft_cap: List[str] = []

    def over_cap(self, key: str, value: Any) -> Optional[str]:
        return over_cap(self, key, value)

    def settings_view(self) -> Dict[str, Dict[str, Any]]:
        """Per setting: the automatic value, the soft cap, the hard limit and the reason (numbers and words only)."""
        return {key: {"auto": self.auto.get(key), "soft_cap": self.soft_caps.get(key),
                      "hard_limit": self.hard_limits.get(key), "reason": self.reasons.get(key, "")}
                for key in CACHE_SETTINGS}


def cache_number(key: str, value: Any) -> Optional[float]:
    """A cache setting's value as a number to compare: MiB, minutes (0 = never unload counts as endless, NEVER) or the
    KV-cache type's memory share (KV_TYPE_FACTORS); None for "auto" or nothing. ValueError for a value the setting
    cannot take: never a negative number, so llama.cpp's "-1 = no limit" cannot be reached."""
    text = str(value).strip().lower() if value is not None else "auto"
    if text in ("", "auto"):
        return None
    if key == "local_kv_cache":
        if text not in KV_TYPE_FACTORS:
            raise ValueError(f"{key} is auto, {', '.join(KV_TYPE_FACTORS)}, not '{value}'")
        return KV_TYPE_FACTORS[text]
    if not text.isdigit() or len(text) > 7:
        unit = "MiB" if key.endswith("_mib") else "minutes"
        raise ValueError(f"{key} is auto or a whole number of {unit} (0 or more), not '{value}'")
    number = int(text)
    if key == "local_cache_keep_minutes" and number == 0:
        return NEVER
    return float(number)


def over_cap(budget: CacheBudget, key: str, value: Any) -> Optional[str]:
    """"hard" when `value` of setting `key` is above this computer's hard limit, "soft" when above the soft cap (0 =
    never unload counts as above it), None otherwise ("auto" never is). ValueError for a value the setting cannot
    take."""
    number = cache_number(key, value)
    if number is None or key == "local_kv_cache":
        return None
    hard, soft = budget.hard_limits.get(key), budget.soft_caps.get(key)
    if hard is not None and number != NEVER and number > hard:
        return "hard"
    if soft is not None and number > soft:
        return "soft"
    return None


def _ram_in_use(hw: HardwareInfo, weights: int, kv: int, choice: Optional[BackendChoice]) -> int:
    """The part of the weights and the KV cache that lives in RAM: none when a dedicated GPU holds both (the rule of
    compute_context), else all of it."""
    gpu = choice.gpu if choice and choice.backend in ("cuda", "vulkan", "rocm") else None
    if gpu and gpu.vram_mib and not gpu.unified:
        vram = (gpu.vram_free_mib if gpu.vram_free_mib is not None else gpu.vram_mib * 0.9) * MIB
        if weights + kv + OS_MARGIN // 2 <= vram:
            return 0
    return weights + kv


def _saved_number(key: str, saved: Dict[str, Any]) -> Optional[float]:
    try:
        return cache_number(key, saved.get(key))
    except ValueError:
        return None  # a damaged saved value counts as auto (the settings schema refuses it when it is set)


def local_budget(hw: HardwareInfo, model_bytes: int, kv_bytes_per_token: int, ctx: int,
                 disk_free: Optional[int] = None, disk_total: Optional[int] = None,
                 saved: Optional[Dict[str, Any]] = None, *, choice: Optional[BackendChoice] = None,
                 keep_minutes_default: int = 10) -> CacheBudget:
    """The one memory budget of Ditto's server on this computer, from the measured RAM (HardwareInfo of the probe; never
    a guess): the KV-cache type (local_kv_cache; auto is the boards' rule, kv_cache_default), the prompt cache in RAM
    (local_cache_ram_mib) and how long the model stays loaded (local_cache_keep_minutes). `kv_bytes_per_token` is the
    model's f16 KV cache per token (the catalog's value), `ctx` the context that must keep fitting next to the prompt
    cache (the minimum context, or the one asked for), `choice` the GPU build (a model held by a dedicated GPU does not
    count in RAM). `saved` holds the person's settings (auto or a value). The launcher then computes the context with
    the prompt cache as reserve_bytes (compute_context), so the same RAM is counted once. Slot files on disk
    (disk_free, disk_total) are not used yet: disk_mib is 0."""
    saved = dict(saved or {})
    reasons: Dict[str, str] = {}
    weights = int((model_bytes or 0) * 1.05)
    auto_kind, left_gib = kv_cache_default(hw.ram_total, model_bytes or None)
    chosen_kind = str(saved.get("local_kv_cache") or "auto").strip().lower()
    if chosen_kind in KV_TYPE_FACTORS:
        kind = chosen_kind
        reasons["local_kv_cache"] = f"{kind} (your setting)"
    else:
        kind = auto_kind
        reasons["local_kv_cache"] = (f"{auto_kind} (automatic, the boards' rule: {left_gib:g} GiB RAM left next to the "
                                     "model and the system reserve)")
    kv = kv_bytes_for(kv_bytes_per_token, kind) * max(int(ctx or 0), 0)
    in_ram = _ram_in_use(hw, weights, kv, choice)
    low, high = CACHE_RAM_RULE_MIB
    rule = max(low, min(high, hw.ram_total // (8 * MIB)))
    room = max(0, hw.ram_available - in_ram - OS_MARGIN) // MIB
    auto_ram = min(rule, room // 2)
    if auto_ram < CACHE_RAM_MIN_MIB:
        auto_ram = 0
    hard_ram = max(auto_ram, max(0, hw.ram_total - SYSTEM_RESERVE - in_ram) // MIB)
    chosen = _saved_number("local_cache_ram_mib", saved)
    if chosen is None:
        ram = auto_ram
        reasons["local_cache_ram_mib"] = (
            f"{ram} MiB (automatic: an eighth of the RAM, {low} to {high} MiB, and at most half of the {room} MiB free "
            "RAM left next to the model, its context and the system's margin"
            + ("; too little is left, so the prompt cache is off)" if ram == 0 else ")"))
    elif chosen > hard_ram:
        ram = int(hard_ram)
        reasons["local_cache_ram_mib"] = (f"{ram} MiB (your {int(chosen)} MiB is above this computer's hard limit of "
                                          f"{hard_ram} MiB)")
    else:
        ram = int(chosen)
        reasons["local_cache_ram_mib"] = f"{ram} MiB (your setting)"
    keep_n = _saved_number("local_cache_keep_minutes", saved)
    if keep_n is None:
        keep = int(keep_minutes_default)
        reasons["local_cache_keep_minutes"] = (f"{keep} minutes (automatic)" if keep
                                               else "never unloaded (automatic)")
    elif keep_n == NEVER:
        keep = 0
        reasons["local_cache_keep_minutes"] = "never unloaded (your setting)"
    else:
        keep = int(min(keep_n, KEEP_MINUTES_HARD_LIMIT))
        reasons["local_cache_keep_minutes"] = f"{keep} minutes (your setting)"
    budget = CacheBudget(
        ram_mib=ram, keep_minutes=keep, kv_cache=kind,
        auto={"local_cache_ram_mib": auto_ram, "local_cache_keep_minutes": int(keep_minutes_default),
              "local_kv_cache": auto_kind},
        soft_caps={"local_cache_ram_mib": auto_ram,
                   "local_cache_keep_minutes": max(KEEP_MINUTES_SOFT_CAP, int(keep_minutes_default))},
        hard_limits={"local_cache_ram_mib": int(hard_ram), "local_cache_keep_minutes": KEEP_MINUTES_HARD_LIMIT},
        reasons=reasons)
    budget.over_soft_cap = [key for key in ("local_cache_ram_mib", "local_cache_keep_minutes")
                            if _saved_number(key, saved) is not None and over_cap(budget, key, saved.get(key))]
    return budget


def compute_context(model: ModelEntry, hw: HardwareInfo, choice: Optional[BackendChoice],
                    defaults: ServerDefaults, cap: Optional[int] = None,
                    model_bytes: Optional[int] = None, kv_kind: str = "f16",
                    reserve_bytes: int = 0) -> Tuple[int, str]:
    """Context length (-c) that fits next to the model: never the native context, never above the cap. `kv_kind` is the
    KV-cache type the server gets (kv_bytes_for), `reserve_bytes` RAM kept free for something beside it (the prompt
    cache of local_budget). With the defaults the result is the one of 3.1.0."""
    cap = min(cap or defaults.ctx_cap, model.native_context)
    floor = min(defaults.ctx_min, cap)
    weights = int((model_bytes or model.size or 0) * 1.05)
    per_token = kv_bytes_for(model, kv_kind)
    reserve = max(0, int(reserve_bytes or 0))
    gpu = choice.gpu if choice and choice.backend in ("cuda", "vulkan", "rocm") else None
    if gpu and gpu.vram_mib and not gpu.unified:
        vram = (gpu.vram_free_mib if gpu.vram_free_mib is not None else gpu.vram_mib * 0.9) * MIB
        if weights + OS_MARGIN // 2 <= vram:
            budget, where = vram - weights - OS_MARGIN // 2, f"{vram / GIB:.1f} GiB free GPU memory"
        else:
            # Partial offload: layers that do not fit stay in RAM (the GPU's half margin and the OS margin).
            budget = vram + hw.ram_available - weights - OS_MARGIN - OS_MARGIN // 2 - reserve
            where = f"{vram / GIB:.1f} GiB GPU + {hw.ram_available / GIB:.1f} GiB free RAM"
    else:
        # CPU, Apple Silicon and integrated GPUs: weights and KV cache live in the (shared) RAM.
        budget = hw.ram_available - weights - OS_MARGIN - reserve  # leaves room for the OS and the framework
        where = f"{hw.ram_available / GIB:.1f} GiB free RAM"
    fits = max(int(budget), 0) // per_token
    ctx = min(fits, cap) // 256 * 256
    if ctx < floor:
        return floor, f"{floor} tokens (minimum; only ~{fits} fit in {where} next to the model)"
    limit = "cap" if ctx == cap else "memory"
    kind = "" if kv_kind in (None, "f16") else f" {kv_kind}"
    return ctx, f"{ctx} tokens ({limit}; {where}, {per_token // 1024} KiB{kind} KV cache per token)"
