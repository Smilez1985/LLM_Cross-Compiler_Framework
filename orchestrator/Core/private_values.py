#!/usr/bin/env python3
"""
The module wizard's move of a private value (docs/DESIGN.md, the wizard's local layer for private values).

A module is made to be shared, so what is private to this computer must not stand in its files. The wizard's two shell
fields, the setup commands and the quantization logic, are where a person types a path, an address, a key file or a
password; this module finds such a value by the rules the framework already has (the export review's shapes: a path on
a network drive, a Windows path, an internal domain, the key file of `ssh -i`; and the pattern table's secrets: key
formats and the secrets that only their place gives away, such as `--password X`), replaces it by `${LOCAL_NAME}` and
returns the value for the person's own file (Core/modules_env.py), from which a build hands it to the stages.

Nothing here writes a file; the generator does that after the module was made. A report names the variables and what each
one is, never a value.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from orchestrator.Core import modules_env
from orchestrator.security import export_review, secret_patterns

# The wizard's fields that hold shell code a build runs; the flags and the package list take plain options and names.
SHELL_FIELDS = ("setup_commands", "quantization_logic")
_PASSWORD_RULES = ("password", "passwd", "pwd", "sshpass", "netrc", "curl user", "standard input", "sudo -s", "wpa", "passphrase")
_REFERENCE = re.compile(r"\$\{LOCAL_[A-Z][A-Z0-9_]*\}")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str      # in words, for the person
    prefix: str    # PATH, HOST, SSH_KEY, PASSWORD or SECRET: the variable's name is LOCAL_<prefix>_<n>
    value: str


@dataclass(frozen=True)
class Moved:
    """What the person is told: the variable, what it is, the field it was in; never the value."""
    name: str
    kind: str
    field: str


def _prefix_of_review(f: "export_review.Finding") -> str:
    if "domain" in f.kind:
        return "HOST"
    if "key file" in f.kind:
        return "SSH_KEY"
    return "PATH"


def spans(text: str) -> List[Span]:
    """The private values in one field's text, without overlaps, in order. A value that already is a ${LOCAL_NAME}, a
    value with a line break and a value of a rule that only marks the start of a block (a private key) are left, so the
    export's own check still stops what this cannot move."""
    found: List[Span] = []
    for f in export_review.to_review(text):
        if f.kind == "a line of an SSH configuration":
            continue   # a shell field holds no configuration file; an ssh -i key file is found as its own shape
        found.append(Span(f.start, f.end, f.kind, _prefix_of_review(f), f.original))
    for rule in secret_patterns.PATTERNS:
        if not rule.redact or not secret_patterns._may_match(rule, text.lower()):
            continue
        group = "secret" if "secret" in rule.regex.groupindex else 0
        for m in rule.regex.finditer(text):
            value = m.group(group)
            if not value or "\n" in value or "REDACTED" in value or value.startswith("$"):
                continue
            said = (rule.name + " " + m.group(0)).lower()
            prefix = "PASSWORD" if any(word in said for word in _PASSWORD_RULES) else "SECRET"
            article = "an" if rule.name[:1].lower() in "aeiou" else "a"
            found.append(Span(m.start(group), m.end(group), f"{article} {rule.name}", prefix, value))
    found.sort(key=lambda s: (s.start, -(s.end - s.start)))
    out: List[Span] = []
    for span in found:
        if out and span.start < out[-1].end:
            continue
        if _REFERENCE.fullmatch(span.value):
            continue
        out.append(span)
    return out


def _inside_single_quotes(text: str, offset: int) -> bool:
    """Whether `offset` stands inside '...' of its line (a variable does not expand there)."""
    line = text[text.rfind("\n", 0, offset) + 1:offset]
    return line.count("'") % 2 == 1 and line.count('"') % 2 == 0


def _free_name(prefix: str, taken: set) -> str:
    n = 1
    while f"LOCAL_{prefix}_{n}" in taken:
        n += 1
    return f"LOCAL_{prefix}_{n}"


def extract(fields: Mapping[str, str], existing: Optional[Mapping[str, str]] = None
            ) -> Tuple[Dict[str, str], Dict[str, str], List[Moved]]:
    """(the fields with each private value replaced by ${LOCAL_NAME}, the values by name, what moved). `existing` are
    the values the target's file already has: a value that is there keeps its name, and a new name is never one of them."""
    known = dict(existing or {})
    by_value = {v: k for k, v in known.items()}
    values: Dict[str, str] = {}
    taken = set(known)
    moved: List[Moved] = []
    clean: Dict[str, str] = {}
    for field, text in fields.items():
        text = text if isinstance(text, str) else ""
        if field not in SHELL_FIELDS or not text:
            clean[field] = text
            continue
        out, pos = [], 0
        for span in spans(text):
            name = by_value.get(span.value)
            if name is None:
                try:
                    modules_env.check(f"LOCAL_{span.prefix}_1", span.value)   # the file can hold it (one line, length)
                except modules_env.LocalValuesError:
                    continue
                name = _free_name(span.prefix, taken)
                taken.add(name)
                by_value[span.value] = name
                values[name] = span.value
                moved.append(Moved(name, span.kind, field))
            reference = "${" + name + "}"
            if _inside_single_quotes(text, span.start):
                reference = "'\"" + reference + "\"'"   # close the single quotes, expand, open them again
            out += [text[pos:span.start], reference]
            pos = span.end
        out.append(text[pos:])
        clean[field] = "".join(out)
    return clean, values, moved


def describe(moved: List[Moved], target: str, where: str) -> str:
    """The sentence the person reads after a module was made: which variables hold what, and where the values are."""
    if not moved:
        return ""
    names = ", ".join(f"{m.name} ({m.kind})" for m in moved)
    return (f"{len(moved)} value(s) private to this computer were taken out of the module's {', '.join(sorted({m.field for m in moved}))} "
            f"and written to {where}: {names}. The module holds ${{NAME}} in their place, so a pack of it carries none.")
