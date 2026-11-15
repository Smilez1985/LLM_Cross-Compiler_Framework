#!/usr/bin/env python3
"""
Ditto's web search (DESIGN, Knowledge base and Deep Ingest; decisions of 2026-11-14 and 2026-11-15, rounds 97, 100, 101).

One search uses every way the person has turned on, in parallel within a time limit, and merges what they find:

- `searxng`: a SearXNG instance, the person's own or one in the home network (no key). Its address is the setting
  `web_search_url` (default http://127.0.0.1:8888); the instance must allow the format `json` in its `search: formats`.
- `api`: search APIs with the person's own keys, every provider whose key is in the secrets store and which the setting
  `web_search_apis` lists: Tavily, Exa, Bright Data's SERP API, Serper, SearchApi and Brave Search. A provider's free
  allowance and price are not written down here; they are the provider's to state.
- `html`: the HTML results of a search page (DuckDuckGo's HTML page), off until the person turns it on. The page's
  robots.txt is read first and a "no" is respected.

A hit is a public https address, a title and a short snippet, cleaned and meant to be read as data; it is never a page
(reading a page is Deep Ingest's path). The same address found by several ways is one hit with all its ways. Each hit
says where it comes from: `vendor` (a listed manufacturer's website), `repository`, `forum` or `other`. The query goes
through the privacy filter at the person's `ai_security_level` first, and a query that still holds a path or a secret is
refused. In offline mode only a SearXNG instance on a local address is reached. Nothing a search returns is stored.

The network is behind one function, `fetch`, so the tests need none: the default reaches public addresses only (the
framework's PublicOnlyAdapter) for the APIs and the search page, and a local or home address only for SearXNG.
"""

import html as htmllib
import re
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

WAYS = ("searxng", "api", "html")
DEFAULT_URL = "http://127.0.0.1:8888"
DEFAULT_COUNT = 10
MAX_COUNT = 20
TIME_LIMIT = 15.0
MAX_BODY = 2 * 1024 * 1024
SNIPPET_MAX = 300
TITLE_MAX = 200
QUERY_MAX = 300
ORIGINS = ("vendor", "repository", "forum", "other")
REPOSITORY_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "sr.ht", "git.sr.ht")
FORUM_HOSTS = ("reddit.com", "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
               "askubuntu.com", "news.ycombinator.com", "discourse.org", "hackster.io", "electronics.stackexchange.com")
FORUM_PREFIXES = ("forum", "forums", "discuss", "discussion", "community", "bbs", "board", "talk", "ask")
TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|msclkid|mc_[a-z]+|ref|ref_src|igshid|yclid|_hsenc|_hsmi)$", re.I)
# What stays in a query after the privacy filter and must not leave the computer: a path of this computer or a secret.
PATH_IN_QUERY = re.compile(r"(?:\b[A-Za-z]:\\|\\\\[\w.-]+\\|(?<![\w.:])/(?:home|Users|root|etc|var|tmp|opt|mnt|usr)/)")
SECRET_IN_QUERY = re.compile(r"(?:\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}|"
                             r"\bxox[abpr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY|\beyJ[A-Za-z0-9_-]{15,}\.)")

# Messages, in plain words (the tool, the CLI and the window show them as they are).
OFF = "The web search is off: no way is turned on (setting web_search_ways)."
NO_KEY = "no key is saved for it (Secrets window: {name})"
JSON_OFF = ("this SearXNG instance does not allow the format json: its settings.yml needs "
            "'search: formats' with html and json (most public instances do not allow it)")


class SearchRefused(ValueError):
    """The whole search is refused before any request (a query that holds a path or a secret, no query, no way on)."""


@dataclass
class SearchHit:
    """One result: a public https address, its title and a short snippet, where it comes from and which ways found it."""
    address: str
    title: str
    snippet: str
    domain: str
    origin: str
    ways: List[str] = field(default_factory=list)


@dataclass
class WayReport:
    """What one way did: `ok`, `failed` (with the reason), `skipped` (not turned on or no key) or `refused` (offline
    mode, robots.txt)."""
    way: str
    status: str
    count: int = 0
    reason: str = ""


@dataclass
class SearchResult:
    hits: List[SearchHit]
    ways: List[WayReport]
    query: str          # what was actually sent: the query after the privacy filter


@dataclass
class Request:
    method: str
    url: str
    params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    local: bool = False      # a SearXNG address of this computer or the home network (never through a proxy)


@dataclass
class Response:
    status: int
    text: str

    def json(self) -> Any:
        import json
        try:
            return json.loads(self.text)
        except ValueError:
            return None


class WayError(RuntimeError):
    """A way that failed, with the reason in plain words."""


# --- providers --------------------------------------------------------------------------------------------------


@dataclass
class Provider:
    """A search API: how to ask it and how to read what it answers. The secret holding the person's key is
    `web_search_<name>_key`."""
    name: str
    label: str
    build: Callable[[str, int, str, Dict[str, Any]], Request]
    parse: Callable[[Any], List[Tuple[str, str, str]]]
    needs: Tuple[str, ...] = ()     # extra settings it needs (Bright Data's zone)

    @property
    def secret(self) -> str:
        return f"web_search_{self.name}_key"


def _rows(items: Any, url: str, title: str, text: str) -> List[Tuple[str, str, str]]:
    found = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get(url), str):
            found.append((item[url], str(item.get(title) or ""), str(item.get(text) or "")))
    return found


def _tavily(query, count, key, extra):
    return Request("POST", "https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                   body={"query": query, "max_results": count, "include_answer": False})


def _exa(query, count, key, extra):
    return Request("POST", "https://api.exa.ai/search", headers={"x-api-key": key},
                   body={"query": query, "numResults": count, "contents": {"text": {"maxCharacters": SNIPPET_MAX}}})


def _brave(query, count, key, extra):
    return Request("GET", "https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": count},
                   headers={"X-Subscription-Token": key, "Accept": "application/json"})


def _serper(query, count, key, extra):
    return Request("POST", "https://google.serper.dev/search", headers={"X-API-KEY": key},
                   body={"q": query, "num": count})


def _searchapi(query, count, key, extra):
    return Request("GET", "https://www.searchapi.io/api/v1/search",
                   params={"engine": "google", "q": query, "num": count}, headers={"Authorization": f"Bearer {key}"})


def _brightdata(query, count, key, extra):
    target = "https://www.google.com/search?" + urlencode({"q": query, "num": count, "brd_json": 1})
    return Request("POST", "https://api.brightdata.com/request", headers={"Authorization": f"Bearer {key}"},
                   body={"zone": str(extra.get("web_search_brightdata_zone") or ""), "url": target, "format": "raw"})


def _parse_brightdata(data: Any) -> List[Tuple[str, str, str]]:
    return _rows(data.get("organic") if isinstance(data, dict) else None, "link", "title", "description")


PROVIDERS: Dict[str, Provider] = {p.name: p for p in (
    Provider("tavily", "Tavily", _tavily,
             lambda d: _rows(d.get("results") if isinstance(d, dict) else None, "url", "title", "content")),
    Provider("exa", "Exa", _exa,
             lambda d: _rows(d.get("results") if isinstance(d, dict) else None, "url", "title", "text")),
    Provider("brightdata", "Bright Data SERP API", _brightdata, _parse_brightdata,
             needs=("web_search_brightdata_zone",)),
    Provider("serper", "Serper", _serper,
             lambda d: _rows(d.get("organic") if isinstance(d, dict) else None, "link", "title", "snippet")),
    Provider("searchapi", "SearchApi", _searchapi,
             lambda d: _rows(d.get("organic_results") if isinstance(d, dict) else None, "link", "title", "snippet")),
    Provider("brave", "Brave Search", _brave,
             lambda d: _rows(((d.get("web") or {}).get("results")) if isinstance(d, dict) else None, "url", "title",
                             "description")),
)}


# --- reading what comes back ------------------------------------------------------------------------------------


def parse_searxng(data: Any) -> List[Tuple[str, str, str]]:
    return _rows(data.get("results") if isinstance(data, dict) else None, "url", "title", "content")


def parse_duckduckgo(text: str) -> List[Tuple[str, str, str]]:
    """The results of DuckDuckGo's HTML page: each result's link (the address behind its redirect), title, snippet."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "html.parser")
    found = []
    for block in soup.select("div.result, div.web-result"):
        link = block.select_one("a.result__a")
        if link is None or not link.get("href"):
            continue
        href = str(link["href"])
        if "uddg=" in href:   # the page's own redirect: the address is in its query
            query = dict(parse_qsl(urlsplit(href if "//" in href else "https:" + href).query))
            href = unquote(query.get("uddg", ""))
        snippet = block.select_one(".result__snippet")
        found.append((href, link.get_text(" ", strip=True), snippet.get_text(" ", strip=True) if snippet else ""))
    return found


def normalize(address: str) -> str:
    """The key under which two ways' hits for the same page meet: no scheme difference, no "www.", no default port, no
    fragment, no tracking parameters, no trailing slash, host in lower case."""
    parts = urlsplit(address.strip())
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit(("", host + port, parts.path.rstrip("/") or "", query, "")).lstrip("/")


def clean_text(text: str, limit: int) -> str:
    """Text of a stranger as a plain line: no markup, no invisible characters, no comments, cut to `limit`."""
    from orchestrator.utils.untrusted import clean_untrusted
    plain = re.sub(r"<[^>]+>", " ", htmllib.unescape(str(text or "")))
    plain, _ = clean_untrusted(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain if len(plain) <= limit else plain[:limit - 1].rstrip() + "…"


def origin_of(address: str, table: Optional[Dict[str, Any]] = None) -> str:
    """vendor (a listed manufacturer's website), repository, forum or other."""
    from orchestrator.utils import archive_sources
    host = (urlsplit(address).hostname or "").lower()
    try:
        if archive_sources.vendor_of(address, table if table is not None else archive_sources.load_table()):
            return "vendor"
    except Exception:  # noqa: BLE001 - an unreadable table makes every hit "other", never an error
        pass
    on = lambda names: any(host == n or host.endswith("." + n) for n in names)  # noqa: E731
    if on(REPOSITORY_HOSTS):
        return "repository"
    if on(FORUM_HOSTS) or host.split(".")[0] in FORUM_PREFIXES or "/forum" in urlsplit(address).path.lower():
        return "forum"
    return "other"


def public_https(address: str) -> bool:
    """A public https address without a login in it (the same rule as a private page's label)."""
    from orchestrator.utils.archive_sources import host_of
    return address.lower().startswith("https://") and bool(host_of(address))


def merge(found: Sequence[Tuple[str, List[Tuple[str, str, str]]]], count: int,
          table: Optional[Dict[str, Any]] = None) -> List[SearchHit]:
    """The hits of every way as one list: one hit per page with all the ways that found it, the most found first, then
    by the best rank. Only public https addresses; title and snippet cleaned; at most `count` hits."""
    merged: Dict[str, Dict[str, Any]] = {}
    for way, rows in found:
        for rank, (address, title, snippet) in enumerate(rows):
            address = str(address).strip()
            if not public_https(address):
                continue
            entry = merged.setdefault(normalize(address), {"address": address, "title": "", "snippet": "", "ways": [],
                                                           "rank": rank})
            entry["rank"] = min(entry["rank"], rank)
            if way not in entry["ways"]:
                entry["ways"].append(way)
            title, snippet = clean_text(title, TITLE_MAX), clean_text(snippet, SNIPPET_MAX)
            entry["title"] = entry["title"] or title
            if len(snippet) > len(entry["snippet"]):
                entry["snippet"] = snippet
    ordered = sorted(merged.values(), key=lambda e: (-len(e["ways"]), e["rank"]))
    return [SearchHit(e["address"], e["title"] or urlsplit(e["address"]).hostname or "", e["snippet"],
                      (urlsplit(e["address"]).hostname or "").lower(), origin_of(e["address"], table), e["ways"])
            for e in ordered[:count]]


# --- the query --------------------------------------------------------------------------------------------------


def prepare_query(query: str, level: str = "STRICT") -> str:
    """The query as it may leave the computer: through the privacy filter at `level`. SearchRefused for an empty
    query, one that is too long, or one that still holds a path of this computer or a secret."""
    from orchestrator.llm.privacy import sanitize_for_cloud
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    if not text:
        raise SearchRefused("There is nothing to search for: the query is empty.")
    if len(text) > QUERY_MAX:
        raise SearchRefused(f"The query is longer than {QUERY_MAX} characters; shorten it to the question itself.")
    clean, _ = sanitize_for_cloud(text, level)
    # the original too: the filter swaps a path for a placeholder, and a search for a placeholder finds nothing
    if any(p.search(t) for p in (PATH_IN_QUERY, SECRET_IN_QUERY) for t in (text, clean)):
        raise SearchRefused("The query holds a path of this computer or a secret and is not sent anywhere; ask the "
                            "question without it.")
    return clean


# --- the network ------------------------------------------------------------------------------------------------


def default_fetch(request: Request, config: Any = None, timeout: float = TIME_LIMIT) -> Response:
    """Sends a request: public addresses only (PublicOnlyAdapter), except a SearXNG address of this computer or the home
    network (`request.local`: no proxy, a plain connection). The answer is read up to MAX_BODY bytes."""
    import requests
    from orchestrator.security.http import PublicOnlyAdapter
    session = requests.Session()
    if request.local:
        session.trust_env = False
    else:
        adapter = PublicOnlyAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    headers = {"User-Agent": "llmcf-web-search", **request.headers}
    try:
        answer = session.request(request.method, request.url, params=request.params or None, json=request.body,
                                 headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        raw = answer.raw.read(MAX_BODY + 1, decode_content=True)
        answer.close()
    except requests.RequestException as e:
        raise WayError(f"the request failed ({type(e).__name__})") from None
    finally:
        session.close()
    return Response(answer.status_code, raw[:MAX_BODY].decode(answer.encoding or "utf-8", errors="replace"))


def _http_problem(status: int, searxng: bool = False) -> str:
    if searxng and status in (403, 400):
        return JSON_OFF
    if status in (401, 403):
        return "the provider refused the key (check it in the Secrets window)"
    if status == 402:
        return "the provider's allowance is used up or the account needs payment"
    if status == 429:
        return "the provider's rate limit is reached; try again later"
    return f"the service answered with HTTP {status}"


# --- the search -------------------------------------------------------------------------------------------------


class WebSearch:
    """The search. `config`: the framework's settings (get(key, default)); `secret_of(name)`: the person's key or None;
    `fetch(request) -> Response`: the network; `reach(url, config)`: offline mode's check (check_reach)."""

    def __init__(self, config: Any, secret_of: Callable[[str], Optional[str]], fetch: Optional[Callable] = None,
                 reach: Optional[Callable] = None, table: Optional[Dict[str, Any]] = None,
                 time_limit: float = TIME_LIMIT):
        self.config, self.secret_of, self.table, self.time_limit = config, secret_of, table, time_limit
        self.fetch = fetch or (lambda request: default_fetch(request, config, time_limit))
        self._reach = reach

    # settings
    def _setting(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.config, "get", None)
        try:
            value = getter(key, default) if callable(getter) else default
        except Exception:  # noqa: BLE001 - an unreadable setting is its default
            value = default
        return default if value is None else value

    def ways(self) -> List[str]:
        """The ways the person turned on (setting web_search_ways), in the order of WAYS; none is off."""
        chosen = self._setting("web_search_ways", [])
        chosen = [w.strip() for w in chosen.split(",")] if isinstance(chosen, str) else list(chosen or [])
        return [w for w in WAYS if w in chosen]

    def providers(self) -> List[str]:
        """The API providers the person listed (setting web_search_apis), in PROVIDERS' order."""
        chosen = self._setting("web_search_apis", [])
        chosen = [w.strip() for w in chosen.split(",")] if isinstance(chosen, str) else list(chosen or [])
        return [name for name in PROVIDERS if name in chosen]

    def offline(self) -> bool:
        from orchestrator.utils.locality import offline_mode
        return offline_mode(self.config)

    def address(self) -> str:
        return str(self._setting("web_search_url", DEFAULT_URL) or DEFAULT_URL).strip().rstrip("/")

    # the search
    def search(self, query: str, count: int = DEFAULT_COUNT) -> SearchResult:
        """Searches with every way that is turned on. SearchRefused when there is no way or the query may not leave;
        a way that fails is a WayReport, never an error of the whole search."""
        if not self.ways():
            raise SearchRefused(OFF)
        count = max(1, min(int(count), MAX_COUNT))
        sent = prepare_query(query, str(self._setting("ai_security_level", "STRICT")))
        jobs: List[Tuple[str, str, Callable[[], List[Tuple[str, str, str]]]]] = []
        reports: List[WayReport] = []
        for way in self.ways():
            if way == "searxng":
                jobs.append(("searxng", "searxng", lambda q=sent: self._searxng(q, count)))
            elif way == "api":
                names = self.providers()
                if not names:
                    reports.append(WayReport("api", "skipped", reason="no provider is listed (setting "
                                                                      "web_search_apis)"))
                for name in names:
                    problem = self._provider_problem(PROVIDERS[name])
                    if problem:
                        reports.append(WayReport(f"api:{name}", "skipped" if "no key" in problem else "refused",
                                                 reason=problem))
                    else:
                        jobs.append((f"api:{name}", "api", lambda q=sent, n=name: self._api(n, q, count)))
            elif way == "html":
                jobs.append(("html", "html", lambda q=sent: self._html(q)))
        found, more = self._run(jobs)
        order = {way: n for n, (way, _group, _job) in enumerate(jobs)}
        found.sort(key=lambda item: order[item[0]])   # the same ways in the same order, whichever answered first
        reports += more
        hits = merge([(way, rows) for way, rows in found], count, self.table)
        reports.sort(key=lambda r: r.way)
        for report in reports:
            if report.status == "ok":
                report.count = sum(1 for h in hits if any(w == report.way or w.startswith(report.way) for w in h.ways))
        return SearchResult(hits, reports, sent)

    def _run(self, jobs) -> Tuple[List[Tuple[str, List[Tuple[str, str, str]]]], List[WayReport]]:
        found, reports = [], []
        if not jobs:
            return found, reports
        pool = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="web-search")
        futures = {pool.submit(job): (way, group) for way, group, job in jobs}
        try:
            done, pending = wait(list(futures), timeout=self.time_limit)
            for future in done:
                way, _ = futures[future]
                try:
                    rows = future.result()
                except (WayError, SearchRefused) as e:
                    reports.append(WayReport(way, "refused" if isinstance(e, SearchRefused) else "failed",
                                             reason=str(e)))
                except Exception as e:  # noqa: BLE001 - one way breaking never stops the others
                    reports.append(WayReport(way, "failed", reason=f"unexpected error ({type(e).__name__})"))
                else:
                    found.append((way, rows))
                    reports.append(WayReport(way, "ok"))
            for future in pending:
                reports.append(WayReport(futures[future][0], "failed",
                                         reason=f"no answer within {self.time_limit:g} seconds"))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return found, reports

    def _provider_problem(self, provider: Provider) -> str:
        if self.offline():
            return "offline mode reaches no search API"
        if not (self.secret_of(provider.secret) or "").strip():
            return NO_KEY.format(name=provider.secret)
        for key in provider.needs:
            if not str(self._setting(key, "") or "").strip():
                return f"the setting {key} is missing"
        return ""

    def _searxng(self, query: str, count: int) -> List[Tuple[str, str, str]]:
        base = self.address()
        if not re.match(r"^https?://[^\s/]+", base, re.I):
            raise WayError("the SearXNG address must start with http:// or https:// (setting web_search_url)")
        if self.offline():
            reach = self._reach
            if reach is None:
                from orchestrator.utils.locality import check_reach as reach
            locality = reach(base, self.config)
            if not locality.local:
                raise SearchRefused(locality.refusal("the web search"))
        local = self._is_local(base)
        response = self.fetch(Request("GET", base + "/search", params={"q": query, "format": "json"},
                                      headers={"Accept": "application/json"}, local=local))
        if response.status != 200:
            raise WayError(_http_problem(response.status, searxng=True))
        data = response.json()
        if data is None:
            raise WayError("the instance did not answer with json (its settings.yml needs 'search: formats' with json)")
        return parse_searxng(data)[:count]

    def _is_local(self, base: str) -> bool:
        from orchestrator.utils.locality import is_home_name, is_localhost_name
        host = urlsplit(base).hostname or ""
        if is_localhost_name(host) or is_home_name(host):
            return True
        import ipaddress
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return address.is_private or address.is_loopback or address.is_link_local

    def _api(self, name: str, query: str, count: int) -> List[Tuple[str, str, str]]:
        provider = PROVIDERS[name]
        key = (self.secret_of(provider.secret) or "").strip()
        extra = {k: self._setting(k, "") for k in provider.needs}
        response = self.fetch(provider.build(query, count, key, extra))
        if response.status != 200:
            raise WayError(f"{provider.label}: {_http_problem(response.status)}")
        data = response.json()
        if data is None:
            raise WayError(f"{provider.label}: the answer was not json")
        return provider.parse(data)[:count]

    def _html(self, query: str) -> List[Tuple[str, str, str]]:
        if self.offline():
            raise SearchRefused("offline mode reaches no search page")
        if not self._robots_allow("https://html.duckduckgo.com/html/"):
            raise SearchRefused("the search page's robots.txt does not allow it; nothing was asked")
        response = self.fetch(Request("GET", "https://html.duckduckgo.com/html/", params={"q": query}))
        if response.status != 200:
            raise WayError(f"the search page: {_http_problem(response.status)}")
        return parse_duckduckgo(response.text)

    def _robots_allow(self, url: str) -> bool:
        """The search page's robots.txt (read once per search): True when it allows a generic client to fetch `url`.
        An unreadable robots.txt is no "no" (the page answered nothing about it)."""
        from urllib.robotparser import RobotFileParser
        parts = urlsplit(url)
        try:
            response = self.fetch(Request("GET", f"{parts.scheme}://{parts.netloc}/robots.txt"))
        except WayError:
            return True
        if response.status in (401, 403):
            return False
        if response.status != 200:
            return True
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch("llmcf-web-search", url)


__all__ = ["DEFAULT_URL", "JSON_OFF", "OFF", "ORIGINS", "PROVIDERS", "Provider", "Request", "Response", "SearchHit",
           "SearchRefused", "SearchResult", "WAYS", "WayError", "WayReport", "WebSearch", "clean_text", "default_fetch",
           "merge", "normalize", "origin_of", "parse_duckduckgo", "parse_searxng", "prepare_query", "public_https"]
