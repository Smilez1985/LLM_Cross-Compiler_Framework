#!/usr/bin/env python3
"""
Settings > Language model > Context window (Ditto > Context Window... opens it): `llm-cli ditto context` by mouse.

It shows the window of the model Ditto's chat uses with its source (the local server's /props, the provider's server,
the pinned table or the conservative default), how the tokens are counted, the saved conversation against the turn
budget, and the compactions kept with their originals (Core/ditto_context.context_overview, the same call as the CLI).
Reading the window may ask the provider's server, so it runs in a worker thread; Check again asks it anew instead of
the 24-hour cache. Show original shows the newest compaction's original conversation (plain text, read-only); Undo
asks first (No by default) and then restores it in the conversation file, which the chat reads again before its next
turn. Every text is plain text; nothing goes to an AI.
"""

import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text


class ContextWindowBox(QGroupBox):
    """The context window, the counting, the conversation's budget and its compactions (`llm-cli ditto context`)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 history_factory: Optional[Callable[[], Any]] = None, threaded: bool = True):
        self.lang = lang or current_language()
        super().__init__(ui_text("context.title", self.lang), parent)
        self.setObjectName("grp_context_window")
        self.framework = framework
        self._history_factory = history_factory or self._history
        self._threaded = threaded
        self.view: Any = None
        self._token: Optional[object] = None
        layout = QVBoxLayout(self)
        intro = plain_label(self._t("context.intro"), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.lbl_info = plain_label(self._t("context.loading"), self)
        self.lbl_info.setObjectName("lbl_context_info")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_info)
        row = QHBoxLayout()
        self.btn_refresh = self._button("context.refresh", "btn_context_refresh", lambda: self.refresh(True))
        self.btn_original = self._button("context.original", "btn_context_original", self.show_original)
        self.btn_undo = self._button("context.undo", "btn_context_undo", self.undo)
        for button in (self.btn_refresh, self.btn_original, self.btn_undo):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.refresh()

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, name: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _history(self) -> Any:
        from pathlib import Path
        from orchestrator.Core.ditto_manager import ChatHistory
        from orchestrator.runtime.profile import default_state_dir
        config = getattr(self.framework, "config", None)
        return ChatHistory.in_state_dir(Path(getattr(config, "user_state_dir", None) or default_state_dir()))

    def _ditto(self) -> Any:
        return getattr(self.framework, "ditto_manager", None)

    # --- reading (`ditto context`) ---
    def refresh(self, ask_server: bool = False) -> None:
        """Reads the overview again (in a worker thread: it may ask the provider's server)."""
        from orchestrator.Core.ditto_context import context_overview
        ditto = self._ditto()
        if ditto is None:
            self.lbl_info.setText(self._t("context.no_ditto"))
            self._set_buttons()
            return
        token, relay, history = object(), worker_relay(), self._history_factory
        self._token = token
        self.lbl_info.setText(self._t("context.loading"))

        def run() -> None:
            try:
                result: Any = (True, context_overview(ditto, history(), refresh=ask_server))
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            if self._threaded:
                relay.done.emit(token, result)
            else:
                self._on_relay(token, result)
        if self._threaded:
            threading.Thread(target=run, name="context-window", daemon=True).start()
        else:
            run()

    def _on_relay(self, token: object, result: Any) -> None:
        if token is not self._token:
            return
        ok, value = result
        if not ok:
            self.view = None
            self.lbl_info.setText(self._t("context.failed", reason=one_line(str(value))))
        else:
            self.view = value
            self.lbl_info.setText(self.text())
        self._set_buttons()

    def _set_buttons(self) -> None:
        has = bool(self.view is not None and self.view.compactions)
        self.btn_original.setEnabled(has)
        self.btn_undo.setEnabled(has)

    def text(self) -> str:
        """The overview in the person's language."""
        view = self.view
        if view is None:
            return ""
        window = view.window
        source = self._t(f"context.source.{window.get('source')}")
        lines = [self._t("context.window", tokens=window.get("tokens"), source=source),
                 self._t("context.how", how=one_line(str(window.get("how") or "-")))]
        if window.get("max_output"):
            lines.append(self._t("context.max_output", tokens=window["max_output"]))
        if window.get("started"):
            state = ("context.started.confirmed" if window.get("confirmed") else
                     "context.started.differs" if window.get("confirmed") is False else "context.started.open")
            lines.append(self._t(state, ctx=window["started"]))
        if window.get("note"):
            lines.append(one_line(str(window["note"])))
        lines.append(self._t("context.counting", how=view.counting))
        budget = view.budget
        lines.append(self._t("context.budget", messages=view.messages, history=budget.get("history", 0),
                             room=max(0, int(budget.get("room") or 0)), answer=budget.get("answer", 0)))
        if view.compactions:
            newest = view.compactions[-1]
            lines.append(self._t("context.compactions", n=len(view.compactions), id=newest.id, at=newest.at,
                                 summarized=newest.summarized, kept=newest.kept))
        else:
            lines.append(self._t("context.no_compactions"))
        return "\n".join(lines)

    # --- the original and undo (`ditto context original`, `ditto context undo`) ---
    def show_original(self) -> Optional[QDialog]:
        from orchestrator.Core.ditto_context import original_text
        text = original_text(self._history_factory())
        if text is None:
            plain_box(self, "information", self._t("context.title"), self._t("context.none"))
            return None
        dialog = QDialog(self)
        dialog.setObjectName("dlg_context_original")
        dialog.setWindowTitle(self._t("context.original.title"))
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(dialog)
        view.setObjectName("txt_context_original")
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(760, 520)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        return dialog

    def undo(self) -> bool:
        history = self._history_factory()
        if not history.compactions:
            plain_box(self, "information", self._t("context.title"), self._t("context.none"))
            return False
        record = history.compactions[-1]
        if not ask(self, self._t("context.undo.title"),
                   self._t("context.undo.question", id=record.get("id"), at=record.get("at"),
                           n=len(record.get("messages") or []))):
            return False
        if history.undo_compaction() is None or not history.save():
            plain_box(self, "warning", self._t("context.undo.title"), self._t("context.undo.failed"))
            return False
        plain_box(self, "information", self._t("context.undo.title"), self._t("context.undo.done"))
        self.refresh()
        return True
