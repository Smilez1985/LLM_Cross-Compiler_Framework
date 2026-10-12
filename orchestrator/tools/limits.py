#!/usr/bin/env python3
"""
Per-client counters for the limits of the tool call path (MCP design 6.5, S8): rates (a token bucket), counts in a
time window (starts per day, questions per hour, "no" answers) and the cooldown after a person's "no". In memory, per
registry (one per process: an stdio server serves one client, an HTTP server keys every count by the named token).
The numbers themselves live in one table (orchestrator.tools.spec: CallerLimits and each ToolSpec.limits), never in
handlers; this module only counts. The clock is injectable, so tests never sleep.
"""

import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Deque, Dict, Hashable, Optional, Tuple

MAX_KEYS = 4096  # counters kept per kind; the oldest are dropped first (a client cannot grow memory without bound)


def wait_text(seconds: float) -> str:
    """'in 40 seconds', 'in 3 minutes', 'in 2 hours' for a refusal that says when to try again."""
    seconds = max(1, int(round(seconds)))
    if seconds < 90:
        return f"in {seconds} second{'s' if seconds != 1 else ''}"
    minutes = int(round(seconds / 60))
    if minutes < 90:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    hours = int(round(minutes / 60))
    return f"in {hours} hour{'s' if hours != 1 else ''}"


class Limiter:
    """Thread-safe counters. Every method takes a key the caller builds (caller, tool, argument hash, ...)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._buckets: "OrderedDict[Hashable, Tuple[float, float]]" = OrderedDict()  # key -> (tokens, at)
        self._events: "OrderedDict[Hashable, Deque[float]]" = OrderedDict()

    @staticmethod
    def _keep(table: OrderedDict, key: Hashable, value) -> None:
        table.pop(key, None)
        table[key] = value
        while len(table) > MAX_KEYS:
            table.popitem(last=False)

    # --- rates ---------------------------------------------------------------------------------------------------

    def take(self, key: Hashable, count: int, per: float, burst: int = 0) -> Optional[float]:
        """Takes one call from a token bucket that refills `count` calls per `per` seconds and holds at most `burst`
        (default `count`). None when the call may run, else the seconds until the next one may."""
        capacity = float(burst or count)
        rate = count / per
        now = self.clock()
        with self._lock:
            tokens, at = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - at) * rate)
            if tokens < 1.0:
                self._keep(self._buckets, key, (tokens, now))
                return (1.0 - tokens) / rate
            self._keep(self._buckets, key, (tokens - 1.0, now))
            return None

    # --- counts in a time window ---------------------------------------------------------------------------------

    def _window(self, key: Hashable, window: float, now: float) -> Deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
        while events and now - events[0] >= window:
            events.popleft()
        return events

    def count(self, key: Hashable, window: float) -> Tuple[int, Optional[float], Optional[float]]:
        """(events in the last `window` seconds, seconds since the newest, seconds until the oldest leaves)."""
        now = self.clock()
        with self._lock:
            events = self._window(key, window, now)
            if events:
                self._keep(self._events, key, events)
            else:
                self._events.pop(key, None)
            if not events:
                return 0, None, None
            return len(events), now - events[-1], window - (now - events[0])

    def add(self, key: Hashable, window: float) -> None:
        """Records one event (a start, a question, a "no"); events older than `window` are dropped."""
        now = self.clock()
        with self._lock:
            events = self._window(key, window, now)
            events.append(now)
            self._keep(self._events, key, events)

    def full(self, key: Hashable, limit: int, window: float) -> Optional[float]:
        """None while fewer than `limit` events lie in the window, else the seconds until one leaves it."""
        number, _, oldest_leaves = self.count(key, window)
        return oldest_leaves if number >= limit else None

    def clear(self, key: Hashable) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._buckets.pop(key, None)

    def snapshot(self) -> Dict[str, int]:
        """How many counters are kept (for tests and diagnostics)."""
        with self._lock:
            return {"buckets": len(self._buckets), "windows": len(self._events)}
