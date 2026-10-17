"""The wording toolkit's Qt-free parts (wave 6, f-words; DECISIONS round 10, V6, Q1, Q7).

The glossary (utils/l10n_glossary.py, utils/glossary.py): every term has an English and a German explanation and an
https link; the links into the framework's own manual name headings that exist. The German form of address: du is the
default, a module's FORMAL overlay gives the "Sie" form of exactly the texts that differ, with the same placeholders,
and ui_text reads it when the person chose "Sie". Sizes (utils/sizes.py) and the wording lint helpers
(utils/wording.py).
"""

import importlib
import sys
import types
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from orchestrator.utils import glossary, localization, sizes, wording
from orchestrator.utils.l10n_glossary import CANONICAL, FORMAL, REPO_DOCS, TERMS, TEXTS

REPO_ROOT = Path(__file__).resolve().parent.parent
# Texts that name a form of address rather than use it (the choice "du (informal, the default)"): the same in both.
NAMES_THE_FORM = {"words.address.du", "words.address.sie"}


@pytest.fixture
def form(monkeypatch):
    """Sets the form of address for one test; the default ("du") is back afterwards."""
    monkeypatch.setattr(localization, "_form_of_address", "du")
    return localization.set_form_of_address


# --- the glossary ---------------------------------------------------------------------------------------------------

def test_every_term_has_english_and_german_text_and_an_https_link():
    assert len(TERMS) >= 60
    for term_id, entry in TERMS.items():
        assert term_id == term_id.lower() and " " not in term_id, term_id
        assert entry.shown.strip() == entry.shown and entry.shown, term_id
        for lang in ("en", "de"):
            text = TEXTS[lang].get(f"term.{term_id}", "")
            assert len(text) > 20 and text.strip() == text, (term_id, lang)
            assert text.endswith((".", ")")), (term_id, lang)
        assert localization.placeholders(TEXTS["en"][f"term.{term_id}"]) == \
            localization.placeholders(TEXTS["de"][f"term.{term_id}"]), term_id
        for url in filter(None, (entry.link, entry.link_de)):
            parts = urlsplit(url)
            assert parts.scheme == "https" and parts.hostname, (term_id, url)
            assert url.isascii() and not any(c.isspace() for c in url), (term_id, url)
    explained = {k[len("term."):] for k in TEXTS["en"] if k.startswith("term.")}
    assert explained == set(TERMS), "every explanation belongs to a term and every term is explained"


def test_the_terms_the_maintainer_named_are_explained():
    for name in ("Qdrant", "GGUF", "KV cache", "Quantization", "Importance matrix", "RKLLM", "RKNN", "MCP",
                 "SSH host key", "Fingerprint", "Token", "Keyring", "Digest", "llama.cpp", "Calibration dataset",
                 "Q4_K_M", "Q8_0", "Q4_0", "IQ types", "TQ types"):
        assert any(t.shown == name for t in TERMS.values()), name
    assert TERMS["qdrant"].link.startswith("https://qdrant.tech/")
    assert TERMS["mcp"].link.startswith("https://modelcontextprotocol.io/")
    assert "github.com/ggml-org/" in TERMS["gguf"].link
    assert "github.com/airockchip/" in TERMS["rkllm"].link and "github.com/airockchip/" in TERMS["rknn"].link


def test_names_and_aliases_are_unique():
    seen = {}
    for term_id, entry in TERMS.items():
        for name in (entry.shown,) + entry.aliases:
            key = name.casefold()
            assert key not in seen, f"{name!r} names {seen.get(key)} and {term_id}"
            seen[key] = term_id


def test_links_into_the_manual_name_existing_headings():
    from test_docs import anchors
    checked = 0
    for term_id, entry in TERMS.items():
        for url in filter(None, (entry.link, entry.link_de)):
            if not url.startswith(REPO_DOCS):
                continue
            path, _, anchor = url[len(REPO_DOCS):].partition("#")
            document = REPO_ROOT / "docs" / path
            assert document.is_file(), (term_id, url)
            if anchor:
                assert unquote(anchor) in anchors(document.read_text(encoding="utf-8")), (term_id, url)
            checked += 1
    assert checked >= 30
    for term_id in ("target", "deep_ingest", "vault", "finished_model"):
        assert TERMS[term_id].link_de.startswith(REPO_DOCS) and "_DE.md" in TERMS[term_id].link_de, term_id


def test_explanation_and_link_follow_the_language(form):
    assert glossary.explanation("gguf", "en").startswith("The file format llama.cpp uses")
    assert glossary.explanation("gguf", "de").startswith("Das Dateiformat, das llama.cpp")
    assert glossary.link("npu", "de") == "https://de.wikipedia.org/wiki/KI-Beschleuniger"
    assert glossary.link("npu", "en") == "https://en.wikipedia.org/wiki/Neural_processing_unit"
    assert glossary.link("gguf", "de") == glossary.link("gguf", "en"), "no German page: the best page in English"
    assert "du wählst" in glossary.explanation("target", "de")
    form("sie")
    assert "Sie wählen" in glossary.explanation("target", "de")
    assert glossary.explanation("target", "en") == TEXTS["en"]["term.target"], "English has one form"
    with pytest.raises(KeyError, match="no term 'gguff'"):
        glossary.explanation("gguff")


def test_source_label_names_where_a_link_goes():
    assert glossary.source_label("https://en.wikipedia.org/wiki/SHA-2") == "en.wikipedia.org"
    assert glossary.source_label("https://www.ssh.com/academy/ssh/host-key") == "ssh.com"
    assert glossary.source_label(TERMS["llama_server"].link) == "github.com/ggml-org/llama.cpp"
    assert glossary.source_label(TERMS["qdrant"].link) == "qdrant.tech"
    assert glossary.source_label(TERMS["target"].link) == "github.com/Smilez1985/llm_conversion_framework"


def test_find_terms_marks_whole_words_and_skips_ambiguous_ones():
    text = "Build a GGUF with an importance matrix (imatrix); the KV cache needs memory. IQ2_XS too."
    assert glossary.find_terms(text) == ["gguf", "imatrix", "kv_cache", "iq_types"]
    assert glossary.find_terms("Confirm the SSH host key of the board") == ["ssh_host_key"]
    assert glossary.find_terms("Connect over SSH, then Qdrant") == ["ssh", "qdrant"]
    # words that also mean other things are explained only where a window asks for them by id
    assert glossary.find_terms("Token, Port, Board, Target, Format, Backend, Pack, Signature") == []
    assert glossary.find_terms("storage and brag") == [], "never inside a word"
    assert glossary.find_terms("") == []


# --- the German form of address -------------------------------------------------------------------------------------

def test_set_form_of_address_takes_only_du_or_sie(form):
    assert localization.form_of_address() == "du"
    assert form("SIE") == "sie" and localization.form_of_address() == "sie"
    assert form("Ihr") == "du" and form(None) == "du" and form(" sie ") == "sie"


def test_ui_text_reads_the_formal_overlay_only_for_german_with_sie(form):
    key = "words.card.more_tip"
    assert localization.ui_text(key, "de", url="u") == "Öffnet u in deinem Webbrowser."
    form("sie")
    assert localization.ui_text(key, "de", url="u") == "Öffnet u in Ihrem Webbrowser."
    assert localization.ui_text(key, "en", url="u") == "Opens u in your web browser."
    assert localization.ui_text("words.size.bytes", "de", n=3) == "3 Byte", "a text without overlay: its du text"
    manager = localization.LocalizationManager("de")
    assert manager.get_text(key) == "Öffnet {url} in Ihrem Webbrowser."
    assert localization.text_tables("du")["de"][key] == "Öffnet {url} in deinem Webbrowser."


def _module(monkeypatch, name, texts, formal=None):
    module = types.ModuleType(name)
    module.TEXTS = texts
    if formal is not None:
        module.FORMAL = formal
    monkeypatch.setitem(sys.modules, name, module)
    return name


def test_merge_takes_formal_overlays_of_du_texts_only(monkeypatch):
    table = {"en": {"a.x": "Hello"}, "de": {"a.x": "Hallo du"}}
    formal = {}
    good = _module(monkeypatch, "lcf_formal_good", {"en": {"b.y": "Open {name}"}, "de": {"b.y": "Öffne {name}"}},
                   {"de": {"b.y": "Öffnen Sie {name}", "a.x": "Hallo Sie"}})
    localization.merge_text_modules(table, (good, good), formal)   # named twice: merged once
    assert formal == {"de": {"b.y": "Öffnen Sie {name}", "a.x": "Hallo Sie"}}
    assert table["de"]["b.y"] == "Öffne {name}", "the du table keeps the du text"
    cases = (({"de": {"c.missing": "Sie"}}, "has no du text"), ({"de": {"a.x": "Hallo {name}"}}, "placeholders"),
             ({"fr": {"a.x": "Salut"}}, "unknown language"), ({"de": {"a.x": 3}}, "is not text"))
    for i, (overlay, message) in enumerate(cases):
        bad = _module(monkeypatch, f"lcf_formal_bad{i}", {"en": {f"n{i}": "x"}, "de": {f"n{i}": "x"}}, overlay)
        with pytest.raises(ValueError, match=message):
            localization.merge_text_modules(table, (bad,), {})
        assert f"n{i}" not in table["en"], "nothing of a refused module is merged"
    again = _module(monkeypatch, "lcf_formal_again", {"en": {"d.z": "x"}, "de": {"d.z": "x"}},
                    {"de": {"a.x": "Guten Tag"}})
    with pytest.raises(ValueError, match="exists already"):
        localization.merge_text_modules(table, (again,), formal)


def test_formal_overlays_name_du_texts_with_the_same_placeholders():
    """Always on: every FORMAL overlay of every text module names a German du text with the same placeholders."""
    table = localization.LocalizationManager.TRANSLATIONS
    found = 0
    for name in localization.TEXT_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        for key, text in getattr(module, "FORMAL", {}).get("de", {}).items():
            du = module.TEXTS.get("de", {}).get(key, table["de"].get(key))
            assert du is not None, (name, key)
            assert localization.placeholders(text) == localization.placeholders(du), (name, key)
            assert localization.FORMAL_TEXTS["de"][key] == text, (name, key)
            assert not wording.informal_forms(text), (name, key, wording.informal_forms(text))
            found += 1
    assert found >= len(FORMAL["de"])


def test_the_glossary_module_has_the_sie_form_of_every_text_that_says_du():
    for key, text in TEXTS["de"].items():
        if wording.informal_forms(text) and key not in NAMES_THE_FORM:
            assert key in FORMAL["de"], key
        assert not wording.formal_forms(text), (key, wording.formal_forms(text))
    for key, text in FORMAL["de"].items():
        assert text != TEXTS["de"][key] and wording.formal_forms(text), key


def test_every_german_text_has_its_formal_form():
    """Every German text that says du has its Sie form. Skipped until the 'formal' chain gives every text module a
    FORMAL table (the legacy table's overlay included, in a module of its own); then it runs by itself."""
    modules = []
    for name in localization.TEXT_MODULES:
        try:
            modules.append(importlib.import_module(name))
        except ModuleNotFoundError:
            continue
    without = [m.__name__ for m in modules if not hasattr(m, "FORMAL")]
    if without:
        pytest.skip(f"the 'formal' chain writes the Sie overlays; still without FORMAL: {len(without)} modules")
    missing = [key for key, text in localization.LocalizationManager.TRANSLATIONS["de"].items()
               if wording.informal_forms(text) and key not in localization.FORMAL_TEXTS["de"]
               and key not in NAMES_THE_FORM]
    assert missing == []


# --- sizes ----------------------------------------------------------------------------------------------------------

def test_sizes_in_english_and_german():
    f = sizes.format_size
    assert f(0, "en") == "0 bytes" and f(532, "de") == "532 Byte"
    assert f(1_500, "en") == "2 kB" and f(12_345, "de") == "12 kB"
    assert f(1_500_000, "en") == "1.5 MB" and f(1_500_000, "de") == "1,5 MB"
    assert f(1_500_000_000, "en") == "1.5 GB" and f(1_500_000_000, "de") == "1,5 GB"
    assert f(2_500_000_000_000, "de") == "2,5 TB"
    assert f(1_234_000_000_000_000, "en") == "1,234.0 TB" and f(1_234_000_000_000_000, "de") == "1.234,0 TB"
    assert f(999_999, "en") == "1.0 MB", "rounding never shows 1000 kB"
    assert f(-5, "en") == "0 bytes" and f(None, "en") == "0 bytes"
    assert f(2_048_000, "en", exact=True) == "2.0 MB (2,048,000 bytes)"
    assert f(2_048_000, "de", exact=True) == "2,0 MB (2.048.000 Byte)"


def test_deletion_lists_keep_their_english_sizes():
    from orchestrator.Core.deletion import size_text
    assert [size_text(n) for n in (500, 1_500, 2_000_000, 3_500_000_000)] == ["500 bytes", "2 kB", "2.0 MB",
                                                                               "3.5 GB"]
    assert size_text(3_500_000_000, "de") == "3,5 GB"


# --- wording lint helpers -------------------------------------------------------------------------------------------

def test_lint_helpers_find_what_they_should():
    assert wording.ellipsis_problems("Settings...") == []
    assert len(wording.ellipsis_problems("Settings … and more ...")) == 2
    assert wording.informal_forms("Wähle dein Board, dann siehst du es.") == ["dein", "du"]
    assert wording.informal_forms("Die Dualität dirigiert") == []
    assert wording.formal_forms("Wählen Sie Ihr Board.") == ["Ihr", "Sie"]
    assert wording.formal_forms("Sie läuft schon. Das Board: Sie ist bereit.") == [], "she/they at a sentence start"
    assert wording.synonym_problems("The board is gekoppelt", "de") == ["'gekoppelt': say 'vertrautes Board'"]
    assert wording.synonym_problems("a golden artifact", "en") == ["'golden artifact': say 'finished model'"]
    assert wording.synonym_problems("a finished model", "en") == []
    assert wording.menu_path_problems("Open Tools > Mojo Health Check... now") == ["Tools > Mojo Health Check..."]
    assert wording.menu_path_problems("Open {path:act_mojo} now; 3 > 2") == []
    assert all(entry.en and entry.de for entry in CANONICAL)


def test_technical_details_hide_secrets_and_the_home_folder():
    token = "hf_" + "Q7x" * 12   # the shape of a Hugging Face token, made up
    log = str(Path.home() / "builds" / "run.log")
    shown = wording.technical_details(f"Traceback: failed with {token} in {log}")
    assert token not in shown and str(Path.home()) not in shown
    assert "run.log" in shown and "~" in shown and shown.startswith("Traceback: failed with ")
    assert wording.technical_details(None) == ""
