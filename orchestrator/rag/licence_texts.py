#!/usr/bin/env python3
"""
The canonical texts of the licences that ask for their text or permission notice with every copy (DECISIONS,
answers of 2026-10-16, W3).

A pack that carries pages or code in full under such a licence ships the licence's text with them (pack_licences.py).
The texts come unchanged from the SPDX License List data (https://github.com/spdx/license-list-data, under CC0-1.0)
at one pinned version. They live in orchestrator/data/licenses/<SPDX id>.txt, and licenses.json there records the
version, where each text came from and its sha256. A text whose file is missing, or whose sha256 differs from
licenses.json, counts as not there: a page under that licence then goes out as its address and short facts, and the
reason says so. The framework never writes a licence text of its own.

Which licences ask for their text, and why (one sentence each, shown to the person and, as fixed words, to an AI):
* MIT and ISC: the copyright notice and the permission notice go with every copy;
* BSD-2-Clause and BSD-3-Clause: copies keep the copyright notice, the list of conditions and the disclaimer;
* Apache-2.0: recipients get a copy of the licence, and the notices of the work's NOTICE file;
* MPL-2.0: recipients learn that the text is under the licence and how to get a copy of it;
* CC BY and CC BY-SA (1.0 to 4.0): copies include the licence or its address (the pack includes the text).
CC0, public domain, 0BSD and the Unlicense ask for no text. Licences whose pages a pack never carries in full (NC, ND,
GPL, LGPL, AGPL, GFDL) need none here either.
"""

import functools
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "licenses"
MANIFEST_NAME = "licenses.json"
LICENSES_DIR = "LICENSES"      # in a pack, next to its knowledge file
NOTICE_NAME = "NOTICE"         # in a pack, next to its knowledge file
TEXT_FILE_KEY = "licence_text_file"   # metadata of a page carried in full: LICENSES/<id>.txt next to the knowledge file
# Where a pack keeps a knowledge file: a knowledge pack at its top, a target pack in knowledge/ (its snapshot).
KNOWLEDGE_FOLDERS = ("", "knowledge/")

_ID = r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}"
_SPDX_ID = re.compile(rf"^{_ID}$")
TEXT_PATH = re.compile(rf"^{LICENSES_DIR}/({_ID})\.txt$")

_NOTICE_AND_PERMISSION = "{} asks that the copyright notice and this permission notice go with every copy"
_BSD = "{} asks that copies keep the copyright notice, the list of conditions and the disclaimer"
_CC = "{} asks that copies include the licence or its address; the pack includes the text"

# The licences that ask for their text or permission notice, and why.
TEXT_REQUIRED: Dict[str, str] = {
    "MIT": _NOTICE_AND_PERMISSION.format("MIT"),
    "ISC": _NOTICE_AND_PERMISSION.format("ISC"),
    "BSD-2-Clause": _BSD.format("BSD-2-Clause"),
    "BSD-3-Clause": _BSD.format("BSD-3-Clause"),
    "Apache-2.0": "Apache-2.0 asks that recipients get a copy of the licence, and the notices of the work's NOTICE file",
    "MPL-2.0": "MPL-2.0 asks that recipients learn that the text is under it and how to get a copy of it",
}
for _version in ("1.0", "2.0", "2.5", "3.0", "4.0"):
    TEXT_REQUIRED[f"CC-BY-{_version}"] = _CC.format(f"CC BY {_version}")
    TEXT_REQUIRED[f"CC-BY-SA-{_version}"] = _CC.format(f"CC BY-SA {_version}")

# Licences whose copyright line goes with the licence text (the notice is part of what they ask to keep).
COPYRIGHT_WITH_TEXT = frozenset({"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause"})


@dataclass(frozen=True)
class Canonical:
    """One canonical licence text, checked against its sha256."""
    licence: str
    name: str
    data: bytes


@dataclass(frozen=True)
class _Texts:
    version: str
    texts: Dict[str, Canonical]
    problems: Tuple[str, ...]


@functools.lru_cache(maxsize=4)
def _load(folder: str) -> _Texts:
    """The texts of licenses.json in `folder` whose files are there with the recorded sha256, and what is wrong with
    the others (read once per folder)."""
    root = Path(folder)
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        listed, version = manifest["texts"], str(manifest["licence_list_version"])
        if not isinstance(listed, dict):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError):
        return _Texts("", {}, (f"{MANIFEST_NAME} is missing or damaged",))
    texts: Dict[str, Canonical] = {}
    problems = []
    for licence, entry in listed.items():
        try:
            name, file, digest = str(entry["name"]), str(entry["file"]), str(entry["sha256"])
        except (KeyError, TypeError):
            problems.append(f"{licence}: incomplete entry in {MANIFEST_NAME}")
            continue
        if not _SPDX_ID.match(str(licence)) or file != f"{licence}.txt":
            problems.append(f"{licence}: not a licence id with its file <id>.txt")
            continue
        try:
            data = (root / file).read_bytes()
        except OSError:
            problems.append(f"{file}: missing")
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"{file}: its sha256 differs from {MANIFEST_NAME}")
            continue
        texts[licence] = Canonical(licence, name, data)
    return _Texts(version, texts, tuple(problems))


def _texts() -> _Texts:
    return _load(str(DATA_DIR))


def canonical(licence: str) -> Optional[Canonical]:
    """The canonical text of a licence, or None when the framework has none (or its file does not match)."""
    return _texts().texts.get(str(licence or ""))


def list_version() -> str:
    """The version of the SPDX License List the texts come from ('' when licenses.json cannot be read)."""
    return _texts().version


def problems() -> Tuple[str, ...]:
    """What is wrong with the shipped texts (empty: every listed text is there with its sha256)."""
    return _texts().problems


def needs_text(licence: str) -> bool:
    """Whether a licence asks for its text or permission notice with every copy."""
    return str(licence or "") in TEXT_REQUIRED


def missing_text(licence: str) -> bool:
    """A licence that asks for its text, and the framework has no canonical text of it."""
    return needs_text(licence) and canonical(licence) is None


def text_path(licence: str) -> str:
    """Where a pack keeps a licence's text, next to its knowledge file: LICENSES/<id>.txt."""
    return f"{LICENSES_DIR}/{licence}.txt"


def canonical_file(rel: str, data: bytes) -> bool:
    """Whether a file of a pack (its path relative to the pack) is a canonical licence text, byte for byte:
    LICENSES/<id>.txt next to a knowledge file. Such a file is never changed by the privacy scrub."""
    for folder in KNOWLEDGE_FOLDERS:
        if folder and not rel.startswith(folder):
            continue
        match = TEXT_PATH.match(rel[len(folder):])
        if match:
            text = canonical(match.group(1))
            return text is not None and text.data == data
    return False
