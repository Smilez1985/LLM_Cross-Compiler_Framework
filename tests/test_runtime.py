"""
Managed llama-server runtime: catalog, hardware detection and build/model choice, pinned downloads, atomic
install, the server process, the CLI commands and the "local" provider of LLMClient.

No real downloads and no real GPU: nvidia-smi / vulkaninfo / PowerShell output samples, sysfs trees in tmp_path,
a fake HTTP session with Range support, archives built in tmp_path and a small Python stand-in for llama-server
that speaks /health, /v1/models and /v1/chat/completions with API-key checks.
"""

import contextlib
import hashlib
import io
import json
import os
import signal
import stat
import sys
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
import requests
import yaml

from orchestrator.runtime import llama_server as ls
from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog, version_tuple
from orchestrator.runtime.hardware import (GIB, MIB, BackendChoice, GpuInfo, HardwareInfo, choose_model_tier,
                                           compute_context, detect_hardware, guess_discrete, model_fits,
                                           parse_nvidia_smi, parse_vulkaninfo_summary, probe_nvidia, select_backend,
                                           usable_vram_gib)
from orchestrator.runtime.llama_server import (IntegrityError, LocalLLMRuntime, LocalRuntimeError,
                                               build_server_command, popen_options, read_endpoint, server_env)

CATALOG = load_catalog()
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="process groups and shebang scripts are POSIX")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gone(pid: int) -> bool:
    """The process has exited (a zombie waiting for its parent counts as gone)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


# --- probe samples ---------------------------------------------------------------------------------------------

def nvidia_line(driver="580.95.05", cap="7.5", total=11264, free=10850, name="NVIDIA GeForce RTX 2080 Ti"):
    return f"{driver}, {cap}, {total}, {free}, {name}"


VULKAN_780M = """\
==========
VULKANINFO
==========

Vulkan Instance Version: 1.4.309

Devices:
========
GPU0:
\tapiVersion         = 1.4.305
\tdriverVersion      = 25.0.7
\tvendorID           = 0x1002
\tdeviceID           = 0x15bf
\tdeviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
\tdeviceName         = AMD Radeon 780M Graphics (RADV PHOENIX)
\tdriverID           = DRIVER_ID_MESA_RADV
\tdriverName         = radv
\tdriverInfo         = Mesa 25.0.7
GPU1:
\tapiVersion         = 1.4.305
\tvendorID           = 0x10005
\tdeviceID           = 0x0000
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 19.1.7, 256 bits)
"""

VULKAN_7900XTX = """\
Devices:
========
GPU0:
\tvendorID           = 0x1002
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
\tdeviceName         = AMD Radeon RX 7900 XTX (RADV NAVI31)
\tdriverInfo         = Mesa 25.0.7
"""


VULKAN_SOFTWARE_ONLY = """\
Devices:
========
GPU0:
\tvendorID           = 0x10005
\tdeviceID           = 0x0000
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 19.1.7, 256 bits)
"""


class FakeRunner:
    """Answers probe commands by tool name (nvidia_smi, vulkaninfo, powershell); others are "not installed"."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        tool = Path(cmd[0]).name.lower().replace(".exe", "").replace("-", "_")
        answer = self.answers.get(tool)
        if answer is None:
            return -1, "", "not found"
        return answer(cmd) if callable(answer) else answer


def amd_sysfs(root: Path, card="card1", device="0x15bf", vram=512 * MIB, render=128, gfx_version=110003):
    """A /sys tree with one amdgpu card and its KFD topology node."""
    dev = root / "class" / "drm" / card / "device"
    (dev / "drm" / f"renderD{render}").mkdir(parents=True)
    (dev / "vendor").write_text("0x1002\n")
    (dev / "device").write_text(f"{device}\n")
    (dev / "mem_info_vram_total").write_text(f"{vram}\n")
    (root / "class" / "drm" / f"{card}-DP-1").mkdir(parents=True)  # a connector, not a card
    node = root / "class" / "kfd" / "kfd" / "topology" / "nodes" / "1"
    node.mkdir(parents=True)
    (node / "properties").write_text(f"cpu_cores_count 0\nsimd_count 48\ngfx_target_version {gfx_version}\n"
                                     f"drm_render_minor {render}\n")
    return root


def hw(gpus=(), ram_gib=16.0, os_name="linux", arch="x86_64", vulkan=False, hip=None, free_gib=None, libc=None,
       libc_version=None):
    """A machine; Linux ones get glibc 2.39 unless the C library is given."""
    total = int(ram_gib * GIB)
    if os_name == "linux" and libc is None:
        libc, libc_version = "glibc", libc_version or "2.39"
    return HardwareInfo(os=os_name, arch=arch, ram_total=total, ram_available=int((free_gib or ram_gib * 0.8) * GIB),
                        gpus=list(gpus), vulkan=vulkan, hip_runtime=hip, libc=libc, libc_version=libc_version)


def nvidia(driver="580.95.05", cap=7.5, vram=11264, name="NVIDIA GeForce RTX 2080 Ti", free=None):
    return GpuInfo(vendor="nvidia", name=name, discrete=True, driver_version=driver, compute_cap=cap, vram_mib=vram,
                   vram_free_mib=free, source="nvidia-smi")


# --- catalog ---------------------------------------------------------------------------------------------------

class TestCatalog:

    def test_every_download_is_pinned_with_size_and_sha256(self):
        for asset in CATALOG.llama_cpp.assets:
            assert asset.pinned, asset.id
            for f in asset.files:
                assert f.size > 0 and len(f.sha256) == 64
        for model in CATALOG.models:
            assert model.pinned and len(model.revision) == 40, model.tier
            assert model.url(CATALOG.model_base_url).startswith(f"https://huggingface.co/{model.repo}/resolve/")

    def test_cuda_assets_carry_the_driver_rules(self):
        cuda = [a for a in CATALOG.llama_cpp.assets if a.backend == "cuda"]
        assert {a.cuda for a in cuda} >= {"12.8", "13.4", "12.4"}
        for a in cuda:
            assert a.min_driver and a.jit_min_driver and a.native_compute_caps and a.min_compute_cap
            assert version_tuple(a.jit_min_driver) > version_tuple(a.min_driver)
            # The CUDA runtime libraries ship as a second archive that goes next to llama-server.
            assert [f.libs for f in a.files] == [False, True], a.id
        assert CATALOG.model("default").pinned and CATALOG.tier_rules[-1].unconditional

    def test_a_q2_tier_exists_for_weak_hardware(self):
        q2 = [m for m in CATALOG.models if "Q2" in m.file]
        assert any(not m.on_request for m in q2)
        assert any(rule.tier in {m.tier for m in q2} and rule.max_ram_gib for rule in CATALOG.tier_rules)

    def test_versions_compare_part_by_part(self):
        assert version_tuple("570.124.06") > version_tuple("570.26")  # as floats it would be older
        assert version_tuple("580") < version_tuple("580.65.06") < version_tuple("615.20")
        assert version_tuple("[N/A]") is None and version_tuple(None) is None

    def _data(self):
        return yaml.safe_load(open(Path(__file__).resolve().parents[1] / "configs" / "llama_runtime.yml"))

    @pytest.mark.parametrize("breakage, message", [
        (lambda d: d["llama_cpp"]["assets"][2].__setitem__("min_driver", 570.60), "quoted strings"),
        (lambda d: d["tier_rules"].append({"tier": "small", "min_ram_gib": 1}), "last tier rule"),
        (lambda d: d["tier_rules"].insert(0, {"tier": "nope"}), "unknown tier"),
        (lambda d: d["llama_cpp"]["assets"][0]["files"][0].__setitem__("name", "llama.exe"), "files.0.name"),
        (lambda d: d["llama_cpp"]["assets"][0]["files"][0].__setitem__("name", "../x.tar.gz"), "files.0.name"),
        (lambda d: d["models"][0].__setitem__("file", "model.bin"), "file"),
        (lambda d: d["models"][0].__setitem__("revision", "main"), "revision"),
        (lambda d: d.__setitem__("model_base_url", "http://mirror.example/"), "https://"),
        (lambda d: d["llama_cpp"].__setitem__("base_url", "http://github.com/x/"), "https://"),
    ])
    def test_schema_refuses_unsafe_or_ambiguous_entries(self, breakage, message):
        data = self._data()
        breakage(data)
        with pytest.raises(ValueError, match=message):
            RuntimeCatalog.model_validate(data)

    def test_a_newer_model_is_data_only(self, tmp_path):
        data = self._data()
        data["models"].append({"tier": "next", "repo": "example/Next-GGUF", "revision": "a" * 40,
                               "file": "Next-14B-Q4_K_M.gguf", "size": 8 * GIB, "sha256": "b" * 64,
                               "native_context": 131072, "kv_bytes_per_token": 65536})
        data["tier_rules"].insert(0, {"tier": "next", "min_vram_gib": 20})
        path = tmp_path / "llama_runtime.yml"
        path.write_text(yaml.safe_dump(data))
        catalog = load_catalog(path)
        machine = hw([nvidia(driver="615.20", cap=8.9, vram=24564, name="NVIDIA GeForce RTX 4090")], ram_gib=64)
        model, why = choose_model_tier(machine, catalog, select_backend(machine, catalog))
        assert model.tier == "next" and "24.0 GiB GPU memory" in why


# --- hardware probes ---------------------------------------------------------------------------------------------

class TestProbes:

    def test_nvidia_smi_lines(self):
        out = "\n".join([nvidia_line(), nvidia_line("580.95.05", "8.6", 24576, "[N/A]", "NVIDIA GeForce RTX 3090"),
                         "", "garbage", "[N/A], 7.5, 1, 1, x"])
        gpus = parse_nvidia_smi(out)
        assert [g.name for g in gpus] == ["NVIDIA GeForce RTX 2080 Ti", "NVIDIA GeForce RTX 3090"]
        assert gpus[0].compute_cap == 7.5 and gpus[0].vram_mib == 11264 and gpus[0].vram_free_mib == 10850
        assert gpus[1].vram_free_mib is None and gpus[1].driver_version == "580.95.05"

    def test_old_driver_without_compute_cap_field(self):
        def smi(cmd):
            if "compute_cap" in cmd[1]:
                return 6, 'Field "compute_cap" is not a valid field to query.', ""
            return 0, "470.82.01, 11264, 11000, NVIDIA GeForce RTX 2080 Ti\n", ""
        gpus = probe_nvidia(FakeRunner(nvidia_smi=smi))
        assert gpus[0].compute_cap is None and gpus[0].driver_version == "470.82.01"
        choice = select_backend(hw(gpus), CATALOG)
        assert choice.backend == "cpu" and "570.26" in choice.reason  # too old for any CUDA build

    def test_nvidia_smi_outside_path_on_windows(self, monkeypatch):
        monkeypatch.setenv("ProgramW6432", r"C:\Program Files")
        def answer(cmd):
            return (0, nvidia_line("572.16") + "\n", "") if "NVSMI" in cmd[0] else (-1, "", "not found")
        runner = FakeRunner(nvidia_smi=answer)
        gpus = probe_nvidia(runner, "windows")
        assert gpus and gpus[0].driver_version == "572.16" and len(runner.calls) == 2

    def test_vulkaninfo_skips_software_renderers(self):
        gpus = parse_vulkaninfo_summary(VULKAN_780M)
        assert len(gpus) == 1
        assert gpus[0].vendor == "amd" and gpus[0].unified and gpus[0].discrete is False

    @pytest.mark.parametrize("vendor, name, discrete", [
        ("amd", "AMD Radeon(TM) Graphics", False),            # Ryzen Z1 Extreme / most Ryzen APUs
        ("amd", "AMD Radeon 780M Graphics", False),
        ("amd", "AMD Radeon(TM) RX Vega 11 Graphics", False),  # Ryzen 2400G
        ("amd", "AMD Radeon 8060S Graphics", False),           # Strix Halo
        ("amd", "AMD Radeon RX 7900 XTX", True),
        ("amd", "AMD Radeon RX 6600M", True),
        ("amd", "Radeon RX Vega", True),
        ("amd", "AMD Radeon PRO W7800", True),
        ("intel", "Intel(R) Arc(TM) A770 Graphics", True),
        ("intel", "Intel(R) UHD Graphics 770", False),
        ("intel", "Intel(R) Arc(TM) Graphics", False),
    ])
    def test_windows_adapter_names(self, vendor, name, discrete):
        assert guess_discrete(vendor, name) is discrete

    def test_linux_amd_apu_from_sysfs_without_vulkaninfo(self, tmp_path):
        root = amd_sysfs(tmp_path / "sys", vram=4 * GIB)  # a 4 GiB carve-out still counts as integrated
        info = detect_hardware(FakeRunner(), system="Linux", machine="x86_64", memory=(16 * GIB, 12 * GIB),
                               sysfs_root=root, rocm_globs=(),
                               find_library=lambda name: "libvulkan.so.1" if name == "vulkan" else None)
        gpu = info.gpus[0]
        assert gpu.unified and gpu.discrete is False and gpu.gfx_target == "gfx1103" and info.vulkan
        assert info.hip_runtime is False

    def test_linux_vulkaninfo_and_sysfs_are_merged(self, tmp_path):
        root = amd_sysfs(tmp_path / "sys", device="0x744c", vram=24 * GIB, gfx_version=110000)
        info = detect_hardware(FakeRunner(vulkaninfo=(0, VULKAN_7900XTX, "")), system="Linux", machine="x86_64",
                               memory=(64 * GIB, 50 * GIB), sysfs_root=root, find_library=lambda n: None,
                               rocm_globs=())
        gpu = info.gpus[0]
        assert gpu.discrete and not gpu.unified and gpu.vram_mib == 24 * 1024 and gpu.gfx_target == "gfx1100"

    def test_windows_adapters_with_registry_memory(self):
        amd = "Advanced Micro Devices, Inc."
        adapters = json.dumps([{"Name": "AMD Radeon RX 7900 XTX", "AdapterCompatibility": amd,
                                "DriverVersion": "32.0.12033.1030", "Memory": 25753026560},
                               {"Name": "Microsoft Basic Display Adapter", "AdapterCompatibility": "Microsoft"}])
        info = detect_hardware(FakeRunner(powershell=(0, adapters, "")), system="Windows", machine="AMD64",
                               memory=(32 * GIB, 24 * GIB))
        assert [g.name for g in info.gpus] == ["AMD Radeon RX 7900 XTX"]
        assert info.gpus[0].vram_mib == 25753026560 // MIB and info.vulkan and info.arch == "x86_64"

    def test_vulkaninfo_with_only_a_software_renderer_means_no_gpu_driver(self, tmp_path):
        # The loader is installed (vulkaninfo runs), but no GPU has a Vulkan driver: the Vulkan build would only find
        # llvmpipe, so the CPU build is the honest choice. Without vulkaninfo the loader still counts (next test).
        root = amd_sysfs(tmp_path, card="card0", device="0x744c", vram=24 * GIB, gfx_version=110000)
        info = detect_hardware(FakeRunner(vulkaninfo=(0, VULKAN_SOFTWARE_ONLY, "")), system="Linux",
                               machine="x86_64", memory=(32 * GIB, 24 * GIB), sysfs_root=root, rocm_globs=(),
                               find_library=lambda name: "libvulkan.so.1" if name == "vulkan" else None)
        assert not info.vulkan and info.gpus[0].vram_mib == 24 * 1024
        choice = select_backend(info, CATALOG)
        assert choice.backend == "cpu" and "no Vulkan driver" in choice.reason
        without = detect_hardware(FakeRunner(), system="Linux", machine="x86_64", memory=(32 * GIB, 24 * GIB),
                                  sysfs_root=root, rocm_globs=(),
                                  find_library=lambda name: "libvulkan.so.1" if name == "vulkan" else None)
        assert without.vulkan

    def test_windows_vulkaninfo_with_only_a_software_renderer(self):
        # The display adapter is there, but vulkaninfo lists only a software renderer: neither the adapter nor the
        # Vulkan loader (vulkan-1.dll) makes it a Vulkan GPU.
        adapters = json.dumps({"Name": "AMD Radeon RX 7900 XTX", "AdapterCompatibility": "Advanced Micro Devices, Inc.",
                               "DriverVersion": "32.0.12033.1030", "Memory": 25753026560})
        info = detect_hardware(FakeRunner(vulkaninfo=(0, VULKAN_SOFTWARE_ONLY, ""), powershell=(0, adapters, "")),
                               system="Windows", machine="AMD64", memory=(32 * GIB, 24 * GIB))
        assert [g.name for g in info.gpus] == ["AMD Radeon RX 7900 XTX"] and not info.vulkan

    def test_probes_that_fail_contribute_nothing(self, tmp_path):
        info = detect_hardware(FakeRunner(nvidia_smi=(9, "", "NVIDIA-SMI has failed"), vulkaninfo=(1, "", "")),
                               system="Linux", machine="aarch64", memory=(8 * GIB, 6 * GIB),
                               sysfs_root=tmp_path, find_library=lambda n: None, rocm_globs=())
        assert info.gpus == [] and not info.vulkan and info.arch == "arm64"


# --- build choice ---------------------------------------------------------------------------------------------

class TestBackend:

    @pytest.mark.parametrize("driver, expected, note", [
        ("560.35.03", None, "570.26"),     # 12.8 JIT-compiles Turing's code: needs the 12.8 driver
        ("570.124.06", "12.8", "615"),     # newer than 570.26 although the float 570.124 is smaller
        ("580.95.05", "12.8", "615"),      # minor version compatibility does not cover PTX: 13.4 needs R615
        ("615.20", "13.4", ""),
    ])
    def test_rtx_2080_ti_linux(self, driver, expected, note):
        choice = select_backend(hw([nvidia(driver)], vulkan=True), CATALOG)
        if expected:
            assert choice.backend == "cuda" and choice.asset.cuda == expected
        else:
            assert choice.backend == "vulkan" and "newer NVIDIA driver" in choice.reason
        assert note in choice.reason
        assert "NVIDIA NVIDIA" not in choice.reason

    def test_rtx_2080_ti_old_driver_without_vulkan_falls_back_to_cpu(self):
        choice = select_backend(hw([nvidia("550.54.14")]), CATALOG)
        assert choice.backend == "cpu" and "570.26" in choice.reason

    @pytest.mark.parametrize("gpu, driver, expected", [
        (("NVIDIA GeForce RTX 3090", 8.6), "580.65.06", "13.4"),   # native code: minor version compatibility
        (("NVIDIA GeForce GTX 1080", 6.1), "580.65.06", "12.8"),   # CUDA 13 dropped GPUs before Turing
        (("NVIDIA GeForce RTX 5090", 12.0), "575.51.03", "12.8"),  # 13.x needs >= 580 even with native code
        (("NVIDIA A100", 8.0), "590.10", "12.8"),                  # PTX only (80-virtual)
    ])
    def test_cuda_matrix(self, gpu, driver, expected):
        choice = select_backend(hw([nvidia(driver, cap=gpu[1], name=gpu[0], vram=24576)]), CATALOG)
        assert choice.backend == "cuda" and choice.asset.cuda == expected

    def test_windows_turing_uses_the_12_4_build(self):
        choice = select_backend(hw([nvidia("572.16")], os_name="windows"), CATALOG)
        assert choice.asset.id == "windows-x64-cuda-12.4"

    def test_every_nvidia_gpu_must_be_supported(self):
        gpus = [nvidia("580.65.06", 8.6, name="NVIDIA GeForce RTX 3090", vram=24576),
                nvidia("580.65.06", 6.1, name="NVIDIA GeForce GTX 1080", vram=8192)]
        choice = select_backend(hw(gpus), CATALOG)
        assert choice.asset.cuda == "12.8" and choice.gpu.name == "NVIDIA GeForce RTX 3090"

    def test_ryzen_z1_extreme_uses_vulkan_with_shared_memory(self, tmp_path, monkeypatch):
        # The probe asks the running C library for the glibc version (os.confstr); this simulated Linux machine has
        # glibc 2.39 whatever the test runs on (Windows has no confstr, Alpine has musl).
        monkeypatch.setattr(os, "confstr", lambda name: "glibc 2.39", raising=False)
        info = detect_hardware(FakeRunner(vulkaninfo=(0, VULKAN_780M, "")), system="Linux", machine="x86_64",
                               memory=(16 * GIB, 12 * GIB), sysfs_root=amd_sysfs(tmp_path / "sys", vram=4 * GIB),
                               find_library=lambda n: None, rocm_globs=())
        choice = select_backend(info, CATALOG)
        assert choice.backend == "vulkan" and choice.gpu.gfx_target == "gfx1103" and "shared RAM" in choice.reason
        assert usable_vram_gib(choice) is None  # the carve-out is RAM, not extra memory
        model, _ = choose_model_tier(info, CATALOG, choice)
        assert model.tier == "default"
        ctx, why = compute_context(model, info, choice, CATALOG.server)
        assert "free RAM" in why and ctx == CATALOG.server.ctx_cap

    def test_ryzen_z1_extreme_on_windows(self):
        amd = "Advanced Micro Devices, Inc."
        adapters = json.dumps({"Name": "AMD Radeon(TM) Graphics", "AdapterCompatibility": amd,
                               "DriverVersion": "32.0.12033.1030", "Memory": 4294967296})
        info = detect_hardware(FakeRunner(powershell=(0, adapters, "")), system="Windows", machine="AMD64",
                               memory=(16 * GIB, 11 * GIB))
        choice = select_backend(info, CATALOG)
        assert choice.asset.id == "windows-x64-vulkan" and choice.gpu.unified and choice.gpu.vram_mib is None

    def test_rocm_only_when_asked_and_supported(self, tmp_path):
        apu = GpuInfo(vendor="amd", name="AMD Radeon 780M", discrete=False, unified=True, gfx_target="gfx1103")
        assert select_backend(hw([apu], vulkan=True, hip=True), CATALOG).backend == "vulkan"
        rocm = select_backend(hw([apu], vulkan=True, hip=True), CATALOG, "rocm")
        assert rocm.asset.id == "linux-x64-rocm-10.0"
        no_hip = select_backend(hw([apu], vulkan=True, hip=False), CATALOG, "rocm")
        assert no_hip.asset is None and "HIP runtime" in no_hip.reason
        old = apu.model_copy(update={"gfx_target": "gfx906", "name": "AMD Radeon VII"})
        unsupported = select_backend(hw([old], vulkan=True, hip=True), CATALOG, "rocm")
        assert unsupported.asset is None and "gfx906" in unsupported.reason and "Vulkan" in unsupported.reason
        windows = select_backend(hw([apu.model_copy(update={"gfx_target": None})], os_name="windows"), CATALOG, "rocm")
        assert windows.asset.id == "windows-x64-rocm-10.0" and "unknown" in windows.reason
        assert select_backend(hw([], hip=True), CATALOG, "rocm").asset is None

    def test_weak_intel_graphics_stays_on_the_cpu(self):
        uhd = GpuInfo(vendor="intel", name="Intel(R) UHD Graphics 620", discrete=False, unified=True)
        choice = select_backend(hw([uhd], vulkan=True, os_name="windows"), CATALOG)
        assert choice.backend == "cpu" and "--backend vulkan" in choice.reason
        assert select_backend(hw([uhd], vulkan=True, os_name="windows"), CATALOG, "vulkan").backend == "vulkan"

    def test_apple_silicon(self):
        for ram, tier in ((16, "large"), (8, "default")):
            info = detect_hardware(FakeRunner(), system="Darwin", machine="arm64", memory=(ram * GIB, ram * GIB // 2))
            choice = select_backend(info, CATALOG)
            assert choice.backend == "metal"
            assert choose_model_tier(info, CATALOG, choice)[0].tier == tier

    def test_unknown_platform_and_missing_backend(self):
        assert "No llama.cpp build" in select_backend(hw(arch="riscv64"), CATALOG).reason
        assert "No 'metal' build" in select_backend(hw(), CATALOG, "metal").reason
        assert "CUDA requested" in select_backend(hw(), CATALOG, "cuda").reason


# --- model tier and context --------------------------------------------------------------------------------------

class TestTier:

    @pytest.mark.parametrize("ram, tier", [(64, "default"), (8, "default"), (4.6, "default"), (3.7, "small"),
                                           (2.9, "small-q2"), (1.9, "small-q2")])
    def test_cpu_only(self, ram, tier):
        machine = hw(ram_gib=ram)
        model, why = choose_model_tier(machine, CATALOG, select_backend(machine, CATALOG))
        assert model.tier == tier
        assert ("may not fit" in why) == (ram < 2)

    def test_gpu_with_8_gib_or_more_gets_the_large_model(self):
        machine = hw([nvidia("615.20")], ram_gib=32)
        choice = select_backend(machine, CATALOG)
        model, why = choose_model_tier(machine, CATALOG, choice)
        assert model.tier == "large" and "11.0 GiB GPU memory" in why

    def test_vram_counts_only_with_a_gpu_build(self):
        machine = hw([nvidia("615.20")], ram_gib=32)
        cpu = BackendChoice(asset=CATALOG.assets_for("linux", "x86_64")[0], backend="cpu", reason="test")
        assert choose_model_tier(machine, CATALOG, cpu)[0].tier == "default"

    def test_rules_skip_models_that_do_not_fit(self):
        data = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "configs" / "llama_runtime.yml"))
        data["tier_rules"] = [{"tier": "large"}, {"tier": "small"}]
        catalog = RuntimeCatalog.model_validate(data)
        model, why = choose_model_tier(hw(ram_gib=6), catalog, None)
        assert model.tier == "small" and "large needs" in why
        fits, need, usable = model_fits(catalog.model("large"), hw(ram_gib=6), None, catalog.server)
        assert not fits and need > usable

    def test_context_dedicated_partial_cpu_and_floor(self):
        model = CATALOG.model("large")
        machine = hw([nvidia("615.20", free=10000)], ram_gib=32, free_gib=20)
        choice = select_backend(machine, CATALOG)
        ctx, why = compute_context(model, machine, choice, CATALOG.server)
        assert ctx == 32768 and "free GPU memory" in why
        small_gpu = hw([nvidia("615.20", vram=4096, free=3500, name="NVIDIA GeForce GTX 1650")], ram_gib=16,
                       free_gib=12)
        ctx, why = compute_context(model, small_gpu, select_backend(small_gpu, CATALOG), CATALOG.server)
        assert "GPU +" in why  # partial offload: the rest of the layers stays in RAM
        ctx, why = compute_context(model, hw(ram_gib=8, free_gib=5.5), None, CATALOG.server)
        assert ctx == CATALOG.server.ctx_min and "minimum" in why
        ctx, _ = compute_context(model, hw(ram_gib=64, free_gib=60), None, CATALOG.server, cap=8000)
        assert ctx == 7936  # multiples of 256 below the cap


# --- downloads ---------------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, url="https://example.invalid/file", cut_after=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
        self.url, self.cut_after = url, cut_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        sent = 0
        for i in range(0, len(self.body), 4096):
            chunk = self.body[i:i + 4096]
            if self.cut_after is not None and sent + len(chunk) > self.cut_after:
                yield chunk[:self.cut_after - sent]
                raise requests.exceptions.ChunkedEncodingError("connection broken")
            sent += len(chunk)
            yield chunk


class FakeSession:
    """Serves files by URL with Range support. cuts: url -> [bytes before the connection breaks, ...]."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.calls = []
        self.cuts, self.statuses, self.final_url = {}, {}, {}
        self.ignore_range = False

    def get(self, url, headers=None, stream=False, timeout=None, allow_redirects=True):
        headers = headers or {}
        self.calls.append((url, headers.get("Range")))
        statuses = self.statuses.get(url)
        if statuses:
            return FakeResponse(statuses.pop(0), url=url)
        if url not in self.files:
            return FakeResponse(404, url=url)
        data = self.files[url]
        cut = self.cuts.get(url, []).pop(0) if self.cuts.get(url) else None
        final = self.final_url.get(url, url)
        rng = headers.get("Range")
        if rng and not self.ignore_range:
            start = int(rng.split("=")[1].rstrip("-"))
            return FakeResponse(206, data[start:], {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"},
                                final, cut)
        return FakeResponse(200, data, {"Content-Length": str(len(data))}, final, cut)


def self_signed_certificate(folder: Path):
    """A throwaway CA and a server certificate for 127.0.0.1 signed by it, as PEM files in `folder`: (the CA's
    certificate, which a client trusts; the server's certificate chain; the server's key). Both carry the extensions a
    strict verification (VERIFY_X509_STRICT) asks for."""
    import datetime
    import ipaddress
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.datetime.now(datetime.timezone.utc)

    def usage(**on):
        names = ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment",
                 "key_agreement", "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")
        return x509.KeyUsage(**{n: on.get(n, False) for n in names})

    def build(subject, issuer, public, signer, issuer_public, ca):
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(public)
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=5))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_public), critical=False))
        if ca:
            builder = builder.add_extension(usage(key_cert_sign=True, crl_sign=True), critical=True)
        else:
            builder = (builder.add_extension(usage(digital_signature=True), critical=True)
                       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                       .add_extension(x509.SubjectAlternativeName(
                           [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False))
        return builder.sign(signer, hashes.SHA256())

    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "llmcf test CA")])
    ca = build(ca_name, ca_name, ca_key.public_key(), ca_key, ca_key.public_key(), True)
    server = build(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]), ca_name, key.public_key(),
                   ca_key, ca_key.public_key(), False)
    pem = serialization.Encoding.PEM
    ca_file, chain_file, key_file = folder / "tls-ca.pem", folder / "tls-chain.pem", folder / "tls-key.pem"
    ca_file.write_bytes(ca.public_bytes(pem))
    chain_file.write_bytes(server.public_bytes(pem) + ca.public_bytes(pem))
    key_file.write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_file, chain_file, key_file


@contextlib.contextmanager
def tls_server(cert: Path, key: Path, body: bytes, drops: int = 0):
    """An HTTPS server on 127.0.0.1 that serves `body` at /blob; its first `drops` connections end during the TLS
    handshake (the ClientHello is read, then the connection is closed without an answer, as a flapping link or a proxy
    ends it). `connections` counts every connection."""
    import socket
    import ssl
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    listener.settimeout(0.1)   # a closed listener does not wake a waiting accept on every system: look again often
    stop = threading.Event()
    state = SimpleNamespace(url=f"https://127.0.0.1:{listener.getsockname()[1]}/blob", connections=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def serve():
        left = drops
        while not stop.is_set():
            try:
                raw, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            state.connections += 1
            raw.settimeout(10)
            try:
                if left > 0:
                    left -= 1
                    raw.recv(65536)
                    time.sleep(0.05)
                    raw.shutdown(socket.SHUT_WR)
                    time.sleep(0.2)
                    continue
                conn = context.wrap_socket(raw, server_side=True)
                Handler(conn, address, None)
                conn.close()
            except (OSError, ssl.SSLError):
                pass
            finally:
                raw.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        stop.set()
        thread.join(5)
        listener.close()


def runtime_catalog(model_bytes: bytes = b"", assets=None, models=None, **server) -> RuntimeCatalog:
    return RuntimeCatalog.model_validate({
        "llama_cpp": {"build": "b1", "base_url": "https://example.invalid/releases/", "assets": assets or [
            {"id": "linux-x64-cpu", "os": "linux", "arch": "x86_64", "backend": "cpu",
             "files": [{"name": "llama-b1-cpu.tar.gz", "size": 1, "sha256": "0" * 64}]}],
            "features": {"sleep_idle_seconds": True}},
        "model_base_url": "https://models.invalid/",
        "server": {"health_timeout_seconds": 20, "sleep_idle_seconds": 600, **server},
        "models": models or [{"tier": "tiny", "repo": "example/Tiny-GGUF", "revision": "c" * 40,
                              "file": "tiny.gguf", "size": len(model_bytes) or 1,
                              "sha256": sha(model_bytes) if model_bytes else "1" * 64,
                              "native_context": 8192, "kv_bytes_per_token": 1024}],
        "tier_rules": [{"tier": (models or [{"tier": "tiny"}])[0]["tier"]}],
    })


CPU_HW = hw(ram_gib=16)


class TestDownloads:

    URL = "https://example.invalid/releases/blob.bin"

    def fetch(self, tmp_path, session, data, **kw):
        sleeps = []
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                             catalog=runtime_catalog(), hardware=CPU_HW, session=session, sleep=sleeps.append)
        dest = tmp_path / "dl" / "blob.bin"
        size = kw.pop("size", len(data))
        digest = kw.pop("digest", sha(data))
        return rt, rt._fetch(self.URL, dest, size, digest, **kw), sleeps

    def test_complete_download_gets_its_name_after_verification(self, tmp_path):
        data = os.urandom(10000)
        progress = []
        _, path, _ = self.fetch(tmp_path, FakeSession({self.URL: data}), data,
                                progress=lambda n, done, total: progress.append(done))
        assert path.read_bytes() == data and not path.with_name("blob.bin.part").exists()
        assert progress[-1] == len(data)

    def test_interrupted_download_resumes_with_range(self, tmp_path):
        data = os.urandom(20000)
        session = FakeSession({self.URL: data})
        session.cuts[self.URL] = [6000]
        _, path, sleeps = self.fetch(tmp_path, session, data)
        assert path.read_bytes() == data
        assert session.calls == [(self.URL, None), (self.URL, "bytes=6000-")] and len(sleeps) == 1

    def test_partial_file_from_an_earlier_run_is_continued(self, tmp_path):
        data = os.urandom(9000)
        part = tmp_path / "dl" / "blob.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(data[:4000])
        session = FakeSession({self.URL: data})
        self.fetch(tmp_path, session, data)
        assert session.calls == [(self.URL, "bytes=4000-")]

    def test_server_ignoring_range_restarts_cleanly(self, tmp_path):
        data = os.urandom(9000)
        part = tmp_path / "dl" / "blob.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(b"x" * 4000)
        session = FakeSession({self.URL: data})
        session.ignore_range = True
        _, path, _ = self.fetch(tmp_path, session, data)
        assert path.read_bytes() == data

    def test_hash_mismatch_is_deleted_and_refused(self, tmp_path):
        data = os.urandom(5000)
        with pytest.raises(IntegrityError, match="does not match its pin"):
            self.fetch(tmp_path, FakeSession({self.URL: data}), data, digest=sha(b"other"))
        assert not list((tmp_path / "dl").iterdir())

    def test_wrong_size_on_the_server_is_refused_before_writing(self, tmp_path):
        data = os.urandom(5000)
        with pytest.raises(IntegrityError, match="5000 bytes on the server"):
            self.fetch(tmp_path, FakeSession({self.URL: data}), data, size=4000)
        assert not (tmp_path / "dl" / "blob.bin.part").exists()

    @pytest.mark.parametrize("size, digest", [(None, "a" * 64), (10, None), (0, "a" * 64)])
    def test_unpinned_downloads_never_touch_the_network(self, tmp_path, size, digest):
        session = FakeSession()
        with pytest.raises(IntegrityError, match="pinned"):
            self.fetch(tmp_path, session, b"", size=size, digest=digest)
        assert session.calls == []

    def test_redirect_away_from_https_is_refused(self, tmp_path):
        data = b"abc"
        session = FakeSession({self.URL: data})
        session.final_url[self.URL] = "http://mirror.invalid/blob.bin"
        with pytest.raises(LocalRuntimeError, match="away from HTTPS"):
            self.fetch(tmp_path, session, data)

    def test_server_errors_are_retried_client_errors_are_not(self, tmp_path):
        data = os.urandom(3000)
        session = FakeSession({self.URL: data})
        session.statuses[self.URL] = [503, 429]
        _, path, sleeps = self.fetch(tmp_path, session, data)
        assert path.read_bytes() == data and len(sleeps) == 2
        session = FakeSession()
        with pytest.raises(LocalRuntimeError, match="HTTP 404"):
            self.fetch(tmp_path / "b", session, data)
        assert len(session.calls) == 1

    def test_gives_up_after_the_attempts_and_keeps_the_part_file(self, tmp_path):
        data = os.urandom(50000)
        session = FakeSession({self.URL: data})
        session.cuts[self.URL] = [1000] * 5
        with pytest.raises(LocalRuntimeError, match="run the command again to resume"):
            self.fetch(tmp_path, session, data)
        assert (tmp_path / "dl" / "blob.bin.part").stat().st_size == 5000

    def test_an_already_verified_file_is_reused(self, tmp_path):
        data = os.urandom(3000)
        dest = tmp_path / "dl" / "blob.bin"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(data)
        session = FakeSession()
        self.fetch(tmp_path, session, data)
        assert session.calls == []

    def test_tls_failures_are_not_retried_and_name_only_the_host(self, tmp_path):
        # A certificate problem does not go away on its own (a TLS-inspecting proxy needs its CA): it used to be
        # retried five times with backoff and to end in "did not complete after 5 attempts".
        session = FakeSession()

        def get(url, **kw):
            session.calls.append(url)
            raise requests.exceptions.SSLError(f"certificate verify failed for {url}?X-Amz-Signature=secret")
        session.get = get
        with pytest.raises(LocalRuntimeError, match="TLS certificate of example.invalid") as err:
            self.fetch(tmp_path, session, b"abc")
        assert len(session.calls) == 1 and "Signature" not in str(err.value) and "REQUESTS_CA_BUNDLE" in str(err.value)

    def test_a_tls_failure_after_a_redirect_names_the_host_that_failed(self, tmp_path):
        # Review of port-runtime: release archives and models are redirected to a CDN host. A proxy that inspects only
        # some hosts fails the CDN's certificate; the message named the first host, whose certificate was fine.
        session = FakeSession()
        cdn = "https://objects.cdn.invalid/blob.bin?X-Amz-Signature=secret"

        def get(url, **kw):
            session.calls.append(url)
            raise requests.exceptions.SSLError("certificate verify failed",
                                               request=requests.Request("GET", cdn).prepare())
        session.get = get
        with pytest.raises(LocalRuntimeError, match="TLS certificate of objects.cdn.invalid could not") as err:
            self.fetch(tmp_path, session, b"abc")
        assert "Signature" not in str(err.value) and "example.invalid" not in str(err.value)

    def test_a_tls_connection_that_ends_during_its_handshake_is_retried_against_a_real_server(self, tmp_path):
        """Review of stage C wave 3: every TLS error was taken for a certificate that cannot be verified. A connection
        that a flapping link or a proxy ends during its TLS handshake (requests raises SSLError(SSLEOFError) for it)
        ended the download at once, with the certificate text and the advice to set REQUESTS_CA_BUNDLE, although the
        certificate is fine. It is a dropped connection and is retried; a certificate the client does not trust is
        still refused at once, by the host's name only."""
        data = os.urandom(300000)
        ca, cert, key = self_signed_certificate(tmp_path)
        with tls_server(cert, key, data, drops=1) as server:
            session = requests.Session()
            session.trust_env, session.verify = False, str(ca)
            rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                                 catalog=runtime_catalog(), hardware=CPU_HW, session=session, sleep=lambda s: None)
            path = rt._fetch(server.url, tmp_path / "dl" / "blob.bin", len(data), sha(data))
            assert path.read_bytes() == data and server.connections == 2
        with tls_server(cert, key, data, drops=0) as server:
            session = requests.Session()
            session.trust_env = False   # the system's CAs: they do not know this certificate
            rt = LocalLLMRuntime(data_dir=tmp_path / "state2", models_dir=tmp_path / "models",
                                 catalog=runtime_catalog(), hardware=CPU_HW, session=session, sleep=lambda s: None)
            with pytest.raises(LocalRuntimeError, match="TLS certificate of 127.0.0.1 could not be verified"):
                rt._fetch(server.url, tmp_path / "dl2" / "blob.bin", len(data), sha(data))
            assert server.connections == 1

    def test_a_tls_record_that_breaks_mid_way_resumes(self, tmp_path):
        import ssl
        import urllib3
        data = os.urandom(20000)
        session = FakeSession({self.URL: data})
        get = session.get

        def broken_once(url, headers=None, **kw):
            response = get(url, headers=headers, **kw)
            if len(session.calls) == 1:
                body = response.body

                def iter_content(chunk_size=1):
                    yield body[:6000]
                    raise requests.exceptions.SSLError(urllib3.exceptions.SSLError(ssl.SSLError(
                        1, "[SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC] decryption failed or bad record mac")))
                response.iter_content = iter_content
            return response
        session.get = broken_once
        _, path, sleeps = self.fetch(tmp_path, session, data)
        assert path.read_bytes() == data
        assert session.calls == [(self.URL, None), (self.URL, "bytes=6000-")] and len(sleeps) == 1

    @pytest.mark.parametrize("error", [requests.exceptions.TooManyRedirects("30 redirects"),
                                       requests.exceptions.InvalidURL("bad")])
    def test_other_request_errors_end_with_a_clear_message(self, tmp_path, error):
        session = FakeSession()
        session.get = lambda url, **kw: (_ for _ in ()).throw(error)
        with pytest.raises(LocalRuntimeError, match=f"failed \\({type(error).__name__}\\)"):
            self.fetch(tmp_path, session, b"abc")

    def test_a_part_file_that_cannot_be_written(self, tmp_path):
        data = os.urandom(100000)
        (tmp_path / "dl" / "blob.bin.part").mkdir(parents=True)  # stands in for a full or read-only disk
        with pytest.raises(LocalRuntimeError, match="Could not write .*resumes on the next run"):
            self.fetch(tmp_path, FakeSession({self.URL: data}), data)

    def test_not_enough_disk_space(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 * MIB})())
        with pytest.raises(LocalRuntimeError, match="Not enough disk space for the download: "):
            self.fetch(tmp_path, FakeSession(), b"x", size=GIB)


# --- install -----------------------------------------------------------------------------------------------------

def make_tar(path: Path, files: dict, root: str, symlinks: dict = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        folder = tarfile.TarInfo(root)
        folder.type = tarfile.DIRTYPE
        tf.addfile(folder)
        for name, data in files.items():
            info = tarfile.TarInfo(f"{root}/{name}" if root else name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def make_zip(path: Path, files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


class TestInstall:

    BASE = "https://example.invalid/releases/"

    def setup_runtime(self, tmp_path, main_files=None, lib_files=None, os_name="linux", symlinks=None):
        """A CUDA-like asset: the build archive plus a CUDA runtime archive in its own folder (as on Linux)."""
        tmp = tmp_path / "archives"
        tmp.mkdir(exist_ok=True)
        if os_name == "windows":
            main = make_zip(tmp / "main.zip", main_files or {"llama-server.exe": b"MZ", "ggml-cuda.dll": b"dll"})
            libs = make_zip(tmp / "libs.zip", lib_files or {"cudart64_13.dll": b"rt", "cublas64_13.dll": b"bl"})
            names = ("llama-b1-bin-win-cuda-13.4-x64.zip", "cudart-llama-bin-win-cuda-13.4-x64.zip")
        else:
            main = make_tar(tmp / "main.tar.gz", main_files or {
                "llama-server": b"#!/bin/sh\n", "libggml-cuda.so": b"\x7fELF", "libllama.so.0.6.0": b"\x7fELF",
                "LICENSE": b"MIT"}, "llama-b1",
                symlinks if symlinks is not None else {"libllama.so": "libllama.so.0.6.0"})
            libs = make_tar(tmp / "libs.tar.gz", lib_files or {"libcudart.so.13": b"rt", "libcublas.so.13": b"bl"},
                            "cudart-llama-b1-bin-ubuntu-cuda-13.4-x64")
            names = ("llama-b1-bin-ubuntu-cuda-13.4-x64.tar.gz", "cudart-llama-b1-bin-ubuntu-cuda-13.4-x64.tar.gz")
        asset = {"id": f"{os_name}-x64-cuda-13.4", "os": os_name, "arch": "x86_64", "backend": "cuda", "cuda": "13.4",
                 "min_driver": "580", "jit_min_driver": "615", "native_compute_caps": [8.6], "min_compute_cap": 7.5,
                 "files": [{"name": names[0], "size": len(main), "sha256": sha(main)},
                           {"name": names[1], "size": len(libs), "sha256": sha(libs), "libs": True}]}
        cpu = {"id": f"{os_name}-x64-cpu", "os": os_name, "arch": "x86_64", "backend": "cpu",
               "files": [{"name": names[0], "size": len(main), "sha256": sha(main)}]}
        session = FakeSession({self.BASE + names[0]: main, self.BASE + names[1]: libs})
        machine = hw([nvidia("615.20", cap=8.6, name="NVIDIA GeForce RTX 3090")], os_name=os_name)
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                             catalog=runtime_catalog(assets=[asset, cpu]), hardware=machine, session=session,
                             sleep=lambda s: None)
        return rt, session

    def test_linux_cuda_install_merges_the_runtime_libraries(self, tmp_path):
        rt, session = self.setup_runtime(tmp_path)
        inst = rt.install_prebuilt()
        assert inst.variant == "cuda-13.4" and inst.server == rt.root / "b1-cuda-13.4" / "llama-b1" / "llama-server"
        bin_dir = inst.server.parent
        assert (bin_dir / "libcudart.so.13").read_bytes() == b"rt" and (bin_dir / "libcublas.so.13").exists()
        assert (bin_dir / "libllama.so").is_symlink()
        assert not list(inst.path.glob(".libs-*")) and not list((rt.root / "downloads").iterdir())
        if os.name == "posix":
            assert os.stat(inst.server).st_mode & stat.S_IXUSR and os.stat(bin_dir / "libcudart.so.13").st_mode & 0o100
            assert not os.stat(bin_dir / "LICENSE").st_mode & stat.S_IXUSR
        assert rt.installed() == inst
        calls = len(session.calls)
        assert rt.install_prebuilt() == inst and len(session.calls) == calls  # already installed: nothing downloaded
        rt.install_prebuilt(force=True)
        assert len(session.calls) == calls + 2

    def test_windows_zips_unpack_into_one_folder(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, os_name="windows")
        inst = rt.install_prebuilt()
        assert inst.server.name == "llama-server.exe" and (inst.server.parent / "cudart64_13.dll").exists()

    def test_a_failed_reinstall_keeps_the_previous_installation(self, tmp_path):
        rt, session = self.setup_runtime(tmp_path)
        inst = rt.install_prebuilt()
        for url in session.files:
            session.files[url] = b"tampered" + session.files[url][8:]
        with pytest.raises(IntegrityError):
            rt.install_prebuilt(force=True)
        assert rt.installed() == inst and inst.server.is_file()
        assert not list(rt.root.glob(".staging-*")) and not list(rt.root.glob(".old-*"))

    def test_archive_escaping_its_folder_is_refused(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, symlinks={"evil": "../../../../outside"})
        with pytest.raises(IntegrityError, match="Unsafe or broken archive"):
            rt.install_prebuilt()
        assert rt.installed() is None and not (rt.root / "b1-cuda-13.4").exists()

    def test_runtime_libraries_may_not_replace_build_files(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, lib_files={"libggml-cuda.so": b"other"})
        with pytest.raises(IntegrityError, match="would replace"):
            rt.install_prebuilt()

    def test_missing_server_binary(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, main_files={"llama-cli": b"x"}, symlinks={})
        with pytest.raises(LocalRuntimeError, match="llama-server not found"):
            rt.install_prebuilt()

    def test_unpinned_asset_is_refused_before_any_download(self, tmp_path):
        asset = {"id": "linux-x64-cpu", "os": "linux", "arch": "x86_64", "backend": "cpu",
                 "files": [{"name": "llama-b1-cpu.tar.gz", "size": 10}]}
        session = FakeSession()
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", catalog=runtime_catalog(assets=[asset]), hardware=CPU_HW,
                             session=session, models_dir=tmp_path / "m")
        with pytest.raises(IntegrityError, match="refusing to download"):
            rt.install_prebuilt()
        assert session.calls == []

    def test_old_installations_are_kept_and_listed_other_variants_stay(self, tmp_path):
        """An install deletes no other installation (round 5 f): one of another llama.cpp version is only listed for
        the person (`ditto runtime prune`); another variant of this version is not even listed."""
        rt, _ = self.setup_runtime(tmp_path)
        (rt.root / "b0-cpu").mkdir(parents=True)
        (rt.root / "b1-vulkan").mkdir(parents=True)
        rt.install_prebuilt()
        assert (rt.root / "b0-cpu").exists() and (rt.root / "b1-vulkan").exists()
        assert [i.full_path for i in rt.cleanup_plan().items] == [str(rt.root / "b0-cpu")]

    def test_refuses_to_replace_the_installation_the_server_runs_from(self, tmp_path, monkeypatch):
        rt, session = self.setup_runtime(tmp_path)
        inst = rt.install_prebuilt()

        class Running:
            pid = 1
            def cmdline(self):
                return [str(inst.server)]
            def exe(self):
                return str(inst.server)
        monkeypatch.setattr(ls, "owned_process", lambda state: Running())
        calls = len(session.calls)
        with pytest.raises(LocalRuntimeError, match="stop it first"):
            rt.install_prebuilt(force=True)
        assert len(session.calls) == calls  # refused before downloading, not after

    def test_downloads_do_not_hold_the_runtime_lock(self, tmp_path):
        # A CUDA archive is hundreds of MB; holding the lock meanwhile made `runtime stop`, `runtime start` and the exit
        # handler of a GUI with a tied server wait for the whole download (up to the lock's 15 minutes).
        rt, session = self.setup_runtime(tmp_path)
        free = []
        get = session.get

        def checking_get(url, **kw):
            with ls._FileLock(rt.lock_file, timeout=0.3, sleep=lambda s: time.sleep(0.05)):
                free.append(url)
            return get(url, **kw)
        session.get = checking_get
        rt.install_prebuilt()
        assert len(free) == 2 and rt.installed()

    def test_the_leftovers_of_an_interrupted_install_are_removed_before_the_space_check(self, tmp_path, monkeypatch):
        # Review of port-runtime: the unpack space check moved before the downloads, out of the lock, and so before
        # the leftovers of an interrupted install (.staging-*, .old-*) were removed; on a disk they filled, the install
        # was refused although removing them (as it did before) made room.
        rt, session = self.setup_runtime(tmp_path)
        leftover = rt.root / ".staging-cuda-13.4-dead"
        leftover.mkdir(parents=True)
        (leftover / "llama-server").write_bytes(b"partial")

        def usage(path):
            full = any(rt.root.glob(".staging-*"))
            return SimpleNamespace(total=2 * 1024 * GIB, used=GIB, free=0 if full else 1024 * GIB)
        monkeypatch.setattr(ls.shutil, "disk_usage", usage)
        inst = rt.install_prebuilt()
        assert inst == rt.installed() and not leftover.exists()

    @pytest.mark.parametrize("force", [False, True])
    def test_an_install_finished_meanwhile_by_another_process(self, tmp_path, force):
        rt, session = self.setup_runtime(tmp_path)
        other = LocalLLMRuntime(data_dir=rt.data_dir, models_dir=rt.models_dir, catalog=rt.catalog,
                                hardware=rt.hardware(), session=session, sleep=lambda s: None)
        lock = rt._lock

        def other_installs_first():
            other.install_prebuilt(force=force)  # it reuses our verified archives and deletes them when done
            return lock()
        rt._lock = other_installs_first
        calls_before = len(session.calls)
        inst = rt.install_prebuilt(force=force)
        assert inst == rt.installed() and inst.server.is_file()
        downloads = len(session.calls) - calls_before
        # Without force the other installation is taken as it is; with force the removed archives are fetched again
        # instead of failing as a "broken archive".
        assert downloads == (4 if force else 2)
        assert not list((rt.root / "downloads").glob("*.tar.gz"))

    def test_no_build_for_this_machine(self, tmp_path):
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", catalog=runtime_catalog(),
                             hardware=hw(arch="riscv64"))
        with pytest.raises(LocalRuntimeError, match="No llama.cpp build"):
            rt.install_prebuilt()


# --- models -----------------------------------------------------------------------------------------------------

class TestModels:

    def runtime(self, tmp_path, data, session=None):
        return LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                               catalog=runtime_catalog(data), hardware=CPU_HW, session=session, sleep=lambda s: None)

    def test_download_at_the_pinned_revision(self, tmp_path):
        data = b"GGUF" + os.urandom(5000)
        url = f"https://models.invalid/example/Tiny-GGUF/resolve/{'c' * 40}/tiny.gguf"
        session = FakeSession({url: data})
        rt = self.runtime(tmp_path, data, session)
        model = rt.catalog.model("tiny")
        assert rt.model_state(model) == "missing"
        path = rt.download_model("tiny")
        assert path.read_bytes() == data and rt.model_ready(model) and rt.model_state(model) == "ready"
        assert session.calls == [(url, None)]
        assert rt.download_model("tiny") == path and len(session.calls) == 1

    def test_changed_model_file_is_verified_again_and_refused(self, tmp_path):
        data = b"GGUF" + os.urandom(5000)
        rt = self.runtime(tmp_path, data)
        model = rt.catalog.model("tiny")
        path = rt.model_path(model)
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        assert rt.model_state(model) == "unverified"
        assert rt._ensure_model_verified(model) == path and rt.model_ready(model)
        before = path.stat().st_mtime_ns
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))  # same size, different content (always)
        os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))  # a new mtime even on coarse clocks
        assert not rt.model_ready(model)
        with pytest.raises(IntegrityError):
            rt._ensure_model_verified(model)
        assert not path.exists()
        with pytest.raises(LocalRuntimeError, match="models download tiny"):
            rt._ensure_model_verified(model)

    def test_unpinned_model_is_refused(self, tmp_path):
        models = [{"tier": "loose", "repo": "example/Loose-GGUF", "revision": "d" * 40, "file": "loose.gguf"}]
        session = FakeSession()
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", hardware=CPU_HW, session=session,
                             catalog=runtime_catalog(models=models))
        with pytest.raises(IntegrityError, match="refusing to download"):
            rt.download_model("loose")
        with pytest.raises(LocalRuntimeError, match="Unknown model tier"):
            rt.download_model("nope")
        assert session.calls == []


# --- command line and environment ---------------------------------------------------------------------------------

class TestServerCommand:

    def test_loopback_key_file_and_no_dangerous_options(self, tmp_path):
        cmd = build_server_command(Path("/opt/llama-server"), Path("/m/x.gguf"), 41234, tmp_path / "server-a.key",
                                   8192, "x", gpu=True, cache_ram_mib=2048, sleep_idle_seconds=600)
        assert cmd[cmd.index("--host") + 1] == "127.0.0.1" and cmd[cmd.index("--port") + 1] == "41234"
        assert cmd[cmd.index("--api-key-file") + 1] == str(tmp_path / "server-a.key")
        assert {"--no-ui", "--no-slots", "--offline", "--jinja"} <= set(cmd)
        assert "--api-key" not in cmd and cmd[cmd.index("-ngl") + 1] == "auto"
        assert cmd[cmd.index("--sleep-idle-seconds") + 1] == "600"
        cpu = build_server_command(Path("s"), Path("m"), 1, Path("k"), 4096, "x", gpu=False, cache_ram_mib=256)
        assert "-ngl" not in cpu and "--sleep-idle-seconds" not in cpu

    # --reuse-port (SO_REUSEPORT) would let another process of this user bind the server's port beside it.
    @pytest.mark.parametrize("alias", ["--tools", "--api-key", "--agent", "--rpc", "--webui", "-hfr", "--hf-token",
                                       "-mmu", "--log-prompts-dir", "--reuse-port", "--reuse_port"])
    def test_forbidden_options_never_reach_the_server(self, alias):
        with pytest.raises(LocalRuntimeError, match="Refusing"):
            build_server_command(Path("s"), Path("m"), 1, Path("k"), 4096, alias, gpu=False, cache_ram_mib=256)

    def test_environment_has_no_secrets_and_no_llama_options(self):
        env = server_env({"PATH": "/usr/bin", "HOME": "/home/u", "CUDA_VISIBLE_DEVICES": "0",
                          "LLAMA_ARG_TOOLS": "all", "LLAMA_API_KEY": "x", "OPENAI_API_KEY": "sk-fake",
                          "HF_TOKEN": "hf_fake", "AWS_SECRET_ACCESS_KEY": "x", "GGML_VK_VISIBLE_DEVICES": "0",
                          "GGML_BACKEND_PATH": "/tmp/extra-backend.so"})
        assert {k: v for k, v in env.items() if k not in ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP")} == {
            # HOME as a path of this computer: on Windows '/home/u' is the folder '\\home\\u' of the current drive
            "PATH": "/usr/bin:/bin" if os.name != "nt" else "", "HOME": str(Path("/home/u")),
            "LANG": "C", "LC_ALL": "C",
            "CUDA_VISIBLE_DEVICES": "0", "GGML_VK_VISIBLE_DEVICES": "0",
            **({"USERPROFILE": str(Path("/home/u"))} if os.name == "nt" else {})}

    def test_process_group_options(self):
        assert popen_options("posix") == {"start_new_session": True}
        flags = popen_options("nt")["creationflags"]
        assert flags & 0x08000000 and flags & 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


# --- the server process (with a stand-in for llama-server) --------------------------------------------------------

FAKE_SERVER = r'''
import json, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

here = Path(__file__).resolve().parent
args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1] if name in args else None
behavior = json.loads((here / "behavior.json").read_text())
mode = behavior.get("mode", "ok")
starts = int((here / "starts").read_text()) + 1 if (here / "starts").exists() else 1
(here / "starts").write_text(str(starts))
(here / f"argv-{starts}.json").write_text(json.dumps(args))
(here / f"env-{starts}.json").write_text(json.dumps(sorted(os.environ)))
print("fake llama-server starting", flush=True)
if mode == "exit":
    print("error: failed to load model 'tiny.gguf'", flush=True)
    sys.exit(1)
if mode == "bind_once" and starts == 1:
    print(f"couldn't bind HTTP server socket, hostname: 127.0.0.1, port: {opt('--port')}", flush=True)
    sys.exit(1)
if mode == "child":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
keys = [line.strip() for line in open(opt("--api-key-file")) if line.strip() and not line.startswith("#")]
alias = opt("--alias")

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if mode == "noauth":
            return True
        return self.headers.get("Authorization", "").removeprefix("Bearer ") in keys

    def do_GET(self):
        if self.path == "/health":
            return self.reply(503 if mode == "slow" else 200, {"status": "ok"})
        if not self.authorized():
            return self.reply(401, {"error": {"message": "Invalid API Key", "code": 401}})
        if self.path == "/v1/models":
            return self.reply(200, {"object": "list", "data": [{"id": alias, "object": "model"}]})
        self.reply(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.authorized():
            return self.reply(401, {"error": {"message": "Invalid API Key", "code": 401}})
        (here / "last_request.json").write_text(json.dumps(body))
        self.reply(200, {"id": "c1", "object": "chat.completion", "created": 0, "model": body.get("model"),
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": f"pong from {alias}"}}],
                         "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})

    def log_message(self, fmt, *a):
        print(fmt % a, flush=True)

time.sleep(behavior.get("bind_delay", 0))  # llama-server loads its GPU backends before it binds: seconds on some PCs
try:
    httpd = ThreadingHTTPServer((opt("--host"), int(opt("--port"))), Handler)
except OSError:  # the port is taken: llama-server says so and exits
    print(f"couldn't bind HTTP server socket, hostname: {opt('--host')}, port: {opt('--port')}", flush=True)
    sys.exit(1)
httpd.serve_forever()
'''


@pytest.fixture
def fake_runtime(tmp_path):
    """A runtime whose installed "llama-server" is the stand-in above (an executable script)."""
    made = []

    def factory(mode="ok", **server):
        model_bytes = b"GGUF" + os.urandom(4096)
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                             catalog=runtime_catalog(model_bytes, **server), hardware=CPU_HW)
        folder = rt.root / "b1-cpu"
        folder.mkdir(parents=True, exist_ok=True)
        python = sys.executable if len(sys.executable) < 120 else "/usr/bin/env python3"
        server_path = folder / "llama-server"
        server_path.write_text(f"#!{python}\n{FAKE_SERVER}")
        server_path.chmod(0o755)
        (folder / "behavior.json").write_text(json.dumps({"mode": mode}))
        (folder / "runtime.json").write_text(json.dumps({"build": "b1", "asset_id": "linux-x64-cpu",
                                                         "variant": "cpu", "backend": "cpu",
                                                         "server": "llama-server", "files": {}}))
        # As `ditto runtime install --prebuilt` leaves it: the archive is used only after that opt-in (D1).
        rt._update_active(variant="cpu", asset_id="linux-x64-cpu", use="prebuilt")
        rt.models_dir.mkdir(parents=True, exist_ok=True)
        rt.model_path(rt.catalog.model("tiny")).write_bytes(model_bytes)
        made.append(rt)
        return rt

    yield factory
    for rt in made:
        try:
            rt.stop(timeout=5)
        except LocalRuntimeError:
            pass


class FakeConfig:
    def __init__(self, **values):
        self.values = {"ai_security_level": "STRICT", **values}
        self.config_dir = None

    def get(self, key, default=None):
        return self.values.get(key, default)


@contextlib.contextmanager
def impostor_on_the_first_port(monkeypatch):
    """Another program that took the port the start chooses first: it answers /health, accepts any key and records
    every Authorization header it gets. The start's next choice is a really free port. Yields (its port, headers)."""
    seen = []

    class Impostor(BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Authorization", "")
            seen.append(auth)
            body = b'{"data": [{"id": "tiny"}]}'
            self.send_response(200 if self.path == "/health" or auth else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    impostor = ThreadingHTTPServer(("127.0.0.1", 0), Impostor)
    threading.Thread(target=impostor.serve_forever, daemon=True).start()
    ports = [impostor.server_address[1]]
    free_port = ls._free_port
    monkeypatch.setattr(ls, "_free_port", lambda: ports.pop() if ports else free_port())
    try:
        yield impostor.server_address[1], seen
    finally:
        impostor.shutdown()
        impostor.server_close()


@POSIX_ONLY
class TestServerProcess:

    def test_start_serve_and_stop(self, fake_runtime, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake0123456789abcdefghijk")
        monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")
        monkeypatch.setenv("GGML_BACKEND_PATH", "/nonexistent/extra-backend.so")
        monkeypatch.setenv("AIP_MODE", "PREDICTION")
        rt = fake_runtime()
        ep = rt.start()
        folder = rt.root / "b1-cpu"
        argv = json.loads((folder / "argv-1.json").read_text())
        assert ep.base_url == f"http://127.0.0.1:{ep.port}/v1" and ep.model == "tiny"
        assert argv[argv.index("--host") + 1] == "127.0.0.1" and argv[argv.index("--port") + 1] == str(ep.port)
        key_file = Path(argv[argv.index("--api-key-file") + 1])
        assert key_file.parent == rt.root and stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert ep.api_key not in " ".join(psutil.Process(ep.pid).cmdline())
        assert ep.api_key not in repr(ep)
        env = json.loads((folder / "env-1.json").read_text())
        assert "OPENAI_API_KEY" not in env and "LLAMA_ARG_TOOLS" not in env and "PATH" in env
        assert "GGML_BACKEND_PATH" not in env and "AIP_MODE" not in env, "a cleared environment (decision D22)"
        assert os.getpgid(ep.pid) == ep.pid  # its own process group
        status = rt.status()
        assert status.running and status.healthy and status.tier == "tiny" and status.ctx >= 4096
        assert read_endpoint(rt.root) == ep
        # Anonymous API calls are refused; the key works.
        assert requests.get(f"{ep.base_url}/models", timeout=5).status_code == 401
        log = rt.log_file.read_text()
        assert "fake llama-server starting" in log and ep.api_key not in log
        assert stat.S_IMODE(rt.log_file.stat().st_mode) == 0o600

        assert rt.stop() == 1
        assert gone(ep.pid)
        assert not rt.state_file.exists() and not key_file.exists() and read_endpoint(rt.root) is None
        assert not rt.status().running and rt.stop() == 0

    def test_llm_client_uses_the_running_server(self, fake_runtime, monkeypatch):
        from orchestrator.llm import ChatRequest, LLMClient, Message, NotConfigured
        monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:3128")  # must not be used for loopback
        monkeypatch.setenv("ALL_PROXY", "http://10.255.255.1:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        rt = fake_runtime()
        client = LLMClient(FakeConfig(ai_provider="local", ai_timeout=20), None, state_dir=rt.data_dir)
        with pytest.raises(NotConfigured, match="runtime start"):
            client.chat(ChatRequest(system="s", messages=[Message.user("ping")]))
        ep = rt.start()
        result = client.chat(ChatRequest(system="You are Ditto.", messages=[Message.user("ping")]))
        assert result.text == "pong from tiny"
        sent = json.loads((rt.root / "b1-cpu" / "last_request.json").read_text())
        assert sent["model"] == "tiny" and client.describe()["base_url"] == ep.base_url
        # A restart changes port and key; the client follows without being told.
        ep2 = rt.start(ctx=4096)
        assert (ep2.port, ep2.api_key) != (ep.port, ep.api_key)
        assert client.chat(ChatRequest(system="s", messages=[Message.user("again")])).text == "pong from tiny"
        rt.stop()
        with pytest.raises(NotConfigured):
            client.chat(ChatRequest(system="s", messages=[Message.user("gone")]))

    def test_running_server_is_reused_and_restarted_for_other_settings(self, fake_runtime):
        rt = fake_runtime()
        first = rt.start()
        assert rt.start().pid == first.pid
        second = rt.start(ctx=4096)
        assert second.pid != first.pid and gone(first.pid)
        assert rt.status().ctx == 4096

    def test_exit_during_startup_reports_the_log(self, fake_runtime):
        rt = fake_runtime("exit")
        with pytest.raises(LocalRuntimeError, match="exited during startup") as err:
            rt.start()
        assert "failed to load model" in err.value.log_tail
        assert "failed to load model" not in str(err.value)  # the log is untrusted text, kept apart
        assert not rt.state_file.exists() and not list(rt.root.glob("server-*.key"))

    def test_port_taken_between_choice_and_bind_is_retried(self, fake_runtime):
        rt = fake_runtime("bind_once")
        ep = rt.start()
        assert (rt.root / "b1-cpu" / "starts").read_text() == "2" and rt.status().healthy and ep.pid

    def test_server_that_cannot_be_executed(self, fake_runtime):
        rt = fake_runtime()
        (rt.root / "b1-cpu" / "llama-server").chmod(0o644)
        with pytest.raises(LocalRuntimeError, match="runtime install --prebuilt --force"):
            rt.start()
        assert not list(rt.root.glob("server-*.key"))

    def test_server_that_ignores_the_key_is_stopped(self, fake_runtime):
        rt = fake_runtime("noauth")
        with pytest.raises(LocalRuntimeError, match="did not enforce its API key"):
            rt.start()
        assert not rt.status().running

    def test_health_timeout_kills_the_server(self, fake_runtime):
        rt = fake_runtime("slow")
        with pytest.raises(LocalRuntimeError, match="did not become ready in time"):
            rt.start(timeout=1.5)
        assert not rt.status().running and not rt.state_file.exists()

    def test_stop_ends_the_whole_process_group(self, fake_runtime):
        rt = fake_runtime("child")
        ep = rt.start()
        children = psutil.Process(ep.pid).children(recursive=True)
        assert children
        rt.stop()
        assert all(gone(child.pid) for child in children)

    def test_tied_server_is_stopped_at_exit_unless_it_was_replaced(self, fake_runtime, monkeypatch):
        handlers = []
        monkeypatch.setattr(ls.atexit, "register", handlers.append)
        rt = fake_runtime()
        first = rt.start(tie_to_process=True)
        second = rt.start(ctx=4096)  # replaced: the exit handler of the first start must leave it alone
        handlers[0]()
        assert rt.endpoint().pid == second.pid
        rt.stop()
        third = rt.start(tie_to_process=True)
        handlers[-1]()
        assert gone(third.pid) and rt.endpoint() is None and gone(first.pid)

    def test_a_program_answering_on_the_chosen_port_never_gets_the_key(self, fake_runtime, monkeypatch):
        # Port race: another program takes the free port first and answers /health (and accepts any key). It used to
        # be taken for our server, and _check_auth sent it the new key; the endpoint then pointed at it.
        with impostor_on_the_first_port(monkeypatch) as (taken, seen):
            rt = fake_runtime()
            ep = rt.start()
            assert ep.port != taken and ls.listens_on(ep.pid, ep.port)
            assert seen and not any(ep.api_key in s for s in seen) and all(not s for s in seen)
            assert (rt.root / "b1-cpu" / "starts").read_text() == "2", "the second port was tried"

    def test_other_processes_get_no_endpoint_before_the_port_is_confirmed(self, fake_runtime, monkeypatch):
        # Review of port-runtime: server.json is written right after the launch, and LLMClient in every other process
        # (the GUI, the MCP server) reads it. While the port check still ran it named the impostor's port with the new
        # key, so a chat sent at that moment gave the impostor the key and the prompt. Here the stand-in binds after
        # 1 s, as llama-server binds only after it loaded its GPU backends.
        with impostor_on_the_first_port(monkeypatch) as (taken, seen):
            rt = fake_runtime()
            (rt.root / "b1-cpu" / "behavior.json").write_text(json.dumps({"mode": "ok", "bind_delay": 1.0}))
            handed_out = []
            rt._sleep = lambda s: (handed_out.append(ls.read_endpoint(rt.root)), time.sleep(s))
            ep = rt.start()
            assert ep.port != taken and (rt.root / "b1-cpu" / "starts").read_text() == "2"
            assert handed_out and not [e for e in handed_out if e is not None and e.port == taken]
            assert all(not s for s in seen)
            again = ls.read_endpoint(rt.root)
            assert (again.port, again.api_key, again.pid) == (ep.port, ep.api_key, ep.pid)
            assert "port_unconfirmed" not in json.loads(rt.state_file.read_text()), "server.json as before"

    def test_a_server_that_holds_its_port_is_handed_out_while_it_loads(self, fake_runtime):
        # llama-server binds its port before it loads the model (/health answers 503 meanwhile). Once it is that port's
        # listener, other processes get the endpoint as before (their calls wait for the model), not "not running".
        rt = fake_runtime("slow")
        handed_out = []
        rt._sleep = lambda s: (handed_out.append(ls.read_endpoint(rt.root)), time.sleep(s))
        with pytest.raises(LocalRuntimeError, match="did not become ready in time"):
            rt.start(timeout=2)
        assert [e for e in handed_out if e is not None]

    def test_a_start_that_died_before_the_port_was_confirmed_leaves_no_server_behind(self, fake_runtime):
        # A start killed before the port check finished leaves a running server whose record is not handed out; the
        # next start stops it instead of starting a second one beside it.
        rt = fake_runtime()
        first = rt.start()
        state = json.loads(rt.state_file.read_text())
        rt.state_file.write_text(json.dumps({**state, "port_unconfirmed": True}))
        assert rt.endpoint() is None and rt.status().running
        second = rt.start()
        assert second.pid != first.pid and gone(first.pid) and rt.endpoint().pid == second.pid

    def test_an_unreliable_connection_table_does_not_block_the_start(self, fake_runtime, monkeypatch):
        # Where psutil cannot list the server's sockets, a server that keeps running after the grace period has bound
        # its port (llama-server exits when the port is taken).
        monkeypatch.setattr(ls, "listens_on", lambda pid, port: False)
        rt = fake_runtime()
        rt.PORT_CHECK_GRACE = 0.5
        ep = rt.start()
        assert rt.status().healthy and (rt.root / "b1-cpu" / "starts").read_text() == "1" and ep.pid
        monkeypatch.setattr(ls, "listens_on", lambda pid, port: None)  # cannot be checked at all: no wait
        assert rt.start(ctx=4096).pid != ep.pid

    def test_choosing_a_tier_is_remembered_when_the_running_server_is_reused(self, fake_runtime):
        # `runtime start --model X` (or `--model auto`) on a server that already serves X returned before the choice
        # was recorded, so the next plain start loaded the recommendation instead.
        rt = fake_runtime()
        first = rt.start()
        assert rt.preferred_tier() is None
        assert rt.start(tier="tiny", remember=False).pid == first.pid and rt.preferred_tier() is None
        assert rt.start(tier="tiny").pid == first.pid and rt.preferred_tier() == "tiny"
        assert rt.start(tier="auto").pid == first.pid and rt.preferred_tier() is None

    def test_an_explicit_idle_time_restarts_a_server_started_with_another(self, fake_runtime):
        # `runtime start --sleep-idle 0` was ignored while a server with the catalog's 600 s ran.
        rt = fake_runtime()
        first = rt.start()
        argv = json.loads((rt.root / "b1-cpu" / "argv-1.json").read_text())
        assert argv[argv.index("--sleep-idle-seconds") + 1] == "600"
        second = rt.start(sleep_idle_seconds=0)
        assert second.pid != first.pid and "--sleep-idle-seconds" not in json.loads(
            (rt.root / "b1-cpu" / "argv-2.json").read_text())
        assert rt.start(sleep_idle_seconds=0).pid == second.pid and rt.start().pid == second.pid
        assert rt.start(sleep_idle_seconds=600).pid != second.pid

    def test_an_idle_time_the_build_cannot_use_restarts_nothing(self, fake_runtime):
        # A build without --sleep-idle-seconds always runs with 0: asking for another value compares as 0, so it
        # neither restarts the server on every start nor passes the option.
        rt = fake_runtime()
        rt.catalog.llama_cpp.features["sleep_idle_seconds"] = False
        first = rt.start()
        assert rt.start(sleep_idle_seconds=300).pid == first.pid
        assert "--sleep-idle-seconds" not in json.loads((rt.root / "b1-cpu" / "argv-1.json").read_text())

    def test_exit_handler_does_not_wait_for_a_busy_lock(self, fake_runtime, monkeypatch):
        # Another process holding the runtime lock (its start waits up to minutes for health) must not keep this
        # process from exiting for the lock's 15 minutes; the tied server, verified by pid and start time, is ended
        # anyway.
        handlers = []
        monkeypatch.setattr(ls.atexit, "register", handlers.append)
        rt = fake_runtime()
        ep = rt.start(tie_to_process=True)
        holder = psutil.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            rt.lock_file.write_text(f"{holder.pid} {holder.create_time()}")
            rt.EXIT_LOCK_TIMEOUT = 0.3
            began = time.monotonic()
            handlers[0]()
            assert time.monotonic() - began < 5 and gone(ep.pid)
        finally:
            holder.kill()
            holder.wait(5)
            rt.lock_file.unlink(missing_ok=True)
        assert rt.endpoint() is None

    def test_crashed_server_leaves_nothing_that_is_trusted(self, fake_runtime):
        rt = fake_runtime()
        ep = rt.start()
        os.kill(ep.pid, signal.SIGKILL)
        rt._proc.wait(5)
        assert read_endpoint(rt.root) is None and not rt.status().running
        state = json.loads(rt.state_file.read_text())
        state["pid"] = os.getpid()  # a recorded pid that now belongs to another process
        rt.state_file.write_text(json.dumps(state))
        assert read_endpoint(rt.root) is None and rt.stop() == 0 and psutil.pid_exists(os.getpid())
        assert rt.start().pid != ep.pid

    def test_key_outside_the_runtime_folder_is_never_read(self, fake_runtime, tmp_path):
        rt = fake_runtime()
        rt.start()
        state = json.loads(rt.state_file.read_text())
        elsewhere = tmp_path / "server-x.key"
        elsewhere.write_text("not-the-key")
        state["key_file"] = str(elsewhere)
        rt.state_file.write_text(json.dumps(state))
        assert read_endpoint(rt.root) is None

    def test_cli_start_status_stop(self, fake_runtime, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        rt = fake_runtime()
        monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
        runner = CliRunner()
        started = runner.invoke(cli_module.cli, ["-q", "ditto", "runtime", "start", "--model", "tiny"])
        assert started.exit_code == 0, started.output
        assert "Running:" in started.output and "127.0.0.1" in started.output
        ep = rt.endpoint()
        assert ep.api_key not in started.output and rt.preferred_tier() == "tiny"
        status = runner.invoke(cli_module.cli, ["-q", "ditto", "runtime", "status", "--json"])
        data = json.loads(status.output)
        assert data["running"] and data["healthy"] and data["model"] == "tiny" and ep.api_key not in status.output
        stopped = runner.invoke(cli_module.cli, ["-q", "ditto", "runtime", "stop"])
        assert "Stopped (1 process)" in stopped.output
        assert "Not running." in runner.invoke(cli_module.cli, ["-q", "ditto", "runtime", "stop"]).output

    def test_cli_start_failure_shows_the_log(self, fake_runtime, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        rt = fake_runtime("exit")
        monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
        result = CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", "start"])
        assert result.exit_code == 1 and "exited during startup" in result.output
        assert "failed to load model" in result.output


class TestHousekeeping:

    def runtime(self, tmp_path, **server):
        return LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                               catalog=runtime_catalog(**server), hardware=CPU_HW, sleep=lambda s: None)

    def test_orphans_are_only_servers_from_the_runtime_folder(self, tmp_path):
        rt = self.runtime(tmp_path)
        ours = rt.root / "b1-cpu" / "llama-server"

        class Proc:
            def __init__(self, pid, exe):
                self.pid, self._exe = pid, exe
            def exe(self):
                if self._exe is None:
                    raise psutil.AccessDenied(self.pid)
                return self._exe

        procs = [Proc(10, str(ours)), Proc(11, "/usr/local/bin/llama-server"), Proc(12, None),
                 Proc(13, str(rt.root / "b1-cpu" / "llama-cli")), Proc(14, str(ours))]
        rt._process_iter = lambda: procs
        assert [p.pid for p in rt.find_orphans(keep_pid=14)] == [10]
        stopped = []
        rt._terminate = lambda proc, timeout: stopped.append(proc.pid)
        (rt.root / "server-old.key").parent.mkdir(parents=True, exist_ok=True)
        (rt.root / "server-old.key").write_text("k")
        rt.state_file.write_text(json.dumps({"pid": 999999, "create_time": 1.0, "server": str(ours)}))
        assert rt.cleanup_stale() == 2  # pid 14 is not recorded anywhere either
        assert stopped == [10, 14] and not rt.state_file.exists() and not (rt.root / "server-old.key").exists()

    def test_log_rotation_keeps_the_configured_backups(self, tmp_path):
        rt = self.runtime(tmp_path, log_max_mib=1, log_backups=2)
        rt.log_file.parent.mkdir(parents=True)
        rt.log_file.write_bytes(b"current\n" * 200000)
        rt.log_file.with_name("llama-server.log.1").write_text("previous")
        rt.log_file.with_name("llama-server.log.2").write_text("oldest")
        with rt._open_log() as log:
            log.write(b"new run\n")
        assert rt.log_file.read_text().startswith("\n=== llama-server start")
        assert rt.log_file.with_name("llama-server.log.1").read_bytes().startswith(b"current")
        assert rt.log_file.with_name("llama-server.log.2").read_text() == "previous"
        assert not rt.log_file.with_name("llama-server.log.3").exists()
        if os.name == "posix":
            assert stat.S_IMODE(rt.log_file.stat().st_mode) == 0o600
        assert rt._log_tail() == "new run"  # only the current run

    def test_lock_waits_for_a_live_owner_and_takes_over_a_dead_one(self, tmp_path):
        lock_path = tmp_path / ".lock"
        with ls._FileLock(lock_path):
            with pytest.raises(LocalRuntimeError, match="busy"):
                with ls._FileLock(lock_path, timeout=0.3, sleep=lambda s: time.sleep(0.05)):
                    pass
        assert not lock_path.exists()
        lock_path.write_text("999999 12345.0")  # a process that no longer exists
        with ls._FileLock(lock_path, timeout=0.3):
            assert lock_path.read_text().startswith(str(os.getpid()))

    def test_windows_sharing_violations_are_retried(self, tmp_path, monkeypatch):
        """Windows refuses to delete a file that another process has open at that moment (LLMClient reading
        server.json or the key in the GUI, a lock waiter reading the lock file): stop() and the runtime lock try
        again for a moment (utils/atomic.remove_file) instead of failing with a sharing violation."""
        from orchestrator.utils import atomic
        monkeypatch.setattr(atomic, "REPLACE_RETRIES", 10)  # what a Windows host uses
        monkeypatch.setattr("time.sleep", lambda s: None)
        rt = self.runtime(tmp_path)
        rt.root.mkdir(parents=True)
        rt.state_file.write_text(json.dumps({"pid": 999999, "create_time": 1.0, "server": "x"}))
        (rt.root / "server-a.key").write_text("k")
        real_unlink, refused = os.unlink, []

        def busy_once(path, *args, **kwargs):
            if Path(path).name not in refused:
                refused.append(Path(path).name)
                raise PermissionError(13, "The process cannot access the file")
            return real_unlink(path, *args, **kwargs)
        monkeypatch.setattr(os, "unlink", busy_once)
        assert rt.stop() == 0
        assert not rt.state_file.exists() and not (rt.root / "server-a.key").exists() and not rt.lock_file.exists()
        assert {"server.json", "server-a.key", ".lock"} <= set(refused)

    def test_preferred_tier_and_auto(self, tmp_path):
        rt = self.runtime(tmp_path)
        assert rt.resolve_model()[0].tier == "tiny" and rt.preferred_tier() is None
        rt._update_active(tier="tiny")
        assert "chosen earlier" in rt.resolve_model()[1]
        assert rt.resolve_model("auto")[1] != "chosen"
        rt._update_active(tier="removed-from-catalog")
        assert rt.preferred_tier() is None
        with pytest.raises(LocalRuntimeError, match="Unknown model tier"):
            rt.resolve_model("nope")

    def test_start_without_installation(self, tmp_path):
        with pytest.raises(LocalRuntimeError, match="runtime install"):
            self.runtime(tmp_path).start()


# --- CLI without a server ---------------------------------------------------------------------------------------------

class TestCli:

    def invoke(self, monkeypatch, rt, *args, **kw):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
        return CliRunner().invoke(cli_module.cli, ["-q", "ditto", *args], **kw)

    @staticmethod
    def probed(tmp_path, machine):
        """A runtime with a stored probe and toolchain (no real probe, no compiler run)."""
        from orchestrator.runtime.build.recipe import ToolchainInfo
        from orchestrator.runtime.build.toolchain import DetectedToolchain
        from orchestrator.runtime.profile import read_profile
        profile = read_profile(Path(__file__).parent / "fixtures" / "hardware_probe" / "schema2_pc_two_nvidia.txt")
        tools = DetectedToolchain(info=ToolchainInfo(host_os="linux", host_arch="x86_64", compiler="gcc-13.3.0",
                                                     cmake="3.28.3", openmp=True), cpus=4, ram_available_mib=8192)
        return LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", hardware=machine, profile=profile,
                               toolchain=tools, platform=("linux", "x86_64"))

    def test_status_json_for_an_rtx_2080_ti(self, tmp_path, monkeypatch):
        rt = self.probed(tmp_path, hw([nvidia("580.95.05")], ram_gib=32))
        result = self.invoke(monkeypatch, rt, "runtime", "status", "--json")
        data = json.loads(result.output)
        assert data["prebuilt_option"] == "linux-x64-cuda-12.8" and "615" in data["prebuilt_reason"]
        # The self-built server: CUDA would fit, but CUDA builds come with a later work package, so the CPU build
        # runs and the tier follows the RAM.
        assert data["recommended_backend"] == "cpu" and "cuda" in data["backend_reason"]
        assert data["recommended_tier"] == "default" and data["installed"] is None and not data["running"]

    def test_status_table(self, tmp_path, monkeypatch):
        rt = self.probed(tmp_path, hw(ram_gib=2.9))
        result = self.invoke(monkeypatch, rt, "runtime", "status")
        assert result.exit_code == 0 and "small-q2" in result.output and "runtime install" in result.output
        assert "not self-built" in result.output, "the prebuilt opt-in is labelled"

    def test_install_and_model_download(self, tmp_path, monkeypatch):
        rt, _ = TestInstall().setup_runtime(tmp_path)
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--prebuilt", "--yes")
        assert result.exit_code == 0 and "Installed" in result.output, result.output
        data = b"GGUF" + os.urandom(1000)
        rt.catalog = runtime_catalog(data, assets=[a.model_dump() for a in rt.catalog.llama_cpp.assets])
        rt._session.files[f"https://models.invalid/example/Tiny-GGUF/resolve/{'c' * 40}/tiny.gguf"] = data
        result = self.invoke(monkeypatch, rt, "models", "download", "tiny", "--yes")
        assert result.exit_code == 0 and "Verified and saved" in result.output, result.output
        listing = self.invoke(monkeypatch, rt, "models", "list")
        assert "downloaded" in listing.output and "recommended" in listing.output
        assert "already downloaded" in self.invoke(monkeypatch, rt, "models", "download", "tiny").output

    def test_errors_have_clear_messages_and_exit_codes(self, tmp_path, monkeypatch):
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", hardware=hw(ram_gib=8))
        result = self.invoke(monkeypatch, rt, "runtime", "start")
        assert result.exit_code == 1 and "llm-cli ditto runtime install" in result.output
        result = self.invoke(monkeypatch, rt, "models", "download", "nope")
        assert result.exit_code == 2 and "Unknown model tier" in result.output
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--prebuilt", "--backend", "rocm", "--yes")
        assert result.exit_code == 1 and "ROCm requested" in result.output
        declined = self.invoke(monkeypatch, rt, "models", "download", "small", input="n\n")
        assert declined.exit_code == 0 and not list(rt.models_dir.glob("*.gguf"))

    def test_model_download_defaults_to_the_model_runtime_start_loads(self, tmp_path, monkeypatch):
        # `runtime start` loads the tier chosen earlier with --model; `models download` without a tier fetched the
        # recommendation instead, so the next start still failed with "not downloaded".
        small, other = b"GGUF" + os.urandom(800), b"GGUF" + os.urandom(900)
        models = [{"tier": "tiny", "repo": "example/Tiny-GGUF", "revision": "c" * 40, "file": "tiny.gguf",
                   "size": len(small), "sha256": sha(small)},
                  {"tier": "other", "repo": "example/Other-GGUF", "revision": "d" * 40, "file": "other.gguf",
                   "size": len(other), "sha256": sha(other)}]
        session = FakeSession({f"https://models.invalid/example/Other-GGUF/resolve/{'d' * 40}/other.gguf": other})
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", hardware=CPU_HW, session=session,
                             catalog=runtime_catalog(models=models), sleep=lambda s: None)
        rt._update_active(tier="other")
        assert rt.resolve_model()[0].tier == "other", "what a plain start loads"
        result = self.invoke(monkeypatch, rt, "models", "download", "--yes")
        assert result.exit_code == 0 and "Chosen earlier with 'runtime start --model': other" in result.output, \
            result.output
        assert "Recommended:" not in result.output
        assert rt.model_ready(rt.catalog.model("other")) and not rt.model_path(rt.catalog.model("tiny")).exists()

    def test_install_failure_exits_with_1(self, tmp_path, monkeypatch):
        rt, session = TestInstall().setup_runtime(tmp_path)
        for url in session.files:
            session.files[url] = b"x" + session.files[url][1:]
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--prebuilt", "--yes")
        assert result.exit_code == 1 and "does not match its pin" in result.output


# --- LLMClient integration without a process -------------------------------------------------------------------------

class TestClientIntegration:

    def make(self, tmp_path, resolver=None, http=None, **settings):
        from orchestrator.llm import LLMClient
        return LLMClient(FakeConfig(ai_provider="local", **settings), None, state_dir=tmp_path / "state",
                         runtime_resolver=resolver, http_client=http)

    def test_resolver_supplies_url_key_and_model(self, tmp_path):
        httpx2 = pytest.importorskip("httpx2")
        seen = []

        def handler(request):
            seen.append(request)
            return httpx2.Response(200, json={"id": "c1", "object": "chat.completion", "created": 0, "model": "m",
                                              "choices": [{"index": 0, "finish_reason": "stop",
                                                           "message": {"role": "assistant", "content": "hi"}}]})
        from orchestrator.llm import ChatRequest, Message
        client = self.make(tmp_path, lambda pid: ("http://127.0.0.1:9123/v1", "per-start-key", "Qwen3.5-4B-Q4_K_M"),
                           httpx2.Client(transport=httpx2.MockTransport(handler)))
        assert client.chat(ChatRequest(system="s", messages=[Message.user("x")])).text == "hi"
        assert str(seen[0].url) == "http://127.0.0.1:9123/v1/chat/completions"
        assert seen[0].headers["authorization"] == "Bearer per-start-key"
        assert json.loads(seen[0].content)["model"] == "Qwen3.5-4B-Q4_K_M"
        client.set_runtime_endpoint("local", "http://127.0.0.1:9999/v1", "explicit", "other")  # explicit wins
        assert client.resolve().base_url == "http://127.0.0.1:9999/v1" and client.resolve().model == "other"

    def test_a_new_key_never_goes_to_the_port_of_the_previous_server(self, tmp_path):
        # A restart between resolve() and adapter() gives a new port and a new key. The adapter used to pair the old
        # URL with the new key (the credential read the endpoint again); another program may hold the old port now.
        from orchestrator.llm import ProviderUnavailable
        answers = [("http://127.0.0.1:9123/v1", "old-key", "m"), ("http://127.0.0.1:9124/v1", "new-key", "m")]
        client = self.make(tmp_path, lambda pid: answers[0])
        target = client.resolve()
        answers.pop(0)
        with pytest.raises(ProviderUnavailable, match="stopped or restarted"):
            client.adapter(target)
        assert not client._adapters, "nothing was built for the old port"
        answers[0] = None   # stopped meanwhile
        with pytest.raises(ProviderUnavailable, match="stopped or restarted"):
            client.adapter(target)
        answers[0] = ("http://127.0.0.1:9124/v1", "new-key", "m")
        fresh = client.resolve()
        adapter = client.adapter(fresh)
        assert adapter.base_url == "http://127.0.0.1:9124/v1"
        assert client.credential(fresh).api_key.get_secret_value() == "new-key"

    def test_broken_resolver_means_not_running(self, tmp_path):
        from orchestrator.llm import NotConfigured

        def broken(pid):
            raise ValueError("corrupt state")
        with pytest.raises(NotConfigured, match="not running"):
            self.make(tmp_path, broken).resolve()

    def test_default_resolver_reads_the_managed_state_only_for_local(self, tmp_path):
        client = self.make(tmp_path)
        assert client.runtime_resolver("local") is None and client.runtime_resolver("custom") is None

    def test_loopback_servers_bypass_environment_proxies(self, tmp_path, monkeypatch):
        httpx2 = pytest.importorskip("httpx2")
        monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:3128")
        monkeypatch.setenv("ALL_PROXY", "http://10.255.255.1:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        client = self.make(tmp_path, lambda pid: ("http://127.0.0.1:9123/v1", "k", "m"))
        http = client._http_client_for(client.resolve())
        transport = http._transport_for_url(httpx2.URL("http://127.0.0.1:9123/v1/chat/completions"))
        assert type(transport._pool).__name__ == "ConnectionPool"  # not HTTPProxy
        from orchestrator.llm import Target
        ollama = client.resolve(provider="ollama")  # http://localhost:11434/v1
        assert client._http_client_for(ollama) is http
        cloud = Target(preset=client.catalog.get("openrouter"), model="m", base_url="https://openrouter.ai/api/v1",
                       auth="api_key", local=False)
        assert client._http_client_for(cloud) is None  # cloud providers keep the SDK default (proxies allowed)
