#!/usr/bin/env python3
"""
Qdrant storage of the knowledge base.

One collection per embedding model (alias name + model slug), so a model change never mixes or orphans
vectors; the alias points at the active collection. Every point has a named dense vector and a BM25 sparse
vector (Qdrant applies the IDF), and queries fuse both rankings with Reciprocal Rank Fusion.
"""

import base64
import gzip
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

import numpy as np
from qdrant_client import models

from orchestrator.rag.embeddings import SparseVec, model_slug
from orchestrator.rag.errors import KnowledgeBaseError

DENSE = "dense"
SPARSE = "bm25"
SCHEMA_VERSION = 2
SNAPSHOT_FORMAT = "llm-framework-knowledge-snapshot"
# The size estimate of a snapshot before it is written (DECISIONS, eighth round, answer 2): this many points are
# compressed as the file holds them and scaled to the whole collection, plus a margin for points that compress worse.
EXPORT_SAMPLE = 64
EXPORT_MARGIN = 1.2


class KnowledgeStore:
    """The active collection of one embedding model on an open Qdrant client (local or server)."""

    UPSERT_BATCH = 64

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
        try:
            return next((a.collection_name for a in client.get_aliases().aliases if a.alias_name == alias), None)
        except Exception:
            return None

    @staticmethod
    def model_of(client, collection: str) -> Optional[str]:
        try:
            return (client.get_collection(collection).config.metadata or {}).get("embedding_model")
        except Exception:
            return None

    def _collection_names(self) -> List[str]:
        return [c.name for c in self.client.get_collections().collections]

    def knowledge_collections(self) -> List[str]:
        """All collections of this knowledge base, one per embedding model ever used."""
        return sorted(n for n in self._collection_names() if n.startswith(f"{self.alias}__"))

    # --- schema ---

    def ensure(self) -> None:
        """Creates the collection or checks that the existing one fits the model, then points the alias at it."""
        if self.collection in self._collection_names():
            self._validate()
        else:
            self._create()
        self._point_alias()

    def _create(self) -> None:
        kwargs = dict(
            collection_name=self.collection,
            vectors_config={DENSE: models.VectorParams(size=self.dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        metadata = {"embedding_model": self.model_id, "dim": self.dim, "schema": SCHEMA_VERSION}
        try:
            self.client.create_collection(**kwargs, metadata=metadata)
        except Exception:
            # Qdrant servers before 1.16 do not know collection metadata; the name still identifies the model.
            if not self.server_mode or self.collection in self._collection_names():
                raise
            self.client.create_collection(**kwargs)
        if self.server_mode:  # payload indexes do nothing in local mode (and warn there)
            for field in ("source", "chunk_hash"):
                self.client.create_payload_index(self.collection, field_name=field,
                                                 field_schema=models.PayloadSchemaType.KEYWORD)

    def _validate(self) -> None:
        config = self.client.get_collection(self.collection).config
        vectors = config.params.vectors
        dense = vectors.get(DENSE) if isinstance(vectors, dict) else None
        if dense is None:
            raise KnowledgeBaseError(f"Collection {self.collection} has no '{DENSE}' vector, so it was not created "
                                     "by this framework version. Clear the knowledge base to rebuild it.")
        if dense.size != self.dim:
            raise KnowledgeBaseError(
                f"Collection {self.collection} stores {dense.size}-dimensional vectors, but {self.model_id} "
                f"produces {self.dim}. Choose the model the knowledge base was built with or clear it.")
        sparse = (config.params.sparse_vectors or {}).get(SPARSE)
        if sparse is None or sparse.modifier != models.Modifier.IDF:
            raise KnowledgeBaseError(f"Collection {self.collection} lacks the BM25 sparse vector with IDF. "
                                     "Clear the knowledge base to rebuild it.")
        built_with = (config.metadata or {}).get("embedding_model")
        if built_with and built_with != self.model_id:
            raise KnowledgeBaseError(f"Collection {self.collection} was built with {built_with}, not {self.model_id}.")

    def _point_alias(self) -> None:
        current = self.alias_target(self.client, self.alias)
        if current == self.collection:
            return
        if self.alias in self._collection_names():
            return  # a legacy collection of that name (from the cloud-embedding version) is left untouched
        ops = []
        if current is not None:
            ops.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self.alias)))
        ops.append(models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=self.collection, alias_name=self.alias)))
        self.client.update_collection_aliases(change_aliases_operations=ops)

    # --- data ---

    @staticmethod
    def point(point_id: str, dense: List[float], sparse: SparseVec, payload: dict) -> models.PointStruct:
        vector = {DENSE: dense}
        if sparse.indices:  # text without a single indexable word has no keyword vector
            vector[SPARSE] = models.SparseVector(indices=sparse.indices, values=sparse.values)
        return models.PointStruct(id=point_id, vector=vector, payload=payload)

    def upsert(self, points: Sequence[models.PointStruct]) -> None:
        for start in range(0, len(points), self.UPSERT_BATCH):
            self.client.upsert(collection_name=self.collection, points=list(points[start:start + self.UPSERT_BATCH]),
                               wait=True)

    def delete_stale(self, source: str, keep_ids: Sequence[str]) -> None:
        """Removes chunks of a re-ingested document that its new version no longer contains."""
        flt = models.Filter(must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))],
                            must_not=[models.HasIdCondition(has_id=list(keep_ids))] if keep_ids else None)
        self.client.delete(collection_name=self.collection, points_selector=models.FilterSelector(filter=flt),
                           wait=True)

    def query(self, dense: List[float], sparse: Optional[SparseVec], limit: int,
              dense_floor: Optional[float] = None) -> List[models.ScoredPoint]:
        """Hybrid query: dense and (if given) BM25 candidates, fused with RRF, so scores are always rank-based."""
        candidates = max(limit * 4, 20)
        prefetch = [models.Prefetch(query=dense, using=DENSE, limit=candidates, score_threshold=dense_floor)]
        if sparse is not None and sparse.indices:
            prefetch.append(models.Prefetch(query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                                            using=SPARSE, limit=candidates))
        return self.client.query_points(collection_name=self.collection, prefetch=prefetch,
                                        query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit,
                                        with_payload=True).points

    def count(self) -> int:
        return self.client.count(collection_name=self.collection, exact=True).count

    def records(self, with_vectors: bool = False) -> Iterator[models.Record]:
        offset = None
        while True:
            batch, offset = self.client.scroll(collection_name=self.collection, limit=256, offset=offset,
                                               with_payload=True, with_vectors=with_vectors)
            yield from batch
            if offset is None:
                break

    def payloads(self, fields: Sequence[str]) -> Iterator[dict]:
        """The payload of every point, limited to `fields` (a source listing never reads the chunk text)."""
        offset = None
        while True:
            batch, offset = self.client.scroll(collection_name=self.collection, limit=512, offset=offset,
                                               with_payload=list(fields), with_vectors=False)
            for record in batch:
                yield record.payload or {}
            if offset is None:
                break

    def source_records(self, source: str) -> Iterator[models.Record]:
        """The points of one source (filtered on the `source` payload field)."""
        flt = models.Filter(must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))])
        offset = None
        while True:
            batch, offset = self.client.scroll(collection_name=self.collection, scroll_filter=flt, limit=256,
                                               offset=offset, with_payload=True, with_vectors=False)
            yield from batch
            if offset is None:
                break

    def recreate(self) -> None:
        self.client.delete_collection(self.collection)
        self._create()
        self._point_alias()

    def drop_all(self) -> None:
        """Deletes the collections of every embedding model of this knowledge base."""
        for name in self.knowledge_collections():
            self.client.delete_collection(name)

    # --- snapshots (portable files; local Qdrant has no snapshot API) ---

    def _snapshot_header(self) -> str:
        return json.dumps({"format": SNAPSHOT_FORMAT, "version": 1, "collection": self.collection,
                           "embedding_model": self.model_id, "dim": self.dim,
                           "created": datetime.now().isoformat()}) + "\n"

    @staticmethod
    def _snapshot_line(record) -> str:
        vectors = record.vector if isinstance(record.vector, dict) else {}
        item = {"id": str(record.id), "payload": record.payload or {}}
        if vectors.get(DENSE) is not None:
            item["dense"] = base64.b64encode(np.asarray(vectors[DENSE], dtype="<f4").tobytes()).decode()
        if vectors.get(SPARSE) is not None:
            item["sparse"] = {"indices": list(vectors[SPARSE].indices), "values": list(vectors[SPARSE].values)}
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

    def restore_file(self, path: Path) -> int:
        """Replaces the collection with a snapshot; the whole file is checked before anything is deleted."""
        points = []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            header = json.loads(f.readline() or "{}")
            if header.get("format") != SNAPSHOT_FORMAT:
                raise KnowledgeBaseError(f"{path.name} is not a knowledge base snapshot.")
            if header.get("embedding_model") != self.model_id or header.get("dim") != self.dim:
                raise KnowledgeBaseError(f"{path.name} was made with {header.get('embedding_model')}, "
                                         f"the active model is {self.model_id}.")
            for line in f:
                item = json.loads(line)
                vector = {}
                if "dense" in item:
                    dense = np.frombuffer(base64.b64decode(item["dense"]), dtype="<f4")
                    if dense.shape != (self.dim,):
                        raise KnowledgeBaseError(f"{path.name} contains a vector of the wrong dimension.")
                    vector[DENSE] = dense.tolist()
                if "sparse" in item:
                    vector[SPARSE] = models.SparseVector(**item["sparse"])
                points.append(models.PointStruct(id=item["id"], vector=vector, payload=item.get("payload") or {}))
        self.recreate()
        self.upsert(points)
        return len(points)
