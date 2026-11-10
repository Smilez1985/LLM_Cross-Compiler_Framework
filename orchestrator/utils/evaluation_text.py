#!/usr/bin/env python3
"""
The fixed evaluation text of Auto-Tuning (data/evaluation, public domain, see its README).

Every candidate of a tuning run is compared with the original model on this text (llama-perplexity --kl-divergence),
never on the calibration text its importance matrix was computed from. Checked by sha256 like the calibration sets
(orchestrator/utils/calibration.py) and interleaved block by block the same way, so the first chunks llama-perplexity
reads already hold both languages.
"""

import gzip
import hashlib
from pathlib import Path
from typing import Dict

from orchestrator.utils.calibration import _blocks

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "evaluation"

# Text sha256 of each decompressed set, in the order they are interleaved (data/evaluation/README.md)
EVALUATION_SETS: Dict[str, str] = {
    "de": "894ce3e0b2e1ccb84423c80a068921592cd663b4ec252c3f45eadace7f63779b",
    "en": "5f2d1122350d223a2885ad44a1deab2754a8af719bd1e717a905024e80a0ffd8",
}
FILE_NAME = "evaluation.txt"


class EvaluationTextError(RuntimeError):
    """The shipped evaluation text is missing or was modified."""


def load_set(name: str) -> str:
    expected = EVALUATION_SETS.get(name)
    if expected is None:
        raise EvaluationTextError(f"Unknown evaluation set '{name}' (known: {', '.join(EVALUATION_SETS)}).")
    path = DATA_DIR / f"evaluation_{name}.txt.gz"
    try:
        data = gzip.decompress(path.read_bytes())
    except (OSError, EOFError) as e:
        raise EvaluationTextError(f"The evaluation text '{name}' cannot be read ({path.name}): {e}. Restore it: "
                                  "git checkout -- data/evaluation") from e
    if hashlib.sha256(data).hexdigest() != expected:
        raise EvaluationTextError(f"The evaluation text '{name}' does not match its checksum; it is not used. "
                                  "Restore it: git checkout -- data/evaluation")
    return data.decode("utf-8")


def build_evaluation_text() -> str:
    """Both sets interleaved in blocks of about 2,000 characters, German first."""
    sources = [_blocks(load_set(name)) for name in EVALUATION_SETS]
    out = []
    for i in range(max(len(s) for s in sources)):
        out.extend(s[i] for s in sources if i < len(s))
    return "".join(out)


def text_sha256() -> str:
    return hashlib.sha256(build_evaluation_text().encode("utf-8")).hexdigest()


def write_evaluation_text(dest: Path) -> Path:
    """The evaluation text with LF line ends on every host (the same tokens wherever it was written)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build_evaluation_text().encode("utf-8"))
    return dest
