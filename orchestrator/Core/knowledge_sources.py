#!/usr/bin/env python3
"""
What the knowledge base holds, as the person sees it: every source with the licence and copyright notice it shows, and
what a knowledge pack would carry of it (its full text, only its address and short facts, or nothing) and why
(DECISIONS round 8, Q-1 and Q-1'), and how much hidden text was left out of it before it was stored (recon P1).
`llm-cli knowledge sources` and Tools > Knowledge Sources... show the same list through these functions (DECISIONS W1':
the CLI is never the person's only way); the words of "In a pack" and of "Hidden text left out" are the same on both
(the GUI has them in both languages, l10n_knowledge).

This module imports nothing heavy: the knowledge base is opened by the framework (ensure_rag_manager), and its
RAGManager.list_sources reads payload fields only, never the chunk text.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

# The kinds of a source (rag_manager.source_kind), and the filter's choices.
KINDS = ("web", "pack", "docs", "other")
KIND_CHOICES = KINDS + ("any",)
# What a knowledge pack carries of a source (licence_view's pack_share), in the words the person reads.
CARRIED = {"full_text": "full text", "link_and_facts": "link and short facts", "not_exported": "not exported"}
OFF = ("The knowledge base is off (setting enable_rag_knowledge). Switch it on in the GUI's AI settings first.")
# What was left out of a page from outside before it was stored (RAGManager.list_sources: hidden_removed; the kinds of
# rag/html_text.REMOVED_KINDS), in the words the person reads. The GUI has them in both languages (l10n_knowledge,
# sources.removed.<kind>). Elements hidden from a reader count once each, invisible characters per character.
REMOVED = {"template": "template", "noscript": "noscript", "hidden_attribute": "hidden attribute",
           "aria_hidden": "aria-hidden", "display_none": "display:none", "visibility_hidden": "visibility:hidden",
           "font_size_zero": "font size 0", "opacity_zero": "opacity 0", "off_screen": "off-screen",
           "clipped": "clipped away", "same_colour": "text in its background colour",
           "invisible_characters": "invisible characters", "html_comments": "HTML comments"}
REMOVED_NONE = "none"
# The columns of the list, in this order on both (`llm-cli knowledge sources`, Tools > Knowledge Sources...: its
# sources.col.* texts in English are these words): what a source is and where it belongs come first.
COLUMNS = ("Source", "Kind", "Topic or target", "Hidden text left out", "Licence", "Copyright notice", "In a pack",
           "Why")


class KnowledgeOff(RuntimeError):
    """The knowledge base is switched off (setting enable_rag_knowledge): nothing can be listed."""

    def __init__(self, message: str = OFF):
        super().__init__(message)


def knowledge_base(framework: Any) -> Any:
    """The framework's knowledge base (RAGManager), created on demand; KnowledgeOff when it is switched off."""
    ensure = getattr(framework, "ensure_rag_manager", None)
    rag = ensure() if callable(ensure) else None
    if rag is None:
        raise KnowledgeOff()
    return rag


def sources(rag: Any, kind: str = "any", topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every source of the knowledge base `rag` (RAGManager.list_sources: source, title, kind, licence, copyright,
    pack_share, share_reason, topic, target, ...), only those of `kind` unless it is "any", and with `topic` only those
    that belong to that topic or target (DECISIONS round 23 item 1; ignoring case). Raises KnowledgeBaseError when the
    knowledge base cannot be read, ValueError for an unknown kind."""
    if kind not in KIND_CHOICES:
        raise ValueError(f"Unknown kind {kind!r}; one of {', '.join(KIND_CHOICES)}.")
    return [e for e in rag.list_sources() if kind in ("any", e.get("kind")) and belongs_to(e, topic)]


def topic_of(entry: Dict[str, Any]) -> str:
    """The topic or target a source belongs to ("target X" / "topic X"; "-" for your own pages, which belong to the
    topic or target you name when you export them)."""
    if entry.get("target"):
        return f"target {entry['target']}"
    if entry.get("topic"):
        return f"topic {entry['topic']}"
    return "-"


def belongs_to(entry: Dict[str, Any], topic: Optional[str]) -> bool:
    """Whether a source or an excerpt's metadata belongs to `topic` (a topic or a target, ignoring case); True for
    no topic."""
    if not topic:
        return True
    wanted = str(topic).strip().casefold()
    # an excerpt several packs (or a pack and your own crawl) hold belongs to the topic or target of each of them
    holders = entry.get("holder_info")
    owners = [entry] + [v for v in holders.values() if isinstance(v, dict)] if isinstance(holders, dict) else [entry]
    return any(str(owner.get(key) or "").casefold() == wanted for owner in owners for key in ("topic", "target"))


def carried(entry: Dict[str, Any]) -> str:
    """What a knowledge pack would carry of the source, in the person's words ("-" when it is not known)."""
    return CARRIED.get(str(entry.get("pack_share") or ""), "-")


def removed_text(entry: Dict[str, Any], label: Callable[[str], str] = lambda kind: REMOVED.get(kind, kind),
                 none: str = REMOVED_NONE) -> str:
    """How much hidden text was left out of the source before it was stored, kind by kind in REMOVED's order, e.g.
    "display:none 2, invisible characters 5"; `none` when the check found nothing; "-" for a source stored without
    the check (the framework's documents, pages stored before it existed). label and none: the words of the GUI's
    language."""
    removed = entry.get("hidden_removed")
    if not isinstance(removed, dict):
        return "-"
    counts = [(kind, count) for kind, count in removed.items() if isinstance(count, int) and count > 0]
    if not counts:
        return none
    order = list(REMOVED)
    counts.sort(key=lambda item: order.index(item[0]) if item[0] in order else len(order))
    return ", ".join(f"{label(kind)} {count}" for kind, count in counts)


def totals(entries: List[Dict[str, Any]]) -> Tuple[int, int]:
    """(sources a pack carries with their full text, sources it carries only as their address and short facts)."""
    full = sum(1 for e in entries if e.get("pack_share") == "full_text")
    facts = sum(1 for e in entries if e.get("pack_share") == "link_and_facts")
    return full, facts


def empty_sentence(kind: str = "any", topic: Optional[str] = None) -> str:
    return ("The knowledge base holds no source" + ("" if kind == "any" else f" of kind {kind}")
            + (f" on the topic or target {topic}" if topic else "") + ".")


def totals_sentence(entries: List[Dict[str, Any]]) -> str:
    full, facts = totals(entries)
    return (f"{len(entries)} source(s): a knowledge pack would carry {full} with their full text and {facts} only as "
            "their address and short facts (llm-cli zoo export knowledge lists them again).")


__all__ = ["CARRIED", "KINDS", "KIND_CHOICES", "KnowledgeOff", "OFF", "REMOVED", "REMOVED_NONE", "belongs_to",
           "carried", "empty_sentence", "knowledge_base", "removed_text", "sources", "topic_of", "totals",
           "totals_sentence"]
