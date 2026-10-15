#!/usr/bin/env python3
"""
New chat rows from every framework process (delegation design 5.1): once a second the watcher asks the feed whether
anything was committed since the last look (`PRAGMA data_version`, cheap) and only then reads the rows after its
cursor. Both reads wait at most 0.2 seconds for another process's write lock: a busy feed is simply read again on the
next tick, so the GUI never waits. Rows that do not verify are never shown; the panel is told how many were skipped.
"""

from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

INTERVAL_MS = 1000
TIMEOUT = 0.2
BATCH = 200


class FeedWatcher(QObject):
    entries = Signal(list)     # List[StoredEntry], oldest first
    notices = Signal(list)     # framework lines about skipped rows
    problem = Signal(str)      # the feed cannot be read (once per problem)

    def __init__(self, store: Any, last_id: int = 0, lang: str = "en", parent: Optional[QObject] = None,
                 start: bool = True):
        super().__init__(parent)
        self.store, self.last_id, self.lang = store, int(last_id), lang
        self._version: Optional[Tuple[int, int, int]] = None
        self._problem: Optional[str] = None
        self.timer = QTimer(self)
        self.timer.setInterval(INTERVAL_MS)
        self.timer.timeout.connect(self.check)
        if start:
            self.timer.start()

    def set_cursor(self, last_id: int) -> None:
        """Reads on from `last_id` (after the panel loaded a page itself, or after "Clear chat")."""
        self.last_id = int(last_id)
        self._version = None

    def stop(self) -> None:
        self.timer.stop()

    def check(self) -> int:
        """One look; returns the number of new rows handed to the panel."""
        version = self.store.data_version(timeout=TIMEOUT)
        if version is None:
            self._report(getattr(self.store, "problem", None))
            return 0
        if version == self._version:
            return 0
        page = self.store.after(self.last_id, limit=BATCH, timeout=TIMEOUT)
        if page.busy:
            return 0                       # another process writes: next tick
        if page.problem:
            self._report(page.problem)
            return 0
        self._report(None)
        self.last_id = max(self.last_id, page.last_id)
        if not page.more:
            self._version = version        # everything up to this version is read
        lines: List[str] = page.notices(self.lang)
        if lines:
            self.notices.emit(lines)
        if page.entries:
            self.entries.emit(list(page.entries))
        return len(page.entries)

    def _report(self, problem: Optional[str]) -> None:
        if problem and problem != self._problem:
            self.problem.emit(problem)
        self._problem = problem


__all__ = ["FeedWatcher", "INTERVAL_MS", "TIMEOUT"]
