#!/usr/bin/env python3
"""
The knowledge base through qdrant-client: on a Qdrant server (setting qdrant_url), and in the local storage of
earlier versions (qdrant-client's local mode, a pure-Python re-implementation of Qdrant meant for development) while
that storage is still in use because its conversion to Qdrant Edge failed (orchestrator/rag/migration.py).

qdrant-client loads only when one of these is used: the local knowledge base of this version is Qdrant Edge.
"""

import os
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from qdrant_client import models

from orchestrator.rag.embeddings import SparseVec
from orchestrator.rag.errors import KnowledgeBaseError
from orchestrator.rag.store import (DENSE, FORMAT_OLD_LOCAL, FORMAT_SERVER, SCHEMA_VERSION, SPARSE,
                                    KnowledgeStore, ScoredHit, StoredRecord)


def _record(record) -> StoredRecord:
    return StoredRecord(id=str(record.id), payload=dict(record.payload or {}),
                        vector=record.vector if isinstance(record.vector, dict) else None)


class ClientKnowledgeStore(KnowledgeStore):
    """The active collection of one embedding model on an open Qdrant client (server, or the old local storage)."""

    def __init__(self, client, alias: str, model_id: str, dim: int, server_mode: bool = False):
        super().__init__(client, alias, model_id, dim, server_mode)
        self.store_format = FORMAT_SERVER if server_mode else FORMAT_OLD_LOCAL

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
        sparse = (config.params.sparse_vectors or {}).get(SPARSE)
        if dense.size == self.dim and (sparse is None or sparse.modifier != models.Modifier.IDF):
            raise KnowledgeBaseError(f"Collection {self.collection} lacks the BM25 sparse vector with IDF. "
                                     "Clear the knowledge base to rebuild it.")
        self._check_built_with(dense.size, (config.metadata or {}).get("embedding_model"))

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
        if sparse is not None and sparse.indices:  # text without a single indexable word has no keyword vector
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

    def delete_origin_stale(self, origin_pack: str, keep_sha256: str) -> None:
        """Removes the excerpts of an installed pack (origin_pack) that its current import did not write (their
        origin_sha256 is another one, or missing)."""
        flt = models.Filter(
            must=[models.FieldCondition(key="origin_pack", match=models.MatchValue(value=origin_pack))],
            must_not=[models.FieldCondition(key="origin_sha256", match=models.MatchValue(value=keep_sha256))])
        self.client.delete(collection_name=self.collection, points_selector=models.FilterSelector(filter=flt),
                           wait=True)

    def query(self, dense: List[float], sparse: Optional[SparseVec], limit: int,
              dense_floor: Optional[float] = None) -> List[ScoredHit]:
        """Hybrid query: dense and (if given) BM25 candidates, fused with RRF, so scores are always rank-based."""
        candidates = max(limit * 4, 20)
        prefetch = [models.Prefetch(query=dense, using=DENSE, limit=candidates, score_threshold=dense_floor)]
        if sparse is not None and sparse.indices:
            prefetch.append(models.Prefetch(query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                                            using=SPARSE, limit=candidates))
        # The plain RRF query (Qdrant's default k, RRF_K) also works on servers before the k parameter (1.16).
        points = self.client.query_points(collection_name=self.collection, prefetch=prefetch,
                                          query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit,
                                          with_payload=True).points
        return [ScoredHit(id=str(p.id), score=float(p.score), payload=dict(p.payload or {})) for p in points]

    def count(self) -> int:
        return self.client.count(collection_name=self.collection, exact=True).count

    def records(self, with_vectors: bool = False) -> Iterator[StoredRecord]:
        offset = None
        while True:
            batch, offset = self.client.scroll(collection_name=self.collection, limit=256, offset=offset,
                                               with_payload=True, with_vectors=with_vectors)
            for record in batch:
                yield _record(record)
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

    def source_records(self, source: str) -> Iterator[StoredRecord]:
        """The points of one source (filtered on the `source` payload field)."""
        flt = models.Filter(must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))])
        offset = None
        while True:
            batch, offset = self.client.scroll(collection_name=self.collection, scroll_filter=flt, limit=256,
                                               offset=offset, with_payload=True, with_vectors=False)
            for record in batch:
                yield _record(record)
            if offset is None:
                break

    def recreate(self) -> None:
        self._delete_collection(self.collection)
        self._create()
        self._point_alias()

    def drop_all(self) -> None:
        """Deletes the collections of every embedding model of this knowledge base."""
        for name in self.knowledge_collections():
            self._delete_collection(name)

    def status(self) -> str:
        return self.client.get_collection(self.collection).status.name

    def _delete_collection(self, name: str) -> None:
        """Deletes one collection. Embedded Qdrant deletes the collection's folder with errors ignored, and on Windows a
        file that is still open cannot be deleted: its storage file stayed, and the next start (or the collection made
        again right away) loaded every vector back, while clearing said it had worked. So the collection's storage is
        closed first, and a folder that is still there afterwards is an error."""
        local = getattr(self.client, "_client", None)  # QdrantLocal behind QdrantClient(path=...)
        collections = getattr(local, "collections", None)
        collection = collections.get(name) if isinstance(collections, dict) else None
        close = getattr(collection, "close", None)
        if callable(close):
            close()
        self.client.delete_collection(name)
        where = getattr(local, "_collection_path", None)
        folder = where(name) if callable(where) else None
        if isinstance(folder, (str, Path)) and os.path.exists(folder):
            raise KnowledgeBaseError(f"The stored vectors of {name} could not be deleted (a file in their folder is "
                                     "still open, for example by another program). Close it and try again.")
