#!/usr/bin/env python3
"""
Where a GGUF build's importance matrix comes from (DESIGN "Build pipeline and target modules", the row "Large models").

Three sources: computed from the calibration text in the build's importance-matrix job (as before, no file given); a
file on this computer (the person's path, an AI's local reference resolved by the build service); a file of a Hugging
Face repository, written `hf:owner/name/path/in/the/repository`, which the build engine downloads before the build with
the framework's verified fetcher (pinned to the commit it resolves, its size and sha256 checked).

check_file() reads a given file before anything is built: the GGUF importance-matrix format of the llama.cpp pin
(general.type "imatrix", tensors NAME.in_sum2 and NAME.counts) or the older .dat format (a count, then per entry a
name, the call count and the values), at most MAX_BYTES. It returns the format and the number of entries; the convert
stage compares the names with the model's own (model_check.py imatrix), because only the model knows its tensors.
"""

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

MAX_BYTES = 2 * 1024 ** 3
HF_PREFIX = "hf:"
# owner/name/path: the repository id as the Hub writes it, then the file's path in the repository
_HF_REF = re.compile(r"^hf:([A-Za-z0-9][\w.-]{0,95})/([A-Za-z0-9][\w.-]{0,95})/((?:[\w.+-]+/)*[\w.+-]+)$")
COMPUTED, FILE, HUGGINGFACE = "computed", "file", "huggingface"
MAX_ENTRIES = 100_000
MAX_NAME = 512


class ImatrixError(ValueError):
    """A given importance matrix that cannot be used; the message names only the file's name, never a path."""


@dataclass(frozen=True)
class ImatrixSource:
    """kind: file or huggingface; `path` for a file, `repo` and `filename` for a Hugging Face file."""
    kind: str
    path: str = ""
    repo: str = ""
    filename: str = ""

    @property
    def name(self) -> str:
        return Path(self.filename).name if self.kind == HUGGINGFACE else Path(self.path).name

    def label(self) -> str:
        """The source in words, without a path: the file's name, or the repository and the file."""
        return f"{self.repo}/{self.filename} (Hugging Face)" if self.kind == HUGGINGFACE else self.name


def is_hub_reference(value: Optional[str]) -> bool:
    return str(value or "").strip().lower().startswith(HF_PREFIX)


def parse_hub(value: str) -> ImatrixSource:
    """`hf:owner/name/path` as a source; ImatrixError when it is not one."""
    match = _HF_REF.match(str(value or "").strip())
    if not match or ".." in match.group(3).split("/"):
        raise ImatrixError(f"'{value}' is not a Hugging Face file: write hf:owner/name/path/in/the/repository, e.g. "
                           "hf:owner/model-GGUF/imatrix.gguf.")
    return ImatrixSource(HUGGINGFACE, repo=f"{match.group(1)}/{match.group(2)}", filename=match.group(3))


def _gguf_type(path: Path) -> Dict[str, Any]:
    from orchestrator.runtime.bundle.generator import BundleError, gguf_header
    try:
        meta = gguf_header(path)
    except (BundleError, OSError) as e:
        raise ImatrixError(f"{path.name} is no readable GGUF file ({e}).") from None
    kind = str(meta.get("general.type") or "")
    if kind != "imatrix":
        raise ImatrixError(f"{path.name} is a GGUF file, but no importance matrix (its general.type is "
                           f"'{kind or 'model'}'); name the matrix, not a model.")
    return {"format": "gguf", "chunks": meta.get("imatrix.chunk_count"),
            "dataset": None}


def _dat(path: Path, size: int) -> Dict[str, Any]:
    """The older .dat format (llama.cpp's tools/imatrix at the pin, load_legacy_imatrix): int32 entry count, then per
    entry int32 name length, the name, int32 call count, int32 value count and that many floats."""
    with open(path, "rb") as f:
        def take(n: int) -> bytes:
            data = f.read(n)
            if len(data) != n:
                raise ImatrixError(f"{path.name} is not an importance matrix (its entries end early).")
            return data
        count = struct.unpack("<i", take(4))[0]
        if not 0 < count <= MAX_ENTRIES:
            raise ImatrixError(f"{path.name} is not an importance matrix (neither the GGUF format nor the .dat "
                               "format of llama-imatrix).")
        for _ in range(count):
            length = struct.unpack("<i", take(4))[0]
            if not 0 < length <= MAX_NAME:
                raise ImatrixError(f"{path.name} is not an importance matrix (a damaged entry name).")
            name = take(length)
            try:
                name.decode("ascii")
            except UnicodeDecodeError:
                raise ImatrixError(f"{path.name} is not an importance matrix (an entry name is no tensor name).") \
                    from None
            _calls, values = struct.unpack("<ii", take(8))
            if values <= 0 or f.tell() + values * 4 > size:
                raise ImatrixError(f"{path.name} is not an importance matrix (an entry is larger than the file).")
            f.seek(values * 4, 1)
    return {"format": "dat", "entries": count}


def check_file(path: Path) -> Dict[str, Any]:
    """{format: gguf|dat, bytes, ...} of a given importance matrix; ImatrixError when it is none or too large."""
    path = Path(path)
    try:
        if not path.is_file():
            raise ImatrixError(f"The importance matrix {path.name} was not found.")
        size = path.stat().st_size
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        raise ImatrixError(f"The importance matrix {path.name} cannot be read.") from None
    if size > MAX_BYTES:
        raise ImatrixError(f"{path.name} is larger than {MAX_BYTES // 1024 ** 3} GiB; no importance matrix is that "
                           "large.")
    info = _gguf_type(path) if magic == b"GGUF" else _dat(path, size)
    info["bytes"] = size
    return {k: v for k, v in info.items() if v is not None}


def record(source: Optional[ImatrixSource], sha256: Optional[str], *, commit: str = "",
           fmt: str = "") -> Dict[str, Any]:
    """build_info.json's imatrix_source: the kind, the file's name, its sha256 and format, the repository and commit
    of a Hugging Face file; never a path. `source` None: computed in the build."""
    if source is None:
        return {"kind": COMPUTED}
    out: Dict[str, Any] = {"kind": source.kind, "name": source.name}
    if sha256:
        out["sha256"] = sha256
    if fmt:
        out["format"] = fmt
    if source.kind == HUGGINGFACE:
        out.update({"repo": source.repo, "file": source.filename})
        if commit:
            out["commit"] = commit
    return out


__all__ = ["COMPUTED", "FILE", "HF_PREFIX", "HUGGINGFACE", "ImatrixError", "ImatrixSource", "MAX_BYTES",
           "check_file", "is_hub_reference", "parse_hub", "record"]
