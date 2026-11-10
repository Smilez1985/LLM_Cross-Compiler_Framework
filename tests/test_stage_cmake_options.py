"""No stage module sets a CMake option of llama.cpp that llama.cpp does not have at the pin.

CMake ignores an unknown -D with a warning, so a wrong name builds without what it seems to ask for: the template's
config stage set GGML_NEON and GGML_FP16 from the probe, neither of which exists (NEON is the ARMv8-A baseline, FP16 a
tag of GGML_CPU_ARM_ARCH). The list of real names is tests/fixtures/llama_cpp_cmake_options.txt, read from llama.cpp's
CMake files at the commit of configs/llama_runtime.yml."""
import re

import yaml

from conftest import REPO_ROOT

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "llama_cpp_cmake_options.txt"
# Gone at the pin, set OFF on purpose: older llama.cpp versions download through libcurl while they are built.
KEPT_OFF = {"LLAMA_CURL"}
SET = re.compile(r"-D((?:GGML|LLAMA)_[A-Z0-9_]+)=([^\s\"']*)")
LOOP = re.compile(r"for\s+(\w+)\s+in\s+([A-Z0-9_ ]+);\s*do(.*?)\bdone\b", re.S)


def known_names():
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    commit = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("# commit:"))
    return commit, {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def emitted(text):
    """(name, value) of every -DGGML_*/-DLLAMA_* a stage module writes outside comments, loops over feature words
    expanded (`for feature in AVX2 CUDA; do ... -DGGML_$feature=ON`)."""
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    found = SET.findall(code)
    for var, words, body in LOOP.findall(code):
        for prefix, value in re.findall(r"-D((?:GGML|LLAMA)_)\$\{?" + var + r"\}?=([^\s\"']*)", body):
            found += [(prefix + word, value) for word in words.split()]
    return found


def test_the_option_list_is_read_at_the_pin():
    pin = yaml.safe_load((REPO_ROOT / "configs" / "llama_runtime.yml").read_text(encoding="utf-8"))
    commit, names = known_names()
    assert commit == pin["llama_cpp"]["source"]["commit"], "a pin bump reads the option list again"
    assert {"GGML_NATIVE", "GGML_CPU_ARM_ARCH", "GGML_AVX2", "LLAMA_BUILD_SERVER"} <= names
    assert not {"GGML_NEON", "GGML_FP16"} & names


def test_no_stage_module_sets_a_cmake_option_llama_cpp_lacks():
    _, names = known_names()
    problems = []
    for script in sorted(REPO_ROOT.glob("targets/*/modules/*.sh")):
        for name, value in emitted(script.read_text(encoding="utf-8")):
            if name in names or (name in KEPT_OFF and value.strip("\"'") == "OFF"):
                continue
            problems.append(f"{script.relative_to(REPO_ROOT)}: -D{name}={value}")
    assert problems == []


def test_the_check_bites():
    _, names = known_names()
    text = 'for feature in NEON AVX2; do\n    CMAKE_OPTIONS="$CMAKE_OPTIONS -DGGML_$feature=ON"\ndone\n# -DGGML_FOO=ON\n'
    got = emitted(text)
    assert ("GGML_NEON", "ON") in got and ("GGML_AVX2", "ON") in got and all(n != "GGML_FOO" for n, _ in got)
    assert "GGML_NEON" not in names
