#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Community Hub

Browses the community zoo (the signed index plus installed packs) with a trust column and filters, installs a
pack after showing its summary, and "Build" fills the Builder from an installed recipe. CommunityExportDialog
makes a scrubbed pack signed with your key and hands it to a pull request. Nothing is uploaded from here and no
repository token is ever asked for.
"""

from pathlib import Path
from typing import Callable, Optional

import yaml
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtGui import Qt as QtText
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from orchestrator.utils.localization import tr

TRUST_COLORS = {"verified": "#4CAF50", "community": "#2196F3", "local": "#FF9800", "revoked": "#F44336"}


class _Task(QThread):
    """Runs one call off the GUI thread (downloads, checks, gh)."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            self.failed.emit(str(e))


class CommunityHubWindow(QMainWindow):
    installed = Signal(str)  # kind of a pack that was just installed

    def __init__(self, framework_manager, parent=None, on_build_recipe: Optional[Callable] = None):
        super().__init__(parent)
        self.framework_manager = framework_manager
        self.comm_manager = getattr(framework_manager, 'community_manager', None)
        self.on_build_recipe = on_build_recipe
        self.all_items = []
        self._tasks = set()
        self.setWindowTitle(tr("menu.open_hub", "Community Hub"))
        self.resize(1000, 600)
        self._init_ui()
        self.refresh_list()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        intro = QLabel("Community targets, recipes and knowledge. Packs from the signed community index are "
                       "reviewed; a pack imported from a file is not (trust: local).")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        hbox = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("hf.search_placeholder", "Search..."))
        self.search_edit.textChanged.connect(self.filter_list)
        hbox.addWidget(self.search_edit, 1)
        self.kind_filter = QComboBox()
        self.kind_filter.setAccessibleName("Kind filter")
        for label, value in (("All kinds", ""), ("Targets", "target"), ("Recipes", "recipe"),
                             ("Knowledge", "knowledge")):
            self.kind_filter.addItem(label, value)
        self.kind_filter.currentIndexChanged.connect(self.filter_list)
        hbox.addWidget(self.kind_filter)
        self.trust_filter = QComboBox()
        self.trust_filter.setAccessibleName("Trust filter")
        for value in ("", "verified", "community", "local", "revoked"):
            self.trust_filter.addItem(value or "Any trust", value)
        self.trust_filter.currentIndexChanged.connect(self.filter_list)
        hbox.addWidget(self.trust_filter)
        self.refresh_btn = QPushButton(tr("btn.refresh", "Refresh"))
        self.refresh_btn.clicked.connect(lambda: self.refresh_list(refresh=True))
        hbox.addWidget(self.refresh_btn)
        self.import_btn = QPushButton("Import file...")
        self.import_btn.setToolTip("Install a pack file someone gave you (trust: local unless the signed index "
                                   "lists it), or an encrypted pack sent to your key.")
        self.import_btn.clicked.connect(self.import_file)
        hbox.addWidget(self.import_btn)
        layout.addLayout(hbox)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Name", "Kind", "Version", "Trust", "Author", "Status", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setStyleSheet("QTableWidget { background-color: #1e1e1e; color: #eee; gridline-color: #333; } "
                                 "QHeaderView::section { background-color: #333; color: white; }")
        layout.addWidget(self.table)
        self.status_label = QLabel(tr("status.ready", "Ready."))
        self.status_label.setTextFormat(Qt.PlainText)  # errors quote pack contents
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _run(self, fn, on_done, *args, **kwargs) -> _Task:
        # Finished threads are dropped here, never from their own finished signal (that runs in the thread).
        self._tasks = {t for t in self._tasks if not t.isFinished()}
        task = _Task(fn, *args, **kwargs)
        task.done.connect(on_done)
        task.failed.connect(self.on_error)
        self._tasks.add(task)
        task.start()
        return task

    def closeEvent(self, event):
        for task in self._tasks:
            task.wait(30000)
        super().closeEvent(event)

    def refresh_list(self, refresh: bool = False):
        if not self.comm_manager:
            self.status_label.setText("Error: Community Manager missing.")
            return
        self.status_label.setText("Loading the community index...")
        self._run(self._load, self.on_load_finished, refresh)

    def _load(self, refresh: bool):
        return self.comm_manager.listing(refresh)

    def on_load_finished(self, result):
        self.all_items, note = result
        self.filter_list()
        self.status_label.setText(note)

    def on_error(self, err):
        self.status_label.setText(f"Error: {err}")
        QMessageBox.warning(self, "Community Hub", QtText.convertFromPlainText(str(err)))  # quotes pack names

    def filter_list(self, *_):
        text = self.search_edit.text().lower()
        kind, trust = self.kind_filter.currentData(), self.trust_filter.currentData()
        self.populate_table([i for i in self.all_items if (not kind or i.kind == kind)
                             and (not trust or i.trust.value == trust)
                             and text in f"{i.title} {i.id} {i.author}".lower()])

    def populate_table(self, items):
        self.table.setRowCount(0)
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(item.title)
            name.setToolTip(item.description or item.id)
            trust = QTableWidgetItem(item.trust.value)
            trust.setForeground(QColor(TRUST_COLORS.get(item.trust.value, "#eee")))
            status = (f"revoked: {item.revoked}" if item.revoked else
                      f"installed {item.installed_version}" if item.installed_version else "available")
            for column, cell in enumerate((name, QTableWidgetItem(item.kind), QTableWidgetItem(item.version), trust,
                                           QTableWidgetItem(item.author), QTableWidgetItem(status))):
                self.table.setItem(row, column, cell)
            btn = QPushButton()
            if item.revoked:
                btn.setText("Revoked")
                btn.setEnabled(False)
            elif item.kind == "recipe" and item.path is not None:
                btn.setText("Build")
                btn.setToolTip("Fill the Builder with this recipe")
                btn.clicked.connect(lambda checked=False, it=item: self.build_recipe(it))
            elif item.installed_version:
                btn.setText("Installed")
                btn.setEnabled(False)
            else:
                btn.setText("Install")
                btn.clicked.connect(lambda checked=False, it=item: self.install_item(it))
            btn.setAccessibleName(f"{btn.text()} {item.kind} {item.id}")
            self.table.setCellWidget(row, 6, btn)

    def install_item(self, item):
        self.status_label.setText(f"Downloading and checking {item.kind} {item.id}...")
        self._run(self.comm_manager.prepare_install, self.confirm_install, item.kind, item.id)

    def import_file(self):
        # A pack encrypted to your age key opens here; a passphrase pack: llm-cli zoo install --file ... --passphrase
        path, _ = QFileDialog.getOpenFileName(self, "Import community pack", "",
                                              "Community packs (*.zip *.lcfpack);;All files (*)")
        if path:
            self.status_label.setText(f"Checking {Path(path).name}...")
            self._run(self.comm_manager.prepare_install_file, self.confirm_install, Path(path))

    def confirm_install(self, prepared):
        from orchestrator.Core.community_manager import TrustLevel
        local = prepared.trust == TrustLevel.LOCAL
        box = QMessageBox(QMessageBox.Warning if local else QMessageBox.Question, "Install community pack",
                          prepared.summary(), QMessageBox.Yes | QMessageBox.No, self)
        # The summary quotes the pack (author, notes): as HTML it could hide the trust level and the warning.
        box.setTextFormat(Qt.PlainText)
        box.setDefaultButton(QMessageBox.No)  # deny by default
        if box.exec() != QMessageBox.Yes:
            self.comm_manager.discard(prepared)
            self.status_label.setText("Not installed.")
            return
        try:
            dest = self.comm_manager.finish_install(prepared)
        except Exception as e:
            self.on_error(str(e))
            return
        tm = getattr(self.framework_manager, "target_manager", None)
        if prepared.manifest.kind == "target" and tm is not None:
            tm.initialize()
        self.status_label.setText(f"Installed {prepared.manifest.kind} {prepared.manifest.id} in {dest}.")
        self.installed.emit(prepared.manifest.kind)
        self.refresh_list()

    def build_recipe(self, item):
        from orchestrator.schemas.recipes import Recipe
        try:
            recipe = Recipe.model_validate(yaml.safe_load((item.path / "recipe.yml").read_text(encoding="utf-8")))
        except Exception as e:
            self.on_error(f"The recipe {item.id} cannot be read: {e}")
            return
        if self.on_build_recipe is None:
            self.on_error("The Builder is not available.")
            return
        self.on_build_recipe(recipe)
        self.status_label.setText(f"Builder filled from recipe {recipe.id}; check the settings and start the build.")


class CommunityExportDialog(QDialog):
    """Export for community: a scrubbed pack signed with your key, a summary of what the privacy scrub changed,
    then (only when you click it) a pull request through the GitHub CLI or the browser."""

    def __init__(self, framework_manager, kind: str, source: Optional[str], parent=None):
        super().__init__(parent)
        self.framework_manager, self.kind, self.source = framework_manager, kind, source
        self.comm_manager = getattr(framework_manager, "community_manager", None)
        self.result = None
        self._task = None
        self.setWindowTitle(f"Export {kind} for the community")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        note = QLabel("The pack is built on this computer: home folders, addresses, host and user names and keys "
                      "are removed, and it is signed with your key. Nothing is sent until you open a pull request.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("Name shown as the author (a pseudonym is fine)")
        form.addRow("Author:", self.author_edit)
        self.license_combo = QComboBox()
        self.license_combo.setEditable(True)
        self.license_combo.addItems(["MIT", "Apache-2.0", "CC-BY-4.0", "CC0-1.0"])
        form.addRow("Licence:", self.license_combo)
        self.model_license_edit = QLineEdit("unknown")
        if kind == "recipe":
            form.addRow("Model licence:", self.model_license_edit)
        layout.addLayout(form)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("font-family: Consolas, Monospace;")
        layout.addWidget(self.output, 1)
        buttons = QHBoxLayout()
        self.create_btn = QPushButton("Create pack")
        self.create_btn.clicked.connect(self.create_pack)
        self.pr_btn = QPushButton("Open pull request")
        self.pr_btn.setEnabled(False)
        self.pr_btn.clicked.connect(self.submit)
        self.folder_btn = QPushButton("Show folder")
        self.folder_btn.setEnabled(False)
        self.folder_btn.clicked.connect(self.show_folder)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        for b in (self.create_btn, self.pr_btn, self.folder_btn):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def create_pack(self):
        author = self.author_edit.text().strip()
        if not author:
            QMessageBox.warning(self, "Export", "Enter an author name (a pseudonym is fine).")
            return
        if self.comm_manager is None:
            self.output.setPlainText("Error: Community Manager missing.")
            return
        try:
            self.result = self.comm_manager.export_pack(
                self.kind, self.source, author=author, license=self.license_combo.currentText().strip() or "MIT",
                model_license=self.model_license_edit.text().strip() or "unknown")
        except Exception as e:
            self.output.setPlainText(f"The pack was not created:\n{e}")
            return
        self.output.setPlainText(self.result.summary() + "\n\nReview the changes above before you share the pack.")
        self.pr_btn.setEnabled(True)
        self.folder_btn.setEnabled(True)

    def submit(self):
        from orchestrator.Core.community_manager import submit_contribution
        config = getattr(self.framework_manager, "config", None)
        repo = config.get("community_repo", "") if config is not None else ""
        self.pr_btn.setEnabled(False)
        self.output.appendPlainText("\nHanding the pack to a pull request...")
        self._task = _Task(submit_contribution, self.result.path, repo, self.kind)
        self._task.done.connect(self._submitted)
        self._task.failed.connect(self._not_submitted)
        self._task.start()

    def _submitted(self, message: str):
        self.output.appendPlainText(message)
        self.pr_btn.setEnabled(True)

    def _not_submitted(self, error: str):
        self.output.appendPlainText(f"Not submitted: {error}")
        self.pr_btn.setEnabled(True)

    def done(self, result):
        if self._task is not None:
            self._task.wait(30000)
        super().done(result)

    def show_folder(self):
        if self.result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result.path.parent)))
