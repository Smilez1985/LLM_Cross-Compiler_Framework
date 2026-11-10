#!/usr/bin/env python3
"""
The "edit all files" step in the GUI: `ModuleEditPanel` (the module wizard's last page and the window
Build > Targets... > Edit Files...) over Core/module_edit.ModuleEdit, the same model as `llm-cli module edit`.

The panel lists the files of the step (the stage modules, the slot modules, target.yml, the Dockerfiles; never the
stage runner), each as plain text in an editor of its own, with the diff of every changed file against the text it
had when the step opened, and runs the checks (validate_target_folder on a copy with the edits) on request and before
Finish or Save, which are refused with the reasons while a check fails.

An AI proposes through the GUI tools (orchestrator/gui/form_surface.py): the form "module_edit" has the files as
fields an AI reads but never fills, and one field "proposal" that takes a unified diff. A filled proposal is applied
to the editors, marked, listed in the banner with its Undo and shown in the diff; it is only a change in this step.
A file the person edited is theirs, so a proposal that would change it is refused. Only the person's Finish or
Save writes anything. Every text is plain text.
"""

import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QListWidget, QPlainTextEdit,
                               QPushButton, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from orchestrator.Core.module_edit import EditRefused, ModuleEdit
from orchestrator.gui.plain import button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

PROPOSAL_FIELD = "proposal"


def problems_text(problems: List[str]) -> str:
    return "\n".join(f"- {p}" for p in problems)


class ModuleEditPanel(QWidget):
    """The files of one ModuleEdit with their diff, the checks and the AI's proposals (see the module docstring)."""

    def __init__(self, parent: Optional[QWidget] = None, registry: Any = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.registry = registry
        self.edit: Optional[ModuleEdit] = None
        self.editors: Dict[str, QPlainTextEdit] = {}
        self.binding = None
        self.form = None
        self._proposal = ""               # the AI's diff now applied ("" none)
        self._proposal_texts: Dict[str, tuple] = {}   # file -> (text before it, text after it)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.note = plain_label("")
        self.note.setVisible(False)
        layout.addWidget(self.note)
        self.banner_slot = QVBoxLayout()
        layout.addLayout(self.banner_slot)
        split = QSplitter(Qt.Horizontal, self)
        self.file_list = QListWidget(split)
        self.file_list.setObjectName("list_module_edit_files")
        self.tabs = QTabWidget(split)
        self.stack = QStackedWidget(self.tabs)
        self.diff_view = QPlainTextEdit(self.tabs)
        self.diff_view.setObjectName("text_module_edit_diff")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.stack, self._t("bld.edit.text"))
        self.tabs.addTab(self.diff_view, self._t("bld.edit.diff"))
        split.setStretchFactor(1, 4)
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.btn_check = QPushButton(button_text(self._t("bld.edit.check")), self)
        self.btn_check.setObjectName("btn_module_edit_check")
        self.btn_apply = QPushButton(button_text(self._t("bld.edit.apply")), self)
        self.btn_apply.setObjectName("btn_module_edit_apply")
        self.btn_reset = QPushButton(button_text(self._t("bld.edit.reset")), self)
        self.btn_reset.setObjectName("btn_module_edit_reset")
        for button, slot in ((self.btn_check, self.run_checks), (self.btn_apply, self.apply_diff_file),
                             (self.btn_reset, self.reset_current)):
            button.setAutoDefault(False)
            button.clicked.connect(lambda *_a, s=slot: s())
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.problems_view = QPlainTextEdit(self)
        self.problems_view.setObjectName("text_module_edit_problems")
        self.problems_view.setReadOnly(True)
        self.problems_view.setMaximumHeight(110)
        layout.addWidget(self.problems_view)
        self.file_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self._set_enabled(False)

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _set_enabled(self, on: bool) -> None:
        for widget in (self.btn_check, self.btn_apply, self.btn_reset, self.file_list, self.tabs):
            widget.setEnabled(on)

    # --- loading ---------------------------------------------------------------------------------------------------

    def load(self, edit: ModuleEdit, note: str = "") -> None:
        """Shows the files of `edit` (replacing what the panel showed) and binds them for the GUI tools."""
        self.unload()
        self.edit = edit
        for rel in edit.files:
            editor = QPlainTextEdit(self.stack)
            editor.setObjectName(f"text_module_edit_{rel}")
            editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            editor.setPlainText(edit.text(rel))   # shell code and YAML: never rich text
            editor.textChanged.connect(lambda r=rel, e=editor: self._edited(r, e))
            self.editors[rel] = editor
            self.stack.addWidget(editor)
            self.file_list.addItem(rel)
        self.file_list.setCurrentRow(0)
        self._bind()
        self.show_note(note)
        self.problems_view.setPlainText("")
        self._set_enabled(True)
        self.refresh()

    def show_error(self, text: str) -> None:
        """The step has no files (they could not be made or opened): only the reason is shown."""
        self.unload()
        self.problems_view.setPlainText(text)

    def show_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def unload(self) -> None:
        from orchestrator.tools import gui_surface
        if self.form is not None:
            gui_surface.unregister_form(self.form)
        for banner in getattr(self.binding, "banners", []):
            banner.setParent(None)
            banner.deleteLater()
        self.binding = self.form = None
        self.edit = None
        self._proposal, self._proposal_texts = "", {}
        self.file_list.clear()
        for editor in self.editors.values():
            self.stack.removeWidget(editor)
            editor.deleteLater()
        self.editors = {}
        self.diff_view.setPlainText("")
        self._set_enabled(False)

    def _bind(self) -> None:
        """The form "module_edit" of the GUI tools: every file to read, the proposal (a diff) to fill."""
        from orchestrator.gui.form_surface import FormBinding, person_audit
        b = FormBinding("module_edit", "bld.edit.form")
        never = ("an AI never writes a file here: propose the change as a unified diff in the field 'proposal' "
                 "(the person sees its diff and decides)")
        for rel, editor in self.editors.items():
            b.text(f"file:{rel}", rel, editor, read_only=lambda: never,
                   hint="the file's text now (the stage runner is never among the files)")
        b.add(PROPOSAL_FIELD, "bld.edit.field.proposal", "code", lambda: self._proposal, self._set_proposal,
              [self.diff_view], check=lambda _current, value: self._proposal_problem(value),
              hint="a unified diff (--- a/<file>, +++ b/<file>, @@ hunks) against the files' text now; it is applied "
                   "to the editors and shown to the person, who saves or drops it. Empty: no proposal")
        self.form = b.finish(self, show=self._show_window, is_open=self.isVisible)
        self.binding = b
        self.banner_slot.addWidget(b.banner(self, audit=person_audit(self.registry)))
        b.refresh()

    def _show_window(self) -> None:
        window = self.window()
        window.show()
        window.raise_()
        window.activateWindow()

    # --- edits, the proposal and the diff --------------------------------------------------------------------------

    def _edited(self, rel: str, editor: QPlainTextEdit) -> None:
        if self.edit is None:
            return
        self.edit.set_text(rel, editor.toPlainText())
        self.refresh()

    def _set_editor(self, rel: str, text: str) -> None:
        editor = self.editors[rel]
        if editor.toPlainText() != text:
            editor.setPlainText(text)

    def _revert_proposal(self) -> None:
        """Takes the applied proposal back from every file that still holds its text."""
        for rel, (before, after) in self._proposal_texts.items():
            now = self.edit.text(rel)
            if now == after:
                self._set_editor(rel, before)
            elif now != before:
                raise ValueError(f"{rel} was changed after the proposal; it is kept as it is")
        self._proposal, self._proposal_texts = "", {}

    def _proposal_problem(self, value: Any) -> str:
        if self.edit is None:
            return "no files are open"
        text = str(value or "")
        if not text.strip():
            return ""
        trial = copy.copy(self.edit)   # the same files, its own texts
        trial.texts = dict(self.edit.texts)
        for rel, (before, after) in self._proposal_texts.items():   # as it will apply: on the texts without the old
            if trial.texts[rel] == after:
                trial.texts[rel] = before
        try:
            trial.apply_diff(text)
        except ValueError as e:
            return str(e)
        return ""

    def _set_proposal(self, value: Any) -> None:
        text = str(value or "")
        self._revert_proposal()
        if not text.strip():
            return
        before = dict(self.edit.texts)
        changed = self.edit.apply_diff(text)
        texts = dict(self.edit.texts)
        self.edit.texts = before        # the editors write each file back into the model
        for rel in changed:
            self._set_editor(rel, texts[rel])
        self._proposal = text
        self._proposal_texts = {rel: (before[rel], texts[rel]) for rel in changed}
        if changed:
            self.file_list.setCurrentRow(self.edit.files.index(changed[0]))
        self.tabs.setCurrentWidget(self.diff_view)

    def apply_diff_text(self, text: str) -> List[str]:
        """The person applies a diff (from an AI or written by hand): the changed files become theirs."""
        if self.edit is None:
            return []
        before = dict(self.edit.texts)
        try:
            changed = self.edit.apply_diff(text)
        except ValueError as e:
            plain_box(self, "critical", self._t("bld.edit.apply_title"), self._t("bld.edit.apply_failed", reason=e))
            return []
        texts = dict(self.edit.texts)
        self.edit.texts = before
        for rel in changed:
            self._set_editor(rel, texts[rel])
            if self.form is not None:
                self.form.person_edited(f"file:{rel}")
        self.tabs.setCurrentWidget(self.diff_view)
        return changed

    def apply_diff_file(self) -> List[str]:
        path, _ = QFileDialog.getOpenFileName(self, self._t("bld.edit.apply_title"), "",
                                              "Diff (*.diff *.patch);;All files (*)")
        if not path:
            return []
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            plain_box(self, "critical", self._t("bld.edit.apply_title"), self._t("bld.edit.apply_failed", reason=e))
            return []
        return self.apply_diff_text(text)

    def current_file(self) -> Optional[str]:
        row = self.file_list.currentRow()
        return self.edit.files[row] if self.edit is not None and 0 <= row < len(self.edit.files) else None

    def reset_current(self) -> None:
        rel = self.current_file()
        if rel is not None:
            self._set_editor(rel, self.edit.generated[rel])

    def refresh(self) -> None:
        if self.edit is None:
            return
        changed = set(self.edit.changed())
        for row, rel in enumerate(self.edit.files):
            item = self.file_list.item(row)
            if item is not None:
                item.setText(self._t("bld.edit.changed", name=rel) if rel in changed else rel)
        self.diff_view.setPlainText(self.edit.diff() or self._t("bld.edit.no_changes"))

    # --- the checks ------------------------------------------------------------------------------------------------

    def run_checks(self) -> List[str]:
        """The checks of the folder with the edits; the result is shown below the files."""
        if self.edit is None:
            return ["no files are open"]
        problems = self.edit.problems()
        self.problems_view.setPlainText(self._t("bld.edit.checks_failed", problems=problems_text(problems))
                                        if problems else self._t("bld.edit.checks_ok"))
        return problems

    def save(self) -> Optional[List[str]]:
        """Writes the edits after the checks passed; None (refused, the reasons shown) while a check fails."""
        if self.edit is None:
            return None
        try:
            written = self.edit.save()
        except EditRefused as e:
            self.problems_view.setPlainText(self._t("bld.edit.checks_failed", problems=problems_text(e.problems)))
            plain_box(self, "critical", self._t("bld.edit.refused_title"),
                      self._t("bld.edit.refused", problems=problems_text(e.problems)))
            return None
        self._proposal, self._proposal_texts = "", {}
        self.problems_view.setPlainText(self._t("bld.edit.checks_ok"))
        self.refresh()
        return written


class ModuleEditDialog(QDialog):
    """Build > Targets... > Edit Files...: the edit step for a target of the targets folder (the repair by hand)."""

    def __init__(self, folder: Path, parent: Optional[QWidget] = None, registry: Any = None,
                 lang: Optional[str] = None, on_saved: Optional[Callable[[List[str]], None]] = None):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.folder = Path(folder)
        self.on_saved = on_saved
        self.saved: List[str] = []
        self.setWindowTitle(ui_text("bld.edit.title", self.lang, target=one_line(self.folder.name)))
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(ui_text("bld.edit.intro", self.lang, target=one_line(self.folder.name))))
        self.panel = ModuleEditPanel(self, registry=registry, lang=self.lang)
        layout.addWidget(self.panel, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_save = QPushButton(button_text(ui_text("bld.edit.save", self.lang)), self)
        self.btn_save.setObjectName("btn_module_edit_save")
        self.btn_save.setAutoDefault(False)
        self.btn_save.clicked.connect(lambda *_a: self.save())
        buttons.addButton(self.btn_save, QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.panel.load(ModuleEdit(self.folder))
        self.finished.connect(lambda *_a: self.panel.unload())

    def save(self) -> Optional[List[str]]:
        written = self.panel.save()
        if written is None:
            return None
        self.saved += [rel for rel in written if rel not in self.saved]
        plain_box(self, "information", ui_text("bld.edit.save", self.lang),
                  ui_text("bld.edit.saved", self.lang, files=", ".join(written)) if written
                  else ui_text("bld.edit.no_changes", self.lang))
        if written and self.on_saved is not None:
            self.on_saved(written)
        return written


def open_module_edit(parent: Optional[QWidget], folder: Path, registry: Any = None,
                     on_saved: Optional[Callable[[List[str]], None]] = None) -> Optional[ModuleEditDialog]:
    """Opens the edit step for an existing target folder; a folder that cannot be opened is said in plain text."""
    try:
        dialog = ModuleEditDialog(Path(folder), parent, registry=registry, on_saved=on_saved)
    except (OSError, ValueError) as e:
        plain_box(parent, "critical", ui_text("bld.edit.refused_title"),
                  ui_text("bld.edit.open_failed", target=one_line(Path(folder).name), reason=one_line(str(e))))
        return None
    dialog.exec()
    return dialog


__all__ = ["ModuleEditDialog", "ModuleEditPanel", "PROPOSAL_FIELD", "open_module_edit", "problems_text"]
