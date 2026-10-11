#!/usr/bin/env python3
"""
Hands results of plain worker threads (threading.Thread) to the GUI thread.

A worker thread must never emit on, or free, a Qt object that a window owns or created for it: a closed window can
take the object with it while the worker still runs, and a QObject whose last reference goes away in a worker
thread is destroyed there, which makes Qt abort. The relay is a single QObject, created in the GUI thread and never
deleted. Receivers connect to `done` with a queued connection and pick their own results by a token they created
(an `object()`), so their slot runs in the GUI thread, or not at all once the receiver is gone. dialogs.py keeps
its own relay of the same kind for model lists (model_list_relay).
"""

from typing import Optional

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
