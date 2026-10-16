#!/usr/bin/env python3
"""
Licences and copyright notices of crawled pages, and what a knowledge pack may carry of them (DECISIONS round 8,
Q-1 and Q-1').

Text and data mining lets the knowledge base keep the text of a page the crawl was allowed to read (the crawler checks
the site's opt-outs). Passing the text on is another matter: a knowledge pack, or the knowledge snapshot in a target,
carries a page's full text only when the page's licence allows passing it on. Every other page goes out as its address
with a few short facts (title, section names, technical terms), and the recipient crawls it under their own checks.
Licences that require attribution keep it in the pack.

What a page can show, and what is recognised:
* a licence link: rel="license" on a <link> or <a>, or in the HTTP Link header;
* licence metadata: <meta name="license">, "dc.rights", "dcterms.license", "dc.license", schema.org "license" in
  JSON-LD (and in a PDF's metadata, a Creative Commons or licence address);
* a link to a Creative Commons licence anywhere on the page;
* a copyright notice: "©", "(c)" with a year, "Copyright" with a year, or "All rights reserved".
For code from GitHub only the repository's licence file decides, and only when the crawl read it; otherwise the
code's licence is unknown (code without a licence is all rights reserved). The site footer of GitHub ("© GitHub") is
about the website, not the code, and is not read there.

Everything a page says is site text: the notice and an unrecognised licence name are cleaned and capped, stored as
metadata of the page and shown to the person as plain text. An AI gets only the fixed vocabulary of this module
(licence ids, states, how a page is shared).
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import Comment
from bs4.element import Declaration, Doctype, ProcessingInstruction

# The licences the framework recognises: id -> (the text may be passed on in a pack, attribution is required).
# Copyleft licences (GPL, LGPL, AGPL, GFDL) need the whole licence text and the source with every copy, which a pack
# of excerpts cannot promise; NC and ND licences forbid commercial use or changes (excerpts are changes).
_CC_VERSIONS = ("1.0", "2.0", "2.5", "3.0", "4.0")
LICENCES: Dict[str, Tuple[bool, bool]] = {
    "CC0-1.0": (True, False), "public-domain": (True, False),
    "MIT": (True, True), "Apache-2.0": (True, True), "BSD-2-Clause": (True, True), "BSD-3-Clause": (True, True),
    "ISC": (True, True), "0BSD": (True, False), "Unlicense": (True, False), "MPL-2.0": (True, True),
    "GPL-2.0": (False, True), "GPL-3.0": (False, True), "LGPL-2.1": (False, True), "LGPL-3.0": (False, True),
    "AGPL-3.0": (False, True), "GFDL-1.3": (False, True),
}
for _version in _CC_VERSIONS:
    LICENCES[f"CC-BY-{_version}"] = (True, True)
    LICENCES[f"CC-BY-SA-{_version}"] = (True, True)
    for _kind in ("NC", "ND", "NC-SA", "NC-ND"):
        LICENCES[f"CC-BY-{_kind}-{_version}"] = (False, True)
OTHER = "other"   # the page names a licence the framework does not recognise

# How a page is shared in a pack, and its licence state (both a fixed vocabulary, also for AIs).
FULL_TEXT, LINK_AND_FACTS = "full_text", "link_and_facts"
STATES = ("open", "restricted", "copyright", "code_without_licence", "unknown")

# Metadata keys a crawled page carries (strings, and the flag licence_file).
META_KEYS = ("licence", "licence_url", "licence_found", "licence_text", "copyright", "code_repo", "licence_file")

MAX_NOTICE = 200
MAX_FACT_TERMS = 25
MAX_FACT_SECTIONS = 12
CHANGES = "split into excerpts, personal data removed"

_SPDX_SITES = re.compile(r"^(?:www\.)?(?:opensource\.org|spdx\.org|choosealicense\.com)$")
_CC_PATH = re.compile(r"^/(licenses|publicdomain)/([a-z-]+)/(\d\.\d)", re.I)
_GNU_PATH = re.compile(r"^/licenses/(gpl|lgpl|agpl|fdl)(?:[-.]?(\d(?:\.\d)?))?", re.I)
_GITHUB_RESERVED = frozenset({"about", "features", "pricing", "topics", "orgs", "marketplace", "sponsors", "settings",
                              "login", "join", "explore", "collections", "enterprise", "security", "site", "trending",
                              "customer-stories", "readme", "notifications", "search", "apps", "contact"})
_LICENCE_FILE = re.compile(r"^(?:licen[cs]e|copying)(?:[.-][\w.-]+)?$", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

# Copyright notices: "©" with a year or a name, "(c)" with a year, "Copyright" with a year or "©", and "all rights
# reserved".
_NOTICE = re.compile(
    r"©\s*(?:(?:19|20)\d{2}|[^\W\d_])"
    r"|\(c\)\s*(?:19|20)\d{2}"
    r"|\bcopyright\b\s*(?:©|\(c\))?\s*(?:(?:19|20)\d{2}|©|\(c\))"
    r"|\ball rights reserved\b|\balle rechte vorbehalten\b", re.I)

# Licence names as pages write them (meta content, JSON-LD, a licence file's text), most specific first.
_NAMES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bcc0\b|creative commons zero|publicdomain/zero", re.I), "CC0-1.0"),
    (re.compile(r"\bunlicense\b|free and unencumbered software released into the public domain", re.I), "Unlicense"),
    (re.compile(r"\bpublic domain\b", re.I), "public-domain"),
    (re.compile(r"\bgnu affero general public license\b|\bagpl", re.I), "AGPL-3.0"),
    (re.compile(r"\bgnu lesser general public license\b[\s\S]{0,80}?\bversion 2\.1|\blgpl[- ]?(?:v)?2\.1", re.I),
     "LGPL-2.1"),
    (re.compile(r"\bgnu lesser general public license\b|\blgpl", re.I), "LGPL-3.0"),
    (re.compile(r"\bgnu free documentation license\b|\bgfdl", re.I), "GFDL-1.3"),
    (re.compile(r"\bgnu general public license\b[\s\S]{0,80}?\bversion 2\b|\bgpl[- ]?(?:v)?2(?:\.0)?\b", re.I),
     "GPL-2.0"),
    (re.compile(r"\bgnu general public license\b|\bgpl[- ]?(?:v)?3", re.I), "GPL-3.0"),
    (re.compile(r"\bmozilla public license\b|\bmpl[- ]?2\.0", re.I), "MPL-2.0"),
    (re.compile(r"\bapache license\b|\bapache[- ]2\.0\b", re.I), "Apache-2.0"),
    (re.compile(r"\bbsd[- ]3[- ]clause\b|neither the name of", re.I), "BSD-3-Clause"),
    (re.compile(r"\bbsd[- ]2[- ]clause\b|redistribution and use in source and binary forms", re.I), "BSD-2-Clause"),
    (re.compile(r"\b0bsd\b|zero[- ]clause bsd", re.I), "0BSD"),
    (re.compile(r"\bisc license\b|permission to use, copy, modify, and/or distribute this software for any purpose",
                re.I), "ISC"),
    (re.compile(r"\bmit license\b|^\s*mit\s*$|permission is hereby granted, free of charge, to any person", re.I),
     "MIT"),
]
_CC_NAME = re.compile(r"\b(?:cc|creative commons)[- ]?(by(?:[- ](?:nc|nd|sa)){0,2}|attribution(?:[- ]"
                      r"(?:noncommercial|noderivatives|noderivs|sharealike)){0,2})\b[^\d]{0,40}?(\d\.\d)?", re.I)
_CC_WORDS = {"noncommercial": "NC", "noderivatives": "ND", "noderivs": "ND", "sharealike": "SA", "nc": "NC",
             "nd": "ND", "sa": "SA"}


def clean(value: Any, limit: int = MAX_NOTICE) -> str:
    """Site text for metadata: control characters dropped, whitespace collapsed, capped."""
    text = unicodedata.normalize("NFC", _CONTROL.sub(" ", str(value or "")))
    return " ".join(text.split())[:limit]


def _cc_id(kind: str, version: Optional[str]) -> Optional[str]:
    parts = [p for p in re.split(r"[- ]+", kind.lower()) if p]
    if not parts or parts[0] not in ("by", "attribution"):
        return None
    terms = []
    for word in parts[1:]:
        term = _CC_WORDS.get(word)
        if term is None:
            return None
        terms.append(term)
    ordered = [t for t in ("NC", "ND", "SA") if t in terms]
    if "ND" in ordered and "SA" in ordered:
        return None
    licence = "-".join(["CC-BY"] + ordered + [version or "4.0"])
    return licence if licence in LICENCES else None


def licence_from_url(url: str) -> Optional[str]:
    """The licence a licence address names (Creative Commons, SPDX, OSI, choosealicense, Apache, GNU, Mozilla,
    unlicense.org), or None."""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host, path = (parsed.hostname or "").lower(), parsed.path or ""
    if host.endswith("creativecommons.org"):
        m = _CC_PATH.match(path)
        if not m:
            return None
        group, kind, version = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if group == "publicdomain":
            return {"zero": "CC0-1.0", "mark": "public-domain"}.get(kind)
        return _cc_id(kind, version)
    if _SPDX_SITES.match(host):
        name = path.rstrip("/").rsplit("/", 1)[-1]
        name = re.sub(r"\.(html|json|txt|php)$", "", name, flags=re.I)
        name = re.sub(r"-(only|or-later)$", "", name, flags=re.I)
        return next((key for key in LICENCES if key.lower() == name.lower()), None)
    if host.endswith("apache.org") and re.match(r"^/licenses/LICENSE-2\.0", path, re.I):
        return "Apache-2.0"
    if host.endswith("gnu.org"):
        m = _GNU_PATH.match(path)
        if m:
            kind, version = m.group(1).lower(), m.group(2)
            if kind == "fdl":
                return "GFDL-1.3"
            if kind == "agpl":
                return "AGPL-3.0"
            if kind == "lgpl":
                return "LGPL-2.1" if version == "2.1" else "LGPL-3.0"
            return "GPL-2.0" if (version or "").startswith("2") else "GPL-3.0"
    if host.endswith("mozilla.org") and re.match(r"^/(?:en-US/)?MPL/2\.0", path, re.I):
        return "MPL-2.0"
    if host.endswith("unlicense.org"):
        return "Unlicense"
    return None


def licence_from_text(text: str) -> Optional[str]:
    """The licence a licence name or a licence file's text names, or None."""
    text = str(text or "")[:20000]
    if not text.strip():
        return None
    url = re.search(r"https?://\S+", text)
    if url:
        found = licence_from_url(url.group(0).rstrip(").,;'\""))
        if found:
            return found
    spdx = next((key for key in LICENCES if key.lower() == text.strip().lower()), None)
    if spdx:
        return spdx
    m = _CC_NAME.search(text)
    if m:
        found = _cc_id(m.group(1), m.group(2))
        if found:
            return found
    return next((licence for pattern, licence in _NAMES if pattern.search(text)), None)


def code_repo(url: str) -> str:
    """'github.com/<owner>/<repo>' for a page of a GitHub repository (also raw.githubusercontent.com), else ''."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    parts = [p for p in (parsed.path or "").split("/") if p]
    if host in ("github.com", "www.github.com") and len(parts) >= 2 and parts[0].lower() not in _GITHUB_RESERVED:
        return f"github.com/{parts[0]}/{parts[1].removesuffix('.git')}".lower()
    if host == "raw.githubusercontent.com" and len(parts) >= 2:
        return f"github.com/{parts[0]}/{parts[1]}".lower()
    return ""


def is_licence_file(url: str) -> bool:
    """A repository's licence file at its top folder: github.com/<o>/<r>/blob/<ref>/LICENSE (or LICENCE, COPYING,
    with an extension), or the same file on raw.githubusercontent.com."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    parts = [p for p in (parsed.path or "").split("/") if p]
    if host in ("github.com", "www.github.com"):
        return len(parts) == 5 and parts[2] in ("blob", "raw") and bool(_LICENCE_FILE.match(parts[4]))
    if host == "raw.githubusercontent.com":
        return len(parts) == 4 and bool(_LICENCE_FILE.match(parts[3]))
    return False


def copyright_notice(text: str) -> str:
    """The first copyright notice in the text (the line it is on, cleaned and capped), or ''."""
    for line in str(text or "").splitlines():
        if _NOTICE.search(line):
            return clean(line)
    return ""


def _link_header_licence(headers: Optional[Mapping[str, Any]], base: str) -> Optional[str]:
    if not headers:
        return None
    try:
        value = headers.get("Link") or headers.get("link") or ""
    except Exception:  # noqa: BLE001 - odd header objects carry no licence
        return None
    for part in str(value).split(","):
        m = re.match(r"\s*<([^>]*)>\s*;(.*)", part)
        if m and re.search(r"\brel\s*=\s*\"?[^\";]*\blicense\b", m.group(2), re.I):
            return urljoin(base, m.group(1).strip())
    return None


def _json_ld_licences(soup) -> List[str]:
    found: List[str] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6 or len(found) >= 5:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() == "license":
                    if isinstance(value, str):
                        found.append(value)
                    elif isinstance(value, dict):
                        found.append(str(value.get("url") or value.get("@id") or value.get("name") or ""))
                else:
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node[:50]:
                walk(item, depth + 1)

    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)})[:10]:
        try:
            walk(json.loads(script.string or script.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return [f for f in found if f]


def _stated(text: str, url: str = "") -> Tuple[str, str]:
    """(licence id or OTHER, the stated text when it is not recognised)."""
    licence = licence_from_url(url) if url else None
    licence = licence or licence_from_text(text)
    return (licence, "") if licence else (OTHER, clean(text or url))


def from_html(soup, url: str, headers: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The licence metadata of an HTML page, from the whole page before its furniture (footer, nav) is removed:
    a dict with the keys of META_KEYS that were found."""
    meta: Dict[str, Any] = {}
    repo = code_repo(url)
    if repo:  # code: only the repository's licence file decides
        meta["code_repo"] = repo
        if is_licence_file(url):
            licence = licence_from_text(soup.get_text("\n"))
            meta["licence_file"] = True
            if licence:
                meta.update(licence=licence, licence_found="licence file of the repository")
        return meta

    candidates: List[Tuple[str, str, str]] = []   # (licence, where, url)
    header = _link_header_licence(headers, url)
    if header:
        licence, stated = _stated("", header)
        candidates.append((licence, "Link header", header if licence != OTHER else stated))
    for tag in soup.find_all(["link", "a"], attrs={"rel": True, "href": True}):
        rel = tag.get("rel")
        rels = rel if isinstance(rel, list) else str(rel).split()
        if any(str(r).lower() == "license" for r in rels):
            href = urljoin(url, str(tag["href"]))
            licence, stated = _stated(tag.get_text(" ") if tag.name == "a" else "", href)
            candidates.append((licence, "rel=license link", href if licence != OTHER else stated))
    for tag in soup.find_all("meta", attrs={"name": True, "content": True}):
        name = str(tag["name"]).strip().lower()
        if name in ("license", "licence", "dc.rights", "dcterms.license", "dc.license", "dcterms.rights"):
            licence, stated = _stated(str(tag["content"]))
            candidates.append((licence, "licence metadata", stated if licence == OTHER else ""))
    for value in _json_ld_licences(soup):
        licence, stated = _stated(value, value if value.startswith("http") else "")
        candidates.append((licence, "licence metadata", stated if licence == OTHER else
                           (value if value.startswith("http") else "")))
    if not candidates:
        for tag in soup.find_all("a", href=True):
            href = urljoin(url, str(tag["href"]))
            if "creativecommons.org" in href.lower():
                licence = licence_from_url(href)
                if licence:
                    candidates.append((licence, "Creative Commons link", href))
    if candidates:
        # One licence decides: the first source found; among several different ones the cautious choice (a licence
        # that does not allow passing the text on, or an unrecognised one) wins.
        closed = [c for c in candidates if c[0] == OTHER or not LICENCES.get(c[0], (False, False))[0]]
        licence, where, extra = (closed or candidates)[0]
        meta.update(licence=licence, licence_found=where)
        if licence == OTHER:
            meta["licence_text"] = clean(extra)
        elif extra.startswith("http"):
            meta["licence_url"] = clean(extra, 500)
    notice = copyright_notice(_page_text(soup))
    if notice:
        meta["copyright"] = notice
    return meta


_SKIPPED = frozenset({"script", "style", "noscript", "template", "head", "title"})
_NOT_TEXT = (Comment, Declaration, Doctype, ProcessingInstruction)
_BLOCKS = frozenset({"html", "body", "p", "div", "footer", "header", "nav", "main", "section", "article", "aside",
                     "address", "li", "ul", "ol", "dl", "dt", "dd", "table", "tr", "td", "th", "blockquote", "pre",
                     "figure", "figcaption", "form", "center", "h1", "h2", "h3", "h4", "h5", "h6"})


def _page_text(soup) -> str:
    """The visible text of the whole page (footer included), one line per block (a link inside a sentence stays in
    its line), without scripts, styles and comments."""
    lines: List[str] = []
    parts: List[str] = []
    current = None
    for node in soup.find_all(string=True):
        parent = node.parent
        if parent is None or parent.name in _SKIPPED or isinstance(node, _NOT_TEXT):
            continue
        block = parent
        while block is not None and block.name not in _BLOCKS:
            block = block.parent
        if block is not current and parts:
            lines.append("".join(parts))
            parts = []
        current = block
        parts.append(str(node))
    if parts:
        lines.append("".join(parts))
    return "\n".join(lines)


def from_pdf(raw: bytes, text: str, url: str, headers: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The licence metadata of a PDF: a licence link in the HTTP Link header, a licence address in its metadata
    (XMP: cc:license, xmpRights:WebStatement) and a copyright notice in its text."""
    meta: Dict[str, Any] = {}
    repo = code_repo(url)
    if repo:
        meta["code_repo"] = repo
        return meta
    header = _link_header_licence(headers, url)
    licence = licence_from_url(header) if header else None
    if licence:
        meta.update(licence=licence, licence_found="Link header", licence_url=clean(header, 500))
    else:
        for m in re.finditer(rb"https?://[A-Za-z0-9./_-]{8,200}", raw or b""):
            address = m.group(0).decode("ascii", "replace")
            licence = licence_from_url(address)
            if licence:
                meta.update(licence=licence, licence_found="PDF metadata", licence_url=clean(address, 500))
                break
    notice = copyright_notice(text)
    if notice:
        meta["copyright"] = notice
    return meta


def canonical_url(licence: str) -> str:
    """Where a recognised licence can be read."""
    m = re.match(r"^CC-BY((?:-(?:NC|ND|SA))*)-(\d\.\d)$", licence)
    if m:
        return f"https://creativecommons.org/licenses/by{m.group(1).lower()}/{m.group(2)}/"
    if licence == "CC0-1.0":
        return "https://creativecommons.org/publicdomain/zero/1.0/"
    if licence == "public-domain":
        return "https://creativecommons.org/publicdomain/mark/1.0/"
    return f"https://spdx.org/licenses/{licence}.html" if licence in LICENCES else ""


@dataclass(frozen=True)
class SharePlan:
    """How one page goes into a knowledge pack, and why (fixed vocabulary plus one sentence for the person)."""
    shared_as: str          # FULL_TEXT or LINK_AND_FACTS
    state: str              # one of STATES
    licence: str            # a LICENCES id, OTHER or ""
    reason: str
    attribution_required: bool = False


def share_plan(meta: Mapping[str, Any], repo_licence: Optional[str] = None) -> SharePlan:
    """How a page with this metadata is shared. repo_licence: the licence of its GitHub repository, when the same
    crawl read the repository's licence file."""
    licence = str(meta.get("licence") or "")
    if not licence and meta.get("code_repo") and repo_licence:
        licence = repo_licence
    if licence in LICENCES:
        shareable, attribution = LICENCES[licence]
        if shareable:
            return SharePlan(FULL_TEXT, "open", licence, f"the licence {licence} allows passing the text on"
                             + (", with attribution" if attribution else ""), attribution)
        why = ("needs the whole licence text and the source with every copy, which a pack of excerpts does not "
               "promise" if licence.startswith(("GPL", "LGPL", "AGPL", "GFDL")) else
               "does not allow passing changed or commercial copies on")
        return SharePlan(LINK_AND_FACTS, "restricted", licence, f"the licence {licence} {why}", attribution)
    if licence == OTHER:
        return SharePlan(LINK_AND_FACTS, "restricted", OTHER,
                         "the page names a licence the framework does not recognise")
    if meta.get("code_repo"):
        return SharePlan(LINK_AND_FACTS, "code_without_licence", "",
                         "code whose repository licence file was not part of the crawl; without a licence it is all "
                         "rights reserved")
    if meta.get("copyright"):
        return SharePlan(LINK_AND_FACTS, "copyright", "", "a copyright notice and no licence that allows passing the "
                                                          "text on")
    return SharePlan(LINK_AND_FACTS, "unknown", "", "no licence found, so the terms are unknown")


def repo_licences(metas: Iterable[Mapping[str, Any]]) -> Dict[Tuple[str, str], str]:
    """{(crawl start address, repository): licence} from the licence files the crawls read."""
    found: Dict[Tuple[str, str], str] = {}
    for meta in metas:
        if meta.get("licence_file") and meta.get("code_repo") and str(meta.get("licence") or "") in LICENCES:
            found.setdefault((str(meta.get("root_url") or ""), str(meta["code_repo"])), str(meta["licence"]))
    return found


def plan_for(meta: Mapping[str, Any], repos: Mapping[Tuple[str, str], str]) -> SharePlan:
    return share_plan(meta, repos.get((str(meta.get("root_url") or ""), str(meta.get("code_repo") or ""))))


def attribution(source: str, title: str, meta: Mapping[str, Any], plan: SharePlan) -> str:
    """What a pack keeps with a page whose licence requires attribution: title, address, the copyright notice, the
    licence with its address, and the changes made."""
    licence_url = str(meta.get("licence_url") or "") or canonical_url(plan.licence)
    parts = [p for p in (clean(title), clean(source, 1000), clean(meta.get("copyright") or "")) if p]
    parts.append(f"licence {plan.licence}" + (f" ({licence_url})" if licence_url else ""))
    parts.append(f"changes: {CHANGES}")
    return " - ".join(parts)


_TERM_STRIP = "\"'`()[]{}<>,;:!?*"


def _terms(texts: Iterable[str]) -> List[str]:
    """Technical terms in the text: options and flags, identifiers with digits, underscores or dots, upper-case
    names; never addresses or e-mail addresses."""
    terms: List[str] = []
    seen = set()
    for text in texts:
        for raw in str(text or "").split():
            token = raw.strip(_TERM_STRIP).rstrip(".")
            if not 3 <= len(token) <= 64 or "://" in token or "@" in token or token.isdigit():
                continue
            if not (token.startswith("-") and any(c.isalpha() for c in token)
                    or any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
                    or "_" in token.strip("_")
                    or re.fullmatch(r"[A-Z][A-Z0-9]{2,}", token)
                    or re.fullmatch(r"v?\d+(?:\.\d+){1,3}", token)
                    or re.fullmatch(r"[\w-]+\.[\w.-]*\w", token) and not token[0].isdigit()):
                continue
            key = token.lower()
            if key not in seen:
                seen.add(key)
                terms.append(token)
                if len(terms) >= MAX_FACT_TERMS:
                    return terms
    return terms


def short_facts(source: str, title: str, sections: Iterable[str], texts: Iterable[str], plan: SharePlan) -> str:
    """The short facts a pack carries instead of a page's text: its address, title, section names and technical
    terms, and why only these are shared."""
    names: List[str] = []
    for section in sections:
        name = clean(section, 120)
        if name and name not in names:
            names.append(name)
        if len(names) >= MAX_FACT_SECTIONS:
            break
    lines = [f"Link: {clean(source, 1000)}"]
    if clean(title):
        lines.append(f"Title: {clean(title)}")
    if names:
        lines.append("Sections: " + "; ".join(names))
    terms = _terms(texts)
    if terms:
        lines.append("Terms: " + ", ".join(terms))
    lines.append(f"Only the link and these short facts are shared ({plan.reason}). Read the page at its address for "
                 "the full text.")
    return "\n".join(lines)
