#!/usr/bin/env python3
"""
The local knowledge base in Qdrant Edge (DECISIONS RG5).

Qdrant Edge is the Rust engine of Qdrant as a library in this process (PyPI qdrant-edge-py, Apache-2.0): no server,
no open port, no Docker. One Edge shard holds one collection; the storage folder (`<state>/qdrant_edge`) holds:

    collections/<collection>/       the shard of one collection (one per embedding model ever used)
    collections/<collection>.json   what that collection holds: embedding model, dimension, schema
    aliases.json                    which collection the alias names (the active one)

Only one process may open the storage at a time. Qdrant Edge locks a shard's write-ahead log itself, so even a reader
cannot open a shard another process holds. The framework takes its own lock first (`<state>/qdrant_edge.lock`,
filelock: works on Linux and Windows), for the whole time the storage is open and around the conversion of the old
storage, so the GUI, the CLI and the MCP server take turns: each one releases the storage after a short idle time
(RAGManager.IDLE_CLOSE_SECONDS), and a second process waits a few seconds for it (RAGManager.LOCK_WAIT_SECONDS) and
then gets a clear "in use by another framework process" error instead of a broken read.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import qdrant_edge as qe

from orchestrator.rag.embeddings import SparseVec
from orchestrator.rag.errors import KnowledgeBaseError
from orchestrator.utils.helpers import safe_json_load
from orchestrator.rag.storage_lock import LOCK_SUFFIX, lock_path, remove_tree, storage_lock, try_lock  # noqa: F401
from orchestrator.rag.store import (DENSE, FORMAT_EDGE, RRF_K, SCHEMA_VERSION, SPARSE, KnowledgeStore, ScoredHit,
                                    StoredRecord)

COLLECTIONS = "collections"
ALIASES = "aliases.json"
# Points per update call: the engine is in this process, so larger batches than over a network are fine.
EDGE_BATCH = 256
# Payload fields the knowledge base filters on (delete/replace per source, the source listing, the reader).
INDEXED_FIELDS = ("source", "chunk_hash")
# Search the dense vectors exactly (a plain index, as the earlier local storage did): with the knowledge base's sizes
# (thousands to some hundred thousand excerpts) an exact search takes milliseconds, every result is the true nearest
# one, and optimize() only merges segments and drops deleted points instead of building a graph index for half a
# minute. 0 turns Qdrant's vector indexing off.
INDEXING_THRESHOLD_KB = 0


class StorageBusy(KnowledgeBaseError):
    """Another framework process holds the knowledge base storage."""


def edge_config(dim: int):
    return qe.EdgeConfig(vectors={DENSE: qe.EdgeVectorParams(size=int(dim), distance=qe.Distance.Cosine)},
                         sparse_vectors={SPARSE: qe.EdgeSparseVectorParams(modifier=qe.Modifier.Idf)},
                         on_disk_payload=True,
                         optimizers=qe.EdgeOptimizersConfig(indexing_threshold=INDEXING_THRESHOLD_KB))


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    """A shard file's object, {} when it is missing, unreadable or no JSON object (utils/helpers.safe_json_load)."""
    return safe_json_load(path, None, dict) or {}


def _busy(error: Exception) -> bool:
    """Qdrant Edge's refusal to open a shard another process (or another handle) holds: its write-ahead log is
    locked ("WouldBlock")."""
    text = str(error)
    return "WouldBlock" in text or "Resource temporarily unavailable" in text or "os error 33" in text


class EdgeStorage:
    """The storage folder of the local knowledge base: its collections (one Edge shard each), their metadata and the
    alias. The caller holds the storage lock (storage_lock) while this is open."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._shards: Dict[str, Any] = {}

    @property
    def collections_dir(self) -> Path:
        return self.root / COLLECTIONS

    def _path(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith("."):
            raise KnowledgeBaseError(f"'{name}' is not a collection name of this framework.")
        return self.collections_dir / name

    def _meta_path(self, name: str) -> Path:
        return self.collections_dir / f"{name}.json"

    def collection_names(self) -> List[str]:
        """The collections in the folder: a shard folder with its metadata file."""
        folder = self.collections_dir
        if not folder.is_dir():
            return []
        return sorted(p.name for p in folder.iterdir()
                      if p.is_dir() and not p.name.startswith(".") and self._meta_path(p.name).is_file())

    def meta(self, name: str) -> Dict[str, Any]:
        return _read_json(self._meta_path(name))

    def model_of(self, name: Optional[str]) -> Optional[str]:
        return (self.meta(name).get("embedding_model") or None) if name else None

    def alias_target(self, alias: str) -> Optional[str]:
        target = _read_json(self.root / ALIASES).get(alias)
        return target if isinstance(target, str) and target in self.collection_names() else None

    def set_alias(self, alias: str, name: str) -> None:
        aliases = _read_json(self.root / ALIASES)
        if aliases.get(alias) == name:
            return
        aliases[alias] = name
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json(self.root / ALIASES, aliases)

    def shard(self, name: str):
        """The open shard of a collection (opened on first use)."""
        shard = self._shards.get(name)
        if shard is None:
            path = self._path(name)
            if not path.is_dir():
                raise KnowledgeBaseError(f"Collection {name} does not exist.")
            try:
                shard = qe.EdgeShard.load(str(path))
            except Exception as e:
                if _busy(e):
                    raise StorageBusy(f"The knowledge base storage {self.root} is in use by another framework "
                                      "process (GUI, CLI or MCP server). Try again shortly.") from None
                raise
            self._shards[name] = shard
        return shard

    def create(self, name: str, dim: int, model_id: str, extra: Optional[Dict[str, Any]] = None):
        """Creates a collection (an empty shard with the dense and the BM25 vector and the payload indexes)."""
        path = self._path(name)
        if path.exists():
            raise KnowledgeBaseError(f"Collection {name} exists already.")
        path.mkdir(parents=True)
        try:
            shard = qe.EdgeShard.create(str(path), edge_config(dim))
            for field in INDEXED_FIELDS:
                shard.update(qe.UpdateOperation.create_field_index(field, qe.PayloadSchemaType.Keyword))
            shard.flush()
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        self._shards[name] = shard
        _write_json(self._meta_path(name), {"embedding_model": model_id, "dim": int(dim), "schema": SCHEMA_VERSION,
                                            **(extra or {})})
        return shard

    def close_shard(self, name: str) -> None:
        shard = self._shards.pop(name, None)
        if shard is not None:
            shard.close()

    def delete(self, name: str) -> None:
        """Deletes one collection. Its shard is closed first: on Windows a file that is still open (mapped) cannot be
        deleted, and a folder that is still there afterwards is an error, never a silent success."""
        self.close_shard(name)
        path = self._path(name)
        if path.exists():
            remove_tree(path)
        if path.exists():
            raise KnowledgeBaseError(f"The stored vectors of {name} could not be deleted (a file in their folder is "
                                     "still open, for example by another program). Close it and try again.")
        try:
            self._meta_path(name).unlink()
        except FileNotFoundError:
            pass
        aliases = _read_json(self.root / ALIASES)
        stale = [a for a, target in aliases.items() if target == name]
        if stale:
            for alias in stale:
                aliases.pop(alias)
            _write_json(self.root / ALIASES, aliases)

    def flush(self) -> None:
        for shard in self._shards.values():
            shard.flush()

    def close(self) -> None:
        """Closes every open shard (each flushes to disk); the caller releases the lock."""
        errors = []
        for name in list(self._shards):
            try:
                self.close_shard(name)
            except Exception as e:  # noqa: BLE001 - the other shards are still closed
                errors.append(e)
        if errors:
            raise errors[0]


def _record(record) -> StoredRecord:
    vector = record.vector if isinstance(record.vector, dict) else None
    return StoredRecord(id=str(record.id), payload=dict(record.payload or {}), vector=vector)


class EdgeKnowledgeStore(KnowledgeStore):
    """The active collection of one embedding model in the local Qdrant Edge storage (`client` is an EdgeStorage)."""

    store_format = FORMAT_EDGE

    def __init__(self, client: EdgeStorage, alias: str, model_id: str, dim: int, server_mode: bool = False):
        super().__init__(client, alias, model_id, dim, server_mode=False)
        self.written = 0  # points written or deleted since the last optimize()

    @property
    def _shard(self):
        return self.client.shard(self.collection)

    def knowledge_collections(self) -> List[str]:
        return [n for n in self.client.collection_names() if n.startswith(f"{self.alias}__")]

    # --- schema ---

    def ensure(self) -> None:
        if self.collection in self.client.collection_names():
            meta = self.client.meta(self.collection)
            dim = meta.get("dim")
            self._check_built_with(int(dim) if isinstance(dim, int) else None, meta.get("embedding_model"))
            self._shard  # noqa: B018 - opens it now: a storage another process holds fails here, not on first use
        else:
            self.client.create(self.collection, self.dim, self.model_id)
        self.client.set_alias(self.alias, self.collection)

    def recreate(self) -> None:
        if self.collection in self.client.collection_names():
            self.client.delete(self.collection)
        self.client.create(self.collection, self.dim, self.model_id)
        self.client.set_alias(self.alias, self.collection)
        self.written = 0

    def drop_all(self) -> None:
        for name in self.knowledge_collections():
            self.client.delete(name)

    # --- data ---

    @staticmethod
    def point(point_id: str, dense: List[float], sparse: SparseVec, payload: dict):
        vector = {DENSE: [float(x) for x in dense]}
        if sparse is not None and sparse.indices:  # text without a single indexable word has no keyword vector
            vector[SPARSE] = qe.SparseVector(indices=[int(i) for i in sparse.indices],
                                             values=[float(v) for v in sparse.values])
        return qe.Point(id=str(point_id), vector=vector, payload=payload)

    def upsert(self, points: Sequence[Any], flush: bool = True) -> None:
        """Writes the points; flush=False leaves them in the write-ahead log until the next flush or close (a bulk
        copy flushes once at its end)."""
        shard = self._shard
        for start in range(0, len(points), EDGE_BATCH):
            shard.update(qe.UpdateOperation.upsert_points(list(points[start:start + EDGE_BATCH])))
        if flush:
            shard.flush()
        self.written += len(points)

    @staticmethod
    def _source_filter(source: str, keep_ids: Optional[Sequence[str]] = None):
        must = [qe.FieldCondition(key="source", match=qe.MatchValue(value=source))]
        must_not = [qe.HasIdCondition(point_ids={str(i) for i in keep_ids})] if keep_ids else None
        return qe.Filter(must=must, must_not=must_not)

    def delete_stale(self, source: str, keep_ids: Sequence[str]) -> None:
        """Removes chunks of a re-ingested document that its new version no longer contains."""
        shard = self._shard
        shard.update(qe.UpdateOperation.delete_points_by_filter(self._source_filter(source, keep_ids)))
        shard.flush()
        self.written += 1

    def delete_origin_stale(self, origin_pack: str, keep_sha256: str) -> None:
        """Removes the excerpts of an installed pack (origin_pack) that its current import did not write (their
        origin_sha256 is another one, or missing)."""
        shard = self._shard
        flt = qe.Filter(must=[qe.FieldCondition(key="origin_pack", match=qe.MatchValue(value=origin_pack))],
                        must_not=[qe.FieldCondition(key="origin_sha256", match=qe.MatchValue(value=keep_sha256))])
        shard.update(qe.UpdateOperation.delete_points_by_filter(flt))
        shard.flush()
        self.written += 1

    def query(self, dense: List[float], sparse: Optional[SparseVec], limit: int,
              dense_floor: Optional[float] = None) -> List[ScoredHit]:
        """Hybrid query: dense and (if given) BM25 candidates, fused with RRF, so scores are always rank-based."""
        candidates = max(limit * 4, 20)
        prefetch = [qe.Prefetch(limit=candidates, query=qe.Query.Nearest([float(x) for x in dense], using=DENSE),
                                score_threshold=dense_floor)]
        if sparse is not None and sparse.indices:
            keyword = qe.SparseVector(indices=[int(i) for i in sparse.indices],
                                      values=[float(v) for v in sparse.values])
            prefetch.append(qe.Prefetch(limit=candidates, query=qe.Query.Nearest(keyword, using=SPARSE)))
        hits = self._shard.query(qe.QueryRequest(limit=limit, prefetches=prefetch, query=qe.Fusion.Rrf(k=RRF_K),
                                                 with_payload=True))
        return [ScoredHit(id=str(h.id), score=float(h.score), payload=dict(h.payload or {})) for h in hits]

    def count(self) -> int:
        return int(self._shard.count(qe.CountRequest(exact=True)))

    def _scroll(self, limit: int, with_payload: Any, with_vectors: bool = False, flt: Any = None) -> Iterator[Any]:
        shard = self._shard
        offset = None
        while True:
            batch, offset = shard.scroll(qe.ScrollRequest(offset=offset, limit=limit, filter=flt,
                                                          with_payload=with_payload, with_vector=with_vectors))
            yield from batch
            if offset is None or not batch:
                break

    def records(self, with_vectors: bool = False) -> Iterator[StoredRecord]:
        for record in self._scroll(256, True, with_vectors):
            yield _record(record)

    def payloads(self, fields: Sequence[str]) -> Iterator[dict]:
        """The payload of every point, limited to `fields` (a source listing never reads the chunk text)."""
        for record in self._scroll(512, list(fields)):
            yield dict(record.payload or {})

    def source_records(self, source: str, with_vectors: bool = False) -> Iterator[StoredRecord]:
        """The points of one source (filtered on the `source` payload field)."""
        for record in self._scroll(256, True, with_vectors, flt=self._source_filter(source)):
            yield _record(record)

    def records_where(self, key: str, value: str, with_vectors: bool = False) -> Iterator[StoredRecord]:
        """The points whose payload field `key` is `value`, or holds it (a list field such as the holders)."""
        flt = qe.Filter(must=[qe.FieldCondition(key=key, match=qe.MatchValue(value=value))])
        for record in self._scroll(256, True, with_vectors, flt=flt):
            yield _record(record)

    def delete_ids(self, point_ids: Sequence[str]) -> None:
        """Removes exactly these points."""
        if not point_ids:
            return
        shard = self._shard
        shard.update(qe.UpdateOperation.delete_points([str(i) for i in point_ids]))
        shard.flush()
        self.written += 1

    def retrieve(self, point_ids: Sequence[str], with_vectors: bool = True) -> List[StoredRecord]:
        return [_record(r) for r in self._shard.retrieve([str(i) for i in point_ids], with_payload=True,
                                                         with_vector=with_vectors)]

    def optimize(self) -> bool:
        """Merges segments and drops deleted points (blocking); after a restore, the conversion and bulk writes."""
        changed = bool(self._shard.optimize())
        self.written = 0
        return changed
