#!/usr/bin/env python3
"""
The Ditto console's reader (DESIGN "GUI and the person's path", the row "Beside the chat sits the Ditto console"):
what Ditto and external AI apps type and do, as lines in order, for the console beside the chat
(gui/chat_panel/console.py) and for `llm-cli ditto console`. Qt-free.

Nothing new is stored. It reads the two records the framework keeps already:

* the signed chat feed (Core/chat_feed.py, every process's rows, those "Clear chat" hid included) through a read
  connection of its own, so it never waits for the chat panel's: the rows Ditto or an AI app wrote, that is what they
  type (Ditto's answers, an app's replies, the reason an AI recorded, Ditto's tool notes) and the activity rows of
  Core/activity.py (what an AI changed, its jobs' and builds' ends, standing approvals used, questions asked and ended,
  work it could not do);
* the audit log of tool calls (tools/audit.py, by its id cursor): every call of Ditto or an app, with the tool, its
  arguments as the audit log's preview holds them, and the outcome in words.

The person's own calls and messages are left out. Every text passes `utils/wording.technical_details` (secrets of known
formats redacted, the home folder as "~"); an argument whose name says it holds key material is shown as "[hidden]";
a tool's output is never read (the audit log keeps only its hash); each line is one line with hidden characters
escaped, cut at MAX_CHARS. The reader never writes to either record, and clearing the console clears only the view.
"""

import json
import os
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Tuple

from orchestrator.Core.chat_feed import FeedSender, FeedStore, StoredEntry, caller_words, clean_client_name
from orchestrator.Core.feed_words import framework_text, one_line, text
from orchestrator.utils.logging import get_logger

logger = get_logger("DittoConsole")

MAX_LINES = 2000        # lines the console keeps (a ring buffer); the oldest go first
FIRST_LOOK = 200        # rows of each record read at the first look
MAX_CHARS = 1000        # characters of one line's text
POLL_SECONDS = 1.0      # how often the reader thread looks at both records
FEED_TIMEOUT = 0.2      # seconds a feed read waits for another process's write lock (then: next look)
PAGE = 500              # rows per read
MAX_PAGES = 4           # reads per look and record; the rest comes at the next look
SEEN = 4 * MAX_LINES    # keys of shown lines remembered, so a line that arrives twice is shown once

# The feed rows the console shows: what Ditto or an app typed, and what they did.
SAYS_KINDS = frozenset({"ditto_answer", "client_reply", "delegated_decision", "ditto_tool_note"})
DID_KINDS = frozenset({"ai_action", "job_result", "grant_used", "approval", "approval_end", "blocked"})
SOURCES = ("all", "ditto", "apps", "commands")
PERSON_ACTORS = frozenset({"", "gui", "cli"})
CALLER = re.compile(r"^(?:ditto|mcp|mcp:[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)$")

# An argument whose name says it holds key material: its value is never shown. The name is split at "_", "-" and "."
# so that max_tokens or key_storage_kind stay readable while api_key, password or token do not.
SECRET_PARTS = frozenset({"password", "passwd", "passphrase", "pwd", "secret", "secrets", "token", "credential",
                          "credentials", "apikey", "pin", "otp", "cookie", "authorization"})
SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("secret", "key"), ("access", "key"),
                          ("signing", "key"), ("ssh", "key"), ("auth", "key"), ("client", "secret")})
_JSON_PAIR = re.compile(r'"((?:[^"\\]|\\.){1,80})"\s*:\s*("(?:[^"\\]|\\.)*"?|[^,}\]\s][^,}\]]*)')


def secret_name(name: str) -> bool:
    """Whether an argument named `name` holds key material (its value is never shown)."""
    lowered = name.lower()
    if lowered in ("key", "keys"):
        return True
    parts = [p for p in re.split(r"[_\-.\s]+", lowered) if p]
    if any(p in SECRET_PARTS for p in parts):
        return True
    return any((a, b) in SECRET_PAIRS for a, b in zip(parts, parts[1:]))


def hide_secret_values(preview: str, hidden: str = "[hidden]") -> str:
    """`preview` (the audit log's JSON preview of a call's arguments, maybe cut short) with the value of every argument
    whose name holds key material replaced by `hidden`; works on a cut JSON text too."""
    def replace(match: "re.Match[str]") -> str:
        if not secret_name(match.group(1)):
            return match.group(0)
        return match.group(0)[:match.start(2) - match.start(0)] + f'"{hidden}"'
    return _JSON_PAIR.sub(replace, preview or "")


def scrub(value: Any, limit: int = MAX_CHARS) -> str:
    """`value` as a console shows it: secrets redacted and the home folder as "~" (the error box's technical details),
    on one line with hidden characters escaped, cut at `limit` characters."""
    from orchestrator.utils.wording import technical_details
    shown = one_line(technical_details(str(value or "")))
    return shown if len(shown) <= limit else shown[:limit - 1] + "…"


def _moment(ts: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def clock(ts: str) -> str:
    """A line's time in the person's time zone: "14:05:03" today, "2026-10-11 14:05:03" otherwise."""
    moment = _moment(ts)
    if moment.year == 1:
        return "--:--:--"
    local = moment.astimezone()
    today = datetime.now().astimezone().date()
    return local.strftime("%H:%M:%S") if local.date() == today else local.strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class ConsoleLine:
    """One line of the console. `key`: what makes it unique across both records (feed:<entry id>, audit:<id>:<time>,
    since an archived audit log starts its ids again; run:<id>:<n> for a command the person allowed); `source`: ditto,
    app, or command (a command the person allowed); `caller`: the trusted caller id (ditto, mcp, mcp:<token name>);
    `who`: the source in the framework's words (an app's own name only quoted as its claim); `kind`: says, call or did,
    and runs or output for an allowed command; `text`: what it typed or did, scrubbed; `level`: info or warning."""
    key: str
    ts: str
    source: str
    caller: str
    who: str
    kind: str
    text: str
    level: str = "info"

    def shown(self, lang: str = "en") -> str:
        """The line as the console and the terminal show it."""
        return f"{clock(self.ts)}  {self.who}  {text(f'console.kind.{self.kind}', lang)}: {self.text}"

    def data(self) -> Dict[str, str]:
        """The line for `--json`."""
        return asdict(self)


def matches(line: ConsoleLine, source: str = "all", search: str = "", lang: str = "en") -> bool:
    """Whether `line` passes the console's filters: `source` all, ditto, apps, commands (the commands the person
    allowed) or one caller id; `search` a text the
    shown line contains (case does not matter)."""
    if source == "ditto" and line.source != "ditto":
        return False
    if source == "apps" and line.source != "app":
        return False
    if source == "commands" and line.source != "command":
        return False
    if source not in SOURCES and line.caller != source:
        return False
    return not search or search.casefold() in line.shown(lang).casefold()


def command_line_entry(run_id: str, number: int, kind: str, words: str, *, level: str = "info",
                       lang: str = "en") -> ConsoleLine:
    """A console line of a command the person allowed (gui/allow_command.py): `kind` "runs" (the person's yes and the
    command), "output" (one line of its output, redacted already) or "did" (how it ended). `run_id` makes the keys of
    one run unique; `words` is scrubbed like every other line."""
    return ConsoleLine(key=f"run:{run_id}:{number}", ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                       source="command", caller="", who=text("console.who.allowed", lang), kind=kind,
                       text=scrub(words), level=level)


def _sender(caller: str, client: str = "") -> FeedSender:
    if caller == "ditto":
        return FeedSender(role="ditto", caller="ditto")
    return FeedSender(role="client", caller=caller, client=clean_client_name(client))


def line_of_feed(stored: StoredEntry, lang: str = "en") -> Optional[ConsoleLine]:
    """The console line of a feed row, or None for a row the console does not show (the person's, the framework's
    own, a conversation notice)."""
    entry = stored.entry
    sender = entry.sender
    if sender.role not in ("ditto", "client") or not CALLER.fullmatch(sender.caller or ""):
        return None
    if entry.kind in SAYS_KINDS:
        kind = "says"
    elif entry.kind in DID_KINDS:
        kind = "did"
    else:
        return None
    parts = []
    if entry.text_key or not entry.ai_text:
        parts.append(framework_text(entry, lang))
    if entry.ai_text:
        parts.append(entry.ai_text)
    return ConsoleLine(key=f"feed:{entry.entry_id}", ts=entry.ts, source="ditto" if sender.caller == "ditto" else "app",
                       caller=sender.caller, who=scrub(caller_words(sender, lang), 200), kind=kind,
                       text=scrub(" - ".join(parts)), level=entry.level)


def _outcome_words(entry: Mapping[str, Any], lang: str) -> str:
    outcome = str(entry.get("outcome") or "")
    key = f"console.outcome.{outcome}"
    words = text(key, lang)
    parts = [words if words != key else outcome.replace("_", " ")]
    answer = str(entry.get("answer") or "")
    if answer:
        answer_key = f"console.answer.{answer}"
        answer_words = text(answer_key, lang)
        parts.append(text("console.answer", lang,
                          answer=answer_words if answer_words != answer_key else answer.replace("_", " ")))
    code = str(entry.get("reason_code") or "")
    if code and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        parts.append(text("console.reason", lang, code=code))
    return ", ".join(parts)


def line_of_audit(entry: Mapping[str, Any], lang: str = "en") -> Optional[ConsoleLine]:
    """The console line of an audit entry, or None for the person's own entries and an actor that is no trusted
    caller id."""
    actor = str(entry.get("actor") or "")
    if actor in PERSON_ACTORS or not CALLER.fullmatch(actor):
        return None
    try:
        sender = _sender(actor, str(entry.get("client") or ""))
    except ValueError:
        sender = _sender(actor)
    tool = one_line(str(entry.get("tool") or "?"))[:80]
    preview = str(entry.get("args_preview") or "")
    arguments = hide_secret_values(preview, text("console.hidden", lang)) if preview else ""
    home = str(Path.home())
    escaped = json.dumps(home)[1:-1]   # the preview is JSON: a Windows home folder appears with doubled backslashes
    if len(home) > 1 and escaped != home:
        arguments = arguments.replace(escaped, "~")
    if not arguments or arguments in ("{}", "null"):
        arguments = text("console.no_arguments", lang)
    outcome = str(entry.get("outcome") or "")
    level = "warning" if outcome not in ("ok", "started", "confirmation_requested", "ai_answered") else "info"
    shown = f"{tool} {arguments} -> {_outcome_words(entry, lang)}"
    return ConsoleLine(key=f"audit:{entry.get('id')}:{entry.get('ts')}", ts=str(entry.get("ts") or ""),
                       source="ditto" if actor == "ditto" else "app", caller=actor,
                       who=scrub(caller_words(sender, lang), 200), kind="call", text=scrub(shown), level=level)


def _identity(path: Optional[Path]) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path) if path is not None else None
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st is not None else None


class ConsoleReader:
    """Reads new console lines from the chat feed and the audit log by their id cursors. It only reads: it never
    writes to, clears or moves either record.

    `feed`: the framework's FeedStore (the reader opens its own connection to the same file with the same key);
    `audit`: the tool registry's AuditLog. Either may be None. `first_look()` returns the newest rows of each and sets
    the cursors behind them; `poll()` returns what came since. A record that starts anew (the audit log archived, the
    feed moved aside) is read again from its start. `problems` holds a sentence per record that could not be read at
    the last look."""

    def __init__(self, feed: Optional[FeedStore] = None, audit: Any = None, lang: str = "en"):
        self.lang = lang
        self.feed = (FeedStore(feed.path.parent, key=feed.key, refuse_unkeyed=feed.refuse_unkeyed)
                     if feed is not None else None)
        self.audit = audit
        self.feed_cursor = 0
        self.audit_cursor = 0
        self.problems: Dict[str, str] = {}
        self._feed_identity: Optional[Tuple[int, int]] = None
        self._lock = threading.Lock()

    @classmethod
    def of_framework(cls, framework: Any, lang: str = "en") -> "ConsoleReader":
        registry = getattr(framework, "tool_registry", None)
        return cls(getattr(framework, "chat_feed", None), getattr(registry, "audit", None), lang)

    def close(self) -> None:
        if self.feed is not None:
            self.feed.close()

    # --- the feed ------------------------------------------------------------------------------------------------

    def _feed_lines(self, entries: Iterable[StoredEntry]) -> List[ConsoleLine]:
        lines = []
        for stored in entries:
            try:
                line = line_of_feed(stored, self.lang)
            except Exception as e:  # noqa: BLE001 - one row that cannot be shown never stops the console
                logger.debug(f"A feed row is not shown in the console: {type(e).__name__}")
                continue
            if line is not None:
                lines.append(line)
        return lines

    def _feed_problem(self, problem: Optional[str]) -> None:
        if problem:
            self.problems["feed"] = text("console.problem", self.lang, what=text("console.what.feed", self.lang),
                                         reason=scrub(problem, 200))
        else:
            self.problems.pop("feed", None)

    def _feed_replaced(self) -> None:
        identity = _identity(self.feed.path) if self.feed is not None else None
        if identity is not None and self._feed_identity is not None and identity != self._feed_identity:
            self.feed_cursor = 0
        if identity is not None:
            self._feed_identity = identity

    def _first_feed(self, limit: int) -> List[ConsoleLine]:
        if self.feed is None or not self.feed.path.exists():
            return []
        self._feed_replaced()
        page = self.feed.page(limit=limit, timeout=FEED_TIMEOUT, view="all")
        self._feed_problem(page.problem)
        if page.busy or page.problem:
            return []
        self.feed_cursor = max([self.feed_cursor, page.last_id] + [s.row_id for s in page.entries])
        return self._feed_lines(page.entries)

    def _new_feed(self) -> List[ConsoleLine]:
        if self.feed is None or not self.feed.path.exists():
            return []
        self._feed_replaced()
        lines: List[ConsoleLine] = []
        for _ in range(MAX_PAGES):
            page = self.feed.after(self.feed_cursor, limit=PAGE, timeout=FEED_TIMEOUT, view="all")
            self._feed_problem(page.problem)
            if page.busy or page.problem:
                break
            self.feed_cursor = max(self.feed_cursor, page.last_id)
            lines.extend(self._feed_lines(page.entries))
            if not page.more:
                break
        return lines

    # --- the audit log -------------------------------------------------------------------------------------------

    def _audit_lines(self, entries: Iterable[Mapping[str, Any]]) -> List[ConsoleLine]:
        lines = []
        for entry in entries:
            try:
                self.audit_cursor = max(self.audit_cursor, int(entry.get("id") or 0))
                line = line_of_audit(entry, self.lang)
            except Exception as e:  # noqa: BLE001 - one entry that cannot be shown never stops the console
                logger.debug(f"An audit entry is not shown in the console: {type(e).__name__}")
                continue
            if line is not None:
                lines.append(line)
        return lines

    def _audit_read(self, read: Callable[[], List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        path = getattr(self.audit, "path", None)
        if self.audit is None or path is None or not os.path.lexists(path):
            return []
        try:
            entries = read()
        except Exception as e:  # noqa: BLE001 - sqlite or OS errors: said once, read again at the next look
            self.problems["audit"] = text("console.problem", self.lang, what=text("console.what.audit", self.lang),
                                          reason=scrub(f"{type(e).__name__}: {e}", 200))
            return []
        self.problems.pop("audit", None)
        return entries

    def _first_audit(self, limit: int) -> List[ConsoleLine]:
        entries = self._audit_read(lambda: list(reversed(self.audit.entries(limit))))
        return self._audit_lines(entries)

    def _archived(self) -> bool:
        """Whether the audit log was archived since the last look: archiving empties it in place and its ids start
        again at 1, so its newest id is below the cursor. The cursor then starts again at 0."""
        if self.audit_cursor <= 0:
            return False
        newest = self._audit_read(lambda: self.audit.entries(1))
        if "audit" in self.problems or (newest and int(newest[0].get("id") or 0) >= self.audit_cursor):
            return False
        self.audit_cursor = 0
        return True

    def _new_audit(self) -> List[ConsoleLine]:
        lines: List[ConsoleLine] = []
        for _ in range(MAX_PAGES):
            entries = self._audit_read(lambda: self.audit.entries_after(self.audit_cursor, PAGE))
            if not entries and not lines and self._archived():
                continue
            lines.extend(self._audit_lines(entries))
            if len(entries) < PAGE:
                break
        return lines

    # --- both ----------------------------------------------------------------------------------------------------

    @staticmethod
    def ordered(lines: Iterable[ConsoleLine]) -> List[ConsoleLine]:
        """`lines` by their time. The feed stamps its rows to the second and the audit log to the millisecond, and a
        feed row tells what came of a call: within one second the calls come first, then the feed's rows; otherwise
        lines of the same moment keep the order they were read in."""
        def key(line: ConsoleLine) -> Tuple[datetime, int, datetime]:
            moment = _moment(line.ts)
            return moment.replace(microsecond=0), 0 if line.kind == "call" else 1, moment
        return sorted(lines, key=key)

    def first_look(self, limit: int = FIRST_LOOK) -> List[ConsoleLine]:
        """The newest `limit` rows of each record as lines, oldest first; later polls read on behind them."""
        with self._lock:
            return self.ordered(self._first_feed(limit) + self._first_audit(limit))[-max(1, limit):]

    def poll(self) -> List[ConsoleLine]:
        """The lines written since the last look, oldest first."""
        with self._lock:
            return self.ordered(self._new_feed() + self._new_audit())


class SeenKeys:
    """The keys of lines shown already (bounded), so a line that arrives twice - from this process's activity
    listener and again from the feed - is shown once."""

    def __init__(self, size: int = SEEN):
        self.size = size
        self._keys: "OrderedDict[str, None]" = OrderedDict()

    def first_time(self, key: str) -> bool:
        if key in self._keys:
            return False
        self._keys[key] = None
        while len(self._keys) > self.size:
            self._keys.popitem(last=False)
        return True

    def clear(self) -> None:
        self._keys.clear()


class ConsoleFollower:
    """Runs a ConsoleReader on a thread of its own: the first look, then a look every `interval` seconds; the lines
    wait in a bounded queue (MAX_LINES, the oldest dropped first) until `take()` collects them, so neither the reads
    nor a burst of lines ever block the caller's thread. `push()` adds lines from elsewhere (this process's activity
    rows, as they are written)."""

    def __init__(self, reader: ConsoleReader, interval: float = POLL_SECONDS, first: int = FIRST_LOOK):
        self.reader = reader
        self.interval = max(0.05, float(interval))
        self.first = first
        self._queue: Deque[ConsoleLine] = deque(maxlen=MAX_LINES)
        self._queue_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.problems: List[str] = []

    def start(self) -> "ConsoleFollower":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ditto-console", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.reader.close()

    def look_now(self) -> None:
        """Asks for the next look at once (tests, and a person who just opened the console)."""
        self._wake.set()

    def push(self, lines: Iterable[ConsoleLine]) -> None:
        with self._queue_lock:
            self._queue.extend(lines)

    def take(self) -> List[ConsoleLine]:
        with self._queue_lock:
            lines = list(self._queue)
            self._queue.clear()
        return lines

    def _run(self) -> None:
        first = True
        while not self._stop.is_set():
            try:
                lines = self.reader.first_look(self.first) if first else self.reader.poll()
                first = False
                self.problems = list(self.reader.problems.values())
                self.push(lines)
            except Exception as e:  # noqa: BLE001 - the console keeps looking
                logger.warning(f"The Ditto console could not look at its records: {type(e).__name__}")
            self._wake.wait(self.interval)
            self._wake.clear()


def follow(reader: ConsoleReader, emit: Callable[[ConsoleLine], None], *, interval: float = POLL_SECONDS,
           stop: Optional[threading.Event] = None, sleep: Callable[[float], None] = time.sleep) -> None:
    """The terminal's --follow: polls `reader` every `interval` seconds and hands each new line to `emit`, until
    `stop` is set (or Ctrl+C ends the process)."""
    while stop is None or not stop.is_set():
        for line in reader.poll():
            emit(line)
        sleep(interval)


__all__ = ["ConsoleLine", "ConsoleReader", "command_line_entry", "ConsoleFollower", "SeenKeys", "MAX_LINES",
           "FIRST_LOOK", "MAX_CHARS", "SOURCES", "follow", "hide_secret_values", "line_of_audit", "line_of_feed", "matches", "scrub",
           "secret_name", "clock"]
