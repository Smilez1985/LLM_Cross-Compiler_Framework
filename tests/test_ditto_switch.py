"""Ditto's switch (orchestrator/gui/ditto_switch.py; DESIGN "Targets, hardware and templates", the row "Ditto's switch,
and Ditto as the pilot in the module wizard"): a main switch and a switch per window that can turn Ditto off there,
shown only when Ditto is set up, set only by the person."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")   # a GUI module: skipped by the install without the 'gui' extra
# (its settings, which need no Qt, are tested in test_ditto_switch_settings.py)

from orchestrator.gui import ditto_switch  # noqa: E402
from orchestrator.gui.ditto_switch import DittoSwitch, SwitchBox, off_reason, switch_of  # noqa: E402


class Config:
    def __init__(self, **values):
        self.values, self.saved = dict(values), 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, source_type="override"):
        self.values[key] = value
        return True

    def save_user_config(self):
        self.saved += 1
        return True


def framework(set_up=True, **values):
    ditto = SimpleNamespace(is_configured=lambda task="chat": set_up)
    return SimpleNamespace(config=Config(**values), ditto_manager=ditto,
                           get_component=lambda name: ditto if name == "ditto_manager" else None)


class TestTheSwitch:
    def test_on_by_default_and_a_window_can_turn_it_off_alone(self):
        fw = framework()
        switch = switch_of(fw)
        assert switch is switch_of(fw), "one switch per framework"
        assert switch.on() and switch.on("builder") and switch.on("wizard")
        switch.set_window("wizard", False)
        assert switch.on("builder") and not switch.on("wizard") and fw.config.values["ditto_off_in"] == ["wizard"]
        switch.set_window("wizard", True)
        assert switch.on("wizard") and fw.config.values["ditto_off_in"] == []
        assert fw.config.saved == 2, "the person's choice is remembered"

    def test_the_main_switch_off_turns_every_window_off_and_back(self):
        fw = framework()
        switch = switch_of(fw)
        switch.set_window("builder", False)
        switch.set_main(False)
        assert not switch.on() and not switch.on("wizard") and not switch.on("builder")
        switch.set_main(True)
        assert switch.on("wizard") and not switch.on("builder"), "a window's own choice stays"

    def test_an_unknown_window_or_a_bad_value_decides_nothing(self):
        switch = DittoSwitch(Config(ditto_off_in="builder"))
        assert switch.on("builder"), "only a list counts"
        with pytest.raises(ValueError):
            switch.set_window("chat", False)
        assert DittoSwitch(None).on("builder"), "no settings: the defaults"

    def test_the_reason_names_which_switch_is_off(self):
        fw = framework()
        assert off_reason(fw, "builder") == ""
        switch_of(fw).set_window("builder", False)
        assert "in this window" in off_reason(fw, "builder") and off_reason(fw, "wizard") == ""
        switch_of(fw).set_main(False)
        assert "tool bar" in off_reason(fw, "wizard")

    def test_without_ditto_set_up_the_switch_decides_nothing(self):
        fw = framework(set_up=False, ditto_enabled=False)
        assert off_reason(fw, "builder") == "", "the window's Ditto buttons offer the setup as before"

    def test_the_settings_are_known_and_kept(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        config = ConfigManager(tmp_path / "configs")
        assert config.get("ditto_enabled") is True and config.get("ditto_off_in") == []
        switch = DittoSwitch(config)
        switch.set_window("wizard", False)
        switch.set_main(False)
        again = ConfigManager(tmp_path / "configs")
        assert again.get("ditto_enabled") is False and again.get("ditto_off_in") == ["wizard"]


class TestTheBoxes:
    def test_hidden_while_ditto_is_not_set_up(self, qapp):
        box = SwitchBox(framework(set_up=False), "builder")
        assert box.isHidden()
        box.deleteLater()

    def test_a_windows_box_follows_the_main_switch(self, qapp):
        fw = framework()
        main, window = SwitchBox(fw), SwitchBox(fw, "wizard")
        assert not main.isHidden() and main.isChecked() and window.isChecked() and window.isEnabled()
        window.setChecked(False)
        assert fw.config.values["ditto_off_in"] == ["wizard"]
        main.setChecked(False)
        assert not window.isChecked() and not window.isEnabled() and "tool bar" in window.toolTip()
        main.setChecked(True)
        assert window.isEnabled() and not window.isChecked(), "its own choice stays"
        main.deleteLater()
        window.deleteLater()

    def test_a_framework_that_comes_later_is_followed(self, qapp):
        holder = SimpleNamespace(fw=None)
        box = SwitchBox(lambda: holder.fw, "wizard")
        assert box.isHidden()
        holder.fw = framework()
        box.refresh()
        assert not box.isHidden() and box.isChecked()
        switch_of(holder.fw).set_window("wizard", False)
        assert not box.isChecked(), "it follows the switch of the framework it got"
        box.deleteLater()


@pytest.fixture
def builder(booted_framework, qapp, monkeypatch):
    monkeypatch.setattr(ditto_switch, "ditto_set_up", lambda fw: True)
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(booted_framework)
    yield tab
    switch_of(booted_framework).set_main(True)
    switch_of(booted_framework).set_window("builder", True)
    tab.deleteLater()


def test_the_builders_ditto_buttons_follow_its_switch(builder):
    """The check box "Enable Ditto AI Optimization" became the Builder's switch: off, Ask Ditto for Config is disabled
    with the reason, a click asks nothing, the board advice's "Ask Ditto" goes nowhere and Ditto's alternatives go."""
    assert builder.btn_optimize.isEnabled()
    builder.pilot_alternatives.show_alternatives("a/b-9B", "rk3588", [{"model": "a/b-2B", "quantization": "Q4_0"}])
    asked = []
    builder.ask_ditto_requested.connect(asked.append)
    builder.chk_ai.setChecked(False)
    assert not builder.btn_optimize.isEnabled() and "in this window" in builder.btn_optimize.toolTip()
    assert builder.pilot_alternatives.isHidden()
    builder._run_ai_optimizer()
    builder._ask_ditto_in_chat("which board?")
    assert asked == [] and not hasattr(builder, "ai_worker")
    assert builder.log_area.toPlainText().count("switched off in this window") == 2
    builder.chk_ai.setChecked(True)
    assert builder.btn_optimize.isEnabled()
    builder._ask_ditto_in_chat("which board?")
    assert asked == ["which board?"]


def test_an_answer_that_arrives_after_the_switch_went_off_fills_nothing(builder):
    builder.format_combo.setCurrentIndex(builder.format_combo.findData("gguf"))
    before = builder.opt_combo.currentText()
    switch_of(builder.framework).set_main(False)
    builder._on_ai_finished(True, {"format": "gguf", "quantization": "Q4_0", "optimization": "SPEED"}, "ok")
    assert builder.opt_combo.currentText() == before and "tool bar" in builder.log_area.toPlainText()


def test_the_wizards_ditto_buttons_follow_its_switch(booted_framework, qapp, tmp_path, monkeypatch):
    from orchestrator.gui import wizards
    monkeypatch.setattr(ditto_switch, "ditto_set_up", lambda fw: True)
    problems = []
    monkeypatch.setattr(wizards.ModuleCreationWizard, "_run_problem", lambda self, text: problems.append(text))
    w = wizards.ModuleCreationWizard(tmp_path / "targets")
    w.framework_manager = booted_framework   # the framework comes after the pages, as in the main window
    w.show()
    try:
        assert not w.chk_ditto.isHidden() and w.btn_formats_propose.isEnabled()
        w.chk_ditto.setChecked(False)
        assert not any(b.isEnabled() for b, _busy in w._ditto_buttons())
        assert "in this window" in w.btn_import_ai.toolTip()
        assert w.propose_formats() is False and w.propose_dependencies() is False
        w.run_ai_analysis()
        assert len(problems) == 3 and all("in this window" in p for p in problems)
        w._enable_ditto_button(w.btn_formats_propose)
        assert not w.btn_formats_propose.isEnabled(), "a request that ends while off brings no button back"
        w.chk_ditto.setChecked(True)
        assert all(b.isEnabled() for b, _busy in w._ditto_buttons())
        assert w.btn_import_ai.toolTip() == w.btn_import_ai.property("ditto_tip")
    finally:
        switch_of(booted_framework).set_window("wizard", True)
        w.reject()
        w.deleteLater()


def test_the_chat_says_ditto_is_off_and_keeps_the_question(tmp_path, monkeypatch, qapp):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui import chat_window
    started = []

    class FakeWorker:
        def __init__(self, *args):
            started.append(args)
            for name in ("answered", "failed", "tool_used"):
                setattr(self, name, SimpleNamespace(connect=lambda f: None))

        def start(self):
            pass

        def isRunning(self):
            return False
    monkeypatch.setattr(chat_window, "ChatWorker", FakeWorker)
    monkeypatch.setattr(ditto_switch, "ditto_set_up", lambda fw: True)
    fw = SimpleNamespace(config=ConfigManager(tmp_path / "configs"), ditto_manager=object(),
                         tool_registry=SimpleNamespace(get=lambda name: None))
    win = chat_window.ChatWindow(fw)
    switch_of(fw).set_main(False)
    win.txt_input.setText("which quantization?")
    win.send_message()
    assert started == [] and win.txt_input.text() == "which quantization?"
    assert "Ditto is switched off" in win.feed_view.plain_text()
    switch_of(fw).set_main(True)
    win.send_message()
    assert len(started) == 1
