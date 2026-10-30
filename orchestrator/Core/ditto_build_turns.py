#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Ditto's turn when its build ends (GUI process only)

When a build ends, the chat feed gets one row for it (Core/activity.row_for_build). In the GUI process this module
decides whether that row gives Ditto a turn of its own: only for a build Ditto started (the row's caller is
"ditto") or one it waited for (get_build_status with wait_seconds, noted with note_wait), once per build (by its
request id), and never more than MAX_AUTO_TURNS turns in a row without a message of the person (no loop: a build
Ditto starts in such a turn may end and give it one more turn, up to that limit). The turn's input is a fixed
framework note with the end's fixed words and the request id only; Ditto checks the result itself
(get_build_status, read_build_log) and proposes or runs the next step within its approvals. Rows of other builds,
rows that are not a build's end, and anything with a value outside the closed vocabulary give no turn.

The chat panel (gui/chat_panel/panel.py) asks offer() for every new feed row, queues the note while the person's
own turn runs, and starts the next queued turn when a turn ends (take()). Qt-free.
"""

import re
import threading
from collections import OrderedDict, deque
from typing import Any, Deque, Optional, Tuple

from orchestrator.Core.build_end import END_REASONS, EVENT_OF

DITTO = "ditto"
MAX_AUTO_TURNS = 3          # turns in a row without a message of the person
MAX_REMEMBERED = 256        # request ids remembered (the ends seen, the builds waited for)
REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{8}$")   # the feed's request ids (Core/chat_feed.py)
BUILD_END_KEYS = re.compile(r"^activity\.build\.(?:ready|stopped|error\.[a-z_]+)$")

# What Ditto reads as its turn's input: framework text with fixed words and the request id, never outside text.
NOTE = ("[Framework note, not the person] The build {request_id} ended: {event} (end reason {reason}). Check its "
        "result with get_build_status and, if it did not end with done, read_build_log; then tell the person in one "
        "or two sentences how it went and propose the next step, or run it if your permissions and approvals allow "
        "it. Do not start the same build again unless the person asks.")

_lock = threading.Lock()
_waited: "OrderedDict[str, None]" = OrderedDict()


def note_wait(actor: str, request_id: str) -> None:
    """Remembers that Ditto waited for a build (get_build_status with wait_seconds): its end gives Ditto a turn."""
    if actor != DITTO or not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        return
    with _lock:
        _waited[request_id] = None
        _waited.move_to_end(request_id)
        while len(_waited) > MAX_REMEMBERED:
            _waited.popitem(last=False)


def waited_for(request_id: str) -> bool:
    with _lock:
        return request_id in _waited


def note_text(request_id: str, reason: str) -> str:
    """The turn's input for one build end; ValueError for a value outside the closed vocabulary."""
    if not REQUEST_ID_RE.fullmatch(request_id or "") or reason not in END_REASONS:
        raise ValueError("a build end outside the closed vocabulary")
    return NOTE.format(request_id=request_id, event=EVENT_OF[reason], reason=reason)


def end_of_row(entry: Any) -> Optional[tuple]:
    """(request_id, reason, caller) of a feed row that is a build's end (job_result, activity.build.*), else None."""
    entry = getattr(entry, "entry", entry)   # a StoredEntry or a FeedEntry
    try:
        if entry.kind != "job_result" or not BUILD_END_KEYS.fullmatch(entry.text_key or ""):
            return None
        request_id = entry.refs.request_id or ""
        reason = (entry.params or {}).get("reason", "")
        caller = entry.sender.caller or ""
    except AttributeError:
        return None
    if not REQUEST_ID_RE.fullmatch(request_id) or reason not in END_REASONS:
        return None
    return request_id, reason, caller


def _cancelled_by(entry: Any) -> str:
    """The caller id of the AI that cancelled the build of a build end's row ("" for none)."""
    entry = getattr(entry, "entry", entry)
    try:
        return str((entry.params or {}).get("by") or "")
    except AttributeError:
        return ""


class BuildTurns:
    """The turns one chat panel gives Ditto for build ends: offer(row) -> note or None, queued notes, the limit of
    turns in a row. Not thread-safe by itself: the panel calls it on the GUI thread."""

    def __init__(self, max_auto_turns: int = MAX_AUTO_TURNS):
        self.max_auto_turns = max(0, int(max_auto_turns))
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._queue: Deque[Tuple[str, str]] = deque()   # (request id, note)
        self.in_row = 0          # automatic turns since the person's last message
        self.skipped = 0         # ends that gave no turn because of the limit

    def offer(self, row: Any) -> Optional[str]:
        """The note for a build end that gives Ditto a turn (queued; take() hands it out), None for any other row.
        Each build once."""
        found = end_of_row(row)
        if found is None:
            return None
        request_id, reason, caller = found
        if request_id in self._seen:
            return None
        if caller != DITTO and not waited_for(request_id):
            return None
        if reason == "cancelled" and _cancelled_by(row) == DITTO and not waited_for(request_id):
            # Ditto cancelled it itself (the row's caller is then Ditto also for the person's own build, which Ditto
            # neither started nor waited for): nothing new for Ditto to look at
            return None
        self._seen[request_id] = None
        while len(self._seen) > MAX_REMEMBERED:
            self._seen.popitem(last=False)
        note = note_text(request_id, reason)
        self._queue.append((request_id, note))
        return note

    def take(self) -> Optional[Tuple[str, str]]:
        """The next queued (request id, note) when Ditto may take a turn now (the caller checked that no turn runs), else None.
        Beyond the limit of turns in a row the queue is dropped: the rows stay in the chat."""
        if not self._queue:
            return None
        if self.in_row >= self.max_auto_turns:
            self.skipped += len(self._queue)
            self._queue.clear()
            return None
        self.in_row += 1
        return self._queue.popleft()

    def pending(self) -> int:
        return len(self._queue)

    def person_spoke(self) -> None:
        """The person sent a message: the count of turns in a row starts again."""
        self.in_row = 0


__all__ = ["DITTO", "MAX_AUTO_TURNS", "NOTE", "BuildTurns", "note_wait", "waited_for", "note_text", "end_of_row"]
