"""The llama.cpp conversion tools of the target images, built from the recipe (design 4.3; decisions D13, D21, D6).

targets/Rockchip/dockerfile, targets/_template/Dockerfile and targets/_template/Dockerfile.gpu fetch the pinned commit,
check it, build the tools with LLAMA_CMAKE_ARGS from the build recipe and the options every build has after them, and
check llama-bench --version. The RUN steps run here on the host with sh: against a fake upstream repository (file://),
fake cmake and llama-bench, and fake CUDA redist archives. No Docker, no network.
"""

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from dockerfile_steps import defaults, find_run, instructions, run
from orchestrator.runtime.build.executor import DOWNLOAD_MARKERS
from orchestrator.runtime.build.host_tools import CMAKE_WORD, cmake_args, host_tools, plan_host_tools
from orchestrator.runtime.build.recipe import DOWNLOAD_OPTIONS_OFF, NEVER_SET, TARGETS, PlanError
from orchestrator.runtime.catalog import load_catalog
from orchestrator.runtime.profile import read_profile
from orchestrator.utils.prebuilt_llama import scan_target_folder

CATALOG = load_catalog()
PIN = CATALOG.llama_cpp.source
PROBES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"
ROCKCHIP = REPO_ROOT / "targets" / "Rockchip" / "dockerfile"
TEMPLATE = REPO_ROOT / "targets" / "_template" / "Dockerfile"
TEMPLATE_GPU = REPO_ROOT / "targets" / "_template" / "Dockerfile.gpu"
DOCKERFILES = [ROCKCHIP, TEMPLATE, TEMPLATE_GPU]
FETCH_MARKER = 'rev-parse HEAD'
BUILD_MARKER = '${LLAMA_CMAKE_ARGS}'

needs_sh = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="needs a POSIX shell")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def cmake_line(dockerfile: Path) -> str:
    step = find_run(text(dockerfile), BUILD_MARKER)
    return step[step.index("cmake -S"):step.index("> \"${log}\"")]


def defines(fragment: str) -> dict:
    """-DNAME=VALUE options in a text, in order; a later one wins, as with CMake."""
    out = {}
    for name, value in re.findall(r"-D([A-Z][A-Z0-9_]*)=(\"[^\"]*\"|\S+)", fragment):
        out[name] = value
    return out


# --- what the Dockerfiles say ------------------------------------------------------------------------------------

class TestTheRecipeInTheDockerfiles:

    def test_every_target_dockerfile_builds_llama_cpp_the_same_way(self):
        """The template follows the shipped target: the same fetch and build steps (decision D21, recipes are what is
        shared)."""
        rockchip = text(ROCKCHIP)
        for other in (TEMPLATE, TEMPLATE_GPU):
            assert find_run(text(other), FETCH_MARKER) == find_run(rockchip, FETCH_MARKER), other.name
            assert find_run(text(other), BUILD_MARKER) == find_run(rockchip, BUILD_MARKER), other.name
            for arg in ("LLAMA_CPP_REPO", "LLAMA_CPP_TAG", "LLAMA_CPP_COMMIT", "LLAMA_CMAKE_ARGS"):
                assert defaults(text(other))[arg] == defaults(rockchip)[arg], (other.name, arg)

    @pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
    def test_the_defaults_are_the_pin_and_the_baseline_the_recipe_plans(self, dockerfile):
        values = defaults(text(dockerfile))
        assert values["LLAMA_CPP_TAG"] == PIN.tag and values["LLAMA_CPP_COMMIT"] == PIN.commit
        assert values["LLAMA_CPP_REPO"] == PIN.repo
        # The default is what the recipe plans for an AVX2 machine without AVX-512 (the fixed baseline these tools
        # always had, written out); the framework passes the plan for the real build host instead.
        haswell = plan_host_tools(read_profile(PROBES / "schema2_pc_two_nvidia.txt"), catalog=CATALOG)
        words = values["LLAMA_CMAKE_ARGS"].split()
        assert all(CMAKE_WORD.fullmatch(w) for w in words)
        default = defines(" ".join(words))
        assert default == {k: haswell.options()[k] for k in default}
        assert default["GGML_NATIVE"] == "OFF" and default["GGML_AVX2"] == "ON" and default["GGML_AVX512"] == "OFF"

    @pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
    def test_the_options_every_build_has_come_last(self, dockerfile):
        line = cmake_line(dockerfile)
        before, after = line.split(BUILD_MARKER)
        fixed = defines(after)
        assert not defines(before)
        assert {k: fixed.get(k) for k in DOWNLOAD_OPTIONS_OFF} == {k: "OFF" for k in DOWNLOAD_OPTIONS_OFF}
        assert fixed["LLAMA_SUBPROCESS"] == "OFF" and fixed["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON"
        assert fixed["LLAMA_OPENSSL"] == "OFF" and fixed["LLAMA_BUILD_SERVER"] == "OFF"
        assert fixed["LLAMA_BUILD_NUMBER"] == '"${build_number}"' and fixed["LLAMA_BUILD_COMMIT"] == '"${short_commit}"'
        assert not set(NEVER_SET) & set(fixed)

    @pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
    def test_refused_options_targets_and_download_markers_follow_the_recipe(self, dockerfile):
        step = find_run(text(dockerfile), BUILD_MARKER)
        branches = set(re.findall(r"-D([A-Z][A-Z0-9_]*)=\*", step.split("case")[1].split("esac")[0]))
        assert branches - {"GGML_NATIVE"} == set(NEVER_SET), "every option that downloads whenever it is set"
        targets = re.search(r"--target ((?:llama-[a-z]+ ?)+)", step).group(1).split()
        assert sorted(targets) == sorted(TARGETS["host-tools"]) and "llama-bench" in targets
        grep = re.search(r"grep -Ei '([^']+)'", step).group(1).lower()
        for marker in (m.replace("\\b", "") for m in DOWNLOAD_MARKERS.pattern.split("|")):
            assert marker.lower().replace("\\(", "(") in grep.replace("\\(", "("), marker

    @pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
    def test_the_source_is_fetched_by_commit(self, dockerfile):
        step = find_run(text(dockerfile), FETCH_MARKER)
        assert 'fetch -q --depth 1 origin "${LLAMA_CPP_COMMIT}"' in step
        assert '[ "${head}" = "${LLAMA_CPP_COMMIT}" ]' in step and 'rm -rf "${LLAMA_CPP_PATH}/.git"' in step
        assert "clone" not in step and "-b " not in step, "no clone of a branch or tag"

    def test_no_shipped_target_or_template_uses_prebuilt_llama_cpp(self):
        for folder in (REPO_ROOT / "targets").iterdir():
            if folder.is_dir():
                assert scan_target_folder(folder) == [], folder.name

    def test_the_gpu_template_takes_cuda_from_the_pinned_redist_archives(self):
        """Decision D6: CUDA from NVIDIA's redist archives pinned through the manifest; the prebuilt development image
        only as a labelled opt-in pinned by digest. Every value comes from configs/llama_runtime.yml."""
        gpu = text(TEMPLATE_GPU)
        values = defaults(gpu)
        toolkit = next(t for t in CATALOG.cuda.toolkits if t.version == values["CUDA_VERSION"])
        assert values["CUDA_REDIST_MANIFEST_SHA256"] == toolkit.manifest.sha256
        assert values["CUDA_REDIST_BASE_URL"] == CATALOG.cuda.redist_base_url
        assert values["CUDA_COMPONENTS"].split() == list(toolkit.components)
        assert values["CUDA_SOURCE"] == "redist"
        base = next(b for b in CATALOG.build_bases if b.id == "ubuntu-22.04")
        froms = [rest.split()[0] for word, rest in instructions(gpu) if word == "FROM"]
        assert froms == [base.image, base.image], "both stages on the pinned Ubuntu base, no nvidia/cuda image"
        opt_in = re.search(r"#\s+FROM (nvidia/cuda:\S+) AS builder", gpu).group(1)
        assert opt_in in {i.image for i in CATALOG.cuda.prebuilt_images if i.cuda == toolkit.version}
        assert 'LABEL org.llm-framework.cuda-source="${CUDA_SOURCE}"' in gpu


# --- the fetch step --------------------------------------------------------------------------------------------------

IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false", "-c", "init.defaultBranch=main"]


def git(*args, cwd):
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "HOME": str(cwd)}
    return subprocess.run(["git", *IDENTITY, *args], cwd=cwd, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A fake llama.cpp upstream: two commits, the tag b42 on the first."""
    up = tmp_path / "upstream"
    up.mkdir()
    git("init", "-q", cwd=up)
    (up / "CMakeLists.txt").write_text("project(fake C)\n")
    git("add", ".", cwd=up)
    git("commit", "-q", "-m", "one", cwd=up)
    first = git("rev-parse", "HEAD", cwd=up)
    git("tag", "b42", cwd=up)
    (up / "README.md").write_text("two\n")
    git("add", ".", cwd=up)
    git("commit", "-q", "-m", "two", cwd=up)
    git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=up)
    return {"url": up.as_uri(), "first": first, "second": git("rev-parse", "HEAD", cwd=up)}


def fetch_env(tmp_path, upstream, **values):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "LLAMA_CPP_PATH": str(tmp_path / "llama.cpp"),
           "LLAMA_CPP_REPO": upstream["url"], "LLAMA_CPP_TAG": "b42", "LLAMA_CPP_COMMIT": upstream["second"]}
    env.update(values)
    return env


@needs_sh
@needs_git
class TestFetchStep:

    def step(self):
        return find_run(text(ROCKCHIP), FETCH_MARKER)

    def test_the_pinned_commit_is_fetched_and_checked(self, tmp_path, upstream):
        result = run(self.step(), fetch_env(tmp_path, upstream))
        assert result.returncode == 0, result.stderr
        tree = tmp_path / "llama.cpp"
        assert (tree / "README.md").read_text() == "two\n" and not (tree / ".git").exists()
        assert (tree / ".llama_cpp_tag").read_text().strip() == "b42"
        assert (tree / ".llama_cpp_commit").read_text().strip() == upstream["second"]

    def test_a_tag_that_points_elsewhere_is_refused(self, tmp_path, upstream):
        """The commit is not upstream (here: unknown), the tag fallback gives another commit: the build stops."""
        result = run(self.step(), fetch_env(tmp_path, upstream, LLAMA_CPP_COMMIT="1" * 40))
        assert result.returncode != 0
        assert f"got commit {upstream['first']}, the pin is {'1' * 40}" in result.stderr

    @pytest.mark.parametrize("values, message", [
        ({"LLAMA_CPP_COMMIT": "main"}, "full commit id"),
        ({"LLAMA_CPP_COMMIT": "D81235049384534C167CAEA52B85A694F6103D14"}, "full commit id"),
        ({"LLAMA_CPP_TAG": "master"}, "build tag"),
        ({"LLAMA_CPP_TAG": "b1; touch pwned"}, "build tag"),
    ])
    def test_malformed_pins_are_refused_before_any_fetch(self, tmp_path, upstream, values, message):
        result = run(self.step(), fetch_env(tmp_path, upstream, **values))
        assert result.returncode != 0 and message in result.stderr
        assert not (tmp_path / "llama.cpp").exists() and not (tmp_path / "pwned").exists()


# --- the build step --------------------------------------------------------------------------------------------------

FAKE_CMAKE = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
if [ "$1" = "-S" ]; then
    mkdir -p "$4"
    printf '%s\\n' "${FAKE_CONFIGURE:--- Configuring done}"
    exit "${FAKE_CONFIGURE_EXIT:-0}"
fi
if [ "$1" = "--build" ]; then
    mkdir -p "$2/bin" "$2/CMakeFiles"
    printf '#!/bin/sh\\necho "%s" >&2\\n' "${FAKE_VERSION}" > "$2/bin/llama-bench"
    chmod +x "$2/bin/llama-bench"
fi
"""


@pytest.fixture
def fake_tools(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    cmake = bin_dir / "cmake"
    cmake.write_text(FAKE_CMAKE)
    cmake.chmod(cmake.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "llama.cpp").mkdir()
    return bin_dir


def build_env(tmp_path, fake_tools, **values):
    env = {"PATH": f"{fake_tools}:{os.environ.get('PATH', '')}", "HOME": str(tmp_path),
           "LLAMA_CPP_PATH": str(tmp_path / "llama.cpp"), "LLAMA_CPP_TAG": PIN.tag, "LLAMA_CPP_COMMIT": PIN.commit,
           "LLAMA_CMAKE_ARGS": defaults(text(ROCKCHIP))["LLAMA_CMAKE_ARGS"], "BUILD_JOBS": "2",
           "FAKE_LOG": str(tmp_path / "cmake.log"),
           "FAKE_VERSION": f"version: 0.6.0 (build {PIN.build_number}, commit {PIN.commit[:8]})"}
    env.update(values)
    return env


def calls(tmp_path):
    log = tmp_path / "cmake.log"
    return log.read_text().splitlines() if log.exists() else []


@needs_sh
class TestBuildStep:

    def step(self):
        return find_run(text(ROCKCHIP), BUILD_MARKER)

    def test_the_default_build_configures_builds_and_checks_the_version(self, tmp_path, fake_tools):
        result = run(self.step(), build_env(tmp_path, fake_tools))
        assert result.returncode == 0, result.stderr
        configure, build = calls(tmp_path)
        options = defines(configure)
        assert options["LLAMA_BUILD_NUMBER"] == str(PIN.build_number) and options["LLAMA_BUILD_COMMIT"] == PIN.commit[:8]
        assert options["GGML_AVX2"] == "ON" and options["LLAMA_SUBPROCESS"] == "OFF"
        assert build.split()[:2] == ["--build", str(tmp_path / "llama.cpp" / "build")] and "-j2" in build.split()
        assert "llama-bench" in build.split()
        assert not (tmp_path / "llama.cpp" / "build" / "CMakeFiles").exists()
        assert not (tmp_path / "llama.cpp" / "configure.log").exists()

    def test_the_options_every_build_has_win_over_the_arguments(self, tmp_path, fake_tools):
        args = "-DGGML_NATIVE=OFF -DLLAMA_SUBPROCESS=ON -DFETCHCONTENT_FULLY_DISCONNECTED=OFF -DLLAMA_BUILD_APP=ON"
        result = run(self.step(), build_env(tmp_path, fake_tools, LLAMA_CMAKE_ARGS=args))
        assert result.returncode == 0, result.stderr
        options = defines(calls(tmp_path)[0])  # CMake takes the last -D of a name
        assert options["LLAMA_SUBPROCESS"] == "OFF" and options["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON"
        assert options["LLAMA_BUILD_APP"] == "OFF"

    @pytest.mark.parametrize("args, message", [
        ("-DGGML_NATIVE=OFF $(touch pwned)", "refusing '$(touch'"),
        ("-DGGML_NATIVE=OFF;touch${IFS}pwned", "refusing"),
        ("-DGGML_NATIVE=OFF -DCMAKE_C_FLAGS='-O0'", "refusing"),
        ("-DGGML_NATIVE=OFF --trace", "refusing '--trace'"),
        ("-DGGML_NATIVE=OFF -DLLAMA_BUILD_BORINGSSL=ON", "LLAMA_BUILD_BORINGSSL downloads at configure time"),
        ("-DGGML_NATIVE=OFF -DGGML_CUDA_CCCL_VERSION=v3.4.3", "GGML_CUDA_CCCL_VERSION downloads"),
        ("-DGGML_AVX2=ON", "must set GGML_NATIVE"),
        ("", "must set GGML_NATIVE"),
    ])
    def test_unsafe_arguments_are_refused_before_configure(self, tmp_path, fake_tools, args, message):
        result = run(self.step(), build_env(tmp_path, fake_tools, LLAMA_CMAKE_ARGS=args), cwd=tmp_path)
        assert result.returncode != 0 and message in result.stderr, result.stderr
        assert calls(tmp_path) == [] and not (tmp_path / "pwned").exists()

    def test_a_configure_log_that_reports_a_download_stops_the_build(self, tmp_path, fake_tools):
        result = run(self.step(), build_env(tmp_path, fake_tools, FAKE_CONFIGURE="-- Downloading ui assets from x"))
        assert result.returncode != 0 and "reported a download" in result.stderr
        assert len(calls(tmp_path)) == 1, "never built"

    def test_a_failed_configure_shows_its_log(self, tmp_path, fake_tools):
        result = run(self.step(), build_env(tmp_path, fake_tools, FAKE_CONFIGURE="CMake Error: no compiler",
                                            FAKE_CONFIGURE_EXIT="1"))
        assert result.returncode != 0 and "CMake Error: no compiler" in result.stderr

    def test_tools_that_report_another_build_are_refused(self, tmp_path, fake_tools):
        result = run(self.step(), build_env(tmp_path, fake_tools, FAKE_VERSION="version: 0.5.0 (build 4009, commit "
                                                                               "deadbeef)"))
        assert result.returncode != 0 and f"expected build {PIN.build_number}, commit {PIN.commit[:8]}" in \
            result.stderr

    @pytest.mark.parametrize("probe", ["schema2_pc_two_nvidia.txt", "schema2_linux_ivybridge.txt",
                                       "schema2_windows_apu.txt"])
    def test_every_plan_of_the_recipe_passes_the_dockerfile_check(self, tmp_path, fake_tools, probe):
        """The framework's LLAMA_CMAKE_ARGS (explicit features, or all variants with $ORIGIN) pass the word check,
        and no word is expanded by the shell."""
        plan = plan_host_tools(read_profile(PROBES / probe), catalog=CATALOG, base_image="debian:bookworm-slim")
        args = cmake_args(plan)
        result = run(self.step(), build_env(tmp_path, fake_tools, LLAMA_CMAKE_ARGS=args))
        assert result.returncode == 0, result.stderr
        configure = calls(tmp_path)[0]
        assert args in configure
        if plan.options().get("CMAKE_INSTALL_RPATH"):
            assert "-DCMAKE_INSTALL_RPATH=$ORIGIN" in configure.split()


# --- the plan the framework passes -------------------------------------------------------------------------------

class TestHostToolsArguments:

    def pin(self):
        return {"tag": PIN.tag, "commit": PIN.commit}

    def test_the_plan_of_the_build_host_becomes_the_arguments(self):
        tools = host_tools(read_profile(PROBES / "schema2_linux_ivybridge.txt"), pin=self.pin(), catalog=CATALOG,
                           base_image="debian:bookworm-slim")
        args = tools.build_args()
        assert args["LLAMA_CPP_TAG"] == PIN.tag and args["LLAMA_CPP_COMMIT"] == PIN.commit
        words = args["LLAMA_CMAKE_ARGS"].split()
        assert "-DGGML_AVX=ON" in words and "-DGGML_AVX2=OFF" in words and "-DGGML_NATIVE=OFF" in words
        assert tools.tuning == "explicit" and "AVX" in tools.reason
        record = tools.record()
        assert record["plan_id"] == tools.plan.build_id and record["cmake_args"] == words

    def test_a_probe_that_could_not_tell_gets_all_variants(self):
        tools = host_tools(read_profile(PROBES / "schema2_windows_apu.txt"), pin=self.pin(), catalog=CATALOG)
        assert tools.tuning == "variants" and "-DGGML_CPU_ALL_VARIANTS=ON" in tools.build_args()["LLAMA_CMAKE_ARGS"]

    @pytest.mark.parametrize("profile, catalog, reason", [
        (None, CATALOG, "no hardware probe of this machine"),
        ("schema2_rk3566_dietpi.txt", None, "catalog could not be read"),
    ])
    def test_without_a_plan_the_dockerfile_baseline_stays(self, profile, catalog, reason):
        tools = host_tools(read_profile(PROBES / profile) if profile else None, pin=self.pin(), catalog=catalog)
        assert tools.tuning == "baseline" and reason in tools.reason
        assert set(tools.build_args()) == {"LLAMA_CPP_TAG", "LLAMA_CPP_COMMIT"}

    def test_another_commit_in_the_catalog_is_not_planned(self):
        tools = host_tools(read_profile(PROBES / "schema2_pc_two_nvidia.txt"),
                           pin={"tag": PIN.tag, "commit": "0" * 40}, catalog=CATALOG)
        assert tools.tuning == "baseline" and "another llama.cpp commit" in tools.reason

    @pytest.mark.parametrize("pin", [{"tag": "master", "commit": PIN.commit}, {"tag": PIN.tag, "commit": "main"}])
    def test_a_malformed_pin_is_refused(self, pin):
        with pytest.raises(PlanError):
            host_tools(None, pin=pin, catalog=CATALOG)


# --- the CUDA step of the GPU template ---------------------------------------------------------------------------

def _xz_archive(top: str, files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for rel, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def redist(tmp_path):
    """Fake redist archives of the template's CUDA components and their manifest, for this machine's platform."""
    platform = {"x86_64": "linux-x86_64", "aarch64": "linux-sbsa"}.get(os.uname().machine, "linux-x86_64")
    root = tmp_path / "redist"
    root.mkdir()
    contents = {"cuda_nvcc": {"bin/nvcc": "#!/bin/sh\necho nvcc\n"},
                "cuda_cudart": {"lib/libcudart.so.12": "cudart"},
                "cuda_cccl": {"include/cuda/std/version": "cccl"},
                "libcublas": {"lib/libcublas.so.12": "cublas", "lib/libcublasLt.so.12": "cublaslt"}}
    manifest = {"release_label": "fake"}
    for component, files in contents.items():
        rel = f"{component}/{platform}/{component}-{platform}-1.0-archive.tar.xz"
        data = _xz_archive(f"{component}-{platform}-1.0-archive", files)
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_bytes(data)
        manifest[component] = {platform: {"relative_path": rel, "sha256": hashlib.sha256(data).hexdigest()}}
    (root / "redistrib_9.9.9.json").write_text(json.dumps(manifest))
    return {"root": root, "manifest": manifest, "platform": platform}


def cuda_env(tmp_path, redist, **values):
    data = (redist["root"] / "redistrib_9.9.9.json").read_bytes()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "CUDA_SOURCE": "redist", "CUDA_VERSION": "9.9.9",
           "CUDA_REDIST_BASE_URL": redist["root"].as_uri() + "/",
           "CUDA_REDIST_MANIFEST_SHA256": hashlib.sha256(data).hexdigest(),
           "CUDA_COMPONENTS": defaults(text(TEMPLATE_GPU))["CUDA_COMPONENTS"],
           "CUDA_HOME": str(tmp_path / "cuda"), "CUDA_RUNTIME_DIR": str(tmp_path / "cuda-runtime")}
    env.update(values)
    return env


needs_cuda_tools = pytest.mark.skipif(any(shutil.which(t) is None for t in ("curl", "jq", "xz", "tar", "sha256sum")),
                                      reason="needs curl, jq, xz, tar and sha256sum")


@needs_sh
@needs_cuda_tools
class TestCudaStep:

    def step(self):
        return find_run(text(TEMPLATE_GPU), "CUDA_REDIST_MANIFEST_SHA256")

    def test_the_components_are_checked_and_unpacked(self, tmp_path, redist):
        result = run(self.step(), cuda_env(tmp_path, redist))
        assert result.returncode == 0, result.stderr
        cuda = tmp_path / "cuda"
        assert (cuda / "bin" / "nvcc").is_file() and (cuda / "lib64" / "libcudart.so.12").is_file()
        assert sorted(p.name for p in (tmp_path / "cuda-runtime" / "lib").iterdir()) == [
            "libcublas.so.12", "libcublasLt.so.12", "libcudart.so.12"]
        assert "checked against the pinned manifest" in result.stdout

    def test_a_manifest_that_is_not_the_pinned_one_is_refused(self, tmp_path, redist):
        result = run(self.step(), cuda_env(tmp_path, redist, CUDA_REDIST_MANIFEST_SHA256="0" * 64))
        assert result.returncode != 0 and not (tmp_path / "cuda" / "bin").exists()

    def test_a_changed_archive_is_refused(self, tmp_path, redist):
        rel = redist["manifest"]["libcublas"][redist["platform"]]["relative_path"]
        (redist["root"] / rel).write_bytes(_xz_archive("x", {"lib/libcublas.so.12": "tampered"}))
        result = run(self.step(), cuda_env(tmp_path, redist))
        assert result.returncode != 0 and not (tmp_path / "cuda" / "lib" / "libcublas.so.12").exists()

    @pytest.mark.parametrize("path", ["../outside.tar.xz", "/etc/x.tar.xz", "cuda_nvcc/x.zip"])
    def test_an_unexpected_archive_path_is_refused(self, tmp_path, redist, path):
        manifest = dict(redist["manifest"])
        manifest["cuda_nvcc"] = {redist["platform"]: {"relative_path": path, "sha256": "a" * 64}}
        (redist["root"] / "redistrib_9.9.9.json").write_text(json.dumps(manifest))
        result = run(self.step(), cuda_env(tmp_path, redist))
        assert result.returncode != 0 and "unexpected archive path" in result.stderr

    def test_the_prebuilt_image_needs_its_toolkit_and_nothing_else_is_accepted(self, tmp_path, redist):
        result = run(self.step(), cuda_env(tmp_path, redist, CUDA_SOURCE="prebuilt-image"))
        assert result.returncode != 0 and "needs an NVIDIA development image" in result.stderr
        result = run(self.step(), cuda_env(tmp_path, redist, CUDA_SOURCE="anything"))
        assert result.returncode != 0 and "must be redist or prebuilt-image" in result.stderr
