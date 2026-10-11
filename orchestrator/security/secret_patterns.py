#!/usr/bin/env python3
"""
The one table of secret formats. scripts/secret_scan.py loads this file by path to stop credentials before
they are committed, and runtime redaction (orchestrator.utils.helpers.redact_secrets, the logging filter)
imports it, so the two cannot drift apart. Standard library only, and nothing from orchestrator: the scan runs
from a bare checkout and in git hooks.

- find_secrets(line): what the repository scan reports (rules with scan=True; placeholders are skipped).
- find_exact_formats(line): values in the exact format of a token that GitHub push protection blocks, which the
  repository scan reports even when they look like placeholders (EXACT_FORMATS, scan only).
- redact(text): what runtime redaction blanks (rules with redact=True, plus credential headers and query
  parameters, which are too generic for the scan).

Tests build sample keys at runtime (string concatenation), so the repository never holds a matching literal.
"""

import re
from typing import List, NamedTuple, Pattern, Tuple


class SecretPattern(NamedTuple):
    name: str                 # what the scan reports
    regex: "Pattern[str]"
    replacement: str          # replaces the match, or only its group "secret" when the regex has one
    scan: bool = True         # used by the repository scan
    redact: bool = True       # used by runtime redaction
    hints: Tuple[str, ...] = ()  # lowercase; every match contains one, so text without any is skipped quickly


def _rule(name: str, regex: str, replacement: str = "REDACTED", *, scan: bool = True, redact: bool = True,
          hints: Tuple[str, ...] = ()) -> SecretPattern:
    return SecretPattern(name, re.compile(regex), replacement, scan, redact, hints)


def _key(start: str, rest: str) -> str:
    """A key beginning with `start` (a fixed-width regex) at the start of a word, or right after an escape
    ("\\nhf_...", "\\x00hf_...", "\\u00a0hf_...") or a percent-encoded character ("%20sk-..."), where \\b alone
    would miss it. The literal comes first, so the regex engine can search for it quickly. Word characters are
    ASCII ones, as for GitHub's detector: a key right after "é" or Chinese text ("令牌hf_...") is still a key."""
    escapes = (r"\\[nrt]", r"\\x[0-9A-Fa-f]{2}", r"\\u[0-9A-Fa-f]{4}", r"%[0-9A-Fa-f]{2}")
    return (start + r"(?:(?<![A-Za-z0-9_]" + start + ")" + "".join("|(?<=" + e + start + ")" for e in escapes) + ")"
            + rest)


# An optional quote, also escaped as in JSON inside a JSON string ("{\"api_key\": \"...\"}").
_Q = r"""(?:\\?['"])"""


_PRIVATE_KEY = r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"

# Order matters: a match inside an earlier one is not reported again, so specific formats come first and the
# generic rules (Authorization header, secret assignment) last.
PATTERNS: Tuple[SecretPattern, ...] = (
    _rule("Anthropic key", r"sk-ant-(?:api|admin|oat|ort)\d{2}-[A-Za-z0-9_\-]{20,}", "sk-REDACTED"),
    _rule("OpenAI key", r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}|" + _key("sk-", r"[A-Za-z0-9]{48}\b"),
          "sk-REDACTED"),
    _rule("OpenRouter key", r"sk-or-v1-[a-f0-9]{40,}", "sk-REDACTED"),
    _rule("Unsloth Studio key", _key("sk-unsloth-", r"[A-Za-z0-9]{24,}"), "sk-REDACTED"),
    # Other sk- keys (DeepSeek, Moonshot, ...): too broad for the scan. The digit lookahead spares words such
    # as "disk-usage-monitoring-service"; it is bounded, so a long run of "sk-sk-sk-..." stays linear.
    _rule("sk- key", r"sk-(?=[A-Za-z_\-]{0,64}\d)[A-Za-z0-9_\-]{20,}", "sk-REDACTED", scan=False),
    _rule("Hugging Face token",
          _key("hf_", r"[A-Za-z0-9]{20,}") + "|" + _key("api_org_", r"[A-Za-z]{34}(?![A-Za-z0-9])"), "hf_REDACTED"),
    _rule("GitHub token", _key("gh[pousr]_", r"[A-Za-z0-9]{36,}") + "|" + _key("github_pat_", r"[A-Za-z0-9_]{22,}"),
          "gh-REDACTED"),
    # With the version and checksum after the dot that a routable token carries.
    _rule("GitLab token", _key("glpat-", r"[A-Za-z0-9_\-]{20,}(?:\.[0-9a-z]{9}(?![A-Za-z0-9_\-]))?")),
    _rule("AWS access key id", _key("A[KS]IA", r"[A-Z0-9]{16}(?![A-Za-z0-9_])")),
    _rule("AWS secret key",
          r"(?i)aws_secret_access_key" + _Q + r"?\s*[:=]\s*" + _Q + r"?(?P<secret>[A-Za-z0-9/+]{40})\b",
          hints=("aws_secret_access_key",)),
    _rule("Bedrock API key", _key("ABSK", r"[A-Za-z0-9+/=]{60,}")),
    _rule("Bedrock short-term key", _key("bedrock-api-key-", r"[A-Za-z0-9+/]{40,}={0,2}(?:&Version=\d+)?")),
    _rule("Google API key", _key("AIza", r"[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])"), "AIza-REDACTED"),
    _rule("Google OAuth secret", _key("GOCSPX-", r"[A-Za-z0-9_\-]{20,}")),
    _rule("Groq key", _key("gsk_", r"[A-Za-z0-9]{40,}")),
    _rule("xAI key", _key("xai-", r"[A-Za-z0-9_]{40,}")),
    _rule("Perplexity key", _key("pplx-", r"[A-Za-z0-9]{40,}")),
    _rule("NVIDIA key", _key("nvapi-", r"[A-Za-z0-9_\-]{40,}")),
    _rule("Cerebras key", _key("csk-", r"[a-z0-9]{40,}")),
    _rule("Replicate token", _key("r8_", r"[A-Za-z0-9_\-]{30,}")),
    _rule("Fireworks key", _key("fw_", r"[A-Za-z0-9]{20,}")),
    _rule("LocalAI key", _key("lai-", r"[A-Za-z0-9]{24,}")),
    _rule("Slack token", _key("xox[abprs]-", r"[A-Za-z0-9\-]{10,}")),
    _rule("Stripe key", _key("[rs]k_", r"(?:live|test)_[A-Za-z0-9]{20,}")),
    _rule("PyPI token", _key("pypi-AgE", r"[A-Za-z0-9_\-]{50,}")),
    # An age identity (community packs, age-keygen, rage-keygen): bech32 of 32 bytes, upper case as the tools write
    # it, or lower case, which rage reads too (bech32 is never mixed case).
    _rule("age secret key", _key("AGE-SECRET-KEY-1", r"[02-9AC-HJ-NP-Z]{58}\b") + "|"
          + _key("age-secret-key-1", r"[02-9ac-hj-np-z]{58}\b")),
    _rule("JSON web token", _key("eyJ", r"[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    # The scan reads line by line and reports the header; redaction removes the whole block (or everything
    # after a header without its end).
    _rule("private key", _PRIVATE_KEY, redact=False),
    _rule("private key block", _PRIVATE_KEY + r"[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
          "REDACTED PRIVATE KEY", scan=False),
    _rule("certificate", r"-{5}BEGIN CERTIFICATE-{5}", redact=False),  # public, but no business in the repo
    _rule("URL with password",
          r"://(?<=[A-Za-z0-9+.\-]://)[^\s:/@'\"\\]+:(?P<secret>[^\s/@'\"\\]{6,})@[A-Za-z0-9.\-]+"),
    _rule("Authorization header",
          r"(?i)authorization" + _Q + r"?\s*[:=]\s*" + _Q
          + r"?bearer(?:\s+|%20)(?P<secret>[A-Za-z0-9._~+/\-]{20,}=*)",
          hints=("authorization",)),
    # Runtime only: a bearer token without its header name, e.g. quoted in a server's error message.
    _rule("Bearer token", r"(?i)\bbearer(?:\s+|%20|\+)(?P<secret>[A-Za-z0-9._~+/\-]{20,}=*)", scan=False,
          hints=("bearer",)),
    # The value needs a digit or a capital: a lower-case identifier is the name of a secret
    # (AGE_SECRET = "community_age_identity"), and a random 24-character key lacks both almost never.
    _rule("secret assignment",
          r"(?i)(?:api[_-]?key|secret|token)" + _Q + r"?\s*[:=]\s*\\?['\"]"
          r"(?P<secret>(?-i:(?=[a-z_\-]*[A-Z0-9]))[A-Za-z0-9_\-]{24,})\\?['\"]",
          hints=("key", "secret", "token")),
    # Runtime only, too broad for the scan: quoted token-shaped values (OAuth access tokens with dots, base64
    # secrets), and environment-style assignments (env dumps, `docker run -e`, .env lines, shell exports).
    # Neither takes <, >, | or brackets, so tokenizer settings in build logs ("pad_token": "<|...|>") stay.
    _rule("quoted secret",
          r"(?i)(?:api[_-]?key|secret|token|password|passwd)" + _Q
          + r"?\s*[:=]\s*\\?['\"](?P<secret>[A-Za-z0-9._~+/\-]{20,}=*)\\?['\"]",
          scan=False, hints=("key", "secret", "token", "password", "passwd")),
    _rule("environment variable",
          r"(?<![A-Z0-9])(?:API_?KEY|ACCESS_KEY|TOKEN|SECRET|PASSWORD|PASSWD)(?:_[A-Z0-9]{1,32}){0,4}(?:=|:[ \t]*)"
          r"['\"]?(?P<secret>[^\s'\"<>|(){}\[\]]{8,})",
          scan=False, hints=("api_key", "apikey", "access_key", "token", "secret", "password", "passwd")),
)

# Obvious placeholders in documentation and tests: marker words, runs a random key never has (sequential digits
# or letters), and reserved example hosts (RFC 2606). Only the scan skips them; redaction blanks them too.
PLACEHOLDER = re.compile(
    r"EXAMPLE|PLACEHOLDER|REDACTED|DUMMY|FAKE|TESTKEY|x{8,}|X{8,}|0{12,}|\.\.\.|<[^>]*>"
    r"|123456789|abcdefghij|ABCDEFGHIJ|AbCdEf|aBcDeF"
    r"|@(?:[A-Za-z0-9\-]+\.)*(?:example|test|invalid|localhost)\b|@(?:[A-Za-z0-9\-]+\.)*example\.(?:com|org|net)\b")


class ExactFormat(NamedTuple):
    name: str                 # the rule of PATTERNS that also redacts such a value at runtime
    regex: "Pattern[str]"
    starts: Tuple[str, ...]   # regexes of the fixed-width prefixes; every match begins with one


def _exact(name: str, *alternatives: Tuple[str, str]) -> ExactFormat:
    """A format from (prefix, rest) alternatives, each a key that starts a word (see _key)."""
    return ExactFormat(name, re.compile("|".join(_key(start, rest) for start, rest in alternatives)),
                       tuple(start for start, _ in alternatives))


# The exact formats of tokens that GitHub push protection blocks. GitHub refuses a push with such a value however
# fake it looks ("hf_" and 34 letters of the alphabet) and does not know our "secret-scan: allow" marker, so the
# scan reports these matches whatever PLACEHOLDER and the marker say. Scan only: runtime redaction uses PATTERNS,
# and each format is a value its named rule there redacts (tests/test_secret_scan.py checks that).
# Which providers push protection covers: GitHub's list of supported patterns,
#   https://docs.github.com/en/code-security/secret-scanning/introduction/supported-secret-scanning-patterns
# (push protection for hf_user_access_token, hf_org_api_key, the github_* tokens, aws_access_key_id,
# slack_api_token, gitlab_access_token, stripe_api_key, stripe_live/test_restricted_key, stripe_test_secret_key,
# pypi_api_token, openai_api_key, anthropic_api_key, anthropic_admin_api_key, openrouter_api_key, groq_api_key,
# xai_api_key, perplexity_api_key, replicate_api_token; not for Google API keys). GitHub does not publish its
# regular expressions; the formats come from the vendors where they document them, otherwise from the open
# scanners gitleaks (config/gitleaks.toml) and TruffleHog (pkg/detectors/<provider>), all checked 2026-10-11:
# - Prefixes: GitHub https://github.blog/engineering/platform-security/behind-githubs-new-authentication-token-formats/
#   (ghp_, gho_, ghu_, ghs_, ghr_), AWS https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_identifiers.html
#   (AKIA access key, ASIA temporary access key), Slack https://docs.slack.dev/authentication/tokens/, GitLab
#   https://docs.gitlab.com/security/tokens/ (glpat-), Stripe https://docs.stripe.com/keys (sk_/rk_, live/test).
# - PyPI documents the whole format, https://docs.pypi.org/api/secrets/: pypi-[A-Za-z0-9-_]{85,}. Real tokens are
#   macaroons that start with "AgE", which the PyPI rule above redacts.
# - Lengths: gitleaks for Hugging Face (hf_ or api_org_ and 34 letters), GitHub (36; github_pat_ 82), GitLab
#   (20, or the routable form), OpenAI (the full form with "T3BlbkFJ" inside) and Anthropic (93 characters and
#   "AA"); TruffleHog for Slack, Stripe (20 or more), OpenRouter (64 hex), Groq (52), xAI (80) and Replicate (37);
#   both for Perplexity (48).
# GitHub's list pairs the AWS access key id with its secret key; the scan reports the id alone, because the secret
# key may sit in another line or file. A match must not run on into letters, digits or other characters its format
# allows (one character too many is no longer the exact format).
EXACT_FORMATS: Tuple[ExactFormat, ...] = (
    _exact("Hugging Face token", ("hf_", r"[A-Za-z]{34}(?![A-Za-z0-9])"),
           ("api_org_", r"[A-Za-z]{34}(?![A-Za-z0-9])")),
    _exact("GitHub token", ("gh[pousr]_", r"[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
           ("github_pat_", r"[A-Za-z0-9_]{82}(?![A-Za-z0-9_])")),
    _exact("AWS access key id", ("A[KS]IA", r"[A-Z0-9]{16}(?![A-Za-z0-9_])")),
    _exact("Slack token", ("xox[abprs]-", r"[0-9]{10,13}-[0-9]{10,13}[A-Za-z0-9\-]*")),
    _exact("GitLab token", ("glpat-", r"[A-Za-z0-9_\-]{20}(?![A-Za-z0-9_\-])"),
           ("glpat-", r"[A-Za-z0-9_\-]{27,300}\.[0-9a-z]{9}(?![A-Za-z0-9_\-])")),
    _exact("Stripe key", ("[rs]k_", r"(?:live|test)_[A-Za-z0-9]{20,}")),
    _exact("PyPI token", ("pypi-", r"[A-Za-z0-9_\-]{85,}")),
    _exact("OpenAI key",
           ("sk-", r"(?:proj|svcacct|admin)-(?:[A-Za-z0-9_\-]{74}|[A-Za-z0-9_\-]{58})T3BlbkFJ"
                   r"(?:[A-Za-z0-9_\-]{74}|[A-Za-z0-9_\-]{58})(?![A-Za-z0-9_\-])"),
           ("sk-", r"[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}(?![A-Za-z0-9])")),
    _exact("Anthropic key", ("sk-ant-", r"(?:api03|admin01)-[A-Za-z0-9_\-]{93}AA(?![A-Za-z0-9_\-])")),
    _exact("OpenRouter key", ("sk-or-v1-", r"[0-9a-f]{64}(?![A-Za-z0-9])")),
    _exact("Groq key", ("gsk_", r"[A-Za-z0-9]{52}(?![A-Za-z0-9])")),
    _exact("xAI key", ("xai-", r"[A-Za-z0-9_]{80}(?![A-Za-z0-9_])")),
    _exact("Perplexity key", ("pplx-", r"[A-Za-z0-9]{48}(?![A-Za-z0-9])")),
    _exact("Replicate token", ("r8_", r"[A-Za-z0-9_\-]{37}(?![A-Za-z0-9_\-])")),
)
# Every match begins with one of the prefixes, so a line without any (nearly all of them) is done in one search.
_EXACT_START = re.compile("|".join(sorted({start for fmt in EXACT_FORMATS for start in fmt.starts})))

# Runtime only: values of credential headers and query parameters, whatever their format.
SENSITIVE_HEADERS = ("authorization", "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key",
                     "x-amz-security-token", "cookie", "set-cookie")
SENSITIVE_QUERY_PARAMETERS = ("code", "state", "key", "api_key", "apikey", "access_token", "refresh_token",
                              "id_token", "id_token_hint", "token", "client_secret", "code_verifier", "password",
                              "login_hint", "sig", "x-amz-security-token", "x-amz-signature")


def _alternatives(names: Tuple[str, ...]) -> str:
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


# "Name: value" (header line, also inside quotes), 'name': 'value' (dict, JSON, also with escaped quotes) and
# ('name', 'value') (tuples, also bytes). An unquoted value runs to the end of the line, a quote, a backslash
# (an escaped line break), a closing bracket or an "&".
_HEADER = re.compile(
    r"(?i)(?<![\w-])(?:" + _alternatives(SENSITIVE_HEADERS) + r")"
    r"(?:" + _Q + r"?[ \t]*[:=][ \t]*(?:(?-i:[bfru]{0,2})(?P<q1>\\?['\"])(?P<v1>.*?)(?P=q1)"
    r"|(?P<v2>[^\s'\",)}\]&\\][^\r\n'\",)}\]&\\]*))"
    r"|" + _Q + r"[ \t]*,[ \t]*(?-i:[bfru]{0,2})(?P<q2>\\?['\"])(?P<v3>.*?)(?P=q2))")
# In a URL, or at the start of a line as in a form-encoded body ("code=...&grant_type=authorization_code").
_QUERY = re.compile(r"(?im)((?:^|[?&#;])(?:" + _alternatives(SENSITIVE_QUERY_PARAMETERS) + r")=)[^&#\s'\"<>]+")
# A URL with an id_token_hint (an OpenID reauthorization link) loses its whole query. Found word by word, so
# the cost stays linear whatever the text.
_WORD = re.compile(r"[^\s'\"<>]+")
_SCHEME = re.compile(r"(?i)(?<![a-z0-9+.\-])[a-z][a-z0-9+.\-]{0,30}://")
_TOKEN_HINT = re.compile(r"(?i)\bid_token_hint=")


def _without_token_hint_query(m: "re.Match[str]") -> str:
    word = m.group(0)
    scheme = _SCHEME.search(word)
    query = word.find("?", scheme.end()) if scheme else -1
    if query < 0 or "#" in word[scheme.end():query] or not _TOKEN_HINT.search(word, query):
        return word
    return word[:query] + "?REDACTED"


def _may_match(rule: SecretPattern, lowered: str) -> bool:
    return not rule.hints or any(hint in lowered for hint in rule.hints)


def find_secrets(line: str) -> List[Tuple[str, str]]:
    """(rule name, matched text) for each credential the repository scan reports in one line. Placeholders are
    skipped, and a match that overlaps an earlier one is not reported twice."""
    found: List[Tuple[str, str]] = []
    spans: List[Tuple[int, int]] = []
    lowered = line.lower()
    for rule in PATTERNS:
        if not rule.scan or not _may_match(rule, lowered):
            continue
        for m in rule.regex.finditer(line):
            start, end = m.span()
            if PLACEHOLDER.search(m.group(0)) or any(start < e and s < end for s, e in spans):
                continue
            spans.append((start, end))
            found.append((rule.name, m.group(0)))
    return found


def find_exact_formats(line: str) -> List[Tuple[str, str]]:
    """(rule name, matched text) for each value in one line in the exact format of a token that GitHub push
    protection blocks, placeholder or not."""
    found: List[Tuple[str, str]] = []
    spans: List[Tuple[int, int]] = []
    if not _EXACT_START.search(line):
        return found
    for fmt in EXACT_FORMATS:
        for m in fmt.regex.finditer(line):
            start, end = m.span()
            if not any(start < e and s < end for s, e in spans):
                spans.append((start, end))
                found.append((fmt.name, m.group(0)))
    return found


def _replacer(rule: SecretPattern):
    if "secret" not in rule.regex.groupindex:
        return rule.replacement  # plain text, no group references

    def replace(m: "re.Match[str]") -> str:
        if m.group("secret") is None:
            return rule.replacement
        text, offset = m.group(0), m.start()
        return text[:m.start("secret") - offset] + rule.replacement + text[m.end("secret") - offset:]
    return replace


def _header_value(m: "re.Match[str]") -> str:
    for group in ("v1", "v2", "v3"):
        if m.group(group):  # an empty value hides nothing (headers.get("authorization", ""))
            text, offset = m.group(0), m.start()
            return text[:m.start(group) - offset] + "REDACTED" + text[m.end(group) - offset:]
    return m.group(0)


def redact(text: str) -> str:
    """text with every credential this module knows replaced: key formats, credential headers, query
    parameters."""
    lowered = text.lower()
    for rule in PATTERNS:
        if rule.redact and _may_match(rule, lowered):
            text = rule.regex.sub(_replacer(rule), text)
    if "id_token_hint" in lowered:
        text = _WORD.sub(_without_token_hint_query, text)
    if any(name in lowered for name in SENSITIVE_HEADERS):
        text = _HEADER.sub(_header_value, text)
    return _QUERY.sub(r"\1REDACTED", text)
