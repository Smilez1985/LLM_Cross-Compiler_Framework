"""
Managed llama-server runtime: catalog, hardware detection and build/model choice, pinned downloads, atomic
install, the server process, the CLI commands and the "local" provider of LLMClient.

No real downloads and no real GPU: nvidia-smi / vulkaninfo / PowerShell output samples, sysfs trees in tmp_path,
a fake HTTP session with Range support, archives built in tmp_path and a small Python stand-in for llama-server
that speaks /health, /v1/models and /v1/chat/completions with API-key checks.
"""

import hashlib
import io
import json
import os
import signal
import stat
import sys
import tarfile
import time
import zipfile
from pathlib import Path

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


def hw(gpus=(), ram_gib=16.0, os_name="linux", arch="x86_64", vulkan=False, hip=None, free_gib=None):
    total = int(ram_gib * GIB)
    return HardwareInfo(os=os_name, arch=arch, ram_total=total, ram_available=int((free_gib or ram_gib * 0.8) * GIB),
                        gpus=list(gpus), vulkan=vulkan, hip_runtime=hip)


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

    def test_ryzen_z1_extreme_uses_vulkan_with_shared_memory(self, tmp_path):
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

    def test_not_enough_disk_space(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 * MIB})())
        with pytest.raises(LocalRuntimeError, match="Not enough free disk space"):
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
        inst = rt.install()
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
        assert rt.install() == inst and len(session.calls) == calls  # already installed: nothing downloaded
        rt.install(force=True)
        assert len(session.calls) == calls + 2

    def test_windows_zips_unpack_into_one_folder(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, os_name="windows")
        inst = rt.install()
        assert inst.server.name == "llama-server.exe" and (inst.server.parent / "cudart64_13.dll").exists()

    def test_a_failed_reinstall_keeps_the_previous_installation(self, tmp_path):
        rt, session = self.setup_runtime(tmp_path)
        inst = rt.install()
        for url in session.files:
            session.files[url] = b"tampered" + session.files[url][8:]
        with pytest.raises(IntegrityError):
            rt.install(force=True)
        assert rt.installed() == inst and inst.server.is_file()
        assert not list(rt.root.glob(".staging-*")) and not list(rt.root.glob(".old-*"))

    def test_archive_escaping_its_folder_is_refused(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, symlinks={"evil": "../../../../outside"})
        with pytest.raises(IntegrityError, match="Unsafe or broken archive"):
            rt.install()
        assert rt.installed() is None and not (rt.root / "b1-cuda-13.4").exists()

    def test_runtime_libraries_may_not_replace_build_files(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, lib_files={"libggml-cuda.so": b"other"})
        with pytest.raises(IntegrityError, match="would replace"):
            rt.install()

    def test_missing_server_binary(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path, main_files={"llama-cli": b"x"}, symlinks={})
        with pytest.raises(LocalRuntimeError, match="llama-server not found"):
            rt.install()

    def test_unpinned_asset_is_refused_before_any_download(self, tmp_path):
        asset = {"id": "linux-x64-cpu", "os": "linux", "arch": "x86_64", "backend": "cpu",
                 "files": [{"name": "llama-b1-cpu.tar.gz", "size": 10}]}
        session = FakeSession()
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", catalog=runtime_catalog(assets=[asset]), hardware=CPU_HW,
                             session=session, models_dir=tmp_path / "m")
        with pytest.raises(IntegrityError, match="refusing to download"):
            rt.install()
        assert session.calls == []

    def test_old_builds_are_pruned_other_variants_stay(self, tmp_path):
        rt, _ = self.setup_runtime(tmp_path)
        (rt.root / "b0-cpu").mkdir(parents=True)
        (rt.root / "b1-vulkan").mkdir(parents=True)
        rt.install()
        assert not (rt.root / "b0-cpu").exists() and (rt.root / "b1-vulkan").exists()

    def test_refuses_to_replace_the_installation_the_server_runs_from(self, tmp_path, monkeypatch):
        rt, _ = self.setup_runtime(tmp_path)
        inst = rt.install()

        class Running:
            pid = 1
            def cmdline(self):
                return [str(inst.server)]
            def exe(self):
                return str(inst.server)
        monkeypatch.setattr(ls, "owned_process", lambda state: Running())
        with pytest.raises(LocalRuntimeError, match="stop it first"):
            rt.install(force=True)

    def test_no_build_for_this_machine(self, tmp_path):
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", catalog=runtime_catalog(),
                             hardware=hw(arch="riscv64"))
        with pytest.raises(LocalRuntimeError, match="No llama.cpp build"):
            rt.install()


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
        path.write_bytes(data[:-1] + b"!")  # same size, different content
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

    @pytest.mark.parametrize("alias", ["--tools", "--api-key", "--agent", "--rpc", "--webui"])
    def test_forbidden_options_never_reach_the_server(self, alias):
        with pytest.raises(LocalRuntimeError, match="Refusing"):
            build_server_command(Path("s"), Path("m"), 1, Path("k"), 4096, alias, gpu=False, cache_ram_mib=256)

    def test_environment_has_no_secrets_and_no_llama_options(self):
        env = server_env({"PATH": "/usr/bin", "CUDA_VISIBLE_DEVICES": "0", "LLAMA_ARG_TOOLS": "all",
                          "LLAMA_API_KEY": "x", "OPENAI_API_KEY": "sk-fake", "HF_TOKEN": "hf_fake",
                          "AWS_SECRET_ACCESS_KEY": "x", "GGML_VK_VISIBLE_DEVICES": "0"})
        assert env == {"PATH": "/usr/bin", "CUDA_VISIBLE_DEVICES": "0", "GGML_VK_VISIBLE_DEVICES": "0"}

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

ThreadingHTTPServer((opt("--host"), int(opt("--port"))), Handler).serve_forever()
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
        rt._update_active(variant="cpu", asset_id="linux-x64-cpu")
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


@POSIX_ONLY
class TestServerProcess:

    def test_start_serve_and_stop(self, fake_runtime, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake0123456789abcdefghijk")
        monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")
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
        with pytest.raises(LocalRuntimeError, match="runtime install --force"):
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

    def test_status_json_for_an_rtx_2080_ti(self, tmp_path, monkeypatch):
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m",
                             hardware=hw([nvidia("580.95.05")], ram_gib=32))
        result = self.invoke(monkeypatch, rt, "runtime", "status", "--json")
        data = json.loads(result.output)
        assert data["recommended_build"] == "linux-x64-cuda-12.8" and "615" in data["build_reason"]
        assert data["recommended_tier"] == "large" and data["installed"] is None and not data["running"]

    def test_status_table(self, tmp_path, monkeypatch):
        rt = LocalLLMRuntime(data_dir=tmp_path / "s", models_dir=tmp_path / "m", hardware=hw(ram_gib=2.9))
        result = self.invoke(monkeypatch, rt, "runtime", "status")
        assert result.exit_code == 0 and "small-q2" in result.output and "runtime install" in result.output

    def test_install_and_model_download(self, tmp_path, monkeypatch):
        rt, _ = TestInstall().setup_runtime(tmp_path)
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--yes")
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
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--backend", "rocm", "--yes")
        assert result.exit_code == 1 and "ROCm requested" in result.output
        declined = self.invoke(monkeypatch, rt, "models", "download", "small", input="n\n")
        assert declined.exit_code == 0 and not list(rt.models_dir.glob("*.gguf"))

    def test_install_failure_exits_with_1(self, tmp_path, monkeypatch):
        rt, session = TestInstall().setup_runtime(tmp_path)
        for url in session.files:
            session.files[url] = b"x" + session.files[url][1:]
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--yes")
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
