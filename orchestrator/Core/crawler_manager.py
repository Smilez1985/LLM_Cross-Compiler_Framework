#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Crawler Manager
DIREKTIVE: Goldstandard, Enterprise Security, 'Polite' Crawling.

Zweck:
Verantwortlich für den "Deep Ingest" von Dokumentation.
Lädt Webseiten rekursiv, parst PDFs und bereitet Inhalte für die Vektor-DB auf.
Beachtet strikt robots.txt und Rate-Limits.
Integriert zentrale Sicherheitsvalidierung (SSRF-Schutz).
"""

import time
from dataclasses import dataclass, field
import logging
import requests
import re
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from pathlib import Path
from typing import List, Set, Optional, Dict, Any
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

from orchestrator.utils.logging import get_logger
# NEU: Import der zentralen Validierungslogik
# Bitte Pfad anpassen, falls validation.py woanders liegt (z.B. orchestrator.Core.validation)
from requests.adapters import HTTPAdapter
from requests.utils import requote_uri
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from orchestrator.rag.html_text import html_to_markdown
from orchestrator.utils.validation import validate_public_url, resolve_public_addresses

DEFAULT_LANGUAGES = ("en", "de")


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
    Enterprise-Grade Web Crawler.
    Features:
    - Recursive Crawling (BFS)
    - Robots.txt Compliance
    - Rate Limiting
    - Language Filtering
    - Native PDF Support
    - Centralized Security Validation (SSRF Protection)
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
        self.robot_parsers: Dict[str, RobotFileParser] = {}
        self.session = requests.Session()
        adapter = PublicOnlyAdapter()
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": "LLM-Conversion-Framework-Bot/1.6.0 (Open Source; +https://github.com/Smilez1985/llm_conversion_framework)"
        })

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
        Startet den Crawl-Vorgang ab der root_url.
        Gibt eine Liste von Documents zurück.
        """
        self.logger.info(f"Starting Deep Ingest for: {root_url}")
        self.visited.clear()
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
            
            # 1. Strenge Sicherheitsvalidierung (SSRF / Format)
            if not validate_public_url(url):
                self.logger.warning(f"Security Alert: Skipping unsafe or invalid URL: {url}")
                self.visited.add(url)
                continue

            if not self.download_pdfs and urlparse(url).path.lower().endswith('.pdf'):
                self.visited.add(url)
                continue

            # 2. Politeness Checks (Robots.txt)
            if not self._can_fetch(url):
                self.logger.warning(f"Skipping {url} (Disallowed by robots.txt)")
                self.visited.add(url) # Mark as visited to avoid re-check
                continue
                
            # 3. Fetch Content
            self.logger.info(f"Crawling ({pages_crawled}/{self.max_pages}): {url}")
            self.visited.add(url)
            requests_made += 1
            try:
                time.sleep(self.rate_limit) # Politeness delay
                response = self._guarded_get(url)

                # Redirect targets are queued instead of followed, so they pass the SSRF check too.
                if response.is_redirect:
                    location = urljoin(url, response.headers.get('Location', ''))
                    if hops >= self.MAX_REDIRECTS:
                        self.logger.warning(f"Too many redirects starting from {url}, giving up.")
                    elif location and location not in self.visited:
                        if depth == 0:
                            allowed_hosts.add(urlparse(location).netloc)  # e.g. example.com -> www.example.com
                        queue.insert(0, (location, depth, hops + 1))
                    continue

                if response.status_code != 200:
                    self.logger.warning(f"Failed to fetch {url}: Status {response.status_code}")
                    continue
                    
                content_type = response.headers.get('Content-Type', '').lower()
                pages_crawled += 1
                
                # 4. Process Content
                doc = None
                new_links = []
                
                if 'application/pdf' in content_type or url.endswith('.pdf'):
                    if self.download_pdfs:
                        doc = self._process_pdf(response.content, url)
                elif 'text/html' in content_type:
                    doc, new_links = self._process_html(response.text, url, allowed_hosts)
                
                if doc:
                    documents.append(doc)
                
                # 5. Enqueue new links if depth allows
                if depth < self.max_depth:
                    for link in new_links:
                        if link not in self.visited:
                            queue.append((link, depth + 1, 0))
                            
            except Exception as e:
                self.logger.error(f"Error crawling {url}: {e}")
                
        self.logger.info(f"Crawl finished. Collected {len(documents)} documents.")
        return documents

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
        url = robots_url
        try:
            for _ in range(self.MAX_REDIRECTS + 1):
                response = self._guarded_get(url)
                if not response.is_redirect:
                    break
                url = urljoin(url, response.headers.get('Location', ''))
            else:
                raise RuntimeError("too many redirects")

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

    def _process_html(self, html_content: str, url: str, allowed_hosts: Set[str]) -> tuple[Optional[Document], List[str]]:
        """Extracts the page as Markdown (headings, code blocks, tables kept) and its links."""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Cleanup (Remove Nav, Footer, Scripts)
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'template', 'svg']):
            tag.decompose()

        # Link Extraction
        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            # Normalize
            full_url = urljoin(url, href)
            # Filter: Only internal links, no fragments
            parsed = urlparse(full_url)
            if parsed.netloc in allowed_hosts and '#' not in href:
                links.append(full_url)
        
        title, text = html_to_markdown(soup)

        # Language filter (configurable, crawler_languages); detection failures keep the page.
        lang = self._detect_language(text)
        if lang and self.languages and lang not in self.languages:
            self.logger.debug(f"Skipping page in language '{lang}' (allowed: {sorted(self.languages)}): {url}")
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
