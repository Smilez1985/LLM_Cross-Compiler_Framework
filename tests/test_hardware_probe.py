"""
The hardware probe (scripts/hardware_probe.py, the main path) and its manual fallbacks scripts/hardware_probe.sh and
scripts/hardware_probe.ps1, all writing schema 2 (configs/hardware_probe_schema.yml).

- Fixture machines are built in tmp_path as a sysroot (HW_PROBE_SYSROOT) from the /proc/cpuinfo and os-release files
  in tests/fixtures/hardware_probe/ (written from public specifications: an Intel Core i5-8250U, an AMD Ryzen 7 7840U,
  an RK3566 with four Cortex-A55 and an RK3588 with Cortex-A55 and Cortex-A76) plus made-up sysfs and device-tree
  entries. No serial number, host name or MAC address appears in them except made-up ones that must never come out.
- The .sh and the .py are run on the same sysroots with the same fake tools on PATH; their stable keys and perf_key
  must be identical.
- The probe's copy of the schema (key table, tables, Windows feature constants) and the .sh/.ps1 tables are compared
  with the schema; the scripts' write calls are checked against it.
- No network: the probe runs without sockets and without reading stdin; external tools are fakes.
"""

import ast
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from orchestrator.runtime import hardware
from orchestrator.runtime.profile import SCHEMA_PATH, load_schema, parse_profile, parse_profile_json

REPO = Path(__file__).resolve().parents[1]
PROBE_PY = REPO / "scripts" / "hardware_probe.py"
PROBE_SH = REPO / "scripts" / "hardware_probe.sh"
PROBE_PS1 = REPO / "scripts" / "hardware_probe.ps1"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hardware_probe"
SCHEMA = load_schema()
RAW_SCHEMA = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
probe = hardware.load_probe()

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="the shell probe and sysroots are POSIX")
GIB = 1024 ** 3
MADE_UP_SERIAL = "c0ffee00deadbeef"
MADE_UP_HOST = "probe-fixture-host"

# Tools the .sh needs; the test PATH holds links to them and to fake vendor tools only.
SHELL_TOOLS = ("cat", "grep", "sed", "awk", "tr", "cut", "head", "tail", "sort", "uname", "date", "df", "ls",
               "readlink", "wc", "id", "timeout")


def no_tools(cmd, timeout):
    return -1, "", "not found"


# --- fixture machines ------------------------------------------------------------------------------------------

def write(root: Path, rel: str, text, mode="w"):
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def meminfo(total_kb, available_kb, swap_kb):
    return (f"MemTotal:       {total_kb} kB\nMemFree:        {available_kb // 2} kB\n"
            f"MemAvailable:   {available_kb} kB\nBuffers:           1024 kB\nSwapTotal:      {swap_kb} kB\n")


def pci_device(root, address, vendor, device, pci_class):
    base = f"/sys/bus/pci/devices/{address}"
    write(root, base + "/vendor", vendor + "\n")
    write(root, base + "/device", device + "\n")
    write(root, base + "/class", pci_class + "\n")


def usb_device(root, name, vendor, product):
    base = f"/sys/bus/usb/devices/{name}"
    write(root, base + "/idVendor", vendor + "\n")
    write(root, base + "/idProduct", product + "\n")
    write(root, base + "/serial", MADE_UP_SERIAL + "\n")  # never read


def drm_card(root, card, vendor, device, extra=None):
    base = f"/sys/class/drm/{card}/device"
    write(root, base + "/vendor", vendor + "\n")
    write(root, base + "/device", device + "\n")
    write(root, base + "/class", "0x030000\n")
    for name, value in (extra or {}).items():
        write(root, f"{base}/{name}", value)
    (root / f"sys/class/drm/{card}-eDP-1").mkdir(parents=True, exist_ok=True)  # a connector, not a card


def make_sysroot(base: Path, kind: str) -> Path:
    root = base / kind
    write(root, "/etc/hostname", MADE_UP_HOST + "\n")
    write(root, "/proc/1/comm", "systemd\n")
    if kind == "intel":
        write(root, "/proc/cpuinfo", (FIXTURES / "cpuinfo_x86_intel.txt").read_text())
        write(root, "/etc/os-release", (FIXTURES / "os-release_ubuntu.txt").read_text())
        write(root, "/proc/sys/kernel/osrelease", "6.8.0-45-generic\n")
        write(root, "/proc/sys/kernel/arch", "x86_64\n")
        write(root, "/proc/meminfo", meminfo(16219780, 11021512, 2097148))
        (root / "run/systemd/system").mkdir(parents=True)
        write(root, "/lib/x86_64-linux-gnu/libc.so.6", b"")
        write(root, "/usr/lib/x86_64-linux-gnu/libvulkan.so.1", b"")
        drm_card(root, "card0", "0x8086", "0x5917")
        pci_device(root, "0000:00:02.0", "0x8086", "0x5917", "0x030000")
        usb_device(root, "usb1", "1d6b", "0002")    # a USB root hub (Linux Foundation): no MemryX
        usb_device(root, "1-2", "2207", "350a")     # an RK3588 board in maskrom mode on USB
    elif kind == "amd":
        write(root, "/proc/cpuinfo", (FIXTURES / "cpuinfo_x86_amd.txt").read_text())
        write(root, "/etc/os-release", (FIXTURES / "os-release_alpine.txt").read_text())
        write(root, "/proc/sys/kernel/osrelease", "6.6.58-0-lts\n")
        write(root, "/proc/sys/kernel/arch", "x86_64\n")
        write(root, "/proc/meminfo", meminfo(32106120, 28000000, 0))
        write(root, "/proc/1/comm", "init\n")
        (root / "run/openrc").mkdir(parents=True)
        write(root, "/lib/ld-musl-x86_64.so.1", b"")
        drm_card(root, "card1", "0x1002", "0x15bf", {"mem_info_vram_total": f"{512 * 1024 * 1024}\n",
                                                     "mem_info_vram_used": f"{100 * 1024 * 1024}\n"})
        (root / "sys/class/drm/card1/device/drm/renderD128").mkdir(parents=True)
        write(root, "/sys/class/kfd/kfd/topology/nodes/1/properties",
              "cpu_cores_count 0\nsimd_count 24\ngfx_target_version 110003\ndrm_render_minor 128\n")
    elif kind == "rk3566":
        write(root, "/proc/cpuinfo", (FIXTURES / "cpuinfo_rk3566.txt").read_text())
        write(root, "/etc/os-release", (FIXTURES / "os-release_dietpi.txt").read_text())
        write(root, "/boot/dietpi/.version", "G_DIETPI_VERSION_CORE=9\n")
        write(root, "/proc/sys/kernel/osrelease", "6.1.99-vendor-rk35xx\n")
        write(root, "/proc/sys/kernel/arch", "aarch64\n")
        write(root, "/proc/meminfo", meminfo(4005940, 3380000, 2002968))
        (root / "run/systemd/system").mkdir(parents=True)
        write(root, "/lib/aarch64-linux-gnu/libc.so.6", b"")
        for cpu in range(4):
            write(root, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq", "1800000\n")
        write(root, "/proc/device-tree/compatible", b"radxa,zero-3w-aic8800ds2\0rockchip,rk3566\0")
        write(root, "/proc/device-tree/model", b"Radxa ZERO 3W\0")
        write(root, "/proc/device-tree/gpu@fde60000/compatible", b"rockchip,rk3568-mali\0arm,mali-bifrost\0")
        write(root, "/proc/device-tree/gpu@fde60000/status", b"okay\0")
        write(root, "/proc/device-tree/gpu-off@0/compatible", b"arm,mali-midgard\0")
        write(root, "/proc/device-tree/gpu-off@0/status", b"disabled\0")
        write(root, "/dev/rknpu", b"")
    elif kind == "rk3588":
        write(root, "/proc/cpuinfo", (FIXTURES / "cpuinfo_rk3588.txt").read_text())
        write(root, "/etc/os-release", (FIXTURES / "os-release_dietpi.txt").read_text())
        write(root, "/etc/armbian-release", "BOARD=rock-5b\n")
        write(root, "/proc/sys/kernel/osrelease", "6.1.75-vendor-rk35xx\n")
        write(root, "/proc/sys/kernel/arch", "aarch64\n")
        write(root, "/proc/meminfo", meminfo(16063520, 14000000, 0))
        write(root, "/proc/1/comm", "init\n")
        (root / "sbin").mkdir(parents=True)
        os.symlink("/bin/busybox", root / "sbin" / "init")
        write(root, "/lib/ld-linux-aarch64.so.1", b"")
        for cpu in range(8):
            write(root, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq",
                  "1800000\n" if cpu < 4 else "2400000\n")
        write(root, "/proc/device-tree/compatible", b"radxa,rock-5b\0rockchip,rk3588\0")
        write(root, "/proc/device-tree/model", b"Radxa ROCK 5B\0")
        write(root, "/proc/device-tree/gpu@fb000000/compatible", b"rockchip,rk3588-mali\0arm,mali-valhall-csf\0")
        (root / "sys/bus/platform/drivers/rknpu").mkdir(parents=True)
        (root / "sys/class/drm/renderD129/device").mkdir(parents=True)
        os.symlink("../../../../bus/platform/drivers/rknpu", root / "sys/class/drm/renderD129/device/driver")
        pci_device(root, "0000:01:00.0", "0x1e60", "0x2864", "0x120000")  # a Hailo-8 on the M.2 slot
        write(root, "/dev/hailo0", b"")
    else:
        raise ValueError(kind)
    return root


FAKE_NVIDIA_SMI = """#!/bin/sh
case "$1" in
  --query-gpu=*)
    echo "580.95.05, 8.6, 24576, 23810, 00000000:01:00.0, 0x220410DE, NVIDIA GeForce RTX 3090"
    echo "580.95.05, 7.5, 11264, 10850, 00000000:02:00.0, 0x1E0710DE, NVIDIA GeForce RTX 2080 Ti" ;;
  -q)
    printf '\\n==============NVSMI LOG==============\\n\\nDriver Version : 580.95.05\\nCUDA Version : 13.0\\n' ;;
  *) exit 1 ;;
esac
"""
FAKE_VULKANINFO = """#!/bin/sh
cat <<'EOF'
Devices:
========
GPU0:
	apiVersion         = 1.4.305
	vendorID           = 0x10de
	deviceID           = 0x2204
	deviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
	deviceName         = NVIDIA GeForce RTX 3090
	driverInfo         = 580.95.05
GPU1:
	vendorID           = 0x8086
	deviceID           = 0x5917
	deviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
	deviceName         = Intel(R) UHD Graphics 620 (KBL GT2)
	driverInfo         = Mesa 24.0.9
GPU2:
	vendorID           = 0x10005
	deviceID           = 0x0000
	deviceType         = PHYSICAL_DEVICE_TYPE_CPU
	deviceName         = llvmpipe (LLVM 17.0.6, 256 bits)
EOF
"""


def tool_dir(base: Path, fake_gpu_tools=False) -> Path:
    """A PATH folder with the shell tools the .sh needs and, optionally, fake nvidia-smi and vulkaninfo."""
    bindir = base / "bin"
    bindir.mkdir(exist_ok=True)
    for name in SHELL_TOOLS:
        found = shutil.which(name)
        if found and not (bindir / name).exists():
            os.symlink(found, bindir / name)
    if fake_gpu_tools:
        for name, text in (("nvidia-smi", FAKE_NVIDIA_SMI), ("vulkaninfo", FAKE_VULKANINFO)):
            path = bindir / name
            path.write_text(text)
            path.chmod(0o755)
    return bindir


def run_emitter(cmd, root: Path, bindir: Path, home: Path) -> str:
    env = {"PATH": str(bindir), "HW_PROBE_SYSROOT": str(root), "HOME": str(home), "LC_ALL": "C"}
    result = subprocess.run(cmd + ["--output", "-"], env=env, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def available_shells():
    """POSIX shells to run the .sh with, as absolute command prefixes (the test PATH holds only tool links)."""
    shells = [shutil.which(s) for s in ("dash", "bash") if shutil.which(s)]
    if shutil.which("busybox"):
        shells.append(shutil.which("busybox") + " sh")
    return shells


def py_probe(root: Path, run=no_tools):
    return probe.collect(root=str(root), run=run, sections=("cpu", "memory", "soc", "npu", "gpu"))


# --- the Python probe on fixture machines ---------------------------------------------------------------------

@POSIX_ONLY
class TestFixtureMachines:

    def test_x86_intel_on_ubuntu(self, tmp_path):
        values = py_probe(make_sysroot(tmp_path, "intel"))
        profile = parse_profile(probe.render_text(values))
        expect = {"OS": "linux", "OS_ID": "ubuntu", "OS_ID_LIKE": "debian", "OS_FAMILY": "debian",
                  "OS_VERSION_ID": "24.04", "OS_VERSION_CODENAME": "noble", "OS_PRETTY_NAME": "Ubuntu 24.04.1 LTS",
                  "KERNEL": "6.8.0-45-generic", "KERNEL_ARCH": "x86_64", "ARCH": "x86_64", "LIBC": "glibc",
                  "INIT_SYSTEM": "systemd", "CPU_MODEL": "Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz",
                  "CPU_VENDOR": "intel", "CPU_CORES_LOGICAL": "8", "CPU_CORES_PHYSICAL": "4",
                  "CPU_FLAGS_SOURCE": "proc-cpuinfo", "SUPPORTS_SSE42": "ON", "SUPPORTS_AVX": "ON",
                  "SUPPORTS_AVX2": "ON", "SUPPORTS_BMI2": "ON", "SUPPORTS_F16C": "ON", "SUPPORTS_FMA": "ON",
                  "SUPPORTS_AVX512": "OFF", "SUPPORTS_AVX512_VNNI": "OFF", "SUPPORTS_AMX_TILE": "OFF",
                  "SUPPORTS_NEON": "OFF", "SUPPORTS_DOTPROD": "OFF", "RAM_TOTAL_MB": "15839", "SWAP_TOTAL_MB": "2047",
                  "USB_ROCKCHIP_PID": "0x350a", "USB_ROCKCHIP_MODE": "rockusb", "SUPPORTS_RKNN": "OFF",
                  "SUPPORTS_MX3": "OFF", "GPU_COUNT": "1", "GPU0_VENDOR": "intel", "GPU0_PCI_ID": "0x8086:0x5917",
                  "GPU0_DISCRETE": "OFF", "GPU0_UNIFIED": "ON", "SUPPORTS_INTEL_XPU": "ON", "VULKAN": "ON",
                  "HIP_RUNTIME": "OFF", "METAL": "OFF"}
        assert {k: profile.get(k) for k in expect} == expect
        assert "CPU_CLUSTERS" not in profile and "LIBC_VERSION" not in profile and "NPU_VENDOR" not in profile

    def test_x86_amd_on_alpine_with_musl(self, tmp_path):
        profile = parse_profile(probe.render_text(py_probe(make_sysroot(tmp_path, "amd"))))
        expect = {"OS_ID": "alpine", "OS_FAMILY": "alpine", "OS_VERSION_ID": "3.20.3", "LIBC": "musl",
                  "INIT_SYSTEM": "openrc", "CPU_VENDOR": "amd", "CPU_CORES_LOGICAL": "16", "CPU_CORES_PHYSICAL": "8",
                  "CPU_MODEL": "AMD Ryzen 7 7840U w/ Radeon 780M Graphics", "SUPPORTS_AVX512": "ON",
                  "SUPPORTS_AVX512_VBMI": "ON", "SUPPORTS_AVX512_VNNI": "ON", "SUPPORTS_AVX512_BF16": "ON",
                  "SUPPORTS_AVX_VNNI": "OFF", "SUPPORTS_AMX_INT8": "OFF", "GPU0_VENDOR": "amd",
                  "GPU0_NAME": "AMD Radeon gfx1103 (integrated)", "GPU0_PCI_ID": "0x1002:0x15bf",
                  "GPU0_DISCRETE": "OFF", "GPU0_UNIFIED": "ON", "GPU0_VRAM_MB": "512", "GPU0_VRAM_FREE_MB": "412",
                  "GPU0_GFX_TARGET": "gfx1103", "GPU0_SOURCE": "kfd", "VULKAN": "OFF", "SUPPORTS_CUDA": "OFF"}
        assert {k: profile.get(k) for k in expect} == expect
        assert "OS_VARIANT_ID" not in profile

    def test_rk3566_on_dietpi(self, tmp_path):
        profile = parse_profile(probe.render_text(py_probe(make_sysroot(tmp_path, "rk3566"))))
        expect = {"OS_ID": "debian", "OS_FAMILY": "debian", "OS_VARIANT_ID": "dietpi", "OS_VERSION_ID": "12",
                  "OS_VERSION_CODENAME": "bookworm", "KERNEL_ARCH": "arm64", "ARCH": "arm64", "LIBC": "glibc",
                  "INIT_SYSTEM": "systemd", "CPU_MODEL": "Cortex-A55", "CPU_VENDOR": "arm", "CPU_CORES_LOGICAL": "4",
                  "CPU_CORES_PHYSICAL": "4", "CPU_CLUSTERS": "0-3:0x41:0xd05:1800", "SUPPORTS_NEON": "ON",
                  "SUPPORTS_DOTPROD": "ON", "SUPPORTS_FP16_ARITH": "ON", "SUPPORTS_I8MM": "OFF", "SUPPORTS_SVE": "OFF",
                  "SUPPORTS_AVX2": "OFF", "SUPPORTS_F16C": "OFF", "SOC_VENDOR": "rockchip", "SOC_MODEL": "rk3566",
                  "BOARD_MODEL": "Radxa ZERO 3W", "NPU_VENDOR": "rockchip", "NPU_MODEL": "rk3566",
                  "NPU_DEVICE": "/dev/rknpu", "SUPPORTS_RKNN": "ON", "GPU_COUNT": "1", "GPU0_VENDOR": "arm",
                  "GPU0_NAME": "Mali (bifrost)", "GPU0_UNIFIED": "ON", "GPU0_SOURCE": "device-tree", "VULKAN": "OFF",
                  "PROBE_WARNINGS": "needs_root:RKNPU_DRIVER_VERSION"}
        assert {k: profile.get(k) for k in expect} == expect
        assert profile.soc_model_known and "CPU_FLAGS_ALL" in profile

    def test_rk3588_with_two_core_types_npu_node_and_hailo(self, tmp_path):
        profile = parse_profile(probe.render_text(py_probe(make_sysroot(tmp_path, "rk3588"))))
        expect = {"OS_VARIANT_ID": "armbian", "INIT_SYSTEM": "busybox", "LIBC": "glibc",
                  "CPU_MODEL": "Cortex-A55+Cortex-A76", "CPU_CORES_LOGICAL": "8",
                  "CPU_CLUSTERS": "0-3:0x41:0xd05:1800,4-7:0x41:0xd0b:2400", "SUPPORTS_DOTPROD": "ON",
                  "SUPPORTS_I8MM": "OFF", "SOC_MODEL": "rk3588", "BOARD_MODEL": "Radxa ROCK 5B",
                  "NPU_VENDOR": "rockchip", "NPU_MODEL": "rk3588", "NPU_DEVICE": "/dev/dri/renderD129",
                  "SUPPORTS_RKNN": "ON", "SUPPORTS_HAILO": "ON", "GPU0_NAME": "Mali (valhall-csf)"}
        assert {k: profile.get(k) for k in expect} == expect

    def test_cluster_entries_split_on_frequency_and_unknown_parts(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3588")
        write(root, "/sys/devices/system/cpu/cpu7/cpufreq/cpuinfo_max_freq", "2256000\n")
        text = (root / "proc/cpuinfo").read_text().replace("CPU part\t: 0xd05", "CPU part\t: 0xd99", 1)
        write(root, "/proc/cpuinfo", text)
        values = py_probe(root)
        assert values["CPU_CLUSTERS"] == ("0:0x41:0xd99:1800,1-3:0x41:0xd05:1800,4-6:0x41:0xd0b:2400,"
                                          "7:0x41:0xd0b:2256")
        assert values["CPU_MODEL"] == "0x41:0xd99+Cortex-A55+Cortex-A76"
        assert "unknown_arm_part" in values["PROBE_WARNINGS"]

    def test_mainline_rockchip_npu_accel_node(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        (root / "dev/rknpu").unlink()
        (root / "sys/bus/platform/drivers/rocket").mkdir(parents=True)
        (root / "sys/class/accel/accel0/device").mkdir(parents=True)
        os.symlink("../../../../bus/platform/drivers/rocket", root / "sys/class/accel/accel0/device/driver")
        values = py_probe(root)
        assert values["NPU_DEVICE"] == "/dev/accel/accel0" and values["SUPPORTS_RKNN"] == "ON"

    def test_no_cpuinfo_flags_means_unknown(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        write(root, "/proc/cpuinfo", re.sub(r"Features\t: .*\n", "", (root / "proc/cpuinfo").read_text()))
        values = py_probe(root)
        assert values["SUPPORTS_DOTPROD"] == "UNKNOWN" and values["SUPPORTS_AVX2"] == "OFF"
        assert "CPU_FLAGS_ALL" not in values

    def test_serial_numbers_and_host_names_never_come_out(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        write(root, "/proc/cpuinfo", (root / "proc/cpuinfo").read_text() +
              f"\nHardware\t: Rockchip RK3566\nSerial\t\t: {MADE_UP_SERIAL}\n")
        text = probe.render_text(py_probe(root))
        assert MADE_UP_SERIAL not in text and MADE_UP_HOST not in text
        assert "HOSTNAME" not in text and "Serial" not in text

    def test_untrusted_os_release_values_are_dropped_or_cleaned(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        write(root, "/etc/os-release", 'ID="debian; rm -rf /"\nVERSION_ID="12$(id)"\n'
                                       'PRETTY_NAME="Debian\tGNU/Linux \x1b[31m12\x07"\nVERSION_CODENAME=bookworm\n')
        values = py_probe(root)
        assert "OS_ID" not in values and "OS_VERSION_ID" not in values
        assert values["OS_PRETTY_NAME"] == "Debian GNU/Linux [31m12"
        assert {"not_determined:OS_ID", "not_determined:OS_VERSION_ID"} <= set(values["PROBE_WARNINGS"].split(","))
        parse_profile(probe.render_text(values))

    def test_fake_gpu_tools_are_merged_with_sysfs(self, tmp_path):
        root = make_sysroot(tmp_path, "intel")
        bindir = tool_dir(tmp_path, fake_gpu_tools=True)
        out = run_emitter([sys.executable, "-I", str(PROBE_PY)], root, bindir, tmp_path)
        profile = parse_profile(out)
        gpus = profile.gpus()
        assert [g["NAME"] for g in gpus] == ["NVIDIA GeForce RTX 3090", "NVIDIA GeForce RTX 2080 Ti",
                                            "Intel(R) UHD Graphics 620 (KBL GT2)"]
        assert gpus[0]["COMPUTE_CAP"] == "8.6" and gpus[0]["PCI_ID"] == "0x10de:0x2204"
        assert gpus[1]["VRAM_MB"] == "11264" and gpus[1]["VRAM_FREE_MB"] == "10850"
        assert gpus[2]["PCI_ID"] == "0x8086:0x5917" and gpus[2]["DRIVER_VERSION"] == "24.0.9"
        assert profile.get("NVIDIA_DRIVER_CUDA_MAX") == "13.0" and profile.get("VULKAN_DEVICES") == "2"
        assert profile.is_on("SUPPORTS_CUDA") and profile.is_on("VULKAN")

    def test_json_view_has_the_same_values(self, tmp_path):
        values = py_probe(make_sysroot(tmp_path, "rk3588"))
        assert parse_profile_json(probe.render_json(values)) == parse_profile(probe.render_text(values))


# --- .sh and .py on the same machines ---------------------------------------------------------------------------

def comparable(text: str):
    profile = parse_profile(text)
    stable = {k: v for k, v in profile.stable().values.items()
              if not (k.startswith("TOOL_") or k in ("GENERATED_AT", "PROBE_TOOL", "PROBE_TOOL_VERSION",
                                                       "PROBE_WARNINGS"))}
    return stable, sorted(profile.warnings), profile.perf_key()


@POSIX_ONLY
@pytest.mark.skipif(not available_shells(), reason="no POSIX shell")
class TestShellEquivalence:

    @pytest.mark.parametrize("shell", available_shells())
    @pytest.mark.parametrize("kind, fake_tools", [("intel", False), ("intel", True), ("amd", False),
                                                  ("rk3566", False), ("rk3588", False)])
    def test_sh_and_py_agree(self, tmp_path, shell, kind, fake_tools):
        root = make_sysroot(tmp_path, kind)
        bindir = tool_dir(tmp_path, fake_gpu_tools=fake_tools)
        sh_text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
        py_text = run_emitter([sys.executable, "-I", str(PROBE_PY)], root, bindir, tmp_path)
        sh_values, sh_warnings, sh_key = comparable(sh_text)
        py_values, py_warnings, py_key = comparable(py_text)
        assert sh_values == py_values
        assert sh_warnings == py_warnings
        assert sh_key == py_key
        assert parse_profile(sh_text).get("PROBE_TOOL") == "sh"

    @pytest.mark.parametrize("shell", available_shells())
    def test_untrusted_values_are_handled_the_same(self, tmp_path, shell):
        root = make_sysroot(tmp_path, "rk3566")
        write(root, "/etc/os-release", 'ID="x debian"\nVERSION_ID="12$(id)"\nID_LIKE="* ../x"\n'
                                       'PRETTY_NAME="Debian\tGNU/Linux \x1b[31m12\x07"\nVARIANT_ID="a;b"\n')
        write(root, "/proc/device-tree/model", b"Radxa \xc3\x84 ZERO\0")
        text = (root / "proc/cpuinfo").read_text().replace("asimddp", "asimddp * ../../etc")
        write(root, "/proc/cpuinfo", text)
        bindir = tool_dir(tmp_path)
        sh_text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
        py_text = run_emitter([sys.executable, "-I", str(PROBE_PY)], root, bindir, tmp_path)
        assert comparable(sh_text) == comparable(py_text)
        assert "$(id)" not in sh_text and "x debian" not in sh_text
        assert parse_profile(sh_text).get("OS_FAMILY") == "other"
        assert parse_profile(sh_text).get("BOARD_MODEL") == "Radxa ZERO"

    def test_sh_reads_no_stdin_and_installs_nothing_by_default(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        bindir = tool_dir(tmp_path)
        read_end, write_end = os.pipe()  # kept open: a read from stdin would block until the timeout
        try:
            for cmd in ([available_shells()[0].split()[0], str(PROBE_SH)], [sys.executable, "-I", str(PROBE_PY)]):
                proc = subprocess.run(cmd + ["--output", "-"], stdin=read_end, capture_output=True, text=True,
                                      timeout=60, check=False,
                                      env={"PATH": str(bindir), "HW_PROBE_SYSROOT": str(root), "HOME": str(tmp_path)})
                assert proc.returncode == 0, proc.stderr
        finally:
            os.close(read_end)
            os.close(write_end)
        text = PROBE_SH.read_text(encoding="utf-8")
        assert re.search(r'\[ "\$INSTALL_TOOLS" = 1 \] && install_tools', text)
        body = text.split("install_tools() {", 1)[1].split("\n}\n", 1)[0]
        code = [ln for ln in text.splitlines() if "sudo" in ln and not ln.lstrip().startswith("#")]
        assert code and all(ln in body for ln in code), "sudo only inside the opt-in install helper"
        reads = [ln for ln in text.replace(body, "").splitlines()
                 if not ln.lstrip().startswith("#") and re.search(r"(^|[;&|]\s*)read\s", ln.strip())]
        assert not reads, "the only read is the opt-in prompt"

    def test_install_tools_without_a_terminal_does_not_ask(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        bindir = tool_dir(tmp_path)
        proc = subprocess.run([available_shells()[0].split()[0], str(PROBE_SH), "--install-tools", "--output", "-"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
                              env={"PATH": str(bindir), "HW_PROBE_SYSROOT": str(root), "HOME": str(tmp_path)})
        assert proc.returncode == 0 and "needs a terminal" in proc.stderr
        assert proc.stdout.startswith("# LLM Cross-Compiler Framework hardware profile")


# --- the probe's copy of the schema ---------------------------------------------------------------------------

def schema_rule(raw, gpu=False):
    kind = raw["type"]
    spec = {"bool": None, "timestamp": None, "enum": tuple(raw.get("values") or ()),
            "int": (raw.get("min"), raw.get("max")), "pattern": raw.get("pattern"),
            "version": raw.get("pattern", RAW_SCHEMA["patterns"]["version"]), "text": raw.get("max_length"),
            "list": (raw.get("item_pattern"), raw.get("max_items"))}[kind]
    required = 1 if (kind == "bool" or raw.get("required")) else 0
    if gpu:
        return kind, spec, required, tuple(raw.get("only_gpu_vendor") or ())
    return kind, spec, required, tuple(raw.get("only_arch") or ()), tuple(raw.get("only_os") or ())


class TestKeyTable:

    def test_key_table_is_the_schema(self):
        expected = {k: schema_rule(v) for k, v in RAW_SCHEMA["keys"].items() if v.get("writer", "probe") == "probe"}
        assert probe.KEYS == expected
        assert probe.GPU_KEYS == {k: schema_rule(v, True) for k, v in RAW_SCHEMA["gpu_keys"].items()}
        assert probe.OS_VALUES == {k: {val: tuple(oses) for val, oses in v["os_values"].items()}
                                   for k, v in RAW_SCHEMA["keys"].items() if v.get("os_values")}
        assert "PROBE_CONVERTED_FROM" not in probe.KEYS  # only the parser writes it

    def test_tables_are_the_schema(self):
        t = RAW_SCHEMA["tables"]
        assert probe.ARCH_ALIASES == t["arch_aliases"]
        assert probe.ARCH_32BIT_USERLAND == t["arch_32bit_userland"]
        assert probe.OS_FAMILIES == {k: tuple(v) for k, v in t["os_families"].items()}
        assert probe.OS_FAMILY_FIXED == t["os_family_fixed"]
        assert dict(probe.OS_VARIANT_MARKERS) == t["os_variant_markers"]
        assert probe.X86_VENDORS == t["x86_vendors"]
        assert probe.ARM_IMPLEMENTERS == t["arm_implementers"]
        assert probe.ARM_PARTS == t["arm_parts"]
        assert probe.PCI_VENDORS == t["pci_vendors"]
        assert probe.SMBIOS_MEMORY_TYPES == t["smbios_memory_types"]
        assert probe.USB_ROCKCHIP_PIDS == t["usb_rockchip_pids"]
        assert set(probe.X86_VENDORS.values()) | set(probe.ARM_IMPLEMENTERS.values()) <= set(
            SCHEMA.keys["CPU_VENDOR"].values)

    def test_cpu_feature_table_is_the_schema(self):
        features = {k: v for k, v in RAW_SCHEMA["keys"].items() if v["group"] in ("x86", "arm")}
        assert set(probe.CPU_FEATURES) == set(features)
        for key, (tokens, any_of, constant, min_build, mac) in probe.CPU_FEATURES.items():
            raw = features[key]
            assert tokens == tuple(raw.get("cpuinfo") or ()), key
            assert any_of == tuple(raw.get("cpuinfo_any") or ()), key
            assert constant == raw.get("windows_feature"), key
            assert min_build == raw.get("windows_min_build"), key
            assert mac == raw.get("macos_sysctl"), key

    def test_python_3_8_syntax(self):
        ast.parse(PROBE_PY.read_text(encoding="utf-8"), feature_version=(3, 8))
        source = PROBE_PY.read_text(encoding="utf-8")
        for newer in ("removeprefix", "removesuffix", "zoneinfo", "functools.cache(", " | None"):
            assert newer not in source, newer
        tree = ast.parse(source)
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
        assert "socket" not in imported and "urllib" not in imported

    def test_no_socket_and_no_stdin(self, tmp_path, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("the probe must not open a socket")

        class NoStdin:
            def read(self, *a):
                raise AssertionError("the probe must not read stdin")
            readline = read

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(sys, "stdin", NoStdin())
        calls = []

        def spy(cmd, **kwargs):
            calls.append((cmd, kwargs))
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(probe.subprocess, "run", spy)
        values = probe.collect(root=str(make_sysroot(tmp_path, "rk3566")))
        assert values["CPU_MODEL"] == "Cortex-A55" and calls
        assert all(kwargs["stdin"] is subprocess.DEVNULL and kwargs.get("timeout") for _c, kwargs in calls)

    def test_command_line(self, tmp_path):
        result = subprocess.run([sys.executable, "-I", str(PROBE_PY), "--output", "-", "--no-toolchain", "--json"],
                                capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL)
        assert result.returncode == 0, result.stderr
        profile = parse_profile_json(result.stdout)
        assert profile.get("PROBE_TOOL") == "python" and not any(k.startswith("TOOL_C") for k in profile.values)
        bad = subprocess.run([sys.executable, "-I", str(PROBE_PY), "--bogus"], capture_output=True, text=True,
                             timeout=30, check=False)
        assert bad.returncode == 2
        target = tmp_path / "out" / "probe.txt"
        target.parent.mkdir()
        ok = subprocess.run([sys.executable, "-I", str(PROBE_PY), "-o", str(target), "--no-toolchain"],
                            capture_output=True, text=True, timeout=120, check=False, cwd=tmp_path)
        assert ok.returncode == 0 and parse_profile(target.read_bytes()).get("PROBE_TOOL") == "python"


# --- the scripts' tables and write calls ----------------------------------------------------------------------

def sh_table(name):
    text = PROBE_SH.read_text(encoding="utf-8")
    body = text.split(f"\n{name}() {{", 1)[1].split("\n}", 1)[0]
    return dict(re.findall(r"^\s+([^)\s*]+)\) echo (\S+) ;;$", body, re.M))


def sh_written_keys():
    text = PROBE_SH.read_text(encoding="utf-8")
    keys = set(re.findall(r"\b(?:emit|put|put_int|put_text|feature) \"?([A-Z][A-Z0-9_]+)\b", text))
    keys |= set(re.findall(r"\b(TOOL_[A-Z]+):[a-z+]+", text))
    gpu = set(re.findall(r"GPU\$\{n\}_([A-Z_]+)=", text))
    return keys, gpu


def ps1_written_keys():
    text = PROBE_PS1.read_text(encoding="utf-8")
    keys = set(re.findall(r"\b(?:Put|Put-Text|Set-Flag) '([A-Z][A-Z0-9_]+)'", text))
    keys |= set(re.findall(r"^\s+'(SUPPORTS_[A-Z0-9_]+)'\s*=\s*@\(", text, re.M))
    keys |= set(re.findall(r"@\('(TOOL_[A-Z]+)', '[a-z-]+'\)", text))
    gpu = set(re.findall(r"Put-Gpu \$index '([A-Z_]+)'", text))
    return keys, gpu


class TestScripts:

    def test_sh_tables_are_the_schema(self):
        t = RAW_SCHEMA["tables"]
        assert sh_table("arch_alias") == t["arch_aliases"]
        assert sh_table("os_family_of") == {i: fam for fam, ids in t["os_families"].items() for i in ids}
        assert sh_table("x86_vendor") == t["x86_vendors"]
        assert sh_table("arm_implementer") == t["arm_implementers"]
        assert sh_table("arm_part") == t["arm_parts"]
        assert sh_table("pci_vendor") == t["pci_vendors"]
        assert sh_table("usb_rockchip_mode") == t["usb_rockchip_pids"]

    @pytest.mark.parametrize("written", [sh_written_keys, ps1_written_keys], ids=["sh", "ps1"])
    def test_scripts_write_schema_keys_and_every_required_key(self, written):
        keys, gpu = written()
        schema_keys = {k for k, v in RAW_SCHEMA["keys"].items() if v.get("writer", "probe") == "probe"}
        assert keys <= schema_keys, keys - schema_keys
        assert gpu <= set(RAW_SCHEMA["gpu_keys"]), gpu - set(RAW_SCHEMA["gpu_keys"])
        required = {k for k in schema_keys if SCHEMA.keys[k].required}
        assert required <= keys, required - keys
        assert {k for k, s in SCHEMA.gpu_keys.items() if s.required} <= gpu

    def test_no_identifying_or_schema_1_keys(self):
        for path in (PROBE_SH, PROBE_PS1, PROBE_PY):
            text = path.read_text(encoding="utf-8")
            for old in ("HOSTNAME", "COMPUTERNAME", "RAM_SLOT_", "PartNumber", "SerialNumber", "CPU_DEVICE_ID",
                        "CPU_ARCH_TYPE", "NPU_STATUS", "DOCKER_VERSION=", "TRUE/FALSE"):
                assert old not in text, (path.name, old)
            assert "PROBE_SCHEMA" in text

    def test_ps1_feature_constants_are_the_schema(self):
        """The old .ps1 asked for constant 40 as AVX-512 (40 is AVX2) and 37 as NEON (SSE4.1): nearly every PC
        claimed AVX-512 and NEON."""
        text = PROBE_PS1.read_text(encoding="utf-8")
        rows = re.findall(r"^\s+'(SUPPORTS_[A-Z0-9_]+)'\s*=\s*@\((\d+|\$null),\s*(\d+|\$null),\s*'(x86|arm)'\)",
                          text, re.M)
        features = {k: v for k, v in RAW_SCHEMA["keys"].items() if v["group"] in ("x86", "arm")}
        assert {k for k, _c, _b, _g in rows} == set(features)
        for key, constant, build, group in rows:
            assert (None if constant == "$null" else int(constant)) == features[key].get("windows_feature"), key
            assert (None if build == "$null" else int(build)) == features[key].get("windows_min_build"), key
            assert group == features[key]["group"], key
        assert features["SUPPORTS_AVX512"]["windows_feature"] == 41 and features["SUPPORTS_NEON"]["windows_feature"] == 29
        assert not re.search(r"PF_AVX512F_INSTRUCTIONS_AVAILABLE\s*=\s*40|PF_ARM_NEON_INSTRUCTIONS_AVAILABLE", text)

    def test_ps1_never_assigns_the_automatic_pid_variable(self):
        text = PROBE_PS1.read_text(encoding="utf-8")
        assert not re.search(r"\$pid\s*=", text, re.I)

    def test_ps1_elevation_is_opt_in(self):
        text = PROBE_PS1.read_text(encoding="utf-8")
        assert re.search(r"\[switch\]\$Elevate", text)
        (line,) = [ln for ln in text.splitlines() if "Start-Process" in ln and "-Verb RunAs" in ln]
        assert " -Wait " in line and " -PassThru " in line and "Exit $elevated.ExitCode" in line
        assert '-OutputFile `"$OutputFile`"' in line
        block = text.split("if ($Elevate -and -not $isAdmin) {", 1)[1]
        assert block.index("Start-Process") < block.index("\n}"), "elevation only inside the -Elevate block"
        assert text.count("Start-Process") == 1 and "Set-ExecutionPolicy" not in text


# --- Windows (with a stand-in for the Windows API) -----------------------------------------------------------

class FakeWindows:
    def __init__(self, build=22631, features=None, machine=0x8664, cpu="  Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz  ",
                 vendor="GenuineIntel"):
        self.build, self.machine = build, machine
        self.features = features if features is not None else {38: True, 39: True, 40: True, 41: False, 60: False}
        self.reg = {"ProcessorNameString": cpu, "VendorIdentifier": vendor, "ProductName": "Windows 10 Pro"}

    def registry(self, path, name):
        return self.reg.get(name)

    def version(self):
        return 10, 0, self.build

    def feature(self, number):
        return self.features.get(number, False)

    def memory(self):
        return 32 * GIB, 20 * GIB, 40 * GIB

    def native_machine(self):
        return self.machine

    def file_version(self, path):
        return "10.0.22621.3672"


def windows_runner(system_answer=None, adapters=None):
    def run(cmd, timeout):
        if cmd[0] == "powershell" and probe.WINDOWS_SYSTEM in cmd:
            return 0, json.dumps(system_answer or {"Probe": "system", "Cores": 6, "MemoryType": 26,
                                                   "MemorySpeed": 2666, "Ids": ["usb:2207:350a", "pci:8086:3e92"],
                                                   "IntelNpu": False}), ""
        if cmd[0] == "powershell":
            return 0, json.dumps(adapters or {"Name": "Intel(R) UHD Graphics 630",
                                              "AdapterCompatibility": "Intel Corporation",
                                              "DriverVersion": "31.0.101.2125", "Memory": 1073741824,
                                              "Pci": "0x8086:0x3e92"}), ""
        return -1, "", "not found"
    return run


def windows_probe(api, run=None):
    return probe.collect(system="Windows", windows_api=api, run=run or windows_runner(), env={}, root=os.sep)


class TestWindows:

    def test_cpu_model_from_the_registry_brand_string(self):
        values = windows_probe(FakeWindows())
        profile = parse_profile(probe.render_text(values))
        assert profile.get("CPU_MODEL") == "Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz"
        expect = {"OS": "windows", "OS_ID": "windows", "OS_FAMILY": "windows", "OS_VERSION_ID": "10.0.22631",
                  "KERNEL": "10.0.22631", "OS_PRETTY_NAME": "Windows 11 Pro", "ARCH": "x86_64", "LIBC": "ucrt",
                  "LIBC_VERSION": "10.0.22621.3672", "INIT_SYSTEM": "scm", "CPU_VENDOR": "intel",
                  "CPU_CORES_PHYSICAL": "6", "CPU_FLAGS_SOURCE": "windows-api", "RAM_TOTAL_MB": "32768",
                  "SWAP_TOTAL_MB": "8192", "RAM_TYPE": "DDR4", "RAM_SPEED_MTS": "2666", "USB_ROCKCHIP_PID": "0x350a",
                  "USB_ROCKCHIP_MODE": "rockusb", "SUPPORTS_RKNN": "OFF", "GPU0_VENDOR": "intel",
                  "GPU0_PCI_ID": "0x8086:0x3e92", "GPU0_DISCRETE": "OFF", "VULKAN": "ON", "HIP_RUNTIME": "OFF"}
        assert {k: profile.get(k) for k in expect} == expect
        assert "CPU_FLAGS_ALL" not in profile

    @pytest.mark.parametrize("build, avx512, bmi2", [(19045, "UNKNOWN", "UNKNOWN"), (22631, "OFF", "UNKNOWN"),
                                                     (26100, "OFF", "OFF")])
    def test_feature_constants_and_build_thresholds(self, build, avx512, bmi2):
        values = windows_probe(FakeWindows(build=build))
        assert (values["SUPPORTS_SSE42"], values["SUPPORTS_AVX"], values["SUPPORTS_AVX2"]) == ("ON", "ON", "ON")
        assert values["SUPPORTS_AVX512"] == avx512 and values["SUPPORTS_BMI2"] == bmi2
        for key in ("SUPPORTS_F16C", "SUPPORTS_FMA", "SUPPORTS_AVX512_VNNI", "SUPPORTS_AVX_VNNI", "SUPPORTS_AMX_TILE"):
            assert values[key] == "UNKNOWN", key  # Windows has no constant for these
        assert values["SUPPORTS_NEON"] == "OFF" and values["SUPPORTS_DOTPROD"] == "OFF"

    def test_the_old_wrong_constants_cannot_claim_avx512(self):
        # An AVX2 PC: constant 40 (AVX2) is TRUE, 41 (AVX-512 F) FALSE. The old script read 40 as AVX-512.
        values = windows_probe(FakeWindows(features={38: True, 39: True, 40: True, 37: True}))
        assert values["SUPPORTS_AVX512"] == "OFF" and values["SUPPORTS_NEON"] == "OFF"

    def test_windows_on_arm(self):
        api = FakeWindows(machine=0xAA64, cpu="Snapdragon(R) X Elite - X1E78100 - Qualcomm(R) Oryon(TM) CPU",
                          vendor="Qualcomm Technologies Inc", build=26100,
                          features={29: True, 43: True, 66: False, 67: True, 46: False})
        values = windows_probe(api)
        assert values["ARCH"] == "arm64" and "CPU_VENDOR" not in values
        assert (values["SUPPORTS_NEON"], values["SUPPORTS_DOTPROD"], values["SUPPORTS_FP16_ARITH"]) == ("ON",) * 3
        assert values["SUPPORTS_I8MM"] == "OFF" and values["SUPPORTS_SVE"] == "OFF"
        assert values["SUPPORTS_BF16_ARM"] == "UNKNOWN" and values["SUPPORTS_AVX2"] == "OFF"
        parse_profile(probe.render_text(values))

    def test_feature_value_rule(self):
        ask = {39: True, 41: False}.get
        assert probe.windows_feature_value("x86_64", "SUPPORTS_AVX", 39, 22000, 19045, ask) == "ON"
        assert probe.windows_feature_value("x86_64", "SUPPORTS_AVX512", 41, 22000, 19045, ask) == "UNKNOWN"
        assert probe.windows_feature_value("x86_64", "SUPPORTS_AVX512", 41, 22000, 22000, ask) == "OFF"
        assert probe.windows_feature_value("x86_64", "SUPPORTS_FMA", None, None, 26100, ask) == "UNKNOWN"
        assert probe.windows_feature_value("arm64", "SUPPORTS_AVX", 39, 22000, 26100, ask) == "OFF"
        assert probe.windows_feature_value("x86_64", "SUPPORTS_AVX2", 40, 22000, 26100, lambda n: None) == "UNKNOWN"

    def test_powershell_queries_send_no_identifiers(self):
        for script in (probe.WINDOWS_SYSTEM, probe.WINDOWS_ADAPTERS):
            assert "SerialNumber" not in script and "PartNumber" not in script and "COMPUTERNAME" not in script
        parsed = probe.parse_windows_system(json.dumps({"Probe": "system", "Ids": ["usb:2207:350a", "USB\\VID_1"]}))
        assert parsed["ids"] == ["usb:2207:350a"]


# --- the probe on a device over SSH ---------------------------------------------------------------------------

class FakeChannel:
    def __init__(self, owner):
        self.owner = owner
        self.write_closed = False

    def shutdown_write(self):
        self.write_closed = True

    def recv_exit_status(self):
        return self.owner.status

    def close(self):
        self.owner.closed = True


class FakeStdin:
    def __init__(self, owner):
        self.owner, self.channel = owner, owner.channel

    def write(self, data):
        self.owner.sent += data

    def flush(self):
        pass


class FakeStdout:
    """Runs the command when its output is first read (after the script arrived on stdin)."""

    def __init__(self, owner):
        self.owner, self.channel = owner, owner.channel
        self.buffer = None

    def read(self, size=-1):
        if self.buffer is None:
            assert self.channel.write_closed, "stdin must be closed before the output is read"
            self.owner.status, data = self.owner.client.answer(self.owner.command, self.owner.sent)
            self.buffer = io.BytesIO(data)
        return self.buffer.read(size)


class FakeExec:
    def __init__(self, client, command):
        self.client, self.command = client, command
        self.sent, self.status, self.closed = b"", None, False
        self.channel = FakeChannel(self)


class FakeDevice:
    """An SSH client stand-in for a device: python3 of a given version (or none) and a fixture sysroot. The script
    that arrives on stdin is really run (python3 -I - / sh -s) against that sysroot."""

    def __init__(self, root, bindir, python="3.11", python_fails=False, override=None):
        self.root, self.bindir, self.python, self.python_fails, self.override = root, bindir, python, python_fails, \
            override
        self.commands, self.sent = [], {}

    def exec_command(self, command, timeout=None):
        assert timeout, "every remote command has a time limit"
        self.commands.append(command)
        job = FakeExec(self, command)
        return FakeStdin(job), FakeStdout(job), io.BytesIO(b"")

    def answer(self, command, stdin_data):
        from orchestrator.runtime import remote_probe as rp
        self.sent[command] = stdin_data
        if self.override and command in self.override:
            return self.override[command]
        if command == rp.PYTHON_CHECK:
            return (0, (self.python + "\n").encode()) if self.python else (1, b"")
        env = {"PATH": str(self.bindir), "HW_PROBE_SYSROOT": str(self.root), "HOME": str(self.root)}
        if command == rp.PYTHON_COMMAND:
            if self.python_fails:
                return 1, b""
            cmd = [sys.executable, "-I", "-", "--output", "-"]
        elif command == rp.SHELL_COMMAND:
            cmd = [available_shells()[0].split()[0], "-s", "--", "--output", "-"]
        else:
            raise AssertionError(f"unexpected command {command!r}")
        result = subprocess.run(cmd, input=stdin_data, env=env, capture_output=True, timeout=120, check=False)
        return result.returncode, result.stdout


@POSIX_ONLY
@pytest.mark.skipif(not available_shells(), reason="no POSIX shell")
class TestRemoteProbe:

    def device(self, tmp_path, **options):
        return FakeDevice(make_sysroot(tmp_path, "rk3566"), tool_dir(tmp_path), **options)

    def test_python_probe_when_the_device_has_python_3_8_or_newer(self, tmp_path):
        from orchestrator.runtime import remote_probe as rp
        dev = self.device(tmp_path)
        result = rp.probe_over_ssh(dev)
        assert result.tool == "python" and not result.notes
        assert result.profile.get("PROBE_TOOL") == "python" and result.profile.get("SOC_MODEL") == "rk3566"
        assert dev.commands == [rp.PYTHON_CHECK, rp.PYTHON_COMMAND]
        assert dev.sent[rp.PYTHON_COMMAND] == PROBE_PY.read_bytes()

    @pytest.mark.parametrize("python, note", [("3.6", "older than 3.8"), (None, "no python3")])
    def test_shell_probe_without_a_usable_python(self, tmp_path, python, note):
        from orchestrator.runtime import remote_probe as rp
        dev = self.device(tmp_path, python=python)
        result = rp.probe_over_ssh(dev)
        assert result.tool == "sh" and note in result.notes[0]
        assert result.profile.get("PROBE_TOOL") == "sh" and result.profile.get("CPU_MODEL") == "Cortex-A55"
        assert dev.commands == [rp.PYTHON_CHECK, rp.SHELL_COMMAND]
        assert dev.sent[rp.SHELL_COMMAND] == PROBE_SH.read_bytes()

    def test_a_failing_python_probe_falls_back_to_the_shell_probe(self, tmp_path):
        from orchestrator.runtime import remote_probe as rp
        result = rp.probe_over_ssh(self.device(tmp_path, python_fails=True))
        assert result.tool == "sh" and "exited with status 1" in result.notes[0]

    def test_device_output_is_untrusted(self, tmp_path):
        from orchestrator.runtime import remote_probe as rp
        hostile = b"PROBE_SCHEMA=2\nPROBE_TOOL=sh\nNPU_DEVICE=/dev/sda\n"
        cases = (((0, hostile), "cannot read"), ((0, b"x" * 200000), "more than"), ((0, b"\x00\xff garbage"), "cannot read"))
        for index, (answer, message) in enumerate(cases):
            dev = self.device(tmp_path / str(index), python=None, override={rp.SHELL_COMMAND: answer})
            with pytest.raises(rp.RemoteProbeError, match=message):
                rp.probe_over_ssh(dev)

    def test_timeouts_and_lost_connections_are_errors(self):
        from orchestrator.runtime import remote_probe as rp

        class Hanging:
            def exec_command(self, command, timeout=None):
                raise socket.timeout("timed out")

        with pytest.raises(rp.RemoteProbeError, match="no answer"):
            rp.probe_over_ssh(Hanging())

    def test_commands_are_fixed_strings(self):
        from orchestrator.runtime import remote_probe as rp
        for command in (rp.PYTHON_CHECK, rp.PYTHON_COMMAND, rp.SHELL_COMMAND):
            assert "{" not in command and "%s" not in command.replace('"%d.%d"', "")
        assert rp.PYTHON_COMMAND.startswith("python3 -I - ") and rp.SHELL_COMMAND.startswith("sh -s -- ")

    @pytest.mark.parametrize("host, ok", [("192.0.2.10", True), ("board.local", True), ("fe80::1", True),
                                          ("-oProxyCommand=x", False), ("a;b", False), ("", False),
                                          ("host name", False)])
    def test_hosts(self, host, ok):
        from orchestrator.runtime.remote_probe import valid_host
        assert valid_host(host) is ok

    def test_store_address(self):
        from orchestrator.runtime.remote_probe import store_address
        assert store_address("192.0.2.10", 22) == "192.0.2.10"
        assert store_address("192.0.2.10", 2222) == "192.0.2.10:2222"
        assert store_address("fe80::1", 2222) == "[fe80::1]:2222"


class FakeHostKey:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"made-up host key of a fixture device"


@POSIX_ONLY
class TestScanDevice:

    @pytest.fixture(autouse=True)
    def keep_logger_levels(self):
        """`llm-cli -q` sets every existing logger to ERROR (set_global_level); later tests read WARNING logs."""
        import logging
        saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
        root = logging.getLogger().level
        yield
        for name in list(logging.Logger.manager.loggerDict):
            logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
        logging.getLogger().setLevel(root)

    def fake_ssh(self, monkeypatch, status="unknown"):
        from orchestrator.utils import ssh_trust
        trusted, connected = [], []

        class Client:
            def close(self):
                connected.append("closed")

        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: FakeHostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: status)
        monkeypatch.setattr(ssh_trust, "trust_host_key", lambda state, host, port, key: trusted.append((host, port)))
        monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: (
            connected.append((host, user, port, password)) or Client()))
        monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line: connected.append("paired"))
        return trusted, connected

    def test_scan_device_stores_the_profile_by_host_key(self, tmp_path, monkeypatch):
        from orchestrator.runtime import remote_probe as rp
        from orchestrator.runtime.profile import ProfileStore, device_id_for_host_key, parse_profile
        from orchestrator.utils import ssh_trust
        from test_cli_builds import invoke_plain, state_dir
        trusted, connected = self.fake_ssh(monkeypatch)
        board = parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text())
        monkeypatch.setattr(rp, "probe_over_ssh", lambda client: rp.RemoteProbeResult(board, "sh", ["no python3"]))
        fp = ssh_trust.fingerprint(FakeHostKey())
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--device", "192.0.2.10", "--user", "dietpi", "--trust-fingerprint", fp,
                              "--label", "ZERO 3W", "--output", "board.txt")
        assert result.code == 0, result.out
        assert trusted == [("192.0.2.10", 22)] and connected[0] == ("192.0.2.10", "dietpi", 22, None)
        assert connected[-1] == "closed" and "no python3" in result.out
        store = ProfileStore(state_dir())
        device_id = device_id_for_host_key(fp)
        assert store.list_devices() == [device_id]
        assert store.load_device(device_id) == board.stable()
        assert store.device_info(device_id)["label"] == "ZERO 3W"
        assert store.device_info(device_id)["address"] == "192.0.2.10"
        assert parse_profile((tmp_path / "board.txt").read_text()) == board

    def test_an_unknown_device_is_not_trusted_without_a_confirmation(self, tmp_path, monkeypatch):
        from test_cli_builds import invoke_plain
        trusted, connected = self.fake_ssh(monkeypatch)
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--device", "192.0.2.10", "--user", "dietpi")
        assert result.code == 1 and "not trusted" in result.out
        assert trusted == [] and connected == []
        wrong = invoke_plain("scan", "--device", "192.0.2.10", "--user", "dietpi", "--trust-fingerprint",
                             "SHA256:" + "A" * 43)
        assert wrong.code == 1 and "does not match" in wrong.out

    def test_a_changed_host_key_stops(self, tmp_path, monkeypatch):
        from test_cli_builds import invoke_plain
        _trusted, connected = self.fake_ssh(monkeypatch, status="changed")
        result = invoke_plain("scan", "--device", "192.0.2.10", "--user", "dietpi")
        assert result.code == 1 and "CHANGED" in result.out and connected == []

    @pytest.mark.parametrize("args", [["--device", "192.0.2.10"], ["--device", "-oProxyCommand=x", "--user", "u"],
                                      ["--device", "192.0.2.10", "--user", "u", "--port", "0"]])
    def test_bad_arguments(self, args):
        from test_cli_builds import invoke_plain
        assert invoke_plain("scan", *args).code == 2


@pytest.mark.skipif(os.name != "nt", reason="needs Windows (the windows-latest CI job)")
def test_ps1_and_py_agree_on_windows(tmp_path):
    ps1_out, py_out = tmp_path / "ps1.txt", tmp_path / "py.txt"
    ps1 = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                          str(PROBE_PS1), "-OutputFile", str(ps1_out), "-NoToolchain"], capture_output=True, text=True,
                         timeout=300, check=False, stdin=subprocess.DEVNULL)
    assert ps1.returncode == 0, ps1.stderr
    py = subprocess.run([sys.executable, "-I", str(PROBE_PY), "--output", str(py_out), "--no-toolchain"],
                        capture_output=True, text=True, timeout=300, check=False, stdin=subprocess.DEVNULL)
    assert py.returncode == 0, py.stderr
    ps1_values, ps1_warnings, ps1_key = comparable(ps1_out.read_text(encoding="utf-8"))
    py_values, py_warnings, py_key = comparable(py_out.read_text(encoding="utf-8"))
    assert ps1_values == py_values
    assert ps1_warnings == py_warnings and ps1_key == py_key


def test_module_wizard_reads_the_schema_2_architecture(qapp, tmp_path, monkeypatch):
    """The wizard's architecture list uses uname names (aarch64, armv7l); schema 2 writes arm64 and armv7."""
    from orchestrator.gui.wizards import ModuleCreationWizard
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target_hardware_config.txt").write_text((FIXTURES / "schema2_rk3566_dietpi.txt").read_text())
    wizard = ModuleCreationWizard(tmp_path / "targets")
    try:
        wizard.arch_combo.setCurrentText("riscv64")
        wizard.detect_cb.setChecked(True)
        assert wizard.arch_combo.currentText() == "aarch64"
    finally:
        wizard.close()


# --- the runtime's hardware is built on the profile -----------------------------------------------------------

class TestRuntimeHardware:

    def test_a_stored_device_profile_becomes_hardware_info(self):
        board = parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text())
        info = hardware.hardware_from_profile(board)
        assert (info.os, info.arch) == ("linux", "arm64")
        assert info.ram_total == 3912 * 1024 * 1024 and info.ram_available == 3301 * 1024 * 1024
        assert [(g.vendor, g.name, g.unified) for g in info.gpus] == [("other", "Mali-G52", True)]
        assert info.vulkan is False and info.hip_runtime is False
        stored = hardware.hardware_from_profile(board.stable())  # no volatile values: all RAM counts as free
        assert stored.ram_available == stored.ram_total

    def test_an_nvidia_card_without_driver_is_not_offered(self, tmp_path):
        root = tmp_path / "sys"
        dev = root / "class" / "drm" / "card0" / "device"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text("0x10de\n")
        (dev / "device").write_text("0x2204\n")
        values = probe.collect(root=os.sep, sys_dir=str(root), run=no_tools, system="Linux", machine="x86_64",
                               memory=(16 * GIB, 8 * GIB), find_library=lambda n: None, rocm_globs=(),
                               sections=("memory", "gpu"))
        profile = parse_profile(probe.render_text(values))
        assert profile.get("GPU0_SOURCE") == "sysfs" and profile.is_on("SUPPORTS_CUDA")
        assert hardware.hardware_from_profile(profile).gpus == []  # no nvidia-smi: no driver to run a GPU build

    def test_detect_hardware_survives_a_missing_required_key(self, monkeypatch):
        real = probe.collect

        def collect(**options):
            values = real(**options)
            values.pop("KERNEL")
            return values

        monkeypatch.setattr(probe, "collect", collect)
        info = hardware.detect_hardware(no_tools, system="Linux", machine="x86_64", memory=(8 * GIB, 4 * GIB),
                                        find_library=lambda n: None, rocm_globs=())
        assert (info.os, info.arch, info.ram_total, info.ram_available) == ("linux", "x86_64", 8 * GIB, 4 * GIB)

    def test_free_memory_is_read_at_every_start(self, tmp_path, monkeypatch):
        from orchestrator.runtime import llama_server
        calls = []
        monkeypatch.setattr(llama_server, "detect_hardware", lambda run: calls.append(run) or hardware.HardwareInfo(
            os="linux", arch="x86_64", ram_total=8 * GIB, ram_available=(4 + len(calls)) * GIB))
        rt = llama_server.LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models")
        assert rt.hardware().ram_available == 5 * GIB and rt.hardware().ram_available == 5 * GIB
        assert not rt._hardware_given and rt.hardware(refresh=True).ram_available == 6 * GIB
        given = llama_server.LocalLLMRuntime(data_dir=tmp_path / "s2", models_dir=tmp_path / "m2",
                                             hardware=rt.hardware())
        assert given._hardware_given
