#!/usr/bin/env python3
"""
The chat panel's bubbles (delegation design 5.2 and 5.5): one widget per feed row, plus the bubble of a question that
waits, built from the pending-approvals store.

Rendering rules:
* R1 every text is a plain-text label (gui/plain.py); text longer than 20 lines is cut there and opens whole in a
  read-only plain-text window. No rich-text widget or call exists in this package (a source test checks it).
* R2 AI text, the app's self-reported name and every parameter of a row pass through confirmation.visible(): hidden
  characters become escapes, a parameter stays on one line, and a value from outside the framework is quoted, so a
  file name can never pose as a line of the framework's own text.
* R3 nothing is clickable inline: an address in a text is offered only in the context menu ("Open address…"), behind
  a question that shows the whole address.
* R4 the style comes from the trusted role and caller id of the row, never from its text: the person on the right,
  Ditto on the left with its avatar, an AI app on the left with the trusted connection name on line one and its own
  claim about its name small on line two, the framework over the full width with "Warning:" spelled out. An app's
  colours are picked by the hash of its caller id; every text/background pair has a contrast of at least 4.5:1.
* R5 an app that calls itself Ditto, the framework, "you" or the system is shown as calling itself so, never with
  Ditto's avatar.
* R6 AI text inside a bubble the framework wrote (a recorded reason, a note, an app's reply) sits in an AiTextBox
  with the caption "In its own words (AI text, not checked by the framework)".
* R7 exact framework ids in AI text are shown as their speaking names «like this»; the stored text is unchanged and
  "Show the original words" shows it; ids inside code keep their form and get a legend line. Guillemets the text
  holds itself are shown as escapes next to the names, so only the framework writes «name».

Bubble buttons have no mnemonics (button_text doubles every "&"), there is no default button, and Enter or Space on a
bubble does nothing: no key approves anything. Interactive question and decision bubbles are later packages: they
register their classes with `register_bubble()` and add buttons with `Bubble.add_action()`.
"""

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from orchestrator.Core.chat_feed import TEXTS as FEED_TEXTS
from orchestrator.Core.chat_feed import FeedEntry, FeedSender, StoredEntry, caller_words, feed_text
from orchestrator.Core.speaking_names import SpeakingNames
from orchestrator.gui.chat_panel.segments import blocks, has_marks, has_names, shown_blocks, split_segments
from orchestrator.gui.plain import ask, button_text, display_text, one_line, plain_label
from orchestrator.utils.localization import TRANSLATIONS, text_tables

MAX_LINES = 20                       # lines shown in a bubble before "Show all"
ASSETS = Path(__file__).resolve().parents[3] / "assets"
URL_RE = re.compile(r"https?://[^\s<>\"'«»()\[\]{}]+", re.I)
RESERVED_NAMES = ("ditto", "framework", "you", "du", "system")


# --- texts ---------------------------------------------------------------------------------------------------------

class _Fill(dict):
    """format_map values: a placeholder without a value stays visible as {name} instead of failing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def text(key: str, lang: str = "en", **values: Any) -> str:
    """A framework text: the feed's own (chat_feed.TEXTS) or the GUI's (l10n_chat and the rest), in `lang` with
    English as the fallback, placeholders filled; a key without a text is shown as the key."""
    if key in FEED_TEXTS["en"]:
        template = FEED_TEXTS.get(lang, {}).get(key) or FEED_TEXTS["en"][key]
    else:
        table = text_tables()   # German in the person's form of address, as ui_text reads it
        template = table.get(lang, {}).get(key) or table["en"].get(key) or key
    if "{path:" in template:   # a place in the window, from the live labels in `lang` (wave 6, V2)
        from orchestrator.utils.next_steps import fill_paths
        template = fill_paths(template, lang)
    return template.format_map(_Fill(values)) if values else template


def param_text(entry: FeedEntry, name: str) -> str:
    """One parameter of a row as the person reads it: one line, hidden characters escaped, and quoted when it comes
    from outside the framework (R2)."""
    value = one_line(entry.params.get(name, ""))
    return f"'{value}'" if entry.param_origin.get(name) == "external" else value


def framework_text(entry: FeedEntry, lang: str = "en") -> str:
    """The framework's own line of a row: its text key in the person's language with the parameters filled (each by
    param_text), or the generic line of the row's kind."""
    key = entry.text_key or f"chat.kind.{entry.kind}"
    return text(key, lang, **{name: param_text(entry, name) for name in entry.params})


def local_time(ts: str) -> str:
    """A row's time in the person's time zone: "14:05" today, "2026-10-11 14:05" otherwise."""
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return one_line(ts or "")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone()
    return moment.strftime("%H:%M") if moment.date() == datetime.now().astimezone().date() \
        else moment.strftime("%Y-%m-%d %H:%M")


def reserved_name(name: str) -> bool:
    """Whether an app's own name claims to be Ditto, the framework, the person or the system (R5): checked after
    NFKC normalization and case folding, so look-alike spellings count too."""
    folded = unicodedata.normalize("NFKC", name or "").casefold()
    return any(word in folded for word in RESERVED_NAMES)


def self_name_text(client: str, lang: str = "en") -> str:
    """Line two of an app's bubble: its own claim about its name, marked as such."""
    shown = one_line(client)
    if reserved_name(client):
        return text("chat.sender.reserved", lang, name=shown)
    return feed_text("caller.self_name", lang, client=shown)


def trusted_name(sender: FeedSender, lang: str = "en") -> str:
    """Line one: who the framework knows the sender as (never the app's own claim)."""
    if sender.role == "person":
        return text("chat.sender.you", lang)
    if sender.role == "framework" and not sender.caller:
        return text("chat.sender.framework", lang)
    return one_line(caller_words(sender.model_copy(update={"client": ""}), lang))


# --- styles --------------------------------------------------------------------------------------------------------

def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """The WCAG contrast ratio of two #rrggbb colours."""
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@dataclass(frozen=True)
class SenderStyle:
    """How a sender's bubbles look. `align`: right (the person), left (Ditto, an app) or full (the framework)."""
    role: str
    align: str
    background: str
    foreground: str
    caption: str
    border: str

    def sheet(self) -> str:
        return (f"QFrame#bubble {{ background-color: {self.background}; border: 1px solid {self.border}; "
                f"border-radius: 8px; }}"
                f"QFrame#bubble:focus {{ border: 2px solid #8ab4f8; }}"
                f"QLabel {{ color: {self.foreground}; background: transparent; }}"
                f"QLabel#caption {{ color: {self.caption}; font-size: 11px; }}"
                f"QFrame#aiText {{ border: 1px dashed {self.caption}; border-radius: 6px; }}")


PERSON = SenderStyle("person", "right", "#24452c", "#f2fff4", "#c3e6cb", "#3d6b47")
DITTO = SenderStyle("ditto", "left", "#1d3349", "#eef6ff", "#b9d4ee", "#3a5f85")
FRAMEWORK = SenderStyle("framework", "full", "#333333", "#f0f0f0", "#c8c8c8", "#555555")
FRAMEWORK_WARNING = SenderStyle("framework", "full", "#4a3300", "#fff1cc", "#f0d9a0", "#8a6400")
# AI apps: picked by sha256 of the caller id (never by a name an app chooses).
CLIENT_PALETTE = (
    SenderStyle("client", "left", "#3b2d4d", "#f6f0ff", "#d7c6ee", "#5e4a7a"),
    SenderStyle("client", "left", "#2c3f4a", "#eefaff", "#c2dce8", "#4a6878"),
    SenderStyle("client", "left", "#4a382a", "#fff6ee", "#e8d0bd", "#7a5c45"),
    SenderStyle("client", "left", "#2a4a3f", "#effff9", "#bfe5d6", "#457a67"),
    SenderStyle("client", "left", "#4a2a3a", "#fff0f6", "#e8c0d3", "#7a4560"),
    SenderStyle("client", "left", "#3d4a2a", "#f8fff0", "#d3e5bd", "#647a45"),
)
ALL_STYLES = (PERSON, DITTO, FRAMEWORK, FRAMEWORK_WARNING) + CLIENT_PALETTE


def sender_style(sender: FeedSender, level: str = "info") -> SenderStyle:
    """The style of a row's sender, from its trusted role and caller id only."""
    if sender.role == "person":
        return PERSON
    if sender.role == "ditto":
        return DITTO
    if sender.role == "client" and sender.caller:
        index = int(hashlib.sha256(sender.caller.encode("utf-8")).hexdigest(), 16) % len(CLIENT_PALETTE)
        return CLIENT_PALETTE[index]
    return FRAMEWORK_WARNING if level == "warning" else FRAMEWORK


# --- pieces --------------------------------------------------------------------------------------------------------

def confirm_open_link(parent: Optional[QWidget], url: str, lang: str = "en") -> bool:
    """Opens an http(s) address only after the person saw it whole and said yes (No is the default)."""
    if not re.match(r"^https?://", url or "", re.I):
        return False
    if not ask(parent, text("chat.bubble.open_address_title", lang),
               text("chat.bubble.open_address_question", lang, url=one_line(url))):
        return False
    QDesktopServices.openUrl(QUrl(url))
    return True


def text_label(value: str, lang: str = "en", *, caption: bool = False, mono: bool = False) -> QLabel:
    """A plain-text label with a context menu: Copy, Select all and, for each address in the text, "Open address…"
    (behind a question; nothing is clickable inline)."""
    label = plain_label(value)
    if caption:
        label.setObjectName("caption")
    if mono:
        label.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
    label.setContextMenuPolicy(Qt.CustomContextMenu)
    label.customContextMenuRequested.connect(lambda pos, lab=label: _label_menu(lab, pos, lang))
    return label


def _label_menu(label: QLabel, pos: Any, lang: str) -> None:
    menu = QMenu(label)
    selected = label.selectedText() or label.text()
    menu.addAction(button_text(text("chat.bubble.copy", lang))).triggered.connect(
        lambda: QGuiApplication.clipboard().setText(selected))
    menu.addAction(button_text(text("chat.bubble.select_all", lang))).triggered.connect(
        lambda: label.setSelection(0, len(label.text())))
    urls = list(dict.fromkeys(URL_RE.findall(label.text())))[:10]
    if urls:
        menu.addSeparator()
        for url in urls:
            action = menu.addAction(button_text(text("chat.bubble.open_address", lang) + " " + url))
            action.triggered.connect(lambda _=False, u=url: confirm_open_link(label, u, lang))
    menu.exec(label.mapToGlobal(pos))


def make_button(label: str) -> QPushButton:
    """A bubble button: no mnemonic, never a default button."""
    button = QPushButton(button_text(label))
    button.setAutoDefault(False)
    button.setDefault(False)
    return button


class LongTextDialog(QDialog):
    """The whole text of a long message, read-only plain text."""

    def __init__(self, title: str, body: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setPlainText(body)
        layout.addWidget(self.view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MessageBody(QWidget):
    """Text an AI wrote, rendered by R7: speaking names «like this» (guillemets of the text itself as escapes), code
    monospace with a legend, more than 20 lines cut with "Show all"; "Show the original words" switches to the stored
    text, unchanged."""

    def __init__(self, value: str, lang: str = "en", names: Optional[SpeakingNames] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.value, self.lang, self.names = value or "", lang, names
        self.segments = split_segments(self.value, names)
        self.original = False
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(4)
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        self.layout_.addWidget(self.content)
        self.btn_original: Optional[QPushButton] = None
        if has_names(self.segments) or has_marks(self.segments):
            self.btn_original = make_button(text("chat.bubble.original_words", lang))
            self.btn_original.setCheckable(True)
            self.btn_original.toggled.connect(self.show_original)
            self.layout_.addWidget(self.btn_original, alignment=Qt.AlignLeft)
        self._render()

    def show_original(self, original: bool) -> None:
        self.original = bool(original)
        if self.btn_original is not None:
            self.btn_original.setText(button_text(text(
                "chat.bubble.speaking_names" if self.original else "chat.bubble.original_words", self.lang)))
        self._render()

    def shown_text(self) -> str:
        """The text as currently shown (names or the original words), without the cut."""
        return "\n".join(b.text for b in blocks(self.segments, self.original))

    def _render(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:   # gone at once (hidden and out of the bubble), not only at the next event loop turn
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        budget = MAX_LINES
        whole = []
        cut = False
        for block in shown_blocks(self.segments, self.original):
            body = block.text   # hidden characters already escaped
            whole.append(body)
            lines = body.split("\n")
            if budget <= 0:
                cut = True
                continue
            if len(lines) > budget:
                body, cut = "\n".join(lines[:budget]) + "\n…", True
            budget -= len(lines)
            self.content_layout.addWidget(text_label(body, self.lang, mono=block.kind == "code"))
        legend = [] if self.original or self.names is None else self.names.legend(self.value)
        if legend:
            self.content_layout.addWidget(text_label(text("chat.bubble.legend", self.lang) + "\n"
                                                     + "\n".join(display_text(line) for line in legend),
                                                     self.lang, caption=True))
        if cut:
            full = "\n\n".join(whole)
            button = make_button(text("chat.bubble.show_all", self.lang, n=full.count("\n") + 1))
            button.clicked.connect(lambda: LongTextDialog(text("chat.bubble.full_text_title", self.lang), full,
                                                          self).exec())
            self.content_layout.addWidget(button, alignment=Qt.AlignLeft)

    def plain_text(self) -> str:
        return "\n".join(label.text() for label in self.content.findChildren(QLabel))


class AiTextBox(QFrame):
    """AI text inside a bubble the framework wrote (R6), with its caption and R7 rendering."""

    def __init__(self, value: str, lang: str = "en", names: Optional[SpeakingNames] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("aiText")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        self.caption = text_label(feed_text("chat.ai_text.caption", lang), lang, caption=True)
        layout.addWidget(self.caption)
        self.body = MessageBody(value, lang, names, self)
        layout.addWidget(self.body)


# --- bubbles -------------------------------------------------------------------------------------------------------

DITTO_KINDS = frozenset({"person_message", "ditto_answer", "ditto_tool_note", "explanation"})
DECISION_KINDS = frozenset({"delegated_decision", "approval", "approval_end", "grant_used", "person_action",
                            "job_result"})
NEEDS_YOU_KINDS = frozenset({"blocked"})


def category(kind: str) -> str:
    """The panel filter a row belongs to besides "All": ditto, decisions, needs_you or "" (only under All)."""
    if kind in DITTO_KINDS:
        return "ditto"
    if kind in DECISION_KINDS:
        return "decisions"
    if kind in NEEDS_YOU_KINDS:
        return "needs_you"
    return ""


class Bubble(QFrame):
    """The base of every bubble: focusable for keyboard moves between bubbles (Up/Down, Home/End, Escape back to
    the composer), an accessible name "<sender>, <time>" and description, the sender's style, and a row of action
    buttons that later packages fill (`add_action`). Enter and Space do nothing on a bubble."""

    navigate = Signal(int)   # -1: the bubble above, +1: the one below
    jump = Signal(str)       # "first" or "last"
    escape = Signal()

    def __init__(self, entry: Optional[FeedEntry] = None, *, row_id: int = 0, lang: str = "en",
                 names: Optional[SpeakingNames] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.entry, self.row_id, self.lang, self.names = entry, row_id, lang, names
        self.setObjectName("bubble")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFrameShape(QFrame.StyledPanel)
        self.style_ = sender_style(entry.sender, entry.level) if entry is not None else FRAMEWORK
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(8, 6, 8, 6)
        self.layout_.setSpacing(3)
        self.actions_row: Optional[QHBoxLayout] = None
        self.build()
        if entry is not None and row_id < 0:   # a row the panel could not save: shown here only
            self.add_caption(text("chat.bubble.local", lang))
        self.setStyleSheet(self.style_.sheet())
        self.update_accessibility()

    # what the subclasses build
    def build(self) -> None:
        if self.entry is not None:
            self.add_header()
            self.add_text(framework_text(self.entry, self.lang))

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id if self.entry is not None else ""

    def category(self) -> str:
        return category(self.entry.kind) if self.entry is not None else ""

    def sender_text(self) -> str:
        return trusted_name(self.entry.sender, self.lang) if self.entry is not None else ""

    def time_text(self) -> str:
        return local_time(self.entry.ts) if self.entry is not None else ""

    # building blocks
    def add_header(self, with_avatar: bool = False) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        if with_avatar:
            avatar = plain_label("", self, selectable=False)
            pixmap = QPixmap(str(ASSETS / "Ditto.png"))
            if not pixmap.isNull():
                avatar.setPixmap(pixmap.scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                avatar.setAccessibleName(text("chat.sender.ditto", self.lang))
                row.addWidget(avatar)
            else:
                avatar.deleteLater()
        header = text_label(f"{self.sender_text()} · {self.time_text()}", self.lang, caption=True)
        row.addWidget(header, stretch=1)
        self.layout_.addLayout(row)

    def add_caption(self, value: str) -> QLabel:
        label = text_label(value, self.lang, caption=True)
        self.layout_.addWidget(label)
        return label

    def add_text(self, value: str, *, warning: bool = False) -> QLabel:
        if warning:
            value = f"{text('chat.bubble.warning', self.lang)} {value}"
        label = text_label(value, self.lang)
        self.layout_.addWidget(label)
        return label

    def add_widget(self, widget: QWidget) -> QWidget:
        self.layout_.addWidget(widget)
        return widget

    def add_action(self, button: QPushButton) -> QPushButton:
        """Extension point: a button in the bubble's action row (made without mnemonic and never default; see
        make_button)."""
        if self.actions_row is None:
            self.actions_row = QHBoxLayout()
            self.actions_row.addStretch(1)
            self.layout_.addLayout(self.actions_row)
        button.setAutoDefault(False)
        button.setDefault(False)
        self.actions_row.insertWidget(self.actions_row.count() - 1, button)
        return button

    def plain_text(self) -> str:
        """Everything the bubble shows, as text (for the accessible description and tests)."""
        return "\n".join(label.text() for label in self.findChildren(QLabel) if label.text())

    def update_accessibility(self) -> None:
        self.setAccessibleName(text("chat.sender.accessible", self.lang, sender=self.sender_text(),
                                    time=self.time_text()))
        self.setAccessibleDescription(self.plain_text()[:1000])

    # keyboard: moves only; nothing is ever approved from a key
    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        if key == Qt.Key_Up:
            self.navigate.emit(-1)
        elif key == Qt.Key_Down:
            self.navigate.emit(1)
        elif key == Qt.Key_Home:
            self.jump.emit("first")
        elif key == Qt.Key_End:
            self.jump.emit("last")
        elif key == Qt.Key_Escape:
            self.escape.emit()
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            event.accept()   # never an approval, never a click
            return
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class PersonBubble(Bubble):
    """The person's own words, as typed (hidden characters escaped), on the right."""

    def build(self) -> None:
        self.add_header()
        if self.entry.text_key:
            self.add_caption(framework_text(self.entry, self.lang))
        if self.entry.person_text:
            self.add_text(display_text(self.entry.person_text))


class DittoBubble(Bubble):
    """Ditto's answer: its avatar, and the answer with speaking names (R7)."""

    def build(self) -> None:
        self.add_header(with_avatar=True)
        self.body = self.add_widget(MessageBody(self.entry.ai_text, self.lang, self.names, self))


class ClientBubble(Bubble):
    """An AI app's words: the trusted connection name, its own claim about its name small below (R4, R5), the
    framework's line and the app's text in an AiTextBox (R6)."""

    def build(self) -> None:
        self.add_header()
        if self.entry.sender.client:
            self.add_caption(self_name_text(self.entry.sender.client, self.lang))
        if self.entry.text_key:
            self.add_text(framework_text(self.entry, self.lang))
            self.add_caption(text("chat.bubble.written_by_framework", self.lang))
        if self.entry.ai_text:
            self.ai_box = self.add_widget(AiTextBox(self.entry.ai_text, self.lang, self.names, self))


class NoticeBubble(Bubble):
    """A notice of the framework (full width, "Warning:" spelled out), one of Ditto's tool notes, or a notice about
    what an AI app did: then under the app's trusted name, its own claim about its name small below (R4, R5), and the
    sentence marked as the framework's."""

    def build(self) -> None:
        sender = self.entry.sender
        self.add_header(with_avatar=sender.role == "ditto")
        if sender.role == "client" and sender.client:
            self.add_caption(self_name_text(sender.client, self.lang))
        self.add_text(framework_text(self.entry, self.lang), warning=self.entry.level == "warning")
        if sender.role == "client":
            self.add_caption(text("chat.bubble.written_by_framework", self.lang))


class RecordBubble(Bubble):
    """A record of what an AI did, as the framework wrote it; the AI's own words, if any, in an AiTextBox. The
    interactive versions (Yes/No, Why?, Undo) come with their own packages."""

    def build(self) -> None:
        self.add_header()
        if self.entry.sender.role == "client" and self.entry.sender.client:
            self.add_caption(self_name_text(self.entry.sender.client, self.lang))
        self.add_text(framework_text(self.entry, self.lang), warning=self.entry.level == "warning")
        if self.entry.text_key or self.entry.ai_text:
            self.add_caption(text("chat.bubble.written_by_framework", self.lang))
        self.add_warnings()
        if self.entry.ai_text:
            self.ai_box = self.add_widget(AiTextBox(self.entry.ai_text, self.lang, self.names, self))

    def add_warnings(self) -> None:
        def words(rows: List[Any]) -> str:
            known = TRANSLATIONS["en"]
            return "; ".join(one_line(text(f"warning.{r.code}", self.lang,
                                           **{k: one_line(v) for k, v in r.numbers.items()}))
                             if f"warning.{r.code}" in known else r.code.replace("_", " ") for r in rows)
        if self.entry.warnings_decided:
            self.add_caption(text("chat.record.weighed", self.lang, warnings=words(self.entry.warnings_decided)))
        if self.entry.warnings_for_person:
            self.add_caption(text("chat.record.left", self.lang, warnings=words(self.entry.warnings_for_person)))


class DecisionBubble(RecordBubble):
    """An AI's decision on its own question: who decided and the answer, written by the framework, the warnings it
    weighed and those left for the person, and its reason in an AiTextBox (R6, R7)."""

    def build(self) -> None:
        self.add_header()
        sender = self.entry.sender
        if sender.role == "client" and sender.client:
            self.add_caption(self_name_text(sender.client, self.lang))
        answer = feed_text(f"chat.answer.{self.entry.answer}", self.lang) if self.entry.answer else ""
        self.add_text(text("chat.record.decision", self.lang, who=self.sender_text(), answer=answer))
        if self.entry.text_key or self.entry.params:
            self.add_text(framework_text(self.entry, self.lang))
        self.add_caption(text("chat.bubble.written_by_framework", self.lang))
        self.add_warnings()
        if self.entry.ai_text:
            self.ai_box = self.add_widget(AiTextBox(self.entry.ai_text, self.lang, self.names, self))


class JobResultBubble(RecordBubble):
    """The end of work an AI started (framework text; "Warning:" when it failed)."""


class BlockedBubble(RecordBubble):
    """Work an AI could not do because only the person can fix what is missing (framework text)."""


class ExplanationBubble(RecordBubble):
    """An explanation of a decision: the framework's header and the explaining AI's words in an AiTextBox."""


class GroupBubble(Bubble):
    """Routine decisions of one app within ten minutes, folded into one line: "Claude Code did 6 things [Show]"."""

    def __init__(self, members: List[Bubble], *, lang: str = "en", parent: Optional[QWidget] = None):
        self.members: List[Bubble] = []
        self.first = members[0]
        self.expanded = False
        super().__init__(members[0].entry, row_id=members[0].row_id, lang=lang, names=members[0].names,
                         parent=parent)
        for member in members:
            self.add(member)

    def build(self) -> None:
        self.title = self.add_text("")
        self.btn_toggle = self.add_action(make_button(text("chat.group.show", self.lang)))
        self.btn_toggle.clicked.connect(lambda: self.set_expanded(not self.expanded))
        self.box = QWidget(self)
        self.box_layout = QVBoxLayout(self.box)
        self.box_layout.setContentsMargins(0, 4, 0, 0)
        self.box.setVisible(False)
        self.layout_.addWidget(self.box)

    @property
    def caller(self) -> str:
        return self.first.entry.sender.caller

    def category(self) -> str:
        return "decisions"

    def add(self, member: Bubble) -> None:
        member.setParent(self.box)
        self.box_layout.addWidget(member)
        self.members.append(member)
        self.title.setText(text("chat.group.title", self.lang, who=self.sender_text(), n=len(self.members)))
        self.update_accessibility()

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)
        self.box.setVisible(self.expanded)
        self.btn_toggle.setText(button_text(text("chat.group.hide" if self.expanded else "chat.group.show",
                                                 self.lang)))

    def plain_text(self) -> str:
        return self.title.text() if hasattr(self, "title") else ""


class ApprovalBubble(Bubble):
    """A question that waits for the person, built from the pending-approvals store (never from a feed row alone):
    who asks (trusted name, and the app's own claim marked as such), what, and the time left. Deciding happens in the
    approvals window for now; the inline Yes/No of design 5.5 is a later package (it adds its buttons here, from
    the signed store record)."""

    open_requested = Signal(str)   # the approval id

    def __init__(self, record: Any, *, lang: str = "en", parent: Optional[QWidget] = None):
        self.record = record
        super().__init__(None, lang=lang, parent=parent)
        self.style_ = FRAMEWORK_WARNING
        self.setStyleSheet(self.style_.sheet())

    @property
    def entry_id(self) -> str:
        return f"approval:{self.record.id}"

    def category(self) -> str:
        return "needs_you"

    def sender_text(self) -> str:
        caller = str(getattr(self.record, "caller", "") or "")
        try:
            role = "ditto" if caller == "ditto" else "client"
            return trusted_name(FeedSender(role=role, caller=caller), self.lang)
        except ValueError:
            return one_line(getattr(self.record, "caller_class", caller))

    def time_text(self) -> str:
        return local_time(str(getattr(self.record, "created", "")))

    def build(self) -> None:
        record = self.record
        self.add_caption(text("chat.pending.header", self.lang, who=self.sender_text()))
        if getattr(record, "client", ""):
            self.add_caption(self_name_text(record.client, self.lang))
        key = "chat.pending.grant_request" if getattr(record, "kind", "call") == "grant_request" \
            else "chat.pending.title"
        self.add_text(text(key, self.lang, title=one_line(record.title)), warning=True)
        self.lbl_left = self.add_caption("")
        self._left_text()
        button = self.add_action(make_button(text("chat.pending.open", self.lang)))
        button.clicked.connect(lambda: self.open_requested.emit(record.id))

    def _left_text(self) -> bool:
        """Shows the time left; True when the text changed."""
        try:
            minutes = max(1, math.ceil(self.record.seconds_left() / 60))
            value = text("chat.pending.left", self.lang, minutes=minutes)
        except Exception:  # noqa: BLE001 - a record without an expiry still shows its question
            value = ""
        changed = value != self.lbl_left.text()
        self.lbl_left.setText(value)
        self.lbl_left.setVisible(bool(value))
        return changed

    def refresh_time(self) -> None:
        """Counts the time left again (the list does this while the question waits); the accessible description
        changes only when the minutes do, so a screen reader is not told the same thing every 30 seconds."""
        if self._left_text():
            self.update_accessibility()


# --- which bubble shows which row ----------------------------------------------------------------------------------

BUBBLES: Dict[str, Type[Bubble]] = {
    "person_message": PersonBubble,
    "relay_question": PersonBubble,
    "ditto_answer": DittoBubble,
    "ditto_tool_note": NoticeBubble,
    "notice": NoticeBubble,
    "feed_cleared": NoticeBubble,
    "client_reply": ClientBubble,
    "approval": RecordBubble,
    "approval_end": RecordBubble,
    "grant_used": RecordBubble,
    "person_action": RecordBubble,
    "delegated_decision": DecisionBubble,
    "job_result": JobResultBubble,
    "blocked": BlockedBubble,
    "explanation": ExplanationBubble,
}


def register_bubble(kind: str, cls: Type[Bubble]) -> None:
    """Extension point for later packages (interactive decisions, explanations): the bubble class of a row kind."""
    if not (isinstance(cls, type) and issubclass(cls, Bubble)):
        raise TypeError("a bubble class derives from Bubble")
    BUBBLES[kind] = cls


def make_bubble(stored: StoredEntry, lang: str = "en", names: Optional[SpeakingNames] = None,
                parent: Optional[QWidget] = None) -> Bubble:
    """The bubble of one feed row. Row id > 0: saved in the feed; 0: shown from Ditto's transcript because the panel
    has no feed; < 0: the panel could not save it (the bubble says so)."""
    entry = stored.entry
    cls = BUBBLES.get(entry.kind, NoticeBubble)
    role = entry.sender.role
    if cls is DittoBubble and role != "ditto":   # Ditto's avatar and look follow the trusted sender, never the kind
        cls = ClientBubble if role == "client" else RecordBubble
    if role == "person" and cls is RecordBubble:
        cls = PersonBubble if entry.person_text else NoticeBubble
    return cls(entry, row_id=stored.row_id, lang=lang, names=names, parent=parent)


def groupable(entry: FeedEntry) -> bool:
    """A routine decision of an AI that may be folded into a group: a "yes" that left nothing for the person, or a
    use of a standing approval."""
    if not entry.sender.caller:
        return False
    if entry.kind == "delegated_decision":
        return entry.answer == "yes" and not entry.warnings_for_person
    return entry.kind == "grant_used"


__all__ = ["text", "param_text", "framework_text", "local_time", "reserved_name", "self_name_text", "trusted_name",
           "contrast_ratio", "SenderStyle", "ALL_STYLES", "CLIENT_PALETTE", "sender_style", "confirm_open_link",
           "text_label", "make_button", "LongTextDialog", "MessageBody", "AiTextBox", "category", "Bubble",
           "PersonBubble", "DittoBubble", "ClientBubble", "NoticeBubble", "RecordBubble", "DecisionBubble",
           "JobResultBubble", "BlockedBubble", "ExplanationBubble", "GroupBubble", "ApprovalBubble", "BUBBLES",
           "register_bubble", "make_bubble", "groupable"]
