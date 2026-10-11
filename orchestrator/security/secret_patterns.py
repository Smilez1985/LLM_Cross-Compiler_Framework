#!/usr/bin/env python3
"""
The one table of secret formats. scripts/secret_scan.py loads this file by path to stop credentials before
they are committed, and runtime redaction (orchestrator.utils.helpers.redact_secrets, the logging filter)
imports it, so the two cannot drift apart. Standard library only, and nothing from orchestrator: the scan runs
from a bare checkout and in git hooks.

- find_secrets(line): what the repository scan reports (rules with scan=True; placeholders are skipped).
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
    would miss it. The literal comes first, so the regex engine can search for it quickly."""
    escapes = (r"\\[nrt]", r"\\x[0-9A-Fa-f]{2}", r"\\u[0-9A-Fa-f]{4}", r"%[0-9A-Fa-f]{2}")
    return start + r"(?:(?<!\w" + start + ")" + "".join("|(?<=" + e + start + ")" for e in escapes) + ")" + rest


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
    _rule("Hugging Face token", _key("hf_", r"[A-Za-z0-9]{20,}"), "hf_REDACTED"),
    _rule("GitHub token", _key("gh[pousr]_", r"[A-Za-z0-9]{36,}") + "|" + _key("github_pat_", r"[A-Za-z0-9_]{22,}"),
          "gh-REDACTED"),
    _rule("GitLab token", _key("glpat-", r"[A-Za-z0-9_\-]{20,}")),
    _rule("AWS access key id", _key("A[KS]IA", r"[A-Z0-9]{16}\b")),
    _rule("AWS secret key",
          r"(?i)aws_secret_access_key" + _Q + r"?\s*[:=]\s*" + _Q + r"?(?P<secret>[A-Za-z0-9/+]{40})\b",
          hints=("aws_secret_access_key",)),
    _rule("Bedrock API key", _key("ABSK", r"[A-Za-z0-9+/=]{60,}")),
    _rule("Bedrock short-term key", _key("bedrock-api-key-", r"[A-Za-z0-9+/]{40,}={0,2}(?:&Version=\d+)?")),
    _rule("Google API key", _key("AIza", r"[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])"), "AIza-REDACTED"),
    _rule("Google OAuth secret", _key("GOCSPX-", r"[A-Za-z0-9_\-]{20,}")),
    _rule("Groq key", _key("gsk_", r"[A-Za-z0-9]{40,}")),
    _rule("xAI key", _key("xai-", r"[A-Za-z0-9]{40,}")),
    _rule("Perplexity key", _key("pplx-", r"[A-Za-z0-9]{40,}")),
    _rule("NVIDIA key", _key("nvapi-", r"[A-Za-z0-9_\-]{40,}")),
    _rule("Cerebras key", _key("csk-", r"[a-z0-9]{40,}")),
    _rule("Replicate token", _key("r8_", r"[A-Za-z0-9]{30,}")),
    _rule("Fireworks key", _key("fw_", r"[A-Za-z0-9]{20,}")),
    _rule("LocalAI key", _key("lai-", r"[A-Za-z0-9]{24,}")),
    _rule("Slack token", _key("xox[abprs]-", r"[A-Za-z0-9\-]{10,}")),
    _rule("Stripe key", _key("[rs]k_live_", r"[A-Za-z0-9]{20,}")),
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
