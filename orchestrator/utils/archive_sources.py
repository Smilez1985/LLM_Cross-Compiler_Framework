#!/usr/bin/env python3
"""
Ditto's archive: what counts as a source (DESIGN, Ditto, the archive's rows).

Independent sources are sources of different registrable domains: a host name without its sub-domains, with the small
table of two-part public suffixes of `configs/archive_sources.yml` (co.uk, com.cn, ...). Mirrors, sub-domains and
several pages of one domain count once. A manufacturer's website is one whose registrable domain the same file lists for
a vendor; it counts as one source like any other. Standard library and PyYAML only; the table is data.
"""

import ipaddress
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urlsplit

import yaml

TABLE_FILE = Path(__file__).resolve().parents[2] / "configs" / "archive_sources.yml"
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
# Names that never belong to a public source, whatever their suffix.
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".invalid", ".test", ".example")


def load_table(path: Optional[Path] = None) -> Dict[str, Any]:
    """configs/archive_sources.yml as data (an empty table when it cannot be read: nothing is a vendor, no tool is
    listened to, and every two-label host is its own domain)."""
    try:
        data = yaml.safe_load(Path(path or TABLE_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def suffixes_of(table: Dict[str, Any]) -> Set[str]:
    return {str(s).lower() for s in (table.get("suffixes") or []) if isinstance(s, str)}


def listened_tools(table: Dict[str, Any]) -> Set[str]:
    return {str(t) for t in (table.get("listen") or []) if isinstance(t, str)}


def host_of(address: str) -> str:
    """The lower-case host of a public https address, or "" (not https, an address of a computer, a local name, an
    address with a login)."""
    try:
        parts = urlsplit(str(address))
        host = (parts.hostname or "").lower().rstrip(".")
        parts.port  # noqa: B018 - a port outside 1-65535 raises
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.username or parts.password or not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    if not _HOST.match(host) or host.endswith(_LOCAL_SUFFIXES):
        return ""
    return host


def registrable_domain(host: str, suffixes: Iterable[str] = ()) -> str:
    """The host without its sub-domains: the last two labels, or the last three below a two-part public suffix."""
    labels = str(host).lower().rstrip(".").split(".")
    if len(labels) < 2:
        return ""
    two = ".".join(labels[-2:])
    if len(labels) >= 3 and two in set(suffixes):
        return ".".join(labels[-3:])
    return two


def domains_of(evidence: Iterable[str], suffixes: Iterable[str] = ()) -> List[str]:
    """The different registrable domains of the evidence's https addresses (labels of builds and tests name none),
    sorted."""
    suffixes = set(suffixes)
    found = {registrable_domain(host_of(item), suffixes) for item in evidence}
    return sorted(d for d in found if d)


def independent_sources(evidence: Iterable[str], suffixes: Iterable[str] = ()) -> int:
    """How many independent sources the evidence names: its different registrable domains."""
    return len(domains_of(evidence, suffixes))


def vendor_of(address: str, table: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The vendor row whose website `address` is (by registrable domain), or None."""
    domain = registrable_domain(host_of(address), suffixes_of(table))
    if not domain:
        return None
    for row in table.get("vendors") or []:
        if isinstance(row, dict) and domain in {str(d).lower() for d in (row.get("domains") or [])}:
            return row
    return None
