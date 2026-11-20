"""Secrets that only their place gives away, not their shape (orchestrator/security/secret_patterns.py).

The first run (2026-11-20) put 15 such samples through the prompt filter and the pack scrub, and 12 got through: a
flag of a command, a password key without quotes, a password handed to a program. These tests keep each of them
out of the text that leaves, and keep the words that look like them but name no secret. Every value is made at
run time from random characters, so this file holds no secret and no secret-shaped literal.
"""

import base64
import random
import string

import pytest

from orchestrator.Core.community_pack import scrub
from orchestrator.llm.privacy import PrivacyFilter
from orchestrator.security import secret_patterns

ALNUM = string.ascii_letters + string.digits


def value(seed, n=12):
    return "".join(random.Random(seed).choices(ALNUM, k=n))


# (label, template with {s} for the secret) - every one of them is a secret by its place
BY_PLACE = [
    ("yaml password, unquoted", "password: {s}"),
    ("yaml password with a prefix", "db_password: {s}"),
    ("properties password", "app.password={s}"),
    ("yaml token, unquoted", "api_token: {s}"),
    ("json password, short", '{{"password": "{s}"}}'),
    ("json escaped inside a string", '{{"cmd": "{{\\"password\\": \\"{s}\\"}}"}}'),
    ("sshpass -p", "sshpass -p {s} ssh dev@board.invalid"),
    ("sshpass -p quoted", "sshpass -p '{s}' ssh dev@board.invalid"),
    ("--password flag", "tool login --user dev --password {s}"),
    ("--password=value", "tool login --password={s}"),
    ("--token flag", "tool push --token {s}"),
    ("--passphrase flag", "gpg --batch --passphrase {s} -c file"),
    ("mysql -pSECRET", "mysql -u root -p{s} -h db"),
    ("curl -u user:pass", "curl -u dev:{s} https://x.invalid/api"),
    ("curl --user", "curl --user dev:{s} https://x.invalid/api"),
    ("netrc password", "machine host.invalid login dev password {s}"),
    ("SSHPASS variable", "SSHPASS={s} sshpass -e ssh dev@board"),
    ("PGPASSWORD variable", "PGPASSWORD={s} psql -h db"),
    ("chpasswd", "echo 'dev:{s}' | chpasswd"),
    ("sudo -S", "echo {s} | sudo -S apt-get update"),
    ("wpa_passphrase", 'wpa_passphrase "Net" {s}'),
    ("wpa_supplicant psk", '  psk="{s}"'),
    ("openssl -passin", "openssl rsa -in k.pem -passin pass:{s}"),
    ("docker login -p", "docker login -u dev -p {s} registry.invalid"),
]


@pytest.mark.parametrize("label,template", BY_PLACE, ids=[label for label, _ in BY_PLACE])
def test_a_secret_by_its_place_is_redacted(label, template):
    secret = value(label)
    text = template.format(s=secret)
    assert secret not in secret_patterns.redact(text), label
    # the two ways out of the computer that the first run measured
    assert secret not in PrivacyFilter("STRICT", b"k" * 32).sanitize(text)[0], label
    assert secret not in scrub(text), label


def test_a_basic_authorization_header_stays_redacted():
    secret = value("basic")
    header = base64.b64encode(f"dev:{secret}".encode()).decode()
    assert header not in secret_patterns.redact(f"Authorization: Basic {header}")


def test_the_words_around_a_secret_stay_so_that_the_text_can_be_read():
    out = secret_patterns.redact("tool login --user dev --password " + value("w"))
    assert out.startswith("tool login --user dev --password ") and out.endswith("REDACTED")
    out = secret_patterns.redact("password: " + value("y"))
    assert out == "password: REDACTED"


# Text that looks like a secret's place and names none: it must come through unchanged.
HARMLESS = [
    "tool login --password $DB_PASSWORD",
    "tool login --password ${DB_PASSWORD}",
    "tool login --password <your-password>",
    "tool login --password-stdin",
    "tool push --token-file /run/secrets/t",
    "tool push --token $TOKEN",
    "password: required",
    "password: null",
    "password: ",
    "password: $DB_PASSWORD",
    "password: <set it in the settings>",
    "max_token: 1024",
    "max_tokens: 4096",
    "token_count: 12",
    "pad_token: <|endoftext|>",
    "secret: required",
    "mysql -u root -p",
    "mysql -u root -h db",
    "sshpass -e ssh dev@board",
    "curl -u dev https://x.invalid/api",
    "curl -u dev:$PASS https://x.invalid/api",
    "the password manager asks for a password and a token",
    "SSHPASS=$SECRET_FROM_VAULT sshpass -e ssh dev@board",
    "docker login registry.invalid",
]


@pytest.mark.parametrize("text", HARMLESS)
def test_words_that_look_like_a_secret_but_are_none_stay(text):
    assert secret_patterns.redact(text) == text


def test_the_repository_scan_reports_none_of_these_rules():
    """They are runtime redaction only: documentation and tests are full of examples of these words."""
    names = {"password flag", "sshpass", "mysql password", "curl user", "netrc password", "password variable",
             "password key, quoted", "password key, unquoted", "key value, unquoted", "password on standard input",
             "sudo -S password", "wpa passphrase", "wpa psk", "openssl password", "docker login password"}
    found = {p.name: p for p in secret_patterns.PATTERNS if p.name in names}
    assert set(found) == names
    assert not any(p.scan for p in found.values())
    assert all(p.redact for p in found.values())
    assert secret_patterns.find_secrets("password: " + value("scan")) == []


def test_the_new_rules_stay_linear_on_a_long_hostile_line():
    # a long line of repeated trigger words must not make a rule backtrack
    import time
    hostile = ("curl mysql docker sshpass password token --password " * 4000) + "x" * 20000
    start = time.perf_counter()
    secret_patterns.redact(hostile)
    assert time.perf_counter() - start < 5.0
