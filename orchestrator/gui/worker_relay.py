#!/usr/bin/env python3
"""
Hands results of plain worker threads (threading.Thread) to the GUI thread.

A worker thread must never emit on, or free, a Qt object that a window owns or created for it: a closed window can
take the object with it while the worker still runs, and a QObject whose last reference goes away in a worker
thread is destroyed there, which makes Qt abort. The relay is a single QObject, created in the GUI thread and never
deleted. Receivers connect to `done` with a queued connection and pick their own results by a token they created
(an `object()`), so their slot runs in the GUI thread, or not at all once the receiver is gone. Every worker of the
GUI uses this one relay: the AI settings' model lists, the module wizard's AI import, the chat window's access check
and the sign-in panel.

A worker that needs a yes/no answer before it goes on (the CLI asks at that point too: fetch the source?, the board's
build question, ...) uses ask_from_worker: the question travels through the relay as a WorkerQuestion, the receiver
asks it in the GUI thread ("No" is the default) and answers it with WorkerQuestion.reply, while the worker waits.
"""

import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QCoreApplication, QObject, Signal


class WorkerRelay(QObject):
    done = Signal(object, object)  # token, result


_relay: Optional[WorkerRelay] = None


def worker_relay() -> WorkerRelay:
    """The relay. The first call comes from the GUI thread (a window or dialog asks for it before it starts a worker);
    a relay made elsewhere moves to the application's thread."""
    global _relay
    if _relay is None:
        relay = WorkerRelay()
        app = QCoreApplication.instance()
        if app is not None and relay.thread() is not app.thread():
            relay.moveToThread(app.thread())
        _relay = relay
    return _relay


class WorkerQuestion:
    """A yes/no question from a worker thread, asked in the GUI thread by the receiver of its token."""

    def __init__(self, title: str, text: str):
        self.title, self.text, self.yes = str(title), str(text), False
        self.answered = threading.Event()

    def reply(self, asking: Callable[[], Any]) -> None:
        """Runs `asking()` in the GUI thread (the receiver's own plain question, "No" the default), keeps its answer
        and wakes the waiting worker, also when asking fails."""
        try:
            self.yes = bool(asking())
        finally:
            self.answered.set()


def ask_from_worker(token: object, title: str, text: str, stop: Optional[Callable[[], Any]] = None,
                    interval: float = 0.1) -> bool:
    """Asks `text` from a worker thread: hands a WorkerQuestion with `token` to the GUI thread and waits for the
    receiver's reply. While it waits, `stop()` runs every `interval` seconds: it may raise (a Cancel) or return true to
    give up with "No" (e.g. its window was closed)."""
    question = WorkerQuestion(title, text)
    worker_relay().done.emit(token, question)
    while not question.answered.wait(interval):
        if stop is not None and stop():
            return False
    return question.yes
