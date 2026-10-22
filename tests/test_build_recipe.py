"""The llama.cpp build recipe (orchestrator/runtime/build/recipe.py), the pin and its places.

Golden plans live in tests/fixtures/build_plans/. After an intended change of the recipe, regenerate them with
LLM_FRAMEWORK_UPDATE_GOLDEN=1 and review the diff before committing.
"""

import json
import os
from pathlib import Path

import pytest
import yaml

from orchestrator.runtime.build import pins
from orchestrator.runtime.build.recipe import (DOWNLOAD_OPTIONS_OFF, NEVER_SET, X86_FEATURES, PlanError,
                                               ToolchainInfo, arm_arch, check_download_options, choose_backend,
                                               choose_build_base, choose_cuda_toolkit, plan_build, plan_variants)
from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog
from orchestrator.runtime.profile import HardwareProfile, load_schema, parse_profile, read_profile
from orchestrator.utils import llama_pin

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "hardware_probe"
GOLDEN = ROOT / "tests" / "fixtures" / "build_plans"
UPDATE = os.environ.get("LLM_FRAMEWORK_UPDATE_GOLDEN") == "1"
CATALOG = load_catalog()
PIN = CATALOG.llama_cpp.source


def probe(name: str) -> HardwareProfile:
    return read_profile(PROBES / name)


def changed(profile: HardwareProfile, drop=(), **values) -> HardwareProfile:
    """The profile with keys changed or dropped, validated by the parser again (a real probe file could say it)."""
    data = {k: v for k, v in profile.as_dict().items() if k not in drop}
    data.update(values)
    text = "\n".join(f"{k}={v}" for k, v in data.items()) + "\n"
    return parse_profile(text)


def raw(profile: HardwareProfile, **values) -> HardwareProfile:
    """The profile with values the parser would refuse: what a HardwareProfile built in code could hold."""
    data = dict(profile.as_dict())
    data.update(values)
    return HardwareProfile(data, schema=profile.schema)


RK3566 = probe("schema2_rk3566_dietpi.txt")
TWO_NVIDIA = probe("schema2_pc_two_nvidia.txt")
Z1_LINUX = probe("schema2_linux_z1_extreme.txt")
Z1_WINDOWS = probe("schema2_windows_apu.txt")
IVYBRIDGE = probe("schema2_linux_ivybridge.txt")

LINUX_BUILD_HOST = ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1")
WINDOWS_BUILD_HOST = ToolchainInfo(host_os="windows", host_arch="x86_64", compiler="clang-19.1.5", cmake="3.31.6")

# The maintainer's hardware (decision D14: the probe ran on the target, the plan follows its result).
SCENARIOS = {
    "rk3566_dietpi_cross": dict(profile=RK3566, purpose="device-runtime", placement="cross",
                                toolchain=LINUX_BUILD_HOST),
    "rtx2080ti_rtx3090_cuda": dict(profile=TWO_NVIDIA, purpose="host-runtime", placement="native",
                                   toolchain=ToolchainInfo.from_profile(TWO_NVIDIA)),
    "z1_extreme_linux_vulkan": dict(profile=Z1_LINUX, purpose="host-runtime", placement="native",
                                    toolchain=ToolchainInfo.from_profile(Z1_LINUX)),
    "z1_extreme_windows_vulkan_clang": dict(profile=Z1_WINDOWS, purpose="host-runtime", placement="native",
                                            toolchain=ToolchainInfo.from_profile(Z1_WINDOWS)),
    "ivybridge_pre_avx2_cross": dict(profile=IVYBRIDGE, purpose="device-runtime", placement="cross",
                                     toolchain=LINUX_BUILD_HOST),
    "windows_unknown_features_variants": dict(profile=Z1_WINDOWS, purpose="device-runtime", placement="cross",
                                              backend="cpu", toolchain=WINDOWS_BUILD_HOST),
    "host_tools_container": dict(profile=TWO_NVIDIA, purpose="host-tools", placement="container",
                                 toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1",
                                                         base_image="debian:bookworm-slim")),
}


def plan(name: str, **overrides):
    kwargs = dict(SCENARIOS[name])
    kwargs.update(overrides)
    profile = kwargs.pop("profile")
    return plan_build(profile, catalog=kwargs.pop("catalog", CATALOG), **kwargs)


# --- golden plans -------------------------------------------------------------------------------------------------

class TestGoldenPlans:

    @pytest.mark.parametrize("name", sorted(SCENARIOS))
    def test_plan_matches_the_golden_file(self, name):
        text = plan(name).to_json()
        path = GOLDEN / f"{name}.json"
        if UPDATE:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        assert path.is_file(), f"{path.name} is missing: run with LLM_FRAMEWORK_UPDATE_GOLDEN=1 and review it"
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads(text), \
            f"the plan for {name} changed; if intended, regenerate with LLM_FRAMEWORK_UPDATE_GOLDEN=1 and review"

    def test_every_golden_file_has_a_scenario(self):
        assert {p.stem for p in GOLDEN.glob("*.json")} == set(SCENARIOS)

    @pytest.mark.parametrize("name", sorted(SCENARIOS))
    def test_no_download_capable_option_is_set(self, name):
        for step in plan(name).steps:
            options = dict(step.options)
            check_download_options(options)
            assert not set(NEVER_SET) & set(options)
            assert all(options[k] == "OFF" for k in DOWNLOAD_OPTIONS_OFF)
            assert options["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON"
            assert options["LLAMA_SUBPROCESS"] == "OFF"
            assert options["LLAMA_OPENSSL"] == "OFF"
            assert options["LLAMA_BUILD_NUMBER"] == str(PIN.build_number)
            assert options["LLAMA_BUILD_COMMIT"] == PIN.commit[:8]
            assert options["GGML_NATIVE"] in ("ON", "OFF"), "GGML_NATIVE is always explicit"

    @pytest.mark.parametrize("name", sorted(SCENARIOS))
    def test_every_plan_builds_the_pinned_commit(self, name):
        p = plan(name)
        assert p.source["commit"] == PIN.commit and p.source["tag"] == PIN.tag
        assert p.verify["version"] == {"build_number": PIN.build_number, "commit": PIN.commit[:8]}
        assert len(p.build_id) == 64


# --- what the recipe derives -------------------------------------------------------------------------------------

class TestDerivedOptions:

    def test_rk3566_cross_build_follows_the_board(self):
        p = plan("rk3566_dietpi_cross")
        o = p.options()
        assert o["GGML_NATIVE"] == "OFF" and o["GGML_CPU_ARM_ARCH"] == "armv8.2-a+dotprod+fp16"
        assert o["BUILD_SHARED_LIBS"] == "OFF" and o["GGML_OPENMP"] == "ON"
        assert o["CMAKE_EXE_LINKER_FLAGS"] == "-static-libstdc++ -static-libgcc"
        assert o["CMAKE_INSTALL_RPATH"] == "$ORIGIN;$ORIGIN/../lib"
        assert p.builder["base"] == "debian-12", "DietPi on Debian 12: glibc 2.36, the newest base not newer"
        assert p.target == {"os": "linux", "arch": "arm64", "libc": "glibc", "libc_version": "2.36",
                            "triple": "aarch64-linux-gnu"}
        assert "set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)" in p.steps[0].toolchain_file
        assert p.verify["glibc_max"] == "2.36" and p.verify["expect_sdot"] is True
        assert p.verify["compile_check_flags"] == ["-march=armv8.2-a+dotprod+fp16"]
        assert p.verify["runtime_libs"] == ["libgomp.so.1"]
        assert p.backend == "cpu" and any("D8" in n for n in p.notes), "Mali: Vulkan only after measurement"

    def test_two_nvidia_gpus_get_real_code_for_both_and_a_clamped_toolkit(self):
        p = plan("rtx2080ti_rtx3090_cuda")
        o = p.options()
        assert p.backend == "cuda" and o["GGML_CUDA"] == "ON" and o["GGML_NATIVE"] == "ON"
        assert o["CMAKE_CUDA_ARCHITECTURES"] == "75-real;86-real"
        assert o["GGML_CUDA_NCCL"] == "OFF"
        assert p.toolchain["cuda"] == "13.0.2", "the driver supports CUDA 13.0: 13.4 is clamped away (D6)"
        assert p.verify["devices"] == ["CUDA0", "CUDA1"]
        assert "native" in p.toolchain, "a GGML_NATIVE build is keyed by the CPU it is built on"

    @pytest.mark.parametrize("cuda_max, caps, compiler, expected", [
        ("13.4", ("8.6", "7.5"), "gcc-13.3.0", "13.4.2"),
        ("13.0", ("8.6", "7.5"), "gcc-13.3.0", "13.0.2"),
        ("12.9", ("8.6", "7.5"), "gcc-13.3.0", "12.9.1"),
        ("12.8", ("8.6", "7.5"), "gcc-13.3.0", "12.8.1"),
        ("13.4", ("6.1",), "gcc-13.3.0", "12.9.1"),         # a GTX 10xx needs CUDA 12
        ("13.4", ("8.6", "7.5"), "gcc-15.2.0", "13.4.2"),
        ("13.0", ("8.6", "7.5"), "gcc-16.1.0", None),        # no toolkit accepts GCC 16 within the driver's limit
        ("12.6", ("8.6",), "gcc-13.3.0", None),              # driver older than every pinned toolkit
        ("13.4", ("12.0",), "gcc-13.3.0", "13.4.2"),
        ("12.8", ("12.1",), "gcc-13.3.0", None),             # compute 12.1 needs CUDA 12.9 or newer
    ])
    def test_cuda_toolkit_meets_all_four_constraints(self, cuda_max, caps, compiler, expected):
        values = {"NVIDIA_DRIVER_CUDA_MAX": cuda_max, "GPU_COUNT": str(len(caps))}
        drop = [k for k in TWO_NVIDIA.values if k.startswith("GPU1_")]
        for n, cap in enumerate(caps):
            values[f"GPU{n}_COMPUTE_CAP"] = cap
            for key in ("VENDOR", "DISCRETE", "UNIFIED"):
                values[f"GPU{n}_{key}"] = TWO_NVIDIA.get(f"GPU0_{key}")
        profile = changed(TWO_NVIDIA, drop=drop, **values)
        toolkit, why = choose_cuda_toolkit(profile, CATALOG, ToolchainInfo.from_profile(profile, compiler=compiler))
        assert (toolkit.version if toolkit else None) == expected, why

    def test_cmake_too_old_for_cuda_is_named(self):
        tc = ToolchainInfo.from_profile(TWO_NVIDIA, cmake="3.16.3")
        toolkit, why = choose_cuda_toolkit(TWO_NVIDIA, CATALOG, tc)
        assert toolkit is None and any("CMake 3.18" in w for w in why)

    def test_a_toolkit_named_by_the_toolchain_must_be_pinned_and_fit(self):
        assert choose_cuda_toolkit(TWO_NVIDIA, CATALOG, ToolchainInfo.from_profile(TWO_NVIDIA, cuda="11.8.0"))[0] \
            is None
        with pytest.raises(PlanError, match="newer than the driver"):
            plan("rtx2080ti_rtx3090_cuda", toolchain=ToolchainInfo.from_profile(TWO_NVIDIA, cuda="13.4.2"),
                 backend="cuda")

    def test_plan_variants_builds_the_cpu_fallback_first(self):
        plans = plan_variants(TWO_NVIDIA, purpose="host-runtime", placement="native", catalog=CATALOG,
                              toolchain=ToolchainInfo.from_profile(TWO_NVIDIA))
        assert [p.backend for p in plans] == ["cpu", "cuda"]
        assert plans[0].options()["GGML_CUDA"] == "OFF" and plans[0].build_id != plans[1].build_id
        board = plan_variants(RK3566, purpose="device-runtime", placement="cross", catalog=CATALOG,
                              toolchain=LINUX_BUILD_HOST)
        assert [p.backend for p in board] == ["cpu"]

    def test_z1_extreme_linux_is_a_native_vulkan_build(self):
        p = plan("z1_extreme_linux_vulkan")
        o = p.options()
        assert p.backend == "vulkan" and o["GGML_VULKAN"] == "ON" and o["GGML_NATIVE"] == "ON"
        assert o["GGML_OPENMP"] == "ON" and p.toolchain["glslc"] == "2025.3"
        assert p.builder == {"kind": "native"} and p.verify["devices"] == ["Vulkan0"]

    def test_z1_extreme_windows_uses_clang_with_native_cpu_code(self):
        p = plan("z1_extreme_windows_vulkan_clang")
        o = p.options()
        assert p.backend == "vulkan" and p.steps[0].compiler == "clang"
        assert o["CMAKE_C_COMPILER"] == "clang" and o["CMAKE_CXX_COMPILER"] == "clang++"
        assert o["GGML_NATIVE"] == "ON", "built on the machine itself: UNKNOWN probe features do not matter"
        assert o["GGML_OPENMP"] == "OFF", "LLVM's OpenMP would be downloaded at configure time on Windows"
        assert "GGML_BACKEND_DL" not in o and o["BUILD_SHARED_LIBS"] == "OFF"

    def test_old_cpu_gets_exactly_its_features(self):
        p = plan("ivybridge_pre_avx2_cross")
        o = p.options()
        assert o["GGML_NATIVE"] == "OFF"
        on = {opt for opt in X86_FEATURES.values() if o[opt] == "ON"}
        assert on == {"GGML_SSE42", "GGML_AVX", "GGML_F16C"}
        assert all(o[opt] in ("ON", "OFF") for opt in X86_FEATURES.values()), "all 14 options are explicit"
        assert p.verify["compile_check_flags"] == ["-msse4.2", "-mf16c", "-mavx"]
        assert p.builder["base"] == "debian-11" and p.steps[0].toolchain_file is None
        assert any("GCC 10" in n for n in p.notes), "the unverified compiler floor is said"

    def test_windows_profile_with_unknown_features_gets_all_cpu_variants(self):
        p = plan("windows_unknown_features_variants")
        o = p.options()
        assert o["GGML_NATIVE"] == "OFF" and o["GGML_BACKEND_DL"] == "ON" and o["GGML_CPU_ALL_VARIANTS"] == "ON"
        assert o["BUILD_SHARED_LIBS"] == "ON", "GGML_BACKEND_DL requires shared libraries"
        assert "CMAKE_INSTALL_RPATH" not in o, "Windows finds the DLLs next to the executable"
        assert p.builder == {"kind": "native", "host": "another Windows PC"}

    def test_linux_variants_carry_an_origin_rpath(self):
        p = plan_build(changed(IVYBRIDGE, SUPPORTS_FMA="UNKNOWN"), purpose="device-runtime", placement="cross",
                       catalog=CATALOG, toolchain=LINUX_BUILD_HOST)
        o = p.options()
        assert o["GGML_CPU_ALL_VARIANTS"] == "ON" and o["CMAKE_INSTALL_RPATH"] == "$ORIGIN;$ORIGIN/../lib"
        assert o["CMAKE_SHARED_LINKER_FLAGS"] == "-static-libstdc++ -static-libgcc"
        # GGML_BACKEND_DL builds the CPU variants as MODULE libraries (ggml/src/CMakeLists.txt:381-383 at the pin),
        # which CMake links with CMAKE_MODULE_LINKER_FLAGS: without it they need the base's libstdc++.so.6.
        assert o["CMAKE_MODULE_LINKER_FLAGS"] == "-static-libstdc++ -static-libgcc"

    def test_host_tools_are_cpu_builds_in_the_target_image(self):
        p = plan("host_tools_container")
        o = p.options()
        assert p.targets == ("llama-quantize", "llama-imatrix", "llama-perplexity", "llama-completion",
                             "llama-bench")
        assert o["LLAMA_BUILD_SERVER"] == "OFF"
        # Tuned to the build host (D13) with its probe's CPU features written out, not GGML_NATIVE inside the image
        # build (design 4.3): the Ryzen 9 5900X has AVX2, no AVX-512.
        assert o["GGML_NATIVE"] == "OFF" and o["GGML_AVX2"] == "ON" and o["GGML_AVX512"] == "OFF"
        assert set(X86_FEATURES.values()) <= set(o) and "native" not in p.toolchain
        assert p.builder["base"] == "target-image" and "CMAKE_EXE_LINKER_FLAGS" not in o
        with pytest.raises(PlanError, match="CPU builds"):
            plan("host_tools_container", backend="cuda")

    def test_host_tools_follow_the_build_host_probe(self):
        old = plan("host_tools_container", profile=IVYBRIDGE)
        assert old.options()["GGML_AVX"] == "ON" and old.options()["GGML_AVX2"] == "OFF", "no SIGILL before Haswell"
        # A probe that could not tell the CPU features (a Windows build host): all variants, chosen at run time.
        unknown = plan("host_tools_container", profile=changed(TWO_NVIDIA, SUPPORTS_AVX512="UNKNOWN"))
        o = unknown.options()
        assert o["GGML_CPU_ALL_VARIANTS"] == "ON" and o["GGML_BACKEND_DL"] == "ON" and o["BUILD_SHARED_LIBS"] == "ON"
        assert o["CMAKE_INSTALL_RPATH"] == "$ORIGIN" and "CMAKE_EXE_LINKER_FLAGS" not in o
        assert len({old.build_id, unknown.build_id, plan("host_tools_container").build_id}) == 3

    @pytest.mark.parametrize("flags, expected", [
        ({}, "armv8-a"),
        ({"SUPPORTS_DOTPROD": "ON"}, "armv8.2-a+dotprod"),
        ({"SUPPORTS_DOTPROD": "ON", "SUPPORTS_FP16_ARITH": "ON", "SUPPORTS_I8MM": "ON"},
         "armv8.2-a+dotprod+fp16+i8mm"),
        ({"SUPPORTS_DOTPROD": "ON", "SUPPORTS_FP16_ARITH": "ON", "SUPPORTS_SVE": "ON"}, "armv8.2-a+dotprod+fp16+sve"),
        ({"SUPPORTS_DOTPROD": "UNKNOWN", "SUPPORTS_FP16_ARITH": "ON"}, "armv8.2-a+fp16"),
    ])
    def test_arm_arch_asks_only_for_confirmed_features(self, flags, expected):
        base = {k: "OFF" for k in ("SUPPORTS_DOTPROD", "SUPPORTS_FP16_ARITH", "SUPPORTS_I8MM", "SUPPORTS_SVE")}
        base.update(flags)
        assert arm_arch(changed(RK3566, **base)) == expected

    def test_arm_board_with_unknown_features_gets_all_cpu_variants(self):
        # glibc 2.41: the Debian 13 builder with GCC 14, which knows the +sme of upstream's armv9.2 variants
        p = plan_build(changed(RK3566, SUPPORTS_DOTPROD="UNKNOWN", LIBC_VERSION="2.41"), purpose="device-runtime",
                       placement="cross", catalog=CATALOG, toolchain=LINUX_BUILD_HOST)
        assert p.builder["base"] == "debian-13"
        assert p.options()["GGML_CPU_ALL_VARIANTS"] == "ON" and "GGML_CPU_ARM_ARCH" not in p.options()

    @pytest.mark.parametrize("libc_version, base, gcc", [("2.36", "debian-12", "12.2"), ("2.39", "ubuntu-24.04", "13.3"),
                                                         ("2.31", "debian-11", "10.2")])
    def test_arm_variants_with_a_compiler_that_lacks_sme_are_refused(self, libc_version, base, gcc):
        """GCC 13 and older reject -march=armv9.2-a+sme, which upstream's ARM variant list contains (no option
        shortens it): the plan says why and how to get a build, instead of failing deep inside the build."""
        with pytest.raises(PlanError) as e:
            plan_build(changed(RK3566, SUPPORTS_DOTPROD="UNKNOWN", LIBC_VERSION=libc_version),
                       purpose="device-runtime", placement="cross", catalog=CATALOG, toolchain=LINUX_BUILD_HOST)
        message = str(e.value)
        assert "armv9.2-a+sme" in message and "GCC 14" in message and f"{base} has GCC {gcc}" in message
        assert "Probe the machine again" in message

    def test_arm_variants_with_an_unknown_compiler_version_say_so(self):
        alpine = changed(RK3566, SUPPORTS_DOTPROD="UNKNOWN", LIBC="musl", LIBC_VERSION="1.2.5", OS_ID="alpine",
                         OS_FAMILY="alpine", drop=("OS_VARIANT_ID", "OS_VERSION_CODENAME"))
        p = plan_build(alpine, purpose="device-runtime", placement="cross", catalog=CATALOG,
                       toolchain=LINUX_BUILD_HOST)
        assert p.options()["GGML_CPU_ALL_VARIANTS"] == "ON"
        assert any("armv9.2-a+sme" in n and "not known" in n for n in p.notes)

    def test_linux_clang_uses_openmp_only_with_libomp(self):
        """CMake's FindOpenMP fails quietly without libomp, and a Clang build would lose OpenMP unnoticed."""
        clang = ToolchainInfo(host_os="linux", host_arch="x86_64", compiler="clang-18.1.3", cmake="3.28.3")
        without = plan_build(TWO_NVIDIA, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                             toolchain=clang)
        unchecked_is_off = without.options()["GGML_OPENMP"]
        detected = plan_build(TWO_NVIDIA, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                              toolchain=ToolchainInfo(**{**clang.__dict__, "openmp": True}))
        missing = plan_build(TWO_NVIDIA, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                             toolchain=ToolchainInfo(**{**clang.__dict__, "openmp": False}))
        assert unchecked_is_off == "OFF" and missing.options()["GGML_OPENMP"] == "OFF"
        assert any("libomp" in n for n in without.notes)
        assert detected.options()["GGML_OPENMP"] == "ON"
        assert detected.build_id != missing.build_id, "the build ID tells an OpenMP build from one without"
        assert missing.build_id == without.build_id

    def test_gcc_keeps_openmp_unless_detection_failed(self):
        gcc = ToolchainInfo.from_profile(TWO_NVIDIA)
        assert plan_build(TWO_NVIDIA, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                          toolchain=gcc).options()["GGML_OPENMP"] == "ON"
        broken = ToolchainInfo(**{**gcc.__dict__, "openmp": False})
        p = plan_build(TWO_NVIDIA, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                       toolchain=broken)
        assert p.options()["GGML_OPENMP"] == "OFF" and any("OpenMP" in n for n in p.notes)

    def test_mali_vulkan_only_when_asked_for(self):
        assert choose_backend(RK3566, purpose="device-runtime", placement="cross", catalog=CATALOG,
                              toolchain=LINUX_BUILD_HOST)[0] == "cpu"
        p = plan("rk3566_dietpi_cross", backend="vulkan")
        assert p.options()["GGML_VULKAN"] == "ON" and any("D8" in n for n in p.notes)

    def test_musl_board_gets_the_alpine_base_and_clang_with_a_sysroot(self):
        alpine = changed(RK3566, LIBC="musl", LIBC_VERSION="1.2.5", OS_ID="alpine", OS_FAMILY="alpine",
                         drop=("OS_VARIANT_ID", "OS_VERSION_CODENAME"))
        p = plan_build(alpine, purpose="device-runtime", placement="cross", catalog=CATALOG,
                       toolchain=LINUX_BUILD_HOST)
        assert p.builder["base"] == "alpine-3.22" and p.target["triple"] == "aarch64-alpine-linux-musl"
        assert "set(CMAKE_SYSROOT /opt/sysroot)" in p.steps[0].toolchain_file
        assert p.options()["GGML_OPENMP"] == "OFF" and "glibc_max" not in p.verify
        with pytest.raises(PlanError, match="older than every builder base"):
            plan_build(changed(alpine, LIBC_VERSION="1.1.24"), purpose="device-runtime", placement="cross",
                       catalog=CATALOG, toolchain=LINUX_BUILD_HOST)

    def test_glibc_picks_the_newest_base_that_is_not_newer(self):
        assert choose_build_base(CATALOG, "glibc", "2.36").id == "debian-12"
        assert choose_build_base(CATALOG, "glibc", "2.38").id == "debian-12"
        assert choose_build_base(CATALOG, "glibc", "2.39").id == "ubuntu-24.04"
        assert choose_build_base(CATALOG, "glibc", "2.42").id == "debian-13"
        with pytest.raises(PlanError, match="older than every builder base"):
            choose_build_base(CATALOG, "glibc", "2.28")
        with pytest.raises(PlanError, match="probe it again"):
            choose_build_base(CATALOG, "glibc", None)

    def test_windows_cuda_is_split_into_a_clang_cpu_part_and_an_msvc_cuda_backend(self):
        nvidia = changed(Z1_WINDOWS, GPU0_VENDOR="nvidia", GPU0_NAME="NVIDIA GeForce RTX 3090",
                         GPU0_PCI_ID="0x10de:0x2204", GPU0_DISCRETE="ON", GPU0_UNIFIED="OFF", GPU0_COMPUTE_CAP="8.6",
                         GPU0_SOURCE="nvidia-smi", NVIDIA_DRIVER_CUDA_MAX="13.0", SUPPORTS_CUDA="ON")
        p = plan_build(nvidia, purpose="host-runtime", placement="native", catalog=CATALOG,
                       toolchain=ToolchainInfo.from_profile(nvidia, msvc="19.44.35207"))
        assert p.backend == "cuda" and [s.name for s in p.steps] == ["cpu", "cuda"]
        cpu, cuda = (dict(s.options) for s in p.steps)
        assert cpu["CMAKE_C_COMPILER"] == "clang" and cpu["GGML_CUDA"] == "OFF"
        assert cpu["GGML_BACKEND_DL"] == "ON" and cpu["GGML_NATIVE"] == "OFF", \
            "GGML_NATIVE is not compatible with GGML_BACKEND_DL on x86"
        assert cuda["CMAKE_C_COMPILER"] == "cl" and cuda["GGML_CPU"] == "OFF" and cuda["GGML_CUDA"] == "ON"
        assert cuda["CMAKE_CUDA_ARCHITECTURES"] == "86-real" and p.steps[1].targets == ("ggml-cuda",)
        assert p.toolchain["msvc"] == "19.44.35207"
        assert "GGML_CUDA_CCCL_VERSION" not in cuda, "upstream fetches CCCL here; the recipe never does"

    def test_metal_on_apple_silicon(self):
        mac = parse_profile("\n".join([
            "PROBE_SCHEMA=2", "PROBE_TOOL=python", "PROBE_TOOL_VERSION=3.0.0", "GENERATED_AT=2026-10-11T08:00:00Z",
            "OS=macos", "OS_ID=macos", "OS_FAMILY=macos", "KERNEL=24.6.0", "ARCH=arm64", "LIBC=libsystem",
            "CPU_VENDOR=apple", "CPU_CORES_LOGICAL=8", "RAM_TOTAL_MB=16384", "GPU_COUNT=1", "GPU0_VENDOR=apple",
            "GPU0_DISCRETE=OFF", "GPU0_UNIFIED=ON", "METAL=ON", "TOOL_CC=appleclang-17.0.0", "TOOL_CMAKE=4.1.1",
        ] + [f"{k}=OFF" for k in load_schema().bool_keys if k not in ("METAL",)]) + "\n")
        p = plan_build(mac, purpose="host-runtime", placement="native", catalog=CATALOG,
                       toolchain=ToolchainInfo.from_profile(mac))
        o = p.options()
        assert p.backend == "metal" and o["GGML_METAL"] == "ON" and o["GGML_METAL_EMBED_LIBRARY"] == "ON"
        assert o["GGML_OPENMP"] == "OFF" and p.steps[0].compiler == "appleclang"
        assert any("untested" in n for n in p.notes)

    def test_rocm_only_when_asked_for_on_linux_with_a_gfx_target(self):
        tc = ToolchainInfo.from_profile(Z1_LINUX, rocm="6.4.3")
        assert choose_backend(Z1_LINUX, purpose="host-runtime", placement="native", catalog=CATALOG,
                              toolchain=tc)[0] == "vulkan"
        p = plan("z1_extreme_linux_vulkan", backend="rocm", toolchain=tc)
        assert p.options()["GGML_HIP"] == "ON" and p.options()["GPU_TARGETS"] == "gfx1103"
        assert p.toolchain["rocm"] == "6.4.3" and p.verify["devices"] == ["ROCm0"]
        with pytest.raises(PlanError, match="6.1 or newer"):
            plan("z1_extreme_linux_vulkan", backend="rocm", toolchain=ToolchainInfo.from_profile(Z1_LINUX,
                                                                                                rocm="5.7.1"))
        with pytest.raises(PlanError, match="Linux only"):
            plan("z1_extreme_windows_vulkan_clang", backend="rocm")
        with pytest.raises(PlanError, match="GFX_TARGET"):
            plan("z1_extreme_linux_vulkan", backend="rocm", profile=changed(Z1_LINUX, drop=("GPU0_GFX_TARGET",)))
        with pytest.raises(PlanError, match="ROCm installation"):
            plan("z1_extreme_linux_vulkan", backend="rocm", toolchain=tc, placement="container")

    def test_prebuilt_nvidia_image_is_an_explicit_opt_in(self):
        tc = ToolchainInfo.from_profile(TWO_NVIDIA, cuda_image="cuda-13.0.2-devel-ubuntu24.04")
        p = plan_build(TWO_NVIDIA, purpose="host-runtime", placement="container", backend="cuda", catalog=CATALOG,
                       toolchain=tc)
        assert p.builder["prebuilt"] is True and p.builder["image"].startswith("nvidia/cuda:13.0.2-devel")
        assert p.toolchain["cuda"] == "13.0.2" and any("D6" in n for n in p.notes)
        default = plan_build(TWO_NVIDIA, purpose="host-runtime", placement="container", backend="cuda",
                             catalog=CATALOG, toolchain=ToolchainInfo.from_profile(TWO_NVIDIA))
        assert "prebuilt" not in default.builder and default.builder["base"] == "ubuntu-24.04", \
            "never chosen automatically"
        with pytest.raises(PlanError, match="container builds"):
            plan("rtx2080ti_rtx3090_cuda", toolchain=tc)
        assert all(img.opt_in is True for img in CATALOG.cuda.prebuilt_images)


# --- build ID ----------------------------------------------------------------------------------------------------

VOLATILE = dict(RAM_AVAILABLE_MB="512", DISK_FREE_MB="1024", GENERATED_AT="2027-01-01T00:00:00Z",
                PROBE_WARNINGS="not_determined:RAM_TYPE", KERNEL="6.1.115-vendor-rk35xx", PROBE_TOOL="python",
                PROBE_TOOL_VERSION="3.0.1", OS_PRETTY_NAME="DietPi v9 (Debian 12)", SWAP_TOTAL_MB="4095",
                RAM_TOTAL_MB="3900", BOARD_MODEL="Some Other Board Name")


class TestBuildId:

    def test_volatile_and_recorded_values_do_not_change_it(self):
        before = plan("rk3566_dietpi_cross")
        after = plan("rk3566_dietpi_cross", profile=changed(RK3566, **VOLATILE))
        assert after.build_id == before.build_id
        # A newer glibc on the board that keeps the same builder base keeps the build (and its check uses the new one).
        newer = plan("rk3566_dietpi_cross", profile=changed(RK3566, LIBC_VERSION="2.37"))
        assert newer.build_id == before.build_id and newer.verify["glibc_max"] == "2.37"

    def test_volatile_gpu_values_do_not_change_a_cuda_build(self):
        tc = ToolchainInfo.from_profile(TWO_NVIDIA)
        before = plan("rtx2080ti_rtx3090_cuda", toolchain=tc)
        after = plan("rtx2080ti_rtx3090_cuda", toolchain=tc, profile=changed(
            TWO_NVIDIA, GPU0_VRAM_FREE_MB="100", GPU1_VRAM_FREE_MB="200", RAM_AVAILABLE_MB="1000",
            GPU0_DRIVER_VERSION="580.99.01", GPU1_DRIVER_VERSION="580.99.01", DISK_FREE_MB="5"))
        assert after.build_id == before.build_id

    @pytest.mark.parametrize("change", [
        dict(profile=changed(RK3566, SUPPORTS_DOTPROD="OFF")),
        dict(profile=changed(RK3566, SUPPORTS_I8MM="ON")),
        dict(profile=changed(RK3566, LIBC_VERSION="2.39")),            # another builder base
        dict(profile=changed(RK3566, LIBC="musl", LIBC_VERSION="1.2.5")),
        dict(backend="vulkan"),
        dict(toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.31.6")),
        dict(toolchain=ToolchainInfo(host_os="linux", host_arch="arm64", cmake="3.25.1")),  # no cross compiler
    ])
    def test_option_and_toolchain_changes_change_it(self, change):
        assert plan("rk3566_dietpi_cross", **change).build_id != plan("rk3566_dietpi_cross").build_id

    @pytest.mark.parametrize("change", [
        dict(profile=changed(TWO_NVIDIA, GPU0_COMPUTE_CAP="8.9")),
        dict(profile=changed(TWO_NVIDIA, CPU_MODEL="AMD Ryzen 9 7950X 16-Core Processor")),  # GGML_NATIVE
        dict(toolchain=ToolchainInfo.from_profile(TWO_NVIDIA, compiler="gcc-14.2.0")),
        dict(toolchain=ToolchainInfo.from_profile(TWO_NVIDIA, native_expansion="-march=znver3")),
        dict(profile=changed(TWO_NVIDIA, NVIDIA_DRIVER_CUDA_MAX="13.4")),  # newer toolkit
    ])
    def test_native_cuda_build_id_follows_cpu_gpu_and_toolchain(self, change):
        assert plan("rtx2080ti_rtx3090_cuda", **change).build_id != plan("rtx2080ti_rtx3090_cuda").build_id

    def test_another_pin_changes_every_build_id(self):
        data = CATALOG.model_dump(mode="json")
        data["llama_cpp"]["source"]["commit"] = "0" * 40
        other = RuntimeCatalog.model_validate(data)
        assert plan("rk3566_dietpi_cross", catalog=other).build_id != plan("rk3566_dietpi_cross").build_id

    def test_ccache_does_not_change_it(self):
        with_cache = ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1", ccache=True)
        p = plan("rk3566_dietpi_cross", toolchain=with_cache)
        assert p.options()["GGML_CCACHE"] == "ON" and p.build_id == plan("rk3566_dietpi_cross").build_id


# --- untrusted values, unsupported combinations -----------------------------------------------------------------

class TestRefusals:

    @pytest.mark.parametrize("name", sorted(NEVER_SET))
    def test_a_never_set_option_is_refused(self, name):
        options = dict(plan("rk3566_dietpi_cross").options())
        options[name] = "ON"
        with pytest.raises(PlanError, match=name):
            check_download_options(options)

    @pytest.mark.parametrize("name", sorted(DOWNLOAD_OPTIONS_OFF) + ["FETCHCONTENT_FULLY_DISCONNECTED",
                                                                     "LLAMA_SUBPROCESS"])
    def test_a_download_option_must_keep_its_value(self, name):
        options = dict(plan("rk3566_dietpi_cross").options())
        options[name] = "OFF" if options[name] == "ON" else "ON"
        with pytest.raises(PlanError, match=name):
            check_download_options(options)

    @pytest.mark.parametrize("key, value", [
        ("GPU0_COMPUTE_CAP", "8.6;rm -rf /"),
        ("GPU0_COMPUTE_CAP", "86"),
        ("LIBC_VERSION", "2.36 $(id)"),
    ])
    def test_invalid_probe_values_never_reach_an_option(self, key, value):
        profile = raw(TWO_NVIDIA if key.startswith("GPU") else RK3566, **{key: value})
        with pytest.raises(PlanError):
            if key.startswith("GPU"):
                plan("rtx2080ti_rtx3090_cuda", profile=profile, backend="cuda")
            else:
                plan("rk3566_dietpi_cross", profile=profile)

    def test_invalid_gfx_target_is_refused(self):
        with pytest.raises(PlanError, match="gfx target"):
            plan("z1_extreme_linux_vulkan", backend="rocm", profile=raw(Z1_LINUX, GPU0_GFX_TARGET="gfx1103 -x"),
                 toolchain=ToolchainInfo.from_profile(Z1_LINUX, rocm="6.4.3"))

    def test_free_text_never_reaches_options_or_toolchain_files(self):
        marker = "Evil;$(touch x)"
        profile = raw(RK3566, CPU_MODEL=marker, BOARD_MODEL=marker, GPU0_NAME=marker, OS_PRETTY_NAME=marker)
        p = plan("rk3566_dietpi_cross", profile=profile)
        for step in p.steps:
            assert all(marker not in v for _k, v in step.options) and marker not in (step.toolchain_file or "")
            assert all("$(" not in arg for arg in step.cmake_args())

    @pytest.mark.parametrize("kwargs, message", [
        (dict(placement="cross", toolchain=LINUX_BUILD_HOST, profile=Z1_WINDOWS, backend="cpu"),
         "Windows binaries are built on Windows"),
        (dict(placement="native", toolchain=LINUX_BUILD_HOST), "runs on the machine itself"),
        (dict(placement="container", toolchain=LINUX_BUILD_HOST), "cross build"),
        (dict(purpose="host-runtime", placement="cross"), "purpose device-runtime"),
        (dict(profile=changed(RK3566, ARCH="armv7", KERNEL_ARCH="armv7")), "architecture 'armv7'"),
        (dict(profile=changed(RK3566, LIBC_VERSION="2.28")), "older than every builder base"),
        (dict(profile=changed(RK3566, drop=("LIBC_VERSION",))), "LIBC_VERSION"),
        (dict(purpose="nope"), "purpose must be"),
        (dict(backend="opencl"), "backend must be"),
        (dict(backend="metal"), "macOS only"),
        (dict(backend="cuda"), "compute capability"),
    ])
    def test_unsupported_combinations_say_why(self, kwargs, message):
        with pytest.raises(PlanError, match=message):
            plan("rk3566_dietpi_cross", **kwargs)

    def test_every_nvidia_gpu_needs_its_compute_capability(self):
        # A -real build has no kernel for a GPU it was not built for: with GPU1's capability unknown (older driver,
        # converted profile) the build would cover only the RTX 3090 while claiming CUDA0 and CUDA1.
        partial = changed(TWO_NVIDIA, drop=("GPU1_COMPUTE_CAP",))
        tc = ToolchainInfo.from_profile(partial)
        with pytest.raises(PlanError, match="GPU1_COMPUTE_CAP"):
            plan_build(partial, purpose="host-runtime", placement="native", backend="cuda", catalog=CATALOG,
                       toolchain=tc)
        backend, reason = choose_backend(partial, purpose="host-runtime", placement="native", catalog=CATALOG,
                                         toolchain=tc)
        assert backend == "vulkan" and "GPU1_COMPUTE_CAP" in reason
        assert choose_cuda_toolkit(partial, CATALOG, tc)[0] is None

    def test_cuda_is_not_offered_on_musl(self):
        # NVIDIA's toolkit (redist archives and images) is built for glibc; the plan follows the C library (decision 8).
        alpine = changed(TWO_NVIDIA, LIBC="musl", LIBC_VERSION="1.2.5", OS_ID="alpine", OS_FAMILY="alpine",
                         drop=("OS_VARIANT_ID", "OS_VERSION_CODENAME"))
        tc = ToolchainInfo.from_profile(alpine)
        toolkit, why = choose_cuda_toolkit(alpine, CATALOG, tc)
        assert toolkit is None and any("glibc" in w for w in why)
        with pytest.raises(PlanError, match="glibc"):
            plan_build(alpine, purpose="host-runtime", placement="native", backend="cuda", catalog=CATALOG,
                       toolchain=tc)
        assert choose_backend(alpine, purpose="host-runtime", placement="native", catalog=CATALOG,
                              toolchain=tc)[0] == "vulkan"

    def test_rocm_version_from_the_profile_is_validated(self):
        profile = raw(Z1_LINUX, ROCM_VERSION="6.4.3; rm -rf /")
        with pytest.raises(PlanError, match="ROCM_VERSION"):
            plan("z1_extreme_linux_vulkan", backend="rocm", profile=profile,
                 toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.31.6"))

    def test_windows_builds_need_clang_not_msvc_for_the_cpu_part(self):
        # D9: Clang builds the CPU backend on Windows; a toolchain that only reports MSVC cannot run the plan, and its
        # compiler must not key the build ID of a Clang build.
        tc = ToolchainInfo(host_os="windows", host_arch="x86_64", compiler="msvc-19.44.35207", cmake="3.31.6")
        with pytest.raises(PlanError, match="Clang"):
            plan("z1_extreme_windows_vulkan_clang", toolchain=tc)
        unknown = ToolchainInfo(host_os="windows", host_arch="x86_64", cmake="3.31.6")
        assert plan("z1_extreme_windows_vulkan_clang", toolchain=unknown).steps[0].compiler == "clang"

    def test_host_tools_need_the_target_image(self):
        with pytest.raises(PlanError, match="toolchain image"):
            plan("host_tools_container", toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1"))

    def test_cuda_cross_builds_are_not_offered(self):
        nvidia_board = changed(TWO_NVIDIA, LIBC_VERSION="2.39")
        with pytest.raises(PlanError, match="CUDA cross builds"):
            plan_build(nvidia_board, purpose="device-runtime", placement="cross", backend="cuda", catalog=CATALOG,
                       toolchain=LINUX_BUILD_HOST)
        assert choose_backend(nvidia_board, purpose="device-runtime", placement="cross", catalog=CATALOG,
                              toolchain=LINUX_BUILD_HOST)[0] == "vulkan"

    def test_a_converted_schema1_profile_needs_a_new_probe_for_a_cross_build(self):
        old = probe("schema1_sh_rk3566.txt")
        assert old.needs_reprobe
        with pytest.raises(PlanError, match="probe"):
            plan_build(old, purpose="device-runtime", placement="cross", catalog=CATALOG, toolchain=LINUX_BUILD_HOST)

    def test_toolchain_values_are_validated(self):
        for bad in (dict(compiler="gcc 13; rm"), dict(cmake="3.28 x"), dict(host_os="freebsd"),
                    dict(base_image="debian:12 --privileged"), dict(native_expansion="-march=native\n-x")):
            with pytest.raises(PlanError):
                ToolchainInfo(**{"host_os": "linux", "host_arch": "x86_64", **bad})

    def test_catalog_without_a_source_has_nothing_to_build(self):
        data = CATALOG.model_dump(mode="json")
        data["llama_cpp"].pop("source")
        with pytest.raises(PlanError, match="no llama_cpp.source"):
            plan("rk3566_dietpi_cross", catalog=RuntimeCatalog.model_validate(data))


# --- the pin and the catalog -------------------------------------------------------------------------------------

class TestPin:

    def test_the_catalog_pins_a_commit_with_its_label(self):
        assert PIN.commit == "d81235049384534c167caea52b85a694f6103d14"
        assert PIN.tag == CATALOG.llama_cpp.build == "b11429" and PIN.build_number == 11429
        assert PIN.repo == "https://github.com/ggml-org/llama.cpp.git"

    def test_every_pin_place_agrees_with_the_pin(self):
        places = pins.pin_places(PIN)
        assert not pins.mismatches(places), [(p.path, p.what, p.found) for p in pins.mismatches(places)]
        kinds = {p.kind for p in places}
        assert {"derived", "reference"} <= kinds
        paths = {p.path for p in places}
        assert {"targets/Rockchip/dockerfile", "targets/Rockchip/target.yml", "targets/_template/target.yml",
                "targets/_template/Dockerfile", "targets/_template/Dockerfile.gpu",
                "configs/project_sources.yml", "configs/detailed_project_sources.yml"} <= paths
        # Every Dockerfile that builds llama.cpp fetches the pinned commit, not only the tag.
        commits = {p.path for p in places if p.what == "ARG LLAMA_CPP_COMMIT"}
        assert commits == {"targets/Rockchip/dockerfile", "targets/_template/Dockerfile",
                           "targets/_template/Dockerfile.gpu"}

    def test_legacy_places_are_reported_not_failed(self, tmp_path):
        # The shipped targets' stage modules name no llama.cpp version of their own since build.sh runs them
        # (DECISIONS U9): the source stage reports the image's pin. A target that still has the source_module.sh of
        # version 1.x (an older community target) is reported, never failed.
        assert [p for p in pins.pin_places(PIN) if p.kind == "legacy"] == []
        root = tmp_path / "repo"
        (root / "configs").mkdir(parents=True)
        (root / "configs" / "llama_runtime.yml").write_text(
            (ROOT / "configs" / "llama_runtime.yml").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "targets" / "Old" / "modules").mkdir(parents=True)
        (root / "targets" / "Old" / "modules" / "source_module.sh").write_text(
            '#!/bin/bash\nreadonly LLAMA_CPP_COMMIT="${LLAMA_CPP_COMMIT:-v0.6.0}"\n')
        legacy = [p for p in pins.pin_places(PIN, root) if p.kind == "legacy"]
        assert [(p.path, p.found) for p in legacy] == [("targets/Old/modules/source_module.sh", "v0.6.0")]
        assert all(p not in pins.mismatches(legacy) for p in legacy)

    def test_a_mismatch_is_found(self, tmp_path):
        root = tmp_path / "repo"
        (root / "configs").mkdir(parents=True)
        (root / "targets" / "Board").mkdir(parents=True)
        (root / "configs" / "llama_runtime.yml").write_text(
            (ROOT / "configs" / "llama_runtime.yml").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "targets" / "Board" / "Dockerfile").write_text("FROM debian\nARG LLAMA_CPP_TAG=v0.6.0\n")
        (root / "targets" / "Board" / "target.yml").write_text("docker:\n  build_args:\n    LLAMA_CPP_TAG: b4009\n")
        (root / "targets" / "Other").mkdir()
        (root / "targets" / "Other" / "Dockerfile").write_text(
            "FROM debian\nRUN git clone -b master https://github.com/ggml-org/llama.cpp.git /src\n")
        (root / "targets" / "Plain").mkdir()
        (root / "targets" / "Plain" / "Dockerfile").write_text("FROM debian\nENV LLAMA_CPP_PATH=/usr/src/llama.cpp\n")
        # Variants of a target's Dockerfile (the template has Dockerfile.gpu) are pin places too.
        (root / "targets" / "Plain" / "Dockerfile.gpu").write_text(
            "FROM debian\nARG LLAMA_CPP_TAG=b4009\nRUN git clone https://github.com/ggml-org/llama.cpp /src\n")
        # A Dockerfile that names llama.cpp needs the commit too: without ARG LLAMA_CPP_COMMIT it builds a moving tag.
        (root / "targets" / "Fixed").mkdir()
        (root / "targets" / "Fixed" / "dockerfile").write_text(
            f"FROM debian\nARG LLAMA_CPP_TAG={PIN.tag}\nARG LLAMA_CPP_COMMIT={'0' * 40}\n")
        (root / "targets" / "Fixed" / "target.yml").write_text(
            f"docker:\n  build_args:\n    LLAMA_CPP_TAG: {PIN.tag}\n    LLAMA_CPP_COMMIT: '{PIN.commit}'\n")
        bad = {(p.path, p.what, p.found) for p in pins.mismatches(pins.pin_places(PIN, root))}
        assert bad == {("targets/Board/Dockerfile", "ARG LLAMA_CPP_TAG", "v0.6.0"),
                       ("targets/Board/Dockerfile", "ARG LLAMA_CPP_COMMIT", None),
                       ("targets/Board/target.yml", "docker.build_args.LLAMA_CPP_TAG", "b4009"),
                       ("targets/Other/Dockerfile", "ARG LLAMA_CPP_TAG", None),
                       ("targets/Other/Dockerfile", "ARG LLAMA_CPP_COMMIT", None),
                       ("targets/Plain/Dockerfile.gpu", "ARG LLAMA_CPP_TAG", "b4009"),
                       ("targets/Plain/Dockerfile.gpu", "ARG LLAMA_CPP_COMMIT", None),
                       ("targets/Fixed/dockerfile", "ARG LLAMA_CPP_COMMIT", "0" * 40)}

    def test_settings_default_is_derived_from_the_pin(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        assert llama_pin.pinned_tag() == PIN.tag
        assert llama_pin.pinned_source() == {"tag": PIN.tag, "commit": PIN.commit}
        cfg = ConfigManager(tmp_path / "configs")
        assert cfg.config_schemas["llama_cpp_version"].default_value == PIN.tag
        assert cfg.get("llama_cpp_version") == PIN.tag

    def test_the_pin_bump_helper_checks_the_places_offline(self, capsys):
        import importlib.util
        spec = importlib.util.spec_from_file_location("llama_cpp_pin_script", ROOT / "scripts" / "llama_cpp_pin.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.main(["check", "--local"]) == 0
        out = capsys.readouterr().out
        assert f"Pin: {PIN.tag} = {PIN.commit}" in out and "targets/Rockchip/dockerfile" in out
        # No shipped place is legacy any more: the stage modules read the image's pin (DECISIONS U9).
        assert "MISMATCH" not in out and "legacy" not in out

    def test_image_build_without_a_pin_stops(self, tmp_path, monkeypatch):
        from orchestrator.Core import builder as builder_module
        from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
        from orchestrator.Core.config_manager import ConfigManager
        monkeypatch.setattr(builder_module.docker, "from_env",
                            lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
        monkeypatch.setattr(builder_module, "pinned_source", lambda: None)
        engine = BuildEngine(ConfigManager(tmp_path / "configs"))
        seen = {}
        monkeypatch.setattr(engine, "_build_with_buildkit", lambda *a, **k: seen.setdefault("built", True))
        monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
        dockerfile = tmp_path / "ctx" / "Dockerfile"
        dockerfile.parent.mkdir()
        dockerfile.write_text("FROM scratch\n")
        config = type("C", (), {"target_arch": "rockchip", "build_id": "b1", "build_args": {}})()
        with pytest.raises(Exception, match="No llama.cpp pin"):
            engine._build_docker_image(config, BuildProgress("b1", BuildStatus.QUEUED, ""), dockerfile)
        assert "built" not in seen

    def test_the_setting_cannot_move_target_images_off_the_pin(self, tmp_path, monkeypatch):
        """llama_cpp_version names a tag; target images build the pinned commit whatever it says (a tag can move,
        and only the pin has a commit to check)."""
        from orchestrator.Core import builder as builder_module
        from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
        from orchestrator.Core.config_manager import ConfigManager
        monkeypatch.setattr(builder_module.docker, "from_env",
                            lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
        engine = BuildEngine(ConfigManager(tmp_path / "configs"))
        monkeypatch.setattr(engine, "_get_conf",
                            lambda key, default=None: "b4009" if key == "llama_cpp_version" else default)
        seen = {}
        monkeypatch.setattr(engine, "_build_with_buildkit", lambda tag, path, args, *a, **k: seen.update(args))
        monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
        monkeypatch.setattr(engine, "docker_client", type("D", (), {"images": type("I", (), {
            "get": staticmethod(lambda tag: tag)})})())
        dockerfile = tmp_path / "ctx" / "Dockerfile"
        dockerfile.parent.mkdir()
        dockerfile.write_text("FROM scratch\n")
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")
        config = type("C", (), {"target_arch": "rockchip", "build_id": "b1", "build_args": {}})()
        engine._build_docker_image(config, progress, dockerfile)
        assert seen["LLAMA_CPP_TAG"] == PIN.tag and seen["LLAMA_CPP_COMMIT"] == PIN.commit
        assert any("llama_cpp_version (b4009) is not the llama.cpp pin" in w for w in progress.warnings)

    def test_the_pin_reader_fails_closed(self, tmp_path):
        broken = tmp_path / "llama_runtime.yml"
        broken.write_text("llama_cpp:\n  source:\n    tag: v0.6.0\n    commit: main\n")
        assert llama_pin.pinned_tag(broken) == "" and llama_pin.pinned_source(broken) is None
        assert llama_pin.pinned_tag(tmp_path / "missing.yml") == ""

    def _data(self):
        return yaml.safe_load((ROOT / "configs" / "llama_runtime.yml").read_text(encoding="utf-8"))

    @pytest.mark.parametrize("breakage, message", [
        (lambda d: d["llama_cpp"]["source"].__setitem__("commit", "b11429"), "commit"),
        (lambda d: d["llama_cpp"]["source"].__setitem__("commit", "D81235049384534C167CAEA52B85A694F6103D14"),
         "commit"),
        (lambda d: d["llama_cpp"]["source"].__setitem__("tag", "b11430"), "disagree"),
        (lambda d: d["llama_cpp"]["source"].__setitem__("repo", "http://github.com/ggml-org/llama.cpp.git"), "repo"),
        (lambda d: d["llama_cpp"]["source"].__setitem__("repo", "https://user:pw@example.com/llama.cpp.git"),
         "repo"),
        (lambda d: d["llama_cpp"].__setitem__("build", "b11430"), "same label"),
        (lambda d: d["cuda"]["toolkits"][0]["manifest"].__setitem__("name", "redistrib_13.0.2.json"), "manifest"),
        (lambda d: d["cuda"]["toolkits"][0]["manifest"].__setitem__("sha256", "x" * 64), "sha256"),
        (lambda d: d["cuda"]["toolkits"][0]["compute_caps"].__setitem__("min", "13.0"), "empty"),
        (lambda d: d["cuda"]["prebuilt_images"][0].__setitem__("opt_in", False), "opt_in"),
        (lambda d: d["cuda"]["prebuilt_images"][0].__setitem__("image", "nvidia/cuda:13.4.2-devel-ubuntu24.04"),
         "image"),
        (lambda d: d["build_bases"][0].__setitem__("image", "debian:11-slim"), "image"),
        (lambda d: d["build_bases"].append(dict(d["build_bases"][0])), "unique"),
    ])
    def test_schema_refuses_unsafe_or_ambiguous_entries(self, breakage, message):
        data = self._data()
        breakage(data)
        with pytest.raises(ValueError, match=message):
            RuntimeCatalog.model_validate(data)

    def test_cuda_toolkits_are_pinned_through_their_manifest(self):
        assert CATALOG.cuda.redist_base_url == "https://developer.download.nvidia.com/compute/cuda/redist/"
        for toolkit in CATALOG.cuda.toolkits:
            assert toolkit.manifest.name == f"redistrib_{toolkit.version}.json"
            assert len(toolkit.manifest.sha256) == 64 and toolkit.manifest.size > 0
            assert {"cuda_nvcc", "cuda_cudart", "libcublas"} <= set(toolkit.components)
        for image in CATALOG.cuda.prebuilt_images:
            assert "@sha256:" in image.image and image.opt_in is True
        assert all("@sha256:" in b.image for b in CATALOG.build_bases)
