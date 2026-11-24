#!/usr/bin/env python3
"""
Private pages: the person's own saved copy of a page that could not be crawled (DESIGN, Knowledge base and Deep Ingest;
maintainer's decisions of 2026-11-14).

A page that robots.txt, another opt-out, a login, a paywall or an error keeps out is given to the person as a link and
is never fetched or worked around. The person may open it in a browser, save it as a PDF or as HTML, and hand the file
over by its path (`llm-cli knowledge add-private`) or by dragging it into the Knowledge Sources window. This module
reads that file and stores it as a private page (rag/private.py): marked private wherever it is listed, kept on this
computer, never put into a pack, an export or the knowledge wiki, never learned by Ditto's archive. The person's own AI
model, local or cloud, may read its excerpts like those of any source.

The file is read with the readers the crawler uses (a PDF's text with pypdf, a whole HTML page as the crawler reads
one, with the text a reader does not see left out) and is not copied: the knowledge base keeps its text, the person
keeps the file. Its label is the address the person names (the page's own address, where it came from) or else the
file's name, never the folder it lies in.
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict

from orchestrator.rag.private import private_label

MIB = 1024 * 1024
MAX_BYTES = {"html": 8 * MIB, "pdf": 64 * MIB}    # the crawler's limits for a page and a PDF
HTML_SUFFIXES = (".html", ".htm", ".xhtml")
SUFFIXES = HTML_SUFFIXES + (".pdf",)
TITLE_MAX = 500


class PrivatePageError(ValueError):
    """The file cannot be stored as a private page; the message says why in plain words."""


@dataclass
class SavedPage:
    """What a saved file holds: its kind (html or pdf), its text (raw HTML for a page, the extracted text of a PDF) and
    the title the file itself names ("" when it names none)."""
    kind: str
    text: str
    title: str
    name: str


@dataclass
class PrivateResult:
    """How a file was stored: the label it has in the knowledge base and its kind."""
    source: str
    kind: str
    title: str


def pdf_text(data: bytes) -> "tuple[str, str]":
    """(the text, the title) of a PDF, read the way the crawler reads one (pypdf, page by page). PrivatePageError for a
    PDF that cannot be read or has no text (a scan has none)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PrivatePageError("PDF support (the package pypdf) is not installed.") from None
    try:
        reader = PdfReader(BytesIO(data))
        text = "".join(extracted + "\n" for page in reader.pages if (extracted := page.extract_text()))
        info = getattr(reader.metadata, "title", None) if reader.metadata else None
    except Exception as e:  # noqa: BLE001 - pypdf raises many kinds; the person gets one plain sentence
        raise PrivatePageError(f"The PDF cannot be read ({type(e).__name__}).") from None
    if not text.strip():
        raise PrivatePageError("The PDF holds no text (a scanned page has none); save the page as HTML, or as a PDF "
                               "that keeps its text, instead.")
    return text, info.strip() if isinstance(info, str) else ""


def read_saved_page(path: Any) -> SavedPage:
    """Reads a file the person saved. PrivatePageError for a missing file, a folder, a link, a kind that is not a PDF or
    an HTML page, a file above the crawler's limits, or one without text. Links are never followed."""
    file = Path(str(path)).expanduser()
    try:
        if file.is_symlink():
            raise PrivatePageError("A link is not read; give the file itself.")
        if not file.is_file():
            raise PrivatePageError("There is no such file." if not file.exists() else "That is a folder, not a file.")
        suffix = file.suffix.lower()
        if suffix not in SUFFIXES:
            raise PrivatePageError("Only a PDF or an HTML page can be added (.pdf, .html, .htm).")
        kind = "pdf" if suffix == ".pdf" else "html"
        size = file.stat().st_size
        if size > MAX_BYTES[kind]:
            raise PrivatePageError(f"The file is larger than the {MAX_BYTES[kind] // MIB} MiB a saved "
                                   f"{'PDF' if kind == 'pdf' else 'page'} may have.")
        data = file.read_bytes()
    except OSError as e:
        raise PrivatePageError(f"The file cannot be read ({type(e).__name__}).") from None
    if kind == "pdf":
        text, title = pdf_text(data)
        return SavedPage("pdf", text, title, file.name)
    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        raise PrivatePageError("The file holds no text.")
    title = ""
    from orchestrator.rag.html_text import is_html_document, page_markdown
    if is_html_document(text):
        title = page_markdown(text)[0]   # the page's own title, read the way the crawler reads a page
    return SavedPage("html", text, title, file.name)


def label_for(address: str, name: str) -> str:
    """The label a private page has: the address the person gave (https only, no login in it), else the file's name."""
    from orchestrator.utils.archive_sources import host_of
    address = str(address or "").strip()
    if address:
        if not host_of(address):
            raise PrivatePageError("The address must be a public https address (https://...).")
        return private_label(address)
    return private_label(name)


def add_private_page(rag: Any, path: Any, address: str = "", title: str = "") -> PrivateResult:
    """Stores the saved file as a private page of the knowledge base `rag`; the same file or address again replaces its
    earlier version. PrivatePageError with the reason when it cannot be stored."""
    page = read_saved_page(path)
    label = label_for(address, page.name)
    metadata: Dict[str, Any] = {"content_type": page.kind}
    shown = (title or page.title or "").strip()[:TITLE_MAX]
    if shown:
        metadata["title"] = shown
    stored = rag.ingest_document(label, page.text, metadata, replace=True, outside=True, private=True)
    if not stored:
        raise PrivatePageError(str(getattr(rag, "last_error", "") or "The page holds no text to store."))
    return PrivateResult(source=label, kind=page.kind, title=shown)


def cannot_crawl_note(address: str, reason: str) -> str:
    """The sentence that gives a refused page to the person as a link and says what they can do: nothing is fetched or
    worked around."""
    return (f"{address} could not be crawled ({reason}). Open it yourself; to keep it in the knowledge base, save it as "
            "a PDF or as HTML and add the file: it stays private on this computer and is never shared.")


REFUSED_HOW = ("Pages that were left out are never fetched or worked around: open their addresses yourself. To keep one "
               "in the knowledge base, save it as a PDF or as HTML and add the file with knowledge add-private or in the "
               "Knowledge Sources window: it stays private on this computer and is never shared.")


def refused_hint(skipped: Any) -> str:
    """The sentence a crawl's result adds when pages were left out (skipped for a reason such as robots.txt, an
    opt-out, a login or an error): the addresses are in the list next to it, this says what the person can do. Empty
    when nothing was left out."""
    return REFUSED_HOW if skipped else ""


__all__ = ["PrivatePageError", "PrivateResult", "REFUSED_HOW", "SavedPage", "add_private_page", "cannot_crawl_note",
           "label_for", "pdf_text", "read_saved_page", "refused_hint"]
