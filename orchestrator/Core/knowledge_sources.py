#!/usr/bin/env python3
"""
What the knowledge base holds, as the person sees it: every source with the licence and copyright notice it shows, and
what a knowledge pack would carry of it (its full text, only its address and short facts, or nothing) and why
(DECISIONS round 8, Q-1 and Q-1'). `llm-cli knowledge sources` and Tools > Knowledge Sources... show the same list
through these functions (DECISIONS W1': the CLI is never the person's only way); the words of "In a pack" are the same
on both (the GUI has them in both languages, l10n_knowledge).

This module imports nothing heavy: the knowledge base is opened by the framework (ensure_rag_manager), and its
RAGManager.list_sources reads payload fields only, never the chunk text.
"""

from typing import Any, Dict, List, Tuple

# The kinds of a source (rag_manager.source_kind), and the filter's choices.
KINDS = ("web", "pack", "docs", "other")
KIND_CHOICES = KINDS + ("any",)
# What a knowledge pack carries of a source (licence_view's pack_share), in the words the person reads.
CARRIED = {"full_text": "full text", "link_and_facts": "link and short facts", "not_exported": "not exported"}
OFF = ("The knowledge base is off (setting enable_rag_knowledge). Switch it on in the GUI's AI settings first.")


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


def sources(rag: Any, kind: str = "any") -> List[Dict[str, Any]]:
    """Every source of the knowledge base `rag` (RAGManager.list_sources: source, title, kind, licence, copyright,
    pack_share, share_reason, ...), only those of `kind` unless it is "any". Raises KnowledgeBaseError when the
    knowledge base cannot be read, ValueError for an unknown kind."""
    if kind not in KIND_CHOICES:
        raise ValueError(f"Unknown kind {kind!r}; one of {', '.join(KIND_CHOICES)}.")
    return [e for e in rag.list_sources() if kind in ("any", e.get("kind"))]


def carried(entry: Dict[str, Any]) -> str:
    """What a knowledge pack would carry of the source, in the person's words ("-" when it is not known)."""
    return CARRIED.get(str(entry.get("pack_share") or ""), "-")


def totals(entries: List[Dict[str, Any]]) -> Tuple[int, int]:
    """(sources a pack carries with their full text, sources it carries only as their address and short facts)."""
    full = sum(1 for e in entries if e.get("pack_share") == "full_text")
    facts = sum(1 for e in entries if e.get("pack_share") == "link_and_facts")
    return full, facts


def empty_sentence(kind: str = "any") -> str:
    return "The knowledge base holds no source" + ("." if kind == "any" else f" of kind {kind}.")


def totals_sentence(entries: List[Dict[str, Any]]) -> str:
    full, facts = totals(entries)
    return (f"{len(entries)} source(s): a knowledge pack would carry {full} with their full text and {facts} only as "
            "their address and short facts (llm-cli zoo export knowledge lists them again).")


__all__ = ["CARRIED", "KINDS", "KIND_CHOICES", "KnowledgeOff", "OFF", "carried", "empty_sentence", "knowledge_base",
           "sources", "totals", "totals_sentence"]
