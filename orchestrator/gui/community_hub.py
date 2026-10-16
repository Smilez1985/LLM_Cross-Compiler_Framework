#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Community Hub

Browses the community zoo (the signed index plus installed packs) with a trust column and filters, installs a
pack after showing its summary, and "Build" fills the Builder from an installed recipe. CommunityExportDialog
makes a scrubbed pack signed with your key and hands it to a pull request. Nothing is uploaded from here and no
repository token is ever asked for.

Every `llm-cli zoo` action has its mouse path here (DECISIONS W1'), each calling the same core function as the CLI:
Details... (zoo show: CommunityManager.details), Import file... with a passphrase or the key files of the only senders
to accept (zoo install --file --passphrase / --from: prepare_install_file), Check installed packs (zoo verify:
verify_installed), Check a pack file... (zoo verify FILE: verify_file), Export... (zoo export target|recipe|knowledge
with --id, --to, --passphrase, --out: export_pack) and My public keys... (zoo keys: UserKeys.export_public). Downloads,
checks, exports and the keys run off the GUI thread; passphrases are never stored or shown.
"""

from pathlib import Path
from typing import Callable, Optional

import yaml
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtGui import Qt as QtText
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from orchestrator.gui.plain import button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.utils.localization import current_language, tr, ui_text

PACK_FILTER = "Community packs (*.zip *.lcfpack);;All files (*)"

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
        self.lang = current_language()
        self.all_items = []
        self.shown_items = []   # the table's rows, in order
        self._details_for = ""
        self._install_note = ""   # what the last install did; kept in the status line after the list is read again
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
        self.import_btn.setToolTip(plain_tooltip("Install a pack file someone gave you (trust: local unless the signed "
                                                 "index lists it), or an encrypted pack sent to your key or protected "
                                                 "with a passphrase."))
        self.import_btn.clicked.connect(self.import_file)
        hbox.addWidget(self.import_btn)
        layout.addLayout(hbox)
        # The object names CLI_PATHS uses (orchestrator/gui/menus/knowledge.py).
        for widget, name in ((self.kind_filter, "cmb_zoo_kind"), (self.trust_filter, "cmb_zoo_trust"),
                             (self.refresh_btn, "btn_zoo_refresh"), (self.import_btn, "btn_zoo_import")):
            widget.setObjectName(name)

        actions = QHBoxLayout()
        self.details_btn = self._action_button("zoo.details", self.show_details, "btn_zoo_details")
        self.verify_btn = self._action_button("zoo.verify_installed", self.verify_installed,
                                              "btn_zoo_verify_installed")
        self.verify_file_btn = self._action_button("zoo.verify_file", self.verify_file, "btn_zoo_verify_file")
        self.export_btn = self._action_button("zoo.export", self.export_pack, "btn_zoo_export")
        self.keys_btn = self._action_button("zoo.keys", self.show_keys, "btn_zoo_keys")
        for button in (self.details_btn, self.verify_btn, self.verify_file_btn, self.export_btn):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.keys_btn)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("tbl_zoo_packs")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.show_details())
        self.table.setHorizontalHeaderLabels(["Name", "Kind", "Version", "Trust", "Author", "Status", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setStyleSheet("QTableWidget { background-color: #1e1e1e; color: #eee; gridline-color: #333; } "
                                 "QHeaderView::section { background-color: #333; color: white; }")
        layout.addWidget(self.table)
        self.status_label = QLabel(tr("status.ready", "Ready."))
        self.status_label.setTextFormat(Qt.PlainText)  # errors quote pack contents
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _action_button(self, key: str, slot, name: str) -> QPushButton:
        button = QPushButton(button_text(ui_text(key, self.lang)))
        button.setObjectName(name)
        button.setToolTip(plain_tooltip(ui_text(f"{key}.tip", self.lang)))
        button.clicked.connect(lambda _checked=False: slot())
        return button

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
        self.status_label.setText("\n".join(t for t in (self._install_note, note) if t))
        self._install_note = ""

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
        self.shown_items = list(items)
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(item.title)
            # The description comes from the index or the pack: plain text, never rendered as HTML.
            name.setToolTip(QtText.convertFromPlainText(item.description or item.id, Qt.WhiteSpaceNormal))
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

    def selected_item(self):
        row = self.table.currentRow()
        return self.shown_items[row] if 0 <= row < len(self.shown_items) else None

    def show_details(self):
        """Details... (or a double click on a row): CommunityManager.details for the selected pack, the facts of
        `llm-cli zoo show ID --kind KIND`, in a worker thread (it reads the cached index and the installed folder)."""
        item = self.selected_item()
        if item is None:
            self.status_label.setText(ui_text("zoo.choose_first", self.lang))
            return
        if not self.comm_manager:
            self.on_error("Community Manager missing.")
            return
        self._details_for = item.id
        self.status_label.setText(ui_text("zoo.details.loading", self.lang, kind=item.kind, id=item.id))
        self._run(self.comm_manager.details, self._show_details, item.id, item.kind)

    def _show_details(self, found):
        if not found:
            self.status_label.setText(ui_text("zoo.details.none", self.lang, id=self._details_for))
            return
        from orchestrator.gui.community_dialogs import PackDetailsDialog
        self.status_label.setText(tr("status.ready", "Ready."))
        PackDetailsDialog(found, self, self.lang).exec()

    def import_file(self):
        """Import file...: the pack file, its passphrase (a password field; never stored or shown) and the key files of
        the only senders to accept, then prepare_install_file in a worker thread and the summary with "No" as the
        default (`llm-cli zoo install --file FILE [--passphrase] [--from KEYFILE...]`)."""
        from orchestrator.gui.community_dialogs import ImportPackDialog, key_file_problem
        if not self.comm_manager:
            self.on_error("Community Manager missing.")
            return
        dialog = ImportPackDialog(self, self.lang)
        if not dialog.exec():
            return
        path, passphrase, key_files = dialog.take()
        senders = None
        if key_files:   # the CLI's --from: Ed25519 keys of the senders, read the same way (read_public_key_files)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from orchestrator.Core.community_pack import KeyFileError, PackError, read_public_key_files
            try:
                senders = read_public_key_files(key_files, Ed25519PublicKey)
            except KeyFileError as e:
                self.on_error(key_file_problem(e, self.lang))
                return
            except (PackError, OSError) as e:
                self.on_error(str(e))
                return
        self.status_label.setText(ui_text("zoo.import.checking", self.lang, name=path.name))
        self._run(self._prepare_file, self.confirm_install, path, [passphrase] if passphrase else [], senders)

    def _prepare_file(self, path: Path, secret: list, senders):
        """In the worker: prepare_install_file with the passphrase, which is dropped right after the call."""
        passphrase = secret.pop() if secret else None
        return self.comm_manager.prepare_install_file(path, passphrase=passphrase, trusted_senders=senders)

    def verify_installed(self):
        """Check installed packs: CommunityManager.verify_installed (`llm-cli zoo verify`) in a worker thread; every
        installed pack with its problems (revoked, changed since it was installed, licence files). Nothing changes."""
        if not self.comm_manager:
            self.on_error("Community Manager missing.")
            return
        self.status_label.setText(ui_text("zoo.verify.checking", self.lang))
        self._run(self.comm_manager.verify_installed, self._show_verified)

    def _show_verified(self, results):
        from orchestrator.gui.community_dialogs import VerifyResultsDialog
        bad = sum(1 for *_, problems in results if problems)
        self.status_label.setText(ui_text("zoo.verify.summary", self.lang, n=len(results), bad=bad) if results
                                  else ui_text("zoo.verify.none", self.lang))
        VerifyResultsDialog(results, self, self.lang).exec()

    def verify_file(self):
        """Check a pack file...: CommunityManager.verify_file (`llm-cli zoo verify FILE`: format, hashes, validators,
        privacy scrub) before installing or sharing it, in a worker thread. Nothing is installed."""
        if not self.comm_manager:
            self.on_error("Community Manager missing.")
            return
        path, _ = QFileDialog.getOpenFileName(self, ui_text("zoo.verify_file.choose", self.lang), "", PACK_FILTER)
        if not path:
            return
        self.status_label.setText(ui_text("zoo.verify_file.checking", self.lang, name=Path(path).name))
        self._run(self._verify_file, self._show_file_verified, Path(path))

    def _verify_file(self, path: Path):
        return self.comm_manager.verify_file(path)

    def _show_file_verified(self, result):
        manifest, problems = result
        values = dict(kind=manifest.kind, id=one_line(manifest.id), version=manifest.version)
        if problems:
            text = ui_text("zoo.verify_file.bad", self.lang, problems="\n".join(f"- {one_line(p)}" for p in problems),
                           **values)
        else:
            text = ui_text("zoo.verify_file.ok", self.lang, **values)
        self.status_label.setText(text.splitlines()[0])
        plain_box(self, "warning" if problems else "information", ui_text("zoo.verify_file.choose", self.lang), text)

    def export_pack(self):
        """Export...: what to export (a target folder, a finished build's build_info.json or the knowledge base), then
        CommunityExportDialog (author, licence, pack id, encryption, folder): `llm-cli zoo export KIND SOURCE`."""
        from orchestrator.gui.community_dialogs import ExportSourceDialog
        if not self.comm_manager:
            self.on_error("Community Manager missing.")
            return
        dialog = ExportSourceDialog(self.comm_manager.exportable_targets(), self._build_start(), self, self.lang)
        if not dialog.exec():
            return
        kind, source = dialog.values()
        CommunityExportDialog(self.framework_manager, kind, source, self).exec()

    def _build_start(self) -> str:
        """Where the build_info.json chooser opens: the output folder, as the Builder's export does."""
        config = getattr(self.framework_manager, "config", None)
        start = config.get("output_dir", "output") if config is not None and hasattr(config, "get") else ""
        resolve = getattr(self.framework_manager, "resolve_path", None)
        return str(resolve(start)) if callable(resolve) and start else str(start or "")

    def show_keys(self):
        """My public keys...: UserKeys.export_public (`llm-cli zoo keys`) in a worker thread (the secrets store may
        take a moment), then the keys with Copy and Save... The private keys are never shown."""
        self.status_label.setText(ui_text("zoo.keys.loading", self.lang))
        self._run(self._public_keys, self._show_keys)

    def _public_keys(self) -> str:
        from orchestrator.Core.community_pack import UserKeys
        return UserKeys(getattr(self.framework_manager, "secrets_manager", None)).export_public()

    def _show_keys(self, text: str):
        from orchestrator.gui.community_dialogs import PublicKeysDialog
        self.status_label.setText(tr("status.ready", "Ready."))
        PublicKeysDialog(text, self, self.lang).exec()

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
        # In the background like the download and the checks: a knowledge pack is imported into the knowledge base
        # here (a snapshot first, then every page), which takes minutes. A failure arrives in on_error.
        self.status_label.setText(ui_text("zoo.install.installing", self.lang, kind=prepared.manifest.kind,
                                          id=one_line(prepared.manifest.id)))
        self._run(self._finish_install, self._installed, prepared)

    def _finish_install(self, prepared):
        """In the worker: CommunityManager.finish_install (`llm-cli zoo install` after the person's "Yes")."""
        return prepared, self.comm_manager.finish_install(prepared)

    def _installed(self, result):
        prepared, dest = result
        tm = getattr(self.framework_manager, "target_manager", None)
        if prepared.manifest.kind == "target" and tm is not None:
            tm.initialize()
        problem = getattr(self.comm_manager, "last_sync_problem", "") if prepared.manifest.kind == "knowledge" else ""
        lines = [f"Installed {prepared.manifest.kind} {prepared.manifest.id} in {dest}."]
        snapshot = getattr(self.comm_manager, "last_sync_snapshot", None) if prepared.manifest.kind == "knowledge" \
            else None
        if snapshot and not problem:   # N13, as `llm-cli zoo install` says it: saved first, so it can be undone
            lines.append(ui_text("zoo.install.snapshot", self.lang, name=one_line(snapshot)))
        self._install_note = "\n".join(lines)
        self.status_label.setText(self._install_note)
        if problem:  # installed, but not imported: the snapshot taken first did not fit (N13)
            from orchestrator.gui.knowledge_snapshots_window import offer_snapshot_list
            offer_snapshot_list(self, self.framework_manager, problem)
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
    then (only when you click it) a pull request through the GitHub CLI or the browser. The same export as `llm-cli zoo
    export KIND SOURCE` (CommunityManager.export_pack): a knowledge pack takes its pack id (--id); private sharing
    encrypts the pack to the age keys in key files (--to) or with a passphrase (--passphrase, typed twice, never stored
    or shown); the folder can be chosen (--out). An encrypted pack is for private sharing and is never submitted."""

    ENCRYPT = ("none", "keys", "passphrase")

    def __init__(self, framework_manager, kind: str, source: Optional[str], parent=None):
        super().__init__(parent)
        self.framework_manager, self.kind, self.source = framework_manager, kind, source
        self.comm_manager = getattr(framework_manager, "community_manager", None)
        self.lang = current_language()
        self.result = None
        self._task = None
        self._export_task = None
        self.recipient_files: list = []
        self.out_dir: Optional[Path] = getattr(self.comm_manager, "contributions_dir", None)
        self.setObjectName("dlg_zoo_export")
        self.setWindowTitle(f"Export {kind} for the community")
        self.resize(780, 600)
        layout = QVBoxLayout(self)
        note = QLabel("The pack is built on this computer: home folders, addresses, host and user names and keys "
                      "are removed, and it is signed with your key. Nothing is sent until you open a pull request.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.author_edit = QLineEdit()
        self.author_edit.setObjectName("edt_zoo_export_author")
        self.author_edit.setPlaceholderText("Name shown as the author (a pseudonym is fine)")
        form.addRow("Author:", self.author_edit)
        self.license_combo = QComboBox()
        self.license_combo.setObjectName("cmb_zoo_export_license")
        self.license_combo.setEditable(True)
        # Knowledge holds other people's pages: NOASSERTION, as `llm-cli zoo export knowledge` defaults to.
        self.license_combo.addItems((["NOASSERTION"] if kind == "knowledge" else [])
                                    + ["MIT", "Apache-2.0", "CC-BY-4.0", "CC0-1.0"])
        form.addRow("Licence:", self.license_combo)
        self.model_license_edit = QLineEdit("unknown")
        if kind == "recipe":
            form.addRow("Model licence:", self.model_license_edit)
        self.id_edit = QLineEdit()
        self.id_edit.setObjectName("edt_zoo_export_id")
        self.id_edit.setPlaceholderText(self._t("zoo.export.id.hint"))
        if kind == "knowledge":
            form.addRow(plain_label(self._t("zoo.export.id"), wrap=False), self.id_edit)
        self.encrypt_combo = QComboBox()
        self.encrypt_combo.setObjectName("cmb_zoo_export_encrypt")
        for mode in self.ENCRYPT:
            self.encrypt_combo.addItem(self._t(f"zoo.export.encrypt.{mode}"), mode)
        self.encrypt_combo.currentIndexChanged.connect(lambda _index: self._encryption_changed())
        form.addRow(plain_label(self._t("zoo.export.encrypt"), wrap=False), self.encrypt_combo)
        keys_row = QHBoxLayout()
        self.recipients_label = plain_label(self._t("zoo.export.recipients.none"))
        keys_row.addWidget(self.recipients_label, 1)
        self.recipients_btn = QPushButton(button_text(self._t("zoo.export.recipients.choose")))
        self.recipients_btn.setObjectName("btn_zoo_export_recipients")
        self.recipients_btn.clicked.connect(self.choose_recipients)
        keys_row.addWidget(self.recipients_btn)
        form.addRow(plain_label(self._t("zoo.export.recipients"), wrap=False), keys_row)
        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setObjectName("edt_zoo_export_passphrase")
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        form.addRow(plain_label(self._t("zoo.export.passphrase"), wrap=False), self.passphrase_edit)
        self.passphrase2_edit = QLineEdit()
        self.passphrase2_edit.setObjectName("edt_zoo_export_passphrase2")
        self.passphrase2_edit.setEchoMode(QLineEdit.Password)
        form.addRow(plain_label(self._t("zoo.export.passphrase2"), wrap=False), self.passphrase2_edit)
        folder_row = QHBoxLayout()
        self.folder_label = plain_label(str(self.out_dir or ""))
        folder_row.addWidget(self.folder_label, 1)
        self.choose_folder_btn = QPushButton(button_text(self._t("zoo.export.folder.choose")))
        self.choose_folder_btn.setObjectName("btn_zoo_export_folder")
        self.choose_folder_btn.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.choose_folder_btn)
        form.addRow(plain_label(self._t("zoo.export.folder"), wrap=False), folder_row)
        layout.addLayout(form)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("font-family: Consolas, Monospace;")
        layout.addWidget(self.output, 1)
        buttons = QHBoxLayout()
        self.create_btn = QPushButton("Create pack")
        self.create_btn.setObjectName("btn_zoo_export_create")
        self.create_btn.clicked.connect(self.create_pack)
        self.pr_btn = QPushButton("Open pull request")
        self.pr_btn.setObjectName("btn_zoo_export_pr")
        self.pr_btn.setEnabled(False)
        self.pr_btn.clicked.connect(self.submit)
        self.folder_btn = QPushButton("Show folder")
        self.folder_btn.setObjectName("btn_zoo_export_show_folder")
        self.folder_btn.setEnabled(False)
        self.folder_btn.clicked.connect(self.show_folder)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        for b in (self.create_btn, self.pr_btn, self.folder_btn):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self._encryption_changed()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _encryption_changed(self) -> None:
        mode = self.encrypt_combo.currentData()
        self.recipients_btn.setEnabled(mode == "keys")
        for edit in (self.passphrase_edit, self.passphrase2_edit):
            edit.setEnabled(mode == "passphrase")
            if mode != "passphrase":
                edit.clear()

    def choose_recipients(self) -> None:
        from orchestrator.gui.community_dialogs import KEY_FILE_FILTER
        files, _ = QFileDialog.getOpenFileNames(self, self._t("zoo.keyfile.choose"), "", KEY_FILE_FILTER)
        if files:
            self.recipient_files = list(files)
            self.recipients_label.setText("\n".join(self.recipient_files))

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._t("zoo.export.folder.title"), str(self.out_dir or ""))
        if folder:
            self.out_dir = Path(folder)
            self.folder_label.setText(folder)

    def _warn(self, text: str) -> None:
        plain_box(self, "warning", "Export", text)

    def create_pack(self):
        """CommunityManager.export_pack with the same arguments as `llm-cli zoo export`, in a worker thread (a
        knowledge pack reads the whole knowledge base); the passphrase leaves the fields at once and is dropped right
        after the call."""
        author = self.author_edit.text().strip()
        if not author:
            self._warn("Enter an author name (a pseudonym is fine).")
            return
        if self.comm_manager is None:
            self.output.setPlainText("Error: Community Manager missing.")
            return
        mode = self.encrypt_combo.currentData()
        recipients, secret = [], []
        if mode == "keys":   # the CLI's --to: the age recipients in the key files (read_public_key_files)
            if not self.recipient_files:
                self._warn(self._t("zoo.export.no_recipients"))
                return
            from pyrage.x25519 import Recipient

            from orchestrator.Core.community_pack import KeyFileError, PackError, read_public_key_files
            from orchestrator.gui.community_dialogs import key_file_problem
            try:
                recipients = read_public_key_files(self.recipient_files, Recipient)
            except KeyFileError as e:
                self._warn(key_file_problem(e, self.lang))
                return
            except (PackError, OSError) as e:
                self._warn(str(e))
                return
        elif mode == "passphrase":   # the CLI's --passphrase, asked twice
            first, second = self.passphrase_edit.text(), self.passphrase2_edit.text()
            self.passphrase_edit.clear()
            self.passphrase2_edit.clear()
            if not first:
                self._warn(self._t("zoo.export.passphrase_empty"))
                return
            if first != second:
                self._warn(self._t("zoo.export.passphrase_differs"))
                return
            secret = [first]
        arguments = dict(author=author, license=self.license_combo.currentText().strip() or None,
                         pack_id=(self.id_edit.text().strip() or None) if self.kind == "knowledge" else None,
                         model_license=self.model_license_edit.text().strip() or "unknown", recipients=recipients,
                         out_dir=self.out_dir)
        self.create_btn.setEnabled(False)
        self.pr_btn.setEnabled(False)
        self.output.setPlainText(self._t("zoo.export.creating"))
        self._export_task = _Task(self._export, arguments, secret)
        self._export_task.done.connect(self._created)
        self._export_task.failed.connect(self._not_created)
        self._export_task.start()

    def _export(self, arguments: dict, secret: list):
        passphrase = secret.pop() if secret else None
        return self.comm_manager.export_pack(self.kind, self.source, passphrase=passphrase, **arguments)

    def _created(self, result):
        self.result = result
        text = result.summary() + "\n\nReview the changes above before you share the pack."
        if result.encrypted:
            text += "\n" + self._t("zoo.export.encrypted_no_pr")
        self.output.setPlainText(text)
        self.create_btn.setEnabled(True)
        self.pr_btn.setEnabled(not result.encrypted)   # encrypted packs are for private sharing only
        self.folder_btn.setEnabled(True)

    def _not_created(self, error: str):
        self.output.setPlainText(f"The pack was not created:\n{error}")
        self.create_btn.setEnabled(True)

    def submit(self):
        """Open pull request: after the summary, the question of `llm-cli zoo export --pr` ("No" is the default):
        publishing is a release of its own. Then submit_contribution in a worker thread."""
        from orchestrator.Core.community_manager import submit_contribution
        from orchestrator.gui.plain import ask
        if self.result is None or self.result.encrypted:
            self.output.appendPlainText("\n" + self._t("zoo.export.encrypted_no_pr"))
            return
        if not ask(self, self._t("zoo.export.submit.title"), self._t("zoo.export.submit.ask")):
            self.output.appendPlainText("\n" + self._t("zoo.export.submit.no", path=str(self.result.path)))
            return
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
        for edit in (self.passphrase_edit, self.passphrase2_edit):   # a typed passphrase never stays in the window
            edit.clear()
        for task in (self._task, self._export_task):
            if task is not None:
                task.wait(30000)
        super().done(result)

    def show_folder(self):
        if self.result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result.path.parent)))
