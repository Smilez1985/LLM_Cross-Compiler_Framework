"""targets/_template follows the conventions of targets/Rockchip/modules/build.sh: convert_hf_to_gguf.py and
build/bin/llama-quantize of llama.cpp, intermediates in the work folder (not /tmp), no silent copy of the model.
A module rendered by the module generator passes bash -n and the placeholder check, and its build.sh works with
stub llama.cpp tools."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from board_toolchain import install_toolchain
from conftest import REPO_ROOT
from model_files import GGUF_HEAD, gguf_text
from orchestrator.Core.module_generator import ModuleGenerator, check_shell_syntax, validate_target_folder

TEMPLATE = REPO_ROOT / "targets" / "_template"
needs_bash = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="needs bash")


def generate(tmp_path, profile=None, **overrides):
    targets = tmp_path / "targets"
    targets.mkdir(exist_ok=True)
    generator = ModuleGenerator(targets)
    generator.template_dir = TEMPLATE
    data = dict(module_name="My Board", architecture="aarch64", sdk="none", base_os="debian:bookworm-slim",
                packages=["build-essential"], cpu_flags="", cmake_flags="", setup_commands="",
                detection_commands="lscpu", quantization_logic="")
    data.update(overrides)
    return generator.generate_module(data, profile=profile)


def stub_llama(root: Path) -> Path:
    """convert_hf_to_gguf.py and build/bin/llama-quantize that write small files and log their arguments (the
    converter a GGUF with a valid header, which the convert stage checks)."""
    llama = root / "llama.cpp"
    (llama / "build" / "bin").mkdir(parents=True)
    (llama / "convert_hf_to_gguf.py").write_text(
        "import sys\n"
        "a = sys.argv\n"
        f"open(a[a.index('--outfile') + 1], 'w').write({GGUF_HEAD!r} + 'gguf')\n"
        f"open({str(root / 'calls.log')!r}, 'a').write('convert ' + ' '.join(a[1:]) + '\\n')\n")
    quantize = llama / "build" / "bin" / "llama-quantize"
    quantize.write_text(f"#!{sys.executable}\n"
                        "import os, sys\n"
                        "open(sys.argv[-2], 'w').write('quantized ' + sys.argv[-1] + '.' * int(os.environ.get('STUB_PAD', '0')))\n"
                        f"open({str(root / 'calls.log')!r}, 'a').write('quantize ' + ' '.join(sys.argv[1:]) + '\\n')\n")
    quantize.chmod(0o755)
    return llama


def run_build(script: Path, root: Path, llama: Path, quant: str):
    work = root / "work"
    model = root / "model"
    model.mkdir(exist_ok=True)
    if not (model / "config.json").exists():  # written once: a second build of the same model resumes
        (model / "config.json").write_text("{}")
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(root), "BUILD_CACHE_DIR": str(work),
           "LLAMA_CPP_PATH": str(llama), "MODEL_SOURCE": str(model), "MODEL_NAME": "model",
           "QUANTIZATION": quant, "TARGET_FORMAT": "gguf", "JOB_TYPE": "build"}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60), work


STAGES = ("build.sh", "source_module.sh", "config_module.sh", "convert_module.sh", "target_module.sh")


def stage_code(target: Path) -> str:
    """The build of a target: the stage runner and its four stage modules (DECISIONS U9)."""
    return "\n".join((target / "modules" / name).read_text(encoding="utf-8").split("set -euo pipefail", 1)[1]
                     for name in STAGES)


def test_template_uses_the_current_llama_cpp_layout_and_no_tmp():
    text = stage_code(TEMPLATE)
    assert "convert_hf_to_gguf.py" in text and "convert-hf-to-gguf.py" not in text
    assert 'QUANTIZE_BIN="$LLAMA_BASE/build/bin/llama-quantize"' in text
    assert 'IMATRIX_BIN="$LLAMA_BASE/build/bin/llama-imatrix"' in text
    assert "/tmp/" not in text, "intermediates belong in the work folder"
    assert 'SCRATCH_DIR="${BUILD_SCRATCH_DIR:-$WORK_DIR}"' in text
    assert not re.search(r'\bcp\b[^\n]*\$MODEL_SOURCE', text), "no copy of the unconverted model"
    for placeholder, stage in (("[MODULE_NAME]", "build.sh"), ("# [SDK_SETUP_COMMANDS]", "source_module.sh"),
                               ("[QUANTIZATION_LOGIC]", "convert_module.sh"),
                               ("# [PACKAGING_COMMANDS]", "target_module.sh")):
        assert placeholder in (TEMPLATE / "modules" / stage).read_text(encoding="utf-8"), placeholder
    # The type lists are the Rockchip config stage's (same llama.cpp version).
    rockchip = stage_code(REPO_ROOT / "targets" / "Rockchip")
    for name in ("GGUF_CONVERT_TYPES", "GGUF_QUANT_TYPES", "GGUF_NEEDS_IMATRIX", "GGUF_WANTS_IMATRIX"):
        pattern = re.compile(rf'^{name}=".*?"$', re.M | re.S)
        assert pattern.search(text).group(0) == pattern.search(rockchip).group(0), name


def test_the_template_runs_the_rockchip_stage_runner():
    """One stage runner for every target: the template's build.sh is the Rockchip one with its own name."""
    def runner(target, label):
        code = (target / "modules" / "build.sh").read_text(encoding="utf-8").split("set -euo pipefail", 1)[1]
        assert f'TARGET_LABEL="{label}"' in code
        return code.replace(f'TARGET_LABEL="{label}"', "")
    assert runner(TEMPLATE, "[MODULE_NAME]") == runner(REPO_ROOT / "targets" / "Rockchip", "Rockchip")


@needs_bash
def test_a_generated_module_passes_bash_n_and_the_placeholder_check(tmp_path):
    target = generate(tmp_path, quantization_logic='    Q4_K_M|Q8_0) build_gguf "$QUANT_TYPE" ;;')
    assert check_shell_syntax(target) == []
    assert validate_target_folder(target) == []
    meta = yaml.safe_load((target / "target.yml").read_text())
    assert meta["capabilities"]["formats"]["gguf"]["quantizations"] == ["Q4_K_M", "Q8_0"]
    script = (target / "modules" / "build.sh").read_text()
    # The stage runner's banner names the module ("=== Build Started: My Board (stages: ...) ===").
    assert 'TARGET_LABEL="My Board"' in script and '=== Build Started: $TARGET_LABEL' in script
    assert "cp -r build/*" not in script
    assert all((target / "modules" / name).is_file() for name in STAGES)
    assert "Placeholder" not in (target / "modules" / "convert_module.sh").read_text()


@needs_bash
def test_the_generated_build_script_converts_quantizes_and_cleans_up(tmp_path):
    target = generate(tmp_path)
    llama = stub_llama(tmp_path)

    result, work = run_build(target / "modules" / "build.sh", tmp_path, llama, "Q4_K_M")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "output" / "model-Q4_K_M.gguf").read_text() == "quantized Q4_K_M"
    assert yaml.safe_load((work / "output" / "build_info.json").read_text())["quantization"] == "Q4_K_M"
    calls = (tmp_path / "calls.log").read_text()
    assert f"--outfile {work}/tmp-model-F16.gguf --outtype f16" in calls, "the F16 intermediate is in the work folder"
    assert not list(work.glob("tmp-*")), "the intermediate is removed"


@needs_bash
@pytest.mark.parametrize("quant, llama_ok, message", [
    ("NOT_A_TYPE", True, "Unsupported GGUF quantization 'NOT_A_TYPE'"),
    ("Q4_K_M", False, "not found at"),
    ("F16", False, "llama.cpp converter not found"),
    ("IQ2_XS", True, "IQ2_XS needs an importance matrix"),
])
def test_no_silent_fallback(tmp_path, quant, llama_ok, message):
    target = generate(tmp_path)
    llama = stub_llama(tmp_path) if llama_ok else tmp_path / "empty-llama"

    result, work = run_build(target / "modules" / "build.sh", tmp_path, llama, quant)

    assert result.returncode != 0 and message in result.stderr, result.stdout + result.stderr
    output = work / "output"
    assert not output.exists() or not any(output.iterdir()), "nothing in the output, the model is never copied"


@needs_bash
def test_a_branch_that_writes_nothing_fails(tmp_path):
    target = generate(tmp_path, quantization_logic='    Q4_K_M) echo "quantize" ;;')
    result, _ = run_build(target / "modules" / "build.sh", tmp_path, stub_llama(tmp_path), "Q4_K_M")
    assert result.returncode != 0 and "No artifact" in result.stderr


# --- a generated module under the stage runner (DECISIONS U9) ------------------------------------------------------

@needs_bash
def test_the_quantization_logic_and_the_packaging_may_use_quant_as_before(tmp_path):
    """The dispatcher of the template set QUANT="$QUANT_TYPE" next to QUANT_TYPE, and Ditto's field described the
    block as `case "$QUANT" in`: a module definition that uses $QUANT keeps working in the stages (set -u)."""
    target = generate(tmp_path, quantization_logic='    Q8_0) build_gguf "$QUANT" ;;',
                      packaging_commands='echo "packaged $QUANT" > "$OUTPUT_DIR/notes.txt"')

    result, work = run_build(target / "modules" / "build.sh", tmp_path, stub_llama(tmp_path), "q8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "output" / "notes.txt").read_text() == "packaged Q8_0\n"
    assert (work / "output" / "model-Q8_0.gguf").is_file()


@needs_bash
def test_a_generated_module_takes_the_options_of_the_1x_convert_and_target_modules(tmp_path):
    """convert_module.sh --input --output --model-name writes the F16 GGUF there; target_module.sh --input <that
    GGUF> --quantization quantizes it without converting again and keeps it (HOW2_ORIGINAL, U9 addendum)."""
    target = generate(tmp_path)
    llama = stub_llama(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    work = tmp_path / "work"
    # target_module.sh by hand also packages the model for the board (version 1.x): the module's aarch64 cross
    # compilers (CC, CXX), cmake and make of the image are stubs (tests/board_toolchain.py), and the package's input
    # check of 1.x wants a model of at least 1 MiB.
    toolchain, buildtools = install_toolchain(tmp_path)
    env = {"PATH": f"{toolchain}:{buildtools}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "BUILD_CACHE_DIR": str(work), "LLAMA_CPP_PATH": str(llama), "TARGET_FORMAT": "gguf", "JOB_TYPE": "build",
           "CC": "aarch64-linux-gnu-gcc", "CXX": "aarch64-linux-gnu-g++", "STUB_PAD": str(1 << 20)}
    modules = target / "modules"
    f16 = tmp_path / "granite.fp16.gguf"

    convert = subprocess.run(["bash", str(modules / "convert_module.sh"), "--input", str(model), "--output", str(f16),
                              "--model-name", "granite"], env=env, capture_output=True, text=True, timeout=60)
    assert convert.returncode == 0, convert.stdout + convert.stderr
    assert gguf_text(f16) == "gguf"

    quantize = subprocess.run(["bash", str(modules / "target_module.sh"), "--input", str(f16), "--quantization",
                               "Q4_K_M", "--model-name", "granite"], env=env, capture_output=True, text=True,
                              timeout=60)
    assert quantize.returncode == 0, quantize.stdout + quantize.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert [c.split()[0] for c in calls] == ["convert", "quantize"], "the GGUF input is not converted again"
    assert (work / "output" / "granite-Q4_K_M.gguf").read_text().rstrip(".") == "quantized Q4_K_M"
    assert f16.is_file(), "the input is the person's file"
    assert (work / "output" / "packages" / "granite_q4_k_m_latest" / "MANIFEST.json").is_file()


@needs_bash
def test_the_setup_commands_run_once_and_what_they_export_reaches_the_later_stages(tmp_path):
    target = generate(tmp_path,
                      setup_commands='echo setup >> "$BUILD_CACHE_DIR/setup.log"\nexport SDK_TAG=vendor-7',
                      quantization_logic='    Q8_0) convert_to_gguf q8_0 "$OUTPUT_DIR/${MODEL_NAME}-$SDK_TAG.gguf" ;;',
                      packaging_commands='cp "$OUTPUT_DIR/${MODEL_NAME}-$SDK_TAG.gguf" "$OUTPUT_DIR/packaged-$SDK_TAG.gguf"')

    result, work = run_build(target / "modules" / "build.sh", tmp_path, stub_llama(tmp_path), "Q8_0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "setup.log").read_text() == "setup\n", "the setup commands ran once"
    names = sorted(p.name for p in (work / "output").iterdir())
    assert names == ["build_info.json", "model-vendor-7.gguf", "packaged-vendor-7.gguf"], names
    assert (work / "stages" / "build" / "sdk_environment.sh").read_text() == 'export SDK_TAG="vendor-7"\n'


@needs_bash
def test_a_generated_build_resumes_after_a_failed_quantization(tmp_path):
    target = generate(tmp_path)
    llama = stub_llama(tmp_path)
    quantize = llama / "build" / "bin" / "llama-quantize"
    working = quantize.read_text()
    quantize.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(9)\n")

    first, work = run_build(target / "modules" / "build.sh", tmp_path, llama, "Q4_K_M")
    assert first.returncode == 9 and "=== Stage 4/4: target failed (exit 9) ===" in first.stderr
    quantize.write_text(working)
    second, work = run_build(target / "modules" / "build.sh", tmp_path, llama, "Q4_K_M")

    assert second.returncode == 0, second.stdout + second.stderr
    for stage in ("1/4: source", "2/4: config", "3/4: convert"):
        assert f"=== Stage {stage} resumed ===" in second.stdout
    assert (tmp_path / "calls.log").read_text().count("convert ") == 1, "the model was converted once"
    assert (work / "output" / "model-Q4_K_M.gguf").read_text() == "quantized Q4_K_M"


def test_a_stage_without_a_template_stops_the_build_instead_of_doing_nothing(tmp_path):
    """Only an incomplete installation lacks a stage template; the fallback was an 'exit 0' placeholder, which let a
    build look finished without its stage."""
    folder = tmp_path / "targets" / "board"
    (folder / "modules").mkdir(parents=True)
    (folder / "modules" / "convert_module.sh").write_text("#!/bin/bash\necho convert\n")
    ModuleGenerator(tmp_path / "targets")._write_standard_modules(folder)
    assert (folder / "modules" / "convert_module.sh").read_text() == "#!/bin/bash\necho convert\n", "kept"
    text = (folder / "modules" / "target_module.sh").read_text()
    assert "exit 1" in text and "Placeholder" not in text
    if shutil.which("bash") and os.name != "nt":
        result = subprocess.run(["bash", str(folder / "modules" / "target_module.sh")], capture_output=True, text=True)
        assert result.returncode == 1 and "the target stage of this target has no script yet" in result.stderr


# --- the recipe in generated modules (design 4.3, decisions D14, D20, D21, D6) ----------------------------------

PROBES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"
CUDA_OPT_IN = ("nvidia/cuda:12.9.1-devel-ubuntu22.04@sha256:"
               "bd4e2680a261c212f1e2fea241606f71497dc67a417f73175d794ec8212b5ba8")


def runtime_of(target: Path) -> dict:
    from orchestrator.schemas.targets import TargetRuntime
    block = yaml.safe_load((target / "target.yml").read_text())["runtime"]
    TargetRuntime.model_validate(block)
    return block


def test_the_runtime_block_is_the_recipe_plan_from_the_target_probe(tmp_path):
    from orchestrator.runtime.profile import read_profile
    profile = read_profile(PROBES / "schema2_rk3566_dietpi.txt")
    target = generate(tmp_path, module_name="Zero 3W", cpu_flags="-mcpu=cortex-a55",
                      cmake_flags="-DGGML_NEON=ON -DGGML_NATIVE=ON", profile=profile)
    block = runtime_of(target)
    assert block["built_from"] == "source" and block["purpose"] == "device-runtime"
    assert block["os"] == "linux" and block["arch"] == "arm64" and block["libc_min"] == "glibc-2.36"
    assert block["placement"] == "cross" and block["builder_base"] == "debian-12"
    assert block["cpu"] == "explicit" and block["cpu_arm_arch"] == "armv8.2-a+dotprod+fp16"
    assert block["backends"] == ["cpu"] and "llama-bench" in block["targets"]
    assert block["profile_perf_key"] == profile.perf_key()
    # The wizard's or AI's flags are advisory: kept for the reader, in no build input (decision D20).
    assert block["advisory"]["cmake_flags"] == "-DGGML_NEON=ON -DGGML_NATIVE=ON"
    assert "never applied" in block["advisory"]["note"]
    dockerfile = (target / "Dockerfile").read_text()
    assert "GGML_NEON" not in dockerfile and "cortex-a55" not in dockerfile
    assert validate_target_folder(target) == []


def test_without_a_probe_the_block_says_how_the_runtime_is_planned(tmp_path):
    target = generate(tmp_path)
    block = runtime_of(target)
    assert block["arch"] == "arm64" and block["backends"] == ["cpu"] and "profile_perf_key" not in block
    assert "llm-cli scan --device" in block["notes"][0] and "advisory" not in block


@pytest.mark.parametrize("values, architecture", [
    ({"LIBC_VERSION": "2.17"}, "aarch64"),                   # older than every builder base
    ({"ARCH": "armv7", "KERNEL_ARCH": "armv7l"}, "armv7l"),  # an architecture the recipe has no plan for yet
    ({"OS": "freebsd", "LIBC": "bsdlibc"}, "aarch64"),       # an OS the recipe has no plan for yet
])
def test_a_probe_the_recipe_cannot_plan_yet_still_makes_a_module(tmp_path, values, architecture):
    from orchestrator.runtime.profile import HardwareProfile, read_profile
    board = read_profile(PROBES / "schema2_rk3566_dietpi.txt")
    profile = HardwareProfile(dict(board.as_dict(), **values), schema=board.schema)
    target = generate(tmp_path, profile=profile, architecture=architecture)
    block = runtime_of(target)
    assert block["backends"] == [] and any("no device runtime plan" in n for n in block["notes"])
    assert validate_target_folder(target) == []


def test_the_generated_dockerfile_builds_llama_cpp_from_the_recipe(tmp_path):
    from dockerfile_steps import find_run
    target = generate(tmp_path)
    dockerfile = (target / "Dockerfile").read_text()
    rockchip = (REPO_ROOT / "targets" / "Rockchip" / "dockerfile").read_text()
    for marker in ("rev-parse HEAD", "${LLAMA_CMAKE_ARGS}"):
        assert find_run(dockerfile, marker) == find_run(rockchip, marker)
    assert "COPY --from=builder /usr/src/llama.cpp /usr/src/llama.cpp" in dockerfile


def test_a_cuda_target_gets_cuda_from_the_pinned_redist_archives(tmp_path):
    target = generate(tmp_path, module_name="GPU Box", architecture="x86_64", sdk="cuda")
    dockerfile = (target / "Dockerfile").read_text()
    assert "ARG CUDA_SOURCE=redist" in dockerfile
    assert not re.search(r"^FROM nvidia/cuda", dockerfile, re.M)
    assert validate_target_folder(target) == []


class LogRecorder:
    """Stands in for the generator's logger: (level, message) of every call."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *args, **kwargs: self.lines.append((level, str(message)))


def test_the_prebuilt_cuda_image_is_an_explicit_opt_in_pinned_by_digest(tmp_path, monkeypatch):
    log = LogRecorder()
    monkeypatch.setattr("orchestrator.Core.module_generator.get_logger", lambda name: log)
    target = generate(tmp_path, module_name="GPU Opt", architecture="x86_64", sdk="cuda", base_os=CUDA_OPT_IN)
    dockerfile = (target / "Dockerfile").read_text()
    assert f"FROM {CUDA_OPT_IN} AS builder" in dockerfile and "ARG CUDA_SOURCE=prebuilt-image" in dockerfile
    assert re.search(r"^FROM ubuntu:22\.04@sha256:[0-9a-f]{64} AS runtime$", dockerfile, re.M)
    assert validate_target_folder(target) == []
    # Labelled for what it is: in the generator's log and in the image build's log (review of WP9).
    assert any(level == "warning" and "not self-built" in message for level, message in log.lines)
    assert "(explicit opt-in, not self-built)" in dockerfile.split("prebuilt-image)", 1)[1].split(";;", 1)[0]


@pytest.mark.parametrize("base_os, message", [
    ("nvidia/cuda:12.2.2-devel-ubuntu22.04", "must be pinned by digest"),
    ("nvidia/cuda:13.0.2-devel-ubuntu24.04@sha256:" + "5" * 64, "devel-ubuntu22.04 image pinned by digest"),
    ("nvidia/cuda:12.9.1-runtime-ubuntu22.04@sha256:" + "6" * 64, "devel-ubuntu22.04 image pinned by digest"),
    ("ghcr.io/ggml-org/llama.cpp:full@sha256:" + "7" * 64, "prebuilt llama.cpp image"),
])
def test_unpinned_or_prebuilt_bases_are_refused(tmp_path, base_os, message):
    with pytest.raises(ValueError, match=message):
        generate(tmp_path, module_name="Bad Base", architecture="x86_64", sdk="cuda", base_os=base_os)
    assert not (tmp_path / "targets" / "bad_base").exists()


@pytest.mark.parametrize("change", [
    {"arch": "mips"},
    {"built_from": "prebuilt"},
    {"advisory": {"cmake_flags": "-DGGML_NATIVE=ON; curl x | sh"}},
    {"extra": "x"},
])
def test_an_invalid_runtime_block_in_a_pack_is_reported(tmp_path, change):
    target = generate(tmp_path)
    meta = yaml.safe_load((target / "target.yml").read_text())
    meta["runtime"].update(change)
    (target / "target.yml").write_text(yaml.safe_dump(meta))
    assert any("invalid runtime block" in p for p in validate_target_folder(target))


# --- the convert stage's format helpers (DECISIONS round 23 item 5) ---------------------------------------------
# The template's rknn_module.sh was a stub: it cloned rknn-toolkit2 at its newest commit and pip-installed a wheel at
# build time (a build container has no network) and then converted nothing ("Dummy call for template");
# rkllm_module.sh cloned rknn-llm the same way. Both are now the Rockchip helpers, which use the image's venv
# (RKNN_PYTHON, RKLLM_PYTHON) and refuse in plain words without it; the converters they start are the Rockchip ones.

ROCKCHIP = REPO_ROOT / "targets" / "Rockchip"
HELPERS = ("modules/rknn_module.sh", "modules/rkllm_module.sh")
CONVERTERS = ("scripts/rknn_converter.py", "scripts/export_rkllm.py")
FETCHES = re.compile(r"\bgit\s+clone\b|\bpip3?\s+install\b|\bcurl\b|\bwget\b")


def _below_header(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if i > 0 and not line.startswith("#"))
    return "".join(lines[start:])


def test_the_template_format_helpers_are_the_rockchip_ones():
    for rel in HELPERS:
        template, rockchip = (TEMPLATE / rel).read_text(encoding="utf-8"), (ROCKCHIP / rel).read_text(encoding="utf-8")
        assert _below_header(template) == _below_header(rockchip), rel
        assert "Below this header the same script as targets/Rockchip/" + rel in template.replace("\n# ", " "), rel
    for rel in CONVERTERS:
        assert (TEMPLATE / rel).read_bytes() == (ROCKCHIP / rel).read_bytes(), rel
    for rel in HELPERS + CONVERTERS:
        for target in (TEMPLATE, ROCKCHIP):
            code = "\n".join(line for line in (target / rel).read_text(encoding="utf-8").splitlines()
                             if not line.lstrip().startswith("#"))
            assert not FETCHES.search(code), (target.name, rel)


@needs_bash
@pytest.mark.parametrize("helper, venv, message", [
    ("rknn_module.sh", "RKNN_PYTHON", "RKNN toolkit venv not found at {python}: the image needs RKNN-Toolkit2 in that "
                                      "venv"),
    ("rkllm_module.sh", "RKLLM_PYTHON", "RKLLM toolkit venv not found at {python}: the image needs RKLLM-Toolkit in "
                                        "that venv"),
])
def test_a_template_helper_without_the_toolkit_refuses_and_fetches_nothing(tmp_path, helper, venv, message):
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "fetch.log"
    for name in ("git", "pip", "pip3", "curl", "wget"):
        tool = tools / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        tool.chmod(0o755)
    uname = tools / "uname"  # the x86_64 image the toolkits have wheels for, on every machine that runs the test
    uname.write_text('#!/bin/sh\n[ "$1" = -m ] && { echo x86_64; exit 0; }\n'
                     '[ -x /bin/uname ] && exec /bin/uname "$@"\nexec /usr/bin/uname "$@"\n')
    uname.chmod(0o755)
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.onnx").write_bytes(b"onnx")
    python = tmp_path / "no-venv" / "bin" / "python"
    env = {"PATH": os.pathsep.join([str(tools), "/usr/bin", "/bin"]), "MODEL_SOURCE": str(model),
           "BUILD_CACHE_DIR": str(tmp_path / "cache"), venv: str(python)}
    result = subprocess.run(["bash", str(TEMPLATE / "modules" / helper)], env=env, capture_output=True, text=True,
                            timeout=60, stdin=subprocess.DEVNULL)
    assert result.returncode == 1, result.stdout + result.stderr
    assert message.format(python=python) in result.stderr
    assert not log.exists(), "nothing is cloned, installed or downloaded"
