#!/usr/bin/env python3
"""
The licence files a pack ships with the pages it carries in full (DECISIONS, answers of 2026-10-16, W3).

A knowledge pack, and a target's knowledge snapshot, carry a page's full text only where its licence allows it
(licences.py). When a page carried in full is under a licence that asks for its text or permission notice (MIT, BSD,
ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA; licence_texts.py), the pack ships next to its knowledge file:

* LICENSES/<SPDX id>.txt: the canonical text of each such licence, unchanged from the SPDX License List;
* NOTICE: every page of the knowledge file with its source address, its copyright line as found and its SPDX id,
  grouped by licence. For MIT, BSD and ISC the copyright lines stand with the licence text they go with (code from
  GitHub without a notice of its own takes the line of its repository's licence file). For Apache-2.0 the notices of
  the work's NOTICE file follow, when the same crawl read it (a repository's NOTICE file at its top folder, or a page
  named NOTICE on the same site); otherwise NOTICE says that none was read. The pages shared only as their address
  and short facts are listed with why.

Everything here is computed from the knowledge file's entries as the pack carries them (scrubbed), so the export, the
module wizard and the checks of an install or a verify all read the same thing. NOTICE is plain text: the copyright
lines and the notices of a NOTICE file are site text, for people; an AI gets only the licence ids and counts.

check() is what an install, `zoo verify` and the community repository's check (verify_file) ask: every licence text
under LICENSES/ is the canonical one, byte for byte; every licence that asks for its text and covers a page carried in
full has its text there; NOTICE is there and lists each such page.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from orchestrator.rag import licence_texts, licences
from orchestrator.rag.licences import FULL_TEXT, LINK_AND_FACTS

SOURCE_PREFIX = "Source: "
_OVERLAP_WORDS = 200      # how far back a chunk's overlap with the one before it is looked for
_MIN_OVERLAP_WORDS = 3    # a shorter match is kept twice rather than risk dropping words of a notice


@dataclass(frozen=True)
class IncludedText:
    """One licence text a pack ships, and why."""
    licence: str
    path: str        # relative to the pack: LICENSES/MIT.txt, or knowledge/LICENSES/MIT.txt in a target pack
    pages: int
    reason: str

    def line(self) -> str:
        return f"{self.path}: {self.pages} page(s) under {self.licence}; {self.reason}"


@dataclass
class LicenceFiles:
    """The licence files for one knowledge file: the texts it needs, and every file's content by its path relative to
    the knowledge file's folder (LICENSES/<id>.txt, NOTICE). Empty when no page carried in full asks for a text."""
    texts: List[IncludedText] = field(default_factory=list)
    files: Dict[str, bytes] = field(default_factory=dict)
    without_text: Dict[str, int] = field(default_factory=dict)   # licence -> pages shared as link: no canonical text

    def notes(self) -> List[str]:
        """Notes in fixed words (licence ids and counts), also for an AI."""
        notes = []
        if self.texts:
            notes.append("licence texts included (LICENSES/, unchanged from the SPDX License List "
                         f"{licence_texts.list_version()}): " + ", ".join(f"{t.licence} for {t.pages} page(s)"
                                                                         for t in self.texts)
                         + "; NOTICE lists every page with its source address, copyright line and licence id")
        for licence, count in sorted(self.without_text.items()):
            notes.append(f"{count} page(s) under {licence} only as their address and short facts: the licence asks "
                         "for its text with every copy, and the framework has no canonical text of it")
        return notes


def _pages(entries: Iterable[Mapping[str, Any]]) -> Dict[str, List[Mapping[str, Any]]]:
    """The entries of a knowledge file by their source (a page is several chunks), in their order."""
    pages: Dict[str, List[Mapping[str, Any]]] = {}
    for entry in entries:
        if isinstance(entry, Mapping) and isinstance(entry.get("source"), str) and entry["source"].strip():
            pages.setdefault(entry["source"], []).append(entry)
    return pages


def _meta(chunks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """A page's metadata from its chunks: the first value of each field."""
    meta: Dict[str, Any] = {}
    for chunk in chunks:
        found = chunk.get("metadata")
        for key, value in (found.items() if isinstance(found, Mapping) else ()):
            if key not in meta and value not in (None, ""):
                meta[key] = value
    return meta


def _drop_overlap(before: str, text: str) -> str:
    """`text` without the words it repeats from the end of `before` (the knowledge base's chunks overlap)."""
    tail, head = before.split()[-_OVERLAP_WORDS:], text.split()[:_OVERLAP_WORDS]
    for size in range(min(len(tail), len(head)), _MIN_OVERLAP_WORDS - 1, -1):
        if tail[-size:] == head[:size]:
            return re.sub(r"^\s*(?:\S+\s+){%d}\S+" % (size - 1), "", text, count=1).lstrip()
    return text


def joined_text(chunks: Sequence[Mapping[str, Any]]) -> str:
    """A page's text from its chunks: in their order (chunk_index when given), the overlap of each chunk with the one
    before it dropped. What the crawl read, not the file byte for byte."""
    def position(item: Tuple[int, Mapping[str, Any]]) -> Tuple[int, int]:
        meta = item[1].get("metadata")
        index = meta.get("chunk_index") if isinstance(meta, Mapping) else None
        return (index if isinstance(index, int) and not isinstance(index, bool) else item[0], item[0])

    text = ""
    for _, chunk in sorted(enumerate(chunks), key=position):
        part = str(chunk.get("content") or "")
        if not part.strip():
            continue
        part = _drop_overlap(text, part) if text else part
        text = f"{text.rstrip()}\n{part}" if text else part
    return text.strip()


def _licence_words(licence: str) -> str:
    if licence == "public-domain":
        return "none (public domain)"
    if licence == licences.OTHER:
        return "none (a licence the framework does not recognise)"
    return licence or "none found"


def _entry(source: str, meta: Mapping[str, Any], why: str = "") -> List[str]:
    notice = licences.clean(meta.get("copyright") or "")
    found = licences.clean(meta.get(licences.COPYRIGHT_FOUND_KEY) or "")
    lines = [f"{SOURCE_PREFIX}{source}",
             "Copyright: " + ((f"{notice} (from {found})" if found else notice) if notice else "none found"),
             f"SPDX id: {_licence_words(str(meta.get('licence') or ''))}"]
    return lines + ([f"Why: {why}"] if why else []) + [""]


def _quoted(text: str) -> List[str]:
    return [("    " + line).rstrip() for line in text.splitlines()] or ["    (empty)"]


def build(entries: Sequence[Mapping[str, Any]], prefix: str = "") -> LicenceFiles:
    """The licence files for a knowledge file's entries as a pack carries them. prefix: the knowledge file's folder in
    the pack ("" for a knowledge pack, "knowledge/" for a target pack), for the paths the summary names."""
    pages = _pages(entries)
    metas = {source: _meta(chunks) for source, chunks in pages.items()}
    full = {s: m for s, m in metas.items() if m.get("shared_as") == FULL_TEXT}
    result = LicenceFiles()
    for meta in metas.values():
        if meta.get("licence_state") == licences.NO_LICENCE_TEXT and meta.get("licence"):
            licence = str(meta["licence"])
            result.without_text[licence] = result.without_text.get(licence, 0) + 1
    by_licence: Dict[str, List[str]] = {}
    for source, meta in full.items():
        by_licence.setdefault(str(meta.get("licence") or ""), []).append(source)
    needed = sorted(licence for licence in by_licence if licence_texts.needs_text(licence))
    texts = {licence: licence_texts.canonical(licence) for licence in needed}
    needed = [licence for licence in needed if texts[licence] is not None]   # share_plan never lets the others in
    if not needed:
        return result
    version = licence_texts.list_version()
    # A NOTICE file the pack carries in full gives its notices; one shared only as its address is named with it.
    notices = {licences.work_key(s, m): (s, joined_text(pages[s]) if s in full else None) for s, m in metas.items()
               if licences.is_notice_file(s)}
    out = ["NOTICE", "",
           "What this pack carries of each page and file: its source address, its copyright line as found and its",
           "licence (SPDX id). Pages carried in full keep their own licences; the pack's licence does not cover them.",
           f"Changes made to every page carried in full: {licences.CHANGES}.",
           "",
           "The licences marked \"licence text\" ask for their text or permission notice with every copy. Their",
           f"canonical texts are in the folder {licence_texts.LICENSES_DIR}/, unchanged from the SPDX License List "
           f"{version}.", ""]
    for licence in sorted(by_licence, key=lambda name: (name not in needed, name)):
        sources = sorted(by_licence[licence])
        if licence in needed:
            path = licence_texts.text_path(licence)
            out += [f"== {licence} (licence text: {path}) ==", licence_texts.TEXT_REQUIRED[licence] + "."]
            if licence in licence_texts.COPYRIGHT_WITH_TEXT:
                out.append(f"Each copyright line below goes with the licence text in {path}.")
            out.append("")
            result.texts.append(IncludedText(licence, prefix + path, len(sources), licence_texts.TEXT_REQUIRED[licence]))
            result.files[path] = texts[licence].data
        else:
            out += [f"== {_licence_words(licence)} (asks for no licence text) ==", ""]
        for source in sources:
            out += _entry(source, full[source])
        if licence == "Apache-2.0":
            for work in sorted({licences.work_key(s, full[s]) for s in sources}):
                found = notices.get(work)
                name = work[1] or "this work"
                if found and found[1] is not None:
                    out += [f"NOTICE file of {name}, as the crawl read it ({found[0]}):", *_quoted(found[1]), ""]
                elif found:
                    out += [f"The crawl read the NOTICE file of {name}, but this pack carries only its address: read "
                            f"its notices at {found[0]}.", ""]
                else:
                    out += [f"The crawl read no NOTICE file of {name}; if the work has one, its notices apply too "
                            "(read them at the source).", ""]
    linked = sorted(s for s, m in metas.items() if m.get("shared_as") == LINK_AND_FACTS)
    if linked:
        out += ["== Shared only as the address and short facts (no text of these pages is in this pack) ==", ""]
        for source in linked:
            out += _entry(source, metas[source], licences.clean(metas[source].get("share_reason") or "", 300))
    result.files[licence_texts.NOTICE_NAME] = ("\n".join(out).rstrip() + "\n").encode("utf-8")
    return result


def write(folder: Path, entries: Sequence[Mapping[str, Any]], prefix: str = "") -> LicenceFiles:
    """Writes the licence files of a knowledge file into its folder (build) and returns them."""
    files = build(entries, prefix)
    for rel, data in files.files.items():
        path = Path(folder).joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def check(folder: Path, entries: Sequence[Mapping[str, Any]], prefix: str = "") -> Tuple[List[str], List[str]]:
    """(problems, notes) of the licence files next to a knowledge file in `folder` (see the module text). Messages name
    files and licence ids, never a page's address (pack content is external text)."""
    folder = Path(folder)
    problems: List[str] = []
    present: Dict[str, str] = {}
    texts_dir = folder / licence_texts.LICENSES_DIR
    if texts_dir.is_dir():
        for path in sorted(p for p in texts_dir.rglob("*") if not p.is_dir()):
            rel = path.relative_to(folder).as_posix()
            match = licence_texts.TEXT_PATH.match(rel)
            text = licence_texts.canonical(match.group(1)) if match else None
            if match is None:
                problems.append(f"{prefix}{rel}: not a licence text ({licence_texts.LICENSES_DIR}/<SPDX id>.txt)")
            elif text is None:
                problems.append(f"{prefix}{rel}: {match.group(1)} is not a licence whose canonical text this framework "
                                "ships")
            elif path.read_bytes() != text.data:
                problems.append(f"{prefix}{rel}: differs from the canonical text of {match.group(1)} (SPDX License "
                                f"List {licence_texts.list_version()})")
            else:
                present[match.group(1)] = rel
    pages = _pages(entries)
    needed: Dict[str, List[str]] = {}
    for source, chunks in pages.items():
        meta = _meta(chunks)
        licence = str(meta.get("licence") or "")
        if meta.get("shared_as") == FULL_TEXT and licence_texts.needs_text(licence):
            needed.setdefault(licence, []).append(source)
    for licence, sources in sorted(needed.items()):
        if licence not in present:
            problems.append(f"{len(sources)} page(s) carry their full text under {licence}, but "
                            f"{prefix}{licence_texts.text_path(licence)} is missing")
    if needed:
        notice = folder / licence_texts.NOTICE_NAME
        try:
            listed = {line[len(SOURCE_PREFIX):].strip() for line in notice.read_text(encoding="utf-8").splitlines()
                      if line.startswith(SOURCE_PREFIX)}
        except (OSError, ValueError):
            listed = None
        if listed is None:
            problems.append(f"{prefix}{licence_texts.NOTICE_NAME}: missing, or not text; it must list every page "
                            "carried in full under a licence that asks for its text")
        else:
            unlisted = sum(1 for sources in needed.values() for s in sources if s.strip() not in listed)
            if unlisted:
                problems.append(f"{prefix}{licence_texts.NOTICE_NAME} does not list {unlisted} page(s) carried in "
                                "full under a licence that asks for its text")
    notes = [f"licence texts kept with the pack: {', '.join(sorted(present))} ({prefix}{licence_texts.LICENSES_DIR}/)"
             + (f" and {prefix}{licence_texts.NOTICE_NAME}" if (folder / licence_texts.NOTICE_NAME).is_file() else "")
             ] if present else []
    return problems, notes
