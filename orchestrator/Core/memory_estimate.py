#!/usr/bin/env python3
"""
The memory estimate before a build starts: how much memory the model needs on the board at the chosen quantization,
against the board's memory, before anything is downloaded or built. A warning and never a refusal: the person
decides what they ask of their board (the board profiles' memory rule, board_profiles.memory_warning, which compares
the real model file after the build with the same thresholds).

One function, estimate(), for every caller: the Builder's line under the quantization, `llm-cli build start` and
`build preflight`, the AI's start question (build_plan) and the build's record (build_info.json "memory_estimate", next
to the real file size, so later work can calibrate it). The parts:

- the weights: what llama.cpp's quantizer writes for the model's shape (ModelShape: hidden size, layers, heads, KV
  heads, head size, feed-forward size, vocabulary, tied embeddings), tensor by tensor with the type llama.cpp picks
  for it at the pin (src/llama-quant.cpp: llama_tensor_get_type_impl and tensor_type_fallback; embeddings and output
  usually stay at Q6_K or Q8_0, the first and last layers' attention V and feed-forward down get more bits in the
  _M mixes, a row length that is no multiple of the type's block falls back to a 32-block type), and the block sizes
  of ggml's types (ggml/src/ggml-common.h). Without a shape: the parameter count times the type's bits per weight,
  marked as the rougher estimate. Parameters the shape does not explain (a part of the model the shape leaves out)
  are added at the type's bits per weight;
- the server's buffers: SERVER_SHARE (a tenth) of the weights, the share of the post-build warning;
- the KV cache for the context the board plans room for (the request's context, else measure.CTX_FLOOR, at most the
  model's training context): layers x KV heads x head size x 2 (K and V) x tokens x the bytes of the KV type, q8_0
  when less than hardware.KV_LITTLE_RAM is left next to the model and the board's reserve (the boards' rule,
  hardware.kv_kind_for_left), else f16.

The need is compared with the board's memory (the probe's RAM, else the most memory the board comes with,
`ram_max_gb`) by memory_state, the thresholds of the post-build warning: above the memory "does not
fit" (it runs only with swap, if at all), above three quarters of it "tight". Every number is an estimate and says
so; the board measurement on the board (runtime/device/measure.py) measures what really fits.

QUANT_BITS is the framework's one table of bits per weight of llama.cpp's quantization types (the types' block sizes
at the pin); disk_plan.bits_of reads it too.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

MIB = 1024 * 1024
PROBE_SOURCE = "the board's probe"   # board_profiles.board_memory's source when the probe gave the memory

# The server's buffers next to the weights (compute buffers) as a share of the model file: an estimate for the
# warning only; the board measurement says the real need. The KV cache of the context comes on top when it is known
# (the memory estimate before the build counts it, and the warning after the build takes the same number).
SERVER_SHARE = 0.10
TIGHT_SHARE = 0.75


def memory_state(need_mib: float, ram_mib: float) -> str:
    """The verdict of a need against the board's memory, for the estimate before the build (memory_estimate) and the
    warning after it alike (board_profiles.memory_warning), so the two never disagree on the same numbers:
    does_not_fit above the memory, tight above TIGHT_SHARE of it, else fits."""
    if need_mib > ram_mib:
        return "does_not_fit"
    return "tight" if need_mib > ram_mib * TIGHT_SHARE else "fits"


def memory_need_mib(model_bytes: int, kv_bytes: int = 0) -> int:
    """MiB a model needs on the board: the file plus SERVER_SHARE of it for the server, plus the KV cache when it is
    known."""
    return int((model_bytes * (1 + SERVER_SHARE) + max(0, int(kv_bytes or 0))) / 1048576 + 0.5)


# --- ggml's types: (values per block, bytes per block), ggml/src/ggml-common.h at the pin ---------------------------
GGML_BLOCKS: Dict[str, Tuple[int, int]] = {
    "F32": (1, 4), "F16": (1, 2), "BF16": (1, 2),
    "Q4_0": (32, 18), "Q4_1": (32, 20), "Q5_0": (32, 22), "Q5_1": (32, 24), "Q8_0": (32, 34),
    "Q2_0": (64, 18), "Q1_0": (128, 18),
    "Q2_K": (256, 84), "Q3_K": (256, 110), "Q4_K": (256, 144), "Q5_K": (256, 176), "Q6_K": (256, 210),
    "IQ1_S": (256, 50), "IQ1_M": (256, 56), "IQ2_XXS": (256, 66), "IQ2_XS": (256, 74), "IQ2_S": (256, 82),
    "IQ3_XXS": (256, 98), "IQ3_S": (256, 110), "IQ4_NL": (32, 18), "IQ4_XS": (256, 136),
    "TQ1_0": (256, 54), "TQ2_0": (256, 66),
}

# llama.cpp's quantization types (tools/quantize/quantize.cpp) -> the ggml type of their tensors before the per-tensor
# rules (src/llama-quant.cpp llama_ftype_get_default_type)
FTYPE_BASE: Dict[str, str] = {
    "Q4_0": "Q4_0", "Q4_1": "Q4_1", "Q5_0": "Q5_0", "Q5_1": "Q5_1", "Q8_0": "Q8_0", "F16": "F16", "BF16": "BF16",
    "F32": "F32", "Q2_0": "Q2_0", "Q2_K": "Q2_K", "Q2_K_S": "Q2_K", "IQ3_XS": "IQ3_S", "Q3_K_S": "Q3_K",
    "Q3_K_M": "Q3_K", "Q3_K_L": "Q3_K", "Q4_K_S": "Q4_K", "Q4_K_M": "Q4_K", "Q5_K_S": "Q5_K", "Q5_K_M": "Q5_K",
    "Q6_K": "Q6_K", "TQ1_0": "TQ1_0", "TQ2_0": "TQ2_0", "IQ2_XXS": "IQ2_XXS", "IQ2_XS": "IQ2_XS", "IQ2_S": "IQ2_XS",
    "IQ2_M": "IQ2_S", "IQ3_XXS": "IQ3_XXS", "IQ1_S": "IQ1_S", "IQ1_M": "IQ1_M", "IQ4_NL": "IQ4_NL",
    "IQ4_XS": "IQ4_XS", "IQ3_S": "IQ3_S", "IQ3_M": "IQ3_S",
}


def _bits(ggml_type: str) -> float:
    block, size = GGML_BLOCKS[ggml_type]
    return size * 8 / block


# The one table of bits per weight: the base type's bits of each quantization type (what quantize.cpp's list says for
# the IQ types: IQ4_XS 4.25, IQ3_M on IQ3_S 3.44, IQ2_M on IQ2_S 2.56; a whole model has a little more, see estimate).
QUANT_BITS: Dict[str, float] = {name: _bits(base) for name, base in FTYPE_BASE.items()}

# The NPU formats' types (RKLLM w4a16, w8a8, their group variants; RKNN i8/fp16): the weights' bits plus a scale
# per group (an assumption of half a bit for 4-bit types, a quarter for 8-bit ones); always the rough estimate.
_NPU_TYPE = re.compile(r"^w(\d+)a(\d+)(?:_g(\d+))?$")
_NPU_PLAIN = {"i8": 8.0, "int8": 8.0, "u8": 8.0, "fp16": 16.0, "f16": 16.0}


def bits_per_weight(quantization: Optional[str]) -> Optional[float]:
    """Bits per weight of a quantization type: llama.cpp's types from QUANT_BITS, the NPU formats' types by their
    weight bits; None for a type the table does not know."""
    q = str(quantization or "").strip()
    if not q:
        return None
    if q.upper() in QUANT_BITS:
        return QUANT_BITS[q.upper()]
    low = q.lower()
    if low in _NPU_PLAIN:
        return _NPU_PLAIN[low]
    m = _NPU_TYPE.match(low)
    if m:
        bits = int(m.group(1))
        return bits + (0.5 if bits <= 4 else 0.25)
    return None


# --- the model's shape ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelShape:
    """What the estimate knows of a model before it is downloaded: its parameter count and, when known, the shape of
    a dense transformer (hidden size, layers, heads, KV heads, head size, feed-forward size, vocabulary, tied input
    and output embeddings, biases of q/k/v, the training context). `source` says where it comes from (a label, never
    a path)."""
    parameters: Optional[int] = None
    hidden: Optional[int] = None
    layers: Optional[int] = None
    heads: Optional[int] = None
    kv_heads: Optional[int] = None
    head_dim: Optional[int] = None
    intermediate: Optional[int] = None
    vocab: Optional[int] = None
    tied: bool = True
    qkv_bias: bool = False
    experts: int = 0
    train_ctx: Optional[int] = None
    source: str = ""

    @property
    def detailed(self) -> bool:
        """The shape is enough for the per-tensor estimate (a dense model; a mixture of experts is not modelled)."""
        return bool(self.hidden and self.layers and self.heads and self.kv_heads and self.head_dim
                    and self.intermediate and self.vocab) and self.experts <= 1

    @property
    def has_kv(self) -> bool:
        return bool(self.layers and self.kv_heads and self.head_dim)

    def shape_parameters(self) -> Optional[int]:
        """The parameters the shape accounts for (weights and norms), None without a full shape."""
        if not self.detailed:
            return None
        return sum(rows * cols for _name, _cat, _layer, cols, rows in _tensors(self))

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", 0) or k == "tied"}


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def shape_from_config(config: Mapping[str, Any], parameters: Optional[int] = None,
                      source: str = "the model's config.json") -> ModelShape:
    """The shape from a Hugging Face config.json (a multimodal model's text part from `text_config`): hidden_size,
    num_hidden_layers, num_attention_heads, num_key_value_heads (default: the heads), head_dim (default: hidden size
    / heads), intermediate_size, vocab_size, tie_word_embeddings (transformers' default: tied), the q/k/v biases
    (attention_bias, or Qwen2's always), the experts and max_position_embeddings. Untrusted input: only positive
    numbers are taken."""
    config = config if isinstance(config, Mapping) else {}
    text = config.get("text_config") if isinstance(config.get("text_config"), Mapping) else None
    c = {**config, **text} if text else config
    hidden, heads = _int(c.get("hidden_size") or c.get("n_embd") or c.get("d_model")), \
        _int(c.get("num_attention_heads") or c.get("n_head"))
    head_dim = _int(c.get("head_dim")) or (hidden // heads if hidden and heads and hidden % heads == 0 else None)
    experts = _int(c.get("num_local_experts") or c.get("num_experts") or c.get("n_routed_experts")) or 0
    tied = c.get("tie_word_embeddings", config.get("tie_word_embeddings", True))
    model_type = str(c.get("model_type") or config.get("model_type") or "").lower()
    return ModelShape(parameters=_int(parameters), hidden=hidden,
                      layers=_int(c.get("num_hidden_layers") or c.get("n_layer")), heads=heads,
                      kv_heads=_int(c.get("num_key_value_heads")) or heads, head_dim=head_dim,
                      intermediate=_int(c.get("intermediate_size") or c.get("ffn_dim")),
                      vocab=_int(c.get("vocab_size")), tied=tied is not False,
                      qkv_bias=c.get("attention_bias") is True or model_type == "qwen2",
                      experts=experts, train_ctx=_int(c.get("max_position_embeddings")), source=source)


def shape_from_gguf(path: Path) -> ModelShape:
    """The shape from a GGUF file's header (llama.cpp's keys <arch>.embedding_length, .block_count,
    .attention.head_count, .attention.head_count_kv, .attention.key_length, .feed_forward_length, .context_length,
    .expert_count and the length of tokenizer.ggml.tokens). The header does not say whether the output shares the
    embeddings: counted as not shared (the larger estimate). ValueError when the header cannot be read."""
    from orchestrator.runtime.bundle.generator import BundleError, gguf_header
    try:
        meta = gguf_header(Path(path))
    except (BundleError, OSError) as e:
        raise ValueError(str(e)) from None
    arch = str(meta.get("general.architecture") or "")

    def key(name: str) -> Optional[int]:
        return _int(meta.get(f"{arch}.{name}"))
    hidden, heads = key("embedding_length"), key("attention.head_count")
    head_dim = key("attention.key_length") or (hidden // heads if hidden and heads and hidden % heads == 0 else None)
    return ModelShape(hidden=hidden, layers=key("block_count"), heads=heads,
                      kv_heads=key("attention.head_count_kv") or heads, head_dim=head_dim,
                      intermediate=key("feed_forward_length"), vocab=_int(meta.get("tokenizer.ggml.tokens")),
                      tied=False, experts=key("expert_count") or 0, train_ctx=key("context_length"),
                      source="the GGUF file's header")


def shape_from_local(path: Path, parameters: Optional[int] = None) -> Optional[ModelShape]:
    """The shape of a local model: a folder's config.json (its safetensors index's size as 16-bit parameters when no
    count is given), a GGUF file's header; None when neither can be read."""
    path = Path(path)
    try:
        if path.is_file() and path.suffix.lower() == ".gguf":
            return shape_from_gguf(path)
        folder = path if path.is_dir() else path.parent
        config = folder / "config.json"
        if config.is_file() and config.stat().st_size <= 1 << 20:
            data = json.loads(config.read_text(encoding="utf-8"))
            if parameters is None:
                parameters = _index_parameters(folder)
            return shape_from_config(data if isinstance(data, Mapping) else {}, parameters,
                                     source="the model folder's config.json")
        ggufs = sorted(folder.glob("*.gguf")) if path.is_dir() else []
        if ggufs:
            return shape_from_gguf(ggufs[0])
    except (OSError, ValueError, UnicodeError):
        return None
    return None


def _index_parameters(folder: Path) -> Optional[int]:
    """The parameter count a safetensors index implies (its metadata.total_size read as 16-bit weights), None
    without one."""
    index = folder / "model.safetensors.index.json"
    try:
        if index.is_file() and index.stat().st_size <= 16 << 20:
            data = json.loads(index.read_text(encoding="utf-8"))
            total = _int((data.get("metadata") or {}).get("total_size")) if isinstance(data, Mapping) else None
            return total // 2 if total else None
    except (OSError, ValueError, UnicodeError, AttributeError):
        return None
    return None


# --- the weights, tensor by tensor ----------------------------------------------------------------------------------

_IQ2_GROUP = frozenset({"IQ2_XXS", "IQ2_XS", "IQ1_S", "IQ2_S", "IQ2_M", "IQ1_M"})
_OUTPUT_Q5K = frozenset({"IQ2_XXS", "IQ2_XS", "IQ3_XXS", "IQ1_S", "IQ2_S", "IQ2_M", "IQ1_M"})
_FALLBACK = {"IQ1_S": "IQ4_NL", "IQ1_M": "IQ4_NL", "IQ2_XXS": "IQ4_NL", "IQ2_XS": "IQ4_NL", "IQ2_S": "IQ4_NL",
             "IQ3_XXS": "IQ4_NL", "IQ3_S": "IQ4_NL", "IQ4_XS": "IQ4_NL", "Q2_0": "Q4_0", "Q2_K": "Q4_0",
             "Q3_K": "Q4_0", "TQ1_0": "Q4_0", "TQ2_0": "Q4_0", "Q4_K": "Q5_0", "Q5_K": "Q5_1", "Q6_K": "Q8_0"}
# GGUF's metadata next to the tensors, mostly the tokenizer (tokens, their types and the merges): about this many
# bytes per vocabulary entry (from the published files the tests compare with: 1.9 to 8 MB for 49k to 152k tokens)
METADATA_PER_TOKEN = 50


def _tensors(shape: ModelShape) -> Iterable[Tuple[str, str, int, int, int]]:
    """(name, category, layer, columns, rows) of every tensor of a dense transformer as llama.cpp's GGUF holds it;
    columns is the row length (ne[0]) the block size must divide."""
    h, q_rows, kv_rows = shape.hidden, shape.heads * shape.head_dim, shape.kv_heads * shape.head_dim
    yield "token_embd", "token_embd", -1, h, shape.vocab
    if not shape.tied:
        yield "output", "output", -1, h, shape.vocab
    yield "output_norm", "norm", -1, h, 1
    for i in range(shape.layers):
        yield "attn_norm", "norm", i, h, 1
        yield "ffn_norm", "norm", i, h, 1
        yield "attn_q", "attn_q", i, h, q_rows
        yield "attn_k", "attn_k", i, h, kv_rows
        yield "attn_v", "attn_v", i, h, kv_rows
        yield "attn_output", "attn_output", i, q_rows, h
        yield "ffn_gate", "ffn_gate", i, h, shape.intermediate
        yield "ffn_up", "ffn_up", i, h, shape.intermediate
        yield "ffn_down", "ffn_down", i, shape.intermediate, h
        if shape.qkv_bias:
            yield "attn_bias", "norm", i, q_rows + 2 * kv_rows, 1


def _more_bits(i: int, n: int) -> bool:
    return i < n // 8 or i >= 7 * n // 8 or (i - n // 8) % 3 == 2


def tensor_type(ftype: str, category: str, layer: int, layers: int, columns: int, *, n_gqa: int = 1,
                imatrix: bool = True, is_70b: bool = False) -> str:
    """The ggml type llama.cpp's quantizer gives one tensor of a dense model (no experts, not Falcon) for the
    quantization type `ftype`: llama_tensor_get_type_impl and tensor_type_fallback of src/llama-quant.cpp at the pin.
    Norms and biases stay F32; F16, BF16 and F32 builds keep every matrix at their type."""
    base = FTYPE_BASE[ftype]
    if category == "norm":
        return "F32"
    if base in ("F16", "BF16", "F32"):
        return base
    t = base
    if category in ("output", "token_embd_tied"):
        if columns % GGML_BLOCKS[t][0]:
            t = "Q8_0"
        elif ftype in _OUTPUT_Q5K:
            t = "Q5_K"
        elif t != "Q8_0":
            t = "Q6_K"
    elif category == "token_embd":
        if ftype in ("IQ2_XXS", "IQ2_XS", "IQ1_S", "IQ1_M"):
            t = "Q2_K"
        elif ftype in ("IQ2_S", "IQ2_M", "IQ3_XXS"):
            t = "IQ3_S"
        elif ftype in ("TQ1_0", "TQ2_0", "Q2_0"):
            t = "Q4_K"
    elif ftype in _IQ2_GROUP:
        iq2sm = ftype in ("IQ2_S", "IQ2_M")
        if category == "attn_v":
            t = "Q4_K" if n_gqa >= 4 else ("IQ3_S" if iq2sm else "Q2_K")
        elif category == "ffn_down" and layer < layers // 8:
            t = "IQ3_S" if iq2sm else "Q2_K"
        elif category == "attn_output":
            if ftype in ("IQ1_S", "IQ1_M"):
                t = "IQ2_XXS"
            elif iq2sm:
                t = "IQ3_S"
    elif category == "attn_v":
        if ftype == "Q2_K":
            t = "Q4_K" if n_gqa >= 4 else "Q3_K"
        elif ftype == "Q2_K_S" and n_gqa >= 4:
            t = "Q4_K"
        elif ftype == "IQ3_XXS":
            t = "Q4_K" if n_gqa >= 4 else ("IQ3_XXS" if imatrix else "IQ3_S")
        elif ftype in ("IQ3_XS", "IQ3_S") and n_gqa >= 4:
            t = "Q4_K"
        elif ftype == "IQ3_M":
            t = "Q4_K"
        elif ftype == "Q3_K_M":
            t = "Q5_K" if layer < 2 else "Q4_K"
        elif ftype == "Q3_K_L":
            t = "Q5_K"
        elif ftype in ("IQ4_NL", "IQ4_XS") and n_gqa >= 4:
            t = "Q5_K"
        elif ftype in ("Q4_K_M", "Q5_K_M") and _more_bits(layer, layers):
            t = "Q6_K"
        elif ftype == "Q4_K_S" and layer < 4:
            t = "Q5_K"
        if is_70b and t in ("Q3_K", "Q4_K"):
            t = "Q5_K"
    elif category == "attn_k":
        if ftype == "IQ3_XS":
            t = "IQ3_XXS"
        elif ftype == "IQ3_XXS":
            t = "IQ2_S"
    elif category == "attn_q":
        if ftype == "IQ3_XS":
            t = "IQ3_XXS"
        elif ftype == "IQ3_XXS":
            t = "IQ2_S"
    elif category == "ffn_down":
        n8 = layers // 8
        if ftype == "Q2_K":
            t = "Q3_K"
        elif ftype == "Q2_K_S":
            if layer < n8:
                t = "Q4_K"
        elif ftype == "IQ3_XXS" and not imatrix:
            t = "Q4_K" if layer < n8 else "Q3_K"
        elif ftype == "Q3_K_M":
            t = "Q5_K" if layer < layers // 16 else "Q4_K"
        elif ftype == "IQ3_M" and layer < n8:
            t = "Q4_K"
        elif ftype == "Q3_K_L":
            t = "Q5_K"
        elif ftype == "Q4_K_M":
            if _more_bits(layer, layers):
                t = "Q6_K"
        elif layer < n8 and ftype in ("IQ4_NL", "IQ4_XS") and not imatrix:
            t = "Q5_K"
        elif ftype == "Q5_K_M" and _more_bits(layer, layers):
            t = "Q6_K"
        elif ftype == "Q4_K_S" and layer < n8:
            t = "Q5_K"
        elif ftype in ("Q4_0", "Q5_0") and imatrix and layer < n8:
            t = "Q4_1" if ftype == "Q4_0" else "Q5_1"
    elif category == "attn_output":
        t = {"Q2_K": "Q3_K", "IQ3_XXS": "IQ3_S", "Q3_K_M": "Q4_K", "Q3_K_L": "Q5_K", "IQ3_M": "Q4_K"}.get(ftype, t)
    elif category in ("ffn_gate", "ffn_up"):
        if ftype == "IQ3_XS" and layers // 8 <= layer < 7 * layers // 8:
            t = "IQ3_XXS"
    # a row length the type's block does not divide: llama.cpp's fallback type, else F16
    if columns % GGML_BLOCKS[t][0]:
        t = _FALLBACK.get(t, t)
        if columns % GGML_BLOCKS[t][0]:
            t = "F16"
    return t


def _tensor_bytes(ggml_type: str, columns: int, rows: int) -> int:
    block, size = GGML_BLOCKS[ggml_type]
    return -(-columns // block) * size * rows


def weights_bytes(shape: ModelShape, quantization: str, *, imatrix: bool = True,
                  tensor_types: Sequence[str] = ()) -> Tuple[Optional[int], str]:
    """(bytes of the model file, how it was estimated) at `quantization`: per tensor from the shape (`per_tensor`),
    else the parameter count times the bits per weight (`parameters`); (None, "") when neither is known.
    `tensor_types`: the build's per-tensor types (Core/tensor_types.py, canonical entries), which replace the
    quantizer's own choice for the tensors they match, as llama-quantize applies them (the fallback of a row length
    the block does not divide still applies)."""
    q = str(quantization or "").strip().upper()
    bits = bits_per_weight(quantization)
    if q in FTYPE_BASE and shape.detailed:
        n_gqa = max(1, shape.heads // max(1, shape.kv_heads))
        is_70b = shape.layers == 80 and shape.hidden == 8192
        total = 0
        counted = 0
        for _name, cat, layer, cols, rows in _tensors(shape):
            category = "token_embd_tied" if cat == "token_embd" and shape.tied else cat
            t = tensor_type(q, category, layer, shape.layers, cols, n_gqa=n_gqa, imatrix=imatrix, is_70b=is_70b)
            if tensor_types and cat != "norm" and q not in ("F16", "BF16", "F32"):
                chosen = _override(tensor_types, _name, category, layer)
                if chosen in GGML_BLOCKS:
                    t = chosen if not cols % GGML_BLOCKS[chosen][0] else _FALLBACK.get(chosen, "F16")
                    if cols % GGML_BLOCKS[t][0]:
                        t = "F16"
            total += _tensor_bytes(t, cols, rows)
            counted += cols * rows
        # parameters the shape does not explain (another part of the model): at the type's bits per weight
        rest = (shape.parameters or 0) - counted
        if rest > counted * 0.02:
            total += int(rest * QUANT_BITS[q] / 8)
        return total + shape.vocab * METADATA_PER_TOKEN, "per_tensor"
    if bits is not None and shape.parameters:
        return int(shape.parameters * bits / 8), "parameters"
    return None, ""


def _override(tensor_types: Sequence[str], name: str, category: str, layer: int) -> Optional[str]:
    """The per-tensor type an entry gives this tensor (tensor_types.type_for on llama.cpp's tensor name)."""
    from orchestrator.Core.tensor_types import type_for
    full = f"blk.{layer}.{name}.weight" if layer >= 0 else f"{name}.weight"
    return type_for(tensor_types, full, category)


def kv_bytes(shape: ModelShape, ctx: int, kv_type: str = "f16") -> int:
    """The KV cache of `ctx` tokens: layers x KV heads x head size x 2 (K and V) x tokens x the bytes of `kv_type`
    (f16 two bytes; q8_0 and q4_0 by their ggml block size, hardware.KV_TYPE_FACTORS); 0 without the shape."""
    from orchestrator.runtime.hardware import KV_TYPE_FACTORS
    if not shape.has_kv or ctx <= 0:
        return 0
    per_value = 2 * KV_TYPE_FACTORS.get(kv_type, 1.0)
    return int(shape.layers * shape.kv_heads * shape.head_dim * 2 * ctx * per_value)


# --- the estimate ---------------------------------------------------------------------------------------------------

@dataclass
class MemoryEstimate:
    """The estimate of one model at one quantization on one board. `state`: fits, tight, does_not_fit (the thresholds
    of memory_state) or unknown (no size or no board memory known; `reason` says which)."""
    board: str
    quantization: str
    state: str = "unknown"
    weights_bytes: Optional[int] = None
    server_bytes: Optional[int] = None
    kv_bytes: int = 0
    ctx: int = 0
    kv_type: str = ""
    need_mib: Optional[int] = None
    ram_mib: Optional[int] = None
    ram_source: str = ""
    basis: str = ""            # per_tensor, parameters
    shape_source: str = ""
    reason: str = ""
    reason_code: str = ""      # no_shape, no_quantization, unknown_type, no_parameters, no_memory (mem.reason.*)
    uncertainty: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """What build_info.json and the AI's facts carry: numbers and words, never a path."""
        out = {k: v for k, v in asdict(self).items() if k != "extras" and v not in (None, "")}
        out.update(self.extras)
        return out

    def text(self, lang: Optional[str] = "en") -> str:
        """The person's line: the estimated need against the board's memory, what it is made of, the verdict, and
        that it is an estimate (the measurement on the board says what really fits). English by default (the CLI and
        the AI's facts); the Builder passes None, the UI language (orchestrator/utils/l10n_build.py "mem.*")."""
        from orchestrator.utils.localization import ui_text
        if self.state == "unknown":
            return ui_text("mem.unknown", lang, board=self.board,
                           reason=ui_text(f"mem.reason.{self.reason_code}", lang, board=self.board)
                           if self.reason_code else self.reason)
        parts = [ui_text("mem.part.weights", lang, mb=_mb(self.weights_bytes), quant=self.quantization),
                 ui_text("mem.part.server", lang, mb=_mb(self.server_bytes))]
        if self.kv_bytes:
            parts.append(ui_text("mem.part.kv", lang, mb=_mb(self.kv_bytes), ctx=self.ctx, kv=self.kv_type))
        if self.ram_source == PROBE_SOURCE:
            source = ui_text("mem.source.probe", lang)
        else:
            source = ui_text("mem.source.max", lang, board=self.board) + ui_text("mem.probe_hint", lang)
        return ui_text("mem.line", lang, board=self.board, need=self.need_mib, parts="; ".join(parts),
                       ram=self.ram_mib, source=source, verdict=ui_text(f"mem.verdict.{self.state}", lang),
                       uncertainty=ui_text(f"mem.uncertainty.{self.basis}", lang))

    def short(self) -> str:
        """The verdict with its two numbers, for the start question's consequence."""
        if self.state == "unknown":
            return f"memory estimate unknown ({self.reason})"
        words = {"fits": "fits", "tight": "tight", "does_not_fit": "does not fit (only with swap, if at all)"}
        return (f"{words[self.state]}: about {self.need_mib} MB needed of {self.ram_mib} MB ({self.ram_source}; "
                "estimate)")

    def fit(self):
        """The estimate as the build plan's board Fit (disk_plan.Fit: bytes needed and usable)."""
        from orchestrator.Core.disk_plan import Fit
        if self.state == "unknown":
            return Fit("unknown", source=self.reason)
        how = ("weights estimated per tensor from the model's shape" if self.basis == "per_tensor" else
               "weights roughly estimated from the parameter count")
        return Fit(self.state, int(self.need_mib) * MIB, int(self.ram_mib) * MIB,
                   f"memory from {self.ram_source}{self.probe_hint()}; {how}, the server's buffers and the KV cache "
                   f"of {self.ctx} tokens")

    def probe_hint(self) -> str:
        """Without the board's probe: how the board's own memory is known (its probe)."""
        return "" if self.ram_source == PROBE_SOURCE else "; a hardware probe of the board gives its own"


def _mb(value: Optional[int]) -> int:
    return int((value or 0) / MIB + 0.5)


def context_for(shape: ModelShape, ctx: Optional[int] = None) -> int:
    """The context the estimate counts: the one given (the request's), else the context the board measurement keeps
    room for (measure.CTX_FLOOR), never above the model's training context."""
    from orchestrator.runtime.device.measure import CTX_FLOOR
    wanted = int(ctx) if ctx else CTX_FLOOR
    return min(wanted, shape.train_ctx) if shape.train_ctx else wanted


def estimate(shape: Optional[ModelShape], quantization: Optional[str], *, board: str,
             ram_mib: Optional[float], ram_source: str = "", ctx: Optional[int] = None,
             kv_type: Optional[str] = None, imatrix: bool = True,
             tensor_types: Sequence[str] = ()) -> MemoryEstimate:
    """The memory estimate of a model (`shape`) at `quantization` on `board` with `ram_mib` of memory (from
    `ram_source`: "the board's probe" or "the most memory a <board> comes with"): the weights (weights_bytes), the
    server's buffers (SERVER_SHARE of them), the KV cache for the context (context_for; `kv_type`
    given, else the boards' rule), judged by memory_state. Never raises for missing facts: the state
    is then "unknown" with the reason."""
    from orchestrator.runtime.device.measure import RESERVE_MIN_MIB, RESERVE_SHARE
    from orchestrator.runtime.hardware import kv_kind_for_left
    from orchestrator.utils.localization import ui_text
    quant = str(quantization or "").strip()
    est = MemoryEstimate(board=str(board or "the board"), quantization=quant or "the default type")
    if shape is None:
        est.reason_code = "no_shape"
        est.reason = "the model's size is not known before the build (no parameter count or shape could be read)"
        return est
    est.shape_source = shape.source
    if not quant:
        est.reason_code, est.reason = "no_quantization", "no quantization type is chosen"
        return est
    weights, basis = weights_bytes(shape, quant, imatrix=imatrix, tensor_types=tensor_types)
    if weights is None:
        est.reason_code = "unknown_type" if bits_per_weight(quant) is None else "no_parameters"
        est.reason = (f"the size of {quant} is not known to the estimate" if bits_per_weight(quant) is None else
                      "the model's parameter count is not known before the build")
        return est
    est.weights_bytes, est.basis = int(weights), basis
    est.server_bytes = int(weights * SERVER_SHARE)
    ram = int(ram_mib) if ram_mib and ram_mib > 0 else None
    est.ctx = context_for(shape, ctx) if shape.has_kv else 0
    if shape.has_kv:
        if kv_type:
            est.kv_type = kv_type
        else:
            reserve = max(RESERVE_MIN_MIB, int(round((ram or 0) * RESERVE_SHARE))) * MIB
            left = (ram * MIB if ram else float("inf")) - weights - est.server_bytes - reserve
            est.kv_type = kv_kind_for_left(left)
        est.kv_bytes = kv_bytes(shape, est.ctx, est.kv_type)
    need_bytes = weights + est.server_bytes + est.kv_bytes
    est.need_mib = int(need_bytes / MIB + 0.5)
    est.uncertainty = ui_text(f"mem.uncertainty.{basis}", "en")
    if ram is None:
        est.reason_code = "no_memory"
        est.reason = f"the memory of {est.board} is not known: no hardware probe of the board and no ram_max_gb"
        est.extras["need_known"] = True
        return est
    est.ram_mib, est.ram_source = ram, ram_source or "the board"
    est.state = memory_state(est.need_mib, ram)
    return est


def unknown(board: Optional[str], quantization: Optional[str], reason: str) -> MemoryEstimate:
    """An estimate that could not be made, with the reason (shown like any other)."""
    return MemoryEstimate(board=str(board or "the board"), quantization=str(quantization or ""), reason=reason)


__all__ = ["FTYPE_BASE", "GGML_BLOCKS", "MemoryEstimate", "ModelShape", "QUANT_BITS", "SERVER_SHARE", "TIGHT_SHARE",
           "bits_per_weight", "context_for", "estimate", "kv_bytes", "memory_need_mib", "memory_state",
           "shape_from_config", "shape_from_gguf", "shape_from_local", "tensor_type", "unknown", "weights_bytes"]
