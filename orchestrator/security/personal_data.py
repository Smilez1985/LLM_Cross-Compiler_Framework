#!/usr/bin/env python3
"""
Personal data in text: host addresses, e-mail addresses, a person's home folder and the paths of an AI session's
sandbox. scripts/secret_scan.py loads this file by path and checks every line a commit adds (pre-commit and pre-push);
the blueprint's scrub test (tests/test_docs.py) checks the design, roadmap and changelog with it. Standard library
only, and nothing from orchestrator: the scan runs from a bare checkout and in git hooks.

Two levels of strictness:

- BLUEPRINT: the public blueprint names no private address at all. It passes only the documentation ranges, a few
  special addresses, the public resolvers the text names, and the default accounts of CI runners and board images;
  every e-mail address is reported.
- CODE: what a commit adds to code, tests and documents. Their examples pass: e-mail addresses at domains reserved
  for examples and the project's own addresses; IPv4 and IPv6 addresses in the documentation, private, loopback,
  link-local, shared and other non-routable ranges and a short list of well-known public examples; a dotted version
  number (after "==" or "v", or anywhere in a requirements or lock file); home folders whose account is one of the
  example names the repository's documents and tests use. The paths of an AI session's sandbox are always reported.

Private addresses and real account names cannot be told from examples by their shape: the maintainer's own ones
belong in the scan's local pattern file (.git/info/secret-patterns), never in the repository.

Prefixes that would themselves look like a finding are assembled from parts, so this file passes its own rules.
"""

import ipaddress
import re
from typing import Callable, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote

BLUEPRINT = "blueprint"
CODE = "code"
LEVELS = (BLUEPRINT, CODE)
# What the code level reports.
CODE_KINDS = ("address", "e-mail address", "home folder", "AI sandbox path")

# --- The blueprint level (the scrub test of the design, roadmap and changelog) ---------------------------------------

# Addresses that may appear: the documentation ranges (RFC 5737, RFC 3849) and the well-known special addresses; a
# range written with its prefix (10.0.0.0/8) names a class of addresses, not a host. Public resolvers are named
# where the text explains that the framework no longer contacts them.
DOC_NETWORKS = ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"]
WELL_KNOWN_ADDRESSES = {"8.8.8.8", "1.1.1.1"}
# Account names in home paths that are no person's: defaults of CI runners and board images, the framework's own
# account, and placeholders.
DEFAULT_ACCOUNTS = {"runner", "pi", "dietpi", "ubuntu", "llmcf", "...", "<name>", "<user>"}
# E-mail addresses the blueprint lists itself (none today).
PROJECT_EMAILS: Set[str] = set()
_V4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(/\d{1,2})?(?![\w.]*\d)")
_V6 = re.compile(r"(?<![\w:.])((?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4})(/\d{1,3})?(?![\w:])")
_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_HOME = re.compile(r"/home/([^/\s`)\]]+)|[A-Za-z]:\\\\?Users\\\\?([^\\\s`)\]]+)")

# --- The code level (every line a commit adds) -----------------------------------------------------------------------

# IPv4 and IPv6 networks whose addresses are examples or reach no host on the internet.
CODE_NETWORKS = [
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",  # documentation (RFC 5737)
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",       # private (RFC 1918)
    "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10",      # loopback, link-local, shared (RFC 6598)
    "0.0.0.0/8", "224.0.0.0/4", "255.255.255.255/32",      # unspecified, multicast, broadcast
    "198.18.0.0/15",                                       # benchmarking (RFC 2544)
    "2001:db8::/32", "::1/128", "::/128", "fe80::/10", "fc00::/7", "ff00::/8",  # IPv6: documentation, special
]
# Public addresses the repository uses as well-known examples: public DNS resolvers (Google, Cloudflare, Quad9), the
# addresses example.com resolved to (RFC 2606's example domain) and its neighbour the tests use as a second public
# host, the classic placeholder 1.2.3.4, and Azure's platform address (168.63.129.16), which a locality check names.
CODE_WELL_KNOWN_ADDRESSES = {
    "8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "9.9.9.9", "1.2.3.4", "168.63.129.16",
    "93.184.215.14", "93.184.215.15", "93.184.216.34",
    "2001:4860:4860::8888", "2001:4860:4860::8844", "2606:4700:4700::1111", "2606:4700:4700::1001",
    "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "2606:2800:220:1:248:1893:25c8:1946",
}
# IPv6 outside the global unicast space (2000::/3) reaches no host, unless it carries an IPv4 address: mapped
# (::ffff:0:0/96), NAT64 (64:ff9b::/96, 64:ff9b:1::/48) and 6to4 (2002::/16) addresses are judged by that address.
_GLOBAL_V6 = ipaddress.ip_network("2000::/3")
_NAT64 = [ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48")]
# Domains reserved for examples (RFC 2606, RFC 6761, RFC 6762): the domain itself and every name below it.
RESERVED_DOMAINS = ("example.com", "example.org", "example.net", "example", "invalid", "test", "local", "localhost")
# Addresses the project uses itself: the commit trailer's no-reply address and the contacts the licence names.
CODE_PROJECT_EMAILS = {"noreply" + "@" + "anthropic.com",
                       "legal" + "@" + "llm-framework.org", "security" + "@" + "llm-framework.org"}
# "git@<host>" is the account of an SSH remote, not a person's address; these hosts are public forges.
SSH_REMOTE_HOSTS = {"github.com", "gitlab.com"}
# Account names in home folders that the repository's documents and tests use as examples (compared without case):
# made-up people, generic accounts, the defaults of CI runners, board images and Windows, and the framework's own.
EXAMPLE_ACCOUNTS = {
    "jane", "john", "jroe", "jane.doe", "alice", "alice-private", "bob", "carol", "dave", "erin", "eve", "frank",
    "pat", "jenny", "ann", "first", "o'brien", "o'neil", "someone", "other", "me", "user", "example-user", "u", "x",
    "a", "board", "pi", "radxa", "dietpi", "llmcf", "lcf", "runner", "ubuntu", "public", "default",
}
# A placeholder instead of an account: <name>, {ACCOUNT}, $USER, %USERNAME%, ..., a glob or a regex.
_PLACEHOLDER_START = tuple("<{$%*.[(|~-")
# A home folder: /home/<account>, /Users/<account> (also C:/Users/...) and C:\Users\<account> (also escaped).
_CODE_HOME = re.compile(r"(?:/home/|/Users/|(?<![A-Za-z])[A-Za-z]:\\{1,2}Users\\{1,2})(?P<account>[^\s/\\`\"),;:\]|]*)")
# An e-mail address: the local part begins with a letter or digit ("+@click.option" in a patch is none).
_CODE_MAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][\w.+-]*@(?P<domain>[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})")
# Four dotted numbers that are a version or a section, not an address: a pin ("==1.2.3.4", ">=", "~="), after the
# word version or release, after "v " or "v." ("v1.2.3.4" never matches _V4), or a section of an RFC
# ("RFC 9309 2.3.1.4", "section 2.3.1.4", "§ 2.3.1.4").
_VERSION_BEFORE = re.compile(r"(?:[=<>~!]=\s*|(?i:\bversion|\brelease|\bv\.?|\brfc\s*\d+|\bsection|§)[\s:=\"']*)$")
# Files where four dotted numbers are versions or hashes, never addresses.
_VERSION_FILES = re.compile(r"(?:^|/)(?:requirements[^/]*\.(?:txt|in)|constraints[^/]*\.txt|[^/]*\.lock|"
                            r"[^/]*-lock\.(?:json|ya?ml)|go\.sum)$", re.I)
# An AI session's sandbox: its temporary folder and its runtime folder, followed by a path or a session id. The bare
# prefix named in prose ("`/tmp/claude…`", "`/root/.ccr`") is not a path.
_SANDBOX = re.compile("(?:" + re.escape("/tmp/" + "claude") + r"(?=[-/\w])|" + re.escape("/root/" + ".ccr") + "(?=/))"
                      + r"[^\s`\"')\]]*")


def _in_networks(address, networks) -> bool:
    return any(address.version == n.version and address in n for n in networks)


_BLUEPRINT_NETWORKS = [ipaddress.ip_network(n) for n in DOC_NETWORKS]
_CODE_NETWORKS = [ipaddress.ip_network(n) for n in CODE_NETWORKS]


def _code_address_passes(address) -> bool:
    if str(address) in CODE_WELL_KNOWN_ADDRESSES or _in_networks(address, _CODE_NETWORKS):
        return True
    if address.version == 6:
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is None and any(address in n for n in _NAT64):
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded is not None:
            return _code_address_passes(embedded)
        return address not in _GLOBAL_V6
    return False


def _addresses(line: str, level: str, path: str) -> List[Tuple[str, str]]:
    found = []
    for pattern in (_V4, _V6):
        if level == CODE and pattern is _V4 and _VERSION_FILES.search(path):
            continue
        for m in pattern.finditer(line):
            try:
                address = ipaddress.ip_address(m.group(1))
            except ValueError:
                continue
            if m.group(2):  # a range written with its prefix names a class of addresses, not a host
                continue
            if level == BLUEPRINT:
                if str(address) in WELL_KNOWN_ADDRESSES or _in_networks(address, _BLUEPRINT_NETWORKS):
                    continue
                if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
                    continue
            else:
                if _code_address_passes(address):
                    continue
                if pattern is _V4 and _VERSION_BEFORE.search(line[:m.start()]):
                    continue
                if pattern is _V6 and re.match(r"\.\d", line[m.end():]):
                    continue  # the head of a mapped address ("::ffff:192.0.2.1"): _V4 judges its IPv4 part
            found.append(("address", m.group(1)))
    return found


def _reserved(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in RESERVED_DOMAINS)


def _emails(line: str, level: str) -> List[Tuple[str, str]]:
    if level == BLUEPRINT:
        return [("e-mail", m.group(0)) for m in _MAIL.finditer(line) if m.group(0) not in PROJECT_EMAILS]
    found = []
    for m in _CODE_MAIL.finditer(line):
        value, domain = m.group(0), m.group("domain")
        if _reserved(domain) or value.lower() in CODE_PROJECT_EMAILS:
            continue
        if value.lower().startswith("git@") and domain.lower() in SSH_REMOTE_HOSTS:
            continue
        found.append(("e-mail address", value))
    return found


def example_account(account: str) -> bool:
    """Whether the account of a home folder is an example or a placeholder (code level)."""
    if not account or account.startswith(_PLACEHOLDER_START):
        return True
    account = re.split(r"[ %]", unquote(account))[0].rstrip("'.")
    if not account or account.endswith(("<", "_")):
        return True
    return account.lower() in EXAMPLE_ACCOUNTS


def _homes(line: str, level: str) -> List[Tuple[str, str]]:
    found = []
    if level == BLUEPRINT:
        for m in _HOME.finditer(line):
            account = m.group(1) or m.group(2)
            if account not in DEFAULT_ACCOUNTS and not account.startswith(("<", "$", "%")):
                found.append(("home folder", m.group(0)))
        return found
    for m in _CODE_HOME.finditer(line):
        if not example_account(m.group("account")):
            found.append(("home folder", m.group(0)))
    return found


def sandbox_paths(line: str) -> List[Tuple[str, str]]:
    """The paths of an AI session's sandbox in line: reported at every level and whatever else the line says."""
    return [("AI sandbox path", m.group(0)) for m in _SANDBOX.finditer(line)]


def find_personal_data(line: str, level: str = CODE, path: str = "") -> List[Tuple[str, str]]:
    """(kind, value) for each piece of personal data in one line. path: the file the line belongs to (a
    requirements or lock file holds versions, not addresses)."""
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}; expected one of {LEVELS}")
    found = _addresses(line, level, path) + _emails(line, level) + _homes(line, level)
    if level == CODE:
        found += sandbox_paths(line)
    return found


def scrub_findings(text: str, level: str = BLUEPRINT,
                   secrets: Optional[Callable[[str], Iterable[Tuple[str, str]]]] = None) -> List[str]:
    """What in a text looks like private data, one "<line>: <kind> <value>" each: a host address, an e-mail
    address, a person's home folder, and with `secrets` (secret_patterns.find_secrets) a secret, named by its kind
    only."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        found += [f"{number}: {kind} {value}" for kind, value in find_personal_data(line, level)]
        if secrets is not None:
            found += [f"{number}: {kind}" for kind, _ in secrets(line)]
    return found
