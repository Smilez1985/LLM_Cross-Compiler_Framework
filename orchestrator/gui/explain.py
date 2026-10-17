#!/usr/bin/env python3
"""
Technical terms explained on mouse-over (wave 6, f-words; DECISIONS round 10, V6).

A marked widget shows a hover card when the mouse rests on it: for each term the term itself, one or two plain
sentences in the person's language and a link to the page that explains it best (utils/l10n_glossary.py). The card is
a small window of its own, not a QToolTip: it stays while the mouse is on the widget or on the card, so the link can
be clicked; it goes away a moment after the mouse left both. A link opens in the web browser only when the person
clicks it, and only a link of the glossary (https). Every text is plain text.

For the keyboard: F1 on a marked widget (or on the field of a marked label) opens the same card as a popup that takes
the focus (Escape closes it), and help_button() gives a focusable "?" button that does the same. Screen readers read
the explanation and the link from the widget's accessible description.

API for the windows (each area chain marks the terms of its own windows):

    explain(widget, "quantization")            mark one widget with one or more glossary ids
    term_label("Quantization:", "quantization", buddy=combo)
                                               a plain label marked with its term, the buddy of `buddy`
    help_button("kv_cache")                    a "?" button that opens the card
    mark_terms(widget)                         find the terms in a widget's own text and tooltip and mark them
    mark_terms_in(window)                      the same for every label, button and group box of a window

The widget's own tooltip stays its first line in the card (the card replaces the plain tooltip on that widget).
"""

import logging
from typing import Iterable, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QFont, QGuiApplication, QPalette, QTextDocument
from PySide6.QtGui import Qt as GuiQt   # Qt.mightBeRichText lives in QtGui
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QGroupBox, QLabel, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, plain_label, plain_tooltip
from orchestrator.utils import glossary
from orchestrator.utils.l10n_glossary import TERMS
from orchestrator.utils.localization import current_language, ui_text

logger = logging.getLogger(__name__)

SHOW_DELAY_MS = 500     # the mouse rests this long on a marked widget before the card shows
HIDE_DELAY_MS = 400     # time to move the mouse from the widget onto the card
CARD_WIDTH = 380
TERMS_PROPERTY = "glossary_terms"       # the glossary ids a widget is marked with
F1_PROPERTY = "glossary_f1_terms"       # the ids F1 opens on a marked label's field
LANG_PROPERTY = "glossary_lang"
URL_PROPERTY = "glossary_url"
OWN_DESCRIPTION_PROPERTY = "glossary_own_description"   # the widget's description before it was marked


def _open_url(url: str) -> bool:
    """Opens `url` in the web browser (tests replace this)."""
    return QDesktopServices.openUrl(QUrl(url))


def open_link(url: str) -> bool:
    """Opens a glossary link in the web browser; any other address (or one that is not https) is refused."""
    known = {u for t in TERMS.values() for u in (t.link, t.link_de) if u}
    if url not in known or not url.startswith("https://"):
        logger.warning("Refused to open a link that is not in the glossary.")
        return False
    return bool(_open_url(url))


def _alive(widget: QObject) -> bool:
    """Whether the Qt object behind `widget` still exists (a window may be gone while a timer still names it)."""
    try:
        import shiboken6
        return shiboken6.isValid(widget)
    except ImportError:  # pragma: no cover - PySide6 always ships shiboken6
        return True


def _terms_of(widget: QWidget, prop: str = TERMS_PROPERTY) -> List[str]:
    value = widget.property(prop)
    return [str(t) for t in value] if isinstance(value, (list, tuple)) else []


def _tooltip_text(widget: QWidget) -> str:
    """The widget's own tooltip as plain text, read the way Qt reads it: rich text (plain_tooltip wraps a text in
    escaped rich text) is turned into its words, any other tooltip stays exactly as it is ("Size < 4 GB")."""
    tip = widget.toolTip()
    if not tip:
        return ""
    if not GuiQt.mightBeRichText(tip):
        return tip.strip()
    document = QTextDocument()
    document.setHtml(tip)
    return document.toPlainText().strip()


def description(terms: Iterable[str], lang: Optional[str] = None) -> str:
    """What a screen reader reads for marked terms: each explanation with its link."""
    return " ".join(ui_text("words.card.described", lang, explanation=glossary.explanation(t, lang),
                            url=glossary.link(t, lang)) for t in terms)


def _own_description(widget: QWidget) -> str:
    """What the window itself says about `widget` for screen readers, remembered the first time it is marked: its
    accessible description, else its tooltip (Qt reads the tooltip only while no description is set, so marking must
    not silence it)."""
    own = widget.property(OWN_DESCRIPTION_PROPERTY)
    if own is None:
        own = widget.accessibleDescription() or _tooltip_text(widget)
        widget.setProperty(OWN_DESCRIPTION_PROPERTY, own)
    return str(own)


def _describe(widget: QWidget, terms: List[str], lang: Optional[str]) -> None:
    """The widget's own description first, then the explanation and link of each term."""
    widget.setAccessibleDescription(" ".join(filter(None, (_own_description(widget), description(terms, lang)))))


class HoverCard(QFrame):
    """The card for `terms` (glossary ids): the term, its explanation and a link button per term, after the widget's
    own tooltip text (`context`). `pinned`: opened by F1 or a "?" button, a popup that takes the focus."""

    def __init__(self, terms: Iterable[str], *, context: str = "", lang: Optional[str] = None, pinned: bool = False):
        flags = (Qt.Popup if pinned else Qt.ToolTip) | Qt.FramelessWindowHint
        super().__init__(None, flags)
        self.terms = [t for t in terms if t in TERMS]
        self.lang = lang or current_language()
        self.pinned = pinned
        self.setObjectName("glossary_hover_card")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not pinned)
        self.setFrameShape(QFrame.Box)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ToolTipBase)
        self.setForegroundRole(QPalette.ToolTipText)
        self.setFixedWidth(CARD_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.links: List[QPushButton] = []
        if context:
            layout.addWidget(self._label(context))
        for term_id in self.terms:
            entry = glossary.term(term_id)
            title = self._label(entry.shown)
            font = QFont(title.font())
            font.setBold(True)
            title.setFont(font)
            layout.addWidget(title)
            layout.addWidget(self._label(glossary.explanation(term_id, self.lang)))
            layout.addWidget(self._link(term_id), 0, Qt.AlignLeft)
        names = ", ".join(glossary.term(t).shown for t in self.terms)
        self.setAccessibleName(ui_text("words.card.name", self.lang, term=names))
        self.adjustSize()

    def _label(self, text: str) -> QLabel:
        label = plain_label(text, self, selectable=False)
        label.setForegroundRole(QPalette.ToolTipText)
        return label

    def _link(self, term_id: str) -> QPushButton:
        url = glossary.link(term_id, self.lang)
        shown = ui_text("words.card.more", self.lang, source=glossary.source_label(url))
        button = QPushButton(button_text(shown + " ↗"), self)   # ↗: it leaves the program
        button.setObjectName(f"glossary_link_{term_id}")
        button.setFlat(True)
        button.setAutoDefault(False)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.StrongFocus)
        font = QFont(button.font())
        font.setUnderline(True)
        button.setFont(font)
        palette = button.palette()
        palette.setColor(QPalette.ButtonText, palette.color(QPalette.Link))
        button.setPalette(palette)
        button.setToolTip(plain_tooltip(ui_text("words.card.more_tip", self.lang, url=url)))
        button.setAccessibleName(shown)
        button.setAccessibleDescription(url)
        button.setProperty(URL_PROPERTY, url)
        button.clicked.connect(lambda _=False, u=url: self._clicked(u))
        self.links.append(button)
        return button

    def _clicked(self, url: str) -> None:
        open_link(url)
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def show_near(self, widget: QWidget) -> None:
        """Shows the card right below `widget` (above it when there is no room below), inside the screen."""
        self.adjustSize()
        below = widget.mapToGlobal(QPoint(0, widget.height() + 2))
        screen = widget.screen() or QGuiApplication.primaryScreen()
        x, y = below.x(), below.y()
        if screen is not None:
            area = screen.availableGeometry()
            if y + self.height() > area.bottom():
                y = widget.mapToGlobal(QPoint(0, 0)).y() - self.height() - 2
            x = max(area.left(), min(x, area.right() - self.width()))
            y = max(area.top(), y)
        self.move(x, y)
        self.show()
        if self.pinned and self.links:
            self.links[0].setFocus(Qt.PopupFocusReason)


class _HoverController(QObject):
    """Watches every marked widget and the one card: shows it after the mouse rested, keeps it while the mouse is on
    the widget or the card, hides it after the mouse left both."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.card: Optional[HoverCard] = None
        self.owner: Optional[QWidget] = None
        self.pending: Optional[QWidget] = None
        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self._show_pending)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.close_card)

    def watch(self, widget: QWidget) -> None:
        if not widget.property("glossary_watched"):
            widget.setProperty("glossary_watched", True)
            widget.installEventFilter(self)
            widget.destroyed.connect(self._widget_gone)

    def _widget_gone(self, *_args) -> None:
        """A watched widget was deleted (its window closed): forget it, and its card goes too."""
        if self.pending is not None and not _alive(self.pending):
            self.show_timer.stop()
            self.pending = None
        if self.owner is not None and not _alive(self.owner):
            self.close_card()

    def show_for(self, widget: QWidget, *, pinned: bool = False, terms: Optional[List[str]] = None) -> HoverCard:
        self.show_timer.stop()
        self.hide_timer.stop()
        self.close_card()
        terms = terms if terms is not None else _terms_of(widget)
        lang = widget.property(LANG_PROPERTY) or None
        card = HoverCard(terms, context=_tooltip_text(widget), lang=lang, pinned=pinned)
        card.installEventFilter(self)
        card.destroyed.connect(lambda *_: self._forget(card))
        self.card, self.owner = card, widget
        card.show_near(widget)
        return card

    def _forget(self, card: HoverCard) -> None:
        if self.card is card:
            self.card, self.owner = None, None

    def close_card(self) -> None:
        self.hide_timer.stop()
        card, self.card, self.owner = self.card, None, None
        if card is not None:
            try:
                card.close()
            except RuntimeError:   # already deleted by Qt
                pass

    def _show_pending(self) -> None:
        widget, self.pending = self.pending, None
        if widget is not None and _alive(widget) and widget.isVisible() and _terms_of(widget):
            self.show_for(widget)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        kind = event.type()
        if self.card is not None and obj is self.card:
            if kind == QEvent.Enter:
                self.hide_timer.stop()
            elif kind == QEvent.Leave and not self.card.pinned:
                self.hide_timer.start(HIDE_DELAY_MS)
            return False
        if not isinstance(obj, QWidget):
            return False
        if kind == QEvent.KeyPress and event.key() == Qt.Key_F1:
            terms = _terms_of(obj) or _terms_of(obj, F1_PROPERTY)
            if terms:
                self.show_for(obj, pinned=True, terms=terms)
                return True
            return False
        if not _terms_of(obj):
            return False
        if kind == QEvent.ToolTip:   # the person rested on it: the card instead of the plain tooltip
            if self.owner is not obj:
                self.show_for(obj)
            return True
        if kind == QEvent.Enter:
            self.hide_timer.stop()
            if self.owner is not obj:
                self.pending = obj
                self.show_timer.start(SHOW_DELAY_MS)
        elif kind == QEvent.Leave:
            if self.pending is obj:
                self.show_timer.stop()
                self.pending = None
            if self.owner is obj and self.card is not None and not self.card.pinned:
                self.hide_timer.start(HIDE_DELAY_MS)
        elif kind in (QEvent.MouseButtonPress, QEvent.Hide, QEvent.DeferredDelete):
            if self.pending is obj:
                self.show_timer.stop()
                self.pending = None
            if self.owner is obj:
                self.close_card()
        return False


_controller: Optional[_HoverController] = None


def hover_controller() -> _HoverController:
    """The one controller of the application's hover cards."""
    global _controller
    if _controller is None:
        _controller = _HoverController(QApplication.instance())
        _controller.destroyed.connect(_reset_controller)
    return _controller


def _reset_controller(*_args) -> None:
    global _controller
    _controller = None


def explain(widget: QWidget, *terms: str, lang: Optional[str] = None) -> QWidget:
    """Marks `widget` with glossary terms (ids from utils/l10n_glossary.py; an unknown id raises KeyError): the hover
    card on mouse-over, the popup card on F1, the explanation in its accessible description. Returns the widget."""
    if not terms:
        raise ValueError("explain() needs at least one glossary id")
    for term_id in terms:
        glossary.term(term_id)
    marked = list(dict.fromkeys(_terms_of(widget) + list(terms)))
    widget.setProperty(TERMS_PROPERTY, marked)
    if lang:
        widget.setProperty(LANG_PROPERTY, lang)
    _describe(widget, marked, lang or widget.property(LANG_PROPERTY) or None)
    if isinstance(widget, QLabel):
        widget.setCursor(Qt.WhatsThisCursor)   # the usual sign that help is there
        buddy = widget.buddy()
        if buddy is not None:
            _mark_buddy(buddy, marked, lang)
    hover_controller().watch(widget)
    return widget


def _mark_buddy(buddy: QWidget, terms: List[str], lang: Optional[str]) -> None:
    buddy.setProperty(F1_PROPERTY, terms)
    if lang:
        buddy.setProperty(LANG_PROPERTY, lang)
    if not _terms_of(buddy):   # a field marked itself describes its own terms
        _describe(buddy, terms, lang)
    hover_controller().watch(buddy)


def term_label(text: str, *terms: str, parent: Optional[QWidget] = None, buddy: Optional[QWidget] = None,
               lang: Optional[str] = None) -> QLabel:
    """A plain-text label marked with `terms`; with `buddy`, the label names that field (its mnemonic focuses it,
    F1 on the field opens the card, and the field's accessible name is the label's text)."""
    label = QLabel(parent)
    label.setTextFormat(Qt.PlainText)
    label.setText(text)
    if buddy is not None:
        label.setBuddy(buddy)
        if not buddy.accessibleName():
            from orchestrator.gui.accessible import accessible_text
            buddy.setAccessibleName(accessible_text(text))
    return explain(label, *terms, lang=lang)


def help_button(term_id: str, parent: Optional[QWidget] = None, lang: Optional[str] = None) -> QToolButton:
    """A small focusable "?" button for `term_id`: the card on mouse-over, the popup card on a click or Space."""
    shown = glossary.term(term_id).shown
    button = QToolButton(parent)
    button.setText("?")
    button.setObjectName(f"help_{term_id}")
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.StrongFocus)
    button.setCursor(Qt.WhatsThisCursor)
    button.setAccessibleName(ui_text("words.help.name", lang, term=shown))
    button.setToolTip(plain_tooltip(ui_text("words.help.tip", lang, term=shown)))
    explain(button, term_id, lang=lang)
    button.clicked.connect(lambda _=False: hover_controller().show_for(button, pinned=True))
    return button


def _own_text(widget: QWidget) -> str:
    if isinstance(widget, QGroupBox):
        return widget.title()
    if isinstance(widget, (QLabel, QAbstractButton)):
        return widget.text().replace("&&", "\x00").replace("&", "").replace("\x00", "&")
    return ""


def mark_terms(widget: QWidget, lang: Optional[str] = None) -> List[str]:
    """Marks `widget` with the glossary terms its own text and tooltip name (only terms that may be found
    automatically); returns the ids, empty when there are none."""
    found = glossary.find_terms(_own_text(widget) + "\n" + _tooltip_text(widget))
    if found:
        explain(widget, *found, lang=lang)
    return found


def mark_terms_in(root: QWidget, lang: Optional[str] = None) -> int:
    """mark_terms for every label, button and group box inside `root` (not the hover cards); returns how many
    widgets were marked."""
    count = 0
    for widget in root.findChildren(QWidget):
        if isinstance(widget, (QLabel, QAbstractButton, QGroupBox)) and not isinstance(widget.window(), HoverCard):
            count += bool(mark_terms(widget, lang))
    return count


def card_under_mouse() -> bool:
    """Whether the mouse is on the card that shows now (for a window that wants to keep it open)."""
    card = hover_controller().card
    return card is not None and card.isVisible() and card.geometry().contains(QCursor.pos())


__all__ = ["CARD_WIDTH", "HIDE_DELAY_MS", "HoverCard", "SHOW_DELAY_MS", "TERMS_PROPERTY", "card_under_mouse",
           "description", "explain", "help_button", "hover_controller", "mark_terms", "mark_terms_in", "open_link",
           "term_label"]
