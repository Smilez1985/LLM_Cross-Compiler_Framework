#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Crawler Manager

Deep Ingest of documentation: crawls web pages breadth-first, extracts text from HTML and PDFs and prepares
it for the knowledge base. Rate-limited, behind the SSRF guard (every request and every redirect hop).

Which addresses a crawl may read (DECISIONS round 26 Q5): online, public addresses only, for every crawl
(validate_public_url, PublicOnlyAdapter). In offline mode a crawl the person starts (actor "gui" or "cli", the
person's channels) reads only sites on this computer or in the own network, by the one network-locality rule with the
person's opt-outs, over a pinned connection without a proxy (validate_own_network_url, LocalOnlyAdapter with crawl):
every redirect and every followed link is checked again by the same rule, and IPv4 link-local addresses and the cloud
metadata services are never read (utils/locality.crawl_addresses). A crawl an AI starts (crawl_jobs: no person actor)
keeps the public-only guard, so in offline mode it reads nothing.

Text and data mining of public pages is allowed unless the rightsholder reserved it in machine-readable form
(EU DSM Directive Art. 4, German UrhG 44b). The crawler therefore honours, with no switch to turn them off:
* robots.txt as in RFC 9309, per scheme, host and port (the groups naming our product token merged, else the
  '*' groups; '*' and '$' patterns; the longest rule wins). One deliberate deviation: a robots.txt answering 401 or
  403 means "disallow everything" (RFC 9309 allows crawling on a 4xx): a reservation we cannot read still counts;
* X-Robots-Tag headers and <meta name="robots"> / <meta name="llm-cross-compiler-framework-bot">:
  noindex, none, noai and noimageai keep the page out; nofollow and none stop following its links;
  robots.txt groups, X-Robots-Tag scopes and meta tags written for the product token before the repository was
  renamed (LLM-Conversion-Framework-Bot, LEGACY_BOT_NAMES) count as ours too, so an earlier opt-out still holds;
* the W3C TDM Reservation Protocol (TDMRep 1.0): /.well-known/tdmrep.json per origin, the tdm-reservation
  header, <meta name="tdm-reservation"> and tdm:reservation in PDF metadata.
A reservation from any of these sources wins (TDMRep lets a later source lift an earlier reservation; the
crawler stays on the cautious side). Every page left out (opt-out, address check, PDFs off, other language,
neither HTML nor PDF, no text) is logged and listed in `skipped` with the reason, every page that could not be
fetched or read in `failed`.

Hostile sites cannot fill the memory or hold the crawl: each response body is read up to a limit for its type
(BODY_LIMITS: HTML pages 8 MiB, PDFs 64 MiB, robots.txt 500 KiB as RFC 9309 allows, tdmrep.json 1 MiB) and each
request has a deadline for headers and body together (REQUEST_DEADLINE 60 s, PDFs PDF_DEADLINE 180 s); requests'
timeout only bounds each socket read. A page over its limit is skipped, one over its deadline failed, each named
with the reason. A robots.txt over its limit counts up to the limit (its last, cut line left out); one that does not
arrive in time counts as unreachable (nothing is crawled); a tdmrep.json over its limit is not used.

Every page also records what it says about its licence and copyright (rag/licences.py: a licence link or licence
metadata, a Creative Commons link, a copyright notice; for code from GitHub the repository's licence file when the crawl
reads it), read from the whole page before its footer is dropped. The knowledge base keeps the text the crawl was
allowed to read; what a knowledge pack may pass on is decided from these fields when it is exported.

iter_crawl() yields each page as soon as it is extracted, so the caller can store it right away; a stop event
ends the crawl cleanly between two pages, also while a slow response is still arriving, and a progress callback
gets "page N of M" before each request.
"""

import json
import threading
import time
from dataclasses import dataclass, field
import logging
import requests
import re
from urllib.parse import urlparse, urljoin
from typing import Callable, Iterable, Iterator, List, NamedTuple, Set, Optional, Dict, Any, Tuple, Union
from io import BytesIO

# Third-party imports
from bs4 import BeautifulSoup
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
try:
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0  # langdetect is randomized; a fixed seed gives the same verdict for the same page
except ImportError:
    detect = None

from requests.utils import requote_uri
from urllib3.exceptions import HTTPError as Urllib3Error, ReadTimeoutError

from orchestrator import __version__
from orchestrator.rag import licences
from orchestrator.rag.html_text import html_to_markdown, parse_page, strip_boilerplate
# The SSRF-safe adapter lives in orchestrator/security/http.py (consolidation chain c1-imports); the names stay
# importable here.
from orchestrator.security.http import (PublicOnlyAdapter, _PinnedConnectionMixin,  # noqa: F401
                                        _PinnedHTTPConnection, _PinnedHTTPConnectionPool, _PinnedHTTPSConnection,
                                        _PinnedHTTPSConnectionPool)
from orchestrator.tools.confirmation import visible
from orchestrator.utils.logging import get_logger
from orchestrator.utils.validation import validate_own_network_url, validate_public_url

DEFAULT_LANGUAGES = ("en", "de")

BOT_NAME = "LLM-Cross-Compiler-Framework-Bot"
# The product token before the repository was renamed to LLM_Cross-Compiler_Framework. Sites may have written robots.txt
# groups, X-Robots-Tag scopes or <meta name="..."> tags for it: they keep applying to this crawler (decision Q-1,
# robots.txt is respected), so an opt-out made earlier still holds. Only ever extended, never shortened.
LEGACY_BOT_NAMES = ("LLM-Conversion-Framework-Bot",)
BOT_TOKENS = (BOT_NAME, *LEGACY_BOT_NAMES)
USER_AGENT = f"{BOT_NAME}/{__version__} (Open Source; +https://github.com/Smilez1985/LLM_Cross-Compiler_Framework)"
# Robots directives that keep a page out of the knowledge base, and those that stop following its links.
SKIP_DIRECTIVES = frozenset({"noindex", "none", "noai", "noimageai"})
NOFOLLOW_DIRECTIVES = frozenset({"nofollow", "none"})
# Directives with a value of their own; any other "word:" prefix in X-Robots-Tag names a crawler.
_VALUED_DIRECTIVES = frozenset({"unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"})
TDMREP_PATH = "/.well-known/tdmrep.json"
MAX_TDM_RULES = 1000
_KIB, _MIB = 1024, 1024 * 1024
_BODY_LABELS = {"html": "HTML pages", "pdf": "PDFs", "robots": "robots.txt", "tdmrep": "tdmrep.json"}
_READ_SIZE = 64 * 1024
_PDF_TDM_RESERVATION = re.compile(rb"tdm:reservation\s*(?:=\s*[\"']\s*1\s*[\"']|>\s*1\s*<)")
# The person's own channels (the actor of a call: "gui", "cli"; an AI is "ditto", "mcp" or "mcp:<token>"): only a crawl
# the person starts may read the own network in offline mode.
PERSON_ACTORS = ("gui", "cli")
OFFLINE_REFUSAL = ("Offline mode is on, so this crawl reads no web site: only a crawl the person starts reads sites in "
                   "the own network then.")


class ResponseRefused(Exception):
    """A response the crawler does not read to the end: larger than the limit for its type, or not complete within
    its deadline. The message is the reason named in the crawl log."""


class ResponseTooLarge(ResponseRefused):
    pass


class ResponseTooSlow(ResponseRefused, requests.exceptions.Timeout):
    """Also a requests timeout: a robots.txt that does not arrive counts as unreachable."""


class _CrawlStopped(Exception):
    """The stop event was set while a request was running."""


def _size_text(size: int) -> str:
    if size % _MIB == 0:
        return f"{size // _MIB} MiB"
    return f"{size // _KIB} KiB" if size % _KIB == 0 else f"{size} bytes"


def _close(response, read_to_the_end: bool) -> None:
    """Releases a streamed response. A body not read to its end closes the connection: its rest must never be read
    as the start of the next response on a reused connection."""
    try:
        raw = getattr(response, "raw", None)
        if not read_to_the_end and raw is not None and callable(getattr(raw, "close", None)):
            raw.close()
        if callable(getattr(response, "close", None)):
            response.close()
    except Exception:
        pass


def _read_body(response, limit: int, label: str, cut: bool, abandoned: threading.Event) -> None:
    """Reads the body into response.content: at most `limit` bytes. Over the limit, cut=True keeps the first
    `limit` bytes (response.body_cut = True); otherwise ResponseTooLarge. Reads return as soon as data arrives
    (read1), so an abandoned request ends with the next piece of data or the next read timeout."""
    too_large = ResponseTooLarge(f"response larger than {_size_text(limit)} (the limit for {label})")
    read1 = getattr(getattr(response, "raw", None), "read1", None)
    if not callable(read1):  # a response without a stream (prepared responses in tests)
        body = response.content or b""
        if len(body) > limit and not cut:
            raise too_large
        response.content, response.body_cut = body[:limit], len(body) > limit
        return
    declared = str(response.headers.get("Content-Length") or "").strip()
    if not cut and declared.isdigit() and int(declared) > limit:
        _close(response, False)
        raise too_large
    body, complete = bytearray(), False
    try:
        while len(body) <= limit and not abandoned.is_set():
            data = read1(_READ_SIZE, decode_content=True)  # decoded: a compressed bomb counts at its real size
            if not data:
                complete = True
                break
            body += data
    except ReadTimeoutError as e:
        raise requests.exceptions.ReadTimeout(str(e)) from None
    except (Urllib3Error, OSError) as e:
        raise requests.exceptions.ConnectionError(f"reading the response failed: {e}") from None
    finally:
        _close(response, complete)
    if abandoned.is_set():
        raise _CrawlStopped()
    if len(body) > limit and not cut:
        raise too_large
    response._content, response._content_consumed = bytes(body[:limit]), True
    response.body_cut = len(body) > limit


@dataclass(frozen=True)
class SkippedPage:
    """A page the crawl left out or could not read. opt_out: the site reserved it (robots.txt, robots
    directives, TDMRep)."""
    url: str
    reason: str
    opt_out: bool = False


class TdmRule(NamedTuple):
    location: str
    reserved: Optional[bool]   # None: an invalid value, which TDMRep treats as unset
    policy: Optional[str]


def _bot_tokens(bot: Union[str, Iterable[str]]) -> Set[str]:
    """The lower-case product tokens that name this crawler: one token, or several (the current one and the earlier
    ones in BOT_TOKENS)."""
    return {bot.lower()} if isinstance(bot, str) else {str(token).lower() for token in bot}


def robots_directives(values: Iterable[str], bot: Union[str, Iterable[str]] = BOT_TOKENS) -> Set[str]:
    """Lower-case directives of X-Robots-Tag or meta robots values that apply to this crawler: unscoped
    ones and those after "<bot>:" for any of its product tokens (a scope lasts until the next one within the same
    value). Directives are separated by commas; "noindex nofollow" (spaces only) counts too."""
    ours = _bot_tokens(bot)
    found: Set[str] = set()
    for value in values:
        scope = None
        for part in str(value).lower().split(","):
            part = part.strip()
            scoped = re.match(r"([a-z0-9_.-]+)\s*:\s*(.*)", part)
            if scoped and scoped.group(1) not in _VALUED_DIRECTIVES:
                scope, part = scoped.group(1), scoped.group(2)
            if (scope is None or scope in ours) and part:
                found.update(word.split(":", 1)[0].strip() for word in ([part] if ":" in part else part.split()))
    return found


def _robots_opt_out(label: str, values: Iterable[str]) -> Tuple[List[str], bool]:
    """The reason if noindex, none, noai or noimageai apply to this crawler; whether to stop following links."""
    directives = robots_directives(values)
    hits = sorted(directives & SKIP_DIRECTIVES)
    return ([f"{label}: {', '.join(hits)}"] if hits else []), bool(directives & NOFOLLOW_DIRECTIVES)


def tdm_flag(value: Any) -> Optional[bool]:
    """TDMRep value: 1 reserved, 0 not reserved; anything else is a protocol error and counts as unset."""
    return None if value is None else {"1": True, "0": False}.get(str(value).strip())


def _root_pattern(location: str) -> str:
    """A pattern written relative ("docs/*") or as a full URL means the same path from the root (cautious)."""
    if re.match(r"https?://", location, re.I):
        return urlparse(location).path or "/"
    return location if location.startswith(("/", "*")) else "/" + location


def tdm_location_matches(location: str, path: str) -> bool:
    """TDMRep location pattern (robots.txt syntax, case-sensitive): matches from the start of the path,
    '*' is any run of characters, a trailing '$' anchors the end. Greedy segment search, no regex."""
    location = _root_pattern(location)
    anchored = location.endswith("$")
    parts = requote_uri(location[:-1] if anchored else location).split("*")
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    if len(parts) == 1:
        return not anchored or pos == len(path)
    for part in parts[1:-1]:
        pos = path.find(part, pos)
        if pos < 0:
            return False
        pos += len(part)
    if anchored:
        return path.endswith(parts[-1]) and len(path) - len(parts[-1]) >= pos
    return path.find(parts[-1], pos) >= 0


def robots_rules(text: str, bot: Union[str, Iterable[str]] = BOT_TOKENS) -> List[Tuple[bool, str]]:
    """(allow, pattern) rules of a robots.txt for this crawler (RFC 9309): all groups whose user-agent equals
    one of its product tokens (case-insensitive; a group for "bot" is not ours; groups for the current and the
    earlier token are merged), else all '*' groups.
    urllib.robotparser would ignore '*'/'$' patterns and every '*' group after the first."""
    tokens = _bot_tokens(bot)
    groups: List[Tuple[Set[str], List[Tuple[bool, str]]]] = []
    in_rules = False
    for line in text.splitlines():
        key, _, value = line.split("#", 1)[0].partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not groups or in_rules:   # a user-agent line after rules starts a new group
                groups.append((set(), []))
                in_rules = False
            # The product token is letters, '_' and '-' (RFC 9309 2.2.1): "Bot/3.1" names "bot".
            groups[-1][0].add(value if value == "*" else re.match(r"[a-z_-]*", value.lower()).group(0))
        elif key in ("allow", "disallow") and groups:
            in_rules = True
            if value:   # "Disallow:" (empty) allows everything
                groups[-1][1].append((key == "allow", _root_pattern(value)))
    ours = [group for group in groups if tokens & group[0]]
    return [rule for _, rules in (ours or [g for g in groups if "*" in g[0]]) for rule in rules]


def robots_allows(rules: List[Tuple[bool, str]], path: str) -> bool:
    """The longest matching rule decides; allow wins a tie; no matching rule means allowed (RFC 9309 2.2.2)."""
    best = (-1, True)
    for allow, pattern in rules:
        if tdm_location_matches(pattern, path):
            best = max(best, (len(pattern), allow))
    return best[1]


def _origin_and_path(url: str) -> Tuple[str, str]:
    """scheme://host[:port] and the path with its query: robots.txt (RFC 9309 2.3) and tdmrep.json apply per
    origin, so http:// and https:// of one host have their own."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}", (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")


def _utf8(response) -> str:
    """Body of a robots.txt or tdmrep.json (both UTF-8); a byte order mark would hide the first line or rule."""
    return (response.content or b"").decode("utf-8-sig", errors="replace")


def _header_values(response, name: str) -> List[str]:
    """Every value of a header; requests joins repeated headers with commas, which would merge their scopes."""
    raw = getattr(getattr(response, "raw", None), "headers", None)
    if hasattr(raw, "getlist"):
        return [str(v) for v in raw.getlist(name)]
    value = response.headers.get(name)
    return [value] if value else []


def _remote_text(value: Any, limit: int = 200) -> str:
    """Site-controlled text for log lines and reasons: control characters escaped, length capped."""
    return visible(str(value))[:limit]


@dataclass
class Document:
    """A crawled page or PDF (replaces langchain_core's Document). hidden_removed: how many hidden elements with text
    the extraction left out, by kind (rag/html_text.HIDDEN_KINDS); the knowledge base records it with the page."""
    page_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    hidden_removed: Dict[str, int] = field(default_factory=dict)


class CrawlerManager:
    """
    Breadth-first web crawler: robots.txt, robots directives and TDMRep reservations, rate limit, language
    filter, PDF support, SSRF guard on every request. Skipped pages end up in `skipped`, pages that could not
    be fetched or read in `failed`, each with the reason; `stopped` tells whether a stop event ended the crawl.
    """

    MAX_REDIRECTS = 5
    # Body limits per response type and deadlines per request (headers and body); see the module docstring.
    BODY_LIMITS = {"html": 8 * _MIB, "pdf": 64 * _MIB, "robots": 500 * _KIB, "tdmrep": 1 * _MIB}
    REQUEST_DEADLINE = 60.0
    PDF_DEADLINE = 180.0

    # Options a crawl may set (the Deep Ingest dialog, the crawl_docs_site job); anything else is ignored with a log
    # line. No opt-out check is among them: robots.txt, the robots directives and TDMRep are always checked.
    OPTION_KEYS = frozenset({"max_depth", "max_pages", "request_delay", "download_pdfs", "languages"})

    def __init__(self, framework_manager, options: Optional[Dict[str, Any]] = None, *, actor: str = "",
                 _skip_robots_txt_for_tests: bool = False):
        """`actor`: who starts the crawl ("gui" or "cli" for the person; anything else, also none, is an AI's crawl,
        which reads public sites only and nothing in offline mode)."""
        self.logger = get_logger(__name__)
        self.config = framework_manager.config
        from orchestrator.utils.locality import offline_mode
        self.offline = offline_mode(self.config)
        # Round 26 Q5: in offline mode a crawl the person starts reads the own network (and only that)
        self.own_network = self.offline and actor in PERSON_ACTORS
        opts = dict(options or {})
        ignored = sorted(str(k) for k in opts if k not in self.OPTION_KEYS)
        if ignored:
            self.logger.warning(f"Crawl options ignored: {', '.join(ignored)[:200]} (opt-out checks cannot be "
                                "switched off).")
            opts = {k: v for k, v in opts.items() if k in self.OPTION_KEYS}

        # Per-crawl options (e.g. from the Deep Ingest dialog) override the stored defaults. robots.txt is an
        # opt-out like the others, so neither the dialog, the config nor an options dict can switch it off. The
        # keyword-only flag exists for tests with a fake web only (no product code passes it; a guard test in
        # tests/test_security.py checks that).
        self.respect_robots = not _skip_robots_txt_for_tests
        self.max_depth = int(opts.get("max_depth", self.config.get("crawler_max_depth", 2)))
        self.max_pages = int(opts.get("max_pages", self.config.get("crawler_max_pages", 50)))
        self.rate_limit = float(opts.get("request_delay", self.config.get("crawler_rate_limit", 1.0)))
        self.download_pdfs = bool(opts.get("download_pdfs", self.config.get("crawler_download_pdfs", True)))
        # Pages in other languages are skipped (with their links); an empty list keeps every language.
        self.languages = self._language_codes(opts.get("languages", self.config.get("crawler_languages",
                                                                                    list(DEFAULT_LANGUAGES))))
        # Redirects and failed fetches also cost requests; this bounds the total work per crawl.
        self.max_requests = self.max_pages * 5
        
        # State
        self.visited: Set[str] = set()
        self.skipped: List[SkippedPage] = []
        self.failed: List[SkippedPage] = []
        self.stopped = False
        self.complete = False
        self._links_left = False
        self._stop = threading.Event()  # iter_crawl's stop event; a running request ends when it is set
        self.robot_rules: Dict[str, List[Tuple[bool, str]]] = {}   # per origin, from robots.txt
        self.tdm_rules: Dict[str, List[TdmRule]] = {}   # per origin, from /.well-known/tdmrep.json
        self.session = requests.Session()
        if self.own_network:   # only the checked local addresses, also after a DNS answer changed (rule e and f)
            from orchestrator.security.http import LocalOnlyAdapter
            adapter = LocalOnlyAdapter(self.config, "the crawl", crawl=True)
        else:
            adapter = PublicOnlyAdapter()
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _address_refusal(self, url: str) -> Optional[str]:
        """Why the crawl may not read `url` (None when it may): the public-only SSRF guard, or in offline mode for the
        person's crawl the one network-locality rule (validate_own_network_url); an AI's crawl in offline mode reads
        nothing."""
        from orchestrator.utils.locality import offline_mode
        if self.own_network:
            check = validate_own_network_url(url, self.config)
        elif self.offline or offline_mode(self.config):
            # offline mode, also when it was switched on during a crawl that started online: nothing more is read
            return OFFLINE_REFUSAL
        else:
            check = validate_public_url(url)
        return None if check else check.message

    def _guarded_get(self, url: str, kind: str = "page", timeout: int = 10) -> requests.Response:
        """Fetches exactly the URL that passed the SSRF check, without following redirects. kind is "page",
        "robots" or "tdmrep". The body of a 200 response that the crawl reads (robots.txt, tdmrep.json, an HTML
        page, a PDF when PDFs are on) is read here, up to BODY_LIMITS for its type; other responses keep no body.
        Headers and body must arrive within the deadline (ResponseTooSlow); a set stop event ends the wait at once.
        The request runs in a worker thread, so neither a slow server nor a stop has to wait for a socket read."""
        prepared = self.session.prepare_request(requests.Request("GET", url))
        refused = self._address_refusal(prepared.url)
        if refused:
            raise PermissionError(refused)
        # send() alone skips what get() does: env proxies and REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE. Streamed: the
        # body is read below, within its limit.
        settings = self.session.merge_environment_settings(prepared.url, {}, True, None, None)
        if self.own_network:
            settings["proxies"] = {}   # offline mode: no proxy, which would resolve the name itself
        start = time.monotonic()
        deadline = [self.REQUEST_DEADLINE]  # seconds; the worker extends it for a PDF
        result: Dict[str, Any] = {}
        done, abandoned = threading.Event(), threading.Event()

        def fetch():
            try:
                response = self.session.send(prepared, timeout=timeout, allow_redirects=False, **settings)
                body = self._body_type(kind, response, url)
                if body is None:
                    _close(response, False)
                    response._content, response._content_consumed = b"", True
                else:
                    if body == "pdf":
                        deadline[0] = max(deadline[0], self.PDF_DEADLINE)
                    _read_body(response, self.BODY_LIMITS[body], _BODY_LABELS[body], body == "robots", abandoned)
                result["response"] = response
            except BaseException as e:  # handed to the caller
                result["error"] = e
            finally:
                done.set()

        threading.Thread(target=fetch, name="crawler-request", daemon=True).start()
        while not done.wait(0.1):
            if self._stop.is_set() or time.monotonic() - start > deadline[0]:
                abandoned.set()  # the worker closes the response with its next read
                if self._stop.is_set():
                    raise _CrawlStopped()
                raise ResponseTooSlow(f"no complete response within {deadline[0]:g} s")
        if "error" in result:
            raise result["error"]
        return result["response"]

    def _body_type(self, kind: str, response, url: str) -> Optional[str]:
        """Which limit applies to the body of this response, or None when the crawl does not read its body."""
        if response.status_code != 200 or response.is_redirect:
            return None
        if kind in ("robots", "tdmrep"):
            return kind
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "application/pdf" in content_type or url.endswith(".pdf"):
            return "pdf" if self.download_pdfs else None
        return "html" if "text/html" in content_type else None

    def crawl(self, root_url: str) -> List[Document]:
        """
        Crawls breadth-first from root_url and returns the pages as Documents. Pages left out (opt-outs,
        unsafe or off-site redirects, other languages) are listed in self.skipped with the reason.
        """
        return list(self.iter_crawl(root_url))

    def iter_crawl(self, root_url: str, stop: Optional[threading.Event] = None,
                   progress: Optional[Callable[[int, int, str], None]] = None) -> Iterator[Document]:
        """
        Like crawl(), but yields each page as soon as it is extracted. Setting stop ends the crawl before the
        next request (the politeness delay is cut short too); progress gets (page number, max pages, url)
        before each request.
        """
        self.logger.info(f"Starting Deep Ingest for: {root_url}")
        self.visited.clear()
        self.skipped, self.failed, self.stopped = [], [], False
        self.complete = False   # set below: every page of the site was reached (a re-crawl may then drop gone pages)
        # links a limit of this crawl left unread (its depth, or a page left out for its language with its links): the
        # pages behind them are not gone, only not reached, so the crawl is not complete
        self._links_left = False
        stop = self._stop = stop or threading.Event()
        collected = 0

        # Queue für BFS: (url, current_depth, redirect_hops)
        queue = [(root_url, 0, 0)]
        allowed_hosts = {urlparse(root_url).netloc}

        pages_crawled = 0
        requests_made = 0

        while queue and pages_crawled < self.max_pages:
            if stop.is_set():
                self.stopped = True
                break
            if requests_made >= self.max_requests:
                self.logger.warning(f"Request budget of {self.max_requests} exhausted, stopping crawl.")
                break

            url, depth, hops = queue.pop(0)
            # Percent-encode what links may contain raw (e.g. spaces in "User Guide.pdf").
            url = requote_uri(url)

            if url in self.visited:
                continue
            self.visited.add(url)

            # 1. SSRF / format check (offline mode: the own network only, for the person's crawl)
            refused = self._address_refusal(url)
            if refused:
                reason = refused if self.offline or refused == OFFLINE_REFUSAL \
                    else "unsafe or invalid address (security check)"
                self._skip(url, reason, level=logging.WARNING)
                continue

            if not self.download_pdfs and urlparse(url).path.lower().endswith('.pdf'):
                self._skip(url, "PDF downloads are switched off")
                continue

            # 2. Opt-outs known before the request: robots.txt, then the origin's TDMRep file.
            try:
                allowed = self._can_fetch(url)
                reserved = self._tdm_file_reservation(url) if allowed else None
            except _CrawlStopped:
                self.stopped = True
                break
            if not allowed:
                self._skip(url, "disallowed by robots.txt", opt_out=True)
                continue
            if reserved:
                self._skip(url, reserved, opt_out=True)
                continue

            # 3. Fetch Content
            self.logger.info(f"Crawling ({pages_crawled}/{self.max_pages}): {url}")
            if progress:
                progress(pages_crawled + 1, self.max_pages, url)
            requests_made += 1
            if stop.wait(self.rate_limit):  # politeness delay, cut short by a stop
                self.stopped = True
                break
            try:
                response = self._guarded_get(url)

                # Redirect targets are queued instead of followed, so they pass the SSRF check and the
                # opt-out checks of their own host (robots.txt, tdmrep.json) too.
                if response.is_redirect:
                    location = urljoin(url, response.headers.get('Location', ''))
                    host = urlparse(location).netloc
                    if hops >= self.MAX_REDIRECTS:
                        self._fail(url, f"more than {self.MAX_REDIRECTS} redirects")
                    elif depth > 0 and host not in allowed_hosts:
                        # A link on the site that redirects elsewhere is an external link.
                        if location not in self.visited:
                            self.visited.add(location)
                            self._skip(location, f"redirect from {url} leaves the crawled site")
                    elif location and location not in self.visited:
                        if depth == 0:
                            allowed_hosts.add(host)  # e.g. example.com -> www.example.com
                        queue.insert(0, (location, depth, hops + 1))
                    continue

                if response.status_code != 200:
                    self._fail(url, f"HTTP status {response.status_code}")
                    continue

                content_type = response.headers.get('Content-Type', '').lower()
                pages_crawled += 1

                # 4. Opt-outs in the response, then the content
                reasons, nofollow = self._header_opt_outs(response)
                doc, new_links, left_out = None, [], None

                if 'application/pdf' in content_type or url.endswith('.pdf'):
                    if not self.download_pdfs:
                        left_out = "PDF downloads are switched off"
                    elif not reasons and _PDF_TDM_RESERVATION.search(response.content or b""):
                        reasons.append("TDM reservation (tdm:reservation in the PDF metadata)")
                    elif not reasons:
                        doc = self._process_pdf(response.content, url, response.headers)
                elif 'text/html' in content_type:
                    if not (reasons and nofollow):
                        soup = parse_page(response.text)   # read the way a browser reads it (rag/html_text)
                        meta_reasons, meta_nofollow = self._meta_opt_outs(soup)
                        reasons += meta_reasons
                        nofollow = nofollow or meta_nofollow
                        if reasons:
                            self._strip_boilerplate(soup)
                            new_links = [] if nofollow else self._html_links(soup, url, allowed_hosts)
                        else:
                            doc, new_links = self._process_html(soup, url, allowed_hosts, response.headers)
                else:
                    left_out = f"neither HTML nor PDF ({_remote_text(content_type) or 'no content type'})"

                if reasons:
                    self._skip(url, "; ".join(reasons), opt_out=True)
                elif left_out:
                    self._skip(url, left_out)
                elif doc:
                    collected += 1
                    yield doc
                if nofollow and new_links:
                    self.logger.info(f"Not following the links of {url} (nofollow).")
                    new_links = []

                # 5. Enqueue new links if depth allows
                if depth < self.max_depth:
                    for link in new_links:
                        if link not in self.visited:
                            queue.append((link, depth + 1, 0))
                elif any(link not in self.visited for link in new_links):
                    self._links_left = True

            except _CrawlStopped:
                self.stopped = True
                break
            except ResponseTooLarge as e:  # left out by a limit, like a type the crawl does not read
                pages_crawled += 1  # it arrived (only 200 responses are read): it counts like any other page
                self._skip(url, str(e), level=logging.WARNING)
            except ResponseTooSlow as e:  # could not be read
                self._fail(url, str(e))
            except Exception as e:
                self._fail(url, _remote_text(e), level=logging.ERROR)

        self.complete = not self.stopped and not self._links_left \
            and not any(item[0] not in self.visited for item in queue)
        if self.stopped:
            self.logger.info(f"Crawl stopped on request after {collected} documents.")
        self.logger.info(f"Crawl finished. Collected {collected} documents, skipped {len(self.skipped)} pages, "
                         f"{len(self.failed)} failed.")

    def _skip(self, url: str, reason: str, opt_out: bool = False, level: int = logging.INFO) -> None:
        """Records a page the crawl leaves out; every skip is named in the log with its reason."""
        self.skipped.append(SkippedPage(url=url, reason=reason, opt_out=opt_out))
        self.logger.log(level, f"Skipped {_remote_text(url, 500)}: {reason}")

    def _fail(self, url: str, reason: str, level: int = logging.WARNING) -> None:
        """Records a page that could not be fetched or read."""
        self.failed.append(SkippedPage(url=url, reason=reason))
        self.logger.log(level, f"Failed {_remote_text(url, 500)}: {reason}")

    def _header_opt_outs(self, response) -> Tuple[List[str], bool]:
        """Reservations in the response headers (X-Robots-Tag, TDMRep tdm-reservation) and whether the
        page's links must not be followed."""
        reasons, nofollow = _robots_opt_out("X-Robots-Tag", _header_values(response, "X-Robots-Tag"))
        # Repeated headers arrive joined ("1, 1"); any reserving value counts.
        if any(tdm_flag(v) for value in _header_values(response, "tdm-reservation") for v in value.split(",")):
            reasons.append(self._tdm_reason("tdm-reservation header", response.headers.get("tdm-policy")))
        return reasons, nofollow

    def _meta_opt_outs(self, soup: BeautifulSoup) -> Tuple[List[str], bool]:
        """Reservations in <meta name="robots">, <meta name="<our bot>"> (the current or an earlier product token)
        and <meta name="tdm-reservation">."""
        reasons, nofollow, bots = [], False, _bot_tokens(BOT_TOKENS)
        for meta in soup.find_all("meta", attrs={"name": True, "content": True}):
            name = str(meta["name"]).strip().lower()
            if name == "robots" or name in bots:
                found, stop = _robots_opt_out(f'<meta name="{name}">', [str(meta["content"])])
                reasons, nofollow = reasons + found, nofollow or stop
            elif name == "tdm-reservation" and tdm_flag(meta["content"]):
                reasons.append('TDM reservation (<meta name="tdm-reservation">)')
        return reasons, nofollow

    @staticmethod
    def _tdm_reason(source: str, policy: Optional[str]) -> str:
        return f"TDM reservation ({source}" + (f", policy {_remote_text(policy)})" if policy else ")")

    def _tdm_file_reservation(self, url: str) -> Optional[str]:
        """The reason when the origin's /.well-known/tdmrep.json reserves this URL (the first matching rule
        decides, TDMRep 1.0 section 3.2). The file is fetched once per origin."""
        origin, path = _origin_and_path(url)
        if origin not in self.tdm_rules:
            self.tdm_rules[origin] = self._load_tdm_rules(origin + TDMREP_PATH)
        for rule in self.tdm_rules[origin]:
            if tdm_location_matches(rule.location, path):
                if rule.reserved:
                    return self._tdm_reason(f"{TDMREP_PATH} rule '{_remote_text(rule.location)}'", rule.policy)
                return None
        return None

    def _load_tdm_rules(self, file_url: str) -> List[TdmRule]:
        """TDMRep rules of one origin, fetched through the SSRF guard. A missing or unreadable file means the
        origin does not use the protocol (TDMRep 1.0); a page header or meta tag can still reserve a page."""
        try:
            response = self._get_following_redirects(file_url, "tdmrep")
            if response.status_code != 200:
                return []
            data = json.loads(_utf8(response))
        except _CrawlStopped:
            raise
        except ResponseRefused as e:
            self.logger.warning(f"Not using {file_url}: {e}.")
            return []
        except Exception as e:
            self.logger.info(f"No usable {file_url} ({_remote_text(e)}).")
            return []
        if not isinstance(data, list):
            self.logger.warning(f"Ignoring {file_url}: not a JSON array of rules.")
            return []
        rules = []
        for item in data[:MAX_TDM_RULES]:
            if isinstance(item, dict) and isinstance(item.get("location"), str):
                policy = item.get("tdm-policy")
                rules.append(TdmRule(item["location"], tdm_flag(item.get("tdm-reservation")),
                                     policy if isinstance(policy, str) else None))
        return rules

    def _get_following_redirects(self, url: str, kind: str) -> requests.Response:
        """GET that follows up to MAX_REDIRECTS redirects, each hop through the SSRF guard again."""
        for _ in range(self.MAX_REDIRECTS + 1):
            response = self._guarded_get(url, kind)
            if not response.is_redirect:
                return response
            url = urljoin(url, response.headers.get('Location', ''))
        raise RuntimeError("too many redirects")

    def _can_fetch(self, url: str) -> bool:
        """Checks the origin's robots.txt (fetched once per scheme, host and port)."""
        if not self.respect_robots:
            return True
        origin, path = _origin_and_path(url)
        if origin not in self.robot_rules:
            self.robot_rules[origin] = self._load_robots(origin + "/robots.txt")
        return robots_allows(self.robot_rules[origin], path)

    def _load_robots(self, robots_url: str) -> List[Tuple[bool, str]]:
        """robots.txt rules through the SSRF guard, every redirect hop checked again."""
        disallow_all = [(False, "/")]
        try:
            response = self._get_following_redirects(robots_url, "robots")
            if response.status_code == 200:
                text = _utf8(response)
                if getattr(response, "body_cut", False):
                    # RFC 9309 2.5: a parsing limit of at least 500 KiB; the last line may be cut in the middle.
                    # Lines end with LF, CR or CRLF (RFC 9309 2.2).
                    text = text[:max(text.rfind("\n"), text.rfind("\r")) + 1]
                    limit = _size_text(self.BODY_LIMITS["robots"])
                    self.logger.warning(f"{robots_url} is larger than {limit}; only its first {limit} are used.")
                return robots_rules(text)
            if response.status_code in (401, 403) or response.status_code >= 500:
                # A server error means complete disallow (RFC 9309 2.3.1.4). 401 and 403 deviate on purpose:
                # RFC 9309 2.3.1.3 allows crawling on a 4xx, but a robots.txt locked away may hold a reservation
                # we cannot read, and when in doubt the crawler does not crawl.
                return disallow_all
            return []
        except _CrawlStopped:
            raise
        except requests.RequestException as e:  # also ResponseTooSlow
            self.logger.warning(f"Could not reach {robots_url} ({e}). Treating the site as disallowed.")
            return disallow_all
        except Exception as e:
            # Blocked by the SSRF guard or too many redirects: RFC 9309 treats robots.txt as unavailable.
            self.logger.warning(f"Could not read {robots_url} ({e}). Assuming allowed.")
            return []

    @staticmethod
    def _language_codes(value: Any) -> Set[str]:
        """ISO 639-1 codes from a list or a comma-separated string (environment variables arrive as strings)."""
        items = value.split(",") if isinstance(value, str) else (value or [])
        return {str(item).strip().lower().split("-")[0] for item in items if str(item).strip()}

    def _detect_language(self, text: str) -> Optional[str]:
        """Language of the prose (code blocks would skew the guess), None if unknown."""
        if not detect:
            return None
        prose = re.sub(r"(?ms)^(`{3,}|~{3,}).*?^\1\s*$", " ", text)
        try:
            return detect(prose[:1500]).split("-")[0]
        except Exception:
            return None

    @staticmethod
    def _strip_boilerplate(soup: BeautifulSoup) -> Dict[str, int]:
        """Removes navigation, footers, scripts and similar, and every element a person reading the page does not see
        (rag/html_text.strip_boilerplate); the links of both are not followed either. Returns how many hidden
        elements with text were removed, by kind."""
        return strip_boilerplate(soup)

    @staticmethod
    def _html_links(soup: BeautifulSoup, url: str, allowed_hosts: Set[str]) -> List[str]:
        """Links to the crawled site's hosts, without fragment links."""
        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(url, href)
            if urlparse(full_url).netloc in allowed_hosts and '#' not in href:
                links.append(full_url)
        return links

    def _process_html(self, html_content: Any, url: str, allowed_hosts: Set[str],
                      headers: Optional[Any] = None) -> tuple[Optional[Document], List[str]]:
        """Extracts the page (HTML text or parsed soup) as Markdown (headings, code blocks, tables kept) and
        its links. Its licence and copyright notice are read first, from the whole page (the footer included). Text
        hidden from a person reading the page is left out; how much, by kind, is the Document's hidden_removed."""
        soup = html_content if isinstance(html_content, BeautifulSoup) else parse_page(html_content)
        licence = licences.from_html(soup, url, headers)
        removed = self._strip_boilerplate(soup)
        if removed:
            self.logger.info(f"Left out hidden text of {_remote_text(url, 500)}: "
                             + ", ".join(f"{kind} {count}" for kind, count in removed.items()))
        links = self._html_links(soup, url, allowed_hosts)
        title, text = html_to_markdown(soup)

        # Language filter (configurable, crawler_languages); detection failures keep the page.
        lang = self._detect_language(text)
        if lang and self.languages and lang not in self.languages:
            self._skip(url, f"language '{lang}' is not one of {', '.join(sorted(self.languages))}")
            if any(link not in self.visited for link in links):
                self._links_left = True   # its links are not followed: what lies behind them was not read
            return None, []
        if not text.strip():
            self._skip(url, "no text")
            return None, links
        metadata = {"source": url, "content_type": "html", "title": title, **licence}
        if lang:
            metadata["language"] = lang
        return Document(page_content=text, metadata=metadata, hidden_removed=removed), links

    def _process_pdf(self, pdf_bytes: bytes, url: str, headers: Optional[Any] = None) -> Optional[Document]:
        """The PDF's text with its licence and copyright notice; a PDF without text (e.g. scanned) is skipped, an
        unreadable one failed."""
        if not PdfReader:
            self._skip(url, "PDF support (pypdf) is not installed", level=logging.WARNING)
            return None
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "".join(extracted + "\n" for page in reader.pages if (extracted := page.extract_text()))
            if not text.strip():
                self._skip(url, "no text in the PDF")
                return None
            metadata = {"source": url, "content_type": "pdf", **licences.from_pdf(pdf_bytes, text, url, headers)}
            info_title = getattr(reader.metadata, "title", None) if reader.metadata else None
            if isinstance(info_title, str) and info_title.strip():
                metadata["title"] = info_title.strip()
            return Document(page_content=text, metadata=metadata)
        except Exception as e:
            self._fail(url, f"unreadable PDF ({_remote_text(e)})", level=logging.ERROR)
            return None
