#!/usr/bin/env python3
"""
Keyboard and screen-reader helpers (wave 6, f-words; design C.7).

* `accessible_text(label)`        a label's text as a screen reader should say it: the mnemonic "&" removed ("&&"
                                  stays one "&"), a trailing ":" removed.
* `pair(label, field)`            the label names the field: setBuddy (its mnemonic focuses the field) and the
                                  field's accessible name, unless it has one.
* `name_icon_button(button, name, tip=None)`
                                  an icon-only button gets an accessible name and a plain tooltip.
* `accessibility_problems(root)`  for tests and the final lint: icon-only buttons without a name, fields
                                  (line edits, combo boxes, spin boxes, text edits) without an accessible name and
                                  without a label that names them, buttons that are not in the tab order, and labels
                                  marked with glossary terms (gui/explain.py) whose card no key opens (no buddy, no
                                  "?" button or field marked with the terms, no marked group box around a field).

Mnemonics in labels: plain.mnemonic_text() keeps the "&" of a fixed label template and escapes it in inserted values.
"""

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QPlainTextEdit,
                               QTextEdit, QWidget)

from orchestrator.gui.plain import plain_tooltip

FIELDS = (QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit)


def accessible_text(label: str) -> str:
    """`label` without its mnemonic marker and trailing colon: "&Language:" -> "Language", "Q && A" -> "Q & A"."""
    text = (label or "").replace("&&", "\x00").replace("&", "").replace("\x00", "&").strip()
    return text[:-1].rstrip() if text.endswith(":") else text


def pair(label: QLabel, field: QWidget) -> QWidget:
    """`label` names `field`: its buddy, and the field's accessible name when it has none. Returns the field."""
    label.setBuddy(field)
    if not field.accessibleName():
        field.setAccessibleName(accessible_text(label.text()))
    return field


def name_icon_button(button: QAbstractButton, name: str, tip: Optional[str] = None) -> QAbstractButton:
    """An icon-only button with an accessible name and a plain tooltip (the name when `tip` is not given)."""
    button.setAccessibleName(name)
    button.setToolTip(plain_tooltip(tip or name))
    return button


def _named_by_label(root: QWidget) -> set:
    return {id(label.buddy()) for label in root.findChildren(QLabel) if label.buddy() is not None}


def _terms(widget: QWidget, prop: str) -> List[str]:
    value = widget.property(prop)
    return [str(t) for t in value] if isinstance(value, (list, tuple)) else []


def _unreachable_terms(label: QLabel, root: QWidget, reachable: set) -> List[str]:
    """The glossary terms of a marked label that no key opens: not through a buddy, not through a focusable widget
    marked with them (a "?" button, a field), not through a marked parent that holds a focusable field (F1 in the
    field goes on to the parent)."""
    from orchestrator.gui.explain import TERMS_PROPERTY
    terms = _terms(label, TERMS_PROPERTY)
    if not terms or label.buddy() is not None:
        return []
    parent = label.parentWidget()
    while parent is not None:
        if set(terms) <= set(_terms(parent, TERMS_PROPERTY)) and any(
                w.focusPolicy() != Qt.NoFocus for w in parent.findChildren(QWidget)):
            return []
        if parent is root:
            break
        parent = parent.parentWidget()
    return [t for t in terms if t not in reachable]


def accessibility_problems(root: QWidget) -> List[str]:
    """What keyboard and screen-reader users would miss in `root`, one line per widget (empty when nothing)."""
    from orchestrator.gui.explain import F1_PROPERTY, TERMS_PROPERTY
    found = []
    named = _named_by_label(root)
    reachable = {t for w in root.findChildren(QWidget) if w.focusPolicy() != Qt.NoFocus
                 for t in _terms(w, TERMS_PROPERTY) + _terms(w, F1_PROPERTY)}
    for widget in root.findChildren(QWidget):
        name = widget.objectName() or type(widget).__name__
        if isinstance(widget, QAbstractButton):
            if not widget.text().strip() and not widget.accessibleName():
                found.append(f"{name}: a button without text needs an accessible name")
            if widget.focusPolicy() == Qt.NoFocus:
                found.append(f"{name}: a button out of the tab order")
        elif isinstance(widget, FIELDS) and widget.parent() is not None and not isinstance(widget.parent(), FIELDS):
            if not widget.accessibleName() and id(widget) not in named:
                found.append(f"{name}: a field without a label or an accessible name")
        elif isinstance(widget, QLabel):
            missing = _unreachable_terms(widget, root, reachable)
            if missing:
                found.append(f"{name}: a marked label that no key reaches needs a buddy or a help button "
                             f"({', '.join(missing)})")
    return found


__all__ = ["FIELDS", "accessibility_problems", "accessible_text", "name_icon_button", "pair"]
