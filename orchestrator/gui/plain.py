#!/usr/bin/env python3
"""
Plain-text helpers for every window that shows text the framework did not write itself (delegation design 5.2 R1,
SEC-M2).

Qt guesses whether a text is rich text (Qt.AutoText) in labels, message boxes and tooltips: a model's answer, a file
name or an app's self-reported name that starts with a tag would then be drawn as HTML, with links, images and
styles. Everything here keeps text literal:

* `plain_label()`   a QLabel in Qt.PlainText, selectable, word-wrapped, with no link interaction.
* `plain_box()`     a QMessageBox in Qt.PlainText (the static QMessageBox helpers guess the format); `ask()` is the
                    yes/no question with "No" as the default.
* `plain_tooltip()` a tooltip whose text is shown exactly as given (escaped inside a rich-text container, so Qt's
                    guess can never turn it into markup).
* `button_text()`   a button label with every "&" doubled: a name with an ampersand never becomes a keyboard
                    mnemonic (bubble buttons have none at all, so no key approves anything).
* `notify()`        a desktop notification with framework text only, markup characters replaced (some systems draw
                    a notification body as markup).
* `display_text()`  multi-line text with hidden characters shown as escapes, line by line; `one_line()` the same
                    for a value that must stay on one line (a parameter can never start a line of its own).
"""

import html
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from orchestrator.tools.confirmation import visible

# A tooltip from plain_tooltip() starts with this container; the panel's test recognizes escaped tooltips by it.
TOOLTIP_PREFIX = "<p style='white-space:pre-wrap'>"
TOOLTIP_SUFFIX = "</p>"
_MARKUP = str.maketrans({"<": "\u2039", ">": "\u203a", "&": "\uff06"})   # look-alikes: never markup on any system

KINDS = {
    "question": QMessageBox.Question,
    "information": QMessageBox.Information,
    "warning": QMessageBox.Warning,
    "critical": QMessageBox.Critical,
    "none": QMessageBox.NoIcon,
}


def display_text(value: Any) -> str:
    """`value` as the person sees it: line breaks kept, tabs kept, every other hidden character (bidi overrides,
    zero-width characters, line and paragraph separators, control characters) shown as an escape."""
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\r\n", "\n")
    return "\n".join("\t".join(visible(part) for part in line.split("\t")) for line in text.split("\n"))


def one_line(value: Any) -> str:
    """`value` on one line: every line break and hidden character shown as an escape (a parameter can never pose
    as a line of the framework's own text, design 5.2 R2)."""
    return visible(value if isinstance(value, str) else str(value))


def plain_label(text: str = "", parent: Optional[QWidget] = None, *, wrap: bool = True,
                selectable: bool = True) -> QLabel:
    """A label that shows `text` literally: plain text, never a link, selectable with mouse and keyboard."""
    label = QLabel(parent)
    label.setTextFormat(Qt.PlainText)
    label.setOpenExternalLinks(False)
    label.setWordWrap(wrap)
    flags = Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard if selectable else Qt.NoTextInteraction
    label.setTextInteractionFlags(flags)
    label.setText(text)
    return label


def _exec(box: QMessageBox) -> int:
    """Shows the box modally (tests replace this to answer without a window)."""
    return box.exec()


def plain_box(parent: Optional[QWidget], kind: str, title: str, text: str, *,
              buttons: QMessageBox.StandardButton = QMessageBox.Ok,
              default: Optional[QMessageBox.StandardButton] = None, details: str = "") -> QMessageBox.StandardButton:
    """A message box whose text is plain text, whatever it contains. `kind`: question, information, warning,
    critical or none. Returns the button the person chose (the Escape button when the box was closed)."""
    box = QMessageBox(KINDS[kind], title, "", buttons, parent if isinstance(parent, QWidget) else None)
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    if details:
        box.setDetailedText(details)   # always plain text in Qt
    if default is not None:
        box.setDefaultButton(default)
        if default == QMessageBox.No and buttons & QMessageBox.No:
            box.setEscapeButton(QMessageBox.No)
    result = _exec(box)
    try:
        return QMessageBox.StandardButton(result)
    except ValueError:
        return QMessageBox.NoButton


def ask(parent: Optional[QWidget], title: str, text: str) -> bool:
    """A yes/no question in plain text with "No" as the default and the Escape answer; True only for "Yes"."""
    answer = plain_box(parent, "question", title, text, buttons=QMessageBox.Yes | QMessageBox.No,
                       default=QMessageBox.No)
    return answer == QMessageBox.Yes


def plain_tooltip(text: Any) -> str:
    """A tooltip that shows `text` exactly: hidden characters escaped, then every character that could be markup
    escaped inside a rich-text container. setToolTip() is only ever called with this (a source test checks it)."""
    escaped = html.escape(display_text(text), quote=False)
    return f"{TOOLTIP_PREFIX}{escaped}{TOOLTIP_SUFFIX}"


def button_text(template: str, **params: Any) -> str:
    """A button label from a template and parameters (each on one line, hidden characters escaped) with every "&"
    doubled, so neither a name nor the label itself creates a keyboard mnemonic."""
    text = template.format(**{k: one_line(v) for k, v in params.items()}) if params else template
    return text.replace("&", "&&")


def notification_text(text: Any) -> str:
    """Framework text for a desktop notification: one line per line, hidden characters escaped, and the characters
    some systems read as markup replaced by look-alikes."""
    return display_text(text).translate(_MARKUP)


def notify(tray: Any, title: str, body: str) -> str:
    """Shows a desktop notification through `tray` (a QSystemTrayIcon) with framework text only; returns the body
    as shown."""
    shown = notification_text(body)
    tray.showMessage(notification_text(title), shown)
    return shown


__all__ = ["TOOLTIP_PREFIX", "display_text", "one_line", "plain_label", "plain_box", "ask", "plain_tooltip",
           "button_text", "notification_text", "notify"]
