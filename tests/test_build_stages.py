"""The Rockchip build inside its image, run with stub toolchains (DECISIONS U9, consolidation stage C).

Characterization first: these tests pin what a Rockchip build does today, so the build keeps doing exactly that when
build.sh becomes the stage runner over the four modules (source -> config -> convert -> target). For the GGUF, RKLLM
and RKNN paths and the importance-matrix job they pin the commands in their order (with their arguments), the files in
the output folder with their SHA256SUMS, build_info.json, what is left in the work folder, the exit code and the
reason of every refusal, and the environment variables the scripts read.

The image layout is simulated: the target's modules/ and scripts/ are copied to <tmp>/app (the paths /app/modules and
/app/scripts in the copies point there), llama.cpp, the RKLLM and the RKNN toolkits are stubs that log their arguments
and write small files, and the mounts of the build container are folders below <tmp>/build-cache.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from board_toolchain import install_toolchain
from conftest import NO_POSIX_BASH, REPO_ROOT, posix_shell

ROCKCHIP = REPO_ROOT / "targets" / "Rockchip"
# The stubs are Python scripts started through their #! line; Git for Windows' bash cannot run those reliably.
needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason=NO_POSIX_BASH)

CONVERT_STUB = """\
import os, sys
a = sys.argv
log = os.environ["STUB_LOG"]
out = a[a.index('--outfile') + 1]
outtype = a[a.index('--outtype') + 1]
with open(log, 'a') as f:
    f.write('convert ' + ' '.join(a[1:]) + '\\n')
if os.environ.get('STUB_FAIL') == 'convert':
    sys.exit(3)
with open(out, 'w') as f:
    f.write('gguf ' + outtype)
"""

QUANTIZE_STUB = """\
import os, sys
a = sys.argv
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('quantize ' + ' '.join(a[1:]) + '\\n')
if os.environ.get('STUB_FAIL') == 'quantize':
    sys.exit(4)
src, dst, kind = a[-3], a[-2], a[-1]
with open(src) as f:
    data = f.read()
with open(dst, 'w') as f:
    f.write('quantized ' + kind + ' of ' + data + (' with imatrix' if '--imatrix' in a else ''))
    f.write('.' * int(os.environ.get('STUB_PAD', '0')))
"""

IMATRIX_STUB = """\
import os, sys
a = sys.argv
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('imatrix ' + ' '.join(a[1:]) + '\\n')
if os.environ.get('STUB_FAIL') == 'imatrix':
    sys.exit(5)
with open(a[a.index('-o') + 1], 'w') as f:
    f.write('imatrix of ' + open(a[a.index('-m') + 1]).read())
"""

# The toolkit venvs' python: `-c` asks for the RKLLM package folder; anything else is a converter call.
TOOLKIT_STUB = """\
import os, sys
a = sys.argv
log = os.environ["STUB_LOG"]
tool = os.environ["STUB_TOOL"]
if a[1] == '-c':
    print(os.environ["STUB_PKG"])
    sys.exit(0)
with open(log, 'a') as f:
    f.write(tool + ' ' + os.path.basename(a[1]) + ' ' + ' '.join(a[2:]) +
            (' PYTHONPATH=' + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else '') + '\\n')
if os.environ.get('STUB_FAIL') == tool:
    sys.exit(6)
if os.environ.get('STUB_FAIL') == tool + '-silent':
    sys.exit(0)
out = a[a.index('--output') + 1]
with open(out, 'w') as f:
    f.write(tool + ' ' + ' '.join(x for x in a[2:] if not x.startswith('/')))
"""


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


class Image:
    """A Rockchip image on the host: modules and scripts under <root>/app, stub toolchains, the build mounts."""

    def __init__(self, root: Path, target: Path = ROCKCHIP):
        self.root = root
        self.app = root / "app"
        for part in ("modules", "scripts"):
            shutil.copytree(target / part, self.app / part)
            for script in (self.app / part).iterdir():
                text = script.read_text(encoding="utf-8")
                script.write_text(text.replace("/app/modules/", f"{self.app}/modules/")
                                  .replace("/app/scripts/", f"{self.app}/scripts/"), encoding="utf-8")
                script.chmod(0o755)
        self.llama = root / "llama.cpp"
        _script(self.llama / "convert_hf_to_gguf.py", CONVERT_STUB)
        _script(self.llama / "build" / "bin" / "llama-quantize", QUANTIZE_STUB)
        _script(self.llama / "build" / "bin" / "llama-imatrix", IMATRIX_STUB)
        (self.llama / ".llama_cpp_tag").write_text("b11429\n")
        self.rkllm_python = _script(root / "venv-rkllm" / "bin" / "python", TOOLKIT_STUB)
        self.rknn_python = _script(root / "venv-rknn" / "bin" / "python", TOOLKIT_STUB)
        self.pkg = root / "site" / "rkllm"
        (self.pkg / "lib").mkdir(parents=True)
        self.cache = root / "build-cache"
        self.output = self.cache / "output"
        self.work = self.cache / "work"
        self.imatrix = self.cache / "imatrix"
        for folder in (self.output, self.work / "tmp", self.imatrix):
            folder.mkdir(parents=True)
        self.model = root / "input-model"
        self.model.mkdir()
        for name, text in (("config.json", "{}"), ("tokenizer.json", "{}"), ("model.safetensors", "weights")):
            (self.model / name).write_text(text)
        self.log = root / "calls.log"
        # The image's compilers (the aarch64 cross toolchain, gcc and g++), cmake and make: stubs that never write to
        # the calls log (tests/board_toolchain.py)
        self.toolchain, self.buildtools = install_toolchain(root)

    def env(self, **values) -> dict:
        """The environment the BuildEngine gives the build container (builder.py _container_env and _sandbox)."""
        env = {
            "PATH": os.pathsep.join([str(self.toolchain), str(self.buildtools), str(Path(sys.executable).parent),
                                     "/usr/bin", "/bin"]),
            "HOME": str(self.work / "home"), "XDG_CACHE_HOME": str(self.work / "cache"),
            "BUILD_CACHE_DIR": str(self.cache), "BUILD_SCRATCH_DIR": str(self.work), "TMPDIR": str(self.work / "tmp"),
            "LLAMA_CPP_PATH": str(self.llama), "RKLLM_PYTHON": str(self.rkllm_python),
            "RKNN_PYTHON": str(self.rknn_python),
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
            "BUILD_ID": "build_1", "MODEL_SOURCE": str(self.model), "MODEL_NAME": "model", "MODEL_TASK": "LLM",
            "TARGET_ARCH": "rockchip", "TARGET_BOARD": "rk3588", "TARGET_FORMAT": "gguf",
            "OPTIMIZATION_LEVEL": "balanced", "QUANTIZATION": "Q4_K_M", "JOB_TYPE": "build",
            "STUB_LOG": str(self.log), "STUB_PKG": str(self.pkg),
        }
        env.update({k: str(v) for k, v in values.items() if v is not None})
        for key in [k for k, v in values.items() if v is None]:
            env.pop(key, None)
        return env

    def run(self, *args: str, tool: str = "rkllm", **values) -> subprocess.CompletedProcess:
        env = self.env(**values)
        env["STUB_TOOL"] = tool
        return subprocess.run([posix_shell("bash"), str(self.app / "modules" / "build.sh"), *args], env=env,
                              capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    def calls(self) -> list:
        if not self.log.exists():
            return []
        return [line.replace(str(self.root), "<root>") for line in self.log.read_text().splitlines()]

    def sha256sums(self) -> str:
        """The output folder as a SHA256SUMS file (sorted relative paths)."""
        files = sorted(p for p in self.output.rglob("*") if p.is_file())
        return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(self.output).as_posix()}\n"
                       for p in files)

    def outputs(self) -> dict:
        return {p.relative_to(self.output).as_posix(): p.read_text() for p in sorted(self.output.rglob("*"))
                if p.is_file()}

    def build_info(self) -> dict:
        return json.loads((self.output / "build_info.json").read_text())

    def leftovers(self) -> list:
        """Intermediates left in the work folder (the F16 GGUF and the RKLLM package copy)."""
        return sorted(p.name for p in self.work.glob("tmp-*")) + sorted(p.name for p in self.work.glob("rkllm-pkg"))


@pytest.fixture
def image(tmp_path):
    return Image(tmp_path)


def sums(files: dict) -> str:
    return "".join(f"{hashlib.sha256(text.encode()).hexdigest()}  {name}\n" for name, text in sorted(files.items()))


def info(fmt="gguf", board="rk3588", quant="Q4_K_M") -> str:
    return (f'{{"model": "model", "format": "{fmt}", "board": "{board}", "quantization": "{quant}", '
            f'"llama_cpp": "b11429"}}\n')


# ---------------------------------------------------------------------------------------------------------------
# GGUF
# ---------------------------------------------------------------------------------------------------------------

F16 = "<root>/build-cache/work/tmp-model-F16.gguf"
OUT = "<root>/build-cache/output"


@needs_bash
@pytest.mark.parametrize("quant, kind", [("Q4_K_M", "Q4_K_M"), ("q4", "Q4_K_M"), ("INT4", "Q4_K_M"),
                                         ("Q5", "Q5_K_M"), ("Q6_K", "Q6_K"), ("Q4_0", "Q4_0")])
def test_gguf_quantized_type(image, quant, kind):
    result = image.run(QUANTIZATION=quant)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [
        f"convert <root>/input-model --outfile {F16} --outtype f16",
        f"quantize {F16} {OUT}/model-{kind}.gguf {kind}",
    ]
    files = {f"model-{kind}.gguf": f"quantized {kind} of gguf f16", "build_info.json": info(quant=quant)}
    assert image.outputs() == files
    assert image.sha256sums() == sums(files)
    assert image.leftovers() == [], "the F16 intermediate is removed"
    assert result.stdout.rstrip().splitlines()[-1] == "Done."


@needs_bash
@pytest.mark.parametrize("quant, outtype, name", [("F16", "f16", "F16"), ("fp16", "f16", "F16"), ("", "f16", "F16"),
                                                  ("BF16", "bf16", "BF16"), ("F32", "f32", "F32"),
                                                  ("Q8_0", "q8_0", "Q8_0"), ("int8", "q8_0", "Q8_0")])
def test_gguf_types_the_converter_writes_directly(image, quant, outtype, name):
    result = image.run(QUANTIZATION=quant)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [f"convert <root>/input-model --outfile {OUT}/model-{name}.gguf --outtype {outtype}"]
    files = {f"model-{name}.gguf": f"gguf {outtype}", "build_info.json": info(quant=quant or "default")}
    assert image.outputs() == files and image.sha256sums() == sums(files)
    assert image.leftovers() == []


@needs_bash
def test_gguf_with_an_importance_matrix(image):
    matrix = image.imatrix / "imatrix.dat"
    matrix.write_text("imatrix")

    result = image.run(QUANTIZATION="IQ4_XS", USE_IMATRIX="1", IMATRIX_PATH=matrix)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [
        f"convert <root>/input-model --outfile {F16} --outtype f16",
        f"quantize --imatrix <root>/build-cache/imatrix/imatrix.dat {F16} {OUT}/model-IQ4_XS.gguf IQ4_XS",
    ]
    assert image.outputs()["model-IQ4_XS.gguf"] == "quantized IQ4_XS of gguf f16 with imatrix"
    assert "WARNING" not in result.stderr


@needs_bash
def test_gguf_i_quant_without_an_importance_matrix_warns_and_builds(image):
    result = image.run(QUANTIZATION="IQ4_XS")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: IQ4_XS without an importance matrix loses quality" in result.stderr
    assert [c.split()[0] for c in image.calls()] == ["convert", "quantize"]


@needs_bash
def test_gguf_without_a_board_builds_for_the_default_board(image):
    result = image.run(TARGET_BOARD="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert image.build_info()["board"] == "rk3588"


@needs_bash
def test_gguf_upper_case_format_and_board_are_lowered(image):
    result = image.run(TARGET_FORMAT="GGUF", TARGET_BOARD="RK3566", QUANTIZATION="Q4_0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert image.build_info()["board"] == "rk3566" and image.build_info()["format"] == "gguf"


@needs_bash
@pytest.mark.parametrize("values, message, calls", [
    ({"QUANTIZATION": "NOT_A_TYPE"}, "Unsupported GGUF quantization 'NOT_A_TYPE'. Supported: F16 BF16 F32 Q8_0 and", []),
    ({"QUANTIZATION": "IQ2_XS"}, "IQ2_XS needs an importance matrix; enable IMatrix calibration for this build.", []),
    ({"QUANTIZATION": "IQ2_XS", "USE_IMATRIX": "1", "IMATRIX_PATH": "/nonexistent/imatrix.dat"},
     "USE_IMATRIX=1 but importance matrix '/nonexistent/imatrix.dat' is missing.", []),
    ({"MODEL_SOURCE": "/nonexistent/model"}, "MODEL_SOURCE '/nonexistent/model' does not exist in the container.", []),
    ({"MODEL_SOURCE": ""}, "MODEL_SOURCE '' does not exist in the container.", []),
    ({"TARGET_FORMAT": "tflite"}, "Format 'tflite' is not supported by the Rockchip target (gguf, rkllm, rknn).", []),
])
def test_gguf_refusals(image, values, message, calls):
    values = {k: v.replace("/nonexistent", str(image.root / "missing")) for k, v in values.items()}
    message = message.replace("/nonexistent", str(image.root / "missing"))
    result = image.run(**values)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"ERROR: {message}" in result.stderr
    assert image.calls() == calls
    assert not any(image.output.iterdir()), "nothing in the output folder"


@needs_bash
def test_gguf_without_llama_quantize_refuses_before_converting(image):
    (image.llama / "build" / "bin" / "llama-quantize").unlink()
    result = image.run()
    assert result.returncode == 1
    assert f"ERROR: llama-quantize not found at {image.llama}/build/bin/llama-quantize" in result.stderr
    assert image.calls() == []


@needs_bash
@pytest.mark.parametrize("quant", ["F16", "Q4_K_M"])
def test_gguf_without_the_converter(image, quant):
    (image.llama / "convert_hf_to_gguf.py").unlink()
    result = image.run(QUANTIZATION=quant)
    assert result.returncode == 1
    assert f"ERROR: llama.cpp converter not found at {image.llama}/convert_hf_to_gguf.py" in result.stderr
    assert image.calls() == [] and not any(image.output.iterdir())


@needs_bash
def test_a_failing_converter_keeps_its_exit_code(image):
    result = image.run(STUB_FAIL="convert")
    assert result.returncode == 3, result.stdout + result.stderr
    assert image.calls() == [f"convert <root>/input-model --outfile {F16} --outtype f16"]
    assert not any(image.output.iterdir())


@needs_bash
def test_a_failing_quantizer_keeps_its_exit_code_and_the_f16_intermediate(image):
    result = image.run(STUB_FAIL="quantize")
    assert result.returncode == 4, result.stdout + result.stderr
    assert [c.split()[0] for c in image.calls()] == ["convert", "quantize"]
    assert image.leftovers() == ["tmp-model-F16.gguf"]
    assert not (image.output / "build_info.json").exists()


# ---------------------------------------------------------------------------------------------------------------
# The importance-matrix job
# ---------------------------------------------------------------------------------------------------------------

@needs_bash
def test_the_imatrix_job(image):
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    image.output.rmdir()  # the imatrix container has no output mount, and its image filesystem is read-only

    result = image.run(JOB_TYPE="imatrix", DATASET_PATH=dataset)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [
        f"convert <root>/input-model --outfile {F16} --outtype f16",
        f"imatrix -m {F16} -f <root>/dataset.txt -o <root>/build-cache/imatrix/imatrix.dat --chunks 100",
    ]
    assert (image.imatrix / "imatrix.dat").read_text() == "imatrix of gguf f16"
    assert image.leftovers() == [] and not image.output.exists()
    assert result.stdout.rstrip().splitlines()[-1] == "Done (imatrix)."


@needs_bash
@pytest.mark.parametrize("change, message", [
    ("no dataset", "Calibration dataset not found at ''."),
    ("no tool", "llama-imatrix not found at"),
])
def test_imatrix_job_refusals(image, change, message):
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    values = {"JOB_TYPE": "imatrix", "DATASET_PATH": dataset}
    if change == "no dataset":
        values["DATASET_PATH"] = None
    else:
        (image.llama / "build" / "bin" / "llama-imatrix").unlink()
    result = image.run(**values)
    assert result.returncode == 1 and f"ERROR: {message}" in result.stderr
    assert image.calls() == []


# ---------------------------------------------------------------------------------------------------------------
# RKLLM
# ---------------------------------------------------------------------------------------------------------------

@needs_bash
@pytest.mark.parametrize("board, quant, platform, kind", [("rk3588", "", "rk3588", "w8a8"),
                                                          ("RK3588", "W8A8_G128", "rk3588", "w8a8_g128"),
                                                          ("rk3576", "int4", "rk3576", "w4a16"),
                                                          ("rk3576", "q4_k_m", "rk3576", "w4a16")])
def test_rkllm(image, board, quant, platform, kind):
    result = image.run(TARGET_FORMAT="rkllm", TARGET_BOARD=board, QUANTIZATION=quant)

    assert result.returncode == 0, result.stdout + result.stderr
    out = f"{OUT}/model-{platform}-{kind}.rkllm"
    assert image.calls() == [
        f"rkllm export_rkllm.py --model <root>/input-model --output {out} --quant {kind} --target {platform}"]
    files = {f"model-{platform}-{kind}.rkllm": f"rkllm --model --output --quant {kind} --target {platform}",
             "build_info.json": info("rkllm", board.lower(), quant or "default")}
    assert image.outputs() == files and image.sha256sums() == sums(files)


@needs_bash
def test_rkllm_with_dataset_context_and_a_read_only_toolkit(image):
    dataset = image.root / "dataset.json"
    dataset.write_text("[]")
    shutil.rmtree(image.pkg / "lib")  # no writable lib/ folder (a read-only image; also seen this way by root)

    result = image.run(TARGET_FORMAT="rkllm", QUANTIZATION="w8a8", DATASET_PATH=dataset, MAX_CONTEXT_LENGTH=4096)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [
        f"rkllm export_rkllm.py --model <root>/input-model --output {OUT}/model-rk3588-w8a8.rkllm --quant w8a8 "
        "--target rk3588 --dataset <root>/dataset.json --max-context 4096 "
        "PYTHONPATH=<root>/build-cache/work/rkllm-pkg"]
    assert (image.work / "rkllm-pkg" / "rkllm").is_dir(), "the toolkit runs from a writable copy"


@needs_bash
@pytest.mark.parametrize("values, code, message", [
    ({"TARGET_BOARD": "rk3566"}, 1,
     "ERROR: RKLLM needs an RK3588 or RK3576 NPU; board 'rk3566' should use the gguf format (CPU)."),
    ({"TARGET_BOARD": ""}, 0, None),
    ({"STUB_FAIL": "rkllm"}, 6, None),
    ({"STUB_FAIL": "rkllm-silent"}, 1, "Converter finished but"),
    ({"RKLLM_PYTHON": "/nonexistent/python"}, 1, ">> [RKLLM] ERROR: RKLLM toolkit venv not found at /nonexistent/python"),
    ({"MODEL_SOURCE": "/nonexistent"}, 1, "ERROR: MODEL_SOURCE '/nonexistent' does not exist in the container."),
])
def test_rkllm_outcomes(image, values, code, message):
    values = {k: v.replace("/nonexistent", str(image.root / "missing")) for k, v in values.items()}
    result = image.run(TARGET_FORMAT="rkllm", **values)
    assert result.returncode == code, result.stdout + result.stderr
    if message:
        assert message.replace("/nonexistent", str(image.root / "missing")) in result.stderr
    if code:
        assert not (image.output / "build_info.json").exists()
    else:
        assert image.build_info()["board"] == "rk3588"


# ---------------------------------------------------------------------------------------------------------------
# RKNN
# ---------------------------------------------------------------------------------------------------------------

@needs_bash
@pytest.mark.parametrize("quant, dtype", [("", "fp16"), ("F16", "fp16"), ("int8", "i8")])
def test_rknn(image, quant, dtype):
    folder = image.root / "onnx"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "net.onnx").write_text("onnx")
    dataset = image.root / "samples.txt"
    dataset.write_text("a.jpg\n")

    result = image.run(TARGET_FORMAT="rknn", TARGET_BOARD="rk3566", QUANTIZATION=quant, MODEL_SOURCE=folder,
                       DATASET_PATH=dataset, tool="rknn")

    assert result.returncode == 0, result.stdout + result.stderr
    out = f"{OUT}/net-rk3566-{dtype}.rknn"
    dataset_args = " --dataset <root>/samples.txt" if dtype == "i8" else ""
    assert image.calls() == [
        f"rknn rknn_converter.py --model <root>/onnx/sub/net.onnx --target rk3566 --dtype {dtype}{dataset_args} "
        f"--output {out}"]
    assert image.build_info() == json.loads(info("rknn", "rk3566", quant or "default"))


@needs_bash
@pytest.mark.parametrize("change, message", [
    ("hf folder", "Expected one .onnx file in"),
    ("i8 without dataset", "INT8 needs a calibration dataset"),
    ("bad type", "Unsupported RKNN quantization 'w8a8' (i8, fp16)."),
])
def test_rknn_refusals(image, change, message):
    values = {"TARGET_FORMAT": "rknn", "QUANTIZATION": {"i8 without dataset": "i8", "bad type": "w8a8"}.get(change, "")}
    if change != "hf folder":
        (image.model / "net.onnx").write_text("onnx")
    result = image.run(tool="rknn", **values)
    assert result.returncode == 1 and message in result.stderr, result.stdout + result.stderr
    assert image.calls() == []


# ---------------------------------------------------------------------------------------------------------------
# What the scripts read from the environment
# ---------------------------------------------------------------------------------------------------------------

# Every variable the Rockchip build reads from its environment today: the engine's (builder.py _container_env,
# _sandbox, the stage settings) and the image's (dockerfile ENV). Characterization: the stage runner reads the same
# ones, plus only the new ones below, which it needs for its own job.
READ_TODAY = {
    "BUILD_CACHE_DIR", "BUILD_SCRATCH_DIR", "TMPDIR", "PYTHONPATH", "LLAMA_CPP_PATH", "RKLLM_PYTHON", "RKNN_PYTHON",
    "TARGET_FORMAT", "TARGET_BOARD", "QUANTIZATION", "JOB_TYPE", "MODEL_SOURCE", "MODEL_NAME", "OUTPUT_DIR",
    "USE_IMATRIX", "IMATRIX_PATH", "DATASET_PATH", "MAX_CONTEXT_LENGTH",
}
# Read by the stage runner for its resume digest (MODEL_REVISION: the BuildEngine sets it for a Hugging Face model;
# HARDWARE_CONFIG_FILE: the hardware profile of version 1.x, never set by the BuildEngine) and by the source stage,
# which says that a llama.cpp repository override has no effect inside a build container.
NEW_FOR_THE_STAGE_RUNNER = {"MODEL_REVISION", "HARDWARE_CONFIG_FILE", "LLAMA_CPP_REPO_OVERRIDE"}
# Read only by stage code started by hand with an option (source_module.sh --clone-llama-cpp), never in a build.
ONLY_ON_REQUEST = {"LLAMA_CPP_COMMIT"}
# What the runner hands its stages (build.sh): not read from the build container's environment. STAGE_LAST (the last
# stage of this run), CONVERT_OUTPUT (the --output option) and PACKAGE_REQUESTED (the --package option) are set by
# the runner itself, never from outside.
RUNNER_API = {"STAGE_DIR", "STAGE_NAME", "STAGE_RUNNER", "MODULES_DIR", "STAGE_LAST", "CONVERT_OUTPUT",
              "PACKAGE_REQUESTED"}
BASH_VARIABLES = {"BASH_SOURCE", "SECONDS", "RANDOM", "LINENO", "FUNCNAME", "PIPESTATUS", "BASH_REMATCH", "PWD", "IFS"}
BUILD_SCRIPTS = ("build.sh", "source_module.sh", "config_module.sh", "convert_module.sh", "rotate_module.sh",
                 "target_module.sh", "rkllm_module.sh", "rknn_module.sh")

_ASSIGNED = re.compile(r"(?:^|(?<=[\s;()&|]))([A-Z][A-Z0-9_]*)(?:\[[^\]]*\])?\+?="
                       r"|\bdeclare\s+(?:-\w+\s+)*([A-Z][A-Z0-9_]*)|\bfor\s+([A-Z][A-Z0-9_]*)\s+in\b", re.MULTILINE)
_USED = re.compile(r"(?<!\\)\$\{?([A-Z][A-Z0-9_]*)")  # not an escaped \${...} that a heredoc writes for CMake


def _code(script: Path) -> str:
    return "\n".join(line for line in script.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def _assigned(text: str) -> set:
    return {name for match in _ASSIGNED.finditer(text) for name in match.groups() if name}


def environment_reads(scripts) -> set:
    """The variables a set of scripts reads from the environment. In each script: every $NAME it never assigns, and
    every name whose assignment reads its own value (NAME="${NAME:-x}", PYTHONPATH="a${PYTHONPATH:+:$PYTHONPATH}").
    A script that sources a module of the set has that module's names, and one that sources the build plan the plan's
    (plan_line NAME in config_module.sh); the runner's hand-over to its stages and bash's own variables are no input
    from outside either."""
    texts = {script.name: _code(script) for script in scripts}
    plan = {name for text in texts.values() for name in re.findall(r"\bplan_line\s+([A-Z][A-Z0-9_]*)\b", text)}

    def provided(name, seen=()):
        text = texts.get(name, "")
        names = _assigned(text) | (plan if 'source "$BUILD_PLAN"' in text else set())
        for module in re.findall(r'source "\$MODULES_DIR/([\w.]+)"', text):
            if module not in seen:
                names |= provided(module, seen + (name,))
        return names

    reads = set()
    for name, text in texts.items():
        used = set(_USED.findall(text))
        reads |= used - provided(name)
        reads |= {n for line in text.splitlines() for n in re.findall(r"\b([A-Z][A-Z0-9_]*)\+?=", line)
                  if re.search(rf"\b{n}\+?=.*\$\{{?{n}\b", line)}
    return reads - RUNNER_API - BASH_VARIABLES


def stage_scripts(modules: Path):
    """build.sh and every script a build runs from it: the modules of its stages (STAGES=(...)) and the scripts they
    name, transitively."""
    found, todo = [], ["build.sh"]
    while todo:
        name = todo.pop(0)
        if name in found or not (modules / name).is_file():
            continue
        found.append(name)
        text = _code(modules / name)
        stages = [s for group in re.findall(r"\bSTAGES\+?=\(([^)]*)\)", text) for s in group.split()]
        todo += [f"{stage}_module.sh" for stage in stages] + re.findall(r"\b(\w+_module\.sh)\b", text)
    assert set(found) <= set(BUILD_SCRIPTS), found
    return [modules / name for name in found]


def test_the_build_reads_the_same_environment_variables():
    reads = environment_reads(stage_scripts(ROCKCHIP / "modules"))
    assert reads - NEW_FOR_THE_STAGE_RUNNER - ONLY_ON_REQUEST == READ_TODAY


# ---------------------------------------------------------------------------------------------------------------
# The stage runner: stages in order, progress lines, resume markers, the rotate hook (DECISIONS U9)
# ---------------------------------------------------------------------------------------------------------------

STAGE_LINE = re.compile(r"^=== Stage (\d+)/(\d+): (\w+)(?: (done in \d+ s|resumed|failed \(exit \d+\)))? ===$")


def stage_lines(result) -> list:
    """The runner's progress lines, with the seconds left out: ('1/4', 'source', 'started' | 'done' | ...)."""
    lines = []
    for line in (result.stdout + result.stderr).splitlines():
        m = STAGE_LINE.match(line)
        if m:
            state = (m.group(4) or "started").split(" in ")[0].split(" (")[0]
            lines.append((f"{m.group(1)}/{m.group(2)}", m.group(3), state))
    return lines


def ran(*names, total=4, start=1):
    """The progress lines of stages that ran (started, then done)."""
    return [line for i, name in enumerate(names, start) for line in
            ((f"{i}/{total}", name, "started"), (f"{i}/{total}", name, "done"))]


def resumed(*names, total=4):
    return [(f"{i}/{total}", name, "resumed") for i, name in enumerate(names, 1)]


@needs_bash
def test_a_build_runs_the_four_stages_in_order(image):
    result = image.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", "target")
    assert "=== Build Started: Rockchip (stages: source config convert target) ===" in result.stdout
    markers = sorted(p.name for p in (image.work / "stages" / "build").glob("*.done"))
    assert markers == ["config.done", "convert.done", "source.done", "target.done"]


@needs_bash
def test_the_imatrix_job_runs_source_config_and_convert(image):
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    image.output.rmdir()
    result = image.run(JOB_TYPE="imatrix", DATASET_PATH=dataset)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", total=3)
    assert (image.work / "stages" / "imatrix" / "convert.done").is_file()
    assert not (image.work / "stages" / "build").exists(), "the jobs keep separate markers"


@needs_bash
def test_a_failed_stage_writes_no_marker_and_the_next_run_resumes_after_the_last_finished_stage(image):
    first = image.run(STUB_FAIL="quantize")
    assert first.returncode == 4
    assert stage_lines(first)[-1] == ("4/4", "target", "failed")
    assert not (image.work / "stages" / "build" / "target.done").exists()

    second = image.run()

    assert second.returncode == 0, second.stdout + second.stderr
    assert stage_lines(second) == resumed("source", "config", "convert") + ran("target", start=4)
    assert [c.split()[0] for c in image.calls()] == ["convert", "quantize", "quantize"], "converted only once"
    assert image.outputs()["model-Q4_K_M.gguf"] == "quantized Q4_K_M of gguf f16"
    assert image.leftovers() == []


@needs_bash
def test_a_finished_build_started_again_runs_nothing(image):
    assert image.run().returncode == 0
    before = image.sha256sums()

    again = image.run()

    assert again.returncode == 0, again.stdout + again.stderr
    assert stage_lines(again) == resumed("source", "config", "convert", "target")
    assert len(image.calls()) == 2 and image.sha256sums() == before


@pytest.mark.parametrize("change", ["quantization", "model file", "module script", "dataset"])
@needs_bash
def test_other_inputs_run_every_stage_again(image, change):
    dataset = image.root / "dataset.txt"
    dataset.write_text("one")
    assert image.run(DATASET_PATH=dataset, STUB_FAIL="quantize").returncode == 4
    values = {"DATASET_PATH": dataset}
    if change == "quantization":
        values["QUANTIZATION"] = "Q5_K_M"
    elif change == "model file":
        (image.model / "model.safetensors").write_text("other weights")
    elif change == "module script":
        script = image.app / "modules" / "target_module.sh"
        script.write_text(script.read_text() + "\n# changed\n")
    else:
        dataset.write_text("two")

    result = image.run(**values)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", "target")


@needs_bash
def test_a_stage_whose_files_are_gone_runs_again(image):
    assert image.run(STUB_FAIL="quantize").returncode == 4
    (image.work / "tmp-model-F16.gguf").unlink()  # the intermediate the target stage needs

    result = image.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == resumed("source", "config") + ran("convert", "target", start=3)


@needs_bash
def test_fresh_runs_every_stage(image):
    assert image.run().returncode == 0
    result = image.run("--fresh")
    assert result.returncode == 0
    assert stage_lines(result) == ran("source", "config", "convert", "target")


@needs_bash
def test_to_stops_after_a_stage_and_the_rest_follows_later(image):
    first = image.run("--to", "convert")
    assert first.returncode == 0, first.stdout + first.stderr
    assert stage_lines(first) == ran("source", "config", "convert")
    assert first.stdout.rstrip().splitlines()[-1] == "Stopped after the stage convert (--to)."
    assert not (image.output / "build_info.json").exists()

    rest = image.run()

    assert stage_lines(rest) == resumed("source", "config", "convert") + ran("target", start=4)
    assert image.build_info()["quantization"] == "Q4_K_M"


@needs_bash
def test_to_with_every_stage_resumed_keeps_the_later_markers(image):
    """`--to config` after a finished build runs nothing, so the markers of convert and target stay valid: the next
    full run resumes every stage instead of converting the model again."""
    assert image.run().returncode == 0

    stop = image.run("--to", "config")
    assert stage_lines(stop) == resumed("source", "config")
    again = image.run()

    assert stage_lines(again) == resumed("source", "config", "convert", "target")
    assert len(image.calls()) == 2, "converted and quantized once"


@needs_bash
@pytest.mark.parametrize("args, message", [
    (["--to", "package"], "'package' is not a stage of this build (source config convert target)."),
    (["--to"], "--to needs a value"),
    (["--bogus"], "Unknown option '--bogus'"),
])
def test_runner_refusals(image, args, message):
    result = image.run(*args)
    assert result.returncode == 1 and f"ERROR: {message}" in result.stderr
    assert image.calls() == []


@needs_bash
def test_a_module_started_by_hand_runs_the_build_up_to_its_stage(image):
    result = subprocess.run([posix_shell("bash"), str(image.app / "modules" / "convert_module.sh")], env=image.env(),
                            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert")


@needs_bash
def test_the_options_of_the_1x_modules(image):
    other = image.root / "other-model"
    shutil.copytree(image.model, other)
    result = image.run("--input", str(other), "--model-name", "granite", "--quantization", "Q8_0",
                       MODEL_SOURCE=None, MODEL_NAME=None, QUANTIZATION=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [f"convert <root>/other-model --outfile {OUT}/granite-Q8_0.gguf --outtype q8_0"]


# Each module on its own with its options, as in version 1.x (DECISIONS round 16, U9 addendum; HOW2_ORIGINAL:
# `./convert_module.sh --input ... --output ... --model-name ...`, `./target_module.sh --input <F16 GGUF>
# --quantization ... --model-name ...`, `./config_module.sh --hardware-config <profile>`).

def by_hand(image, module, *args, **values):
    """A stage module started by hand in the folder <root>, with the options of its 1.x version."""
    env = image.env(**values)
    env["STUB_TOOL"] = "rkllm"
    return subprocess.run([posix_shell("bash"), str(image.app / "modules" / f"{module}_module.sh"), *map(str, args)],
                          env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                          cwd=image.root)


FROM_OPTIONS = {"MODEL_SOURCE": None, "MODEL_NAME": None, "QUANTIZATION": None}  # the request comes from the options
# target_module.sh started by hand (and the entrypoint modes target and pipeline) also makes the package for the board,
# as in version 1.x; the package's input check of 1.x wants a model of at least 1 MiB, so the stub pads its model.
PAD = {"STUB_PAD": 1 << 20}
PROFILE = "# probe\nARCHITECTURE_FULL=aarch64\nCPU_MODEL_NAME=Cortex-A55\nSUPPORTS_NEON=ON\nCPU_CORES=4\n"


@needs_bash
def test_convert_module_by_hand_writes_the_f16_gguf_where_output_says(image):
    """--output of the 1.x convert module: the GGUF F16 goes there (a relative path is the caller's folder)."""
    result = by_hand(image, "convert", "--input", image.model, "--output", "granite.fp16.gguf",
                     "--model-name", "granite", **FROM_OPTIONS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert")
    assert image.calls() == ["convert <root>/input-model --outfile <root>/granite.fp16.gguf --outtype f16"]
    assert (image.root / "granite.fp16.gguf").read_text() == "gguf f16"
    assert not any(image.output.iterdir()), "nothing else in the output folder"


@needs_bash
def test_a_build_with_output_quantizes_that_f16_file_and_keeps_it(image):
    f16 = image.root / "granite.fp16.gguf"
    result = image.run("--output", str(f16), "--model-name", "granite", MODEL_NAME=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [f"convert <root>/input-model --outfile <root>/granite.fp16.gguf --outtype f16",
                             f"quantize <root>/granite.fp16.gguf {OUT}/granite-Q4_K_M.gguf Q4_K_M"]
    assert f16.read_text() == "gguf f16", "a file the person named is no intermediate"
    assert sorted(image.outputs()) == ["build_info.json", "granite-Q4_K_M.gguf"]


@needs_bash
def test_target_module_by_hand_quantizes_a_gguf_input_and_keeps_it(image):
    """The 1.x target module takes the F16 GGUF of the convert module: the convert stage sees a GGUF input and
    converts nothing, the target stage quantizes it and leaves the person's file where it is."""
    f16 = image.root / "granite.fp16.gguf"
    f16.write_text("gguf f16")

    result = by_hand(image, "target", "--input", f16, "--quantization", "Q5_K_M", "--model-name", "granite",
                     **FROM_OPTIONS, **PAD)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", "target")
    assert image.calls() == [f"quantize <root>/granite.fp16.gguf {OUT}/granite-Q5_K_M.gguf Q5_K_M"]
    assert f16.read_text() == "gguf f16"
    assert (image.output / "granite-Q5_K_M.gguf").read_text().rstrip(".") == "quantized Q5_K_M of gguf f16"
    # and packaged for the board, as target_module.sh of 1.x did (tests/test_target_stage.py has the details)
    assert (image.output / "packages" / "granite_q5_k_m_latest" / "granite-Q5_K_M.gguf").is_file()
    assert image.build_info()["model"] == "granite" and image.build_info()["quantization"] == "Q5_K_M"


@needs_bash
def test_a_gguf_input_without_type_or_name_follows_the_1x_target_module(image):
    """1.x defaults of target_module.sh: Q4_K_M, and the name of the input file without .gguf and .fp16."""
    f16 = image.root / "granite.fp16.gguf"
    f16.write_text("gguf f16")

    result = by_hand(image, "target", "--input", f16, **FROM_OPTIONS, **PAD)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [f"quantize <root>/granite.fp16.gguf {OUT}/granite-Q4_K_M.gguf Q4_K_M"]
    assert image.build_info()["model"] == "granite" and image.build_info()["quantization"] == "Q4_K_M"


@needs_bash
@pytest.mark.parametrize("quant", ["F16", "bf16"])
def test_a_gguf_input_needs_a_type_llama_quantize_makes(image, quant):
    f16 = image.root / "granite.fp16.gguf"
    f16.write_text("gguf f16")
    result = image.run(MODEL_SOURCE=f16, QUANTIZATION=quant)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR: The model is a GGUF file already; the target stage quantizes it to a type of llama-quantize" \
        in result.stderr
    assert image.calls() == [] and f16.read_text() == "gguf f16"


@needs_bash
def test_the_imatrix_job_measures_a_gguf_input_as_it_is(image):
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    f16 = image.root / "granite.fp16.gguf"
    f16.write_text("gguf f16")
    image.output.rmdir()

    result = image.run(JOB_TYPE="imatrix", DATASET_PATH=dataset, MODEL_SOURCE=f16)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [f"imatrix -m <root>/granite.fp16.gguf -f <root>/dataset.txt "
                             f"-o <root>/build-cache/imatrix/imatrix.dat --chunks 100"]
    assert f16.read_text() == "gguf f16"


@needs_bash
def test_config_module_by_hand_with_only_a_hardware_profile_writes_toolchain_and_plan(image):
    """`config_module.sh --hardware-config FILE` of 1.x needs no model; a build without a model is still refused,
    also right after it (the plan without a model is never resumed into a build)."""
    profile = image.root / "target_hardware_config.txt"
    profile.write_text(PROFILE)

    result = by_hand(image, "config", "--hardware-config", "target_hardware_config.txt", MODEL_SOURCE=None)

    assert result.returncode == 0, result.stdout + result.stderr
    stages = image.work / "stages" / "build"
    # the cross compiler the config stage found in the image (here the stub of tests/board_toolchain.py)
    assert f"SET(CMAKE_C_COMPILER   {image.toolchain}/aarch64-linux-gnu-gcc)" in \
        (stages / "cross_compile_toolchain.cmake").read_text()
    assert "BOARD_PACKAGE=1" in (stages / "build_config.sh").read_text()
    assert f"HARDWARE_PROFILE={profile}" in (stages / "build_config.sh").read_text(), "the path is made absolute"

    later = image.run("--hardware-config", str(profile), MODEL_SOURCE=None)
    assert later.returncode == 1 and "ERROR: MODEL_SOURCE '' does not exist in the container." in later.stderr
    assert ("2/4", "config", "failed") in stage_lines(later)


PROBE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"


def plan_of(image) -> dict:
    """The board's part of the build plan the config stage wrote, read as the target stage reads it (sourced)."""
    plan = image.work / "stages" / "build" / "build_config.sh"
    names = ("BOARD_PACKAGE", "PROFILE_ARCH", "TARGET_CPU", "CFLAGS", "CMAKE_FLAGS")
    shown = subprocess.run([posix_shell("bash"), "-c", 'source "$1"; shift; for n in "$@"; do printf "%s\\n" "${!n}"; '
                            "done", "_", str(plan), *names], capture_output=True, text=True, timeout=60, check=True)
    return dict(zip(names, shown.stdout.splitlines()))


@needs_bash
@pytest.mark.parametrize("fixture, text, arch, cpu, flag", [
    ("schema2_rk3566_dietpi.txt", None, "aarch64", "Cortex-A55", "-mcpu=cortex-a55"),
    ("schema2_rk3588_devuan_sysvinit.txt", None, "aarch64", "Cortex-A76", "-mcpu=cortex-a76"),
    # RK3576 (target.yml: 4x Cortex-A72 + 4x Cortex-A53): the probe names both core types
    (None, "PROBE_SCHEMA=2\nARCH=arm64\nCPU_MODEL=Cortex-A53+Cortex-A72\nSUPPORTS_NEON=ON\n", "aarch64",
     "Cortex-A53+Cortex-A72", "-mcpu=cortex-a72"),
    ("schema1_sh_rk3566.txt", None, "aarch64", "", "-march=armv8-a"),   # an older probe file: no core named
])
def test_config_module_plans_the_board_from_the_file_the_probe_writes(image, fixture, text, arch, cpu, flag):
    """HOW2_ORIGINAL (DECISIONS round 16, U9 addendum): hardware_probe.sh on the board writes target_hardware_config.txt,
    and `config_module.sh --hardware-config` plans the board from it: the CPU-specific flags of Cortex-A55/A72/A76.
    The probe writes ARCH and CPU_MODEL (configs/hardware_probe_schema.yml); version 1.x's own names
    (ARCHITECTURE_FULL, CPU_MODEL_NAME) are read as before."""
    profile = image.root / "target_hardware_config.txt"
    profile.write_text(text if text is not None else (PROBE_FIXTURES / fixture).read_text(encoding="utf-8"))

    result = by_hand(image, "config", "--hardware-config", profile.name, MODEL_SOURCE=None)

    assert result.returncode == 0, result.stdout + result.stderr
    plan = plan_of(image)
    assert (plan["BOARD_PACKAGE"], plan["PROFILE_ARCH"], plan["TARGET_CPU"]) == ("1", arch, cpu)
    assert flag in plan["CFLAGS"] and "-DGGML_NEON=ON" in plan["CMAKE_FLAGS"]
    toolchain = (image.work / "stages" / "build" / "cross_compile_toolchain.cmake").read_text()
    assert f"SET(CMAKE_C_COMPILER   {image.toolchain}/aarch64-linux-gnu-gcc)" in toolchain and flag in toolchain


@needs_bash
def test_config_module_plans_an_x86_64_profile_with_its_simd_flags(image):
    """Native x86 with the SIMD the probe found (AVX2, AVX512 -> GGML flags), never the aarch64 cross toolchain."""
    profile = image.root / "target_hardware_config.txt"
    profile.write_text((PROBE_FIXTURES / "schema2_linux_z1_extreme.txt").read_text(encoding="utf-8"))

    result = by_hand(image, "config", "--hardware-config", profile.name, MODEL_SOURCE=None)

    assert result.returncode == 0, result.stdout + result.stderr
    plan = plan_of(image)
    assert plan["PROFILE_ARCH"] == "x86_64" and "-march=native" in plan["CFLAGS"]
    assert "-DGGML_AVX2=ON" in plan["CMAKE_FLAGS"] and "-DGGML_AVX512=ON" in plan["CMAKE_FLAGS"]
    toolchain = (image.work / "stages" / "build" / "cross_compile_toolchain.cmake").read_text()
    assert "aarch64-linux-gnu" not in toolchain and f"SET(CMAKE_C_COMPILER   {image.toolchain}/gcc)" in toolchain


@needs_bash
@pytest.mark.parametrize("arch", ["armv7", "x86", "riscv64"])
def test_config_module_refuses_a_profile_this_image_cannot_compile_for(image, arch):
    """A board whose userland this image has no compiler for (a 32-bit OS on an RK board writes ARCH=armv7) is refused
    before anything is planned for it; before, it got an aarch64 or a host toolchain whose programs cannot run there."""
    profile = image.root / "target_hardware_config.txt"
    profile.write_text(f"PROBE_SCHEMA=2\nARCH={arch}\nCPU_MODEL=Cortex-A7\nSUPPORTS_NEON=ON\n")

    result = by_hand(image, "config", "--hardware-config", profile.name, MODEL_SOURCE=None)

    assert result.returncode == 1
    assert (f"ERROR: The hardware profile is of a {arch} system; this image compiles for aarch64 (the Rockchip "
            "boards) and x86_64 only, so nothing was planned for it.") in result.stderr
    assert ("2/4", "config", "failed") in stage_lines(result)
    assert not (image.work / "stages" / "build" / "cross_compile_toolchain.cmake").exists()


@needs_bash
def test_a_changed_default_hardware_profile_runs_the_config_stage_again(image):
    """config_module.sh reads $BUILD_CACHE_DIR/target_hardware_config.txt when no profile is named: it is an input
    of the resume digest like a named one."""
    assert image.run("--to", "config").returncode == 0
    (image.cache / "target_hardware_config.txt").write_text(PROFILE)

    result = image.run("--to", "config")

    assert stage_lines(result) == ran("source", "config")
    assert (image.work / "stages" / "build" / "cross_compile_toolchain.cmake").is_file()


@needs_bash
def test_another_output_folder_runs_the_stages_again(image):
    assert image.run().returncode == 0
    other = image.root / "other-cache"
    (other / "output").mkdir(parents=True)

    result = image.run(BUILD_CACHE_DIR=other)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", "target")
    assert sorted(p.name for p in (other / "output").iterdir()) == ["build_info.json", "model-Q4_K_M.gguf"]


@needs_bash
def test_a_model_folder_with_only_config_json_builds_as_before(image):
    """The model folder of the characterization tests as they were written before the move (commit 7d583d5): only
    config.json. The build is the same as before; new are only the convert stage's warnings about the missing
    tokenizer and weights (validation of the 1.x convert module, U9 addendum), which name no path."""
    for name in ("tokenizer.json", "model.safetensors"):
        (image.model / name).unlink()
    matrix = image.imatrix / "imatrix.dat"
    matrix.write_text("imatrix")

    result = image.run(QUANTIZATION="IQ4_XS", USE_IMATRIX="1", IMATRIX_PATH=matrix)

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [
        f"convert <root>/input-model --outfile {F16} --outtype f16",
        f"quantize --imatrix <root>/build-cache/imatrix/imatrix.dat {F16} {OUT}/model-IQ4_XS.gguf IQ4_XS",
    ]
    files = {"model-IQ4_XS.gguf": "quantized IQ4_XS of gguf f16 with imatrix", "build_info.json": info(quant="IQ4_XS")}
    assert image.outputs() == files and image.leftovers() == []
    assert [line for line in result.stderr.splitlines() if "WARNING" in line] == [
        "WARNING: Missing Tokenizer files (tokenizer.json, tokenizer.model or vocab.json) in the model folder.",
        "WARNING: No model weights found (model.safetensors, pytorch_model.bin or their shards) in the model folder."]


ROTATE_HOOK = """#!/bin/bash
# A rotation step of a target (none is shipped): changes the full-precision intermediate before quantization.
set -euo pipefail
source "$STAGE_DIR/build_config.sh"
f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
echo "rotate $f16" >> "$STUB_LOG"
[ "${STUB_FAIL:-}" != rotate ] || exit 7
printf ' rotated' >> "$f16"
stage_output "$f16"
"""


@needs_bash
def test_the_rotate_hook_runs_between_convert_and_target_only_when_the_target_has_it(image):
    (image.app / "modules" / "rotate_module.sh").write_text(ROTATE_HOOK)

    result = image.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", "rotate", "target", total=5)
    assert [c.split()[0] for c in image.calls()] == ["convert", "rotate", "quantize"]
    assert image.outputs()["model-Q4_K_M.gguf"] == "quantized Q4_K_M of gguf f16 rotated"


@needs_bash
def test_a_failing_rotate_hook_stops_the_build_before_quantization(image):
    (image.app / "modules" / "rotate_module.sh").write_text(ROTATE_HOOK)
    result = image.run(STUB_FAIL="rotate")
    assert result.returncode == 7
    assert stage_lines(result)[-1] == ("4/5", "rotate", "failed")
    assert [c.split()[0] for c in image.calls()] == ["convert", "rotate"]


@needs_bash
def test_without_a_hook_there_is_no_rotate_stage(image):
    result = image.run()
    assert "rotate" not in [name for _, name, _ in stage_lines(result)]


@needs_bash
def test_a_build_never_runs_the_host_benchmark(image):
    shutil.copy(REPO_ROOT / "targets" / "_template" / "modules" / "benchmark_module.sh", image.app / "modules")
    probe = image.root / "benchmark-ran"
    script = image.app / "modules" / "benchmark_module.sh"
    script.write_text(script.read_text().replace("set -euo pipefail", f"set -euo pipefail\ntouch {probe}", 1))
    result = image.run()
    assert result.returncode == 0 and not probe.exists()
    code = "\n".join(line for line in (ROCKCHIP / "modules" / "build.sh").read_text().splitlines()
                     if not line.lstrip().startswith("#"))
    assert "benchmark" not in code


# ---------------------------------------------------------------------------------------------------------------
# entrypoint.sh: the modes pipeline and source|config|convert|target run the stage runner
# ---------------------------------------------------------------------------------------------------------------

def entrypoint(image, *args, **values):
    env = image.env(**values)
    env.update(LLM_ENTRYPOINT_INITIALIZED="1", STAGE_RUNNER_SCRIPT=str(image.app / "modules" / "build.sh"),
               STUB_TOOL="rkllm")
    return subprocess.run([posix_shell("bash"), str(REPO_ROOT / "entrypoint.sh"), *map(str, args)], env=env,
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


@needs_bash
def test_the_pipeline_mode_runs_the_stages(image):
    result = entrypoint(image, "pipeline", image.model, "granite", "Q5_K_M", MODEL_SOURCE=None, MODEL_NAME=None,
                        QUANTIZATION=None, **PAD)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert", "target")
    assert sorted(p.name for p in image.output.iterdir() if p.is_file()) == ["build_info.json", "granite-Q5_K_M.gguf"]
    # Stage 4/4 of the 1.x pipeline, "Quantization & Packaging": the package for the board, here without a profile
    assert (image.output / "packages" / "granite_q5_k_m_latest" / "MANIFEST.json").is_file()
    assert ">>> Pipeline COMPLETED successfully" in result.stderr


@needs_bash
def test_the_pipeline_mode_keeps_the_stages_exit_code_and_needs_its_arguments(image):
    failed = entrypoint(image, "pipeline", image.model, "granite", STUB_FAIL="quantize")
    assert failed.returncode == 4 and "Pipeline failed (exit 4)" in failed.stderr
    usage = entrypoint(image, "pipeline")
    assert usage.returncode == 2 and "Usage: pipeline INPUT_PATH MODEL_NAME" in usage.stderr


@needs_bash
def test_a_stage_mode_runs_the_build_up_to_that_stage(image):
    result = entrypoint(image, "convert")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_lines(result) == ran("source", "config", "convert")
    target = entrypoint(image, "target", "--quantization", "Q4_K_M", **PAD)
    assert stage_lines(target) == resumed("source", "config", "convert") + ran("target", start=4)
    assert entrypoint(image, "package").returncode != 0


@needs_bash
def test_the_stage_modes_take_the_options_of_the_1x_modules(image):
    """`docker-compose exec <builder> convert --input ... --output ... --model-name ...` (HOW2_ORIGINAL) and the
    target mode with the F16 GGUF of the convert mode."""
    f16 = image.root / "granite.fp16.gguf"
    convert = entrypoint(image, "convert", "--input", image.model, "--output", f16, "--model-name", "granite",
                         **FROM_OPTIONS)
    assert convert.returncode == 0, convert.stdout + convert.stderr
    assert f16.read_text() == "gguf f16"

    target = entrypoint(image, "target", "--input", f16, "--quantization", "Q4_K_M", "--model-name", "granite",
                        **FROM_OPTIONS, **PAD)
    assert target.returncode == 0, target.stdout + target.stderr
    assert image.calls()[-1] == f"quantize <root>/granite.fp16.gguf {OUT}/granite-Q4_K_M.gguf Q4_K_M"
    assert f16.is_file()


# ---------------------------------------------------------------------------------------------------------------
# What the modules of version 1.x did, on request: the llama.cpp clone and the package for the board
# ---------------------------------------------------------------------------------------------------------------

# git of the image: clone makes the folder, checkout records the commit, rev-parse HEAD gives it back (STUB_HEAD: another
# one, a checkout that is not the commit asked for)
GIT_STUB = """\
import os, sys
a = sys.argv[1:]
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('git ' + ' '.join(a) + '\\n')
if a[0] == 'clone':
    os.makedirs(os.path.join(a[-1], '.git'))
if a[0] == 'checkout':
    open(os.path.join('.git', 'stub_head'), 'w').write(a[-1])
if a[0] == 'rev-parse':
    print(os.environ.get('STUB_HEAD') or open(os.path.join('.git', 'stub_head')).read())
"""


@needs_bash
def test_source_module_clones_llama_cpp_by_hand(image):
    bin_dir = image.root / "bin"
    _script(bin_dir / "git", GIT_STUB)
    commit = "d81235049384534c167caea52b85a694f6103d14"
    link = image.root / "src" / "llama.cpp"
    env = image.env(PATH=f"{bin_dir}{os.pathsep}{image.env()['PATH']}", LLAMA_CPP_COMMIT=commit, LLAMA_CPP_PATH=link)
    command = [posix_shell("bash"), str(image.app / "modules" / "source_module.sh"), "--clone-llama-cpp"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stdout + result.stderr
    repo = "<root>/build-cache/repos/llama.cpp"
    # `--` before the address (it never becomes an option of git) and the checkout checked against the commit asked for
    assert image.calls() == [f"git clone -- https://github.com/ggml-org/llama.cpp.git {repo}",
                             f"git checkout -f {commit}", "git submodule update --init --recursive",
                             "git rev-parse HEAD"]
    assert link.is_symlink() and link.resolve() == (image.cache / "repos" / "llama.cpp").resolve()

    # The entrypoint mode `source` cloned llama.cpp in 1.x: `source --clone-llama-cpp` still does
    (image.cache / "repos").rename(image.root / "first-clone")
    link.unlink()
    log = image.log.read_text()
    image.log.unlink()
    mode = subprocess.run([posix_shell("bash"), str(REPO_ROOT / "entrypoint.sh"), "source", "--clone-llama-cpp"],
                          env={**env, "LLM_ENTRYPOINT_INITIALIZED": "1",
                               "STAGE_RUNNER_SCRIPT": str(image.app / "modules" / "build.sh")},
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert mode.returncode == 0, mode.stdout + mode.stderr
    assert image.calls() == log.replace(str(image.root), "<root>").splitlines()
    assert link.is_symlink()

    env["LLAMA_CPP_COMMIT"] = "b11429"
    refused = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert refused.returncode == 1 and "must be a full commit id" in refused.stderr


CMAKE_STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('cmake ' + ' '.join(sys.argv[1:]) + '\\n')
"""
MAKE_STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('make ' + ' '.join(sys.argv[1:]) + '\\n')
os.makedirs('bin', exist_ok=True)
for name in sys.argv[2:]:  # an aarch64 ELF header (machine 183): the target stage checks the programs' processor
    open(os.path.join('bin', name), 'wb').write(b'\\x7fELF' + bytes([2, 1, 1]) + bytes(9) + bytes([2, 0, 183, 0]))
"""


@needs_bash
def test_a_hardware_profile_also_packages_the_model_for_the_board(image):
    bin_dir = image.root / "bin"
    _script(bin_dir / "cmake", CMAKE_STUB)
    _script(bin_dir / "make", MAKE_STUB)
    profile = image.root / "target_hardware_config.txt"
    profile.write_text("# probe\nARCHITECTURE_FULL=aarch64\nCPU_MODEL_NAME=Cortex-A55\nSUPPORTS_NEON=ON\nCPU_CORES=4\n")

    result = image.run("--hardware-config", str(profile), STUB_PAD=1 << 20,
                       PATH=f"{bin_dir}{os.pathsep}{image.env()['PATH']}")

    assert result.returncode == 0, result.stdout + result.stderr
    toolchain = (image.work / "stages" / "build" / "cross_compile_toolchain.cmake").read_text()
    assert f"SET(CMAKE_C_COMPILER   {image.toolchain}/aarch64-linux-gnu-gcc)" in toolchain and "-mcpu=cortex-a55" in toolchain
    # The config stage had CMake read the toolchain file first (its syntax check), then the target stage configured
    assert image.calls()[0] == "cmake -P <root>/build-cache/work/stages/build/cross_compile_toolchain.cmake"
    cmake = next(c for c in image.calls() if c.startswith("cmake ") and "-DCMAKE_TOOLCHAIN_FILE=" in c)
    assert cmake.startswith("cmake <root>/llama.cpp ")
    assert "-DGGML_NEON=ON" in cmake and "-DFETCHCONTENT_FULLY_DISCONNECTED=ON" in cmake
    assert "make -j4 llama-cli llama-server" in image.calls()
    packages = image.output / "packages"
    (package,) = [p for p in packages.iterdir() if not p.is_symlink()]
    assert sorted(p.name for p in package.iterdir()) == [
        "MANIFEST.json", "README.md", "SHA256SUMS", "cmake_toolchain.cmake", "deploy.sh", "llama-cli", "llama-server",
        "model-Q4_K_M.gguf", "target_hardware_config.txt", "test_model.sh"]
    assert "./llama-cli -m model-Q4_K_M.gguf" in (package / "README.md").read_text()
    assert (packages / "model_q4_k_m_latest").is_symlink()
    assert image.build_info()["quantization"] == "Q4_K_M", "build_info.json as without a profile"


@needs_bash
def test_without_a_hardware_profile_nothing_is_cross_compiled(image):
    result = image.run()
    assert result.returncode == 0
    assert not (image.output / "packages").exists()
    assert not (image.work / "stages" / "build" / "cross_compile_toolchain.cmake").exists()


# ---------------------------------------------------------------------------------------------------------------
# The BuildEngine reads the stage lines (its one progress channel: the container log) and gives the resume inputs
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch):
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    engine = BuildEngine(ConfigManager(tmp_path / "configs"), base_dir=tmp_path)
    yield engine
    engine._executor.shutdown(wait=True, cancel_futures=True)


def follow(engine, progress, prefix, lines):
    """Feeds container log lines to the engine as _run_container does; gives (step, percent) at each record write."""
    seen = []
    engine._save_record = lambda build_id: seen.append((progress.current_stage, progress.progress_percent))
    for line in lines:
        engine._note_stage(progress, prefix, line)
    return seen


@needs_bash
def test_the_engine_turns_the_runners_lines_into_step_and_percent(image, engine):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    first = image.run(STUB_FAIL="quantize")
    progress = BuildProgress("b1", BuildStatus.BUILDING, "Running modules", 60)

    seen = follow(engine, progress, "CONT: ", first.stdout.splitlines() + first.stderr.splitlines())

    assert seen == [("Running modules: source (1/4)", 60), ("Running modules: config (2/4)", 66),
                    ("Running modules: convert (3/4)", 72), ("Running modules: target (4/4)", 78)]
    assert progress.progress_percent == 78, "a failed stage moves nothing"

    again = image.run()
    progress = BuildProgress("b1", BuildStatus.BUILDING, "Running modules", 60)
    seen = follow(engine, progress, "CONT: ", again.stdout.splitlines())
    assert seen == [("Running modules: source (1/4) resumed", 66), ("Running modules: config (2/4) resumed", 72),
                    ("Running modules: convert (3/4) resumed", 78), ("Running modules: target (4/4)", 78)]
    assert progress.progress_percent == 85


def test_a_stage_container_s_log_moves_the_build_s_step(engine, tmp_path):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    from test_review_build_engine import FakeContainer, FakeDockerClient, config_for
    client = FakeDockerClient()
    container = FakeContainer(client, "llm-build-b1", lines=[
        "=== Build Started: Rockchip (stages: source config convert target) ===", "=== Stage 1/4: source ===",
        "=== Stage 1/4: source done in 0 s ===", "=== Stage 4/4: target ===", ">> Quantizing -> Q4_K_M"])
    progress = BuildProgress("b1", BuildStatus.BUILDING, "Running modules", 60)
    engine._builds["b1"] = progress
    try:
        assert engine._run_container(config_for(tmp_path), progress, container, "CONT: ") == 0
    finally:
        client.release_all()
    assert progress.current_stage == "Running modules: target (4/4)" and progress.progress_percent == 78
    assert "CONT: >> Quantizing -> Q4_K_M" in progress.logs[-1], "every line still reaches the build log"


def test_the_imatrix_container_has_its_own_band_and_other_lines_change_nothing(engine):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    progress = BuildProgress("b1", BuildStatus.CALIBRATING, "Calculating IMatrix", 50)
    seen = follow(engine, progress, "IMATRIX: ", [
        "=== Stage 1/3: source ===", "=== Stage 3/3: convert ===", ">> Computing importance matrix...",
        "=== Stage 3/3: convert done in 12 s ===", "=== Stage 9/3: bogus ===", "=== Stage 2/3: config", "Stage 1/3"])
    assert seen == [("Calculating IMatrix: source (1/3)", 50), ("Calculating IMatrix: convert (3/3)", 56)]
    assert progress.progress_percent == 60
    assert follow(engine, progress, "BUILD: ", ["=== Stage 1/4: source ==="]) == [], "no stages in an image build"


def test_a_stage_line_read_after_the_build_moved_on_changes_nothing(engine):
    """The log reader may still deliver the container's last lines after _run_container returned (it waits 10 s for
    it): a stage line then never takes back the step and percent of the next step."""
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    progress = BuildProgress("b1", BuildStatus.BUILDING, "Extracting", 85)
    assert follow(engine, progress, "CONT: ", ["=== Stage 4/4: target done in 3 s ==="]) == []
    assert progress.current_stage == "Extracting" and progress.progress_percent == 85


def test_a_running_build_shows_its_stage_in_the_build_status(engine):
    from datetime import datetime
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    state = WorkflowState("req_1", OrchestrationStatus.BUILDING, datetime.now(),
                          current_stage="Building org/m for Rockchip", total_builds=1, build_ids=["req_1_001"])
    build = BuildProgress("req_1_001", BuildStatus.BUILDING, "Running modules", 60)
    follow(engine, build, "CONT: ", ["=== Stage 3/4: convert ==="])
    service = BuildService(SimpleNamespace(orchestrator=SimpleNamespace(
        _workflows={"req_1": state}, build_engine=SimpleNamespace(get_build_status=lambda bid: build))))
    status = service.get_status("req_1")
    assert status.stage == "Building org/m for Rockchip: Running modules: convert (3/4)"
    assert status.progress_percent == 72


def test_the_build_record_carries_the_stage_for_other_processes(engine, tmp_path):
    from orchestrator.Core import build_records
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    progress = BuildProgress("b1", BuildStatus.BUILDING, "Running modules", 60)
    engine._builds["b1"] = progress
    engine._records["b1"] = {"build_id": "b1", **build_records.this_process()}
    engine._note_stage(progress, "CONT: ", "=== Stage 2/4: config ===")
    record = json.loads((engine.records_dir / "b1.json").read_text())
    assert record["stage"] == "Running modules: config (2/4)" and record["progress"] == 66


def test_the_engine_gives_the_model_revision_to_the_stage_runner(engine):
    from orchestrator.Core.builder import BuildConfiguration, ModelFormat
    config = BuildConfiguration(build_id="b1", timestamp="t", model_source="org/model", target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir="out")
    assert "MODEL_REVISION" not in engine._container_env(config, "/build-cache/models/x")
    config.model_revision = "0123456789abcdef0123456789abcdef01234567"
    assert engine._container_env(config, "/x")["MODEL_REVISION"] == config.model_revision
