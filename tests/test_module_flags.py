#!/usr/bin/env python3
"""The CMake flags of a target module (DECISIONS round 25 Q6): one verdict in Python (recipe.cmake_flag_verdicts) and
the same verdicts in the build container (module_flag_verdicts of targets/*/modules/target_module.sh), over one shared
list of words (tests/data/cmake_flag_vectors.json)."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, posix_shell
from orchestrator.runtime.build import recipe

VECTORS = json.loads((Path(__file__).parent / "data" / "cmake_flag_vectors.json").read_text(encoding="utf-8"))
needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason="needs bash")
CONTEXTS = {"module": "--module", "package": "--package", "call": "--alone"}
SEP = "\x1f"


def _cases():
    for i, vector in enumerate(VECTORS["vectors"]):
        for context in CONTEXTS:
            yield pytest.param(vector, context, id=f"{i}-{context}-{'_'.join(vector['words'])[:40]}")


@pytest.mark.parametrize("vector, context", list(_cases()))
def test_the_python_verdict_of_each_vector(vector, context):
    got = [[v.word, v.verdict] for v in recipe.cmake_flag_verdicts(vector["words"], context=context,
                                                                     prefetched=vector.get("prefetched", ()))]
    assert got == vector[context]


def shell_verdicts(words, context, prefetched=(), target="Rockchip", tmp=None, env=None):
    modules = REPO_ROOT / "targets" / target / "modules"
    deps = []
    for name in prefetched:
        folder = Path(tmp) / name.lower()
        folder.mkdir(exist_ok=True)
        deps.append(f"{name}={folder}")
    result = subprocess.run(
        [posix_shell("bash"), "-c", 'source "$1" && shift && module_flag_verdicts "$@"', "_",
         str(modules / "target_module.sh"), CONTEXTS[context], *words],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        env={**os.environ, "MODULES_DIR": str(modules), "LLMCF_DEPS": " ".join(deps), **(env or {})})
    assert result.returncode == 0, result.stderr
    return [line.split(SEP) for line in result.stdout.splitlines() if line]


@needs_bash
@pytest.mark.parametrize("target", ["Rockchip", "_template"])
def test_the_shell_gives_the_same_verdicts_as_the_recipe(tmp_path, target):
    """The in-container last check agrees with the Python verdict on every vector and in every context."""
    for vector in VECTORS["vectors"]:
        for context in CONTEXTS:
            lines = shell_verdicts(vector["words"], context, vector.get("prefetched", ()), target, tmp_path)
            assert [[line[1], line[0]] for line in lines] == vector[context], (vector["words"], context)
            python = recipe.cmake_flag_verdicts(vector["words"], context=context,
                                                prefetched=vector.get("prefetched", ()))
            assert [line[4] for line in lines] == [" ".join(v.words) for v in python], (vector["words"], context)


def test_the_shell_lists_are_the_recipe_s():
    for target in ("Rockchip", "_template"):
        text = (REPO_ROOT / "targets" / target / "modules" / "target_module.sh").read_text(encoding="utf-8")

        def names(variable, text=text):
            return re.search(rf"^{variable}=\(([^)]*)\)", text, re.M).group(1).split()
        assert names("DECISION_SWITCH_NAMES") == list(recipe.DECISION_SWITCHES), target
        assert names("REFUSED_SWITCH_NAMES") == list(recipe.REFUSED_SWITCHES), target
        assert names("DEPENDENCY_OPTIONS") == [f"{k}:{v}" for k, v in recipe.DEPENDENCY_OF_OPTION.items()], target


def _problem(*words, env=None):
    modules = REPO_ROOT / "targets" / "Rockchip" / "modules"
    result = subprocess.run([posix_shell("bash"), "-c", 'source "$1" && shift && module_flag_problem "$@"', "_",
                             str(modules / "target_module.sh"), *words], capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL, env={**os.environ, "MODULES_DIR": str(modules), **(env or {})})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@needs_bash
def test_a_valid_option_outside_the_list_needs_the_person_s_decision_in_the_container():
    """Without the person's decision the package stops; allowed by the person it passes; with the standard flags it is
    left out (module_flags_kept)."""
    problem = _problem("-DGGML_NATIVE=OFF", "-G", "Ninja")
    assert problem.startswith("-G Ninja (a valid CMake option outside llmcf's list: the person decides")
    assert _problem("-DGGML_NATIVE=OFF", "-G", "Ninja", env={"LLMCF_ALLOWED_FLAGS": "-G Ninja"}) == ""
    assert _problem("-DGGML_NATIVE=OFF", "-G", "Ninja", env={"LLMCF_FLAG_DECISION": "standard"}) == ""
    modules = REPO_ROOT / "targets" / "Rockchip" / "modules"
    kept = subprocess.run(
        [posix_shell("bash"), "-c", 'source "$1" && shift && module_flags_kept "$@"', "_",
         str(modules / "target_module.sh"), "-DGGML_NATIVE=OFF", "-G", "Ninja", "--trace", "-Wno-dev"],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        env={**os.environ, "MODULES_DIR": str(modules), "LLMCF_FLAG_DECISION": "standard",
             "LLMCF_ALLOWED_FLAGS": "--trace"})
    assert kept.returncode == 0, kept.stderr
    assert kept.stdout.strip() == "-DGGML_NATIVE=OFF --trace -Wno-dev"
    assert "Left out -G Ninja" in kept.stderr


@needs_bash
def test_a_dependency_option_is_allowed_only_with_its_mounted_copy(tmp_path):
    assert _problem("-DLLAMA_BUILD_BORINGSSL=ON").startswith("-DLLAMA_BUILD_BORINGSSL=ON (LLAMA_BUILD_BORINGSSL "
                                                             "downloads at configure time")
    missing = tmp_path / "gone"
    assert _problem("-DLLAMA_BUILD_BORINGSSL=ON", env={"LLMCF_DEPS": f"BORINGSSL={missing}"}) != ""
    present = tmp_path / "boringssl"
    present.mkdir()
    assert _problem("-DLLAMA_BUILD_BORINGSSL=ON", env={"LLMCF_DEPS": f"BORINGSSL={present}"}) == ""


def test_the_module_generator_refuses_what_the_verdict_refuses_and_keeps_a_decision():
    from orchestrator.Core.module_generator import cmake_flag_problems
    assert cmake_flag_problems(["-DGGML_NATIVE=OFF", "-G", "Ninja", "-Wno-dev"]) == []
    problems = cmake_flag_problems(["-DCMAKE_TOOLCHAIN_FILE=/x.cmake", "-S", "src", "-DCMAKE_MODULE_PATH=/m"])
    assert len(problems) == 3 and all("is not allowed in a module's CMake flags" in p for p in problems), problems
    assert "a module toolchain file" in problems[0]


def test_check_download_options_allows_an_option_whose_dependency_is_mounted():
    with pytest.raises(recipe.PlanError, match="LLAMA_BUILD_LIBRESSL"):
        recipe.check_download_options({"LLAMA_BUILD_LIBRESSL": "ON", "FETCHCONTENT_FULLY_DISCONNECTED": "ON",
                                       **{n: "OFF" for n in recipe.DOWNLOAD_OPTIONS_OFF}, "LLAMA_SUBPROCESS": "OFF"})
    recipe.check_download_options({"LLAMA_BUILD_LIBRESSL": "ON", "FETCHCONTENT_FULLY_DISCONNECTED": "ON",
                                   **{n: "OFF" for n in recipe.DOWNLOAD_OPTIONS_OFF}, "LLAMA_SUBPROCESS": "OFF"},
                                  prefetched=("LIBRESSL",))


@pytest.mark.parametrize("line, expected", [
    ("CMAKE_OPTIONS='-DGGML_NATIVE=OFF -DCMAKE_TOOLCHAIN_FILE=/x.cmake'", ["-DCMAKE_TOOLCHAIN_FILE=/x.cmake"]),
    ('ARG LLAMA_CMAKE_ARGS="-DGGML_NATIVE=OFF -S /src -DCMAKE_MODULE_PATH=/m"', ["-S /src", "-DCMAKE_MODULE_PATH=/m"]),
    ("ENV LLAMA_CMAKE_ARGS=-DGGML_NATIVE=OFF", []),
    ("CMAKE_OPTIONS='-DGGML_NATIVE=OFF -G Ninja'", []),
    ('ARG LLAMA_CMAKE_ARGS="-DGGML_NATIVE=OFF ${EXTRA}"', []),
    ("echo -DCMAKE_TOOLCHAIN_FILE=/x.cmake", []),
], ids=["config-toolchain", "dockerfile-arg", "dockerfile-env", "decision-is-no-refusal", "expanded-word",
        "other-line"])
def test_the_folder_check_gives_a_target_s_flag_lines_the_module_verdict(line, expected):
    from orchestrator.Core.module_generator import flag_line_problems
    problems = flag_line_problems(line)
    assert [p.split(" is not allowed")[0] for p in problems] == expected, problems


def test_validate_target_folder_checks_a_dockerfile_s_llama_cmake_args(tmp_path):
    from orchestrator.Core.module_generator import validate_target_folder
    (tmp_path / "Dockerfile").write_text('FROM debian:bookworm-slim\nARG LLAMA_CMAKE_ARGS="-DGGML_NATIVE=OFF '
                                         '-DCMAKE_TOOLCHAIN_FILE=/opt/t.cmake"\n', encoding="utf-8")
    problems = validate_target_folder(tmp_path)
    assert any(p.startswith("Dockerfile:2: not allowed, -DCMAKE_TOOLCHAIN_FILE=/opt/t.cmake") for p in problems), \
        problems


FAKE_CMAKE = """#!/bin/sh
if [ "$1" = --help ]; then echo "  --fresh  = Configure a fresh build tree"; exit 0; fi
echo "args: $*"
echo "toolchain env: ${CMAKE_TOOLCHAIN_FILE-unset}"
"""


def _shim(tmp_path, *words, env=None, target="Rockchip"):
    """The stage modules' cmake (the check of every cmake call of a module's own scripts: the runtime layer's
    stage_env.sh, the same for every target since the runner is the framework's), with a fake cmake on PATH."""
    text = (REPO_ROOT / "orchestrator" / "runtime_layer" / "stage_env.sh").read_text(encoding="utf-8")
    function = re.search(r"^cmake\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "cmake").write_text(FAKE_CMAKE, encoding="utf-8")
    (bindir / "cmake").chmod(0o755)
    modules = REPO_ROOT / "targets" / target / "modules"
    script = function + 'cmake "$@"\n'
    return subprocess.run([posix_shell("bash"), "-c", script, "_", *words], capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL,
                          env={**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                               "MODULES_DIR": str(modules), **(env or {})})


@needs_bash
@pytest.mark.parametrize("target", ["Rockchip", "_template"])
def test_a_module_s_cmake_call_starts_from_a_fresh_cache_without_a_toolchain_from_the_environment(tmp_path, target):
    """Review leftover (build-probe/build-safety): a CMakeCache.txt left in the module's build folder or a
    CMAKE_TOOLCHAIN_FILE in the environment set options without a word on the command line."""
    run = _shim(tmp_path, "-S", "src", "-B", "b", env={"CMAKE_TOOLCHAIN_FILE": "/tmp/evil.cmake"}, target=target)
    assert run.returncode == 0, run.stderr
    assert "args: --fresh -S src -B b" in run.stdout
    assert "toolchain env: unset" in run.stdout
    build = _shim(tmp_path, "--build", "b", target=target)
    assert "args: --build b" in build.stdout, "only a configure run gets --fresh"


@needs_bash
def test_the_toolchain_the_build_plan_set_stays(tmp_path):
    plan = tmp_path / "build_config.sh"
    plan.write_text("CMAKE_TOOLCHAIN_FILE=/build-cache/plan/toolchain.cmake\n", encoding="utf-8")
    run = _shim(tmp_path, "-S", "src", env={"CMAKE_TOOLCHAIN_FILE": "/build-cache/plan/toolchain.cmake",
                                            "BUILD_PLAN": str(plan)})
    assert "toolchain env: /build-cache/plan/toolchain.cmake" in run.stdout, run.stdout + run.stderr


def test_the_shell_s_program_variables_are_the_recipe_s():
    """A variable that makes CMake or the build run another program (a launcher, a code checker, the cross emulator,
    the build tool) is refused in a module's flags; the shell's pattern names the same variables as the recipe's."""
    names = ["CMAKE_C_COMPILER_LAUNCHER", "CMAKE_CXX_COMPILER_LAUNCHER", "CMAKE_CUDA_COMPILER_LAUNCHER",
             "CMAKE_C_LINKER_LAUNCHER", "CMAKE_CXX_CLANG_TIDY", "CMAKE_C_CPPCHECK", "CMAKE_CXX_CPPLINT",
             "CMAKE_CXX_INCLUDE_WHAT_YOU_USE", "CMAKE_C_ICSTAT", "CMAKE_LINK_WHAT_YOU_USE_CHECK",
             "CMAKE_CROSSCOMPILING_EMULATOR", "CMAKE_MAKE_PROGRAM", "CMAKE_C_COMPILER", "CMAKE_C_FLAGS",
             "CMAKE_BUILD_TYPE", "GGML_CCACHE"]
    refused = [n for n in names if recipe.PROGRAM_VARIABLE_RE.match(n)]
    assert refused == names[:12]
    for target in ("Rockchip", "_template"):
        text = (REPO_ROOT / "targets" / target / "modules" / "target_module.sh").read_text(encoding="utf-8")
        shell = re.compile(re.search(r"^PROGRAM_VARIABLE_PATTERN='([^']*)'$", text, re.M).group(1))
        assert [n for n in names if shell.search(n)] == refused, target
