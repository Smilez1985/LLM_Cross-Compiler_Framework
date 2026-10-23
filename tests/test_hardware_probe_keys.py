"""
The keys of hardware probe 3.1.0 (decisions round 23 item 11, O-PREP and O-HAILO; HAILO_PLAN.md section 2): the
largest level-2 cache, the Vulkan instance version and the OpenCL platforms, the Hailo keys and the vendor runtime
keys. Each is read the way the existing keys are read; the .py and the .sh run on the same fixture machines with the
same fake tools and must agree, and the .ps1 carries the same rules. A profile of probe 3.0.0, which has none of these
keys, stays readable.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from test_hardware_probe import (
    FAKE_VULKANINFO,
    FIXTURES,
    POSIX_ONLY,
    PROBE_PS1,
    PROBE_PY,
    PROBE_SH,
    FakeWindows,
    available_shells,
    comparable,
    make_sysroot,
    probe,
    py_probe,
    run_emitter,
    tool_dir,
    windows_runner,
    write,
)

from orchestrator.runtime.profile import ProfileStore, parse_profile, parse_profile_json

SHELLS = available_shells()
NEEDS_SHELL = pytest.mark.skipif(not SHELLS, reason="no POSIX shell")


def fake_tool(bindir: Path, name: str, text: str) -> None:
    path = bindir / name
    path.write_text(text)
    path.chmod(0o755)


def both(tmp_path, root, bindir, shell):
    """(.sh output, .py output) on the same sysroot and PATH."""
    sh_text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
    py_text = run_emitter([sys.executable, "-I", str(PROBE_PY)], root, bindir, tmp_path)
    return sh_text, py_text


# --- item 11: L2 cache, Vulkan instance version, OpenCL platforms ---------------------------------------------------

def caches(root, cpus, levels):
    """/sys/devices/system/cpu/cpu<N>/cache/index<M> with (level, type, size) per index."""
    for cpu in range(cpus):
        for index, (level, kind, size) in enumerate(levels):
            base = f"/sys/devices/system/cpu/cpu{cpu}/cache/index{index}"
            write(root, base + "/level", f"{level}\n")
            write(root, base + "/type", kind + "\n")
            if size is not None:
                write(root, base + "/size", size + "\n")


# clinfo's human output (labels from clinfo.c; the layout of a published three-platform run): every platform's block,
# then per platform its name again before "Number of devices".
FAKE_CLINFO = """#!/bin/sh
cat <<'EOF'
Number of platforms                               3
  Platform Name                                   NVIDIA CUDA
  Platform Vendor                                 NVIDIA Corporation
  Platform Version                                OpenCL 3.0 CUDA 12.4.89
  Platform Profile                                FULL_PROFILE
  Platform Name                                   Intel(R) OpenCL Graphics
  Platform Vendor                                 Intel(R) Corporation
  Platform Version                                OpenCL 3.0
  Platform Name                                   Portable Computing Language
  Platform Vendor                                 The pocl project
  Platform Version                                OpenCL 3.0 PoCL 5.0+debian  Linux, None+Asserts, RELOC, LLVM 16.0.6

  Platform Name                                   NVIDIA CUDA
Number of devices                                 1
  Device Name                                     NVIDIA GeForce RTX 3090
  Device Version                                  OpenCL 3.0 CUDA

  Platform Name                                   Intel(R) OpenCL Graphics
Number of devices                                 1
  Device Name                                     Intel(R) UHD Graphics 620

ICD loader properties
  ICD loader Name                                 OpenCL ICD Loader
  ICD loader Version                              2.3.2
  ICD loader Profile                              OpenCL 3.0
EOF
"""
FAKE_VULKANINFO_VERSION = FAKE_VULKANINFO.replace("cat <<'EOF'\n", "cat <<'EOF'\n==========\nVULKANINFO\n==========\n\n"
                                                  "Vulkan Instance Version: 1.4.305\n\n\n", 1)


class TestCacheVulkanOpenCL:

    def test_parse_clinfo(self):
        out = FAKE_CLINFO.split("cat <<'EOF'\n", 1)[1].rsplit("EOF", 1)[0]
        assert probe.parse_clinfo(out) == ["nvidia-cuda:3.0", "intel-opencl-graphics:3.0",
                                           "portable-computing-language:3.0"]
        assert probe.parse_clinfo("Number of platforms 0\n") == []
        assert probe.opencl_name("Intel(R) FPGA Emulation Platform for OpenCL(TM)") == \
            "intel-fpga-emulation-platform-for-opencl"
        assert probe.opencl_name("AMD Accelerated Parallel Processing") == "amd-accelerated-parallel-processing"
        assert probe.opencl_name("rusticl") == "rusticl" and probe.opencl_name("(R)") is None
        assert len(probe.opencl_name("x" * 47 + " y" * 10)) <= 48

    def test_vulkan_instance_version_line(self):
        assert probe.vulkan_instance_version("==========\nVULKANINFO\n==========\n\nVulkan Instance Version: "
                                             "1.3.275\n\n\nInstance Extensions: count = 24\n") == "1.3.275"
        assert probe.vulkan_instance_version("Devices:\n") is None

    @POSIX_ONLY
    def test_l2_cache_from_sysfs(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3588")
        caches(root, 4, [(1, "Data", "32K"), (1, "Instruction", "32K"), (2, "Unified", "128K"), (3, "Unified", "3072K")])
        for cpu in range(4, 8):
            for index, (level, kind, size) in enumerate([(1, "Data", "64K"), (2, "Unified", "512K")]):
                base = f"/sys/devices/system/cpu/cpu{cpu}/cache/index{index}"
                write(root, base + "/level", f"{level}\n")
                write(root, base + "/type", kind + "\n")
                write(root, base + "/size", size + "\n")
        values = py_probe(root)
        assert values["CPU_L2_CACHE_KB"] == "512"   # the big cores' L2, not cpu0's
        assert "CPU_L2_CACHE_KB" not in values.get("PROBE_WARNINGS", "")

    @POSIX_ONLY
    @pytest.mark.parametrize("levels, value, warned", [
        ([(1, "Data", "32K"), (3, "Unified", "512K")], None, False),          # RK3566: L1 and L3 only
        ([(1, "Data", "32K"), (2, "Unified", None)], None, True),             # an L2 without a size
        ([(2, "Instruction", "64K")], None, False),                          # not a data cache
        ([(2, "Unified", "2M")], "2048", False),
    ])
    def test_l2_cache_rules(self, tmp_path, levels, value, warned):
        root = make_sysroot(tmp_path, "rk3566")
        caches(root, 4, levels)
        values = py_probe(root)
        assert values.get("CPU_L2_CACHE_KB") == value
        assert ("not_determined:CPU_L2_CACHE_KB" in values.get("PROBE_WARNINGS", "")) is warned

    @POSIX_ONLY
    @NEEDS_SHELL
    @pytest.mark.parametrize("shell", SHELLS)
    def test_sh_and_py_agree(self, tmp_path, shell):
        root = make_sysroot(tmp_path, "intel")
        caches(root, 8, [(1, "Data", "32K"), (1, "Instruction", "32K"), (2, "Unified", "256K"), (3, "Unified", "6144K")])
        write(root, "/usr/lib/x86_64-linux-gnu/libvulkan.so.1.3.275", b"")
        bindir = tool_dir(tmp_path)
        fake_tool(bindir, "clinfo", FAKE_CLINFO)
        sh_text, py_text = both(tmp_path, root, bindir, shell)
        assert comparable(sh_text) == comparable(py_text)
        for text in (sh_text, py_text):
            profile = parse_profile(text)
            assert profile.get("CPU_L2_CACHE_KB") == "256"
            assert profile.get("VULKAN_INSTANCE_VERSION") == "1.3.275"   # the loader's file name (no vulkaninfo)
            assert profile.items_of("OPENCL_PLATFORMS") == ["nvidia-cuda:3.0", "intel-opencl-graphics:3.0",
                                                            "portable-computing-language:3.0"]
            assert profile.get("PROBE_TOOL_VERSION") == "3.1.0"

    @POSIX_ONLY
    @NEEDS_SHELL
    @pytest.mark.parametrize("shell", SHELLS)
    def test_vulkaninfo_line_wins_and_a_failing_clinfo_gives_nothing(self, tmp_path, shell):
        root = make_sysroot(tmp_path, "intel")
        write(root, "/usr/lib/x86_64-linux-gnu/libvulkan.so.1.3.275", b"")
        bindir = tool_dir(tmp_path, fake_gpu_tools=True)
        fake_tool(bindir, "vulkaninfo", FAKE_VULKANINFO_VERSION)
        fake_tool(bindir, "clinfo", FAKE_CLINFO + "exit 1\n")   # a clinfo that crashed after its output
        sh_text, py_text = both(tmp_path, root, bindir, shell)
        assert comparable(sh_text) == comparable(py_text)
        for text in (sh_text, py_text):
            profile = parse_profile(text)
            assert profile.get("VULKAN_INSTANCE_VERSION") == "1.4.305" and "OPENCL_PLATFORMS" not in profile
            assert not any("L2" in w or "OPENCL" in w or "VULKAN" in w for w in profile.warnings)

    def test_windows_l2_cache_and_vulkan_loader(self):
        class Api(FakeWindows):
            def l2_cache_kb(self):
                return 1280

            def file_version(self, path):
                return "1.3.296.0" if path.endswith("vulkan-1.dll") else "10.0.22621.3672"

        values = probe.collect(system="Windows", windows_api=Api(), run=windows_runner(), env={"SystemRoot": "C:\\W"},
                               root=os.sep)
        assert values["CPU_L2_CACHE_KB"] == "1280" and values["VULKAN_INSTANCE_VERSION"] == "1.3.296"

        class Unknown(FakeWindows):
            def l2_cache_kb(self):
                return None

        values = probe.collect(system="Windows", windows_api=Unknown(), run=windows_runner(), env={}, root=os.sep)
        assert "CPU_L2_CACHE_KB" not in values and "not_determined:CPU_L2_CACHE_KB" in values["PROBE_WARNINGS"]

    def test_macos_l2_cache(self, tmp_path):
        answers = {"sysctl -n hw.optional.arm64": (0, "1\n", ""), "sysctl -n hw.perflevel0.l2cachesize": (0, "16777216\n", ""),
                   "sysctl -n hw.perflevel1.l2cachesize": (0, "4194304\n", ""), "sysctl -n hw.l2cachesize": (0, "4194304\n", "")}

        def run(cmd, timeout):
            return answers.get(" ".join(cmd), (1, "", "sysctl: unknown oid"))

        values = probe.collect(root=str(tmp_path), system="Darwin", machine="arm64", run=run, sections=("cpu",))
        assert values["CPU_L2_CACHE_KB"] == "16384"

    def test_ps1_reads_the_same_sources(self):
        text = PROBE_PS1.read_text(encoding="utf-8")
        assert "GetLogicalProcessorInformation" in text and "if (info.Relationship != 2)" in text
        assert "'(?m)^\\s*Vulkan Instance Version:\\s*([0-9][0-9.]*)'" in text and "System32\\vulkan-1.dll" in text
        assert "Invoke-Tool 'clinfo'" in text and "'^Platform Version[ \\t]+OpenCL ([0-9]+\\.[0-9]+)'" in text
        # A failure of the cache type must not take the other native calls with it.
        assert text.index("$CacheCode = @\"") > text.index("$HaveNative = $true")


# --- 3.0.0 profiles stay readable -----------------------------------------------------------------------------------

class TestVersion:

    def test_every_emitter_is_3_1_0(self):
        assert probe.TOOL_VERSION == "3.1.0"
        assert "PROBE_VERSION=3.1.0" in PROBE_SH.read_text(encoding="utf-8")
        assert "$ProbeVersion = '3.1.0'" in PROBE_PS1.read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("schema2_*.txt")))
    def test_a_3_0_0_profile_stays_readable(self, tmp_path, name):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        profile = parse_profile(text)
        assert profile.get("PROBE_TOOL_VERSION") == "3.0.0"
        assert not any(k.startswith(("HAILO", "CPU_L2", "OPENCL", "VULKAN_INSTANCE")) for k in profile.values)
        store = ProfileStore(tmp_path / "state")
        store.save_device("0123456789abcdef", profile)
        assert store.load_device("0123456789abcdef") == profile.stable()
        assert parse_profile_json(json.dumps(profile.as_dict())) == profile

    def test_new_keys_are_never_required_or_booleans(self):
        """A file of an older probe lacks the new keys: none of them may be required or an ON/OFF key."""
        from test_hardware_probe import RAW_SCHEMA
        old = parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8"))
        new_keys = [k for k in RAW_SCHEMA["keys"] if k not in old.values and RAW_SCHEMA["keys"][k].get("writer") is None]
        assert "CPU_L2_CACHE_KB" in new_keys
        for key in new_keys:
            spec = RAW_SCHEMA["keys"][key]
            if key.startswith(("CPU_L2", "VULKAN_INSTANCE", "OPENCL", "HAILO", "JETSON", "CUDA_TOOLKIT", "TENSORRT",
                               "OPENVINO", "INTEL_", "ROCM_GFX")):
                assert spec["type"] != "bool" and not spec.get("required"), key
