#!/usr/bin/env python3
"""
Local text embeddings for the knowledge base (FastEmbed on ONNX Runtime, no cloud API).

A small registry describes the dense models: dimension, context length, the prefixes the model was trained
with and, for models FastEmbed does not ship, where the ONNX export lives. BM25 sparse vectors (FastEmbed
"Qdrant/bm25"; Qdrant applies the IDF) complement them for exact keywords such as error codes and flags;
identifiers such as -DGGML_CUDA=ON or rknn_toolkit2 also count by their parts.
Models are cached under the framework's models directory. In offline mode nothing is downloaded.
"""

import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Literal, Optional, Protocol, Sequence, Tuple, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict

from orchestrator.rag.errors import EmbeddingError, EmbeddingModelNotCached
from orchestrator.utils.helpers import redact_secrets

# Conservative for multilingual subword tokenizers (EN/DE text averages closer to 4 characters per token),
# so chunks sized with it stay below the model's context.
CHARS_PER_TOKEN = 3.0
SPARSE_MODEL = "Qdrant/bm25"
# The keyword encoding stored with each collection: FastEmbed's BM25 over the text plus the split forms of its
# identifiers. A collection of another encoding is re-encoded, never mixed.
SPARSE_ENCODING = "Qdrant/bm25+ids1"
# One stemmer for every document keeps query and document tokens consistent; German words are stemmed
# imperfectly but identically on both sides, and German stop words get a low IDF anyway.
BM25_LANGUAGE = "english"


def approx_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


@dataclass
class SparseVec:
    indices: List[int] = field(default_factory=list)
    values: List[float] = field(default_factory=list)


@runtime_checkable
class DenseEmbedder(Protocol):
    model_id: str
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> List[List[float]]: ...

    def embed_query(self, text: str) -> List[float]: ...

    def count_tokens(self, text: str) -> int: ...


@runtime_checkable
class SparseEmbedder(Protocol):
    model_id: str

    def embed_passages(self, texts: Sequence[str]) -> List[SparseVec]: ...

    def embed_query(self, text: str) -> SparseVec: ...


class CustomOnnxSource(BaseModel):
    """Where FastEmbed finds a model it does not ship (registered with TextEmbedding.add_custom_model)."""
    model_config = ConfigDict(frozen=True)

    hf_repo: str
    model_file: str
    additional_files: Tuple[str, ...] = ()  # e.g. external weights (model.onnx_data) next to the graph
    pooling: Literal["CLS", "MEAN"]
    normalization: bool = True


class EmbeddingModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str
    dim: int
    max_tokens: int  # the model's context; the model truncates longer input
    chunk_tokens: int  # chunk body budget; leaves room for the preamble and the prefix
    query_prefix: str = ""
    passage_prefix: str = ""
    tier: Literal["cpu", "gpu"] = "cpu"
    size_gb: float = 0.0
    license: str = ""
    custom: Optional[CustomOnnxSource] = None


MODEL_REGISTRY = {spec.model_id: spec for spec in [
    # Default: multilingual (~100 languages incl. German and English), trained for retrieval with
    # "query: "/"passage: " prefixes, 118M parameters, 384 dims, fast on a CPU. FastEmbed 0.8.1 does not ship
    # it, so the ONNX export in the model authors' repository is registered as a custom model.
    EmbeddingModelSpec(model_id="intfloat/multilingual-e5-small", dim=384, max_tokens=512, chunk_tokens=384,
                       query_prefix="query: ", passage_prefix="passage: ", tier="cpu", size_gb=0.47,
                       license="mit",
                       custom=CustomOnnxSource(hf_repo="intfloat/multilingual-e5-small",
                                               model_file="onnx/model.onnx", pooling="MEAN")),
    # Same family, 1024 dims, shipped by FastEmbed: better quality, several times slower on a CPU.
    EmbeddingModelSpec(model_id="intfloat/multilingual-e5-large", dim=1024, max_tokens=512, chunk_tokens=384,
                       query_prefix="query: ", passage_prefix="passage: ", tier="cpu", size_gb=2.24,
                       license="mit"),
    # GPU tier: BGE-M3 dense vectors (1024 dims, 8192 tokens context, no prefixes). The graph keeps its
    # weights in an external file that has to be downloaded with it.
    EmbeddingModelSpec(model_id="BAAI/bge-m3", dim=1024, max_tokens=8192, chunk_tokens=512, tier="gpu",
                       size_gb=2.27, license="mit",
                       custom=CustomOnnxSource(hf_repo="BAAI/bge-m3", model_file="onnx/model.onnx",
                                               additional_files=("onnx/model.onnx_data",), pooling="CLS")),
]}
DEFAULT_CPU_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_GPU_MODEL = "BAAI/bge-m3"


def model_slug(model_id: str) -> str:
    """Collection-name-safe form of a model id."""
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:80] or "model"


def spec_for_slug(slug: str) -> Optional[EmbeddingModelSpec]:
    return next((s for s in MODEL_REGISTRY.values() if model_slug(s.model_id) == slug), None)


def resolve_spec(model_id: str) -> EmbeddingModelSpec:
    """The registry entry, or a generic one for any other dense model FastEmbed ships."""
    key = (model_id or "").strip()
    for spec in MODEL_REGISTRY.values():
        if spec.model_id.lower() == key.lower():
            return spec
    try:
        from fastembed import TextEmbedding
        native = TextEmbedding.list_supported_models()
    except ImportError:
        native = []
    for desc in native:
        if desc["model"].lower() == key.lower():
            # FastEmbed's own query_embed/passage_embed handle any model-specific instructions.
            context = re.search(r"(\d+) input tokens truncation", desc.get("description") or "")
            max_tokens = int(context.group(1)) if context else 512
            return EmbeddingModelSpec(model_id=desc["model"], dim=int(desc["dim"]), max_tokens=max_tokens,
                                      chunk_tokens=max(32, min(384, max_tokens * 3 // 4)),
                                      size_gb=float(desc.get("size_in_GB") or 0),
                                      license=str(desc.get("license") or ""))
    raise EmbeddingError(f"Unknown embedding model '{model_id}'. Use one of {', '.join(MODEL_REGISTRY)} "
                         "or a model listed by fastembed.TextEmbedding.list_supported_models().")


def machine_resources(providers: Optional[Sequence[str]] = None,
                      total_ram_gb: Optional[float] = None) -> Tuple[Sequence[str], float]:
    """ONNX Runtime execution providers and total RAM in GB, unless the caller knows them."""
    if providers is None:
        try:
            import onnxruntime
            providers = onnxruntime.get_available_providers()
        except Exception:
            providers = []
    if total_ram_gb is None:
        try:
            import psutil
            total_ram_gb = psutil.virtual_memory().total / 2 ** 30
        except Exception:
            total_ram_gb = 0.0
    return providers, total_ram_gb


def recommend_model(providers: Optional[Sequence[str]] = None,
                    total_ram_gb: Optional[float] = None) -> Tuple[str, str]:
    """Picks a model for this machine and says why."""
    providers, total_ram_gb = machine_resources(providers, total_ram_gb)
    cuda = "CUDAExecutionProvider" in providers
    if cuda and total_ram_gb >= 8:
        return DEFAULT_GPU_MODEL, (f"CUDA is available to ONNX Runtime and {total_ram_gb:.0f} GB RAM: "
                                   "BGE-M3 (1024 dims, long context)")
    why = f"only {total_ram_gb:.0f} GB RAM" if cuda else "no CUDA execution provider in ONNX Runtime"
    return DEFAULT_CPU_MODEL, f"{why}: multilingual-e5-small (384 dims, fast on a CPU)"


def check_vectors(vectors: Iterable, expected: int, dim: int, model_id: str) -> List[List[float]]:
    """Plain float lists, after checking count, dimension and finiteness (a bad model must not poison the store)."""
    out = []
    for vector in vectors:
        arr = np.asarray(vector, dtype=np.float32)
        if arr.ndim != 1 or arr.shape[0] != dim:
            raise EmbeddingError(f"{model_id} returned a vector of shape {arr.shape}, expected ({dim},).")
        if not np.isfinite(arr).all():
            raise EmbeddingError(f"{model_id} returned a vector with NaN or infinite values.")
        out.append(arr.tolist())
    if len(out) != expected:
        raise EmbeddingError(f"{model_id} returned {len(out)} vectors for {expected} texts.")
    return out


_REGISTER_LOCK = threading.Lock()


def _register_custom(spec: EmbeddingModelSpec) -> None:
    if spec.custom is None:
        return
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    with _REGISTER_LOCK:
        if any(m["model"].lower() == spec.model_id.lower() for m in TextEmbedding.list_supported_models()):
            return  # registered earlier in this process, or shipped by a newer FastEmbed
        TextEmbedding.add_custom_model(
            model=spec.model_id, pooling=PoolingType(spec.custom.pooling),
            normalization=spec.custom.normalization, sources=ModelSource(hf=spec.custom.hf_repo),
            dim=spec.dim, model_file=spec.custom.model_file, additional_files=list(spec.custom.additional_files),
            license=spec.license, size_in_gb=spec.size_gb,
            description=f"Registered by the LLM Cross-Compiler Framework ({spec.max_tokens} tokens).")


def _quiet_fastembed() -> None:
    """FastEmbed logs through loguru to stderr; failures reach us as exceptions and are reported once."""
    try:
        from loguru import logger
        logger.disable("fastembed")
    except ImportError:
        pass


class _FastEmbedBase:
    kind = "embedding model"  # for messages

    def __init__(self, cache_dir: Path, offline: Callable[[], bool], size_gb: float):
        self.cache_dir = Path(cache_dir)
        self._offline = offline
        self._size_gb = size_gb
        self._model = None
        self._lock = threading.Lock()

    def _create(self, offline: bool):
        raise NotImplementedError

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                import fastembed  # noqa: F401
            except ImportError as e:
                raise EmbeddingError("Local embeddings need the 'fastembed' package.") from e
            _quiet_fastembed()
            # Read at load time: once loaded, the model is in memory and never downloads again.
            offline = bool(self._offline())
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._model = self._create(offline)
            except EmbeddingError:
                raise
            except Exception as e:
                if offline:
                    raise EmbeddingModelNotCached(
                        f"The {self.kind} {self.model_name} is not in {self.cache_dir} and offline mode is on. "
                        f"Turn offline mode off once to download it (about {self._size_gb:.2f} GB).") from e
                raise EmbeddingError(f"Could not load the {self.kind} {self.model_name}: "
                                     f"{redact_secrets(str(e))}") from e
            return self._model

    @property
    def model_name(self) -> str:
        """The model's FastEmbed name."""
        return self.model_id

    def prepare(self) -> None:
        """Downloads (unless offline) and loads the model now instead of on first use."""
        self._load()

    def release(self) -> bool:
        """Frees the model's memory; the next use loads it again (a call in progress keeps its reference).
        True if a model was loaded."""
        with self._lock:
            loaded, self._model = self._model is not None, None
            self._released()
        return loaded

    def _released(self) -> None:
        """Drops what belongs to the released model (called under the lock)."""


class FastEmbedDenseEmbedder(_FastEmbedBase):
    """Dense embeddings with FastEmbed; adds the model's query/passage prefixes and caches query vectors."""

    # Attention memory grows with batch size x tokens^2 and chunks have up to 512 tokens; larger batches
    # were not faster for chunk-sized input on a 4-core CPU, only bigger (measured with e5-small).
    BATCH_SIZE = 8
    QUERY_CACHE_SIZE = 256

    def __init__(self, spec: EmbeddingModelSpec, cache_dir: Path, offline: Callable[[], bool] = lambda: False):
        super().__init__(cache_dir, offline, spec.size_gb)
        self.spec = spec
        self.model_id = spec.model_id
        self.dim = spec.dim
        self._tokenizer = None
        self._cache_lock = threading.Lock()
        self._query_cache: "OrderedDict[str, List[float]]" = OrderedDict()

    def _create(self, offline: bool):
        from fastembed import TextEmbedding
        _register_custom(self.spec)
        model = TextEmbedding(model_name=self.model_id, cache_dir=str(self.cache_dir), local_files_only=offline)
        self._tokenizer = _untruncated_tokenizer(model)
        return model

    def _released(self) -> None:
        self._tokenizer = None

    @property
    def _prefixed(self) -> bool:
        return bool(self.spec.query_prefix or self.spec.passage_prefix)

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        self._load()
        tokenizer = self._tokenizer  # read once: release() may drop it at any time
        if tokenizer is None:
            return approx_tokens(text)
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    def embed_passages(self, texts: Sequence[str]) -> List[List[float]]:
        texts = list(texts)
        if not texts:
            return []
        model = self._load()
        if self._prefixed:
            vectors = model.embed([self.spec.passage_prefix + t for t in texts], batch_size=self.BATCH_SIZE)
        else:
            vectors = model.passage_embed(texts, batch_size=self.BATCH_SIZE)
        return check_vectors(vectors, len(texts), self.dim, self.model_id)

    def embed_query(self, text: str) -> List[float]:
        with self._cache_lock:
            if text in self._query_cache:
                self._query_cache.move_to_end(text)
                return list(self._query_cache[text])
        model = self._load()
        vectors = model.embed([self.spec.query_prefix + text]) if self._prefixed else model.query_embed(text)
        vector = check_vectors(vectors, 1, self.dim, self.model_id)[0]
        with self._cache_lock:
            self._query_cache[text] = vector
            while len(self._query_cache) > self.QUERY_CACHE_SIZE:
                self._query_cache.popitem(last=False)
        return list(vector)


class FastEmbedSparseEmbedder(_FastEmbedBase):
    """BM25 term weights with FastEmbed over the text plus its split identifiers (expand_identifiers); the
    collection's sparse vector must use Modifier.IDF. model_id names the encoding (SPARSE_ENCODING)."""

    BATCH_SIZE = 64

    def __init__(self, cache_dir: Path, offline: Callable[[], bool] = lambda: False, language: str = BM25_LANGUAGE,
                 model_dir: Optional[Path] = None):
        super().__init__(cache_dir, offline, 0.01)
        self.model_id = SPARSE_ENCODING
        self.language = language
        self._model_dir = model_dir  # a local copy of the stop word files, used instead of the cache

    def _create(self, offline: bool):
        from fastembed import SparseTextEmbedding
        model_dir = self._model_dir or (self._cached_copy() if offline else None)
        extra = {"specific_model_path": str(model_dir)} if model_dir else {}
        return SparseTextEmbedding(model_name=SPARSE_MODEL, cache_dir=str(self.cache_dir),
                                   local_files_only=offline, language=self.language, **extra)

    def _cached_copy(self) -> Optional[Path]:
        """The folder of the downloaded stop word file. FastEmbed 0.8.1 checks its cache for a model file that
        BM25 does not have, so offline it never finds a downloaded copy by itself (and the copy is a partial
        snapshot, which huggingface_hub's offline snapshot lookup refuses)."""
        try:
            from huggingface_hub import try_to_load_from_cache
            found = try_to_load_from_cache(SPARSE_MODEL, f"{self.language}.txt", cache_dir=str(self.cache_dir))
        except Exception:
            return None
        return Path(found).parent if isinstance(found, str) else None  # else not downloaded: FastEmbed reports it

    @property
    def model_name(self) -> str:
        return SPARSE_MODEL

    def embed_passages(self, texts: Sequence[str]) -> List[SparseVec]:
        texts = [expand_identifiers(t) for t in texts]
        if not texts:
            return []
        return [_sparse(e) for e in self._load().embed(texts, batch_size=self.BATCH_SIZE)]

    def embed_query(self, text: str) -> SparseVec:
        return _sparse(next(iter(self._load().query_embed(expand_identifiers(text)))))


# FastEmbed's BM25 tokenizer keeps \w runs whole: "-DGGML_CUDA=ON" becomes "dggml_cuda", so a search for
# GGML_CUDA or "ggml cuda" misses it; snake_case and camelCase names stay one token. Their parts are added.
_WORD = re.compile(r"\w+")
_CMAKE_DEFINE = re.compile(r"D[A-Z][A-Z0-9]*(_|$)")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z]{2})")  # not plurals such as GPUs
_VERSION_PART = re.compile(r"\d{1,2}")  # the 64 of x86_64, the 2 and 17 of manylinux_2_17


def expand_identifiers(text: str) -> str:
    """The text plus the split forms of its identifiers, for the BM25 input only (the dense input and the
    stored content stay as they are): -DGGML_CUDA=ON adds GGML_CUDA GGML CUDA, rknn_toolkit2 adds rknn toolkit2,
    rknnToolkitLite adds rknn Toolkit Lite, x86_64 adds x86. Each form is added once."""
    added: Dict[str, None] = {}
    for match in _WORD.finditer(text or ""):
        word, start, end = match.group(), match.start(), match.end()
        define = _CMAKE_DEFINE.match(word) if start and text[start - 1] == "-" else None
        if define and (define.group(1) or text[end:end + 1] == "="):  # -DNAME=VALUE or -DNAME_X, not -DEBUG
            word = word[1:]
            added.setdefault(word, None)
        parts = [p for p in _CAMEL.sub("_", word).split("_") if p]
        if len(parts) > 1:
            for part in parts:
                if not _VERSION_PART.fullmatch(part):
                    added.setdefault(part, None)
    return f"{text}\n{' '.join(added)}" if added else text


def _sparse(embedding) -> SparseVec:
    return SparseVec(indices=[int(i) for i in np.asarray(embedding.indices).tolist()],
                     values=[float(v) for v in np.asarray(embedding.values).tolist()])


def _untruncated_tokenizer(model):
    """A copy of the model's tokenizer without truncation, so chunk sizes are measured, not capped at 512."""
    tokenizer = getattr(getattr(model, "model", None), "tokenizer", None)
    if tokenizer is None:
        return None
    try:
        from tokenizers import Tokenizer
        copy = Tokenizer.from_str(tokenizer.to_str())
        copy.no_truncation()
        copy.no_padding()
        return copy
    except Exception:
        return None
