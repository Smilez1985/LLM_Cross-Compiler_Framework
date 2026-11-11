#!/usr/bin/env python3
"""
The crash dialog: what the person sees when the GUI meets an error nobody caught (docs/DESIGN.md, GUI and the
person's path: starting without a terminal).

`install(app, folder)` sets sys.excepthook and threading.excepthook once Qt runs. Each error is logged first (the
scrubbed details, logger "llmcf.crash"), then shown on the GUI thread (from another thread through a queued signal of
a QObject that lives there). One dialog at a time: an error while it is open or waiting is logged and counted, and the
open dialog says how many more were logged. The dialog is the error box (gui/error_dialog.ErrorDialog) with a short
plain message, "Open log folder", "Copy details" (the traceback, llmcf's version, Python and the operating system,
scrubbed by utils/crash_report.crash_details) and Close; the program keeps running after Close. Ctrl+C keeps
Python's own handling.
"""

import logging
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton

from orchestrator.gui.error_dialog import ErrorDialog
from orchestrator.gui.plain import button_text, plain_label
from orchestrator.utils.crash_report import crash_details
from orchestrator.utils.localization import ui_text

log = logging.getLogger("llmcf.crash")


def open_folder(folder: Path) -> bool:
    """Opens `folder` in the file manager, made first when it is missing (tests replace this)."""
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))


class CrashDialog(ErrorDialog):
    """The error box for an unexpected error: the message, the details folded away, "Open log folder", "Copy
    details" and Close."""

    def __init__(self, details: str, folder: Path, parent: Any = None, lang: Optional[str] = None,
                 opener: Optional[Callable[[Path], bool]] = None):
        super().__init__(parent, ui_text("crash.summary", lang), why=ui_text("crash.why", lang), details=details,
                         title=ui_text("crash.title", lang), kind="critical", lang=lang)
        self.setObjectName("crash_dialog")
        self.folder = Path(folder)
        self.opener = opener
        self.lbl_more = plain_label("", self)
        self.lbl_more.setObjectName("lbl_crash_more")
        self.lbl_more.setVisible(False)
        self.layout().insertWidget(2, self.lbl_more)
        self.btn_open_log = QPushButton(button_text(ui_text("crash.open_log", self.lang)), self)
        self.btn_open_log.setObjectName("btn_crash_open_log")
        self.btn_open_log.setAutoDefault(False)
        self.btn_open_log.clicked.connect(self.open_log_folder)
        row = QHBoxLayout()
        row.addWidget(self.btn_open_log)
        row.addStretch(1)
        self.layout().insertLayout(3, row)

    def set_more(self, count: int) -> None:
        """How many more errors were logged while this dialog was open or waiting."""
        self.lbl_more.setText(ui_text("crash.more", self.lang, count=count) if count else "")
        self.lbl_more.setVisible(bool(count))

    def open_log_folder(self) -> bool:
        return bool((self.opener or open_folder)(self.folder))


class CrashReporter(QObject):
    """Logs every uncaught error and shows at most one CrashDialog at a time, always on the GUI thread."""

    reported = Signal(str)

    def __init__(self, folder: Path, app: Optional[QApplication] = None,
                 opener: Optional[Callable[[Path], bool]] = None):
        super().__init__(None)
        app = app or QApplication.instance()
        if app is not None:
            self.moveToThread(app.thread())
        self.folder = Path(folder)
        self.opener = opener
        self.lock = threading.Lock()
        self.busy = False      # a dialog is open or on its way
        self.more = 0          # errors logged while it was
        self.dialog: Optional[CrashDialog] = None
        self.shown = 0
        self.previous_hook = sys.excepthook
        self.previous_thread_hook = threading.excepthook
        self.reported.connect(self._show, Qt.QueuedConnection)

    def handle(self, kind: Any, error: BaseException, trace: Any = None, where: str = "") -> None:
        """Logs the error and shows the dialog unless one is open or waiting (then it is counted)."""
        if error is not None and trace is not None and error.__traceback__ is None:
            error = error.with_traceback(trace)
        details = crash_details(error, where)
        log.critical("Unexpected error%s:\n%s", f" in {where}" if where else "", details)
        with self.lock:
            if self.busy:
                self.more += 1
                if self.dialog is not None:
                    self.reported.emit("")   # the open dialog's count is updated on the GUI thread
                return
            self.busy = True
            self.more = 0
        self.reported.emit(details)

    def show_now(self, error: BaseException, where: str = "") -> None:
        """Logs the error and shows the dialog at once, on the calling (GUI) thread: for an error before the event
        loop runs, when a queued signal would never be delivered."""
        details = crash_details(error, where)
        log.critical("Unexpected error%s:\n%s", f" in {where}" if where else "", details)
        with self.lock:
            if self.busy:
                self.more += 1
                return
            self.busy = True
            self.more = 0
        self._show(details)

    def excepthook(self, kind: Any, error: BaseException, trace: Any) -> None:
        if isinstance(error, KeyboardInterrupt) or not isinstance(error, Exception):
            self.previous_hook(kind, error, trace)
            return
        self.handle(kind, error, trace)

    def thread_excepthook(self, args: Any) -> None:
        if args.exc_type is SystemExit or not issubclass(args.exc_type, Exception):
            self.previous_thread_hook(args)
            return
        name = getattr(args.thread, "name", "") or "a background thread"
        self.handle(args.exc_type, args.exc_value, args.exc_traceback, f"thread {name}")

    @Slot(str)
    def _show(self, details: str) -> None:
        if not details:
            if self.dialog is not None:
                with self.lock:
                    self.dialog.set_more(self.more)
            return
        try:
            self.dialog = CrashDialog(details, self.folder, parent=QApplication.activeWindow(), opener=self.opener)
            with self.lock:
                self.dialog.set_more(self.more)
            self.shown += 1
            _exec(self.dialog)
        except Exception:  # noqa: BLE001 - the dialog itself failing must not loop through this hook again
            log.exception("The crash dialog could not be shown")
        finally:
            with self.lock:
                self.busy = False
                self.more = 0
                dialog, self.dialog = self.dialog, None
            if dialog is not None:
                dialog.deleteLater()


def _exec(dialog: CrashDialog) -> int:
    """Shows the dialog modally (tests replace this)."""
    return dialog.exec()


_reporter: Optional[CrashReporter] = None


def install(folder: Path, app: Optional[QApplication] = None,
            opener: Optional[Callable[[Path], bool]] = None) -> CrashReporter:
    """Sets sys.excepthook and threading.excepthook to the crash reporter (once; a second call returns it)."""
    global _reporter
    if _reporter is None:
        _reporter = CrashReporter(folder, app, opener)
        sys.excepthook = _reporter.excepthook
        threading.excepthook = _reporter.thread_excepthook
    return _reporter


def report_now(error: BaseException, where: str = "") -> None:
    """The crash dialog for `error` right now (the reporter install made, else one for the default log folder)."""
    if _reporter is None:
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.utils.crash_report import log_folder
        try:
            folder = log_folder(ConfigManager())
        except Exception:  # noqa: BLE001 - the settings may be what failed
            folder = log_folder()
        CrashReporter(folder).show_now(error, where)
        return
    _reporter.show_now(error, where)


def uninstall() -> None:
    """Puts the earlier hooks back (tests)."""
    global _reporter
    if _reporter is not None:
        sys.excepthook = _reporter.previous_hook
        threading.excepthook = _reporter.previous_thread_hook
        _reporter.deleteLater()
        _reporter = None


__all__ = ["CrashDialog", "CrashReporter", "install", "open_folder", "report_now", "uninstall"]
