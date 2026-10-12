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

from conftest import REPO_ROOT
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
    """convert_hf_to_gguf.py and build/bin/llama-quantize that write small files and log their arguments."""
    llama = root / "llama.cpp"
    (llama / "build" / "bin").mkdir(parents=True)
    (llama / "convert_hf_to_gguf.py").write_text(
        "import sys\n"
        "a = sys.argv\n"
        "open(a[a.index('--outfile') + 1], 'w').write('gguf')\n"
        f"open({str(root / 'calls.log')!r}, 'a').write('convert ' + ' '.join(a[1:]) + '\\n')\n")
    quantize = llama / "build" / "bin" / "llama-quantize"
    quantize.write_text(f"#!{sys.executable}\n"
                        "import sys\n"
                        "open(sys.argv[-2], 'w').write('quantized ' + sys.argv[-1])\n"
                        f"open({str(root / 'calls.log')!r}, 'a').write('quantize ' + ' '.join(sys.argv[1:]) + '\\n')\n")
    quantize.chmod(0o755)
    return llama


def run_build(script: Path, root: Path, llama: Path, quant: str):
    work = root / "work"
    model = root / "model"
    model.mkdir(exist_ok=True)
    (model / "config.json").write_text("{}")
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(root), "BUILD_CACHE_DIR": str(work),
           "LLAMA_CPP_PATH": str(llama), "MODEL_SOURCE": str(model), "MODEL_NAME": "model",
           "QUANTIZATION": quant, "TARGET_FORMAT": "gguf", "JOB_TYPE": "build"}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60), work


def test_template_uses_the_current_llama_cpp_layout_and_no_tmp():
    text = (TEMPLATE / "modules" / "build.sh").read_text(encoding="utf-8")
    assert "convert_hf_to_gguf.py" in text and "convert-hf-to-gguf.py" not in text
    assert '"$LLAMA_BASE/build/bin/llama-quantize"' in text and '"$LLAMA_BASE/build/bin/llama-imatrix"' in text
    assert "/tmp/" not in text.split("set -euo pipefail")[1], "intermediates belong in the work folder"
    assert 'SCRATCH_DIR="${BUILD_SCRATCH_DIR:-$WORK_DIR}"' in text
    assert not re.search(r'\bcp\b[^\n]*\$MODEL_SOURCE', text), "no copy of the unconverted model"
    for placeholder in ("[MODULE_NAME]", "# [SDK_SETUP_COMMANDS]", "[QUANTIZATION_LOGIC]", "# [PACKAGING_COMMANDS]"):
        assert placeholder in text, placeholder
    # The type lists are the Rockchip dispatcher's (same llama.cpp version).
    rockchip = (REPO_ROOT / "targets" / "Rockchip" / "modules" / "build.sh").read_text(encoding="utf-8")
    for name in ("GGUF_CONVERT_TYPES", "GGUF_QUANT_TYPES", "GGUF_NEEDS_IMATRIX", "GGUF_WANTS_IMATRIX"):
        pattern = re.compile(rf'^{name}=".*?"$', re.M | re.S)
        assert pattern.search(text).group(0) == pattern.search(rockchip).group(0), name


@needs_bash
def test_a_generated_module_passes_bash_n_and_the_placeholder_check(tmp_path):
    target = generate(tmp_path, quantization_logic='    Q4_K_M|Q8_0) build_gguf "$QUANT_TYPE" ;;')
    assert check_shell_syntax(target) == []
    assert validate_target_folder(target) == []
    meta = yaml.safe_load((target / "target.yml").read_text())
    assert meta["capabilities"]["formats"]["gguf"]["quantizations"] == ["Q4_K_M", "Q8_0"]
    script = (target / "modules" / "build.sh").read_text()
    assert "=== Build Started: My Board ===" in script and "cp -r build/*" not in script


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
