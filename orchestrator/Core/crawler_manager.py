#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Crawler Manager

Deep Ingest of documentation: crawls web pages breadth-first, extracts text from HTML and PDFs and prepares
it for the knowledge base. Rate-limited, behind the SSRF guard (every request and every redirect hop).

Text and data mining of public pages is allowed unless the rightsholder reserved it in machine-readable form
(EU DSM Directive Art. 4, German UrhG 44b). Besides robots.txt the crawler therefore honours:
* X-Robots-Tag headers and <meta name="robots"> / <meta name="llm-conversion-framework-bot">:
  noindex, none, noai and noimageai keep the page out; nofollow and none stop following its links;
* the W3C TDM Reservation Protocol (TDMRep 1.0): /.well-known/tdmrep.json per origin, the tdm-reservation
  header, <meta name="tdm-reservation"> and tdm:reservation in PDF metadata.
A reservation from any of these sources wins (TDMRep lets a later source lift an earlier reservation; the
crawler stays on the cautious side). Every skipped page is logged and listed in `skipped` with the reason.
"""

import json
import time
from dataclasses import dataclass, field
import logging
import requests
import re
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from typing import Iterable, List, NamedTuple, Set, Optional, Dict, Any, Tuple
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

from requests.adapters import HTTPAdapter
from requests.utils import requote_uri
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from orchestrator import __version__
from orchestrator.rag.html_text import html_to_markdown
from orchestrator.tools.confirmation import visible
from orchestrator.utils.logging import get_logger
from orchestrator.utils.validation import validate_public_url, resolve_public_addresses

DEFAULT_LANGUAGES = ("en", "de")

BOT_NAME = "LLM-Conversion-Framework-Bot"
USER_AGENT = f"{BOT_NAME}/{__version__} (Open Source; +https://github.com/Smilez1985/llm_conversion_framework)"
# Robots directives that keep a page out of the knowledge base, and those that stop following its links.
SKIP_DIRECTIVES = frozenset({"noindex", "none", "noai", "noimageai"})
NOFOLLOW_DIRECTIVES = frozenset({"nofollow", "none"})
# Directives with a value of their own; any other "word:" prefix in X-Robots-Tag names a crawler.
_VALUED_DIRECTIVES = frozenset({"unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"})
TDMREP_PATH = "/.well-known/tdmrep.json"
MAX_TDM_RULES = 1000
_PDF_TDM_RESERVATION = re.compile(rb"tdm:reservation\s*(?:=\s*[\"']\s*1\s*[\"']|>\s*1\s*<)")


@dataclass(frozen=True)
class SkippedPage:
    """A page the crawl left out. opt_out: the site reserved it (robots.txt, robots directives, TDMRep)."""
    url: str
    reason: str
    opt_out: bool = False


class TdmRule(NamedTuple):
    location: str
    reserved: Optional[bool]   # None: an invalid value, which TDMRep treats as unset
    policy: Optional[str]


def robots_directives(values: Iterable[str], bot: str = BOT_NAME) -> Set[str]:
    """Lower-case directives of X-Robots-Tag or meta robots values that apply to this crawler: unscoped
    ones and those after "<bot>:" (a scope lasts until the next one within the same value)."""
    found: Set[str] = set()
    for value in values:
        scope = None
        for part in str(value).lower().split(","):
            part = part.strip()
            scoped = re.match(r"([a-z0-9_.-]+)\s*:\s*(.*)", part)
            if scoped and scoped.group(1) not in _VALUED_DIRECTIVES:
                scope, part = scoped.group(1), scoped.group(2)
            if scope in (None, bot.lower()) and part:
                found.add(part.split(":", 1)[0].strip())
    return found


def tdm_flag(value: Any) -> Optional[bool]:
    """TDMRep value: 1 reserved, 0 not reserved; anything else is a protocol error and counts as unset."""
    return None if value is None else {"1": True, "0": False}.get(str(value).strip())


def tdm_location_matches(location: str, path: str) -> bool:
    """TDMRep location pattern (robots.txt syntax, case-sensitive): matches from the start of the path,
    '*' is any run of characters, a trailing '$' anchors the end. Greedy segment search, no regex."""
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
    """A crawled page or PDF (replaces langchain_core's Document; only these two fields are used)."""
    page_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class _PinnedConnectionMixin:
    """Re-checks the address at connect time and connects to exactly that IP (defeats DNS rebinding).
    TLS still verifies against the original host name, which urllib3 reads after _new_conn()."""

    def _new_conn(self):
        vetted = resolve_public_addresses(self._dns_host, self.port)[0]
        original, self._dns_host = self._dns_host, vetted
        try:
            return super()._new_conn()
        finally:
            self._dns_host = original


class _PinnedHTTPConnection(_PinnedConnectionMixin, HTTPConnection):
    pass


class _PinnedHTTPSConnection(_PinnedConnectionMixin, HTTPSConnection):
    pass


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class PublicOnlyAdapter(HTTPAdapter):
    """Direct connections only reach public addresses. Requests through a configured proxy are
    resolved by the proxy and therefore only covered by the URL check."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _PinnedHTTPConnectionPool, "https": _PinnedHTTPSConnectionPool}

class CrawlerManager:
    """
    Breadth-first web crawler: robots.txt, robots directives and TDMRep reservations, rate limit, language
    filter, PDF support, SSRF guard on every request. Skipped pages end up in `skipped` with the reason.
    """

    MAX_REDIRECTS = 5

    def __init__(self, framework_manager, options: Optional[Dict[str, Any]] = None):
        self.logger = get_logger(__name__)
        self.config = framework_manager.config
        opts = options or {}
        
        # Per-crawl options (e.g. from the Deep Ingest dialog) override the stored defaults.
        self.respect_robots = bool(opts.get("respect_robots", self.config.get("crawler_respect_robots", True)))
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
        self.robot_parsers: Dict[str, RobotFileParser] = {}
        self.tdm_rules: Dict[str, List[TdmRule]] = {}   # per origin, from /.well-known/tdmrep.json
        self.session = requests.Session()
        adapter = PublicOnlyAdapter()
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _guarded_get(self, url: str, timeout: int = 10) -> requests.Response:
        """Fetches exactly the URL that passed the SSRF check, without following redirects."""
        prepared = self.session.prepare_request(requests.Request("GET", url))
        check = validate_public_url(prepared.url)
        if not check:
            raise PermissionError(check.message)
        # send() alone skips what get() does: env proxies and REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE.
        settings = self.session.merge_environment_settings(prepared.url, {}, None, None, None)
        return self.session.send(prepared, timeout=timeout, allow_redirects=False, **settings)

    def crawl(self, root_url: str) -> List[Document]:
        """
        Crawls breadth-first from root_url and returns the pages as Documents. Pages left out (opt-outs,
        unsafe or off-site redirects, other languages) are listed in self.skipped with the reason.
        """
        self.logger.info(f"Starting Deep Ingest for: {root_url}")
        self.visited.clear()
        self.skipped = []
        documents: List[Document] = []

        # Queue für BFS: (url, current_depth, redirect_hops)
        queue = [(root_url, 0, 0)]
        allowed_hosts = {urlparse(root_url).netloc}

        pages_crawled = 0
        requests_made = 0

        while queue and pages_crawled < self.max_pages:
            if requests_made >= self.max_requests:
                self.logger.warning(f"Request budget of {self.max_requests} exhausted, stopping crawl.")
                break

            url, depth, hops = queue.pop(0)
            # Percent-encode what links may contain raw (e.g. spaces in "User Guide.pdf").
            url = requote_uri(url)

            if url in self.visited:
                continue
            self.visited.add(url)

            # 1. SSRF / format check
            if not validate_public_url(url):
                self._skip(url, "unsafe or invalid address (security check)", level=logging.WARNING)
                continue

            if not self.download_pdfs and urlparse(url).path.lower().endswith('.pdf'):
                self._skip(url, "PDF downloads are switched off")
                continue

            # 2. Opt-outs known before the request: robots.txt, then the origin's TDMRep file.
            if not self._can_fetch(url):
                self._skip(url, "disallowed by robots.txt", opt_out=True)
                continue
            reserved = self._tdm_file_reservation(url)
            if reserved:
                self._skip(url, reserved, opt_out=True)
                continue

            # 3. Fetch Content
            self.logger.info(f"Crawling ({pages_crawled}/{self.max_pages}): {url}")
            requests_made += 1
            try:
                time.sleep(self.rate_limit) # Politeness delay
                response = self._guarded_get(url)

                # Redirect targets are queued instead of followed, so they pass the SSRF check and the
                # opt-out checks of their own host (robots.txt, tdmrep.json) too.
                if response.is_redirect:
                    location = urljoin(url, response.headers.get('Location', ''))
                    host = urlparse(location).netloc
                    if hops >= self.MAX_REDIRECTS:
                        self._skip(url, f"more than {self.MAX_REDIRECTS} redirects")
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
                    self.logger.warning(f"Failed to fetch {url}: Status {response.status_code}")
                    continue

                content_type = response.headers.get('Content-Type', '').lower()
                pages_crawled += 1

                # 4. Opt-outs in the response, then the content
                reasons, nofollow = self._header_opt_outs(response)
                doc = None
                new_links = []

                if 'application/pdf' in content_type or url.endswith('.pdf'):
                    if self.download_pdfs and not reasons:
                        if _PDF_TDM_RESERVATION.search(response.content or b""):
                            reasons.append("TDM reservation (tdm:reservation in the PDF metadata)")
                        else:
                            doc = self._process_pdf(response.content, url)
                elif 'text/html' in content_type and not (reasons and nofollow):
                    soup = BeautifulSoup(response.text, 'html.parser')
                    meta_reasons, meta_nofollow = self._meta_opt_outs(soup)
                    reasons += meta_reasons
                    nofollow = nofollow or meta_nofollow
                    if reasons:
                        self._strip_boilerplate(soup)
                        new_links = [] if nofollow else self._html_links(soup, url, allowed_hosts)
                    else:
                        doc, new_links = self._process_html(soup, url, allowed_hosts)

                if reasons:
                    self._skip(url, "; ".join(reasons), opt_out=True)
                elif doc:
                    documents.append(doc)
                if nofollow and new_links:
                    self.logger.info(f"Not following the links of {url} (nofollow).")
                    new_links = []

                # 5. Enqueue new links if depth allows
                if depth < self.max_depth:
                    for link in new_links:
                        if link not in self.visited:
                            queue.append((link, depth + 1, 0))

            except Exception as e:
                self.logger.error(f"Error crawling {url}: {e}")

        self.logger.info(f"Crawl finished. Collected {len(documents)} documents, skipped {len(self.skipped)} pages.")
        return documents

    def _skip(self, url: str, reason: str, opt_out: bool = False, level: int = logging.INFO) -> None:
        """Records a page the crawl leaves out; every skip is named in the log with its reason."""
        self.skipped.append(SkippedPage(url=url, reason=reason, opt_out=opt_out))
        self.logger.log(level, f"Skipped {_remote_text(url, 500)}: {reason}")

    def _header_opt_outs(self, response) -> Tuple[List[str], bool]:
        """Reservations in the response headers (X-Robots-Tag, TDMRep tdm-reservation) and whether the
        page's links must not be followed."""
        directives = robots_directives(_header_values(response, "X-Robots-Tag"))
        reasons = []
        hits = sorted(directives & SKIP_DIRECTIVES)
        if hits:
            reasons.append(f"X-Robots-Tag: {', '.join(hits)}")
        if tdm_flag(response.headers.get("tdm-reservation")):
            reasons.append(self._tdm_reason("tdm-reservation header", response.headers.get("tdm-policy")))
        return reasons, bool(directives & NOFOLLOW_DIRECTIVES)

    def _meta_opt_outs(self, soup: BeautifulSoup) -> Tuple[List[str], bool]:
        """Reservations in <meta name="robots">, <meta name="<our bot>"> and <meta name="tdm-reservation">."""
        reasons, nofollow, bot = [], False, BOT_NAME.lower()
        for meta in soup.find_all("meta", attrs={"name": True, "content": True}):
            name = str(meta["name"]).strip().lower()
            if name in ("robots", bot):
                directives = robots_directives([str(meta["content"])])
                hits = sorted(directives & SKIP_DIRECTIVES)
                if hits:
                    reasons.append(f'<meta name="{name}">: {", ".join(hits)}')
                nofollow = nofollow or bool(directives & NOFOLLOW_DIRECTIVES)
            elif name == "tdm-reservation" and tdm_flag(meta["content"]):
                reasons.append('TDM reservation (<meta name="tdm-reservation">)')
        return reasons, nofollow

    @staticmethod
    def _tdm_reason(source: str, policy: Optional[str]) -> str:
        return f"TDM reservation ({source}" + (f", policy {_remote_text(policy)})" if policy else ")")

    def _tdm_file_reservation(self, url: str) -> Optional[str]:
        """The reason when the origin's /.well-known/tdmrep.json reserves this URL (the first matching rule
        decides, TDMRep 1.0 section 3.2). The file is fetched once per origin."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.tdm_rules:
            self.tdm_rules[origin] = self._load_tdm_rules(origin + TDMREP_PATH)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
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
            response = self._get_following_redirects(file_url)
            if response.status_code != 200:
                return []
            data = json.loads(response.text)
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

    def _get_following_redirects(self, url: str) -> requests.Response:
        """GET that follows up to MAX_REDIRECTS redirects, each hop through the SSRF guard again."""
        for _ in range(self.MAX_REDIRECTS + 1):
            response = self._guarded_get(url)
            if not response.is_redirect:
                return response
            url = urljoin(url, response.headers.get('Location', ''))
        raise RuntimeError("too many redirects")

    def _can_fetch(self, url: str) -> bool:
        """Prüft robots.txt Regeln."""
        if not self.respect_robots:
            return True
            
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain not in self.robot_parsers:
            self.robot_parsers[domain] = self._load_robots(f"{parsed.scheme}://{domain}/robots.txt")
        
        return self.robot_parsers[domain].can_fetch(self.session.headers["User-Agent"], url)

    def _load_robots(self, robots_url: str) -> RobotFileParser:
        """Loads robots.txt through the SSRF guard (RobotFileParser.read() would follow redirects unchecked)."""
        rp = RobotFileParser(robots_url)
        try:
            response = self._get_following_redirects(robots_url)
            if response.status_code == 200:
                rp.parse(response.text.splitlines())
            elif response.status_code in (401, 403) or response.status_code >= 500:
                # RFC 9309 2.3.1.4: an unreachable robots.txt (server error) means complete disallow.
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except requests.RequestException as e:
            self.logger.warning(f"Could not reach {robots_url} ({e}). Treating the site as disallowed.")
            rp.disallow_all = True
        except Exception as e:
            # Blocked by the SSRF guard or too many redirects: RFC 9309 treats robots.txt as unavailable.
            self.logger.warning(f"Could not read {robots_url} ({e}). Assuming allowed.")
            rp.allow_all = True
        return rp

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
    def _strip_boilerplate(soup: BeautifulSoup) -> None:
        """Removes navigation, footers, scripts and similar (their links are not followed either)."""
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'template', 'svg']):
            tag.decompose()

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

    def _process_html(self, html_content: Any, url: str, allowed_hosts: Set[str]) -> tuple[Optional[Document], List[str]]:
        """Extracts the page (HTML text or parsed soup) as Markdown (headings, code blocks, tables kept) and
        its links."""
        soup = html_content if isinstance(html_content, BeautifulSoup) else BeautifulSoup(html_content, 'html.parser')
        self._strip_boilerplate(soup)
        links = self._html_links(soup, url, allowed_hosts)
        title, text = html_to_markdown(soup)

        # Language filter (configurable, crawler_languages); detection failures keep the page.
        lang = self._detect_language(text)
        if lang and self.languages and lang not in self.languages:
            self._skip(url, f"language '{lang}' is not one of {', '.join(sorted(self.languages))}")
            return None, []
        if not text.strip():
            return None, links
        metadata = {"source": url, "content_type": "html", "title": title}
        if lang:
            metadata["language"] = lang
        return Document(page_content=text, metadata=metadata), links

    def _process_pdf(self, pdf_bytes: bytes, url: str) -> Optional[Document]:
        """Extrahiert Text aus PDF-Streams."""
        if not PdfReader:
            self.logger.warning("pypdf not installed. Skipping PDF.")
            return None
            
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            
            if not text.strip():
                return None
                
            metadata = {"source": url, "content_type": "pdf"}
            info_title = getattr(reader.metadata, "title", None) if reader.metadata else None
            if isinstance(info_title, str) and info_title.strip():
                metadata["title"] = info_title.strip()
            return Document(page_content=text, metadata=metadata)
            
        except Exception as e:
            self.logger.error(f"Failed to parse PDF {url}: {e}")
            return None
