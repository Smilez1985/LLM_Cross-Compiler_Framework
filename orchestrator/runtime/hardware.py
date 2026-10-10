#!/usr/bin/env python3
"""
Hardware detection for the local runtime: which llama.cpp build fits this machine, which Ditto model
tier, and how much context fits next to the model. Every decision carries a human-readable reason.

Probes (each optional; a missing tool contributes nothing): nvidia-smi for NVIDIA GPUs (driver, compute
capability, VRAM), vulkaninfo for any Vulkan device, amdgpu's sysfs and the KFD topology on Linux (VRAM,
gfx target), WMI and the display-class registry on Windows (adapter names, dedicated VRAM).
"""

import ctypes.util
import glob
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Sequence, Tuple

import psutil
from pydantic import BaseModel

from orchestrator.runtime.catalog import (GPU_BACKENDS, ModelEntry, RuntimeAsset, RuntimeCatalog, ServerDefaults,
                                          version_tuple)

GIB = 1024 ** 3
MIB = 1024 ** 2
SYSTEM_RESERVE = int(1.5 * GIB)  # RAM left to the OS, the GUI and the knowledge base when a model is chosen

# (command, timeout) -> (returncode, stdout, stderr); returncode -1 means "not found / not runnable".
Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]
LibraryFinder = Callable[[str], Optional[str]]

# PCI device ids of AMD APU graphics (amdgpu_drv.c): their "VRAM" is a carve-out of the system RAM.
_AMD_APU_DEVICE_IDS = frozenset({
    "0x15dd", "0x15d8",                      # Raven, Picasso
    "0x1636", "0x1638", "0x164c", "0x15e7",  # Renoir, Cezanne, Lucienne, Barcelo
    "0x163f", "0x1435",                      # Van Gogh (Steam Deck)
    "0x1681", "0x1506", "0x164e", "0x13c0",  # Rembrandt, Mendocino, Raphael, Granite Ridge
    "0x15bf", "0x15c8",                      # Phoenix / Hawk Point (Radeon 780M, Ryzen Z1 Extreme), Phoenix2 (Z1)
    "0x150e", "0x1114", "0x1586",            # Strix Point, Krackan Point, Strix Halo
})
_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x1022": "amd", "0x8086": "intel", "0x106b": "apple"}


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

    def summary(self) -> str:
        gpus = "; ".join(g.label() for g in self.gpus) or "no GPU found"
        return f"{self.os}/{self.arch}, {self.ram_total / GIB:.1f} GiB RAM, {gpus}"


class BackendChoice(BaseModel):
    asset: Optional[RuntimeAsset] = None
    backend: Optional[str] = None
    gpu: Optional[GpuInfo] = None
    reason: str


def _default_runner(cmd: Sequence[str], timeout: float) -> Tuple[int, str, str]:
    """Runs a probe command; a missing tool or a hang counts as "not available"."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        res = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False,
                             stdin=subprocess.DEVNULL, creationflags=flags)
        return res.returncode, res.stdout or "", res.stderr or ""
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


def normalize_platform(system: Optional[str] = None, machine: Optional[str] = None) -> Tuple[str, str]:
    system = (system or platform.system()).lower()
    os_name = {"darwin": "macos"}.get(system, system)
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "arm64e": "arm64"}.get(machine, machine)
    return os_name, arch


# --- probes ------------------------------------------------------------------------------------------------

def _float(text: str) -> Optional[float]:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def parse_nvidia_smi(output: str) -> List[GpuInfo]:
    """Lines of `--query-gpu=driver_version,compute_cap,memory.total,memory.free,name --format=csv,noheader,nounits`."""
    gpus = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(",", 4)]
        if len(parts) != 5 or not parts[0] or parts[0].startswith("["):
            continue
        driver, cap, total, free, name = parts
        total_f, free_f = _float(total), _float(free)
        gpus.append(GpuInfo(vendor="nvidia", name=name, discrete=True, driver_version=driver,
                            compute_cap=_float(cap), vram_mib=int(total_f) if total_f else None,
                            vram_free_mib=int(free_f) if free_f is not None else None, source="nvidia-smi"))
    return gpus


def _nvidia_smi_paths(os_name: str) -> List[str]:
    paths = ["nvidia-smi"]
    if os_name == "windows":
        # Older drivers install it here without adding it to PATH (current ones put it into System32).
        for base in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles")):
            if base:
                paths.append(str(Path(base) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"))
    return paths


def probe_nvidia(run: Runner, os_name: str = "linux") -> List[GpuInfo]:
    fields = "driver_version,compute_cap,memory.total,memory.free,name"
    for exe in _nvidia_smi_paths(os_name):
        rc, out, err = run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"], 15)
        if rc == -1:
            continue  # not installed here
        if rc != 0 and "compute_cap" in (out + err):
            # Drivers before R510 do not know compute_cap; ask without it and leave the column empty.
            rc, out, _ = run([exe, "--query-gpu=driver_version,memory.total,memory.free,name",
                              "--format=csv,noheader,nounits"], 15)
            out = "\n".join(line.replace(",", ",,", 1) for line in out.splitlines())
        return parse_nvidia_smi(out) if rc == 0 else []
    return []


def parse_vulkaninfo_summary(output: str) -> List[GpuInfo]:
    """The "Devices:" section of `vulkaninfo --summary` (GPU0: ... key = value lines)."""
    gpus, current = [], None
    for raw in output.splitlines():
        line = raw.strip()
        if re.fullmatch(r"GPU\d+:", line):
            current = {}
            gpus.append(current)
        elif current is not None and " = " in line:
            key, _, value = line.partition(" = ")
            current[key.strip()] = value.strip()
    result = []
    for dev in gpus:
        dtype = dev.get("deviceType", "")
        if dtype.endswith(("_CPU", "_OTHER")) or not dev.get("deviceName"):
            continue  # llvmpipe/lavapipe and other software renderers
        integrated = dtype.endswith("INTEGRATED_GPU")
        result.append(GpuInfo(vendor=_VENDORS.get(dev.get("vendorID", "").lower(), "other"), name=dev["deviceName"],
                              discrete=dtype.endswith("DISCRETE_GPU"), unified=integrated,
                              driver_version=dev.get("driverInfo") or None, source="vulkaninfo"))
    return result


def probe_vulkaninfo(run: Runner) -> Tuple[bool, List[GpuInfo]]:
    rc, out, _ = run(["vulkaninfo", "--summary"], 20)
    if rc != 0 or "Devices" not in out:
        return False, []
    return True, parse_vulkaninfo_summary(out)


def guess_discrete(vendor: str, name: str) -> Optional[bool]:
    """From a Windows adapter name: dedicated Radeon RX/PRO and Arc A/B cards; APU graphics are integrated."""
    if vendor == "amd":
        # APUs: "Radeon(TM) Graphics", "Radeon 780M Graphics", "Radeon RX Vega 11 Graphics", "Radeon 8060S".
        if re.search(r"Radeon(\s*\(TM\))?\s+Graphics|\b\d{3}M\b|\bVega\s+(3|6|7|8|9|10|11)\b|\b80[456]0S\b", name,
                     re.I):
            return False
        if re.search(r"\b(RX|PRO|Instinct|FirePro|R9|R7)\b", name, re.I):
            return True
        return None
    if vendor == "intel":
        return bool(re.search(r"\bArc\b.*\b[AB]\d{3}", name, re.I))
    return None


# One PowerShell call: adapter names from WMI, and the exact dedicated memory from the display-class registry
# (WMI's AdapterRAM is a 32-bit value and stops at 4 GiB).
_WINDOWS_ADAPTERS = r"""
$mem = @{}
$display = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*'
Get-ItemProperty $display -ErrorAction SilentlyContinue | ForEach-Object {
  $v = $_.'HardwareInformation.qwMemorySize'
  if ($_.DriverDesc -and ($v -is [long] -or $v -is [int])) { $mem[$_.DriverDesc] = [int64]$v }
}
Get-CimInstance Win32_VideoController | ForEach-Object {
  [pscustomobject]@{Name=$_.Name; AdapterCompatibility=$_.AdapterCompatibility;
                    DriverVersion=$_.DriverVersion; Memory=$mem[$_.Name]}
} | ConvertTo-Json -Compress
"""


def probe_windows_adapters(run: Runner) -> List[GpuInfo]:
    """Display adapters. Every current AMD/Intel/NVIDIA driver on Windows ships the Vulkan runtime."""
    rc, out, _ = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_ADAPTERS], 20)
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    gpus = []
    for item in data if isinstance(data, list) else [data]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "")
        maker = f"{item.get('AdapterCompatibility') or ''} {name}".lower()
        vendor = ("nvidia" if "nvidia" in maker else "amd" if ("advanced micro" in maker or "amd" in maker)
                  else "intel" if "intel" in maker else "other")
        if vendor == "other" or not name:
            continue  # Microsoft Basic Display Adapter, remote display drivers
        discrete = guess_discrete(vendor, name)
        memory = item.get("Memory")
        vram = int(memory) // MIB if isinstance(memory, (int, float)) and memory > 0 and discrete else None
        gpus.append(GpuInfo(vendor=vendor, name=name, discrete=discrete, unified=discrete is False, vram_mib=vram,
                            driver_version=item.get("DriverVersion"), source="windows"))
    return gpus


def _gfx_name(version: int) -> str:
    """KFD gfx_target_version (major*10000 + minor*100 + stepping) -> LLVM target name: 110003 -> gfx1103."""
    return f"gfx{version // 10000}{(version // 100) % 100:x}{version % 100:x}"


def _kfd_gfx_targets(root: Path) -> Dict[int, str]:
    """DRM render minor -> gfx target, from the KFD topology of amdgpu compute nodes."""
    targets = {}
    for props in (root / "class" / "kfd" / "kfd" / "topology" / "nodes").glob("*/properties"):
        try:
            values = dict(line.split(None, 1) for line in props.read_text().splitlines() if " " in line.strip())
            version, minor = int(values.get("gfx_target_version", "0")), int(values.get("drm_render_minor", "-1"))
        except (OSError, ValueError):
            continue
        if version > 0 and minor >= 0:
            targets[minor] = _gfx_name(version)
    return targets


def probe_linux_sysfs(root: Path) -> List[GpuInfo]:
    """AMD GPUs from /sys/class/drm: dedicated VRAM, APU detection and the gfx target."""
    targets = _kfd_gfx_targets(root)
    gpus = []
    for card in sorted((root / "class" / "drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue  # connectors such as card0-DP-1
        dev = card / "device"
        try:
            vendor = (dev / "vendor").read_text().strip().lower()
        except OSError:
            continue
        if _VENDORS.get(vendor) != "amd":
            continue
        try:
            device_id = (dev / "device").read_text().strip().lower()
        except OSError:
            device_id = ""
        try:
            vram = int((dev / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            vram = 0
        # An APU reports only its carve-out as VRAM (512 MiB to a few GiB, set in the firmware).
        apu = device_id in _AMD_APU_DEVICE_IDS or vram < 2 * GIB
        gfx = None
        for render in (dev / "drm").glob("renderD*"):
            try:
                gfx = targets.get(int(render.name[len("renderD"):]), gfx)
            except ValueError:
                pass
        kind = "integrated" if apu else "dedicated"
        gpus.append(GpuInfo(vendor="amd", name=f"AMD Radeon {gfx or device_id or card.name} ({kind})",
                            discrete=not apu, unified=apu, vram_mib=vram // MIB or None, gfx_target=gfx,
                            source="sysfs"))
    return gpus


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\((TM|R)\)|[™®]", "", name, flags=re.I)).strip().lower()


def _hip_runtime(find_library: LibraryFinder, rocm_globs: Sequence[str]) -> bool:
    if find_library("amdhip64"):
        return True
    return any(glob.glob(pattern) for pattern in rocm_globs)


def detect_hardware(run: Optional[Runner] = None, system: Optional[str] = None, machine: Optional[str] = None,
                    memory: Optional[Tuple[int, int]] = None, sysfs_root: Path = Path("/sys"),
                    find_library: Optional[LibraryFinder] = None,
                    rocm_globs: Sequence[str] = ("/opt/rocm*/lib*/libamdhip64.so*",)) -> HardwareInfo:
    """Probes RAM and GPUs. Probe tools that are missing or fail simply contribute nothing."""
    run = run or _default_runner
    find_library = find_library or ctypes.util.find_library
    os_name, arch = normalize_platform(system, machine)
    if memory is None:
        vm = psutil.virtual_memory()
        memory = (vm.total, vm.available)
    ram_total, ram_available = memory

    if os_name == "macos":
        gpus = []
        if arch == "arm64":
            # Unified memory: Metal may use roughly two thirds of the RAM for buffers.
            gpus.append(GpuInfo(vendor="apple", name="Apple Silicon GPU", discrete=False, unified=True,
                                vram_mib=int(ram_total * 2 / 3) // MIB, source="macos"))
        return HardwareInfo(os=os_name, arch=arch, ram_total=ram_total, ram_available=ram_available, gpus=gpus)

    nvidia = probe_nvidia(run, os_name)
    vulkan, vk_gpus = probe_vulkaninfo(run)
    others = [g for g in vk_gpus if g.vendor != "nvidia" or not nvidia]
    hip = None
    if os_name == "windows":
        adapters = [g for g in probe_windows_adapters(run) if g.vendor != "nvidia" or not nvidia]
        if not vk_gpus:
            others = adapters
            vulkan = bool(adapters)
        else:
            # vulkaninfo knows the device type, the registry the memory size.
            by_name = {_norm_name(a.name): a for a in adapters}
            for g in others:
                match = by_name.get(_norm_name(g.name))
                if match and match.vram_mib and not g.unified:
                    g.vram_mib = match.vram_mib
    elif os_name == "linux":
        sysfs = probe_linux_sysfs(sysfs_root)
        if not vk_gpus:
            others = sysfs
            # Without vulkaninfo: a Vulkan loader plus a GPU whose driver ships a Vulkan ICD (Mesa, NVIDIA).
            vulkan = bool((others or nvidia) and find_library("vulkan"))
        else:
            # vulkaninfo has no memory sizes; amdgpu's sysfs does. Matched only when the count agrees.
            amd_vk = [g for g in others if g.vendor == "amd"]
            if amd_vk and len(amd_vk) == len(sysfs):
                for g, fs in zip(amd_vk, sysfs):
                    g.vram_mib, g.gfx_target = fs.vram_mib, fs.gfx_target
        hip = _hip_runtime(find_library, rocm_globs)
    return HardwareInfo(os=os_name, arch=arch, ram_total=ram_total, ram_available=ram_available,
                        gpus=nvidia + others, vulkan=vulkan or bool(vk_gpus), hip_runtime=hip)


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


def select_backend(hw: HardwareInfo, catalog: RuntimeCatalog, prefer: Optional[str] = None) -> BackendChoice:
    """Picks the llama.cpp build for this machine. ROCm builds are only used when asked for (prefer="rocm")."""
    assets = catalog.assets_for(hw.os, hw.arch)
    if not assets:
        return BackendChoice(reason=f"No llama.cpp build for {hw.os}/{hw.arch} in the catalog.")
    by_backend: Dict[str, List[RuntimeAsset]] = {}
    for a in assets:
        by_backend.setdefault(a.backend, []).append(a)
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
        why = "; ".join(notes) or f"no CUDA build for {hw.os}/{hw.arch}"
        hint = "; a newer NVIDIA driver enables the CUDA build" if any("driver" in n for n in notes) else ""
        return None, f"{_named(nvidia)} (driver {nvidia.driver_version}{cap}): {why}{hint}"

    def rocm_asset() -> Tuple[Optional[RuntimeAsset], Optional[GpuInfo], str]:
        if "rocm" not in by_backend:
            return None, None, f"no ROCm build for {hw.os}/{hw.arch}"
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


def compute_context(model: ModelEntry, hw: HardwareInfo, choice: Optional[BackendChoice],
                    defaults: ServerDefaults, cap: Optional[int] = None,
                    model_bytes: Optional[int] = None) -> Tuple[int, str]:
    """Context length (-c) that fits next to the model: never the native context, never above the cap."""
    cap = min(cap or defaults.ctx_cap, model.native_context)
    floor = min(defaults.ctx_min, cap)
    weights = int((model_bytes or model.size or 0) * 1.05)
    gpu = choice.gpu if choice and choice.backend in ("cuda", "vulkan", "rocm") else None
    if gpu and gpu.vram_mib and not gpu.unified:
        vram = (gpu.vram_free_mib if gpu.vram_free_mib is not None else gpu.vram_mib * 0.9) * MIB
        if weights + 512 * MIB <= vram:
            budget, where = vram - weights - 512 * MIB, f"{vram / GIB:.1f} GiB free GPU memory"
        else:
            # Partial offload: layers that do not fit stay in RAM.
            budget = vram + hw.ram_available - weights - 1536 * MIB
            where = f"{vram / GIB:.1f} GiB GPU + {hw.ram_available / GIB:.1f} GiB free RAM"
    else:
        # CPU, Apple Silicon and integrated GPUs: weights and KV cache live in the (shared) RAM.
        budget = hw.ram_available - weights - GIB  # leaves room for the OS and the framework
        where = f"{hw.ram_available / GIB:.1f} GiB free RAM"
    fits = max(int(budget), 0) // model.kv_bytes_per_token
    ctx = min(fits, cap) // 256 * 256
    if ctx < floor:
        return floor, f"{floor} tokens (minimum; only ~{fits} fit in {where} next to the model)"
    limit = "cap" if ctx == cap else "memory"
    return ctx, f"{ctx} tokens ({limit}; {where}, {model.kv_bytes_per_token // 1024} KiB KV cache per token)"
