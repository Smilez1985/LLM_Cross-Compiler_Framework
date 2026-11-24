"""The settings of Ditto's switch (DESIGN "Targets, hardware and templates", the row "Ditto's switch, and Ditto as the
pilot in the module wizard"), without Qt: they run in the install without the 'gui' extra too. The switch itself
(orchestrator/gui/ditto_switch.py) is tested in test_ditto_switch.py."""

from orchestrator.Core.config_manager import ConfigManager
from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, person_only_text


def test_only_the_person_sets_it():
    assert {"ditto_enabled", "ditto_off_in"} <= set(PERSON_ONLY_SETTINGS)
    for key in ("ditto_enabled", "ditto_off_in"):
        assert "Only the person switches it" in person_only_text(key)


def test_on_by_default_and_kept_in_the_users_settings(tmp_path):
    config = ConfigManager(tmp_path / "configs")
    assert config.get("ditto_enabled") is True and config.get("ditto_off_in") == []
    assert config.set("ditto_enabled", False) and config.set("ditto_off_in", ["wizard"])
    assert config.save_user_config()
    again = ConfigManager(tmp_path / "configs")
    assert again.get("ditto_enabled") is False and again.get("ditto_off_in") == ["wizard"]
