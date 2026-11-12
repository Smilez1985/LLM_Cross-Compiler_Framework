#!/usr/bin/env python3
"""
The form per value of the general template's variables (docs/DESIGN.md, Targets, hardware and templates: the form
per value): one tab per output format, one field per variable, generated from the schema through
Core/template_values.format_fields (a line for a word or a list of words, a choice for a closed set, ticks for the
input formats, a table for the runtime libraries and the vendor files), each with its explanation as help text, the
required ones marked, Ditto's proposed value with its reason, source and confidence beside it, and the checks'
reasons under the field they belong to.

The form keeps one value with a YAML text editor (the wizard's formats field, the edit step's variables field): every
change of the form writes that YAML, which the generator, the edit step and the GUI tools read; YAML typed by hand
(the advanced view behind the toggle) or filled by an AI fills the form again. Every text is plain text.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import yaml
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from orchestrator.Core.template_values import (FieldSpec, field_problems, format_fields, get_value, missing_required,
                                               parse_text, set_value, value_text)
from orchestrator.gui.plain import button_text, plain_label
from orchestrator.utils.localization import current_language, ui_text

PRESETS = ("rknn", "rkllm", "gguf")
ERROR_STYLE = "color: #c62828;"
NOTE_STYLE = "color: #1565c0;"


def _plain(text: Any) -> str:
    from orchestrator.utils.untrusted import sanitize_untrusted
    return sanitize_untrusted(str(text), 600)


class FieldRow(QWidget):
    """One variable: its input, Ditto's proposal beside it, the checks' reasons under it."""

    def __init__(self, spec: FieldSpec, on_change, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.spec = spec
        self._on_change = on_change
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        self.input = self._make_input()
        box.addWidget(self.input)
        self.note = plain_label("")
        self.note.setStyleSheet(NOTE_STYLE)
        self.note.setVisible(False)
        box.addWidget(self.note)
        self.error = plain_label("")
        self.error.setStyleSheet(ERROR_STYLE)
        self.error.setVisible(False)
        box.addWidget(self.error)
        self.setToolTip(spec.help)

    # --- the input by the field's kind -------------------------------------------------------------------------
    def _make_input(self) -> QWidget:
        spec = self.spec
        name = "fmt_" + spec.path.replace(".", "_")
        if spec.kind == "multi":
            holder = QWidget(self)
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            self.ticks = {}
            for choice in spec.choices:
                tick = QCheckBox(choice, holder)
                tick.setObjectName(f"{name}_{choice}")
                tick.toggled.connect(lambda _on: self._on_change())
                row.addWidget(tick)
                self.ticks[choice] = tick
            row.addStretch(1)
            return holder
        if spec.kind == "bool":
            tick = QCheckBox(self)
            tick.toggled.connect(lambda _on: self._on_change())
            tick.setObjectName(name)
            return tick
        if spec.kind == "choice" or spec.choices_from:
            combo = QComboBox(self)
            combo.setObjectName(name)
            combo.setEditable(bool(spec.choices_from))
            combo.addItems([""] + list(spec.choices))
            combo.currentTextChanged.connect(lambda _t: self._on_change())
            return combo
        if spec.kind == "records":
            holder = QWidget(self)
            col = QVBoxLayout(holder)
            col.setContentsMargins(0, 0, 0, 0)
            self.table = QTableWidget(0, len(spec.columns), holder)
            self.table.setObjectName(name)
            self.table.setHorizontalHeaderLabels([c.path + (" *" if c.required else "") for c in spec.columns])
            for i, c in enumerate(spec.columns):
                header = self.table.horizontalHeaderItem(i)
                if header is not None:
                    header.setToolTip(c.help)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setMinimumHeight(90)
            self.table.itemChanged.connect(lambda _item: self._on_change())
            col.addWidget(self.table)
            buttons = QHBoxLayout()
            add = QPushButton(button_text(ui_text("bld.form.row_add")), holder)
            add.setObjectName(name + "_add")
            add.setAutoDefault(False)
            add.clicked.connect(lambda *_a: self.add_row())
            remove = QPushButton(button_text(ui_text("bld.form.row_remove")), holder)
            remove.setObjectName(name + "_remove")
            remove.setAutoDefault(False)
            remove.clicked.connect(lambda *_a: self.remove_row())
            buttons.addWidget(add)
            buttons.addWidget(remove)
            buttons.addStretch(1)
            col.addLayout(buttons)
            return holder
        line = QLineEdit(self)
        line.setObjectName(name)
        if spec.default not in (None, "", []):
            line.setPlaceholderText(value_text(spec, spec.default))
        line.textChanged.connect(lambda _t: self._on_change())
        return line

    def add_row(self, values: Optional[Dict[str, Any]] = None) -> None:
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)
        for i, c in enumerate(self.spec.columns):
            self.table.setItem(row, i, QTableWidgetItem(value_text(c, (values or {}).get(c.path))))
        self.table.blockSignals(False)
        self._on_change()

    def remove_row(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True) or \
            ([self.table.rowCount() - 1] if self.table.rowCount() else [])
        for row in rows:
            self.table.removeRow(row)
        self._on_change()

    # --- value -----------------------------------------------------------------------------------------------
    def value(self) -> Any:
        spec = self.spec
        if spec.kind == "multi":
            return [c for c, tick in self.ticks.items() if tick.isChecked()] or None
        if spec.kind == "bool":
            return True if self.input.isChecked() else None
        if spec.kind == "records":
            rows = []
            for r in range(self.table.rowCount()):
                record: Dict[str, Any] = {}
                for i, c in enumerate(spec.columns):
                    item = self.table.item(r, i)
                    try:
                        value = parse_text(c, item.text() if item is not None else "")
                    except ValueError:
                        value = item.text() if item is not None else None
                    if value is not None:
                        record[c.path] = value
                if record:
                    rows.append(record)
            return rows or None
        if isinstance(self.input, QComboBox):
            return self.input.currentText().strip() or None
        return parse_text(spec, self.input.text())

    def set_value(self, value: Any) -> None:
        spec = self.spec
        widgets = list(getattr(self, "ticks", {}).values()) + [self.input] + \
            ([self.table] if spec.kind == "records" else [])
        for w in widgets:
            w.blockSignals(True)
        try:
            if spec.kind == "multi":
                chosen = set(value or [])
                for c, tick in self.ticks.items():
                    tick.setChecked(c in chosen)
            elif spec.kind == "bool":
                self.input.setChecked(bool(value))
            elif spec.kind == "records":
                self.table.setRowCount(0)
                for record in value or []:
                    row = self.table.rowCount()
                    self.table.insertRow(row)
                    for i, c in enumerate(spec.columns):
                        self.table.setItem(row, i, QTableWidgetItem(
                            value_text(c, record.get(c.path) if isinstance(record, dict) else None)))
            elif isinstance(self.input, QComboBox):
                text = value_text(spec, value)
                if self.input.findText(text) < 0 and text:
                    self.input.addItem(text)
                self.input.setCurrentText(text)
            else:
                self.input.setText(value_text(spec, value))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_choices(self, choices: List[str]) -> None:
        if not isinstance(self.input, QComboBox):
            return
        current = self.input.currentText()
        self.input.blockSignals(True)
        self.input.clear()
        self.input.addItems([""] + [c for c in choices if c])
        self.input.setCurrentText(current)
        self.input.blockSignals(False)

    def show_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def show_error(self, reasons: List[str]) -> None:
        self.error.setText("\n".join(_plain(r) for r in reasons))
        self.error.setVisible(bool(reasons))


class FormatTab(QWidget):
    """One format's fields."""

    def __init__(self, on_change, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.rows: Dict[str, FieldRow] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        inner = QWidget(scroll)
        form = QFormLayout(inner)
        self.general = plain_label("")
        self.general.setStyleSheet(ERROR_STYLE)
        self.general.setVisible(False)
        form.addRow(self.general)
        for spec in format_fields():
            row = FieldRow(spec, on_change, inner)
            label = QLabel(spec.path + (" *" if spec.required else ""), inner)
            label.setToolTip(spec.help)
            form.addRow(label, row)
            self.rows[spec.path] = row
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.extra: Dict[str, Any] = {}   # values the form has no field for are kept as they are

    def values(self) -> Dict[str, Any]:
        out = copy.deepcopy(self.extra)
        for path, row in self.rows.items():
            set_value(out, path, row.value())
        return out

    def load(self, values: Dict[str, Any]) -> None:
        known = {p.split(".")[0] for p in self.rows}
        self.extra = {k: copy.deepcopy(v) for k, v in values.items() if k not in known}
        for path, row in self.rows.items():
            row.set_value(get_value(values, path))
        self.rows["default_quantization"].set_choices(list(get_value(values, "quantizations") or []))


class FormatsForm(QWidget):
    """The formats as a form per value, kept in one value with `text_edit` (the YAML; see the module text)."""

    personEdited = Signal()

    def __init__(self, text_edit: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.text_edit = text_edit
        self.notes: Dict[str, Dict[str, Any]] = {}
        self.top_extra: Dict[str, Any] = {}   # the edit step's source_images: kept with the formats
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.preset = QComboBox(self)
        self.preset.setObjectName("combo_formats_preset")
        self.preset.addItem(self._t("bld.form.new_format"), "")
        for name in PRESETS:
            self.preset.addItem(name, name)
        self.btn_add = QPushButton(button_text(self._t("bld.form.add")), self)
        self.btn_add.setObjectName("btn_formats_add")
        self.btn_add.setAutoDefault(False)
        self.btn_add.clicked.connect(lambda *_a: self.add_format(self.preset.currentData() or ""))
        self.btn_remove = QPushButton(button_text(self._t("bld.form.remove")), self)
        self.btn_remove.setObjectName("btn_formats_remove")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.clicked.connect(lambda *_a: self.remove_format())
        self.chk_yaml = QCheckBox(self._t("bld.form.yaml"), self)
        self.chk_yaml.setObjectName("chk_formats_yaml")
        self.chk_yaml.setToolTip(self._t("bld.form.yaml_tip"))
        self.chk_yaml.toggled.connect(self._toggle_yaml)
        for w in (self.preset, self.btn_add, self.btn_remove):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.chk_yaml)
        layout.addLayout(bar)
        self.summary = plain_label(self._t("bld.form.none"))
        layout.addWidget(self.summary)
        self.stack = QStackedWidget(self)
        self.tabs = QTabWidget(self.stack)
        self.tabs.setObjectName("tabs_formats")
        self.stack.addWidget(self.tabs)
        self.stack.addWidget(text_edit)
        self.stack.setMinimumHeight(260)
        layout.addWidget(self.stack)
        self.yaml_error = plain_label("")
        self.yaml_error.setStyleSheet(ERROR_STYLE)
        self.yaml_error.setVisible(False)
        layout.addWidget(self.yaml_error)
        text_edit.textChanged.connect(self._text_changed)
        self.reload()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    # --- the YAML -------------------------------------------------------------------------------------------
    def _text(self) -> str:
        return self.text_edit.toPlainText()

    def _text_changed(self) -> None:
        if self._syncing:
            return
        if self.chk_yaml.isChecked():   # typed in the advanced view: the form follows when the view is left
            return
        self.reload()

    def reload(self) -> bool:
        """Fills the form from the YAML (False, with the reason shown, when it cannot be read as formats)."""
        from orchestrator.Core.module_generator import parse_formats
        from orchestrator.schemas.target_template import resolve_formats
        text = self._text()
        try:
            given = yaml.safe_load(text) if text.strip() else []
            self.top_extra = {k: v for k, v in given.items() if k != "formats"} if isinstance(given, dict) else {}
            formats = resolve_formats(parse_formats(text)) if text.strip() else []
        except (ValueError, yaml.YAMLError) as e:
            self.yaml_error.setText(self._t("bld.form.yaml_unreadable", reason=_plain(e)))
            self.yaml_error.setVisible(True)
            return False
        self.yaml_error.setVisible(False)
        current = self.tabs.currentIndex()
        while self.tabs.count() < len(formats):
            self.tabs.addTab(FormatTab(self._person_change, self.tabs), "")
        while self.tabs.count() > len(formats):
            widget = self.tabs.widget(self.tabs.count() - 1)
            self.tabs.removeTab(self.tabs.count() - 1)
            widget.deleteLater()
        for i, values in enumerate(formats):
            tab = self.tabs.widget(i)
            tab.load(values if isinstance(values, dict) else {})
            self.tabs.setTabText(i, str(values.get("id") or "?") if isinstance(values, dict) else "?")
        if 0 <= current < self.tabs.count():
            self.tabs.setCurrentIndex(current)
        self._check()
        return True

    def formats(self) -> List[Dict[str, Any]]:
        return [self.tabs.widget(i).values() for i in range(self.tabs.count())]

    def _write(self) -> None:
        formats = self.formats()
        if self.top_extra:
            text = yaml.safe_dump({"formats": formats, **self.top_extra}, sort_keys=False, allow_unicode=True)
        else:
            text = yaml.safe_dump(formats, sort_keys=False, allow_unicode=True) if formats else ""
        if text == self._text():
            return
        self._syncing = True
        try:
            self.text_edit.setPlainText(text)
        finally:
            self._syncing = False

    def _person_change(self) -> None:
        if self._syncing:
            return
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            self.tabs.setTabText(i, str(tab.rows["id"].value() or "?"))
            tab.rows["default_quantization"].set_choices(list(tab.rows["quantizations"].value() or []))
        self._write()
        self._check()
        self.personEdited.emit()

    def _toggle_yaml(self, on: bool) -> None:
        if on:
            self._write()
            self.stack.setCurrentIndex(1)
            return
        if not self.reload():   # YAML that is not formats: stay in the advanced view with the reason
            self.chk_yaml.blockSignals(True)
            self.chk_yaml.setChecked(True)
            self.chk_yaml.blockSignals(False)
            return
        self.stack.setCurrentIndex(0)

    # --- formats ---------------------------------------------------------------------------------------------
    def add_format(self, preset: str = "") -> None:
        from orchestrator.schemas.target_template import resolve_formats
        values = resolve_formats([preset])[0] if preset else {"id": f"format{self.tabs.count() + 1}"}
        tab = FormatTab(self._person_change, self.tabs)
        tab.load(values)
        self.tabs.addTab(tab, str(values.get("id")))
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self._person_change()

    def remove_format(self) -> None:
        index = self.tabs.currentIndex()
        if index < 0:
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        widget.deleteLater()
        self._person_change()

    # --- Ditto's proposal ---------------------------------------------------------------------------------------
    def load_proposal(self, proposal: Any) -> None:
        """Ditto's proposal (Core/format_proposal.FormatProposal): its drafts fill the form (the YAML with them), and
        each value shows its reason, source and confidence beside its field; the gaps say why they are empty."""
        self.set_proposal_notes(proposal)
        text = self.proposal_text(proposal)
        self._syncing = True
        try:
            self.text_edit.setPlainText(text)
        finally:
            self._syncing = False
        self.reload()

    def proposal_text(self, proposal: Any) -> str:
        """The YAML of the formats with the proposal's drafts in place of the formats of the same id."""
        existing = {f.get("id"): f for f in self.formats()}
        merged = [d.values for d in proposal.drafts] + [f for fid, f in existing.items()
                                                         if fid not in {d.id for d in proposal.drafts}]
        return yaml.safe_dump(merged, sort_keys=False, allow_unicode=True) if merged else ""

    def set_proposal_notes(self, proposal: Any) -> None:
        """Each proposed value's reason, source and confidence (and each gap's why) beside its field."""
        self.notes = {}
        for draft in proposal.drafts:
            notes: Dict[str, str] = {}
            for path, note in draft.notes.items():
                key = path.split(".")[0] if path.split(".")[0] in ("runtime_libraries", "vendor_files") else path
                line = self._t("bld.form.proposed", value=_plain(value_text(_spec(path), note.value)),
                               source=_plain(note.source), confidence=f"{note.confidence:.1f}",
                               reason=_plain(note.reason))
                notes[key] = (notes[key] + "\n" + line) if key in notes else line
            for path, why in draft.gaps:
                key = path.split(".")[0] if path.split(".")[0] in ("runtime_libraries", "vendor_files") else path
                notes[key] = ((notes.get(key, "") + "\n") if key in notes else "") + \
                    self._t("bld.form.gap", reason=_plain(why))
            self.notes[draft.id] = notes
        self._check()

    # --- checks ------------------------------------------------------------------------------------------------
    def problems(self) -> Dict[str, Dict[str, List[str]]]:
        """The checks' reasons per format id and field path (empty: every check passed)."""
        out: Dict[str, Dict[str, List[str]]] = {}
        for i in range(self.tabs.count()):
            values = self.tabs.widget(i).values()
            found: Dict[str, List[str]] = {}
            for path in missing_required(values):
                found.setdefault(path, []).append(self._t("bld.form.required"))
            if not found:
                found = field_problems(values)
            if found:
                out[str(values.get("id") or i)] = found
        return out

    def _check(self) -> None:
        problems = self.problems()
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            fid = str(tab.rows["id"].value() or i)
            found = problems.get(fid, {})
            notes = self.notes.get(fid, {})
            for path, row in tab.rows.items():
                row.show_error(found.get(path, []))
                row.show_note(notes.get(path, ""))
            general = [r for p, rs in found.items() if p not in tab.rows for r in rs]
            tab.general.setText("\n".join(_plain(r) for r in general))
            tab.general.setVisible(bool(general))
        n = self.tabs.count()
        bad = sum(1 for _ in problems)
        self.summary.setText(self._t("bld.form.none") if not n else
                             self._t("bld.form.summary", n=n, bad=bad))


def _spec(path: str) -> FieldSpec:
    from orchestrator.Core.template_values import field_by_path
    found = field_by_path(path)
    if found is None:
        return FieldSpec(path, "text", False, "")
    spec, _index, column = found
    return column or spec
