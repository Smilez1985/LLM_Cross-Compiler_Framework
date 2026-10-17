#!/usr/bin/env python3
"""
Structure-aware, token-aware chunking for the knowledge base.

Markdown (also crawled HTML, which the crawler converts to Markdown) is split into blocks: headings,
paragraphs, lists, fenced code and tables. Code blocks and tables stay whole as long as the embedding model
sees them whole (hard_tokens); larger ones are split at line boundaries, each piece repeating the table
header or reopening the code fence. Headings start new chunks and give each chunk its section path. Sizes
are measured with the embedding model's tokenizer when it is available. Source files are split at blank
lines instead, and plain text (PDFs) at paragraphs and sentences.
"""

import hashlib
import re
import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Literal, Optional, Tuple

TokenCounter = Callable[[str], int]
Kind = Literal["markdown", "code", "text"]

# Fixed namespace so point ids are stable across machines and versions.
CHUNK_NAMESPACE = uuid.UUID("6f1c2b8e-3d4a-5b6c-8d7e-9f0a1b2c3d4e")
# Tokens of the model's context kept for the preamble line and the passage prefix.
PREAMBLE_RESERVE_TOKENS = 96

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_LIST_ITEM = re.compile(r"^\s*([-*+]|\d{1,9}[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+(?=[\"'„“(\[]?[A-ZÄÖÜ0-9])")


@dataclass
class Block:
    kind: str  # heading | prose | list | code | table
    text: str
    level: int = 0
    title: str = ""


@dataclass
class Chunk:
    text: str
    section: str
    index: int
    tokens: int
    hash: str


def normalized_hash(text: str) -> str:
    """Hash of the text with Unicode, case and whitespace normalized: equal content, equal hash."""
    norm = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def point_id(source: str, chunk_hash: str) -> str:
    """Deterministic id: re-ingesting the same content of the same source overwrites instead of duplicating."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{source}\x1f{chunk_hash}"))


def build_preamble(source: str, title: str = "", section: str = "") -> str:
    """The structural line embedded in front of every chunk (free context for the embedding and BM25)."""
    def clean(value: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", value or "").strip()
        return value if len(value) <= limit else value[:limit - 1] + "…"

    parts = [f"Source: {clean(source, 120)}"]
    if title:
        parts.append(f"Title: {clean(title, 100)}")
    if section and clean(section, 120) != clean(title, 120):
        parts.append(f"Section: {clean(section, 120)}")
    return " | ".join(parts)


def passage_text(source: str, title: str, section: str, text: str) -> str:
    """What is embedded for a chunk: its preamble line, a blank line and the chunk."""
    return build_preamble(source, title, section) + "\n\n" + text


# --- parsing ---------------------------------------------------------------------------------------------

def parse_markdown(text: str) -> List[Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[Block] = []
    para: List[str] = []

    def flush():
        if para:
            body = "\n".join(para).strip("\n")
            if body.strip():
                blocks.append(Block("list" if _LIST_ITEM.match(para[0]) else "prose", body))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_OPEN.match(line)
        if fence:
            flush()
            marker = fence.group(1)
            close = re.compile(rf"^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}\s*$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            blocks.append(Block("code", "\n".join(lines[i:j + 1])))  # an unclosed fence runs to the end
            i = j + 1
            continue
        heading = _ATX.match(line)
        if heading:
            flush()
            blocks.append(Block("heading", line.strip(), level=len(heading.group(1)), title=heading.group(2)))
            i += 1
            continue
        if len(para) == 1 and _SETEXT.match(line) and not _LIST_ITEM.match(para[0]) and "|" not in para[0]:
            title = para[0].strip()
            para.clear()
            blocks.append(Block("heading", title, level=1 if line.strip()[0] == "=" else 2, title=title))
            i += 1
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ("|" in line and _TABLE_SEP.match(nxt)) or (_TABLE_ROW.match(line) and _TABLE_ROW.match(nxt)):
            flush()
            j = i
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            blocks.append(Block("table", "\n".join(lines[i:j])))
            i = j
            continue
        if not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


def parse_paragraphs(text: str, kind: str = "prose") -> List[Block]:
    parts = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    return [Block(kind, p.strip("\n")) for p in parts if p.strip()]


# --- splitting of oversized prose ------------------------------------------------------------------------

def _hard_split(text: str, max_tokens: int, count: TokenCounter) -> List[str]:
    """Last resort for a single over-long sentence or line: windows at whitespace."""
    total = max(count(text), 1)
    window = max(int(len(text) * max_tokens / total * 0.9), 1)
    pieces, rest = [], text.strip()
    while rest:
        # Only short remainders are counted, so a huge single line stays linear.
        if len(rest) <= window * 1.2 and count(rest) <= max_tokens:
            pieces.append(rest)
            break
        cut = rest.rfind(" ", 0, window)
        cut = cut if cut > window // 2 else window
        piece = rest[:cut].strip()
        while piece and count(piece) > max_tokens and len(piece) > 1:
            piece = piece[: int(len(piece) * 0.8)].strip()
        piece = piece or rest[:1]
        pieces.append(piece)
        rest = rest[len(piece):].strip()
    return pieces


def _split_block(block: Block, limit: int, count: TokenCounter) -> List[str]:
    """An oversized code block or table as pieces of at most `limit` tokens, cut at line boundaries. Every
    piece repeats the table's header and separator rows, or reopens and closes the code fence."""
    lines = block.text.split("\n")
    head: List[str] = []
    body: List[str] = lines
    tail: List[str] = []
    fence = _FENCE_OPEN.match(lines[0]) if block.kind == "code" else None
    if block.kind == "table" and len(lines) > 2 and _TABLE_SEP.match(lines[1]):
        head, body = lines[:2], lines[2:]
    elif fence:
        marker = fence.group(1)
        close = re.compile(rf"^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}\s*$")
        closed = len(lines) > 1 and close.match(lines[-1])
        # An unclosed fence (it runs to the end of the document) is closed in every piece.
        head, body, tail = [lines[0]], lines[1:-1] if closed else lines[1:], [lines[-1] if closed else marker]
    frame = count("\n".join(head + tail))
    if frame > limit // 2:  # a giant header is not worth repeating
        head, body, tail, frame = [], lines, [], 0
    room = max(limit - frame, 1)
    groups: List[List[str]] = [[]]
    used = 0
    for line in body:
        tokens = count(line)
        for part in (_hard_split(line, room, count) if tokens > room else [line]):
            part_tokens = tokens if part is line else count(part)
            if groups[-1] and used + part_tokens > room:
                groups.append([])
                used = 0
            groups[-1].append(part)
            used += part_tokens
    pieces: List[str] = []
    while groups:  # summed line counts can miss tokens at the joins: halve a group that does not fit
        rows = groups.pop(0)
        piece = "\n".join(head + rows + tail)
        if len(rows) > 1 and count(piece) > limit:
            groups[:0] = [rows[:len(rows) // 2], rows[len(rows) // 2:]]
        elif rows:
            pieces.append(piece)
    return pieces


def _parts(text: str, kind: str) -> List[str]:
    """Natural split points of a block: list items, lines of source code, or sentences."""
    if kind == "list":
        items, current = [], []
        for line in text.split("\n"):
            if _LIST_ITEM.match(line) and current:
                items.append("\n".join(current))
                current = []
            current.append(line)
        items.append("\n".join(current))
        return items
    if kind == "source":
        return [line for line in text.split("\n") if line.strip()]
    sentences = []
    for line in text.split("\n"):
        sentences.extend(s.strip() for s in _SENTENCE_END.split(line) if s.strip())
    return sentences


def _joiner(kind: str) -> str:
    return "\n" if kind in ("list", "source") else " "


@dataclass
class _Unit:
    text: str
    kind: str
    sep: str  # how it joins the previous unit inside a chunk
    tokens: int
    level: int = 0  # headings only
    title: str = ""


def _to_units(blocks: List[Block], max_tokens: int, count: TokenCounter,
              hard_tokens: Optional[int] = None) -> List[_Unit]:
    """Whole blocks where they fit; oversized prose, lists and source become sentences, items or lines, and
    code blocks and tables beyond hard_tokens (the model's context) become pieces of at most max_tokens."""
    units: List[_Unit] = []
    for block in blocks:
        tokens = count(block.text)
        if block.kind in ("code", "table") and hard_tokens and tokens > hard_tokens:
            for piece in _split_block(block, min(max_tokens, hard_tokens), count):
                units.append(_Unit(piece, block.kind, "\n\n", count(piece)))
            continue
        if block.kind in ("heading", "code", "table") or tokens <= max_tokens:
            units.append(_Unit(block.text, block.kind, "\n\n", tokens, block.level, block.title))
            continue
        sep = "\n\n"
        for part in _parts(block.text, block.kind):
            part_tokens = count(part)
            subs = _hard_split(part, max_tokens, count) if part_tokens > max_tokens else [part]
            for sub in subs:
                units.append(_Unit(sub, block.kind, sep, count(sub) if len(subs) > 1 else part_tokens))
                sep = _joiner(block.kind)
    return units


def _split_unit(unit: _Unit, budget: int, count: TokenCounter) -> Tuple[_Unit, Optional[_Unit]]:
    """The largest leading part of a unit that fits into budget, and the rest."""
    head: List[str] = []
    used = 0
    parts = _parts(unit.text, unit.kind)
    if len(parts) <= 1:
        parts = _hard_split(unit.text, budget, count)
    for i, part in enumerate(parts):
        tokens = count(part)
        if head and used + tokens > budget:
            rest = _joiner(unit.kind).join(parts[i:])
            first = _joiner(unit.kind).join(head)
            return _Unit(first, unit.kind, unit.sep, used), _Unit(rest, unit.kind, unit.sep, count(rest))
        head.append(part)
        used += tokens
    return unit, None


def _overlap_tail(units: List[_Unit], budget: int, count: TokenCounter) -> List[_Unit]:
    """Trailing prose of a finished chunk (whole units or sentences) that fits into the overlap budget."""
    tail: List[_Unit] = []
    used = 0
    for unit in reversed(units):
        if unit.kind not in ("prose", "list"):
            break
        if used + unit.tokens <= budget:
            tail.insert(0, unit)
            used += unit.tokens
            continue
        for part in reversed(_parts(unit.text, unit.kind)):
            tokens = count(part)
            if used + tokens > budget:
                break
            tail.insert(0, _Unit(part, unit.kind, _joiner(unit.kind), tokens))
            used += tokens
        break
    return tail


# --- packing ---------------------------------------------------------------------------------------------

def chunk_document(text: str, *, count_tokens: TokenCounter, max_tokens: int, overlap_tokens: int = 0,
                   kind: Kind = "markdown", base_section: str = "", hard_tokens: Optional[int] = None) -> List[Chunk]:
    """Splits a document into chunks of at most max_tokens. Code blocks and tables may exceed it whole up to
    hard_tokens (no limit when None); larger ones are split at lines."""
    if not text or not text.strip():
        return []
    max_tokens = max(int(max_tokens), 16)
    overlap_tokens = max(0, min(int(overlap_tokens), max_tokens // 4))
    if kind == "markdown":
        blocks = parse_markdown(text)
    elif kind == "code":
        blocks = parse_paragraphs(text, "source")
        overlap_tokens = 0  # repeated lines of code only add noise
    else:
        blocks = parse_paragraphs(text)

    min_tokens = max_tokens // 4
    chunks: List[Chunk] = []
    headings: List[Tuple[int, str]] = []
    current: List[_Unit] = []
    current_tokens = 0
    current_section = base_section

    def section_path() -> str:
        return " > ".join(title for _, title in headings if title) or base_section

    def has_content() -> bool:
        return any(u.kind != "heading" for u in current)

    def emit(next_tokens: Optional[int] = None):
        """Finishes the current chunk; with next_tokens, carries an overlap if the next unit still fits."""
        nonlocal current, current_tokens, current_section
        body = (current[0].text + "".join(u.sep + u.text for u in current[1:])).strip()
        chunks.append(Chunk(text=body, section=current_section, index=len(chunks),
                            tokens=count_tokens(body), hash=normalized_hash(body)))
        tail = _overlap_tail(current, overlap_tokens, count_tokens) if next_tokens is not None else []
        if sum(u.tokens for u in tail) + (next_tokens or 0) > max_tokens:
            tail = []
        current = tail
        current_tokens = sum(u.tokens for u in tail)
        current_section = section_path()

    queue = deque(_to_units(blocks, max_tokens, count_tokens, hard_tokens))
    while queue:
        unit = queue.popleft()
        if unit.kind == "heading":
            # Headings start a new chunk unless the current one is still small (tiny sections are merged).
            if current_tokens >= min_tokens and has_content():
                emit()
            while headings and headings[-1][0] >= unit.level:
                headings.pop()
            headings.append((unit.level, unit.title.strip()))
            if not has_content():
                current_section = section_path()
            current.append(unit)
            current_tokens += unit.tokens
            continue
        room = max_tokens - current_tokens
        if unit.tokens > room:
            if has_content():
                emit(next_tokens=unit.tokens)
            elif current and room > 0 and unit.kind not in ("code", "table"):
                # Only headings so far: put as much of the unit next to them as fits.
                unit, rest = _split_unit(unit, room, count_tokens)
                if rest is not None:
                    queue.appendleft(rest)
        current.append(unit)
        current_tokens += unit.tokens
    if has_content():
        emit()
    return chunks
