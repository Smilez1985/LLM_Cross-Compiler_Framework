#!/usr/bin/env python3
"""
The chat panel's list of bubbles (delegation design 5.1, 5.4 and 5.9).

* The newest 100 rows are shown; "Show earlier messages" asks the panel for the page before them.
* It scrolls to a new bubble only when it was already at the bottom; otherwise a "New messages ↓" pill appears.
* "New since you last looked" marks the first row the person has not seen, with a framework summary counted from the
  rows (away_summary): what Ditto answered, what AI apps decided, results, blocked work, and what needs the person.
* Routine decisions of one app within ten minutes are folded into one group bubble (LAY-L8).
* Questions that wait come from the pending-approvals store and stay at the bottom under "Needs you".
* Keyboard: Up/Down move between bubbles, Home/End to the first and last, Escape back to the composer. New rows are
  announced to screen readers: questions assertively, everything else politely. No key approves anything.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from orchestrator.Core.chat_feed import FeedEntry, StoredEntry
from orchestrator.Core.speaking_names import SpeakingNames
from orchestrator.gui.chat_panel.bubbles import (ApprovalBubble, Bubble, GroupBubble, groupable, local_time,
                                                 make_bubble, make_button, text, text_label)
from orchestrator.gui.plain import button_text

PAGE = 100                      # rows shown at first, and per "Show earlier messages"
GROUP_MIN = 3                   # routine decisions of one app folded from this many on
GROUP_WINDOW = timedelta(minutes=10)
QUESTION_KINDS = frozenset({"blocked", "approval"})


def _moment(ts: str) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _count(lang: str, key: str, n: int) -> str:
    return text(f"chat.away.{key}.{'one' if n == 1 else 'many'}", lang, n=n)


def away_summary(entries: Iterable[FeedEntry], pending: int = 0, lang: str = "en") -> str:
    """The framework's summary of what came while the person was away, counted from feed rows only (never from what
    an AI wrote): "Since 18:02: 2 answers from Ditto, 3 decisions by AI apps, 4 results (1 failed); 1 thing needs
    you." `pending`: questions that wait now (from the pending-approvals store)."""
    rows = [e for e in entries if e.sender.role != "person"]
    answers = sum(1 for e in rows if e.kind == "ditto_answer")
    decisions = sum(1 for e in rows if e.kind == "delegated_decision")
    results = [e for e in rows if e.kind == "job_result"]
    failed = sum(1 for e in results if e.level == "warning")
    blocked = sum(1 for e in rows if e.kind == "blocked")
    notices = sum(1 for e in rows if e.kind in ("notice", "grant_used", "approval_end"))
    parts = []
    if answers:
        parts.append(_count(lang, "answers", answers))
    if decisions:
        parts.append(_count(lang, "decisions", decisions))
    if results:
        part = _count(lang, "results", len(results))
        parts.append(f"{part} ({text('chat.away.failed', lang, n=failed)})" if failed else part)
    if blocked:
        parts.append(_count(lang, "blocked", blocked))
    if notices:
        parts.append(_count(lang, "notices", notices))
    summary = ", ".join(parts) if parts else text("chat.away.nothing", lang)
    if pending:
        summary += "; " + _count(lang, "needs", pending)
    first = rows[0].ts if rows else ""
    return text("chat.away.since", lang, time=local_time(first) if first else "-", parts=summary) + "."


class _Row(QWidget):
    """One line of the list: a bubble aligned by its sender (the person right, Ditto and apps left, the framework
    over the full width), or the "new since" separator."""

    def __init__(self, bubble: Optional[Bubble] = None, separator: Optional[QWidget] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.bubble, self.separator = bubble, separator
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        widget = bubble if bubble is not None else separator
        align = bubble.style_.align if bubble is not None else "full"
        if align == "right":
            layout.addStretch(1)
            layout.addWidget(widget, stretch=6)
        elif align == "left":
            layout.addWidget(widget, stretch=6)
            layout.addStretch(1)
        else:
            layout.addWidget(widget)


class FeedView(QScrollArea):
    escape_pressed = Signal()
    earlier_requested = Signal()
    open_approval = Signal(str)

    def __init__(self, lang: str = "en", names: Optional[SpeakingNames] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang, self.names = lang, names
        self.filter = "all"
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.NoFocus)
        self.container = QWidget(self)
        self.container.setObjectName("feedContainer")
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(2, 2, 2, 2)
        self.list_layout.setSpacing(4)
        self.btn_earlier = make_button(text("chat.view.earlier", lang))
        self.btn_earlier.setVisible(False)
        self.btn_earlier.clicked.connect(self.earlier_requested.emit)
        self.list_layout.addWidget(self.btn_earlier, alignment=Qt.AlignHCenter)
        self.list_layout.addStretch(1)
        self.empty = text_label(text("chat.view.empty", lang), lang, caption=True)
        self.list_layout.addWidget(self.empty)
        self.pending_box = QWidget(self.container)
        self.pending_layout = QVBoxLayout(self.pending_box)
        self.pending_layout.setContentsMargins(0, 6, 0, 0)
        self.pending_title = text_label(text("chat.view.needs_you", lang), lang)
        self.pending_layout.addWidget(self.pending_title)
        self.pending_box.setVisible(False)
        self.list_layout.addWidget(self.pending_box)
        self.setWidget(self.container)

        self.pill = QPushButton(button_text(text("chat.view.new_messages", lang)), self)
        self.pill.setAutoDefault(False)
        self.pill.setVisible(False)
        self.pill.clicked.connect(self.scroll_to_bottom)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

        self._rows: List[_Row] = []                     # in display order (separator included)
        self._bubbles: Dict[str, Bubble] = {}           # entry id -> bubble (group members included)
        self._stored: Dict[str, StoredEntry] = {}       # entry id -> row
        self._pending: List[ApprovalBubble] = []
        self._separator: Optional[_Row] = None
        self._run: List[_Row] = []                      # trailing routine decisions of one app, not grouped yet
        self._group: Optional[_Row] = None              # the trailing group, if the last row is one

    # --- contents --------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows, self._bubbles, self._stored = [], {}, {}
        self._separator, self._run, self._group = None, [], None
        self.btn_earlier.setVisible(False)
        self._update_empty()

    def set_entries(self, entries: Iterable[StoredEntry], more: bool = False) -> None:
        self.clear()
        for stored in entries:
            self._add(stored)
        self.btn_earlier.setVisible(bool(more))
        self._update_empty()
        QTimer.singleShot(0, self.scroll_to_bottom)

    def prepend_entries(self, entries: Iterable[StoredEntry], more: bool = False) -> None:
        """Older rows ("Show earlier messages"), above the shown ones."""
        position = 0
        for stored in entries:
            if stored.entry.entry_id in self._bubbles:
                continue
            bubble = self._bubble(stored)
            row = _Row(bubble, parent=self.container)
            self._insert_row(row, position)
            position += 1
        self.btn_earlier.setVisible(bool(more))
        self._update_empty()

    def append_entries(self, entries: Iterable[StoredEntry], announce: bool = True) -> List[Bubble]:
        """New rows at the bottom (a row already shown is skipped); scrolls only when the view was at the bottom."""
        at_bottom = self.at_bottom()
        added = []
        for stored in entries:
            if stored.entry.entry_id in self._bubbles:
                continue
            added.append(self._add(stored))
        if not added:
            return []
        self._update_empty()
        if at_bottom:
            QTimer.singleShot(0, self.scroll_to_bottom)
        else:
            self._show_pill()
        if announce:
            for bubble in added:
                kind = bubble.entry.kind if bubble.entry is not None else ""
                key = f"chat.kind.{kind}"
                words = text(key, self.lang)
                self.announce(bubble.accessibleName() + (f": {words}" if words != key else ""),
                              assertive=kind in QUESTION_KINDS)
        return added

    def _bubble(self, stored: StoredEntry) -> Bubble:
        bubble = make_bubble(stored, self.lang, self.names, parent=self.container)
        bubble.navigate.connect(lambda delta, b=bubble: self.move_focus(b, delta))
        bubble.jump.connect(self.jump)
        bubble.escape.connect(self.escape_pressed.emit)
        self._bubbles[stored.entry.entry_id] = bubble
        self._stored[stored.entry.entry_id] = stored
        return bubble

    def _insert_row(self, row: _Row, position: Optional[int] = None) -> None:
        """Puts a row into the list (at `position` among the rows, default the end)."""
        position = len(self._rows) if position is None else position
        anchor = self._rows[position] if position < len(self._rows) else self.empty
        self.list_layout.insertWidget(self.list_layout.indexOf(anchor), row)
        self._rows.insert(position, row)
        if row.bubble is not None:
            row.setVisible(self._matches(row.bubble))

    def _add(self, stored: StoredEntry) -> Bubble:
        """Appends a row, folding routine decisions of one app into a group."""
        entry = stored.entry
        bubble = self._bubble(stored)
        moment = _moment(entry.ts)
        if not groupable(entry) or moment is None:
            self._run, self._group = [], None
            self._insert_row(_Row(bubble, parent=self.container))
            return bubble
        group = self._group.bubble if self._group is not None else None
        if (isinstance(group, GroupBubble) and group.caller == entry.sender.caller
                and moment - (_moment(group.first.entry.ts) or moment) <= GROUP_WINDOW):
            group.add(bubble)
            return bubble
        self._group = None
        first = self._run[0].bubble.entry if self._run else None
        if not (first is not None and first.sender.caller == entry.sender.caller
                and moment - (_moment(first.ts) or moment) <= GROUP_WINDOW):
            self._run = []
        row = _Row(bubble, parent=self.container)
        self._insert_row(row)
        self._run.append(row)
        if len(self._run) >= GROUP_MIN:
            position = self._rows.index(self._run[0])
            # The group takes the bubbles over first; only then the rows that held them go.
            grouped = GroupBubble([r.bubble for r in self._run], lang=self.lang, parent=self.container)
            for old in self._run:
                self._rows.remove(old)
                old.hide()
                old.deleteLater()
            grouped.navigate.connect(lambda delta, b=grouped: self.move_focus(b, delta))
            grouped.jump.connect(self.jump)
            grouped.escape.connect(self.escape_pressed.emit)
            self._group = _Row(grouped, parent=self.container)
            self._insert_row(self._group, position)
            self._run = []
        return bubble

    def set_pending(self, records: List[Any]) -> List[ApprovalBubble]:
        """The questions that wait now (pending-approvals store records), under "Needs you"; returns the bubbles of
        questions that were not shown before (they are announced assertively)."""
        before = {b.record.id for b in self._pending}
        for bubble in self._pending:
            bubble.setParent(None)
            bubble.deleteLater()
        self._pending = []
        new = []
        for record in records:
            bubble = ApprovalBubble(record, lang=self.lang, parent=self.pending_box)
            bubble.open_requested.connect(self.open_approval.emit)
            bubble.navigate.connect(lambda delta, b=bubble: self.move_focus(b, delta))
            bubble.jump.connect(self.jump)
            bubble.escape.connect(self.escape_pressed.emit)
            self.pending_layout.addWidget(bubble)
            self._pending.append(bubble)
            if record.id not in before:
                new.append(bubble)
        self.pending_box.setVisible(bool(self._pending) and self.filter in ("all", "needs_you"))
        for bubble in new:
            self.announce(text("chat.pending.announce", self.lang, title=bubble.record.title), assertive=True)
        self._update_empty()
        return new

    def set_separator(self, after_row_id: int, summary: str) -> bool:
        """Marks the first saved row newer than `after_row_id` with "New since you last looked" and `summary`;
        False when no shown row is newer (no mark)."""
        if self._separator is not None:
            self._rows.remove(self._separator)
            self._separator.setParent(None)
            self._separator.deleteLater()
            self._separator = None
        position = next((i for i, row in enumerate(self._rows)
                         if row.bubble is not None and 0 < row.bubble.row_id and row.bubble.row_id > after_row_id),
                        None)
        if position is None:
            return False
        box = QWidget(self.container)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 4, 0, 4)
        title = text_label(text("chat.view.since", self.lang), self.lang)
        title.setAlignment(Qt.AlignHCenter)
        layout.addWidget(title)
        layout.addWidget(text_label(summary, self.lang, caption=True))
        self._separator = _Row(separator=box, parent=self.container)
        self._insert_row(self._separator, position)
        return True

    def set_filter(self, name: str) -> None:
        self.filter = name if name in ("all", "needs_you", "decisions", "ditto") else "all"
        for row in self._rows:
            row.setVisible(self._matches(row.bubble) if row.bubble is not None else self.filter == "all")
        self.pending_box.setVisible(bool(self._pending) and self.filter in ("all", "needs_you"))
        self._update_empty()

    def _matches(self, bubble: Bubble) -> bool:
        return self.filter == "all" or bubble.category() == self.filter

    def _update_empty(self) -> None:
        shown = any(not row.isHidden() for row in self._rows if row.bubble is not None)
        self.empty.setVisible(not shown and not self._pending)

    # --- reading back ----------------------------------------------------------------------------------------------

    def stored(self) -> List[StoredEntry]:
        return list(self._stored.values())

    def stored_after(self, row_id: int) -> List[StoredEntry]:
        """Saved rows newer than `row_id`, oldest first (rows only shown here, never saved, are left out)."""
        return sorted((s for s in self._stored.values() if s.row_id > row_id), key=lambda s: s.row_id)

    def newest_row_id(self) -> int:
        return max((s.row_id for s in self._stored.values()), default=0)

    def oldest_row_id(self) -> Optional[int]:
        ids = [s.row_id for s in self._stored.values() if s.row_id > 0]
        return min(ids) if ids else None

    def bubble_of(self, entry_id: str) -> Optional[Bubble]:
        return self._bubbles.get(entry_id)

    def focus_chain(self) -> List[Bubble]:
        """The bubbles a keyboard can move between, top to bottom (members of an open group included)."""
        chain: List[Bubble] = []
        for row in self._rows:
            if row.bubble is None or row.isHidden():
                continue
            chain.append(row.bubble)
            if isinstance(row.bubble, GroupBubble) and row.bubble.expanded:
                chain.extend(row.bubble.members)
        if not self.pending_box.isHidden():
            chain.extend(self._pending)
        return chain

    def plain_text(self) -> str:
        """Everything the list shows, as text."""
        parts = []
        for row in self._rows:
            if row.isHidden():
                continue
            if row.bubble is not None:
                parts.append(row.bubble.plain_text())
            elif row.separator is not None:
                parts.append("\n".join(label.text() for label in row.separator.findChildren(type(self.empty))))
        if not self.pending_box.isHidden():
            parts.extend(b.plain_text() for b in self._pending)
        return "\n".join(p for p in parts if p)

    # --- keyboard and scrolling ------------------------------------------------------------------------------------

    def move_focus(self, bubble: Bubble, delta: int) -> None:
        chain = self.focus_chain()
        if bubble not in chain:
            return
        index = max(0, min(len(chain) - 1, chain.index(bubble) + delta))
        self.focus_bubble(chain[index])

    def jump(self, where: str) -> None:
        chain = self.focus_chain()
        if chain:
            self.focus_bubble(chain[0] if where == "first" else chain[-1])

    def focus_last(self) -> bool:
        chain = self.focus_chain()
        if chain:
            self.focus_bubble(chain[-1])
        return bool(chain)

    def focus_bubble(self, bubble: Bubble) -> None:
        bubble.setFocus(Qt.OtherFocusReason)
        self.ensureWidgetVisible(bubble)

    def at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.pill.setVisible(False)

    def _show_pill(self) -> None:
        self.pill.adjustSize()
        self._place_pill()
        self.pill.setVisible(True)
        self.pill.raise_()

    def _place_pill(self) -> None:
        width = self.pill.sizeHint().width()
        self.pill.move(max(0, (self.width() - width) // 2), max(0, self.height() - self.pill.sizeHint().height() - 8))

    def _scrolled(self, _value: int) -> None:
        if self.pill.isVisible() and self.at_bottom():
            self.pill.setVisible(False)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.pill.isVisible():
            self._place_pill()

    # --- screen readers --------------------------------------------------------------------------------------------

    def announce(self, message: str, assertive: bool = False) -> None:
        """Tells screen readers about a new row: assertive for questions, polite otherwise."""
        event = QAccessibleAnnouncementEvent(self, message)
        event.setPoliteness(QAccessible.AnnouncementPoliteness.Assertive if assertive
                            else QAccessible.AnnouncementPoliteness.Polite)
        _post_announcement(event)


def _post_announcement(event: QAccessibleAnnouncementEvent) -> None:
    """Hands an announcement to the platform's accessibility layer (a screen reader reads it)."""
    QAccessible.updateAccessibility(event)


__all__ = ["PAGE", "GROUP_MIN", "GROUP_WINDOW", "away_summary", "FeedView"]
