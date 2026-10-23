#!/usr/bin/env python3
"""The checks of the convert stage (modules/convert_module.sh): what the convert module of version 1.x promised in the
maintainer's design (DECISIONS round 16, U9 addendum) and never had.

- Format detection: a Hugging Face model folder (SafeTensors or PyTorch weights), a GGUF file (nothing to convert),
  an ONNX model, a single weights file, a folder of GGUF files. What the format of the build cannot use is refused
  with the reason, before anything slow runs.
- Integrity of the input (config and tokenizer JSON, SafeTensors headers against the file sizes as the safetensors
  library checks them, the shards an index lists, PyTorch archives, a GGUF header) and of the GGUF the converter
  wrote (every tensor's data inside the file, as llama.cpp's model loader checks it).
- Statistics: input and output size, their ratio, the duration of the conversion; in the log and in the stage's
  convert_stats.json, which the target stage puts into build_info.json ("conversion").
- Functional test: a short run (8 tokens, bounded, no network) of the written GGUF with llama-completion, the
  one-shot generator that was llama-cli in version 1.x (llama-cli itself when the image has only that); skipped with
  a note when the image has neither or the model does not generate text.
- Cleanup: a file a converter is about to replace is kept as FILE.convert-backup until the new one passed its checks;
  a failed or stopped stage removes what it wrote and puts the backups back.

Runs in the target image with its python3 and the standard library only (no numpy, no gguf-py), so the tests run the
same code. Reads model files, writes only the files named on its command line and the stage's own files, and never
uses the network. Every target ships the same copy (targets/Rockchip/scripts and targets/_template/scripts).

  model_check.py input PATH --format FORMAT --stats FILE [--onnx-next TEXT]
  model_check.py begin FILE --state FILE [--scratch DIR]
  model_check.py watch DIR --state FILE
  model_check.py output (FILE | --watched) --stats FILE --state FILE --format FORMAT [--type TYPE] [--seconds N]
                 [--tools DIR --test-seconds N | --test-skip REASON]
  model_check.py restore --state FILE (--failed | --stopped)
  model_check.py keep --state FILE

Exit codes: 0 done, 1 a refusal or a failed check (the reason on stderr), 2 wrong use.
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The suffixes of model files as the framework names them (orchestrator/tools/local_files.py MODEL_SUFFIXES and
# ModelManager._detect_model_format): the build container has no orchestrator package, so the stage carries the
# same map, and tests/test_convert_stage.py keeps the two equal.
MODEL_SUFFIXES = {".gguf": "gguf", ".onnx": "onnx", ".tflite": "tflite", ".pt": "pytorch", ".pth": "pytorch",
                  ".bin": "pytorch", ".safetensors": "safetensors"}
# JSON files of a Hugging Face folder that the converter reads when they are there.
JSON_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "generation_config.json",
              "special_tokens_map.json", "model.safetensors.index.json", "pytorch_model.bin.index.json")
INDEX_FILES = ("model.safetensors.index.json", "pytorch_model.bin.index.json")

# GGUF (gguf-py/gguf/constants.py at the llama.cpp pin): value types and their sizes, and the ggml tensor types as
# (block size, bytes per block). A type not listed (a newer one) has no size check; its offset is still checked.
GGUF_MAGIC = b"GGUF"
GGUF_VERSIONS = (2, 3)
GGUF_DEFAULT_ALIGNMENT = 32
GGML_MAX_DIMS = 4
GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
GGUF_STRING, GGUF_ARRAY = 8, 9
QK_K = 256
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20), 6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36), 10: ("Q2_K", QK_K, 84), 11: ("Q3_K", QK_K, 110),
    12: ("Q4_K", QK_K, 144), 13: ("Q5_K", QK_K, 176), 14: ("Q6_K", QK_K, 210), 15: ("Q8_K", QK_K, 292),
    16: ("IQ2_XXS", QK_K, 66), 17: ("IQ2_XS", QK_K, 74), 18: ("IQ3_XXS", QK_K, 98), 19: ("IQ1_S", QK_K, 50),
    20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", QK_K, 110), 22: ("IQ2_S", QK_K, 82), 23: ("IQ4_XS", QK_K, 136),
    24: ("I8", 1, 1), 25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8),
    29: ("IQ1_M", QK_K, 56), 30: ("BF16", 1, 2), 34: ("TQ1_0", QK_K, 54), 35: ("TQ2_0", QK_K, 66),
    39: ("MXFP4", 32, 17), 40: ("NVFP4", 64, 36), 41: ("Q1_0", 128, 18), 42: ("Q2_0", 64, 18),
}
# SafeTensors dtypes and their bytes per value (the sub-byte FP4/FP6 types have no size check).
SAFETENSORS_DTYPES = {"BOOL": 1, "U8": 1, "I8": 1, "F8_E5M2": 1, "F8_E4M3": 1, "F8_E8M0": 1, "I16": 2, "U16": 2,
                      "F16": 2, "BF16": 2, "I32": 4, "U32": 4, "F32": 4, "C64": 8, "F64": 8, "I64": 8, "U64": 8}
SAFETENSORS_MAX_HEADER = 100_000_000  # the safetensors library's own limit

# The functional test: llama-completion is the one-shot generator (llama-cli of version 1.x; llama.cpp split it
# off when llama-cli became the chat program); llama-cli is used when the image has only that.
TEST_TOOLS = ("llama-completion", "llama-cli")
TEST_TOKENS = 8
BACKUP_SUFFIX = ".convert-backup"


class Problem(Exception):
    """A refusal or a failed check: the reason, for the person, without a path."""


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [CONVERT] {text}", flush=True)


def warn(text: str) -> None:
    print(f"WARNING: {text}", file=sys.stderr, flush=True)


def size_text(size: Optional[int]) -> str:
    if size is None:
        return "-"
    value, unit = float(size), "B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            break
        value /= 1024
    return f"{size} B" if unit == "B" else f"{value:.2f} {unit}"


def _visible(name: str) -> bool:
    return not name.startswith(".")


def tree_bytes(path: Path) -> int:
    """The size of a file, or of every file below a folder (links to files followed, hidden entries left out: a
    git folder beside the weights is no part of the model)."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for current, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if _visible(d)]
        for name in files:
            if _visible(name):
                try:
                    total += os.stat(os.path.join(current, name)).st_size
                except OSError:
                    pass
    return total


# --- Detection ---------------------------------------------------------------------------------------------------

def detect(path: Path) -> Dict[str, Any]:
    """What the input is: kind huggingface, gguf, onnx, weights-file, gguf-folder, tflite, folder or file, with what
    was seen (weights: safetensors, pytorch or none; the ONNX and GGUF files)."""
    if path.is_file():
        kind = MODEL_SUFFIXES.get(path.suffix.lower(), "file")
        if kind in ("pytorch", "safetensors"):
            return {"kind": "weights-file", "weights": kind}
        return {"kind": kind}
    top = sorted(p for p in path.iterdir() if _visible(p.name))
    files = [p for p in top if p.is_file()]
    safetensors = [p for p in files if p.suffix.lower() == ".safetensors"]
    pytorch = [p for p in files if p.name.startswith("pytorch_model") and p.suffix.lower() == ".bin"]
    weights = "safetensors" if safetensors else "pytorch" if pytorch else "none"
    # ONNX files in the folder and one level below, where rknn_module.sh looks for them
    onnx = [p for p in files if p.suffix.lower() == ".onnx"]
    for sub in (p for p in top if p.is_dir()):
        onnx += sorted(p for p in sub.iterdir() if p.is_file() and _visible(p.name) and p.suffix.lower() == ".onnx")
    gguf = [p for p in files if p.suffix.lower() == ".gguf"]
    has_config = (path / "config.json").is_file()
    info: Dict[str, Any] = {"weights": weights, "safetensors": safetensors, "pytorch": pytorch, "onnx": onnx,
                            "gguf": gguf, "config": has_config}
    if has_config and weights != "none":
        info["kind"] = "huggingface"
    elif onnx:
        info["kind"] = "onnx"
    elif gguf and not has_config:
        info["kind"] = "gguf-folder"
    elif has_config:
        info["kind"] = "huggingface"   # without weights: the warning of the convert stage says so
    else:
        info["kind"] = "folder"
    return info


def describe(info: Dict[str, Any]) -> str:
    kind = info["kind"]
    if kind == "huggingface":
        weights = {"safetensors": "SafeTensors weights", "pytorch": "PyTorch weights"}.get(info["weights"],
                                                                                           "no weights")
        return f"a Hugging Face model folder with {weights}"
    return {"gguf": "a GGUF file", "onnx": "an ONNX model", "weights-file": "a single weights file",
            "gguf-folder": "a folder of GGUF files", "tflite": "a TensorFlow Lite model",
            "folder": "a folder without config.json", "file": "a file of no model format"}[kind]


def refusal(info: Dict[str, Any], path: Path, fmt: str, onnx_next: str) -> Optional[str]:
    """Why the build's format cannot use this input (None: it can, or the format's helper decides)."""
    kind, name = info["kind"], path.name
    if fmt == "rknn":
        return None  # rknn_module.sh finds the ONNX file and refuses everything else itself
    if kind == "onnx":
        if fmt == "gguf":
            why = "llama.cpp has no converter from ONNX to GGUF, so no GGUF can be built from it."
            needs = ("A GGUF is built from the model's Hugging Face folder (config.json, the tokenizer files and the "
                     "weights).")
        else:
            why = f"the format {fmt} converts the Hugging Face folder of an LLM, not an ONNX model."
            needs = ""
        return " ".join(part for part in (f"The model is an ONNX model ({name}): {why}", onnx_next, needs) if part)
    if fmt != "gguf":
        return None  # rkllm_module.sh checks for a Hugging Face folder itself
    if kind == "weights-file":
        weights = "a SafeTensors file" if info["weights"] == "safetensors" else "a PyTorch weights file"
        return (f"'{name}' is {weights}, the weights only: the converter reads config.json, the tokenizer files and "
                "every weights file from the model's folder. Give the folder of the model as the input.")
    if kind == "gguf-folder":
        return ("The folder holds GGUF files but no Hugging Face model: give the GGUF file itself as the input (it "
                "needs no conversion; the target stage quantizes it).")
    if kind == "tflite":
        return (f"'{name}' is a TensorFlow Lite model; llama.cpp has no converter from it to GGUF. A GGUF is built "
                "from the model's Hugging Face folder.")
    if kind == "file":
        return (f"'{name}' is no model this stage converts: the input is a Hugging Face model folder (config.json, "
                "tokenizer, SafeTensors or PyTorch weights) or a GGUF file.")
    return None


# --- Integrity ---------------------------------------------------------------------------------------------------

def check_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise Problem(f"{path.name} in the model folder is no valid JSON (a download that broke off?).") from None


def check_safetensors(path: Path) -> int:
    """The header and the data of a SafeTensors file, as the safetensors library checks them: the header fits in
    the file, every tensor's size matches its shape, and the tensors fill the data exactly (no gap, no overlap, no
    missing end). Returns the number of tensors."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise Problem(f"{path.name} is incomplete or no SafeTensors file (shorter than its header length).")
        (length,) = struct.unpack("<Q", head)
        if length > SAFETENSORS_MAX_HEADER or 8 + length > size:
            raise Problem(f"{path.name} is incomplete or no SafeTensors file (its header does not fit in the file).")
        raw = f.read(length)
    try:
        header = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        header = None
    if not isinstance(header, dict):
        raise Problem(f"{path.name} has a damaged header (no JSON object).")
    tensors = []
    for name, entry in header.items():
        if name == "__metadata__":
            continue
        offsets = entry.get("data_offsets") if isinstance(entry, dict) else None
        shape = entry.get("shape") if isinstance(entry, dict) else None
        if not (isinstance(offsets, list) and len(offsets) == 2 and all(isinstance(o, int) for o in offsets)
                and isinstance(shape, list) and all(isinstance(d, int) and d >= 0 for d in shape)):
            raise Problem(f"{path.name} has a damaged header (a tensor without valid offsets or shape).")
        begin, end = offsets
        count = 1
        for dim in shape:
            count *= dim
        width = SAFETENSORS_DTYPES.get(entry.get("dtype"))
        if begin < 0 or end < begin or (width is not None and end - begin != count * width):
            raise Problem(f"{path.name} has a damaged header (a tensor's size does not match its shape).")
        tensors.append((begin, end))
    data = size - 8 - length
    position = 0
    for begin, end in sorted(tensors):
        if begin != position:
            raise Problem(f"{path.name} has a damaged header (its tensors overlap or leave gaps).")
        position = end
    if position > data:
        raise Problem(f"{path.name} is incomplete: its tensors need {position} bytes of data, the file has {data} "
                      "(an interrupted download?).")
    if position < data:
        raise Problem(f"{path.name} has {data - position} bytes after its last tensor (a damaged file?).")
    return len(tensors)


def check_pytorch(path: Path) -> None:
    """A PyTorch weights file: a complete zip archive (torch.save since 1.6; its table of contents is at the end, so a
    cut-off download has none) or a pickle (older torch.save)."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                archive.namelist()
        except (zipfile.BadZipFile, OSError, ValueError):
            raise Problem(f"{path.name} is incomplete: the PyTorch archive has no complete table of contents "
                          "(an interrupted download?).") from None
    elif not head.startswith(b"\x80"):
        raise Problem(f"{path.name} is neither a PyTorch archive nor a pickle (a damaged file?).")


def check_index(folder: Path, name: str) -> None:
    index = check_json(folder / name)
    weight_map = index.get("weight_map") if isinstance(index, dict) else None
    if not isinstance(weight_map, dict):
        raise Problem(f"{name} in the model folder has no weight_map.")
    missing = sorted({str(f) for f in weight_map.values() if not (folder / str(f)).is_file()})
    if missing:
        shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
        raise Problem(f"{name} lists weights files that are not in the model folder: {shown} (an incomplete "
                      "download?).")


class _Reader:
    """Bounded little-endian reads of a GGUF header; reading past the end is a Problem, never an exception."""

    def __init__(self, f, size: int, name: str):
        self.f, self.size, self.name = f, size, name

    def take(self, n: int) -> bytes:
        if n < 0 or self.f.tell() + n > self.size:
            raise Problem(f"{self.name} is incomplete: its header ends early (an interrupted copy?).")
        return self.f.read(n)

    def skip(self, n: int) -> None:
        if n < 0 or self.f.tell() + n > self.size:
            raise Problem(f"{self.name} is incomplete: its header ends early (an interrupted copy?).")
        self.f.seek(n, os.SEEK_CUR)

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self, keep: bool = True) -> Optional[str]:
        length = self.unpack("<Q")
        if not keep:
            self.skip(length)
            return None
        return self.take(length).decode("utf-8", errors="replace")

    def value(self, kind: int, depth: int = 0) -> Any:
        if kind in GGUF_SCALARS:
            return self.unpack(GGUF_SCALARS[kind])
        if kind == GGUF_STRING:
            return self.string()
        if kind == GGUF_ARRAY and depth < 2:
            item, count = self.unpack("<I"), self.unpack("<Q")
            if item in GGUF_SCALARS:
                self.skip(count * struct.calcsize(GGUF_SCALARS[item]))
            elif item == GGUF_STRING:
                for _ in range(count):
                    self.string(keep=False)
            else:
                for _ in range(count):
                    self.value(item, depth + 1)
            return count
        raise Problem(f"{self.name} has a damaged header (an unknown value type {kind}).")


def read_gguf(path: Path) -> Dict[str, Any]:
    """The header of a GGUF file, checked as llama.cpp's loader checks it: magic, version, the key-value pairs, the
    tensor infos with their offsets on the alignment, and every tensor's data inside the file."""
    size, name = path.stat().st_size, path.name
    with open(path, "rb") as f:
        r = _Reader(f, size, name)
        if size < 4 or r.take(4) != GGUF_MAGIC:
            raise Problem(f"{name} is no GGUF file (it does not start with GGUF).")
        version = r.unpack("<I")
        if version not in GGUF_VERSIONS:
            raise Problem(f"{name} has GGUF version {version}; llama.cpp reads the versions 2 and 3.")
        n_tensors, n_kv = r.unpack("<Q"), r.unpack("<Q")
        if n_tensors > size or n_kv > size:
            raise Problem(f"{name} has a damaged header (impossible counts).")
        meta: Dict[str, Any] = {}
        for _ in range(n_kv):
            key = r.string() or ""
            meta[key] = r.value(r.unpack("<I"))
        alignment = meta.get("general.alignment", GGUF_DEFAULT_ALIGNMENT)
        if not isinstance(alignment, int) or alignment <= 0 or alignment & (alignment - 1):
            raise Problem(f"{name} has a damaged header (alignment {alignment}).")
        tensors = []
        for _ in range(n_tensors):
            r.string(keep=False)
            n_dims = r.unpack("<I")
            if n_dims > GGML_MAX_DIMS:
                raise Problem(f"{name} has a damaged header (a tensor with {n_dims} dimensions).")
            dims = [r.unpack("<Q") for _ in range(n_dims)]
            tensors.append((dims, r.unpack("<I"), r.unpack("<Q")))
        data_start = -(-f.tell() // alignment) * alignment
    if not tensors:
        raise Problem(f"{name} holds no tensors.")
    end = data_start
    for dims, kind, offset in tensors:
        if offset % alignment:
            raise Problem(f"{name} has a damaged header (a tensor offset off the alignment).")
        nbytes = 0
        if kind in GGML_TYPES:
            _, block, width = GGML_TYPES[kind]
            count = 1
            for dim in dims:
                count *= dim
            if dims and dims[0] % block:
                raise Problem(f"{name} has a damaged header (a tensor row that does not fill its blocks).")
            nbytes = count // block * width
        end = max(end, data_start + offset + nbytes)
    if end > size:
        raise Problem(f"{name} is incomplete: its tensor data needs {end} bytes, the file has {size} (an interrupted "
                      "copy or conversion?).")
    arch = meta.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        raise Problem(f"{name} names no model architecture (general.architecture), so llama.cpp cannot load it.")
    return {"version": version, "tensors": len(tensors), "architecture": arch, "bytes": size,
            "embedding": f"{arch}.pooling_type" in meta or arch.endswith("bert") or arch == "t5encoder"}


def check_input(path: Path, info: Dict[str, Any]) -> str:
    """The integrity checks of the input; returns what was checked, for the log."""
    kind = info["kind"]
    if kind == "gguf":
        gguf = read_gguf(path)
        return (f"GGUF version {gguf['version']}, {plural(gguf['tensors'], 'tensor')}, "
                f"architecture {gguf['architecture']}")
    if kind == "onnx":
        for onnx in ([path] if path.is_file() else info["onnx"]):
            if onnx.stat().st_size == 0:
                raise Problem(f"{onnx.name} is empty.")
        return "not empty"
    if path.is_file() or kind == "gguf-folder":
        return "nothing"
    checked = []
    for name in JSON_FILES:
        if (path / name).is_file():
            check_json(path / name)
            checked.append(name)
    for name in INDEX_FILES:
        if (path / name).is_file():
            check_index(path, name)
    for weights in info["safetensors"]:
        check_safetensors(weights)
    for weights in info["pytorch"]:
        check_pytorch(weights)
    if info["safetensors"]:
        checked.append(plural(len(info["safetensors"]), "SafeTensors file"))
    if info["pytorch"]:
        checked.append(plural(len(info["pytorch"]), "PyTorch file"))
    return ", ".join(checked) if checked else "nothing to check"


# --- The stage's files -----------------------------------------------------------------------------------------------

def load(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# The keys of convert_stats.json ("conversion" in build_info.json), in this order.
STATS_KEYS = ("input_format", "weights", "input_bytes", "converted", "output_format", "output_type", "output_bytes",
              "ratio", "seconds", "functional_test", "functional_test_tool")


def write_stats(path: Path, values: Dict[str, Any], fresh: bool = False) -> Dict[str, Any]:
    stats = {} if fresh else load(path)
    stats.update(values)
    stats = {key: stats[key] for key in sorted(stats, key=lambda k: STATS_KEYS.index(k) if k in STATS_KEYS else 99)}
    save(path, stats)
    return stats


def plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


# --- Functional test ---------------------------------------------------------------------------------------------

def functional_test(gguf: Path, tools: Path, seconds: int) -> Tuple[str, Optional[str]]:
    """A short run of the GGUF: (passed | timed out | stopped | skipped, the tool). A run that ends with an error is a
    Problem: the GGUF the converter wrote does not work."""
    tool = next((tools / n for n in TEST_TOOLS if (tools / n).is_file() and os.access(tools / n, os.X_OK)), None)
    if tool is None:
        log("Functional test skipped: this image has neither llama-completion nor llama-cli to run the model.")
        return "skipped", None
    try:
        shown = subprocess.run([str(tool), "--help"], check=False, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, errors="replace", timeout=60)
        options = shown.stdout + shown.stderr
    except (OSError, subprocess.TimeoutExpired):
        log(f"Functional test skipped: {tool.name} --help did not answer.")
        return "skipped", tool.name
    # One prompt, no conversation: plain completion (-no-cnv), or a single turn of the chat program
    mode = next((option for option in ("-no-cnv", "--single-turn") if option in options), None)
    if mode is None:
        log(f"Functional test skipped: this {tool.name} has no option for a run without a conversation.")
        return "skipped", tool.name
    args = [str(tool), "-m", str(gguf), "-p", "Hello", "-n", str(TEST_TOKENS), "-c", "256", mode]
    for option, value in (("--offline", None), ("--no-warmup", None), ("--temp", "0"), ("--simple-io", None)):
        if option in options:
            args += [option] + ([value] if value else [])
    started = time.monotonic()
    try:
        run = subprocess.run(args, check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True, errors="replace", timeout=seconds)
    except subprocess.TimeoutExpired:
        log(f"Functional test: {tool.name} did not finish within {seconds} s on this build host (a large model?); "
            "not counted as a failure.")
        return "timed out", tool.name
    except OSError as e:
        log(f"Functional test skipped: {tool.name} could not be started ({e.strerror or e}).")
        return "skipped", tool.name
    took = int(time.monotonic() - started)
    if run.returncode == 0:
        log(f"Functional test passed: {tool.name} loaded {gguf.name} and generated {TEST_TOKENS} tokens ({took} s).")
        return "passed", tool.name
    if run.returncode < 0:
        log(f"Functional test: {tool.name} was stopped by signal {-run.returncode} (not enough memory?); not counted "
            "as a failure.")
        return "stopped", tool.name
    if run.returncode in (126, 127):
        # The tool itself did not start (a library of the image missing, no permission): nothing said about the GGUF
        log(f"Functional test skipped: {tool.name} does not start in this image (exit {run.returncode}).")
        return "skipped", tool.name
    tail = [line for line in (run.stderr or "").splitlines() if line.strip()][-6:]
    raise Problem(f"The functional test failed: {tool.name} could not run {gguf.name} (exit {run.returncode}), so "
                  "the GGUF the converter wrote does not work." + ("" if not tail else " Its last lines:\n  "
                                                                   + "\n  ".join(tail)))


# --- Commands ----------------------------------------------------------------------------------------------------

def cmd_input(args) -> None:
    path = Path(args.path)
    if not path.exists():
        raise Problem("The model input does not exist in the container.")
    info = detect(path)
    reason = refusal(info, path, args.format, args.onnx_next)
    if reason:
        raise Problem(reason)
    checked = check_input(path, info)
    size = tree_bytes(path)
    log(f"Input: {describe(info)}, {size_text(size)}; checked: {checked}.")
    kind = info["kind"]
    stats = {"input_format": kind, "input_bytes": size, "converted": kind != "gguf"}
    if kind == "huggingface":
        stats["weights"] = info["weights"]
    write_stats(Path(args.stats), stats, fresh=True)
    if kind == "gguf" and args.format == "gguf":
        log("Nothing to convert: the input is a GGUF file already, which the target stage quantizes.")


def cmd_begin(args) -> None:
    path, state_file = Path(args.file), Path(args.state)
    if not path.parent.is_dir():
        raise Problem(f"The folder for {path.name} does not exist.")
    if path.is_dir():
        raise Problem(f"{path.name} is a folder; the converter writes a file there.")
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    state = load(state_file)
    entry = {"path": str(path), "backup": None}
    if any(e.get("path") == str(path) for e in state.get("files", [])):
        if path.exists():
            path.unlink()  # written earlier in this run and not kept yet: its first entry knows the backup
        return
    if path.exists():
        scratch = Path(args.scratch).resolve() if args.scratch else None
        if scratch is not None and path.resolve().parent == scratch and path.name.startswith("tmp-"):
            path.unlink()  # an intermediate of an earlier run of this stage, in the work folder
        else:
            if os.path.lexists(backup):
                raise Problem(f"{backup.name} is in the way: move it away and start again (this stage keeps the "
                              f"file it replaces there until the new {path.name} passed its checks).")
            os.rename(path, backup)
            entry["backup"] = str(backup)
            log(f"Kept the earlier {path.name} as {backup.name} until the new one passed its checks.")
    state.setdefault("files", []).append(entry)
    save(state_file, state)


def cmd_watch(args) -> None:
    folder, state_file = Path(args.dir), Path(args.state)
    state = load(state_file)
    state["watch"] = {"dir": str(folder), "before": sorted(p.name for p in folder.iterdir()) if folder.is_dir()
                      else []}
    save(state_file, state)


def cmd_output(args) -> None:
    stats_file = Path(args.stats)
    if args.watched:
        watch = load(Path(args.state)).get("watch") or {}
        folder, before = Path(watch.get("dir", ".")), set(watch.get("before", []))
        new = [p for p in folder.iterdir() if p.is_file() and p.name not in before] if folder.is_dir() else []
        if not new:
            raise Problem(f"The {args.format} converter finished but wrote no file.")
        path = max(new, key=lambda p: p.stat().st_size)
    else:
        path = Path(args.file)
    if not path.is_file() or path.stat().st_size == 0:
        raise Problem(f"The converter finished but {path.name} is missing or empty.")
    kind = args.type or (path.stem.rsplit("-", 1)[-1] if "-" in path.stem else "")
    stats = load(stats_file)
    values: Dict[str, Any] = {"converted": True, "output_format": args.format, "output_type": kind or None,
                              "output_bytes": path.stat().st_size, "seconds": args.seconds}
    gguf = None
    if args.format == "gguf":
        gguf = read_gguf(path)
        log(f"Output: {path.name}, GGUF version {gguf['version']}, {plural(gguf['tensors'], 'tensor')}, "
            f"architecture {gguf['architecture']}, {size_text(gguf['bytes'])}; its tensor data lies inside the file.")
    if args.test_skip:
        log(f"Functional test skipped: {args.test_skip}.")
        result, tool = "skipped", None
    elif gguf is not None and gguf["embedding"]:
        log("Functional test skipped: an embedding model does not generate text; its header was checked.")
        result, tool = "skipped", None
    elif gguf is not None and args.tools:
        result, tool = functional_test(path, Path(args.tools), args.test_seconds)
    else:
        result, tool = "skipped", None
    values["functional_test"] = result
    if tool:
        values["functional_test_tool"] = tool
    inputs = stats.get("input_bytes")
    values["ratio"] = round(inputs / values["output_bytes"], 2) if isinstance(inputs, int) and inputs else None
    stats = write_stats(stats_file, values)
    shown_type = f" {kind}" if kind else ""
    weights = f", {stats['weights']}" if stats.get("weights") else ""
    ratio = "-" if values["ratio"] is None else values["ratio"]
    log(f"Statistics: input {size_text(inputs)} ({stats.get('input_format', '?')}{weights}), output "
        f"{size_text(values['output_bytes'])} ({args.format}{shown_type}), ratio {ratio} (input / output), "
        f"conversion {args.seconds} s, functional test {result}.")


def cmd_restore(args) -> None:
    state_file = Path(args.state)
    if not state_file.is_file():
        return
    state = load(state_file)
    if args.stopped:
        log("An earlier run of this stage did not finish: removing what it wrote.")
    for entry in reversed(state.get("files", [])):
        path = Path(entry["path"])
        if os.path.lexists(path):
            path.unlink()
            log(f"Removed the unfinished {path.name}.")
        backup = entry.get("backup")
        if backup and os.path.lexists(backup):
            os.replace(backup, path)
            log(f"Put back the earlier {path.name}.")
    watch = state.get("watch") or {}
    folder = Path(watch.get("dir", ""))
    # New files of a folder a format helper wrote into: only in the run that failed (after a stopped run the person
    # may have put files there since)
    if args.failed and watch and folder.is_dir():
        before = set(watch.get("before", []))
        for path in sorted(p for p in folder.iterdir() if p.is_file() and p.name not in before):
            path.unlink()
            log(f"Removed the unfinished {path.name}.")
    state_file.unlink()


def cmd_keep(args) -> None:
    state_file = Path(args.state)
    if not state_file.is_file():
        return
    for entry in load(state_file).get("files", []):
        backup = entry.get("backup")
        if backup and os.path.lexists(backup):
            os.unlink(backup)
            log(f"Replaced the earlier {Path(entry['path']).name}.")
    state_file.unlink()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="model_check.py", description="The checks of the convert stage.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("input")
    s.add_argument("path")
    s.add_argument("--format", required=True)
    s.add_argument("--stats", required=True)
    s.add_argument("--onnx-next", default="")
    s = sub.add_parser("begin")
    s.add_argument("file")
    s.add_argument("--state", required=True)
    s.add_argument("--scratch", default="")
    s = sub.add_parser("watch")
    s.add_argument("dir")
    s.add_argument("--state", required=True)
    s = sub.add_parser("output")
    s.add_argument("file", nargs="?", default="")
    s.add_argument("--watched", action="store_true")
    s.add_argument("--stats", required=True)
    s.add_argument("--state", default="")
    s.add_argument("--format", required=True)
    s.add_argument("--type", default="")
    s.add_argument("--seconds", type=int, default=0)
    s.add_argument("--tools", default="")
    s.add_argument("--test-seconds", type=int, default=120)
    s.add_argument("--test-skip", default="")
    s = sub.add_parser("restore")
    s.add_argument("--state", required=True)
    group = s.add_mutually_exclusive_group(required=True)
    group.add_argument("--failed", action="store_true")
    group.add_argument("--stopped", action="store_true")
    s = sub.add_parser("keep")
    s.add_argument("--state", required=True)
    return p


COMMANDS = {"input": cmd_input, "begin": cmd_begin, "watch": cmd_watch, "output": cmd_output,
            "restore": cmd_restore, "keep": cmd_keep}


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except Problem as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        return 1
    except OSError as e:
        print(f"ERROR: {args.command}: {e.strerror or e} ({Path(e.filename or '').name or 'no file'}).",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
