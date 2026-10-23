#!/usr/bin/env python3
"""
The chat panel (delegation design 5.1, 5.3, 5.6 and 5.9): the header with the access line, the filter and the "⋯"
menu, the list of bubbles and the composer. It owns Ditto's conversation (ChatHistory), the confirmation dialog of
Ditto's tool calls (ToolConfirmation, which Ditto's turn waits on), the worker that runs a turn, the feed watcher and
the link to the pending approvals.

What it shows comes from the signed chat feed (Core/chat_feed.py): the person's messages, Ditto's answers and tool
notes, notices, and the records of what AI apps did, written by any framework process. Ditto's earlier transcript is
imported into the feed once. Without a feed (it cannot be opened) the panel shows Ditto's transcript as before and
says that messages are not kept. Questions that wait come from the pending-approvals store, never from feed rows
alone.

"Clear chat" asks first, listing for every file its full path, file name, size and what it is (Ditto's conversation
in chat_history.json and the conversation rows of the feed, from the feed's ClearPlan); "No" is the default. The
records of what AIs did are hidden from the panel, not deleted.
"""

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QMenu, QProgressBar, QToolButton,
                               QVBoxLayout, QWidget)

from orchestrator.Core.chat_feed import (ClearPlan, ClearPlanChanged, FeedRefs, FeedSender, FeedWriteError,
                                         FeedWriter, StoredEntry, import_chat_history)
from orchestrator.Core.ditto_manager import ChatHistory
from orchestrator.Core.speaking_names import SpeakingNames
from orchestrator.gui.chat_panel.bubbles import make_button, text, text_label
from orchestrator.gui.chat_panel.composer import Composer
from orchestrator.gui.chat_panel.feed_view import PAGE, FeedView, away_summary
from orchestrator.gui.chat_panel.question_options import QuestionChoice, QuestionOptions
from orchestrator.gui.chat_panel.watcher import FeedWatcher
from orchestrator.gui.plain import button_text, one_line, plain_box, plain_tooltip
from orchestrator.gui.tool_confirmation import ToolConfirmation
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.announce import announce
from orchestrator.utils.localization import current_language, ui_text
from orchestrator.utils.localization import get_instance as get_i18n
from orchestrator.utils.logging import get_logger

logger = get_logger("ChatPanel")

GUI_WRITE_TIMEOUT = 0.5    # seconds a write from the GUI waits for another process's lock
GUI_READ_TIMEOUT = 0.5     # the first page; later reads (the watcher) wait 0.2 s
LOAD_TRIES = 5             # a busy feed at start is read again once a second, this many times
FILTERS = ("all", "needs_you", "decisions", "ditto")


def holds_conversation(data: bytes) -> bool:
    """Whether the bytes of chat_history.json hold a conversation: any message or transcript line, an old plain list
    with entries, or anything that cannot be read (clearing would replace it, so it is listed too)."""
    if not data.strip():
        return False
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return True
    if isinstance(raw, list):
        return bool(raw)
    if isinstance(raw, dict):
        return bool(raw.get("messages") or raw.get("transcript"))
    return True


class HistoryNotListable(OSError):
    """This window holds a conversation of Ditto that its file does not show (the file is gone or cannot be read):
    it cannot be listed, so "Clear chat" deletes nothing."""


def size_text(size: int, lang: str = "en") -> str:
    """A size as the person reads it, with the exact bytes."""
    if size < 1024:
        return text("chat.size.bytes", lang, n=size)
    if size < 1024 * 1024:
        return text("chat.size.kb", lang, kb=f"{size / 1024:.1f}", n=f"{size:,}")
    return text("chat.size.mb", lang, mb=f"{size / 1024 / 1024:.1f}", n=f"{size:,}")


@dataclass(frozen=True)
class HistoryItem:
    """Ditto's conversation file as "Clear chat" lists it; `content_sha256` binds the clear to what was shown."""
    full_path: str
    file_name: str
    size_bytes: int
    content_sha256: str

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode("utf-8")).hexdigest()


class _Collector:
    """Stands in for the feed when the panel has none: takes the rows import_chat_history would append."""

    def __init__(self):
        self.entries: List[Any] = []

    def append_many(self, entries, *, timeout=None, once=""):
        self.entries = list(entries)
        return self.entries


class ClearChatDialog(QDialog):
    """The question before "Clear chat": for each file its name, full path, file name, size and what it is, the
    records that are only hidden, and "No, keep everything" as the default button."""

    def __init__(self, parent: Optional[QWidget], history: Optional[HistoryItem], plan: Optional[ClearPlan],
                 lang: str = "en"):
        super().__init__(parent)
        self.setWindowTitle(text("chat.clear.title", lang))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label(text("chat.clear.intro", lang), lang))
        if history is not None:
            layout.addWidget(self._item(text("chat.clear.history_name", lang), history.full_path, history.file_name,
                                        size_text(history.size_bytes, lang), text("chat.clear.history_what", lang),
                                        lang))
        if plan is not None and plan.rows:
            layout.addWidget(self._item(text("chat.clear.feed_name", lang, rows=plan.rows), plan.full_path,
                                        plan.file_name, size_text(plan.size_bytes, lang),
                                        f"{plan.what(lang)}. {text('chat.clear.feed_stays', lang)}", lang))
        if plan is not None and plan.hidden_records:
            key = "chat.clear.hidden.one" if plan.hidden_records == 1 else "chat.clear.hidden.many"
            layout.addWidget(text_label(text(key, lang, n=plan.hidden_records), lang))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_yes = make_button(text("chat.clear.yes", lang))
        self.btn_no = make_button(text("chat.clear.no", lang))
        self.btn_no.setAutoDefault(True)
        self.btn_no.setDefault(True)            # Enter keeps everything
        self.btn_yes.clicked.connect(self.accept)
        self.btn_no.clicked.connect(self.reject)
        buttons.addWidget(self.btn_yes)
        buttons.addWidget(self.btn_no)
        layout.addLayout(buttons)
        self.btn_no.setFocus(Qt.OtherFocusReason)

    @staticmethod
    def _item(name: str, path: str, file_name: str, size: str, what: str, lang: str) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        box = QVBoxLayout(frame)
        title = text_label(name, lang)
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        box.addWidget(title)
        box.addWidget(text_label(text("chat.clear.path", lang, path=one_line(path)), lang, mono=True))
        box.addWidget(text_label(text("chat.clear.file", lang, name=one_line(file_name)), lang))
        box.addWidget(text_label(text("chat.clear.size", lang, size=size), lang))
        box.addWidget(text_label(text("chat.clear.what_label", lang, what=what), lang))
        return frame

    def plain_text(self) -> str:
        from PySide6.QtWidgets import QLabel
        return "\n".join(label.text() for label in self.findChildren(QLabel))


class PanelHeader(QWidget):
    """Title, the access line Ditto uses, the filter (All / Needs you (n) / Decisions / Ditto chat), "Clear chat…"
    and the "⋯" menu."""

    def __init__(self, lang: str = "en", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang = lang
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        top = QHBoxLayout()
        self.title = text_label(text("chat.panel.heading", lang), lang)
        font = QFont(self.title.font())
        font.setBold(True)
        self.title.setFont(font)
        top.addWidget(self.title, stretch=1)
        # Ditto is optional and experimental (eighth round, Q-2): the badge stays; the notice below is shown once.
        self.badge = text_label(text("chat.ditto.badge", lang), lang, caption=True)
        self.badge.setStyleSheet("color: #1e1e1e; background-color: #ffd27f; border-radius: 3px; padding: 0 4px;")
        self.badge.setToolTip(plain_tooltip(text("chat.ditto.badge_tip", lang)))
        self.badge.setAccessibleName(text("chat.ditto.badge_tip", lang))
        top.addWidget(self.badge)
        self.btn_clear = make_button(text("chat.clear.button", lang))
        self.btn_clear.setToolTip(plain_tooltip(text("chat.clear.tip", lang)))
        top.addWidget(self.btn_clear)
        self.btn_menu = QToolButton(self)
        self.btn_menu.setText("⋯")
        self.btn_menu.setAccessibleName(text("chat.menu.button", lang))
        self.btn_menu.setToolTip(plain_tooltip(text("chat.menu.button", lang)))
        self.btn_menu.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.btn_menu)
        self.act_clear = self.menu.addAction(button_text(text("chat.menu.clear", lang)))
        self.act_approvals = self.menu.addAction(button_text(text("chat.menu.approvals", lang)))
        self.act_permissions = self.menu.addAction(button_text(text("chat.menu.permissions", lang)))
        self.menu.addSeparator()
        self.act_auto_show = self.menu.addAction(button_text(text("chat.menu.auto_show", lang)))
        self.act_auto_show.setCheckable(True)
        self.act_auto_show.setChecked(True)
        self.btn_menu.setMenu(self.menu)
        top.addWidget(self.btn_menu)
        layout.addLayout(top)

        self.lbl_access = text_label("", lang, caption=True)
        self.lbl_access.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        self.lbl_access.setToolTip(plain_tooltip(text("chat.panel.access_tip", lang)))
        layout.addWidget(self.lbl_access)

        row = QHBoxLayout()
        self.lbl_filter = text_label(text("chat.filter.label", lang), lang, caption=True)
        row.addWidget(self.lbl_filter)
        self.cb_filter = QComboBox(self)
        for name in FILTERS:
            self.cb_filter.addItem("", name)
        self.cb_filter.setAccessibleName(text("chat.filter.label", lang))
        row.addWidget(self.cb_filter, stretch=1)
        layout.addLayout(row)
        self.needs_you = 0
        self._filter_texts()

        self.lbl_warning = text_label("", lang)
        self.lbl_warning.setStyleSheet("color: #ffd27f;")
        self.lbl_warning.setVisible(False)
        layout.addWidget(self.lbl_warning)

    def _filter_texts(self) -> None:
        keys = {"all": "chat.filter.all", "needs_you": "chat.filter.needs_you", "decisions": "chat.filter.decisions",
                "ditto": "chat.filter.ditto"}
        for index, name in enumerate(FILTERS):
            self.cb_filter.setItemText(index, text(keys[name], self.lang, n=self.needs_you))

    def set_needs_you(self, count: int) -> None:
        self.needs_you = int(count)
        self._filter_texts()

    def set_filter(self, name: str) -> None:
        index = self.cb_filter.findData(name if name in FILTERS else "all")
        self.cb_filter.setCurrentIndex(max(0, index))

    def current_filter(self) -> str:
        return self.cb_filter.currentData() or "all"

    def warn(self, message: str) -> None:
        self.lbl_warning.setText(f"{text('chat.bubble.warning', self.lang)} {one_line(message)}" if message else "")
        self.lbl_warning.setVisible(bool(message))

    def retranslate(self, lang: str) -> None:
        self.lang = lang
        self.title.setText(text("chat.panel.heading", lang))
        self.badge.setText(text("chat.ditto.badge", lang))
        self.badge.setToolTip(plain_tooltip(text("chat.ditto.badge_tip", lang)))
        self.badge.setAccessibleName(text("chat.ditto.badge_tip", lang))
        self.btn_clear.setText(button_text(text("chat.clear.button", lang)))
        self.btn_clear.setToolTip(plain_tooltip(text("chat.clear.tip", lang)))
        self.btn_menu.setAccessibleName(text("chat.menu.button", lang))
        self.btn_menu.setToolTip(plain_tooltip(text("chat.menu.button", lang)))
        self.act_clear.setText(button_text(text("chat.menu.clear", lang)))
        self.act_approvals.setText(button_text(text("chat.menu.approvals", lang)))
        self.act_permissions.setText(button_text(text("chat.menu.permissions", lang)))
        self.act_auto_show.setText(button_text(text("chat.menu.auto_show", lang)))
        self.lbl_access.setToolTip(plain_tooltip(text("chat.panel.access_tip", lang)))
        self.lbl_filter.setText(text("chat.filter.label", lang))
        self._filter_texts()


class DittoNoticeBanner(QFrame):
    """The one-time notice that Ditto is experimental (eighth round, Q-2), at the top of the panel until the person
    clicks "Understood"; then it is stored per person (utils/ditto_notice.py) and not shown again. It blocks nothing:
    the chat, the questions that wait and every other window work while it is shown."""

    def __init__(self, state_dir: Optional[Path], lang: str = "en", parent: Optional[QWidget] = None):
        super().__init__(parent)
        from orchestrator.utils.ditto_notice import DittoNotice
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("DittoNoticeBanner { border: 1px solid #ffd27f; }")
        box = QVBoxLayout(self)
        self.title = text_label("", lang)
        font = QFont(self.title.font())
        font.setBold(True)
        self.title.setFont(font)
        box.addWidget(self.title)
        self.text = text_label("", lang)
        box.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_ok = make_button("")
        self.btn_ok.clicked.connect(self.acknowledge)
        row.addWidget(self.btn_ok)
        box.addLayout(row)
        self.retranslate(lang)
        self.notice = None
        needed = True
        try:
            if state_dir is not None:
                self.notice = DittoNotice(state_dir)
                needed = self.notice.needed()
        except OSError as e:  # no per-user folder: the notice is shown every time
            logger.warning(f"The notice on Ditto cannot be remembered ({e}).")
        self.setVisible(needed)

    def retranslate(self, lang: str) -> None:
        from orchestrator.utils.ditto_notice import notice_text
        title, body = notice_text(lang)
        self.title.setText(title)
        self.text.setText(body)
        self.setAccessibleName(title)
        self.btn_ok.setText(button_text(text("chat.ditto.notice.ok", lang)))

    def acknowledge(self) -> None:
        self.setVisible(False)
        if self.notice is None:
            return
        try:
            self.notice.acknowledge("gui")
        except OSError as e:
            logger.warning(f"Could not remember the notice on Ditto ({e}); it is shown again next time.")


def show_ditto_notice(dock: Any, panel: "ChatPanel") -> bool:
    """While the person has not read the notice on Ditto, a hidden chat panel is shown once, without taking the focus:
    the builder's "Ask Ditto for Config" and the module wizard use Ditto without the panel, and the notice comes before
    the first use (eighth round, Q-2). Once it is read, the person's choice to hide the panel stays. True when the
    panel was shown for it."""
    if panel.notice.isHidden():
        return False
    return bool(dock.show_without_focus())


class ChatPanel(QWidget):
    counts_changed = Signal(int, int)          # unread rows, questions that wait
    question_arrived = Signal()                # a new pending approval: the dock may show itself (without focus)
    open_approvals_requested = Signal(str)     # the approval id, or "" for the window
    permissions_requested = Signal()
    auto_show_changed = Signal(bool)

    def __init__(self, framework_manager: Any, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.framework = framework_manager
        self.ditto = getattr(framework_manager, "ditto_manager", None)
        self.i18n = get_i18n()
        self.lang = current_language()
        self.worker = None
        self._pending_question: Optional[str] = None
        self.confirmation = ToolConfirmation(self)

        # Ditto's conversation is personal data: the user's state directory, never a CWD-relative cache.
        config = framework_manager.config
        history_dir = Path(getattr(config, "user_state_dir", None) or Path.home() / ".llm-framework")
        history_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = history_dir / ChatHistory.FILE_NAME
        self._migrate_legacy_history(config)
        self.history = ChatHistory.in_state_dir(history_dir)

        self.feed = getattr(framework_manager, "chat_feed", None)
        self.writer = FeedWriter(self.feed, "gui")
        try:
            self.names: Optional[SpeakingNames] = SpeakingNames.for_framework(framework_manager, self.lang)
        except Exception as e:  # noqa: BLE001 - names help the person; the chat works without them
            logger.warning(f"Speaking names are not available: {e}")
            self.names = None
        self.state: Any = None
        self.last_seen = 0
        self.watcher: Optional[FeedWatcher] = None
        self._feed_failed = False
        self._imported = False
        self._load_tries = 0
        self._approvals_store: Any = None
        self._pending_records: List[Any] = []

        self._access_request = object()  # token of the header text that may still change the header
        worker_relay().done.connect(self._on_access, Qt.QueuedConnection)
        self._init_ui()
        if self.ditto is None:
            self.composer.set_available(False, text("chat.composer.no_ditto", self.lang))
        if self.i18n is not None:
            self.i18n.language_changed.connect(self._language_changed)
        self._load()
        self.refresh_access()

    # --- layout ----------------------------------------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.header = PanelHeader(self.lang, self)
        layout.addWidget(self.header)
        self.notice = DittoNoticeBanner(self.history_file.parent, self.lang, self)
        layout.addWidget(self.notice)
        self.feed_view = FeedView(self.lang, self.names, self)
        layout.addWidget(self.feed_view, stretch=1)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName(text("chat.panel.thinking", self.lang))
        self.progress.setStyleSheet("QProgressBar::chunk { background-color: #2196F3; }")
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        # `ditto ask --no-tools / --provider / --model` by mouse: for the next question only (W1')
        self.options = QuestionOptions(self.lang, self._provider_catalog, self)
        layout.addWidget(self.options)
        self._choice = QuestionChoice()
        self.composer = Composer(self.lang, self)
        layout.addWidget(self.composer)

        # the names of the former chat tab, kept for its callers
        self.txt_input = self.composer.input
        self.btn_send = self.composer.btn_send
        self.btn_clear = self.header.btn_clear
        self.lbl_access = self.header.lbl_access

        self.composer.send_requested.connect(self.send_message)
        self.composer.to_feed.connect(self.feed_view.focus_last)
        self.feed_view.escape_pressed.connect(self.focus_composer)
        self.feed_view.earlier_requested.connect(self._load_earlier)
        self.feed_view.open_approval.connect(self.open_approvals_requested.emit)
        self.header.btn_clear.clicked.connect(self._confirm_clear_history)
        self.header.act_clear.triggered.connect(self._confirm_clear_history)
        self.header.act_approvals.triggered.connect(lambda: self.open_approvals_requested.emit(""))
        self.header.act_permissions.triggered.connect(self.permissions_requested.emit)
        self.header.act_auto_show.toggled.connect(self.auto_show_changed.emit)
        self.header.cb_filter.currentIndexChanged.connect(self._filter_changed)

    def focus_composer(self) -> None:
        self.composer.input.setFocus(Qt.ShortcutFocusReason)

    def set_auto_show(self, enabled: bool) -> None:
        blocked = self.header.act_auto_show.blockSignals(True)
        self.header.act_auto_show.setChecked(bool(enabled))
        self.header.act_auto_show.blockSignals(blocked)

    # --- the feed --------------------------------------------------------------------------------------------------

    def _load(self) -> None:
        """Shows the newest rows of the feed (importing Ditto's transcript once), or the transcript without one."""
        self._load_tries += 1
        if self.feed is None or self._feed_failed:
            self._show_transcript()
            return
        if not self._imported:
            try:
                import_chat_history(self.feed, self.history, program="gui")
                self._imported = True
            except FeedWriteError as e:
                if e.reason == "busy" and self._load_tries < LOAD_TRIES:
                    QTimer.singleShot(1000, self._load)
                    return
                self._imported = True    # the transcript stays in its own file; the next start imports it
                self.header.warn(text("chat.view.not_stored", self.lang, reason=str(e)))
            except Exception as e:  # noqa: BLE001 - a transcript that cannot be taken over never stops the chat
                self._imported = True
                logger.warning(f"Ditto's transcript was not imported into the chat: {e}")
        page = self.feed.page(limit=PAGE, timeout=GUI_READ_TIMEOUT)
        if page.busy and self._load_tries < LOAD_TRIES:
            QTimer.singleShot(1000, self._load)
            return
        if page.problem or page.busy:
            self._feed_failed = True
            self.header.warn(text("chat.view.feed_problem", self.lang, reason=page.problem or page.notices(self.lang)[0]))
            self._show_transcript()
            return
        self.feed_view.set_entries(page.entries, page.more)
        for line in page.notices(self.lang):
            self._local_notice(line)
        if self.watcher is None:
            self.watcher = FeedWatcher(self.feed, page.last_id, self.lang, parent=self)
            self.watcher.entries.connect(self._on_new_entries)
            self.watcher.notices.connect(lambda lines: [self._local_notice(line) for line in lines])
            self.watcher.problem.connect(lambda problem: self.header.warn(
                text("chat.view.feed_problem", self.lang, reason=problem)))
        else:
            self.watcher.set_cursor(page.last_id)
        self._after_load()

    def _show_transcript(self) -> None:
        """Ditto's transcript as bubbles, when the panel has no feed (nothing is saved in a feed then)."""
        collector = _Collector()
        try:
            import_chat_history(collector, self.history, program="gui")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Ditto's transcript cannot be shown: {e}")
        self.feed_view.set_entries([StoredEntry(0, entry) for entry in collector.entries])
        self._after_load()

    def _after_load(self) -> None:
        if self.state is not None:
            self._place_separator()
        self.feed_view.set_pending(self._pending_records)
        self._update_counts()

    def _load_earlier(self) -> None:
        oldest = self.feed_view.oldest_row_id()
        if self.feed is None or self._feed_failed or oldest is None:
            return
        page = self.feed.page(before_id=oldest, limit=PAGE, timeout=GUI_READ_TIMEOUT)
        if not page.busy and not page.problem:
            self.feed_view.prepend_entries(page.entries, page.more)

    def _on_new_entries(self, entries: List[StoredEntry]) -> None:
        self.feed_view.append_entries(entries)
        self._after_new_rows()

    def _after_new_rows(self) -> None:
        self._maybe_mark_seen()
        self._update_counts()

    def _local_notice(self, line: str, level: str = "warning") -> None:
        """A framework line shown in this window only (a skipped row, an error): never saved."""
        entry = self.writer.entry("notice", FeedSender(role="framework"), text_key="chat.history.line",
                                  params={"text": line}, level=level)
        self.feed_view.append_entries([StoredEntry(-1 if self.feed is not None else 0, entry)], announce=False)

    def _post(self, kind: str, sender: FeedSender, *, announce_row: bool = False, **content: Any) \
            -> Optional[StoredEntry]:
        """Writes a row and shows it; when it cannot be saved it is shown anyway, marked, and the header says why."""
        try:
            entry = self.writer.entry(kind, sender, **content)
        except ValueError as e:
            logger.warning(f"A chat row could not be built: {e}")
            return None
        if self.feed is not None and not self._feed_failed:
            try:
                stored = self.feed.append(entry, timeout=GUI_WRITE_TIMEOUT)
            except FeedWriteError as e:
                self.header.warn(text("chat.view.not_stored", self.lang, reason=str(e)))
                self.feed_view.append_entries([StoredEntry(-1, entry)], announce=announce_row)
                return None
            self.feed_view.append_entries([stored], announce=announce_row)
            self._after_new_rows()
            return stored
        self.feed_view.append_entries([StoredEntry(0 if self.feed is None else -1, entry)], announce=announce_row)
        return None

    def post_notice(self, text_key: str, params: Optional[dict] = None, external: tuple = (),
                    level: str = "info", sender: Optional[FeedSender] = None) -> Optional[StoredEntry]:
        """A framework notice in the chat (the main window's notices: a fix proposal, a standing approval used).
        `sender`: the AI app (or Ditto) the notice is about, by its trusted caller id, so the bubble names it in the
        person's language with its own claim marked (design 5.2 R4, R5); the framework when not given."""
        return self._post("notice", sender or FeedSender(role="framework"), text_key=text_key, params=params or {},
                          external=external, level=level)

    # --- seen, unread, questions -----------------------------------------------------------------------------------

    def apply_state(self, state: Any) -> None:
        """Takes the remembered state (gui_state.GuiState): the filter and the newest row the person has seen."""
        self.state = state
        newest = self.feed_view.newest_row_id()
        seen = int(getattr(state, "last_seen_entry", 0) or 0)
        # Never looked before, or a feed that was replaced since: what is there now counts as seen.
        self.last_seen = newest if seen <= 0 or seen > newest else seen
        state.last_seen_entry = self.last_seen
        self.header.set_filter(getattr(state, "filter", "all"))
        self.feed_view.set_filter(self.header.current_filter())
        self._place_separator()
        self._update_counts()

    def _place_separator(self) -> None:
        unseen = [s.entry for s in self.feed_view.stored_after(self.last_seen) if s.entry.sender.role != "person"]
        if unseen:
            self.feed_view.set_separator(self.last_seen, away_summary(unseen, len(self._pending_records), self.lang))

    def unread_count(self) -> int:
        return sum(1 for s in self.feed_view.stored_after(self.last_seen) if s.entry.sender.role != "person")

    def pending_count(self) -> int:
        return len(self._pending_records)

    def mark_seen(self) -> None:
        newest = self.feed_view.newest_row_id()
        if newest > self.last_seen:
            self.last_seen = newest
            if self.state is not None:
                self.state.last_seen_entry = newest
        self._update_counts()

    def _maybe_mark_seen(self) -> None:
        window = self.window()
        if self.isVisible() and window is not None and window.isActiveWindow():
            self.mark_seen()

    def _update_counts(self) -> None:
        self.header.set_needs_you(self.pending_count())
        self.counts_changed.emit(self.unread_count(), self.pending_count())

    def link_approvals(self, store: Any, changed: Any = None) -> None:
        """Questions that wait come from this pending-approvals store; `changed` (a signal) says when to look."""
        self._approvals_store = store
        if changed is not None:
            changed.connect(self.refresh_pending)
        self.refresh_pending()

    def refresh_pending(self, *_args: Any) -> None:
        if self._approvals_store is None:
            return
        try:
            records = list(self._approvals_store.pending())
        except Exception as e:  # noqa: BLE001 - never break the chat over the questions
            logger.warning(f"Could not read the pending approvals: {e}")
            return
        self._pending_records = records
        new = self.feed_view.set_pending(records)
        if new:
            self.question_arrived.emit()
            for bubble in new:   # spoken only with audio_announcements on (and never in tests or CI)
                announce(text("chat.pending.announce", self.lang, title=one_line(bubble.record.title)),
                         config=getattr(self.framework, "config", None))
        self._update_counts()

    def _filter_changed(self, _index: int) -> None:
        name = self.header.current_filter()
        self.feed_view.set_filter(name)
        if self.state is not None:
            self.state.filter = name

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._place_separator()
        QTimer.singleShot(0, self._maybe_mark_seen)

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange:
            self._maybe_mark_seen()

    # --- the header's access line ----------------------------------------------------------------------------------

    def refresh_access(self) -> None:
        """Reads the provider and sign-in Ditto uses in a worker thread and shows them in the header. The worker
        holds no widget and no Qt object of its own: it answers on the relay, which lives as long as the
        application (see worker_relay.py)."""
        from orchestrator.gui import chat_window
        request = self._access_request = object()
        ditto, relay = self.ditto, worker_relay()
        i18n = self.i18n
        translate = (lambda key, default: i18n.t(key, default)) if i18n else None
        describe = chat_window.describe_access

        def work():
            relay.done.emit(request, describe(ditto, translate))

        threading.Thread(target=work, name="chat-access", daemon=True).start()

    def _on_access(self, request: object, value: Any) -> None:
        if request is not self._access_request:  # another window's answer, or an older one of this window
            return
        self.lbl_access.setText(one_line(value or ""))
        if self.ditto is None:
            self.composer.set_available(False, text("chat.composer.no_ditto", self.lang))
            return
        not_set_up = self.i18n.t("chat.access.not_set_up", "AI not set up") if self.i18n else "AI not set up"
        ready = value != not_set_up
        self.composer.set_available(ready, "" if ready else text("chat.composer.not_set_up", self.lang))

    def _migrate_legacy_history(self, config: Any) -> None:
        """Moves the history from the old cache/ location (CWD-relative, not git-ignored) once."""
        get_cfg = getattr(config, "get", lambda k, d=None: getattr(config, k, d))
        legacy = Path(str(get_cfg("cache_dir", "cache"))) / "chat_history.json"
        try:
            if legacy.is_file() and not self.history_file.exists():
                legacy.replace(self.history_file)
                os.chmod(self.history_file, 0o600)
        except OSError:
            pass

    # --- talking to Ditto ------------------------------------------------------------------------------------------

    def _provider_catalog(self) -> Any:
        """The provider catalogue Ditto checks a provider against (read when the options are first shown)."""
        from orchestrator.gui.ai_choice import catalog_of
        return catalog_of(self.ditto, getattr(self.framework, "config", None))

    def _new_worker(self, question: str) -> Any:
        """The worker of one turn, with this question's options (self._choice): another provider or model asks a Ditto
        made for this question alone (Core/ai_override.ditto_with; the saved settings stay unchanged), and "Answer
        without tools" sets use_tools False."""
        from orchestrator.gui import chat_window   # ChatWorker stays where the former chat tab defined it
        choice = self._choice
        ditto = self.ditto
        if choice.provider or choice.model:
            from orchestrator.Core.ai_override import ditto_with, override_values
            ditto = ditto_with(self.ditto, override_values(choice.provider, choice.model, ditto=self.ditto))
        worker = chat_window.ChatWorker(ditto, question, self.history, self.confirmation.confirm)
        worker.use_tools = choice.use_tools
        return worker

    def _question_choice(self) -> Optional[QuestionChoice]:
        """This question's options, checked like `ditto ask` checks --provider (None: refused, the person was told)."""
        choice = self.options.choice()
        if choice.provider:
            from orchestrator.Core.ai_override import provider_problem
            catalog = self.options._catalog
            problem = provider_problem(catalog, choice.provider) if catalog is not None else None
            if problem:
                plain_box(self, "warning", ui_text("ditto_models.chat.options", self.lang),
                          ui_text("ditto_models.chat.refused", self.lang, reason=one_line(problem)))
                return None
        return choice

    @staticmethod
    def _ditto_sender() -> FeedSender:
        return FeedSender(role="ditto", caller="ditto")

    def send_message(self) -> None:
        question = self.txt_input.text().strip()
        if not question or (self.worker is not None and self.worker.isRunning()):
            return
        choice = self._question_choice()
        if choice is None:
            return
        self.txt_input.clear()
        reply_to = self.composer.reply_to
        self.composer.clear_reply()
        self._post("person_message", FeedSender(role="person"), person_text=question,
                   refs=FeedRefs(reply_to=reply_to))
        self._choice = choice
        if not choice.is_default:   # said in this window (not saved): what this one question uses
            self._local_notice(ui_text("ditto_models.chat.notice", self.lang, what=choice.summary(self.lang)),
                               level="info")
        self.options.reset()        # the options are for one question
        self._pending_question = question
        self._set_thinking(True)
        # The worker only reads the history; nothing changes it until the reply is back on this thread.
        self.worker = self._new_worker(question)
        self.worker.answered.connect(self._on_ai_response)
        self.worker.failed.connect(self._on_ai_error)
        self.worker.tool_used.connect(self._on_tool_used)
        self.worker.start()

    def _framework_title(self, use: Any) -> bool:
        """Whether a tool note's title is the framework's own: the title of a tool registered here. A name the model
        made up (an unknown tool) is the AI's word, never this framework's."""
        registry = getattr(self.framework, "tool_registry", None)
        try:
            return bool(use.title) and registry.get(use.name).title == use.title
        except Exception:  # noqa: BLE001 - unknown tool, no registry: outside text
            return False

    def _on_tool_used(self, use: Any) -> None:
        key = "chat.tool.used" if use.ok else "chat.tool.not_run"
        # Why it did not run can quote a handler's error, and a title the framework did not give is the model's word:
        # both outside text, quoted on the framework's line (design 5.2 R2).
        external = ["detail"] if self._framework_title(use) else ["title", "detail"]
        params = {"title": use.title, "detail": use.detail or "-"}
        self._post("ditto_tool_note", self._ditto_sender(), text_key=key, params=params, external=external)

    def _on_ai_response(self, reply: Any) -> None:
        self._set_thinking(False)
        self._pending_question = None
        if getattr(reply, "source_changed", False):
            # A saved conversation of the removed remote mode named another computer's objects: it starts anew once.
            self.post_notice("chat.notice.source_local")
        elif reply.new_conversation:
            # Summarized because it got long, or because it had tool turns and was asked without tools.
            no_tools = getattr(reply, "new_conversation_reason", "") == "no_tools"
            self.post_notice("chat.notice.new_conversation_no_tools" if no_tools else "chat.notice.new_conversation")
        if reply.text:
            self._post("ditto_answer", self._ditto_sender(), ai_text=reply.text, ai_text_kind="answer",
                       announce_row=True)
        self.history.apply(reply)
        self.history.save()

    def _on_ai_error(self, error_msg: str) -> None:
        self._set_thinking(False)
        # Shown, but not stored: an error is not an answer and must not reach the next prompt.
        self._pending_question = None
        entry = self.writer.entry("notice", FeedSender(role="framework"), text_key="chat.notice.error",
                                  params={"error": error_msg}, external=["error"], level="warning")
        self.feed_view.append_entries([StoredEntry(-1 if self.feed is not None else 0, entry)])

    def _set_thinking(self, active: bool) -> None:
        self.composer.set_busy(active)
        self.options.set_busy(active)
        self.btn_clear.setEnabled(not active)  # the running turn reads the history
        self.header.act_clear.setEnabled(not active)
        self.progress.setVisible(active)

    # --- "Clear chat" ----------------------------------------------------------------------------------------------

    def _history_item(self) -> Optional[HistoryItem]:
        """Ditto's conversation file as the question lists it: what is on disk now (another window or the terminal
        may have written it since this window read it), bound to its bytes. None when neither the file nor this
        window holds a conversation; HistoryNotListable when this window holds one that the file does not show
        (it could not be listed, so it is never cleared)."""
        path = Path(self.history_file)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = b""
        except OSError as e:
            raise HistoryNotListable(type(e).__name__) from None
        if not holds_conversation(data):
            if self.history.messages or self.history.transcript:
                raise HistoryNotListable("not in its file")
            return None
        return HistoryItem(full_path=str(path.resolve()), file_name=path.name, size_bytes=len(data),
                           content_sha256=hashlib.sha256(data).hexdigest())

    def _audit(self) -> Any:
        audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        return audit if callable(getattr(audit, "record", None)) else None

    def _tell(self, kind: str, message: str) -> None:
        plain_box(self, kind, text("chat.clear.title", self.lang), message)

    def _confirm_clear_history(self) -> bool:
        """Asks with the full list (path, file name, size, what it is; No is the default) and clears only what the
        person saw: the conversation rows of the feed (the records of what AIs did are hidden, not deleted) and
        Ditto's conversation file. Nothing is asked, and nothing deleted, without the audit log (every clear is
        recorded before anything goes) or when this window holds a conversation it cannot list. Returns True when
        something was cleared."""
        if self.worker is not None and self.worker.isRunning():
            return False
        if self._audit() is None:
            self._tell("warning", text("chat.clear.failed", self.lang, reason=text("chat.clear.no_audit", self.lang)))
            return False
        plan: Optional[ClearPlan] = None
        if self.feed is not None and not self._feed_failed:
            try:
                plan = self.feed.clear_plan(timeout=GUI_READ_TIMEOUT)
            except Exception as e:  # noqa: BLE001 - sqlite or file errors: nothing is deleted
                self._tell("warning", text("chat.clear.failed", self.lang, reason=one_line(type(e).__name__)))
                return False
        try:
            history = self._history_item()
        except HistoryNotListable:
            self._tell("warning", text("chat.clear.failed", self.lang,
                                       reason=text("chat.clear.history_unlisted", self.lang)))
            return False
        if history is None and (plan is None or (not plan.rows and not plan.hidden_records)):
            self._tell("information", text("chat.clear.empty", self.lang))
            return False
        if not ClearChatDialog(self, history, plan, self.lang).exec():
            return False
        return self._clear(history, plan)

    def _clear(self, history: Optional[HistoryItem], plan: Optional[ClearPlan]) -> bool:
        audit = self._audit()
        if audit is None:   # every clear is recorded before anything is deleted
            self._tell("warning", text("chat.clear.failed", self.lang, reason=text("chat.clear.no_audit", self.lang)))
            return False
        try:
            unchanged = self._history_item() == history
        except HistoryNotListable:
            unchanged = False
        if not unchanged:   # checked before anything goes: a changed conversation leaves the messages too
            self._tell("warning", text("chat.clear.changed", self.lang))
            return False
        cleared_feed = False
        if plan is not None:
            try:
                self.feed.clear_conversation(plan, "gui", audit)
                cleared_feed = True
            except ClearPlanChanged:
                self._tell("warning", text("chat.clear.changed", self.lang))
                return False
            except (FeedWriteError, PermissionError) as e:
                self._tell("warning", text("chat.clear.failed", self.lang, reason=one_line(str(e))))
                return False

        def failed(reason: str) -> bool:
            key = "chat.clear.history_failed" if cleared_feed else "chat.clear.failed"
            self._tell("warning", text(key, self.lang, reason=reason))
            self._reload()
            return cleared_feed

        if history is not None:
            try:
                unchanged = self._history_item() == history
            except HistoryNotListable:
                unchanged = False
            if not unchanged:   # it changed in the moment the messages were cleared: Ditto's conversation stays
                return failed(text("chat.clear.history_changed", self.lang))
            try:   # write-ahead: Ditto's conversation is cleared only once its clearing is recorded
                audit.record("gui", "chat_history", "framework", "person", "feed_cleared", approved=True,
                             arguments={"bytes": history.size_bytes},
                             detail="Ditto's conversation cleared (chat_history.json)",
                             extra={"deletion_sha256": history.sha256(), "answered_by": "person",
                                    "confirm_channel": "framework-gui"})
            except Exception as e:  # noqa: BLE001 - the reason without a host path
                return failed(one_line(type(e).__name__))
            self.history.clear()
            if not self.history.save():
                self.history = ChatHistory.load(self.history_file)   # this window keeps what the file still has
                return failed("OSError")
        self.header.warn("")
        self._reload()
        return True

    def _reload(self) -> None:
        """Shows the chat again from the feed (after "Clear chat"), or the (now empty) transcript."""
        self.feed_view.clear()
        self._load_tries = 0
        self._load()

    # --- language and closing --------------------------------------------------------------------------------------

    def _language_changed(self, lang: str) -> None:
        self.lang = lang
        self.header.retranslate(lang)
        self.notice.retranslate(lang)
        self.composer.retranslate(lang)
        self.options.retranslate(lang)
        self.progress.setAccessibleName(text("chat.panel.thinking", lang))
        self.feed_view.lang = lang
        self.feed_view.btn_earlier.setText(button_text(text("chat.view.earlier", lang)))
        self.feed_view.pill.setText(button_text(text("chat.view.new_messages", lang)))
        self.feed_view.empty.setText(text("chat.view.empty", lang))
        self.feed_view.pending_title.setText(text("chat.view.needs_you", lang))
        more = not self.feed_view.btn_earlier.isHidden()
        self.feed_view.set_entries(self.feed_view.stored(), more)
        self.feed_view.set_pending(self._pending_records)
        self._place_separator()

    def closeEvent(self, event: Any) -> None:
        self.confirmation.close()  # a waiting call is answered "no"
        if self.watcher is not None:
            self.watcher.stop()
        super().closeEvent(event)


__all__ = ["ChatPanel", "PanelHeader", "ClearChatDialog", "HistoryItem", "size_text"]
