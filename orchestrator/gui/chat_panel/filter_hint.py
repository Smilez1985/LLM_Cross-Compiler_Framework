#!/usr/bin/env python3
"""
The filter notice of the chat panel (docs/DESIGN.md, "The person is told, quietly and exactly, what was filtered out").

`describe` words a FilterReport (llm/filter_report.py) in the person's language: one entry per place with its kinds and
counts, never a value. `notice_text` is the plain text of the symbol's mouse-over. `mark` underlines, in the composer's
plain-text field, exactly the stretches the filter will replace (`FilterReport.stretches`), with a wavy line, not a
colour alone; `unmark` takes the marks away. No rich text anywhere: the marks are character formats of a plain-text
document (extra selections), and every text is plain text.
"""

from typing import List, Sequence, Tuple

from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

from orchestrator.llm.filter_report import HIDDEN_FOR_GOOD, PLACES, FilterReport
from orchestrator.utils.localization import ui_text

KIND_KEYS = {"IP", "MAC", "EMAIL", "USER", "HOST", "SERIAL", "UUID", "PATH"}


def kind_text(kind: str, count: int, lang: str) -> str:
    """A kind of value in words and number: personal data by the framework's words, a secret by the name of its rule."""
    which = "one" if count == 1 else "many"
    if kind in KIND_KEYS:
        return ui_text(f"chat.filter.kind.{kind}.{which}", lang)
    if kind == HIDDEN_FOR_GOOD:
        return ui_text(f"chat.filter.kind.hidden.{which}", lang)
    return kind if count == 1 or kind.endswith("s") else kind + "s"


def describe(report: FilterReport, lang: str = "en") -> List[str]:
    """One line per place, in the order of a request: 'your input: 1 password flag (SECRET_1); 2 IP addresses'."""
    lines = []
    rows = report.counts()
    for place in PLACES:
        parts = [f"{n} {kind_text(kind, n, lang)}" + (f" ({', '.join(labels)})" if labels else "")
                 for p, kind, n, labels in rows if p == place]
        if parts:
            lines.append(f"{ui_text('chat.filter.place.' + place, lang)}: {'; '.join(parts)}")
    return lines


def restored_text(report: FilterReport, lang: str = "en") -> str:
    """What was put back in the answer, in words; '' when nothing was."""
    data = report.as_data()["restored"]
    if not data:
        return ""
    return "; ".join(f"{n} {kind_text(kind, n, lang)}" for kind, n in sorted(data.items()))


def notice_text(report: FilterReport, lang: str, *, preview: bool) -> str:
    """The mouse-over text: the whole list by place, and, after the answer, what was put back; plain text."""
    lines = describe(report, lang)
    key = "chat.filter.preview" if preview else "chat.filter.sent"
    text = ui_text(key, lang, list="\n  " + "\n  ".join(lines) + "\n")
    back = restored_text(report, lang)
    if back and not preview:
        text += "\n" + ui_text("chat.filter.back", lang, list=back)
    return text + "\n" + ui_text("chat.filter.dismiss", lang)


def _units(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def mark(field: QPlainTextEdit, stretches: Sequence[Tuple[int, int, str]]) -> int:
    """Underlines the stretches (start, end, kind) of the plain-text field with a wavy line; returns how many."""
    selections = []
    text = field.toPlainText()
    for start, end, _kind in stretches:
        cursor = QTextCursor(field.document())
        cursor.setPosition(_units(text, start))   # Qt counts UTF-16 code units, Python counts characters
        cursor.setPosition(_units(text, end), QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.WaveUnderline)
        selection = QTextEdit.ExtraSelection()
        selection.cursor, selection.format = cursor, fmt
        selections.append(selection)
    field.setExtraSelections(selections)
    return len(selections)


def unmark(field: QPlainTextEdit) -> None:
    field.setExtraSelections([])


__all__ = ["describe", "kind_text", "mark", "notice_text", "restored_text", "unmark"]
