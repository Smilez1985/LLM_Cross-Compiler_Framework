"""
The keys of hardware probe 3.1.0 (decisions round 23 item 11, O-PREP and O-HAILO; HAILO_PLAN.md section 2): the
largest level-2 cache, the Vulkan instance version and the OpenCL platforms, the Hailo keys and the vendor runtime
keys. Each is read the way the existing keys are read; the .py and the .sh run on the same fixture machines with the
same fake tools and must agree, and the .ps1 carries the same rules. A profile of probe 3.0.0, which has none of these
keys, stays readable.
"""

import json
import os
import re
import shutil
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
    pci_device,
    probe,
    py_probe,
    run_emitter,
    tool_dir,
    usb_device,
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
            assert profile.get("PROBE_TOOL_VERSION") == "3.2.0"

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

    @POSIX_ONLY
    @NEEDS_SHELL
    @pytest.mark.parametrize("shell", SHELLS)
    def test_a_loader_link_counts_in_both(self, tmp_path, shell):
        """Both look through the loader folders the same way: a link named libvulkan.so.1.Y.Z counts whether or not
        its target is inside the sysroot, as the TensorRT and OpenVINO file names do in both."""
        root = make_sysroot(tmp_path, "intel")
        os.symlink("/opt/vulkan-sdk/libvulkan.so.1.3.250", root / "usr/lib/x86_64-linux-gnu/libvulkan.so.1.3.250")
        write(root, "/usr/lib64/libvulkan.so.1.3.275", b"")
        sh_text, py_text = both(tmp_path, root, tool_dir(tmp_path), shell)
        assert comparable(sh_text) == comparable(py_text)
        assert parse_profile(sh_text).get("VULKAN_INSTANCE_VERSION") == "1.3.250"   # the first loader folder

    def test_the_quick_gpu_probe_runs_no_new_tool(self, tmp_path):
        """detect_hardware() reads free RAM and the GPUs at every start of the local runtime and for a build's
        estimate: it runs the tools it ran before probe 3.1.0, never clinfo. The full probe reads the vendor
        runtimes (OpenCL platforms, the loader version, CUDA, TensorRT, OpenVINO, ROCm, the Intel GPU)."""
        from orchestrator.runtime import hardware
        for system, machine, tools in (("Linux", "x86_64", {"nvidia-smi", "vulkaninfo"}),
                                       ("Windows", "AMD64", {"nvidia-smi", "vulkaninfo", "powershell"})):
            calls = []

            def run(cmd, timeout, calls=calls):
                calls.append(cmd[0])
                return -1, "", "not found"
            hardware.detect_hardware(run, system=system, machine=machine, memory=(8 << 30, 4 << 30),
                                     sysfs_root=tmp_path / "sys", find_library=lambda name: None, rocm_globs=())
            assert set(calls) == tools, system
        calls = []
        probe.collect(root=str(tmp_path), system="Linux", machine="x86_64",
                      run=lambda cmd, timeout: calls.append(cmd[0]) or (-1, "", "not found"))
        assert "clinfo" in calls

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

    def test_every_emitter_is_3_2_0(self):
        assert probe.TOOL_VERSION == "3.2.0"
        assert "PROBE_VERSION=3.2.0" in PROBE_SH.read_text(encoding="utf-8")
        assert "$ProbeVersion = '3.2.0'" in PROBE_PS1.read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("schema2_*.txt")))
    def test_a_3_0_0_profile_stays_readable(self, tmp_path, name):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        profile = parse_profile(text)
        assert profile.get("PROBE_TOOL_VERSION") == "3.0.0"
        assert not any(k.startswith(("HAILO", "CPU_L2", "OPENCL", "VULKAN_INSTANCE")) for k in profile.values)
        # probe 3.2.0's CPU keys of the other architectures: OFF on these x86 and ARM machines, no probe_again
        assert profile.flag("SUPPORTS_RVV") == profile.flag("SUPPORTS_VXE2") == "OFF"
        assert not [n for n in profile.notes if n.startswith("probe_again:")]
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


# --- O-HAILO: the Hailo keys (HAILO_PLAN.md section 2, fixtures of 6.1 item 1) ---------------------------------------

HAILO = FIXTURES / "hailo"
SERIAL = "c0ffee00deadbeef"   # the made-up serial number of the identify fixtures: it must never come out


def hailo_text(name):
    return (HAILO / name).read_bytes().decode("utf-8")


def board(tmp_path):
    """A board without a Rockchip NPU (a Raspberry Pi 5 stand-in), so NPU_VENDOR describes the Hailo."""
    root = make_sysroot(tmp_path, "rk3566")
    (root / "dev/rknpu").unlink()
    write(root, "/proc/device-tree/compatible", b"raspberrypi,5-model-b\0brcm,bcm2712\0")
    write(root, "/proc/device-tree/model", b"Raspberry Pi 5 Model B Rev 1.0\0")
    return root


def hailo8_files(root, version="4.24.0"):
    write(root, "/sys/module/hailo_pci/version", version + "\n")
    write(root, "/sys/class/hailo_chardev/hailo0/device_id", "1e60:2864\n")
    write(root, "/dev/hailo0", b"")
    pci_device(root, "0000:01:00.0", "0x1e60", "0x2864", "0x120000")


def hailo10_files(root):
    write(root, "/sys/module/hailo1x_pci/version", "5.4.0\n")
    write(root, "/sys/class/hailo1x/h1x-0/device_id", "0000:01:00.0\n")
    write(root, "/dev/h1x-0", b"")
    pci_device(root, "0000:01:00.0", "0x1e60", "0x45c4", "0x120000")


def fake_hailortcli(bindir, version="version_4.24.0.txt", version_err=None, version_rc=0, scan=None, identify=None,
                    scan_rc=0):
    """A hailortcli on PATH that answers -v, scan and fw-control identify -s <id> from the fixture files. `true` goes
    on PATH too, so the .sh finds a working `timeout` and runs every tool with its time limit, as on a board."""
    if shutil.which("true") and not (bindir / "true").exists():
        os.symlink(shutil.which("true"), bindir / "true")
    data = bindir.parent / "hailo-data"
    data.mkdir(exist_ok=True)
    (data / "v.out").write_bytes((HAILO / version).read_bytes() if version else b"")
    (data / "v.err").write_bytes((HAILO / version_err).read_bytes() if version_err else b"")
    (data / "scan.out").write_bytes((HAILO / scan).read_bytes() if scan else b"")
    cases = []
    for index, (device, name) in enumerate((identify or {}).items()):
        (data / f"id{index}.out").write_bytes((HAILO / name).read_bytes())
        cases.append(f"      '{device}') cat \"$D/id{index}.out\" ;;")
    script = "\n".join([
        "#!/bin/sh",
        f"D='{data}'",
        'case "$1" in',
        f'  -v) cat "$D/v.out"; cat "$D/v.err" >&2; exit {version_rc} ;;',
        f'  scan) cat "$D/scan.out"; exit {scan_rc} ;;',
        '  fw-control)',
        '    [ "$2" = identify ] && [ "$3" = -s ] || exit 2',
        '    case "$4" in',
        *cases,
        "      *) exit 1 ;;",
        "    esac ;;",
        "  *) exit 2 ;;",
        "esac",
        ""])
    fake_tool(bindir, "hailortcli", script)


def run_both(tmp_path, root, bindir):
    """The .py and every available shell's .sh on the same machine: they must agree; the .py's profile."""
    py_text = run_emitter([sys.executable, "-I", str(PROBE_PY)], root, bindir, tmp_path)
    for shell in SHELLS:
        sh_text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
        assert comparable(sh_text) == comparable(py_text), shell
        assert SERIAL not in sh_text
    assert SERIAL not in py_text
    return parse_profile(py_text)


def hailo_view(profile):
    keys = ("SUPPORTS_HAILO", "NPU_VENDOR", "NPU_MODEL", "NPU_DEVICE", "HAILO_FAMILY", "HAILO_ARCH", "HAILO_DEVICES",
            "HAILO_INTERFACE", "HAILO_DRIVER", "HAILO_DRIVER_VERSION", "HAILO_FW_VERSION", "HAILORT_VERSION",
            "HAILO_PYHAILORT_VERSION")
    return {k: profile.get(k) for k in keys if profile.get(k) is not None}


def hailo_warnings(profile):
    return sorted(w for w in profile.warnings if "HAILO" in w)


class TestHailoParsers:

    @pytest.mark.parametrize("name, word, firmware", [
        ("identify_hailo8l_4.17_rpi5_docs.txt", "HAILO8L", "4.17.0"),
        ("identify_hailo10h_model_zoo_docs.txt", "HAILO10H", "4.6.0"),
        ("identify_hailo8_b0_tappas_docs.txt", "HAILO8_B0", None),          # "X.X.X" is no version
        ("identify_current_hailo8_4.24.0.txt", "HAILO8", "4.24.0"),
        ("identify_current_hailo8l_4.24.0.txt", "HAILO8L", "4.24.0"),
        ("identify_current_hailo10h_5.4.0.txt", "HAILO10H", "5.4.0"),
        ("identify_current_hailo15h_5.4.0.txt", "HAILO15H", "5.4.0"),
        ("identify_current_hailo15l_5.4.0.txt", "HAILO15L", "5.4.0"),
        ("identify_current_mars_5.4.0.txt", "MARS", "5.4.0"),
        ("identify_current_unknown_4.24.0.txt", "UNKNOWN", "4.24.0"),
        ("identify_current_hailo8l_nul_bytes.txt", "HAILO8L", "4.24.0"),
        ("identify_empty.txt", None, None),
    ])
    def test_identify(self, name, word, firmware):
        assert probe.parse_hailo_identify(hailo_text(name)) == (word, firmware)

    def test_arch_words_are_looked_up_exactly(self):
        assert probe.HAILO_ARCH_NAMES["HAILO8L"] == "hailo8l" and probe.HAILO_ARCH_NAMES["HAILO8"] == "hailo8"
        assert "HAILO8L_X" not in probe.HAILO_ARCH_NAMES and probe.HAILO_ARCH_NAMES["MARS"] == "hailo12l"
        assert probe.hailo_npu_model("hailo10h") == "hailo-10h" and probe.hailo_npu_model(None) is None

    @pytest.mark.parametrize("name, ids", [
        ("scan_not_found.txt", []), ("scan_one_bdf_4x.txt", ["0000:01:00.0"]),
        ("scan_one_pci_5x.txt", ["pci/0000:01:00.0"]), ("scan_two_pci_5x.txt", ["pci/0000:01:00.0", "pci/0000:02:00.0"]),
        ("scan_usb_5x.txt", ["usb/002:016"]), ("scan_integrated.txt", ["[integrated]"]),
    ])
    def test_scan(self, name, ids):
        assert probe.parse_hailo_scan(hailo_text(name)) == ids
        assert [probe.hailo_id_interface(i) for i in ids] == [
            {"[": "integrated", "u": "usb"}.get(i[0], "pcie") for i in ids]

    def test_scan_ids_of_another_form_are_never_passed_on(self):
        assert probe.parse_hailo_scan("[-] Device: ;reboot\n[-] Device: 0000:01:00.0\n") == ["0000:01:00.0"]
        assert probe.parse_hailo_scan("garbage") is None

    def test_version(self):
        assert probe.parse_hailo_version(hailo_text("version_4.24.0.txt"), 0) == ("4.24.0", None)
        assert probe.parse_hailo_version(hailo_text("version_mismatch_stderr.txt"), 255) == (None, "version_mismatch")
        assert probe.parse_hailo_version("", 255) == (None, "version_mismatch")
        assert probe.parse_hailo_version("HailoRT-CLI\n", 0) == (None, "not_determined")

    def test_nul_bytes_of_scan_and_version_are_dropped(self):
        """A shell drops the NUL bytes of a tool's output (dash and bash do): the .py does the same, so a scan line
        with a NUL behind the id gives the same device in both."""
        assert probe.parse_hailo_scan("Hailo Devices:\n[-] Device: 0000:01:00.0\0\n") == ["0000:01:00.0"]
        assert probe.parse_hailo_scan("Hailo devices not \0found\n") == []
        assert probe.parse_hailo_version("HailoRT-CLI version 4.24\0.0\n", 0) == ("4.24.0", None)


@POSIX_ONLY
@NEEDS_SHELL
class TestHailoMachines:
    """Every fixture machine runs through the .py and the .sh (each available shell); the two must agree."""

    def test_hailo8_on_a_pi_with_hailort(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root)
        write(root, "/usr/lib/python3/dist-packages/hailort-4.24.0.dist-info/METADATA",
              "Metadata-Version: 2.1\nName: hailort\nVersion: 4.24.0\n")
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_one_bdf_4x.txt", identify={"0000:01:00.0": "identify_current_hailo8_4.24.0.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert hailo_view(profile) == {
            "SUPPORTS_HAILO": "ON", "NPU_VENDOR": "hailo", "NPU_MODEL": "hailo-8", "NPU_DEVICE": "/dev/hailo0",
            "HAILO_FAMILY": "hailo8", "HAILO_ARCH": "hailo8", "HAILO_DEVICES": "1", "HAILO_INTERFACE": "pcie",
            "HAILO_DRIVER": "hailo_pci", "HAILO_DRIVER_VERSION": "4.24.0", "HAILO_FW_VERSION": "4.24.0",
            "HAILORT_VERSION": "4.24.0", "HAILO_PYHAILORT_VERSION": "4.24.0"}
        assert hailo_warnings(profile) == []
        facts = profile.hailo()
        assert facts.present and facts.model == "hailo-8" and facts.hailort_line == "4" and facts.devices == 1

    def test_hailo8l_with_the_old_identify_of_the_pi_docs(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root, version="4.17.0")
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_one_bdf_4x.txt", identify={"0000:01:00.0": "identify_hailo8l_4.17_rpi5_docs.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert (profile.get("HAILO_ARCH"), profile.get("NPU_MODEL"), profile.get("HAILO_FW_VERSION")) == \
            ("hailo8l", "hailo-8l", "4.17.0")   # "Board Name: Hailo-8" on an 8L is not read

    @pytest.mark.parametrize("identify, arch", [("identify_current_hailo10h_5.4.0.txt", "hailo10h"),
                                                ("identify_current_hailo15h_5.4.0.txt", "hailo10h")])
    def test_hailo10h_on_pcie(self, tmp_path, identify, arch):
        root = board(tmp_path)
        hailo10_files(root)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, version="version_5.4.0.txt", scan="scan_one_pci_5x.txt",
                        identify={"pci/0000:01:00.0": identify})
        profile = run_both(tmp_path, root, bindir)
        assert hailo_view(profile) == {
            "SUPPORTS_HAILO": "ON", "NPU_VENDOR": "hailo", "NPU_MODEL": "hailo-10h", "NPU_DEVICE": "/dev/h1x-0",
            "HAILO_FAMILY": "hailo1x", "HAILO_ARCH": arch, "HAILO_DEVICES": "1", "HAILO_INTERFACE": "pcie",
            "HAILO_DRIVER": "hailo1x_pci", "HAILO_DRIVER_VERSION": "5.4.0", "HAILO_FW_VERSION": "5.4.0",
            "HAILORT_VERSION": "5.4.0"}
        assert profile.hailo().hailort_line == "5"

    @pytest.mark.parametrize("identify, arch", [("identify_current_hailo15h_5.4.0.txt", "hailo15h"),
                                                ("identify_current_hailo15l_5.4.0.txt", "hailo15l"),
                                                ("identify_current_mars_5.4.0.txt", "hailo12l")])
    def test_integrated_device(self, tmp_path, identify, arch):
        root = board(tmp_path)
        write(root, "/sys/module/hailo_integrated_nnc/version", "5.4.0\n")
        write(root, "/sys/class/hailo1x_integrated/h1x/dev", "240:0\n")
        write(root, "/dev/h1x", b"")
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, version="version_5.4.0.txt", scan="scan_integrated.txt", identify={"[integrated]": identify})
        profile = run_both(tmp_path, root, bindir)
        assert (profile.get("HAILO_ARCH"), profile.get("HAILO_INTERFACE"), profile.get("NPU_DEVICE"),
                profile.get("HAILO_FAMILY")) == (arch, "integrated", "/dev/h1x", "hailo1x")

    def test_usb_hailo10h(self, tmp_path):
        root = board(tmp_path)
        usb_device(root, "2-1", "0b05", "1d6f")
        profile = run_both(tmp_path, root, tool_dir(tmp_path))   # no driver, no hailortcli: the USB id tells
        assert hailo_view(profile) == {"SUPPORTS_HAILO": "ON", "NPU_VENDOR": "hailo", "NPU_MODEL": "hailo-10h",
                                       "HAILO_FAMILY": "hailo1x", "HAILO_ARCH": "hailo10h", "HAILO_DEVICES": "1",
                                       "HAILO_INTERFACE": "usb"}
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, version="version_5.4.0.txt", scan="scan_usb_5x.txt",
                        identify={"usb/002:016": "identify_current_hailo10h_5.4.0.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert profile.get("HAILO_ARCH") == "hailo10h" and profile.get("HAILO_FW_VERSION") == "5.4.0"

    # The rows of HAILO_PLAN 2.5, one by one.

    def test_no_device_and_no_hailort(self, tmp_path):
        profile = run_both(tmp_path, board(tmp_path), tool_dir(tmp_path))
        assert hailo_view(profile) == {"SUPPORTS_HAILO": "OFF"} and hailo_warnings(profile) == []
        assert profile.hailo() == type(profile.hailo())(present=False)

    def test_hailort_installed_without_a_device(self, tmp_path):
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_not_found.txt")
        profile = run_both(tmp_path, board(tmp_path), bindir)
        assert hailo_view(profile) == {"SUPPORTS_HAILO": "OFF", "HAILORT_VERSION": "4.24.0", "HAILO_DEVICES": "0"}
        assert hailo_warnings(profile) == []   # never HAILO_FAMILY from an installed HailoRT alone

    @pytest.mark.parametrize("device, family", [("0x2864", "hailo8"), ("0x45c4", "hailo1x"), ("0xffff", None)])
    def test_device_on_pcie_without_a_driver(self, tmp_path, device, family):
        root = board(tmp_path)
        pci_device(root, "0000:01:00.0", "0x1e60", device, "0x120000")
        profile = run_both(tmp_path, root, tool_dir(tmp_path))
        expect = {"SUPPORTS_HAILO": "ON", "NPU_VENDOR": "hailo", "HAILO_DEVICES": "1", "HAILO_INTERFACE": "pcie"}
        if family:
            expect["HAILO_FAMILY"] = family
        assert hailo_view(profile) == expect
        assert hailo_warnings(profile) == ["not_determined:HAILO_ARCH"]

    def test_driver_loaded_without_hailortcli(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root)
        profile = run_both(tmp_path, root, tool_dir(tmp_path))
        assert hailo_view(profile) == {
            "SUPPORTS_HAILO": "ON", "NPU_VENDOR": "hailo", "NPU_DEVICE": "/dev/hailo0", "HAILO_FAMILY": "hailo8",
            "HAILO_DEVICES": "1", "HAILO_INTERFACE": "pcie", "HAILO_DRIVER": "hailo_pci",
            "HAILO_DRIVER_VERSION": "4.24.0"}
        assert hailo_warnings(profile) == ["not_determined:HAILO_ARCH", "not_determined:HAILO_FW_VERSION"]

    @pytest.mark.skipif(os.name == "posix" and os.geteuid() == 0, reason="root opens every node")
    def test_node_this_user_cannot_open(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root)
        (root / "dev/hailo0").chmod(0o600 & 0o400)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_one_bdf_4x.txt", identify={})
        profile = run_both(tmp_path, root, bindir)
        assert hailo_warnings(profile) == ["needs_root:HAILO_ARCH"]

    @pytest.mark.parametrize("identify", ["identify_empty.txt", "identify_current_unknown_4.24.0.txt"])
    def test_identify_without_an_architecture(self, tmp_path, identify):
        root = board(tmp_path)
        hailo8_files(root)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_one_bdf_4x.txt", identify={"0000:01:00.0": identify})
        profile = run_both(tmp_path, root, bindir)
        assert "HAILO_ARCH" not in profile and profile.get("HAILO_FAMILY") == "hailo8"   # the family from the driver
        assert hailo_warnings(profile) == ["not_determined:HAILO_ARCH"]

    def test_cli_and_library_differ(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, version=None, version_err="version_mismatch_stderr.txt", version_rc=255,
                        scan="scan_one_bdf_4x.txt", identify={"0000:01:00.0": "identify_current_hailo8_4.24.0.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert "HAILORT_VERSION" not in profile and "HAILO_ARCH" not in profile   # scan and identify not run
        assert hailo_warnings(profile) == ["version_mismatch:HAILORT_VERSION"]
        assert profile.hailo().version_mismatch

    def test_sources_that_disagree(self, tmp_path):
        root = board(tmp_path)
        hailo10_files(root)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, version="version_5.4.0.txt", scan="scan_one_pci_5x.txt",
                        identify={"pci/0000:01:00.0": "identify_current_hailo8l_4.24.0.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert "HAILO_FAMILY" not in profile and "HAILO_ARCH" not in profile
        assert hailo_warnings(profile) == ["not_determined:HAILO_FAMILY"]

    @pytest.mark.parametrize("second, arch, warned", [("identify_current_hailo8_4.24.0.txt", "hailo8", []),
                                                      ("identify_current_hailo8l_4.24.0.txt", None,
                                                       ["not_determined:HAILO_ARCH"])])
    def test_two_devices(self, tmp_path, second, arch, warned):
        root = board(tmp_path)
        hailo8_files(root)
        write(root, "/sys/class/hailo_chardev/hailo1/device_id", "1e60:2864\n")
        write(root, "/dev/hailo1", b"")
        pci_device(root, "0000:03:00.0", "0x1e60", "0x2864", "0x120000")
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_two_bdf_4x.txt", identify={
            "0000:01:00.0": "identify_current_hailo8_4.24.0.txt", "0000:03:00.0": second})
        profile = run_both(tmp_path, root, bindir)
        assert profile.get("HAILO_DEVICES") == "2" and profile.get("HAILO_ARCH") == arch
        assert profile.get("HAILO_FAMILY") == "hailo8" and hailo_warnings(profile) == warned

    def test_driver_without_a_version_and_nul_bytes(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root)
        (root / "sys/module/hailo_pci/version").unlink()
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_one_bdf_4x.txt",
                        identify={"0000:01:00.0": "identify_current_hailo8l_nul_bytes.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert profile.get("HAILO_ARCH") == "hailo8l" and "HAILO_DRIVER_VERSION" not in profile
        assert hailo_warnings(profile) == ["not_determined:HAILO_DRIVER_VERSION"]

    def test_scan_output_with_nul_bytes(self, tmp_path):
        root = board(tmp_path)
        hailo8_files(root)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, identify={"0000:01:00.0": "identify_current_hailo8_4.24.0.txt"})
        (bindir.parent / "hailo-data" / "scan.out").write_bytes(b"Hailo Devices:\n[-] Device: 0000:01:00.0\0\n")
        profile = run_both(tmp_path, root, bindir)
        assert (profile.get("HAILO_ARCH"), profile.get("HAILO_DEVICES"), profile.get("HAILO_FW_VERSION")) == \
            ("hailo8", "1", "4.24.0")
        assert hailo_warnings(profile) == []

    def test_a_rockchip_board_with_a_hailo_keeps_both(self, tmp_path):
        root = make_sysroot(tmp_path, "rk3566")
        hailo8_files(root)
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_one_bdf_4x.txt", identify={"0000:01:00.0": "identify_current_hailo8l_4.24.0.txt"})
        profile = run_both(tmp_path, root, bindir)
        assert (profile.get("NPU_VENDOR"), profile.get("NPU_MODEL"), profile.get("NPU_DEVICE")) == \
            ("rockchip", "rk3566", "/dev/rknpu")
        assert profile.is_on("SUPPORTS_HAILO") and profile.get("HAILO_ARCH") == "hailo8l"


@POSIX_ONLY
@NEEDS_SHELL
class TestHailoTimeCap:
    """The Hailo part asks no further device once it has run for 60 s (HAILO_TOOL_SECONDS in the .py). The .sh's clock:
    date +%s where it gives a number (%s is not POSIX; some busybox builds and date tools lack it), else the whole
    seconds since boot from /proc/uptime; with neither, the 16-device cap alone applies (as before)."""

    def machine(self, tmp_path, date_seconds):
        """Two Hailo-8 on PCIe; every 'fw-control identify' moves both clocks on by 61 s. `date_seconds`: whether the
        date on PATH knows +%s."""
        root = board(tmp_path)
        hailo8_files(root)
        write(root, "/sys/class/hailo_chardev/hailo1/device_id", "1e60:2864\n")
        write(root, "/dev/hailo1", b"")
        pci_device(root, "0000:03:00.0", "0x1e60", "0x2864", "0x120000")
        write(root, "/proc/uptime", "1000.25 3000.50\n")
        bindir = tool_dir(tmp_path)
        fake_hailortcli(bindir, scan="scan_two_bdf_4x.txt", identify={
            "0000:01:00.0": "identify_current_hailo8_4.24.0.txt", "0000:03:00.0": "identify_current_hailo8_4.24.0.txt"})
        clock, log = tmp_path / "clock", tmp_path / "identify.log"
        clock.write_text("1000\n")
        (bindir / "hailortcli").rename(bindir / "hailortcli.fixture")
        fake_tool(bindir, "hailortcli", "\n".join([
            "#!/bin/sh",
            'if [ "$1" = fw-control ]; then',
            f'    echo "$4" >> "{log}"',
            f'    n=$(cat "{clock}"); n=$((n + 61)); echo "$n" > "{clock}"',
            f'    echo "$n.25 3000.50" > "{root}/proc/uptime"',
            "fi",
            f'exec "{bindir / "hailortcli.fixture"}" "$@"', ""]))
        real_date = shutil.which("date")
        (bindir / "date").unlink()
        answer = f'cat "{clock}"; exit 0' if date_seconds else 'echo "date: invalid date format" >&2; exit 1'
        fake_tool(bindir, "date", f'#!/bin/sh\nif [ "$1" = "+%s" ]; then {answer}; fi\nexec "{real_date}" "$@"\n')
        return root, bindir, log

    @pytest.mark.parametrize("date_seconds", [True, False], ids=["date", "proc-uptime"])
    @pytest.mark.parametrize("shell", SHELLS)
    def test_no_device_is_asked_after_60_seconds(self, tmp_path, shell, date_seconds):
        root, bindir, log = self.machine(tmp_path, date_seconds)
        text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
        assert log.read_text().split() == ["0000:01:00.0"], "the second device comes after 61 s"
        assert parse_profile(text).get("HAILO_DEVICES") == "2"

    @pytest.mark.parametrize("shell", SHELLS)
    def test_without_any_clock_both_devices_are_asked(self, tmp_path, shell):
        root, bindir, log = self.machine(tmp_path, date_seconds=False)
        (root / "proc/uptime").unlink()
        text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
        assert log.read_text().split() == ["0000:01:00.0", "0000:03:00.0"]
        assert parse_profile(text).get("HAILO_ARCH") == "hailo8"

    def test_the_sh_reads_seconds_only_where_they_work(self):
        text = PROBE_SH.read_text(encoding="utf-8")
        code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        assert [ln.strip() for ln in code if "date +%s" in ln] == ['date) s=$(date +%s 2>/dev/null) ;;'], \
            "date +%s is used only as the first of two clocks"
        assert "rd /proc/uptime" in text


class TestPs1Arch:

    def test_the_hailo_loop_never_overwrites_the_scripts_arch(self):
        """PowerShell variable names ignore case: a Hailo loop variable named $arch (or $ARCH) would overwrite the
        ARCH value the script wrote above. Every assignment to it comes before 'Put 'ARCH' $arch'."""
        text = PROBE_PS1.read_text(encoding="utf-8")
        put = text.index("Put 'ARCH' $arch")
        assignments = [m.start() for m in re.finditer(r"\$arch\s*=(?!=)", text, re.IGNORECASE)]
        assert assignments and all(pos < put for pos in assignments)
        assert "$recordArch = $HailoArchNames[$record[1]]" in text


class TestHailoWindows:
    """The .py on Windows: the PnP ids and hailortcli on PATH (no driver keys); the .ps1 carries the same rules."""

    def hailortcli(self, answers):
        def run(cmd, timeout):
            if cmd[0] == "hailortcli":
                return answers.get(tuple(cmd[1:]), (1, "", ""))
            return windows_runner(system_answer={"Probe": "system", "Cores": 8, "MemoryType": 26, "MemorySpeed": 3200,
                                                 "Ids": ["pci:1e60:2864", "pci:8086:3e92"], "IntelNpu": False})(cmd, timeout)
        return run

    def test_pnp_ids_and_hailortcli(self):
        run = self.hailortcli({("-v",): (0, hailo_text("version_4.24.0.txt"), ""),
                               ("scan",): (0, hailo_text("scan_one_bdf_4x.txt"), ""),
                               ("fw-control", "identify", "-s", "0000:01:00.0"):
                                   (0, hailo_text("identify_current_hailo8l_4.24.0.txt"), "")})
        values = probe.collect(system="Windows", windows_api=FakeWindows(), run=run, env={}, root=os.sep)
        assert {k: values.get(k) for k in ("SUPPORTS_HAILO", "NPU_VENDOR", "NPU_MODEL", "HAILO_FAMILY", "HAILO_ARCH",
                                           "HAILO_DEVICES", "HAILO_INTERFACE", "HAILO_FW_VERSION", "HAILORT_VERSION")} == {
            "SUPPORTS_HAILO": "ON", "NPU_VENDOR": "hailo", "NPU_MODEL": "hailo-8l", "HAILO_FAMILY": "hailo8",
            "HAILO_ARCH": "hailo8l", "HAILO_DEVICES": "1", "HAILO_INTERFACE": "pcie", "HAILO_FW_VERSION": "4.24.0",
            "HAILORT_VERSION": "4.24.0"}
        assert "HAILO_DRIVER" not in values and "NPU_DEVICE" not in values
        parse_profile(probe.render_text(values))

    def test_pnp_id_alone(self):
        run = self.hailortcli({})
        values = probe.collect(system="Windows", windows_api=FakeWindows(), run=lambda cmd, t: (
            (-1, "", "") if cmd[0] == "hailortcli" else run(cmd, t)), env={}, root=os.sep)
        assert values["SUPPORTS_HAILO"] == "ON" and values["HAILO_FAMILY"] == "hailo8"
        assert "not_determined:HAILO_ARCH" in values["PROBE_WARNINGS"]

    def test_ps1_carries_the_tables_and_rules(self):
        from test_hardware_probe import RAW_SCHEMA
        text = PROBE_PS1.read_text(encoding="utf-8")
        t = RAW_SCHEMA["tables"]
        for name, table in (("HailoArchNames", t["hailo_arch_names"]), ("HailoArchFamily", t["hailo_arch_family"]),
                            ("HailoPciDevices", t["hailo_pci_devices"]), ("HailoUsbDevices", t["hailo_usb_devices"])):
            line = next(ln for ln in text.splitlines() if ln.startswith(f"${name} = @{{"))
            assert dict(re.findall(r"'([^']+)' = '([^']+)'", line)) == table, name
        for needle in ("'-v'", "'scan'", "'fw-control', 'identify', '-s'", "version_mismatch:HAILORT_VERSION",
                       "not_determined:HAILO_FAMILY", "'(?m)^\\s*Device Architecture\\s*:\\s*([A-Za-z0-9_]+)\\s*$'",
                       "Replace([string][char]0, '')", "if ($hailoClock.Elapsed.TotalSeconds -ge 60) { break }",
                       # NUL bytes leave the -v and scan outputs too, as a shell drops them (the .sh, the .py)
                       "$scanOut = ([string]$result.Out).Replace([string][char]0, '')",
                       "[regex]::Matches($scanOut, ",
                       ("$text = ([string]$result.Out + \"`n\" + [string]$result.Err)"
                        ".Replace([string][char]0, '')")):
            assert needle in text, needle
        assert "HAILO_DRIVER" not in text   # Linux-only keys

    def test_identify_never_runs_extended(self):
        """--extended prints the MAC address and the SoC and ULT ids: no probe ever passes it."""
        for path in (PROBE_PY, PROBE_SH, PROBE_PS1):
            code = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                    if not ln.lstrip().startswith("#") and '"""' not in ln]
            assert not any("--extended" in ln for ln in code), path.name


class TestHailoTables:

    def test_tables_are_the_schema(self):
        from test_hardware_probe import RAW_SCHEMA, sh_table
        t = RAW_SCHEMA["tables"]
        assert probe.HAILO_ARCH_NAMES == t["hailo_arch_names"] == sh_table("hailo_arch_name")
        assert probe.HAILO_ARCH_FAMILY == t["hailo_arch_family"] == sh_table("hailo_arch_family")
        assert probe.HAILO_DRIVERS == t["hailo_drivers"] == sh_table("hailo_driver_family")
        assert list(probe.HAILO_DRIVERS) == list(t["hailo_drivers"])   # the order they are looked for
        assert probe.HAILO_SYSFS_CLASSES == t["hailo_sysfs_classes"] == sh_table("hailo_class_family")
        assert list(probe.HAILO_SYSFS_CLASSES) == list(t["hailo_sysfs_classes"])
        assert probe.HAILO_PCI_DEVICES == t["hailo_pci_devices"] == sh_table("hailo_pci_family")
        assert probe.HAILO_USB_DEVICES == t["hailo_usb_devices"] == sh_table("hailo_usb_arch")
        assert set(t["hailo_arch_names"].values()) == set(t["hailo_arch_family"]) == set(
            RAW_SCHEMA["keys"]["HAILO_ARCH"]["values"])
        assert "version_mismatch" in t["warning_codes"]
        sh = PROBE_SH.read_text(encoding="utf-8")
        assert "for m in hailo_pci hailo1x_pci hailo_integrated_nnc; do" in sh
        assert "for cls in hailo_chardev hailo1x hailo1x_integrated; do" in sh

    @pytest.mark.parametrize("node, ok", [("/dev/h1x-0", True), ("/dev/h1x", True), ("/dev/h1x-12", True),
                                          ("/dev/hailo3", True), ("/dev/h1x-", False), ("/dev/h1x0", False),
                                          ("/dev/h1x-0/../sda", False)])
    def test_npu_device_accepts_the_master_driver_nodes(self, node, ok):
        text = (FIXTURES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8").replace(
            "NPU_DEVICE=/dev/rknpu", "NPU_DEVICE=" + node)
        if ok:
            assert parse_profile(text).get("NPU_DEVICE") == node
        else:
            with pytest.raises(Exception, match="NPU_DEVICE"):
                parse_profile(text)


class TestHailoAccess:

    def test_needs_root_when_the_node_cannot_be_opened(self, tmp_path, monkeypatch):
        """The same rule as TestHailoMachines.test_node_this_user_cannot_open, also where the tests run as root."""
        root = board(tmp_path)
        hailo8_files(root)

        def run(cmd, timeout):
            if cmd == ["hailortcli", "-v"]:
                return 0, hailo_text("version_4.24.0.txt"), ""
            if cmd == ["hailortcli", "scan"]:
                return 0, hailo_text("scan_one_bdf_4x.txt"), ""
            return (1, "", "cannot open the device") if cmd[0] == "hailortcli" else (-1, "", "")

        monkeypatch.setattr(probe.os, "access", lambda path, mode: False)
        values = py_probe(root, run=run)
        assert "needs_root:HAILO_ARCH" in values["PROBE_WARNINGS"] and "HAILO_ARCH" not in values
        assert values["HAILORT_VERSION"] == "4.24.0" and values["HAILO_DEVICES"] == "1"

    def test_the_accessor_of_a_3_0_0_profile(self):
        facts = parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8")).hailo()
        assert facts.present is False and facts.family is None and facts.model is None and facts.hailort_line is None


# --- O-PREP: vendor runtimes of targets the maintainer cannot test (read only when present) --------------------------

DPKG_STATUS = """Package: nvidia-l4t-core
Status: install ok installed
Priority: standard
Version: 36.4.0-20240912212859
Description: NVIDIA Core Package
 a continuation line: Version: 9.9.9

Package: nvidia-jetpack
Status: deinstall ok config-files
Version: 5.1.2-b104

Package: nvidia-jetpack
Status: install ok installed
Architecture: arm64
Version: 6.1+b123
"""
CUDA_VERSION_JSON = """{
   "cuda" : {
      "name" : "CUDA SDK",
      "version" : "12.6.3"
   },
   "cuda_cccl" : {
      "name" : "CUDA C++ Core Compute Libraries",
      "version" : "12.6.77"
   }
}
"""


def jetson(tmp_path):
    root = make_sysroot(tmp_path, "rk3588")   # an arm64 board; the RK3588 parts do not matter here
    write(root, "/etc/nv_tegra_release", "# R36 (release), REVISION: 4.0, GCID: 37537400, BOARD: generic, EABI: "
                                         "aarch64, DATE: Fri Sep 13 04:36:44 UTC 2024\n# KERNEL_VARIANT: oot\n")
    write(root, "/var/lib/dpkg/status", DPKG_STATUS)
    write(root, "/usr/local/cuda/version.json", CUDA_VERSION_JSON)
    write(root, "/usr/lib/aarch64-linux-gnu/libnvinfer.so.10.3.0", b"")
    os.symlink("libnvinfer.so.10.3.0", root / "usr/lib/aarch64-linux-gnu/libnvinfer.so.10")
    return root


def intel_pc(tmp_path):
    root = make_sysroot(tmp_path, "intel")
    write(root, "/usr/lib/libopenvino.so.2025.3.0", b"")
    os.symlink("libopenvino.so.2025.3.0", root / "usr/lib/libopenvino.so.2530")
    for node, driver in (("renderD128", "i915"), ("renderD129", "xe")):
        write(root, f"/sys/class/drm/{node}/device/vendor", "0x8086\n")
        (root / f"sys/bus/pci/drivers/{driver}").mkdir(parents=True, exist_ok=True)
        os.symlink(f"../../../../bus/pci/drivers/{driver}", root / f"sys/class/drm/{node}/device/driver")
    (root / "sys/bus/pci/drivers/intel_vpu").mkdir(parents=True)
    (root / "sys/class/accel/accel0/device").mkdir(parents=True)
    os.symlink("../../../../bus/pci/drivers/intel_vpu", root / "sys/class/accel/accel0/device/driver")
    return root


class TestVendorRuntimes:

    def test_parsers(self):
        assert probe.parse_nv_tegra_release("# R35 (release), REVISION: 4.1, GCID: 33958178, BOARD: t186ref, "
                                            "EABI: aarch64, DATE: Tue Aug 1 19:57:35 UTC 2023\n") == "35.4.1"
        assert probe.parse_nv_tegra_release("# R32 (release), REVISION: 7.4\n") == "32.7.4"
        assert probe.parse_nv_tegra_release("garbage\n") is None
        assert probe.dpkg_installed_version(DPKG_STATUS.splitlines(True), "nvidia-jetpack") == "6.1+b123"
        assert probe.dpkg_installed_version(DPKG_STATUS.splitlines(True), "nvidia-l4t-core") == "36.4.0-20240912212859"
        assert probe.dpkg_installed_version(["Package: x\n"], "nvidia-jetpack") is None
        assert probe.parse_cuda_version_file(CUDA_VERSION_JSON, True) == "12.6.3"
        assert probe.parse_cuda_version_file("CUDA Version 10.2.300\n", False) == "10.2.300"

    @POSIX_ONLY
    @NEEDS_SHELL
    def test_jetson(self, tmp_path):
        profile = run_both(tmp_path, jetson(tmp_path), tool_dir(tmp_path))
        assert (profile.get("JETSON_L4T_VERSION"), profile.get("JETSON_JETPACK_VERSION"),
                profile.get("CUDA_TOOLKIT_VERSION"), profile.get("TENSORRT_VERSION")) == ("36.4.0", "6.1", "12.6.3",
                                                                                         "10.3.0")
        assert not [w for w in profile.warnings if "JETSON" in w or "CUDA" in w]

    @POSIX_ONLY
    @NEEDS_SHELL
    def test_jetson_files_that_say_nothing(self, tmp_path):
        root = jetson(tmp_path)
        write(root, "/etc/nv_tegra_release", "# a damaged file\n")
        write(root, "/usr/local/cuda/version.json", "{}\n")
        write(root, "/var/lib/dpkg/status", "Package: nvidia-jetpack\nStatus: deinstall ok config-files\nVersion: 6.1\n")
        profile = run_both(tmp_path, root, tool_dir(tmp_path))
        assert not any(k in profile for k in ("JETSON_L4T_VERSION", "JETSON_JETPACK_VERSION", "CUDA_TOOLKIT_VERSION"))
        assert {"not_determined:JETSON_L4T_VERSION", "not_determined:CUDA_TOOLKIT_VERSION"} <= set(profile.warnings)

    @POSIX_ONLY
    @NEEDS_SHELL
    def test_cuda_version_txt_of_older_toolkits(self, tmp_path):
        root = jetson(tmp_path)
        (root / "usr/local/cuda/version.json").unlink()
        write(root, "/usr/local/cuda/version.txt", "CUDA Version 10.2.300\n")
        assert run_both(tmp_path, root, tool_dir(tmp_path)).get("CUDA_TOOLKIT_VERSION") == "10.2.300"

    @POSIX_ONLY
    @NEEDS_SHELL
    def test_files_with_crlf_line_ends(self, tmp_path):
        """Python reads a file's lines without the CR of a CRLF line end; the .sh drops it too (as for os-release)."""
        root = jetson(tmp_path)
        write(root, "/etc/nv_tegra_release", "# R36 (release), REVISION: 4.3, GCID: 1\r\n")
        write(root, "/var/lib/dpkg/status", DPKG_STATUS.replace("\n", "\r\n"))
        profile = run_both(tmp_path, root, tool_dir(tmp_path))
        assert (profile.get("JETSON_L4T_VERSION"), profile.get("JETSON_JETPACK_VERSION")) == ("36.4.3", "6.1")

    @POSIX_ONLY
    @NEEDS_SHELL
    def test_intel_openvino_gpu_and_npu(self, tmp_path):
        profile = run_both(tmp_path, intel_pc(tmp_path), tool_dir(tmp_path))
        assert (profile.get("OPENVINO_VERSION"), profile.get("INTEL_GPU_DEVICE"), profile.get("INTEL_GPU_DRIVER"),
                profile.get("INTEL_NPU_DEVICE")) == ("2025.3.0", "/dev/dri/renderD128", "i915", "/dev/accel/accel0")
        assert profile.get("NPU_DEVICE") == "/dev/accel/accel0" and profile.get("NPU_MODEL") == "intel-npu"
        assert "JETSON_L4T_VERSION" not in profile   # x86: never looked for

    @POSIX_ONLY
    @NEEDS_SHELL
    def test_rocm_gfx_targets_also_of_compute_devices(self, tmp_path):
        root = make_sysroot(tmp_path, "amd")
        # An Instinct card (PCI class 0x12, no display controller, so no GPU<n>) next to the APU's node of the fixture.
        write(root, "/sys/class/kfd/kfd/topology/nodes/2/properties", "simd_count 304\ngfx_target_version 90402\n")
        write(root, "/sys/class/kfd/kfd/topology/nodes/3/properties", "gfx_target_version 90402\n")
        write(root, "/sys/class/kfd/kfd/topology/nodes/0/properties", "cpu_cores_count 16\ngfx_target_version 0\n")
        profile = run_both(tmp_path, root, tool_dir(tmp_path))
        assert profile.items_of("ROCM_GFX_TARGETS") == ["gfx1103", "gfx942"]
        assert [g.get("GFX_TARGET") for g in profile.gpus()] == ["gfx1103"]

    def test_nothing_is_written_without_the_files(self, tmp_path):
        values = py_probe(make_sysroot(tmp_path, "intel"))
        for key in ("JETSON_L4T_VERSION", "JETSON_JETPACK_VERSION", "CUDA_TOOLKIT_VERSION", "TENSORRT_VERSION",
                    "OPENVINO_VERSION", "INTEL_GPU_DEVICE", "INTEL_GPU_DRIVER", "INTEL_NPU_DEVICE", "ROCM_GFX_TARGETS"):
            assert key not in values, key
        assert not any(k in values.get("PROBE_WARNINGS", "") for k in ("JETSON", "CUDA", "TENSORRT", "OPENVINO"))

    @pytest.mark.parametrize("node", ["/dev/sda", "/dev/dri/card0", "/dev/accel/../mem"])
    def test_device_keys_name_accelerator_nodes_only(self, node):
        text = (FIXTURES / "schema2_linux_ivybridge.txt").read_text(encoding="utf-8")
        for key in ("INTEL_GPU_DEVICE", "INTEL_NPU_DEVICE"):
            with pytest.raises(Exception, match=key):
                parse_profile(text + f"{key}={node}\n")


@POSIX_ONLY
def test_cli_scan_import_shows_the_hailo(tmp_path, monkeypatch):
    """`llm-cli scan --import` (and the other scan commands) say what the probe found of a Hailo: one line from
    HardwareProfile.hailo(), also next to a Rockchip NPU."""
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    root = make_sysroot(tmp_path, "rk3566")
    hailo8_files(root)

    def run(cmd, timeout):
        answers = {("hailortcli", "-v"): hailo_text("version_4.24.0.txt"),
                   ("hailortcli", "scan"): hailo_text("scan_one_bdf_4x.txt"),
                   ("hailortcli", "fw-control", "identify", "-s", "0000:01:00.0"):
                       hailo_text("identify_current_hailo8l_4.24.0.txt")}
        return (0, answers[tuple(cmd)], "") if tuple(cmd) in answers else (-1, "", "")

    probe_file = tmp_path / "board.txt"
    probe_file.write_text(probe.render_text(py_probe(root, run=run)), encoding="utf-8")
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    result = CliRunner().invoke(cli_module.cli, ["-q", "scan", "--import", str(probe_file), "--label", "Zero 3W"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "NPU: rockchip rk3566" in out
    assert "Hailo: hailo-8l, family hailo8 (HailoRT 4.x), 1 device on pcie, HailoRT 4.24.0 installed" in out
