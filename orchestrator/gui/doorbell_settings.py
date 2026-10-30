#!/usr/bin/env python3
"""
File > Settings... > Build doorbell: the person's MQTT broker for the build doorbell (Core/doorbell.py) and its
"Send a test doorbell" (`llm-cli config doorbell [--test]` on the command line). The settings are the person's alone;
the password goes into the credential vault, never into the settings file. A test runs in a worker thread.
"""

import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from orchestrator.gui.plain import one_line, plain_label
from orchestrator.utils.localization import current_language, ui_text


class _Relay(QObject):
    done = Signal(bool, str)


class DoorbellSettings(QWidget):
    """The tab: broker URL, user name, topic prefix, CA file, password (to the vault), Save, Send a test doorbell."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.config = framework.config
        self.lang = lang or current_language()
        self.setObjectName("tab_settings_doorbell")
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("bell.intro"), self))
        form = QFormLayout()
        self.edit_url = self._edit("edit_bell_broker", form, "bell.field.broker")
        self.edit_user = self._edit("edit_bell_username", form, "bell.field.username")
        self.edit_prefix = self._edit("edit_bell_prefix", form, "bell.field.prefix")
        self.edit_ca = self._edit("edit_bell_ca_file", form, "bell.field.ca_file")
        self.edit_password = self._edit("edit_bell_password", form, "bell.field.password")
        self.edit_password.setEchoMode(QLineEdit.Password)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.btn_save = QPushButton(self._t("bell.save"), self)
        self.btn_save.setObjectName("btn_bell_save")
        self.btn_save.clicked.connect(self.save)
        self.btn_test = QPushButton(self._t("bell.test"), self)
        self.btn_test.setObjectName("btn_bell_test")
        self.btn_test.clicked.connect(self.test)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        layout.addLayout(row)
        self.lbl_result = QLabel("", self)
        self.lbl_result.setObjectName("lbl_bell_result")
        self.lbl_result.setWordWrap(True)
        layout.addWidget(self.lbl_result)
        layout.addStretch(1)
        self._relay = _Relay(self)
        self._relay.done.connect(self._on_test_done)
        self.refresh()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _edit(self, name: str, form: QFormLayout, label: str) -> QLineEdit:
        edit = QLineEdit(self)
        edit.setObjectName(name)
        form.addRow(self._t(label), edit)
        return edit

    def refresh(self) -> None:
        get = self.config.get
        self.edit_url.setText(str(get("mqtt_broker_url", "") or ""))
        self.edit_user.setText(str(get("mqtt_username", "") or ""))
        self.edit_prefix.setText(str(get("mqtt_topic_prefix", "llmcf") or "llmcf"))
        self.edit_ca.setText(str(get("mqtt_ca_file", "") or ""))
        self.edit_password.clear()

    def save(self) -> bool:
        """Saves the settings by the doorbell's rules (Core/doorbell.apply_settings) and a typed password into the
        vault; says what happened in plain words."""
        from orchestrator.Core.doorbell import PASSWORD_SECRET, DoorbellRefused, apply_settings
        try:
            apply_settings(self.config, {"mqtt_broker_url": self.edit_url.text(),
                                         "mqtt_username": self.edit_user.text(),
                                         "mqtt_topic_prefix": self.edit_prefix.text() or "llmcf",
                                         "mqtt_ca_file": self.edit_ca.text()})
        except (DoorbellRefused, OSError) as e:
            self.lbl_result.setText(one_line(str(e)))
            return False
        if self.edit_password.text():
            from orchestrator.Core.secret_entries import store_secret
            try:
                store_secret(getattr(self.framework, "secrets_manager", None), PASSWORD_SECRET,
                             self.edit_password.text())
            except Exception as e:  # noqa: BLE001 - shown as plain text, never the password
                self.lbl_result.setText(self._t("bell.password_not_saved", reason=e.__class__.__name__))
                return False
            self.edit_password.clear()
        self.lbl_result.setText(self._t("bell.saved"))
        return True

    def test(self) -> None:
        """Send a test doorbell, in a worker thread; the result comes back to the label."""
        from orchestrator.Core.doorbell import send_test_doorbell
        self.btn_test.setEnabled(False)
        self.lbl_result.setText(self._t("bell.testing"))
        relay, framework = self._relay, self.framework

        def run():
            try:
                ok, text = send_test_doorbell(framework)
            except Exception as e:  # noqa: BLE001
                ok, text = False, e.__class__.__name__
            relay.done.emit(ok, text)

        threading.Thread(target=run, name="doorbell-test", daemon=True).start()

    def _on_test_done(self, ok: bool, text: str) -> None:
        try:
            self.btn_test.setEnabled(True)
            self.lbl_result.setText(one_line(text))
        except RuntimeError:   # the window was closed meanwhile
            pass
