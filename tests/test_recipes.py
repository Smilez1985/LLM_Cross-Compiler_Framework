"""Recipes (the zoo's model templates) and the build_info.json fields they are written from."""

import hashlib
import os
from datetime import datetime, timezone

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.builder import BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, ModelFormat
from orchestrator.Core.community_pack import tree_sha256
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.schemas.build import BuildRequestIn
from orchestrator.schemas.recipes import Recipe
from orchestrator.schemas.targets import TargetCapabilities

COMMIT = "a" * 40
SHA = "b" * 64


def rockchip() -> TargetCapabilities:
    data = yaml.safe_load((REPO_ROOT / "targets" / "Rockchip" / "target.yml").read_text(encoding="utf-8"))
    return TargetCapabilities.model_validate(data["capabilities"])


def recipe_data(**overrides):
    data = {
        "schema_version": 1,
        "id": "qwen3.5-2b-rk3588-rkllm-w8a8",
        "model": {"repo": "Qwen/Qwen3.5-2B", "revision": COMMIT, "license": "apache-2.0"},
        "target": {"id": "Rockchip", "pack_version": "1.2.0", "pack_sha256": SHA},
        "board": "rk3588", "format": "rkllm", "quantization": "w8a8",
        "options": {"use_imatrix": False, "max_context_length": 4096},
        "results": [{"artifact": {"file": "Qwen3.5-2B-w8a8.rkllm", "size": 2340000000, "sha256": SHA},
                     "lcf_version": "3.1.0", "toolchain_image": "ghcr.io/example/rkllm@sha256:" + "c" * 64,
                     "device_test": {"board_model": "ROCK 5B", "runtime": "rkllm 1.2.1", "tokens_per_s": 12.3,
                                     "smoke_passed": True}}],
        "author": "alice",
        "created": "2026-10-11T12:00:00Z",
    }
    data.update(overrides)
    return data


def build_info(**overrides):
    info = {
        "build_id": "b1", "model_source": "Qwen/Qwen3.5-2B", "model_revision": COMMIT, "target": "Rockchip",
        "board": "rk3588", "format": "rkllm", "quantization": "w8a8", "imatrix": False,
        "created": "2026-10-11T12:00:00", "primary": "Qwen3.5-2B-w8a8.rkllm",
        "artifacts": [{"file": "Qwen3.5-2B-w8a8.rkllm", "bytes": 2340000000, "sha256": SHA}],
        "multi_file": False, "target_pack_sha256": SHA, "target_version": "1.0.0",
        "toolchain_image": "sha256:" + "d" * 64, "max_context_length": 4096, "dataset": None,
        "lcf_version": "3.0.0",
    }
    info.update(overrides)
    return info


class TestRecipeSchema:

    def test_plan_example_validates_and_round_trips_as_yaml(self):
        recipe = Recipe.model_validate(recipe_data())
        assert recipe.created.tzinfo == timezone.utc
        assert Recipe.model_validate(yaml.safe_load(recipe.to_yaml())) == recipe

    @pytest.mark.parametrize("change", [
        {"model": {"repo": "Qwen/Qwen3.5-2B", "revision": "a" * 39}},           # 39-hex revision
        {"model": {"repo": "Qwen/Qwen3.5-2B", "revision": "main"}},             # a branch is not a commit
        {"model": {"repo": "../etc/passwd", "revision": COMMIT}},
        {"target": {"id": "Rockchip", "pack_sha256": "x" * 64}},
        {"id": "Has Spaces"},
        {"created": "2026-10-11T12:00:00"},                                     # no time zone
        {"weights_url": "https://example.org/model.bin"},                       # unknown field
    ])
    def test_invalid_recipes_are_rejected(self, change):
        with pytest.raises(ValueError):
            Recipe.model_validate(recipe_data(**change))

    def test_unpinned_toolchain_image_is_rejected(self):
        data = recipe_data()
        data["results"][0]["toolchain_image"] = "ghcr.io/example/rkllm:latest"
        with pytest.raises(ValueError):
            Recipe.model_validate(data)

    def test_target_capabilities_are_checked(self):
        assert Recipe.model_validate(recipe_data()).check_target(rockchip()) == []
        missing_format = Recipe.model_validate(recipe_data(format="tflite"))
        assert "not available" in missing_format.check_target(rockchip())[0]
        bad_quant = Recipe.model_validate(recipe_data(quantization="Q4_K_M"))
        assert any("not supported" in p for p in bad_quant.check_target(rockchip()))


class TestRecipeFromBuildInfo:

    def test_generated_from_build_info(self):
        now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        recipe = Recipe.from_build_info(build_info(), author="alice", license="apache-2.0", now=now)
        assert recipe.id == "qwen3.5-2b-rk3588-rkllm-w8a8"
        assert (recipe.model.repo, recipe.model.revision, recipe.target.pack_sha256) == ("Qwen/Qwen3.5-2B", COMMIT, SHA)
        assert recipe.target.pack_version == "1.0.0"
        result = recipe.results[0]
        assert (result.artifact.file, result.artifact.size, result.lcf_version) == ("Qwen3.5-2B-w8a8.rkllm",
                                                                                  2340000000, "3.0.0")
        assert result.toolchain_image == "sha256:" + "d" * 64
        assert recipe.options.max_context_length == 4096 and recipe.check_target(rockchip()) == []

    @pytest.mark.parametrize("change, message", [
        ({"model_source": "/home/alice/models/qwen"}, "local model"),
        ({"model_revision": None}, "known commit"),
        ({"target_pack_sha256": None}, "fingerprint"),
        ({"primary": None}, "primary artifact"),
    ])
    def test_unreproducible_builds_are_refused(self, change, message):
        with pytest.raises(ValueError, match=message):
            Recipe.from_build_info(build_info(**change), author="alice")

    def test_dataset_name_is_scrubbed(self, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "jenny")
        info = build_info(dataset={"name": "jenny-calibration.txt", "sha256": SHA}, format="gguf",
                          quantization="Q4_K_M", imatrix=True)
        recipe = Recipe.from_build_info(info, author="alice")
        assert recipe.options.dataset.name == "<user>-calibration.txt"
        assert recipe.options.use_imatrix

    def test_odd_target_version_is_left_out(self):
        recipe = Recipe.from_build_info(build_info(target_version="v1"), author="alice")
        assert recipe.target.pack_version is None


# --- Builder side ------------------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    return BuildEngine(ConfigManager(tmp_path / "configs"))


def make_config(**overrides):
    values = dict(build_id="b1", timestamp="t", model_source="HuggingFaceTB/SmolLM2-135M-Instruct",
                  target_arch="Rockchip", target_format=ModelFormat.GGUF, output_dir="out",
                  quantization="Q4_K_M", target_board="rk3566")
    values.update(overrides)
    return BuildConfiguration(**values)


class TestBuildInfoFields:

    def test_hf_download_is_pinned_to_the_resolved_commit(self, engine, monkeypatch):
        import huggingface_hub
        seen = {}

        class FakeApi:
            def __init__(self, token=None):
                pass

            def model_info(self, repo, revision=None, token=None, files_metadata=False):
                seen["list" if files_metadata else "info"] = revision
                return type("Info", (), {"sha": COMMIT, "siblings": [
                    type("S", (), {"rfilename": n, "size": 1, "lfs": None})()
                    for n in ("config.json", "model.safetensors")]})()

        monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
        monkeypatch.setattr(type(engine), "_hub_fetcher", staticmethod(lambda token, stop: type("F", (), {
            "fetch_file": lambda self, repo, commit, file, dest, progress=None: seen.update(download=commit)
            or (dest, "0")})()))
        config = make_config()
        engine._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert seen == {"info": "main", "list": COMMIT, "download": COMMIT}
        assert config.model_revision == COMMIT

    def test_unknown_commit_leaves_the_revision_open(self, engine):
        class NoInfo:
            def model_info(self, repo, revision=None):
                raise OSError("offline")

        assert engine._model_commit(NoInfo(), "a/b", "main") is None
        assert engine._model_commit(NoInfo(), "a/b", COMMIT) == COMMIT

    def test_manifest_records_what_a_recipe_needs(self, engine, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "m-Q4_K_M.gguf").write_bytes(b"weights")
        dataset = tmp_path / "calib.txt"
        dataset.write_text("calibration")
        config = make_config(model_revision=COMMIT, target_pack_sha256=SHA, target_version="1.0.0",
                             toolchain_image="sha256:" + "e" * 64, max_context_length=4096,
                             dataset_path=str(dataset), use_imatrix=True)
        info = engine._write_build_manifest(config, out)
        assert info["model_revision"] == COMMIT and info["target_pack_sha256"] == SHA
        assert info["toolchain_image"] == "sha256:" + "e" * 64 and info["max_context_length"] == 4096
        assert info["dataset"] == {"name": "calib.txt", "sha256": hashlib.sha256(b"calibration").hexdigest()}
        recipe = Recipe.from_build_info(info, author="alice")
        assert recipe.results[0].artifact.sha256 == hashlib.sha256(b"weights").hexdigest()

    def test_target_fingerprint_and_version_are_recorded(self, engine, tmp_path):
        target = tmp_path / "Demo"
        (target / "modules").mkdir(parents=True)
        (target / "target.yml").write_text("metadata:\n  version: 1.2.0\n", encoding="utf-8")
        (target / "modules" / "build.sh").write_text("echo\n")
        config = make_config(target_arch="Demo")
        engine._record_target(config, BuildProgress("b1", BuildStatus.QUEUED, ""), target)
        assert config.target_pack_sha256 == tree_sha256(target) and config.target_version == "1.2.0"

    @pytest.mark.skipif(os.name != "posix", reason="symbolic links need privileges on Windows")
    def test_target_with_a_link_gets_no_fingerprint(self, engine, tmp_path):
        target = tmp_path / "Demo"
        target.mkdir()
        (target / "target.yml").write_text("metadata: {}\n")
        (target / "secret").symlink_to(tmp_path)
        config, progress = make_config(target_arch="Demo"), BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._record_target(config, progress, target)
        assert config.target_pack_sha256 is None and config.target_version is None
        assert "cannot become a recipe" in progress.warnings[0]

    @pytest.mark.parametrize("name", ["../Rockchip", "Rockchip/../../etc", "", ".hidden"])
    def test_target_names_with_paths_are_refused(self, engine, name):
        engine._builds["b1"] = BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._execute_build(make_config(target_arch=name))
        progress = engine._builds["b1"]
        assert progress.status == BuildStatus.FAILED
        assert "Invalid target name" in " ".join(progress.errors)


class TestBuildServiceRevision:

    def test_revision_is_passed_to_the_build(self):
        from orchestrator.Core.orchestrator import BuildRequest
        assert "model_revision" in BuildRequest.__dataclass_fields__
        with pytest.raises(ValueError):
            BuildRequestIn(model="a/b", target="Rockchip", format="gguf", revision="main")
