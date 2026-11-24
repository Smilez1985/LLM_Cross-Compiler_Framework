#!/usr/bin/env python3
"""
Ditto's switch (DESIGN "Targets, hardware and templates", the row "Ditto's switch, and Ditto as the pilot in the module
wizard"; maintainer's decision of 2026-11-24).

Ditto stays optional. When it is set up, the person switches it on and off:
- the main switch, the person-only setting `ditto_enabled` (default on), in the main window's tool bar beside Ditto's
  avatar: off, no window asks Ditto and the chat says Ditto is off;
- one switch per window where Ditto helps (WINDOWS: the Builder, the module wizard), which follows the main switch and
  can turn Ditto off in that window alone (`ditto_off_in`, the window ids, person-only, remembered). A window's switch
  is on only while the main switch is on.

Both switches are shown only when Ditto is set up (a provider and its access are configured); without Ditto every
window behaves as before (its Ditto buttons offer the setup). Only the person's click changes them: the settings are
in llm/settings_change.PERSON_DECIDES, so no AI and no settings change of an AI can.
"""

from typing import Any, List, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QCheckBox, QWidget

from orchestrator.gui.plain import button_text, plain_tooltip
from orchestrator.utils.localization import current_language, ui_text

MAIN = "ditto_enabled"
OFF_IN = "ditto_off_in"
WINDOWS = ("builder", "wizard")


class DittoSwitch(QObject):
    """The switch's state from the framework's settings; `changed` after every change (the windows follow it)."""
    changed = Signal()

    def __init__(self, config: Any, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.config = config

    def _get(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(key, default)
        except Exception:  # noqa: BLE001 - a config that cannot be read: the defaults (Ditto on)
            return default

    def main_on(self) -> bool:
        return bool(self._get(MAIN, True))

    def off_in(self) -> List[str]:
        value = self._get(OFF_IN, []) or []
        return [w for w in value if w in WINDOWS] if isinstance(value, (list, tuple)) else []

    def window_on(self, window: str) -> bool:
        """The window's own switch (it can be on while the main switch is off; on() says what counts)."""
        return window not in self.off_in()

    def on(self, window: Optional[str] = None) -> bool:
        """Whether Ditto may act: the main switch, and the window's switch when a window is named."""
        return self.main_on() and (window is None or self.window_on(window))

    def _save(self) -> bool:
        save = getattr(self.config, "save_user_config", None)
        try:
            return bool(save()) if callable(save) else True
        except Exception:  # noqa: BLE001 - not saved: it holds for this session, the caller says so
            return False

    def set_main(self, on: bool) -> bool:
        """The person's click on the main switch; False when it could not be saved (it holds for this session)."""
        self.config.set(MAIN, bool(on))
        saved = self._save()
        self.changed.emit()
        return saved

    def set_window(self, window: str, on: bool) -> bool:
        """The person's click on a window's switch."""
        if window not in WINDOWS:
            raise ValueError(f"no such window: {window}")
        off = [w for w in self.off_in() if w != window] + ([] if on else [window])
        self.config.set(OFF_IN, off)
        saved = self._save()
        self.changed.emit()
        return saved


def switch_of(framework: Any) -> DittoSwitch:
    """The framework's one switch (made on first use, kept on the framework object)."""
    switch = getattr(framework, "_ditto_switch", None)
    if switch is None:
        switch = DittoSwitch(getattr(framework, "config", None))
        try:
            framework._ditto_switch = switch
        except AttributeError:   # a framework that takes no attribute: a switch per call, still from the settings
            pass
    return switch


def ditto_set_up(framework: Any) -> bool:
    """Whether Ditto is set up: a provider and its access resolve for a chat (DittoCoder.is_configured)."""
    get = getattr(framework, "get_component", None)
    ditto = get("ditto_manager") if callable(get) else getattr(framework, "ditto_manager", None)
    check = getattr(ditto, "is_configured", None)
    try:
        return bool(check("chat")) if callable(check) else False
    except Exception:  # noqa: BLE001 - a setup that cannot be checked counts as not set up
        return False


class SwitchBox(QCheckBox):
    """A check box of the switch: the main switch (`window` None) or a window's own. Hidden while Ditto is not set up;
    a window's box is disabled, with the reason, while the main switch is off. `framework` is the framework, or a
    function that gives it (a window that gets its framework after it was built, as the module wizard); `refresh()`
    reads it again. `applied` follows every refresh, so the window's Ditto buttons follow the switch."""
    applied = Signal()

    def __init__(self, framework: Any, window: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._framework, self.window = framework, window
        self._bound: Optional[DittoSwitch] = None
        self._updating = False
        self.setObjectName("chk_ditto_main" if window is None else f"chk_ditto_{window}")
        self.toggled.connect(self._clicked)
        self.refresh()

    @property
    def framework(self) -> Any:
        f = self._framework
        return f() if callable(f) and not hasattr(f, "config") else f

    @property
    def switch(self) -> DittoSwitch:
        return switch_of(self.framework)

    def refresh(self) -> None:
        switch = self.switch
        if switch is not self._bound:   # the framework came later or changed: follow its switch
            if self._bound is not None:
                try:
                    self._bound.changed.disconnect(self.refresh)
                except (RuntimeError, TypeError):
                    pass
            switch.changed.connect(self.refresh)
            self._bound = switch
        lang = current_language()
        self._updating = True
        try:
            self.setVisible(ditto_set_up(self.framework))
            if self.window is None:
                self.setText(button_text(ui_text("ditto.switch.main", lang)))
                self.setChecked(switch.main_on())
                self.setToolTip(plain_tooltip(ui_text("ditto.switch.main_tip", lang)))
            else:
                self.setText(button_text(ui_text("ditto.switch.window", lang)))
                main = switch.main_on()
                self.setChecked(main and switch.window_on(self.window))
                self.setEnabled(main)
                self.setToolTip(plain_tooltip(ui_text("ditto.switch.window_tip" if main else "ditto.switch.main_off",
                                                      lang)))
        finally:
            self._updating = False
        self.applied.emit()

    def _clicked(self, on: bool) -> None:
        if self._updating:
            return
        if self.window is None:
            self.switch.set_main(on)
        else:
            self.switch.set_window(self.window, on)


def off_reason(framework: Any, window: Optional[str] = None) -> str:
    """Why Ditto does not act here ("" when it may): the main switch or the window's switch is off. Without Ditto set
    up the switches are hidden and decide nothing ("": the window's Ditto buttons offer the setup as before)."""
    if not ditto_set_up(framework):
        return ""
    switch = switch_of(framework)
    if not switch.main_on():
        return ui_text("ditto.switch.main_off")
    if window is not None and not switch.window_on(window):
        return ui_text("ditto.switch.window_off")
    return ""


__all__ = ["DittoSwitch", "MAIN", "OFF_IN", "SwitchBox", "WINDOWS", "ditto_set_up", "off_reason", "switch_of"]
