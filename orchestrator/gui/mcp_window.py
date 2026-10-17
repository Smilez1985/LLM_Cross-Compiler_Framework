#!/usr/bin/env python3
"""
Security > AI App Connections (MCP)... (wave 5, GUI parity: every `llmcf mcp` action by mouse).

* The connection entries for Claude Code, Claude Desktop (with the path of its configuration file) and VS Code, each
  with a Copy button (`mcp config`, mcp_server.client_snippets).
* Write to Claude Desktop... (`mcp config --write claude-desktop`): shows the file, whether it exists, what changes,
  the entries that are kept and the backup, asks ("No" is the default) and writes exactly what it showed
  (mcp_server.plan_claude_desktop_config / write_claude_desktop_config, the CLI's functions; the CLI's backup).
* The local HTTP server (`mcp serve --transport http [--host] [--port] [--container]`) as a child process of the GUI
  with the CLI's own entry (mcp_server.http_serve_command): the same refusals before it starts (http_refusal), its
  address, state and output shown, Stop, and a question before the framework closes while it still serves.
* The named tokens (`mcp token add [--policy-from] / rotate / revoke`, `tools permissions --reset --token`) through
  TokenListMixin: a token is shown once, right after it is made or rotated; the list shows names only.
Every text is plain text; nothing here goes to an AI.
"""

import collections
import os
import signal
import subprocess  # nosec B404 - runs the framework's own CLI entry with an argument list, never a shell
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from orchestrator.gui.dialogs import TokenListMixin
from orchestrator.gui.plain import ask, button_text, display_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text

SNIPPETS = (("claude-code", "tmcp.mcp.claude_code"), ("claude-desktop", "tmcp.mcp.claude_desktop"),
            ("vscode", "tmcp.mcp.vscode"))
LOOPBACK_CHOICES = ("127.0.0.1", "::1", "localhost")
DEFAULT_PORT = 8765           # the CLI's --port default
STOP_WAIT = 15.0              # seconds the server gets to end its sessions before it is stopped at once
INPUT_WAIT = 5.0              # of those, seconds after its input closed before Ctrl+Break / SIGTERM follows
SERVING_MARK = "MCP server on http"   # the line mcp_server.serve() writes once it listens


def _redact(text: str) -> str:
    from orchestrator.utils.helpers import redact_secrets
    return redact_secrets(text)


class McpHttpServer(QObject):
    """The HTTP MCP server as a child process of the GUI: started with the CLI's entry, its output kept (the last
    lines), stopped on request or when the framework closes. One per main window; it outlives the window that
    started it, not the GUI. `changed` fires on the GUI thread whenever its state or output changes."""

    changed = Signal()

    def __init__(self, framework, app_root: Optional[Path] = None,
                 command_for: Optional[Callable[[str, int, bool], Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self.framework = framework
        self.app_root = app_root
        self._command_for = command_for
        self.process: Optional[subprocess.Popen] = None
        self.state = "off"           # off, starting, running, stopping, stopped, failed
        self.url = ""
        self.exit_code: Optional[int] = None
        self.lines: Deque[str] = collections.deque(maxlen=200)
        self._token: Optional[object] = None
        self._quit_hooked = False
        worker_relay().done.connect(self._relay_done, Qt.QueuedConnection)

    # --- facts ------------------------------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process is not None else None

    def _tokens(self):
        from orchestrator.tools.mcp_tokens import TokenStore
        tokens = getattr(getattr(self.framework, "tool_registry", None), "tokens", None)
        return tokens if tokens is not None else TokenStore(Path(self.framework.config.user_state_dir))

    def refusal(self, host: str, container: bool) -> Optional[str]:
        """Why `mcp serve --transport http` would refuse these settings (the server's own checks), or None."""
        from orchestrator import mcp_server
        tokens = self._tokens()
        return mcp_server.http_refusal(tokens, tokens.path.parent, host, container)

    def command(self, host: str, port: int, container: bool) -> Dict[str, Any]:
        from orchestrator import mcp_server
        if self._command_for is not None:
            return self._command_for(host, port, container)
        return mcp_server.http_serve_command(self.app_root or mcp_server.installation_root(), host, port, container)

    # --- start and stop -----------------------------------------------------------------------------------------------

    def start(self, host: str, port: int, container: bool = False) -> Optional[str]:
        """Starts the server unless it runs already or the CLI would refuse; returns why it did not start, or None.
        The child gets this environment without LCF_MCP_TOKEN (each HTTP client sends its own token)."""
        from orchestrator import mcp_server
        from orchestrator.tools.mcp_env import ENV_VAR
        if self.running:
            return None
        problem = self.refusal(host, container)
        if problem:
            return problem
        try:
            command = self.command(host, port, container)
        except ValueError as e:   # e.g. an installation path that PYTHONPATH cannot hold (launch_command)
            return str(e)
        env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
        env.update(command.get("env") or {})
        env[mcp_server.STOP_ON_EOF_ENV] = "1"   # Stop closes the child's input (Ctrl+Break needs a console on Windows)
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":   # a group of its own, so Ctrl+Break can end it like Ctrl+C would
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | _no_window_flag()
        try:
            process = subprocess.Popen(  # nosec B603 - the framework's own interpreter and CLI entry, no shell
                [command["command"], *command["args"]], cwd=command.get("cwd"), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                **kwargs)
        except OSError as e:
            return ui_text("tmcp.http.start_failed", reason=str(e))
        self.process, self.exit_code = process, None
        self.url = mcp_server.http_url(host, port, container)
        self.state = "starting"
        self.lines.clear()
        token = self._token = object()
        relay = worker_relay()

        def read() -> None:  # the child's output, line by line, until it ends
            for line in process.stdout:
                relay.done.emit(token, ("line", line.rstrip("\r\n")))
            relay.done.emit(token, ("exit", process.wait()))
        threading.Thread(target=read, name="mcp-http-output", daemon=True).start()
        app = QCoreApplication.instance()
        if app is not None and not self._quit_hooked:   # never left behind when the GUI quits another way
            app.aboutToQuit.connect(self.stop_and_wait)
            self._quit_hooked = True
        self.changed.emit()
        return None

    def _signal_stop(self) -> None:
        """The first step of a stop: the end of the server's input. The server ends as on Ctrl+C (its sessions end,
        their builds and jobs are cancelled), on every system; on Windows a GUI without a console cannot send it
        Ctrl+Break."""
        process = self.process
        if process is None or process.poll() is not None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _interrupt(process) -> None:
        """The second step, when the server did not end within INPUT_WAIT seconds: Ctrl+Break (Windows) or SIGTERM."""
        try:
            if sys.platform == "win32":
                os.kill(process.pid, signal.CTRL_BREAK_EVENT)   # the server ends its sessions (uvicorn: SIGBREAK)
            else:
                process.terminate()
        except OSError:
            process.terminate()

    def _wait_or_kill(self, timeout: float) -> None:
        """Waits for the server after its input closed; after INPUT_WAIT seconds interrupts it, after `timeout`
        seconds in all ends it at once."""
        process = self.process
        if process is None:
            return
        first = min(INPUT_WAIT, timeout)
        for step, wait in ((None, first), (self._interrupt, max(timeout - first, 0.1))):
            if step is not None and process.poll() is None:
                step(process)
            try:
                process.wait(wait)
                return
            except subprocess.TimeoutExpired:
                continue
        process.kill()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            pass

    def stop(self) -> bool:
        """Ends the server: first as Ctrl+C would (its sessions' builds and jobs are cancelled, the framework shuts
        down), after STOP_WAIT seconds at once. The waiting runs off the GUI thread."""
        if not self.running:
            return False
        self.state = "stopping"
        self.changed.emit()
        self._signal_stop()
        threading.Thread(target=self._wait_or_kill, args=(STOP_WAIT,), name="mcp-http-stop", daemon=True).start()
        return True

    def stop_and_wait(self, timeout: float = STOP_WAIT) -> None:
        """For closing the framework: ends the server and waits for it (at most `timeout` seconds, then at once)."""
        if not self.running:
            return
        self.state = "stopping"
        self._signal_stop()
        self._wait_or_kill(timeout)

    def _relay_done(self, token: object, event: Any) -> None:
        if token is not self._token:
            return
        kind, value = event
        if kind == "line":
            line = _redact(value)
            self.lines.append(line)
            if self.state == "starting" and SERVING_MARK in line:
                self.state = "running"
        elif kind == "exit":
            self.exit_code = value
            self.state = "stopped" if self.state == "stopping" or value == 0 else "failed"
        self.changed.emit()

    def state_text(self, lang: Optional[str] = None) -> str:
        if self.state == "starting":
            return ui_text("tmcp.http.state.starting", lang, url=self.url)
        if self.state == "running":
            return ui_text("tmcp.http.state.running", lang, url=self.url, pid=self.pid)
        if self.state == "stopping":
            return ui_text("tmcp.http.state.stopping", lang)
        if self.state == "stopped":
            return ui_text("tmcp.http.state.stopped", lang, code=self.exit_code)
        if self.state == "failed":
            last = next((line for line in reversed(self.lines) if line.strip()), "")
            return ui_text("tmcp.http.state.failed", lang, code=self.exit_code, reason=one_line(last))
        return ui_text("tmcp.http.state.off", lang)


def _no_window_flag() -> int:
    """CREATE_NO_WINDOW when the GUI itself has no console (pythonw), so the server opens no console window."""
    try:
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow() == 0:   # type: ignore[attr-defined]
            return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    except (AttributeError, OSError):
        pass
    return 0


def server_of(window) -> McpHttpServer:
    """The main window's HTTP server controller (made on first use; it outlives the AI App Connections window)."""
    server = getattr(window, "mcp_http_server", None)
    if server is None:
        server = McpHttpServer(window.framework, getattr(window, "app_root", None), parent=window)
        window.mcp_http_server = server
    return server


class McpConnectionsWindow(QDialog, TokenListMixin):
    """Security > AI App Connections (MCP)...: the connection entries, Write to Claude Desktop..., the HTTP server
    and the named tokens."""

    def __init__(self, framework, server: McpHttpServer, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 app_root: Optional[Path] = None):
        super().__init__(parent)
        from orchestrator import mcp_server
        self.framework = framework
        self.registry = getattr(framework, "tool_registry", None)
        self.server = server
        self.lang = lang or current_language()
        self.app_root = Path(app_root) if app_root else mcp_server.installation_root()
        self.setWindowTitle(self._t("tmcp.mcp.title"))
        self.resize(900, 860)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("tmcp.mcp.intro")))

        self.snippet_fields: Dict[str, QPlainTextEdit] = {}
        try:
            snippets = mcp_server.client_snippets(self.app_root)
            problem = ""
        except (ValueError, OSError) as e:
            snippets, problem = {}, str(e)
        if problem:
            layout.addWidget(plain_label(self._t("tmcp.mcp.snippets_failed", reason=one_line(problem))))
        for key, title_key in SNIPPETS if snippets else ():
            title = self._t(title_key, path=str(mcp_server.claude_desktop_config_path())) \
                if key == "claude-desktop" else self._t(title_key)
            box = QGroupBox()
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(plain_label(title))
            row = QHBoxLayout()
            field = QPlainTextEdit(snippets[key])
            field.setObjectName(f"txt_mcp_{key}")
            field.setReadOnly(True)
            field.setMaximumHeight(110 if key != "claude-code" else 60)
            row.addWidget(field, 1)
            copy = QPushButton(button_text(self._t("tmcp.mcp.copy")))
            copy.setObjectName(f"btn_mcp_copy_{key}")
            copy.setAutoDefault(False)
            copy.clicked.connect(lambda _=False, k=key: self.copy(k))
            row.addWidget(copy)
            box_layout.addLayout(row)
            if key == "claude-desktop":
                self.btn_write = QPushButton(button_text(self._t("tmcp.mcp.write")))
                self.btn_write.setObjectName("btn_mcp_write_desktop")
                self.btn_write.setAutoDefault(False)
                self.btn_write.clicked.connect(lambda _=False: self.write_desktop())
                box_layout.addWidget(self.btn_write)
            layout.addWidget(box)
            self.snippet_fields[key] = field
        self.copied = plain_label("")
        layout.addWidget(self.copied)

        http = QGroupBox(self._t("tmcp.http.box"))
        http_layout = QVBoxLayout(http)
        http_layout.addWidget(plain_label(self._t("tmcp.http.note")))
        row = QHBoxLayout()
        row.addWidget(plain_label(self._t("tmcp.http.host"), wrap=False, selectable=False))
        self.host = QComboBox()
        self.host.setObjectName("combo_mcp_host")
        self.host.setEditable(True)   # any value; the server's own checks decide (loopback, or the container's)
        self.host.addItems(LOOPBACK_CHOICES)
        row.addWidget(self.host)
        row.addWidget(plain_label(self._t("tmcp.http.port"), wrap=False, selectable=False))
        self.port = QSpinBox()
        self.port.setObjectName("spin_mcp_port")
        self.port.setRange(1, 65535)
        self.port.setValue(DEFAULT_PORT)
        row.addWidget(self.port)
        self.container = QCheckBox(button_text(self._t("tmcp.http.container")))
        self.container.setObjectName("chk_mcp_container")
        row.addWidget(self.container)
        row.addStretch()
        self.btn_start = QPushButton(button_text(self._t("tmcp.http.start")))
        self.btn_start.setObjectName("btn_mcp_http_start")
        self.btn_stop = QPushButton(button_text(self._t("tmcp.http.stop")))
        self.btn_stop.setObjectName("btn_mcp_http_stop")
        for button, slot in ((self.btn_start, self.start_server), (self.btn_stop, self.stop_server)):
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, s=slot: s())
            row.addWidget(button)
        http_layout.addLayout(row)
        self.state = plain_label("")
        self.state.setObjectName("lbl_mcp_http_state")
        http_layout.addWidget(self.state)
        self.output = QPlainTextEdit()
        self.output.setObjectName("txt_mcp_http_log")
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(110)
        http_layout.addWidget(self.output)
        layout.addWidget(http)

        if self.registry is not None:
            layout.addWidget(self._token_section())
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        server.changed.connect(self._server_changed)
        self._server_changed()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    # --- `mcp config` -------------------------------------------------------------------------------------------------

    def copy(self, key: str) -> None:
        QGuiApplication.clipboard().setText(self.snippet_fields[key].toPlainText())
        self.copied.setText(self._t("tmcp.mcp.copied"))

    def write_desktop(self, path: Optional[Path] = None) -> bool:
        """`mcp config --write claude-desktop`: shows the file and what changes, asks ("No" is the default), writes
        exactly that (a file changed meanwhile is refused) and keeps the CLI's backup."""
        import json

        from orchestrator import mcp_server
        title = self.windowTitle()
        try:
            plan = mcp_server.plan_claude_desktop_config(self.app_root, path)
        except (ValueError, OSError) as e:
            plain_box(self, "warning", title, self._t("tmcp.mcp.write.failed", reason=one_line(str(e))))
            return False
        if plan.unchanged:
            plain_box(self, "information", title, self._t("tmcp.mcp.write.unchanged", path=str(plan.path)))
            return False
        name = mcp_server.SERVER_NAME
        question = self._t(
            "tmcp.mcp.write.question", path=str(plan.path),
            state=self._t("tmcp.mcp.write.exists" if plan.original is not None else "tmcp.mcp.write.new"),
            change=self._t("tmcp.mcp.write.replaces" if plan.replaces else "tmcp.mcp.write.adds", name=name),
            others=", ".join(one_line(o) for o in plan.others) or self._t("tmcp.mcp.write.none"),
            backup=self._t("tmcp.mcp.write.backup", path=str(plan.backup)) if plan.backup is not None
            else self._t("tmcp.mcp.write.no_backup"),
            entry=display_text(json.dumps({name: plan.entry}, indent=2)))
        if not ask(self, title, question):
            return False
        try:
            written = mcp_server.write_claude_desktop_config(self.app_root, plan.path, plan=plan)
        except (ValueError, OSError) as e:
            plain_box(self, "warning", title, self._t("tmcp.mcp.write.failed", reason=one_line(str(e))))
            return False
        plain_box(self, "information", title, self._t("tmcp.mcp.write.done", path=str(written)))
        return True

    # --- `mcp serve --transport http` ------------------------------------------------------------------------------------

    def start_server(self) -> bool:
        problem = self.server.start(self.host.currentText().strip(), self.port.value(), self.container.isChecked())
        if problem:
            plain_box(self, "warning", self.windowTitle(), self._t("tmcp.http.refused", reason=one_line(problem)))
            return False
        return True

    def stop_server(self) -> bool:
        return self.server.stop()

    def _server_changed(self) -> None:
        running = self.server.running
        self.state.setText(self.server.state_text(self.lang))
        self.output.setPlainText("\n".join(display_text(line) for line in self.server.lines))
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running and self.server.state != "stopping")
        for widget in (self.host, self.port, self.container):
            widget.setEnabled(not running)


def open_mcp_window(window, lang: Optional[str] = None) -> Optional[McpConnectionsWindow]:
    """Shows Security > AI App Connections (MCP)... of the main window (an open one comes to the front)."""
    current = getattr(window, "mcp_window", None)
    try:
        if current is not None and current.isVisible():
            current.raise_()
            current.activateWindow()
            return current
    except RuntimeError:   # its window object is gone
        current = None
    try:
        dialog = McpConnectionsWindow(window.framework, server_of(window), window, lang=lang,
                                      app_root=None)
    except Exception as e:  # noqa: BLE001 - plain text (SEC-M2)
        plain_box(window, "critical", ui_text("tmcp.mcp.title", lang), str(e))
        return None
    if current is not None:
        current.deleteLater()
    window.mcp_window = dialog
    dialog.show()
    return dialog


__all__ = ["McpConnectionsWindow", "McpHttpServer", "open_mcp_window", "server_of"]
