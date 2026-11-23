#!/usr/bin/env python3
"""
Ditto's archive: the shapes (DESIGN, Ditto, the archive's rows; maintainer's decisions of 2026-11-13).

An archive entry is one plain sentence about llmcf or about building, with the ground it rests on and the evidence for
it. It enters the archive only with one of three grounds:

- ``sources``: several independent sources (different registrable domains) say the same;
- ``vendor``: the website of the hardware's manufacturer (a domain `configs/archive_sources.yml` lists for a vendor);
- ``tested``: it worked in a real test or build (a green-tested artifact build).

The person confirms an entry whenever they choose. Ditto confirms one only when enough independent sources say the
same: the archive's security level (the person's setting `ditto_archive_security`) decides how many (low 3, mid 5,
high 7). Only the shapes and the numbers live here; the rules that use them are in `Core/ditto_archive.py`.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

GROUNDS = ("sources", "vendor", "tested")
STATUSES = ("unconfirmed", "confirmed")
CONFIRMERS = ("person", "ditto")
KINDS = ("entry", "copy")          # a copy is a person-confirmed text a newer entry replaced (kept, with Undo)

SETTING = "ditto_archive_security"
LEVELS = ("low", "mid", "high")
DEFAULT_LEVEL = "mid"
# How many independent sources Ditto needs to confirm an entry by itself, by level (decided 2026-11-13).
LEVEL_SOURCES = {"low": 3, "mid": 5, "high": 7}

ARCHIVE_ALIAS = "ditto_archive"    # the collection's alias in the knowledge base's storage

TEXT_MAX = 600                     # a manufacturer's page names up to about twenty chips of one vendor
EVIDENCE_MAX = 20                  # evidence items kept per entry
EVIDENCE_LENGTH = 500

# kind:subject/property, for example target:rk3588/npu_tops or build:Rockchip/rk3588-gguf-Q4_K_M/works
KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9_./-]{0,95}$"
_KEY = re.compile(KEY_PATTERN)
# Evidence: a public https address, or the label of the build or test an entry rests on (never a local path).
_LABEL = re.compile(r"^(?:build|test):[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ADDRESS = re.compile(r"^https://[A-Za-z0-9.-]{1,253}(?::\d{1,5})?(?:/[^\s<>\"'`\\]*)?$")


# A claim's value (phase 2, part 1): the same statement from several sources is found by its key and its normalized
# value, whatever the wording of the sentence. The type says how a value is normalized.
VALUE_TYPES = ("text", "number", "version", "flag")
VALUE_MAX = 200
_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"^([-+]?)(\d{1,30})(?:[.](\d{1,30}))?\s*([A-Za-z%][A-Za-z0-9%/]{0,15})?$")
_VERSION = re.compile(r"^[vV]?(\d{1,9}(?:\.\d{1,9}){0,5})$")
_FLAGS = {"true": "true", "yes": "true", "on": "true", "1": "true",
          "false": "false", "no": "false", "off": "false", "0": "false"}


def comparable_text(text: object) -> str:
    """`text` as it is compared: white space collapsed and the letters' case ignored, except inside code (a fenced
    ``` block or an inline `span`), which is kept exactly, since a command or a flag differs by its case."""
    out = []
    for part in _CODE.split(str(text or "")):
        if not part:
            continue
        out.append(part if part.startswith("`") else _SPACES.sub(" ", part).casefold())
    return "".join(out).strip()


def normalize_value(value_type: object, raw: object) -> Optional[str]:
    """The normalized value of a claim, or None when `raw` is not a value of `value_type`:
    - text: comparable_text (case ignored outside code);
    - number: a decimal without needless zeros and an optional unit in lower case ("6.0 TOPS" and "6 tops" -> "6 tops");
    - version: dotted numbers without a leading v and without leading zeros ("v1.09.5" -> "1.9.5"; 1.9 is not 1.9.0);
    - flag: true or false (yes/no, on/off, 1/0 as well)."""
    text = str(raw if raw is not None else "").strip()
    if not text or len(text) > VALUE_MAX:
        return None
    if value_type == "text":
        return comparable_text(text) or None
    if value_type == "number":
        found = _NUMBER.match(text)
        if not found:
            return None
        sign, whole, fraction, unit = found.groups()
        try:
            number = Decimal(f"{sign}{whole}.{fraction or '0'}")
        except InvalidOperation:
            return None
        plain = format(number.normalize(), "f")
        plain = "0" if Decimal(plain) == 0 else plain
        return f"{plain} {unit.casefold()}" if unit else plain
    if value_type == "version":
        found = _VERSION.match(text)
        return ".".join(str(int(p)) for p in found.group(1).split(".")) if found else None
    if value_type == "flag":
        return _FLAGS.get(text.casefold())
    return None


def normalize_level(level: object) -> str:
    """The level of a setting value: low, mid or high; anything else is the default."""
    word = str(level or "").strip().lower()
    return word if word in LEVELS else DEFAULT_LEVEL


def confirm_needed(level: object) -> int:
    """How many independent sources Ditto needs to confirm an entry at `level` (3, 5 or 7)."""
    return LEVEL_SOURCES[normalize_level(level)]


def valid_key(key: object) -> bool:
    return isinstance(key, str) and bool(_KEY.match(key))


def valid_evidence(item: object) -> bool:
    """A public https address (at most EVIDENCE_LENGTH characters) or a build or test label."""
    return isinstance(item, str) and len(item) <= EVIDENCE_LENGTH and bool(_ADDRESS.match(item) or _LABEL.match(item))


COMPETING_MAX = 5                  # competing claims kept per key (phase 2, part 2)


class CompetingClaim(BaseModel):
    """Another value for the key of an entry, offered from a web page (phase 2, part 2): kept beside the entry with its
    own evidence, never merged into it and never replacing it. While a key has one, Ditto confirms neither."""
    model_config = ConfigDict(extra="forbid")

    value_type: Literal["text", "number", "version", "flag"]
    value: str = Field(max_length=VALUE_MAX * 2)
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    evidence: List[str] = Field(default_factory=list, max_length=EVIDENCE_MAX)
    passages: Dict[str, str] = Field(default_factory=dict)
    independent_sources: int = 0

    @field_validator("evidence")
    @classmethod
    def _evidence_is_public(cls, value: List[str]) -> List[str]:
        if any(not valid_evidence(v) for v in value):
            raise ValueError("evidence must be public https addresses or build and test labels")
        return value


class ArchiveEntry(BaseModel):
    """One archive entry as the person, the CLI, the window and the tools see it (the storage's payload without the
    vectors)."""
    model_config = ConfigDict(extra="forbid")

    id: str
    key: str = Field(pattern=KEY_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    ground: Literal["sources", "vendor", "tested"]
    evidence: List[str] = Field(default_factory=list, max_length=EVIDENCE_MAX)
    status: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    confirmed_by: Optional[Literal["person", "ditto"]] = None
    kind: Literal["entry", "copy"] = "entry"
    created: str = ""
    updated: str = ""
    copied_from: Optional[str] = None     # a copy: the id of the entry whose text it kept
    copied_at: Optional[str] = None
    independent_sources: int = 0          # how many different registrable domains the evidence names
    value_type: Optional[Literal["text", "number", "version", "flag"]] = None   # the claim's value (phase 2)
    value: Optional[str] = Field(default=None, max_length=VALUE_MAX * 2)       # normalized (normalize_value)
    # phase 2, part 2: the fingerprint of the stored passage around the quote, per evidence address (two addresses of
    # different domains with the same passage are one source, a mirror), and the competing claims of the key
    passages: Dict[str, str] = Field(default_factory=dict)
    competing: List[CompetingClaim] = Field(default_factory=list, max_length=COMPETING_MAX)

    @field_validator("evidence")
    @classmethod
    def _evidence_is_public(cls, value: List[str]) -> List[str]:
        bad = [v for v in value if not valid_evidence(v)]
        if bad:
            raise ValueError("evidence must be public https addresses or build and test labels")
        return value


__all__ = ["ARCHIVE_ALIAS", "ArchiveEntry", "COMPETING_MAX", "CONFIRMERS", "CompetingClaim", "DEFAULT_LEVEL", "EVIDENCE_MAX", "GROUNDS", "KEY_PATTERN",
           "KINDS", "LEVELS", "LEVEL_SOURCES", "SETTING", "STATUSES", "TEXT_MAX", "VALUE_MAX", "VALUE_TYPES",
           "comparable_text", "confirm_needed", "normalize_level", "normalize_value", "valid_evidence", "valid_key"]
