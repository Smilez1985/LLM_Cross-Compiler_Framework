#!/usr/bin/env python3
"""
Storage of the knowledge base: what every storage backend shares.

One collection per embedding model (alias name + model slug), so a model change never mixes or orphans
vectors; the alias points at the active collection. Every point has a named dense vector and a BM25 sparse
vector (Qdrant applies the IDF), and queries fuse both rankings with Reciprocal Rank Fusion.

Two backends offer the same methods (RAGManager calls only these):

- EdgeKnowledgeStore (orchestrator/rag/edge_store.py): the local knowledge base on this computer, in Qdrant Edge
  (the Rust engine of Qdrant, in the process; DECISIONS RG5).
- ClientKnowledgeStore (orchestrator/rag/client_store.py): qdrant-client, for a Qdrant server (setting qdrant_url),
  and for the local storage of earlier versions (qdrant-client's local mode, a Python re-implementation) until it is
  converted to Qdrant Edge (orchestrator/rag/migration.py).

This module holds the shared parts: the names, the neutral point and record types, and the snapshot files. A
snapshot is a portable file (gzip, one JSON line per point with its vectors and payload), the same for every backend:
version 1 was written by the old local storage, version 2 names the storage it came from. Both restore into any
backend, so an old snapshot is converted when it is restored.
"""

import base64
import gzip
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import numpy as np

from orchestrator.rag.embeddings import SparseVec, model_slug
from orchestrator.rag.errors import KnowledgeBaseError

DENSE = "dense"
SPARSE = "bm25"
SCHEMA_VERSION = 2
SNAPSHOT_FORMAT = "llm-framework-knowledge-snapshot"
# Version 1: written by the local storage of earlier versions (qdrant-client's local mode). Version 2 adds the storage
# the snapshot came from ("store"); its lines are the same. A restore reads both; a newer version is refused.
SNAPSHOT_VERSION = 2
SNAPSHOT_VERSIONS = (1, 2)
# Reciprocal Rank Fusion: score = sum of 1 / (k + rank). k = 2 is Qdrant's default, which the earlier local storage
# used too, so the fused scores (and the reranker's fused_score) stay what they were.
RRF_K = 2
# The size estimate of a snapshot before it is written (DECISIONS, eighth round, answer 2): this many points are
# compressed as the file holds them and scaled to the whole collection, plus a margin for points that compress worse.
EXPORT_SAMPLE = 64
EXPORT_MARGIN = 1.2
# What a status names as the storage of the knowledge base.
FORMAT_EDGE = "qdrant-edge"
FORMAT_OLD_LOCAL = "old-local"
FORMAT_SERVER = "server"


@dataclass
class StoredRecord:
    """A point as every backend returns it: its id (text), payload and, when asked for, its vectors
    ({DENSE: [floats], SPARSE: object with indices and values})."""
    id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    vector: Optional[Dict[str, Any]] = None


@dataclass
class ScoredHit:
    """One search result: id (text), score and payload."""
    id: str
    score: float
    payload: Dict[str, Any] = field(default_factory=dict)


def sparse_of(vector: Any) -> Optional[SparseVec]:
    """The sparse vector of a record's vector dict as a SparseVec (None when it has none)."""
    sparse = vector.get(SPARSE) if isinstance(vector, dict) else None
    if sparse is None:
        return None
    return SparseVec(indices=[int(i) for i in sparse.indices], values=[float(v) for v in sparse.values])


class KnowledgeStore:
    """The active collection of one embedding model. Subclasses implement the storage (ensure, point, upsert,
    delete_stale, delete_origin_stale, query, count, records, payloads, source_records, recreate, drop_all,
    knowledge_collections, status);
    the snapshot files are the same for all of them."""

    UPSERT_BATCH = 64
    store_format = ""

    def __init__(self, client, alias: str, model_id: str, dim: int, server_mode: bool = False):
        self.client = client
        self.alias = alias
        self.model_id = model_id
        self.dim = dim
        self.server_mode = server_mode
        self.collection = self.collection_name(alias, model_id)

    @staticmethod
    def collection_name(alias: str, model_id: str) -> str:
        return f"{alias}__{model_slug(model_id)}"

    @staticmethod
    def alias_target(client, alias: str) -> Optional[str]:
        """The collection the alias points at, on the open storage `client` of any backend (None when there is none)."""
        own = getattr(client, "alias_target", None)
        if callable(own):  # the local Qdrant Edge storage (EdgeStorage)
            return own(alias)
        try:
            return next((a.collection_name for a in client.get_aliases().aliases if a.alias_name == alias), None)
        except Exception:
            return None

    @staticmethod
    def model_of(client, collection: str) -> Optional[str]:
        """The embedding model a collection was built with, as its metadata says (None when unknown)."""
        own = getattr(client, "model_of", None)
        if callable(own):  # EdgeStorage
            return own(collection)
        try:
            return (client.get_collection(collection).config.metadata or {}).get("embedding_model")
        except Exception:
            return None

    def _check_built_with(self, dim: Optional[int], built_with: Optional[str]) -> None:
        """The messages of a collection that does not fit the model (the same for every backend)."""
        if dim is not None and dim != self.dim:
            raise KnowledgeBaseError(
                f"Collection {self.collection} stores {dim}-dimensional vectors, but {self.model_id} "
                f"produces {self.dim}. Choose the model the knowledge base was built with or clear it.")
        if built_with and built_with != self.model_id:
            raise KnowledgeBaseError(f"Collection {self.collection} was built with {built_with}, not {self.model_id}.")

    # --- what a backend implements ---

    def ensure(self) -> None:  # pragma: no cover - every backend has its own
        raise NotImplementedError

    def point(self, point_id: str, dense: List[float], sparse: SparseVec, payload: dict) -> Any:
        raise NotImplementedError  # pragma: no cover

    def upsert(self, points: Sequence[Any]) -> None:
        raise NotImplementedError  # pragma: no cover

    def records(self, with_vectors: bool = False) -> Iterator[Any]:
        raise NotImplementedError  # pragma: no cover

    def count(self) -> int:
        raise NotImplementedError  # pragma: no cover

    def recreate(self) -> None:
        raise NotImplementedError  # pragma: no cover

    def status(self) -> str:
        return "GREEN"

    def optimize(self) -> bool:
        """Merges and indexes the storage after many writes (Qdrant Edge); nothing to do elsewhere."""
        return False

    def close(self) -> None:
        """Releases what this store holds open (Qdrant Edge's shard); the storage's owner closes the rest."""

    # --- snapshots (portable files, the same for every backend) ---

    def _snapshot_header(self) -> str:
        return json.dumps({"format": SNAPSHOT_FORMAT, "version": SNAPSHOT_VERSION, "store": self.store_format,
                           "collection": self.collection, "embedding_model": self.model_id, "dim": self.dim,
                           "created": datetime.now().isoformat()}) + "\n"

    @staticmethod
    def _snapshot_line(record) -> str:
        vectors = record.vector if isinstance(record.vector, dict) else {}
        item = {"id": str(record.id), "payload": record.payload or {}}
        if vectors.get(DENSE) is not None:
            item["dense"] = base64.b64encode(np.asarray(vectors[DENSE], dtype="<f4").tobytes()).decode()
        sparse = sparse_of(vectors)
        if sparse is not None:
            item["sparse"] = {"indices": sparse.indices, "values": sparse.values}
        return json.dumps(item) + "\n"

    def export_file(self, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        count = 0
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(self._snapshot_header())
            for record in self.records(with_vectors=True):
                f.write(self._snapshot_line(record))
                count += 1
        tmp.replace(path)
        return count

    def export_estimate(self, sample: int = EXPORT_SAMPLE) -> int:
        """The bytes export_file would write, estimated before anything is written: the first `sample` points are
        compressed exactly as the file holds them, scaled to every point, plus a margin (EXPORT_MARGIN). 0 when the
        collection is empty."""
        count = self.count()
        if not count:
            return 0
        buffer, seen = io.BytesIO(), 0
        with gzip.GzipFile(fileobj=buffer, mode="wb") as f:
            for record in self.records(with_vectors=True):
                f.write(self._snapshot_line(record).encode("utf-8"))
                seen += 1
                if seen >= sample:
                    break
        per_point = len(buffer.getvalue()) / max(seen, 1)
        return int(per_point * count * EXPORT_MARGIN) + len(self._snapshot_header()) + 4096

    def read_snapshot(self, path: Path) -> List[Any]:
        """Every point of a snapshot file as this backend's points, checked completely before anything is changed:
        its format and version (1, written by the old local storage, or 2), the embedding model and dimension, and
        every vector's dimension."""
        points = []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            header = json.loads(f.readline() or "{}")
            if not isinstance(header, dict) or header.get("format") != SNAPSHOT_FORMAT:
                raise KnowledgeBaseError(f"{path.name} is not a knowledge base snapshot.")
            version = header.get("version", 1)
            if version not in SNAPSHOT_VERSIONS:
                raise KnowledgeBaseError(f"{path.name} was written by a newer framework version (snapshot format "
                                         f"{version}); update the framework to restore it.")
            if header.get("embedding_model") != self.model_id or header.get("dim") != self.dim:
                raise KnowledgeBaseError(f"{path.name} was made with {header.get('embedding_model')}, "
                                         f"the active model is {self.model_id}.")
            for line in f:
                item = json.loads(line)
                if "dense" not in item:
                    raise KnowledgeBaseError(f"{path.name} contains a point without its vector.")
                dense = np.frombuffer(base64.b64decode(item["dense"]), dtype="<f4")
                if dense.shape != (self.dim,):
                    raise KnowledgeBaseError(f"{path.name} contains a vector of the wrong dimension.")
                raw = item.get("sparse") or {}
                sparse = SparseVec(indices=[int(i) for i in raw.get("indices") or []],
                                   values=[float(v) for v in raw.get("values") or []])
                if len(sparse.indices) != len(sparse.values):
                    raise KnowledgeBaseError(f"{path.name} contains a broken keyword vector.")
                points.append(self.point(str(item["id"]), dense.tolist(), sparse, item.get("payload") or {}))
        return points

    def restore_file(self, path: Path) -> int:
        """Replaces the collection with a snapshot; the whole file is checked before anything is deleted."""
        points = self.read_snapshot(path)
        self.recreate()
        self.upsert(points)
        self.optimize()
        return len(points)
