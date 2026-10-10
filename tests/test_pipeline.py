"""Host side of the build pipeline: model staging, container environment, orchestration (no Docker needed)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.builder import (BuildConfiguration, BuildEngine, BuildProgress, BuildStatus,
                                       ModelFormat, OptimizationLevel)
from orchestrator.Core.config_manager import ConfigManager


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

    def test_local_file_is_mounted_with_its_directory(self, engine, tmp_path):
        model = tmp_path / "model.onnx"
        model.write_bytes(b"onnx")
        path, volumes = engine._stage_model(make_config(model_source=str(model)), BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert path == "/build-cache/input-model/model.onnx"
        assert str(tmp_path.resolve()) in volumes

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

    def test_user_calibration_dataset_is_not_replaced(self, orchestrator, tmp_path):
        dataset = tmp_path / "calib.txt"
        dataset.write_text("domain text")

        class Ditto:
            def prepare_calibration_dataset(self, path):
                raise AssertionError("Ditto must not overwrite a user dataset")

        orchestrator.ditto = Ditto()
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
        assert "LLAMA_CPP_TAG=v0.6.0" in cmd and "EXTRA=1" in cmd
        assert cmd[-1] == str(dockerfile.parent)
        assert "No space left on device" in str(excinfo.value)
