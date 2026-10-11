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
    ("\\nhf_...") or a percent-encoded character ("%20sk-..."), where \\b alone would miss it. The literal comes
    first, so the regex engine can search for it quickly."""
    return start + r"(?:(?<!\w" + start + r")|(?<=\\[nrt]" + start + r")|(?<=%[0-9A-Fa-f]{2}" + start + r"))" + rest


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
    # as "disk-usage-monitoring-service".
    _rule("sk- key", r"sk-(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{20,}", "sk-REDACTED", scan=False),
    _rule("Hugging Face token", _key("hf_", r"[A-Za-z0-9]{20,}"), "hf_REDACTED"),
    _rule("GitHub token", _key("gh[pousr]_", r"[A-Za-z0-9]{36,}") + "|" + _key("github_pat_", r"[A-Za-z0-9_]{22,}"),
          "gh-REDACTED"),
    _rule("GitLab token", _key("glpat-", r"[A-Za-z0-9_\-]{20,}")),
    _rule("AWS access key id", _key("A[KS]IA", r"[A-Z0-9]{16}\b")),
    _rule("AWS secret key", r"(?i)aws_secret_access_key['\"]?\s*[:=]\s*['\"]?(?P<secret>[A-Za-z0-9/+]{40})\b",
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
          r"(?i)authorization['\"]?\s*[:=]\s*['\"]?bearer(?:\s+|%20)(?P<secret>[A-Za-z0-9._~+/\-]{20,}=*)",
          hints=("authorization",)),
    _rule("secret assignment",
          r"(?i)(?:api[_-]?key|secret|token)['\"]?\s*[:=]\s*['\"](?P<secret>[A-Za-z0-9_\-]{24,})['\"]",
          hints=("key", "secret", "token")),
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


# "Name: value" (header line, also inside quotes), 'name': 'value' (dict, JSON) and ('name', 'value') (tuples,
# also bytes). An unquoted value runs to the end of the line, a quote, a closing bracket or an "&".
_HEADER = re.compile(
    r"(?i)(?<![\w-])(?:" + _alternatives(SENSITIVE_HEADERS) + r")"
    r"(?:['\"]?[ \t]*[:=][ \t]*(?:(?-i:b)?(?P<q1>['\"])(?P<v1>.*?)(?P=q1)|(?P<v2>[^\s'\",)}\]&][^\r\n'\",)}\]&]*))"
    r"|['\"][ \t]*,[ \t]*(?-i:b)?(?P<q2>['\"])(?P<v3>.*?)(?P=q2))")
_QUERY = re.compile(r"(?i)([?&#;](?:" + _alternatives(SENSITIVE_QUERY_PARAMETERS) + r")=)[^&#\s'\"<>]+")
# A URL with an id_token_hint (an OpenID reauthorization link) loses its whole query.
_TOKEN_HINT_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s?#'\"<>]*)\?[^\s'\"<>]*\bid_token_hint=[^\s'\"<>]*")


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
        if m.group(group) is not None:
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
        text = _TOKEN_HINT_URL.sub(r"\1?REDACTED", text)
    if any(name in lowered for name in SENSITIVE_HEADERS):
        text = _HEADER.sub(_header_value, text)
    return _QUERY.sub(r"\1REDACTED", text)
