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


def generate(tmp_path, **overrides):
    targets = tmp_path / "targets"
    targets.mkdir(exist_ok=True)
    generator = ModuleGenerator(targets)
    generator.template_dir = TEMPLATE
    data = dict(module_name="My Board", architecture="aarch64", sdk="none", base_os="debian:bookworm-slim",
                packages=["build-essential"], cpu_flags="", cmake_flags="", setup_commands="",
                detection_commands="lscpu", quantization_logic="")
    data.update(overrides)
    return generator.generate_module(data)


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
