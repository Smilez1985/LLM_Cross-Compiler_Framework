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

import pytest

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
        (self.model / "config.json").write_text("{}")
        self.log = root / "calls.log"

    def env(self, **values) -> dict:
        """The environment the BuildEngine gives the build container (builder.py _container_env and _sandbox)."""
        env = {
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
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
NEW_FOR_THE_STAGE_RUNNER: set = set()
# Read by stage code that runs only on request (a module started by hand with its own options), never in a build.
ONLY_ON_REQUEST: set = set()
BUILD_SCRIPTS = ("build.sh", "rkllm_module.sh", "rknn_module.sh")

_ASSIGNED = re.compile(r"(?:^|[\s;()&|])([A-Z][A-Z0-9_]*)(?:\[[^\]]*\])?\+?=", re.MULTILINE)
_USED = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)")


def environment_reads(scripts) -> set:
    """The variables a set of scripts reads from the environment: every $NAME the scripts never assign themselves,
    every ${NAME:-...} of such a name, and every name whose assignment reads its own value (NAME="${NAME:-x}",
    PYTHONPATH="a${PYTHONPATH:+:$PYTHONPATH}")."""
    texts = ["\n".join(line for line in s.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
             for s in scripts]
    assigned = {name for text in texts for name in _ASSIGNED.findall(text)}
    assigned |= {name for text in texts for name in re.findall(r"\bfor\s+([A-Z][A-Z0-9_]*)\s+in\b", text)}
    used = {name for text in texts for name in _USED.findall(text)}
    own = {name for text in texts for line in text.splitlines() for name in _ASSIGNED.findall(line)
           if re.search(rf"\b{name}\+?=.*\$\{{?{name}\b", line)}
    return (used - assigned) | own


def stage_scripts(modules: Path):
    return [modules / name for name in BUILD_SCRIPTS if (modules / name).is_file()]


def test_the_build_reads_the_same_environment_variables():
    reads = environment_reads(stage_scripts(ROCKCHIP / "modules"))
    assert reads - NEW_FOR_THE_STAGE_RUNNER == READ_TODAY
