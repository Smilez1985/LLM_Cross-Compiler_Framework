#!/usr/bin/env python3
"""
Retrieval quality on top of the hybrid search: a cross-encoder reranker and a token budget for excerpts.

The reranker reads the query together with each candidate (local FastEmbed/ONNX model, loaded on first use
like the embedders, never in offline mode). Its logit becomes a 0..1 relevance, so one floor per model drops
candidates that do not answer the question at all. pack_excerpts fits the best excerpts into a prompt budget.
"""

import hashlib
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

from orchestrator.rag.embeddings import _REGISTER_LOCK, _FastEmbedBase, approx_tokens, machine_resources
from orchestrator.rag.errors import EmbeddingError
from orchestrator.utils.helpers import redact_secrets

DEFAULT_MIN_SCORE = 0.2  # for cross-encoders without a calibrated floor
CUT_MARK = "\n[…]"


class RerankerSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str  # FastEmbed name; registered as a custom model when FastEmbed does not ship it
    model_file: str = "onnx/model.onnx"
    size_gb: float = 0.0
    license: str = ""
    min_score: float = DEFAULT_MIN_SCORE  # relevance floor (0..1), calibrated with the retrieval evaluation
    tier: Literal["cpu", "gpu"] = "cpu"


RERANKER_REGISTRY = {spec.model_id: spec for spec in [
    # Multilingual MiniLM cross-encoder trained on mMARCO (14 languages incl. German and English), about 0.5 GB
    # in memory; the u8u8 int8 graph keeps its accuracy on AVX2 CPUs without VNNI.
    RerankerSpec(model_id="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", model_file="onnx/model_quint8_avx2.onnx",
                 size_gb=0.12, license="apache-2.0", min_score=0.2, tier="cpu"),
    # GPU tier: XLM-R large based, int8 export; 1.1-1.5 GB and several seconds per search on a CPU.
    RerankerSpec(model_id="onnx-community/bge-reranker-v2-m3-ONNX", model_file="onnx/model_quantized.onnx",
                 size_gb=0.57, license="apache-2.0", min_score=0.2, tier="gpu"),
]}
DEFAULT_CPU_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
DEFAULT_GPU_RERANKER = "onnx-community/bge-reranker-v2-m3-ONNX"


def relevance(logit: float) -> float:
    """A cross-encoder logit as a 0..1 relevance (sigmoid in a form that cannot overflow)."""
    return 0.5 * (1.0 + math.tanh(logit / 2.0))


def resolve_reranker(model_id: str) -> RerankerSpec:
    """The registry entry, or a generic one for another cross-encoder FastEmbed ships."""
    key = (model_id or "").strip()
    for spec in RERANKER_REGISTRY.values():
        if spec.model_id.lower() == key.lower():
            return spec
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        native = TextCrossEncoder.list_supported_models()
    except ImportError:
        native = []
    for desc in native:
        if desc["model"].lower() == key.lower():
            return RerankerSpec(model_id=desc["model"], model_file=desc.get("model_file") or "onnx/model.onnx",
                                size_gb=float(desc.get("size_in_GB") or 0), license=str(desc.get("license") or ""))
    raise EmbeddingError(f"Unknown reranker '{model_id}'. Use auto, off, one of {', '.join(RERANKER_REGISTRY)} "
                         "or a model listed by fastembed.TextCrossEncoder.list_supported_models().")


def select_reranker(setting: str = "auto", providers: Optional[Sequence[str]] = None,
                    total_ram_gb: Optional[float] = None) -> Tuple[Optional[RerankerSpec], str]:
    """The reranker for the setting rag_reranker (auto, off or a model id) and why; None: no reranking."""
    key = (setting or "auto").strip()
    if key.lower() == "off":
        return None, "turned off (rag_reranker: off)"
    if key.lower() != "auto":
        return resolve_reranker(key), "set in the configuration (rag_reranker)"
    providers, total_ram_gb = machine_resources(providers, total_ram_gb)
    if "CUDAExecutionProvider" in providers and total_ram_gb >= 16:
        return RERANKER_REGISTRY[DEFAULT_GPU_RERANKER], (f"CUDA and {total_ram_gb:.0f} GB RAM: bge-reranker-v2-m3 "
                                                         "(multilingual, large)")
    if total_ram_gb >= 6:
        return RERANKER_REGISTRY[DEFAULT_CPU_RERANKER], (f"{total_ram_gb:.0f} GB RAM: multilingual MiniLM "
                                                         "cross-encoder (fast on a CPU)")
    return None, (f"only {total_ram_gb:.0f} GB RAM: no reranker (it needs about 0.5 GB next to the embedding "
                  "model)")


# Adapted from rag4ll reranking/rerank_fastembed.py (MIT, (c) 2026 Smilez1985, https://github.com/Smilez1985/rag4ll).
# Changed: the shared locked, offline-aware loader, small batches, checked 0..1 scores and a cache keyed by text.
class FastEmbedReranker(_FastEmbedBase):
    """Cross-encoder relevance with FastEmbed's TextCrossEncoder; scores are cached per (model, query, text)."""

    kind = "reranking model"
    # FastEmbed's default of 64 pairs of up to 512 tokens is a memory spike on a 4 GB PC.
    BATCH_SIZE = 8
    CACHE_SIZE = 2048

    def __init__(self, spec: RerankerSpec, cache_dir: Path, offline: Callable[[], bool] = lambda: False):
        super().__init__(cache_dir, offline, spec.size_gb)
        self.spec = spec
        self.model_id = spec.model_id
        self._cache_lock = threading.Lock()
        self._scores: "OrderedDict[str, float]" = OrderedDict()

    def _create(self, offline: bool):
        from fastembed.common.model_description import ModelSource
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        with _REGISTER_LOCK:
            if not any(m["model"].lower() == self.model_id.lower() for m in TextCrossEncoder.list_supported_models()):
                TextCrossEncoder.add_custom_model(
                    model=self.model_id, sources=ModelSource(hf=self.model_id), model_file=self.spec.model_file,
                    license=self.spec.license, size_in_gb=self.spec.size_gb,
                    description="Registered by the LLM Cross-Compiler Framework.")
        return TextCrossEncoder(self.model_id, cache_dir=str(self.cache_dir), local_files_only=offline)

    def _key(self, query: str, document: str) -> str:
        # By text, never by position in a candidate list: the same document keeps its score in any order.
        return hashlib.sha256(f"{self.model_id}\x1f{query}\x1f{document}".encode("utf-8")).hexdigest()

    def scores(self, query: str, documents: Sequence[str]) -> List[float]:
        """The relevance (0..1) of each document for the query."""
        keys = [self._key(query, d) for d in documents]
        known = {}
        with self._cache_lock:
            for key in keys:
                if key in self._scores:
                    self._scores.move_to_end(key)
                    known[key] = self._scores[key]
        todo = {key: doc for key, doc in zip(keys, documents) if key not in known}  # duplicates once
        if todo:
            model = self._load()
            try:
                logits = [float(x) for x in model.rerank(query, list(todo.values()), batch_size=self.BATCH_SIZE)]
            except Exception as e:
                raise EmbeddingError(f"The reranking model {self.model_id} failed: {redact_secrets(str(e))}") from e
            if len(logits) != len(todo) or not all(math.isfinite(x) for x in logits):
                raise EmbeddingError(f"{self.model_id} returned unusable rerank scores.")
            with self._cache_lock:
                for key, logit in zip(todo, logits):
                    known[key] = self._scores[key] = relevance(logit)
                while len(self._scores) > self.CACHE_SIZE:
                    self._scores.popitem(last=False)
        return [known[key] for key in keys]


# Adapted from rag4ll retrieval/budget.py (MIT, (c) 2026 Smilez1985, https://github.com/Smilez1985/rag4ll).
# Changed: an excerpt that does not fit is skipped instead of ending the walk, and an over-long first one is cut.
def pack_excerpts(texts: Sequence[str], max_tokens: int, count: Callable[[str], int] = approx_tokens) -> List[str]:
    """The excerpts (best first) that fit into max_tokens together. One that does not fit is skipped, so a
    smaller later one can still come along; a first excerpt that alone exceeds the budget is cut at a line
    boundary, so there is always some context."""
    packed: List[str] = []
    used = 0
    for text in texts:
        tokens = count(text)
        if used + tokens <= max_tokens:
            packed.append(text)
            used += tokens
        elif not packed:
            cut = _cut(text, max_tokens, count)
            if cut:
                packed.append(cut)
                used += count(cut)
    return packed


def _cut(text: str, max_tokens: int, count: Callable[[str], int]) -> str:
    """The leading lines of text within max_tokens (a single over-long line is cut inside), marked as cut."""
    budget = max_tokens - count(CUT_MARK)
    lines: List[str] = []
    used = 0
    for line in text.split("\n"):
        tokens = count(line) + 1  # the line break
        if used + tokens > budget:
            if not lines:  # the first line alone is too long: keep its share of the budget
                part = line[:max(int(len(line) * budget / max(tokens, 1)), 0)]
                while part and count(part) > budget:
                    part = part[:int(len(part) * 0.8)]
                lines.append(part)
            break
        lines.append(line)
        used += tokens
    kept = "\n".join(lines).rstrip()
    return kept + CUT_MARK if kept else ""
