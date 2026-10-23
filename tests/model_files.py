"""Small model files for the tests of the convert stage (scripts/model_check.py of the targets checks them).

GGUF_HEAD is a complete GGUF header (version 3, general.architecture, one F32 tensor of one value) followed by that
tensor's 4 bytes of data: every byte is ASCII, so the stub converters of the tests write it as text and the text they
append after it (what the tests compare) stays readable. A SafeTensors file here holds metadata only (no tensor), which
the safetensors library accepts as well.
"""

import json
import struct
from pathlib import Path
from typing import Optional


def _string(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def gguf_bytes(tensors=((1,),), arch: str = "llama", kv=(), tensor_type: int = 0, version: int = 3,
               data: Optional[bytes] = None) -> bytes:
    """A GGUF file: the header with general.architecture (and the extra (key, value type, packed value) pairs of
    `kv`), one tensor info per shape in `tensors`, the padding to 32 bytes and the tensors' data (zeros of the F32
    size unless `data` is given)."""
    pairs = [(b"general.architecture", 8, _string(arch))] if arch else []
    pairs += [(k.encode("utf-8"), t, v) for k, t, v in kv]
    head = b"GGUF" + struct.pack("<IQQ", version, len(tensors), len(pairs))
    for key, kind, value in pairs:
        head += struct.pack("<Q", len(key)) + key + struct.pack("<I", kind) + value
    offset, sizes = 0, []
    for number, shape in enumerate(tensors):
        head += _string(f"t{number}") + struct.pack("<I", len(shape)) + b"".join(struct.pack("<Q", d) for d in shape)
        count = 1
        for d in shape:
            count *= d
        head += struct.pack("<IQ", tensor_type, offset)
        sizes.append(count * 4)
        offset += count * 4 + (-(count * 4) % 32)  # the next tensor starts on the alignment
    head += b"\0" * (-len(head) % 32)
    if data is None:  # each tensor's zeros, padded to the alignment except the last
        data = b"".join(b"\0" * size + (b"\0" * (-size % 32) if i < len(sizes) - 1 else b"")
                        for i, size in enumerate(sizes))
    return head + data


GGUF_HEAD = gguf_bytes().decode("ascii")


def write_gguf(path: Path, text: str = "") -> Path:
    """A valid GGUF file with `text` after its data (the stubs' way to tell their files apart)."""
    path.write_text(GGUF_HEAD + text, encoding="utf-8")
    return path


def gguf_text(path: Path) -> str:
    """What a stub wrote after the GGUF header (a file without the header as it is)."""
    text = Path(path).read_text(encoding="utf-8")
    return text[len(GGUF_HEAD):] if text.startswith(GGUF_HEAD) else text


def safetensors_bytes(tensors: Optional[dict] = None, metadata: Optional[dict] = None, data: bytes = b"") -> bytes:
    header = dict(tensors or {})
    if metadata is not None:
        header["__metadata__"] = metadata
    raw = json.dumps(header).encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw + data


def write_safetensors(path: Path, name: str = "weights") -> Path:
    """A SafeTensors file with metadata only; `name` tells two of them apart."""
    path.write_bytes(safetensors_bytes(metadata={"name": name}))
    return path
