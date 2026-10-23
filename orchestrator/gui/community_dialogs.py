#!/usr/bin/env python3
"""
The Community Hub's smaller windows (DECISIONS W1': every `llmcf zoo` action also by mouse). Each shows or collects
what one CLI command shows or takes; the Community Hub calls the same core function as the CLI with it:

- PackDetailsDialog (`llm-cli zoo show`): licence, trust and revocation, installed version and folder, download address,
  size and sha256, description, and an installed recipe's recipe.yml (CommunityManager.details).
- ImportPackDialog (`llm-cli zoo install --file FILE [--passphrase] [--from KEYFILE...]`): the pack file, its passphrase
  (password field, never stored or shown) and the key files of the only senders to accept; the same refusals as the
  CLI before anything is read (a passphrase pack without its passphrase, senders for a plain pack).
- VerifyResultsDialog (`llm-cli zoo verify`): every installed pack with its problems
  (CommunityManager.verify_installed).
- PublicKeysDialog (`llm-cli zoo keys`): the person's public keys with Copy and Save... (UserKeys.export_public).
- ExportSourceDialog (`llm-cli zoo export KIND SOURCE`): what to export: a target folder, a finished build's
  build_info.json, or the knowledge base; CommunityExportDialog then takes the author, licence, pack id, encryption
  and folder.

Every text is plain text in the person's language (l10n_knowledge); names, addresses and notes from packs and the index
are data and never rendered as markup.
"""

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, display_text, one_line, plain_label
from orchestrator.utils.localization import current_language, ui_text

KEY_FILE_FILTER = "Key files (*.keys *.txt *.pub);;All files (*)"


def _mono(widget: QPlainTextEdit) -> QPlainTextEdit:
    widget.setReadOnly(True)
    widget.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
    return widget


class _Dialog(QDialog):
    def __init__(self, parent: Optional[QWidget], lang: Optional[str], title_key: str):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.setWindowTitle(self._t(title_key))

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, slot, name: str) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button


# --- zoo show ---------------------------------------------------------------------------------------------------------

def detail_fields(details: Any, lang: str) -> List[Tuple[str, str]]:
    """The fields of one zoo entry (CommunityManager.details) as (label, value) in the person's language: the same
    facts as `llm-cli zoo show` (ZooDetails.lines)."""
    i = details.item
    t = lambda key, **v: ui_text(key, lang, **v)  # noqa: E731
    trust = t("zoo.value.revoked", trust=i.trust.value, reason=i.revoked) if i.revoked else i.trust.value
    fields = [(t("zoo.field.pack"), t("zoo.value.pack", kind=i.kind, id=i.id, version=i.version, author=i.author)),
              (t("zoo.field.licence"), i.license or t("zoo.value.unknown_licence")),
              (t("zoo.field.trust"), trust),
              (t("zoo.field.installed"), i.installed_version or t("zoo.value.not_installed"))]
    if i.path:
        fields.append((t("zoo.field.folder"), str(i.path)))
    if i.entry:
        fields += [(t("zoo.field.download"), str(i.entry.url)),
                   (t("zoo.field.size"), t("zoo.value.bytes", size=i.entry.size)),
                   (t("zoo.field.sha256"), str(i.entry.sha256))]
    if i.description:
        fields.append((t("zoo.field.description"), i.description))
    return fields


class PackDetailsDialog(_Dialog):
    """The details of every entry with the chosen id and kind (usually one)."""

    def __init__(self, found: Sequence[Any], parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent, lang, "zoo.details.title")
        self.setObjectName("dlg_zoo_details")
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget(scroll)
        inner = QVBoxLayout(body)
        self.fields: List[List[Tuple[str, str]]] = []
        self.recipes: List[QPlainTextEdit] = []
        for details in found:
            box = QGroupBox(one_line(details.item.title), body)
            form = QFormLayout(box)
            fields = detail_fields(details, self.lang)
            self.fields.append(fields)
            for label, value in fields:
                form.addRow(plain_label(label, wrap=False), plain_label(display_text(value)))
            if details.recipe_text is not None:
                recipe = _mono(QPlainTextEdit(box))
                recipe.setPlainText(details.recipe_text)
                form.addRow(plain_label(self._t("zoo.field.recipe"), wrap=False), recipe)
                self.recipes.append(recipe)
            inner.addWidget(box)
        inner.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# --- zoo install --file -----------------------------------------------------------------------------------------------

class ImportPackDialog(_Dialog):
    """A pack file to install, with its passphrase and the senders to accept (the same refusals as the CLI)."""

    def __init__(self, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent, lang, "zoo.import.title")
        self.setObjectName("dlg_zoo_import")
        self.resize(720, 300)
        self.key_files: List[str] = []
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("zoo.import.intro")))
        form = QFormLayout()
        row = QHBoxLayout()
        self.file_edit = QLineEdit(self)
        self.file_edit.setObjectName("edt_zoo_import_file")
        row.addWidget(self.file_edit, 1)
        row.addWidget(self._button("zoo.import.browse", self._browse, "btn_zoo_import_browse"))
        form.addRow(plain_label(self._t("zoo.import.file"), wrap=False), row)
        self.passphrase = QLineEdit(self)
        self.passphrase.setObjectName("edt_zoo_import_passphrase")
        self.passphrase.setEchoMode(QLineEdit.Password)
        self.passphrase.setPlaceholderText(self._t("zoo.import.passphrase.hint"))
        form.addRow(plain_label(self._t("zoo.import.passphrase"), wrap=False), self.passphrase)
        senders = QHBoxLayout()
        self.senders_label = plain_label(self._t("zoo.import.senders.none"))
        senders.addWidget(self.senders_label, 1)
        senders.addWidget(self._button("zoo.import.senders.choose", self._choose_senders, "btn_zoo_import_senders"))
        senders.addWidget(self._button("zoo.import.senders.clear", self._clear_senders, "btn_zoo_import_clear"))
        form.addRow(plain_label(self._t("zoo.import.senders"), wrap=False), senders)
        layout.addLayout(form)
        self.problem = plain_label("")
        layout.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.btn_ok = buttons.addButton(button_text(self._t("zoo.import.ok")), QDialogButtonBox.AcceptRole)
        self.btn_ok.setObjectName("btn_zoo_import_ok")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._t("zoo.import.title"), "",
                                              "Community packs (*.zip *.lcfpack);;All files (*)")
        if path:
            self.file_edit.setText(path)

    def _choose_senders(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, self._t("zoo.keyfile.choose"), "", KEY_FILE_FILTER)
        if files:
            self.key_files = list(files)
            self.senders_label.setText("\n".join(self.key_files))

    def _clear_senders(self) -> None:
        self.key_files = []
        self.senders_label.setText(self._t("zoo.import.senders.none"))

    def _check(self) -> None:
        """Accepts only what the CLI would start with: an existing file; senders only for an encrypted pack; a
        passphrase for a pack protected with one."""
        from orchestrator.Core.community_pack import is_encrypted_pack, needs_passphrase
        path = Path(self.file_edit.text().strip()) if self.file_edit.text().strip() else None
        if path is None or not path.is_file():
            self.problem.setText(self._t("zoo.import.no_file"))
            return
        if self.key_files and not is_encrypted_pack(path):
            self.problem.setText(self._t("zoo.import.not_encrypted"))
            return
        if not self.passphrase.text() and needs_passphrase(path):
            self.problem.setText(self._t("zoo.import.needs_passphrase"))
            self.passphrase.setFocus()
            return
        self.problem.setText("")
        self.accept()

    def take(self) -> Tuple[Path, Optional[str], List[str]]:
        """(pack file, passphrase or None, key files); the passphrase field is emptied (it is never kept here)."""
        passphrase = self.passphrase.text() or None
        self.passphrase.clear()
        return Path(self.file_edit.text().strip()), passphrase, list(self.key_files)

    def done(self, result: int) -> None:
        """Cancel or Close empties the passphrase field: the window lives on (hidden) with the Community Hub, and a
        typed passphrase never stays in it. After Import, take() reads and empties it."""
        if int(result) != QDialog.DialogCode.Accepted.value:
            self.passphrase.clear()
        super().done(result)


def key_file_problem(error: Any, lang: str) -> str:
    """A KeyFileError in the person's language."""
    if getattr(error, "code", "") == "not_text":
        return ui_text("zoo.keyfile.not_text", lang, file=one_line(error.file))
    return ui_text("zoo.keyfile.no_keys", lang)


# --- zoo verify -------------------------------------------------------------------------------------------------------

VERIFY_COLUMNS = ("zoo.verify.col.kind", "zoo.verify.col.id", "zoo.verify.col.version", "zoo.verify.col.folder",
                  "zoo.verify.col.result")


class VerifyResultsDialog(_Dialog):
    """Every installed pack with its trust and problems (CommunityManager.verify_installed); nothing is changed."""

    def __init__(self, results: Sequence[Any], parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent, lang, "zoo.verify.title")
        self.setObjectName("dlg_zoo_verify")
        self.resize(1000, 380)
        layout = QVBoxLayout(self)
        bad = sum(1 for *_, problems in results if problems)
        self.summary = plain_label(self._t("zoo.verify.summary", n=len(results), bad=bad) if results else
                                   self._t("zoo.verify.none"))
        layout.addWidget(self.summary)
        self.table = QTableWidget(len(results), len(VERIFY_COLUMNS), self)
        self.table.setObjectName("tbl_zoo_verify")
        self.table.setHorizontalHeaderLabels([self._t(c) for c in VERIFY_COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, (folder, prov, trust, problems) in enumerate(results):
            result = "; ".join(problems) if problems else (
                self._t("zoo.verify.ok", trust=trust.value) if trust else self._t("zoo.verify.ok_plain"))
            for column, value in enumerate((prov.kind, prov.id, prov.version, str(folder), result)):
                self.table.setItem(row, column, QTableWidgetItem(one_line(value)))
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# --- zoo keys ---------------------------------------------------------------------------------------------------------

class PublicKeysDialog(_Dialog):
    """The person's public keys (not secret) with Copy and Save...; the private keys are never shown. The Maintainer
    tab shows the public entry of a new index signing key with it too (intro, title_key, file_name, name)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None, lang: Optional[str] = None, *,
                 intro: Optional[str] = None, title_key: str = "zoo.keys.title",
                 file_name: str = "lcf-public-keys.keys", name: str = "dlg_zoo_keys"):
        super().__init__(parent, lang, title_key)
        self.setObjectName(name)
        self.resize(760, 300)
        self.text, self.title_key, self.file_name = text, title_key, file_name
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(intro if intro is not None else self._t("zoo.keys.intro")))
        self.view = _mono(QPlainTextEdit(self))
        self.view.setObjectName("txt_zoo_keys")
        self.view.setPlainText(text)
        layout.addWidget(self.view, 1)
        self.status = plain_label("")
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_copy = self._button("zoo.keys.copy", self.copy, "btn_zoo_keys_copy")
        self.btn_save = self._button("zoo.keys.save", self.save, "btn_zoo_keys_save")
        buttons.addButton(self.btn_copy, QDialogButtonBox.ActionRole)
        buttons.addButton(self.btn_save, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text)
        self.status.setText(self._t("zoo.keys.copied"))

    def save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self._t(self.title_key), self.file_name, KEY_FILE_FILTER)
        if not path:
            return
        try:
            Path(path).write_text(self.text, encoding="utf-8")
        except OSError as e:
            self.status.setText(self._t("zoo.keys.not_saved", reason=one_line(e.strerror or e)))
            return
        self.status.setText(self._t("zoo.keys.saved", path=one_line(path)))


# --- zoo export: what -------------------------------------------------------------------------------------------------

EXPORT_KINDS = ("target", "recipe", "knowledge")


class ExportSourceDialog(_Dialog):
    """What to export: a target folder (CommunityManager.exportable_targets), a finished build's build_info.json, or
    the knowledge base."""

    def __init__(self, targets: Sequence[str], build_start: str = "", parent: Optional[QWidget] = None,
                 lang: Optional[str] = None):
        super().__init__(parent, lang, "zoo.export.title")
        self.setObjectName("dlg_zoo_export_source")
        self.resize(720, 260)
        self.build_start = build_start
        self.build_info = ""
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox(self)
        self.kind.setObjectName("cmb_zoo_export_kind")
        for kind in EXPORT_KINDS:
            self.kind.addItem(self._t(f"zoo.export.kind.{kind}"), kind)
        form.addRow(plain_label(self._t("zoo.export.what"), wrap=False), self.kind)
        layout.addLayout(form)
        self.stack = QStackedWidget(self)
        target_page = QWidget(self.stack)
        target_form = QFormLayout(target_page)
        self.target = QComboBox(target_page)
        self.target.setObjectName("cmb_zoo_export_target")
        self.target.addItems(list(targets))
        target_form.addRow(plain_label(self._t("zoo.export.target"), wrap=False), self.target)
        if not targets:
            target_form.addRow(plain_label(self._t("zoo.export.no_targets")))
        build_page = QWidget(self.stack)
        build_form = QFormLayout(build_page)
        row = QHBoxLayout()
        self.build_label = plain_label(self._t("zoo.export.build.none"))
        row.addWidget(self.build_label, 1)
        row.addWidget(self._button("zoo.export.build.choose", self._choose_build, "btn_zoo_export_build"))
        build_form.addRow(plain_label(self._t("zoo.export.build"), wrap=False), row)
        knowledge_page = QWidget(self.stack)
        QVBoxLayout(knowledge_page).addWidget(plain_label(self._t("zoo.export.knowledge.note")))
        for page in (target_page, build_page, knowledge_page):
            self.stack.addWidget(page)
        self.kind.currentIndexChanged.connect(self.stack.setCurrentIndex)
        layout.addWidget(self.stack)
        self.problem = plain_label("")
        layout.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.btn_next = buttons.addButton(button_text(self._t("zoo.export.next")), QDialogButtonBox.AcceptRole)
        self.btn_next.setObjectName("btn_zoo_export_next")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_build(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._t("zoo.export.build.title"), self.build_start,
                                              "Build manifest (build_info.json)")
        if path:
            self.build_info = path
            self.build_label.setText(path)

    def _check(self) -> None:
        kind = self.kind.currentData()
        if kind == "target" and not self.target.currentText():
            self.problem.setText(self._t("zoo.export.choose_target"))
            return
        if kind == "recipe" and not self.build_info:
            self.problem.setText(self._t("zoo.export.choose_build"))
            return
        self.problem.setText("")
        self.accept()

    def values(self) -> Tuple[str, Optional[str]]:
        """(kind, source): a target id, a build_info.json path, or None for the knowledge base."""
        kind = str(self.kind.currentData())
        source = {"target": self.target.currentText(), "recipe": self.build_info}.get(kind)
        return kind, source or None


__all__ = ["EXPORT_KINDS", "ExportSourceDialog", "ImportPackDialog", "PackDetailsDialog", "PublicKeysDialog",
           "VERIFY_COLUMNS", "VerifyResultsDialog", "detail_fields", "key_file_problem"]
