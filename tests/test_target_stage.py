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
from collections import namedtuple
from pathlib import Path

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


@needs_bash
@pytest.mark.parametrize("arch", ["armv7l", "armv8l", "armhf"])
def test_a_32_bit_arm_profile_of_version_1x_gets_the_plain_reason_as_well(image, arch):
    """The probe of version 1.x wrote `ARCH=$(uname -m)`: armv7l, or armv8l for a 32-bit system on a 64-bit CPU (the
    probe of today writes armv7 for both, scripts/hardware_probe.sh arch_alias)."""
    result = config_with(image, f"ARCH={arch}\nCPU_MODEL=Cortex-A55\nCPU_CORES_LOGICAL=4\n")

    assert result.returncode == 1
    assert "The board runs a 32-bit system:" in result.stderr
    assert tools_log(image) == [], "nothing was compiled for it"


# --- every quantization method of llama.cpp (target_module.sh of version 1.x: "Q4_0, Q4_K_M, Q8_0, all llama.cpp
# methods") ---------------------------------------------------------------------------------------------------------

# QUANT_OPTIONS of tools/quantize/quantize.cpp at the pinned commit (llama.cpp b11429 = v0.6.0), in its order.
PINNED_QUANT_OPTIONS = (
    "Q1_0 Q2_0 Q4_0 Q4_1 MXFP4_MOE Q5_0 Q5_1 IQ2_XXS IQ2_XS IQ2_S IQ2_M IQ1_S IQ1_M TQ1_0 TQ2_0 Q2_K Q2_K_S IQ3_XXS "
    "IQ3_S IQ3_M Q3_K IQ3_XS Q3_K_S Q3_K_M Q3_K_L IQ4_NL IQ4_XS Q4_K Q4_K_S Q4_K_M Q5_K Q5_K_S Q5_K_M Q6_K Q8_0 F16 "
    "BF16 F32 COPY").split()
ALIASES = {"Q3_K": "Q3_K_M", "Q4_K": "Q4_K_M", "Q5_K": "Q5_K_M"}   # "alias for ..." in QUANT_OPTIONS
# The methods llama-quantize refuses without an importance matrix at the pin: tensor_requires_imatrix of
# src/llama-quant.cpp is true for a type some tensors of the file get (llama_tensor_get_type_impl): IQ1_S, IQ1_M,
# IQ2_XXS, IQ2_XS, IQ2_S and IQ3_XXS as types, IQ2_M is mostly IQ2_S, Q2_K_S has Q2_K, and IQ3_XS makes attn_q, attn_k
# and the middle ffn_gate and ffn_up IQ3_XXS.
PINNED_NEEDS_IMATRIX = {"IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M", "IQ3_XXS", "IQ3_XS", "Q2_K_S"}


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
@pytest.mark.parametrize("quant", ["IQ2_M", "IQ3_XXS", "IQ3_XS", "Q2_K_S"])
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
    for quant in ("IQ2_M", "IQ3_XXS", "IQ3_XS", "Q2_K_S"):
        assert any("needs an importance matrix" in p for p in caps.check_request("gguf", "rk3588", quant)), quant
    assert caps.check_request("gguf", "rk3588", "Q4_K") == [], "the short name of Q4_K_M"


# --- the package for the board (target_module.sh of version 1.x: "Professional Packaging", "Helper Scripts",
# "Complete Manifest", "Automated Testing") ----------------------------------------------------------------------

import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

from model_files import write_gguf  # noqa: E402
from test_build_stages import _script, ran, resumed  # noqa: E402
# The package's functional test is the convert stage's (scripts/model_check.py test): the same llama-completion stub,
# which answers --help and fails as STUB_FAIL says (completion, -hang, -signal, -nostart, -abort, -sigill)
from test_convert_stage import COMPLETION_STUB  # noqa: E402

PAD = {"STUB_PAD": 1 << 20}  # the package's input check of 1.x wants a model of at least 1 MiB
OUT_DIR = "<root>/build-cache/output"


def f16_input(image):
    """The F16 GGUF of the convert module: a valid GGUF header (the convert stage checks a GGUF input), then the text
    the stubs carry on (tests/model_files.py)."""
    return write_gguf(image.root / "granite.fp16.gguf", "gguf f16")


def package_of(image, name="granite_q4_k_m_latest"):
    link = image.output / "packages" / name
    assert link.is_symlink(), sorted(p.name for p in (image.output / "packages").iterdir())
    return link.resolve()


def manifest_of(package):
    return json.loads((package / "MANIFEST.json").read_text())


def target_by_hand(image, *args, **values):
    """target_module.sh --input FILE.gguf ... of version 1.x, with the stub toolchain's padded model."""
    return by_hand(image, "target", "--input", f16_input(image), *args, MODEL_SOURCE=None, MODEL_NAME=None,
                   QUANTIZATION=None, **PAD, **values)


@needs_bash
def test_target_module_by_hand_packages_without_a_hardware_profile_with_the_plain_defaults(image):
    result = target_by_hand(image, "--model-name", "granite")

    assert result.returncode == 0, result.stdout + result.stderr
    package = package_of(image)
    assert sorted(p.name for p in package.iterdir()) == [
        "MANIFEST.json", "README.md", "SHA256SUMS", "cmake_toolchain.cmake", "deploy.sh", "granite-Q4_K_M.gguf",
        "llama-cli", "llama-server", "test_model.sh"], "no hardware profile: none in the package"
    manifest = manifest_of(package)
    hardware = manifest["hardware"]
    assert hardware["source"] == "defaults" and hardware["arch"] == "aarch64"
    assert hardware["profile"] == "none: the plain defaults of the Rockchip target (no hardware profile was given)"
    assert "-march=armv8-a" in hardware["cflags"] and "-mcpu" not in hardware["cflags"]
    assert "-DGGML_NEON=ON" in hardware["cmake_flags"]
    assert "Hardware profile:** none: the plain defaults of the Rockchip target" in (package / "README.md").read_text()
    # The plain defaults were planned by the config stage's functions in the stage folder, with the compiler test
    toolchain = (image.root / STAGES / "package_toolchain.cmake").read_text()
    assert f"SET(CMAKE_C_COMPILER   {image.toolchain}/aarch64-linux-gnu-gcc)" in toolchain
    assert (package / "cmake_toolchain.cmake").read_text() == toolchain
    assert "Compiler test passed: C and C++ programs for aarch64" in result.stdout


@needs_bash
def test_the_manifest_holds_every_build_detail_and_the_checksums_cover_every_file(image):
    profile = image.root / "target_hardware_config.txt"
    profile.write_text(RK3566)
    result = target_by_hand(image, "--model-name", "granite", "--hardware-config", profile.name)

    assert result.returncode == 0, result.stdout + result.stderr
    package = package_of(image)
    manifest = manifest_of(package)
    assert {k: manifest[k] for k in ("name", "model", "quantization", "format", "target", "board")} == {
        "name": package.name, "model": "granite", "quantization": "Q4_K_M", "format": "gguf", "target": "Rockchip",
        "board": "rk3588"}
    assert manifest["build"]["llama_cpp"]["tag"] == "b11429" and manifest["build"]["source_model"] == "granite.fp16.gguf"
    assert manifest["build"]["input_format"] == "gguf" and isinstance(manifest["build"]["jobs"], int)
    assert manifest["hardware"]["source"] == "profile" and manifest["hardware"]["cpu"] == "Cortex-A55"
    assert "-mcpu=cortex-a55" in manifest["hardware"]["cflags"]
    assert manifest["tests"]["functional"] == "skipped: the image has no llama-completion or llama-cli for this machine"
    assert manifest["binaries"]["llama-cli"]["elf_machine"] == 183
    model = package / "granite-Q4_K_M.gguf"
    assert manifest["model_file"] == {"name": model.name, "size_bytes": model.stat().st_size,
                                      "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}
    files = {f["path"] for f in manifest["files"]}
    assert files == {p.name for p in package.iterdir()} - {"MANIFEST.json", "SHA256SUMS"}
    assert "target_hardware_config.txt" in files
    assert str(image.root) not in (package / "MANIFEST.json").read_text(), "no path of the build machine"
    sums = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=package, capture_output=True, text=True)
    assert sums.returncode == 0, sums.stdout + sums.stderr
    assert "MANIFEST.json: OK" in sums.stdout


def short_functional_test(image, seconds=1):
    """The image's target_module.sh with a functional test of `seconds` (120 in the module)."""
    module = image.app / "modules" / "target_module.sh"
    text = module.read_text(encoding="utf-8")
    assert "FUNCTIONAL_TEST_SECONDS=120\n" in text
    module.write_text(text.replace("FUNCTIONAL_TEST_SECONDS=120\n", f"FUNCTIONAL_TEST_SECONDS={seconds}\n"),
                      encoding="utf-8")


@needs_bash
@pytest.mark.parametrize("fail, outcome", [(None, "passed (llama-completion, 8 tokens on the build machine)"),
                                           ("completion-hang", "timed out after 1 s (llama-completion on the build "
                                                               "machine); not a failure")])
def test_the_functional_test_runs_the_model_on_the_build_machine(image, fail, outcome):
    """The package's functional test is the convert stage's (scripts/model_check.py test, one implementation): 8
    tokens of the quantized model, bounded, offline, with the options this llama-completion has."""
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)
    short_functional_test(image)

    result = target_by_hand(image, "--model-name", "granite", STUB_FAIL=fail)

    assert result.returncode == 0, result.stdout + result.stderr
    run = [c for c in image.calls() if c.startswith("llama-completion ")]
    assert run == [f"llama-completion -m {OUT_DIR}/granite-Q4_K_M.gguf -p Hello -n 8 -c 256 -no-cnv --offline "
                   "--no-warmup --temp 0 --simple-io"]
    assert manifest_of(package_of(image))["tests"]["functional"] == outcome
    assert "[TARGET] Functional test" in result.stdout, "the target stage's log line"


@needs_bash
@pytest.mark.parametrize("fail, how", [("completion", "could not run granite-Q4_K_M.gguf (exit 1)"),
                                       ("completion-abort", "crashed running granite-Q4_K_M.gguf (signal 6)")])
def test_a_model_the_functional_test_cannot_run_gets_no_package(image, fail, how):
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)

    result = target_by_hand(image, "--model-name", "granite", STUB_FAIL=fail)

    assert result.returncode == 1
    assert f"ERROR: The functional test failed: llama-completion {how}, so the quantized model does not work." \
        in result.stderr
    assert not (image.output / "packages").exists()


@needs_bash
@pytest.mark.parametrize("fail, outcome", [
    ("completion-sigill", "skipped: llama-completion uses instructions this build machine's CPU does not have "
                          "(signal 4)"),
    ("completion-nostart", "skipped: llama-completion does not start in this image (exit 127)"),
    ("completion-signal", "stopped by signal 9 (llama-completion on the build machine, not enough memory?); not a "
                          "failure"),
])
def test_what_says_nothing_about_the_model_is_a_note_as_in_the_convert_stage(image, fail, outcome):
    """Stage C wave 2 seam (how2-convert and how2-target built a functional test each): the package's test reads a
    run the way the convert stage does. A tool this machine's CPU cannot run, one that does not start in the image
    and a run the out-of-memory killer stopped say nothing about the model, so the package is made with the note (the
    convert stage's test of the same machine and tool notes them too, so a build no longer passed its convert stage
    and then failed its package for the same reason)."""
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)

    result = target_by_hand(image, "--model-name", "granite", STUB_FAIL=fail)

    assert result.returncode == 0, result.stdout + result.stderr
    assert manifest_of(package_of(image))["tests"]["functional"] == outcome


@needs_bash
def test_an_embedding_model_is_packaged_without_a_text_run(image):
    """An embedding GGUF generates no text: the convert stage skips its run, and so does the package's test now (a
    text run of it fails in llama-completion, so the package was refused)."""
    from model_files import gguf_bytes
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)
    # a quantizer that keeps the model's header (the stub of tests/test_build_stages.py writes a llama header)
    _script(image.llama / "build" / "bin" / "llama-quantize", "import shutil, sys\nshutil.copyfile(sys.argv[-3], "
            "sys.argv[-2])\n")
    model = image.root / "embed.f16.gguf"
    model.write_bytes(gguf_bytes(arch="bert") + b"." * (1 << 20))

    result = by_hand(image, "target", "--input", model, "--model-name", "embed", MODEL_SOURCE=None, MODEL_NAME=None,
                     QUANTIZATION=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not [c for c in image.calls() if c.startswith("llama-completion ")], "no text run of an embedding model"
    package = package_of(image, "embed_q4_k_m_latest")
    assert manifest_of(package)["tests"]["functional"] == "skipped: an embedding model does not generate text"


@needs_bash
def test_programs_for_another_processor_get_no_package(image):
    result = target_by_hand(image, "--model-name", "granite", STUB_MAKE_MACHINE="62")

    assert result.returncode == 1
    assert ("ERROR: llama-cli is no program for the aarch64 board (ELF machine 62, expected 183); no package was made."
            in result.stderr)
    assert not any((image.output / "packages").glob("granite_*_aarch64_*"))


# --- the disk rule before the package's copies (scripts/board_package.py room) ------------------------------------

GIB = 1024 ** 3


def small_disk(image, *frees):
    """The disk the image's python3 measures (tests/board_toolchain.py set_disk): 100 GB, free as given, one value per
    measurement, the last one for every further measurement."""
    from board_toolchain import set_disk
    set_disk(image.toolchain, *frees)
    return {}


def disk_rule_refusal(needed, free, total=100 * 10 ** 9):
    """The refusal of the framework's disk rule (orchestrator/Core/disk_plan.py) for the package on the output disk."""
    from orchestrator.Core import disk_plan
    usage = namedtuple("usage", "total used free")(total, total - free, free)
    check = disk_plan.room(Path("."), needed, label="output", disk_usage=lambda _path: usage)
    problem = disk_plan.refusal([check], "the package for the board")
    return None if problem is None else str(problem)


@pytest.mark.parametrize("needed, free, total", [
    (5 * 10 ** 9, 20 * 10 ** 9, 500 * 10 ** 9),      # fits: 15 GB left, 10 GB (2 %) must stay free
    (5 * 10 ** 9, 14 * 10 ** 9, 500 * 10 ** 9),      # 9 GB left: less than the 2 %
    (1 << 20, GIB, 100 * 10 ** 9),                   # a small disk keeps 2 GiB
    (1 << 20, 2 * GIB + (1 << 20), 100 * 10 ** 9),   # exactly the reserve left: fits
    (1 << 20, 2 * GIB + (1 << 20) - 1, 100 * 10 ** 9),
    (900 * 10 ** 6, 3 * 10 ** 9, 0),                 # a measurement without the disk's size keeps 2 GiB
])
def test_the_package_s_room_is_the_framework_s_disk_rule(tmp_path, needed, free, total):
    """The image has no framework, so scripts/board_package.py carries the disk rule's numbers and words: the same
    verdict and the same refusal as disk_plan.refusal for the output disk, without the reservation ledger (the per-user
    folder of the computer, which a build container does not see)."""
    from orchestrator.Core import disk_plan
    from orchestrator.utils.localization import ui_text
    board_package = _board_package()
    model = tmp_path / "model.gguf"
    with open(model, "wb") as f:
        f.truncate(needed)  # sparse: the size counts, not the blocks
    usage = namedtuple("usage", "total used free")(total, total - free, free)

    problem = board_package.room(tmp_path / "packages", [model], "the package for the board",
                                 disk_usage=lambda _path: usage)

    assert problem == disk_rule_refusal(needed, free, total)
    assert (board_package.RESERVE_MIN, board_package.RESERVE_SHARE) == (disk_plan.RESERVE_MIN, disk_plan.RESERVE_SHARE)
    assert board_package.FREE_DISK == ui_text("action.free_disk", "en")
    for value in (0, 999_999, 10 ** 9 - 1, 10 ** 9, 2 * GIB, 123_456_789_012):
        assert board_package.gb(value) == disk_plan.gb(value)


def test_the_package_s_room_counts_every_file_on_the_disk_of_the_nearest_folder(tmp_path):
    board_package = _board_package()
    files = []
    for name, size in (("model.gguf", 3 * 10 ** 9), ("llama-cli", 10 ** 6), ("llama-server", 10 ** 6)):
        files.append(tmp_path / name)
        with open(files[-1], "wb") as f:
            f.truncate(size)
    asked = []

    def usage(path):
        asked.append(path)
        return namedtuple("usage", "total used free")(100 * 10 ** 9, 0, 2 * GIB + 3 * 10 ** 9 + 10 ** 6)

    assert board_package.room(tmp_path / "packages" / "new", files[:2], "x", disk_usage=usage) is None
    assert asked == [str(tmp_path)], "measured on the folder that exists"
    assert board_package.room(tmp_path / "packages", files, "the package for the board", disk_usage=usage) == (
        "Not enough disk space for the package for the board: output disk: 3.0 GB needed, 5.1 GB free, 2.1 GB must "
        "stay free. Free disk space (old builds, the model cache, the trash), or choose a smaller model.")


@needs_bash
def test_a_package_the_disk_cannot_hold_is_refused_before_anything_slow(image):
    """Version 1.x copied the model into the package without a look at the disk. Now the framework's disk rule comes
    first: the model alone does not fit, so neither the functional test nor the cross build runs."""
    result = target_by_hand(image, "--model-name", "granite", STUB_TOOLS_LOG=image.root / "tools.log",
                            **small_disk(image, GIB))

    assert result.returncode == 1, result.stdout + result.stderr
    model = image.output / "granite-Q4_K_M.gguf"
    expected = disk_rule_refusal(model.stat().st_size, GIB)
    assert f"ERROR: {expected} No package was made." in result.stderr
    assert expected.startswith("Not enough disk space for the package for the board: output disk: 1 MB needed, "
                               "1.1 GB free, 2.1 GB must stay free. Free disk space")
    assert not any(c.startswith("cmake ") and "-DCMAKE_TOOLCHAIN_FILE=" in c for c in tools_log(image))
    assert "Functional test" not in result.stdout
    assert not (image.output / "packages").exists()


@needs_bash
def test_a_disk_that_fills_during_the_cross_build_stops_the_package_before_the_copy(image):
    """The check before the copy counts every file the package copies (the model, the programs, the toolchain file);
    here another job filled the disk while the programs were built."""
    result = target_by_hand(image, "--model-name", "granite", STUB_TOOLS_LOG=image.root / "tools.log",
                            **small_disk(image, 50 * 10 ** 9, GIB))

    assert result.returncode == 1, result.stdout + result.stderr
    assert any(c.startswith("cmake ") and "-DCMAKE_TOOLCHAIN_FILE=" in c for c in tools_log(image)), "cross build ran"
    assert ("ERROR: Not enough disk space for the package for the board: output disk: 1 MB needed, 1.1 GB free, "
            "2.1 GB must stay free.") in result.stderr
    assert "No package was made." in result.stderr
    assert not (image.output / "packages").exists()


@needs_bash
def test_the_package_s_cross_build_turns_off_every_option_that_downloads(image):
    """The programs for the board are built like every llama.cpp build of the framework: each option that can download
    something is off (DOWNLOAD_OPTIONS_OFF of orchestrator/runtime/build/recipe.py, which the image's Dockerfile turns
    off as well). At the pin LLAMA_USE_PREBUILT_UI is on by default and fetches llama-server's web UI from a Hugging
    Face bucket while llama-server is built, whenever the container has network (target_module.sh started by hand,
    the entrypoint's pipeline mode)."""
    from orchestrator.runtime.build.recipe import DOWNLOAD_OPTIONS_OFF

    result = target_by_hand(image, "--model-name", "granite", STUB_TOOLS_LOG=image.root / "tools.log")

    assert result.returncode == 0, result.stdout + result.stderr
    configure = next(c for c in tools_log(image) if c.startswith("cmake ") and "-DCMAKE_TOOLCHAIN_FILE=" in c)
    words = configure.split()
    assert [option for option in DOWNLOAD_OPTIONS_OFF if f"-D{option}=OFF" not in words] == [], configure
    assert "-DFETCHCONTENT_FULLY_DISCONNECTED=ON" in words


@needs_bash
def test_package_is_an_input_of_the_target_stage_only(image):
    """The convert stage first, then the target stage with --package: the stages before target resume (the request
    is the same), and only the target stage's marker names the package."""
    assert image.run("--to", "convert").returncode == 0

    packaged = image.run("--to", "target", "--package", **PAD)
    assert packaged.returncode == 0, packaged.stdout + packaged.stderr
    assert stage_lines(packaged) == resumed("source", "config", "convert") + ran("target", start=4)
    assert (image.output / "packages" / "model_q4_k_m_latest").is_symlink()
    markers = image.root / "build-cache" / "work" / "stages" / "build"
    inputs = {stage: next(line for line in (markers / f"{stage}.done").read_text().splitlines()
                          if line.startswith("inputs ")) for stage in ("convert", "target")}
    assert inputs["target"] == inputs["convert"] + "+package", "a target stage without --package does not resume it"


@needs_bash
def test_package_is_said_and_skipped_for_an_npu_format(image):
    result = image.run("--package", TARGET_FORMAT="rkllm", QUANTIZATION="w8a8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No package for the board: a package holds a GGUF model with llama.cpp's programs" in result.stdout
    assert not (image.output / "packages").exists()


def _board_package():
    """scripts/board_package.py as a module, without writing its bytecode into the target folder (a __pycache__
    folder there would go into every image and break the tests' copy of the image)."""
    import importlib.util
    from conftest import REPO_ROOT
    spec = importlib.util.spec_from_file_location(
        "board_package", REPO_ROOT / "targets" / "Rockchip" / "scripts" / "board_package.py")
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def test_the_package_check_finds_a_changed_a_missing_and_an_unlisted_file(tmp_path):
    board_package = _board_package()
    (tmp_path / "model.gguf").write_bytes(b"GGUF model")
    (tmp_path / "llama-cli").write_bytes(b"\x7fELF")
    board_package.write(tmp_path, ["model=m", "hardware.arch=aarch64"], ["build.jobs=4"], "model.gguf",
                        ["llama-cli=183"])
    assert board_package.check(tmp_path) == []
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text())
    assert manifest["hardware"] == {"arch": "aarch64"} and manifest["build"] == {"jobs": 4}

    (tmp_path / "model.gguf").write_bytes(b"GGUF other")
    (tmp_path / "extra.txt").write_text("x")
    (tmp_path / "llama-cli").unlink()
    problems = board_package.check(tmp_path)
    assert "model.gguf does not have the checksum SHA256SUMS lists" in problems
    assert "extra.txt is not in SHA256SUMS" in problems
    assert "llama-cli is listed in SHA256SUMS but not in the package" in problems
    assert "the files of MANIFEST.json are not the files of the package" in problems


# --- the package's helper scripts -----------------------------------------------------------------------------

def fake_package(tmp_path, arch):
    """A package as target_module.sh makes it, with a llama-cli that logs its arguments."""
    from conftest import REPO_ROOT
    scripts = REPO_ROOT / "targets" / "Rockchip" / "scripts"
    package = tmp_path / "granite_q4_k_m_aarch64_20261023_010203"
    package.mkdir()
    (package / "granite-Q4_K_M.gguf").write_text("GGUF")
    _script(package / "llama-cli", f"import sys\nopen({str(tmp_path / 'cli.log')!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    _script(package / "llama-server", "pass\n")
    for name in ("test_model.sh", "deploy.sh"):
        shutil.copy2(scripts / f"package_{name}", package / name)
    _board_package().write(package, [f"hardware.arch={arch}"], [], "granite-Q4_K_M.gguf", [])
    return package


def bash_run(*args, env=None, cwd=None):
    return subprocess.run([posix_shell("bash"), *map(str, args)], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL, env=env, cwd=cwd)


@needs_bash
def test_test_model_sh_checks_the_package_and_asks_llama_cli_for_one_answer(tmp_path):
    package = fake_package(tmp_path, platform.machine())

    result = bash_run(package / "test_model.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    args = (tmp_path / "cli.log").read_text()
    assert args.startswith("-m granite-Q4_K_M.gguf -p Hello, world! -n 10 -st --no-warmup -t ")
    assert bash_run(package / "test_model.sh", "Hi", "x").returncode == 2

    (package / "granite-Q4_K_M.gguf").write_text("GGUF changed")
    damaged = bash_run(package / "test_model.sh")
    assert damaged.returncode == 1 and "The package is damaged" in damaged.stderr


@needs_bash
def test_test_model_sh_on_another_machine_says_where_to_run_it(tmp_path):
    other = "riscv64" if platform.machine() != "riscv64" else "aarch64"
    result = bash_run(fake_package(tmp_path, other) / "test_model.sh")
    assert result.returncode == 1
    assert f"This package is for a {other} system; this machine is" in result.stderr
    assert not (tmp_path / "cli.log").exists()


def run_on_machine(tmp_path, package, machine):
    """test_model.sh of `package` on a machine whose `uname -m` says `machine`."""
    bin_dir = tmp_path / "uname-bin"
    _script(bin_dir / "uname", f"print({machine!r})\n")
    env = {"PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]), "HOME": str(tmp_path)}
    return bash_run(package / "test_model.sh", env=env)


@needs_bash
@pytest.mark.parametrize("machine", ["armv7l", "armv8l", "aarch64"])
def test_test_model_sh_runs_a_32_bit_arm_package_on_every_arm_machine_that_runs_it(tmp_path, machine):
    """A package of a module for 32-bit Arm (armv7l) runs on a 32-bit system (armv7l), on a 32-bit system of a 64-bit
    CPU (armv8l) and on a 64-bit kernel with the 32-bit system (aarch64: Raspberry Pi OS 32-bit on a Pi 4 or 5)."""
    result = run_on_machine(tmp_path, fake_package(tmp_path, "armv7l"), machine)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "cli.log").read_text().startswith("-m granite-Q4_K_M.gguf -p Hello, world! -n 10 -st")


@needs_bash
@pytest.mark.parametrize("arch, machine", [("aarch64", "armv7l"), ("armv7l", "x86_64"), ("x86_64", "aarch64")])
def test_test_model_sh_still_refuses_a_machine_that_cannot_run_the_package(tmp_path, arch, machine):
    result = run_on_machine(tmp_path, fake_package(tmp_path, arch), machine)

    assert result.returncode == 1
    assert f"This package is for a {arch} system; this machine is {machine}." in result.stderr
    assert not (tmp_path / "cli.log").exists()


SSH_STUB = """\
import os, sys
with open(os.environ["SSH_LOG"], 'a') as f:
    f.write(os.path.basename(sys.argv[0]) + ' ' + ' '.join(sys.argv[1:]) + '\\n')
"""


def deploy(tmp_path, package, *args):
    bin_dir = tmp_path / "ssh-bin"
    for name in ("ssh", "scp"):
        _script(bin_dir / name, SSH_STUB)
    env = {"PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(tmp_path),
           "SSH_LOG": str(tmp_path / "ssh.log")}
    result = bash_run(package / "deploy.sh", *args, env=env)
    log = tmp_path / "ssh.log"
    calls = [c.replace(str(package), "<pkg>") for c in log.read_text().splitlines()] if log.exists() else []
    return result, calls


@needs_bash
def test_deploy_sh_copies_over_ssh_and_scp_with_strict_host_key_checking(tmp_path):
    package = fake_package(tmp_path, "aarch64")
    dest = f"llm/{package.name}"
    options = "-o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"

    result, calls = deploy(tmp_path, package, "radxa@zero3w.local")

    assert result.returncode == 0, result.stdout + result.stderr
    files = " ".join(f"<pkg>/{p.name}" for p in sorted(package.iterdir()))
    assert calls[0] == f"ssh {options} -p 22 -- radxa@zero3w.local mkdir -p -- '{dest}'"
    assert calls[1] == f"scp {options} -P 22 -p -- {files} radxa@zero3w.local:{dest}/"
    assert calls[2].startswith(f"ssh {options} -p 22 -- radxa@zero3w.local cd -- '{dest}' && ")
    assert "sha256sum -c --quiet SHA256SUMS" in calls[2]
    assert all("StrictHostKeyChecking=yes" in c for c in calls) and len(calls) == 3


@needs_bash
def test_deploy_sh_takes_a_port_a_key_an_ipv6_address_and_a_destination(tmp_path):
    package = fake_package(tmp_path, "aarch64")
    key = tmp_path / "board_key"
    key.write_text("key")

    result, calls = deploy(tmp_path, package, "pi@[fd00::5]", "/opt/models/granite", "--port", "2222",
                           "--identity", key)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"-i {key} -o IdentitiesOnly=yes -p 2222 -- pi@fd00::5 mkdir -p -- '/opt/models/granite'" in calls[0]
    assert calls[1].endswith(" pi@[fd00::5]:/opt/models/granite/") and " -P 2222 " in calls[1]


@needs_bash
@pytest.mark.parametrize("args, message", [
    (("ro ot@board",), "'ro ot' is no user name of a board"),
    (("-oProxyCommand=evil@board",), "Unknown option '-oProxyCommand=evil@board'"),
    (("pi@board", "../etc"), "The destination '../etc' may hold only"),
    (("pi@board", "a b"), "The destination 'a b' may hold only"),
    (("pi@board;reboot",), "'board;reboot' is no host name or address"),
    (("pi@board", "--port", "99999"), "'99999' is no port."),
])
def test_deploy_sh_refuses_what_could_reach_a_shell_or_an_option(tmp_path, args, message):
    package = fake_package(tmp_path, "aarch64")
    result, calls = deploy(tmp_path, package, *args)
    assert result.returncode == 1 and f"deploy.sh: {message}" in result.stderr
    assert calls == [], "nothing was started"


@needs_bash
def test_deploy_sh_on_the_board_itself_copies_and_checks_as_in_version_1x(tmp_path):
    package = fake_package(tmp_path, "aarch64")
    dest = tmp_path / "opt-llm"

    result, calls = deploy(tmp_path, package, dest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [] and sorted(p.name for p in dest.iterdir()) == sorted(p.name for p in package.iterdir())


# --- the same packaging in the template, and the generator copies the helpers -------------------------------------

def _block(path, start, end):
    text = path.read_text(encoding="utf-8")
    first = text.index(start)
    return text[first:text.index(end, first)]


def test_the_template_packages_with_the_same_functions_and_helpers_as_the_rockchip_target():
    from conftest import REPO_ROOT
    targets = REPO_ROOT / "targets"
    blocks = [_block(targets / t / "modules" / "target_module.sh", "# PACKAGE FOR THE BOARD (version 1.x",
                     "# END OF PACKAGE") for t in ("Rockchip", "_template")]
    assert blocks[0] == blocks[1]
    for name in ("board_package.py", "package_test_model.sh", "package_deploy.sh"):
        assert (targets / "_template" / "scripts" / name).read_bytes() == \
            (targets / "Rockchip" / "scripts" / name).read_bytes(), name


def test_a_generated_module_gets_the_package_helpers_executable(tmp_path):
    from test_target_template import generate
    target = generate(tmp_path)
    for name in ("package_test_model.sh", "package_deploy.sh"):
        script = target / "scripts" / name
        assert script.is_file() and (os.name == "nt" or os.access(script, os.X_OK)), name
    assert (target / "scripts" / "board_package.py").is_file()


# --- source_module.sh --clone-llama-cpp: the pinned commit by default, LLAMA_CPP_REPO_OVERRIDE -------------------

from test_build_stages import GIT_STUB  # noqa: E402

PIN_COMMIT = "d81235049384534c167caea52b85a694f6103d14"


def clone(image, **values):
    bin_dir = image.root / "bin"
    _script(bin_dir / "git", GIT_STUB)
    env = image.env(PATH=f"{bin_dir}{os.pathsep}{image.env()['PATH']}", LLAMA_CPP_PATH=image.root / "src" / "llama.cpp",
                    **values)
    return subprocess.run([posix_shell("bash"), str(image.app / "modules" / "source_module.sh"), "--clone-llama-cpp"],
                          env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


@needs_bash
def test_the_clone_takes_the_framework_s_pin_when_no_commit_is_named(image):
    result = clone(image, LLAMA_CPP_COMMIT=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"git checkout -f {PIN_COMMIT}" in image.calls()
    assert f"LLAMA_CPP_COMMIT is not set: the framework's pin, llama.cpp b11429 (commit {PIN_COMMIT})." in result.stdout


@needs_bash
def test_the_clone_takes_the_repository_override(image):
    result = clone(image, LLAMA_CPP_COMMIT=None, LLAMA_CPP_REPO_OVERRIDE="https://git.example.com/mirror/llama.cpp.git")

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls()[0] == "git clone -- https://git.example.com/mirror/llama.cpp.git " \
                               "<root>/build-cache/repos/llama.cpp"


@needs_bash
@pytest.mark.parametrize("url", ["https://builder:secret@git.example.com/l.git", "ssh://git@git.example.com/l.git",
                                 "--upload-pack=touch /tmp/x", "ext::sh -c id", "https://git.example.com/l.git?t=1",
                                 "https://git.example.com/a b.git"])
def test_a_repository_override_that_is_no_plain_https_address_clones_nothing(image, url):
    result = clone(image, LLAMA_CPP_REPO_OVERRIDE=url)

    assert result.returncode == 1
    assert ("LLAMA_CPP_REPO_OVERRIDE must be an https address of a git repository without a user name, password, "
            "token or query; nothing was cloned.") in result.stderr
    assert image.calls() == []


@needs_bash
def test_a_checkout_that_is_not_the_commit_asked_for_is_not_used(image):
    result = clone(image, STUB_HEAD="0" * 40)

    assert result.returncode == 1
    assert f"llama.cpp: got commit {'0' * 40}, asked for {PIN_COMMIT}; the source is not used." in result.stderr
    assert not (image.root / "src" / "llama.cpp").is_symlink()


@needs_bash
def test_the_source_stage_says_where_the_image_s_source_came_from(image):
    result = image.run("--to", "source", LLAMA_CPP_REPO_OVERRIDE="https://git.example.com/mirror/llama.cpp.git")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LLAMA_CPP_REPO_OVERRIDE is set: the framework fetches the pinned llama.cpp commit (above) from that " \
           "repository when it builds this target's image" in result.stdout


def test_the_clone_default_is_a_pin_place_of_both_shipped_targets():
    from orchestrator.runtime.build import pins
    from orchestrator.runtime.catalog import load_catalog
    source = load_catalog().llama_cpp.source
    places = [p for p in pins.pin_places(source) if "--clone-llama-cpp" in p.what]
    assert {(p.path, p.kind) for p in places} == {
        (f"targets/{t}/modules/source_module.sh", "derived") for t in ("Rockchip", "_template")}
    assert len(places) == 4 and all(p.ok for p in places)


def test_a_clone_default_off_the_pin_is_a_mismatch(tmp_path):
    from conftest import REPO_ROOT
    from orchestrator.runtime.build import pins
    from orchestrator.runtime.catalog import load_catalog
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "llama_runtime.yml").write_text(
        (REPO_ROOT / "configs" / "llama_runtime.yml").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "targets" / "Board" / "modules").mkdir(parents=True)
    (root / "targets" / "Board" / "modules" / "source_module.sh").write_text(
        '#!/bin/bash\nLLAMA_CPP_PIN_TAG="b4009"\nLLAMA_CPP_PIN_COMMIT="' + "1" * 40 + '"\n')
    bad = {(p.what.split()[0], p.found) for p in pins.mismatches(pins.pin_places(load_catalog().llama_cpp.source, root))}
    assert bad == {("LLAMA_CPP_PIN_TAG", "b4009"), ("LLAMA_CPP_PIN_COMMIT", "1" * 40)}


def test_the_template_clones_with_the_same_functions_as_the_rockchip_target():
    from conftest import REPO_ROOT
    blocks = [_block(REPO_ROOT / "targets" / t / "modules" / "source_module.sh", "# THE LLAMA.CPP SOURCE BY HAND",
                     "# END OF LLAMA.CPP SOURCE") for t in ("Rockchip", "_template")]
    assert blocks[0] == blocks[1] and "repo_url_ok" in blocks[0]


# --- the image builds take LLAMA_CPP_REPO_OVERRIDE as where the pinned commit comes from ---------------------------

def image_build_args(tmp_path, monkeypatch, repos, dockerfile_text="FROM scratch\nARG LLAMA_CPP_REPO=x\n"):
    """The build arguments BuildEngine._build_docker_image gives the target image, and the build's progress."""
    from orchestrator.Core import builder as builder_module
    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.setattr(builder_module.docker, "from_env", lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    engine = BuildEngine(ConfigManager(tmp_path / "configs"))
    monkeypatch.setattr(engine, "_get_conf", lambda key, default=None: repos if key == "source_repositories" else default)
    seen = {}
    monkeypatch.setattr(engine, "_build_with_buildkit", lambda tag, path, args, *a, **k: seen.update(args))
    monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
    monkeypatch.setattr(engine, "docker_client", type("D", (), {"images": type("I", (), {
        "get": staticmethod(lambda tag: tag)})})())
    dockerfile = tmp_path / "ctx" / "Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text(dockerfile_text)
    progress = BuildProgress("b1", BuildStatus.QUEUED, "")
    config = type("C", (), {"target_arch": "rockchip", "build_id": "b1", "build_args": {}})()
    engine._build_docker_image(config, progress, dockerfile)
    return seen, progress


MIRROR = "https://git.example.com/mirror/llama.cpp.git"


def test_the_image_fetches_the_pinned_commit_from_the_repository_override(tmp_path, monkeypatch):
    seen, progress = image_build_args(tmp_path, monkeypatch, {"llama.cpp": {"url": MIRROR}})

    assert seen["LLAMA_CPP_REPO"] == MIRROR and seen["LLAMA_CPP_COMMIT"] == PIN_COMMIT
    assert any(f"llama.cpp source: the pinned commit from the repository override {MIRROR}" in line
               for line in progress.logs)


@pytest.mark.parametrize("url", ["https://builder:FAKEsecret1@git.example.com/l.git",
                                 "https://git.example.com/l.git?private_token=FAKEsecret1",
                                 "ssh://git@git.example.com/l.git", "file:///srv/mirror/llama.cpp"])
def test_an_override_the_image_cannot_use_safely_leaves_the_upstream_source(tmp_path, monkeypatch, url):
    seen, progress = image_build_args(tmp_path, monkeypatch, {"llama_cpp": {"url": url}})

    assert "LLAMA_CPP_REPO" not in seen and seen["LLAMA_CPP_COMMIT"] == PIN_COMMIT
    warning = next(w for w in progress.warnings if "is not used for the image" in w)
    assert "FAKEsecret1" not in warning and "fetches the pinned commit from upstream" in warning


def test_a_dockerfile_without_the_repository_argument_gets_none(tmp_path, monkeypatch):
    seen, progress = image_build_args(tmp_path, monkeypatch, {"llama.cpp": {"url": MIRROR}},
                                      dockerfile_text="FROM scratch\n")
    assert "LLAMA_CPP_REPO" not in seen and not progress.warnings


def test_the_shipped_images_fetch_the_pinned_commit_from_llama_cpp_repo_and_check_it():
    from conftest import REPO_ROOT
    for dockerfile in (REPO_ROOT / "targets" / "Rockchip" / "dockerfile", REPO_ROOT / "targets" / "_template" / "Dockerfile",
                       REPO_ROOT / "targets" / "_template" / "Dockerfile.gpu"):
        text = dockerfile.read_text(encoding="utf-8")
        assert 'remote add origin "${LLAMA_CPP_REPO}"' in text, dockerfile
        assert '[ "${head}" = "${LLAMA_CPP_COMMIT}" ]' in text, dockerfile


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


@needs_bash
@pytest.mark.parametrize("arch", ["armv8l", "x86", "ppc64le"])
def test_a_generated_module_refuses_every_profile_that_is_not_of_its_system(tmp_path, arch):
    """A 32-bit ARM system on a 64-bit CPU (armv8l of a 1.x probe file), a 32-bit x86 system (the probe's x86) and a
    system the module does not know are no aarch64 board either: before, the module planned its own system for them."""
    result, stages, _ = generated_config(tmp_path, f"ARCH={arch}\nCPU_MODEL=Cortex-A55\n", STUB_CC_MACHINE="183")

    assert result.returncode == 1
    assert (f"ERROR: The hardware profile is of a {arch} system; this module builds for aarch64, so nothing was "
            "planned for it.") in result.stderr
    assert not (stages / "build_config.sh").exists()
