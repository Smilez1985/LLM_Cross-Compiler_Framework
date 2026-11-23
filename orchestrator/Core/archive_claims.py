#!/usr/bin/env python3
"""
Ditto's archive, phase 2, part 2: a claim Ditto proposes about a page, checked by fixed rules (DESIGN, Ditto, the row
"Ditto's research for the archive starts from the hardware probe"; maintainer's decision of 2026-11-23, round 117).

Ditto proposes; these rules decide. Everything is checked offline against the text the knowledge base stores for the
page (Deep Ingest put it there, with its notice and opt-out checks); nothing is fetched again.

- The page is a public https page of the knowledge base: kind "web" (never a private page, a pack or the framework's own
  documents).
- The quote (QUOTE_MIN to QUOTE_MAX characters) is on the page, compared as the archive compares (white space collapsed,
  case ignored except inside code).
- The value is in the quote: a number with its unit, a version as written, a text as words of the quote. A flag cannot
  be read off a quote and is refused from a page.
- The ground is the framework's: "vendor" for a listed manufacturer's page, else "sources"; the proposal never names a
  ground, a status or a confirmation.
- The fingerprint of the stored passage around the quote goes with the evidence, so a mirror on another domain counts as
  one source.

The result is a `Candidate` with `origin="web"`, which `DittoArchive.record` checks again (key, sentence, value, private
data) and never lets replace an entry.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from orchestrator.Core.ditto_archive import Candidate
from orchestrator.schemas.archive import VALUE_TYPES, comparable_text, normalize_value
from orchestrator.utils import archive_sources

QUOTE_MIN = 20
QUOTE_MAX = 400
PASSAGE_CONTEXT = 200      # characters on each side of the quote that make the passage's fingerprint
_NUMBER_TOKEN = re.compile(r"(?<![\w.])[-+]?\d{1,30}(?:\.\d{1,30})?(?:\s*[A-Za-z%][A-Za-z0-9%/]{0,15})?")
_VERSION_TOKEN = re.compile(r"(?<![\w.])[vV]?\d{1,9}(?:\.\d{1,9}){1,5}(?![\w.]\d)")


@dataclass
class ClaimOffer:
    """What Ditto proposes about one page."""
    key: str
    value_type: str
    value: str
    sentence: str
    address: str
    quote: str


def value_in_quote(value_type: str, value: str, quote: str) -> bool:
    """Whether the normalized `value` of `value_type` is written in `quote`."""
    if value_type == "number":
        return any(normalize_value("number", t.strip()) == value for t in _NUMBER_TOKEN.findall(quote))
    if value_type == "version":
        return any(normalize_value("version", t) == value for t in _VERSION_TOKEN.findall(quote))
    if value_type == "text":
        return bool(value) and value in comparable_text(quote)
    return False


def passage_fingerprint(page: str, quote: str) -> str:
    """The sha256 of the page's passage around the quote (both as compared), so two pages that carry the same passage
    give the same fingerprint; "" when the quote is not on the page."""
    text, needle = comparable_text(page), comparable_text(quote)
    at = text.find(needle)
    if at < 0 or not needle:
        return ""
    passage = text[max(0, at - PASSAGE_CONTEXT):at + len(needle) + PASSAGE_CONTEXT]
    return hashlib.sha256(passage.encode("utf-8")).hexdigest()


def page_text(rag: Any, address: str) -> Tuple[str, str]:
    """(kind, text) of the knowledge base's source at `address`: its kind (web, pack, docs, private, other) and its
    stored chunks joined in their order; ("", "") when the knowledge base has no such source."""
    entry = next((s for s in rag.list_sources() if s.get("source") == address), None)
    if entry is None:
        return "", ""
    chunks, total = rag.read_source(address, 0, 1)
    if total > 1:
        chunks, _ = rag.read_source(address, 0, total)
    return str(entry.get("kind") or ""), "\n".join(str(c.get("content") or "") for c in chunks)


def check_claim(rag: Any, table: Dict[str, Any], offer: ClaimOffer) -> Tuple[Optional[Candidate], str]:
    """(the candidate for the archive, "") or (None, why the claim is refused)."""
    address = str(offer.address or "").strip()
    if not archive_sources.host_of(address):
        return None, "the page must be a public https address"
    if offer.value_type not in VALUE_TYPES:
        return None, "the value's type is not text, number, version or flag"
    if offer.value_type == "flag":
        return None, "a flag cannot be read off a quote; name the property as a text, number or version"
    value = normalize_value(offer.value_type, offer.value)
    if not value:
        return None, f"the value is not a {offer.value_type}"
    quote = " ".join(str(offer.quote or "").split())
    if not QUOTE_MIN <= len(quote) <= QUOTE_MAX:
        return None, f"the quote must be {QUOTE_MIN} to {QUOTE_MAX} characters"
    kind, text = page_text(rag, address)
    if not kind:
        return None, "the page is not in the knowledge base; take it in with Deep Ingest first"
    if kind != "web":
        return None, "only a public web page of the knowledge base can ground a claim (not a private page, a pack or " \
                     "the framework's documents)"
    fingerprint = passage_fingerprint(text, quote)
    if not fingerprint:
        return None, "the quote is not on the stored page"
    if not value_in_quote(offer.value_type, value, quote):
        return None, "the value is not in the quote"
    ground = "vendor" if archive_sources.vendor_of(address, table) else "sources"
    return Candidate(offer.key, offer.sentence, ground, [address], offer.value_type, offer.value, "web",
                     {address: fingerprint}), ""


__all__ = ["ClaimOffer", "PASSAGE_CONTEXT", "QUOTE_MAX", "QUOTE_MIN", "check_claim", "page_text", "passage_fingerprint",
           "value_in_quote"]
