"""
The GUI takes what another framework process saved (DESIGN, No dead code: the open findings of the second recon,
decided on 2026-11-27): ConfigManager.reload_user_config through config_manager.take_saved_settings, when the activity
row of an AI app's update_settings names keys, and when File > Settings... or Help > Framework Log... opens; a change of
log_levels applies the levels per part again.
"""

import logging
from types import SimpleNamespace

import pytest

from orchestrator.Core.activity import changed_setting_keys
from orchestrator.Core.config_manager import ConfigManager, take_saved_settings
from orchestrator.utils.logging import apply_part_levels, get_logger


@pytest.fixture(autouse=True)
def no_part_levels():
    apply_part_levels({})
    yield
    apply_part_levels({})


def row(kind="ai_action", **params):
    return SimpleNamespace(entry=SimpleNamespace(kind=kind, params=params))


def test_the_keys_of_update_settings_rows_once_each():
    entries = [row(tool="update_settings", keys="build_jobs, language"), row(tool="start_build"),
               row(kind="notice", tool="update_settings", keys="x"), row(tool="update_settings", keys="language,")]
    assert changed_setting_keys(entries) == ["build_jobs", "language"]
    assert changed_setting_keys([]) == [] and changed_setting_keys(None) == []


def test_a_setting_another_process_saved_reaches_this_one_with_the_levels_per_part(tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    gui, cli = ConfigManager(folder), ConfigManager(folder)
    assert cli.set("log_levels", {"ReloadProbe": "DEBUG"}) and cli.save_user_config()
    logger = get_logger("ReloadProbe")
    result = take_saved_settings(gui)
    assert "log_levels" in result.changed and gui.get("log_levels") == {"ReloadProbe": "DEBUG"}
    assert logger.level == logging.DEBUG, "the levels per part are applied again"
    assert take_saved_settings(gui, ["log_levels"]).changed == ()
    assert take_saved_settings(None) is None and take_saved_settings(SimpleNamespace()) is None


def test_the_chat_panel_takes_the_keys_an_ai_app_changed(monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.Core import config_manager
    from orchestrator.gui.chat_panel.panel import ChatPanel
    taken = []
    monkeypatch.setattr(config_manager, "take_saved_settings", lambda config, keys=None: taken.append((config, keys)))
    config = object()
    panel = SimpleNamespace(feed_view=SimpleNamespace(append_entries=lambda entries: None),
                            _after_new_rows=lambda: None, ditto=None, framework=SimpleNamespace(config=config))
    ChatPanel._on_new_entries(panel, [row(tool="update_settings", keys="build_jobs")])
    ChatPanel._on_new_entries(panel, [row(tool="start_build")])
    assert taken == [(config, ["build_jobs"])]


def test_the_framework_log_window_reads_the_file_again_when_it_opens(qapp, monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from orchestrator.Core import config_manager
    from orchestrator.gui.menus import logs
    taken = []
    monkeypatch.setattr(config_manager, "take_saved_settings", lambda config, keys=None: taken.append(config))
    config = SimpleNamespace(get=lambda key, default=None: str(tmp_path) if key == "logs_dir" else default)
    from orchestrator.gui import log_window
    opened = []
    monkeypatch.setattr(log_window, "FrameworkLogWindow",
                        lambda folder, parent=None, config=None: opened.append(config) or SimpleNamespace(show=lambda: None))
    window = SimpleNamespace(framework=SimpleNamespace(config=config), framework_log_window=None)
    logs.open_log_window(window)
    assert taken == [config] and opened == [config]
