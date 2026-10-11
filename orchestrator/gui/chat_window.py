#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Chat Window
DIREKTIVE: Echter Threading-Support (QThread) & Persistenz.

Features:
- Ditto answers in a worker thread (no GUI freeze) and may use the framework tools within the user's tool
  permissions; tools that need a confirmation ask in a modal dialog showing every argument.
- Persistent, append-only chat history in the per-user state directory (Core.ditto_manager.ChatHistory).
- Manual clearing of the history.
- The header shows the access Ditto uses ("Anthropic Claude (API) · API key · wrkspc_...", "Local · Ollama",
  "key from OPENAI_API_KEY"), read in a worker thread (it opens the credential vault).
"""

import html
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from orchestrator.Core.ditto_manager import ChatHistory, DittoError
from orchestrator.gui.tool_confirmation import ToolConfirmation
from orchestrator.llm.auth.signin import access_text
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.localization import get_instance as get_i18n
from orchestrator.utils.logging import get_logger

logger = get_logger("ChatWindow")


class ChatWorker(QThread):
    """Runs one chat turn in the background. The history is only read here; the GUI thread appends the reply."""
    answered = Signal(object)   # ChatReply
    failed = Signal(str)
    tool_used = Signal(object)  # ToolUse, while the turn is running

    def __init__(self, ditto, question, history, confirm):
        super().__init__()
        self.ditto = ditto
        self.question = question
        self.history = history
        self.confirm = confirm

    def run(self):
        try:
            if not self.ditto:
                raise DittoError("The AI assistant (Ditto) is not initialized.")
            reply = self.ditto.chat(self.question, self.history, confirm=self.confirm, on_tool=self.tool_used.emit)
            self.answered.emit(reply)
        except DittoError as e:
            self.failed.emit(str(e))
        except Exception as e:  # a bug, not a provider problem: keep the details in the log
            logger.error(f"Chat turn failed: {e}", exc_info=True)
            self.failed.emit(f"Unexpected error: {redact_secrets(str(e))}")


class _AccessSignals(QObject):
    done = Signal(int, str)  # request number, header text


def describe_access(ditto, translate=None) -> str:
    """The header text for Ditto's current provider and sign-in (no secrets)."""
    if ditto is None:
        return ""
    try:
        return access_text(ditto.describe("chat"), translate)
    except Exception:  # not set up (DittoError) or a broken setting: the chat itself explains it
        return translate("chat.access.not_set_up", "AI not set up") if translate else "AI not set up"


class ChatWindow(QWidget):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework = framework_manager
        self.ditto = getattr(framework_manager, 'ditto_manager', None)
        self.i18n = get_i18n()
        self.worker = None
        self._pending_question = None
        self.confirmation = ToolConfirmation(self)

        # The history is personal data: keep it in the user's state directory, not a CWD-relative cache.
        config = framework_manager.config
        history_dir = Path(getattr(config, "user_state_dir", None) or Path.home() / ".llm-framework")
        history_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = history_dir / ChatHistory.FILE_NAME
        self._migrate_legacy_history(config)
        self.history = ChatHistory.load(self.history_file)

        self._access_request = 0
        self._init_ui()
        self._show_transcript()
        self.refresh_access()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        header_layout = QHBoxLayout()
        title = QLabel("Ditto AI Assistant")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)
        self.lbl_access = QLabel("")
        self.lbl_access.setTextFormat(Qt.PlainText)
        self.lbl_access.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        self.lbl_access.setToolTip("The provider and sign-in Ditto uses (Tools > Configure AI Agent)")
        header_layout.addWidget(self.lbl_access, stretch=1)

        self.btn_clear = QPushButton("🗑️ Clear History")
        self.btn_clear.clicked.connect(self._confirm_clear_history)
        self.btn_clear.setFixedWidth(120)
        header_layout.addWidget(self.btn_clear, alignment=Qt.AlignRight)
        layout.addLayout(header_layout)

        self.txt_display = QTextBrowser()
        # Links are never opened directly: the user sees the full URL and confirms first.
        self.txt_display.setOpenExternalLinks(False)
        self.txt_display.setOpenLinks(False)
        self.txt_display.anchorClicked.connect(self._confirm_open_link)
        self.txt_display.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333;")
        layout.addWidget(self.txt_display)

        # Status bar while Ditto is thinking
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar::chunk { background-color: #2196F3; }")
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        input_layout = QHBoxLayout()
        self.txt_input = QLineEdit()
        placeholder = "Ask Ditto about targets, builds or compilation errors..."
        if self.i18n:
            placeholder = self.i18n.t("chat.placeholder", placeholder)
        self.txt_input.setPlaceholderText(placeholder)
        self.txt_input.returnPressed.connect(self.send_message)

        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self.send_message)
        self.btn_send.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")

        input_layout.addWidget(self.txt_input)
        input_layout.addWidget(self.btn_send)
        layout.addLayout(input_layout)

    def refresh_access(self):
        """Reads the provider and sign-in Ditto uses in a worker thread and shows them in the header."""
        self._access_request += 1
        request, ditto = self._access_request, self.ditto
        i18n = self.i18n  # the worker holds no widget
        translate = (lambda key, default: i18n.t(key, default)) if i18n else None
        signals = _AccessSignals()  # no parent: the worker may still emit after the window is gone
        signals.done.connect(self._on_access)

        def work():
            text = describe_access(ditto, translate)
            try:
                signals.done.emit(request, text)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="chat-access", daemon=True).start()

    def _on_access(self, request: int, text: str):
        if request == self._access_request:
            self.lbl_access.setText(text)

    def _migrate_legacy_history(self, config):
        """Moves the history from the old cache/ location (CWD-relative, not git-ignored) once."""
        get_cfg = getattr(config, 'get', lambda k, d=None: getattr(config, k, d))
        legacy = Path(str(get_cfg("cache_dir", "cache"))) / "chat_history.json"
        try:
            if legacy.is_file() and not self.history_file.exists():
                legacy.replace(self.history_file)
                os.chmod(self.history_file, 0o600)
        except OSError:
            pass

    # --- rendering ---
    def _append_html(self, block: str):
        cursor = self.txt_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.txt_display.setTextCursor(cursor)
        self.txt_display.insertHtml(block)
        self.txt_display.ensureCursorVisible()

    def _add_message(self, text, is_user=False):
        color = "#aaffaa" if is_user else "#80d4ff"
        sender = "You" if is_user else "Ditto"
        align = "right" if is_user else "left"
        bg_color = "#2d2d2d" if is_user else "#252526"
        # Model output, knowledge-base text and user input are plain text: escape before rendering as rich text.
        formatted_text = html.escape(str(text)).replace("\n", "<br>")
        self._append_html(f"""
        <div style='text-align: {align}; margin: 10px;'>
            <span style='font-weight: bold; color: {color}; font-size: 12px;'>{sender}:</span><br>
            <div style='background-color: {bg_color}; padding: 8px; border-radius: 8px; display: inline-block; text-align: left;'>
                <span style='color: #eeeeee; font-size: 13px;'>{formatted_text}</span>
            </div>
        </div>
        """)

    def _add_note(self, text):
        """Tool uses and notices: small grey lines between the messages."""
        self._append_html(f"<div style='margin: 2px 10px; color: #8a8a8a; font-size: 11px;'>"
                          f"{html.escape(str(text))}</div><br>")

    def _show_transcript(self):
        self.txt_display.clear()
        for entry in self.history.transcript:
            if entry.role in ("user", "assistant"):
                self._add_message(entry.text, entry.role == "user")
            else:
                self._add_note(entry.text)

    # --- chat ---
    def send_message(self):
        text = self.txt_input.text().strip()
        if not text or (self.worker is not None and self.worker.isRunning()):
            return
        self.txt_input.clear()
        self._add_message(text, True)
        self._pending_question = text
        self._set_thinking(True)

        # The worker only reads the history; nothing changes it until the reply is back on this thread.
        self.worker = ChatWorker(self.ditto, text, self.history, self.confirmation.confirm)
        self.worker.answered.connect(self._on_ai_response)
        self.worker.failed.connect(self._on_ai_error)
        self.worker.tool_used.connect(self._on_tool_used)
        self.worker.start()

    def _on_tool_used(self, use):
        self._add_note(f"Used tool: {use.title}" if use.ok else f"Tool not run: {use.title} ({use.detail})")

    def _on_ai_response(self, reply):
        self._set_thinking(False)
        self._pending_question = None
        if reply.new_conversation:
            self._add_note("The conversation got long: Ditto summarized it and continues in a new conversation.")
        self._add_message(reply.text, False)
        self.history.apply(reply)
        self.history.save()

    def _on_ai_error(self, error_msg):
        self._set_thinking(False)
        # Shown, but not stored: an error is not an answer and must not reach the next prompt.
        self._pending_question = None
        self._add_message(f"⚠️ {error_msg}", False)

    def _confirm_open_link(self, url: QUrl):
        if url.scheme() not in ("http", "https"):
            return
        reply = QMessageBox.question(self, "Open link",
                                     f"Open this address in your browser?\n\n{url.toString()}",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            QDesktopServices.openUrl(url)

    def _set_thinking(self, active: bool):
        self.txt_input.setEnabled(not active)
        self.btn_send.setEnabled(not active)
        self.btn_clear.setEnabled(not active)  # the running turn reads the history
        self.progress.setVisible(active)

    def _confirm_clear_history(self):
        reply = QMessageBox.question(self, "Clear History",
                                     "Are you sure you want to delete the entire chat history?\nThis cannot be undone.",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.history.clear()
            self.history.save()
            self.txt_display.clear()
            self.txt_display.append("History cleared.")

    def closeEvent(self, event):
        self.confirmation.close()  # a waiting tool call is answered "no"
        super().closeEvent(event)
