#!/usr/bin/env python3
"""GUI dialog tests (offscreen Qt) against a real ConfigManager."""

import sys
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
        self.calls = []

    def reinitialize(self, **kwargs):
        self.calls.append(kwargs)


class FakeFramework:
    def __init__(self, config, secrets=None):
        self.config = config
        self.secrets_manager = secrets or FakeSecrets()
        self.ditto_manager = FakeDitto()
        self.rag_requests = 0

    def ensure_rag_manager(self):
        self.rag_requests += 1


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


@pytest.fixture
def framework(tmp_path):
    return FakeFramework(ConfigManager(tmp_path))


def select_provider(dlg, provider_id):
    dlg.cb_provider.setCurrentIndex(dlg.cb_provider.findData(provider_id))


class TestAIConfigurationDialog:

    def test_loads_saved_selection(self, qapp, framework):
        framework.config.set("ai_provider", "Anthropic")
        framework.config.set("ai_model", "claude-sonnet-5-5")

        dlg = AIConfigurationDialog(framework)

        assert dlg.cb_provider.currentData() == "Anthropic"
        assert dlg.cb_model.currentText() == "claude-sonnet-5-5"
        assert dlg.txt_api_key.isEnabled()
        assert not dlg.txt_base_url.isEnabled()

    def test_provider_ids_match_ditto(self, qapp, framework):
        dlg = AIConfigurationDialog(framework)
        ids = {dlg.cb_provider.itemData(i) for i in range(dlg.cb_provider.count())}
        assert ids == {"OpenAI", "Anthropic", "Google", "Ollama", "Custom"}

    def test_cloud_provider_requires_api_key(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "OpenAI")

        dlg._save_config()

        assert message_boxes == [("warning", "Missing API Key")]
        assert dlg.result() == 0
        assert dlg.get_config() == {}
        assert framework.ditto_manager.calls == []

    def test_save_writes_keyring_config_and_ditto(self, qapp, framework, tmp_path, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "Anthropic")
        dlg.cb_model.setCurrentText("claude-opus-5-5")
        dlg.txt_api_key.setText("sk-ant-test-key-0123456789abcdef")
        dlg.chk_enable_rag.setChecked(True)

        dlg._save_config()

        assert message_boxes == []
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.secrets_manager.store["anthropic_api_key"] == "sk-ant-test-key-0123456789abcdef"
        assert framework.rag_requests == 1
        assert framework.ditto_manager.calls[-1]["provider"] == "Anthropic"
        assert framework.ditto_manager.calls[-1]["model"] == "claude-opus-5-5"

        reloaded = ConfigManager(tmp_path)
        assert reloaded.get("ai_provider") == "Anthropic"
        assert reloaded.get("ai_model") == "claude-opus-5-5"
        assert reloaded.get("enable_rag_knowledge") is True
        assert "sk-ant-test-key" not in (tmp_path / "config.yml").read_text()

        assert dlg.get_config()["provider"] == "Anthropic"

    def test_stored_key_is_reused(self, qapp, tmp_path, message_boxes):
        fw = FakeFramework(ConfigManager(tmp_path), FakeSecrets({"openai_api_key": "sk-stored-0123456789abcdefghij"}))
        dlg = AIConfigurationDialog(fw)
        select_provider(dlg, "OpenAI")

        assert "Stored" in dlg.txt_api_key.placeholderText()
        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert fw.secrets_manager.store == {"openai_api_key": "sk-stored-0123456789abcdefghij"}

    def test_ollama_needs_no_key(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "Ollama")

        assert not dlg.txt_api_key.isEnabled()
        assert dlg.txt_base_url.isEnabled()

        dlg.txt_base_url.setText("http://localhost:11434")
        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.ditto_manager.calls[-1]["base_url"] == "http://localhost:11434"

    def test_custom_endpoint_requires_base_url(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "Custom")
        dlg.cb_model.setCurrentText("my-model")

        dlg._save_config()

        assert message_boxes == [("warning", "Missing Base URL")]
        assert dlg.result() == 0

    def test_offline_mode_skips_cloud_requirements(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "OpenAI")
        dlg.chk_offline.setChecked(True)

        assert not dlg.cb_provider.isEnabled()
        assert not dlg.txt_api_key.isEnabled()

        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.ditto_manager.calls[-1]["offline_mode"] is True
        assert framework.config.get("offline_mode") is True

    def test_works_without_framework(self, qapp, message_boxes):
        dlg = AIConfigurationDialog(None)
        select_provider(dlg, "Ollama")

        dlg._save_config()

        assert dlg.get_config()["provider"] == "Ollama"


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
    """Options chosen in URLInputDialog must reach the CrawlerManager that RAG creates."""

    def test_options_flow_from_dialog_to_crawler(self, qapp, tmp_path):
        from orchestrator.Core.crawler_manager import CrawlerManager
        from orchestrator.gui.wizards import CrawlWorker

        cfg = ConfigManager(tmp_path)
        seen = []

        class FakeRag:
            framework = SimpleNamespace(config=cfg)

            def ingest_url(self, url):
                crawler = CrawlerManager(self.framework)
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


class TestMainWindowWiring:

    def test_main_window_exposes_ai_config_and_wizard(self, qapp, tmp_path, monkeypatch, memory_keyring):
        monkeypatch.chdir(tmp_path)
        from PySide6.QtWidgets import QMenu
        from conftest import REPO_ROOT
        from orchestrator.gui.main_window import MainOrchestrator
        from orchestrator.gui.wizards import ModuleCreationWizard

        win = MainOrchestrator(REPO_ROOT)
        try:
            menus = {m.title(): [a.text() for a in m.actions()] for m in win.findChildren(QMenu) if m.title()}
            assert menus["Tools"] == ["Configure AI Agent (Ditto)...", "Create Hardware Module..."]

            wizard = ModuleCreationWizard(tmp_path / "targets", win)
            assert wizard.framework_manager is win.framework
            assert len(wizard.pageIds()) == 5

            win._update_system_state()
        finally:
            win.update_timer.stop()
            win.close()


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
