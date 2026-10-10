#!/usr/bin/env python3
"""
Data-leak protection for cloud providers (config key ai_security_level).

  OFF       only secrets are redacted (always, and irreversibly)
  BALANCED  + IPv4/IPv6 addresses, MAC addresses, e-mail addresses, user@host logins, user names in home paths
  STRICT    + host names announced in probe files (HOSTNAME=...), serial numbers, UUIDs, directories of absolute paths

Placeholders are derived from an HMAC of the value with a per-install key: the same value always gets the same
placeholder, so a re-sent history stays byte-identical (prompt caches, preserved thinking) and nothing has to be
remembered between calls. The returned mapping turns placeholders in the answer back into the real values locally.
"""

import hashlib
import hmac
import ipaddress
import re
import secrets
from typing import Callable, Dict, List, Optional, Tuple

from orchestrator.utils.helpers import redact_secrets

LEVELS = ("STRICT", "BALANCED", "OFF")

_PROCESS_KEY = secrets.token_bytes(32)  # used when the caller has no per-install key

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
_LOGIN = re.compile(r"(?<![\w.@-])([a-z_][a-z0-9_.-]{0,31})@"
                    r"([A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]|[A-Za-z0-9])(?![\w-])")
_MAC = re.compile(r"(?<![\w:.-])(?:[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}|"
                  r"[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4})(?![\w:.-])")
_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w]|\.\d)")
_IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f]{1,4})?"
                   r"(?:%[\w-]+)?(?![\w:]|\.\d)")
_HOME = re.compile(r"(?:(?<![\w.])/home/|(?<![\w.])/Users/|\b[A-Za-z]:\\Users\\|\b[A-Za-z]:/Users/)([^/\\\s\"'<>|:]+)")
_HOSTNAME_DECL = re.compile(r"^[ \t]*(?:export[ \t]+)?(?:HOSTNAME|hostname|Hostname|NODENAME|nodename|"
                            r"Static hostname|Transient hostname)[ \t]*[=:][ \t]*[\"']?"
                            r"([A-Za-z0-9][A-Za-z0-9.-]{0,252})",
                            re.MULTILINE)
_SERIAL = re.compile(r"(?i)\b(?:serial(?:[ _-]?(?:number|no\.?|num))?|serialno|ro\.serialno|ro\.boot\.serialno|"
                     r"machine[-_]id|board[-_]serial|product[-_]serial|chassis[-_]serial)\b[ \t]*[=:][ \t]*[\"']?"
                     r"([A-Za-z0-9][A-Za-z0-9-]{3,63})")
_UUID = re.compile(r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
                   r"(?![0-9A-Fa-f-])")
_POSIX_PATH = re.compile(r"(?<![\w.:/~<>-])/(?:[\w.@+-]+/)+[\w.@+-]*")
_WIN_PATH = re.compile(r"\b[A-Za-z]:\\(?:[^\\/:*?\"<>|\s]+\\)+[^\\/:*?\"<>|\s]*")
_PLACEHOLDER = re.compile(r"<(?:IP|MAC|EMAIL|USER|HOST|SERIAL|UUID|PATH)_[0-9a-f]{8}>")

# Directories that say nothing about the user; paths below them stay readable in STRICT mode.
_SYSTEM_ROOTS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib32/", "/lib64/", "/libx32/", "/etc/", "/dev/", "/proc/",
                 "/sys/", "/run/", "/var/lib/", "/var/log/", "/opt/rocm/", "/workspace/")


def normalize_level(level: Optional[str]) -> str:
    value = str(level or "STRICT").strip().upper()
    return value if value in LEVELS else "STRICT"


class _Span:
    __slots__ = ("start", "end", "kind", "value")

    def __init__(self, start: int, end: int, kind: str, value: str):
        self.start, self.end, self.kind, self.value = start, end, kind, value


class PrivacyFilter:
    """Replaces personal data by stable placeholders. One instance can serve many calls."""

    def __init__(self, level: str = "STRICT", key: Optional[bytes] = None):
        self.level = normalize_level(level)
        self._key = key or _PROCESS_KEY

    def placeholder(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, f"{kind}\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:8]
        return f"<{kind}_{digest}>"

    def sanitize(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Returns (clean_text, {placeholder: original})."""
        text = redact_secrets(text or "")
        if self.level == "OFF" or not text:
            return text, {}
        spans: List[_Span] = []
        for detector in self._detectors():
            spans.extend(detector(text))
        mapping: Dict[str, str] = {}
        out, pos = [], 0
        # Earliest first; on overlap the longer span wins (a whole path beats the user name inside it).
        for span in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
            if span.start < pos:
                continue
            token = self.placeholder(span.kind, span.value)
            mapping[token] = span.value
            out.append(text[pos:span.start])
            out.append(token)
            pos = span.end
        out.append(text[pos:])
        return "".join(out), mapping

    def _detectors(self) -> List[Callable[[str], List[_Span]]]:
        detectors = [_find_emails, _find_logins, _find_macs, _find_ipv4, _find_ipv6, _find_home_users]
        if self.level == "STRICT":
            detectors += [_find_hostnames, _find_serials, _find_uuids, _find_path_dirs]
        return detectors


def _protected(text: str, start: int, end: int) -> bool:
    """True inside an existing placeholder, so re-sanitizing sanitized text changes nothing."""
    for m in _PLACEHOLDER.finditer(text, max(0, start - 16), min(len(text), end + 16)):
        if m.start() < end and start < m.end():
            return True
    return False


def _spans(pattern: re.Pattern, text: str, kind: str, group: int = 0,
           accept: Optional[Callable[[str], bool]] = None) -> List[_Span]:
    found = []
    for m in pattern.finditer(text):
        value = m.group(group)
        if not value or (accept and not accept(value)) or _protected(text, m.start(group), m.end(group)):
            continue
        found.append(_Span(m.start(group), m.end(group), kind, value))
    return found


def _find_emails(text: str) -> List[_Span]:
    return _spans(_EMAIL, text, "EMAIL")


def _find_logins(text: str) -> List[_Span]:
    found = []
    for m in _LOGIN.finditer(text):
        user, host = m.group(1), m.group(2)
        if _EMAIL.fullmatch(m.group(0)) or _protected(text, m.start(), m.end()):
            continue  # e-mail addresses are handled as a whole
        found.append(_Span(m.start(1), m.end(1), "USER", user))
        found.append(_Span(m.start(2), m.end(2), "IP" if _is_reportable_ip(host) else "HOST", host))
    return found


def _find_macs(text: str) -> List[_Span]:
    return _spans(_MAC, text, "MAC", accept=lambda v: re.sub(r"[:.-]", "", v).strip("0") != "")


def _is_reportable_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_unspecified)


def _find_ipv4(text: str) -> List[_Span]:
    return _spans(_IPV4, text, "IP", accept=_is_reportable_ip)


def _find_ipv6(text: str) -> List[_Span]:
    def accept(value: str) -> bool:
        if value.count(":") < 2 or not _is_reportable_ip(value):
            return False
        return ipaddress.ip_address(value.split("%", 1)[0]).version == 6
    return _spans(_IPV6, text, "IP", accept=accept)


def _find_home_users(text: str) -> List[_Span]:
    return _spans(_HOME, text, "USER", group=1)


def _find_hostnames(text: str) -> List[_Span]:
    names = {m.group(1).rstrip(".") for m in _HOSTNAME_DECL.finditer(text)}
    names = {n for n in names if n.lower() not in ("localhost", "localhost.localdomain") and not n.isdigit()}
    found = []
    for name in sorted(names, key=len, reverse=True):
        pattern = re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-]|\.\w)")
        found.extend(_spans(pattern, text, "HOST"))
    return found


def _find_serials(text: str) -> List[_Span]:
    return _spans(_SERIAL, text, "SERIAL", group=1)


def _find_uuids(text: str) -> List[_Span]:
    return _spans(_UUID, text, "UUID")


def _find_path_dirs(text: str) -> List[_Span]:
    """The directory part of absolute paths; the file name stays (it carries the useful extension)."""
    found = []
    for pattern, sep in ((_POSIX_PATH, "/"), (_WIN_PATH, "\\")):
        for m in pattern.finditer(text):
            path = m.group(0)
            if sep == "/" and path.startswith(_SYSTEM_ROOTS):
                continue
            cut = path.rfind(sep)
            directory = path[:cut] if cut > 0 else ""
            if not directory or _protected(text, m.start(), m.start() + len(directory)):
                continue
            found.append(_Span(m.start(), m.start() + len(directory), "PATH", directory))
    return found


def sanitize_for_cloud(text: str, level: str = "STRICT", key: Optional[bytes] = None) -> Tuple[str, Dict[str, str]]:
    """Cleans text before it goes to a cloud provider. Returns (clean_text, {placeholder: original})."""
    return PrivacyFilter(level, key).sanitize(text)


def restore(text: str, mapping: Dict[str, str]) -> str:
    """Puts the original values back into a model answer (locally, never sent anywhere)."""
    if not mapping or not text:
        return text
    return _PLACEHOLDER.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def restore_obj(value, mapping: Dict[str, str]):
    """restore() for every string inside dicts/lists (tool arguments, parsed JSON)."""
    if not mapping:
        return value
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, list):
        return [restore_obj(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: restore_obj(v, mapping) for k, v in value.items()}
    return value
