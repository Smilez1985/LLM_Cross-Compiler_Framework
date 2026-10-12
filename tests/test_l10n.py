"""The person's texts of the facts catalogue (delegation design 4.1, 4.5, 8.4; work package WP4).

Every warning and blocker code has a short text in English and German with the same placeholders, every action and
safer option the catalogue names exists in both languages, and the short texts carry no ids, codes or jargon: a
layperson reads them.
"""

import re
import string

import pytest

from orchestrator.tools import facts_catalog as catalog
from orchestrator.utils.l10n_facts import TEXTS
from orchestrator.utils.localization import LocalizationManager, ui_text

# Words a layperson does not know, or that name the framework's insides; never in the short layer.
JARGON = ("sha256", "digest", "elicitation", "stdio", "toctou", "json", "registry", "enum", "mcp", "delegable",
          "reason code", "blocker", "callfacts", "ssh_user", "known_hosts", "commit_")
ID_PATTERNS = (r"\b(?:req|job|ap|d|g|a|pkg|fe|tr)_[0-9a-f]{6,}", r"\b[0-9a-f]{16,}\b")


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_warning_and_blocker_code_has_en_and_de_text_with_the_same_placeholders():
    keys = [f"warning.{c}" for c in catalog.WARNINGS] + [f"blocker.{c}" for c in catalog.BLOCKERS]
    keys += [s.person_action for s in catalog.BLOCKERS.values() if s.person_action]
    keys += [s.safer for s in catalog.WARNINGS.values() if s.safer]
    for key in keys:
        en, de = TEXTS["en"].get(key), TEXTS["de"].get(key)
        assert en and de, f"{key} is missing in {'German' if en else 'English'}"
        assert placeholders(en) == placeholders(de), key
    assert set(TEXTS["en"]) == set(TEXTS["de"]), "the two languages hold the same keys"
    for key in TEXTS["en"]:
        assert placeholders(TEXTS["en"][key]) == placeholders(TEXTS["de"][key]), key


def test_a_warnings_person_text_uses_only_its_own_numbers():
    """The person's text is filled from the warning's numbers (and speaking names); a placeholder the AI template
    does not have would never be filled."""
    for code, spec in catalog.WARNINGS.items():
        assert placeholders(TEXTS["en"][f"warning.{code}"]) <= placeholders(spec.ai_template), code


def test_short_texts_have_no_ids_or_jargon():
    for lang, table in TEXTS.items():
        for key, text in table.items():
            bare = re.sub(r"\{[a-z_]+\}", "", text)
            assert "_" not in bare, f"{lang} {key}: a code or an id in a person's text"
            low = bare.lower()
            for word in JARGON:
                assert word not in low, f"{lang} {key}: {word!r}"
            for pattern in ID_PATTERNS:
                assert not re.search(pattern, bare), f"{lang} {key}: an id"


def test_the_texts_are_merged_into_the_framework_texts():
    for key in ("warning.disk_tight", "blocker.disk_full", "action.free_disk", "summary.title"):
        assert key in LocalizationManager.TRANSLATIONS["en"] and key in LocalizationManager.TRANSLATIONS["de"]
    assert ui_text("warning.disk_tight", "de", free_after_gb=3.5, reserve_gb=2.0).startswith("Der Speicherplatz")


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_warning_reads_in_both_languages(lang):
    for code, spec in catalog.WARNINGS.items():
        numbers = {name: 1 for name in placeholders(spec.ai_template)}
        text = catalog.warn(code, **numbers).person_text(lang)
        assert text and "{" not in text and text != f"warning.{code}", (lang, code)
