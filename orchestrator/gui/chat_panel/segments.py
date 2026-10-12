#!/usr/bin/env python3
"""
Splitting a message into what the panel shows (delegation design 5.2 R1 and R7).

`split_segments(text, names)` gives the segments of speaking_names (text, name, code): exact framework ids and masked
values outside code become their speaking names, code stays as written. `blocks(segments, original)` groups them for
plain-text widgets: a fenced code block becomes a block of its own, shown monospace; everything else is one text
block, in which a speaking name is written «like this» (a style of its own that plain text can show). With
`original=True` the ids come back exactly as the AI wrote them ("Show the original words"): the stored text is never
changed, only how it is shown.
"""

import re
from dataclasses import dataclass
from typing import List, Literal, Optional

from orchestrator.Core.speaking_names import CODE_RE, Segment, SpeakingNames

NAME_OPEN, NAME_CLOSE = "«", "»"


def split_segments(text: str, names: Optional[SpeakingNames] = None) -> List[Segment]:
    """The segments of `text`. Without `names` only code is split off (nothing is renamed)."""
    text = text or ""
    if names is not None:
        return names.render(text)
    segments: List[Segment] = []
    pos = 0
    for code in CODE_RE.finditer(text):
        if code.start() > pos:
            segments.append(Segment("text", text[pos:code.start()]))
        segments.append(Segment("code", code.group(0)))
        pos = code.end()
    if pos < len(text):
        segments.append(Segment("text", text[pos:]))
    return segments


@dataclass(frozen=True)
class Block:
    """A piece of a bubble: "text" (a plain-text label) or "code" (monospace, the fences and language tag removed)."""
    kind: Literal["text", "code"]
    text: str


def _fenced_body(code: str) -> str:
    """The inside of a ``` block: the opening fence with its language tag and the closing fence removed."""
    body = code[3:]
    if body.endswith("```"):
        body = body[:-3]
    first, newline, rest = body.partition("\n")
    if newline and (not first.strip() or re.fullmatch(r"[\w+.#-]+", first.strip())):
        return rest   # the opening fence's own line: empty or a language tag
    return body


def blocks(segments: List[Segment], original: bool = False) -> List[Block]:
    """The segments as text and code blocks; a name as «name» (or its exact original with `original`)."""
    out: List[Block] = []
    pending: List[str] = []

    def flush() -> None:
        if pending:
            text = "".join(pending)
            if text.strip():
                out.append(Block("text", text.strip("\n")))
            pending.clear()

    for segment in segments:
        if segment.kind == "code" and segment.text.startswith("```"):
            flush()
            out.append(Block("code", _fenced_body(segment.text).strip("\n")))
        elif segment.kind == "name":
            pending.append(segment.original if original else f"{NAME_OPEN}{segment.text}{NAME_CLOSE}")
        else:
            pending.append(segment.text)
    flush()
    return out


def has_names(segments: List[Segment]) -> bool:
    return any(s.kind == "name" for s in segments)


__all__ = ["split_segments", "Block", "blocks", "has_names", "NAME_OPEN", "NAME_CLOSE"]
