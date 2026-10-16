#!/usr/bin/env python3
"""
Deep Ingest for AI callers (MCP design 2.5, work package T7; DECISIONS round 1 item 1).

`crawl_docs_site` crawls documentation sites into the knowledge base Ditto uses, as a job, with exactly the GUI's
crawler and its machine-readable opt-out checks (robots.txt, robots meta and X-Robots-Tag including noai, TDMRep):
none of them can be switched off from here, and no input reaches the politeness delay. This service is the only
way an AI caller reaches the crawler:

- it refuses while the one-time Deep Ingest notice is needed: only a person acknowledges it, in the GUI or with
  `llm-cli tools crawl-notice` (this module never acknowledges it);
- every start address must be a public http(s) address (`validate_public_url`), checked before the question;
- the caps of the limits table (design 6.5): 1-5 start addresses, `max_pages` up to min(setting, 100) per start
  address, `max_depth` up to min(setting, 3), a delay of at least max(1 s, setting); one crawl at a time per user
  (also across framework processes, through a lock file in the per-user folder) and at most 300 stored pages a day
  per caller;
- a stop (cancel_job) ends the crawl after the current page and keeps what is stored; a snapshot is taken before
  each start address, so a crawl can be undone; when that snapshot does not fit on the disk, nothing more is crawled
  (blocker snapshot_no_room, checked before the question too; eighth round, answer 2);
- the result holds counts and the addresses that were skipped or failed with the reason, never page text (the
  client reads pages later through search_knowledge or read_knowledge_source, filtered and marked).
"""

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from orchestrator.utils.logging import get_logger

MAX_START_URLS = 5
MAX_URL_CHARS = 2048
PAGE_CAP = 100            # max_pages never above this, whatever the setting says
DEPTH_CAP = 3             # max_depth never above this
MIN_DELAY = 1.0           # seconds between requests, at least
PAGES_PER_DAY = 300       # stored pages per caller in 24 hours
LIST_CAP = 50             # skipped / failed addresses in a result
LOCK_NAME = "crawl.lock"
DAY = 86400.0

logger = get_logger("CrawlJobs")
# A folder of this computer inside an error text (POSIX or Windows); not applied to addresses (their paths would match).
_PATH = re.compile(r"(?<![\w:/])(?:[A-Za-z]:)?[\\/](?:[^\s'\"\\/]+[\\/])+[^\s'\"\\/,;:)]*")


def _no_paths(text: str) -> str:
    return _PATH.sub("(a folder on this computer)", text)


class CrawlRefused(ValueError):
    """A crawl that cannot start; `code` is the reason code of design 6.11 (D10), `next` the next step, `blocker` the
    facts catalogue's code of the hard fact that refused it (delegation design 4.2)."""

    def __init__(self, message: str, code: str = "failed", next: str = "", blocker: str = ""):
        super().__init__(message)
        self.code, self.next, self.blocker = code, next, blocker


NOTICE_NEXT = ("Ask the person to read the one-time Deep Ingest notice: in the GUI (Tools > Create Hardware "
               "Module..., then Deep Ingest) or with 'llm-cli tools crawl-notice' in a terminal. Only a person can "
               "acknowledge it.")


@dataclass(frozen=True)
class CrawlPlan:
    """What a crawl will do, as the person is asked about it (the question shows every start address)."""
    start_urls: Tuple[str, ...]
    max_pages: int
    max_depth: int
    delay: float
    languages: Tuple[str, ...]
    include_pdfs: bool
    # The room for the snapshot taken first (knowledge_snapshots.SnapshotRoom), as looked up before the question
    disk: Any = field(default=None, compare=False)

    def options(self, pages: int) -> Dict[str, Any]:
        """The crawler options of one start address (only these keys; no opt-out check is among them)."""
        return {"max_pages": pages, "max_depth": self.max_depth, "request_delay": self.delay,
                "download_pdfs": self.include_pdfs, "languages": list(self.languages)}


@dataclass
class _Budget:
    """Stored pages per caller in the last 24 hours (in this process)."""
    used: Deque[Tuple[float, int]] = field(default_factory=deque)

    def total(self, now: float) -> int:
        while self.used and now - self.used[0][0] >= DAY:
            self.used.popleft()
        return sum(n for _, n in self.used)


class CrawlJobService:
    """Starts and runs AI-requested crawls as jobs (kind `crawl`); see the module docstring for the rules."""

    def __init__(self, framework, jobs, clock: Callable[[], float] = time.monotonic):
        self.framework = framework
        self.jobs = jobs
        self.clock = clock
        self._lock = threading.Lock()
        self._running: Dict[str, str] = {}          # job id -> caller
        self._budgets: Dict[str, _Budget] = {}

    # --- facts -----------------------------------------------------------------------------------------------

    def _cfg(self, key: str, default: Any) -> Any:
        get = getattr(getattr(self.framework, "config", None), "get", None)
        try:
            return get(key, default) if callable(get) else default
        except Exception:  # noqa: BLE001 - a setting that cannot be read is its default
            return default

    def _state_dir(self) -> Optional[Path]:
        path = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        return Path(path) if isinstance(path, (str, Path)) else None

    def notice_needed(self) -> bool:
        """Whether the person still has to read the Deep Ingest notice (also when it cannot be told)."""
        from orchestrator.utils.crawl_notice import CrawlNotice
        state = self._state_dir()
        if state is None:
            return True
        try:
            return CrawlNotice(state).needed()
        except OSError:
            return True

    def rag(self):
        get = getattr(self.framework, "get_component", None)
        rag = get("rag_manager") if callable(get) else getattr(self.framework, "rag_manager", None)
        return rag

    def limits(self) -> Dict[str, int]:
        """The caps of this installation: the person's settings, never above the hard caps."""
        def number(key, default):
            try:
                return int(self._cfg(key, default))
            except (TypeError, ValueError):
                return default
        return {"max_pages": max(1, min(number("crawler_max_pages", 50), PAGE_CAP)),
                "max_depth": max(1, min(number("crawler_max_depth", 2), DEPTH_CAP))}

    def delay(self) -> float:
        try:
            setting = float(self._cfg("crawler_rate_limit", MIN_DELAY))
        except (TypeError, ValueError):
            setting = MIN_DELAY
        return max(MIN_DELAY, setting)

    def default_languages(self) -> Tuple[str, ...]:
        value = self._cfg("crawler_languages", ["en", "de"])
        return tuple(str(v) for v in value) if isinstance(value, (list, tuple)) else ("en", "de")

    def pages_left(self, caller: str) -> int:
        with self._lock:
            budget = self._budgets.get(caller)
            return PAGES_PER_DAY - (budget.total(self.clock()) if budget else 0)

    def running(self, caller: Optional[str] = None) -> int:
        """Unfinished crawls (of one caller, or of everyone in this process)."""
        with self._lock:
            ids = [j for j, c in self._running.items() if caller is None or c == caller]
        alive = 0
        for job_id in ids:
            try:
                if not self.jobs.get(job_id).finished:
                    alive += 1
            except ValueError:
                pass
        return alive

    # --- the plan (before the question) ----------------------------------------------------------------------

    def plan(self, start_urls: List[str], max_pages: Optional[int], max_depth: Optional[int],
             languages: Optional[List[str]], include_pdfs: Optional[bool], caller: str) -> CrawlPlan:
        """Checks everything that can be checked before the person is asked; CrawlRefused with the reason code."""
        from orchestrator.utils.validation import validate_public_url
        if self.notice_needed():
            raise CrawlRefused("The person has not read the one-time Deep Ingest notice yet, so no crawl can start.",
                               code="needs_person", next=NOTICE_NEXT, blocker="ingest_notice_unread")
        if bool(self._cfg("offline_mode", False)):
            raise CrawlRefused("Offline mode is on: the framework does not read web sites.", code="not_supported",
                               next="Only the person can switch offline mode off (in the GUI or with llm-cli).",
                               blocker="offline_mode")
        if not bool(self._cfg("enable_rag_knowledge", True)) or self.rag() is None:
            raise CrawlRefused("The knowledge base is switched off, so nothing can be stored.", code="not_supported",
                               next="Only the person can switch it on (setting enable_rag_knowledge, in the GUI or "
                                    "with llm-cli).", blocker="knowledge_off")
        room = self.snapshot_room()
        if room is not None and not room.fits:  # no snapshot, no crawl: there is no "continue anyway"
            from orchestrator.Core.knowledge_snapshots import FREE_SPACE_STEP, SnapshotNoRoom
            raise CrawlRefused(str(SnapshotNoRoom(room, "the Deep Ingest")), code="insufficient_resources",
                               next=FREE_SPACE_STEP, blocker="snapshot_no_room")
        urls = list(dict.fromkeys(u.strip() for u in start_urls))
        if not 1 <= len(urls) <= MAX_START_URLS:
            raise CrawlRefused(f"A crawl takes 1 to {MAX_START_URLS} different start addresses.",
                               code="invalid_value", next=f"Give at most {MAX_START_URLS} addresses.",
                               blocker="crawl_caps")
        for url in urls:
            check = validate_public_url(url) if len(url) <= MAX_URL_CHARS else None
            if not check:
                raise CrawlRefused(f"'{url[:200]}' is not a public http(s) address the crawler may read.",
                                   code="invalid_value",
                                   next="Give public http:// or https:// addresses of documentation sites.",
                                   blocker="url_not_public")
        caps = self.limits()
        pages = caps["max_pages"] if max_pages is None else max_pages
        depth = min(2, caps["max_depth"]) if max_depth is None else max_depth
        if not 1 <= pages <= caps["max_pages"]:
            raise CrawlRefused(f"max_pages must be 1 to {caps['max_pages']} here (the person's setting "
                               f"crawler_max_pages, at most {PAGE_CAP}).", code="invalid_value",
                               next=f"Call it again with max_pages between 1 and {caps['max_pages']}.",
                               blocker="crawl_caps")
        if not 1 <= depth <= caps["max_depth"]:
            raise CrawlRefused(f"max_depth must be 1 to {caps['max_depth']} here (the person's setting "
                               f"crawler_max_depth, at most {DEPTH_CAP}).", code="invalid_value",
                               next=f"Call it again with max_depth between 1 and {caps['max_depth']}.",
                               blocker="crawl_caps")
        left = self.pages_left(caller)
        if left <= 0:
            raise CrawlRefused(f"This client stored {PAGES_PER_DAY} crawled pages in the last 24 hours, the most a "
                               "day allows.", code="limit_reached", next="Try again tomorrow.", blocker="crawl_quota")
        if pages * len(urls) > left:
            raise CrawlRefused(f"This crawl may store up to {pages * len(urls)} pages, but only {left} of the "
                               f"{PAGES_PER_DAY} pages a day are left for this client.", code="limit_reached",
                               next=f"Ask for fewer pages: max_pages times the number of start addresses at most "
                                    f"{left}.", blocker="crawl_quota")
        langs = tuple(dict.fromkeys(languages)) if languages is not None else self.default_languages()
        pdfs = bool(self._cfg("crawler_download_pdfs", True)) if include_pdfs is None else bool(include_pdfs)
        return CrawlPlan(tuple(urls), pages, depth, self.delay(), langs, pdfs, disk=room)

    def snapshot_room(self):
        """The room for the snapshot taken before the crawl, estimated from the newest snapshot (the knowledge base
        is not opened before the question); None when it cannot be told."""
        from orchestrator.Core.knowledge_snapshots import room_before
        try:
            return room_before(self._state_dir())
        except OSError:
            return None

    # --- the job ---------------------------------------------------------------------------------------------

    def _user_lock(self):
        """One crawl at a time per user, also across framework processes (GUI-started MCP servers, the CLI)."""
        from filelock import FileLock, Timeout
        state = self._state_dir()
        if state is None:
            return None
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = FileLock(str(state / LOCK_NAME), timeout=0, thread_local=False)
        try:
            lock.acquire()
        except Timeout:
            raise CrawlRefused("Another crawl for this user is running (in this or another framework process).",
                               code="limit_reached", blocker="crawl_running",
                               next="Wait until it has ended (get_job_status), then call it again.") from None
        return lock

    def start(self, plan: CrawlPlan, owner: str, caller: str):
        """Starts the crawl of a confirmed plan; returns the job."""
        if self.notice_needed():  # its text may have changed since the question
            raise CrawlRefused("The person has not read the current Deep Ingest notice, so no crawl can start.",
                               code="needs_person", next=NOTICE_NEXT, blocker="ingest_notice_unread")
        if bool(self._cfg("offline_mode", False)):  # switched on while the person was asked
            raise CrawlRefused("Offline mode is on: the framework does not read web sites.", code="not_supported",
                               next="Only the person can switch offline mode off (in the GUI or with llm-cli).",
                               blocker="offline_mode")
        if self.running() > 0:
            raise CrawlRefused("Another crawl for this user is running.", code="limit_reached",
                               next="Wait until it has ended (get_job_status), then call it again.",
                               blocker="crawl_running")
        # Checked again: questions answered later (pending approvals) were each planned against the same budget.
        need, left = plan.max_pages * len(plan.start_urls), self.pages_left(caller)
        if need > left:
            raise CrawlRefused(f"This crawl may store up to {need} pages, but only {max(0, left)} of the "
                               f"{PAGES_PER_DAY} pages a day are left for this client now.", code="limit_reached",
                               next="Try again tomorrow, or ask for fewer pages.", blocker="crawl_quota")
        lock = self._user_lock()
        stop = threading.Event()
        rag = self.rag()

        def work(handle) -> Dict[str, Any]:
            try:  # a stopped crawl keeps what it stored; the job is marked cancelled by cancel_job itself
                return self._crawl(plan, rag, stop, handle, caller)
            finally:
                if lock is not None:
                    lock.release()

        try:
            job = self.jobs.start("crawl", owner, work, on_cancel=stop.set)
        except BaseException:
            if lock is not None:
                lock.release()
            raise
        with self._lock:
            self._running[job.job_id] = caller
        return job

    def _crawl(self, plan: CrawlPlan, rag, stop: threading.Event, handle, caller: str) -> Dict[str, Any]:
        pages = chunks = 0
        skipped: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        outcomes: List[str] = []
        snapshot = None
        no_room = ""
        total = plan.max_pages * len(plan.start_urls)
        for number, url in enumerate(plan.start_urls):
            if stop.is_set():
                break
            done_before = number * plan.max_pages

            def progress(page: int, _of: int, _url: str, base=done_before) -> None:
                # page numbers only: the address is external text and would reach the client unmarked elsewhere
                handle.progress(base + page, total, f"page {base + page} of at most {total}")

            result = rag.ingest_url(url, plan.options(plan.max_pages), stop=stop, progress=progress)
            if result.get("blocker") == "snapshot_no_room":  # nothing more is crawled without its snapshot
                no_room = _no_paths(str(result.get("message") or ""))
                break
            snapshot = snapshot or result.get("snapshot")
            outcomes.append(str(result.get("outcome") or ("success" if result.get("success") else "failure")))
            if result.get("outcome") != "failure":
                pages += int(result.get("documents") or 0)
                chunks += int(result.get("count") or 0)
            for item in result.get("skipped") or []:
                skipped.append({"address": str(item.get("url", ""))[:MAX_URL_CHARS],
                                "reason": str(item.get("reason", ""))[:300], "opt_out": bool(item.get("opt_out"))})
            for item in result.get("failed") or []:
                failed.append({"address": str(item.get("url", ""))[:MAX_URL_CHARS],
                               "reason": str(item.get("reason", ""))[:300]})
            if result.get("outcome") == "failure" and not result.get("documents") and not result.get("skipped") \
                    and not result.get("failed") and result.get("message"):
                # the knowledge base's own error can name its storage folder: never a folder of this computer
                failed.append({"address": url, "reason": _no_paths(str(result["message"]))[:300]})
        with self._lock:
            budget = self._budgets.setdefault(caller, _Budget())
            budget.used.append((self.clock(), pages))
        if no_room and not pages:
            from orchestrator.Core.knowledge_snapshots import FREE_SPACE_STEP
            raise CrawlRefused(no_room, code="insufficient_resources", next=FREE_SPACE_STEP,
                               blocker="snapshot_no_room")
        if not pages:
            outcome = "failure"
        elif stop.is_set() or len(outcomes) < len(plan.start_urls) or any(o != "success" for o in outcomes):
            outcome = "partial"
        else:
            outcome = "success"
        return {"outcome": outcome, "pages": pages, "chunks": chunks,
                "skipped": skipped[:LIST_CAP], "skipped_total": len(skipped),
                "failed": failed[:LIST_CAP], "failed_total": len(failed),
                "opt_outs": sum(1 for s in skipped if s["opt_out"]), "snapshot": snapshot,
                **({"stopped_for": no_room} if no_room else {}),
                "note": "Page text is not returned: read it with search_knowledge or read_knowledge_source. Pages "
                        "a site reserved (robots.txt, robots directives, TDMRep) were left out."}
