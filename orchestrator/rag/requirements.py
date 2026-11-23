#!/usr/bin/env python3
"""
The statements of requirements in a page (docs/DESIGN.md, "Dependencies are data in the wizard": Finding them).

A vendor's documentation says what an SDK needs in a few sentences between pages of other text: "requires libusb 1.0.26",
"prerequisites: Python 3.8", `sudo apt install libusb-1.0-0`. Deep Ingest keeps those statements next to the excerpt that
says them (the `requirements` field of a stored excerpt), with the page's own address as their source, so Ditto's proposal
of a target's dependency entries (Core/dependency_proposal.py) has short statements to cite first.

Found by plain rules, no model: a sentence or a line that says "requires", "required", "prerequisite", "depends on",
"must be installed" and the like, and a line that is an install command of a package manager (`apt install`, `pip install`,
`dnf`, `pacman -S`, ...). Each statement is the page's own words on one line (whitespace collapsed, at most MAX_LENGTH
characters), so it is still a piece of the page's text and an entry that cites it is checked against it as against the
page. At most MAX_PER_EXCERPT per excerpt. Standard library only.
"""

import re
from typing import List

MAX_PER_EXCERPT = 8
MAX_LENGTH = 240
_SAYS = re.compile(r"(?i)\b(?:requires?|required|requirements?|pre-?requisites?|depends?\s+on|dependenc(?:y|ies)|"
                   r"must\s+be\s+installed|needs?\s+to\s+be\s+installed|has\s+to\s+be\s+installed|"
                   r"minimum\s+(?:version|driver|firmware)|at\s+least\s+version)\b")
_INSTALL = re.compile(r"(?i)^\s*(?:[$#>]\s*)?(?:sudo\s+)?(?:apt(?:-get)?|dnf|yum|zypper|pacman|apk|brew|pip3?|conda|"
                      r"python3?\s+-m\s+pip)\s+(?:-{1,2}[\w-]+(?:[= ][^\s]+)?\s+)*(?:install|add)\b.*\S|"
                      r"^\s*(?:[$#>]\s*)?(?:sudo\s+)?pacman\s+-S\w*\s+\S+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])")
_MARKUP = re.compile(r"^[\s>*+\-#|]+|[|]+$")


def _clean(statement: str) -> str:
    text = _MARKUP.sub("", statement.strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_LENGTH].rstrip()


def find(text: str, limit: int = MAX_PER_EXCERPT) -> List[str]:
    """The statements of requirements in `text`, in the order the page says them, each once."""
    found: List[str] = []
    seen = set()

    def keep(statement: str) -> bool:
        statement = _clean(statement)
        key = statement.lower()
        if len(statement) < 8 or key in seen:
            return False
        seen.add(key)
        found.append(statement)
        return len(found) >= limit

    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        if _INSTALL.match(line):
            if keep(line):
                break
            continue
        if not _SAYS.search(line):
            continue
        for sentence in _SENTENCE_END.split(line):
            if _SAYS.search(sentence) and keep(sentence):
                return found
    return found


__all__ = ["MAX_LENGTH", "MAX_PER_EXCERPT", "find"]
