"""GUI dialog tests (offscreen Qt) against a real ConfigManager."""

import gc
import json
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
        Yes, No = dialogs.QMessageBox.Yes, dialogs.QMessageBox.No
        answer, asked = dialogs.QMessageBox.Yes, []  # questions are answered, not counted as messages

        @classmethod
        def question(cls, parent, title, text, *args):
            cls.asked.append(text)
            return cls.answer

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

    def test_offline_mode_accepts_a_custom_server_only_on_the_local_network(self, qapp, framework,
                                                                              message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "custom")
        dlg.cb_model.setCurrentText("my-model")
        dlg.chk_offline.setChecked(True)
        dlg.txt_base_url.setText("https://api.example.com/v1")
        dlg._save_config()
        assert message_boxes == [("warning", "Offline Mode")] and dlg.result() == 0
        assert framework.config.get("offline_mode") is not True, "nothing is saved"

        dlg.txt_base_url.setText("http://192.168.1.30:8000/v1")
        dlg._save_config()
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.config.get("ai_provider") == "custom" and framework.config.get("offline_mode") is True

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

    def test_custom_key_belongs_to_the_address_it_was_saved_for(self, qapp, tmp_path, memory_keyring,
                                                                 message_boxes):
        from orchestrator.Core.secrets_manager import SecretsManager, credential_name
        vault = SecretsManager(tmp_path / "state")
        assert vault.initialize(), vault.storage_error
        fw = FakeFramework(ConfigManager(tmp_path), vault)
        key = "custom-" + "secret-0123456789"
        dlg = AIConfigurationDialog(fw)
        select_provider(dlg, "custom")
        dlg.cb_model.setCurrentText("my-model")
        dlg.txt_base_url.setText("http://192.168.1.20:8000/v1")
        dlg.txt_api_key.setText(key)
        dlg._save_config()
        assert message_boxes == [] and dlg.result() == dialogs.QDialog.Accepted
        assert vault.list_secrets() == [credential_name("custom", "api_key", "http://192.168.1.20:8000")]

        dlg2 = AIConfigurationDialog(fw)
        assert "Stored" in dlg2.txt_api_key.placeholderText()
        dlg2.txt_base_url.setText("http://192.168.1.21:8000/v1")  # another server: its key is not this one
        dlg2._apply_enabled_state()
        assert "Stored" not in dlg2.txt_api_key.placeholderText()

    def test_the_key_hint_never_moves_a_key_to_an_address_being_typed(self, qapp, tmp_path, memory_keyring,
                                                                       message_boxes):
        from orchestrator.Core.secrets_manager import SecretsManager
        vault = SecretsManager(tmp_path / "state")
        assert vault.initialize(), vault.storage_error
        assert vault.set_secret("custom_api_key", "custom-" + "secret-0123456789")  # saved by an earlier version
        dlg = AIConfigurationDialog(FakeFramework(ConfigManager(tmp_path), vault))
        select_provider(dlg, "custom")
        dlg.txt_base_url.setText("http://192.168.1.2")  # half typed
        dlg.chk_offline.setChecked(True)  # updates the hint for the address typed so far
        assert "Stored" in dlg.txt_api_key.placeholderText()
        dlg.reject()
        assert vault.list_secrets() == ["cred/custom/api_key"]  # still waiting for the address it is used with

    def test_the_unsaved_key_overlay_reports_an_unusable_vault(self):
        broken = SimpleNamespace(storage_error=RuntimeError("locked"), load_error=None, get_secret=lambda key: None)
        overlay = dialogs._SecretsOverlay(broken, "anthropic_api_key", "")
        assert overlay.storage_error is broken.storage_error and overlay.load_error is None

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

    def test_model_lists_reach_the_gui_thread_through_the_lasting_relay(self, qapp, framework, model_lists):
        """The worker used to emit on a QObject of its own and drop it in the worker thread, which aborted the
        process now and then. It now emits only on the relay that lives as long as the application, queued to
        the GUI thread, and each dialog takes only its own latest list."""
        model_lists.answers["ollama"] = ["qwen3.5:9b"]
        arrived = []

        class Recording(AIConfigurationDialog):
            def _on_models_listed(self, request, ids, message):
                arrived.append(threading.current_thread() is threading.main_thread())
                super()._on_models_listed(request, ids, message)

        from orchestrator.gui.worker_relay import worker_relay
        relay = worker_relay()
        assert relay is worker_relay() and relay.parent() is None
        assert not hasattr(dialogs, "model_list_relay"), "one relay for every GUI worker (folded into WorkerRelay)"
        first, second = Recording(framework), Recording(framework)
        select_provider(first, "ollama")
        assert wait_for(qapp, lambda: first.cb_model.findText("qwen3.5:9b") >= 0)
        assert arrived and all(arrived)
        assert second.cb_model.findText("qwen3.5:9b") < 0, "another dialog's list does not change this one"

    def test_closing_dialogs_while_their_lists_load_is_safe(self, qapp, framework, model_lists):
        model_lists.answers["ollama"] = [f"model-{i}" for i in range(20)]
        for round_ in range(60):
            dlg = AIConfigurationDialog(framework)
            select_provider(dlg, "ollama")
            dlg._refresh_models()
            qapp.processEvents()
            dlg.close()
            dlg.deleteLater()
            del dlg
            qapp.processEvents()
            if round_ % 10 == 0:
                gc.collect()
        last = AIConfigurationDialog(framework)
        select_provider(last, "ollama")
        assert wait_for(qapp, lambda: last.cb_model.findText("model-19") >= 0)

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

    def test_model_list_never_sends_a_key_to_the_saved_providers_url(self, qapp, tmp_path, monkeypatch):
        """Saved: Ollama on a LAN host. Choosing "custom" (with a stored custom key) before typing its address must
        not list models at the saved Ollama address with the custom key: the address field always counts for the
        list, an empty one meaning the provider's own endpoint (none for custom)."""
        from orchestrator.llm import ModelInfo
        from orchestrator.llm.providers.base import ProviderAdapter
        seen = []

        class Recorder(ProviderAdapter):
            def list_models(self):
                seen.append((self.preset.id, self.base_url, self._api_key))
                return [ModelInfo(id="served-model")]

        monkeypatch.setattr("orchestrator.llm.client.adapter_class", lambda name: Recorder)
        monkeypatch.setattr(AIConfigurationDialog, "model_lister", staticmethod(dialogs.list_provider_models))
        config = ConfigManager(tmp_path)
        config.set("ai_provider", "ollama")
        config.set("ai_base_url", "http://192.168.1.20:11434/v1")
        fw = FakeFramework(config, FakeSecrets({"custom_api_key": "custom-secret-0123456789"}))
        dlg = AIConfigurationDialog(fw)
        assert wait_for(qapp, lambda: any(s[:2] == ("ollama", "http://192.168.1.20:11434/v1") for s in seen)), seen

        select_provider(dlg, "custom")
        assert wait_for(qapp, lambda: any(provider == "custom" for provider, _, _ in seen)), seen
        # No address for custom: the request has none (the real adapter refuses it: "no server URL is set"), never
        # the saved Ollama address.
        assert [(url, key) for provider, url, key in seen if provider == "custom"] == [(None, None)], seen
        assert not any(key == "custom-secret-0123456789" for _, url, key in seen if "192.168.1.20" in str(url))

        dlg.txt_base_url.setText("http://192.168.1.30:8000/v1")
        dlg._refresh_models()
        assert wait_for(qapp, lambda: any(s[:2] == ("custom", "http://192.168.1.30:8000/v1") for s in seen)), seen

    def test_model_list_sends_a_typed_key_only_to_the_typed_address(self, qapp, tmp_path, monkeypatch):
        """The provider's own address from `llm-cli ditto login --base-url` (ai_base_url_<provider>) wins over
        ai_base_url in the LLM layer. The address field shows it; when the person types another address and a key for
        it, the model list goes there, as the save would (AISettingsChange.apply writes the field into that setting),
        never with the new key to the old address."""
        from orchestrator.llm import ModelInfo
        from orchestrator.llm.providers.base import ProviderAdapter
        seen = []

        class Recorder(ProviderAdapter):
            def list_models(self):
                seen.append((self.preset.id, self.base_url, self._api_key))
                return [ModelInfo(id="served-model")]

        monkeypatch.setattr("orchestrator.llm.client.adapter_class", lambda name: Recorder)
        monkeypatch.setattr(AIConfigurationDialog, "model_lister", staticmethod(dialogs.list_provider_models))
        config = ConfigManager(tmp_path)
        config.set("ai_provider", "custom")
        config.set("ai_base_url_custom", "http://192.168.1.30:8000/v1")
        dlg = AIConfigurationDialog(FakeFramework(config, FakeSecrets()))
        assert dlg.txt_base_url.text() == "http://192.168.1.30:8000/v1"
        assert wait_for(qapp, lambda: any(s[:2] == ("custom", "http://192.168.1.30:8000/v1") for s in seen)), seen

        dlg.txt_base_url.setText("http://192.168.1.40:8000/v1")
        dlg.txt_api_key.setText("typed-key-for-the-new-server-0123")
        seen.clear()
        dlg._refresh_models()
        assert wait_for(qapp, lambda: bool(seen)), seen
        assert seen == [("custom", "http://192.168.1.40:8000/v1", "typed-key-for-the-new-server-0123")], seen

        dlg.txt_base_url.setText("")   # empty: the provider's own endpoint (custom has none), never the old address
        seen.clear()
        dlg._refresh_models()
        assert wait_for(qapp, lambda: bool(seen)), seen
        assert [url for _, url, _ in seen] == [None], seen

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

    def test_removed_provider_in_the_fallback_chain_is_explained(self, qapp, framework, monkeypatch):
        shown = []

        class Boxes:
            @staticmethod
            def information(parent, title, text, *args):
                shown.append(("information", title, text))

            @staticmethod
            def warning(parent, title, text, *args):
                shown.append(("warning", title, text))

        monkeypatch.setattr(dialogs, "QMessageBox", Boxes)
        framework.config.set("ai_provider", "ollama")
        framework.config.set("ai_fallback_chain", ["claude_code"])
        dlg = AIConfigurationDialog(framework)
        assert [entry[:2] for entry in shown] == [("information", "AI Provider Removed")]
        assert "llm-cli mcp config" in shown[0][2]

        dlg._save_config()
        assert shown[-1][:2] == ("warning", "Invalid Fallback Chain") and "llm-cli mcp config" in shown[-1][2]
        assert dlg.result() == 0 and framework.config.get("ai_fallback_chain") == ["claude_code"]

    def test_workspace_id_only_for_the_claude_api(self, qapp, framework, tmp_path):
        (tmp_path / "llm_providers.yml").write_text(
            "providers:\n  gateway:\n    label: Gateway\n    adapter: anthropic\n"
            "    base_url: https://gateway.example.com\n    secret: anthropic_api_key\n", encoding="utf-8")
        framework.config.set("ai_anthropic_workspace_id", "wrkspc_" + "01Abc")
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "gateway")
        assert not dlg.txt_workspace.isEnabled() and dlg.txt_workspace.text() == ""
        select_provider(dlg, "anthropic")
        assert dlg.txt_workspace.isEnabled() and dlg.txt_workspace.text() == "wrkspc_" + "01Abc"

    def test_sign_in_opens_the_panel_of_the_selected_provider(self, qapp, framework):
        from orchestrator.gui.sign_in import SignInPanel
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        assert dlg.btn_sign_in.text() == "Sign in..." and dlg.btn_sign_in.isEnabled()
        window = dlg._sign_in()
        assert isinstance(window.panel, SignInPanel) and window.panel.preset.id == "anthropic"
        assert wait_for(qapp, lambda: not window.panel.busy)
        window.close()
        without = AIConfigurationDialog(None)
        assert not without.btn_sign_in.isEnabled() and without._sign_in() is None

    def test_a_key_for_plain_http_to_another_computer_needs_a_yes(self, qapp, tmp_path, memory_keyring,
                                                                  message_boxes):
        from orchestrator.Core.secrets_manager import SecretsManager
        vault = SecretsManager(tmp_path / "state")
        assert vault.initialize(), vault.storage_error
        fw = FakeFramework(ConfigManager(tmp_path), vault)
        dialogs.QMessageBox.answer = dialogs.QMessageBox.No

        def save(url):
            dlg = AIConfigurationDialog(fw)
            select_provider(dlg, "custom")
            dlg.cb_model.setCurrentText("my-model")
            dlg.txt_base_url.setText(url)
            dlg.txt_api_key.setText("custom-" + "secret-0123456789")
            dlg._save_config()
            return dlg

        assert save("http://192.0.2.10:8000/v1").result() != dialogs.QDialog.Accepted
        assert vault.list_secrets() == [] and "192.0.2.10:8000" in dialogs.QMessageBox.asked[0]
        dialogs.QMessageBox.answer = dialogs.QMessageBox.Yes
        assert save("http://192.0.2.10:8000/v1").result() == dialogs.QDialog.Accepted and vault.list_secrets()
        assert save("http://127.0.0.1:8000/v1").result() == dialogs.QDialog.Accepted
        assert len(dialogs.QMessageBox.asked) == 2 and message_boxes == [], "this computer: no question"

    def test_a_key_typed_here_replaces_the_method_a_sign_in_remembered(self, qapp, tmp_path, framework,
                                                                      message_boxes):
        # `llm-cli ditto login anthropic --method ant_profile` remembered the profile; a key typed and saved here
        # would otherwise stay unused while the field says it is stored.
        framework.config.set("ai_auth_anthropic", "ant_profile")
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.cb_model.setCurrentText("some-model")
        dlg.txt_api_key.setText("sk-ant-" + "api03-" + "K" * 40)
        dlg._save_config()
        assert message_boxes == [] and dlg.result() == dialogs.QDialog.Accepted
        assert ConfigManager(tmp_path).get("ai_auth_anthropic") == "api_key"
        framework.config.set("ai_auth_anthropic", "ant_profile")
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.cb_model.setCurrentText("some-model")
        dlg._save_config()  # no key typed: the remembered method stays
        assert ConfigManager(tmp_path).get("ai_auth_anthropic") == "ant_profile"

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
        assert URLInputDialog().get_options() == {"max_depth": 2, "download_pdfs": True, "request_delay": 1}

    def test_options_start_from_the_settings_and_the_user_values_count(self, qapp, tmp_path):
        """The dialog is the only way to crawl: it used to open with 2, PDFs on and 1 s (whole seconds) whatever
        crawler_max_depth, crawler_download_pdfs and crawler_rate_limit said, and always passed those."""
        cfg = ConfigManager(tmp_path)
        cfg.set("crawler_max_depth", 0)
        cfg.set("crawler_download_pdfs", False)
        cfg.set("crawler_rate_limit", 0.25)

        dlg = URLInputDialog(config=cfg)
        assert dlg.get_options() == {"max_depth": 0, "download_pdfs": False, "request_delay": 0.25}
        dlg.spin_depth.setValue(4)
        dlg.chk_pdfs.setChecked(True)
        dlg.spin_delay.setValue(1.5)
        assert dlg.get_options() == {"max_depth": 4, "download_pdfs": True, "request_delay": 1.5}

    def test_larger_settings_are_not_cut_down(self, qapp, tmp_path):
        """A configured 30 s delay (a site that asks for it) must not become the dialog's maximum of 10 s."""
        cfg = ConfigManager(tmp_path)
        cfg.set("crawler_max_depth", 8)
        cfg.set("crawler_rate_limit", 30.0)
        assert URLInputDialog(config=cfg).get_options() == {"max_depth": 8, "download_pdfs": True,
                                                            "request_delay": 30.0}
        cfg.set("crawler_max_depth", 2**40)   # beyond what a spin box holds: the dialog still opens
        assert URLInputDialog(config=cfg).get_options()["max_depth"] == 2**31 - 1

    def test_no_switch_to_ignore_robots_txt(self, qapp, tmp_path):
        """robots.txt is a machine-readable opt-out like TDMRep: neither the dialog nor the config turns it off."""
        from PySide6.QtWidgets import QCheckBox
        from orchestrator.Core.crawler_manager import CrawlerManager

        dlg = URLInputDialog()
        assert not [box.text() for box in dlg.findChildren(QCheckBox) if "robots" in box.text().lower()]
        cfg = ConfigManager(tmp_path)
        cfg.set("crawler_respect_robots", False)
        assert "crawler_respect_robots" not in cfg.config_schemas
        assert CrawlerManager(SimpleNamespace(config=cfg), dlg.get_options()).respect_robots is True

    def test_no_recent_list_without_history(self, qapp):
        dlg = URLInputDialog()
        assert dlg.recent_row.isHidden() and dlg.cb_recent.count() == 0

    def test_recent_urls_are_offered_and_added_once(self, qapp):
        dlg = URLInputDialog(recent=["https://docs.radxa.com/", "https://wiki.example.org/"])
        assert not dlg.recent_row.isHidden() and dlg.cb_recent.isEditable()
        assert [dlg.cb_recent.itemText(i) for i in range(dlg.cb_recent.count())] == [
            "https://docs.radxa.com/", "https://wiki.example.org/"]
        assert dlg.cb_recent.currentText() == "" and dlg.get_urls() == []   # offered, not filled in
        assert not dlg.btn_add_recent.autoDefault()   # Enter still means OK

        dlg.cb_recent.setCurrentIndex(1)
        dlg.add_recent_url()
        dlg.add_recent_url()
        assert dlg.get_urls() == ["https://wiki.example.org/"]
        dlg.cb_recent.setEditText(" https://docs.radxa.com/rock5/ ")   # editable: a recent URL can be changed
        dlg.add_recent_url()
        assert dlg.get_urls() == ["https://wiki.example.org/", "https://docs.radxa.com/rock5/"]


class TestCrawlOptionWiring:
    """Options chosen in URLInputDialog apply to that crawl only."""

    def test_options_reach_the_crawler_without_changing_global_config(self, qapp, tmp_path):
        from orchestrator.Core.crawler_manager import CrawlerManager
        from orchestrator.gui.wizards import CrawlWorker

        cfg = ConfigManager(tmp_path)
        seen = []

        class FakeRag:
            framework = SimpleNamespace(config=cfg)

            def ingest_url(self, url, crawler_options=None, **kwargs):
                crawler = CrawlerManager(self.framework, crawler_options)
                seen.append((url, crawler.max_depth, crawler.download_pdfs, crawler.rate_limit))
                return {"success": True, "outcome": "success", "count": 3, "documents": 1}

        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("https://docs.radxa.com/")
        dlg.spin_depth.setValue(4)
        dlg.chk_pdfs.setChecked(False)
        dlg.spin_delay.setValue(3)

        finished = []
        worker = CrawlWorker(FakeRag(), dlg.get_urls(), dlg.get_options(), lang="en")
        worker.done.connect(lambda outcome, message: finished.append((outcome, message)))
        worker.run()

        assert seen == [("https://docs.radxa.com/", 4, False, 3.0)]
        assert finished == [("success", "Deep Ingest finished: 1 pages (3 chunks) are now searchable.")]
        assert cfg.get("crawler_max_depth") == 2 and cfg.get("crawler_download_pdfs") is True
        assert CrawlerManager(SimpleNamespace(config=cfg)).max_depth == 2

    def test_skipped_and_failed_pages_are_named_in_the_crawl_log(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        class FakeRag:
            def ingest_url(self, url, crawler_options=None, **kwargs):
                return {"success": True, "outcome": "partial", "count": 1, "documents": 1, "skipped": [
                    {"url": "https://docs.example/private", "reason": "X-Robots-Tag: noai", "opt_out": True}],
                    "failed": [{"url": "https://docs.example/gone", "reason": "HTTP status 404", "opt_out": False}]}

        lines = []
        worker = CrawlWorker(FakeRag(), ["https://docs.example/"], {})
        worker.progress.connect(lines.append)
        worker.run()

        assert "  -> Skipped https://docs.example/private: X-Robots-Tag: noai" in lines
        assert "  -> Failed https://docs.example/gone: HTTP status 404" in lines


class ScriptedRag:
    """ingest_url answers from a script per URL and records what happened, in order."""

    def __init__(self, results, events=None):
        self.results = results
        self.events = [] if events is None else events
        self.calls = []

    def ingest_url(self, url, crawler_options=None, stop=None, progress=None):
        self.calls.append(url)
        result = self.results[url]
        if callable(result):
            result = result(stop, progress)
        if isinstance(result, Exception):
            raise result
        self.events.append(("stored", url))
        return result


def page_result(outcome, pages=0, chunks=0, stopped=False):
    return {"success": outcome != "failure", "outcome": outcome, "documents": pages, "count": chunks,
            "message": f"{outcome} message", "stopped": stopped}


class TestDeepIngestOutcome:
    """The eject rule: the outcome is reported and spoken once it is true (after the last write), and success,
    partial and failure are told apart; "complete" is never said when nothing was stored."""

    def run_worker(self, rag, urls, lang="en", stop_after_first=False):
        from orchestrator.gui.wizards import CrawlWorker

        worker = CrawlWorker(rag, urls, {}, announcer=lambda text: rag.events.append(("spoken", text)), lang=lang)
        done, pages = [], []
        worker.done.connect(lambda outcome, message: done.append((outcome, message)))
        worker.page.connect(lambda n, m, url: pages.append((n, m, url)))
        worker.run()
        return worker, done, pages

    def test_success_is_spoken_after_the_writes(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("success", 2, 5),
                           "https://b.example/": page_result("success", 1, 2)})
        _, done, _ = self.run_worker(rag, list(rag.results))
        message = "Deep Ingest finished: 3 pages (7 chunks) are now searchable."
        assert done == [("success", message)]
        assert rag.events == [("stored", "https://a.example/"), ("stored", "https://b.example/"), ("spoken", message)]

    def test_one_failed_site_makes_the_crawl_partial(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("success", 2, 5),
                           "https://b.example/": page_result("failure")})
        _, done, _ = self.run_worker(rag, list(rag.results))
        assert done[0][0] == "partial" and "2 pages (5 chunks) are searchable" in done[0][1]
        assert rag.events[-1] == ("spoken", done[0][1])

    def test_a_partial_site_makes_the_crawl_partial(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("partial", 2, 5)})
        assert self.run_worker(rag, list(rag.results))[1][0][0] == "partial"

    def test_nothing_stored_is_a_failure_never_complete(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("failure"), "https://b.example/": page_result("failure")})
        _, done, _ = self.run_worker(rag, list(rag.results))
        assert done == [("failure", "Deep Ingest failed: nothing was stored. See the log.")]
        assert all("complete" not in text for kind, text in rag.events if kind == "spoken")

    def test_outcome_in_the_ui_language(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("success", 1, 3)})
        _, done, _ = self.run_worker(rag, list(rag.results), lang="de")
        assert done == [("success", "Deep Ingest abgeschlossen: 1 Seiten (3 Abschnitte) sind jetzt durchsuchbar.")]

    def test_stop_keeps_what_was_stored_and_skips_the_remaining_sites(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        holder = {}

        def first(stop, progress):
            progress(1, 50, "https://a.example/")
            holder["worker"].stop()           # the user presses Stop during the first site
            assert stop.is_set()
            return page_result("partial", 2, 4, stopped=True)

        rag = ScriptedRag({"https://a.example/": first, "https://b.example/": page_result("success", 9, 9)})
        worker = CrawlWorker(rag, list(rag.results), {}, announcer=lambda t: rag.events.append(("spoken", t)),
                             lang="en")
        holder["worker"] = worker
        done, pages = [], []
        worker.done.connect(lambda outcome, message: done.append((outcome, message)))
        worker.page.connect(lambda n, m, url: pages.append((n, m, url)))
        worker.run()

        assert rag.calls == ["https://a.example/"]
        assert pages == [(1, 50, "https://a.example/")]
        assert done == [("partial", "Deep Ingest stopped: 2 pages (4 chunks) are kept and searchable.")]

    def test_stop_before_anything_was_stored(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("failure", stopped=True)})
        _, done, _ = self.run_worker(rag, list(rag.results))
        assert done == [("failure", "Deep Ingest stopped: nothing was stored.")]

    def test_an_unexpected_error_is_reported_and_spoken_as_failure(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        rag = ScriptedRag({"https://a.example/": RuntimeError("disk full")})
        worker = CrawlWorker(rag, list(rag.results), {}, announcer=lambda t: rag.events.append(("spoken", t)),
                             lang="en")
        errors, done = [], []
        worker.error.connect(errors.append)
        worker.done.connect(lambda *a: done.append(a))
        worker.run()
        assert errors == ["disk full"] and done == []
        assert rag.events == [("spoken", "Deep Ingest failed: nothing was stored. See the log.")]

    def test_an_error_after_stored_sites_does_not_claim_nothing_was_stored(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        rag = ScriptedRag({"https://a.example/": page_result("success", 2, 5),
                           "https://b.example/": RuntimeError("disk full")})
        worker = CrawlWorker(rag, list(rag.results), {}, announcer=lambda t: rag.events.append(("spoken", t)),
                             lang="en")
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        assert errors == ["disk full"]
        assert rag.events == [("stored", "https://a.example/"), ("spoken", (
            "Deep Ingest partly finished: 2 pages (5 chunks) are searchable; other pages were left out or failed. "
            "See the log."))]

    def test_a_broken_announcer_never_breaks_the_report(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        def broken(text):
            raise OSError("no audio device")

        worker = CrawlWorker(ScriptedRag({"https://a.example/": page_result("success", 1, 1)}),
                             ["https://a.example/"], {}, announcer=broken, lang="en")
        done = []
        worker.done.connect(lambda outcome, message: done.append(outcome))
        worker.run()
        assert done == ["success"]


@pytest.fixture
def wizard_boxes(monkeypatch):
    from orchestrator.gui import wizards

    shown = []

    class RecordingMessageBox:
        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", title, text))

        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", title, text))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", title, text))

    monkeypatch.setattr(wizards, "QMessageBox", RecordingMessageBox)
    return shown


class TestDeepIngestControls:
    """Progress "page N of M: <url>" instead of an endless spinner, a Stop button, the notice before the first
    crawl."""

    @pytest.fixture
    def wizard(self, qapp, tmp_path, wizard_boxes):
        from PySide6.QtCore import QThread
        from orchestrator.gui.wizards import ModuleCreationWizard

        wiz = ModuleCreationWizard(tmp_path / "targets")
        yield wiz
        # A crawl still running after a failed assertion must not open a real (modal) box in a later test.
        if isinstance(wiz.crawl_worker, QThread):
            wiz.crawl_worker.stop()
            wiz.crawl_worker.wait(5000)
            qapp.processEvents()
        wiz.close()

    def test_progress_names_page_and_url(self, wizard):
        wizard.on_crawl_page(3, 50, "https://docs.example/guide")
        assert wizard.status_label.text() == "Page 3 of 50: https://docs.example/guide"
        assert (wizard.ai_progress.minimum(), wizard.ai_progress.maximum(), wizard.ai_progress.value()) == (0, 50, 3)

    def test_progress_shows_urls_as_plain_escaped_text(self, wizard):
        wizard.on_crawl_page(1, 5, "https://docs.example/<b>x</b>‮" + "a" * 300)
        text = wizard.status_label.text()
        assert wizard.status_label.textFormat() == dialogs.Qt.PlainText
        assert "\\u202e" in text and "‮" not in text and text.endswith("...") and len(text) < 160

    def test_outcomes_get_their_own_box_and_reset_the_controls(self, wizard, wizard_boxes):
        wizard.btn_stop_crawl.setVisible(True)
        wizard.btn_deep_ingest.setEnabled(False)
        wizard.on_crawl_done("success", "ok")
        wizard.on_crawl_done("partial", "some")
        wizard.on_crawl_done("failure", "none")
        assert wizard_boxes == [("information", "Deep Ingest", "ok"), ("warning", "Deep Ingest", "some"),
                                ("critical", "Deep Ingest", "none")]
        assert wizard.btn_stop_crawl.isHidden() and wizard.btn_deep_ingest.isEnabled()

    def test_stop_button_stops_the_running_worker(self, wizard):
        class Worker:
            stopped = False

            def isRunning(self):
                return True

            def stop(self):
                self.stopped = True

        assert wizard.btn_stop_crawl.isHidden()
        wizard.crawl_worker = Worker()
        wizard.btn_stop_crawl.setEnabled(True)
        wizard.stop_deep_ingest()
        assert wizard.crawl_worker.stopped and not wizard.btn_stop_crawl.isEnabled()
        assert wizard.status_label.text() == "Stopping (a page that is still arriving is abandoned; stored pages stay)..."

    def test_closing_during_a_crawl_waits_for_the_current_page_without_blocking(self, qapp, wizard):
        """The wizard used to give up after a fixed wait and close while the page was still being stored;
        the main window could then shut the knowledge base down in the middle of the write."""
        import threading
        import time
        from orchestrator.gui.wizards import CrawlWorker

        release, started, events = threading.Event(), threading.Event(), []

        class SlowRag:
            def ingest_url(self, url, crawler_options=None, stop=None, progress=None):
                started.set()
                release.wait(10)   # embedding and storing a long page
                events.append(("stored", stop.is_set()))
                return page_result("partial", 1, 9, stopped=True)

        wizard.show()
        wizard.crawl_worker = CrawlWorker(SlowRag(), ["https://docs.example/"], {}, lang="en")
        wizard.crawl_worker.start()
        assert started.wait(5)

        begin = time.monotonic()
        wizard.reject()
        assert time.monotonic() - begin < 1.0          # the GUI stays responsive
        assert wizard.isVisible() and wizard.status_label.text() == "Stopping (a page that is still arriving is abandoned; stored pages stay)..."
        wizard.reject()                                  # a second click changes nothing
        release.set()
        assert wait_for(qapp, lambda: not wizard.isVisible())
        assert events == [("stored", True)] and not wizard.crawl_worker.isRunning()
        assert wizard.result() == dialogs.QDialog.Rejected

    def test_deep_ingest_shows_the_notice_once_then_crawls_in_the_background(self, qapp, wizard, tmp_path,
                                                                           monkeypatch, wizard_boxes):
        from orchestrator.gui import wizards

        cfg = ConfigManager(tmp_path / "configs")
        rag = ScriptedRag({"https://docs.example/": lambda stop, progress: (
            progress(1, 50, "https://docs.example/"), page_result("success", 1, 2))[1]})
        wizard.framework_manager = SimpleNamespace(config=cfg, get_component=lambda name: rag)
        notices, spoken = [], []
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: notices.append(self.windowTitle()))
        monkeypatch.setattr(wizards, "announce", lambda text, lang=None, config=None: spoken.append((text, config)))

        offered = []

        class FakeURLDialog:
            def __init__(self, parent=None, recent=(), config=None):
                offered.append(list(recent))
                assert config is cfg   # the crawl options start from the user's settings

            def exec(self):
                return wizards.QDialog.Accepted

            def get_urls(self):
                return ["https://docs.example/"]

            def get_options(self):
                return {"max_depth": 1}

        monkeypatch.setattr(wizards, "URLInputDialog", FakeURLDialog)

        for _ in range(2):
            wizard.run_deep_ingest()
            assert not wizard.btn_stop_crawl.isHidden() and wizard.ai_progress.maximum() != 0
            assert wait_for(qapp, lambda: len(wizard_boxes) and not wizard.crawl_worker.isRunning())
            wizard_boxes.clear()

        assert notices == ["Before your first Deep Ingest"]   # once per user
        assert (cfg.user_state_dir / "crawl_notice.json").exists()
        assert spoken == [("Deep Ingest finished: 1 pages (2 chunks) are now searchable.", cfg)] * 2
        assert wizard.ai_progress.maximum() == 50   # the "page 1 of 50" progress reached the GUI
        assert wizard.status_label.text() == "Deep Ingest finished: 1 pages (2 chunks) are now searchable."
        assert offered == [[], ["https://docs.example/"]]   # crawled URLs are offered the next time

    def test_finish_waits_for_a_running_crawl(self, qapp, wizard, tmp_path, monkeypatch, wizard_boxes):
        """Finish used to create the module, and its knowledge snapshot of the crawled pages, while Deep Ingest
        was still storing them."""
        from orchestrator.gui import wizards

        release, started, generated = threading.Event(), threading.Event(), []

        class SlowRag:
            def ingest_url(self, url, crawler_options=None, stop=None, progress=None):
                started.set()
                release.wait(10)
                return page_result("success", 1, 1)

        class URLDialog:
            def __init__(self, parent=None, recent=(), config=None):
                pass

            def exec(self):
                return wizards.QDialog.Accepted

            def get_urls(self):
                return ["https://docs.example/"]

            def get_options(self):
                return {}

        rag = SlowRag()
        wizard.framework_manager = SimpleNamespace(config=ConfigManager(tmp_path / "configs"),
                                                   get_component=lambda name: rag)
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: 0)
        monkeypatch.setattr(wizards, "URLInputDialog", URLDialog)
        monkeypatch.setattr(wizards, "ModuleGenerator", lambda targets: SimpleNamespace(
            generate_module=lambda *args, **kwargs: generated.append(args)))
        wizard.name_edit.setText("Test_Board")
        wizard.show()
        while wizard.nextId() != -1:
            wizard.next()
        finish = wizard.button(wizards.QWizard.FinishButton)
        assert finish.isEnabled() and wizard.lbl_crawl_running.isHidden()

        wizard.run_deep_ingest()
        assert started.wait(5)
        assert not finish.isEnabled() and not wizard.lbl_crawl_running.isHidden()
        wizard.accept()
        assert generated == [] and wizard.isVisible()

        release.set()
        assert wait_for(qapp, lambda: finish.isEnabled())
        assert wizard.lbl_crawl_running.isHidden() and wizard_boxes[-1][0] == "information"

    @pytest.mark.parametrize("stored", [False, True])
    def test_a_requested_snapshot_that_is_missing_is_reported(self, wizard, tmp_path, monkeypatch, wizard_boxes,
                                                              stored):
        """The snapshot box was ticked, but no stored page came from the crawled URLs (e.g. the crawl stored
        nothing): "Module created" alone used to hide that the snapshot is missing."""
        from orchestrator.gui import wizards

        def generate(data, framework, knowledge_roots=None, profile=None):
            target = tmp_path / "targets" / "test_board"
            (target / "knowledge").mkdir(parents=True)
            if stored and knowledge_roots:
                (target / "knowledge" / "initial_knowledge.json").write_text("[]")
            return target

        monkeypatch.setattr(wizards, "ModuleGenerator", lambda targets: SimpleNamespace(generate_module=generate))
        wizard.framework_manager = SimpleNamespace(config=ConfigManager(tmp_path / "configs"))
        wizard.name_edit.setText("Test_Board")
        wizard.crawled_roots.add("https://docs.example/")
        wizard.chk_knowledge.setEnabled(True)
        wizard.chk_knowledge.setChecked(True)
        wizard.accept()

        kind, _, text = wizard_boxes[-1]
        assert kind == "information" and str(tmp_path / "targets" / "test_board") in text
        assert ("knowledge snapshot was not attached" in text) is not stored

    def test_crawled_urls_are_remembered_without_secrets(self, wizard, tmp_path, monkeypatch):
        from orchestrator.gui import wizards
        from orchestrator.utils.input_history import InputHistory

        cfg = ConfigManager(tmp_path / "configs")
        wizard.framework_manager = SimpleNamespace(config=cfg, get_component=lambda name: object())
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: 0)
        urls = ["https://docs.example/a", "https://docs.example/b?access_token=abc", "https://u:pw@docs.example/c"]
        crawled = []

        class Worker:
            progress = page = done = error = finished = SimpleNamespace(connect=lambda slot: None)

            def __init__(self, rag, urls, options, **kwargs):
                crawled.append(urls)

            def start(self):
                pass

            def isRunning(self):
                return False

        class URLDialog:
            def __init__(self, parent=None, recent=(), config=None):
                pass

            def exec(self):
                return wizards.QDialog.Accepted

            def get_urls(self):
                return urls

            def get_options(self):
                return {}

        monkeypatch.setattr(wizards, "CrawlWorker", Worker)
        monkeypatch.setattr(wizards, "URLInputDialog", URLDialog)
        wizard.run_deep_ingest()
        assert crawled == [urls]   # the crawl gets every URL; only the history leaves some out
        history = InputHistory(cfg.user_state_dir)
        assert history.get("url") == ["https://docs.example/a"]
        assert "abc" not in history.path.read_text() and "pw@" not in history.path.read_text()


class TestCrawlNotice:
    """Information before the first crawl, not a legal confirmation: one button, no checkbox, shown again when
    the text changes, spoken only once it is on screen."""

    def test_one_understood_button_and_no_checkbox(self, qapp):
        dlg = dialogs.CrawlNoticeDialog(lang="en")
        assert [b.text() for b in dlg.buttons()] == ["&Understood"]
        assert dlg.defaultButton() is dlg.understood and dlg.escapeButton() is dlg.understood
        assert dlg.checkBox() is None and dlg.textFormat() == dialogs.Qt.PlainText
        text = dlg.text()
        for needle in ("publicly reachable", "robots.txt", "X-Robots-Tag", "noai", "TDMRep", "stays on this computer",
                       "licence", "release it explicitly", "not a legal confirmation"):
            assert needle in text, needle
        assert dlg.accessibleName() == "Before your first Deep Ingest" and dlg.accessibleDescription() == text

    def test_german_text(self, qapp):
        dlg = dialogs.CrawlNoticeDialog(lang="de")
        assert dlg.windowTitle() == "Vor Ihrem ersten Deep Ingest"
        assert [b.text() for b in dlg.buttons()] == ["&Verstanden"]
        assert "TDMRep" in dlg.text() and "§ 44b UrhG" in dlg.text()

    def test_escape_counts_as_understood(self, qapp):
        from PySide6.QtTest import QTest

        dlg = dialogs.CrawlNoticeDialog(lang="en")
        dlg.show()
        QTest.keyClick(dlg, dialogs.Qt.Key_Escape)
        assert dlg.clickedButton() is dlg.understood and not dlg.isVisible()

    def test_spoken_only_once_the_notice_is_visible(self, qapp):
        spoken = []
        dlg = dialogs.CrawlNoticeDialog(lang="de", announcer=lambda text: spoken.append((text, dlg.isVisible())))
        qapp.processEvents()
        assert spoken == []
        dlg.show()
        assert wait_for(qapp, lambda: spoken)
        dlg.hide()
        dlg.show()
        qapp.processEvents()
        dlg.close()
        assert spoken == [("Bitte lesen Sie den Hinweis vor Ihrem ersten Deep Ingest.", True)]

    def test_shown_once_and_again_when_the_text_changes(self, qapp, tmp_path, monkeypatch):
        import os
        from orchestrator.utils import localization
        from orchestrator.utils.crawl_notice import CrawlNotice

        cfg = SimpleNamespace(user_state_dir=tmp_path / "state")
        shown = []
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: shown.append(self.windowTitle()))

        dialogs.show_crawl_notice_once(None, cfg)
        dialogs.show_crawl_notice_once(None, cfg)
        assert len(shown) == 1
        state = cfg.user_state_dir / CrawlNotice.FILE_NAME
        if os.name == "posix":
            assert state.stat().st_mode & 0o777 == 0o600
        stored = json.loads(state.read_text())
        assert set(stored) == {"notice_sha256", "via", "acknowledged_at"} and len(stored["notice_sha256"]) == 64
        assert stored["via"] == "gui"   # who acknowledged it: a person in the GUI (G18)

        de = dict(localization.LocalizationManager.TRANSLATIONS["de"])
        de["crawl.notice.text"] += " Neu."
        monkeypatch.setitem(localization.LocalizationManager.TRANSLATIONS, "de", de)
        dialogs.show_crawl_notice_once(None, cfg)
        assert len(shown) == 2   # a changed text in any language is shown again

    @pytest.mark.parametrize("content", ["{broken", "[]", '"x"', '{"version": 1}', '{"notice_sha256": "x"}'])
    def test_a_damaged_state_file_shows_the_notice_again(self, tmp_path, content):
        from orchestrator.utils.crawl_notice import CrawlNotice

        (tmp_path / CrawlNotice.FILE_NAME).write_text(content)
        notice = CrawlNotice(tmp_path)
        assert notice.needed()
        notice.acknowledge("cli")
        assert not notice.needed()

    def test_a_failed_save_still_lets_the_crawl_start(self, qapp, tmp_path, monkeypatch):
        from orchestrator.utils.crawl_notice import CrawlNotice

        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: 0)
        monkeypatch.setattr(CrawlNotice, "acknowledge", lambda self, via: (_ for _ in ()).throw(OSError("read-only")))
        dialogs.show_crawl_notice_once(None, SimpleNamespace(user_state_dir=tmp_path))   # no exception

    def test_without_a_usable_state_directory_the_notice_is_shown_and_the_crawl_starts(self, qapp, monkeypatch):
        class Config:
            @property
            def user_state_dir(self):   # ConfigManager creates it on access; a read-only home fails here
                raise PermissionError("read-only home")

        shown = []
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: shown.append(self.windowTitle()))
        dialogs.show_crawl_notice_once(None, Config())
        dialogs.show_crawl_notice_once(None, Config())
        assert shown == ["Before your first Deep Ingest"] * 2   # nothing remembers it: shown every time


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
            # Wave 6 (DECISIONS round 10 Q2, changed on purpose): the AI administration moved into the Ditto menu
            # ("Set up Ditto..." first, runtime/models/evaluation under Ditto > Advanced), Tools has submenus, the
            # titles carry mnemonics; menus are named by their object names, entries compared without separators.
            menus = {m.objectName(): [a.text() for a in m.actions() if not a.isSeparator()]
                     for m in win.findChildren(QMenu) if m.objectName()}
            assert menus["menu_ditto"] == ["Set up Ditto...", "AI Settings...", "Tool Permissions...",
                                           "Folders the AI May Use...", "Pending Approvals...",
                                           "Fix Proposals from AI Apps...", "&Advanced"]
            assert menus["menu_ditto_advanced"] == ["Local Runtime...", "Local Models...", "Evaluation..."]
            assert menus["menu_tools"] == ["Create Hardware Module...", "Hugging Face Model Browser...", "&Knowledge",
                                           "&Maintenance", "Spoken Announcements", "Clear Input History..."]
            assert menus["menu_tools_knowledge"] == ["Open Community Hub", "Knowledge Snapshots...",
                                                     "Knowledge Sources...",   # wave 5 (W1'): knowledge sources
                                                     "Index the Framework's Documentation...",
                                                     "Deep Ingest Notice..."]
            assert menus["menu_tools_maintenance"] == ["Framework Build Images...", "Mojo Health Check..."]
            # File > Settings... (GUI parity, wave 5: config show / config repos by mouse) sits above Exit
            assert menus["menu_file"] == ["Trash...", "Output Folder...", "Settings...", "Exit"]
            assert menus["menu_build"] == ["Targets...", "Build History..."]
            # The audio switch is a persisted setting, on by default.
            assert win.act_audio.isCheckable() and win.act_audio.isChecked()
            win.act_audio.setChecked(False)
            assert win.framework.config.get("audio_announcements") is False
            assert "audio_announcements: false" in (tmp_path / "configs" / "config.yml").read_text()

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

            # Clear Input History: asks first, then forgets everything and empties the recent lists.
            from orchestrator.utils.input_history import InputHistory
            history = InputHistory(win.framework.config.user_state_dir)
            history.add("model", "org/recent-model")
            history.add("device", "10.0.0.7")
            win.builder_tab.reload_history()
            win.deployment_window.reload_history()
            assert win.builder_tab.model_input.itemText(0) == "org/recent-model"
            # The question is a plain-text box that lists the file (full path, file name, size, what it is).
            from orchestrator.gui import plain
            questions = []
            answer = main_window.QMessageBox.No
            monkeypatch.setattr(plain, "_exec", lambda box: questions.append(box.text()) or answer)
            win.act_clear_history.trigger()
            assert history.get("model") == ["org/recent-model"], "No keeps the history"
            answer = main_window.QMessageBox.Yes
            win.act_clear_history.trigger()
            assert len(questions) == 2 and "URLs" in questions[0]
            assert f"Full path: {history.path.resolve()}" in questions[0]
            assert "File name: input_history.json" in questions[0]
            assert not history.path.exists()
            models = [win.builder_tab.model_input.itemText(i) for i in range(win.builder_tab.model_input.count())]
            assert "org/recent-model" not in models and win.deployment_window.cb_device.count() == 0
            assert win.status.currentMessage() == "Input history cleared."
        finally:
            win.update_timer.stop()
            win.close()


def test_a_failed_save_of_the_ai_settings_is_reported(qapp, framework, tmp_path, message_boxes):
    dlg = AIConfigurationDialog(framework)
    select_provider(dlg, "anthropic")
    dlg.cb_model.setCurrentText("my-pinned-model")
    dlg.txt_api_key.setText("sk-ant-test-key-0123456789abcdef")
    (tmp_path / "config.yml").write_text("ai_provider: [unclosed\n")  # unreadable: the save leaves it alone

    dlg._save_config()

    assert ("warning", "Settings Not Saved") in message_boxes
    assert framework.config.get("ai_provider") == "anthropic"  # they apply until the program ends
    assert dlg.result() == dlg.DialogCode.Accepted
