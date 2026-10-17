#!/usr/bin/env python3
"""
The glossary of technical terms, Qt-free (wave 6, f-words; DECISIONS round 10, V6).

The data lives in orchestrator/utils/l10n_glossary.py (TERMS, and the explanations "term.<id>" in TEXTS). This module
reads it for the hover card (gui/explain.py), for a CLI or an AI that wants the same explanation, and for tests:

* `term(term_id)`             the Term (shown, link, link_de, aliases, auto); KeyError for an id that does not exist.
* `explanation(term_id, lang)` the one or two plain sentences in the person's language (and form of address).
* `link(term_id, lang)`        the page to read more: the German page when there is one and the language is German.
* `source_label(url)`          where a link goes, as the card names it: "en.wikipedia.org", "qdrant.tech",
                               "github.com/ggml-org/llama.cpp".
* `find_terms(text)`           the ids of the terms a text names (shown form and aliases, whole words, any case, a
                               hyphen read like a space: "KV-Cache" is "KV cache"), in the order they first appear;
                               terms with auto=False are never found this way.

Links open only when the person clicks one (gui/explain.py); nothing here opens anything.
"""

import re
from functools import lru_cache
from typing import Dict, List, Optional, Pattern, Tuple
from urllib.parse import urlsplit

from orchestrator.utils.l10n_glossary import TERMS, Term
from orchestrator.utils.localization import current_language, ui_text

__all__ = ["TERMS", "Term", "term", "explanation", "link", "source_label", "find_terms", "term_ids"]


def term_ids() -> Tuple[str, ...]:
    return tuple(TERMS)


def term(term_id: str) -> Term:
    """The glossary entry of `term_id`; KeyError (with the id) when the glossary has no such term."""
    try:
        return TERMS[term_id]
    except KeyError:
        raise KeyError(f"the glossary has no term {term_id!r}") from None


def explanation(term_id: str, lang: Optional[str] = None) -> str:
    """The plain explanation of `term_id` in `lang` (default: the UI language)."""
    term(term_id)
    return ui_text(f"term.{term_id}", lang)


def link(term_id: str, lang: Optional[str] = None) -> str:
    """The page that explains `term_id` best, in German when one exists and `lang` is German."""
    entry = term(term_id)
    if (lang or current_language()) == "de" and entry.link_de:
        return entry.link_de
    return entry.link


def source_label(url: str) -> str:
    """Where `url` goes, as a short label: its host without "www.", plus owner and repository on GitHub."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "github.com":
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2:
            return f"{host}/{segments[0]}/{segments[1]}"
    return host


_SEPARATORS = re.compile(r"[\s\-]+")


def _key(name: str) -> str:
    """A name as find_terms compares it: any case, and a hyphen read like a space, because German writes the English
    terms with hyphens ("KV-Cache", "Audit-Log", "Build-Sandbox") and English sometimes without ("llama server")."""
    return _SEPARATORS.sub(" ", name.casefold()).strip()


@lru_cache(maxsize=1)
def _finder() -> Tuple[Pattern[str], Dict[str, str]]:
    names: Dict[str, str] = {}
    for term_id, entry in TERMS.items():
        if not entry.auto:
            continue
        for name in (entry.shown,) + tuple(entry.aliases):
            names.setdefault(_key(name), term_id)
    ordered = sorted(names, key=lambda n: (-len(n), n))   # the longest name wins ("SSH host key" before "SSH")
    alternatives = (r"[\s\-]+".join(re.escape(word) for word in name.split(" ")) for name in ordered)
    # Whole words only: never inside a word or a longer hyphenated name ("storage", "llm-cli", "KV-Cache-Typ").
    pattern = re.compile(r"(?<![\w-])(" + "|".join(alternatives) + r")(?![\w-])", re.IGNORECASE)
    return pattern, names


def find_terms(text: str) -> List[str]:
    """The ids of the glossary terms `text` names, each once, in the order they first appear."""
    if not text:
        return []
    pattern, names = _finder()
    found: List[str] = []
    for match in pattern.finditer(text):
        term_id = names[_key(match.group(1))]
        if term_id not in found:
            found.append(term_id)
    return found
