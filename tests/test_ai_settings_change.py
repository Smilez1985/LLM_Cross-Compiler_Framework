"""One AI-settings change service for every channel (consolidation c3-ai-settings).

The rules lived in the AI settings dialog (AIConfigurationDialog._save_config); the sign-in and `ditto login` repeated
two of them, and MCP update_settings had the direction rule in a tool module. The first part pins what each channel
does (written before the rules moved to orchestrator/llm/settings_change.py, passing before and after); the second
part checks AISettingsChange itself, rule by rule.
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core.config_manager import ConfigManager

# Made-up server addresses with a user name and password in them (refused by the address rule).
PASSWORD_URL = "http://me:pw@192.168.1.20:11434/v1"  # secret-scan: allow
PASSWORD_URL_2 = "http://user:pw@a.example/v1"  # secret-scan: allow
PASSWORD_URL_3 = "http://me:secret@192.168.1.20:11434/v1"  # secret-scan: allow


class FakeSecrets:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get_secret(self, key):
        return self.store.get(key)

    def set_secret(self, key, value):
        self.store[key] = value
        return True


class FakeFramework:
    def __init__(self, config, secrets=None):
        self.config = config
        self.secrets_manager = secrets or FakeSecrets()
        self.ditto_manager = SimpleNamespace(reloads=0)
        self.ditto_manager.reload = lambda: setattr(self.ditto_manager, "reloads", self.ditto_manager.reloads + 1)
        self.rag_requests = 0

    def ensure_rag_manager(self):
        self.rag_requests += 1


@pytest.fixture
def gui(qapp, monkeypatch):
    """The dialog module without a network (no live model list) and without leftover worker threads."""
    import gc
    import threading

    from orchestrator.gui import dialogs

    def lister(config, secrets, provider):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(dialogs.AIConfigurationDialog, "model_lister", staticmethod(lister))
    gc.collect()
    yield dialogs
    for thread in threading.enumerate():
        if thread.name == "ai-model-list":
            thread.join(timeout=5)
    gc.collect()


@pytest.fixture
def boxes(gui, monkeypatch):
    """Every message box with its title and text; questions answered Yes."""
    dialogs = gui
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
    return SimpleNamespace(shown=shown, asked=asked, accepted=dialogs.QDialog.Accepted)


@pytest.fixture
def framework(tmp_path):
    return FakeFramework(ConfigManager(tmp_path))


def dialog(framework, provider, **fields):
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dlg = AIConfigurationDialog(framework)
    dlg.cb_provider.setCurrentIndex(dlg.cb_provider.findData(provider))
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
        assert boxes.shown == [] and dlg.result() == boxes.accepted
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

    def test_an_address_with_a_password_is_refused_as_the_sign_in_does(self, framework, boxes, tmp_path):
        # New with c3-ai-settings: the dialog applies the sign-in's address rule too (CHANGELOG Changed).
        dlg = dialog(framework, "ollama", txt_base_url=PASSWORD_URL)
        dlg._save_config()
        assert boxes.shown[0][:2] == ("warning", "Invalid Base URL") and "no user name or password" in boxes.shown[0][2]
        assert dlg.result() == 0 and not (tmp_path / "config.yml").exists()

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
        assert dlg.result() == boxes.accepted and framework.ditto_manager.reloads == 1

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
                                         (PASSWORD_URL_2, False), ("", False)])
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


# --- AISettingsChange itself (Qt-free) -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def catalog():
    from orchestrator.llm import load_catalog
    return load_catalog()


def change(catalog, provider, **values):
    from orchestrator.llm.settings_change import AISettingsChange
    return AISettingsChange(catalog.providers[provider], catalog, **values)


def codes(found):
    return [p.code for p in found]


class TestRules:

    @pytest.fixture
    def config(self, tmp_path):
        return ConfigManager(tmp_path)

    def test_a_complete_change_has_no_problem(self, catalog, config):
        assert change(catalog, "openai", model="gpt-6.1-sol", api_key="sk-proj-0123456789abcdefghijkl").problems(
            config, FakeSecrets()) == []

    def test_fallback_chain_providers_must_be_known(self, catalog, config):
        found = change(catalog, "ollama", model="m", fallback_chain=["local", "skynet:x"]).problems(config,
                                                                                                 FakeSecrets())
        assert codes(found) == ["fallback_unknown"] and "skynet:x" in found[0].message

    def test_offline_mode_refuses_a_cloud_provider(self, catalog, config):
        cloud = change(catalog, "openai", model="m", api_key="sk-proj-0123456789abcdefghijkl", offline_mode=True)
        assert codes(cloud.problems(config, FakeSecrets())) == ["offline_cloud"]
        assert change(catalog, "ollama", model="m", offline_mode=True).problems(config, FakeSecrets()) == []

    def test_offline_mode_accepts_a_custom_server_only_on_the_local_network(self, catalog, config):
        """The rule of the LLM layer at request time (preset.is_local of the address): a custom server outside this
        computer and the local network is refused at save already, not only when Ditto is asked."""
        public = change(catalog, "custom", model="m", base_url="https://api.example.com/v1", offline_mode=True)
        found = public.problems(config, FakeSecrets())
        assert codes(found) == ["offline_cloud"] and found[0].title == "Offline Mode"
        assert "not on this computer or your local network" in found[0].message
        for address in ("http://192.168.1.30:8000/v1", "http://localhost:8000/v1", "http://llm-box.local:8000/v1"):
            assert change(catalog, "custom", model="m", base_url=address, offline_mode=True).problems(
                config, FakeSecrets()) == [], address
        assert change(catalog, "custom", model="m", base_url="https://api.example.com/v1").problems(
            config, FakeSecrets()) == [], "without offline mode a public server is fine"
        assert codes(change(catalog, "custom", model="m", offline_mode=True).problems(config, FakeSecrets())) == [
            "base_url_missing"], "no address at all: the missing address is the problem to name"

    def test_a_key_is_required_unless_one_is_stored_or_another_method_was_chosen(self, catalog, config):
        assert codes(change(catalog, "openai", model="m").problems(config, FakeSecrets())) == ["key_missing"]
        stored = FakeSecrets({"openai_api_key": "sk-stored-0123456789abcdefghij"})
        assert change(catalog, "openai", model="m").problems(config, stored) == []
        config.set("ai_auth_anthropic", "ant_profile")
        assert change(catalog, "anthropic", model="m").problems(config, FakeSecrets()) == []

    def test_a_rejected_key_is_refused(self, catalog, config):
        found = change(catalog, "anthropic", model="m", api_key="sk-ant-admin01-0123").problems(config, FakeSecrets())
        assert codes(found) == ["key_rejected"] and "Admin" in found[0].message

    def test_the_workspace_id_format_and_only_for_the_claude_api(self, catalog, config):
        bad = change(catalog, "anthropic", model="m", api_key="sk-ant-api03-0123456789", workspace_id="ws-1")
        assert codes(bad.problems(config, FakeSecrets())) == ["workspace_invalid"]
        good = change(catalog, "anthropic", model="m", api_key="sk-ant-api03-0123456789", workspace_id="wrkspc_01")
        assert good.problems(config, FakeSecrets()) == [] and good.workspace == "wrkspc_01"
        assert change(catalog, "openai", workspace_id="ws-1").workspace == "", "never another provider's"

    @pytest.mark.parametrize("url, problem", [("", "base_url_missing"), ("localhost:8000", "base_url_invalid"),
                                              ("ftp://a.example/v1", "base_url_invalid"),
                                              (PASSWORD_URL_2, "base_url_invalid"),
                                              ("https://a.example/v1", None)])
    def test_the_base_url(self, catalog, config, url, problem):
        found = change(catalog, "custom", model="m", base_url=url).problems(config, FakeSecrets())
        assert codes(found) == ([problem] if problem else [])

    def test_a_server_address_with_a_password_is_refused_like_the_sign_in_does(self, catalog, config):
        (found,) = change(catalog, "ollama", model="m", base_url=PASSWORD_URL_3).problems(
            config, FakeSecrets())
        assert found.title == "Invalid Base URL" and "no user name or password" in found.message
        assert "secret" not in found.message

    def test_a_leftover_address_never_reaches_a_provider_with_a_fixed_one(self, catalog):
        cloud = change(catalog, "openai", base_url="https://third-party-proxy.example.com/v1",
                       api_key="sk-proj-0123456789abcdefghijkl")
        assert cloud.address == "" and cloud.key_url == catalog.providers["openai"].base_url

    def test_a_model_is_required_except_for_the_managed_server(self, catalog, config):
        assert "model_missing" in codes(change(catalog, "ollama").problems(config, FakeSecrets()))
        managed = next(p.id for p in catalog.providers.values() if p.runtime_endpoint)
        assert "model_missing" not in codes(change(catalog, managed).problems(config, FakeSecrets()))

    @pytest.mark.parametrize("url, key, asked", [("http://192.168.1.20:8000/v1", "k-0123456789abcdef", True),
                                                 ("http://127.0.0.1:8000/v1", "k-0123456789abcdef", False),
                                                 ("https://a.example/v1", "k-0123456789abcdef", False),
                                                 ("http://192.168.1.20:8000/v1", "", False)])
    def test_plain_http_to_another_computer_needs_the_persons_yes(self, catalog, url, key, asked):
        found = change(catalog, "custom", model="m", base_url=url, api_key=key).plain_http_url()
        assert (found == url) is asked and (found is None) is not asked


class TestApply:

    def test_writes_every_setting_saves_starts_the_knowledge_base_and_reloads_ditto(self, catalog, tmp_path):
        config = ConfigManager(tmp_path)
        framework = FakeFramework(config)
        applied = change(catalog, "ollama", model="qwen3.5:4b", base_url="http://127.0.0.1:11434/v1", effort="low",
                         security_level="OFF", fallback_chain=["local"], offline_mode=True, enable_rag=True).apply(
            config, framework.secrets_manager, framework)
        saved = ConfigManager(tmp_path)
        assert [saved.get(k) for k in ("ai_provider", "ai_model", "ai_base_url", "ai_effort", "ai_security_level",
                                       "ai_fallback_chain", "offline_mode", "enable_rag_knowledge")] == [
            "ollama", "qwen3.5:4b", "http://127.0.0.1:11434/v1", "low", "OFF", ["local"], True, True]
        assert applied.saved is True and framework.rag_requests == 1 and framework.ditto_manager.reloads == 1
        assert applied.result["base_url"] == "http://127.0.0.1:11434/v1" and "api_key" not in applied.result

    def test_the_key_is_stored_for_its_address(self, catalog, tmp_path, memory_keyring):
        from orchestrator.Core.secrets_manager import SecretsManager, credential_name
        vault = SecretsManager(tmp_path / "state")
        assert vault.initialize(), vault.storage_error
        change(catalog, "custom", model="m", base_url="http://192.168.1.20:8000/v1",
               api_key="custom-" + "secret-0123456789").apply(ConfigManager(tmp_path), vault)
        assert vault.list_secrets() == [credential_name("custom", "api_key", "http://192.168.1.20:8000")]

    def test_a_key_the_vault_refuses_writes_no_setting(self, catalog, tmp_path):
        from orchestrator.llm.settings_change import SettingsChangeError
        config = ConfigManager(tmp_path)
        refusing = SimpleNamespace(get_secret=lambda key: None, set_secret=lambda key, value: False)
        with pytest.raises(SettingsChangeError) as raised:
            change(catalog, "custom", model="m", base_url="https://a.example/v1",
                   api_key="custom-" + "secret-0123456789").apply(config, refusing)
        assert raised.value.title == "Error" and config.get("ai_provider") != "custom"
        assert "secret-0123456789" not in str(raised.value)

    def test_a_typed_key_switches_away_from_a_method_without_one(self, catalog, tmp_path):
        config = ConfigManager(tmp_path)
        config.set("ai_auth_anthropic", "ant_profile")
        change(catalog, "anthropic", model="m", api_key="sk-ant-api03-0123456789abcdef").apply(config, FakeSecrets())
        assert config.get("ai_auth_anthropic") == "api_key"
        change(catalog, "anthropic", model="m").apply(config, FakeSecrets())
        assert config.get("ai_auth_anthropic") == "api_key", "no key typed: the method stays"

    def test_settings_that_cannot_be_saved_report_none(self, catalog):
        config = SimpleNamespace(values={}, get=lambda k, d=None: d)
        config.set = lambda k, v: config.values.__setitem__(k, v) or True
        applied = change(catalog, "ollama", model="m").apply(config, FakeSecrets())
        assert applied.saved is None and config.values["ai_provider"] == "ollama"


class TestOneSettingAtATime:
    """AISettingsChange.current and with_setting: what the dialog opens with, and what `llmcf config set` (Q6) uses."""

    def test_current_is_what_the_saved_settings_say(self, catalog, tmp_path):
        from orchestrator.llm.settings_change import AISettingsChange
        config = ConfigManager(tmp_path)
        for key, value in (("ai_provider", "custom"), ("ai_model", "my-model"), ("ai_base_url", "https://a.example/v1"),
                           ("ai_effort", "high"), ("ai_fallback_chain", ["local"]), ("offline_mode", True)):
            config.set(key, value)
        now = AISettingsChange.current(config, catalog)
        assert (now.preset.id, now.model, now.base_url, now.effort, now.fallback_chain, now.offline_mode) == (
            "custom", "my-model", "https://a.example/v1", "high", ["local"], True)
        other = AISettingsChange.current(config, catalog, provider="openai")
        assert other.base_url == "" and other.model == catalog.providers["openai"].model_for("chat")

    def test_a_change_of_one_setting_is_checked_with_every_rule(self, catalog, tmp_path):
        from orchestrator.llm.settings_change import AISettingsChange
        config = ConfigManager(tmp_path)
        config.set("ai_provider", "openai")
        config.set("ai_model", "gpt-6.1-sol")
        secrets = FakeSecrets({"openai_api_key": "sk-stored-0123456789abcdefghij"})
        now = AISettingsChange.current(config, catalog)
        assert now.problems(config, secrets) == []
        assert codes(now.with_setting("offline_mode", True).problems(config, secrets)) == ["offline_cloud"]
        local = now.with_setting("ai_provider", "ollama", config).with_setting("offline_mode", True)
        assert local.preset.id == "ollama" and local.problems(config, secrets) == []
        assert local.with_setting("ai_fallback_chain", "local, ollama:x").fallback_chain == ["local", "ollama:x"]
        assert local.with_setting("ai_security_level", "balanced").security_level == "BALANCED"

    def test_settings_changed_one_by_one_answer_for_the_problems_they_bring(self, catalog, tmp_path):
        """MCP update_settings (and the coming `llmcf config set`) change single settings: they run every rule of the
        dialog on the saved settings with their values, and refuse what those values add, not what was already
        there (stage-B integration)."""
        from orchestrator.llm.settings_change import AISettingsChange
        config = ConfigManager(tmp_path)
        config.set("ai_provider", "openai")
        config.set("ai_model", "gpt-6.1-sol")
        assert codes(AISettingsChange.current(config, catalog).problems(config, FakeSecrets())) == ["key_missing"]
        found = AISettingsChange.problems_of_settings(config, FakeSecrets(), {"offline_mode": True}, catalog)
        assert codes(found) == ["offline_cloud"] and found[0].title == "Offline Mode"
        assert AISettingsChange.problems_of_settings(config, FakeSecrets(), {"enable_rag_knowledge": False},
                                                     catalog) == []
        assert AISettingsChange.problems_of_settings(config, FakeSecrets(), {"language": "de"}, catalog) == []
        config.set("ai_provider", "ollama")
        assert AISettingsChange.problems_of_settings(config, FakeSecrets(), {"offline_mode": True,
                                                                              "enable_rag_knowledge": False},
                                                     catalog) == []
        with pytest.raises(ValueError):
            AISettingsChange.problems_of_settings(config, FakeSecrets(), {"offline_mode": "yes"}, catalog)

    def test_update_settings_asks_the_service(self, monkeypatch):
        """One place: the tool module has no rule of its own for the AI settings."""
        from orchestrator.llm.settings_change import AISettingsChange, SettingsProblem
        from orchestrator.tools import tools_status
        seen = []

        def problems_of_settings(config, secrets, values, catalog=None):
            seen.append(dict(values))
            return [SettingsProblem("offline_cloud", "Offline Mode", "the service's text")]
        monkeypatch.setattr(AISettingsChange, "problems_of_settings", staticmethod(problems_of_settings))
        framework = SimpleNamespace(config=SimpleNamespace(), secrets_manager=None)
        assert tools_status.ai_settings_problem(framework, {"offline_mode": True, "language": "de"}) == \
            "Offline Mode: the service's text"
        assert seen == [{"offline_mode": True}]
        assert tools_status.ai_settings_problem(framework, {"language": "de"}) is None and len(seen) == 1

    @pytest.mark.parametrize("key, value, error", [("language", "de", KeyError), ("offline_mode", "yes", ValueError),
                                                   ("ai_security_level", "loose", ValueError),
                                                   ("ai_effort", "turbo", ValueError),
                                                   ("ai_provider", "skynet", ValueError)])
    def test_values_a_setting_cannot_take(self, catalog, tmp_path, key, value, error):
        from orchestrator.llm.settings_change import AISettingsChange
        now = AISettingsChange.current(ConfigManager(tmp_path), catalog)
        with pytest.raises(error):
            now.with_setting(key, value, ConfigManager(tmp_path))


class TestOnePlace:
    """Every channel uses the one implementation (the old import paths are the same objects)."""

    def test_the_moved_rules_are_one_implementation(self):
        from orchestrator.llm import settings_change
        from orchestrator.llm.auth import signin
        from orchestrator.tools import tools_status
        assert signin.check_base_url is settings_change.check_base_url
        assert signin.plain_http_to_remote is settings_change.plain_http_to_remote
        assert tools_status.direction_problem is settings_change.direction_problem
        assert tools_status.TIGHTEN_ONLY is settings_change.TIGHTEN_ONLY
        assert tools_status.memory_bytes is settings_change.memory_bytes

    def test_the_dialog_reads_its_rules_from_the_service(self, qapp):
        from orchestrator.gui import dialogs
        from orchestrator.llm import settings_change
        assert dialogs.SettingsOverlay is settings_change.SettingsOverlay
        assert dialogs.direction_problem is settings_change.direction_problem
        import inspect
        source = inspect.getsource(dialogs.AIConfigurationDialog._save_config)
        assert "problems(" in source and "apply(" in source and "cfg.set" not in source

    @pytest.mark.usefixtures("boxes")
    def test_the_dialogs_address_workspace_and_saved_values_come_from_the_service(self, framework, monkeypatch):
        """The leftover-address rule exists once (review of c3-ai-settings): the dialog's live model list and its
        sign-in read the address and the workspace through AISettingsChange, and saved values through user_value."""
        from orchestrator.gui import dialogs
        from orchestrator.llm import settings_change
        dlg = dialog(framework, "anthropic", txt_workspace="wrkspc_01")
        preset = dlg._preset()
        monkeypatch.setattr(settings_change.AISettingsChange, "address", property(lambda self: "wired-address"))
        monkeypatch.setattr(settings_change.AISettingsChange, "workspace", property(lambda self: "wired-workspace"))
        monkeypatch.setattr(dialogs, "user_value", lambda config, key, default=None: f"wired-{key}")
        assert dlg._base_url(preset) == "wired-address"
        assert dlg._workspace_id(preset) == "wired-workspace"
        assert dlg._user_value("ai_effort") == "wired-ai_effort"

    def test_the_service_loads_without_qt_or_a_provider_sdk(self):
        import subprocess
        import sys

        from conftest import REPO_ROOT
        code = ("import sys, orchestrator.llm.settings_change, orchestrator.tools.tools_status\n"
                "bad = [m for m in ('PySide6', 'anthropic', 'openai', 'orchestrator.gui') if m in sys.modules]\n"
                "print(bad)")
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=120, check=True).stdout.strip()
        assert out == "[]"
