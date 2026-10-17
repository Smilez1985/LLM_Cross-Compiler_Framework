#!/usr/bin/env python3
"""
What Ditto and AI apps do, as rows of the chat feed (wave 6, design B.1; DECISIONS round 9, U1 and consequence b).

The person sees in the GUI what an AI did through the framework's tools. One producer per framework process writes
it: FrameworkManager.initialize attaches it next to the chat feed, so the GUI, `llmcf`, every stdio MCP server and the
HTTP child (`mcp serve --transport http`) all write rows, and the GUI reads them from every process (FeedWatcher).

Inputs, all framework facts:
- the registry's events (`registry.events`): call_done (a call that changed something ended ok), grant_used,
  approval, approval_end, refused_before_question;
- the job service's finish listener (job_end: every job kind ends there);
- the orchestrator's end listener (build_end: a build completed, failed or was cancelled).

Rules:
- One signed row per event, written through `FeedWriter`: ai_action, job_result, grant_used, approval, approval_end
  or blocked (refused_before_question, and report_cannot_do). Rows of the same refusal within BLOCKED_QUIET seconds
  are written once, so an AI that tries a refused tool in a loop cannot flood the chat; the audit log keeps every one.
- The person's own calls (actor gui or cli) produce nothing; neither does a caller without a trusted caller id.
- The sender is the trusted caller id (ditto, mcp, mcp:<token>). An app's own name appears only as its claim.
- A row holds closed values only: tool, job kind, state and reason codes, setting keys (never their values), ids
  the framework issued, a window from tool_windows.WINDOW_IDS, step ids by pattern, a finished build's folder as a
  label (never a host path). Every value is checked again here; one that does not fit is left out.
- `refs.asked_in` is "gui-chat" only for Ditto's calls in the GUI process for a turn the person typed into its chat.
- It fails open: a row that cannot be written is logged and dropped (listeners still get it, with row id -1), and
  nothing ever runs from a row. The audit log stays the authority.

Rows are written by one background thread, so an event never waits for the feed file (the build loop's thread
reports build ends). `add_listener` hands this process's rows to the GUI at once (Qt-free: the callback runs on that
thread; the GUI moves it to its own thread). `flush()` waits until every queued row is written; `close()` (at
framework shutdown) writes what is queued and stops.
"""

import atexit
import re
import threading
import time
import weakref
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple, Union

from orchestrator.Core.chat_feed import (CALLER_RE, REQUEST_ID_RE, TEXTS, FeedRefs, FeedStore, FeedWriter,
                                         StoredEntry)
from orchestrator.Core.job_service import JOB_ID_RE, JOB_KINDS
from orchestrator.tools.approvals import APPROVAL_ID_RE
from orchestrator.tools.grants import GRANT_ID_RE
from orchestrator.tools.tool_windows import (JOB_KIND_WINDOWS, MAX_STEPS, REASON_WINDOWS, REPORTED_READ_TOOLS,
                                             TOOL_WINDOWS, is_step_id, is_window, window_for_tool)
from orchestrator.utils.any_path import is_absolute_anywhere
from orchestrator.utils.logging import get_logger

logger = get_logger("Activity")

# request_store.program() -> the feed's program: where a row was written.
PROGRAM_OF = {"gui": "gui", "cli": "cli", "mcp": "mcp", "api": "mcp"}
PERSON_ACTORS = frozenset({"gui", "cli", ""})
BLOCKED_QUIET = 30.0    # seconds: the same refusal of the same caller is written once in this time
QUEUE_SIZE = 1000       # rows waiting to be written; more are dropped (logged), the audit log has them all
REMEMBERED_JOBS = 256   # job id -> window of the call that started it, for that job's end
EXIT_FLUSH = 3.0        # seconds a process that ends without the framework's shutdown (llm-cli) writes what is queued

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_COUNT = re.compile(r"^[0-9]{1,6}$")
BUILD_STATES = ("completed", "error", "cancelled")
# A tool or job kind whose text key has another name: the feed's own texts name no knowledge snapshot (it holds no
# reminder about them, tests/test_doctor_fixes.py), so restoring one is a knowledge restore there.
TEXT_NAMES = {"restore_knowledge_snapshot": "restore_knowledge", "snapshot_restore": "knowledge_restore"}


def feed_program(kind: str) -> str:
    """The feed's program for a process of `kind` (request_store.program()); an entry point that named none
    (a script, a test) writes as the command line."""
    return PROGRAM_OF.get(str(kind or ""), "cli")


def trusted_caller(actor: Any) -> Optional[str]:
    """The trusted caller id a row may name (ditto, mcp, mcp:<token>), None for the person (gui, cli) and for any
    actor that is not a caller id."""
    if not isinstance(actor, str) or actor in PERSON_ACTORS:
        return None
    return actor if re.fullmatch(CALLER_RE, actor) else None


_DRIVE = re.compile(r"^[A-Za-z]:$")


def _label(value: str) -> bool:
    """A finished build's folder as a label (output:<relative path> or a folder name): never a host path, neither
    the whole value nor the part after the label's place ("output:/home/<user>/..." is a host path behind a prefix)."""
    if not value or len(value) > 300 or any(ch in value for ch in "\r\n\t\x00"):
        return False
    _where, sep, rest = value.partition(":")
    if "\\" in value or is_absolute_anywhere(value) or (sep and (not rest or is_absolute_anywhere(rest))):
        return False
    return ".." not in re.split(r"[/:]", value) and not any(_DRIVE.fullmatch(part) for part in value.split("/"))


# Every parameter a row may carry, with the rule its value must pass (closed vocabularies and patterns).
PARAM_RULES: Dict[str, Callable[[str], bool]] = {
    "tool": lambda v: _NAME.fullmatch(v) is not None and len(v) <= 39,
    "kind": lambda v: v in JOB_KINDS,
    "state": lambda v: _NAME.fullmatch(v) is not None and len(v) <= 24,
    "status": lambda v: v in BUILD_STATES,
    "code": lambda v: _NAME.fullmatch(v) is not None,
    "reason": lambda v: _NAME.fullmatch(v) is not None,
    "action": lambda v: _NAME.fullmatch(v) is not None,
    "stage": lambda v: _NAME.fullmatch(v) is not None,
    "blocker": lambda v: _NAME.fullmatch(v) is not None,
    "keys": lambda v: bool(v) and all(_KEY.fullmatch(k) for k in v.split(", ")),
    "saved": _label,
    "completed": lambda v: _COUNT.fullmatch(v) is not None,
    "failed": lambda v: _COUNT.fullmatch(v) is not None,
    "by": lambda v: trusted_caller(v) is not None,
}
REF_RULES: Dict[str, Callable[[Any], bool]] = {
    "approval_id": lambda v: isinstance(v, str) and re.fullmatch(APPROVAL_ID_RE, v) is not None,
    "job_id": lambda v: isinstance(v, str) and JOB_ID_RE.fullmatch(v) is not None,
    "request_id": lambda v: isinstance(v, str) and re.fullmatch(REQUEST_ID_RE, v) is not None,
    "grant_id": lambda v: isinstance(v, str) and re.fullmatch(GRANT_ID_RE, v) is not None,
    "window": is_window,
}


def closed_params(params: Mapping[str, Any]) -> Dict[str, str]:
    """Only the parameters a row may carry whose values pass their rule; anything else is left out."""
    kept = {}
    for name, value in params.items():
        rule = PARAM_RULES.get(name)
        text = "" if value is None else str(value)
        if rule is not None and text and rule(text):
            kept[name] = text
    return kept


def closed_refs(refs: Mapping[str, Any]) -> Dict[str, Any]:
    """Only the references a row may carry whose values are ids of the framework's own formats."""
    kept: Dict[str, Any] = {name: value for name, value in refs.items()
                            if name in REF_RULES and value and REF_RULES[name](value)}
    steps = [s for s in refs.get("steps") or () if is_step_id(s)]
    if steps:
        kept["steps"] = list(dict.fromkeys(steps))[:MAX_STEPS]
    return kept


@dataclass(frozen=True)
class Row:
    """A row to write: its kind, the trusted caller it is about, the framework's text by key, closed parameters and
    references, and the app's own name as its claim."""
    kind: str
    caller: str
    text_key: str
    params: Dict[str, str] = field(default_factory=dict)
    refs: Dict[str, Any] = field(default_factory=dict)
    level: str = "info"
    client: str = ""
    asked_in: str = ""


def _text_key(preferred: str, fallback: str, needs: Tuple[str, ...] = (), params: Mapping[str, str] = ()) -> str:
    """`preferred` when the feed has its text and every placeholder it needs has a value, else `fallback`."""
    if preferred in TEXTS["en"] and all(name in params for name in needs):
        return preferred
    return fallback


def _row(kind: str, caller: str, preferred: str, fallback: str, params: Mapping[str, Any], refs: Mapping[str, Any],
         *, level: str = "info", needs: Tuple[str, ...] = (), client: str = "", asked_in: str = "") -> Row:
    kept = closed_params(params)
    return Row(kind, caller, _text_key(preferred, fallback, needs, kept), kept, closed_refs(refs), level,
               client if isinstance(client, str) else "", asked_in)


def row_for_call(data: Mapping[str, Any], *, program: str, job_window: Optional[str] = None) -> Optional[Row]:
    """The row of a call_done: ai_action for a call that changed something, blocked for report_cannot_do. None for
    the person's own call or a caller without a trusted id. `job_window`: the window of the job a cancel_job
    stopped, when it is known."""
    caller = trusted_caller(data.get("actor"))
    tool = str(data.get("tool") or "")
    if caller is None or not tool:
        return None
    asked_in = "gui-chat" if (data.get("channel") == "gui-chat" and caller == "ditto" and program == "gui") else ""
    refs = {"request_id": data.get("request_id"), "job_id": data.get("job_id")}
    if tool in REPORTED_READ_TOOLS:
        reason, action = str(data.get("reason") or ""), str(data.get("action") or "")
        refs["window"] = window_for_tool(tool, reason=reason, action=action)
        return _row("blocked", caller, f"activity.blocked.{reason}", "activity.blocked",
                    {"tool": tool, "reason": reason, "action": action}, refs, level="warning",
                    client=data.get("client", ""), asked_in=asked_in)
    refs["window"] = (job_window if tool == "cancel_job" and job_window else
                      window_for_tool(tool, form=str(data.get("form") or ""), window=str(data.get("window") or "")))
    keys = ", ".join(k for k in str(data.get("keys") or "").split(",") if k)
    if tool == "update_settings" and not keys:
        return None   # every change was refused right before it was set: nothing changed, the audit log has the call
    return _row("ai_action", caller, f"activity.tool.{TEXT_NAMES.get(tool, tool)}", "activity.action",
                {"tool": tool, "keys": keys}, refs, needs=("keys",) if tool == "update_settings" else (),
                client=data.get("client", ""), asked_in=asked_in)


def row_for_event(kind: str, data: Mapping[str, Any]) -> Optional[Row]:
    """The row of a registry event other than call_done: grant_used, approval, approval_end, or blocked for
    refused_before_question. None for the person's own calls and for events that are no row."""
    caller = trusted_caller(data.get("actor"))
    if caller is None:
        return None
    tool, state = str(data.get("tool") or ""), str(data.get("state") or "")
    if kind == "grant_used":
        return _row("grant_used", caller, "activity.grant_used", "activity.grant_used", {"tool": tool},
                    {"grant_id": data.get("grant_id"), "window": TOOL_WINDOWS.get(tool)})
    if kind in ("approval", "approval_end"):
        refs = {"approval_id": data.get("approval_id"), "job_id": data.get("job_id"), "window": "approvals"}
        if kind == "approval":
            return _row("approval", caller, "activity.approval", "activity.approval", {"tool": tool, "state": state},
                        refs)
        return _row("approval_end", caller, f"activity.approval_end.{state}", "activity.approval_end",
                    {"tool": tool, "state": state}, refs, level="warning" if state == "failed" else "info")
    if kind == "refused_before_question":
        code = str(data.get("code") or "")
        steps = [data.get("step"), data.get("person_action")]   # a catalogue id where one is given (chain a0)
        refs = {"window": REASON_WINDOWS.get(code) or TOOL_WINDOWS.get(tool), "steps": steps}
        return _row("blocked", caller, f"activity.blocked.{code}", "activity.blocked",
                    {"tool": tool, "code": code, "stage": data.get("stage"), "blocker": data.get("blocker")}, refs,
                    level="warning")
    return None


def row_for_job(summary: Mapping[str, Any], *, window: Optional[str] = None) -> Optional[Row]:
    """The row of a job's end (job_result), from its summary. None for the person's own jobs and for a pending
    approval's job (its approval_end row says how it ended)."""
    caller = trusted_caller(summary.get("owner"))
    kind, state = str(summary.get("kind") or ""), str(summary.get("state") or "")
    if caller is None or kind not in JOB_KINDS or kind == "approval":
        return None
    outcome = "done" if state == "done" else "not_done"
    return _row("job_result", caller, f"activity.job.{TEXT_NAMES.get(kind, kind)}.{outcome}",
                f"activity.job.{outcome}",
                {"kind": kind, "state": state, "code": summary.get("error")},
                {"job_id": summary.get("job_id"), "window": window or JOB_KIND_WINDOWS.get(kind)},
                level="info" if outcome == "done" else "warning")


def row_for_build(data: Mapping[str, Any]) -> Optional[Row]:
    """The row of a build's end (job_result): a build an AI started, or one an AI cancelled. None for the person's
    own build that no AI touched."""
    owner, by = trusted_caller(data.get("owner")), trusted_caller(data.get("cancelled_by"))
    caller = owner or by
    status = str(data.get("status") or "")
    if caller is None or status not in BUILD_STATES:
        return None
    params = {"status": status, "saved": data.get("saved"), "completed": data.get("completed"),
              "failed": data.get("failed"), "by": by or ""}
    refs = {"request_id": data.get("request_id"), "window": "builder"}
    if status == "completed":
        try:
            partial = int(data.get("failed") or 0) > 0
        except (TypeError, ValueError):
            partial = False
        if partial:
            return _row("job_result", caller, "activity.build.partial", "activity.build.completed", params, refs,
                        level="warning", needs=("completed", "failed"))
        return _row("job_result", caller, "activity.build.saved", "activity.build.completed", params, refs,
                    needs=("saved",))
    return _row("job_result", caller, f"activity.build.{status}", "activity.build.error", params, refs,
                level="info" if status == "cancelled" else "warning")


Listener = Callable[[StoredEntry], None]


class ActivityProducer:
    """Writes one signed chat-feed row per AI activity event of this process (see the module text). Qt-free.

    API: attach(registry, job_service, orchestrator) subscribes it; add_listener(callback) hands this process's rows
    to the GUI at once (callback(StoredEntry) on the writer thread; row id -1 when it could not be saved, 0 without a
    feed); flush(timeout) waits until the queue is written; close(timeout) writes what is queued and stops. The
    on_* methods are the event entry points (also callable directly)."""

    def __init__(self, feed: Optional[FeedStore], program: Union[str, Callable[[], str]] = "cli", *,
                 queue_size: int = QUEUE_SIZE, clock: Callable[[], float] = time.monotonic):
        self.feed = feed
        self._program = program
        self.queue_size = max(1, int(queue_size))
        self._clock = clock
        self._cv = threading.Condition()
        self._pending: Deque[Row] = deque()
        self._busy = False
        self._closing = self._closed = False
        self._thread: Optional[threading.Thread] = None
        self._listeners: List[Listener] = []
        self._unsubscribe: List[Callable[[], None]] = []
        self._job_windows: "OrderedDict[str, str]" = OrderedDict()
        self._quiet: Dict[Tuple[str, str, str], float] = {}
        self._dropped = 0
        self.job_service: Any = None
        _LIVE.add(self)

    # --- wiring ----------------------------------------------------------------------------------------------------

    def attach(self, registry: Any = None, job_service: Any = None, orchestrator: Any = None) -> "ActivityProducer":
        """Subscribes to the registry's events, the job service's job ends and the orchestrator's build ends (each
        when given and able to)."""
        events = getattr(registry, "events", None)
        if callable(getattr(events, "subscribe", None)):
            self._unsubscribe.append(events.subscribe(self.on_registry_event))
        if callable(getattr(job_service, "add_finish_listener", None)):
            self.job_service = job_service
            self._unsubscribe.append(job_service.add_finish_listener(self.on_job_end))
        if callable(getattr(orchestrator, "add_end_listener", None)):
            self._unsubscribe.append(orchestrator.add_end_listener(self.on_build_end))
        return self

    def add_listener(self, callback: Listener) -> Callable[[], None]:
        """Calls `callback(stored_entry)` for every row this producer writes from now on (on the writer thread; a
        listener that raises is logged and skipped). Returns the function that removes it."""
        with self._cv:
            self._listeners.append(callback)

        def remove() -> None:
            with self._cv:
                if callback in self._listeners:
                    self._listeners.remove(callback)
        return remove

    def program(self) -> str:
        """The feed's program of this process, resolved now (the entry point may name it after the boot)."""
        kind = self._program() if callable(self._program) else self._program
        return kind if kind in ("gui", "cli", "mcp", "ditto") else feed_program(kind)

    # --- events ----------------------------------------------------------------------------------------------------

    def on_registry_event(self, kind: str, data: Dict[str, Any]) -> None:
        try:
            if kind == "call_done":
                row = row_for_call(data, program=self.program(), job_window=self._cancelled_job_window(data))
                if row is not None and row.kind == "ai_action":
                    self._remember_job(row)
            else:
                row = row_for_event(kind, data)
            if row is not None and row.kind == "blocked" and self._quieted(row):
                return
            self._put(row)
        except Exception as e:  # noqa: BLE001 - the producer fails open: an event never fails a call
            logger.warning(f"No chat row for the registry event {kind}: {e.__class__.__name__}")

    def on_job_end(self, summary: Dict[str, Any]) -> None:
        try:
            with self._cv:
                window = self._job_windows.get(str(summary.get("job_id") or ""))
            self._put(row_for_job(summary, window=window))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"No chat row for a job's end: {e.__class__.__name__}")

    def on_build_end(self, data: Dict[str, Any]) -> None:
        try:
            self._put(row_for_build(data))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"No chat row for a build's end: {e.__class__.__name__}")

    def _cancelled_job_window(self, data: Mapping[str, Any]) -> Optional[str]:
        """The window of the job a cancel_job stopped: the one of the call that started it, else its kind's."""
        job_id = str(data.get("job_id") or "")
        if data.get("tool") != "cancel_job" or not job_id:
            return None
        with self._cv:
            remembered = self._job_windows.get(job_id)
        if remembered:
            return remembered
        try:
            return JOB_KIND_WINDOWS.get(self.job_service.get(job_id).kind) if self.job_service is not None else None
        except Exception:  # noqa: BLE001 - an unknown job: the tool's own window
            return None

    def _remember_job(self, row: Row) -> None:
        job_id, window = row.refs.get("job_id"), row.refs.get("window")
        if job_id and window and row.params.get("tool") != "cancel_job":
            with self._cv:
                self._job_windows[job_id] = window
                while len(self._job_windows) > REMEMBERED_JOBS:
                    self._job_windows.popitem(last=False)

    def _quieted(self, row: Row) -> bool:
        """Whether the same refusal of the same caller was written less than BLOCKED_QUIET seconds ago."""
        key = (row.caller, row.params.get("tool", ""), row.params.get("code") or row.params.get("reason") or "")
        now = self._clock()
        with self._cv:
            for old in [k for k, at in self._quiet.items() if now - at >= BLOCKED_QUIET]:
                del self._quiet[old]
            if key in self._quiet:
                return True
            self._quiet[key] = now
        return False

    # --- writing ---------------------------------------------------------------------------------------------------

    def _put(self, row: Optional[Row]) -> None:
        if row is None:
            return
        with self._cv:
            if self._closing:
                return
            if len(self._pending) >= self.queue_size:
                self._dropped += 1
                if self._dropped == 1 or self._dropped % 100 == 0:
                    logger.warning(f"{self._dropped} chat rows about AI activity were dropped (the queue is full); "
                                   "the audit log keeps every call.")
                return
            self._pending.append(row)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="activity-feed", daemon=True)
                self._thread.start()
            self._cv.notify_all()

    def _work(self) -> None:
        while True:
            with self._cv:
                while not self._pending and not self._closing:
                    self._cv.wait()
                if not self._pending:
                    self._thread = None
                    self._cv.notify_all()
                    return
                row = self._pending.popleft()
                self._busy = True
            try:
                self._write(row)
            except Exception as e:  # noqa: BLE001 - one bad row never stops the others
                logger.warning(f"A chat row about AI activity was not written: {e.__class__.__name__}")
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def _write(self, row: Row) -> StoredEntry:
        writer = FeedWriter(self.feed, self.program())   # type: ignore[arg-type] - entry() needs no store
        role = "ditto" if row.caller == "ditto" else "client"
        sender = writer.sender(role, row.caller, client=row.client if role == "client" else "")
        asked_in = row.asked_in if writer.program == "gui" and row.caller == "ditto" else ""
        entry = writer.activity_entry(row.kind, sender, text_key=row.text_key, params=row.params,
                                      refs=FeedRefs(**row.refs, asked_in=asked_in), level=row.level)
        stored = writer.write_entry(entry) if self.feed is not None else StoredEntry(0, entry)
        with self._cv:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(stored)
            except Exception as e:  # noqa: BLE001 - a listener never stops the producer
                logger.warning(f"A listener of the AI activity rows failed: {e.__class__.__name__}")
        return stored

    def flush(self, timeout: float = 5.0) -> bool:
        """Waits until every queued row is written (True), at most `timeout` seconds (False)."""
        with self._cv:
            return self._cv.wait_for(lambda: not self._pending and not self._busy, timeout)

    def close(self, timeout: float = 5.0) -> None:
        """Stops listening, writes what is queued (at most `timeout` seconds) and ends the writer thread."""
        for unsubscribe in self._unsubscribe:
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        self._unsubscribe = []
        with self._cv:
            self._closing = True
            self._cv.notify_all()
        self.flush(timeout)
        self._closed = True


# Producers of this process: a process that ends without the framework's shutdown (a `llm-cli` command) still writes
# the rows it queued, at most EXIT_FLUSH seconds in all (the writer thread is a daemon and would stop mid-queue).
_LIVE: "weakref.WeakSet[ActivityProducer]" = weakref.WeakSet()


@atexit.register
def _flush_at_exit() -> None:
    deadline = time.monotonic() + EXIT_FLUSH
    for producer in list(_LIVE):
        try:
            producer.flush(max(0.0, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001 - the process ends anyway
            pass


def attach_activity(framework: Any) -> ActivityProducer:
    """The process's producer, subscribed to the framework's registry, job service and orchestrator, writing to its
    chat feed (rows only reach the listeners while there is none). A finished build's folder is named by the build
    service's label."""
    from orchestrator.Core.request_store import program
    producer = ActivityProducer(getattr(framework, "chat_feed", None), program=program)
    orchestrator, builds = getattr(framework, "orchestrator", None), getattr(framework, "build_service", None)
    if orchestrator is not None and builds is not None and getattr(orchestrator, "artifact_label", None) is None:
        orchestrator.artifact_label = builds.artifact_label
    return producer.attach(getattr(framework, "tool_registry", None), getattr(framework, "job_service", None),
                           orchestrator)


__all__ = ["ActivityProducer", "Row", "attach_activity", "feed_program", "trusted_caller", "closed_params",
           "closed_refs", "row_for_call", "row_for_event", "row_for_job", "row_for_build", "PARAM_RULES",
           "REF_RULES", "BLOCKED_QUIET", "PROGRAM_OF"]
