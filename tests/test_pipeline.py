"""Host side of the build pipeline: model staging, container environment, orchestration (no Docker needed)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.builder import (BuildConfiguration, BuildEngine, BuildProgress, BuildStatus,
                                       ModelFormat, OptimizationLevel)
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.utils.llama_pin import pinned_tag


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env", lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    return BuildEngine(ConfigManager(tmp_path / "configs"))


def make_config(**overrides):
    values = dict(build_id="b1", timestamp="t", model_source="HuggingFaceTB/SmolLM2-135M-Instruct",
                  target_arch="Rockchip", target_format=ModelFormat.GGUF, output_dir="out",
                  quantization="Q4_K_M", target_board="rk3566")
    values.update(overrides)
    return BuildConfiguration(**values)


class TestModelStaging:

    def test_local_directory_is_mounted_read_only(self, engine, tmp_path):
        model = tmp_path / "my-model"
        model.mkdir()
        path, volumes = engine._stage_model(make_config(model_source=str(model)), BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert path == "/build-cache/input-model"
        assert volumes == {str(model.resolve()): {"bind": "/build-cache/input-model", "mode": "ro"}}

    def test_local_file_is_mounted_alone(self, engine, tmp_path):
        # Only the file: its folder may hold anything else (design G8; this test pinned the parent mount before).
        model = tmp_path / "model.onnx"
        model.write_bytes(b"onnx")
        (tmp_path / "id_ed25519").write_text("private key")
        path, volumes = engine._stage_model(make_config(model_source=str(model)), BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert path == "/build-cache/input-model/model.onnx"
        assert volumes == {str(model.resolve()): {"bind": "/build-cache/input-model/model.onnx", "mode": "ro"}}

    @pytest.mark.parametrize("source", ["../etc/passwd", "not a repo", "a/b/c", "/abs/path/that/does/not/exist", ""])
    def test_invalid_sources_are_rejected(self, engine, source):
        with pytest.raises(ValueError):
            engine._stage_model(make_config(model_source=source), BuildProgress("b1", BuildStatus.QUEUED, ""))

    def test_hf_repo_is_downloaded_without_duplicate_weights(self, engine, monkeypatch):
        import huggingface_hub
        calls = {}

        class FakeApi:
            def __init__(self, token=None):
                pass

            def list_repo_files(self, repo, revision=None):
                return ["config.json", "model.safetensors", "pytorch_model.bin", "tokenizer.json"]

        def fake_download(**kwargs):
            calls.update(kwargs)
            return kwargs["local_dir"]

        monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
        monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)

        path, volumes = engine._stage_model(make_config(), BuildProgress("b1", BuildStatus.QUEUED, ""))

        assert path == "/build-cache/models/hf/HuggingFaceTB__SmolLM2-135M-Instruct"
        assert volumes == {}
        assert calls["repo_id"] == "HuggingFaceTB/SmolLM2-135M-Instruct"
        assert "*.bin" in calls["ignore_patterns"] and "*.gguf" in calls["ignore_patterns"]
        assert Path(calls["local_dir"]).is_relative_to(engine.cache_dir / "models")


class TestContainerEnvironment:

    def test_env_carries_board_format_and_quantization(self, engine):
        env = engine._container_env(make_config(target_format=ModelFormat.RKLLM, quantization="w4a16", target_board="rk3588"),
                                    "/build-cache/models/hf/x")
        assert env["MODEL_SOURCE"] == "/build-cache/models/hf/x"
        assert env["TARGET_FORMAT"] == "rkllm"
        assert env["TARGET_BOARD"] == "rk3588"
        assert env["QUANTIZATION"] == "w4a16"
        assert env["MODEL_NAME"] == "SmolLM2-135M-Instruct"

    def test_model_name_is_sanitized(self, engine):
        env = engine._container_env(make_config(model_source="/tmp/my model;rm -rf"), "/x")
        assert env["MODEL_NAME"] == "my_model_rm_-rf"


class TestSecretsAndCalibration:

    def test_stored_hf_token_is_used_for_downloads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("orchestrator.Core.builder.docker.from_env", lambda: (_ for _ in ()).throw(RuntimeError("no docker")))

        class Secrets:
            def get_secret(self, key):
                return "hf_secret" if key == "hf_token" else None

        engine = BuildEngine(ConfigManager(tmp_path / "configs"), secrets_manager=Secrets(), base_dir=tmp_path)
        assert engine._hf_token() == "hf_secret"
        assert engine.targets_dir == tmp_path.resolve() / "targets"

    def test_text_dataset_is_mounted_as_one_file(self, engine, tmp_path):
        ds = tmp_path / "calib.txt"
        ds.write_text("text")
        vols, inner = engine._dataset_mount(make_config(dataset_path=str(ds)))
        assert inner == "/build-cache/dataset.txt"
        assert vols == {str(ds.resolve()): {"bind": "/build-cache/dataset.txt", "mode": "ro"}}

    def test_rknn_dataset_mounts_its_directory(self, engine, tmp_path):
        (tmp_path / "samples").mkdir()
        ds = tmp_path / "samples" / "list.txt"
        ds.write_text("img1.jpg\n")
        vols, inner = engine._dataset_mount(make_config(target_format=ModelFormat.RKNN, dataset_path=str(ds)))
        assert inner == "/build-cache/dataset/list.txt"
        assert vols == {str(ds.parent.resolve()): {"bind": "/build-cache/dataset", "mode": "ro"}}

    def test_missing_dataset_is_an_error(self, engine, tmp_path):
        with pytest.raises(ValueError):
            engine._dataset_mount(make_config(dataset_path=str(tmp_path / "missing.txt")))

    def test_dataset_inside_the_model_folder_reuses_its_mount(self, engine, tmp_path):
        # A model folder with its sample list inside. A second bind of the same host folder would replace the
        # model mount (docker-py keys binds by host path). (The model used to be a single file here, whose parent
        # was mounted; a single file now mounts only itself, see the next test.)
        folder = tmp_path / "yolo"
        folder.mkdir()
        (folder / "yolov5s.onnx").write_bytes(b"onnx")
        (folder / "dataset.txt").write_text("img1.jpg\n")
        config = make_config(target_format=ModelFormat.RKNN, quantization="i8",
                             model_source=str(folder), dataset_path=str(folder / "dataset.txt"))
        _, staged = engine._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
        vols, inner = engine._dataset_mount(config, staged)
        assert vols == {} and inner == "/build-cache/input-model/dataset.txt"

    def test_dataset_next_to_a_model_file_gets_its_own_folder_mount(self, engine, tmp_path):
        # Usual RKNN layout: model, sample list and images in one folder; the model file is bound alone and the
        # sample list's folder separately (different host paths, so neither bind replaces the other).
        folder = tmp_path / "yolo"
        folder.mkdir()
        (folder / "yolov5s.onnx").write_bytes(b"onnx")
        (folder / "dataset.txt").write_text("img1.jpg\n")
        config = make_config(target_format=ModelFormat.RKNN, quantization="i8",
                             model_source=str(folder / "yolov5s.onnx"), dataset_path=str(folder / "dataset.txt"))
        _, staged = engine._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
        vols, inner = engine._dataset_mount(config, staged)
        assert vols == {str(folder.resolve()): {"bind": "/build-cache/dataset", "mode": "ro"}}
        assert inner == "/build-cache/dataset/dataset.txt" and not set(vols) & set(staged)

    def test_json_dataset_keeps_its_suffix(self, engine, tmp_path):
        ds = tmp_path / "calib.json"
        ds.write_text("[]")
        _, inner = engine._dataset_mount(make_config(target_format=ModelFormat.RKLLM, dataset_path=str(ds)))
        assert inner == "/build-cache/dataset.json"

    @pytest.mark.parametrize("fmt, quant, imatrix, kept", [
        (ModelFormat.GGUF, "Q4_K_M", False, False),
        (ModelFormat.GGUF, "IQ2_XS", True, True),
        (ModelFormat.RKNN, "fp16", False, False),
        (ModelFormat.RKNN, "i8", False, True),
        (ModelFormat.RKLLM, "w8a8", False, True),
    ])
    def test_dataset_is_kept_only_where_it_is_used(self, engine, tmp_path, fmt, quant, imatrix, kept):
        ds = tmp_path / "calib.txt"
        ds.write_text("text")
        config = make_config(target_format=fmt, quantization=quant, use_imatrix=imatrix, dataset_path=str(ds))
        engine._resolve_dataset(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert (config.dataset_path is not None) == kept

    def test_missing_dataset_fails_only_builds_that_need_it(self, engine, tmp_path):
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")
        config = make_config(target_format=ModelFormat.RKLLM, dataset_path=str(tmp_path / "gone.txt"))
        engine._resolve_dataset(config, progress)
        assert config.dataset_path is None and progress.warnings
        with pytest.raises(ValueError, match="RKNN"):
            engine._resolve_dataset(make_config(target_format=ModelFormat.RKNN, quantization="i8",
                                                dataset_path=str(tmp_path / "gone.txt")), progress)

    def test_imatrix_runs_only_for_gguf(self, engine, tmp_path, monkeypatch):
        ds = tmp_path / "calib.txt"
        ds.write_text("text")
        calls = []
        (engine.targets_dir / "Rockchip").mkdir(parents=True)
        for name in ("_prepare_build_environment", "_generate_dockerfile", "_scan_image_security",
                     "_execute_build_modules", "_extract_artifacts", "_create_golden_artifact"):
            monkeypatch.setattr(engine, name, lambda *a, **k: None)
        monkeypatch.setattr(engine, "_build_docker_image", lambda *a: SimpleNamespace(tags=["img"]))
        monkeypatch.setattr(engine, "_stage_model", lambda *a: ("/x", {}))
        monkeypatch.setattr(engine, "_generate_imatrix", lambda *a: calls.append("imatrix"))

        for fmt in (ModelFormat.RKLLM, ModelFormat.GGUF):
            engine._builds["b1"] = BuildProgress("b1", BuildStatus.QUEUED, "")
            engine._execute_build(make_config(target_format=fmt, use_imatrix=True, dataset_path=str(ds),
                                              cleanup_after_build=False))
        assert calls == ["imatrix"]


class TestBuildManifest:

    def test_manifest_lists_model_files_with_hashes(self, engine, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "m-Q4_K_M.gguf").write_bytes(b"weights")
        (out / "build_info.json").write_text('{"model": "m", "llama_cpp": "v0.6.0"}')
        info = engine._write_build_manifest(make_config(), out)
        assert info["primary"] == "m-Q4_K_M.gguf" and info["multi_file"] is False
        assert info["llama_cpp"] == "v0.6.0", "container fields are kept"
        assert info["artifacts"][0]["sha256"] == __import__("hashlib").sha256(b"weights").hexdigest()


    def test_manifest_lists_only_this_builds_files(self, engine, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        old = out / "Qwen-rk3588-w8a8.rkllm"
        old.write_bytes(b"x" * 100)  # an earlier build for another board, larger
        new = out / "Qwen-rk3576-w8a8.rkllm"
        new.write_bytes(b"x" * 10)
        (out / "Qwen-F16.gguf").write_bytes(b"x" * 1000)
        info = engine._write_build_manifest(make_config(target_format=ModelFormat.RKLLM), out, [str(new)])
        assert info["primary"] == new.name and [a["file"] for a in info["artifacts"]] == [new.name]

    def test_target_format_is_preferred_as_primary(self, engine, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "m.rkllm").write_bytes(b"x")
        (out / "m-F16.gguf").write_bytes(b"x" * 100)
        assert engine._write_build_manifest(make_config(target_format=ModelFormat.RKLLM), out)["primary"] == "m.rkllm"


class TestBuildStatus:

    def test_cleanup_after_failure_keeps_failed_status(self, engine):
        progress = BuildProgress("b1", BuildStatus.FAILED, "")
        engine._builds["b1"] = progress
        engine.cleanup_build("b1")
        assert progress.status == BuildStatus.FAILED

    def test_failed_build_reaches_a_terminal_state(self, engine, monkeypatch):
        monkeypatch.setattr(engine, "_prepare_build_environment", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        (engine.targets_dir / "Rockchip").mkdir(parents=True)
        engine._builds["b1"] = BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._execute_build(make_config())
        assert engine._builds["b1"].status == BuildStatus.FAILED
        assert "boom" in engine._builds["b1"].errors[-1]


class FakeBuildEngine:
    def __init__(self):
        self.configs = []

    def build_model(self, config):
        self.configs.append(config)
        return config.build_id

    def get_build_status(self, build_id):
        return SimpleNamespace(status=BuildStatus.COMPLETED, errors=[])


class TestFrameworkWiring:

    def test_orchestrator_shares_the_engine_that_knows_the_secrets(self, booted_framework):
        engine = booted_framework.orchestrator.build_engine
        assert engine.secrets_manager is booted_framework.secrets_manager
        if booted_framework.docker_manager.builder is not None:
            assert engine is booted_framework.docker_manager.builder


class TestOrchestration:

    @pytest.fixture
    def orchestrator(self, tmp_path):
        from orchestrator.Core.orchestrator import LLMOrchestrator
        cfg = ConfigManager(tmp_path / "configs")
        (tmp_path / "targets" / "Rockchip").mkdir(parents=True)
        cfg.set("targets_dir", str(tmp_path / "targets"))
        orch = LLMOrchestrator(cfg)
        orch.build_engine = FakeBuildEngine()
        return orch

    def make_request(self, tmp_path, **overrides):
        from orchestrator.Core.orchestrator import BuildRequest, PriorityLevel, WorkflowType
        values = dict(request_id="r1", workflow_type=WorkflowType.SIMPLE_CONVERSION, priority=PriorityLevel.NORMAL,
                      models=["HuggingFaceTB/SmolLM2-135M-Instruct"], targets=["Rockchip"],
                      target_formats=[ModelFormat.RKLLM], optimization_level=OptimizationLevel.BALANCED,
                      quantization_options=["w4a16"], parallel_builds=False, output_base_dir=str(tmp_path / "out"),
                      target_board="rk3588")
        values.update(overrides)
        return BuildRequest(**values)

    def run(self, orch, req):
        from orchestrator.Core.orchestrator import WorkflowState, OrchestrationStatus
        from datetime import datetime
        orch._workflows[req.request_id] = WorkflowState(req.request_id, OrchestrationStatus.PREPARING, datetime.now())
        asyncio.run(orch._run_build_pipeline(req))
        return orch._workflows[req.request_id]

    def test_board_format_and_quantization_reach_the_build_config(self, orchestrator, tmp_path):
        state = self.run(orchestrator, self.make_request(tmp_path))
        config = orchestrator.build_engine.configs[0]
        assert state.status.name == "COMPLETED"
        assert (config.target_board, config.target_format, config.quantization) == ("rk3588", ModelFormat.RKLLM, "w4a16")

    def test_imatrix_is_dropped_for_npu_formats(self, orchestrator, tmp_path):
        state = self.run(orchestrator, self.make_request(tmp_path, use_imatrix=True))
        assert orchestrator.build_engine.configs[0].use_imatrix is False
        assert any("GGUF only" in w for w in state.warnings)

    def test_shipped_calibration_text_reaches_only_gguf_jobs(self, orchestrator, tmp_path):
        orchestrator.config.set("cache_dir", str(tmp_path / "cache"))
        self.run(orchestrator, self.make_request(tmp_path, use_imatrix=True, quantization_options=[],
                                                 target_formats=[ModelFormat.GGUF, ModelFormat.RKLLM]))
        gguf, rkllm = orchestrator.build_engine.configs
        assert gguf.use_imatrix and gguf.dataset_path
        assert not rkllm.use_imatrix and rkllm.dataset_path is None

    def test_user_calibration_dataset_is_not_replaced(self, orchestrator, tmp_path):
        dataset = tmp_path / "calib.txt"
        dataset.write_text("domain text")
        self.run(orchestrator, self.make_request(tmp_path, use_imatrix=True, dataset_path=str(dataset)))
        assert orchestrator.build_engine.configs[0].dataset_path == str(dataset)


class TestBackgroundLoop:

    def test_tasks_created_by_a_coroutine_keep_running(self, booted_framework):
        async def start_task():
            async def worker():
                await asyncio.sleep(0.05)
                return "done"
            return asyncio.get_running_loop().create_task(worker())

        task = booted_framework.run_coroutine(start_task(), timeout=5)
        assert booted_framework.run_coroutine(asyncio.wait_for(asyncio.shield(task), 5), timeout=5) == "done"


class TestImageBuild:

    def test_buildkit_command_and_failure_message_with_log_tail(self, engine, monkeypatch, tmp_path):
        import orchestrator.Core.builder as builder_module

        dockerfile = tmp_path / "ctx" / "Dockerfile"
        dockerfile.parent.mkdir()
        dockerfile.write_text("FROM scratch\n")
        seen = {}

        class FakeProc:
            stdout = iter(["#8 RUN pip install torch\n", "ERROR: No space left on device\n"])
            returncode = 1

            def wait(self):
                return 1

        def fake_popen(cmd, **kwargs):
            seen["cmd"] = cmd
            return FakeProc()

        monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
        monkeypatch.setattr(builder_module.subprocess, "Popen", fake_popen)
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")

        with pytest.raises(RuntimeError) as excinfo:
            engine._build_docker_image(make_config(build_args={"EXTRA": "1"}), progress, dockerfile)

        cmd = seen["cmd"]
        assert cmd[:4] == ["docker", "buildx", "build", "--load"]
        assert f"LLAMA_CPP_TAG={pinned_tag()}" in cmd and pinned_tag() == "b11429" and "EXTRA=1" in cmd
        assert cmd[-1] == str(dockerfile.parent)
        assert "No space left on device" in str(excinfo.value)


class TestCalibrationData:

    def test_shipped_sets_match_their_checksums(self):
        from orchestrator.utils.calibration import CALIBRATION_SETS, load_set
        for name in CALIBRATION_SETS:
            assert len(load_set(name)) > 100_000

    def test_mixed_text_covers_every_language_from_the_start(self):
        from orchestrator.utils.calibration import build_calibration_text, load_set
        text = build_calibration_text("mixed")
        head = text[:20_000]
        for name in ("de", "en", "tools"):
            first_block = load_set(name)[:200]
            assert first_block in head, name

    def test_modified_data_is_refused(self, tmp_path, monkeypatch):
        import gzip
        from orchestrator.utils import calibration
        (tmp_path / "calibration_de.txt.gz").write_bytes(gzip.compress(b"tampered"))
        monkeypatch.setattr(calibration, "DATA_DIR", tmp_path)
        with pytest.raises(calibration.CalibrationDataError, match="checksum"):
            calibration.load_set("de")

    def test_imatrix_without_dataset_uses_the_shipped_text(self, tmp_path):
        from orchestrator.Core.orchestrator import (BuildRequest, LLMOrchestrator, OrchestrationStatus,
                                                    PriorityLevel, WorkflowState, WorkflowType)
        from datetime import datetime
        cfg = ConfigManager(tmp_path / "configs")
        (tmp_path / "targets" / "Rockchip").mkdir(parents=True)
        cfg.set("targets_dir", str(tmp_path / "targets"))
        cfg.set("cache_dir", str(tmp_path / "cache"))
        orch = LLMOrchestrator(cfg)
        orch.build_engine = FakeBuildEngine()
        req = BuildRequest(request_id="r1", workflow_type=WorkflowType.SIMPLE_CONVERSION, priority=PriorityLevel.NORMAL,
                           models=["a/b"], targets=["Rockchip"], target_formats=[ModelFormat.GGUF],
                           optimization_level=OptimizationLevel.BALANCED, quantization_options=["IQ2_XS"],
                           parallel_builds=False, output_base_dir=str(tmp_path / "out"), use_imatrix=True)
        orch._workflows["r1"] = WorkflowState("r1", OrchestrationStatus.PREPARING, datetime.now())
        asyncio.run(orch._run_build_pipeline(req))
        config = orch.build_engine.configs[0]
        assert config.use_imatrix and Path(config.dataset_path).name == "calibration_mixed.txt"
        assert Path(config.dataset_path).stat().st_size > 1_000_000


class TestBuildSandbox:

    def test_conversion_containers_are_locked_down(self, engine, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator.Core.builder.os.getuid", lambda: 1234)
        monkeypatch.setattr("orchestrator.Core.builder.os.getgid", lambda: 1234)
        build_temp = tmp_path / "builds" / "b1"
        kwargs, env, vols = engine._sandbox(make_config(), build_temp)
        assert kwargs["user"] == "1234:1234"
        assert kwargs["network_mode"] == "none" and kwargs["cap_drop"] == ["ALL"]
        assert kwargs["read_only"] is True and "no-new-privileges:true" in kwargs["security_opt"]
        assert env["HF_HUB_OFFLINE"] == "1" and env["BUILD_SCRATCH_DIR"] == "/build-cache/work"
        assert vols == {str(build_temp / "work"): {"bind": "/build-cache/work", "mode": "rw"}}
        assert (build_temp / "work" / "tmp").is_dir()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership")
    def test_root_host_hands_build_folders_to_the_container_user(self, engine, tmp_path, monkeypatch):
        owned = []
        monkeypatch.setattr("orchestrator.Core.builder.os.getuid", lambda: 0)
        monkeypatch.setattr("orchestrator.Core.builder.os.chown", lambda path, uid, gid: owned.append((Path(path), uid)))
        kwargs, _, _ = engine._sandbox(make_config(), tmp_path / "builds" / "b1")
        assert kwargs["user"] == "1000:1000"
        assert (tmp_path / "builds" / "b1" / "work", 1000) in owned

    def test_sandbox_can_be_disabled(self, engine, tmp_path):
        engine.config.set("build_sandbox", False)
        kwargs, env, _ = engine._sandbox(make_config(), tmp_path / "builds" / "b1")
        assert kwargs == {"user": "0:0"} and "HF_HUB_OFFLINE" not in env

    def test_memory_limit_is_optional(self, engine, tmp_path):
        assert "mem_limit" not in engine._sandbox(make_config(), tmp_path / "a")[0]
        engine.config.set("build_memory_limit", "24g")
        assert engine._sandbox(make_config(), tmp_path / "b")[0]["mem_limit"] == "24g"
