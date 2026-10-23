"""The config and target stages of the maintainer's four-module design (HOW2_ORIGINAL; DECISIONS round 16, U9
addendum), run in the simulated Rockchip image of tests/test_build_stages.py with stub toolchains
(tests/board_toolchain.py).

config_module.sh: "Professional Validation - compiler tests, syntax checks": the planned compilers build and link a
small C and C++ program with the planned flags, CMake reads the generated toolchain file, and build_config.sh is a
valid shell script with every name the later stages read. The probe's CPU_CORES_LOGICAL caps the build jobs as
CPU_CORES of version 1.x did, and a 32-bit (armv7) profile is refused with the plain reason.
"""

import os
import platform
import shutil
import subprocess

import pytest

from conftest import posix_shell
from test_build_stages import PROBE_FIXTURES, by_hand, image, needs_bash, plan_of, stage_lines  # noqa: F401

STAGES = "build-cache/work/stages/build"


def tools_log(image):
    """The calls of the stub compilers, cmake and make (STUB_TOOLS_LOG), with the test folder as <root>."""
    log = image.root / "tools.log"
    return [line.replace(str(image.root), "<root>") for line in log.read_text().splitlines()] if log.exists() else []


def config_with(image, profile_text, **values):
    profile = image.root / "target_hardware_config.txt"
    profile.write_text(profile_text)
    return by_hand(image, "config", "--hardware-config", profile.name, MODEL_SOURCE=None,
                   STUB_TOOLS_LOG=image.root / "tools.log", **values)


RK3566 = (PROBE_FIXTURES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8")


# --- the compiler test ------------------------------------------------------------------------------------------

@needs_bash
def test_the_planned_compilers_build_and_link_a_c_and_a_cpp_program_with_the_planned_flags(image):
    result = config_with(image, RK3566)

    assert result.returncode == 0, result.stdout + result.stderr
    test_dir = f"<root>/{STAGES}/compiler-test"
    flags = "-O3 -pthread -fPIC -mcpu=cortex-a55 -march=armv8-a"
    assert tools_log(image)[:3] == [
        f"cmake -P <root>/{STAGES}/cross_compile_toolchain.cmake",
        f"aarch64-linux-gnu-gcc {flags} {test_dir}/test.c -o {test_dir}/test-c",
        f"aarch64-linux-gnu-g++ -std=c++17 {flags} {test_dir}/test.cpp -o {test_dir}/test-cxx",
    ]
    assert "Compiler test passed: C and C++ programs for aarch64, built and linked with the flags of the plan" \
        in result.stdout
    assert not (image.root / STAGES / "compiler-test").exists(), "the test programs are removed"
    assert plan_of(image)["BOARD_PACKAGE"] == "1"


@needs_bash
def test_a_compiler_that_cannot_build_with_the_flags_of_the_plan_stops_the_config_stage(image):
    result = config_with(image, RK3566, STUB_FAIL="cc")

    assert result.returncode == 1
    assert ("ERROR: The compiler test failed: aarch64-linux-gnu-gcc cannot build and link a small program with the "
            "flags of the plan (-O3 -pthread -fPIC -mcpu=cortex-a55 -march=armv8-a): cc1: error: unknown value "
            "'cortex-a99' for '-mcpu'") in result.stderr
    assert ("2/4", "config", "failed") in stage_lines(result)
    assert not (image.root / STAGES / "build_config.sh").exists(), "a plan that failed a check is never written"


@needs_bash
def test_compilers_that_build_for_another_system_are_refused(image):
    """The cross toolchain must make programs for the board's processor (the ELF header says which): an x86_64
    program (machine 62) for an aarch64 board is no plan."""
    result = config_with(image, RK3566, STUB_CC_MACHINE="62")

    assert result.returncode == 1
    assert ("ERROR: The compiler test failed: the plan's compilers do not build programs for the aarch64 system of "
            "the plan (ELF machine 62, expected 183).") in result.stderr
    assert not (image.root / STAGES / "build_config.sh").exists()


@needs_bash
@pytest.mark.skipif(platform.machine() != "x86_64", reason="the native x86_64 plan runs its program on an x86_64 host")
def test_the_native_x86_64_plan_runs_its_test_programs(image):
    result = config_with(image, (PROBE_FIXTURES / "schema2_linux_z1_extreme.txt").read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert any(c.startswith("gcc ") and "-march=native" in c for c in tools_log(image))
    assert "Compiler test passed: C and C++ programs for x86_64" in result.stdout


@needs_bash
def test_an_image_without_the_cross_compiler_plans_nothing(image):
    if shutil.which("aarch64-linux-gnu-gcc", path=f"/usr/bin{os.pathsep}/bin"):
        pytest.skip("this host has an aarch64 cross compiler of its own")
    (image.toolchain / "aarch64-linux-gnu-gcc").unlink()

    result = config_with(image, RK3566)

    assert result.returncode == 1
    assert ("ERROR: This image has no aarch64-linux-gnu-gcc, which the plan for the aarch64 system of the hardware "
            "profile needs; nothing was planned.") in result.stderr


# --- the syntax checks --------------------------------------------------------------------------------------------

@needs_bash
def test_cmake_reads_the_toolchain_file_before_the_plan_is_written(image):
    result = config_with(image, RK3566, STUB_FAIL="cmake-P")

    assert result.returncode == 1
    assert "ERROR: The CMake toolchain file is not valid: CMake Error at toolchain.cmake:3:   Parse error." \
        in result.stderr
    assert not (image.root / STAGES / "build_config.sh").exists()


@needs_bash
@pytest.mark.skipif(shutil.which("cmake") is None, reason="needs CMake")
def test_the_generated_toolchain_file_is_valid_for_cmake_itself(image):
    """Without the stub cmake: the real CMake reads the toolchain file the config stage wrote."""
    path = os.pathsep.join([str(image.toolchain), os.path.dirname(shutil.which("cmake")), "/usr/bin", "/bin"])

    result = config_with(image, RK3566, PATH=path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (image.root / STAGES / "cross_compile_toolchain.cmake.log").exists()


def check_plan(image, text, *names):
    """check_plan_file of config_module.sh on a plan file with `text`."""
    plan = image.root / "plan.sh"
    plan.write_text(text)
    script = f'source "{image.app}/modules/config_module.sh"; check_plan_file "$1" "${{@:2}}"'
    return subprocess.run([posix_shell("bash"), "-c", script, "_", str(plan), *names], env=image.env(),
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


@needs_bash
def test_the_build_plan_must_be_a_valid_shell_script_with_every_name_the_stages_read(image):
    assert check_plan(image, "FORMAT=gguf\nBOARD=rk3588\n", "FORMAT", "BOARD").returncode == 0

    broken = check_plan(image, "FORMAT=gguf\nif then\n", "FORMAT")
    assert broken.returncode == 1 and "ERROR: The build plan is not a valid shell script:" in broken.stderr

    missing = check_plan(image, "FORMAT=gguf\n", "FORMAT", "BOARD", "QUANT")
    assert missing.returncode == 1 and "ERROR: The build plan does not set: BOARD QUANT." in missing.stderr

    # Read in a clean shell: a name the calling stage happens to have is no name of the plan
    inherited = check_plan(image, "FORMAT=gguf\n", "FORMAT", "HOME")
    assert inherited.returncode == 1 and "does not set: HOME." in inherited.stderr


# --- the build jobs and the refused systems ----------------------------------------------------------------------

def host_cores() -> int:
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()


def build_jobs(image) -> str:
    plan = image.root / STAGES / "build_config.sh"
    shown = subprocess.run([posix_shell("bash"), "-c", 'source "$1"; printf "%s" "$BUILD_JOBS"', "_", str(plan)],
                           capture_output=True, text=True, timeout=60, check=True)
    return shown.stdout


@needs_bash
@pytest.mark.parametrize("cores, expected", [("CPU_CORES_LOGICAL=1", 1), ("CPU_CORES=1\nCPU_CORES_LOGICAL=8", 1),
                                             ("CPU_CORES_LOGICAL=four", None)])
def test_the_build_jobs_are_capped_at_the_board_s_cores(image, cores, expected):
    """Version 1.x capped the jobs at CPU_CORES of its own files; the probe names the cores CPU_CORES_LOGICAL (a text
    that is no number caps nothing)."""
    result = config_with(image, f"PROBE_SCHEMA=2\nARCH=arm64\nCPU_MODEL=Cortex-A55\nSUPPORTS_NEON=ON\n{cores}\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert build_jobs(image) == str(expected if expected is not None else host_cores())


@needs_bash
@pytest.mark.parametrize("arch", ["armv7", "armv6"])
def test_a_32_bit_arm_profile_is_refused_with_the_plain_reason(image, arch):
    result = config_with(image, f"PROBE_SCHEMA=2\nARCH={arch}\nCPU_MODEL=Cortex-A55\nSUPPORTS_NEON=ON\n")

    assert result.returncode == 1
    assert ("The board runs a 32-bit system: the Rockchip boards have 64-bit CPUs (Cortex-A55, A72, A76), and this "
            "image has only the aarch64 cross compilers. Install the board's 64-bit (arm64) system, probe the board "
            "again (scripts/hardware_probe.sh) and use the new file.") in result.stderr
    assert tools_log(image) == [], "nothing was compiled for it"


@needs_bash
def test_another_system_gets_no_32_bit_advice(image):
    result = config_with(image, "PROBE_SCHEMA=2\nARCH=riscv64\nCPU_MODEL=U74\n")
    assert result.returncode == 1 and "32-bit" not in result.stderr


# --- every quantization method of llama.cpp (target_module.sh of version 1.x: "Q4_0, Q4_K_M, Q8_0, all llama.cpp
# methods") ---------------------------------------------------------------------------------------------------------

# QUANT_OPTIONS of tools/quantize/quantize.cpp at the pinned commit (llama.cpp b11429 = v0.6.0), in its order.
PINNED_QUANT_OPTIONS = (
    "Q1_0 Q2_0 Q4_0 Q4_1 MXFP4_MOE Q5_0 Q5_1 IQ2_XXS IQ2_XS IQ2_S IQ2_M IQ1_S IQ1_M TQ1_0 TQ2_0 Q2_K Q2_K_S IQ3_XXS "
    "IQ3_S IQ3_M Q3_K IQ3_XS Q3_K_S Q3_K_M Q3_K_L IQ4_NL IQ4_XS Q4_K Q4_K_S Q4_K_M Q5_K Q5_K_S Q5_K_M Q6_K Q8_0 F16 "
    "BF16 F32 COPY").split()
ALIASES = {"Q3_K": "Q3_K_M", "Q4_K": "Q4_K_M", "Q5_K": "Q5_K_M"}   # "alias for ..." in QUANT_OPTIONS
# tensor_requires_imatrix of src/llama-quant.cpp at the pin: the types whose main tensors need an importance matrix
PINNED_NEEDS_IMATRIX = {"IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M", "IQ3_XXS", "Q2_K_S"}


def script_list(name: str) -> set:
    import re
    from conftest import REPO_ROOT
    text = (REPO_ROOT / "targets" / "Rockchip" / "modules" / "config_module.sh").read_text(encoding="utf-8")
    return set(re.search(rf'{name}=" (.*?) "', text, re.S).group(1).replace("\\\n", " ").upper().split()) - {"AUTO"}


def test_every_quantization_method_of_the_pinned_llama_quantize_is_listed():
    from orchestrator.Core.orchestrator import LLAMA_CPP_NEEDS_IMATRIX
    from test_foundation import ROCKCHIP_YML
    import yaml
    methods = set(PINNED_QUANT_OPTIONS) - {"COPY"} - set(ALIASES)
    assert script_list("GGUF_QUANT_TYPES") | script_list("GGUF_CONVERT_TYPES") == methods
    gguf = yaml.safe_load(ROCKCHIP_YML.read_text())["capabilities"]["formats"]["gguf"]
    assert set(gguf["quantizations"]) == methods
    assert {k: gguf["aliases"][k] for k in ALIASES} == ALIASES
    assert script_list("GGUF_NEEDS_IMATRIX") == set(gguf["needs_imatrix"]) == PINNED_NEEDS_IMATRIX
    assert LLAMA_CPP_NEEDS_IMATRIX == PINNED_NEEDS_IMATRIX
    assert not script_list("GGUF_WANTS_IMATRIX") & PINNED_NEEDS_IMATRIX


@needs_bash
@pytest.mark.parametrize("quant, kind", [("Q1_0", "Q1_0"), ("Q2_0", "Q2_0"), ("q4_k", "Q4_K_M"), ("Q3_K", "Q3_K_M"),
                                         ("Q5_K", "Q5_K_M")])
def test_the_new_methods_and_the_short_names_are_accepted(image, quant, kind):
    result = image.run(QUANTIZATION=quant)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls()[-1].endswith(f"/build-cache/output/model-{kind}.gguf {kind}")


@needs_bash
@pytest.mark.parametrize("quant", ["IQ2_M", "IQ3_XXS", "Q2_K_S"])
def test_a_method_llama_quantize_refuses_without_an_importance_matrix_is_refused_before_converting(image, quant):
    """Before, these built until llama-quantize stopped with "this quantization requires an imatrix!"."""
    result = image.run(QUANTIZATION=quant)

    assert result.returncode == 1
    assert f"ERROR: {quant} needs an importance matrix; enable IMatrix calibration for this build." in result.stderr
    assert image.calls() == [], "nothing was converted"


def test_the_capabilities_ask_for_the_importance_matrix_of_these_methods():
    from orchestrator.schemas.targets import TargetCapabilities
    from test_foundation import ROCKCHIP_YML
    import yaml
    caps = TargetCapabilities.model_validate(yaml.safe_load(ROCKCHIP_YML.read_text())["capabilities"])
    for quant in ("IQ2_M", "IQ3_XXS", "Q2_K_S"):
        assert any("needs an importance matrix" in p for p in caps.check_request("gguf", "rk3588", quant)), quant
    assert caps.check_request("gguf", "rk3588", "Q4_K") == [], "the short name of Q4_K_M"


# --- the same validation in the template (targets/_template, the module generator's source) ----------------------

def _validation(path):
    text = path.read_text(encoding="utf-8")
    start = text.index("# VALIDATION (version 1.x")
    return text[start:text.index("# END OF VALIDATION", start)]


def test_the_template_validates_with_the_same_functions_as_the_rockchip_target():
    from conftest import REPO_ROOT
    rockchip = _validation(REPO_ROOT / "targets" / "Rockchip" / "modules" / "config_module.sh")
    assert _validation(REPO_ROOT / "targets" / "_template" / "modules" / "config_module.sh") == rockchip
    for name in ("elf_machine", "compiler_test", "check_toolchain_file", "check_plan_file"):
        assert f"\n{name}() {{" in rockchip


def generated_config(tmp_path, profile_text, **env_values):
    """config_module.sh --hardware-config of a module the generator made from the template (aarch64)."""
    from board_toolchain import install_toolchain
    from test_target_template import generate, stub_llama
    target = generate(tmp_path, cpu_flags="-mcpu=cortex-a55")
    llama = stub_llama(tmp_path)
    toolchain, buildtools = install_toolchain(tmp_path)
    profile = tmp_path / "target_hardware_config.txt"
    profile.write_text(profile_text)
    env = {"PATH": os.pathsep.join([str(toolchain), str(buildtools), "/usr/bin", "/bin"]), "HOME": str(tmp_path),
           "BUILD_CACHE_DIR": str(tmp_path / "work"), "LLAMA_CPP_PATH": str(llama), "TARGET_FORMAT": "gguf",
           "JOB_TYPE": "build", "STUB_TOOLS_LOG": str(tmp_path / "tools.log"), **env_values}
    result = subprocess.run([posix_shell("bash"), str(target / "modules" / "config_module.sh"), "--hardware-config",
                             str(profile)], env=env, capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL)
    return result, tmp_path / "work" / "stages" / "build", toolchain


@needs_bash
def test_a_generated_module_tests_the_image_s_compilers_with_the_module_s_flags(tmp_path):
    """CC and CXX of the image (here cc and c++, writing aarch64 programs) build the test programs with the module's
    CPU flags; the toolchain file names them."""
    result, stages, toolchain = generated_config(tmp_path, RK3566, STUB_CC_MACHINE="183")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "tools.log").read_text().splitlines()
    assert any(c.startswith("cc -mcpu=cortex-a55 ") and c.endswith("/test-c") for c in calls), calls
    text = (stages / "cross_compile_toolchain.cmake").read_text()
    assert f"SET(CMAKE_C_COMPILER   {toolchain}/cc)" in text and f"SET(CMAKE_CXX_COMPILER {toolchain}/c++)" in text
    plan = (stages / "build_config.sh").read_text()
    assert "BOARD_PACKAGE=1" in plan and "PROFILE_SOURCE=profile" in plan and "BUILD_JOBS=" in plan


@needs_bash
def test_a_generated_module_whose_compilers_build_for_the_host_is_refused(tmp_path):
    """Without CC/CXX of a cross compiler the image's cc builds programs for the build host, not for the module's
    aarch64 board: the plan is refused instead of a package of programs the board cannot run."""
    result, stages, _ = generated_config(tmp_path, RK3566, STUB_CC_MACHINE="62")

    assert result.returncode == 1
    assert ("ERROR: The compiler test failed: the plan's compilers do not build programs for the aarch64 system of the "
            "plan (ELF machine 62, expected 183).") in result.stderr
    assert not (stages / "build_config.sh").exists()


@needs_bash
def test_a_generated_module_refuses_a_profile_of_another_system(tmp_path):
    result, stages, _ = generated_config(tmp_path, "PROBE_SCHEMA=2\nARCH=x86_64\nCPU_MODEL=Ryzen\n",
                                         STUB_CC_MACHINE="183")

    assert result.returncode == 1
    assert ("ERROR: The hardware profile is of a x86_64 system; this module builds for aarch64, so nothing was planned "
            "for it.") in result.stderr
