#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Crawler Manager
DIREKTIVE: Goldstandard, Enterprise Security, 'Polite' Crawling.

Zweck:
Verantwortlich für den "Deep Ingest" von Dokumentation.
Lädt Webseiten rekursiv, parst PDFs und bereitet Inhalte für die Vektor-DB auf.
Beachtet strikt robots.txt und Rate-Limits.
"""

import time
import logging
import requests
import re
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from pathlib import Path
from typing import List, Set, Optional, Dict, Any
from io import BytesIO

# Third-party imports (via pyproject.toml v1.6.0)
from bs4 import BeautifulSoup
from langchain_core.documents import Document
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
try:
    from langdetect import detect
except ImportError:
    detect = None

from orchestrator.utils.logging import get_logger

class CrawlerManager:
    """
    Enterprise-Grade Web Crawler.
    Features:
    - Recursive Crawling (BFS)
    - Robots.txt Compliance
    - Rate Limiting
    - Language Filtering
    - Native PDF Support
    """

    def __init__(self, framework_manager):
        self.logger = get_logger(__name__)
        self.config = framework_manager.config
        
        # Configuration Settings
        self.respect_robots = self.config.crawler_respect_robots
        self.max_depth = self.config.crawler_max_depth
        self.max_pages = self.config.crawler_max_pages
        self.rate_limit = 1.0 # Seconds between requests
        
        # State
        self.visited: Set[str] = set()
        self.robot_parsers: Dict[str, RobotFileParser] = {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "LLM-Conversion-Framework-Bot/1.6.0 (Open Source; +https://github.com/Smilez1985/llm_conversion_framework)"
        })

    def crawl(self, root_url: str) -> List[Document]:
        """
        Startet den Crawl-Vorgang ab der root_url.
        Gibt eine Liste von LangChain Documents zurück.
        """
        self.logger.info(f"Starting Deep Ingest for: {root_url}")
        self.visited.clear()
        documents: List[Document] = []
        
        # Queue für BFS: (url, current_depth)
        queue = [(root_url, 0)]
        base_domain = urlparse(root_url).netloc
        
        pages_crawled = 0
        
        while queue and pages_crawled < self.max_pages:
            url, depth = queue.pop(0)
            
            if url in self.visited:
                continue
            
            # 1. Security & Politeness Checks
            if not self._can_fetch(url):
                self.logger.warning(f"Skipping {url} (Disallowed by robots.txt)")
                self.visited.add(url) # Mark as visited to avoid re-check
                continue
                
            # 2. Fetch Content
            self.logger.info(f"Crawling ({pages_crawled}/{self.max_pages}): {url}")
            try:
                time.sleep(self.rate_limit) # Politeness delay
                response = self.session.get(url, timeout=10)
                
                if response.status_code != 200:
                    self.logger.warning(f"Failed to fetch {url}: Status {response.status_code}")
                    self.visited.add(url)
                    continue
                    
                content_type = response.headers.get('Content-Type', '').lower()
                self.visited.add(url)
                pages_crawled += 1
                
                # 3. Process Content
                doc = None
                new_links = []
                
                if 'application/pdf' in content_type or url.endswith('.pdf'):
                    doc = self._process_pdf(response.content, url)
                elif 'text/html' in content_type:
                    doc, new_links = self._process_html(response.text, url, base_domain)
                
                if doc:
                    documents.append(doc)
                
                # 4. Enqueue new links if depth allows
                if depth < self.max_depth:
                    for link in new_links:
                        if link not in self.visited:
                            queue.append((link, depth + 1))
                            
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
        robots_url = f"{parsed.scheme}://{domain}/robots.txt"
        
        if domain not in self.robot_parsers:
            rp = RobotFileParser()
            try:
                rp.set_url(robots_url)
                rp.read()
                self.robot_parsers[domain] = rp
            except Exception:
                # Bei Fehler (z.B. Timeout) nehmen wir an, es ist erlaubt, aber loggen es
                self.logger.warning(f"Could not read robots.txt for {domain}. Assuming allowed.")
                return True
        
        return self.robot_parsers[domain].can_fetch(self.session.headers["User-Agent"], url)

    def _process_html(self, html_content: str, url: str, base_domain: str) -> tuple[Optional[Document], List[str]]:
        """Extrahiert Text und Links aus HTML."""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Cleanup (Remove Nav, Footer, Scripts)
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
            
        text = soup.get_text(separator="\n", strip=True)
        
        # Language Detection Filter
        if detect:
            try:
                # Prüfe die ersten 500 Zeichen
                lang = detect(text[:500])
                if lang != 'en': # Wir fokussieren uns auf Englisch (Tech Standard)
                    self.logger.debug(f"Skipping non-english content ({lang}): {url}")
                    return None, []
            except Exception:
                pass # Fallback: Keep content if detection fails

        # Link Extraction
        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            # Normalize
            full_url = urljoin(url, href)
            # Filter: Only internal links, no fragments
            parsed = urlparse(full_url)
            if parsed.netloc == base_domain and '#' not in href:
                links.append(full_url)
        
        # Create Document
        metadata = {"source": url, "content_type": "html"}
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
            return Document(page_content=text, metadata=metadata)
            
        except Exception as e:
            self.logger.error(f"Failed to parse PDF {url}: {e}")
            return None
