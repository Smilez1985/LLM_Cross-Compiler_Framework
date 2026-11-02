#!/usr/bin/env python3
"""
Ditto > Agents...: the ACP agents the person picks (Core/acp_hosts.py), by mouse (`llm-cli agents ...`).

- Your agents (tbl_acp_agents): name, distribution, version, where it came from, whether it needs the internet, the
  variables of its keys. Sign in... (btn_acp_login: the agent's own sign-in methods, started on this click only),
  Store a key... (btn_acp_key: masked twice, into the credential vault), Remove... (btn_acp_remove: the full list
  and "No" as the default, Core/deletion).
- ACP registry (tbl_acp_registry): fetched only on Fetch the registry (btn_acp_refresh; the offline rule decides),
  then kept; Add the selected agent... (btn_acp_add_registry) shows name, description, distribution, licence, the
  exact command, the download and its checksum before the yes ("No" is the default).
- Your own command: name, program, arguments, "works without the internet", the variables of its keys, then Add your
  command... (the same question).

Every text from the registry or from an agent is shown as plain text. Downloads and the agent's start run on a worker
thread; the window stays usable. Qt only here: everything it does is Core/acp_hosts.py's.
"""

import shlex
import threading
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text
from orchestrator.utils.logging import get_logger

logger = get_logger("AcpAgentsWindow")


class _Relay(QObject):
    """Brings a worker thread's result back to the GUI thread: done(callback, result)."""
    done = Signal(object, object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.done.connect(lambda callback, result: callback(result), Qt.QueuedConnection)


def run_in_worker(relay: _Relay, work: Callable[[], Any], then: Callable[[Any], None]) -> threading.Thread:
    """Runs `work` on a worker thread and calls `then(result or exception)` on the GUI thread."""
    def body():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - shown to the person by `then`
            result = e
        relay.done.emit(then, result)
    thread = threading.Thread(target=body, name="acp-agents", daemon=True)
    thread.start()
    return thread


def close_in_background(session: Any) -> threading.Thread:
    """Closes an agent session on a worker thread: closing waits for the agent to stop (its input closed, then
    terminate, then kill: several seconds for an agent that ignores its closed input), which the GUI thread never
    does."""
    def body():
        try:
            session.close()
        except Exception as e:  # noqa: BLE001 - stopped by its PID in close(); nothing for the person to do
            logger.warning(f"An agent did not stop cleanly: {type(e).__name__}")
    thread = threading.Thread(target=body, name="acp-close", daemon=True)
    thread.start()
    return thread


def _button(key: str, name: str, lang: str) -> QPushButton:
    button = QPushButton(button_text(ui_text(key, lang)))
    button.setObjectName(name)
    button.setAutoDefault(False)
    return button


class AddAgentDialog(QDialog):
    """The question before an agent is added: everything the person needs to decide, "No" as the default."""

    def __init__(self, preview: Any, parent: Optional[QWidget] = None, lang: str = "en"):
        super().__init__(parent)
        e = preview.entry
        self.setWindowTitle(ui_text("acp.preview.title", lang, name=one_line(e.name)))
        self.setObjectName("dlg_acp_add")
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(ui_text("acp.preview.intro", lang)))
        internet = ui_text("acp.preview.internet.needed" if e.network else "acp.preview.internet.local", lang)
        for key, value in (("acp.preview.description", e.description), ("acp.preview.kind", e.kind),
                           ("acp.preview.licence", e.license or "-"), ("acp.preview.command", preview.command_line),
                           ("acp.preview.download", preview.download), ("acp.preview.sha256", preview.sha256),
                           ("acp.preview.internet", internet)):
            if value:
                layout.addWidget(plain_label(ui_text("acp.preview.line", lang, label=ui_text(key, lang),
                                                     value=one_line(value))))
        for problem in preview.problems:
            layout.addWidget(plain_label(ui_text("acp.preview.problem", lang, problem=one_line(problem))))
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_yes = _button("acp.preview.yes", "btn_acp_add_yes", lang)
        self.btn_no = _button("acp.preview.no", "btn_acp_add_no", lang)
        self.btn_yes.setEnabled(preview.ok)
        self.btn_no.setDefault(True)
        self.btn_yes.clicked.connect(self.accept)
        self.btn_no.clicked.connect(self.reject)
        row.addWidget(self.btn_yes)
        row.addWidget(self.btn_no)
        layout.addLayout(row)

    def plain_text(self) -> str:
        return "\n".join(label.text() for label in self.findChildren(QLabel))


class KeyDialog(QDialog):
    """A key for one agent: the variable and the value twice, masked."""

    def __init__(self, name: str, variable: str = "", parent: Optional[QWidget] = None, lang: str = "en"):
        super().__init__(parent)
        self.lang = lang
        self.setWindowTitle(ui_text("acp.key.title", lang, name=one_line(name)))
        form = QFormLayout(self)
        self.txt_variable = QLineEdit(variable)
        self.txt_variable.setObjectName("txt_acp_key_variable")
        self.txt_value = QLineEdit()
        self.txt_value.setEchoMode(QLineEdit.Password)
        self.txt_value.setObjectName("txt_acp_key_value")
        self.txt_repeat = QLineEdit()
        self.txt_repeat.setEchoMode(QLineEdit.Password)
        form.addRow(ui_text("acp.key.variable", lang), self.txt_variable)
        form.addRow(ui_text("acp.key.value", lang), self.txt_value)
        form.addRow(ui_text("acp.key.repeat", lang), self.txt_repeat)
        self.btn_store = _button("acp.key.store", "btn_acp_key_store", lang)
        self.btn_store.clicked.connect(self._check)
        form.addRow(self.btn_store)

    def _check(self) -> None:
        if self.txt_value.text() != self.txt_repeat.text():
            plain_box(self, "warning", self.windowTitle(), ui_text("acp.key.mismatch", self.lang))
            return
        self.accept()


class ManageAgentsWindow(QDialog):
    """The person's agents, the ACP registry and an own command (see the module docstring)."""

    changed = Signal()   # the agents' list changed (the chat panel's picker reads it again)

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, store: Any = None):
        super().__init__(parent)
        from orchestrator.Core.acp_hosts import AgentStore
        self.framework = framework
        self.store = store or AgentStore.for_framework(framework)
        self.lang = current_language()
        self.relay = _Relay(self)
        self.busy = False
        self.setObjectName("win_acp_agents")
        self.setWindowTitle(ui_text("acp.window.title", self.lang))
        self.setMinimumWidth(760)
        self._build()
        self.reload()

    # --- layout -----------------------------------------------------------------------------------------------------

    def _build(self) -> None:
        lang = self.lang
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(ui_text("acp.window.intro", lang)))

        mine = QGroupBox(ui_text("acp.yours", lang))
        box = QVBoxLayout(mine)
        self.tbl_agents = QTableWidget(0, 6)
        self.tbl_agents.setObjectName("tbl_acp_agents")
        self.tbl_agents.setHorizontalHeaderLabels([ui_text(f"acp.col.{c}", lang) for c in
                                                   ("name", "kind", "version", "from", "internet", "keys")])
        self.tbl_agents.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_agents.setEditTriggers(QAbstractItemView.NoEditTriggers)
        box.addWidget(self.tbl_agents)
        row = QHBoxLayout()
        self.btn_login = _button("acp.login", "btn_acp_login", lang)
        self.btn_key = _button("acp.key", "btn_acp_key", lang)
        self.btn_remove = _button("acp.remove", "btn_acp_remove", lang)
        for b in (self.btn_login, self.btn_key, self.btn_remove):
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)
        layout.addWidget(mine)

        registry = QGroupBox(ui_text("acp.registry.title", lang))
        box = QVBoxLayout(registry)
        row = QHBoxLayout()
        self.btn_refresh = _button("acp.registry.refresh", "btn_acp_refresh", lang)
        row.addWidget(self.btn_refresh)
        row.addWidget(plain_label(ui_text("acp.registry.search", lang), wrap=False))
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("txt_acp_search")
        row.addWidget(self.txt_search, stretch=1)
        box.addLayout(row)
        self.lbl_registry = plain_label("")
        box.addWidget(self.lbl_registry)
        self.tbl_registry = QTableWidget(0, 5)
        self.tbl_registry.setObjectName("tbl_acp_registry")
        self.tbl_registry.setHorizontalHeaderLabels([ui_text(f"acp.col.{c}", lang) for c in
                                                     ("name", "version", "licence", "kind", "description")])
        self.tbl_registry.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_registry.setEditTriggers(QAbstractItemView.NoEditTriggers)
        box.addWidget(self.tbl_registry)
        row = QHBoxLayout()
        row.addWidget(plain_label(ui_text("acp.kind.label", lang), wrap=False))
        self.cb_kind = QComboBox()
        self.cb_kind.setObjectName("cb_acp_kind")
        self.cb_kind.addItem(ui_text("acp.kind.auto", lang), "")
        for kind in ("npx", "uvx", "binary"):
            self.cb_kind.addItem(kind, kind)
        row.addWidget(self.cb_kind)
        self.btn_add_registry = _button("acp.add.registry", "btn_acp_add_registry", lang)
        row.addWidget(self.btn_add_registry)
        row.addStretch(1)
        box.addLayout(row)
        layout.addWidget(registry)

        own = QGroupBox(ui_text("acp.own.title", lang))
        form = QFormLayout(own)
        self.txt_name = QLineEdit()
        self.txt_name.setObjectName("txt_acp_name")
        form.addRow(ui_text("acp.own.name", lang), self.txt_name)
        program = QHBoxLayout()
        self.txt_command = QLineEdit()
        self.txt_command.setObjectName("txt_acp_command")
        self.btn_browse = _button("acp.own.browse", "btn_acp_browse", lang)
        program.addWidget(self.txt_command, stretch=1)
        program.addWidget(self.btn_browse)
        form.addRow(ui_text("acp.own.command", lang), program)
        self.txt_args = QLineEdit()
        self.txt_args.setObjectName("txt_acp_args")
        form.addRow(ui_text("acp.own.args", lang), self.txt_args)
        self.chk_local = QCheckBox(ui_text("acp.own.local", lang))
        self.chk_local.setObjectName("chk_acp_local")
        form.addRow(self.chk_local)
        self.txt_keys = QLineEdit()
        self.txt_keys.setObjectName("txt_acp_keys")
        form.addRow(ui_text("acp.own.keys", lang), self.txt_keys)
        self.btn_add_command = _button("acp.add.command", "btn_acp_add_command", lang)
        form.addRow(self.btn_add_command)
        layout.addWidget(own)

        self.lbl_status = plain_label("")
        self.lbl_status.setObjectName("lbl_acp_status")
        layout.addWidget(self.lbl_status)

        self.btn_refresh.clicked.connect(self.refresh_registry)
        self.txt_search.textChanged.connect(lambda _t: self._show_registry())
        self.btn_add_registry.clicked.connect(self.add_from_registry)
        self.btn_add_command.clicked.connect(self.add_command)
        self.btn_browse.clicked.connect(self._browse)
        self.btn_login.clicked.connect(self.sign_in)
        self.btn_key.clicked.connect(self.store_key)
        self.btn_remove.clicked.connect(self.remove)

    # --- reading ----------------------------------------------------------------------------------------------------

    def reload(self) -> None:
        entries = self.store.list()
        self.tbl_agents.setRowCount(len(entries))
        for row, e in enumerate(entries):
            values = (e.name, e.kind, e.version or "-",
                      ui_text("acp.from.registry" if e.source == "registry" else "acp.from.command", self.lang),
                      ui_text("acp.internet.needed" if e.network else "acp.internet.local", self.lang),
                      ", ".join(e.keys) or "-")
            for col, value in enumerate(values):
                item = QTableWidgetItem(one_line(value))
                item.setData(Qt.UserRole, e.id)
                self.tbl_agents.setItem(row, col, item)
        self._show_registry()

    def _show_registry(self) -> None:
        agents, fetched = self.store.cached_registry()
        if fetched is None:
            self.lbl_registry.setText(ui_text("acp.registry.never", self.lang))
        else:
            self.lbl_registry.setText(ui_text("acp.registry.count", self.lang, n=len(agents)))
        shown = self.store.search(self.txt_search.text()) if fetched is not None else []
        self.tbl_registry.setRowCount(len(shown))
        for row, a in enumerate(shown):
            for col, value in enumerate((a.name, a.version, a.license or "-", ", ".join(a.kinds()), a.description)):
                item = QTableWidgetItem(one_line(value))
                item.setData(Qt.UserRole, a.id)
                self.tbl_registry.setItem(row, col, item)

    def selected_agent(self) -> Optional[str]:
        row = self.tbl_agents.currentRow()
        item = self.tbl_agents.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    def selected_registry_agent(self) -> Optional[str]:
        row = self.tbl_registry.currentRow()
        item = self.tbl_registry.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    # --- helpers ----------------------------------------------------------------------------------------------------

    def _say(self, message: str) -> None:
        self.lbl_status.setText(one_line(message))

    def _failed(self, what: str, error: BaseException) -> None:
        from orchestrator.Core.disk_plan import person_reason
        self._say(ui_text("acp.failed", self.lang, what=what, reason=one_line(person_reason(error, self.lang))))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for b in (self.btn_refresh, self.btn_add_registry, self.btn_add_command, self.btn_login, self.btn_key,
                  self.btn_remove):
            b.setEnabled(not busy)
        if busy:
            self._say(ui_text("acp.working", self.lang))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, ui_text("acp.own.command", self.lang))
        if path:
            self.txt_command.setText(path)

    # --- the registry -----------------------------------------------------------------------------------------------

    def refresh_registry(self) -> None:
        """Fetches the registry (worker thread) on this click only."""
        self._set_busy(True)

        def done(result: Any) -> None:
            self._set_busy(False)
            if isinstance(result, Exception):
                self._failed(ui_text("acp.registry.refresh", self.lang), result)
                return
            self._say(ui_text("acp.registry.count", self.lang, n=len(result)))
            self._show_registry()
        run_in_worker(self.relay, lambda: self.store.refresh_registry("gui"), done)

    def _ask_and_add(self, preview: Any) -> None:
        if not AddAgentDialog(preview, self, self.lang).exec():
            return
        self._set_busy(True)

        def done(result: Any) -> None:
            self._set_busy(False)
            if isinstance(result, Exception):
                self._failed(ui_text("acp.add.registry", self.lang), result)
                return
            self._say(ui_text("acp.added", self.lang, name=one_line(result.name)))
            self.reload()
            self.changed.emit()
        run_in_worker(self.relay, lambda: self.store.add(preview, "gui"), done)

    def add_from_registry(self) -> None:
        from orchestrator.Core.acp_hosts import AgentError
        agent_id = self.selected_registry_agent()
        if not agent_id:
            self._say(ui_text("acp.select_first", self.lang))
            return
        try:
            preview = self.store.preview_registry(agent_id, self.cb_kind.currentData() or "")
        except AgentError as e:
            self._failed(ui_text("acp.add.registry", self.lang), e)
            return
        self._ask_and_add(preview)

    def add_command(self) -> None:
        from orchestrator.Core.acp_hosts import AgentError
        try:
            args = shlex.split(self.txt_args.text(), posix=True)
            keys = [k.strip() for k in self.txt_keys.text().split(",") if k.strip()]
            preview = self.store.preview_command(self.txt_name.text(), self.txt_command.text(), args,
                                                 local=self.chk_local.isChecked(), keys=keys)
        except (AgentError, ValueError) as e:
            self._failed(ui_text("acp.add.command", self.lang), e)
            return
        self._ask_and_add(preview)

    # --- keys, sign-in, removal -------------------------------------------------------------------------------------

    def store_key(self, variable: str = "") -> bool:
        from orchestrator.Core.acp_hosts import AgentError
        agent_id = self.selected_agent()
        if not agent_id:
            self._say(ui_text("acp.select_first", self.lang))
            return False
        entry = self.store.get(agent_id)
        dialog = KeyDialog(entry.name, variable or (entry.keys[0] if entry.keys else ""), self, self.lang)
        if not dialog.exec():
            return False
        try:
            self.store.set_key(agent_id, dialog.txt_variable.text().strip(), dialog.txt_value.text(), "gui")
        except AgentError as e:
            self._failed(ui_text("acp.key", self.lang), e)
            return False
        self._say(ui_text("acp.key.stored", self.lang, name=one_line(entry.name),
                          variable=one_line(dialog.txt_variable.text().strip())))
        self.reload()
        return True

    def sign_in(self) -> None:
        """Starts the selected agent (worker thread) to read its sign-in methods, then asks which one."""
        agent_id = self.selected_agent()
        if not agent_id:
            self._say(ui_text("acp.select_first", self.lang))
            return
        from orchestrator.Core.acp_client import FIRST_START_TIMEOUT, AgentHost, AgentSession
        try:
            entry = self.store.get(agent_id)
            if self.store.first_start(entry):   # the wait line of a first start that may download the package
                self._say(ui_text("acp.chat.first_start", self.lang, name=one_line(entry.name),
                                  minutes=int(FIRST_START_TIMEOUT // 60)))
        except Exception:  # noqa: BLE001 - an agent that cannot be read: the start says why
            pass
        self._set_busy(True)
        holder: dict = {}

        def work():
            spec = self.store.launch_spec(agent_id, "gui")
            session = AgentSession(spec, AgentHost(), self.store, "gui")
            holder["session"], holder["spec"] = session, spec
            return session.start()

        def done(result: Any) -> None:
            self._set_busy(False)
            session = holder.get("session")
            if isinstance(result, Exception):
                if session is not None:
                    close_in_background(session)
                self._failed(ui_text("acp.login", self.lang), result)
                return
            self._choose_sign_in(holder["spec"], session, result)
        run_in_worker(self.relay, work, done)

    def _choose_sign_in(self, spec: Any, session: Any, methods: List[Any]) -> None:
        name = one_line(spec.entry.name)
        if not methods:
            close_in_background(session)
            self._say(ui_text("acp.login.none", self.lang, name=name))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(ui_text("acp.login.title", self.lang, name=name))
        layout = QVBoxLayout(dialog)
        layout.addWidget(plain_label(ui_text("acp.login.choose", self.lang, name=name)))
        choices = QListWidget()
        choices.setObjectName("lst_acp_login_methods")
        for m in methods:
            choices.addItem(one_line(f"{m.name} ({m.type})" + (f": {m.description}" if m.description else "")))
        choices.setCurrentRow(0)
        layout.addWidget(choices)
        row = QHBoxLayout()
        go = _button("acp.login.go", "btn_acp_login_go", self.lang)
        cancel = _button("acp.login.cancel", "btn_acp_login_cancel", self.lang)
        cancel.setDefault(True)
        go.clicked.connect(dialog.accept)
        cancel.clicked.connect(dialog.reject)
        row.addStretch(1)
        row.addWidget(go)
        row.addWidget(cancel)
        layout.addLayout(row)
        if not dialog.exec() or choices.currentRow() < 0:
            close_in_background(session)
            return
        method = methods[choices.currentRow()]
        if method.type == "terminal":
            close_in_background(session)
            from orchestrator.Core.acp_client import open_console
            try:
                open_console(spec, method, self.store)
            except Exception as e:  # noqa: BLE001 - no terminal program, or it could not be started
                self._failed(ui_text("acp.login", self.lang), e)
                return
            self.store.record_event("gui", spec.entry, "terminal sign-in opened")
            self._say(ui_text("acp.login.console", self.lang, name=name))
            return
        if method.type == "env_var":
            close_in_background(session)
            plain_box(self, "information", dialog.windowTitle(), ui_text("acp.login.keys", self.lang, name=name))
            for variable in method.variables:
                if not self.store_key(variable):
                    break
            return
        self._say(ui_text("acp.login.agent", self.lang, name=name))
        self._set_busy(True)

        def done(result: Any) -> None:
            close_in_background(session)
            self._set_busy(False)
            if isinstance(result, Exception):
                self._failed(ui_text("acp.login", self.lang), result)
            else:
                self._say(ui_text("acp.login.done", self.lang, name=name))
        run_in_worker(self.relay, lambda: session.authenticate(method.id), done)

    def remove(self) -> None:
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        agent_id = self.selected_agent()
        if not agent_id:
            self._say(ui_text("acp.select_first", self.lang))
            return
        plan = self.store.deletion_plan([agent_id])
        if plan is None:
            return
        accepted = ask_deletion(self, plan)
        if accepted is None:
            return
        try:
            result = self.store.delete(accepted, trash=Trash.for_framework(self.framework))
        except (PlanChanged, PermissionError, RuntimeError) as e:
            self._failed(ui_text("acp.remove", self.lang), e)
            return
        self._say(ui_text("acp.removed", self.lang, names=", ".join(result.done)))
        self.reload()
        self.changed.emit()


def open_agents_window(window: Any, framework: Any = None, panel: Any = None) -> Optional[ManageAgentsWindow]:
    """Shows the window (one per main window; a second click raises it). The chat panel's picker (the main window's
    chat_window, or `panel`) follows its changes."""
    existing = getattr(window, "acp_agents_window", None)
    if existing is not None:
        existing.reload()
        existing.show()
        existing.raise_()
        return existing
    framework = framework if framework is not None else getattr(window, "framework", None)
    try:
        dialog = ManageAgentsWindow(framework, window)
    except Exception as e:  # noqa: BLE001 - no framework home: said, never a crash
        plain_box(window, "warning", ui_text("acp.window.title"), one_line(str(e)))
        return None
    window.acp_agents_window = dialog
    panel = panel if panel is not None else getattr(window, "chat_window", None)
    if panel is not None and hasattr(panel, "reload_agents"):
        dialog.changed.connect(panel.reload_agents)
    dialog.show()
    return dialog


__all__ = ["AddAgentDialog", "KeyDialog", "ManageAgentsWindow", "close_in_background", "open_agents_window",
           "run_in_worker"]
