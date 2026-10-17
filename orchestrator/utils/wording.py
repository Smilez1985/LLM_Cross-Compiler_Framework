#!/usr/bin/env python3
"""
Wording helpers, Qt-free (wave 6, f-words): how framework text is checked and how error details are prepared for the
person.

Lint helpers for the final style test (each returns a list of findings, empty when the text is fine; z-integration
runs them over every l10n value and tightens a baseline):

* `ellipsis_problems(text)`    the ASCII "..." convention: no "…" character, no space before "...".
* `informal_forms(text)`       German du forms ("du", "dich", "dein" ...): the default German texts use them; a text
                               with one needs its "Sie" form in the module's FORMAL overlay (Q1). Only pronouns are
                               found, not imperatives ("Wähle ..."), so the 'formal' chain still reads every text.
* `formal_forms(text)`         German Sie forms ("Ihnen", "Ihr", "Sie" after the start of a sentence): they belong
                               in FORMAL, never in the du table.
* `synonym_problems(text, lang)` a word the glossary's CANONICAL table says to avoid, with the word to use instead.
* `menu_path_problems(text)`   a hand-written menu path ("Tools > Mojo Health Check..."): texts name places with
                               {path:<objectName>} instead (V2; chain a0 owns the guard test).

For the error box (Q7):

* `technical_details(text)`    the English technical detail as it may be shown and copied: secrets redacted (the one
                               table of secret formats) and this computer's home folder written as "~".
"""

import re
from typing import List, Optional

from orchestrator.utils.l10n_glossary import CANONICAL

_INFORMAL = re.compile(r"\b(du|dich|dir|dein|deine|deinem|deinen|deiner|deines|euch|euer|eure|euren|eurem)\b",
                       re.IGNORECASE)
_FORMAL_ANYWHERE = re.compile(r"\b(Ihnen|Ihr|Ihre|Ihrem|Ihren|Ihrer|Ihres)\b")
_SIE = re.compile(r"\bSie\b")
_SENTENCE_START = re.compile(r"(^|[.!?:]\s+|\n\s*|[„\"(]\s*)$")   # after ";" German goes on in lower case
# A menu title is one capitalized word ("Tools", "Datei"); the path ends with the entry's "..." (or a further "> ").
_MENU_PATH = re.compile(r"\b[A-ZÄÖÜ][\w&]*\s+>\s+[A-ZÄÖÜ„\"][^\n>]*?(?:\.\.\.|…)")


def ellipsis_problems(text: str) -> List[str]:
    found = []
    if "…" in text:
        found.append("uses the character … instead of three dots (...)")
    if re.search(r"\s\.\.\.", text):
        found.append("has a space before ...")
    return found


def informal_forms(text: str) -> List[str]:
    """The German du pronouns in `text`, in order."""
    return [m.group(0) for m in _INFORMAL.finditer(text)]


def formal_forms(text: str) -> List[str]:
    """The German Sie forms in `text`: possessives and "Ihnen" anywhere, "Sie" where it cannot be "she/they" at the
    start of a sentence (a heuristic: "Sie" right after a sentence start is left out)."""
    found = [m.group(0) for m in _FORMAL_ANYWHERE.finditer(text)]
    for m in _SIE.finditer(text):
        if not _SENTENCE_START.search(text[:m.start()]):
            found.append("Sie")
    return found


def synonym_problems(text: str, lang: str = "en") -> List[str]:
    """Words of `text` that the CANONICAL table says to avoid in `lang`, each with the word to use instead."""
    found = []
    for entry in CANONICAL:
        avoid, use = (entry.avoid_de, entry.de) if lang == "de" else (entry.avoid_en, entry.en)
        for word in avoid:
            if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", text, re.IGNORECASE):
                found.append(f"{word!r}: say {use!r}")
    return found


def menu_path_problems(text: str) -> List[str]:
    """Hand-written menu paths in `text` ("File > Settings...")."""
    return [m.group(0) for m in _MENU_PATH.finditer(text)]


def technical_details(text: Optional[str]) -> str:
    """`text` with secrets redacted and this computer's home folder written as "~": what the error box shows under
    "Technical details" and copies."""
    from orchestrator.tools.tools_runtime import shorten_home
    from orchestrator.utils.helpers import redact_secrets
    return shorten_home(redact_secrets(str(text or "")))


__all__ = ["ellipsis_problems", "informal_forms", "formal_forms", "synonym_problems", "menu_path_problems",
           "technical_details"]
