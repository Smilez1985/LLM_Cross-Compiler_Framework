#!/usr/bin/env python3
"""
Private pages of the knowledge base (DESIGN, Knowledge base and Deep Ingest; maintainer's decisions of 2026-11-14).

A page that cannot be crawled (robots.txt or another opt-out, a login, a paywall, an error) is given to the person as a
link. The person may save it as a PDF or HTML and hand the file over: the knowledge base then stores it as a private
page. A private page stays on this computer: it is marked private wherever it is listed, and it is never shared, never
put into a knowledge pack or an export, never taken into the community's or the global knowledge wiki, never learned
by Ditto's archive, and never uploaded as a file. The person's own AI model, local or cloud, may read its excerpts like
those of any source, because that is reading on the person's own computer.

`shareable(payload)` is the one function every way out asks: it says no for a private excerpt. The marker is the
framework's alone: `PRIVATE_FIELD` is a reserved payload field (caller metadata, a knowledge pack's included, cannot
set it), and a source label that starts with `PRIVATE_PREFIX` counts as private too, so a label can never hide a page
from the check. Standard library only.
"""

from typing import Any, Mapping

PRIVATE_FIELD = "private"
PRIVATE_PREFIX = "private:"


def is_private(payload: Mapping[str, Any]) -> bool:
    """Whether a stored excerpt (its payload, or an export record's metadata with its `source`) belongs to a private
    page: the framework's marker, or a source label that starts with the private prefix."""
    return bool(payload.get(PRIVATE_FIELD) is True or str(payload.get("source") or "").startswith(PRIVATE_PREFIX))


def shareable(payload: Mapping[str, Any]) -> bool:
    """Whether an excerpt may leave this computer in a pack, an export or a wiki: never a private one."""
    return not is_private(payload)


def private_label(label: str) -> str:
    """The source label of a private page: `label` (the address the person gave, else the file's name) behind the
    private prefix, so that a crawl of the same address never overwrites it and the label itself says it."""
    label = str(label or "").strip()
    return label if label.startswith(PRIVATE_PREFIX) else PRIVATE_PREFIX + label


__all__ = ["PRIVATE_FIELD", "PRIVATE_PREFIX", "is_private", "private_label", "shareable"]
