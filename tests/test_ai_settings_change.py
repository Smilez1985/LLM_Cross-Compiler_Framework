"""One AI-settings change service for every channel (consolidation c3-ai-settings).

The rules lived in the AI settings dialog (AIConfigurationDialog._save_config); the sign-in and `ditto login` repeated
two of them, and MCP update_settings had the direction rule in a tool module. The first part pins what each channel
does (written before the rules moved to orchestrator/llm/settings_change.py, passing before and after); the second
part checks AISettingsChange itself, rule by rule.
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core.config_manager import ConfigManager

pytest.importorskip("PySide6")

from orchestrator.gui import dialogs  # noqa: E402
from orchestrator.gui.dialogs import AIConfigurationDialog  # noqa: E402
from test_dialogs import FakeFramework, FakeSecrets, select_provider  # noqa: E402,F401
from test_dialogs import collect_widgets_on_the_main_thread, model_lists  # noqa: E402,F401


@pytest.fixture
def boxes(monkeypatch):
    """Every message box with its title and text; questions answered Yes."""
    shown, asked = [], []

    class Boxes:
        Yes, No = dialogs.QMessageBox.Yes, dialogs.QMessageBox.No

        @staticmethod
        def question(parent, title, text, *args):
            asked.append((title, text))
            return Boxes.Yes

        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", title, text))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", title, text))

        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", title, text))

    monkeypatch.setattr(dialogs, "QMessageBox", Boxes)
    return SimpleNamespace(shown=shown, asked=asked)


@pytest.fixture
def framework(tmp_path):
    return FakeFramework(ConfigManager(tmp_path))


def dialog(framework, provider, **fields):
    dlg = AIConfigurationDialog(framework)
    select_provider(dlg, provider)
    for name, value in fields.items():
        widget = getattr(dlg, name)
        if hasattr(widget, "setCurrentText") and not hasattr(widget, "setText"):
            widget.setCurrentText(value)
        elif hasattr(widget, "setChecked"):
            widget.setChecked(value)
        else:
            widget.setText(value)
    return dlg


# --- what the GUI channel does (characterization) ------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
class TestDialogChannel:

    @pytest.mark.parametrize("provider, fields, title, text", [
        ("ollama", {"txt_fallback": "local, skynet:model"}, "Invalid Fallback Chain",
         "Unknown provider in the fallback chain: skynet:model.\nKnown providers: "),
        ("openai", {"txt_api_key": "sk-proj-0123456789abcdefghijkl", "chk_offline": True}, "Offline Mode",
         "is a cloud provider. In offline mode choose a local provider (Ollama, the managed local model, or a custom "
         "server on your network)."),
        ("openai", {}, "Missing API Key", "Please provide an API key for this provider."),
        ("anthropic", {"txt_api_key": "sk-ant-admin01-0123456789abcdef"}, "Unsuitable API Key", "Admin"),
        ("anthropic", {"txt_api_key": "sk-ant-api03-0123456789abcdef", "txt_workspace": "workspace-1"},
         "Invalid Workspace ID", "wrkspc_"),
        ("custom", {"cb_model": "my-model"}, "Missing Base URL", "A custom endpoint requires a Base URL."),
        ("ollama", {"txt_base_url": "localhost:11434"}, "Invalid Base URL",
         "The Base URL must start with http:// or https://."),
        ("ollama", {"txt_base_url": "ftp://192.168.1.20/v1"}, "Invalid Base URL",
         "The Base URL must start with http:// or https://."),
        ("openai", {"txt_api_key": "sk-proj-0123456789abcdefghijkl", "cb_model": ""}, "Missing Model",
         "Please select or enter a model."),
    ])
    def test_each_refusal_has_its_title_and_text_and_changes_nothing(self, framework, boxes, tmp_path, provider,
                                                                      fields, title, text):
        dlg = dialog(framework, provider, **fields)
        dlg._save_config()
        assert len(boxes.shown) == 1, boxes.shown
        kind, shown_title, shown_text = boxes.shown[0]
        assert (kind, shown_title) == ("warning", title) and text in shown_text
        assert dlg.result() == 0 and framework.ditto_manager.reloads == 0 and framework.rag_requests == 0
        assert framework.secrets_manager.store == {} and not (tmp_path / "config.yml").exists()

    def test_a_save_writes_every_setting_and_starts_what_it_needs(self, framework, boxes, tmp_path):
        framework.config.set("ai_base_url_custom", "http://192.168.1.9:8000/v1")  # set by `ditto login`
        framework.config.set("ai_auth_custom", "none")
        dlg = dialog(framework, "custom", cb_model="my-model", txt_base_url="https://llm.example.net/v1",
                     txt_api_key="custom-secret-0123456789", txt_fallback="ollama:qwen3.5:4b, local",
                     chk_enable_rag=True)
        dlg.cb_effort.setCurrentIndex(dlg.cb_effort.findData("high"))
        dlg.cb_security.setCurrentIndex(dlg.cb_security.findData("OFF"))
        dlg._save_config()
        assert boxes.shown == [] and dlg.result() == dialogs.QDialog.Accepted
        saved = ConfigManager(tmp_path)
        assert {k: saved.get(k) for k in ("ai_provider", "ai_model", "ai_base_url", "ai_base_url_custom",
                                          "ai_effort", "ai_security_level", "ai_fallback_chain", "offline_mode",
                                          "enable_rag_knowledge")} == {
            "ai_provider": "custom", "ai_model": "my-model", "ai_base_url": "https://llm.example.net/v1",
            "ai_base_url_custom": "https://llm.example.net/v1", "ai_effort": "high", "ai_security_level": "OFF",
            "ai_fallback_chain": ["ollama:qwen3.5:4b", "local"], "offline_mode": False,
            "enable_rag_knowledge": True}
        assert saved.get("ai_auth_custom") != "none", "a key typed here is meant to be used"
        assert framework.rag_requests == 1 and framework.ditto_manager.reloads == 1
        assert dlg.get_config() == {"provider": "custom", "provider_label": dlg._preset().label, "model": "my-model",
                                    "base_url": "https://llm.example.net/v1", "effort": "high",
                                    "security_level": "OFF", "fallback_chain": ["ollama:qwen3.5:4b", "local"],
                                    "offline_mode": False, "enable_rag": True}
        assert "custom-secret" not in (tmp_path / "config.yml").read_text()

    def test_a_cloud_provider_never_keeps_a_leftover_address(self, framework, boxes, tmp_path):
        framework.config.set("ai_base_url", "https://third-party-proxy.example.com/v1")
        dlg = dialog(framework, "openai", cb_model="gpt-6.1-sol", txt_api_key="sk-proj-0123456789abcdefghijkl")
        dlg.txt_base_url.setText("https://third-party-proxy.example.com/v1")  # disabled for openai, still filled
        dlg._save_config()
        assert boxes.shown == [] and ConfigManager(tmp_path).get("ai_base_url") == ""
        assert framework.secrets_manager.store == {"openai_api_key": "sk-proj-0123456789abcdefghijkl"}

    def test_a_key_over_plain_http_to_another_computer_is_asked_first(self, framework, boxes):
        dlg = dialog(framework, "custom", cb_model="m", txt_base_url="http://192.168.1.20:8000/v1",
                     txt_api_key="custom-secret-0123456789")
        dlg._save_config()
        assert len(boxes.asked) == 1 and "plain http://" in boxes.asked[0][1]
        local = dialog(framework, "custom", cb_model="m", txt_base_url="http://127.0.0.1:8000/v1",
                       txt_api_key="custom-secret-0123456789")
        local._save_config()
        assert len(boxes.asked) == 1, "this computer: not asked"

    def test_a_failed_save_of_the_file_is_reported(self, framework, boxes, monkeypatch):
        monkeypatch.setattr(framework.config, "save_user_config", lambda: False)
        dlg = dialog(framework, "ollama")
        dlg._save_config()
        assert boxes.shown == [("warning", "Settings Not Saved", boxes.shown[0][2])]
        assert dlg.result() == dialogs.QDialog.Accepted and framework.ditto_manager.reloads == 1

    def test_a_key_the_vault_refuses_stops_the_save(self, framework, boxes):
        framework.secrets_manager = SimpleNamespace(get_secret=lambda key: None, set_secret=lambda key, value: False)
        dlg = dialog(framework, "custom", cb_model="m", txt_base_url="https://llm.example.net/v1",
                     txt_api_key="custom-secret-0123456789")
        dlg._save_config()
        assert boxes.shown and boxes.shown[0][:2] == ("critical", "Error")
        assert dlg.result() == 0 and framework.config.get("ai_provider") != "custom"


# --- the other channels (characterization) ----------------------------------------------------------------------

class TestOtherChannels:

    @pytest.mark.parametrize("url, ok", [("http://192.168.1.20:11434/v1", True), ("https://a.example/v1", True),
                                         ("localhost:11434", False), ("ftp://a.example", False),
                                         ("http://user:pw@a.example/v1", False), ("", False)])
    def test_signin_checks_a_server_address(self, url, ok):
        from orchestrator.llm.auth.signin import check_base_url
        if ok:
            assert check_base_url(url) == url
        else:
            with pytest.raises(ValueError, match="plain http:// or https:// URL with a host"):
                check_base_url(url)

    @pytest.mark.parametrize("url, plain", [("http://192.168.1.20:8000/v1", True), ("http://127.0.0.1:8000", False),
                                            ("http://localhost:8000", False), ("https://a.example", False),
                                            ("", False), (None, False)])
    def test_signin_tells_plain_http_to_another_computer(self, url, plain):
        from orchestrator.llm.auth.signin import plain_http_to_remote
        assert plain_http_to_remote(url) is plain

    @pytest.mark.parametrize("key, old, new, refused", [
        ("offline_mode", False, True, False), ("offline_mode", True, False, True),
        ("enable_rag_knowledge", True, False, False), ("enable_rag_knowledge", False, True, True),
        ("crawler_max_pages", 50, 10, False), ("crawler_max_pages", 50, 100, True),
        ("crawler_rate_limit", 1.0, 2.0, False), ("crawler_rate_limit", 2.0, 1.0, True),
        ("build_memory_limit", "8g", "4g", False), ("build_memory_limit", "4g", "8g", True),
        ("language", "en", "de", False),
    ])
    def test_update_settings_only_tightens(self, key, old, new, refused):
        from orchestrator.tools.tools_status import direction_problem
        assert bool(direction_problem(key, old, new)) is refused
