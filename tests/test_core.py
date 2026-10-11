"""Kernel tests: configuration loading/persistence, framework boot, Ditto wiring, CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager


class TestConfigManager:

    def test_defaults_are_loaded_on_construction(self, tmp_path):
        cfg = ConfigManager(tmp_path)
        assert cfg.log_level == "INFO"
        assert cfg.max_concurrent_builds == 2
        assert cfg.docker_registry == "ghcr.io"
        assert cfg.get("enable_rag_knowledge") is False
        assert cfg.get("enable_telemetry") is False
        assert cfg.get("ai_provider") == "OpenAI"

    def test_accepts_config_file_path(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("log_level: DEBUG\ncrawler_max_depth: 4\n")

        cfg = ConfigManager(str(config_file))

        assert cfg.config_dir == tmp_path
        assert cfg.log_level == "DEBUG"
        assert cfg.crawler_max_depth == 4

    def test_existing_file_with_other_extension_is_treated_as_file(self, tmp_path):
        conf = tmp_path / "framework.conf"
        conf.write_text("ignored")
        assert ConfigManager(str(conf)).config_dir == tmp_path

    def test_set_and_save_roundtrip(self, tmp_path):
        cfg = ConfigManager(tmp_path)
        assert cfg.set("ai_provider", "Anthropic")
        assert cfg.set("ai_model", "claude-sonnet-5-5")
        assert cfg.set("crawler_max_depth", 3)
        assert cfg.save_user_config()

        reloaded = ConfigManager(tmp_path)
        assert reloaded.get("ai_provider") == "Anthropic"
        assert reloaded.get("ai_model") == "claude-sonnet-5-5"
        assert reloaded.get("crawler_max_depth") == 3

    def test_save_persists_only_runtime_changes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_OFFLINE_MODE", "1")
        cfg = ConfigManager(tmp_path)
        assert cfg.offline_mode is True
        cfg.set("ai_model", "gpt-4o-mini")
        cfg.set("some_runtime_value", "not-persisted")
        cfg.save_user_config()

        saved = yaml.safe_load((tmp_path / "config.yml").read_text())
        assert saved == {"ai_model": "gpt-4o-mini"}

        monkeypatch.delenv("LLM_FRAMEWORK_OFFLINE_MODE")
        assert ConfigManager(tmp_path).offline_mode is False

    def test_existing_user_settings_survive_a_save(self, tmp_path):
        (tmp_path / "config.yml").write_text("language: de\n")
        cfg = ConfigManager(tmp_path)
        cfg.set("ai_model", "llama3.2")
        cfg.save_user_config()

        saved = yaml.safe_load((tmp_path / "config.yml").read_text())
        assert saved == {"language": "de", "ai_model": "llama3.2"}

    def test_explicit_config_file_is_layered_over_user_config(self, tmp_path):
        explicit = tmp_path / "ci.yml"
        explicit.write_text("log_level: DEBUG\nai_model: from-ci\n")

        cfg = ConfigManager(str(explicit))
        cfg.set("ai_provider", "Ollama")
        cfg.save_user_config()

        reloaded = ConfigManager(str(explicit))
        assert reloaded.get("ai_provider") == "Ollama"
        assert reloaded.get("log_level") == "DEBUG"
        assert reloaded.get("ai_model") == "from-ci"
        assert explicit.read_text() == "log_level: DEBUG\nai_model: from-ci\n"

    def test_invalid_value_is_rejected(self, tmp_path):
        cfg = ConfigManager(tmp_path)
        assert cfg.set("log_level", "LOUD") is False
        assert cfg.log_level == "INFO"

    def test_invalid_values_from_file_and_env_are_ignored(self, tmp_path, monkeypatch):
        (tmp_path / "config.yml").write_text("log_level: warn\ncrawler_max_depth: 2\n")
        monkeypatch.setenv("LLM_FRAMEWORK_MAX_CONCURRENT_BUILDS", "99")

        cfg = ConfigManager(tmp_path)

        assert cfg.log_level == "INFO"
        assert cfg.max_concurrent_builds == 2

    def test_environment_overrides_file(self, tmp_path, monkeypatch):
        (tmp_path / "config.yml").write_text("log_level: DEBUG\n")
        monkeypatch.setenv("LLM_FRAMEWORK_LOG_LEVEL", "WARNING")
        assert ConfigManager(tmp_path).log_level == "WARNING"

    def test_unrelated_environment_variables_are_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_CONFIG", "/somewhere/else")
        monkeypatch.setenv("LLM_FRAMEWORK_SOMETHING_ELSE", "x")
        cfg = ConfigManager(tmp_path)
        assert not hasattr(cfg, "config")
        assert cfg.get("something_else") is None


class TestRemovedSettingsAndCode:
    """Leftovers of the removed Transformers offline mode and of the Qdrant container; old config files keep loading."""

    def test_removed_setting_has_no_schema_and_old_files_still_load(self, tmp_path):
        (tmp_path / "config.yml").write_text(yaml.safe_dump({"preferred_tiny_model": "tinyllama_1b",
                                                             "log_level": "DEBUG"}))
        cfg = ConfigManager(tmp_path)
        assert "preferred_tiny_model" not in cfg.config_schemas
        assert cfg.log_level == "DEBUG", "the rest of the file still applies"
        cfg.set("language", "de")
        assert cfg.save_user_config()
        saved = yaml.safe_load((tmp_path / "config.yml").read_text())
        assert saved["language"] == "de" and saved["preferred_tiny_model"] == "tinyllama_1b", "kept, not lost"

    def test_removed_code_is_gone(self):
        from orchestrator.Core.docker_manager import DockerManager
        from orchestrator.Core.model_manager import ModelManager
        assert not hasattr(DockerManager, "ensure_qdrant_service")
        for name in ("get_available_tiny_models", "is_tiny_model_installed", "download_tiny_model"):
            assert not hasattr(ModelManager, name), name
        # image_qdrant stays: deployment packages ship Qdrant to the device (deployment_manager).
        assert "image_qdrant" in ConfigManager().config_schemas


class TestFeaturesInDevelopment:
    """Settings and modules of features that are still being built (see the roadmap) stay in the code base."""

    SETTINGS = {"logs_dir", "auto_cleanup", "docker_namespace", "default_enable_imatrix",
                "default_calibration_dataset", "gui_theme", "gui_auto_refresh", "gui_refresh_interval",
                "api_enabled", "api_port", "api_host"}

    def test_their_settings_keep_their_schema(self):
        assert self.SETTINGS <= set(ConfigManager().config_schemas)

    def test_their_modules_stay(self):
        for rel in ("orchestrator/utils/updater.py", "orchestrator/gui/benchmark_window.py",
                    "orchestrator/Core/consistency_manager.py"):
            assert (REPO_ROOT / rel).exists(), rel


class TestFrameworkBoot:

    def test_all_core_components_are_wired(self, booted_framework):
        fm = booted_framework
        for name in ["secrets_manager", "target_manager", "model_manager", "ditto_manager",
                     "self_healing_manager", "deployment_manager", "community_manager", "orchestrator"]:
            assert fm.get_component(name) is not None, f"{name} not initialized"

    def test_rag_is_opt_in(self, booted_framework):
        assert booted_framework.get_component("rag_manager") is None

    def test_rag_manager_can_be_enabled_at_runtime(self, booted_framework):
        fm = booted_framework
        fm.config.set("enable_rag_knowledge", True)

        rag = fm.ensure_rag_manager()

        assert rag is fm.get_component("rag_manager")
        assert rag.framework is fm
        assert fm.ensure_rag_manager() is rag

    def test_second_boot_reads_secrets_of_the_first(self, make_framework):
        first = make_framework()
        assert first.secrets_manager.set_secret("openai_api_key", "sk-test-value-1234567890")

        second = make_framework()

        assert second.secrets_manager.get_secret("openai_api_key") == "sk-test-value-1234567890"

    def test_boot_survives_unrelated_env_and_bad_log_level(self, make_framework, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_CONFIG", str(tmp_path / "elsewhere"))
        (tmp_path / "configs" / "config.yml").write_text("log_level: warn\n")
        assert make_framework().model_manager is not None

    def test_ditto_uses_configured_provider(self, make_framework, tmp_path):
        config_file = tmp_path / "configs" / "config.yml"
        config_file.write_text("ai_provider: Ollama\nai_model: qwen2.5\nai_base_url: http://localhost:11434/v1\n")

        fm = make_framework(config_file)

        info = fm.ditto_manager.describe()
        assert (info["provider"], info["model"], info["base_url"], info["local"]) == \
               ("ollama", "qwen2.5", "http://localhost:11434/v1", True)
        assert fm.ditto_manager._registry() is fm.tool_registry

    def test_settings_changes_reach_ditto_without_a_restart(self, booted_framework):
        fm = booted_framework
        fm.config.set("ai_provider", "anthropic")
        assert fm.ditto_manager.is_configured() is False  # no key stored yet
        assert fm.secrets_manager.set_secret("anthropic_api_key", "sk-ant-api03-FAKE0123456789abcdefghij")
        fm.ditto_manager.reload()
        assert fm.ditto_manager.describe()["provider"] == "anthropic"

    def test_offline_mode_keeps_the_local_knowledge_base(self, booted_framework):
        # Embeddings are local now; offline mode only forbids downloads (see tests/test_rag.py).
        fm = booted_framework
        fm.config.set("enable_rag_knowledge", True)
        fm.ensure_rag_manager()
        assert fm.rag_manager._offline() is False

        fm.config.set("offline_mode", True)

        assert fm.ditto_manager.offline_mode is True
        assert fm.ditto_manager._rag_manager() is fm.rag_manager
        assert fm.rag_manager._offline() is True

    def test_secrets_roundtrip_through_keyring(self, booted_framework):
        sm = booted_framework.secrets_manager
        assert sm.set_secret("openai_api_key", "sk-test-value-1234567890")
        assert sm.get_secret("openai_api_key") == "sk-test-value-1234567890"
        assert sm.get_secret("missing") is None

    def test_nothing_is_written_to_the_real_home(self, booted_framework, tmp_path):
        assert not (tmp_path / "home" / ".llm-framework").exists()


class TestTargets:

    def test_bundled_targets_are_loaded(self, tmp_path):
        from orchestrator.Core.target_manager import TargetManager

        tm = TargetManager(SimpleNamespace(config=ConfigManager(tmp_path)))
        tm.targets_dir = REPO_ROOT / "targets"
        tm.initialize()

        assert "Rockchip" in tm.list_hardware_profiles()
        assert "_template" not in tm.list_hardware_profiles()


class TestCLI:

    def run_cli(self, tmp_path, *args, keyring_backend="keyring.backends.null.Keyring", storage="ephemeral"):
        # No keyring in a test process: the credential storage is chosen explicitly (as in CI), never guessed.
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND=keyring_backend,
                   LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "output"),
                   LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"))
        if storage:
            env["LLM_SECRET_STORAGE"] = storage
        return subprocess.run([sys.executable, "-m", "orchestrator.cli", *args],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)

    def test_commands_without_framework_work_without_a_keyring(self, tmp_path):
        result = self.run_cli(tmp_path, "-q", "config", "show", keyring_backend="keyring.backends.fail.Keyring")
        assert result.returncode == 0, result.stderr
        assert "targets_dir" in result.stdout

    @pytest.mark.parametrize("backend", ["keyring.backends.fail.Keyring", "keyring.backends.null.Keyring"])
    def test_framework_commands_fail_cleanly_without_a_keyring(self, tmp_path, backend):
        # The null backend keeps nothing: it used to lose every secret silently on the next start.
        result = self.run_cli(tmp_path, "-q", "secrets", "list", keyring_backend=backend, storage=None)
        assert result.returncode == 5, "storage unavailable has its own exit code"
        assert "Traceback" not in result.stderr
        assert "Failed to initialize framework" in result.stderr
        assert "secret_storage" in result.stderr and "passphrase" in result.stderr, "the choices are named"
        assert not (tmp_path / "state" / "secrets.store").exists()

    def test_quiet_suppresses_info_logs(self, tmp_path):
        result = self.run_cli(tmp_path, "-q", "targets", "list", "-f", "json")
        assert result.returncode == 0, result.stderr
        assert "[INFO]" not in result.stderr

    def test_help_works(self, tmp_path):
        result = self.run_cli(tmp_path, "--help")
        assert result.returncode == 0
        assert "targets" in result.stdout

    def test_build_start_offers_boards_and_real_formats(self, tmp_path):
        result = self.run_cli(tmp_path, "build", "start", "--help")
        assert result.returncode == 0, result.stderr
        assert "--board" in result.stdout
        assert "rkllm" in result.stdout and "gguf" in result.stdout

    def test_json_output_is_not_polluted_by_logs(self, tmp_path):
        result = self.run_cli(tmp_path, "targets", "list", "-f", "json")
        assert result.returncode == 0, result.stderr
        assert isinstance(json.loads(result.stdout), list)


class TestDependencyApiRegressions:
    """APIs that changed in the dependency upgrade and used to fail silently."""

    # The Qdrant query API (query_points with prefetch + RRF) is exercised against real local Qdrant
    # in tests/test_rag.py, the FastEmbed API in TestFastEmbedApi there.

    def test_hub_file_download_uses_current_hub_api(self, tmp_path, monkeypatch):
        # The Hugging Face browser's download (the tiny-model snapshot download it replaced was removed).
        import inspect
        import huggingface_hub
        from orchestrator.Core.model_manager import ModelManager

        seen = {}

        def fake_hf_hub_download(**kwargs):
            inspect.signature(huggingface_hub.hf_hub_download).bind(**kwargs)
            seen.update(kwargs)
            return str(Path(kwargs["cache_dir"]) / kwargs["filename"])

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_hub_download)
        mm = ModelManager(ConfigManager(tmp_path / "configs"), base_dir=tmp_path)

        path = mm.download_file("Qwen/Qwen3.5-0.8B", "config.json", token=None)

        assert path is not None, "download_file swallowed an exception"
        assert seen["repo_id"] == "Qwen/Qwen3.5-0.8B" and seen["cache_dir"] == str(tmp_path / "models")
