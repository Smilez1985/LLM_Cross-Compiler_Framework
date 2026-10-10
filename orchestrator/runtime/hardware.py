#!/usr/bin/env python3
"""
Hardware detection for the local runtime: which llama.cpp build fits this machine, which Ditto model
tier, and how much context fits next to the model. Every decision carries a human-readable reason.
"""

import ctypes.util
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Literal, Optional, Sequence, Tuple

import psutil
from pydantic import BaseModel

from orchestrator.runtime.catalog import GPU_BACKENDS, ModelEntry, RuntimeAsset, RuntimeCatalog, ServerDefaults

GIB = 1024 ** 3
MIB = 1024 ** 2

# (command, timeout) -> (returncode, stdout, stderr); injectable for tests.
Runner = Callable[[Sequence[str], float], Tuple[int, str, str]]


class GpuInfo(BaseModel):
    vendor: Literal["nvidia", "amd", "intel", "apple", "other"]
    name: str
    discrete: Optional[bool] = None
    vram_mib: Optional[int] = None
    vram_free_mib: Optional[int] = None
    driver_version: Optional[str] = None
    compute_cap: Optional[float] = None
    source: str = ""

    def label(self) -> str:
        vram = f", {self.vram_mib / 1024:.1f} GiB" if self.vram_mib else ""
        return f"{self.name}{vram}"


class HardwareInfo(BaseModel):
    os: str
    arch: str
    ram_total: int
    ram_available: int
    gpus: List[GpuInfo] = []
    vulkan: bool = False   # a Vulkan loader or Vulkan device was seen

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


def probe_nvidia(run: Runner) -> List[GpuInfo]:
    fields = "driver_version,compute_cap,memory.total,memory.free,name"
    rc, out, err = run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"], 15)
    if rc != 0 and "compute_cap" in (out + err):
        # Drivers before R510 do not know compute_cap; ask without it and leave the column empty.
        rc, out, _ = run(["nvidia-smi", "--query-gpu=driver_version,memory.total,memory.free,name",
                          "--format=csv,noheader,nounits"], 15)
        out = "\n".join(line.replace(",", ",,", 1) for line in out.splitlines())
    return parse_nvidia_smi(out) if rc == 0 else []


_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x1022": "amd", "0x8086": "intel", "0x106b": "apple"}


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
        result.append(GpuInfo(vendor=_VENDORS.get(dev.get("vendorID", "").lower(), "other"), name=dev["deviceName"],
                              discrete=dtype.endswith("DISCRETE_GPU"), driver_version=dev.get("driverInfo") or None,
                              source="vulkaninfo"))
    return result


def probe_vulkaninfo(run: Runner) -> Tuple[bool, List[GpuInfo]]:
    rc, out, _ = run(["vulkaninfo", "--summary"], 20)
    if rc != 0 or "Devices" not in out:
        return False, []
    return True, parse_vulkaninfo_summary(out)


def _guess_discrete(vendor: str, name: str) -> Optional[bool]:
    """Windows adapter names only: dedicated Radeon RX/PRO and Arc A/B cards; plain "Graphics" is integrated."""
    if vendor == "amd":
        return bool(re.search(r"\b(RX|PRO W|PRO V|Instinct)\b", name, re.I))
    if vendor == "intel":
        return bool(re.search(r"\bArc\b.*\b[AB]\d{3}", name, re.I))
    return None


def probe_windows_adapters(run: Runner) -> List[GpuInfo]:
    """Display adapters from WMI. Every current AMD/Intel/NVIDIA driver on Windows ships the Vulkan runtime."""
    script = ("Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility,DriverVersion "
              "| ConvertTo-Json -Compress")
    rc, out, _ = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 20)
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    gpus = []
    for item in data if isinstance(data, list) else [data]:
        name = str(item.get("Name") or "")
        maker = f"{item.get('AdapterCompatibility') or ''} {name}".lower()
        vendor = ("nvidia" if "nvidia" in maker else "amd" if ("advanced micro" in maker or "amd" in maker)
                  else "intel" if "intel" in maker else "other")
        if vendor == "other" or not name:
            continue  # Microsoft Basic Display Adapter, remote display drivers
        gpus.append(GpuInfo(vendor=vendor, name=name, discrete=_guess_discrete(vendor, name),
                            driver_version=item.get("DriverVersion"), source="windows"))
    return gpus


def probe_linux_sysfs(root: Path) -> List[GpuInfo]:
    """AMD GPUs from /sys/class/drm (amdgpu reports dedicated VRAM; APUs only a small carve-out)."""
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
            vram = int((dev / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            vram = 0
        gpus.append(GpuInfo(vendor="amd", name=f"AMD GPU ({card.name})", discrete=vram >= 2 * GIB,
                            vram_mib=vram // MIB or None, source="sysfs"))
    return gpus


def detect_hardware(run: Optional[Runner] = None, system: Optional[str] = None, machine: Optional[str] = None,
                    memory: Optional[Tuple[int, int]] = None, sysfs_root: Path = Path("/sys")) -> HardwareInfo:
    """Probes RAM and GPUs. Probe tools that are missing or fail simply contribute nothing."""
    run = run or _default_runner
    os_name, arch = normalize_platform(system, machine)
    if memory is None:
        vm = psutil.virtual_memory()
        memory = (vm.total, vm.available)
    ram_total, ram_available = memory

    if os_name == "macos":
        gpus = []
        if arch == "arm64":
            # Unified memory: Metal may use roughly two thirds of the RAM for buffers.
            gpus.append(GpuInfo(vendor="apple", name="Apple GPU (unified memory)", discrete=False,
                                vram_mib=int(ram_total * 2 / 3) // MIB, source="macos"))
        return HardwareInfo(os=os_name, arch=arch, ram_total=ram_total, ram_available=ram_available, gpus=gpus)

    gpus = probe_nvidia(run)
    vulkan, vk_gpus = probe_vulkaninfo(run)
    others = [g for g in vk_gpus if g.vendor != "nvidia" or not gpus]
    if not vk_gpus:
        if os_name == "windows":
            adapters = probe_windows_adapters(run)
            others = [g for g in adapters if g.vendor != "nvidia" or not gpus]
            vulkan = vulkan or bool(adapters)
        elif os_name == "linux":
            others = probe_linux_sysfs(sysfs_root)
            vulkan = vulkan or bool(others and ctypes.util.find_library("vulkan"))
    elif os_name == "linux":
        # vulkaninfo has no memory sizes; amdgpu's sysfs does. Matched only when the count agrees.
        amd_vk = [g for g in others if g.vendor == "amd"]
        amd_fs = probe_linux_sysfs(sysfs_root)
        if amd_vk and len(amd_vk) == len(amd_fs):
            for g, fs in zip(amd_vk, amd_fs):
                g.vram_mib = fs.vram_mib
    return HardwareInfo(os=os_name, arch=arch, ram_total=ram_total, ram_available=ram_available,
                        gpus=gpus + others, vulkan=vulkan or bool(vk_gpus))


# --- decisions ---------------------------------------------------------------------------------------------

def _driver_major(version: Optional[str]) -> Optional[float]:
    m = re.match(r"(\d+)(?:\.(\d+))?", version or "")
    return float(f"{m.group(1)}.{m.group(2) or 0}") if m else None


def _best(gpus: List[GpuInfo]) -> Optional[GpuInfo]:
    return max(gpus, key=lambda g: (g.discrete is True, g.vram_mib or 0), default=None)


def select_backend(hw: HardwareInfo, catalog: RuntimeCatalog, prefer: Optional[str] = None) -> BackendChoice:
    """Picks the llama.cpp build for this machine. ROCm builds are only used when asked for (prefer="rocm")."""
    assets = catalog.assets_for(hw.os, hw.arch)
    if not assets:
        return BackendChoice(reason=f"No llama.cpp build for {hw.os}/{hw.arch} in the catalog.")
    by_backend = {}
    for a in assets:
        by_backend.setdefault(a.backend, []).append(a)
    for group in by_backend.values():
        group.sort(key=lambda a: tuple(int(x) for x in (a.cuda or "0.0").split(".")), reverse=True)

    nvidia = _best([g for g in hw.gpus if g.vendor == "nvidia"])

    def cuda_asset() -> Tuple[Optional[RuntimeAsset], str]:
        if not nvidia:
            return None, "no NVIDIA GPU found (nvidia-smi)"
        driver = _driver_major(nvidia.driver_version)
        cap = nvidia.compute_cap
        notes = []
        for asset in by_backend.get("cuda", []):
            if asset.min_driver and (driver is None or driver < asset.min_driver):
                notes.append(f"CUDA {asset.cuda} needs driver >= {asset.min_driver:g}")
                continue
            if asset.min_compute_cap and cap is not None and cap < asset.min_compute_cap:
                notes.append(f"CUDA {asset.cuda} needs compute capability >= {asset.min_compute_cap:g}")
                continue
            return asset, (f"NVIDIA {nvidia.label()} (driver {nvidia.driver_version}"
                           f"{f', compute {cap:g}' if cap else ''}) -> CUDA {asset.cuda} build")
        why = "; ".join(notes) or f"no CUDA build for {hw.os}/{hw.arch}"
        return None, f"NVIDIA {nvidia.name} with driver {nvidia.driver_version}: {why}"

    def pick(backend: str, gpu: Optional[GpuInfo], reason: str) -> BackendChoice:
        return BackendChoice(asset=by_backend[backend][0], backend=backend, gpu=gpu, reason=reason)

    if prefer and prefer != "auto":
        if prefer == "cuda":
            asset, reason = cuda_asset()
            if asset:
                return BackendChoice(asset=asset, backend="cuda", gpu=nvidia, reason=reason)
            return BackendChoice(reason=f"CUDA requested, but {reason}.")
        if prefer not in by_backend:
            have = ", ".join(sorted(by_backend))
            return BackendChoice(reason=f"No '{prefer}' build for {hw.os}/{hw.arch} (available: {have}).")
        gpu = _best([g for g in hw.gpus if g.vendor != "nvidia"]) if prefer in ("vulkan", "rocm") else None
        return pick(prefer, gpu or (nvidia if prefer == "vulkan" else None), f"{prefer} build requested")

    if "metal" in by_backend:
        apple = _best([g for g in hw.gpus if g.vendor == "apple"])
        return pick("metal", apple, "Apple Silicon -> Metal build (GPU with unified memory)")

    fallback_note = ""
    if nvidia:
        asset, reason = cuda_asset()
        if asset:
            return BackendChoice(asset=asset, backend="cuda", gpu=nvidia, reason=reason)
        fallback_note = reason + "; "

    if "vulkan" in by_backend and hw.vulkan:
        discrete = _best([g for g in hw.gpus if g.discrete])
        if discrete:
            return pick("vulkan", discrete, f"{fallback_note}{discrete.vendor.upper()} {discrete.label()} "
                                            f"-> Vulkan build")
        integrated = _best(hw.gpus)
        if integrated:
            fallback_note += (f"only an integrated GPU ({integrated.name}); try '--backend vulkan' to use it; ")

    if "cpu" in by_backend:
        return pick("cpu", None, f"{fallback_note}CPU build (no usable GPU backend)")
    return BackendChoice(reason=f"{fallback_note}No CPU build for {hw.os}/{hw.arch} in the catalog.")


def usable_vram_gib(choice: Optional[BackendChoice]) -> Optional[float]:
    """GPU memory that counts for model choice: only with a GPU backend and a known size."""
    if not choice or choice.backend not in GPU_BACKENDS or not choice.gpu or not choice.gpu.vram_mib:
        return None
    return choice.gpu.vram_mib / 1024


def choose_model_tier(hw: HardwareInfo, catalog: RuntimeCatalog,
                      choice: Optional[BackendChoice] = None) -> Tuple[ModelEntry, str]:
    """First matching rule from the catalog's tier_rules, with the reason."""
    ram = hw.ram_total / GIB
    vram = usable_vram_gib(choice)
    for rule in catalog.tier_rules:
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
        if not why:
            gpu = f"{vram:.1f} GiB GPU memory" if vram is not None else "no GPU memory usable by the runtime"
            why.append(f"{ram:.1f} GiB RAM, {gpu}")
        return model, f"{model.tier}: {', '.join(why)}"
    raise ValueError("No tier rule matched; the catalog needs a final rule without conditions.")


def compute_context(model: ModelEntry, hw: HardwareInfo, choice: Optional[BackendChoice],
                    defaults: ServerDefaults, cap: Optional[int] = None,
                    model_bytes: Optional[int] = None) -> Tuple[int, str]:
    """Context length (-c) that fits next to the model: never the native context, never above the cap."""
    cap = min(cap or defaults.ctx_cap, model.native_context)
    floor = min(defaults.ctx_min, cap)
    weights = int((model_bytes or model.size or 0) * 1.05)
    gpu = choice.gpu if choice and choice.backend in ("cuda", "vulkan", "rocm") else None
    if gpu and gpu.vram_mib:
        vram = (gpu.vram_free_mib if gpu.vram_free_mib is not None else gpu.vram_mib * 0.9) * MIB
        if weights + 512 * MIB <= vram:
            budget, where = vram - weights - 512 * MIB, f"{vram / GIB:.1f} GiB free GPU memory"
        else:
            # Partial offload: layers that do not fit stay in RAM.
            budget = vram + hw.ram_available - weights - 1536 * MIB
            where = f"{vram / GIB:.1f} GiB GPU + {hw.ram_available / GIB:.1f} GiB free RAM"
    else:
        budget = hw.ram_available - weights - GIB  # leaves room for the OS and the framework
        where = f"{hw.ram_available / GIB:.1f} GiB free RAM"
    fits = max(int(budget), 0) // model.kv_bytes_per_token
    ctx = min(fits, cap) // 256 * 256
    if ctx < floor:
        return floor, f"{floor} tokens (minimum; only ~{fits} fit in {where} next to the model)"
    limit = "cap" if ctx == cap else "memory"
    return ctx, f"{ctx} tokens ({limit}; {where}, {model.kv_bytes_per_token // 1024} KiB KV cache per token)"
