"""The bundle's components built by the framework (ROADMAP rows "The bundle, its further components cross-compiled in
their own steps", "Voice with whisper.cpp", "Vision with --mmproj"):

- whisper.cpp through the one self-build path of llama.cpp (runtime/build/projects.py: the pin, the recipe's CPU plan
  of the board's probe, the store, the board package, steps.build_runtime) - llama.cpp's plans stay what they were;
- stt-whisper-cpp takes the framework's whisper.cpp package, a picked program is the explicit block with its warning,
  whisper-server's own options are held to its policy;
- vision-mmproj: --mmproj in the start script's checked options, the model/projector pair check, the memory need;
- licence terms of a block shown and accepted before anything of it is built, opt-in blocks never in a preset;
- one check per component against the probe facts; the CLI and the window offer to build a missing package.

No network: builds are stubs (no container, no compiler), sources are local folders or a git repository in tmp_path.
Every name and address is a test value."""

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

import pytest

from test_board_bundle import DEVICE, RK3566, _elf
from test_device_runtime import HOST, LICENCE, FakeDeviceExecutor, FormatOnlyVerifier

from orchestrator.runtime.build import projects
from orchestrator.runtime.build import recipe
from orchestrator.runtime.build.store import BuildStore
from orchestrator.runtime.bundle import blocks as blk
from orchestrator.runtime.bundle import generator as gen
from orchestrator.runtime.bundle import service
from orchestrator.runtime.catalog import WhisperCppSource, load_catalog
from orchestrator.runtime.device.package import (DeviceRuntimeStore, package_runtime, plan_device_runtime,
                                                 start_script_current)

CATALOG = load_catalog()
WHISPER = "whisper.cpp"


def whisper_plan(profile=RK3566):
    return plan_device_runtime(profile, catalog=CATALOG, host=HOST, project=WHISPER)


def llama_plan(profile=RK3566):
    return plan_device_runtime(profile, catalog=CATALOG, host=HOST)


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text("project(whisper.cpp)\n")
    (src / "LICENSE").write_bytes(LICENCE.encode("utf-8"))
    return src


def stored_whisper(state, source, profile=RK3566):
    store = BuildStore(state, WHISPER)
    build = store.build(whisper_plan(profile), source=source, executor=FakeDeviceExecutor(),
                        verifier=FormatOnlyVerifier(), jobs=1, host=HOST, perf_key=profile.perf_key())
    return store, build


# --- 1. one self-build path, two projects ------------------------------------------------------------------------------

class TestOneSelfBuildPath:

    def test_llama_cpp_plans_are_what_they_were(self):
        """Characterization: a plan without a project is llama.cpp's, its source and build ID name no project."""
        plan = llama_plan()
        assert plan_device_runtime(RK3566, catalog=CATALOG, host=HOST, project="llama.cpp") == plan
        assert "project" not in plan.source and "project" not in plan.id_inputs()["source"]
        assert projects.project_of(plan).name == "llama.cpp"
        assert plan.targets == recipe.TARGETS["device-runtime"]
        assert plan.options()["LLAMA_SUBPROCESS"] == "OFF"

    def test_whisper_cpp_is_planned_from_the_same_probe_with_its_own_pin(self):
        plan, llama = whisper_plan(), llama_plan()
        pin = CATALOG.whisper_cpp.source
        assert isinstance(pin, WhisperCppSource) and pin.project == WHISPER
        assert plan.source == {"repo": pin.repo, "commit": pin.commit, "tag": pin.tag, "project": WHISPER}
        assert plan.id_inputs()["source"]["project"] == WHISPER and plan.build_id != llama.build_id
        assert plan.targets == ("whisper-server", "whisper-cli", "whisper-bench")
        options = plan.options()
        assert not any(k.startswith("LLAMA_") for k in options)
        assert {k: options[k] for k in recipe.WHISPER_DOWNLOAD_OPTIONS_OFF} == \
            {k: "OFF" for k in recipe.WHISPER_DOWNLOAD_OPTIONS_OFF}
        assert options["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON" and options["WHISPER_BUILD_SERVER"] == "ON"
        # the same CPU plan of the board's probe as llama.cpp's (ggml is the same library in both)
        for key in ("GGML_CPU_ARM_ARCH", "GGML_NATIVE", "GGML_OPENMP", "CMAKE_EXE_LINKER_FLAGS"):
            assert options[key] == llama.options()[key]
        assert plan.cpu_features == llama.cpu_features and plan.target == llama.target
        assert plan.builder == llama.builder

    def test_whisper_cpp_is_built_for_a_board_only(self):
        with pytest.raises(recipe.PlanError, match="board only"):
            recipe.plan_build(RK3566, purpose="host-runtime", placement="native", catalog=CATALOG,
                              toolchain=recipe.ToolchainInfo(host_os="linux", host_arch="arm64"), project=WHISPER)
        with pytest.raises(recipe.PlanError):
            plan_device_runtime(RK3566, catalog=CATALOG, host=HOST, project="nope")

    @pytest.mark.parametrize("name", ["WHISPER_CURL", "WHISPER_SDL2", "GGML_CPU_KLEIDIAI", "GGML_OPENMP_FETCH"])
    def test_a_download_option_of_whisper_cpp_is_refused(self, name):
        options = whisper_plan().options()
        options[name] = "ON"
        with pytest.raises(recipe.PlanError, match=name):
            recipe.check_download_options(options, project=WHISPER)
        recipe.check_download_options(whisper_plan().options(), project=WHISPER)   # the plan itself passes

    def test_each_project_keeps_its_source_store_and_packages_in_its_own_folder(self, tmp_path, source):
        from orchestrator.runtime.build.source import source_dir
        commit = CATALOG.whisper_cpp.source.commit
        assert source_dir(tmp_path, commit, WHISPER).parent == tmp_path / "runtime" / "whisper.cpp" / "src"
        assert source_dir(tmp_path, commit).parent == tmp_path / "runtime" / "llama.cpp" / "src"
        store, build = stored_whisper(tmp_path / "state", source)
        assert store.root == tmp_path / "state" / "runtime" / "whisper.cpp"
        assert BuildStore(tmp_path / "state").get(build.build_id) is None, "never in llama.cpp's store"
        devices = DeviceRuntimeStore(tmp_path / "state", DEVICE, WHISPER)
        assert devices.root == tmp_path / "state" / "runtime" / "whisper.cpp" / "device" / DEVICE

    def test_the_board_package_holds_the_programs_its_licence_and_no_llama_start_script(self, tmp_path, source):
        store, build = stored_whisper(tmp_path / "state", source)
        devices = DeviceRuntimeStore(tmp_path / "state", DEVICE, WHISPER)
        packaged = package_runtime(store, build, licence=source / "LICENSE", out_dir=devices.root)
        assert packaged.path.name == f"whisper-runtime-arm64-{build.build_id[:16]}.tar.gz"
        top = build.build_id[:16]
        assert f"{top}/bin/whisper-server" in packaged.members and f"{top}/LICENSE-whisper.cpp" in packaged.members
        assert f"{top}/llama-server.sh" not in packaged.members and f"{top}/SHA256SUMS" in packaged.members
        assert devices.tarball(build).sha256 == packaged.sha256 and devices.tarballs() == [packaged.path]
        assert start_script_current(packaged.path) is None, "no start script: never repackaged for one"

    @pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
    def test_the_pinned_source_is_fetched_by_commit_into_its_own_cache(self, tmp_path):
        from test_build_source import run_git
        from orchestrator.runtime.build.source import cached_source, fetch_source
        up = tmp_path / "upstream"
        up.mkdir()
        run_git("init", "-q", cwd=up)
        (up / "CMakeLists.txt").write_text("project(whisper C)\n")
        run_git("add", ".", cwd=up)
        run_git("commit", "-q", "-m", "one", cwd=up)
        run_git("tag", "v9.9.9", cwd=up)
        commit = run_git("rev-parse", "HEAD", cwd=up)
        pin = WhisperCppSource(repo="https://example.invalid/whisper.cpp.git", commit=commit, tag="v9.9.9")
        said = []
        path = fetch_source(pin, tmp_path / "state", url=up.as_uri(), log=said.append)
        assert path.parent == tmp_path / "state" / "runtime" / "whisper.cpp" / "src"
        assert any("whisper.cpp v9.9.9" in s for s in said)
        assert cached_source(pin, tmp_path / "state") == path


# --- 2. the board's whisper.cpp through the bundle -----------------------------------------------------------------------

@pytest.fixture
def stubbed_build(monkeypatch, source):
    """steps.build_runtime with its builder container, compiler and checks stubbed (the source cached)."""
    import orchestrator.runtime.build.executor as executor_module
    import orchestrator.runtime.build.source as source_module
    import orchestrator.runtime.build.verify as verify_module
    from orchestrator.runtime.build.executor import BuildExecutor
    built = []
    monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
    monkeypatch.setattr(source_module, "cached_source", lambda pin, state, **kw: source)
    monkeypatch.setattr(executor_module, "DockerEngine", lambda: None)
    monkeypatch.setattr(BuildExecutor, "build", lambda self, plan, **kw: (
        built.append(projects.project_of(plan).name) or FakeDeviceExecutor().build(plan, **kw)))
    monkeypatch.setattr(verify_module, "Verifier", FormatOnlyVerifier)
    return built


def _stored(state: Path):
    from orchestrator.runtime.profile import ProfileStore
    ProfileStore(state).save_device(DEVICE, RK3566, label="ZERO 3W", address="192.0.2.10")


def _whisper_model(tmp_path: Path) -> Path:
    path = tmp_path / "ggml-base.bin"
    path.write_bytes(b"lmgg" + b"\1" * (2 * 1024 * 1024))
    return path


def _stt_selection(tmp_path: Path, **kw) -> gen.Selection:
    return gen.Selection(blocks=["stt-whisper-cpp"], files={"stt": {"model": gen.FileChoice(
        path=str(_whisper_model(tmp_path)), licence="MIT")}}, **kw)


class TestWhisperInTheBundle:

    def test_without_the_board_package_the_plan_names_it_for_the_person_to_build(self, tmp_path):
        _stored(tmp_path)
        plan = service.plan_for_device(tmp_path, DEVICE, _stt_selection(tmp_path))
        assert plan.missing_runtimes == ["whisper.cpp"] and not plan.ok
        assert any("whisper.cpp package is not" in p and "after your yes" in p for p in plan.problems)
        labels = plan.labels()
        assert labels["missing_runtimes"] == ["whisper.cpp"] and str(tmp_path) not in json.dumps(labels)

    def test_the_framework_builds_it_through_the_one_path_and_the_block_takes_it(self, tmp_path, stubbed_build):
        _stored(tmp_path)
        said = []
        packages = service.build_components(tmp_path, DEVICE, RK3566, ["whisper.cpp"], say=said.append)
        pkg = packages["whisper.cpp"]
        assert stubbed_build == ["whisper.cpp"] and pkg.path is not None and pkg.project == "whisper.cpp"
        assert pkg.arch == "arm64" and pkg.cpu_features == tuple(whisper_plan().cpu_features["used"])
        assert any("building whisper.cpp" in s for s in said)
        plan = service.plan_for_device(tmp_path, DEVICE, _stt_selection(tmp_path))
        assert plan.ok, plan.problems
        [comp] = plan.manifest.components
        assert comp.start.program == f"runtime/{pkg.build_id[:16]}/bin/whisper-server"
        assert comp.start.args[:2] == ("--host", "127.0.0.1")
        assert {f.role for f in comp.files} == {"runtime", "model"}
        check = next(c for c in plan.checks if c["check"] == "component stt")
        assert check["result"] == "passed" and "whisper.cpp built for arm64" in check["detail"]
        assert all(f in check["detail"] for f in pkg.cpu_features)
        # built once: a second build reuses the stored build and its package
        service.build_components(tmp_path, DEVICE, RK3566, ["whisper.cpp"])
        assert stubbed_build == ["whisper.cpp"]

    def test_a_package_for_another_architecture_fails_the_components_check(self, tmp_path):
        pkg = tmp_path / "pkg.tar.gz"
        pkg.write_bytes(b"\x1f\x8b" + b"\0" * 64)
        runtime = gen.RuntimePackage(pkg, hashlib.sha256(pkg.read_bytes()).hexdigest(), "a" * 64,
                                     project="whisper.cpp", arch="x86_64")
        plan = gen.plan_bundle(_stt_selection(tmp_path), catalog=blk.load_catalog(), profile=RK3566,
                               device_id=DEVICE, offline=False, bundle_id="stt-test",
                               runtimes={"whisper.cpp": runtime})
        assert any("built for x86_64, the board runs arm64" in p for p in plan.problems)
        assert next(c for c in plan.checks if c["check"] == "component stt")["result"] == "failed"

    def test_a_picked_program_is_the_explicit_block_with_its_warning(self, tmp_path):
        prog = tmp_path / "whisper-server"
        prog.write_bytes(_elf(0xB7))
        selection = gen.Selection(blocks=["stt-whisper-cpp-program"], files={"stt": {
            "model": gen.FileChoice(path=str(_whisper_model(tmp_path)), licence="MIT"),
            "program": gen.FileChoice(path=str(prog))}})
        plan = gen.plan_bundle(selection, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE,
                               offline=False, bundle_id="stt-test")
        assert plan.ok, plan.problems
        assert any("program is one you picked" in w for w in plan.warnings)
        assert "voice" in gen.PRESETS and "stt-whisper-cpp-program" not in gen.PRESETS["voice"]
        assert gen.PRESETS["stt"] == ("stt-whisper-cpp",)

    @pytest.mark.parametrize("args, bad", [
        (["--host", "127.0.0.1", "--convert"], "--convert"),
        (["--host", "127.0.0.1", "--public", "x"], "--public"),
        (["--host", "127.0.0.1", "--tmp_dir=x"], "--tmp-dir"),
        (["--host", "0.0.0.0"], "127.0.0.1"),
        (["--port", "8090"], "127.0.0.1"),
        (["--host", "127.0.0.1", "--host", "127.0.0.1"], "exactly one"),
    ])
    def test_whisper_server_keeps_to_its_policy(self, args, bad):
        from orchestrator.runtime.server_policy import PolicyError, check_whisper_command
        with pytest.raises(PolicyError, match=bad):
            check_whisper_command(args)
        check_whisper_command(["--host", "127.0.0.1", "--port", "8090", "-m", "x"])

    def test_a_block_that_gives_whisper_server_a_forbidden_option_is_refused(self, tmp_path):
        text = (blk.BUILTIN_DIR / "stt-whisper-cpp-program" / "block.yml").read_text()
        text = text.replace("id: stt-whisper-cpp-program", "id: stt-conv").replace("    - --host\n",
                                                                                    "    - --convert\n    - --host\n")
        folder = tmp_path / "community" / "blocks" / "stt-conv"
        folder.mkdir(parents=True)
        (folder / "block.yml").write_text(text)
        (folder / "README.md").write_text("# x\n\n## Models\n\nA whisper model.\n")
        prog = tmp_path / "whisper-server"
        prog.write_bytes(_elf(0xB7))
        selection = gen.Selection(blocks=["stt-conv"], files={"stt": {
            "model": gen.FileChoice(path=str(_whisper_model(tmp_path)), licence="MIT"),
            "program": gen.FileChoice(path=str(prog))}})
        plan = gen.plan_bundle(selection, catalog=blk.load_catalog(tmp_path), profile=RK3566, device_id=DEVICE,
                               offline=False, bundle_id="stt-test", allow_own=True)
        assert any("--convert" in p for p in plan.problems), plan.problems


# --- 3. vision with --mmproj ------------------------------------------------------------------------------------------------

def _kv_string(key: str, value: str):
    data = value.encode()
    return key, 8, struct.pack("<Q", len(data)) + data


def _gguf(path: Path, arch: str, kv=(), size: int = 2 * 1024 * 1024) -> Path:
    from model_files import gguf_bytes
    data = gguf_bytes(arch=arch, kv=kv)
    path.write_bytes(data + b"\0" * max(0, size - len(data)))
    return path


def _vision_files(tmp_path: Path, *, model_base="SmolVLM", proj_base="SmolVLM", width=576, proj=576,
                  model_arch="llama", proj_arch="clip", vision=True):
    model = _gguf(tmp_path / "vlm.gguf", model_arch, [_kv_string("general.basename", model_base),
                                                     (f"{model_arch}.embedding_length", 4, struct.pack("<I", width))])
    mm = _gguf(tmp_path / "mmproj.gguf", proj_arch, [_kv_string("general.basename", proj_base),
                                                    ("clip.has_vision_encoder", 7, struct.pack("<?", vision)),
                                                    ("clip.vision.projection_dim", 4, struct.pack("<I", proj))])
    return model, mm


def _runtime_package(tmp_path: Path, start_script: bytes) -> gen.RuntimePackage:
    top = "fedcba9876543210"
    files = {f"{top}/llama-server.sh": start_script, f"{top}/bin/llama-server": b"#!/bin/sh\n"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    path = tmp_path / "runtime.tar.gz"
    path.write_bytes(buf.getvalue())
    return gen.RuntimePackage(path, hashlib.sha256(buf.getvalue()).hexdigest(), top + "0" * 48, arch="arm64")


def _vision_plan(tmp_path, runtime=None, **files):
    from orchestrator.runtime.device.launcher import render_start_script
    model, mm = _vision_files(tmp_path, **files)
    selection = gen.Selection(blocks=["vision-mmproj"], files={"vision": {
        "model": gen.FileChoice(path=str(model), licence="apache-2.0"),
        "mmproj": gen.FileChoice(path=str(mm), licence="apache-2.0")}})
    runtime = runtime or _runtime_package(tmp_path, render_start_script().encode())
    return gen.plan_bundle(selection, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="vision-test", runtime=runtime)


class TestVision:

    def test_the_start_script_passes_a_readable_projector_file_on_and_nothing_else(self, tmp_path):
        from orchestrator.runtime import server_policy
        from orchestrator.runtime.device.launcher import render_start_script
        allowed = {n: kind for names, kind in server_policy.START_SCRIPT_OPTIONS.items() for n in names}
        assert allowed["--mmproj"] == allowed["-mm"] == "file"
        assert "--mmproj-url" in server_policy.FORBIDDEN_FLAGS and "-mmu" in server_policy.FORBIDDEN_FLAGS
        script = render_start_script()
        assert "--mmproj|-mm) kind=file ;;" in script
        if os.name != "posix" or shutil.which("sh") is None:
            return
        folder = tmp_path / "rt"
        (folder / "bin").mkdir(parents=True)
        (folder / "llama-server.sh").write_bytes(script.encode())
        (folder / "bin" / "llama-server").write_bytes(b"#!/bin/sh\nexit 0\n")
        (folder / "bin" / "llama-server").chmod(0o755)
        model = tmp_path / "m.gguf"
        model.write_bytes(b"GGUF")
        env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

        def start(*args):
            # start runs the check of every argument first and refuses before it starts anything
            return subprocess.run(["sh", str(folder / "llama-server.sh"), "start", str(model), *args], env=env,
                                  capture_output=True, text=True, timeout=30)
        bad = start("--mmproj", str(tmp_path / "missing.gguf"))
        assert bad.returncode == 2 and "not a valid value for --mmproj" in bad.stderr
        assert start("--mmproj", "relative.gguf").returncode == 2

    def test_a_model_and_its_projector_are_a_pair(self, tmp_path):
        plan = _vision_plan(tmp_path)
        assert plan.ok, plan.problems
        [comp] = plan.manifest.components
        assert "--mmproj" in comp.start.args and "@DIR@/mmproj.gguf" in comp.start.args and comp.keygen
        assert next(c for c in plan.checks if c["check"] == "vision pair vision")["result"] == "passed"
        both = sum(f.size for f in comp.files if f.role in ("model", "mmproj"))
        assert plan.memory_mb == int(384 + 1.3 * both / 1048576)

    @pytest.mark.parametrize("change, expect", [
        ({"proj_base": "Qwen2-VL"}, "different bases"),
        ({"proj_arch": "llama"}, "no multimodal projector"),
        ({"vision": False}, "no multimodal projector"),
        ({"model_arch": "clip"}, "no language model"),
    ])
    def test_a_pair_that_is_none_is_refused(self, tmp_path, change, expect):
        plan = _vision_plan(tmp_path, **change)
        assert not plan.ok and any(expect in p for p in plan.problems), plan.problems

    def test_a_projector_of_another_width_is_a_warning(self, tmp_path):
        plan = _vision_plan(tmp_path, proj=2048)
        assert plan.ok and any("width (2048)" in w for w in plan.warnings)

    def test_a_runtime_package_older_than_the_mmproj_option_is_named(self, tmp_path):
        old = _runtime_package(tmp_path, b"#!/bin/sh\ncase $opt in\n      --ctx-size|-c) kind=uint ;;\nesac\n")
        plan = _vision_plan(tmp_path, runtime=old)
        assert any("before its start script took --mmproj" in p for p in plan.problems)

    def test_an_older_package_is_packaged_again_when_the_source_is_cached(self, tmp_path, monkeypatch):
        from orchestrator.runtime.device import steps
        from test_device_runtime import stored_build
        src = tmp_path / "src"
        src.mkdir()
        (src / "LICENSE").write_bytes(LICENCE.encode())
        store, build = stored_build(tmp_path / "state", src)
        devices = DeviceRuntimeStore(tmp_path / "state", DEVICE)
        first = package_runtime(store, build, licence=src / "LICENSE", out_dir=devices.root)
        monkeypatch.setattr("orchestrator.runtime.device.package.render_start_script", lambda: "#!/bin/sh\nnew\n")
        assert start_script_current(first.path) is False
        monkeypatch.setattr("orchestrator.runtime.build.source.cached_source", lambda pin, state, **kw: src)
        _devices, again = steps.ensure_package(tmp_path / "state", DEVICE, store, build, lambda: None)
        assert again.sha256 != first.sha256 and start_script_current(again.path) is True
        monkeypatch.setattr("orchestrator.runtime.device.package.render_start_script", lambda: "#!/bin/sh\nnewer\n")
        monkeypatch.setattr("orchestrator.runtime.build.source.cached_source", lambda pin, state, **kw: None)
        _devices, kept = steps.ensure_package(tmp_path / "state", DEVICE, store, build, lambda: None)
        assert kept.sha256 == again.sha256, "without the source the older package still serves"

    def test_a_damaged_header_is_a_problem_not_a_crash(self, tmp_path):
        model, mm = _vision_files(tmp_path)
        mm.write_bytes(b"GGUF" + b"\xff" * 64)
        with pytest.raises(gen.BundleError):
            gen.gguf_header(mm)


# --- 4. licence terms and opt-in blocks --------------------------------------------------------------------------------------

TERMS_BLOCK = """schema: llmcf-bundle-block/1
id: tts-optin
version: 1.0.0
kind: tts
title: An opt-in voice
status: ready
files:
  - {role: program, from: person, name: tts-server, mode: "0755", formats: [elf]}
start:
  program: tts-server
  args: [--port, "8095"]
terms:
  - part: The voice model family
    licence: other
    free: false
    text: These voices may be used for private, non-commercial purposes only.
"""


def _terms_block(app: Path, text: str = TERMS_BLOCK) -> Path:
    folder = app / "community" / "blocks" / "tts-optin"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "block.yml").write_text(text)
    (folder / "README.md").write_text("# Opt-in voice\n\n## Models\n\nA voice model under its own terms.\n")
    return folder


class TestLicenceTerms:

    def _plan(self, tmp_path, accepted=None):
        prog = tmp_path / "tts-server"
        prog.write_bytes(_elf(0xB7))
        selection = gen.Selection(blocks=["tts-optin"], files={"tts": {"program": gen.FileChoice(path=str(prog))}},
                                  accepted_terms=dict(accepted or {}))
        return gen.plan_bundle(selection, catalog=blk.load_catalog(tmp_path / "app"), profile=RK3566,
                               device_id=DEVICE, offline=False, bundle_id="tts-test", allow_own=True)

    def test_nothing_is_planned_before_the_person_accepted_the_terms(self, tmp_path):
        _terms_block(tmp_path / "app")
        plan = self._plan(tmp_path)
        assert not plan.ok and plan.terms_needed == ["tts-optin"]
        assert any("own licence terms (other, not all free licences)" in p for p in plan.problems)
        labels = plan.labels()
        assert labels["terms_needed"] == ["tts-optin"] and "non-commercial" not in json.dumps(labels)
        block = blk.load_catalog(tmp_path / "app").get("tts-optin")
        assert block.opt_in and "non-commercial purposes only" in block.terms_text()
        assert block.label()["terms"] == [{"part": "The voice model family", "licence": "other", "free": False}]

    def test_the_acceptance_holds_for_exactly_the_terms_shown(self, tmp_path):
        _terms_block(tmp_path / "app")
        catalog = blk.load_catalog(tmp_path / "app")
        selection = gen.Selection(blocks=["tts-optin"])
        assert service.accept_terms(catalog, selection, ["tts-optin", "llm-llama-server"]) == ["tts-optin"]
        plan = self._plan(tmp_path, selection.accepted_terms)
        assert plan.ok, plan.problems
        _terms_block(tmp_path / "app", TERMS_BLOCK.replace("private,", "private or shared,"))
        plan = self._plan(tmp_path, selection.accepted_terms)
        assert plan.terms_needed == ["tts-optin"], "changed terms need a new yes"

    def test_opt_in_blocks_are_never_part_of_a_preset_and_the_framework_ships_them_as_templates(self):
        catalog = blk.load_catalog()
        opt_in = {b.id for b in catalog.blocks.values() if b.opt_in}
        assert {"stt-nemo-sherpa", "stt-kws-gigaspeech"} <= opt_in
        assert not opt_in & {b for blocks in gen.PRESETS.values() for b in blocks}
        tts = catalog.get("tts-sherpa-onnx")
        assert {t.licence for t in tts.terms} == {"MIT", "GPL-3.0-or-later"} and not tts.opt_in
        for block_id in ("stt-nemo-sherpa", "stt-kws-gigaspeech", "tts-sherpa-onnx"):
            assert not catalog.get(block_id).ready and blk.check_block_folder(catalog.get(block_id).folder) == []

    @pytest.mark.parametrize("bad", [
        "terms: [{part: x, licence: 'a b', free: true, text: t}]",
        "terms: [{part: x, licence: MIT, free: maybe, text: t}]",
        "terms: [{part: x, licence: MIT, free: true}]",
        "terms: [{part: x, licence: MIT, free: true, text: ''}]",
    ])
    def test_terms_are_checked_like_every_field(self, tmp_path, bad):
        text = TERMS_BLOCK.split("terms:")[0] + bad + "\n"
        with pytest.raises(blk.BlockError):
            blk.parse_block(text, tmp_path)


# --- 5. the CLI offers what the plan waits for ---------------------------------------------------------------------------

class TestCli:

    @pytest.fixture
    def cli_state(self, tmp_path, monkeypatch):
        from rich.console import Console
        from orchestrator import cli as cli_module
        state = tmp_path / "state"
        _stored(state)
        monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: state)
        monkeypatch.setattr(cli_module, "console", Console(width=1000, color_system=None))
        return state

    def test_plan_names_the_missing_package_and_build_asks_before_it_builds(self, tmp_path, cli_state, monkeypatch):
        from test_cli_builds import invoke_plain
        from orchestrator import cli as cli_module
        model = _whisper_model(tmp_path)
        choice = ["--preset", "stt", "--file", f"stt.model={model}", "--licence", "stt.model=MIT"]
        res = invoke_plain("bundle", "plan", "ZERO 3W", *choice)
        assert res.code == 1 and "whisper.cpp package is not" in res.out
        built = []
        monkeypatch.setattr(service, "build_components", lambda *a, **kw: built.append(a[3]) or {})
        res = invoke_plain("bundle", "build", "ZERO 3W", *choice)
        assert res.code == 1 and built == [], "not at a terminal: nothing is asked, nothing built"
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        res = invoke_plain("bundle", "build", "ZERO 3W", *choice, input="n\n")
        assert built == [] and "Build whisper.cpp for board ZERO 3W now?" in res.out and "Nothing was built" in res.out
        res = invoke_plain("bundle", "build", "ZERO 3W", *choice, input="y\n")
        assert built == [["whisper.cpp"]]

    def test_build_shows_the_terms_and_asks_with_no_as_the_default(self, tmp_path, cli_state, monkeypatch):
        from test_cli_builds import invoke_plain
        from orchestrator import cli as cli_module
        app = tmp_path / "app"
        _terms_block(app)
        monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, None))
        prog = tmp_path / "tts-server"
        prog.write_bytes(_elf(0xB7))
        choice = ["--block", "tts-optin", "--file", f"tts.program={prog}", "--allow-own"]
        res = invoke_plain("bundle", "plan", "ZERO 3W", *choice[:4])
        assert res.code == 1 and "non-commercial purposes only" in res.out
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        res = invoke_plain("bundle", "build", "ZERO 3W", *choice, input="\n")
        assert "Accept the licence terms of block tts-optin" in res.out and "Nothing was built" in res.out
        res = invoke_plain("bundle", "plan", "ZERO 3W", *choice[:4], "--accept-terms", "tts-optin", "--json")
        assert json.loads(res.stdout)["terms_needed"] == []
