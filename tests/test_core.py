#!/usr/bin/env python3
"""Kernel tests: configuration loading/persistence and the framework boot sequence."""

import sys

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

    def test_saved_config_only_contains_whitelisted_keys(self, tmp_path):
        cfg = ConfigManager(tmp_path)
        cfg.set("some_runtime_value", "not-persisted")
        cfg.save_user_config()

        saved = yaml.safe_load((tmp_path / "config.yml").read_text())
        assert "some_runtime_value" not in saved
        assert "ai_provider" in saved

    def test_invalid_value_is_rejected(self, tmp_path):
        cfg = ConfigManager(tmp_path)
        assert cfg.set("log_level", "LOUD") is False
        assert cfg.log_level == "INFO"

    def test_environment_overrides_file(self, tmp_path, monkeypatch):
        (tmp_path / "config.yml").write_text("log_level: DEBUG\n")
        monkeypatch.setenv("LLM_FRAMEWORK_LOG_LEVEL", "WARNING")
        assert ConfigManager(tmp_path).log_level == "WARNING"


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

    def test_ditto_uses_configured_provider(self, tmp_path, monkeypatch, memory_keyring):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yml").write_text("ai_provider: Ollama\nai_model: qwen2.5\nai_base_url: http://localhost:11434\n")
        from orchestrator.Core.framework import FrameworkManager

        fm = FrameworkManager(str(tmp_path / "config.yml"))
        fm.info.installation_path = tmp_path
        assert fm.initialize()

        assert fm.ditto_manager.provider == "Ollama"
        assert fm.ditto_manager.litellm_model == "ollama/qwen2.5"
        assert fm.ditto_manager.base_url == "http://localhost:11434"

    def test_secrets_roundtrip_through_keyring(self, booted_framework):
        sm = booted_framework.secrets_manager
        assert sm.set_secret("openai_api_key", "sk-test-value-1234567890")
        assert sm.get_secret("openai_api_key") == "sk-test-value-1234567890"
        assert sm.get_secret("missing") is None


class TestDittoReinitialize:

    def test_switching_provider_and_offline_mode(self, booted_framework):
        ditto = booted_framework.ditto_manager

        ditto.reinitialize(provider="Google", model="gemini-2.0-flash")
        assert ditto.litellm_model == "gemini/gemini-2.0-flash"

        ditto.reinitialize(offline_mode=True)
        assert ditto.offline_mode is True

        ditto.reinitialize(provider="Ollama", model="llama3.2", base_url="http://localhost:11434", offline_mode=False)
        assert ditto.litellm_model == "ollama/llama3.2"
        assert ditto.base_url == "http://localhost:11434"


class TestTargets:

    def test_bundled_targets_are_loaded(self, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        from types import SimpleNamespace
        from orchestrator.Core.target_manager import TargetManager

        tm = TargetManager(SimpleNamespace(config=ConfigManager(REPO_ROOT / "configs")))
        tm.initialize()

        assert "Rockchip" in tm._targets
        assert "_template" not in tm._targets


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
