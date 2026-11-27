#!/usr/bin/env python3
"""
Ditto's archive: what a tool result may teach it (DESIGN, Ditto, the archive's rows; decisions of 2026-11-13).

One extractor per tool takes typed fields out of the tool's result model and writes a plain sentence from a fixed
template. No extractor passes free text of a model, a person, a page or a manifest on: every value that goes into a
sentence is a value the target itself declares (target.yml), a closed word, an address of a listed manufacturer or a
title that matches a strict pattern. A tool that has no extractor teaches the archive nothing, and so does a tool that
`configs/archive_sources.yml` does not list.

- `get_artifact_details`: a green-tested artifact build (the build ended `done`, its manifest records a passed
  functional test, and its target, board, format and quantization are ones the target declares) is the ground `tested`.
- `get_build_status`: a build that failed with a cause of the self-healing pattern library and ended `done` after the
  library's step (the framework's automatic step in the same run, or the person's step and the same build done within
  7 days: the request's `healed`) is the ground `tested` for that step on its target (step 5 of the one log). The cause
  and the step are the library's own words (self_healing_manager.healed_of), never a text of the result.
- `search_knowledge` and `read_knowledge_source`: a page of a listed manufacturer's website (by registrable domain) is
  the ground `vendor`: the entry says that the manufacturer's own page exists for a target; the page's statements are
  not copied.

Nothing here reads a person's conversation or a result about a named board of the person.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from orchestrator.Core.ditto_archive import Candidate, normalized, private_reason
from orchestrator.rag.private import is_private
from orchestrator.schemas.archive import TEXT_MAX
from orchestrator.utils import archive_sources

_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$")
_TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:+()/&_-]{2,100}$")
_ARTIFACT = re.compile(r"^a_[0-9a-f]{16}$")
_REQUEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HEALED_BY = {"automatic": "the framework's automatic step", "person": "the person's step"}


@dataclass
class ExtractContext:
    """What an extractor may ask outside its result: the archive's source table and the declared capabilities of a
    target (None for a target that is unknown or declares none)."""
    table: Dict[str, Any]
    capabilities: Callable[[str], Any] = lambda target: None


def _word(value: Any) -> str:
    return value if isinstance(value, str) and _WORD.match(value) else ""


def from_artifact(result: Any, ctx: ExtractContext) -> List[Candidate]:
    """A built model whose functional test passed: the build, as its manifest and the target's capabilities say."""
    artifact = str(getattr(result, "artifact_id", "") or "")
    manifest = getattr(result, "manifest", None)
    if not _ARTIFACT.match(artifact) or not isinstance(manifest, dict):
        return []
    conversion = manifest.get("conversion")
    test = conversion.get("functional_test") if isinstance(conversion, dict) else None
    if not (isinstance(test, str) and test.split(" ", 1)[0] == "passed"):
        return []
    target, board, fmt = (_word(manifest.get(k)) for k in ("target", "board", "format"))
    raw = manifest.get("quantization")
    quant = "default" if raw in (None, "", "default") else _word(raw)    # a value that is not a word is no "unset"
    caps = ctx.capabilities(target) if target else None
    format_caps = (getattr(caps, "formats", None) or {}).get(fmt) if caps is not None and fmt else None
    if format_caps is None:
        return []
    if quant == "default":
        quant = _word(getattr(format_caps, "default_quantization", ""))
    if quant not in (getattr(format_caps, "quantizations", None) or []):
        return []
    raw_board = manifest.get("board")
    if raw_board not in (None, "") and board not in (getattr(caps, "boards", None) or {}):
        return []
    where = f"board {board}, " if board else ""
    text = (f"A {fmt} build for target {target}, {where}quantization {quant} was built and passed its functional "
            "test.")
    key = f"build:{target}/{(board + '-') if board else ''}{fmt}-{quant}/works"
    # the claim's typed value (phase 2): the property "works" is true, so a second passed build of the same key is the
    # same claim whatever its sentence says
    return [Candidate(key, text, "tested", [f"build:{artifact}"], value_type="flag", value="true")]


def from_healing(result: Any, ctx: ExtractContext) -> List[Candidate]:
    """A build request that ended done after a step of the pattern library healed it: one entry per target and cause.
    Every word of the sentence is closed: a target the framework declares, the library's cause and its step (named
    only as the library's step when the archive's cleaners would change its text)."""
    from orchestrator.Core.self_healing_manager import cause_code, healed_of
    from orchestrator.security.paths import scrub_for_ai
    healed = getattr(result, "healed", None)
    healed = healed.model_dump() if hasattr(healed, "model_dump") else healed
    found = healed_of(healed)
    request_id = str(getattr(result, "request_id", "") or "")
    if found is None or getattr(result, "end_reason", None) != "done" or not _REQUEST.match(request_id):
        return []
    target = _word(found["target"])
    if not target or ctx.capabilities(target) is None:
        return []
    cause = found["cause"].rstrip(".")
    head = (f"For target {target}, a build that failed with the known cause \"{cause}\" ended done again after "
            f"{HEALED_BY[found['how']]} from the self-healing pattern library")
    text = f"{head}: {found['step']}"
    if (len(text) > TEXT_MAX or scrub_for_ai(normalized(text), limit=TEXT_MAX * 4).strip() != normalized(text)
            or private_reason(text)):
        text = f"{head}."
    key = f"healing:{target}/{cause_code(found['cause'])}/helps"
    return [Candidate(key, text, "tested", [f"build:{request_id}"], value_type="flag", value="true")]


def _pages(result: Any) -> List[Any]:
    """The hits of a search or the one source of a read, whatever the tool's result model is called."""
    hits = getattr(result, "results", None)
    if isinstance(hits, list):
        return hits
    one = getattr(result, "source", None)
    return [one] if one is not None and hasattr(one, "source") else []


def from_pages(result: Any, ctx: ExtractContext) -> List[Candidate]:
    """Pages of a listed manufacturer's website among the knowledge base's hits: the manufacturer's own page exists for
    its targets (the ground `vendor`); only the address and a title that matches a strict pattern are used."""
    found: List[Candidate] = []
    seen = set()
    for page in _pages(result):
        address = str(getattr(page, "source", "") or "")
        if getattr(page, "kind", "") == "private" or is_private({"source": address}):
            continue   # a private page (the person's saved copy) is never learned: it stays on this computer
        row = archive_sources.vendor_of(address, ctx.table)
        if row is None or address in seen:
            continue
        seen.add(address)
        vendor = str(row.get("vendor") or "")
        targets = [t for t in (row.get("targets") or []) if _word(t)]
        chips = [c for c in (row.get("chips") or []) if _word(c)]
        slug = re.sub(r"[^A-Za-z0-9]+", "-", vendor).strip("-")
        if not vendor or not slug or not (targets or chips) or not _TITLE.match(vendor):
            continue
        title = str(getattr(page, "title", "") or "").strip()
        named = f' "{title}"' if _TITLE.match(title) and '"' not in title else ""
        covers = " and ".join(part for part in (
            f"target {', '.join(targets)}" if targets else "", f"{'for ' if targets else ''}chips {', '.join(chips)}"
            if chips else "") if part)
        text = f"{vendor}'s own website has a page{named} at {address}, a manufacturer source for {covers}."
        key = f"vendor_page:{slug}/{hashlib.sha256(address.encode('utf-8')).hexdigest()[:12]}"
        found.append(Candidate(key, text, "vendor", [address]))
    return found


EXTRACTORS: Dict[str, Callable[[Any, ExtractContext], List[Candidate]]] = {
    "get_artifact_details": from_artifact,
    "get_build_status": from_healing,
    "search_knowledge": from_pages,
    "read_knowledge_source": from_pages,
}


def extract(tool: str, result: Any, ctx: ExtractContext) -> List[Candidate]:
    """The candidates a tool's result teaches ([] for a tool without an extractor, a tool the table does not list, or a
    result that gives nothing). Never raises: an extractor that fails teaches nothing."""
    extractor: Optional[Callable] = EXTRACTORS.get(tool)
    if extractor is None or tool not in archive_sources.listened_tools(ctx.table):
        return []
    try:
        return extractor(result, ctx)
    except Exception:  # noqa: BLE001 - a result that does not have the expected shape teaches nothing
        return []


__all__ = ["EXTRACTORS", "ExtractContext", "HEALED_BY", "extract", "from_artifact", "from_healing", "from_pages"]
