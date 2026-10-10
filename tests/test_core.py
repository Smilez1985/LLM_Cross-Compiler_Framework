"""Kernel tests: configuration loading/persistence, framework boot, Ditto provider wiring, CLI."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.ditto_manager import DittoCoder


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
        config_file.write_text("ai_provider: Ollama\nai_model: qwen2.5\nai_base_url: http://localhost:11434\n")

        fm = make_framework(config_file)

        assert fm.ditto_manager.provider == "Ollama"
        assert fm.ditto_manager.litellm_model == "ollama/qwen2.5"
        assert fm.ditto_manager.base_url == "http://localhost:11434"

    def test_secrets_roundtrip_through_keyring(self, booted_framework):
        sm = booted_framework.secrets_manager
        assert sm.set_secret("openai_api_key", "sk-test-value-1234567890")
        assert sm.get_secret("openai_api_key") == "sk-test-value-1234567890"
        assert sm.get_secret("missing") is None

    def test_nothing_is_written_to_the_real_home(self, booted_framework, tmp_path):
        assert not (tmp_path / "home" / ".llm-framework").exists()


class TestDittoProviders:

    @pytest.fixture
    def ditto(self):
        return DittoCoder(provider="OpenAI", model="gpt-4o")

    @pytest.mark.parametrize("provider, model, expected", [
        ("OpenAI", "gpt-4o", "gpt-4o"),
        ("Anthropic", "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
        ("Anthropic", "anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
        ("Google", "gemini-2.0-flash", "gemini/gemini-2.0-flash"),
        ("Ollama", "hf.co/org/model", "ollama/hf.co/org/model"),
        ("Custom", "my-model", "openai/my-model"),
    ])
    def test_litellm_model_names(self, ditto, provider, model, expected):
        assert ditto._format_model_name(provider, model) == expected

    def test_cloud_providers_never_get_a_custom_base_url(self, ditto):
        ditto.reinitialize(provider="Custom", model="m", base_url="https://proxy.example.com/v1")
        assert ditto.base_url == "https://proxy.example.com/v1"

        ditto.reinitialize(provider="OpenAI", model="gpt-4o", api_key="sk-x")
        assert ditto.base_url is None

        assert DittoCoder(provider="Anthropic", model="x", base_url="http://localhost:11434").base_url is None

    def test_switching_provider_and_offline_mode(self, ditto):
        ditto.reinitialize(provider="Google", model="gemini-2.0-flash")
        assert ditto.litellm_model == "gemini/gemini-2.0-flash"

        ditto.reinitialize(offline_mode=True)
        assert ditto.offline_mode is True

        ditto.reinitialize(provider="Ollama", model="llama3.2", base_url="http://localhost:11434", offline_mode=False)
        assert ditto.litellm_model == "ollama/llama3.2"
        assert ditto.base_url == "http://localhost:11434"

    def test_completion_receives_endpoint_and_key(self, ditto, monkeypatch):
        calls = []

        def fake_completion(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

        monkeypatch.setattr("orchestrator.Core.ditto_manager.completion", fake_completion)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-leak-1234567890")

        ditto.reinitialize(provider="Custom", model="m", base_url="https://proxy.example.com/v1")
        assert ditto._query_llm([{"role": "user", "content": "hi"}]) == "ok"
        ditto.reinitialize(provider="Anthropic", model="claude-haiku-5-5", api_key="sk-ant-key-1234567890abcdef")
        ditto._query_llm([{"role": "user", "content": "hi"}])

        assert calls[0]["api_base"] == "https://proxy.example.com/v1"
        assert calls[0]["api_key"] == "EMPTY"
        assert calls[0]["model"] == "openai/m"
        assert calls[1]["api_base"] is None
        assert calls[1]["api_key"] == "sk-ant-key-1234567890abcdef"

    def test_offline_mode_disables_rag(self, booted_framework):
        fm = booted_framework
        fm.config.set("enable_rag_knowledge", True)
        fm.ensure_rag_manager()
        assert fm.ditto_manager._get_rag_manager() is not None

        fm.ditto_manager.reinitialize(offline_mode=True)
        fm.config.set("offline_mode", True)

        assert fm.ditto_manager._get_rag_manager() is None
        with pytest.raises(RuntimeError, match="Offline"):
            fm.rag_manager._get_embedding("question")


class TestTargets:

    def test_bundled_targets_are_loaded(self, tmp_path):
        from orchestrator.Core.target_manager import TargetManager

        tm = TargetManager(SimpleNamespace(config=ConfigManager(tmp_path)))
        tm.targets_dir = REPO_ROOT / "targets"
        tm.initialize()

        assert "Rockchip" in tm.list_hardware_profiles()
        assert "_template" not in tm.list_hardware_profiles()


class TestCLI:

    def run_cli(self, tmp_path, *args):
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
        return subprocess.run([sys.executable, "-m", "orchestrator.cli", *args],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)

    def test_help_works(self, tmp_path):
        result = self.run_cli(tmp_path, "--help")
        assert result.returncode == 0
        assert "targets" in result.stdout

    def test_json_output_is_not_polluted_by_logs(self, tmp_path):
        result = self.run_cli(tmp_path, "targets", "list", "-f", "json")
        assert result.returncode == 0, result.stderr
        assert isinstance(json.loads(result.stdout), list)
