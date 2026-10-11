"""GUI dialog tests (offscreen Qt) against a real ConfigManager."""

import gc
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.config_manager import ConfigManager
from orchestrator.gui import dialogs
from orchestrator.gui.dialogs import AIConfigurationDialog, URLInputDialog


class FakeSecrets:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get_secret(self, key):
        return self.store.get(key)

    def set_secret(self, key, value):
        self.store[key] = value
        return True


class FakeDitto:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1


class FakeFramework:
    def __init__(self, config, secrets=None):
        self.config = config
        self.secrets_manager = secrets or FakeSecrets()
        self.ditto_manager = FakeDitto()
        self.rag_requests = 0

    def ensure_rag_manager(self):
        self.rag_requests += 1


@pytest.fixture(autouse=True)
def collect_widgets_on_the_main_thread():
    """Dialogs without a parent belong to Python. If the garbage collector ran inside a model-list worker thread,
    it would destroy their widgets off the GUI thread and crash the process; collect them here instead."""
    gc.collect()
    yield
    for thread in threading.enumerate():
        if thread.name == "ai-model-list":
            thread.join(timeout=5)
    gc.collect()


@pytest.fixture
def message_boxes(monkeypatch):
    shown = []

    class RecordingMessageBox:
        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", title))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", title))

        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", title))

    monkeypatch.setattr(dialogs, "QMessageBox", RecordingMessageBox)
    return shown


@pytest.fixture(autouse=True)
def model_lists(monkeypatch):
    """No network: the live model list comes from here (default: unavailable)."""
    calls, answers = [], {}

    def lister(config, secrets, provider):
        calls.append(SimpleNamespace(provider=provider, model=config.get("ai_model"),
                                     base_url=config.get("ai_base_url"), offline=config.get("offline_mode"),
                                     key=secrets.get_secret("anthropic_api_key"),
                                     workspace=config.get("ai_anthropic_workspace_id")))
        result = answers.get(provider, RuntimeError("no network in tests"))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(AIConfigurationDialog, "model_lister", staticmethod(lister))
    return SimpleNamespace(calls=calls, answers=answers)


@pytest.fixture
def framework(tmp_path):
    return FakeFramework(ConfigManager(tmp_path))


def select_provider(dlg, provider_id):
    dlg.cb_provider.setCurrentIndex(dlg.cb_provider.findData(provider_id))


def wait_for(qapp, condition, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


class TestAIConfigurationDialog:

    def test_providers_come_from_the_catalog(self, qapp, framework):
        from orchestrator.llm import load_catalog
        dlg = AIConfigurationDialog(framework)
        catalog = load_catalog()
        items = {dlg.cb_provider.itemData(i): dlg.cb_provider.itemText(i) for i in range(dlg.cb_provider.count())}
        assert items == {pid: preset.label for pid, preset in catalog.providers.items()}

    def test_loads_saved_selection_including_old_provider_names(self, qapp, framework):
        framework.config.set("ai_provider", "Anthropic")
        framework.config.set("ai_model", "claude-sonnet-5-5")

        dlg = AIConfigurationDialog(framework)

        assert dlg.cb_provider.currentData() == "anthropic"
        assert dlg.cb_model.currentText() == "claude-sonnet-5-5"
        assert dlg.txt_api_key.isEnabled()
        assert not dlg.txt_base_url.isEnabled()

    def test_default_model_of_another_provider_is_not_shown(self, qapp, framework):
        framework.config.set("ai_provider", "anthropic")  # ai_model keeps the schema default (an OpenAI model)
        dlg = AIConfigurationDialog(framework)
        assert dlg.cb_model.currentText() == "claude-opus-5-5"
        assert dlg.cb_model.findText("claude-haiku-5-5") >= 0

    def test_cloud_provider_requires_api_key(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openai")

        dlg._save_config()

        assert message_boxes == [("warning", "Missing API Key")]
        assert dlg.result() == 0
        assert dlg.get_config() == {}
        assert framework.ditto_manager.reloads == 0

    def test_save_writes_keyring_config_and_reloads_ditto(self, qapp, framework, tmp_path, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.cb_model.setCurrentText("claude-opus-5-5")
        dlg.txt_api_key.setText("sk-ant-test-key-0123456789abcdef")
        dlg.cb_effort.setCurrentIndex(dlg.cb_effort.findData("high"))
        dlg.cb_security.setCurrentIndex(dlg.cb_security.findData("BALANCED"))
        dlg.txt_fallback.setText("ollama:qwen3.5:4b, local")
        dlg.chk_enable_rag.setChecked(True)

        dlg._save_config()

        assert message_boxes == []
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.secrets_manager.store["anthropic_api_key"] == "sk-ant-test-key-0123456789abcdef"
        assert framework.rag_requests == 1
        assert framework.ditto_manager.reloads == 1

        reloaded = ConfigManager(tmp_path)
        assert reloaded.get("ai_provider") == "anthropic"
        assert reloaded.get("ai_model") == "claude-opus-5-5"
        assert reloaded.get("ai_effort") == "high"
        assert reloaded.get("ai_security_level") == "BALANCED"
        assert reloaded.get("ai_fallback_chain") == ["ollama:qwen3.5:4b", "local"]
        assert reloaded.get("enable_rag_knowledge") is True
        assert "sk-ant-test-key" not in (tmp_path / "config.yml").read_text()

        result = dlg.get_config()
        assert result["provider"] == "anthropic" and "api_key" not in result
        assert "sk-ant-test-key" not in repr(result)

    def test_saved_settings_are_shown_again(self, qapp, framework):
        framework.config.set("ai_provider", "ollama")
        framework.config.set("ai_effort", "low")
        framework.config.set("ai_security_level", "OFF")
        framework.config.set("ai_fallback_chain", ["local"])
        dlg = AIConfigurationDialog(framework)
        assert dlg.cb_effort.currentData() == "low"
        assert dlg.cb_security.currentData() == "OFF"
        assert dlg.txt_fallback.text() == "local"

    def test_stored_key_is_reused(self, qapp, tmp_path, message_boxes):
        fw = FakeFramework(ConfigManager(tmp_path), FakeSecrets({"openai_api_key": "sk-stored-0123456789abcdefghij"}))
        dlg = AIConfigurationDialog(fw)
        select_provider(dlg, "openai")

        assert "Stored" in dlg.txt_api_key.placeholderText()
        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert fw.secrets_manager.store == {"openai_api_key": "sk-stored-0123456789abcdefghij"}

    def test_ollama_needs_no_key(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")

        assert not dlg.txt_api_key.isEnabled()
        assert dlg.txt_base_url.isEnabled()

        dlg.txt_base_url.setText("http://192.168.1.20:11434/v1")
        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.config.get("ai_base_url") == "http://192.168.1.20:11434/v1"

    def test_custom_endpoint_requires_base_url(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "custom")
        dlg.cb_model.setCurrentText("my-model")

        dlg._save_config()

        assert message_boxes == [("warning", "Missing Base URL")]
        assert dlg.result() == 0

    def test_offline_mode_accepts_only_local_providers(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openai")
        dlg.txt_api_key.setText("sk-proj-0123456789abcdefghijkl")
        dlg.chk_offline.setChecked(True)

        dlg._save_config()
        assert message_boxes == [("warning", "Offline Mode")] and dlg.result() == 0

        select_provider(dlg, "ollama")
        dlg._save_config()
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.config.get("offline_mode") is True and framework.config.get("ai_provider") == "ollama"

    def test_switching_to_cloud_provider_drops_old_base_url(self, qapp, framework, tmp_path, message_boxes):
        custom = AIConfigurationDialog(framework)
        select_provider(custom, "custom")
        custom.cb_model.setCurrentText("my-model")
        custom.txt_base_url.setText("https://third-party-proxy.example.com/v1")
        custom._save_config()
        assert framework.config.get("ai_base_url") == "https://third-party-proxy.example.com/v1"

        dlg = AIConfigurationDialog(framework)
        assert dlg.txt_base_url.text() == "https://third-party-proxy.example.com/v1"
        select_provider(dlg, "openai")
        dlg.cb_model.setCurrentText("gpt-6.1-sol")
        dlg.txt_api_key.setText("sk-proj-0123456789abcdefghijkl")
        dlg._save_config()

        assert message_boxes == []
        assert dlg.get_config()["base_url"] is None
        assert ConfigManager(tmp_path).get("ai_base_url") == ""

    def test_custom_endpoint_key_is_optional_and_stored_separately(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "custom")
        dlg.cb_model.setCurrentText("my-model")
        dlg.txt_base_url.setText("http://192.168.1.20:8000/v1")
        assert dlg.txt_api_key.isEnabled()

        dlg._save_config()
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.secrets_manager.store == {}

        dlg2 = AIConfigurationDialog(framework)
        assert dlg2.cb_provider.currentData() == "custom"
        dlg2.txt_api_key.setText("custom-secret")
        dlg2._save_config()
        assert framework.secrets_manager.store == {"custom_api_key": "custom-secret"}

    def test_invalid_base_url_is_rejected(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        dlg.txt_base_url.setText("localhost:11434")

        dlg._save_config()

        assert message_boxes == [("warning", "Invalid Base URL")]

    def test_unknown_fallback_provider_is_rejected(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        dlg.txt_fallback.setText("local, skynet:model")

        dlg._save_config()

        assert message_boxes == [("warning", "Invalid Fallback Chain")]

    def test_live_model_list_fills_the_combo(self, qapp, framework, model_lists):
        model_lists.answers["anthropic"] = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-new-model"]
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.txt_api_key.setText("sk-ant-typed-0123456789abcdef")
        dlg.cb_model.setCurrentText("my-pinned-model")
        dlg._refresh_models()

        assert wait_for(qapp, lambda: dlg.cb_model.findText("claude-new-model") >= 0)
        assert dlg.cb_model.currentText() == "my-pinned-model", "a typed model id stays"
        last = model_lists.calls[-1]
        assert (last.provider, last.model, last.key) == ("anthropic", "my-pinned-model",
                                                          "sk-ant-typed-0123456789abcdef")
        assert "3 models" in dlg.lbl_models.text()
        assert framework.secrets_manager.store == {}, "listing models stores nothing"

    def test_failed_model_list_keeps_free_text(self, qapp, framework, model_lists):
        from orchestrator.llm import AuthFailed
        model_lists.answers["openrouter"] = AuthFailed("OpenRouter rejected the credentials.")
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openrouter")
        dlg.cb_model.setCurrentText("vendor/some-model")

        assert wait_for(qapp, lambda: "unavailable" in dlg.lbl_models.text())
        assert "rejected the credentials" in dlg.lbl_models.text()
        assert dlg.cb_model.currentText() == "vendor/some-model"

    def test_unsaved_choices_are_used_for_the_model_list(self, qapp, framework, model_lists):
        model_lists.answers["ollama"] = ["qwen3.5:9b"]
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        dlg.txt_base_url.setText("http://192.168.1.20:11434/v1")
        dlg.chk_offline.setChecked(True)
        dlg._refresh_models()
        assert wait_for(qapp, lambda: dlg.cb_model.findText("qwen3.5:9b") >= 0)
        assert (model_lists.calls[-1].base_url, model_lists.calls[-1].offline) == ("http://192.168.1.20:11434/v1",
                                                                                     True)
        assert framework.config.get("ai_base_url") == "", "nothing is saved before Save"

    def test_removed_provider_is_explained_once(self, qapp, framework, monkeypatch):
        shown = []

        class Boxes:
            @staticmethod
            def information(parent, title, text, *args):
                shown.append((title, text))

        monkeypatch.setattr(dialogs, "QMessageBox", Boxes)
        framework.config.set("ai_provider", "claude_code")
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        select_provider(dlg, "anthropic")

        assert len(shown) == 1 and shown[0][0] == "AI Provider Removed"
        for alternative in ("Claude API key", "Max and Team", "Bedrock, Vertex or Foundry", "llm-cli mcp config"):
            assert alternative in shown[0][1]
        assert dlg.cb_provider.findData("claude_code") < 0

    def test_claude_needs_a_key_unless_the_profile_was_chosen(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        assert dlg.txt_api_key.placeholderText() == "Paste API key"
        dlg._save_config()
        assert message_boxes == [("warning", "Missing API Key")]

        framework.config.set("ai_auth_anthropic", "ant_profile")
        message_boxes.clear()
        dlg._save_config()
        assert message_boxes == [] and dlg.result() == dialogs.QDialog.Accepted

    def test_workspace_id_is_validated_and_saved(self, qapp, framework, tmp_path, message_boxes):
        workspace = "wrkspc_" + "01Abc"
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openai")
        assert not dlg.txt_workspace.isEnabled()
        select_provider(dlg, "anthropic")
        assert dlg.txt_workspace.isEnabled()
        dlg.txt_api_key.setText("sk-ant-" + "test-key-0123456789abcdef")

        dlg.txt_workspace.setText("workspace-1")
        dlg._save_config()
        assert message_boxes == [("warning", "Invalid Workspace ID")] and dlg.result() == 0
        assert framework.secrets_manager.store == {}

        message_boxes.clear()
        dlg.txt_workspace.setText(f" {workspace} ")
        dlg._save_config()
        assert message_boxes == [] and dlg.result() == dialogs.QDialog.Accepted
        assert ConfigManager(tmp_path).get("ai_anthropic_workspace_id") == workspace
        assert AIConfigurationDialog(framework).txt_workspace.text() == workspace

    def test_unsaved_workspace_id_is_used_for_the_model_list(self, qapp, framework, model_lists):
        workspace = "wrkspc_" + "01Abc"
        model_lists.answers["anthropic"] = ["claude-opus-5-5"]
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.txt_workspace.setText(workspace)
        dlg._refresh_models()
        assert wait_for(qapp, lambda: "1 models" in dlg.lbl_models.text())
        assert model_lists.calls[-1].workspace == workspace

    def test_admin_key_is_refused(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.txt_api_key.setText("sk-ant-" + "admin01-" + "Z" * 32)
        dlg._save_config()
        assert message_boxes == [("warning", "Unsuitable API Key")]
        assert framework.secrets_manager.store == {} and dlg.result() == 0

    def test_sign_in_placeholder(self, qapp, framework):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        assert dlg.btn_sign_in.text() == "Sign in..." and not dlg.btn_sign_in.isEnabled()
        assert dlg._sign_in() is None

    def test_works_without_framework(self, qapp, message_boxes):
        dlg = AIConfigurationDialog(None)
        select_provider(dlg, "ollama")

        dlg._save_config()

        assert dlg.get_config()["provider"] == "ollama"


class TestURLInputDialog:

    def test_get_urls_ignores_blank_lines(self, qapp):
        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("  https://docs.radxa.com/ \n\n\nhttps://wiki.example.org/\n")
        assert dlg.get_urls() == ["https://docs.radxa.com/", "https://wiki.example.org/"]

    @pytest.mark.parametrize("bad_url", [
        "not-a-url", "javascript:alert(1)", "ftp://files.example.com/", "file:///etc/passwd", "../../../etc/passwd",
    ])
    def test_rejects_non_http_urls(self, qapp, message_boxes, bad_url):
        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText(f"https://docs.radxa.com/\n{bad_url}")

        dlg.validate_and_accept()

        assert message_boxes == [("warning", "Invalid URLs")]
        assert dlg.result() == 0

    def test_accepts_http_urls(self, qapp, message_boxes):
        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("https://docs.radxa.com/\nhttp://example.com/docs")

        dlg.validate_and_accept()

        assert message_boxes == []
        assert dlg.result() == dialogs.QDialog.Accepted

    def test_empty_input_is_rejected(self, qapp, message_boxes):
        dlg = URLInputDialog()
        dlg.validate_and_accept()
        assert message_boxes == [("warning", "No URLs")]

    def test_default_options(self, qapp):
        assert URLInputDialog().get_options() == {
            "max_depth": 2, "respect_robots": True, "download_pdfs": True, "request_delay": 1,
        }


class TestCrawlOptionWiring:
    """Options chosen in URLInputDialog apply to that crawl only."""

    def test_options_reach_the_crawler_without_changing_global_config(self, qapp, tmp_path):
        from orchestrator.Core.crawler_manager import CrawlerManager
        from orchestrator.gui.wizards import CrawlWorker

        cfg = ConfigManager(tmp_path)
        seen = []

        class FakeRag:
            framework = SimpleNamespace(config=cfg)

            def ingest_url(self, url, crawler_options=None):
                crawler = CrawlerManager(self.framework, crawler_options)
                seen.append((url, crawler.max_depth, crawler.respect_robots, crawler.download_pdfs, crawler.rate_limit))
                return {"success": True, "count": 3}

        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("https://docs.radxa.com/")
        dlg.spin_depth.setValue(4)
        dlg.chk_robots.setChecked(False)
        dlg.chk_pdfs.setChecked(False)
        dlg.spin_delay.setValue(3)

        finished = []
        worker = CrawlWorker(FakeRag(), dlg.get_urls(), dlg.get_options())
        worker.finished.connect(finished.append)
        worker.run()

        assert seen == [("https://docs.radxa.com/", 4, False, False, 3.0)]
        assert finished == ["Deep Ingest complete. Added 3 knowledge chunks."]
        assert cfg.get("crawler_respect_robots") is True
        assert cfg.get("crawler_max_depth") == 2
        assert CrawlerManager(SimpleNamespace(config=cfg)).respect_robots is True


class TestMainWindowWiring:

    def test_main_window_exposes_ai_config_and_wizard(self, qapp, tmp_path, monkeypatch, memory_keyring):
        import shutil
        from PySide6.QtWidgets import QMenu
        from conftest import REPO_ROOT
        from orchestrator.Core.framework import FrameworkManager
        from orchestrator.gui import main_window
        from orchestrator.gui.wizards import ModuleCreationWizard

        monkeypatch.chdir(tmp_path)
        (tmp_path / "configs").mkdir()

        class TmpFramework(FrameworkManager):
            def __init__(self, *args, **kwargs):
                super().__init__(str(tmp_path / "configs"))
                self.info.installation_path = tmp_path

        monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
        monkeypatch.setattr(main_window.ModuleCreationWizard, "exec", lambda self: 0)
        # The first-run permissions dialog is modal; a later processEvents() in another test would block on it.
        opened = []
        monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions",
                            lambda self, first_run=False: opened.append(first_run))

        win = main_window.MainOrchestrator(REPO_ROOT)
        try:
            qapp.processEvents()
            assert opened == [True], "the permissions are asked once at the first start"
            menus = {m.title(): [a.text() for a in m.actions()] for m in win.findChildren(QMenu) if m.title()}
            assert menus["Tools"] == ["Configure AI Agent (Ditto)...", "Create Hardware Module...",
                                      "Tool Permissions (Ditto / MCP)..."]

            wizard = ModuleCreationWizard(tmp_path / "targets", win)
            assert wizard.framework_manager is win.framework
            assert len(wizard.pageIds()) == 5

            # A module created by the wizard shows up without restarting the app.
            assert win.builder_tab.target_combo.count() == 0
            shutil.copytree(REPO_ROOT / "targets" / "Rockchip", tmp_path / "targets" / "Rockchip",
                            ignore=shutil.ignore_patterns("scripts", "modules"))
            win._open_module_wizard()
            assert win.builder_tab.target_combo.count() == 1
            assert win.deployment_window.cb_profiles.itemText(0) == "Rockchip"

            win._update_system_state()
        finally:
            win.update_timer.stop()
            win.close()
