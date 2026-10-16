#!/usr/bin/env python3
"""
File > Settings... (GUI parity W1': `llm-cli config show` and `llm-cli config repos [--add NAME URL] [--remove NAME]`
by mouse).

Effective settings: every setting with its value and where the value comes from (settings_view.effective_settings,
the same view as `config show`: secrets redacted). Read-only. The output folder is set with its own entry
(File > Output Folder..., also offered here).

Source repositories: the repository overrides builds receive as <NAME>_REPO_OVERRIDE (settings_view.repo_overrides /
add_repo_override / remove_repo_override, the functions of `config repos`). A URL is shown with any credential in it
redacted. Removing one asks first with exactly what goes and where it is saved ("No" is the default); nothing else is
removed. Every text is plain text.
"""

import json
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.localization import current_language, ui_text


def source_text(source: str, lang: Optional[str] = None) -> str:
    """Where a value comes from, in the person's language (a settings file with its full path)."""
    if source.startswith("file "):
        return ui_text("bld.settings.source.file", lang, path=source[5:])
    if source in ("default", "environment", "override"):
        return ui_text(f"bld.settings.source.{source}", lang)
    return source


def value_text(value: Any) -> str:
    """A setting's value on one line (lists and maps as JSON)."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _variable(name: str) -> str:
    from orchestrator.Core.builder import repo_override_names
    names = repo_override_names(name)
    return names[0] if names else "-"


class SettingsDialog(QDialog):
    """The effective settings (read-only) and the source repository overrides of `framework`.
    `choose_output_folder`: the main window's File > Output Folder... (offered here too)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 choose_output_folder: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.framework = framework
        self.config = framework.config
        self.lang = lang or current_language()
        self._choose_output_folder = choose_output_folder
        self.setWindowTitle(self._t("bld.settings.title"))
        self.resize(1100, 560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._effective_tab(), self._t("bld.settings.tab.effective"))
        self.tabs.addTab(self._repos_tab(), self._t("bld.settings.tab.repos"))
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _config_file(self) -> str:
        return str(Path(getattr(self.config, "config_dir", ".")) / "config.yml")

    # --- effective settings (config show) -------------------------------------------------------------------

    def _effective_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(plain_label(self._t("bld.settings.effective.intro"), page))
        row = QHBoxLayout()
        self.lbl_output = plain_label("", page)
        row.addWidget(self.lbl_output, 1)
        self.btn_output = QPushButton(button_text(self._t("bld.settings.output_folder")), page)
        self.btn_output.setObjectName("btn_settings_output_folder")
        self.btn_output.setAutoDefault(False)
        self.btn_output.clicked.connect(self._output_folder)
        self.btn_output.setVisible(self._choose_output_folder is not None)
        row.addWidget(self.btn_output)
        layout.addLayout(row)
        self.table_effective = QTableWidget(0, 3, page)
        self.table_effective.setObjectName("table_settings_effective")
        self.table_effective.setHorizontalHeaderLabels([self._t("bld.settings.col.name"),
                                                        self._t("bld.settings.col.value"),
                                                        self._t("bld.settings.col.source")])
        self.table_effective.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_effective.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_effective.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_effective.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table_effective)
        return page

    def refresh_effective(self) -> None:
        from orchestrator.Core.settings_view import effective_settings
        shown = effective_settings(self.config)   # secrets redacted, as `config show`
        self.effective = shown
        self.table_effective.setRowCount(len(shown))
        for row, (name, entry) in enumerate(shown.items()):
            for column, text in enumerate((name, value_text(entry["value"]),
                                           source_text(str(entry["source"]), self.lang))):
                self.table_effective.setItem(row, column, QTableWidgetItem(one_line(text)))
        try:
            from orchestrator.Core.artifact_output import output_folder
            self.lbl_output.setText(self._t("bld.settings.output_now", path=one_line(str(output_folder(
                self.framework)))))
        except Exception:  # noqa: BLE001 - a stand-in framework: the line stays empty
            self.lbl_output.setText("")

    def _output_folder(self) -> None:
        if self._choose_output_folder is not None:
            self._choose_output_folder()
        self.refresh_effective()

    # --- source repositories (config repos) -----------------------------------------------------------------

    def _repos_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.lbl_repos = plain_label("", page)
        layout.addWidget(self.lbl_repos)
        self.table_repos = QTableWidget(0, 3, page)
        self.table_repos.setObjectName("table_settings_repos")
        self.table_repos.setHorizontalHeaderLabels([self._t("bld.settings.col.repo"), self._t("bld.settings.col.url"),
                                                    self._t("bld.settings.col.variable")])
        self.table_repos.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_repos.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_repos.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_repos.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_repos.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table_repos)
        form = QFormLayout()
        self.edit_repo_name = QLineEdit(page)
        self.edit_repo_name.setObjectName("edit_repo_name")
        self.edit_repo_name.setPlaceholderText(self._t("bld.settings.repos.name_hint"))
        self.edit_repo_url = QLineEdit(page)
        self.edit_repo_url.setObjectName("edit_repo_url")
        self.edit_repo_url.setPlaceholderText(self._t("bld.settings.repos.url_hint"))
        form.addRow(self._t("bld.settings.repos.name"), self.edit_repo_name)
        form.addRow(self._t("bld.settings.repos.url"), self.edit_repo_url)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.btn_repo_add = QPushButton(button_text(self._t("bld.settings.repos.add")), page)
        self.btn_repo_add.setObjectName("btn_repo_add")
        self.btn_repo_remove = QPushButton(button_text(self._t("bld.settings.repos.remove")), page)
        self.btn_repo_remove.setObjectName("btn_repo_remove")
        for button, slot in ((self.btn_repo_add, self.add_repo), (self.btn_repo_remove, self.remove_repo)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.lbl_repo_result = plain_label("", page)
        layout.addWidget(self.lbl_repo_result)
        return page

    def refresh_repos(self) -> None:
        from orchestrator.Core.settings_view import repo_overrides
        self.repos = repo_overrides(self.config)
        path = one_line(self._config_file())
        self.lbl_repos.setText(self._t("bld.settings.repos.intro", path=path) + "\n" +
                               ("" if self.repos else self._t("bld.settings.repos.empty")))
        self.table_repos.setRowCount(len(self.repos))
        for row, (name, url) in enumerate(self.repos.items()):
            for column, text in enumerate((name, redact_secrets(url), _variable(name))):
                self.table_repos.setItem(row, column, QTableWidgetItem(one_line(text)))
        self.btn_repo_remove.setEnabled(bool(self.repos))

    def selected_repo(self) -> Optional[str]:
        rows = self.table_repos.selectionModel().selectedRows() if self.table_repos.selectionModel() else []
        if not rows:
            return None
        item = self.table_repos.item(rows[0].row(), 0)
        return item.text() if item is not None else None

    def add_repo(self) -> None:
        from orchestrator.Core.settings_view import RepoOverrideError, add_repo_override
        name, url = self.edit_repo_name.text().strip(), self.edit_repo_url.text().strip()
        if not name or not url:
            plain_box(self, "information", self._t("bld.settings.tab.repos"), self._t("bld.settings.repos.need_both"))
            return
        try:
            variable = add_repo_override(self.config, name, url)
        except RepoOverrideError as e:
            plain_box(self, "warning", self._t("bld.settings.tab.repos"), one_line(str(e)))
            return
        self.lbl_repo_result.setText(self._t("bld.settings.repos.added", name=one_line(name.lower()),
                                             url=one_line(redact_secrets(url)), variable=variable))
        self.edit_repo_name.clear()
        self.edit_repo_url.clear()
        self.refresh()

    def remove_repo(self) -> None:
        from orchestrator.Core.settings_view import RepoOverrideError, remove_repo_override
        name = self.selected_repo()
        if not name:
            plain_box(self, "information", self._t("bld.settings.tab.repos"), self._t("bld.settings.repos.pick"))
            return
        question = self._t("bld.settings.repos.remove_question", name=one_line(name),
                           url=one_line(redact_secrets(self.repos.get(name, "-"))), variable=_variable(name),
                           path=one_line(self._config_file()))
        if not ask(self, self._t("bld.settings.tab.repos"), question):   # "No" is the default
            return
        try:
            removed = remove_repo_override(self.config, name)
        except RepoOverrideError as e:
            plain_box(self, "warning", self._t("bld.settings.tab.repos"), one_line(str(e)))
            return
        self.lbl_repo_result.setText(self._t("bld.settings.repos.removed" if removed
                                             else "bld.settings.repos.not_found", name=one_line(name)))
        self.refresh()

    def refresh(self) -> None:
        self.refresh_effective()
        self.refresh_repos()


__all__ = ["SettingsDialog", "source_text", "value_text"]
