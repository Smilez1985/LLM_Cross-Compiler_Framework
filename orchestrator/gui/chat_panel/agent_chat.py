#!/usr/bin/env python3
"""
The chat panel's ACP agents (Core/acp_hosts.py, Core/acp_client.py): beside Ditto, the person may chat with an
external agent they added. Ditto stays the default and works exactly as before; the picker only adds choices.

- AgentPicker: "Chat with: [Ditto | agent ...]" (cb_chat_agent) and "Agents..." (btn_chat_agents, the window
  Ditto > Agents...).
- AgentLive: the running turn of an agent, as it streams: its text (txt_chat_agent_live), its thoughts collapsed
  behind a button (btn_chat_agent_thoughts), tool calls with their status and the plan, and Stop
  (btn_chat_agent_stop: session/cancel). At the end of the turn the whole answer becomes one bubble of the agent in
  the feed (an AI app's reply, marked as the agent's own words), and the live box empties.
- AgentChat: one AgentSession per agent, started on the first message the person sends to it (never by itself),
  kept while the panel is open and closed with the panel or the application (only the processes it started).
  A permission question of the agent is answered in the framework's own dialog: Allow once or Reject ("Reject" is
  the default); when the agent also offers "always", a button opens the framework's own approvals window instead,
  the framework never chooses "always". A form the agent asks for (never a secret one: Core/acp_client declines
  those) is asked in a small dialog.
"""

import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
                               QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget)

from orchestrator.gui.chat_panel.bubbles import make_button, text_label
from orchestrator.gui.plain import button_text, one_line, plain_tooltip
from orchestrator.utils.localization import ui_text
from orchestrator.utils.logging import get_logger

logger = get_logger("AgentChat")


class AgentPicker(QWidget):
    """Who the next message goes to: Ditto (the default) or one of the person's agents."""

    manage_requested = Signal()
    changed = Signal(str)   # the agent id, "" for Ditto

    def __init__(self, lang: str = "en", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang = lang
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.lbl = text_label(ui_text("acp.chat.with", lang), lang, caption=True)
        row.addWidget(self.lbl)
        self.combo = QComboBox(self)
        self.combo.setObjectName("cb_chat_agent")
        self.combo.setAccessibleName(ui_text("acp.chat.with", lang))
        row.addWidget(self.combo, stretch=1)
        self.btn_manage = make_button(ui_text("acp.chat.manage", lang))
        self.btn_manage.setObjectName("btn_chat_agents")
        self.btn_manage.setToolTip(plain_tooltip(ui_text("acp.chat.manage_tip", lang)))
        self.btn_manage.clicked.connect(self.manage_requested.emit)
        row.addWidget(self.btn_manage)
        self.combo.currentIndexChanged.connect(lambda _i: self.changed.emit(self.current()))
        self.set_agents([])

    def set_agents(self, entries: List[Any]) -> None:
        """Ditto first, then the agents by name; the choice stays when the agent is still there."""
        keep = self.current()
        blocked = self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(ui_text("acp.chat.ditto", self.lang), "")
        for e in entries:
            self.combo.addItem(one_line(e.name), e.id)
        index = self.combo.findData(keep)
        self.combo.setCurrentIndex(max(0, index))
        self.combo.blockSignals(blocked)
        if self.current() != keep:
            self.changed.emit(self.current())

    def current(self) -> str:
        return self.combo.currentData() or ""

    def name(self) -> str:
        return self.combo.currentText() if self.current() else ""

    def retranslate(self, lang: str) -> None:
        self.lang = lang
        self.lbl.setText(ui_text("acp.chat.with", lang))
        self.combo.setItemText(0, ui_text("acp.chat.ditto", lang))
        self.btn_manage.setText(button_text(ui_text("acp.chat.manage", lang)))
        self.btn_manage.setToolTip(plain_tooltip(ui_text("acp.chat.manage_tip", lang)))


class AgentLive(QFrame):
    """The agent's running turn as it streams (hidden while no turn runs)."""

    stop_requested = Signal()

    def __init__(self, lang: str = "en", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang = lang
        self.setFrameShape(QFrame.StyledPanel)
        box = QVBoxLayout(self)
        top = QHBoxLayout()
        self.caption = text_label("", lang, caption=True)
        top.addWidget(self.caption, stretch=1)
        self.btn_thoughts = make_button(ui_text("acp.chat.thoughts", lang, n=0))
        self.btn_thoughts.setObjectName("btn_chat_agent_thoughts")
        self.btn_thoughts.setCheckable(True)
        self.btn_thoughts.toggled.connect(lambda shown: self.thoughts.setVisible(shown))
        top.addWidget(self.btn_thoughts)
        self.btn_stop = make_button(ui_text("acp.chat.stop", lang))
        self.btn_stop.setObjectName("btn_chat_agent_stop")
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        top.addWidget(self.btn_stop)
        box.addLayout(top)
        self.thoughts = QPlainTextEdit(self)
        self.thoughts.setObjectName("txt_chat_agent_thoughts")
        self.thoughts.setReadOnly(True)
        self.thoughts.setVisible(False)   # collapsed: the person opens them
        self.thoughts.setMaximumHeight(90)
        box.addWidget(self.thoughts)
        self.live = QPlainTextEdit(self)
        self.live.setObjectName("txt_chat_agent_live")
        self.live.setReadOnly(True)
        self.live.setMaximumHeight(160)
        box.addWidget(self.live)
        self.steps = text_label("", lang, caption=True)
        box.addWidget(self.steps)
        self.reset("")
        self.setVisible(False)

    def reset(self, name: str) -> None:
        self.name = name
        self.text = ""
        self.thought_count = 0
        self.tools: Dict[str, List[str]] = {}
        self.plan = ""
        self.caption.setText(ui_text("acp.chat.live", self.lang, name=one_line(name)) if name else "")
        self.live.setPlainText("")
        self.thoughts.setPlainText("")
        self.btn_thoughts.setChecked(False)
        self.btn_thoughts.setText(button_text(ui_text("acp.chat.thoughts", self.lang, n=0)))
        self.steps.setText("")

    def add(self, event: Any) -> None:
        if event.kind == "text":
            self.text += event.text
            self.live.setPlainText(self.text)
            self.live.verticalScrollBar().setValue(self.live.verticalScrollBar().maximum())
        elif event.kind == "thought":
            self.thought_count += 1
            self.thoughts.setPlainText(self.thoughts.toPlainText() + event.text)
            self.btn_thoughts.setText(button_text(ui_text("acp.chat.thoughts", self.lang, n=self.thought_count)))
        elif event.kind == "tool":
            title, status = self.tools.get(event.tool_id, ["", ""])
            self.tools[event.tool_id] = [event.text or title, event.status or status]
            self._steps()
        elif event.kind == "plan":
            self.plan = ui_text("acp.chat.plan", self.lang,
                                entries="; ".join(f"{one_line(c)} ({s})" for c, s in event.entries))
            self._steps()

    def _steps(self) -> None:
        lines = [ui_text("acp.chat.tool", self.lang, title=one_line(t or tid), status=s or "...")
                 for tid, (t, s) in self.tools.items()]
        self.steps.setText("\n".join(([self.plan] if self.plan else []) + lines[-6:]))


class PermissionDialog(QDialog):
    """The agent's permission question: Allow once or Reject ("Reject" is the default); when the agent offers
    "always", a button opens the framework's own approvals window, never an "always" answer."""

    def __init__(self, name: str, question: Any, parent: Optional[QWidget] = None, lang: str = "en",
                 open_approvals: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.answer = "reject"
        self.setObjectName("dlg_acp_permission")
        self.setWindowTitle(ui_text("acp.perm.title", lang, name=one_line(name)))
        box = QVBoxLayout(self)
        box.addWidget(text_label(ui_text("acp.perm.text", lang, name=one_line(name), title=one_line(question.title)),
                                 lang))
        row = QHBoxLayout()
        if question.has_always and open_approvals is not None:
            self.btn_always = make_button(ui_text("acp.perm.always", lang))
            self.btn_always.setObjectName("btn_acp_perm_always")
            self.btn_always.setToolTip(plain_tooltip(ui_text("acp.perm.always_tip", lang)))
            self.btn_always.clicked.connect(open_approvals)
            row.addWidget(self.btn_always)
        row.addStretch(1)
        self.btn_allow = make_button(ui_text("acp.perm.allow", lang))
        self.btn_allow.setObjectName("btn_acp_perm_allow")
        self.btn_reject = make_button(ui_text("acp.perm.reject", lang))
        self.btn_reject.setObjectName("btn_acp_perm_reject")
        self.btn_reject.setDefault(True)
        if not question.allow_option:   # only "always" or reject offered: no "Allow once" the client could send
            self.btn_allow.setEnabled(False)
            self.btn_allow.setToolTip(plain_tooltip(ui_text("acp.perm.no_once", lang)))
            box.addWidget(text_label(ui_text("acp.perm.no_once", lang), lang, caption=True))
        self.btn_allow.clicked.connect(lambda: self._done("allow"))
        self.btn_reject.clicked.connect(lambda: self._done("reject"))
        row.addWidget(self.btn_allow)
        row.addWidget(self.btn_reject)
        box.addLayout(row)
        self.btn_reject.setFocus(Qt.OtherFocusReason)

    def _done(self, answer: str) -> None:
        self.answer = answer
        self.accept() if answer == "allow" else self.reject()


class FormDialog(QDialog):
    """A form the agent asks to fill (plain fields only; never a secret)."""

    def __init__(self, name: str, question: Any, parent: Optional[QWidget] = None, lang: str = "en"):
        super().__init__(parent)
        self.setWindowTitle(ui_text("acp.form.title", lang, name=one_line(name)))
        form = QFormLayout(self)
        form.addRow(text_label(one_line(question.message), lang))
        self.fields: Dict[str, Any] = {}
        for field_name, kind, title in question.fields:
            if kind == "boolean":
                widget = QCheckBox()
            elif kind == "choice":
                widget = QComboBox()
                for choice in question.choices.get(field_name, ()):
                    widget.addItem(one_line(choice), choice)
            elif kind == "number":
                widget = QDoubleSpinBox()
                widget.setRange(-1e12, 1e12)
            else:
                widget = QLineEdit()
            self.fields[field_name] = widget
            form.addRow(one_line(title), widget)
        row = QHBoxLayout()
        send = make_button(ui_text("acp.form.send", lang))
        cancel = make_button(ui_text("acp.form.cancel", lang))
        cancel.setDefault(True)
        send.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(send)
        row.addWidget(cancel)
        form.addRow(row)

    def values(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name, widget in self.fields.items():
            if isinstance(widget, QCheckBox):
                out[name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                out[name] = widget.currentData()
            elif isinstance(widget, QDoubleSpinBox):
                out[name] = widget.value()
            else:
                out[name] = widget.text()
        return out


class _Bridge(QObject):
    """Signals from the agent's threads to the GUI thread."""
    event = Signal(object)                 # an AgentEvent
    ask = Signal(str, object, object)      # kind ("permission" | "form"), question, waiter dict
    finished = Signal(object)              # the stop reason, or an exception


class _GuiHost:
    """The AgentHost of one agent in the panel: events go to the GUI thread; a question waits until the person
    answered it in the framework's dialog (or the panel closed: then "no")."""

    def __init__(self, bridge: _Bridge):
        self.bridge = bridge
        self.closed = threading.Event()

    def on_event(self, event: Any) -> None:
        self.bridge.event.emit(event)

    def _ask(self, kind: str, question: Any) -> Any:
        waiter = {"done": threading.Event(), "answer": None}
        self.bridge.ask.emit(kind, question, waiter)
        while not waiter["done"].wait(0.2):
            if self.closed.is_set():
                return None
        return waiter["answer"]

    def ask_permission(self, question: Any) -> str:
        return self._ask("permission", question) or "reject"

    def ask_form(self, question: Any) -> Optional[Dict[str, Any]]:
        return self._ask("form", question)


class AgentChat(QObject):
    """The agents of one chat panel (see the module docstring). `post_reply(name, text)` and `notice(text, level)`
    write into the panel; `open_approvals()` opens the framework's approvals window."""

    busy_changed = Signal(bool)

    def __init__(self, framework: Any, live: AgentLive, post_reply: Callable[[str, str], None],
                 notice: Callable[[str, str], None], open_approvals: Callable[[], None],
                 parent: Optional[QWidget] = None, lang: str = "en"):
        super().__init__(parent)
        self.framework = framework
        self.live = live
        self.post_reply = post_reply
        self.notice = notice
        self.open_approvals = open_approvals
        self.lang = lang
        self.parent_widget = parent
        self.sessions: Dict[str, Any] = {}
        self.hosts: Dict[str, _GuiHost] = {}
        self.current_name = ""
        self.running = False
        self.bridge = _Bridge(self)
        self.bridge.event.connect(self._on_event, Qt.QueuedConnection)
        self.bridge.ask.connect(self._on_ask, Qt.QueuedConnection)
        self.bridge.finished.connect(self._on_finished, Qt.QueuedConnection)
        self.live.stop_requested.connect(self.stop)
        # Tests (and nothing else) may answer the questions without a dialog.
        self.answer_permission: Optional[Callable[[str, Any], str]] = None
        self.answer_form: Optional[Callable[[str, Any], Optional[Dict[str, Any]]]] = None
        self._store = None

    @property
    def store(self) -> Any:
        if self._store is None:
            from orchestrator.Core.acp_hosts import AgentStore
            self._store = AgentStore.for_framework(self.framework)
        return self._store

    def agents(self) -> List[Any]:
        try:
            return self.store.list()
        except Exception as e:  # noqa: BLE001 - no framework home: Ditto only
            logger.warning(f"The ACP agents cannot be listed: {type(e).__name__}")
            return []

    # --- a turn -----------------------------------------------------------------------------------------------------

    def send(self, agent_id: str, name: str, message: str) -> None:
        """Sends the person's message to the agent (started now if it is not running) on a worker thread."""
        if self.running:
            return
        self.running = True
        self.current_name = name
        self.live.reset(name)
        self.live.setVisible(True)
        self.busy_changed.emit(True)
        thread = threading.Thread(target=self._turn, args=(agent_id, message), name="acp-turn", daemon=True)
        thread.start()

    def _turn(self, agent_id: str, message: str) -> None:
        from orchestrator.Core.acp_client import AgentSession
        try:
            session = self.sessions.get(agent_id)
            if session is None or not session.running():
                spec = self.store.launch_spec(agent_id, "gui")
                host = _GuiHost(self.bridge)
                session = AgentSession(spec, host, self.store, "gui")
                self.sessions[agent_id], self.hosts[agent_id] = session, host
                session.start()
                session.new_session()
            result: Any = session.prompt(message)
        except Exception as e:  # noqa: BLE001 - said in the panel
            result = e
        self.bridge.finished.emit(result)

    def stop(self) -> None:
        for session in self.sessions.values():
            session.cancel()

    def _on_event(self, event: Any) -> None:
        if event.kind == "notice":
            self.notice(ui_text("acp.chat.notice", self.lang, name=one_line(self.current_name), text=event.text),
                        "info")
            return
        self.live.add(event)

    def _on_ask(self, kind: str, question: Any, waiter: Dict[str, Any]) -> None:
        name = self.current_name
        try:
            if kind == "permission":
                if self.answer_permission is not None:
                    answer = self.answer_permission(name, question)
                else:
                    dialog = PermissionDialog(name, question, self.parent_widget, self.lang, self.open_approvals)
                    dialog.exec()
                    answer = dialog.answer
            else:
                if self.answer_form is not None:
                    answer = self.answer_form(name, question)
                else:
                    dialog = FormDialog(name, question, self.parent_widget, self.lang)
                    answer = dialog.values() if dialog.exec() else None
        except Exception as e:  # noqa: BLE001 - a failed question answers "no"
            logger.warning(f"The agent's question could not be asked: {type(e).__name__}")
            answer = None
        waiter["answer"] = answer
        waiter["done"].set()

    def _on_finished(self, result: Any) -> None:
        self.running = False
        name = self.current_name
        text = self.live.text
        self.live.setVisible(False)
        self.busy_changed.emit(False)
        if text.strip():
            self.post_reply(name, text)
        if isinstance(result, Exception):
            from orchestrator.Core.disk_plan import person_reason
            self.notice(ui_text("acp.chat.error", self.lang, name=one_line(name),
                                reason=one_line(person_reason(result, self.lang))), "warning")
        elif result == "cancelled":
            self.notice(ui_text("acp.chat.stopped", self.lang, name=one_line(name)), "info")

    # --- closing ----------------------------------------------------------------------------------------------------

    def close_all(self) -> None:
        """Stops every agent this panel started (only their own processes)."""
        for host in self.hosts.values():
            host.closed.set()
        for session in list(self.sessions.values()):
            try:
                session.close()
            except Exception as e:  # noqa: BLE001 - closing never stops the window from closing
                logger.warning(f"An agent did not stop cleanly: {type(e).__name__}")
        self.sessions.clear()
        self.hosts.clear()


__all__ = ["AgentChat", "AgentLive", "AgentPicker", "FormDialog", "PermissionDialog"]
