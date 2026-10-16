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
  JSON-LD (and in a PDF's XMP metadata, cc:license or xmpRights:WebStatement; a link in a PDF's text is not its
  licence);
* a link to a Creative Commons licence in the page's footer, or in a sentence that says the page or its content is
  licensed under it; an image credit is not the page's licence, and a bare link never outweighs an explicit "All
  rights reserved";
* a copyright notice: "©", "(c)" with a year, "Copyright" with a year, or "All rights reserved".
A licence is named only by its address, its SPDX id, its own title (with its version) or its standard grant text;
a licence mentioned in passing (a licence text that names other licences, "not in the public domain", a list of third-
party components) names nothing, and another version of a licence is never taken for the one that allows passing the
text on.
For code from GitHub only the repository's licence file decides, and only when the crawl read it; otherwise the
code's licence is unknown (code without a licence is all rights reserved). It covers the repository's code (its start
page, files and folders), not its issues, pull requests, discussions, wiki or releases: those are other people's
words under other terms. The site footer of GitHub ("© GitHub") is about the website, not the code, and is not read
there.

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
    "GPL-1.0": (False, True), "GPL-2.0": (False, True), "GPL-3.0": (False, True), "LGPL-2.0": (False, True),
    "LGPL-2.1": (False, True), "LGPL-3.0": (False, True), "AGPL-3.0": (False, True), "GFDL-1.1": (False, True),
    "GFDL-1.2": (False, True), "GFDL-1.3": (False, True),
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

# Metadata keys a crawled page carries (strings, and the flags licence_file and repo_content: a page of a GitHub
# repository that is not its code).
META_KEYS = ("licence", "licence_url", "licence_found", "licence_text", "copyright", "code_repo", "licence_file",
             "repo_content")

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

# A licence is named by its name (a short statement: meta content, JSON-LD, the text of a licence link), by its own
# title with its version, or by its standard grant text, never by a mention in passing. A pattern without a licence
# (None) is a known family in a version the framework does not take for the one it recognises: it ends the search.
SHORT_NAME = 300   # a statement up to this length is a name; a longer text is a licence text
_LICENCE = r"licen[cs]e"
_NAMES: List[Tuple[re.Pattern, Optional[str]]] = [(re.compile(p, re.I), licence) for p, licence in [
    (r"\bcc0\b|creative commons zero|publicdomain/zero", "CC0-1.0"),
    (r"\bthe unlicen[cs]e\b|^unlicen[cs]e$|free and unencumbered software released into the public domain",
     "Unlicense"),
    (r"\bnot\b[^.;]{0,40}\bpublic domain\b", None),
    (r"^public domain\.?$|\bpublic domain mark\b|\b(?:dedicated|released|placed) (?:in)?to the public domain\b"
     r"|\bis in the public domain\b", "public-domain"),
    (rf"\baffero general public {_LICENCE}\b|\bagpl", "AGPL-3.0"),
    (rf"\blesser general public {_LICENCE}\b[^;]{{0,40}}?\b2\.1\b|\blgpl[- ]?v?2\.1\b", "LGPL-2.1"),
    (rf"\blibrary general public {_LICENCE}\b|\blgpl[- ]?v?2(?:\.0)?\b", "LGPL-2.0"),
    (rf"\blesser general public {_LICENCE}\b[^;]{{0,40}}?\b3(?:\.0)?\b|\blgpl[- ]?v?3", "LGPL-3.0"),
    (rf"\blesser general public {_LICENCE}\b|\blgpl\b", None),
    (rf"\bfree documentation {_LICENCE}\b[^;]{{0,40}}?\b1\.1\b|\bgfdl[- ]?v?1\.1\b", "GFDL-1.1"),
    (rf"\bfree documentation {_LICENCE}\b[^;]{{0,40}}?\b1\.2\b|\bgfdl[- ]?v?1\.2\b", "GFDL-1.2"),
    (rf"\bfree documentation {_LICENCE}\b[^;]{{0,40}}?\b1\.3\b|\bgfdl[- ]?v?1\.3\b", "GFDL-1.3"),
    (rf"\bfree documentation {_LICENCE}\b|\bgfdl\b", None),
    (rf"\bgeneral public {_LICENCE}\b[^;]{{0,40}}?\b(?:version |v)?3(?:\.0)?\b|\bgpl[- ]?v?3(?:\.0)?\b", "GPL-3.0"),
    (rf"\bgeneral public {_LICENCE}\b[^;]{{0,40}}?\b(?:version |v)?2(?:\.0)?\b|\bgpl[- ]?v?2(?:\.0)?\b", "GPL-2.0"),
    (rf"\bgeneral public {_LICENCE}\b[^;]{{0,40}}?\b(?:version |v)?1(?:\.0)?\b|\bgpl[- ]?v?1(?:\.0)?\b", "GPL-1.0"),
    (rf"\bgeneral public {_LICENCE}\b|\bgpl\b", None),
    (rf"\bmozilla public {_LICENCE}\b[^;]{{0,40}}?\b2\.0\b|\bmpl[- ]?v?2\.0\b", "MPL-2.0"),
    (rf"\bmozilla public {_LICENCE}\b|\bmpl\b", None),
    (rf"\bapache {_LICENCE}\b[^;]{{0,40}}?\b2\.0\b|\bapache[- ]2\.0\b", "Apache-2.0"),
    (rf"\bapache {_LICENCE}\b", None),
    (rf"\bartistic {_LICENCE}\b", None),
    (r"\b0bsd\b|zero[- ]clause bsd", "0BSD"),
    (r"\bbsd[- ]3[- ]clause\b|\b3[- ]clause bsd\b|\b(?:new|revised|modified) bsd\b", "BSD-3-Clause"),
    (rf"\bbsd[- ]2[- ]clause\b|\b2[- ]clause bsd\b|\bsimplified bsd\b|\bfreebsd {_LICENCE}\b", "BSD-2-Clause"),
    (rf"\bisc {_LICENCE}\b|^isc$", "ISC"),
    (rf"\bmit {_LICENCE}\b|^mit$|\(mit\)|\bexpat {_LICENCE}\b", "MIT"),
]]
# A licence text is named by its own title with its version, at the start of a line (as licence texts begin), or by
# its standard grant text; the first one found decides, because a licence text names other licences later (the GPL
# names the LGPL, the MPL its secondary licences) and a title in the middle of a sentence is only a mention.
def _text_patterns(patterns: List[Tuple[str, Optional[str]]], title: bool) -> List[Tuple[re.Pattern, Optional[str]]]:
    return [(re.compile((r"^[ \t]*" if title else r"\b") + p.replace(" ", r"\s+"), re.I | re.M), licence)
            for p, licence in patterns]


_TITLES = _text_patterns([
    (rf"gnu affero general public {_LICENCE},? version 3\b", "AGPL-3.0"),
    (rf"gnu lesser general public {_LICENCE},? version 3\b", "LGPL-3.0"),
    (rf"gnu lesser general public {_LICENCE},? version 2\.1\b", "LGPL-2.1"),
    (rf"gnu library general public {_LICENCE},? version 2\b", "LGPL-2.0"),
    (rf"gnu general public {_LICENCE},? version 3\b", "GPL-3.0"),
    (rf"gnu general public {_LICENCE},? version 2\b", "GPL-2.0"),
    (rf"gnu general public {_LICENCE},? version 1\b", "GPL-1.0"),
    (rf"gnu free documentation {_LICENCE},? version 1\.3\b", "GFDL-1.3"),
    (rf"gnu free documentation {_LICENCE},? version 1\.2\b", "GFDL-1.2"),
    (rf"gnu free documentation {_LICENCE},? version 1\.1\b", "GFDL-1.1"),
    (rf"mozilla public {_LICENCE},? version 2\.0\b", "MPL-2.0"),
    (rf"mozilla public {_LICENCE},? version 1\.[01]\b", None),
    (rf"apache {_LICENCE},? version 2\.0\b", "Apache-2.0"),
    (rf"apache {_LICENCE},? version 1\.[01]\b", None),
    (rf"(?:the )?\"?artistic {_LICENCE}\b", None),
    (rf"(?:the )?mit {_LICENCE}(?: \(mit\))?[ \t]*$", "MIT"),
    (r"cc0 1\.0 universal\b", "CC0-1.0"),
    (r"creative commons (?:legal code )?(attribution(?:-(?:noncommercial|noderivatives|noderivs|sharealike)){0,2}) "
     r"(\d\.\d) (?:international|unported|generic)\b", "CC"),   # the licence from the groups; see _grant
], title=True)
_GRANTS = _text_patterns([
    (r"this is free and unencumbered software released into the public domain\b", "Unlicense"),
    (rf"licensed under the apache {_LICENCE}, version 2\.0 \(the \"{_LICENCE}\"\)", "Apache-2.0"),
    (r"permission is hereby granted, free of charge, to any person obtaining a copy\b", "MIT"),
    (r"permission to use, copy, modify, and(?:/or)? distribute this software for any purpose with or without fee "
     r"is hereby granted\b", "ISC"),   # 0BSD when nothing follows; see _grant
    (r"redistribution and use in source and binary forms, with or without modification, are permitted provided "
     r"that\b", "BSD-2-Clause"),       # BSD-3-Clause with "neither the name"; see _grant
], title=False)
_ALL_RIGHTS = re.compile(r"\ball\s+rights\s+reserved\b|\balle\s+rechte\s+vorbehalten\b", re.I)
_SPDX_TAG = re.compile(r"\bSPDX-License-Identifier:\s*([A-Za-z0-9.+-]+)", re.I)
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


def _spdx_id(name: str) -> Optional[str]:
    """A recognised licence by its SPDX id ("GPL-2.0-or-later" and "GPL-2.0+" count as GPL-2.0)."""
    name = re.sub(r"(?:-only|-or-later|\+)$", "", str(name or "").strip(), flags=re.I).lower()
    return next((key for key in LICENCES if key.lower() == name), None)


def _grant(licence: str, match: re.Match, text: str) -> Optional[str]:
    """The licence a title or a standard grant text belongs to, from its groups and from what is around it. A title or
    grant well after an explicit "All rights reserved" (not the copyright line right above it) is a bundled
    component's licence inside another text: it names nothing."""
    before, after = text[:match.start()], text[match.start():]
    if _ALL_RIGHTS.search(before[:-300]):
        return None
    if licence == "CC":
        return _cc_id(match.group(1), match.group(2))
    if licence == "BSD-2-Clause":
        if re.search(r"\badvertising\s+materials\b", after, re.I):
            return None   # the old four-clause licence
        return "BSD-3-Clause" if re.search(r"\bneither\s+the\s+name\s+of\b", after, re.I) else "BSD-2-Clause"
    if licence == "ISC":
        return "ISC" if re.search(r"is\s+hereby\s+granted,?\s+provided\s+that\s+the\s+above\s+copyright\s+notice",
                                  after, re.I) else "0BSD"
    return licence


def licence_from_text(text: str) -> Optional[str]:
    """The licence a licence statement or a licence text names, or None (none, or one the framework does not
    recognise). A short statement (up to SHORT_NAME characters: meta content, the text of a licence link) is read as
    a name: its address, its SPDX id or the licence's name with its version. A longer text is a licence text: its
    SPDX tag, its own title with its version or its standard grant text decides, whichever comes first. A licence
    named in passing (another licence's text names it, "not in the public domain", a list of third-party components)
    names nothing."""
    flat = " ".join(str(text or "")[:20000].split())
    if not flat:
        return None
    tag = _SPDX_TAG.search(flat)
    if tag:
        return _spdx_id(tag.group(1))
    if len(flat) <= SHORT_NAME:
        url = re.search(r"https?://\S+", flat)
        if url:
            found = licence_from_url(url.group(0).rstrip(").,;'\""))
            if found:
                return found
        spdx = _spdx_id(flat)
        if spdx:
            return spdx
        m = _CC_NAME.search(flat)
        if m:
            found = _cc_id(m.group(1), m.group(2))
            if found:
                return found
        for pattern, licence in _NAMES:
            if pattern.search(flat):
                return licence
    lines = "\n".join(" ".join(line.split()) for line in str(text)[:20000].splitlines())
    found: List[Tuple[int, Optional[str]]] = []
    for pattern, licence in (*_TITLES, *_GRANTS):
        m = pattern.search(lines)
        if m:
            found.append((m.start(), _grant(licence, m, lines) if licence else None))
    return min(found, key=lambda item: item[0])[1] if found else None


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


def is_repo_code(url: str) -> bool:
    """A page of a GitHub repository's code, which its licence file covers: the repository's start page, a file or a
    folder (blob, tree, raw), or a file on raw.githubusercontent.com. Not its issues, pull requests, discussions,
    wiki, releases or commits."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    parts = [p for p in (parsed.path or "").split("/") if p]
    if host == "raw.githubusercontent.com":
        return len(parts) >= 3
    return bool(code_repo(url)) and (len(parts) == 2 or parts[2] in ("blob", "tree", "raw"))


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
        if not is_repo_code(url):   # an issue, a pull request, the wiki...: other people's words, not the code
            meta["repo_content"] = True
            return meta
        if is_licence_file(url):
            licence = licence_from_text(_file_text(soup))
            meta.update(licence_file=True, licence=licence or OTHER,
                        licence_found="licence file of the repository" + ("" if licence else " (not recognised)"))
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
    page_text = _page_text(soup)
    if not candidates and not _ALL_RIGHTS.search(page_text):
        # A bare Creative Commons link counts only where it licenses the page (the footer, or a sentence that says
        # the page is licensed under it), never as an image credit, and never against an explicit "All rights
        # reserved".
        for tag in soup.find_all("a", href=True):
            href = urljoin(url, str(tag["href"]))
            if "creativecommons.org" in href.lower() and _licenses_the_page(tag):
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
    notice = copyright_notice(page_text)
    if notice:
        meta["copyright"] = notice
    return meta


_IMAGE_CREDIT = re.compile(r"\b(?:photos?|images?|pictures?|icons?|logos?|figures?|illustrations?|fonts?|bild(?:er)?|"
                           r"fotos?|grafik(?:en)?|abbildung(?:en)?)\b", re.I)
_LICENSING = re.compile(r"\blicen[cs]ed\b|\blicen[cs]e\b|\blizenz|\blizenziert\b|\bavailable under\b|"
                        r"\breleased under\b", re.I)
_FOOTER = re.compile(r"(?:^|[-_ ])footer(?:$|[-_ ])", re.I)


def _licenses_the_page(tag) -> bool:
    """A Creative Commons link that licenses the page: in its footer, or in a sentence that says it is licensed under
    it; not an image credit (a figure, or words such as photo, image or icon around it)."""
    if tag.find_parent(["figure", "figcaption", "picture"]) is not None:
        return False
    block = tag
    while block is not None and block.name not in _BLOCKS:
        block = block.parent
    context = clean((block if block is not None else tag).get_text(" "), 600)
    if _IMAGE_CREDIT.search(context):
        return False
    in_footer = (tag.find_parent("footer") is not None or tag.find_parent(attrs={"role": "contentinfo"}) is not None
                 or tag.find_parent(attrs={"class": _FOOTER}) is not None
                 or tag.find_parent(attrs={"id": _FOOTER}) is not None)
    return in_footer or bool(_LICENSING.search(context))


_FILE_VIEWS = ("textarea#read-only-cursor-text-area", "[data-testid=read-only-cursor-text-area]",
               "article.markdown-body", "[itemprop=text]", "table.highlight", "pre")


def _file_text(soup) -> str:
    """The text of a file shown on a page (a repository's licence file): the file's own view when the page has one,
    otherwise the whole page."""
    for selector in _FILE_VIEWS:
        try:
            view = soup.select_one(selector)
        except Exception:  # noqa: BLE001 - a selector the parser does not support finds nothing
            view = None
        if view is not None and view.get_text(strip=True):
            return view.get_text("\n")
    return soup.get_text("\n")


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


_XMP_LICENCE = re.compile(rb"<(cc:license|xmpRights:WebStatement)\b(.{0,600}?)(?:/>|</\1\s*>)"
                          rb"|\b(?:cc:license|xmpRights:WebStatement)\s*=\s*[\"']([^\"']{8,300})[\"']", re.S | re.I)
_ADDRESS = re.compile(rb"https?://[A-Za-z0-9./_-]{8,200}")


def _xmp_licences(raw: bytes) -> List[str]:
    """The licence addresses a PDF's XMP metadata names (cc:license, xmpRights:WebStatement), never a link in its
    text: a vendor manual that links an open source licence in its third-party notices is not under it."""
    found: List[str] = []
    for m in _XMP_LICENCE.finditer(raw or b""):
        address = _ADDRESS.search(m.group(2) or m.group(3) or b"")
        if address:
            found.append(address.group(0).decode("ascii", "replace"))
        if len(found) >= 10:
            break
    return found


def from_pdf(raw: bytes, text: str, url: str, headers: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The licence metadata of a PDF: a licence link in the HTTP Link header, a licence address in its XMP metadata
    (cc:license, xmpRights:WebStatement; the cautious one among several) and a copyright notice in its text."""
    meta: Dict[str, Any] = {}
    repo = code_repo(url)
    if repo:
        meta["code_repo"] = repo
        if not is_repo_code(url):
            meta["repo_content"] = True
        return meta
    header = _link_header_licence(headers, url)
    licence = licence_from_url(header) if header else None
    if licence:
        meta.update(licence=licence, licence_found="Link header", licence_url=clean(header, 500))
    else:
        named = [(found, address) for address in _xmp_licences(raw)
                 for found in [licence_from_url(address)] if found]
        closed = [n for n in named if not LICENCES[n[0]][0]]
        if named:
            licence, address = (closed or named)[0]
            meta.update(licence=licence, licence_found="PDF metadata", licence_url=clean(address, 500))
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
    if meta.get("repo_content"):
        return SharePlan(LINK_AND_FACTS, "unknown", "", "a page of a GitHub repository that is not its code (an issue, "
                                                        "a pull request, a discussion, the wiki, a release): the "
                                                        "repository's licence does not cover it")
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
                         "the repository's licence file names a licence the framework does not recognise"
                         if meta.get("code_repo") else "the page names a licence the framework does not recognise")
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
        if meta.get("licence_file") and meta.get("code_repo") and (str(meta.get("licence") or "") in LICENCES
                                                                   or meta.get("licence") == OTHER):
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
