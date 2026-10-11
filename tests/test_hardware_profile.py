"""
Hardware probe schema 2 and its parser (configs/hardware_probe_schema.yml, orchestrator/runtime/profile.py).

Fixtures in tests/fixtures/hardware_probe/ hold made-up values only (no real host names, serial or part numbers):
three schema 2 profiles (an RK3566 board on DietPi, a Linux PC with two NVIDIA GPUs, a Windows APU handheld) and
three schema 1 files in the style of today's hardware_probe.sh (v2.4) and hardware_probe.ps1 (v2.5). No network,
no subprocess.
"""

import codecs
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from orchestrator.runtime import profile as pf
from orchestrator.runtime.profile import (HardwareProfile, ProbeSchema, ProfileError, ProfileStore, SchemaError,
                                          device_id_for_host_key, load_schema, new_device_id, parse_profile,
                                          parse_profile_json, read_profile)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hardware_probe"
SCHEMA = load_schema()
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")

# ggml's x86 options at the pinned llama.cpp commit (ggml/CMakeLists.txt): one SUPPORTS_* key each.
GGML_X86 = ["SSE42", "AVX", "AVX2", "BMI2", "F16C", "FMA", "AVX512", "AVX512_VBMI", "AVX512_VNNI", "AVX512_BF16",
            "AVX_VNNI", "AMX_TILE", "AMX_INT8", "AMX_BF16"]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def board() -> HardwareProfile:
    return parse_profile(fixture("schema2_rk3566_dietpi.txt"))


def edit(text: str, key: str, value=None) -> str:
    """The file with KEY replaced (value None removes the line; a missing key is appended)."""
    lines, found = [], False
    for line in text.splitlines():
        if line.split("=", 1)[0] == key:
            found = True
            if value is not None:
                lines.append(f"{key}={value}")
        else:
            lines.append(line)
    if not found and value is not None:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


# --- the schema file -----------------------------------------------------------------------------------------------

class TestSchemaFile:
    def test_every_key_has_source_normalisation_and_validation(self):
        raw = yaml.safe_load((pf.SCHEMA_PATH).read_text(encoding="utf-8"))
        for section in ("keys", "gpu_keys"):
            for name, spec in raw[section].items():
                assert spec.get("sources") and str(spec.get("normalise", "")).strip(), name
                assert spec["type"] in ("bool", "enum", "int", "pattern", "version", "text", "list", "timestamp")
                if spec["type"] == "enum":
                    assert all(isinstance(v, str) for v in spec["values"]), f"{name}: quote YAML values like ON"
        assert len(SCHEMA.keys) > 80

    def test_os_agnostic_keys_of_decision_8(self):
        for key in ("OS", "OS_ID", "OS_ID_LIKE", "OS_FAMILY", "OS_VARIANT_ID", "OS_VERSION_ID", "OS_VERSION_CODENAME",
                    "KERNEL", "KERNEL_ARCH", "INIT_SYSTEM", "LIBC", "LIBC_VERSION", "ARCH"):
            assert key in SCHEMA.keys, key
        assert {"glibc", "musl"} <= set(SCHEMA.keys["LIBC"].values)
        assert {"systemd", "openrc", "other"} <= set(SCHEMA.keys["INIT_SYSTEM"].values)

    def test_build_relevant_keys(self):
        for key in ("SUPPORTS_DOTPROD", "SUPPORTS_FP16_ARITH", "SUPPORTS_I8MM", "SUPPORTS_SVE", "CPU_CLUSTERS",
                    "NVIDIA_DRIVER_CUDA_MAX", "VULKAN", "VULKAN_DEVICES", "HIP_RUNTIME", "METAL", "TOOL_CC",
                    "TOOL_CMAKE", "TOOL_NVCC", "TOOL_GLSLC", "RKNPU_DRIVER_VERSION", "RKLLM_RUNTIME_VERSION"):
            assert key in SCHEMA.keys, key
        for key in ("COMPUTE_CAP", "GFX_TARGET", "PCI_ID", "VRAM_MB", "VRAM_FREE_MB", "DRIVER_VERSION"):
            assert key in SCHEMA.gpu_keys, key
        assert [k for k in SCHEMA.keys if SCHEMA.keys[k].group == "x86"] == [f"SUPPORTS_{k}" for k in GGML_X86]

    def test_no_identifying_keys(self):
        """Decision D3: no host name, serial or part numbers, MAC addresses or network fields in profiles."""
        names = list(SCHEMA.keys) + [f"GPU0_{k}" for k in SCHEMA.gpu_keys]
        forbidden = {"HOST", "HOSTNAME", "SERIAL", "PART", "PARTNUMBER", "MAC", "UUID", "IP", "USER", "ADDRESS",
                     "SLOT", "CLOUD", "PORT"}
        for name in names:
            assert not forbidden & set(name.split("_")), name

    def test_bool_keys_are_required_and_volatile_keys_never_keyed(self):
        for spec in list(SCHEMA.keys.values()) + list(SCHEMA.gpu_keys.values()):
            if spec.type == "bool":
                assert spec.required
            if spec.stability == "volatile":
                assert spec.perf == "none" and not spec.required
        volatile = {k for k, s in SCHEMA.keys.items() if s.stability == "volatile"}
        assert volatile == {"RAM_AVAILABLE_MB", "DISK_FREE_MB"}
        assert SCHEMA.gpu_keys["VRAM_FREE_MB"].stability == "volatile"

    def test_perf_roles_follow_the_design(self):
        keyed = {k for k, s in SCHEMA.keys.items() if s.perf == "key"}
        assert {"ARCH", "LIBC", "CPU_VENDOR", "CPU_MODEL", "CPU_CLUSTERS", "SOC_MODEL", "NPU_MODEL"} <= keyed
        assert {k for k in SCHEMA.keys if k.startswith("SUPPORTS_")} <= keyed
        recorded = {k for k, s in SCHEMA.keys.items() if s.perf == "recorded"}
        assert {"OS_ID", "OS_VERSION_ID", "LIBC_VERSION", "RAM_TOTAL_MB", "NVIDIA_DRIVER_CUDA_MAX"} <= recorded
        never = {"GENERATED_AT", "PROBE_WARNINGS", "PROBE_TOOL", "PROBE_TOOL_VERSION", "KERNEL"}
        never |= {k for k in SCHEMA.keys if k.startswith("TOOL_")}
        assert all(SCHEMA.keys[k].perf == "none" for k in never)
        assert {k for k, s in SCHEMA.gpu_keys.items() if s.perf == "key"} == {"VENDOR", "PCI_ID", "COMPUTE_CAP",
                                                                               "GFX_TARGET"}
        assert {k for k, s in SCHEMA.gpu_keys.items() if s.perf == "recorded"} == {"VRAM_MB", "DRIVER_VERSION"}

    def test_broken_schema_is_refused(self):
        raw = yaml.safe_load((pf.SCHEMA_PATH).read_text(encoding="utf-8"))
        del raw["keys"]["CPU_MODEL"]["sources"]
        with pytest.raises(SchemaError, match="CPU_MODEL"):
            ProbeSchema(raw)
        raw = yaml.safe_load((pf.SCHEMA_PATH).read_text(encoding="utf-8"))
        raw["keys"]["RAM_AVAILABLE_MB"]["perf"] = "key"
        with pytest.raises(SchemaError, match="volatile"):
            ProbeSchema(raw)
        raw = yaml.safe_load((pf.SCHEMA_PATH).read_text(encoding="utf-8"))
        raw["keys"]["OS"]["values"] = [True, False]  # what unquoted ON / OFF become in YAML
        with pytest.raises(SchemaError, match="quoted"):
            ProbeSchema(raw)


class TestNormalisationHelpers:
    def test_arch(self):
        assert SCHEMA.normalise_arch("aarch64") == "arm64"
        assert SCHEMA.normalise_arch("AMD64") == "x86_64"
        assert SCHEMA.normalise_arch("armv7l") == "armv7"
        assert SCHEMA.normalise_arch("i686") == "x86"
        assert SCHEMA.normalise_arch("aarch64", userland_bits=32) == "armv7"  # 64-bit kernel, 32-bit userland
        assert SCHEMA.normalise_arch("x86_64", userland_bits=32) == "x86"
        assert SCHEMA.normalise_arch("sparc64") == "other"

    def test_os_family(self):
        assert SCHEMA.os_family("linux", "debian") == "debian"  # DietPi keeps ID=debian
        assert SCHEMA.os_family("linux", "ubuntu", ["debian"]) == "debian"
        assert SCHEMA.os_family("linux", "rocky", ["rhel", "centos", "fedora"]) == "rhel"
        assert SCHEMA.os_family("linux", "fedora") == "fedora"
        assert SCHEMA.os_family("linux", "alpine") == "alpine"
        assert SCHEMA.os_family("linux", "somethingnew", ["arch"]) == "arch"
        assert SCHEMA.os_family("linux", "somethingnew") == "other"
        assert SCHEMA.os_family("windows") == "windows"
        assert SCHEMA.os_family("macos") == "macos"

    def test_cpu_vendor_and_arm_core_names(self):
        assert SCHEMA.cpu_vendor(vendor_id="GenuineIntel") == "intel"
        assert SCHEMA.cpu_vendor(vendor_id="  Shanghai  ") == "zhaoxin"
        assert SCHEMA.cpu_vendor(implementer="0x41") == "arm"
        assert SCHEMA.cpu_vendor(implementer="0x99") == "other"
        assert SCHEMA.arm_core_name("0x41", "0xd05") == "Cortex-A55"
        assert SCHEMA.arm_core_name("0x41", "0xD0B") == "Cortex-A76"
        assert SCHEMA.arm_core_name("0x51", "0x1") == "0x51:0x001"  # unknown part: implementer:part

    def test_features_from_flags(self):
        arm = SCHEMA.features_from_flags("arm64", "fp asimd fphp asimdhp asimddp".split())
        assert arm["SUPPORTS_NEON"] == arm["SUPPORTS_DOTPROD"] == arm["SUPPORTS_FP16_ARITH"] == "ON"
        assert arm["SUPPORTS_I8MM"] == "OFF" and arm["SUPPORTS_AVX2"] == "OFF"
        assert SCHEMA.features_from_flags("arm64", ["asimdhp"])["SUPPORTS_FP16_ARITH"] == "OFF"  # needs fphp too
        assert SCHEMA.features_from_flags("armv7", ["neon"])["SUPPORTS_NEON"] == "ON"
        x86 = SCHEMA.features_from_flags("x86_64", "sse4_2 avx avx2 avx512_vnni".split())
        assert x86["SUPPORTS_AVX2"] == "ON" and x86["SUPPORTS_AVX512_VNNI"] == "ON"
        assert x86["SUPPORTS_AVX512"] == "OFF"  # avx512f decides, not any avx512 flag
        assert x86["SUPPORTS_NEON"] == "OFF"


# --- schema 2 ----------------------------------------------------------------------------------------------------

class TestSchema2:
    def test_board_fixture(self):
        p = board()
        assert (p.os, p.arch, p.libc, p.libc_version) == ("linux", "arm64", "glibc", "2.36")
        assert p.get("OS_VARIANT_ID") == "dietpi" and p.get("INIT_SYSTEM") == "systemd"
        assert p.source_schema == 2 and not p.converted and not p.needs_reprobe
        assert p.soc_model_known and p.is_on("SUPPORTS_DOTPROD") and p.flag("SUPPORTS_I8MM") == "OFF"
        assert p.items_of("CPU_CLUSTERS") == ["0-3:0x41:0xd05:1800"]
        assert p.gpus() == [{"VENDOR": "arm", "NAME": "Mali-G52", "DISCRETE": "OFF", "UNIFIED": "ON",
                             "SOURCE": "device-tree"}]
        assert p.warnings == ["needs_root:RKNPU_DRIVER_VERSION"]

    @pytest.mark.parametrize("name", ["schema2_rk3566_dietpi.txt", "schema2_pc_two_nvidia.txt",
                                      "schema2_windows_apu.txt"])
    def test_views_round_trip(self, name):
        p = parse_profile(fixture(name))
        assert parse_profile(p.to_text()) == p
        assert parse_profile_json(p.to_json()) == p
        assert p.as_dict()["PROBE_SCHEMA"] == "2"
        assert all(isinstance(v, str) for v in json.loads(p.to_json()).values())
        assert p.to_text().splitlines()[1] == "PROBE_SCHEMA=2"

    def test_two_gpus(self):
        p = parse_profile(fixture("schema2_pc_two_nvidia.txt"))
        caps = [g["COMPUTE_CAP"] for g in p.gpus()]
        assert caps == ["8.6", "7.5"] and p.get("NVIDIA_DRIVER_CUDA_MAX") == "13.0"

    def test_windows_unknown_features(self):
        p = parse_profile(fixture("schema2_windows_apu.txt"))
        assert p.flag("SUPPORTS_AVX512_VNNI") == "UNKNOWN" and p.is_on("SUPPORTS_AVX512")
        assert (p.libc, p.get("INIT_SYSTEM"), p.get("CPU_FLAGS_SOURCE")) == ("ucrt", "scm", "windows-api")

    def test_read_profile_text_and_json(self, tmp_path):
        p = board()
        (tmp_path / "a.txt").write_text(p.to_text(), encoding="utf-8")
        (tmp_path / "a.json").write_text(p.to_json(), encoding="utf-8")
        assert read_profile(tmp_path / "a.txt") == p == read_profile(tmp_path / "a.json")
        with pytest.raises(ProfileError, match="cannot read"):
            read_profile(tmp_path / "missing.txt")

    def test_bom_and_crlf_are_accepted(self):
        text = fixture("schema2_rk3566_dietpi.txt")
        assert parse_profile(codecs.BOM_UTF8 + text.replace("\n", "\r\n").encode("utf-8")) == board()

    def test_whitespace_in_text_is_collapsed(self):
        p = parse_profile(edit(fixture("schema2_pc_two_nvidia.txt"), "CPU_MODEL", "AMD  Ryzen 9   5900X "))
        assert p.get("CPU_MODEL") == "AMD Ryzen 9 5900X"

    def test_other_operating_systems(self):
        """Decision 8: any OS the device runs. musl on Alpine, a Fedora kernel string, macOS on Apple Silicon."""
        alpine = fixture("schema2_rk3566_dietpi.txt")
        for key, value in (("OS_ID", "alpine"), ("OS_FAMILY", "alpine"), ("OS_VARIANT_ID", None),
                           ("OS_VERSION_ID", "3.20.3"), ("OS_VERSION_CODENAME", None), ("LIBC", "musl"),
                           ("LIBC_VERSION", "1.2.5"), ("INIT_SYSTEM", "openrc"), ("KERNEL", "6.6.54-0-lts")):
            alpine = edit(alpine, key, value)
        p = parse_profile(alpine)
        assert (p.libc, p.libc_version, p.get("INIT_SYSTEM")) == ("musl", "1.2.5", "openrc")
        assert p.perf_key() != board().perf_key()  # glibc and musl binaries differ
        fedora = fixture("schema2_pc_two_nvidia.txt")
        for key, value in (("OS_ID", "fedora"), ("OS_ID_LIKE", None), ("OS_FAMILY", "fedora"),
                           ("OS_VERSION_ID", "40"), ("OS_VERSION_CODENAME", None),
                           ("KERNEL", "6.11.3-200.fc40.x86_64")):
            fedora = edit(fedora, key, value)
        assert parse_profile(fedora).get("KERNEL") == "6.11.3-200.fc40.x86_64"
        mac = fixture("schema2_rk3566_dietpi.txt")
        for key, value in (("OS", "macos"), ("OS_ID", "macos"), ("OS_FAMILY", "macos"), ("OS_VARIANT_ID", None),
                           ("OS_VERSION_ID", "15.1"), ("OS_VERSION_CODENAME", None), ("KERNEL", "24.1.0"),
                           ("LIBC", "libsystem"), ("LIBC_VERSION", None), ("INIT_SYSTEM", "launchd"),
                           ("CPU_MODEL", "Apple M3"), ("CPU_VENDOR", "apple"), ("CPU_CLUSTERS", None),
                           ("SOC_VENDOR", None), ("SOC_MODEL", None), ("BOARD_MODEL", None), ("NPU_VENDOR", None),
                           ("NPU_MODEL", None), ("NPU_DEVICE", None), ("SUPPORTS_RKNN", "OFF"),
                           ("GPU0_VENDOR", "apple"), ("GPU0_NAME", "Apple M3"), ("GPU0_SOURCE", "system-profiler"),
                           ("METAL", "ON")):
            mac = edit(mac, key, value)
        p = parse_profile(mac)
        assert (p.os, p.arch, p.libc, p.is_on("METAL")) == ("macos", "arm64", "libsystem", True)

    def test_unknown_key_is_dropped_without_its_value(self):
        p = parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), "HOSTNAME", "fixture-host-0000"))
        assert "HOSTNAME" not in p and "unknown_key:HOSTNAME" in p.notes
        assert "fixture-host-0000" not in p.to_text() + p.to_json() + repr(p) + " ".join(p.notes)


class TestSchema2Rejections:
    def test_duplicate_keys(self):
        text = fixture("schema2_rk3566_dietpi.txt") + "SUPPORTS_DOTPROD=OFF\n"
        with pytest.raises(ProfileError, match=r"SUPPORTS_DOTPROD appears twice \(first in line \d+\)"):
            parse_profile(text)
        same_value = fixture("schema2_rk3566_dietpi.txt") + "ARCH=arm64\n"
        with pytest.raises(ProfileError, match="ARCH appears twice"):
            parse_profile(same_value)
        dumped = board().to_json()[:-2] + ',\n  "ARCH": "arm64"\n}'
        with pytest.raises(ProfileError, match="appears twice"):
            parse_profile_json(dumped)

    @pytest.mark.parametrize("key,value", [
        ("ARCH", "sparc"),
        ("ARCH", "aarch64"),  # emitters write the normalised value
        ("OS", "haiku"),
        ("OS_ID", "Debian"),
        ("OS_FAMILY", "redhat"),
        ("LIBC", "newlib"),
        ("LIBC_VERSION", "2.36$(id)"),
        ("LIBC_VERSION", "2"),
        ("INIT_SYSTEM", "upstart"),
        ("KERNEL", "6.1.99 ; rm -rf /"),
        ("SUPPORTS_DOTPROD", "TRUE"),
        ("SUPPORTS_DOTPROD", "on"),
        ("CPU_CORES_LOGICAL", "-1"),
        ("CPU_CORES_LOGICAL", "0"),
        ("CPU_CORES_LOGICAL", "0x10"),
        ("CPU_CORES_LOGICAL", "04"),
        ("RAM_TOTAL_MB", "99999999999"),
        ("CPU_CLUSTERS", "0-3:0x41:0xd05:1800;reboot"),
        ("CPU_CLUSTERS", "0-3:0x41:0xD05:1800"),
        ("CPU_FLAGS_ALL", "fp asimd"),
        ("CPU_MODEL", "Cortex-A55\x1b[2J"),
        ("CPU_MODEL", "Cortex\u202eA55"),
        ("SOC_MODEL", "rk3566 x"),
        ("SOC_MODEL", "../rk3566"),
        ("NPU_DEVICE", "/dev/../etc/passwd"),
        ("NPU_DEVICE", "/etc/passwd"),
        ("RKNPU_DRIVER_VERSION", "0.9.8-rc1"),
        ("GENERATED_AT", "2026-13-01T00:00:00Z"),
        ("GENERATED_AT", "Sun Oct 11 08:15:00 UTC 2026"),
        ("PROBE_SCHEMA", "2.0"),
        ("PROBE_TOOL", "bash"),
        ("PROBE_WARNINGS", "Not A Code"),
        ("GPU_COUNT", "17"),
        ("TOOL_PYTHON", "/usr/bin/python3"),
    ])
    def test_values_outside_the_table(self, key, value):
        with pytest.raises(ProfileError, match=key):
            parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), key, value))

    @pytest.mark.parametrize("key,value", [
        ("GPU0_COMPUTE_CAP", "8.6; rm -rf /"),
        ("GPU0_COMPUTE_CAP", "\u0668.\u0666"),  # Arabic-Indic digits are not \d here
        ("GPU0_COMPUTE_CAP", "86"),
        ("GPU0_PCI_ID", "0x10DE:0x2204"),
        ("NVIDIA_DRIVER_CUDA_MAX", "13.0.1"),
        ("TOOL_CC", "/usr/bin/gcc-13"),
        ("TOOL_CC", "gcc 13.3.0"),
    ])
    def test_gpu_and_tool_values_outside_the_table(self, key, value):
        with pytest.raises(ProfileError, match=key):
            parse_profile(edit(fixture("schema2_pc_two_nvidia.txt"), key, value))

    def test_gfx_target(self):
        text = fixture("schema2_windows_apu.txt")
        assert parse_profile(edit(text, "GPU0_GFX_TARGET", "gfx1103")).gpus()[0]["GFX_TARGET"] == "gfx1103"
        for bad in ("gfx1103;x", "GFX1103", "gfx11"):
            with pytest.raises(ProfileError, match="GPU0_GFX_TARGET"):
                parse_profile(edit(text, "GPU0_GFX_TARGET", bad))

    @pytest.mark.parametrize("name,key,value,reason", [
        ("schema2_rk3566_dietpi.txt", "SUPPORTS_AVX2", "ON", "cannot be ON with ARCH arm64"),
        ("schema2_pc_two_nvidia.txt", "SUPPORTS_DOTPROD", "ON", "cannot be ON with ARCH x86_64"),
        ("schema2_pc_two_nvidia.txt", "CPU_CLUSTERS", "0-3:0x41:0xd05:1800", "does not exist with ARCH x86_64"),
        ("schema2_pc_two_nvidia.txt", "METAL", "ON", "cannot be ON with OS linux"),
        ("schema2_windows_apu.txt", "HIP_RUNTIME", "ON", "cannot be ON with OS windows"),
        ("schema2_windows_apu.txt", "SUPPORTS_RKNN", "ON", "cannot be ON with OS windows"),
        ("schema2_windows_apu.txt", "GPU0_COMPUTE_CAP", "8.6", "does not exist with GPU0_VENDOR amd"),
        ("schema2_pc_two_nvidia.txt", "GPU1_GFX_TARGET", "gfx1103", "does not exist with GPU1_VENDOR nvidia"),
        ("schema2_pc_two_nvidia.txt", "LIBC", "ucrt", "ucrt does not exist with OS linux"),
        ("schema2_windows_apu.txt", "LIBC", "glibc", "glibc does not exist with OS windows"),
        ("schema2_windows_apu.txt", "INIT_SYSTEM", "systemd", "systemd does not exist with OS windows"),
        ("schema2_rk3566_dietpi.txt", "OS_FAMILY", "windows", "windows does not exist with OS linux"),
        ("schema2_rk3566_dietpi.txt", "GPU1_VENDOR", "arm", "GPU index not below GPU_COUNT"),
        ("schema2_rk3566_dietpi.txt", "GPU_COUNT", "2", "GPU1_VENDOR: missing"),
    ])
    def test_rules_across_keys(self, name, key, value, reason):
        with pytest.raises(ProfileError, match=reason):
            parse_profile(edit(fixture(name), key, value))

    def test_gpu_index_out_of_range(self):
        for key in ("GPU16_VENDOR", "GPU01_VENDOR"):
            with pytest.raises(ProfileError, match="GPU index out of range"):
                parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), key, "arm"))

    def test_value_limit_is_4096_characters(self):
        text = fixture("schema2_pc_two_nvidia.txt")
        flags = ",".join(f"f{i:04d}" for i in range(820))  # 820 * 6 - 1 = 4919 characters
        exact = flags[:4096].rsplit(",", 1)[0]
        exact += "," + "x" * (4096 - len(exact) - 1)
        assert len(exact) == 4096
        assert len(parse_profile(edit(text, "CPU_FLAGS_ALL", exact)).get("CPU_FLAGS_ALL")) == 4096
        with pytest.raises(ProfileError, match="longer than 4096 characters"):
            parse_profile(edit(text, "CPU_FLAGS_ALL", exact + "y"))
        as_json = parse_profile(text).as_dict()
        as_json["CPU_FLAGS_ALL"] = exact + "y"
        with pytest.raises(ProfileError, match="longer than 4096 characters"):
            parse_profile_json(json.dumps(as_json))

    def test_file_size_limit(self):
        big = fixture("schema2_rk3566_dietpi.txt") + ("# padding\n" * 20000)
        with pytest.raises(ProfileError, match="larger than"):
            parse_profile(big.encode("utf-8"))

    def test_booleans_always_present(self):
        for key in SCHEMA.bool_keys:
            with pytest.raises(ProfileError, match=f"{key}: missing"):
                parse_profile(edit(fixture("schema2_pc_two_nvidia.txt"), key, None))
        for key in ("GPU1_DISCRETE", "GPU1_UNIFIED", "GPU1_VENDOR"):
            with pytest.raises(ProfileError, match=f"{key}: missing"):
                parse_profile(edit(fixture("schema2_pc_two_nvidia.txt"), key, None))

    @pytest.mark.parametrize("key", ["PROBE_TOOL", "PROBE_TOOL_VERSION", "GENERATED_AT", "OS", "ARCH", "KERNEL",
                                     "CPU_CORES_LOGICAL", "RAM_TOTAL_MB", "GPU_COUNT"])
    def test_required_keys(self, key):
        with pytest.raises(ProfileError, match=f"{key}: missing"):
            parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), key, None))

    def test_format_errors(self):
        text = fixture("schema2_rk3566_dietpi.txt")
        with pytest.raises(ProfileError, match="CPU_MODEL is empty"):
            parse_profile(edit(text, "CPU_MODEL", ""))
        with pytest.raises(ProfileError, match="not KEY=VALUE"):
            parse_profile(text + "just a line\n")
        with pytest.raises(ProfileError, match="not a valid key"):
            parse_profile(text + "lower_case=1\n")
        with pytest.raises(ProfileError, match="not a valid key"):
            parse_profile(text + "ARCH =arm64\n")
        with pytest.raises(ProfileError, match="first line"):
            parse_profile("OS=linux\n" + text)
        with pytest.raises(ProfileError, match="reads schema 1 and 2"):
            parse_profile(text.replace("PROBE_SCHEMA=2", "PROBE_SCHEMA=3"))
        with pytest.raises(ProfileError, match="must be UTF-8"):
            parse_profile(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
        with pytest.raises(ProfileError, match="control or format character"):
            parse_profile(edit(text, "BOARD_MODEL", "Radxa\rZERO"))

    def test_error_messages_escape_untrusted_values(self):
        with pytest.raises(ProfileError) as e:
            parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), "ARCH", "\x1b]0;evil\x07" + "A" * 200))
        assert "\x1b" not in str(e.value) and len(str(e.value)) < 300

    def test_json_view_rules(self):
        data = board().as_dict()
        with pytest.raises(ProfileError, match="values are strings"):
            parse_profile_json(json.dumps(dict(data, CPU_CORES_LOGICAL=4)))
        with pytest.raises(ProfileError, match="PROBE_SCHEMA"):
            parse_profile_json(json.dumps({k: v for k, v in data.items() if k != "PROBE_SCHEMA"}))
        with pytest.raises(ProfileError, match="one object"):
            parse_profile_json("[]")
        with pytest.raises(ProfileError, match="not valid JSON"):
            parse_profile_json("{")

    def test_deeply_nested_json_is_a_profile_error(self, tmp_path):
        """A hostile file far below the size limit must not escape as RecursionError (callers catch ProfileError)."""
        nested = '{"PROBE_SCHEMA": "2", "X": ' + "[" * 60000 + "]" * 60000 + "}"
        assert len(nested) < SCHEMA.max_bytes
        with pytest.raises(ProfileError, match="not valid JSON"):
            parse_profile_json(nested)
        (tmp_path / "deep.json").write_text(nested, encoding="utf-8")
        with pytest.raises(ProfileError, match="deep.json: not valid JSON"):
            read_profile(tmp_path / "deep.json")

    @pytest.mark.parametrize("node", ["/dev/rknpu", "/dev/dri/renderD129", "/dev/dri/card1", "/dev/accel/accel0",
                                      "/dev/hailo0", "/dev/apex_0"])
    def test_npu_device_accelerator_nodes(self, node):
        assert parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), "NPU_DEVICE", node)).get("NPU_DEVICE") == node

    @pytest.mark.parametrize("node", ["/dev/sda", "/dev/mem", "/dev/kmem", "/dev/nvme0n1", "/dev/dri/../sda",
                                      "/dev/accel/sda", "/dev/rknpu0x", "/dev/-x"])
    def test_npu_device_is_never_another_device_node(self, node):
        """NPU_DEVICE may reach `docker run --device` of a deploy step: an untrusted profile must not name a disk or
        memory device."""
        with pytest.raises(ProfileError, match="NPU_DEVICE"):
            parse_profile(edit(fixture("schema2_rk3566_dietpi.txt"), "NPU_DEVICE", node))


# --- perf_key ----------------------------------------------------------------------------------------------------

class TestPerfKey:
    def test_stable_across_parses_and_line_order(self):
        text = fixture("schema2_pc_two_nvidia.txt")
        lines = text.splitlines()
        shuffled = "\n".join([lines[0], lines[1]] + list(reversed(lines[2:]))) + "\n"
        assert parse_profile(text).perf_key() == parse_profile(text).perf_key() == parse_profile(shuffled).perf_key()

    def test_golden_value(self):
        """A change here means every stored benchmark result becomes stale: bump PERF_KEY_VERSION on purpose."""
        assert board().perf_key() == "2beb8057d73cf1dcc0a5db4f830650d61814ef9b953159f42733c224929fd833"
        assert board().perf_fields()["gpus"] == [{"VENDOR": "arm"}] and board().perf_fields()["v"] == 1

    def test_gpu_order_does_not_matter(self):
        text = fixture("schema2_pc_two_nvidia.txt")
        swapped = text.replace("GPU0_", "GPUX_").replace("GPU1_", "GPU0_").replace("GPUX_", "GPU1_")
        assert parse_profile(swapped).gpus()[0]["COMPUTE_CAP"] == "7.5"
        assert parse_profile(swapped).perf_key() == parse_profile(text).perf_key()

    @pytest.mark.parametrize("key,value", [
        ("GENERATED_AT", "2027-01-01T00:00:00Z"), ("PROBE_TOOL", "sh"), ("PROBE_TOOL_VERSION", "3.1.0"),
        ("PROBE_WARNINGS", "not_determined:TOOL_NVCC"), ("KERNEL", "6.8.0-90-generic"),
        ("RAM_AVAILABLE_MB", "12"), ("DISK_FREE_MB", "1"), ("GPU0_VRAM_FREE_MB", "100"),
        ("OS_ID", "linuxmint"), ("OS_VERSION_ID", "24.10"), ("OS_PRETTY_NAME", "Ubuntu 24.10"),
        ("LIBC_VERSION", "2.40"), ("RAM_TOTAL_MB", "65000"), ("SWAP_TOTAL_MB", "0"),
        ("CPU_CORES_LOGICAL", "12"), ("GPU0_DRIVER_VERSION", "590.10"), ("GPU1_VRAM_MB", "11000"),
        ("NVIDIA_DRIVER_CUDA_MAX", "13.1"), ("VULKAN_DEVICES", "3"), ("GPU0_NAME", "Renamed"),
        ("TOOL_CMAKE", "4.1.2"), ("TOOL_CC", "clang-19.1.5"), ("TOOL_DOCKER", None),
        ("CPU_FLAGS_ALL", "fpu,sse,avx"),
    ])
    def test_ignores_volatile_recorded_and_meta_keys(self, key, value):
        text = fixture("schema2_pc_two_nvidia.txt")
        assert parse_profile(edit(text, key, value)).perf_key() == parse_profile(text).perf_key()

    @pytest.mark.parametrize("key,value", [
        ("SUPPORTS_AVX512", "ON"), ("SUPPORTS_F16C", "UNKNOWN"), ("CPU_MODEL", "AMD Ryzen 9 5950X 16-Core Processor"),
        ("CPU_VENDOR", "intel"), ("LIBC", "musl"), ("GPU0_COMPUTE_CAP", "8.9"), ("GPU1_PCI_ID", "0x10de:0x1e04"),
        ("OS", "windows"),
    ])
    def test_changes_with_keyed_values(self, key, value):
        text = fixture("schema2_pc_two_nvidia.txt")
        if key == "OS":  # a consistent Windows variant of the same machine
            text = edit(edit(edit(text, "LIBC", "ucrt"), "INIT_SYSTEM", "scm"), "OS_FAMILY", "windows")
            text = edit(text, "HIP_RUNTIME", "OFF")
        changed = parse_profile(edit(text, key, value))
        assert changed.perf_key() != parse_profile(fixture("schema2_pc_two_nvidia.txt")).perf_key()

    def test_board_keys(self):
        text = fixture("schema2_rk3566_dietpi.txt")
        base = parse_profile(text).perf_key()
        assert parse_profile(edit(text, "SUPPORTS_DOTPROD", "OFF")).perf_key() != base
        assert parse_profile(edit(text, "CPU_CLUSTERS", "0-3:0x41:0xd05:2000")).perf_key() != base
        assert parse_profile(edit(text, "SOC_MODEL", "rk3568")).perf_key() != base
        assert parse_profile(edit(text, "OS_VARIANT_ID", None)).perf_key() == base
        assert parse_profile(edit(text, "BOARD_MODEL", "Other board")).perf_key() == base

    def test_recorded_values(self):
        recorded = parse_profile(fixture("schema2_pc_two_nvidia.txt")).recorded()
        assert recorded["LIBC_VERSION"] == "2.39" and recorded["GPU1_DRIVER_VERSION"] == "580.95.05"
        assert "GPU0_VRAM_FREE_MB" not in recorded and "KERNEL" not in recorded


# --- schema 1 ----------------------------------------------------------------------------------------------------

class TestSchema1:
    def test_sh_board(self):
        p = parse_profile(fixture("schema1_sh_rk3566.txt"))
        assert p.source_schema == 1 and p.converted and p.needs_reprobe
        assert p.get("PROBE_TOOL") == "sh" and p.get("PROBE_CONVERTED_FROM") == "1"
        assert p.warnings == ["converted_from_schema_1", "reprobe_before_build"]
        assert (p.os, p.arch) == ("linux", "arm64")
        assert (p.get("SOC_VENDOR"), p.get("SOC_MODEL"), p.get("NPU_MODEL")) == ("rockchip", "rk3566", "rk3566")
        assert p.get("NPU_DEVICE") == "/dev/rknpu" and p.is_on("SUPPORTS_RKNN")
        # derived from the Features line, not from the .sh's own SUPPORTS_* lines
        assert p.is_on("SUPPORTS_DOTPROD") and p.is_on("SUPPORTS_FP16_ARITH") and p.is_on("SUPPORTS_NEON")
        assert p.flag("SUPPORTS_I8MM") == "OFF" and p.flag("SUPPORTS_F16C") == "OFF"
        assert (p.get("SWAP_TOTAL_MB"), p.get("TOOL_PYTHON")) == ("1955", "3.11.2")
        assert "RAM_SPEED_MTS" not in p and "RAM_TYPE" not in p and "TOOL_DOCKER" not in p
        assert p.flag("SUPPORTS_HAILO") == "UNKNOWN" and p.flag("METAL") == "OFF"
        assert "GPU_COUNT" not in p and p.gpus() == []

    def test_identifying_values_are_never_kept(self):
        for name, secrets_in_file in (("schema1_sh_rk3566.txt", ["fixture-host-0000"]),
                                      ("schema1_sh_laptop.txt", ["fixture-host-0001"]),
                                      ("schema1_ps1_desktop.txt", ["FIXTURE-PC-0000", "FAKE-PART-0001",
                                                                   "FAKE-PART-0002", "0000000000000000_REV0"])):
            p = parse_profile(fixture(name))
            everything = p.to_text() + p.to_json() + repr(p) + " ".join(p.notes)
            for value in secrets_in_file:
                assert value not in everything, (name, value)
            assert "dropped:HOSTNAME" in p.notes and "HOSTNAME" not in p

    def test_sh_laptop_with_duplicate_gpu_keys(self):
        p = parse_profile(fixture("schema1_sh_laptop.txt"))
        assert {"ambiguous:GPU_VENDOR", "ambiguous:GPU_DEVICE_ID"} <= set(p.notes)
        assert p.gpus() == [{"VENDOR": "nvidia", "NAME": "NVIDIA GeForce RTX 3060 Laptop GPU", "DISCRETE": "UNKNOWN",
                             "UNIFIED": "UNKNOWN", "VRAM_MB": "6144", "DRIVER_VERSION": "580.95.05"}]
        assert p.is_on("SUPPORTS_CUDA") and p.is_on("SUPPORTS_INTEL_XPU")
        assert p.get("CPU_VENDOR") == "intel" and p.get("CPU_FLAGS_SOURCE") == "proc-cpuinfo"
        for key in ("SUPPORTS_AVX2", "SUPPORTS_AVX512", "SUPPORTS_AVX512_VBMI", "SUPPORTS_AVX512_VNNI",
                    "SUPPORTS_F16C", "SUPPORTS_FMA", "SUPPORTS_BMI2", "SUPPORTS_SSE42"):
            assert p.is_on(key), key
        for key in ("SUPPORTS_AVX512_BF16", "SUPPORTS_AMX_TILE", "SUPPORTS_AVX_VNNI", "SUPPORTS_NEON"):
            assert p.flag(key) == "OFF", key
        assert "gpu_list_incomplete" in p.notes

    def test_ps1_cpu_features_become_unknown(self):
        p = parse_profile(fixture("schema1_ps1_desktop.txt"))
        assert p.get("PROBE_TOOL") == "ps1"
        for key in SCHEMA.cpu_feature_keys:
            assert p.flag(key) == "UNKNOWN", key  # also NEON and AVX512, which the file claims TRUE
        assert "cpu_features_unknown" in p.warnings and "cpu_features_unknown" in p.notes

    def test_ps1_other_keys(self):
        p = parse_profile(fixture("schema1_ps1_desktop.txt"))
        assert (p.os, p.arch, p.libc, p.get("INIT_SYSTEM"), p.get("OS_FAMILY")) == (
            "windows", "x86_64", "ucrt", "scm", "windows")
        assert p.get("OS_VERSION_ID") == "10.0.26100" == p.get("KERNEL")
        assert "RAM_TOTAL_MB" not in p and "dropped:RAM_TOTAL_MB" in p.notes  # the sum of SMBIOS slots
        assert (p.get("RAM_TYPE"), p.get("RAM_SPEED_MTS")) == ("DDR4", "3200")
        assert p.gpus()[0]["PCI_ID"] == "0x10de:0x2204" and p.gpus()[0]["VRAM_MB"] == "24576"
        # a board attached over USB: no NPU of this machine, SUPPORTS_RKNN cannot be ON on Windows
        assert (p.get("USB_ROCKCHIP_PID"), p.get("USB_ROCKCHIP_MODE")) == ("0x350b", "rockusb")
        assert "NPU_VENDOR" not in p and p.flag("SUPPORTS_RKNN") == "OFF"
        assert p.get("TOOL_DOCKER") == "27.3.1" and "SOC_VENDOR" not in p
        assert "dropped:RAM_SLOT_*" in p.notes and "dropped:CPU_DEVICE_ID" in p.notes

    def test_ps1_encodings(self):
        text = fixture("schema1_ps1_desktop.txt")
        expected = parse_profile(text)
        assert parse_profile(codecs.BOM_UTF16_LE + text.replace("\n", "\r\n").encode("utf-16-le")) == expected
        ansi = text.replace("AMD Ryzen 9", "AMD Ryzen\u2122 9").encode("cp1252")  # Windows PowerShell 5.1 default
        assert parse_profile(ansi).get("CPU_MODEL") == "AMD Ryzen\u2122 9 5900X 12-Core Processor"

    def test_booleans_always_present_after_conversion(self):
        for name in ("schema1_sh_rk3566.txt", "schema1_sh_laptop.txt", "schema1_ps1_desktop.txt"):
            p = parse_profile(fixture(name))
            for key in SCHEMA.bool_keys:
                assert p.flag(key) == p.get(key), (name, key)
            for gpu in p.gpus():
                assert {"DISCRETE", "UNIFIED"} <= gpu.keys()

    def test_converted_profile_round_trips_as_schema_2(self):
        for name in ("schema1_sh_rk3566.txt", "schema1_sh_laptop.txt", "schema1_ps1_desktop.txt"):
            p = parse_profile(fixture(name))
            again = parse_profile(p.to_text())
            assert again == p and again.converted and again.source_schema == 1

    def test_invalid_values_are_dropped(self):
        text = fixture("schema1_sh_rk3566.txt").replace("KERNEL=6.1.99-vendor-rk35xx", "KERNEL=6.1 $(reboot)")
        p = parse_profile(text)
        assert "KERNEL" not in p and "invalid:KERNEL" in p.notes

    def test_without_flag_list_features_are_unknown(self):
        text = fixture("schema1_sh_laptop.txt")
        text = "\n".join(line for line in text.splitlines() if not line.startswith("CPU_FLAGS_ALL"))
        p = parse_profile(text)
        assert p.flag("SUPPORTS_AVX2") == "UNKNOWN"  # the .sh wrote ON, but without the list it cannot tell
        assert p.flag("SUPPORTS_NEON") == "OFF"  # an x86 machine has no NEON

    def test_not_a_probe_file(self):
        with pytest.raises(ProfileError, match="not a hardware probe file"):
            parse_profile("hello=world\n# nothing else\n")


# --- the store ---------------------------------------------------------------------------------------------------

class TestProfileStore:
    def test_local_round_trip_without_volatile_keys(self, tmp_path):
        store = ProfileStore(tmp_path)
        path = store.save_local(parse_profile(fixture("schema2_pc_two_nvidia.txt")))
        assert path == tmp_path / "hardware" / "local.txt"
        loaded = store.load_local()
        for key in ("RAM_AVAILABLE_MB", "DISK_FREE_MB", "GPU0_VRAM_FREE_MB", "GPU1_VRAM_FREE_MB"):
            assert key not in loaded
        assert loaded.perf_key() == parse_profile(fixture("schema2_pc_two_nvidia.txt")).perf_key()
        assert loaded.get("GENERATED_AT") == "2026-10-11T09:30:00Z"

    @POSIX_ONLY
    def test_private_files(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.save_local(board())
        # 192.0.2.0/24 and 2001:db8::/32 are documentation ranges (RFC 5737, RFC 3849), never real machines
        store.save_device("0123456789abcdef", board(), label="Board on the desk", address="192.0.2.10")
        assert stat.S_IMODE((tmp_path / "hardware").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "hardware" / "devices").stat().st_mode) == 0o700
        for f in ("local.txt", "devices/0123456789abcdef.txt", "devices/0123456789abcdef.json"):
            assert stat.S_IMODE((tmp_path / "hardware" / f).stat().st_mode) == 0o600, f
        assert not list((tmp_path / "hardware").rglob("*.tmp"))

    def test_devices(self, tmp_path):
        store = ProfileStore(tmp_path)
        device = device_id_for_host_key("SHA256:" + "A" * 43)
        assert device == device_id_for_host_key("SHA256:" + "A" * 43) and pf.DEVICE_ID.fullmatch(device)
        assert store.load_device(device) is None and store.list_devices() == []
        store.save_device(device, board(), label="  Board   on the desk ", address="[2001:db8::1]:2222")
        assert store.load_device(device) == board().stable()
        info = store.device_info(device)
        assert (info["label"], info["address"]) == ("Board on the desk", "[2001:db8::1]:2222")
        store.save_device(device, board())  # keeps label and address
        assert store.device_info(device)["label"] == "Board on the desk"
        other = new_device_id()
        store.save_device(other, parse_profile(fixture("schema1_sh_rk3566.txt")))
        assert store.list_devices() == sorted([device, other])
        assert store.load_device(other).converted
        assert store.remove_device(device) and not store.remove_device(device)
        assert store.list_devices() == [other]

    @pytest.mark.parametrize("bad", ["../../etc/passwd", "0123456789ABCDEF", "0123", "local", "", None])
    def test_device_ids_are_validated(self, tmp_path, bad):
        store = ProfileStore(tmp_path)
        with pytest.raises(ValueError):
            store.device_path(bad)
        with pytest.raises(ValueError):
            store.save_device(bad, board())
        assert not (tmp_path / "hardware").exists() or not any((tmp_path / "hardware").rglob("*"))

    def test_labels_addresses_and_fingerprints_are_validated(self, tmp_path):
        store = ProfileStore(tmp_path)
        for kwargs in ({"label": "x" * 65}, {"label": "evil\x1b[2J"}, {"address": "host; reboot"},
                       {"address": "user@host"}, {"address": "-oProxyCommand=x"}):
            with pytest.raises(ValueError):
                store.save_device("0123456789abcdef", board(), **kwargs)
        assert store.list_devices() == [], "nothing is stored when the label or address is refused"
        for fp in ("SHA256:short", "MD5:" + "a" * 43, "", None):
            with pytest.raises(ValueError):
                device_id_for_host_key(fp)

    def test_device_info_ignores_tampered_fields(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.save_device("0123456789abcdef", board(), label="ok", address="board.example")
        meta = tmp_path / "hardware" / "devices" / "0123456789abcdef.json"
        meta.write_text(json.dumps({"label": "bad\x07", "address": "a b", "updated_at": "now", "extra": 1}))
        assert store.device_info("0123456789abcdef") == {}
        meta.write_text("[1, 2]")
        assert store.device_info("0123456789abcdef") == {}
        meta.write_text("[" * 15000)  # below the size limit, deeper than the JSON decoder's recursion limit
        assert store.device_info("0123456789abcdef") == {}

    def test_refuses_a_profile_that_changes_through_its_file(self, tmp_path):
        """A HardwareProfile built in code with a line break in a value must not smuggle another key into the store."""
        values = dict(board().values, CPU_MODEL="Cortex-A55\nRKNPU_DRIVER_VERSION=1.2.3")
        smuggled = HardwareProfile(values, schema=SCHEMA)
        store = ProfileStore(tmp_path)
        with pytest.raises(ProfileError, match="round trip"):
            store.save_local(smuggled)
        with pytest.raises(ProfileError, match="round trip"):
            store.save_device("0123456789abcdef", smuggled, label="board")
        assert not store.local_path().exists() and store.list_devices() == []
        assert not (tmp_path / "hardware" / "devices" / "0123456789abcdef.json").exists()

    def test_damaged_store_file(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.save_local(board())
        store.local_path().write_text(fixture("schema2_rk3566_dietpi.txt") + "ARCH=x86_64\n", encoding="utf-8")
        with pytest.raises(ProfileError, match="local.txt: .*ARCH appears twice"):
            store.load_local()

    def test_refuses_to_store_an_invalid_profile(self, tmp_path):
        store = ProfileStore(tmp_path)
        broken = HardwareProfile({"PROBE_SCHEMA": "2", "OS": "linux"}, schema=SCHEMA)
        with pytest.raises(ProfileError):
            store.save_local(broken)
        assert not store.local_path().exists()
        with pytest.raises(TypeError):
            store.save_local(board().as_dict())

    def test_default_state_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home"))
        store = ProfileStore()
        assert store.root == tmp_path / "home" / "hardware"
        store.save_local(board())
        assert (tmp_path / "home" / "hardware" / "local.txt").is_file()
