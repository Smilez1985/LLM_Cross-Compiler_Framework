"""The enterprise mode on its groundwork (maintainer, 2026-11-24, ROADMAP "The enterprise mode on its groundwork"):
ConfigManager's advanced mode, the enterprise mode's groundwork from before it was renamed, is switched on by
`framework_mode: enterprise` in one place (advanced_mode_for); its features join ENTERPRISE_FEATURES when they are
wired. Private keeps it off; the settings only enterprise may set are read only in enterprise mode.
"""

import os
import stat

import pytest
import yaml

from orchestrator.Core import config_manager as cm
from orchestrator.Core.config_manager import (ENTERPRISE_FEATURES, AdvancedFeature, ConfigManager, ConfigSchema,
                                              advanced_mode_for, set_framework_mode)


@pytest.fixture
def enterprise_setting(monkeypatch):
    """A setting only enterprise may set (none is wired yet: the first is the enterprise mode's own)."""
    real = ConfigManager._initialize_core_schemas

    def schemas(self):
        real(self)
        self.config_schemas["vault_address"] = ConfigSchema("vault_address", str, False, "", "a test setting",
                                                            enterprise_only=True)
    monkeypatch.setattr(ConfigManager, "_initialize_core_schemas", schemas)
    for name in list(os.environ):
        if name.endswith("FRAMEWORK_MODE") or name.endswith("VAULT_ADDRESS"):
            monkeypatch.delenv(name)


def configs(tmp_path, **settings):
    folder = tmp_path / "configs"
    folder.mkdir()
    if settings:
        (folder / "config.yml").write_text(yaml.safe_dump(settings), encoding="utf-8")
    return folder


def test_the_mode_maps_to_the_advanced_mode_in_one_place():
    assert not advanced_mode_for("private").enabled
    assert not advanced_mode_for("private", [AdvancedFeature.TEMPLATES]).enabled_features, "private: no feature"
    enterprise = advanced_mode_for("enterprise", [AdvancedFeature.TEMPLATES])
    assert enterprise.enabled and enterprise.enabled_features == {*ENTERPRISE_FEATURES, AdvancedFeature.TEMPLATES}
    assert ENTERPRISE_FEATURES == frozenset(), "no feature is wired yet; each joins when it is"


def test_private_keeps_the_advanced_mode_off(tmp_path, enterprise_setting):
    config = ConfigManager(configs(tmp_path, vault_address="https://vault.example"))
    assert not config.advanced_mode.enabled
    assert config.get("vault_address", "") == "", "a setting only enterprise may set is not read"
    assert not (config.config_dir / "secrets").exists()


def test_enterprise_switches_it_on_with_its_private_folders_and_its_settings(tmp_path, enterprise_setting):
    config = ConfigManager(configs(tmp_path, framework_mode="enterprise", vault_address="https://vault.example"))
    assert config.advanced_mode.enabled and config.advanced_mode.enabled_features == set(ENTERPRISE_FEATURES)
    assert config.get("vault_address") == "https://vault.example", "read after the mode switched it on"
    for name in ("secrets", "compliance", "audit"):
        folder = config.config_dir / name
        assert folder.is_dir(), name
        if os.name == "posix":
            assert stat.S_IMODE(folder.stat().st_mode) == 0o700, name


def test_the_advanced_mode_follows_a_change_of_the_mode_at_once(tmp_path, enterprise_setting, monkeypatch):
    config = ConfigManager(configs(tmp_path, framework_mode="enterprise", vault_address="https://vault.example"))
    monkeypatch.setattr(config, "save_user_config", lambda: True)
    assert set_framework_mode(config, "private") == "private"
    assert not config.advanced_mode.enabled
    assert config.get("vault_address") == "", "back to its default"
    assert config.set("vault_address", "https://other.example") is False, "only enterprise sets it"
    assert set_framework_mode(config, "enterprise") == "enterprise" and config.advanced_mode.enabled
    assert config.set("vault_address", "https://other.example") is True


def test_enterprise_keeps_the_change_history(tmp_path, enterprise_setting):
    config = ConfigManager(configs(tmp_path, framework_mode="enterprise"))
    config.set("log_level", "DEBUG")
    assert config.change_history[-1]["key"] == "log_level"
    (tmp_path / "p").mkdir()
    private = ConfigManager(configs(tmp_path / "p"))
    private.set("log_level", "DEBUG")
    assert private.change_history == []


def test_a_caller_that_asks_for_the_advanced_mode_keeps_it_in_private(tmp_path, enterprise_setting):
    config = ConfigManager(configs(tmp_path), advanced_mode=True, enable_templates=True)
    assert config.advanced_mode.enabled
    assert config.advanced_mode.is_feature_enabled(AdvancedFeature.TEMPLATES)


def test_the_groundwork_is_kept_whole():
    """Every advanced feature and the advanced mode's switches stay (DESIGN: a preparation built for a purpose is never
    removed; it is wired when the enterprise mode needs it)."""
    assert {f.value for f in AdvancedFeature} == {"secrets_management", "dynamic_updates", "templates", "migration",
                                                  "environments", "compliance"}
    fields = set(cm.AdvancedModeConfig.__dataclass_fields__)
    assert {"show_advanced_options", "show_expert_warnings", "require_confirmation", "audit_changes",
            "require_validation", "enforce_compliance"} <= fields
