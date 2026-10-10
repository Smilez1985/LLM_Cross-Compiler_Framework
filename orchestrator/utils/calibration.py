#!/usr/bin/env python3
"""
Curated calibration text for importance matrices (data/calibration, MIT-licensed, see its README).

Replaces generated filler: an importance matrix is only as good as the text it was computed on, so the
framework ships a vetted multilingual set (German, English, tool-use transcripts) checked by sha256.
"""

import gzip
import hashlib
from pathlib import Path
from typing import Dict, List

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "calibration"

# Text sha256 of each decompressed set (see data/calibration/README.md for the provenance).
CALIBRATION_SETS: Dict[str, str] = {
    "de": "12ed14698863dab2771535d9ddf110477de5dbf8e11b528a07d0bf33678142ac",
    "en": "0b9d1a768f5030cdf3c6397f504b0684eed4727a95f9d80b30191516feb82d8a",
    "tools": "0ecef8aa3cde7691a102576d0290ae882833ec15fcfdb6552c50576d25df5486",
}
LANGUAGE_MIXES: Dict[str, List[str]] = {
    "mixed": ["de", "en", "tools"],
    "de": ["de", "tools"],
    "en": ["en", "tools"],
}
BLOCK_CHARS = 2000


class CalibrationDataError(RuntimeError):
    """The shipped calibration data is missing or was modified."""


def load_set(name: str) -> str:
    expected = CALIBRATION_SETS.get(name)
    if expected is None:
        raise CalibrationDataError(f"Unknown calibration set '{name}' (known: {', '.join(CALIBRATION_SETS)}).")
    path = DATA_DIR / f"calibration_{name}.txt.gz"
    try:
        data = gzip.decompress(path.read_bytes())
    except (OSError, EOFError) as e:
        raise CalibrationDataError(f"Calibration set '{name}' cannot be read ({path}): {e}") from e
    if hashlib.sha256(data).hexdigest() != expected:
        raise CalibrationDataError(f"Calibration set '{name}' does not match its checksum; refusing to use it.")
    return data.decode("utf-8")


def _blocks(text: str) -> List[str]:
    """Splits text into blocks of about BLOCK_CHARS at line boundaries."""
    blocks, current, size = [], [], 0
    for line in text.splitlines(keepends=True):
        current.append(line)
        size += len(line)
        if size >= BLOCK_CHARS:
            blocks.append("".join(current))
            current, size = [], 0
    if current:
        blocks.append("".join(current))
    return blocks


def build_calibration_text(language: str = "mixed") -> str:
    """The calibration text for a language mix, interleaved block by block: llama-imatrix reads only the
    first chunks (--chunks), so every set must appear from the start."""
    names = LANGUAGE_MIXES.get(language)
    if names is None:
        raise CalibrationDataError(f"Unknown calibration language '{language}' (choose {', '.join(LANGUAGE_MIXES)}).")
    sources = [_blocks(load_set(name)) for name in names]
    out = []
    for i in range(max(len(s) for s in sources)):
        out.extend(s[i] for s in sources if i < len(s))
    return "".join(out)


def write_calibration_dataset(dest: Path, language: str = "mixed") -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(build_calibration_text(language), encoding="utf-8")
    return dest
