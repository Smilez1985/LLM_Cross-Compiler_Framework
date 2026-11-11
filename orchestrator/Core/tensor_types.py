#!/usr/bin/env python3
"""
Per-tensor quantization types of a GGUF build (DESIGN "Build pipeline and target modules", the row "Large models").

A build may give llama-quantize per-tensor overrides as `PATTERN=TYPE` entries. The whole rule lives here, so the build
request, the memory estimate, the convert stage's environment, build_info.json, the identical-build check, recipes and
Auto-Tuning's candidates read the same entries:

- PATTERN is the regular expression llama-quantize's `--tensor-type` searches in the tensor's name (lower case, the
  first matching entry wins, llama.cpp's src/llama-quant.cpp at the pin); `output` and `token_embd` alone are the
  quantizer's own `--output-tensor-type` and `--token-embedding-type`, because the pattern `output` would also match
  every `attn_output`.
- TYPE is one of ggml's tensor types as llama-quantize's parse_ggml_type reads them (lower case here).
- At most MAX_ENTRIES entries; a pattern of at most MAX_PATTERN characters from a small set (no spaces, no quotes, no
  shell characters), which must compile.

normalize() gives the canonical list (lower case, "PATTERN=TYPE", in the given order, duplicates of a pattern refused),
env_value() the convert stage's TENSOR_TYPES (entries separated by spaces), quantize_args() the quantizer's arguments
(the stage builds the same from TENSOR_TYPES), type_for() the type an entry gives a tensor of the memory estimate.
"""

import re
from typing import Iterable, List, Optional, Sequence, Tuple

MAX_ENTRIES = 32
MAX_PATTERN = 64
# ggml's quantizable tensor types as llama-quantize names them (ggml_type_name at the pin), lower case
GGML_TYPES = ("f32", "f16", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "q2_k", "q3_k", "q4_k", "q5_k", "q6_k",
              "iq2_xxs", "iq2_xs", "iq3_xxs", "iq1_s", "iq4_nl", "iq3_s", "iq2_s", "iq4_xs", "iq1_m", "tq1_0",
              "tq2_0", "mxfp4")
# The two keys that are the quantizer's own options, not a --tensor-type pattern
OUTPUT, TOKEN_EMBD = "output", "token_embd"
_PATTERN_CHARS = re.compile(r"^[a-z0-9_.\\\[\]()|*+?^$-]+$")


class TensorTypeError(ValueError):
    """An entry llama-quantize would not read, or would read differently; the message names the entry."""


def _split(entry: str) -> Tuple[str, str]:
    text = str(entry or "").strip()
    if "=" not in text:
        raise TensorTypeError(f"'{text}' is not a per-tensor type: write PATTERN=TYPE, e.g. ffn_down=q6_k.")
    pattern, _, kind = text.rpartition("=")
    return pattern.strip().lower(), kind.strip().lower()


def check_entry(entry: str) -> str:
    """One entry in its canonical form (lower case PATTERN=TYPE); TensorTypeError when it is not one."""
    pattern, kind = _split(entry)
    if not pattern:
        raise TensorTypeError(f"'{entry}' names no tensor pattern before '='.")
    if len(pattern) > MAX_PATTERN:
        raise TensorTypeError(f"The pattern '{pattern[:20]}...' is longer than {MAX_PATTERN} characters.")
    if not _PATTERN_CHARS.match(pattern):
        raise TensorTypeError(f"The pattern '{pattern}' may hold only letters, digits, '_', '.', '-' and the regular "
                              "expression characters \\ [ ] ( ) | * + ? ^ $.")
    try:
        re.compile(pattern)
    except re.error as e:
        raise TensorTypeError(f"The pattern '{pattern}' is no regular expression ({e}).") from None
    if kind not in GGML_TYPES:
        raise TensorTypeError(f"'{kind}' is not a tensor type of llama-quantize; known: {', '.join(GGML_TYPES)}.")
    return f"{pattern}={kind}"


def normalize(entries: Optional[Iterable[str]]) -> List[str]:
    """The canonical list of `entries` (each also split at commas, empty ones dropped); [] for none."""
    out: List[str] = []
    seen = set()
    for raw in entries or ():
        for part in str(raw or "").split(","):
            if not part.strip():
                continue
            entry = check_entry(part)
            pattern = entry.rpartition("=")[0]
            if pattern in seen:
                raise TensorTypeError(f"The pattern '{pattern}' is given twice; the quantizer takes only the first.")
            seen.add(pattern)
            out.append(entry)
    if len(out) > MAX_ENTRIES:
        raise TensorTypeError(f"At most {MAX_ENTRIES} per-tensor types are allowed ({len(out)} given).")
    return out


def env_value(entries: Sequence[str]) -> str:
    """TENSOR_TYPES for the convert stage: the canonical entries separated by spaces."""
    return " ".join(normalize(entries))


def quantize_args(entries: Sequence[str]) -> List[str]:
    """llama-quantize's arguments for `entries` (the convert stage builds the same from TENSOR_TYPES)."""
    args: List[str] = []
    for entry in normalize(entries):
        pattern, _, kind = entry.rpartition("=")
        if pattern == OUTPUT:
            args += ["--output-tensor-type", kind]
        elif pattern == TOKEN_EMBD:
            args += ["--token-embedding-type", kind]
        else:
            args += ["--tensor-type", entry]
    return args


def type_for(entries: Sequence[str], tensor_name: str, category: str) -> Optional[str]:
    """The ggml type (upper case, the memory estimate's spelling) an entry gives the tensor `tensor_name`
    (llama.cpp's name, e.g. blk.3.ffn_down.weight) of `category` (output, token_embd or a layer category), None when
    no entry applies (the quantizer's own rule then)."""
    own = OUTPUT if category == "output" else TOKEN_EMBD if category in ("token_embd", "token_embd_tied") else None
    patterns = []
    for entry in entries:
        pattern, _, kind = entry.rpartition("=")
        if pattern in (OUTPUT, TOKEN_EMBD):
            if pattern == own:
                return kind.upper()   # the quantizer's own option comes before every pattern
            continue
        patterns.append((pattern, kind))
    for pattern, kind in patterns:
        if re.search(pattern, tensor_name):
            return kind.upper()
    return None


def describe(entries: Sequence[str]) -> str:
    return ", ".join(entries) if entries else "none"


__all__ = ["GGML_TYPES", "MAX_ENTRIES", "MAX_PATTERN", "OUTPUT", "TOKEN_EMBD", "TensorTypeError", "check_entry",
           "describe", "env_value", "normalize", "quantize_args", "type_for"]
