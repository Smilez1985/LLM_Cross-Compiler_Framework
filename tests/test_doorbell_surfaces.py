"""The build doorbell's surfaces: `llm-cli config doorbell`, File > Settings... > Build doorbell and its Mojo line.
Each saves only by the doorbell's rules (a refused setting leaves every setting as it was), keeps the password in
the credential vault only and sends its test doorbell through the same code. No real broker: a fake client."""

import json
import time
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from orchestrator.Core import doorbell

SECRET = "pw-" + "Kx7" * 6   # made up


class FakeClient:
    calls = []

    def __init__(self, broker, password):
        self.broker, self.password = broker, password

    async def __aenter__(self):
        FakeClient.calls.append(("connect", self.broker.label(), self.password))
        return self

    async def __aexit__(self, *exc):
        return False

    async def publish(self, topic, payload, qos, retain, timeout):
        FakeClient.calls.append(("publish", topic, payload, qos, retain))


class Vault:
    def __init__(self):
        self.values = {}

    def get_secret(self, key):
        return self.values.get(key)

    def set_secret(self, key, value, *a, **kw):
        self.values[key] = value
        return True


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(doorbell, "aiomqtt_client", lambda broker, password: FakeClient(broker, password))


@pytest.fixture
def config(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    folder = tmp_path / "cfg"
    folder.mkdir()
    return ConfigManager(folder)


def saved(config):
    import yaml
    path = config.config_dir / "config.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_the_settings_are_in_the_schema_with_their_defaults(config):
    assert config.get("mqtt_broker_url") == "" and config.get("mqtt_topic_prefix") == "llmcf"
    assert config.get("mqtt_username") == "" and config.get("mqtt_ca_file") == ""
    from orchestrator.Core.secret_entries import KNOWN_SECRETS
    assert "mqtt_password" in KNOWN_SECRETS


def test_apply_settings_saves_and_a_refusal_keeps_everything(config):
    doorbell.apply_settings(config, {"mqtt_broker_url": "mqtt://localhost:1883", "mqtt_topic_prefix": "home"})
    assert saved(config)["mqtt_broker_url"] == "mqtt://localhost:1883" and saved(config)["mqtt_topic_prefix"] == "home"
    with pytest.raises(doorbell.DoorbellRefused):
        doorbell.apply_settings(config, {"mqtt_broker_url": "mqtt://broker.example.org:1883"})   # no TLS
    with pytest.raises(doorbell.DoorbellRefused):
        doorbell.apply_settings(config, {"mqtt_topic_prefix": "Home/#"})
    assert config.get("mqtt_broker_url") == "mqtt://localhost:1883" and config.get("mqtt_topic_prefix") == "home"
    assert saved(config)["mqtt_broker_url"] == "mqtt://localhost:1883"
    with pytest.raises(doorbell.DoorbellRefused):
        doorbell.apply_settings(config, {"ai_provider": "x"})   # not a doorbell setting


# --- the command line -----------------------------------------------------------------------------------------------

def invoke(framework, *args, input=None):
    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", "config", "doorbell", *args], obj=Ctx(), input=input)


def test_cli_sets_the_broker_shows_it_and_sends_a_test_doorbell(config, tmp_path):
    vault = Vault()
    framework = SimpleNamespace(config=config, secrets_manager=vault)
    result = invoke(framework, "--broker", "mqtt://127.0.0.1:1883", "--username", "bell", "--test")
    assert result.exit_code == 0, result.output
    assert "Broker: mqtt://127.0.0.1:1883" in result.output and "A test doorbell was sent" in result.output
    assert saved(config)["mqtt_username"] == "bell"
    sent = [c[1] for c in FakeClient.calls if c[0] == "publish"]
    assert f"llmcf/v1/build/{doorbell.TEST_REQUEST_ID}/end" in sent and "llmcf/v1/events" in sent


def test_cli_refuses_plain_mqtt_beyond_this_computer_with_exit_2(config):
    result = invoke(SimpleNamespace(config=config), "--broker", "mqtt://broker.example.org:1883")
    assert result.exit_code == 2 and "TLS" in result.output
    assert config.get("mqtt_broker_url") == "" and "mqtt_broker_url" not in saved(config)


def test_cli_password_goes_to_the_vault_never_into_the_settings_file(config):
    vault = Vault()
    framework = SimpleNamespace(config=config, secrets_manager=vault)
    result = invoke(framework, "--broker", "mqtts://broker.example.org:8883", "--password", input=f"{SECRET}\n")
    assert result.exit_code == 0, result.output
    assert vault.values == {"mqtt_password": SECRET}
    assert SECRET not in result.output and SECRET not in json.dumps(saved(config))


def test_cli_test_without_a_broker_exits_1(config):
    result = invoke(SimpleNamespace(config=config), "--test")
    assert result.exit_code == 1 and "No MQTT broker is set" in result.output


# --- Mojo ------------------------------------------------------------------------------------------------------------

def test_mojo_has_no_line_without_a_broker_and_one_with_it(config, tmp_path):
    from orchestrator.Core.mojo.checks import check_doorbell
    framework = SimpleNamespace(config=config)
    assert check_doorbell(framework) == []
    config.set("mqtt_broker_url", "mqtt://localhost:1883")
    findings = check_doorbell(framework)
    assert [f.code for f in findings] == ["doorbell.settings"] and findings[0].severity.name == "OK"


def test_mojo_warns_about_a_broken_setting_and_a_failed_send(config, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    from orchestrator.Core.mojo.checks import check_doorbell
    framework = SimpleNamespace(config=config)
    config.set("mqtt_broker_url", "mqtt://broker.example.org:1883")   # edited by hand: no TLS
    (finding,) = check_doorbell(framework)
    assert finding.code == "doorbell.settings" and finding.severity.name == "WARN"
    assert [s.id for s in finding.steps] == ["settings.doorbell"]
    config.set("mqtt_broker_url", "mqtt://localhost:1883")
    bell = doorbell.Doorbell(config, lambda: SECRET, state_dir=config.user_state_dir,
                             client_factory=lambda b, p: (_ for _ in ()).throw(ConnectionRefusedError(SECRET)))
    assert bell.ring(doorbell.payload({"request_id": "req_1a2b3c4d", "end_reason": "done"})) is False
    (finding,) = check_doorbell(framework)
    assert finding.code == "doorbell.send" and finding.severity.name == "WARN"
    assert SECRET not in finding.message and "mqtt://localhost:1883" in finding.message


def test_mojo_runs_the_check_by_default():
    from orchestrator.Core.mojo.checks import check_doorbell
    from orchestrator.Core.mojo.runner import DEFAULT_CHECKS
    assert check_doorbell in DEFAULT_CHECKS


# --- the settings window ---------------------------------------------------------------------------------------------

def wait_for(predicate, qapp, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_the_tab_saves_by_the_rules_and_tests_in_a_worker_thread(qapp, config):
    pytest.importorskip("PySide6")
    from orchestrator.gui import settings_window
    vault = Vault()
    dialog = settings_window.SettingsDialog(SimpleNamespace(config=config, secrets_manager=vault), lang="en")
    tab = dialog.page_bell
    assert tab.objectName() == "tab_settings_doorbell" and tab.edit_prefix.text() == "llmcf"
    tab.edit_url.setText("mqtt://broker.example.org:1883")
    assert tab.save() is False and "TLS" in tab.lbl_result.text() and config.get("mqtt_broker_url") == ""
    tab.edit_url.setText("mqtt://localhost:1883")
    tab.edit_password.setText(SECRET)
    assert tab.save() is True and tab.lbl_result.text() == "Saved."
    assert vault.values == {"mqtt_password": SECRET} and tab.edit_password.text() == ""
    assert saved(config)["mqtt_broker_url"] == "mqtt://localhost:1883"
    tab.btn_test.click()
    assert wait_for(lambda: "A test doorbell was sent" in tab.lbl_result.text(), qapp), tab.lbl_result.text()
    assert ("connect", "mqtt://localhost:1883", SECRET) in FakeClient.calls
    dialog.close()
