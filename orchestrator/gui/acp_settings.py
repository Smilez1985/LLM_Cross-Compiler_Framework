#!/usr/bin/env python3
"""
File > Settings... > Ditto in editors: how an editor adds Ditto as an ACP agent (`llm-cli acp config` by mouse: the
command and the agent_servers entry, each with a Copy button, never a "type this" text) and the MCP servers editors
passed to Ditto (`llm-cli acp servers list/enable/disable`), with the person's consent per entry. No field here is
one Ditto's GUI tools may fill: only the person decides (Core/acp_editor_servers.py; this version connects to none).
"""

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, one_line, plain_label
from orchestrator.utils.localization import current_language, ui_text


class AcpSettings(QWidget):
    """The tab: the editor entry with Copy buttons, and the recorded editor MCP servers with a consent box each."""

    COLUMNS = ("enabled", "name", "what", "editor", "seen")

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 app_root: Any = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.setObjectName("tab_settings_acp")
        self._ids = []
        self._filling = False
        layout = QVBoxLayout(self)
        intro = plain_label(self._t("acp.intro"), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        from orchestrator.utils.stdio_programs import acp_entries, installation_root
        try:
            entries = acp_entries(app_root or installation_root())
            problem = ""
        except ValueError as e:
            entries, problem = {"command": "", "json": ""}, str(e)
        layout.addWidget(plain_label(self._t("acp.command.label"), self))
        row = QHBoxLayout()
        self.edit_command = QLineEdit(entries["command"], self)
        self.edit_command.setObjectName("edit_acp_command")
        self.edit_command.setReadOnly(True)
        row.addWidget(self.edit_command, 1)
        self.btn_copy_command = self._button("btn_acp_copy_command", "acp.copy",
                                             lambda: self._copy(self.edit_command.text()))
        row.addWidget(self.btn_copy_command)
        layout.addLayout(row)
        layout.addWidget(plain_label(self._t("acp.json.label"), self))
        self.text_json = QPlainTextEdit(entries["json"], self)
        self.text_json.setObjectName("text_acp_json")
        self.text_json.setReadOnly(True)
        self.text_json.setMaximumHeight(160)
        layout.addWidget(self.text_json)
        row = QHBoxLayout()
        self.btn_copy_json = self._button("btn_acp_copy_json", "acp.copy",
                                          lambda: self._copy(self.text_json.toPlainText()))
        row.addWidget(self.btn_copy_json)
        row.addStretch(1)
        layout.addLayout(row)
        self.lbl_result = plain_label(self._t("acp.entry.problem", reason=one_line(problem)) if problem else "", self)
        self.lbl_result.setObjectName("lbl_acp_result")
        layout.addWidget(self.lbl_result)

        layout.addWidget(plain_label(self._t("acp.servers.title"), self))
        note = plain_label(self._t("acp.servers.note"), self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.lbl_empty = plain_label(self._t("acp.servers.empty"), self)
        layout.addWidget(self.lbl_empty)
        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setObjectName("table_acp_servers")
        self.table.setHorizontalHeaderLabels([self._t(f"acp.servers.col.{c}") for c in self.COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._changed)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.btn_refresh = self._button("btn_acp_servers_refresh", "acp.servers.refresh", self.refresh)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self.refresh()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, name: str, key: str, slot) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.lbl_result.setText(self._t("acp.copied"))

    def _store(self):
        from orchestrator.Core.acp_editor_servers import EditorServers
        config = getattr(self.framework, "config", None)
        folder = getattr(config, "user_state_dir", None)
        if not folder:
            from orchestrator.Core.config_manager import ConfigManager
            folder = ConfigManager.user_state_path()
        return EditorServers(folder)

    def refresh(self) -> None:
        servers = self._store().list()
        self._filling = True
        try:
            self.table.setRowCount(0)
            self._ids = []
            for server in servers:
                row = self.table.rowCount()
                self.table.insertRow(row)
                box = QTableWidgetItem("")
                box.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                box.setCheckState(Qt.Checked if server.enabled else Qt.Unchecked)
                self.table.setItem(row, 0, box)
                for column, value in enumerate((server.name, server.summary(), server.editor, server.last_seen), 1):
                    self.table.setItem(row, column, QTableWidgetItem(one_line(value)))
                self._ids.append(server.id)
        finally:
            self._filling = False
        self.lbl_empty.setVisible(not servers)
        self.table.setVisible(bool(servers))

    def _changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 0 or item.row() >= len(self._ids):
            return
        enabled = item.checkState() == Qt.Checked
        try:
            changed = self._store().set_enabled(self._ids[item.row()], enabled)
        except OSError as e:
            self.lbl_result.setText(self._t("acp.servers.failed", reason=one_line(str(e))))
            return
        if changed is not None:
            state = self._t("acp.servers.state.on" if enabled else "acp.servers.state.off")
            self.lbl_result.setText(self._t("acp.servers.saved", name=one_line(changed.name), state=state))
