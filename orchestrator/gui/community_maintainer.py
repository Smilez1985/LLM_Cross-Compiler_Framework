#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the Maintainer tab of the Community Hub

Tools > Open Community Hub > Maintainer: the maintainer's three actions as clicks (decision 2026-10-22), each calling
the same core as `llmcf zoo maintainer key|review|revoke` (orchestrator/Core/community_maintainer.py):

1. Create signing key... (once): the name, then create_signing_key in a worker thread; the public entry for
   configs/community_keys.yml is shown with Copy and Save... (a text file the person chooses).
2. Load contributions / Review selected / Publish...: the open pull requests that add packs and the merged packs not
   published yet; Review selected fetches the packs and checks them (zoo verify) and lists every file of each pack
   with a read-only text view; Publish... asks "Publish?" ("No" is the default) and runs Maintainer.publish.
3. Revoke a published pack...: the packs of the signed index on main, the scope (this file, this version, every
   version) and the reason, then "Revoke?" ("No" the default) and Maintainer.revoke.

Everything that reaches git, gh or the keyring runs off the GUI thread (_Task); every step is shown in the progress
list as it happens (through the worker relay), and Cancel stops before the next step up to the last merge. Texts in
English and German (utils/l10n_maintainer.py); pack contents and tool output are shown as plain text with hidden
characters escaped. Nothing here is reachable by an AI: no tool, no MCP server, no Ditto path calls it.
"""

import threading
from datetime import date
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.community_hub import _Task
from orchestrator.gui.plain import ask, button_text, display_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils import l10n_maintainer
from orchestrator.utils.localization import current_language

TRUSTS = ("community", "verified")


def _maintainer(framework: Any, progress: Callable) -> Any:
    """The core's Maintainer for the framework's community_repo (tests put a fake one here)."""
    from orchestrator.Core import community_maintainer as cmaint
    return cmaint.for_framework(framework, "gui", progress=progress)


def _localized(fn: Callable, lang: str) -> Callable:
    """`fn` for a worker thread: a refusal arrives in the person's language (reason, state and next step)."""
    def run(*args, **kwargs):
        from orchestrator.Core.community_maintainer import MaintainerError
        try:
            return fn(*args, **kwargs)
        except MaintainerError as e:
            raise RuntimeError(e.text(lang)) from None
    return run


class MaintainerPanel(QWidget):
    """The Maintainer tab: one action at a time, in a worker thread, with its steps in the progress list."""

    def __init__(self, framework_manager: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.framework_manager = framework_manager
        self.lang = lang or current_language()
        self.setObjectName("pnl_zoo_maintainer")
        self.items: list = []
        self.review = None
        self._files: list = []   # the files of the review, in the order of the file list
        self._tasks: set = set()
        self._cancel = threading.Event()
        self._token = object()
        worker_relay().done.connect(self._relayed, Qt.QueuedConnection)
        config = getattr(framework_manager, "config", None)
        repo = config.get("community_repo", "") if config is not None and hasattr(config, "get") else ""
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("zoo.maint.intro", repo=repo or "-")))

        key_row = QHBoxLayout()
        key_row.addWidget(plain_label(self._t("zoo.maint.key_name"), wrap=False))
        self.key_edit = QLineEdit(self)
        self.key_edit.setObjectName("edt_zoo_maint_key_name")
        self.key_edit.setPlaceholderText(self._t("zoo.maint.key_name.hint"))
        key_row.addWidget(self.key_edit, 1)
        self.key_btn = self._button("zoo.maint.key", self.create_key, "btn_zoo_maint_key")
        key_row.addWidget(self.key_btn)
        layout.addLayout(key_row)

        actions = QHBoxLayout()
        self.load_btn = self._button("zoo.maint.load", self.load_contributions, "btn_zoo_maint_load")
        self.review_btn = self._button("zoo.maint.review", self.review_selected, "btn_zoo_maint_review")
        self.revoke_btn = self._button("zoo.maint.revoke", self.revoke, "btn_zoo_maint_revoke")
        self.cancel_btn = self._button("zoo.maint.cancel", self.cancel, "btn_zoo_maint_cancel")
        self.cancel_btn.setEnabled(False)
        for button in (self.load_btn, self.review_btn, self.revoke_btn):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.cancel_btn)
        layout.addLayout(actions)

        split = QSplitter(Qt.Vertical, self)
        self.table = QTableWidget(0, 5, self)
        self.table.setObjectName("tbl_zoo_maint_contributions")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setHorizontalHeaderLabels([self._t(f"zoo.maint.col.{c}")
                                              for c in ("item", "title", "author", "packs", "check")])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.review_selected())
        split.addWidget(self.table)
        files = QSplitter(Qt.Horizontal, split)
        self.file_list = QListWidget(files)
        self.file_list.setObjectName("lst_zoo_maint_files")
        self.file_list.setAccessibleName(self._t("zoo.maint.files"))
        self.file_list.currentRowChanged.connect(self._show_file)
        self.file_view = QPlainTextEdit(files)
        self.file_view.setObjectName("txt_zoo_maint_file")
        self.file_view.setAccessibleName(self._t("zoo.maint.file"))
        self.file_view.setReadOnly(True)
        self.file_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.file_view.setStyleSheet("font-family: Consolas, Monospace;")
        files.setStretchFactor(1, 3)
        split.addWidget(files)
        layout.addWidget(split, 1)

        publish_row = QHBoxLayout()
        publish_row.addWidget(plain_label(self._t("zoo.maint.trust"), wrap=False))
        self.trust_combo = QComboBox(self)
        self.trust_combo.setObjectName("cmb_zoo_maint_trust")
        for trust in TRUSTS:
            self.trust_combo.addItem(trust, trust)
        publish_row.addWidget(self.trust_combo)
        publish_row.addWidget(plain_label(self._t("zoo.maint.title"), wrap=False))
        self.title_edit = QLineEdit(self)
        self.title_edit.setObjectName("edt_zoo_maint_title")
        self.title_edit.setPlaceholderText(self._t("zoo.maint.title.hint"))
        publish_row.addWidget(self.title_edit, 1)
        self.publish_btn = self._button("zoo.maint.publish", self.publish, "btn_zoo_maint_publish")
        self.publish_btn.setEnabled(False)
        publish_row.addWidget(self.publish_btn)
        layout.addLayout(publish_row)

        self.log = QPlainTextEdit(self)
        self.log.setObjectName("txt_zoo_maint_log")
        self.log.setAccessibleName(self._t("zoo.maint.log"))
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log)
        self._remembered_key()

    # -- helpers --------------------------------------------------------------------------------------------------
    def _t(self, text_key: str, /, **values: Any) -> str:
        return l10n_maintainer.text(text_key, self.lang, **values)

    def _button(self, key: str, slot: Callable, name: str) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setToolTip(plain_tooltip(self._t(f"{key}.tip")) if f"{key}.tip" in l10n_maintainer.TEXTS["en"]
                          else "")
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _say(self, text: str) -> None:
        self.log.appendPlainText(display_text(text))

    def _progress(self, code: str, values: dict) -> None:
        """In the worker thread: the step travels to the GUI thread through the relay (never a widget here)."""
        worker_relay().done.emit(self._token, (code, dict(values)))

    def _relayed(self, token: object, result: Any) -> None:
        if token is not self._token:
            return
        code, values = result
        self._say(self._t(f"zoo.maint.step.{code}", **values))

    def _busy(self) -> bool:
        self._tasks = {t for t in self._tasks if not t.isFinished()}
        if self._tasks:
            self._say(self._t("zoo.maint.busy"))
            return True
        return False

    def _start(self, fn: Callable, on_done: Callable, *args, cancellable: bool = False, **kwargs) -> Optional[_Task]:
        if self._busy():
            return None
        self._cancel.clear()
        for button in (self.key_btn, self.load_btn, self.review_btn, self.revoke_btn, self.publish_btn):
            button.setEnabled(False)
        self.cancel_btn.setEnabled(cancellable)
        task = _Task(_localized(fn, self.lang), *args, **kwargs)
        # The thread ends right after its signal: waited for first, so the next action is not refused as busy.
        task.done.connect(lambda result, t=task: (t.wait(5000), self._idle(), on_done(result)))
        task.failed.connect(lambda error, t=task: (t.wait(5000), self._idle(), self._failed(error)))
        self._tasks.add(task)
        task.start()
        return task

    def _idle(self) -> None:
        for button in (self.key_btn, self.load_btn, self.review_btn, self.revoke_btn):
            button.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.publish_btn.setEnabled(self.review is not None and self.review.ok)

    def _failed(self, error: str) -> None:
        self._say(error)
        plain_box(self, "warning", self._t("zoo.maint.done.title"), error)

    def _maintainer(self) -> Any:
        return _maintainer(self.framework_manager, self._progress)

    def _remembered_key(self) -> None:
        try:
            name = self._maintainer().remembered_key()
        except Exception:  # noqa: BLE001 - no repository set yet: the field stays empty
            name = ""
        if name and not self.key_edit.text():
            self.key_edit.setText(name)

    def key_name(self) -> str:
        return self.key_edit.text().strip()

    def cancel(self) -> None:
        self._cancel.set()
        self.cancel_btn.setEnabled(False)
        self._say(self._t("zoo.maint.cancelling"))

    def shutdown(self) -> None:
        """Before the window closes: a running action stops at its next step."""
        self._cancel.set()
        for task in self._tasks:
            task.wait(30000)
        self._discard_review()

    # -- 1. the signing key ---------------------------------------------------------------------------------------
    def create_key(self) -> None:
        dialog = KeyNameDialog(self.key_name() or f"lcf-community-{date.today().year}", self, self.lang)
        if not dialog.exec():
            return
        name = dialog.name()
        self._say(self._t("zoo.maint.key.creating", name=one_line(name)))
        self._start(self._create_key, self._key_created, name)

    def _create_key(self, name: str):
        from orchestrator.Core import community_maintainer as cmaint
        audit = getattr(getattr(self.framework_manager, "tool_registry", None), "audit", None)
        entry = cmaint.create_signing_key(name, audit=audit, actor="gui")
        try:
            self._maintainer().remember_key(entry.name)
        except Exception:  # noqa: BLE001 - only the convenience of the prefilled name
            pass
        return entry

    def _key_created(self, entry) -> None:
        from orchestrator.gui.community_dialogs import PublicKeysDialog
        self.key_edit.setText(entry.name)
        intro = self._t("zoo.maint.key.done", name=entry.name, key_id=entry.key_id)
        self._say(intro)
        PublicKeysDialog(entry.text(), self, self.lang, intro=intro, title_key="zoo.maint.key.title",
                         file_name=f"{entry.name}.public-key.txt", name="dlg_zoo_maint_key").exec()

    # -- 2. review and publish ------------------------------------------------------------------------------------
    def load_contributions(self) -> None:
        self._start(lambda: self._maintainer().contributions(), self._loaded)

    def _loaded(self, items) -> None:
        self.items = list(items)
        self.table.setRowCount(0)
        for item in self.items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (item.label, item.title, item.author, "\n".join(item.packs),
                      self._t(f"zoo.maint.check.{item.check}"))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(one_line(value) if column != 3 else display_text(value))
                self.table.setItem(row, column, cell)
        self.table.resizeRowsToContents()
        self._say(self._t("zoo.maint.loaded", n=len(self.items)) if self.items else self._t("zoo.maint.none"))

    def selected(self):
        row = self.table.currentRow()
        return self.items[row] if 0 <= row < len(self.items) else None

    def review_selected(self) -> None:
        item = self.selected()
        if item is None:
            self._say(self._t("zoo.maint.choose_first"))
            return
        self._discard_review()
        self._say(self._t("zoo.maint.reviewing", item=one_line(item.label)))
        self._start(lambda: self._maintainer().review(item), self._reviewed)

    def _discard_review(self) -> None:
        if self.review is not None:
            self.review.discard()
        self.review = None
        self._files = []
        self.file_list.clear()
        self.file_view.clear()
        self.publish_btn.setEnabled(False)

    def _reviewed(self, review) -> None:
        self.review = review
        self._files = []
        self.file_list.clear()
        for pack in review.packs:
            for file in pack.files:
                self._files.append(file)
                self.file_list.addItem(one_line(f"{pack.file_name}: {file.path}"))
        problems = review.problems()
        if review.ok:
            self._say(self._t("zoo.maint.reviewed.ok", item=one_line(review.item.label)))
        else:
            text = self._t("zoo.maint.reviewed.bad", item=one_line(review.item.label),
                           problems="\n".join(f"- {p}" for p in problems) or "-")
            self._say(text)
            plain_box(self, "warning", self._t("zoo.maint.done.title"), text)
        if self._files:
            self.file_list.setCurrentRow(0)
        self._idle()

    def _show_file(self, row: int) -> None:
        files = getattr(self, "_files", [])
        if not 0 <= row < len(files):
            self.file_view.clear()
            return
        file = files[row]
        self.file_view.setPlainText(display_text(file.text) if file.text is not None
                                    else self._t("zoo.maint.binary", size=file.size))

    def publish(self) -> None:
        review = self.review
        if review is None or not review.ok:
            self._say(self._t("zoo.maint.choose_first"))
            return
        key, trust, title = self.key_name(), self.trust_combo.currentData(), self.title_edit.text().strip()
        if not key:
            from orchestrator.Core.community_maintainer import MaintainerError
            self._failed(MaintainerError("no_key_name").text(self.lang))
            return
        question = self._t("zoo.maint.publish.ask", packs="\n".join(p.line() for p in review.packs),
                           item=review.item.label, trust=trust, key=key)
        if not ask(self, self._t("zoo.maint.publish.title"), question):
            self._say(self._t("zoo.maint.publish.no"))
            return
        self._start(lambda: self._maintainer().publish(review, key_name=key, trust=trust, title=title,
                                                       cancelled=self._cancel.is_set),
                    self._published, cancellable=True)

    def _published(self, result) -> None:
        self._discard_review()
        self.load_contributions()

    # -- 3. revoke ------------------------------------------------------------------------------------------------
    def revoke(self) -> None:
        key = self.key_name()
        self._say(self._t("zoo.maint.revoke.loading"))
        self._start(lambda: self._maintainer().published(key), lambda index: self._choose_revocation(index, key))

    def _choose_revocation(self, index, key: str) -> None:
        entries = list(index.entries) if index is not None else []
        if not entries:
            self._say(self._t("zoo.maint.revoke.empty"))
            return
        dialog = RevokeDialog(entries, self, self.lang)
        if not dialog.exec():
            return
        kind, pack_id, version, sha256, reason = dialog.values()
        if not key:
            from orchestrator.Core.community_maintainer import MaintainerError
            self._failed(MaintainerError("no_key_name").text(self.lang))
            return
        from orchestrator.Core import community_maintainer as cmaint
        try:
            what = cmaint.describe_revocation(cmaint.revocation(kind, pack_id, reason=reason, version=version,
                                                                sha256=sha256))
        except cmaint.MaintainerError as e:
            self._failed(e.text(self.lang))
            return
        if not ask(self, self._t("zoo.maint.revoke.title"),
                   self._t("zoo.maint.revoke.ask", what=what, reason=reason, key=key)):
            self._say(self._t("zoo.maint.revoke.no"))
            return
        self._start(lambda: self._maintainer().revoke(kind, pack_id, reason=reason, version=version, sha256=sha256,
                                                      key_name=key, cancelled=self._cancel.is_set),
                    lambda result: None, cancellable=True)


class KeyNameDialog(QDialog):
    """The name of a new index signing key (not secret: it appears in the public entry)."""

    def __init__(self, suggested: str, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.setObjectName("dlg_zoo_maint_key_name")
        self.setWindowTitle(l10n_maintainer.text("zoo.maint.key.title", self.lang))
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(l10n_maintainer.text("zoo.maint.key.ask_name", self.lang)))
        self.edit = QLineEdit(suggested, self)
        self.edit.setObjectName("edt_zoo_maint_new_key")
        layout.addWidget(self.edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def name(self) -> str:
        return self.edit.text().strip()


class RevokeDialog(QDialog):
    """Which published pack to revoke, how far (this file, this version, every version) and why."""

    SCOPES = ("version", "file", "all")

    def __init__(self, entries, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.entries = list(entries)
        self.setObjectName("dlg_zoo_maint_revoke")
        self.setWindowTitle(self._t("zoo.maint.revoke.title"))
        self.resize(640, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.pack = QComboBox(self)
        self.pack.setObjectName("cmb_zoo_maint_revoke_pack")
        for entry in self.entries:
            self.pack.addItem(one_line(f"{entry.kind} {entry.id} {entry.version} ({entry.sha256[:12]})"))
        form.addRow(plain_label(self._t("zoo.maint.revoke.pack"), wrap=False), self.pack)
        self.scope = QComboBox(self)
        self.scope.setObjectName("cmb_zoo_maint_revoke_scope")
        for scope in self.SCOPES:
            self.scope.addItem(self._t(f"zoo.maint.revoke.scope.{scope}"), scope)
        form.addRow(plain_label(self._t("zoo.maint.revoke.scope"), wrap=False), self.scope)
        self.reason = QLineEdit(self)
        self.reason.setObjectName("edt_zoo_maint_revoke_reason")
        self.reason.setMaxLength(300)
        self.reason.setPlaceholderText(self._t("zoo.maint.revoke.reason.hint"))
        form.addRow(plain_label(self._t("zoo.maint.revoke.reason"), wrap=False), self.reason)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _t(self, text_key: str, /, **values: Any) -> str:
        return l10n_maintainer.text(text_key, self.lang, **values)

    def _accept(self) -> None:
        if not self.reason.text().strip():
            plain_box(self, "warning", self._t("zoo.maint.revoke.title"), self._t("zoo.maint.revoke.no_reason"))
            return
        self.accept()

    def values(self):
        """(kind, id, version, sha256, reason) of the chosen revocation."""
        entry = self.entries[self.pack.currentIndex()]
        scope = self.scope.currentData()
        return (entry.kind, entry.id, entry.version if scope == "version" else None,
                entry.sha256 if scope == "file" else None, self.reason.text().strip())
