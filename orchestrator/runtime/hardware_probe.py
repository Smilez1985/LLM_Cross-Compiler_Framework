#!/usr/bin/env python3
"""
hardware_probe.py - hardware probe of the LLM Cross-Compiler Framework (main path, schema 2).

Run it on the machine that will run the binaries, this PC or a board:

    python3 -I hardware_probe.py [--output FILE | --output -] [--json] [--no-toolchain] [--disk-path DIR]

It writes target_hardware_config.txt: KEY=VALUE lines starting with PROBE_SCHEMA=2, exactly the keys of
configs/hardware_probe_schema.yml in the framework. --json writes the same keys and string values as one JSON object.
scripts/hardware_probe.sh (Linux, any POSIX shell) and scripts/hardware_probe.ps1 (Windows) write the same keys where
Python is not available; the three are kept in step by the framework's tests.

Rules:
- Standard library only, Python 3.8 syntax. Linux, Windows and macOS; FreeBSD and Android on a best-effort basis.
- No network, no prompts, no stdin, no sudo, no elevation, no package installs. External tools (nvidia-smi,
  vulkaninfo, compilers, PowerShell on Windows) run with a time limit and an empty stdin; a missing tool contributes
  nothing.
- Every value is checked against KEYS / GPU_KEYS below (the validation of the schema; a test compares them) before it
  is written; a value that fails is left out with a warning. Free text is printable ASCII, whitespace collapsed.
- No host name, user name, path, serial number, MAC address or memory part number is ever read into the result.

HW_PROBE_SYSROOT (for tests): a folder that stands in for / when files are read (/proc, /sys, /etc, /dev, /run, /lib,
/opt, /boot). The kernel release and architecture then come from <sysroot>/proc/sys/kernel/osrelease and .../arch,
and facts that only a running program of the host could tell (the glibc version through confstr, find_library) are
not taken from the host.

The framework imports this file in-process (collect(), render_text(), render_json()); orchestrator/runtime/hardware.py
uses its GPU parsers. orchestrator/runtime/hardware_probe.py is a byte-identical copy of this file, so the probe is
also part of the installed package (a wheel has no scripts/ folder). Edit this file, then copy it there; a test fails
while the two differ.
"""

import argparse
import glob
import json
import os
import platform
import re
import shutil
import struct
import subprocess  # runs local probe tools with fixed argument lists, never through a shell
import sys
from datetime import datetime, timezone

TOOL = "python"
TOOL_VERSION = "3.0.0"
SCHEMA = "2"
FILE_NAME = "target_hardware_config.txt"
MAX_VALUE = 4096
MAX_GPUS = 16
BOOLS = ("ON", "OFF", "UNKNOWN")
MIB = 1024 * 1024
GIB = 1024 * MIB
SECTIONS = ("cpu", "memory", "soc", "npu", "gpu", "toolchain")

# --- key table: type, rule, required, only_arch, only_os (a copy of the schema's validation) -----------------------

_VERSION = r"^\d{1,5}(\.\d{1,6}){0,3}$"
_IDENT = "^[a-z0-9][a-z0-9._-]{0,31}$"
_DOTTED = r"^\d{1,3}(\.\d{1,4}){1,3}$"
_TOOLCHAIN = r"^(gcc|clang|appleclang|msvc|icx)-\d{1,4}(\.\d{1,6}){0,3}$"
_ARCHES = ("x86_64", "x86", "arm64", "armv7", "armv6", "riscv64", "ppc64le", "s390x", "loongarch64", "other")
_X86 = ("x86_64", "x86")
_ARM = ("arm64", "armv7", "armv6")

KEYS = {
    "PROBE_SCHEMA": ("enum", ("2",), 1, (), ()),
    "PROBE_TOOL": ("enum", ("python", "sh", "ps1"), 1, (), ()),
    "PROBE_TOOL_VERSION": ("version", _VERSION, 1, (), ()),
    "GENERATED_AT": ("timestamp", None, 1, (), ()),
    "PROBE_WARNINGS": ("list", ("^[a-z][a-z0-9_]{0,47}(:[A-Z][A-Z0-9_]{0,63})?$", 128), 0, (), ()),
    "OS": ("enum", ("linux", "windows", "macos", "freebsd", "android"), 1, (), ()),
    "OS_ID": ("pattern", _IDENT, 0, (), ()),
    "OS_ID_LIKE": ("list", (_IDENT, 16), 0, (), ()),
    "OS_FAMILY": ("enum", ("debian", "rhel", "fedora", "suse", "arch", "alpine", "gentoo", "openwrt", "void", "nixos",
                           "slackware", "other", "windows", "macos", "bsd", "android"), 0, (), ()),
    "OS_VARIANT_ID": ("pattern", _IDENT, 0, (), ()),
    "OS_VERSION_ID": ("pattern", "^[A-Za-z0-9][A-Za-z0-9._~-]{0,31}$", 0, (), ()),
    "OS_VERSION_CODENAME": ("pattern", _IDENT, 0, (), ()),
    "OS_PRETTY_NAME": ("text", 128, 0, (), ()),
    "KERNEL": ("pattern", "^[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}$", 1, (), ()),
    "KERNEL_ARCH": ("enum", _ARCHES, 0, (), ()),
    "ARCH": ("enum", _ARCHES, 1, (), ()),
    "LIBC": ("enum", ("glibc", "musl", "uclibc", "bionic", "ucrt", "libsystem", "bsdlibc"), 0, (), ()),
    "LIBC_VERSION": ("version", r"^\d{1,3}(\.\d{1,5}){1,3}$", 0, (), ()),
    "INIT_SYSTEM": ("enum", ("systemd", "openrc", "sysvinit", "busybox", "runit", "s6", "procd", "launchd", "scm",
                             "bsdinit", "other"), 0, (), ()),
    "CPU_MODEL": ("text", 128, 0, (), ()),
    "CPU_VENDOR": ("enum", ("intel", "amd", "hygon", "centaur", "zhaoxin", "arm", "broadcom", "cavium", "fujitsu",
                            "hisilicon", "nvidia", "apm", "qualcomm", "samsung", "marvell", "apple", "faraday",
                            "microsoft", "phytium", "ampere", "other"), 0, (), ()),
    "CPU_CORES_LOGICAL": ("int", (1, 4096), 1, (), ()),
    "CPU_CORES_PHYSICAL": ("int", (1, 4096), 0, (), ()),
    "CPU_CLUSTERS": ("list", (r"^\d{1,4}(-\d{1,4})?:0x[0-9a-f]{2}:0x[0-9a-f]{3}(:\d{1,5})?$", 64), 0, _ARM, ()),
    "CPU_FLAGS_ALL": ("list", ("^[a-z0-9_.]{1,48}$", 1024), 0, (), ()),
    "CPU_FLAGS_SOURCE": ("enum", ("proc-cpuinfo", "sysctl", "windows-api"), 0, (), ()),
    "SUPPORTS_SSE42": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX2": ("bool", None, 1, _X86, ()),
    "SUPPORTS_BMI2": ("bool", None, 1, _X86, ()),
    "SUPPORTS_F16C": ("bool", None, 1, _X86, ()),
    "SUPPORTS_FMA": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX512": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX512_VBMI": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX512_VNNI": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX512_BF16": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AVX_VNNI": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AMX_TILE": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AMX_INT8": ("bool", None, 1, _X86, ()),
    "SUPPORTS_AMX_BF16": ("bool", None, 1, _X86, ()),
    "SUPPORTS_NEON": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_DOTPROD": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_FP16_ARITH": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_I8MM": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_SVE": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_SVE2": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_SME": ("bool", None, 1, _ARM, ()),
    "SUPPORTS_BF16_ARM": ("bool", None, 1, _ARM, ()),
    "RAM_TOTAL_MB": ("int", (16, 16777216), 1, (), ()),
    "SWAP_TOTAL_MB": ("int", (0, 16777216), 0, (), ()),
    "RAM_AVAILABLE_MB": ("int", (0, 16777216), 0, (), ()),
    "DISK_FREE_MB": ("int", (0, 2147483647), 0, (), ()),
    "RAM_TYPE": ("enum", ("DDR3", "DDR4", "DDR5", "LPDDR3", "LPDDR4", "LPDDR4X", "LPDDR5", "LPDDR5X"), 0, (), ()),
    "RAM_SPEED_MTS": ("int", (100, 20000), 0, (), ()),
    "SOC_VENDOR": ("pattern", "^[a-z0-9][a-z0-9-]{0,31}$", 0, (), ()),
    "SOC_MODEL": ("pattern", _IDENT, 0, (), ()),
    "BOARD_MODEL": ("text", 128, 0, (), ()),
    "NPU_VENDOR": ("enum", ("rockchip", "hailo", "axelera", "memryx", "google", "intel", "qualcomm", "apple", "amd",
                            "other"), 0, (), ()),
    "NPU_MODEL": ("pattern", _IDENT, 0, (), ()),
    "NPU_DEVICE": ("pattern", "^/dev/(rknpu|dri/(renderD|card)[0-9]{1,3}|accel/accel[0-9]{1,3}|hailo[0-9]{1,3}|"
                              "apex_[0-9]{1,3})$", 0, (), ()),
    "RKNPU_DRIVER_VERSION": ("pattern", _DOTTED, 0, (), ("linux", "android")),
    "RKLLM_RUNTIME_VERSION": ("pattern", _DOTTED, 0, (), ("linux", "android")),
    "RKNN_RUNTIME_VERSION": ("pattern", _DOTTED, 0, (), ("linux", "android")),
    "USB_ROCKCHIP_PID": ("pattern", "^0x[0-9a-f]{4}$", 0, (), ()),
    "USB_ROCKCHIP_MODE": ("enum", ("rockusb", "adb", "adb-mtp", "other"), 0, (), ()),
    "SUPPORTS_RKNN": ("bool", None, 1, (), ("linux", "android")),
    "SUPPORTS_HAILO": ("bool", None, 1, (), ()),
    "SUPPORTS_METIS": ("bool", None, 1, (), ()),
    "SUPPORTS_MX3": ("bool", None, 1, (), ()),
    "SUPPORTS_CORAL": ("bool", None, 1, (), ()),
    "SUPPORTS_INTEL_NPU": ("bool", None, 1, (), ()),
    "GPU_COUNT": ("int", (0, 16), 1, (), ()),
    "NVIDIA_DRIVER_CUDA_MAX": ("pattern", r"^\d{1,2}\.\d{1,3}$", 0, (), ()),
    "SUPPORTS_CUDA": ("bool", None, 1, (), ()),
    "SUPPORTS_INTEL_XPU": ("bool", None, 1, (), ()),
    "VULKAN": ("bool", None, 1, (), ()),
    "VULKAN_DEVICES": ("int", (0, 64), 0, (), ()),
    "HIP_RUNTIME": ("bool", None, 1, (), ("linux",)),
    "ROCM_VERSION": ("version", _VERSION, 0, (), ("linux",)),
    "METAL": ("bool", None, 1, (), ("macos",)),
    "TOOL_CC": ("pattern", _TOOLCHAIN, 0, (), ()),
    "TOOL_CXX": ("pattern", _TOOLCHAIN, 0, (), ()),
    "TOOL_CLANG": ("version", _VERSION, 0, (), ()),
    "TOOL_MSVC": ("version", _VERSION, 0, (), ()),
    "TOOL_CMAKE": ("version", _VERSION, 0, (), ()),
    "TOOL_NINJA": ("version", _VERSION, 0, (), ()),
    "TOOL_MAKE": ("version", _VERSION, 0, (), ()),
    "TOOL_GIT": ("version", _VERSION, 0, (), ()),
    "TOOL_CCACHE": ("version", _VERSION, 0, (), ()),
    "TOOL_NVCC": ("version", _VERSION, 0, (), ()),
    "TOOL_GLSLC": ("version", _VERSION, 0, (), ()),
    "TOOL_HIPCONFIG": ("version", _VERSION, 0, (), ("linux",)),
    "TOOL_DOCKER": ("version", _VERSION, 0, (), ()),
    "TOOL_PYTHON": ("version", _VERSION, 0, (), ()),
}
# Per-GPU keys (GPU<n>_<NAME>): type, rule, required, only_gpu_vendor.
GPU_KEYS = {
    "VENDOR": ("enum", ("nvidia", "amd", "intel", "apple", "arm", "qualcomm", "imagination", "broadcom", "other"), 1,
               ()),
    "NAME": ("text", 128, 0, ()),
    "PCI_ID": ("pattern", "^0x[0-9a-f]{4}:0x[0-9a-f]{4}$", 0, ()),
    "DISCRETE": ("bool", None, 1, ()),
    "UNIFIED": ("bool", None, 1, ()),
    "VRAM_MB": ("int", (0, 16777216), 0, ()),
    "VRAM_FREE_MB": ("int", (0, 16777216), 0, ()),
    "DRIVER_VERSION": ("version", _VERSION, 0, ()),
    "COMPUTE_CAP": ("pattern", r"^\d{1,2}\.\d$", 0, ("nvidia",)),
    "GFX_TARGET": ("pattern", "^gfx[0-9a-f]{3,5}$", 0, ("amd",)),
    "SOURCE": ("enum", ("nvidia-smi", "sysfs", "kfd", "vulkaninfo", "wmi", "registry", "system-profiler",
                        "device-tree"), 0, ()),
}
# Values that exist only on some OS values.
OS_VALUES = {
    "OS_FAMILY": {"windows": ("windows",), "macos": ("macos",), "bsd": ("freebsd",), "android": ("android",)},
    "LIBC": {"glibc": ("linux",), "musl": ("linux",), "uclibc": ("linux",), "bionic": ("android", "linux"),
             "ucrt": ("windows",), "libsystem": ("macos",), "bsdlibc": ("freebsd",)},
    "INIT_SYSTEM": {"systemd": ("linux",), "openrc": ("linux",), "sysvinit": ("linux",), "busybox": ("linux",),
                    "runit": ("linux",), "s6": ("linux",), "procd": ("linux",), "launchd": ("macos",),
                    "scm": ("windows",), "bsdinit": ("freebsd",)},
}

# --- tables (copies of configs/hardware_probe_schema.yml tables; a test compares them) -----------------------------

ARCH_ALIASES = {
    "x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64", "x86-64": "x86_64", "x86": "x86", "i386": "x86",
    "i486": "x86", "i586": "x86", "i686": "x86", "arm64": "arm64", "aarch64": "arm64", "arm64e": "arm64",
    "armv8l": "armv7", "armv7": "armv7", "armv7l": "armv7", "armv7hl": "armv7", "armhf": "armv7", "armv6": "armv6",
    "armv6l": "armv6", "riscv64": "riscv64", "ppc64le": "ppc64le", "s390x": "s390x", "loongarch64": "loongarch64",
}
ARCH_32BIT_USERLAND = {"x86_64": "x86", "arm64": "armv7"}
OS_FAMILIES = {
    "debian": ("debian", "ubuntu", "raspbian", "linuxmint", "pop", "elementary", "kali", "devuan", "armbian",
               "dietpi", "zorin", "neon", "pureos", "deepin", "mx"),
    "rhel": ("rhel", "centos", "rocky", "almalinux", "ol", "amzn", "scientific", "eurolinux", "cloudlinux",
             "virtuozzo"),
    "fedora": ("fedora",),
    "suse": ("suse", "opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "sled", "sle-micro"),
    "arch": ("arch", "archarm", "manjaro", "manjaro-arm", "endeavouros", "garuda", "artix"),
    "alpine": ("alpine", "postmarketos"),
    "gentoo": ("gentoo", "funtoo"),
    "openwrt": ("openwrt",),
    "void": ("void",),
    "nixos": ("nixos",),
    "slackware": ("slackware",),
}
OS_FAMILY_FIXED = {"windows": "windows", "macos": "macos", "freebsd": "bsd", "android": "android"}
OS_VARIANT_MARKERS = (("/boot/dietpi/.version", "dietpi"), ("/etc/armbian-release", "armbian"))
X86_VENDORS = {"GenuineIntel": "intel", "AuthenticAMD": "amd", "HygonGenuine": "hygon", "CentaurHauls": "centaur",
               "Shanghai": "zhaoxin"}
ARM_IMPLEMENTERS = {
    "0x41": "arm", "0x42": "broadcom", "0x43": "cavium", "0x46": "fujitsu", "0x48": "hisilicon", "0x4e": "nvidia",
    "0x50": "apm", "0x51": "qualcomm", "0x53": "samsung", "0x56": "marvell", "0x61": "apple", "0x66": "faraday",
    "0x69": "intel", "0x6d": "microsoft", "0x70": "phytium", "0xc0": "ampere",
}
ARM_PARTS = {
    "0x41:0xb76": "ARM1176", "0x41:0xc05": "Cortex-A5", "0x41:0xc07": "Cortex-A7", "0x41:0xc08": "Cortex-A8",
    "0x41:0xc09": "Cortex-A9", "0x41:0xc0d": "Cortex-A17", "0x41:0xc0f": "Cortex-A15", "0x41:0xd01": "Cortex-A32",
    "0x41:0xd02": "Cortex-A34", "0x41:0xd03": "Cortex-A53", "0x41:0xd04": "Cortex-A35", "0x41:0xd05": "Cortex-A55",
    "0x41:0xd06": "Cortex-A65", "0x41:0xd07": "Cortex-A57", "0x41:0xd08": "Cortex-A72", "0x41:0xd09": "Cortex-A73",
    "0x41:0xd0a": "Cortex-A75", "0x41:0xd0b": "Cortex-A76", "0x41:0xd0c": "Neoverse-N1", "0x41:0xd0d": "Cortex-A77",
    "0x41:0xd0e": "Cortex-A76AE", "0x41:0xd40": "Neoverse-V1", "0x41:0xd41": "Cortex-A78",
    "0x41:0xd42": "Cortex-A78AE", "0x41:0xd43": "Cortex-A65AE", "0x41:0xd44": "Cortex-X1",
    "0x41:0xd46": "Cortex-A510", "0x41:0xd47": "Cortex-A710", "0x41:0xd48": "Cortex-X2",
    "0x41:0xd49": "Neoverse-N2", "0x41:0xd4a": "Neoverse-E1", "0x41:0xd4b": "Cortex-A78C", "0x41:0xd4c": "Cortex-X1C",
    "0x41:0xd4d": "Cortex-A715", "0x41:0xd4e": "Cortex-X3", "0x41:0xd4f": "Neoverse-V2", "0x41:0xd80": "Cortex-A520",
    "0x41:0xd81": "Cortex-A720", "0x41:0xd82": "Cortex-X4", "0x41:0xd84": "Neoverse-V3", "0x41:0xd85": "Cortex-X925",
    "0x41:0xd87": "Cortex-A725", "0x41:0xd8e": "Neoverse-N3",
}
PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x1022": "amd", "0x8086": "intel", "0x106b": "apple",
               "0x13b5": "arm", "0x5143": "qualcomm", "0x1010": "imagination", "0x14e4": "broadcom"}
SMBIOS_MEMORY_TYPES = {24: "DDR3", 26: "DDR4", 29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}
USB_ROCKCHIP_PIDS = {"0x350a": "rockusb", "0x350b": "rockusb", "0x0006": "adb-mtp", "0x0019": "adb"}

# CPU features: key -> (/proc/cpuinfo tokens, any-of tokens, Windows IsProcessorFeaturePresent constant, lowest
# Windows build that knows the constant, macOS sysctl name). Linux: ON when every token (or one any-of token) is in
# the flags/Features line. Windows: TRUE -> ON; FALSE -> OFF on builds that know the constant, else UNKNOWN (older
# builds answer FALSE for constants they do not know); no constant -> UNKNOWN. The build thresholds are conservative
# and unverified: below them a FALSE only costs speed (UNKNOWN), never a wrong ON.
CPU_FEATURES = {
    "SUPPORTS_SSE42": (("sse4_2",), (), 38, 22000, "SSE4.2"),
    "SUPPORTS_AVX": (("avx",), (), 39, 22000, "AVX1.0"),
    "SUPPORTS_AVX2": (("avx2",), (), 40, 22000, "AVX2"),
    "SUPPORTS_BMI2": (("bmi2",), (), 60, 26100, "BMI2"),
    "SUPPORTS_F16C": (("f16c",), (), None, None, "F16C"),
    "SUPPORTS_FMA": (("fma",), (), None, None, "FMA"),
    "SUPPORTS_AVX512": (("avx512f",), (), 41, 22000, "AVX512F"),
    "SUPPORTS_AVX512_VBMI": (("avx512vbmi",), (), None, None, "AVX512VBMI"),
    "SUPPORTS_AVX512_VNNI": (("avx512_vnni",), (), None, None, "AVX512VNNI"),
    "SUPPORTS_AVX512_BF16": (("avx512_bf16",), (), None, None, None),
    "SUPPORTS_AVX_VNNI": (("avx_vnni",), (), None, None, None),
    "SUPPORTS_AMX_TILE": (("amx_tile",), (), None, None, None),
    "SUPPORTS_AMX_INT8": (("amx_int8",), (), None, None, None),
    "SUPPORTS_AMX_BF16": (("amx_bf16",), (), None, None, None),
    "SUPPORTS_NEON": ((), ("asimd", "neon"), 29, 0, "hw.optional.neon"),
    "SUPPORTS_DOTPROD": (("asimddp",), (), 43, 22000, "FEAT_DotProd"),
    "SUPPORTS_FP16_ARITH": (("fphp", "asimdhp"), (), 67, 26100, "FEAT_FP16"),
    "SUPPORTS_I8MM": (("i8mm",), (), 66, 26100, "FEAT_I8MM"),
    "SUPPORTS_SVE": (("sve",), (), 46, 26100, "FEAT_SVE"),
    "SUPPORTS_SVE2": (("sve2",), (), 47, 26100, "FEAT_SVE2"),
    "SUPPORTS_SME": (("sme",), (), 70, 26100, "FEAT_SME"),
    "SUPPORTS_BF16_ARM": (("bf16",), (), None, None, "FEAT_BF16"),
}

# PCI device ids of AMD APU graphics (amdgpu_drv.c): their "VRAM" is a carve-out of the system RAM.
AMD_APU_DEVICE_IDS = frozenset({
    "0x15dd", "0x15d8",                      # Raven, Picasso
    "0x1636", "0x1638", "0x164c", "0x15e7",  # Renoir, Cezanne, Lucienne, Barcelo
    "0x163f", "0x1435",                      # Van Gogh (Steam Deck)
    "0x1681", "0x1506", "0x164e", "0x13c0",  # Rembrandt, Mendocino, Raphael, Granite Ridge
    "0x15bf", "0x15c8",                      # Phoenix / Hawk Point (Radeon 780M, Ryzen Z1 Extreme), Phoenix2 (Z1)
    "0x150e", "0x1114", "0x1586",            # Strix Point, Krackan Point, Strix Halo
})
# Intel discrete GPUs (Arc Alchemist / Pro, Flex, Battlemage) by PCI device id range; other Intel GPUs are integrated.
INTEL_DISCRETE_RANGES = ((0x5690, 0x56FF), (0xE202, 0xE2FF))
# NPU and accelerator ids: PCI vendor ids, (vendor, device) pairs on PCI and USB.
PCI_HAILO, PCI_AXELERA, PCI_MEMRYX = "0x1e60", "0x1f4b", "0x1d6b"
CORAL_PCI = (("0x1ac1", "0x089a"),)
CORAL_USB = (("0x1a6e", "0x089a"), ("0x18d1", "0x9302"))
USB_ROCKCHIP_VENDOR = "0x2207"
# Windows IMAGE_FILE_MACHINE_* -> ARCH
WINDOWS_MACHINES = {0x8664: "x86_64", 0xAA64: "arm64", 0x014C: "x86", 0x01C4: "armv7"}

# --- small helpers ---------------------------------------------------------------------------------------------

_RULE_CACHE = {}
_NON_PRINTABLE = re.compile(r"[^\x20-\x7e]")
_INT = re.compile(r"^(0|[1-9][0-9]{0,11})$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HEX = re.compile(r"^(?:0[xX])?([0-9a-fA-F]{1,8})$")
_FIRST_NUMBER = re.compile(r"(\d{1,5}(?:\.\d{1,6}){0,3})")


def _regex(pattern):
    compiled = _RULE_CACHE.get(pattern)
    if compiled is None:
        compiled = _RULE_CACHE[pattern] = re.compile(pattern, re.ASCII)
    return compiled


def clean_text(value, limit=128):
    """Free text as the probes write it: printable ASCII only (other characters become spaces), whitespace collapsed,
    at most `limit` characters. None when nothing is left."""
    text = " ".join(_NON_PRINTABLE.sub(" ", str(value)).split())
    text = text[:limit].rstrip()
    return text or None


def check_value(rule, value):
    """The value normalised for a rule of KEYS / GPU_KEYS, or None when it does not fit."""
    kind, spec = rule[0], rule[1]
    if value is None:
        return None
    if kind == "bool":
        return value if value in BOOLS else None
    if kind == "enum":
        return value if value in spec else None
    if kind == "int":
        text = str(value)
        if not _INT.match(text):
            return None
        return text if spec[0] <= int(text) <= spec[1] else None
    if kind in ("pattern", "version"):
        text = str(value)
        return text if len(text) <= MAX_VALUE and _regex(spec).fullmatch(text) else None
    if kind == "text":
        return clean_text(value, spec)
    if kind == "list":
        items = value.split(",") if isinstance(value, str) else list(value)
        kept = []
        for item in items:
            if _regex(spec[0]).fullmatch(item) and item not in kept and len(kept) < spec[1]:
                kept.append(item)
        while kept and len(",".join(kept)) > MAX_VALUE:
            kept.pop()
        return ",".join(kept) or None
    if kind == "timestamp":
        return value if _TIMESTAMP.match(str(value)) else None
    return None


def hex_id(value, digits):
    """'0x41' / '41' / '0X041' -> '0x41' with at least `digits` lower-case hex digits; None if not hex."""
    match = _HEX.match(str(value).strip())
    return "0x{:0{}x}".format(int(match.group(1), 16), digits) if match else None


def first_number(text, parts=4):
    """The first dotted number in a tool's output, at most `parts` parts (27.3.1-ce -> 27.3.1)."""
    match = _FIRST_NUMBER.search(text or "")
    return ".".join(match.group(1).split(".")[:parts]) if match else None


def default_runner(cmd, timeout):
    """Runs a probe tool: (returncode, stdout, stderr); -1 when it is missing, hangs or cannot start."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        res = subprocess.run(list(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=timeout, check=False, universal_newlines=True,
                             errors="replace", creationflags=flags)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return -1, "", str(e)
    return res.returncode, res.stdout or "", res.stderr or ""


def default_find_library(name):
    try:
        import ctypes.util
    except ImportError:  # some board images ship Python without ctypes
        return None
    try:
        return ctypes.util.find_library(name)
    except (OSError, ValueError):
        return None


def _float(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


# --- GPU parsers (shared with orchestrator/runtime/hardware.py) -------------------------------------------------
# A GPU is a dict of GPU<n>_ suffixes to string values (VENDOR, NAME, PCI_ID, DISCRETE, UNIFIED, VRAM_MB,
# VRAM_FREE_MB, DRIVER_VERSION, COMPUTE_CAP, GFX_TARGET, SOURCE); keys starting with "_" are internal.

NVIDIA_FIELDS = "driver_version,compute_cap,memory.total,memory.free,pci.bus_id,pci.device_id,name"
NVIDIA_FIELDS_OLD = "driver_version,memory.total,memory.free,pci.bus_id,pci.device_id,name"


def parse_nvidia_csv(output):
    """Lines of `nvidia-smi --query-gpu=<NVIDIA_FIELDS> --format=csv,noheader,nounits`; the older five-field form
    driver_version,compute_cap,memory.total,memory.free,name is read as well."""
    gpus = []
    for line in (output or "").splitlines():
        parts = [p.strip() for p in line.split(",", 6)]
        if len(parts) == 7:
            driver, cap, total, free, bus, device, name = parts
        elif len(parts) >= 5:
            driver, cap, total, free = parts[:4]
            name, bus, device = ",".join(parts[4:]), "", ""
        else:
            continue
        if not driver or driver.startswith("["):
            continue
        gpu = {"VENDOR": "nvidia", "DISCRETE": "ON", "UNIFIED": "OFF", "SOURCE": "nvidia-smi"}
        gpu["NAME"] = clean_text(name) or "NVIDIA GPU"
        gpu["DRIVER_VERSION"] = check_value(GPU_KEYS["DRIVER_VERSION"], driver)
        cap_value = _float(cap)
        if cap_value is not None:
            gpu["COMPUTE_CAP"] = check_value(GPU_KEYS["COMPUTE_CAP"], "{:.1f}".format(cap_value))
        total_value, free_value = _float(total), _float(free)
        if total_value:
            gpu["VRAM_MB"] = str(int(total_value))
        if free_value is not None and free_value >= 0:
            gpu["VRAM_FREE_MB"] = str(int(free_value))
        match = re.match(r"^0x([0-9a-fA-F]{4})([0-9a-fA-F]{4})$", device)
        if match:
            gpu["PCI_ID"] = "0x{}:0x{}".format(match.group(2).lower(), match.group(1).lower())
        if bus:
            gpu["_bus"] = bus.lower()
        gpus.append({k: v for k, v in gpu.items() if v is not None})
    return gpus


def nvidia_smi_paths(os_name, env=None):
    env = os.environ if env is None else env
    paths = ["nvidia-smi"]
    if os_name == "windows":
        # Older drivers install it here without adding it to PATH (current ones put it into System32).
        for base in (env.get("ProgramW6432"), env.get("ProgramFiles")):
            if base:
                paths.append(os.path.join(base, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"))
    return paths


def probe_nvidia(run, os_name="linux", env=None):
    """NVIDIA GPUs from nvidia-smi ([] when it is missing or fails); and the executable that answered."""
    for exe in nvidia_smi_paths(os_name, env):
        rc, out, err = run([exe, "--query-gpu=" + NVIDIA_FIELDS, "--format=csv,noheader,nounits"], 15)
        if rc == -1:
            continue  # not installed here
        if rc != 0 and ("compute_cap" in (out + err) or "pci." in (out + err)):
            # Drivers before R510 do not know compute_cap: ask without it and leave the column empty.
            rc, out, _ = run([exe, "--query-gpu=" + NVIDIA_FIELDS_OLD, "--format=csv,noheader,nounits"], 15)
            out = "\n".join(line.replace(",", ",,", 1) for line in out.splitlines())
        return (parse_nvidia_csv(out) if rc == 0 else []), exe
    return [], None


def nvidia_cuda_max(run, exe):
    """The "CUDA Version: X.Y" of nvidia-smi's header (the highest CUDA version the driver supports)."""
    rc, out, _ = run([exe, "-q", "-d", "COMPUTE"], 15)
    match = re.search(r"CUDA Version\s*:\s*(\d{1,2}\.\d{1,3})\b", out or "") if rc == 0 else None
    return match.group(1) if match else None


def parse_vulkaninfo_summary(output):
    """The "Devices:" section of `vulkaninfo --summary`; software renderers (llvmpipe, lavapipe) are skipped."""
    devices, current = [], None
    for raw in (output or "").splitlines():
        line = raw.strip()
        if re.match(r"^GPU\d+:$", line):
            current = {}
            devices.append(current)
        elif current is not None and " = " in line:
            key, _, value = line.partition(" = ")
            current[key.strip()] = value.strip()
    gpus = []
    for dev in devices:
        dtype = dev.get("deviceType", "")
        if dtype.endswith(("_CPU", "_OTHER")) or not dev.get("deviceName"):
            continue
        vendor_id = hex_id(dev.get("vendorID", ""), 4)
        gpu = {"VENDOR": PCI_VENDORS.get(vendor_id or "", "other"), "NAME": clean_text(dev["deviceName"]) or "GPU",
               "DISCRETE": "ON" if dtype.endswith("DISCRETE_GPU") else "OFF",
               "UNIFIED": "ON" if dtype.endswith("INTEGRATED_GPU") else "OFF", "SOURCE": "vulkaninfo"}
        device_id = hex_id(dev.get("deviceID", ""), 4)
        if vendor_id and device_id and device_id != "0x0000" and len(vendor_id) == 6 and len(device_id) == 6:
            gpu["PCI_ID"] = vendor_id + ":" + device_id
        driver = first_number(dev.get("driverInfo", ""))
        if driver and check_value(GPU_KEYS["DRIVER_VERSION"], driver):
            gpu["DRIVER_VERSION"] = driver
        gpus.append(gpu)
    return gpus


def probe_vulkaninfo(run):
    """(vulkaninfo answered with a device list, the GPUs it lists)."""
    rc, out, _ = run(["vulkaninfo", "--summary"], 20)
    if rc != 0 or "Devices" not in (out or ""):
        return False, []
    return True, parse_vulkaninfo_summary(out)


def guess_discrete(vendor, name):
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


# One PowerShell call: adapter names from WMI, the PCI id, and the exact dedicated memory from the display-class
# registry (WMI's AdapterRAM is a 32-bit value and stops at 4 GiB). Only names, versions and ids leave PowerShell.
WINDOWS_ADAPTERS = r"""
$mem = @{}
$display = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*'
Get-ItemProperty $display -ErrorAction SilentlyContinue | ForEach-Object {
  $v = $_.'HardwareInformation.qwMemorySize'
  if ($_.DriverDesc -and ($v -is [long] -or $v -is [int])) { $mem[$_.DriverDesc] = [int64]$v }
}
Get-CimInstance Win32_VideoController | ForEach-Object {
  $pci = $null
  if ($_.PNPDeviceID -match 'VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})') {
    $pci = ('0x' + $Matches[1] + ':0x' + $Matches[2]).ToLower() }
  [pscustomobject]@{Name=$_.Name; AdapterCompatibility=$_.AdapterCompatibility;
                    DriverVersion=$_.DriverVersion; Memory=$mem[$_.Name]; Pci=$pci}
} | ConvertTo-Json -Compress
"""


def parse_windows_adapters(output):
    try:
        data = json.loads(output)
    except ValueError:
        return []
    gpus = []
    for item in data if isinstance(data, list) else [data]:
        if not isinstance(item, dict) or "Name" not in item:
            continue
        name = clean_text(item.get("Name") or "")
        pci = item.get("Pci") if isinstance(item.get("Pci"), str) else ""
        pci = pci if _regex(GPU_KEYS["PCI_ID"][1]).fullmatch(pci) else ""
        maker = "{} {}".format(item.get("AdapterCompatibility") or "", name or "").lower()
        vendor = PCI_VENDORS.get(pci[:6]) if pci else None
        if vendor is None:
            vendor = ("nvidia" if "nvidia" in maker else "amd" if ("advanced micro" in maker or "amd" in maker)
                      else "intel" if "intel" in maker else "other")
        if vendor not in ("nvidia", "amd", "intel") or not name:
            continue  # Microsoft Basic Display Adapter, remote display drivers
        discrete = guess_discrete(vendor, name)
        if vendor == "nvidia":
            discrete = True
        gpu = {"VENDOR": vendor, "NAME": name, "SOURCE": "wmi",
               "DISCRETE": {True: "ON", False: "OFF"}.get(discrete, "UNKNOWN"),
               "UNIFIED": {True: "OFF", False: "ON"}.get(discrete, "UNKNOWN")}
        memory = item.get("Memory")
        if isinstance(memory, (int, float)) and not isinstance(memory, bool) and memory > 0 and discrete:
            gpu["VRAM_MB"] = str(int(memory) // MIB)
        driver = check_value(GPU_KEYS["DRIVER_VERSION"], str(item.get("DriverVersion") or ""))
        if driver:
            gpu["DRIVER_VERSION"] = driver
        if pci:
            gpu["PCI_ID"] = pci
        gpus.append(gpu)
    return gpus


def probe_windows_adapters(run):
    """Display adapters of AMD, Intel and NVIDIA (every current driver of theirs ships the Vulkan runtime)."""
    rc, out, _ = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_ADAPTERS], 20)
    if rc != 0 or not (out or "").strip():
        return []
    return parse_windows_adapters(out)


def gfx_name(version):
    """KFD gfx_target_version (major*10000 + minor*100 + stepping) -> LLVM target name: 110003 -> gfx1103."""
    return "gfx{}{:x}{:x}".format(version // 10000, (version // 100) % 100, version % 100)


def _read(path, limit=1048576):
    """A small text file, or None. Never raises."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except (OSError, ValueError):
        return None
    return data.decode("utf-8", "replace")


def _link_name(path):
    """The last part of a symbolic link's target (a driver link in /sys), or None."""
    try:
        return os.path.basename(os.readlink(path).rstrip("/")) or None
    except (OSError, ValueError):
        return None


def kfd_gfx_targets(sys_dir):
    """DRM render minor -> gfx target, from the KFD topology of amdgpu compute nodes."""
    targets = {}
    nodes = os.path.join(sys_dir, "class", "kfd", "kfd", "topology", "nodes")
    for props in sorted(glob.glob(os.path.join(glob.escape(nodes), "*", "properties"))):
        values = {}
        for line in (_read(props) or "").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                values[parts[0]] = parts[1].strip()
        try:
            version, minor = int(values.get("gfx_target_version", "0")), int(values.get("drm_render_minor", "-1"))
        except ValueError:
            continue
        if version > 0 and minor >= 0:
            targets[minor] = gfx_name(version)
    return targets


def _intel_discrete(device_id):
    try:
        number = int(device_id, 16)
    except (TypeError, ValueError):
        return False
    return any(low <= number <= high for low, high in INTEL_DISCRETE_RANGES)


def probe_linux_sysfs(sys_dir):
    """Display-class PCI GPUs from /sys/class/drm: AMD with dedicated VRAM, APU detection and the KFD gfx target;
    Intel and other vendors with their PCI id."""
    targets = kfd_gfx_targets(sys_dir)
    gpus = []
    drm = os.path.join(sys_dir, "class", "drm")
    cards = [c for c in glob.glob(os.path.join(glob.escape(drm), "card[0-9]*"))
             if re.match(r"^card\d+$", os.path.basename(c))]
    for card in sorted(cards, key=lambda c: int(re.sub(r"\D", "", os.path.basename(c)) or 0)):
        dev = os.path.join(card, "device")
        vendor_id = hex_id((_read(os.path.join(dev, "vendor")) or "").strip(), 4)
        device_id = hex_id((_read(os.path.join(dev, "device")) or "").strip(), 4)
        pci_class = (_read(os.path.join(dev, "class")) or "").strip().lower()
        if not vendor_id or len(vendor_id) != 6:
            continue  # platform devices (Mali, display controllers) are found in the device tree
        if pci_class and not pci_class.startswith("0x03"):
            continue  # not a display controller
        vendor = PCI_VENDORS.get(vendor_id, "other")
        gpu = {"VENDOR": vendor, "SOURCE": "sysfs"}
        if device_id and len(device_id) == 6:
            gpu["PCI_ID"] = vendor_id + ":" + device_id
        if vendor == "amd":
            try:
                vram = int((_read(os.path.join(dev, "mem_info_vram_total")) or "").strip())
            except ValueError:
                vram = 0
            try:
                used = int((_read(os.path.join(dev, "mem_info_vram_used")) or "").strip())
            except ValueError:
                used = None
            # An APU reports only its carve-out as VRAM (512 MiB to a few GiB, set in the firmware).
            apu = device_id in AMD_APU_DEVICE_IDS or vram < 2 * GIB
            gfx = None
            for render in sorted(glob.glob(os.path.join(glob.escape(dev), "drm", "renderD*"))):
                try:
                    gfx = targets.get(int(os.path.basename(render)[len("renderD"):]), gfx)
                except ValueError:
                    pass
            kind = "integrated" if apu else "dedicated"
            gpu["NAME"] = "AMD Radeon {} ({})".format(gfx or device_id or os.path.basename(card), kind)
            gpu["DISCRETE"], gpu["UNIFIED"] = ("OFF", "ON") if apu else ("ON", "OFF")
            if vram // MIB:
                gpu["VRAM_MB"] = str(vram // MIB)
                if used is not None and 0 <= used <= vram:
                    gpu["VRAM_FREE_MB"] = str((vram - used) // MIB)
            if gfx:
                gpu["GFX_TARGET"] = gfx
                gpu["SOURCE"] = "kfd"
        elif vendor == "intel":
            discrete = _intel_discrete(device_id)
            gpu["NAME"] = "Intel GPU {}".format(device_id or os.path.basename(card))
            gpu["DISCRETE"], gpu["UNIFIED"] = ("ON", "OFF") if discrete else ("OFF", "ON")
        else:
            gpu["NAME"] = "{} GPU {}".format(vendor.upper() if vendor != "other" else "PCI", gpu.get("PCI_ID", ""))
            gpu["DISCRETE"], gpu["UNIFIED"] = ("ON", "OFF") if vendor == "nvidia" else ("UNKNOWN", "UNKNOWN")
        gpu["NAME"] = clean_text(gpu["NAME"])
        gpus.append(gpu)
    return gpus


def norm_name(name):
    return re.sub(r"\s+", " ", re.sub(r"\((TM|R)\)|[\u2122\u00ae]", "", name or "", flags=re.I)).strip().lower()


def hip_runtime(find_library, rocm_globs):
    """libamdhip64 found by the dynamic loader or in a ROCm folder."""
    if find_library("amdhip64"):
        return True
    return any(glob.glob(pattern) for pattern in rocm_globs)


# --- Windows -----------------------------------------------------------------------------------------------------

class WindowsApi:
    """The Windows calls of the probe. Every method returns None when the call is not available (another OS, a
    Python without ctypes, an old Windows); tests replace the whole object."""

    def registry(self, path, name):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                value, _kind = winreg.QueryValueEx(key, name)
            return value
        except (ImportError, OSError, ValueError):
            return None

    def version(self):
        """(major, minor, build) of Windows, or None."""
        getter = getattr(sys, "getwindowsversion", None)
        if getter is None:
            return None
        v = getter()
        return v.major, v.minor, v.build

    def feature(self, number):
        """IsProcessorFeaturePresent(number) as True/False, None when it cannot be asked."""
        try:
            import ctypes
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(ctypes.c_uint(number)))
        except (ImportError, AttributeError, OSError, ValueError):
            return None

    def memory(self):
        """(total physical, available physical, total page file) in bytes from GlobalMemoryStatusEx, or None."""
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullTotalPhys, status.ullAvailPhys, status.ullTotalPageFile
        except (ImportError, AttributeError, OSError, ValueError):
            return None

    def native_machine(self):
        """The native IMAGE_FILE_MACHINE_* of IsWow64Process2 (Windows 10 1709 and later), or None."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            function = getattr(kernel32, "IsWow64Process2", None)
            if function is None:
                return None
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
            process, native = ctypes.c_ushort(), ctypes.c_ushort()
            if not function(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
                return None
            return native.value
        except (ImportError, AttributeError, OSError, ValueError):
            return None

    def file_version(self, path):
        """The file version (a.b.c.d) of a DLL, or None."""
        try:
            import ctypes
            version = ctypes.windll.version
            size = version.GetFileVersionInfoSizeW(path, None)
            if not size:
                return None
            buffer = ctypes.create_string_buffer(size)
            if not version.GetFileVersionInfoW(path, 0, size, buffer):
                return None
            pointer, length = ctypes.c_void_p(), ctypes.c_uint()
            if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
                return None
            info = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
            ms, ls = info[2], info[3]
            return "{}.{}.{}.{}".format(ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        except (ImportError, AttributeError, OSError, ValueError):
            return None


# One PowerShell call for what the Windows API does not give directly: core count, the memory type and speed of the
# first module (no part or serial numbers), and the vendor:device ids of present PnP devices (no instance paths).
WINDOWS_SYSTEM = r"""
$ErrorActionPreference = 'SilentlyContinue'
$p = @(Get-CimInstance Win32_Processor)
$m = @(Get-CimInstance Win32_PhysicalMemory)
$speeds = @($m | ForEach-Object { if ($_.ConfiguredClockSpeed) { [int]$_.ConfiguredClockSpeed }
                                  elseif ($_.Speed) { [int]$_.Speed } })
$ids = @(Get-PnpDevice -PresentOnly | ForEach-Object {
  if ($_.InstanceId -match '(VID|VEN)_([0-9A-Fa-f]{4})&(PID|DEV)_([0-9A-Fa-f]{4})') {
    $bus = 'pci'; if ($Matches[1] -eq 'VID') { $bus = 'usb' }
    ($bus + ':' + $Matches[2] + ':' + $Matches[4]).ToLower() } } | Sort-Object -Unique)
$npu = @(Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -like '*AI Boost*' }).Count -gt 0
$type = $null; if ($m.Count -gt 0) { $type = [int]$m[0].SMBIOSMemoryType }
$speed = $null; if ($speeds.Count -gt 0) { $speed = [int](($speeds | Measure-Object -Maximum).Maximum) }
[pscustomobject]@{Probe='system'; Cores=[int](($p | Measure-Object NumberOfCores -Sum).Sum); MemoryType=$type;
                  MemorySpeed=$speed; Ids=$ids; IntelNpu=$npu} | ConvertTo-Json -Compress
"""


def parse_windows_system(output):
    """The answer of WINDOWS_SYSTEM: {'cores', 'memory_type', 'memory_speed', 'ids', 'intel_npu'}; {} if unusable."""
    try:
        data = json.loads(output)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("Probe") != "system":
        return {}
    ids = data.get("Ids") or []
    ids = ids if isinstance(ids, list) else [ids]

    def number(value):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {"cores": number(data.get("Cores")), "memory_type": number(data.get("MemoryType")),
            "memory_speed": number(data.get("MemorySpeed")), "intel_npu": data.get("IntelNpu") is True,
            "ids": [i for i in ids if isinstance(i, str) and re.match(r"^(pci|usb):[0-9a-f]{4}:[0-9a-f]{4}$", i)]}


# --- the probe ---------------------------------------------------------------------------------------------------

class Probe:
    """One run of the probe. collect() is the public entry; the options exist for the framework and for tests."""

    def __init__(self, root=None, sys_dir=None, run=None, find_library=None, system=None, machine=None,
                 windows_api=None, memory=None, rocm_globs=None, disk_path=None, now=None, sections=None, env=None):
        self.env = dict(os.environ if env is None else env)
        given_root = root if root is not None else (self.env.get("HW_PROBE_SYSROOT") or os.sep)
        self.root = os.path.abspath(given_root)
        self.sysroot = self.root != os.path.abspath(os.sep)
        self.sys_dir = os.path.abspath(sys_dir) if sys_dir is not None else os.path.join(self.root, "sys")
        self.sys_override = sys_dir is not None
        self.run = run or default_runner
        self.injected_find_library = find_library
        self.find_library = find_library or default_find_library
        self.system = system
        self.machine = machine
        self.win = windows_api or WindowsApi()
        self.memory_override = memory
        self.rocm_globs = rocm_globs
        self.disk_path = disk_path
        self.now = now
        self.sections = set(SECTIONS if sections is None else sections)
        self.values = {}
        self.gpus = []
        self.warnings = []
        self.os = None
        self.arch = None
        self._metal = False

    # --- writing ---

    def path(self, absolute):
        """An absolute machine path inside the sysroot (/sys follows sys_dir)."""
        if absolute == "/sys" or absolute.startswith("/sys/"):
            return os.path.join(self.sys_dir, absolute[5:])
        return os.path.join(self.root, absolute.lstrip("/"))

    def warn(self, code, key=None):
        item = code + (":" + key if key else "")
        if item not in self.warnings:
            self.warnings.append(item)

    def put(self, key, value, warn_invalid=True):
        """Writes a validated value once (the first value wins); a value that does not fit the key is left out (with a
        warning)."""
        if value is None or key in self.values:
            return False
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        checked = check_value(KEYS[key], value)
        if checked is None:
            if warn_invalid:
                self.warn("not_determined", key)
            return False
        self.values[key] = checked
        return True

    # --- reading ---

    def read(self, absolute, limit=1048576):
        return _read(self.path(absolute), limit)

    def exists(self, absolute):
        return os.path.lexists(self.path(absolute))

    def tool_output(self, cmd, timeout=10):
        rc, out, err = self.run(cmd, timeout)
        return (out or "") + "\n" + (err or "") if rc != -1 else None

    def tool_stdout(self, cmd, timeout=10):
        """Standard output of a tool that succeeded (exit code 0), None otherwise. For values read from the output
        itself, such as sysctl's: an error message on stderr must never become a value."""
        rc, out, _err = self.run(cmd, timeout)
        return out or "" if rc == 0 else None

    # --- run ---

    def collect(self):
        self._system()
        windows_info = {}
        if self.os == "windows" and self.sections & {"cpu", "npu"}:
            rc, out, _ = self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_SYSTEM], 60)
            windows_info = parse_windows_system(out) if rc == 0 else {}
        if "cpu" in self.sections:
            self._cpu(windows_info)
        if "memory" in self.sections:
            self._memory(windows_info)
        if "soc" in self.sections:
            self._soc()
        if "npu" in self.sections:
            self._npu(windows_info)
        if "gpu" in self.sections:
            self._gpu()
        if "toolchain" in self.sections:
            self._toolchain()
        if "CPU_CORES_LOGICAL" not in self.values and not self.sysroot:
            self.put("CPU_CORES_LOGICAL", os.cpu_count())
        return self._finish()

    # --- system ---

    def _system(self):
        self.values["PROBE_SCHEMA"] = SCHEMA
        self.values["PROBE_TOOL"] = TOOL
        self.values["PROBE_TOOL_VERSION"] = TOOL_VERSION
        now = self.now or datetime.now(timezone.utc)
        self.values["GENERATED_AT"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        system = (self.system or platform.system() or "").strip().lower()
        os_name = {"darwin": "macos"}.get(system, system)
        if os_name == "linux" and (self.exists("/system/build.prop") or
                                   (not self.sysroot and hasattr(sys, "getandroidapilevel"))):
            os_name = "android"
        self.os = os_name if os_name in KEYS["OS"][1] else None
        self.put("OS", self.os)
        if self.os in ("linux", "android", "freebsd"):
            self._unix_system()
        elif self.os == "windows":
            self._windows_system()
        elif self.os == "macos":
            self._macos_system()
        self.arch = self.values.get("ARCH")

    def _uname(self):
        """(kernel release, machine): from the sysroot's /proc/sys/kernel in sysroot mode, else os.uname()."""
        if self.sysroot:
            release = (self.read("/proc/sys/kernel/osrelease") or "").strip() or None
            machine = (self.read("/proc/sys/kernel/arch") or "").strip() or None
            return release, self.machine or machine
        try:
            info = os.uname()
            return info.release, self.machine or info.machine
        except AttributeError:
            return platform.release(), self.machine or platform.machine()

    def _unix_system(self):
        release, machine = self._uname()
        self.put("KERNEL", release)
        kernel_arch = ARCH_ALIASES.get((machine or "").strip().lower(), "other")
        self.put("KERNEL_ARCH", kernel_arch)
        arch = kernel_arch
        # Binaries must match the userland: a 32-bit Python on a 64-bit kernel means a 32-bit userland.
        if not self.sysroot and self.machine is None and struct.calcsize("P") == 4:
            arch = ARCH_32BIT_USERLAND.get(arch, arch)
        self.put("ARCH", arch)
        self._os_release()
        if self.os == "android":
            self.put("OS_ID", "android")
            self.put("OS_FAMILY", "android")
            self.put("LIBC", "bionic")
            if not self.sysroot:
                out = self.tool_output(["getprop", "ro.build.version.release"], 5)
                self.put("OS_VERSION_ID", (out or "").strip() or None, warn_invalid=False)
        elif self.os == "freebsd":
            self.put("OS_FAMILY", "bsd")
            self.put("LIBC", "bsdlibc")
            self.put("INIT_SYSTEM", "bsdinit")
        else:
            self._libc()
            self.put("INIT_SYSTEM", self._init_system())

    def _os_release(self):
        text = self.read("/etc/os-release") or self.read("/usr/lib/os-release")
        if text is None:
            if self.os == "linux":
                self.warn("not_determined", "OS_ID")
            return
        fields = parse_os_release(text)
        os_id = (fields.get("ID") or "").lower() or None
        like = [item for item in (fields.get("ID_LIKE") or "").lower().split() if item]
        self.put("OS_ID", os_id)
        if like:
            self.put("OS_ID_LIKE", ",".join(like))
        if self.os == "linux":
            family = "other"
            # Only well-formed IDs count (a crafted ID such as "x debian" must not pick a family).
            for candidate in [c for c in [os_id or ""] + like if _regex(_IDENT).fullmatch(c)]:
                found = next((f for f, ids in OS_FAMILIES.items() if candidate in ids), None)
                if found:
                    family = found
                    break
            self.put("OS_FAMILY", family)
            variant = (fields.get("VARIANT_ID") or "").lower() or None
            if variant is None:
                variant = next((name for marker, name in OS_VARIANT_MARKERS if self.exists(marker)), None)
            self.put("OS_VARIANT_ID", variant)
        self.put("OS_VERSION_ID", fields.get("VERSION_ID") or None)
        self.put("OS_VERSION_CODENAME", (fields.get("VERSION_CODENAME") or "").lower() or None)
        self.put("OS_PRETTY_NAME", fields.get("PRETTY_NAME") or None)

    def _libc(self):
        if not self.sysroot:
            try:
                text = os.confstr("CS_GNU_LIBC_VERSION")
            except (AttributeError, ValueError, OSError):
                text = None
            if text and text.lower().startswith("glibc"):
                self.put("LIBC", "glibc")
                self.put("LIBC_VERSION", first_number(text, 4))
                return
        musl = sorted(glob.glob(os.path.join(glob.escape(self.path("/lib")), "ld-musl-*.so.1")))
        if musl:
            self.put("LIBC", "musl")
            version = None
            if not self.sysroot:
                # The musl loader prints its version when run without arguments (exit code 1).
                rc, out, err = self.run([musl[0]], 5)
                match = re.search(r"Version\s+(\d+\.\d+(?:\.\d+)?)", (out or "") + (err or "")) if rc != -1 else None
                version = match.group(1) if match else None
            if version:
                self.put("LIBC_VERSION", version)
            elif not self.sysroot:
                self.warn("not_determined", "LIBC_VERSION")
            return
        if glob.glob(os.path.join(glob.escape(self.path("/lib")), "ld-uClibc*.so*")):
            self.put("LIBC", "uclibc")
            return
        glibc_markers = ("/lib/ld-linux*.so*", "/lib64/ld-linux*.so*", "/lib/*/ld-linux*.so*", "/lib/*/libc.so.6",
                         "/lib64/libc.so.6", "/lib/libc.so.6", "/usr/lib/*/libc.so.6", "/usr/lib64/libc.so.6")
        if any(glob.glob(self._glob(m)) for m in glibc_markers):
            self.put("LIBC", "glibc")
            return
        self.warn("not_determined", "LIBC")

    def _glob(self, absolute):
        """A glob pattern under the sysroot; the sysroot part is escaped."""
        return os.path.join(glob.escape(self.root), absolute.lstrip("/"))

    def _init_system(self):
        if os.path.isdir(self.path("/run/systemd/system")):
            return "systemd"
        if self.exists("/run/openrc"):
            return "openrc"
        if self.exists("/run/runit") or self.exists("/etc/runit/runsvdir"):
            return "runit"
        if self.exists("/run/s6") or self.exists("/run/s6-rc"):
            return "s6"
        if self.exists("/sbin/procd"):
            return "procd"
        comm = (self.read("/proc/1/comm") or "").strip()
        if comm == "init":
            if _link_name(self.path("/sbin/init")) == "busybox":
                return "busybox"
            if self.exists("/etc/inittab"):
                return "sysvinit"
        return "other"

    def _windows_system(self):
        version = self.win.version()
        if version:
            text = "{}.{}.{}".format(*version)
            self.put("OS_VERSION_ID", text)
            self.put("KERNEL", text)
        else:
            self.put("KERNEL", (platform.release() or "").strip() or None)
        native = self.win.native_machine()
        own = ARCH_ALIASES.get((self.machine or self.env.get("PROCESSOR_ARCHITEW6432") or
                                self.env.get("PROCESSOR_ARCHITECTURE") or platform.machine() or "").lower(), "other")
        arch = WINDOWS_MACHINES.get(native, own) if native is not None and self.machine is None else own
        if native is not None and self.machine is None and own != "other" and own != arch:
            self.warn("emulated")  # this process runs emulated (an x64 Python on an ARM64 PC); ARCH is the native one
        self.put("ARCH", arch)
        self.put("OS_ID", "windows")
        self.put("OS_FAMILY", "windows")
        product = self.win.registry(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName")
        build = version[2] if version else None
        if isinstance(product, str):
            if build and build >= 22000:
                product = product.replace("Windows 10", "Windows 11")  # Windows 11 still says 10 in ProductName
            self.put("OS_PRETTY_NAME", product)
        self.put("LIBC", "ucrt")
        root = self.env.get("SystemRoot") or self.env.get("SYSTEMROOT") or r"C:\Windows"
        self.put("LIBC_VERSION", self.win.file_version(os.path.join(root, "System32", "ucrtbase.dll")),
                 warn_invalid=False)
        self.put("INIT_SYSTEM", "scm")

    def _macos_system(self):
        release, machine = self._uname()
        self.put("KERNEL", release)
        out = self.tool_stdout(["sysctl", "-n", "hw.optional.arm64"], 5)
        if out is not None and out.strip().startswith("1"):
            arch = "arm64"  # also under Rosetta
            if ARCH_ALIASES.get((machine or "").lower()) == "x86_64" and self.machine is None:
                self.warn("emulated")
        elif out is not None and out.strip().startswith("0"):
            arch = "x86_64"
        else:
            arch = ARCH_ALIASES.get((machine or "").lower(), "other")
        self.put("ARCH", arch)
        self.put("OS_ID", "macos")
        self.put("OS_FAMILY", "macos")
        version = platform.mac_ver()[0] if not self.system else ""
        if version:
            self.put("OS_VERSION_ID", version)
            self.put("OS_PRETTY_NAME", "macOS " + version)
        self.put("LIBC", "libsystem")
        self.put("INIT_SYSTEM", "launchd")

    # --- CPU ---

    def _cpu(self, windows_info):
        if self.os in ("linux", "android"):
            self._cpu_linux()
        elif self.os == "windows":
            self._cpu_windows(windows_info)
        elif self.os == "macos":
            self._cpu_macos()
        elif self.os == "freebsd":
            out = self.tool_stdout(["sysctl", "-n", "hw.model"], 5)
            self.put("CPU_MODEL", (out or "").strip() or None, warn_invalid=False)

    def _cpu_linux(self):
        text = self.read("/proc/cpuinfo")
        if text is None:
            self.warn("not_determined", "CPU_FLAGS_ALL")
            return
        processors = parse_cpuinfo(text)
        if not processors:
            return
        first = processors[0][1]
        self.put("CPU_CORES_LOGICAL", len(processors))
        arm = self.arch in KEYS["CPU_CLUSTERS"][3]
        if arm:
            implementer = first.get("CPU implementer")
            if implementer is not None:
                self.put("CPU_VENDOR", ARM_IMPLEMENTERS.get(hex_id(implementer, 2) or "", "other"))
        elif "vendor_id" in first:
            self.put("CPU_VENDOR", X86_VENDORS.get(first["vendor_id"].strip(), "other"))
        pairs = {(p.get("physical id"), p.get("core id")) for _n, p in processors
                 if p.get("physical id") is not None and p.get("core id") is not None}
        if pairs:
            self.put("CPU_CORES_PHYSICAL", len(pairs))
        elif arm:
            self.put("CPU_CORES_PHYSICAL", len(processors))
        clusters = []
        if arm:
            clusters = self._clusters(processors)
            if clusters:
                self.put("CPU_CLUSTERS", ",".join(c[0] for c in clusters))
        if arm and self.arch == "arm64":
            names = []
            for _entry, name in clusters:
                if name not in names:
                    names.append(name)
            if names:
                self.put("CPU_MODEL", "+".join(names))
            else:
                self.warn("not_determined", "CPU_MODEL")
        else:
            model = first.get("model name")
            if model:
                self.put("CPU_MODEL", model)
            else:
                self.warn("not_determined", "CPU_MODEL")
        flags = first.get("flags") if not arm else first.get("Features")
        if flags is None:
            flags = first.get("flags") or first.get("Features")
        tokens = [t for t in (flags or "").lower().split()]
        if tokens:
            self.put("CPU_FLAGS_ALL", ",".join(tokens))
            self.put("CPU_FLAGS_SOURCE", "proc-cpuinfo")
        self._features_from_flags(tokens if flags is not None else None)

    def _clusters(self, processors):
        """[(entry, core name)] per run of CPUs with the same implementer, part and maximum frequency."""
        runs = []
        for number, fields in sorted(processors, key=lambda p: p[0]):
            implementer = hex_id(fields.get("CPU implementer", ""), 2)
            part = hex_id(fields.get("CPU part", ""), 3)
            if not implementer or not part or len(implementer) != 4 or len(part) != 5:
                return []
            khz = (self.read("/sys/devices/system/cpu/cpu{}/cpufreq/cpuinfo_max_freq".format(number)) or "").strip()
            mhz = str(int(khz) // 1000) if khz.isdigit() and 0 < int(khz) // 1000 < 100000 else None
            key = (implementer, part, mhz)
            if runs and runs[-1][0] == key and runs[-1][2] == number - 1:
                runs[-1][2] = number
            else:
                runs.append([key, number, number])
        result = []
        for (implementer, part, mhz), low, high in runs:
            cpus = str(low) if low == high else "{}-{}".format(low, high)
            entry = "{}:{}:{}".format(cpus, implementer, part) + (":" + mhz if mhz else "")
            name = ARM_PARTS.get(implementer + ":" + part)
            if name is None:
                name = implementer + ":" + part
                self.warn("unknown_arm_part")
            result.append((entry, name))
        return result

    def _features_from_flags(self, tokens):
        """SUPPORTS_* from a /proc/cpuinfo flag list (None: the file had no list -> UNKNOWN for this ARCH)."""
        for key, (needed, any_of, _w, _b, _m) in CPU_FEATURES.items():
            if self.arch not in KEYS[key][3]:
                self.values[key] = "OFF"
            elif tokens is None:
                self.values[key] = "UNKNOWN"
            elif any_of:
                self.values[key] = "ON" if set(any_of) & set(tokens) else "OFF"
            else:
                self.values[key] = "ON" if set(needed) <= set(tokens) else "OFF"

    def _cpu_windows(self, info):
        cpu_key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        name = self.win.registry(cpu_key, "ProcessorNameString")
        if isinstance(name, str) and name.strip():
            self.put("CPU_MODEL", name)
        else:
            self.warn("not_determined", "CPU_MODEL")
        vendor = self.win.registry(cpu_key, "VendorIdentifier")
        if isinstance(vendor, str) and vendor.strip() in X86_VENDORS:
            self.put("CPU_VENDOR", X86_VENDORS[vendor.strip()])
        self.put("CPU_CORES_LOGICAL", os.cpu_count())
        if info.get("cores"):
            self.put("CPU_CORES_PHYSICAL", info["cores"])
        self.values["CPU_FLAGS_SOURCE"] = "windows-api"
        version = self.win.version()
        build = version[2] if version else 0
        for key, (_needed, _any, constant, min_build, _mac) in CPU_FEATURES.items():
            self.values[key] = windows_feature_value(self.arch, key, constant, min_build, build, self.win.feature)

    def _cpu_macos(self):
        def sysctl(name):
            out = self.tool_stdout(["sysctl", "-n", name], 5)  # "sysctl: unknown oid" goes to stderr
            return out.strip() if out is not None else None

        self.put("CPU_MODEL", sysctl("machdep.cpu.brand_string") or None)
        if self.arch == "arm64":
            self.put("CPU_VENDOR", "apple")
        else:
            vendor = sysctl("machdep.cpu.vendor") or ""
            if vendor:
                self.put("CPU_VENDOR", X86_VENDORS.get(vendor, "other"))
        self.put("CPU_CORES_LOGICAL", sysctl("hw.logicalcpu") or None, warn_invalid=False)
        self.put("CPU_CORES_PHYSICAL", sysctl("hw.physicalcpu") or None, warn_invalid=False)
        if self.arch == "arm64":
            for key, (_n, _a, _c, _b, name) in CPU_FEATURES.items():
                if self.arch not in KEYS[key][3]:
                    self.values[key] = "OFF"
                    continue
                answer = sysctl(name if name and name.startswith("hw.") else "hw.optional.arm." + str(name))
                self.values[key] = "ON" if answer == "1" else "OFF" if answer == "0" else "UNKNOWN"
            self.values["CPU_FLAGS_SOURCE"] = "sysctl"
            return
        tokens = ((sysctl("machdep.cpu.features") or "") + " " + (sysctl("machdep.cpu.leaf7_features") or "")).split()
        if tokens:
            self.put("CPU_FLAGS_ALL", ",".join(t.lower() for t in tokens))
            self.values["CPU_FLAGS_SOURCE"] = "sysctl"
        upper = {t.upper() for t in tokens}
        for key, (_n, _a, _c, _b, name) in CPU_FEATURES.items():
            if self.arch not in KEYS[key][3]:
                self.values[key] = "OFF"
            elif key.startswith("SUPPORTS_AMX"):
                self.values[key] = "OFF"  # no Mac has AMX
            elif not name or not tokens:
                self.values[key] = "UNKNOWN"
            else:
                self.values[key] = "ON" if name.upper() in upper else "OFF"

    # --- memory ---

    def _memory(self, windows_info):
        total = available = swap = None
        if self.memory_override is not None:
            total, available = self.memory_override
        elif self.os in ("linux", "android"):
            info = parse_meminfo(self.read("/proc/meminfo") or "")
            total, available, swap = info.get("MemTotal"), info.get("MemAvailable"), info.get("SwapTotal")
        elif self.os == "windows":
            status = self.win.memory()
            if status:
                total, available = status[0], status[1]
                swap = max(0, status[2] - status[0])
        elif self.os in ("macos", "freebsd"):
            out = self.tool_stdout(["sysctl", "-n", "hw.memsize" if self.os == "macos" else "hw.physmem"], 5)
            total = int(out.strip()) if out and out.strip().isdigit() else None
            if self.os == "macos":
                available = self._macos_available()
                swap = parse_swapusage(self.tool_stdout(["sysctl", "-n", "vm.swapusage"], 5) or "")
        if total is None and not self.sysroot and hasattr(os, "sysconf"):
            try:
                total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            except (ValueError, OSError):
                total = None
        if total is not None:
            self.put("RAM_TOTAL_MB", int(total) // MIB)
        if swap is not None:
            self.put("SWAP_TOTAL_MB", int(swap) // MIB)
        if available is not None:
            self.put("RAM_AVAILABLE_MB", int(available) // MIB)
        if self.os == "windows":
            ram_type = SMBIOS_MEMORY_TYPES.get(windows_info.get("memory_type") or -1)
            self.put("RAM_TYPE", ram_type, warn_invalid=False)
            self.put("RAM_SPEED_MTS", windows_info.get("memory_speed"), warn_invalid=False)
        path = self.disk_path or self._default_disk_path()
        if path:
            try:
                self.put("DISK_FREE_MB", shutil.disk_usage(path).free // MIB, warn_invalid=False)
            except (OSError, ValueError):
                pass

    def _default_disk_path(self):
        home = os.path.expanduser("~")
        for candidate in (self.env.get("LLM_FRAMEWORK_HOME"), os.path.join(home, ".llm-framework"), home):
            if candidate and os.path.isdir(candidate):
                return candidate
        return None

    def _macos_available(self):
        out = self.tool_stdout(["vm_stat"], 5) or ""
        page = re.search(r"page size of (\d+) bytes", out)
        free = re.search(r"Pages free:\s+(\d+)", out)
        inactive = re.search(r"Pages inactive:\s+(\d+)", out)
        if not (page and free and inactive):
            return None
        return (int(free.group(1)) + int(inactive.group(1))) * int(page.group(1))

    # --- SoC ---

    def _device_tree_bases(self):
        """/proc/device-tree and its target; only the target under a /sys given by the caller."""
        if self.sys_override:
            return ("/sys/firmware/devicetree/base",)
        return ("/proc/device-tree", "/sys/firmware/devicetree/base")

    def _device_tree(self, name):
        """A device-tree property of the root node (NUL-separated strings)."""
        for base in self._device_tree_bases():
            text = self.read(base + "/" + name, 65536)
            if text is not None:
                return text
        return None

    def _soc(self):
        if self.os not in ("linux", "android"):
            return
        # NUL-separated strings; a line break inside one (never in a real device tree) separates as well, as in the
        # .sh, so the last entry is the same for both probes.
        compatible = [c for c in re.split("[\0\n]", self._device_tree("compatible") or "") if c.strip()]
        if compatible:
            vendor, _, model = compatible[-1].strip().lower().partition(",")
            if model:
                self.put("SOC_VENDOR", vendor, warn_invalid=False)
                self.put("SOC_MODEL", model, warn_invalid=False)
        model = self._device_tree("model")
        if model:
            self.put("BOARD_MODEL", model.replace("\0", " "), warn_invalid=False)

    # --- NPU and USB-attached boards ---

    def _pci_devices(self):
        """[(vendor, device)] of the PCI bus, from /sys/bus/pci/devices."""
        found = []
        base = self.path("/sys/bus/pci/devices")
        for dev in sorted(glob.glob(os.path.join(glob.escape(base), "*"))):
            vendor = hex_id((_read(os.path.join(dev, "vendor")) or "").strip(), 4)
            device = hex_id((_read(os.path.join(dev, "device")) or "").strip(), 4)
            if vendor and device:
                found.append((vendor, device))
        return found

    def _usb_devices(self):
        """[(vendor, product)] of the USB bus, from /sys/bus/usb/devices (never the serial attribute)."""
        found = []
        base = self.path("/sys/bus/usb/devices")
        for dev in sorted(glob.glob(os.path.join(glob.escape(base), "*"))):
            vendor = hex_id((_read(os.path.join(dev, "idVendor")) or "").strip(), 4)
            product = hex_id((_read(os.path.join(dev, "idProduct")) or "").strip(), 4)
            if vendor and product:
                found.append((vendor, product))
        return found

    def _rknpu_node(self):
        """The Rockchip NPU node: /dev/rknpu, a DRM node of the vendor rknpu driver (its platform driver is named
        "RKNPU"; compared without case), or an accel node of the mainline rocket driver (unverified on a board)."""
        if self.exists("/dev/rknpu"):
            return "/dev/rknpu"
        drm = self.path("/sys/class/drm")
        for pattern in ("renderD*", "card*"):
            for node in sorted(glob.glob(os.path.join(glob.escape(drm), pattern))):
                name = os.path.basename(node)
                if "-" not in name and (_link_name(os.path.join(node, "device", "driver")) or "").lower() == "rknpu":
                    return "/dev/dri/" + name
        accel = self.path("/sys/class/accel")
        for node in sorted(glob.glob(os.path.join(glob.escape(accel), "accel[0-9]*"))):
            if _link_name(os.path.join(node, "device", "driver")) == "rocket":
                return "/dev/accel/" + os.path.basename(node)
        return None

    def _rknpu_driver_version(self):
        """The vendor rknpu driver's version: its module version, its world-readable procfs node (/proc/rknpu/version,
        "RKNPU driver: v0.9.6") or its debugfs node (root only)."""
        for source in ("/sys/module/rknpu/version", "/proc/rknpu/version", "/sys/kernel/debug/rknpu/version"):
            version = first_number(self.read(source, 4096) or "", 4)
            if version and check_value(KEYS["RKNPU_DRIVER_VERSION"], version):
                return version
        return None

    def _npu(self, windows_info):
        flags = {"SUPPORTS_RKNN": "OFF", "SUPPORTS_HAILO": "OFF", "SUPPORTS_METIS": "OFF", "SUPPORTS_MX3": "OFF",
                 "SUPPORTS_CORAL": "OFF", "SUPPORTS_INTEL_NPU": "OFF"}
        described = None  # (vendor, model, device node)
        if self.os in ("linux", "android"):
            pci, usb = self._pci_devices(), self._usb_devices()
            node = self._rknpu_node()
            if node:
                flags["SUPPORTS_RKNN"] = "ON"
                soc = self.values.get("SOC_MODEL") if self.values.get("SOC_VENDOR") == "rockchip" else None
                described = ("rockchip", soc, node)
                version = self._rknpu_driver_version()
                if version:
                    self.put("RKNPU_DRIVER_VERSION", version)
                else:
                    self.warn("needs_root", "RKNPU_DRIVER_VERSION")
            hailo_nodes = sorted(glob.glob(os.path.join(glob.escape(self.path("/dev")), "hailo[0-9]*")))
            apex_nodes = sorted(glob.glob(os.path.join(glob.escape(self.path("/dev")), "apex_[0-9]*")))
            intel_nodes = []
            accel = self.path("/sys/class/accel")
            for node_path in sorted(glob.glob(os.path.join(glob.escape(accel), "accel[0-9]*"))):
                if _link_name(os.path.join(node_path, "device", "driver")) == "intel_vpu":
                    intel_nodes.append("/dev/accel/" + os.path.basename(node_path))
            vendors = {v for v, _d in pci}
            checks = (
                ("SUPPORTS_HAILO", "hailo", PCI_HAILO in vendors or bool(hailo_nodes),
                 "/dev/" + os.path.basename(hailo_nodes[0]) if hailo_nodes else None),
                ("SUPPORTS_METIS", "axelera", PCI_AXELERA in vendors, None),
                ("SUPPORTS_MX3", "memryx", PCI_MEMRYX in vendors, None),
                ("SUPPORTS_CORAL", "google", bool(set(pci) & set(CORAL_PCI)) or bool(set(usb) & set(CORAL_USB)) or
                 bool(apex_nodes), "/dev/" + os.path.basename(apex_nodes[0]) if apex_nodes else None),
                ("SUPPORTS_INTEL_NPU", "intel", bool(intel_nodes), intel_nodes[0] if intel_nodes else None),
            )
            for key, vendor, present, device in checks:
                if present:
                    flags[key] = "ON"
                    if described is None:
                        described = (vendor, "intel-npu" if vendor == "intel" else None, device)
            rockchip = [p for v, p in usb if v == USB_ROCKCHIP_VENDOR]
            if rockchip:
                self.put("USB_ROCKCHIP_PID", rockchip[0])
                self.put("USB_ROCKCHIP_MODE", USB_ROCKCHIP_PIDS.get(rockchip[0], "other"))
        elif self.os == "windows":
            ids = set(windows_info.get("ids") or [])
            pci_vendors = {i.split(":")[1] for i in ids if i.startswith("pci:")}
            coral = ({"pci:{}:{}".format(v[2:], d[2:]) for v, d in CORAL_PCI} |
                     {"usb:{}:{}".format(v[2:], d[2:]) for v, d in CORAL_USB})
            checks = (
                ("SUPPORTS_HAILO", "hailo", PCI_HAILO[2:] in pci_vendors),
                ("SUPPORTS_METIS", "axelera", PCI_AXELERA[2:] in pci_vendors),
                ("SUPPORTS_MX3", "memryx", PCI_MEMRYX[2:] in pci_vendors),
                ("SUPPORTS_CORAL", "google", bool(ids & coral)),
                ("SUPPORTS_INTEL_NPU", "intel", bool(windows_info.get("intel_npu"))),
            )
            for key, vendor, present in checks:
                if present:
                    flags[key] = "ON"
                    if described is None:
                        described = (vendor, "intel-npu" if vendor == "intel" else None, None)
            rockchip = sorted(i for i in ids if i.startswith("usb:" + USB_ROCKCHIP_VENDOR[2:] + ":"))
            if rockchip:
                pid = "0x" + rockchip[0].rsplit(":", 1)[1]
                self.put("USB_ROCKCHIP_PID", pid)
                self.put("USB_ROCKCHIP_MODE", USB_ROCKCHIP_PIDS.get(pid, "other"))
        else:
            return
        self.values.update(flags)
        if described:
            vendor, model, device = described
            self.put("NPU_VENDOR", vendor)
            self.put("NPU_MODEL", model, warn_invalid=False)
            self.put("NPU_DEVICE", device, warn_invalid=False)

    # --- GPU ---

    def _gpu(self):
        """GPUs and the Vulkan flag. A vulkaninfo that ran and lists only software renderers (llvmpipe, lavapipe)
        means that no GPU has a Vulkan driver: VULKAN stays OFF then, whatever the display adapters, the Vulkan
        loader or vulkan-1.dll suggest (those only stand in when vulkaninfo is missing or fails)."""
        run, os_name = self.run, self.os
        nvidia, vk, vk_ran, vulkan = [], [], False, False
        hip = None
        if os_name == "macos":
            others = self._gpu_macos()
        else:
            nvidia, smi = probe_nvidia(run, os_name, self.env)
            if nvidia and smi:
                self.put("NVIDIA_DRIVER_CUDA_MAX", nvidia_cuda_max(run, smi), warn_invalid=False)
            vk_ran, vk = probe_vulkaninfo(run)
            others = [g for g in vk if g["VENDOR"] != "nvidia" or not nvidia]
            if os_name == "windows":
                adapters = [g for g in probe_windows_adapters(run) if g["VENDOR"] != "nvidia" or not nvidia]
                if not vk:
                    others = adapters
                    vulkan = not vk_ran and bool(adapters)
                else:
                    # vulkaninfo knows the device type, the registry the memory size.
                    by_name = {norm_name(a.get("NAME")): a for a in adapters}
                    for g in others:
                        match = by_name.get(norm_name(g.get("NAME")))
                        if match and match.get("VRAM_MB") and g.get("UNIFIED") != "ON":
                            g["VRAM_MB"] = match["VRAM_MB"]
                        if match and match.get("PCI_ID") and "PCI_ID" not in g:
                            g["PCI_ID"] = match["PCI_ID"]
                if not vulkan and not vk_ran:
                    root = self.env.get("SystemRoot") or self.env.get("SYSTEMROOT")
                    vulkan = bool(root) and os.path.isfile(os.path.join(root, "System32", "vulkan-1.dll"))
            elif os_name in ("linux", "android"):
                sysfs = [g for g in probe_linux_sysfs(self.sys_dir) if g["VENDOR"] != "nvidia" or not nvidia]
                tree = self._gpu_device_tree()
                if not vk:
                    others = sysfs + tree
                    # Without vulkaninfo: a Vulkan loader plus a GPU whose driver ships a Vulkan ICD.
                    vulkan = not vk_ran and bool((others or nvidia) and self._vulkan_loader())
                else:
                    others = merge_sysfs(others, sysfs + tree)
                hip = self._hip()
            if vk_ran:
                self.put("VULKAN_DEVICES", len(vk), warn_invalid=False)
        gpus = (nvidia + others)[:MAX_GPUS]
        self.gpus = gpus
        self.values["VULKAN"] = "ON" if (vulkan or vk) else "OFF"
        self.values["SUPPORTS_CUDA"] = "ON" if any(g["VENDOR"] == "nvidia" for g in gpus) else "OFF"
        self.values["SUPPORTS_INTEL_XPU"] = "ON" if any(g["VENDOR"] == "intel" for g in gpus) else "OFF"
        if os_name == "linux":
            self.values["HIP_RUNTIME"] = "ON" if hip else "OFF"
            rocm = first_number(self.read("/opt/rocm/.info/version", 4096) or "", 4)
            self.put("ROCM_VERSION", rocm, warn_invalid=False)
        self.values["METAL"] = "ON" if os_name == "macos" and self._metal else "OFF"

    def _vulkan_loader(self):
        if self.injected_find_library is not None or not self.sysroot:
            if self.find_library("vulkan"):
                return True
            if not self.sysroot:
                return False
        patterns = ("/usr/lib/*/libvulkan.so.1", "/usr/lib/libvulkan.so.1", "/usr/lib64/libvulkan.so.1",
                    "/lib/*/libvulkan.so.1", "/lib/libvulkan.so.1", "/lib64/libvulkan.so.1")
        return any(glob.glob(self._glob(p)) for p in patterns)

    def _hip(self):
        globs = self.rocm_globs
        if globs is None:
            globs = (self._glob("/opt/rocm*/lib*/libamdhip64.so*"),)
        if self.sysroot and self.injected_find_library is None:
            return any(glob.glob(p) for p in globs)
        return hip_runtime(self.find_library, globs)

    def _gpu_device_tree(self):
        """Mali GPUs of the device tree (top-level nodes whose compatible names arm,mali-*, status okay)."""
        gpus = []
        for base in self._device_tree_bases():
            root = self.path(base)
            if not os.path.isdir(root):
                continue
            for node in sorted(glob.glob(os.path.join(glob.escape(root), "*", "compatible"))):
                entries = [c.strip().lower() for c in (_read(node, 4096) or "").split("\0") if c.strip()]
                mali = next((c for c in entries if c.startswith("arm,mali-")), None)
                if mali is None:
                    continue
                status = (_read(os.path.join(os.path.dirname(node), "status"), 64) or "okay").strip("\0 \n")
                if status not in ("okay", "ok"):
                    continue
                gpus.append({"VENDOR": "arm", "NAME": mali_name(mali), "DISCRETE": "OFF", "UNIFIED": "ON",
                             "SOURCE": "device-tree"})
            break
        return gpus

    def _gpu_macos(self):
        rc, out, _ = self.run(["system_profiler", "SPDisplaysDataType", "-json"], 30)
        gpus = []
        try:
            items = json.loads(out).get("SPDisplaysDataType", []) if rc == 0 else []
        except (ValueError, AttributeError):
            items = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get("sppci_model") or item.get("_name") or "")
            vendor_text = str(item.get("spdisplays_vendor") or item.get("sppci_vendor") or "").lower()
            vendor_id = hex_id(str(item.get("spdisplays_vendor-id") or "").strip(), 4)
            vendor = PCI_VENDORS.get(vendor_id or "") or ("apple" if "apple" in vendor_text else
                                                          "amd" if ("amd" in vendor_text or "ati" in vendor_text)
                                                          else "intel" if "intel" in vendor_text else "other")
            apple = vendor == "apple"
            gpu = {"VENDOR": vendor, "NAME": name or "GPU", "SOURCE": "system-profiler",
                   "DISCRETE": "OFF" if apple or vendor == "intel" else "ON",
                   "UNIFIED": "ON" if apple or vendor == "intel" else "OFF"}
            if any("metal" in str(k).lower() and str(v).strip() for k, v in item.items()):
                self._metal = True
            gpus.append(gpu)
        if self.arch == "arm64":
            self._metal = True  # every Apple Silicon GPU supports Metal
            if not any(g["VENDOR"] == "apple" for g in gpus):
                gpus.insert(0, {"VENDOR": "apple", "NAME": "Apple Silicon GPU", "DISCRETE": "OFF", "UNIFIED": "ON",
                                "SOURCE": "system-profiler"})
        return gpus

    # --- toolchain ---

    def _toolchain(self):
        tools = (("TOOL_CMAKE", ["cmake", "--version"]), ("TOOL_NINJA", ["ninja", "--version"]),
                 ("TOOL_GIT", ["git", "--version"]), ("TOOL_CCACHE", ["ccache", "--version"]),
                 ("TOOL_DOCKER", ["docker", "--version"]))
        if self.os != "windows":
            tools += (("TOOL_MAKE", ["make", "--version"]),)
        if self.os == "linux":
            tools += (("TOOL_HIPCONFIG", ["hipconfig", "--version"]),)
        for key, cmd in tools:
            out = self.tool_output(cmd)
            if out is not None:
                self.put(key, first_number(out.strip().splitlines()[0] if out.strip() else ""), warn_invalid=False)
        out = self.tool_output(["nvcc", "--version"])
        match = re.search(r"release \d+\.\d+, V(\d+(?:\.\d+){1,3})", out or "")
        if match:
            self.put("TOOL_NVCC", match.group(1), warn_invalid=False)
        out = self.tool_output(["glslc", "--version"])
        if out:
            line = next((ln for ln in out.splitlines() if "shaderc" in ln), out.strip().splitlines()[0])
            self.put("TOOL_GLSLC", first_number(line), warn_invalid=False)
        if self.os == "windows":
            clang_cl, msvc = self.tool_output(["clang-cl", "--version"]), self.tool_output(["cl"])
            self.put("TOOL_CC", compiler_id(clang_cl or msvc or ""), warn_invalid=False)
            self.put("TOOL_CXX", self.values.get("TOOL_CC"), warn_invalid=False)
            match = re.search(r"clang version (\d+(?:\.\d+){0,3})", clang_cl or "")
            self.put("TOOL_CLANG", match.group(1) if match else None, warn_invalid=False)
            match = re.search(r"Version (\d+(?:\.\d+){1,3})", msvc or "")
            if match:
                self.put("TOOL_MSVC", match.group(1), warn_invalid=False)
        else:
            self.put("TOOL_CC", compiler_id(self.tool_output(["cc", "--version"]) or ""), warn_invalid=False)
            self.put("TOOL_CXX", compiler_id(self.tool_output(["c++", "--version"]) or ""), warn_invalid=False)
            clang = self.tool_output(["clang", "--version"])
            if clang:
                match = re.search(r"clang version (\d+(?:\.\d+){0,3})", clang)
                self.put("TOOL_CLANG", match.group(1) if match else None, warn_invalid=False)
        self.put("TOOL_PYTHON", python_version())

    # --- the result ---

    def _finish(self):
        values, os_name, arch = self.values, self.os, self.arch
        # Every bool is written: OFF outside its ARCH or OS, else UNKNOWN when nothing was learned.
        for key, rule in KEYS.items():
            if rule[0] != "bool":
                continue
            outside = (rule[3] and arch not in rule[3]) or (rule[4] and os_name not in rule[4])
            if outside:
                values[key] = "OFF"
            elif key not in values:
                values[key] = "UNKNOWN"
        # Keys that only exist on some ARCH or OS, and values that only exist on some OS, are dropped elsewhere.
        for key, rule in KEYS.items():
            if rule[0] != "bool" and key in values and ((rule[3] and arch not in rule[3]) or
                                                         (rule[4] and os_name not in rule[4])):
                del values[key]
        for key, allowed in OS_VALUES.items():
            value = values.get(key)
            if value in allowed and os_name not in allowed[value]:
                del values[key]
        if "gpu" in self.sections or "GPU_COUNT" not in values:
            values["GPU_COUNT"] = str(len(self.gpus))
        result = {}
        for key in KEYS:
            if key == "PROBE_WARNINGS":
                continue
            if key in values:
                result[key] = values[key]
            if key == "GPU_COUNT":
                for index, gpu in enumerate(self.gpus):
                    for suffix, rule in GPU_KEYS.items():
                        value = gpu.get(suffix)
                        if rule[0] == "bool" and value is None:
                            value = "UNKNOWN"
                        if rule[3] and gpu.get("VENDOR") not in rule[3]:
                            value = None
                        checked = check_value(rule, value)
                        if checked is not None:
                            result["GPU{}_{}".format(index, suffix)] = checked
        warnings = check_value(KEYS["PROBE_WARNINGS"], ",".join(self.warnings)) if self.warnings else None
        if warnings:
            result["PROBE_WARNINGS"] = warnings
        return result


def mali_name(compatible):
    """"arm,mali-bifrost" -> "Mali (bifrost)", "arm,mali-t860" -> "Mali-T860"."""
    kind = compatible.split("mali-", 1)[1]
    if kind in ("bifrost", "valhall", "valhall-csf", "midgard", "utgard"):
        return "Mali ({})".format(kind)
    return "Mali-" + kind.upper()


def merge_sysfs(vk_gpus, found):
    """vulkaninfo's GPUs enriched with what sysfs and the device tree know (PCI id match first; AMD GPUs without a
    PCI id are paired by order when the counts agree); GPUs of a vendor vulkaninfo does not list are added."""
    vk_gpus = [dict(g) for g in vk_gpus]
    left = list(found)
    for g in vk_gpus:
        match = next((f for f in left if g.get("PCI_ID") and f.get("PCI_ID") == g["PCI_ID"]), None)
        if match:
            _enrich(g, match)
            left.remove(match)
    amd_vk = [g for g in vk_gpus if g["VENDOR"] == "amd" and "GFX_TARGET" not in g and "VRAM_MB" not in g]
    amd_fs = [f for f in left if f["VENDOR"] == "amd"]
    if amd_vk and len(amd_vk) == len(amd_fs):
        for g, f in zip(amd_vk, amd_fs):
            _enrich(g, f)
            left.remove(f)
    listed = {g["VENDOR"] for g in vk_gpus}
    return vk_gpus + [f for f in left if f["VENDOR"] not in listed]


def _enrich(gpu, found):
    for key in ("VRAM_MB", "VRAM_FREE_MB", "GFX_TARGET", "PCI_ID"):
        if key in found:
            gpu[key] = found[key]


def windows_feature_value(arch, key, constant, min_build, build, ask):
    """ON/OFF/UNKNOWN of one CPU feature on Windows (see CPU_FEATURES)."""
    if arch not in KEYS[key][3]:
        return "OFF"
    if constant is None:
        return "UNKNOWN"
    answer = ask(constant)
    if answer is True:
        return "ON"
    if answer is False and build and min_build is not None and build >= min_build:
        return "OFF"
    return "UNKNOWN"


def python_version(info=None):
    """major.minor.micro of this Python (a pre-release such as 3.14.0rc1 gives 3.14.0, as `python3 --version` read by
    the .sh does)."""
    info = sys.version_info if info is None else info
    return "{}.{}.{}".format(info[0], info[1], info[2])


def compiler_id(banner):
    """`cc --version` (or cl / clang-cl) banner -> gcc-12.2.0, clang-18.1.3, appleclang-16.0.0, msvc-19.44.35207."""
    first = (banner or "").strip().splitlines()[0] if (banner or "").strip() else ""
    match = re.search(r"Apple clang version (\d+(?:\.\d+){0,3})", banner or "")
    if match:
        return "appleclang-" + match.group(1)
    match = re.search(r"clang version (\d+(?:\.\d+){0,3})", banner or "")
    if match:
        return "clang-" + match.group(1)
    match = re.search(r"Intel\(R\) oneAPI DPC\+\+/C\+\+ Compiler (\d+(?:\.\d+){0,3})", banner or "")
    if match:
        return "icx-" + match.group(1)
    match = re.search(r"Microsoft \(R\) C/C\+\+ Optimizing Compiler Version (\d+(?:\.\d+){0,3})", banner or "")
    if match:
        return "msvc-" + match.group(1)
    if "Free Software Foundation" in (banner or "") or re.search(r"\b(gcc|GCC|g\+\+)\b", first):
        number = first_number(first.rsplit(")", 1)[-1]) or first_number(first)
        return "gcc-" + number if number else None
    return None


def parse_os_release(text):
    """KEY=value lines of /etc/os-release (shell quoting: "..." and '...' removed, backslash escapes undone)."""
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
            if line.partition("=")[2].strip().startswith('"'):
                value = re.sub(r"\\(.)", r"\1", value)
        if re.match(r"^[A-Z][A-Z0-9_]*$", key.strip()):
            fields.setdefault(key.strip(), value)
    return fields


def parse_cpuinfo(text):
    """[(processor number, {field: value})] of /proc/cpuinfo; lines outside a processor block (Hardware, Revision,
    Serial, ...) are ignored, so a serial number never reaches the result."""
    processors, current = [], None
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "processor" and re.match(r"^\d{1,5}$", value):
            current = {}
            processors.append((int(value), current))
        elif current is not None and key and key not in current and key.lower() != "serial":
            current[key] = value
    return processors


def parse_meminfo(text):
    """{field: bytes} of /proc/meminfo (kB values)."""
    result = {}
    for line in text.splitlines():
        match = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
        if match:
            result[match.group(1)] = int(match.group(2)) * 1024
    return result


def parse_swapusage(text):
    match = re.search(r"total = ([\d.]+)([MG])", text or "")
    if not match:
        return None
    return int(float(match.group(1)) * (GIB if match.group(2) == "G" else MIB))


# --- public entry points ------------------------------------------------------------------------------------------

def collect(**options):
    """Probes this machine; {KEY: value} in the order of the schema, PROBE_WARNINGS last. Options: root, sys_dir,
    run, find_library, system, machine, windows_api, memory, rocm_globs, disk_path, now, sections, env."""
    return Probe(**options).collect()


def missing_required(values):
    """Required keys that are missing (the parser rejects such a file)."""
    return [k for k, rule in KEYS.items() if rule[2] and k not in values]


def render_text(values):
    lines = ["# LLM Cross-Compiler Framework hardware profile (schema 2, hardware_probe.py {})".format(TOOL_VERSION)]
    lines += ["{}={}".format(k, v) for k, v in values.items()]
    return "\n".join(lines) + "\n"


def render_json(values):
    return json.dumps(values, indent=2, ensure_ascii=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hardware_probe.py",
        description="Hardware probe of the LLM Cross-Compiler Framework: writes " + FILE_NAME + " (schema 2).")
    parser.add_argument("-o", "--output", default=FILE_NAME, help="file to write, '-' for standard output "
                        "(default: %(default)s in the current folder)")
    parser.add_argument("--json", action="store_true", help="write the JSON view instead of KEY=VALUE lines")
    parser.add_argument("--no-toolchain", action="store_true", help="skip the compiler and build tool versions")
    parser.add_argument("--disk-path", default=None, help="folder whose free space DISK_FREE_MB reports "
                        "(default: LLM_FRAMEWORK_HOME, ~/.llm-framework or the home folder)")
    args = parser.parse_args(argv)
    if not args.output:
        parser.error("--output must not be empty")
    sections = [s for s in SECTIONS if not (args.no_toolchain and s == "toolchain")]
    values = collect(sections=sections, disk_path=args.disk_path)
    text = render_json(values) if args.json else render_text(values)
    missing = missing_required(values)
    if args.output == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            sys.stderr.write("[PROBE] cannot write {}: {}\n".format(args.output, e.strerror or e))
            return 1
        sys.stderr.write("[PROBE] Wrote {} ({} keys).\n".format(args.output, len(values)))
    if missing:
        sys.stderr.write("[PROBE] Could not determine required keys: {}\n".format(", ".join(missing)))
        return 1
    return 0


if __name__ == "__main__":
    if sys.version_info < (3, 8):
        sys.stderr.write("hardware_probe.py needs Python 3.8 or newer; use hardware_probe.sh instead.\n")
        sys.exit(3)
    sys.exit(main())
