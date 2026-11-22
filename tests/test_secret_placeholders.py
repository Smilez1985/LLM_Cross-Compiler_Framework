"""A secret in a prompt gets a placeholder that is put back only where the person looks (orchestrator/llm/privacy.py,
orchestrator/llm/client.py; docs/DESIGN.md, "A secret in a prompt gets a placeholder"): the request that leaves holds no
value, the text of the answer gets it back, and the history, a tool's arguments and the structured result keep the
placeholder.

Every value is made at run time from random characters, so this file holds no secret and no secret-shaped literal.
"""

import json

import pytest
from test_secret_by_place import BY_PLACE, value
from test_secret_scan import REDACT_ONLY_SAMPLES, SAMPLES

from orchestrator.Core.community_pack import scrub
from orchestrator.llm.privacy import PrivacyFilter, restore, restore_obj, sanitize_for_cloud, without_secrets
from orchestrator.security import secret_patterns

KEY = b"k" * 32
EVERY_KIND = {**{name: text for name, text in SAMPLES.items()}, **REDACT_ONLY_SAMPLES}
CONTEXTS = ("{s}", "config:\n  {s}\n", "$ {s}", "text before {s} text after")


# --- every kind of the pattern table ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(EVERY_KIND))
@pytest.mark.parametrize("context", CONTEXTS)
def test_each_kind_of_secret_gets_a_placeholder_and_comes_back_in_the_text(name, context):
    text = context.format(s=EVERY_KIND[name])
    if secret_patterns.redact(text) == text:
        pytest.skip("the runtime redaction does not hide this one in this context (the repository scan does)")
    clean, mapping = PrivacyFilter("OFF", KEY).sanitize(text)
    assert clean != text
    hidden = [v for k, v in mapping.items() if k.startswith("<SECRET_")]
    for part in hidden:
        assert part not in clean, (name, "a hidden value is in the request")
    if hidden and "REDACTED" not in clean:
        assert restore(clean, mapping) == text, (name, "the text does not come back as it was")
    elif hidden:
        # a header word that the safety net hides for good (Authorization: Bearer ...): the value itself comes back
        assert all(part in restore(clean, mapping) for part in hidden), name
    else:
        # no span for it (a credential header, a query parameter): hidden for good by the safety net
        assert clean == secret_patterns.redact(text) and "REDACTED" in clean, name


def test_the_secret_rules_that_matter_most_have_a_span():
    for name in ("password flag", "sshpass", "Bearer token", "environment variable", "sk- key"):
        text = "  " + EVERY_KIND[name]
        assert secret_patterns.secret_spans(text), name


@pytest.mark.parametrize("label,template", BY_PLACE, ids=[label for label, _ in BY_PLACE])
def test_a_secret_by_its_place_is_a_placeholder_and_comes_back(label, template):
    secret = value(label)
    text = template.format(s=secret)
    clean, mapping = PrivacyFilter("STRICT", KEY).sanitize(text)
    assert secret not in clean and "<SECRET_" in clean, label
    assert restore(clean, mapping) == text, label


# --- the placeholder ---------------------------------------------------------------------------------------------

def test_the_same_secret_gets_the_same_placeholder_and_another_key_another_one():
    text = "tool login --password " + value("same")
    a = PrivacyFilter("OFF", KEY).sanitize(text)[0]
    assert a == PrivacyFilter("OFF", KEY).sanitize("again " + text)[0].replace("again ", "")
    assert a != PrivacyFilter("OFF", b"z" * 32).sanitize(text)[0]


def test_sanitizing_twice_changes_nothing_and_a_resent_history_stays_identical():
    text = f"x\npassword: {value('twice')}\nsshpass -p {value('twice2')} ssh dev@h.invalid\n10.1.2.3"
    filt = PrivacyFilter("STRICT", KEY)
    once, mapping = filt.sanitize(text)
    twice, again = filt.sanitize(once)
    assert twice == once and not [k for k in again if k.startswith("<SECRET_")]
    assert filt.sanitize(text)[0] == once


def test_a_placeholder_survives_the_redaction_around_it():
    secret = value("around")
    text = f"password={secret} and then api_token: {value('around2')} and password={secret}"
    clean, mapping = PrivacyFilter("OFF", KEY).sanitize(text)
    assert clean.count("<SECRET_") == 3 and secret not in clean
    assert sanitize_for_cloud(clean, "OFF", KEY)[0] == clean


def test_a_pack_scrub_still_redacts_for_good():
    secret = value("pack")
    clean = scrub(f"password: {secret}\n")
    assert secret not in clean and "<SECRET_" not in clean
    plain, mapping = PrivacyFilter("BALANCED", KEY, reversible_secrets=False).sanitize(f"password: {secret}")
    assert secret not in plain and "<SECRET_" not in plain and mapping == {}


def test_what_has_no_span_is_still_redacted_for_good():
    token = value("header", 30)
    clean, mapping = PrivacyFilter("OFF", KEY).sanitize(f"Authorization: {token}\nCookie: session={token}")
    assert token not in clean and token not in mapping.values()


def test_without_secrets_leaves_the_personal_data_only():
    clean, mapping = PrivacyFilter("STRICT", KEY).sanitize(f"login dev@host.invalid password: {value('w')} at 10.1.2.3")
    kept = without_secrets(mapping)
    assert kept and all(not k.startswith("<SECRET_") for k in kept) and len(kept) < len(mapping)
    assert "<SECRET_" in restore(clean, kept) and "10.1.2.3" in restore(clean, kept)
    assert restore_obj({"a": [clean]}, kept)["a"][0].count("<SECRET_") == 1


def test_a_web_query_never_carries_a_secret_or_its_placeholder():
    from orchestrator.Core.web_search import prepare_query
    secret = value("query")
    query = prepare_query(f"how to log in with tool login --password {secret} on a board")
    assert secret not in query and "<SECRET_" not in query and "SECRET" not in query
