#!/usr/bin/env python3
"""
Model Archives... in the Models tab (DESIGN, Hugging Face, the row "A model for a computer without a network travels
as a portable model archive"; the mouse path of `llm-cli models archive write / take / list / delete`).

Write a Model Archive... looks the model up (Core/model_archive.plan_archive, in a worker thread), shows its licence and
gating and asks when the licence is gated, restrictive or unknown ("No" by default), then writes the archive to the file
the person chooses (write_archive, in a worker). Take in a Model Archive... checks the chosen file in quarantine
(prepare_take, in a worker), shows the summary with the licence of the model and its base models and asks the person to
accept it ("No" by default: discard, nothing stays); only then is it put in place (finish_take, actor "gui"). The list
shows the archives taken in; Delete... asks with the deletion dialog. Every text is plain text; an AI reaches nothing
of this window.
"""

import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text


class ModelArchiveDialog(QDialog):
    """The model archives: write one, take one in, list and delete the ones taken in."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.setObjectName("dlg_model_archives")
        self.setWindowTitle(self._t("models.archive.title"))
        self.resize(760, 560)
        self._requests: Dict[str, object] = {}
        self._relay = worker_relay()
        self._relay.done.connect(self._on_result, Qt.QueuedConnection)
        self.stop = threading.Event()

        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("models.archive.intro"), wrap=True))
        form = QFormLayout()
        self.txt_repo = QLineEdit(self)
        self.txt_repo.setObjectName("txt_archive_repo")
        self.txt_repo.setPlaceholderText("HuggingFaceTB/SmolLM2-135M-Instruct")
        form.addRow(self._t("models.archive.repo"), self.txt_repo)
        self.txt_revision = QLineEdit(self)
        self.txt_revision.setObjectName("txt_archive_revision")
        form.addRow(self._t("models.archive.revision"), self.txt_revision)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.btn_write = self._button(row, "btn_archive_write", "models.archive.write", self.write_archive)
        self.btn_take = self._button(row, "btn_archive_take", "models.archive.take", self.take_archive)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel(self._t("models.archive.taken_list"), self))
        self.list_archives = QListWidget(self)
        self.list_archives.setObjectName("list_archives")
        self.list_archives.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.list_archives, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.btn_delete = self._button(actions, "btn_archive_delete", "models.archive.delete", self.delete_selected)
        layout.addLayout(actions)
        self.txt_result = QPlainTextEdit(self)
        self.txt_result.setObjectName("txt_archive_result")
        self.txt_result.setReadOnly(True)
        layout.addWidget(self.txt_result, 1)
        self.refresh()

    def _t(self, key: str, **params: Any) -> str:
        return ui_text(key, self.lang, **params)

    def _button(self, layout, name: str, key: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: slot())   # never the `checked` flag as the slot's argument
        layout.addWidget(button)
        return button

    def busy(self) -> bool:
        return bool(self._requests)

    def _in_worker(self, kind: str, work: Callable[[], Any]) -> None:
        """Runs `work()` in a worker thread; its result comes back through the relay to _on_result."""
        token = object()
        self._requests[kind] = token
        relay = self._relay
        self.txt_result.setPlainText(self._t("models.archive.working"))
        self.btn_write.setEnabled(False)
        self.btn_take.setEnabled(False)

        def run():
            try:
                result = ("ok", work())
            except Exception as e:  # noqa: BLE001 - shown as plain text
                from orchestrator.Core.hf_hub import person_text
                result = ("error", f"{person_text(e, self.lang)} {getattr(e, 'next', '') or ''}".strip())
            relay.done.emit(token, result)

        threading.Thread(target=run, name=f"model-archive-{kind}", daemon=True).start()

    def _on_result(self, token: object, result) -> None:
        kind = next((k for k, t in self._requests.items() if t is token), None)
        if kind is None or not isinstance(result, tuple) or len(result) != 2:
            return
        self._requests.pop(kind, None)
        try:
            self.btn_write.setEnabled(True)
            self.btn_take.setEnabled(True)
            state, value = result
            if state != "ok":
                self.txt_result.setPlainText(str(value))
                return
            handler = {"plan": self._planned, "write": self._written, "take": self._checked}.get(kind)
            if handler is not None:
                handler(value)
        except RuntimeError:   # the window was closed meanwhile
            pass

    # --- the list ---

    def refresh(self) -> None:
        from orchestrator.Core import model_archive as ma
        from orchestrator.utils.sizes import format_size
        self.list_archives.clear()
        try:
            found = ma.stored(ma.models_dir(self.framework))
        except Exception:  # noqa: BLE001 - an unreadable folder lists nothing
            found = []
        if not found:
            item = QListWidgetItem(self._t("models.archive.none"))
            item.setFlags(Qt.NoItemFlags)
            self.list_archives.addItem(item)
        for archive in found:
            item = QListWidgetItem(self._t("models.archive.row", label=archive.label,
                                           size=format_size(archive.size_bytes, self.lang),
                                           taken=str(archive.taken.get("taken") or "")[:16].replace("T", " ")))
            item.setData(Qt.UserRole, str(archive.folder))
            self.list_archives.addItem(item)
        self._update_buttons()

    def _selected_folder(self) -> Optional[str]:
        items = self.list_archives.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _update_buttons(self) -> None:
        self.btn_delete.setEnabled(self._selected_folder() is not None)

    # --- writing ---

    def write_archive(self, path: Optional[str] = None, confirmed: Optional[bool] = None) -> None:
        """Looks the model up in a worker; the licence question and the file choice follow in _planned. `path` and
        `confirmed` stand in for the file dialog and the question (tests)."""
        repo = self.txt_repo.text().strip()
        if not repo:
            self.txt_result.setPlainText(self._t("models.archive.no_repo"))
            return
        revision = self.txt_revision.text().strip() or None
        self._pending_write = {"path": path, "confirmed": confirmed}
        framework = self.framework
        from orchestrator.Core.model_archive import plan_archive
        self._in_worker("plan", lambda: plan_archive(framework, repo, revision))

    def _planned(self, plan) -> None:
        from orchestrator.Core import model_archive as ma
        pending = getattr(self, "_pending_write", None) or {}
        text = ma.plan_text(plan, self.lang)
        self.txt_result.setPlainText(text)
        confirmed = False
        if plan.ask_licence:
            confirmed = pending.get("confirmed")
            if confirmed is None:
                confirmed = ask(self, self._t("models.archive.title"),
                                self._t("models.archive.confirm_write", text=text))
            if not confirmed:
                return
        path = pending.get("path")
        if path is None:
            name = plan.repo.split("/", 1)[1] + "-" + plan.commit[:12] + ma.SUFFIX
            path, _ = QFileDialog.getSaveFileName(self, self._t("models.archive.save_title"), str(Path.home() / name),
                                                  f"Model archive (*{ma.SUFFIX})")
        if not path:
            return
        framework, stop, lang = self.framework, self.stop, self.lang
        self._in_worker("write", lambda: ma.written_text(
            ma.write_archive(framework, plan, Path(path), licence_confirmed=bool(confirmed), stop=stop), lang))

    def _written(self, text: str) -> None:
        self.txt_result.setPlainText(text)

    # --- taking in ---

    def take_archive(self, path: Optional[str] = None, accept: Optional[bool] = None) -> None:
        """Checks the chosen archive in quarantine (a worker); the licence question follows in _checked. `path` and
        `accept` stand in for the file dialog and the question (tests)."""
        from orchestrator.Core import model_archive as ma
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self._t("models.archive.open_title"), str(Path.home()),
                                                  f"Model archive (*{ma.SUFFIX});;All files (*)")
        if not path:
            return
        self._pending_accept = accept
        framework, stop = self.framework, self.stop
        self._in_worker("take", lambda: ma.prepare_take(framework, Path(path), stop=stop))

    def _checked(self, prepared) -> None:
        from orchestrator.Core import model_archive as ma
        summary = ma.summary_text(prepared, self.lang)
        self.txt_result.setPlainText(summary)
        accept = getattr(self, "_pending_accept", None)
        if accept is None:
            accept = ask(self, self._t("models.archive.title"), summary)
        if not accept:
            ma.discard(prepared)
            self.txt_result.setPlainText(self._t("models.archive.declined"))
            return
        try:
            ma.finish_take(prepared, actor="gui", licence_accepted=True)
        except Exception as e:  # noqa: BLE001 - refused meanwhile (taken in already): plain text, nothing stays
            self.txt_result.setPlainText(str(e))
            return
        label = f"{prepared.manifest.repo}@{prepared.manifest.commit}"
        self.txt_result.setPlainText(self._t("models.archive.taken", label=label))
        self.refresh()

    # --- deleting ---

    def delete_selected(self) -> Optional[Any]:
        """Delete...: the selected archive through the deletion dialog (the exact list, "No" by default)."""
        from orchestrator.Core import model_archive as ma
        from orchestrator.Core.deletion import choose, kept_lines
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        folder = self._selected_folder()
        if folder is None:
            return None
        title = self._t("models.archive.delete.title")
        models = ma.models_dir(self.framework)
        plan = ma.deletion_plan(models)
        index = [i for i, item in enumerate(plan.items if plan is not None else []) if item.full_path == folder]
        if plan is None or not index:
            self.refresh()
            return None
        accepted = ask_deletion(self, choose(plan, index), title=title)
        if accepted is None:
            return None
        try:
            trash = Trash.for_framework(self.framework)
        except Exception:  # noqa: BLE001 - no per-user folder: only deleting for good works then
            trash = None
        audit = getattr(self.framework, "audit", None) or \
            getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        try:
            result = ma.delete(models, accepted, trash=trash, audit=audit)
        except Exception as e:  # noqa: BLE001 - refused, changed meanwhile, no trash or audit: nothing gone
            plain_box(self, "warning", title, str(e))
            self.refresh()
            return None
        self.refresh()
        self.txt_result.setPlainText(self._t("models.archive.deleted", count=len(result.done)))
        kept = kept_lines(result)
        if kept:
            plain_box(self, "warning", title, "\n".join(kept))
        return result

    def closeEvent(self, event) -> None:   # noqa: N802 - Qt's name
        self.stop.set()   # a running write or check stops between two chunks; nothing half-done stays
        super().closeEvent(event)


def open_model_archives(parent: QWidget, framework: Any) -> ModelArchiveDialog:
    dialog = ModelArchiveDialog(framework, parent)
    dialog.show()
    return dialog


__all__ = ["ModelArchiveDialog", "open_model_archives"]
