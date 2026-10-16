"""The local knowledge base in Qdrant Edge (DECISIONS RG5): the store contract on every backend, the snapshot files of
both versions, the conversion of the storage of earlier versions (success, failure, a check that does not match, the
disk), the old folder that only the person deletes, and what the person and an AI see of it.

The old storage is real (qdrant-client's local mode in tmp_path), so is Qdrant Edge; the embedders are the
deterministic fakes of test_rag.
"""

import base64
import gzip
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator.rag.embeddings import SparseVec
from orchestrator.rag.errors import KnowledgeBaseError
from test_rag import DOCS, FakeEmbedder, ingest_all, make_rag  # noqa: F401  (make_rag is a fixture)

ALIAS = "framework_knowledge"
DIM = 4


# --- the two backends behind one contract -----------------------------------------------------------------------------

def _points(store, n=6):
    """n points over two sources; point i points at axis i mod 3 (a little apart from every other point, so no two
    tie in a ranking) and has its own keyword 100 + i."""
    out = []
    for i in range(n):
        dense = [0.05] * DIM
        dense[i % 3] = 1.0
        dense[3] = 0.05 + 0.03 * i
        out.append(store.point(str(uuid.uuid5(uuid.NAMESPACE_URL, f"p{i}")), dense, SparseVec([100 + i, 7], [2.0, 1.0]),
                               {"source": f"s{i % 2}", "chunk_index": i, "content": f"text {i}", "chunk_hash": f"h{i}",
                                "tags": ["a", "b"], "score_hint": 0.5, "flag": True}))
    return out


@pytest.fixture(params=["edge", "old-local"])
def backend(request, tmp_path):
    """A store of each backend, opened in tmp_path; closed afterwards."""
    if request.param == "edge":
        from orchestrator.rag.edge_store import EdgeKnowledgeStore, EdgeStorage
        client = EdgeStorage(tmp_path / "qdrant_edge")
        make = lambda model="fake/model", dim=DIM: EdgeKnowledgeStore(client, ALIAS, model, dim)  # noqa: E731
    else:
        from qdrant_client import QdrantClient

        from orchestrator.rag.client_store import ClientKnowledgeStore
        client = QdrantClient(path=str(tmp_path / "qdrant"))
        make = lambda model="fake/model", dim=DIM: ClientKnowledgeStore(client, ALIAS, model, dim)  # noqa: E731
    yield SimpleNamespace(kind=request.param, client=client, make=make)
    client.close()


class TestStoreContract:

    def test_ensure_creates_the_collection_and_points_the_alias(self, backend):
        from orchestrator.rag.store import KnowledgeStore
        store = backend.make()
        store.ensure()
        assert store.count() == 0
        assert store.knowledge_collections() == [store.collection]
        assert KnowledgeStore.alias_target(backend.client, ALIAS) == store.collection
        assert KnowledgeStore.model_of(backend.client, store.collection) == "fake/model"
        assert store.status() == "GREEN"
        assert store.store_format == {"edge": "qdrant-edge", "old-local": "old-local"}[backend.kind]

    def test_a_second_model_gets_its_own_collection_and_the_alias_follows(self, backend):
        from orchestrator.rag.store import KnowledgeStore
        a = backend.make("fake/a")
        a.ensure()
        a.upsert(_points(a))
        b = backend.make("fake/b")
        b.ensure()
        assert sorted(b.knowledge_collections()) == sorted([a.collection, b.collection])
        assert KnowledgeStore.alias_target(backend.client, ALIAS) == b.collection
        assert b.count() == 0 and a.count() == 6

    def test_a_collection_of_another_dimension_or_model_is_refused(self, backend):
        store = backend.make()
        store.ensure()
        with pytest.raises(KnowledgeBaseError, match="4-dimensional"):
            backend.make(dim=8).ensure()

    def test_upsert_query_and_payload_round_trip(self, backend):
        store = backend.make()
        store.ensure()
        points = _points(store)
        store.upsert(points)
        store.upsert(points[:2])  # the same ids again: updated in place, never duplicated
        assert store.count() == 6
        hits = store.query([1.0, 0.05, 0.05, 0.05], SparseVec([100], [1.0]), 3)
        assert all(isinstance(h.id, str) for h in hits)
        assert hits[0].payload["chunk_index"] == 0  # first in both rankings
        assert hits[0].payload == {"source": "s0", "chunk_index": 0, "content": "text 0", "chunk_hash": "h0",
                                   "tags": ["a", "b"], "score_hint": 0.5, "flag": True}
        assert hits[0].score == pytest.approx(2 / 2)  # RRF with k=2: 1/2 from each ranking

    def test_the_fused_scores_are_the_same_on_both_backends(self, tmp_path):
        """RRF k=2 as before: a knowledge base converted to Qdrant Edge ranks and scores as the old storage did."""
        from qdrant_client import QdrantClient

        from orchestrator.rag.client_store import ClientKnowledgeStore
        from orchestrator.rag.edge_store import EdgeKnowledgeStore, EdgeStorage
        old_client, edge = QdrantClient(path=str(tmp_path / "old")), EdgeStorage(tmp_path / "edge")
        try:
            stores = [ClientKnowledgeStore(old_client, ALIAS, "m", DIM), EdgeKnowledgeStore(edge, ALIAS, "m", DIM)]
            results = []
            for store in stores:
                store.ensure()
                # no two points alike in either ranking (ties may come in any order on any engine)
                store.upsert([store.point(str(uuid.uuid5(uuid.NAMESPACE_URL, f"q{i}")),
                                          [float(np.cos(i / 3)), float(np.sin(i / 3)), 0.1 * i, 0.05],
                                          SparseVec([7, 100 + i % 4], [1.0 + 0.1 * i, 0.5 + i]), {"n": i})
                              for i in range(12)])
                hits = store.query([0.2, 1.0, 0.05, 0.3], SparseVec([101, 7], [1.0, 1.0]), 6)
                results.append([(h.id, round(h.score, 6)) for h in hits])
            assert [s for _, s in results[0]] == [s for _, s in results[1]]
            assert results[0][0] == results[1][0]
        finally:
            old_client.close()
            edge.close()

    def test_a_dense_floor_drops_weak_candidates(self, backend):
        store = backend.make()
        store.ensure()
        store.upsert(_points(store))
        hits = store.query([1.0, 0.0, 0.0, 0.0], None, 10, dense_floor=0.9)
        assert {h.payload["chunk_index"] for h in hits} == {0, 3}

    def test_delete_stale_keeps_only_the_new_version_of_a_source(self, backend):
        store = backend.make()
        store.ensure()
        points = _points(store)
        store.upsert(points)
        keep = [str(points[0].id)]
        store.delete_stale("s0", keep)
        left = sorted(r.payload["chunk_index"] for r in store.records())
        assert left == [0, 1, 3, 5]
        store.delete_stale("s1", [])
        assert sorted(r.payload["chunk_index"] for r in store.records()) == [0]

    def test_records_payload_fields_and_source_records(self, backend):
        store = backend.make()
        store.ensure()
        store.upsert(_points(store))
        records = list(store.records(with_vectors=True))
        assert len(records) == 6 and all(isinstance(r.id, str) for r in records)
        first = next(r for r in records if r.payload["chunk_index"] == 1)
        dense = np.asarray(first.vector["dense"])
        assert np.linalg.norm(dense) == pytest.approx(1.0, abs=1e-5)  # cosine: stored normalized
        assert list(first.vector["bm25"].indices) == [7, 101] or list(first.vector["bm25"].indices) == [101, 7]
        assert all(set(p) <= {"source", "title"} for p in store.payloads(["source", "title"]))
        assert sorted(r.payload["chunk_index"] for r in store.source_records("s1")) == [1, 3, 5]

    def test_recreate_and_drop_all(self, backend):
        store = backend.make()
        store.ensure()
        store.upsert(_points(store))
        store.recreate()
        assert store.count() == 0 and store.knowledge_collections() == [store.collection]
        other = backend.make("fake/other")
        other.ensure()
        other.drop_all()
        assert other.knowledge_collections() == []

    def test_snapshot_round_trip_on_this_backend(self, backend, tmp_path):
        store = backend.make()
        store.ensure()
        store.upsert(_points(store))
        path = tmp_path / "snap.jsonl.gz"
        assert store.export_file(path) == 6
        header = json.loads(gzip.open(path, "rt").readline())
        assert header["version"] == 2 and header["store"] == store.store_format
        assert 0 < store.export_estimate() and path.stat().st_size <= store.export_estimate()
        store.recreate()
        assert store.restore_file(path) == 6 and store.count() == 6
        assert store.query([1.0, 0.05, 0.05, 0.05], None, 1)[0].payload["chunk_index"] in (0, 3)


# --- snapshot files of both versions ----------------------------------------------------------------------------------

def write_v1_snapshot(path: Path, model: str, dim: int, items) -> None:
    """A snapshot as the local storage of earlier versions wrote it (format version 1, no "store")."""
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"format": "llm-framework-knowledge-snapshot", "version": 1,
                            "collection": f"{ALIAS}__x", "embedding_model": model, "dim": dim,
                            "created": "2026-09-01T10:00:00"}) + "\n")
        for pid, dense, sparse, payload in items:
            line = {"id": pid, "payload": payload,
                    "dense": base64.b64encode(np.asarray(dense, dtype="<f4").tobytes()).decode()}
            if sparse:
                line["sparse"] = {"indices": sparse[0], "values": sparse[1]}
            f.write(json.dumps(line) + "\n")


class TestSnapshotFormats:

    def test_an_old_format_snapshot_is_converted_on_restore(self, make_rag):  # noqa: F811
        rag = make_rag()
        ingest_all(rag)
        old = []
        for record in rag._with_store(lambda s: list(s.records(with_vectors=True))):
            sparse = record.vector.get("bm25")
            old.append((record.id, record.vector["dense"],
                        (list(sparse.indices), list(sparse.values)) if sparse is not None else None, record.payload))
        rag.snapshot_dir.mkdir(parents=True, exist_ok=True)
        write_v1_snapshot(rag.snapshot_dir / "from_old_storage.jsonl.gz", "fake/concepts", 16, old)
        assert rag.clear_knowledge_base() and rag.get_status()["vector_count"] == 0
        assert rag.restore_snapshot("from_old_storage")
        assert rag.get_status()["vector_count"] == len(old) and rag.get_status()["store_format"] == "qdrant-edge"
        assert rag.search("what does E2041 mean")[0].source == "https://docs.example/e2041"

    def test_a_snapshot_of_a_newer_format_is_refused_before_anything_changes(self, make_rag):  # noqa: F811
        rag = make_rag()
        ingest_all(rag)
        count = rag.get_status()["vector_count"]
        rag.snapshot_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(rag.snapshot_dir / "future.jsonl.gz", "wt") as f:
            f.write(json.dumps({"format": "llm-framework-knowledge-snapshot", "version": 3,
                                "embedding_model": "fake/concepts", "dim": 16}) + "\n")
        assert rag.restore_snapshot("future") is False
        assert "newer framework version" in rag.last_error
        assert rag.get_status()["vector_count"] == count

    def test_a_broken_line_leaves_the_knowledge_base_as_it_was(self, make_rag):  # noqa: F811
        rag = make_rag()
        ingest_all(rag)
        count = rag.get_status()["vector_count"]
        rag.snapshot_dir.mkdir(parents=True, exist_ok=True)
        write_v1_snapshot(rag.snapshot_dir / "short.jsonl.gz", "fake/concepts", 16,
                          [(str(uuid.uuid4()), [0.1] * 8, None, {"source": "x"})])  # 8 values, the model has 16
        assert rag.restore_snapshot("short") is False
        assert rag.get_status()["vector_count"] == count


# --- the conversion of the old storage --------------------------------------------------------------------------------

def build_old_store(state: Path, model="fake/concepts", dim=16, docs=DOCS, extra_model=True, legacy=True):
    """An old local store as earlier versions left it: the knowledge base of `model` (the alias points at it), a
    collection of an earlier model and a legacy collection named like the alias (not converted). Returns the points
    of the active collection {id: payload}."""
    from qdrant_client import QdrantClient, models

    from orchestrator.rag.chunking import point_id
    from test_rag import FakeSparse
    client = QdrantClient(path=str(state / "qdrant"))
    embedder, sparse = FakeEmbedder(model, dim), FakeSparse()

    def make(name, model_id, size):
        client.create_collection(name, vectors_config={"dense": models.VectorParams(size=size,
                                                                                    distance=models.Distance.COSINE)},
                                 sparse_vectors_config={"bm25": models.SparseVectorParams(
                                     modifier=models.Modifier.IDF)},
                                 metadata={"embedding_model": model_id, "dim": size, "schema": 2})

    active = f"{ALIAS}__{model.replace('/', '-')}"
    make(active, model, dim)
    payloads = {}
    points = []
    for n, (source, text) in enumerate(docs.items()):
        pid = point_id(source, f"h{n}")
        payload = {"source": source, "content": text, "chunk_hash": f"h{n}", "chunk_index": 0, "section": "",
                   "embedding_model": model, "ingested_at": "2026-09-01T10:00:00", "title": source.rsplit("/", 1)[1]}
        vec = sparse.embed_passages([text])[0]
        points.append(models.PointStruct(id=pid, vector={"dense": embedder.embed_passages([text])[0],
                                                         "bm25": models.SparseVector(indices=vec.indices,
                                                                                     values=vec.values)},
                                         payload=payload))
        payloads[pid] = payload
    client.upsert(active, points)
    client.update_collection_aliases(change_aliases_operations=[models.CreateAliasOperation(
        create_alias=models.CreateAlias(collection_name=active, alias_name=ALIAS))])
    if extra_model:
        make(f"{ALIAS}__fake-earlier", "fake/earlier", 8)
        client.upsert(f"{ALIAS}__fake-earlier", [models.PointStruct(id=str(uuid.uuid4()), vector={"dense": [1.0] * 8},
                                                                    payload={"source": "old"})])
    if legacy:
        client.create_collection("legacy_other", vectors_config=models.VectorParams(size=3,
                                                                                     distance=models.Distance.COSINE))
    client.close()
    return payloads


class TestConversion:

    def test_an_old_store_is_converted_checked_recorded_and_kept(self, make_rag, tmp_path):  # noqa: F811
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        payloads = build_old_store(state)
        rag = make_rag()
        status = rag.get_status()
        assert status["store_format"] == "qdrant-edge" and status["vector_count"] == len(payloads)
        assert status["other_collections"] == [f"{ALIAS}__fake-earlier"]
        assert rag.search("what does E2041 mean")[0].source == "https://docs.example/e2041"
        stored = {r.id: r.payload for r in rag._with_store(lambda s: list(s.records()))}
        assert stored == payloads  # ids and payloads exactly; nothing was embedded again
        record = migration.read_record(state)
        assert record["state"] == "converted" and record["points"] == len(payloads) + 1
        assert record["verified"]["counts"] is True and record["verified"]["searches_compared"] > 0
        assert {c["name"] for c in record["collections"]} == {f"{ALIAS}__fake-concepts", f"{ALIAS}__fake-earlier"}
        assert (state / "qdrant" / "meta.json").is_file(), "the old folder is kept"
        assert not (state / "qdrant_edge.converting").exists()
        assert status["conversion"]["state"] == "converted" and status["conversion"]["old_folder_kept"] is True
        rag.close()
        again = make_rag()
        assert again.get_status()["vector_count"] == len(payloads)
        assert migration.read_record(state)["converted_at"] == record["converted_at"], "converted once only"

    def test_a_failed_conversion_leaves_the_old_store_working_and_says_why(self, make_rag, tmp_path,  # noqa: F811
                                                                           monkeypatch):
        from orchestrator.rag import edge_store, migration
        state = tmp_path / "state"
        state.mkdir()
        payloads = build_old_store(state, extra_model=False, legacy=False)
        before = sorted(p.name for p in (state / "qdrant").rglob("*"))

        def broken_upsert(self, points, flush=True):
            raise RuntimeError("disk I/O error while writing a segment")

        real_upsert = edge_store.EdgeKnowledgeStore.upsert
        monkeypatch.setattr(edge_store.EdgeKnowledgeStore, "upsert", broken_upsert)
        rag = make_rag()
        status = rag.get_status()
        assert status["store_format"] == "old-local" and status["vector_count"] == len(payloads)
        assert "disk I/O error" in status["old_format_reason"]
        assert rag.search("what does E2041 mean")[0].source == "https://docs.example/e2041"
        record = migration.read_record(state)
        assert record["state"] == "failed" and record["code"] == "error"
        assert record["retry"] == "llm-cli knowledge storage convert"
        assert not (state / "qdrant_edge").exists() and not (state / "qdrant_edge.converting").exists()
        assert sorted(p.name for p in (state / "qdrant").rglob("*")) == before
        rag.close()
        # Not tried again by itself (the same engine would fail the same way) ...
        monkeypatch.setattr(edge_store.EdgeKnowledgeStore, "upsert", real_upsert)
        again = make_rag()
        assert again.get_status()["store_format"] == "old-local"
        # ... until the person asks.
        result = again.convert_storage()
        assert result["used"] == "qdrant-edge" and result["state"] == "converted"
        assert again.get_status()["store_format"] == "qdrant-edge"
        assert again.get_status()["vector_count"] == len(payloads)

    def test_a_check_that_does_not_match_stops_the_conversion(self, make_rag, tmp_path, monkeypatch):  # noqa: F811
        """A copied point that differs from the old one (here: one payload changed on the way) is found by the check;
        nothing of the copy is kept and the old store stays in use."""
        from orchestrator.rag import edge_store, migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        real_point = edge_store.EdgeKnowledgeStore.point

        def tampered(point_id, dense, sparse, payload):
            if payload.get("source") == "https://docs.example/notes":
                payload = {**payload, "content": "changed on the way"}
            return real_point(point_id, dense, sparse, payload)

        monkeypatch.setattr(edge_store.EdgeKnowledgeStore, "point", staticmethod(tampered))
        monkeypatch.setattr(migration, "VERIFY_SAMPLES", 100)  # every point is compared
        rag = make_rag()
        assert rag.get_status()["store_format"] == "old-local"
        record = migration.read_record(state)
        assert record["state"] == "failed" and record["code"] == "verify_mismatch"
        assert "does not hold point" in record["reason"]
        assert not (state / "qdrant_edge").exists() and not (state / "qdrant_edge.converting").exists()

    def test_a_search_that_differs_stops_the_conversion(self, tmp_path, monkeypatch):
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        monkeypatch.setattr(migration, "same_ranking", lambda old, new: False)
        assert migration.run(state)[0] == "old"
        assert migration.read_record(state)["code"] == "verify_mismatch"

    def test_the_comparison_of_two_result_lists(self):
        from orchestrator.rag.migration import same_ranking
        assert same_ranking([("a", 0.9), ("b", 0.5)], [("a", 0.9), ("b", 0.5)])
        # a tie at the end: either point may take the last place
        assert same_ranking([("a", 0.9), ("b", 0.5), ("c", 0.5)], [("a", 0.9), ("c", 0.5), ("d", 0.5)])
        assert not same_ranking([("a", 0.9), ("b", 0.5)], [("x", 0.9), ("b", 0.5)])
        assert not same_ranking([("a", 0.9), ("b", 0.5)], [("a", 0.8), ("b", 0.5)])
        assert not same_ranking([("a", 0.9)], [("a", 0.9), ("b", 0.5)])

    def test_without_room_on_the_disk_nothing_is_copied_and_it_is_tried_again(self, tmp_path):
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        full = lambda path: SimpleNamespace(free=10 * 1024 * 1024, total=10 ** 12, used=0)  # noqa: E731
        use, reason = migration.run(state, disk_usage=full)
        assert use == "old" and "Not enough disk space" in reason
        record = migration.read_record(state)
        assert record["code"] == "disk_space" and record["retry"] == "on the next start"
        assert not (state / "qdrant_edge.converting").exists()
        assert migration.should_try(state) == (True, "")  # a failure of the moment is tried again
        assert migration.run(state)[0] == "edge"

    def test_a_process_tries_the_conversion_once_and_the_next_start_again(self, make_rag, tmp_path,  # noqa: F811
                                                                          monkeypatch):
        """The storage is released after 30 idle seconds and opened again: that reopening does not try the conversion
        again (and does not log it again); the next start does."""
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        tries = []
        real_run = migration.run

        def counted(*args, **kwargs):
            tries.append(1)
            return real_run(*args, disk_usage=lambda p: SimpleNamespace(free=1, total=10 ** 12, used=0))

        monkeypatch.setattr(migration, "run", counted)
        rag = make_rag()
        assert rag.get_status()["store_format"] == "old-local" and len(tries) == 1
        rag.close()  # what the idle time does
        assert rag.get_status()["store_format"] == "old-local" and len(tries) == 1
        assert "Not enough disk space" in rag.get_status()["old_format_reason"]
        rag.close()
        monkeypatch.setattr(migration, "run", real_run)
        assert make_rag().get_status()["store_format"] == "qdrant-edge"  # the next start: converted

    def test_an_old_store_another_program_holds_is_tried_again_later(self, tmp_path):
        from qdrant_client import QdrantClient

        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        holder = QdrantClient(path=str(state / "qdrant"))  # an older framework version still running
        try:
            assert migration.run(state)[0] == "old"
            assert migration.read_record(state)["code"] == "busy"
        finally:
            holder.close()
        assert migration.run(state)[0] == "edge"

    def test_a_new_engine_version_tries_a_failed_conversion_again(self, tmp_path, monkeypatch):
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        migration.write_record(state, {"state": "failed", "code": "verify_mismatch", "reason": "r",
                                       "engine": "qdrant-edge-py 0.0.1"})
        assert migration.should_try(state) == (True, "")
        migration.write_record(state, {"state": "failed", "code": "verify_mismatch", "reason": "r",
                                       "engine": migration.engine_version()})
        assert migration.should_try(state) == (False, "r")
        assert migration.should_try(state, force=True) == (True, "")

    def test_an_empty_edge_folder_takes_the_copy_and_a_failed_move_is_rolled_back(self, tmp_path, monkeypatch):
        """An Edge folder without any collection (made by hand, say) gets the checked collections moved in. When a
        move fails half way, what was moved goes back: never a half-converted knowledge base in use."""
        import os

        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, legacy=False)  # two collections
        (state / "qdrant_edge").mkdir()
        (state / "qdrant_edge" / "aliases.json").write_text("{}", encoding="utf-8")
        real_replace, moves = os.replace, []

        def flaky(src, dst):
            if Path(dst).parent == state / "qdrant_edge" / "collections":
                moves.append(dst)
                if len(moves) == 2:
                    raise PermissionError(13, "Access is denied", str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky)
        assert migration.run(state)[0] == "old"
        assert not migration.edge_in_use(state) and not (state / "qdrant_edge.converting").exists()
        assert migration.read_record(state)["code"] == "error"
        monkeypatch.setattr(os, "replace", real_replace)
        assert migration.run(state, force=True)[0] == "edge"
        assert len(migration.read_record(state)["collections"]) == 2

    def test_the_half_built_copy_of_an_interrupted_attempt_is_replaced(self, tmp_path):
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        payloads = build_old_store(state, extra_model=False, legacy=False)
        leftover = state / "qdrant_edge.converting" / "collections" / "half"
        leftover.mkdir(parents=True)
        (leftover / "segment.dat").write_bytes(b"x" * 100)
        assert migration.run(state)[0] == "edge"
        assert not (state / "qdrant_edge.converting").exists()
        assert migration.read_record(state)["points"] == len(payloads)

    def test_without_the_engine_the_older_local_storage_is_used(self, make_rag, tmp_path, monkeypatch):  # noqa: F811
        """No build of qdrant-edge-py for this computer (Windows on ARM): the knowledge base keeps qdrant-client's local
        mode, says so, and records no failed conversion (it is converted once the engine is there)."""
        import sys

        import qdrant_edge

        from orchestrator.Core.mojo.checks.knowledge import check_storage
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        monkeypatch.setitem(sys.modules, "qdrant_edge", None)  # import qdrant_edge raises ImportError
        rag = make_rag()
        status = rag.get_status()
        assert status["store_format"] == "old-local" and "not available on this computer" in status["old_format_reason"]
        assert rag.search("what does E2041 mean")[0].source == "https://docs.example/e2041"
        assert migration.read_record(state) == {} and not (state / "qdrant_edge").exists()
        (finding,) = check_storage(state)
        assert "not available on this computer" in finding.message
        rag.close()
        # The engine is there again (an extension module loads once per process: it is put back, not loaded again).
        monkeypatch.setitem(sys.modules, "qdrant_edge", qdrant_edge)
        status = make_rag().get_status()
        assert status.get("store_format") == "qdrant-edge", status  # converted once the engine is there

    def test_a_new_knowledge_base_without_the_engine_uses_the_older_local_storage(self, make_rag, tmp_path,  # noqa: F811
                                                                                  monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "qdrant_edge", None)
        rag = make_rag()
        ingest_all(rag)
        assert rag.get_status()["store_format"] == "old-local" and (tmp_path / "state" / "qdrant").is_dir()

    def test_an_empty_old_store_converts_to_an_empty_edge_storage(self, make_rag, tmp_path):  # noqa: F811
        from qdrant_client import QdrantClient

        from orchestrator.rag import migration
        state = tmp_path / "state"
        (state / "qdrant").mkdir(parents=True)
        QdrantClient(path=str(state / "qdrant")).close()
        rag = make_rag()
        assert rag.get_status()["store_format"] == "qdrant-edge"
        assert migration.read_record(state)["state"] == "converted"
        ingest_all(rag)
        assert rag.search("what does E2041 mean")[0].source == "https://docs.example/e2041"


# --- the old folder: only the person deletes it -----------------------------------------------------------------------

class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


def converted_state(tmp_path) -> Path:
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    assert migration.run(state)[0] == "edge"
    return state


class TestOldFolder:

    def test_it_is_listed_only_after_the_conversion(self, tmp_path):
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        assert migration.old_store_plan(state) is None, "before the conversion the old folder IS the knowledge base"
        migration.write_record(state, {"state": "failed", "code": "error", "reason": "r",
                                       "engine": migration.engine_version()})
        assert migration.old_store_plan(state) is None, "while the conversion failed it is still in use"
        migration.write_record(state, {})
        assert migration.run(state)[0] == "edge"
        plan = migration.old_store_plan(state)
        (item,) = plan.items
        assert item.kind == "knowledge_store" and item.full_path == str(state / "qdrant")
        assert item.file_name == "qdrant" and item.size_bytes > 0 and "converted to Qdrant Edge" in item.what
        assert str(state) not in item.speaking_name
        assert plan.mode_offered == ("trash", "permanent")
        ai = json.dumps(plan.ai_view())
        assert str(tmp_path) not in ai

    def test_nothing_is_deleted_without_the_persons_yes(self, tmp_path):
        from orchestrator.Core.deletion import AcceptedDeletion, accept
        from orchestrator.rag import migration
        state = converted_state(tmp_path)
        plan = migration.old_store_plan(state)
        with pytest.raises(PermissionError):
            migration.delete_old_store(state, plan, audit=Audit())  # a plain plan nobody accepted
        with pytest.raises(PermissionError):
            AcceptedDeletion(plan, "permanent", "mcp")  # an AI is never the person
        framework_yes = accept(plan, "framework", "permanent")  # the registry's window for an AI's request
        with pytest.raises(PermissionError):
            migration.delete_old_store(state, framework_yes, audit=Audit())
        with pytest.raises(RuntimeError, match="audit log"):
            migration.delete_old_store(state, accept(plan, "cli", "permanent"), audit=None)
        assert (state / "qdrant").is_dir()

    def test_a_list_that_changed_deletes_nothing(self, tmp_path):
        from orchestrator.Core.deletion import PlanChanged, accept
        from orchestrator.rag import migration
        state = converted_state(tmp_path)
        accepted = accept(migration.old_store_plan(state), "cli", "permanent")
        (state / "qdrant" / "added_later.bin").write_bytes(b"x" * 4096)
        with pytest.raises(PlanChanged):
            migration.delete_old_store(state, accepted, audit=Audit())
        assert (state / "qdrant").is_dir()

    def test_into_the_trash_and_restored(self, tmp_path):
        from orchestrator.Core.deletion import accept
        from orchestrator.Core.trash import Trash
        from orchestrator.rag import migration
        state = converted_state(tmp_path)
        audit, trash = Audit(), Trash(state)
        result = migration.delete_old_store(state, accept(migration.old_store_plan(state), "gui", "trash"),
                                            trash=trash, audit=audit)
        assert result.done and result.trash_id and not (state / "qdrant").exists()
        assert audit.entries[0]["outcome"] == "old_store_deleted"
        (entry,) = trash.entries()
        assert entry.kind == "knowledge_store" and entry.label == "old knowledge base storage"
        record = migration.read_record(state)
        assert record["old_folder_kept"] is False and record["old_folder_removed_how"] == "trash"
        assert migration.old_store_plan(state) is None
        trash.restore(entry.id, "gui")
        assert (state / "qdrant" / "meta.json").is_file()

    def test_for_good(self, tmp_path):
        from orchestrator.Core.deletion import accept
        from orchestrator.rag import migration
        state = converted_state(tmp_path)
        plan = migration.old_store_plan(state)
        result = migration.delete_old_store(state, accept(plan, "cli", "permanent"), audit=Audit())
        assert result.done and result.freed_bytes == plan.total_bytes and not (state / "qdrant").exists()
        assert migration.edge_in_use(state)

    def test_not_while_another_process_holds_the_knowledge_base(self, tmp_path):
        from orchestrator.Core.deletion import accept
        from orchestrator.rag import migration
        from orchestrator.rag.storage_lock import storage_lock, try_lock
        state = converted_state(tmp_path)
        holder = storage_lock(state / "qdrant_edge")
        assert try_lock(holder)
        try:
            with pytest.raises(RuntimeError, match="in use"):
                migration.delete_old_store(state, accept(migration.old_store_plan(state), "cli", "permanent"),
                                           audit=Audit(), lock_wait=0)
        finally:
            holder.release()
        assert (state / "qdrant").is_dir()


# --- what the person and an AI see ------------------------------------------------------------------------------------

class TestWhatIsShown:

    def test_mojo_reports_a_failed_conversion_and_the_kept_folder(self, tmp_path):
        from orchestrator.Core.mojo.checks.knowledge import check_storage
        from orchestrator.Core.mojo.findings import Severity
        from orchestrator.Core.mojo.views import ai_next
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        assert check_storage(state) == []
        migration.write_record(state, {"state": "failed", "code": "error", "reason": "a reason",
                                       "retry": "llm-cli knowledge storage convert",
                                       "engine": migration.engine_version()})
        (warn,) = check_storage(state)
        assert warn.severity == Severity.WARN and "a reason" in warn.message and warn.cannot == "person"
        assert "llm-cli knowledge storage convert" in warn.fix_hint
        migration.write_record(state, {})
        migration.run(state)
        (info,) = check_storage(state)
        assert info.severity == Severity.INFO and info.cannot == "deletion_list" and info.where == str(state / "qdrant")
        assert "delete-old" in ai_next("deletion_list", "knowledge.storage")

    def test_an_ai_sees_the_storage_format_and_no_folder(self, make_rag, tmp_path):  # noqa: F811
        from orchestrator.tools.tools_status import knowledge_section
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        rag = make_rag()
        rag.config.set("enable_rag_knowledge", True)
        framework = SimpleNamespace(config=rag.config, ensure_rag_manager=lambda: rag)
        section = knowledge_section(framework)
        assert section.store_format == "qdrant-edge" and section.vector_count == len(DOCS)
        assert str(tmp_path) not in section.model_dump_json()

    def test_the_texts_have_the_same_placeholders_in_both_languages(self):
        import string

        from orchestrator.utils.l10n_knowledge import TEXTS
        assert set(TEXTS["en"]) == set(TEXTS["de"])
        for key, text in TEXTS["en"].items():
            names = {f for _, f, _, _ in string.Formatter().parse(text) if f}
            assert names == {f for _, f, _, _ in string.Formatter().parse(TEXTS["de"][key]) if f}, key

    def test_the_guides_name_the_storage_buttons_as_the_gui_shows_them_in_their_language(self):
        """A guide names a button of Tools > Knowledge Snapshots... by the label the GUI shows in the guide's language:
        a German guide never sends the person to [Delete the old storage...] or [Try the conversion again], which a
        German window does not show (it shows [Alten Speicher löschen...] and [Umwandlung erneut versuchen]), and an
        English guide never names the German labels."""
        import re

        from orchestrator.utils.l10n_knowledge import TEXTS
        repo = Path(__file__).resolve().parents[1]
        buttons = ("storage.old.delete", "storage.convert")
        guides = sorted(repo.glob("*.md")) + sorted((repo / "docs").glob("*.md"))
        named_somewhere = set()
        for path in guides:
            lang = "de" if path.stem.endswith("_DE") else "en"
            other = "en" if lang == "de" else "de"
            wrong = tuple(TEXTS[other][k] for k in buttons if TEXTS[other][k] != TEXTS[lang][k])
            text = " ".join(path.read_text(encoding="utf-8").split())
            for named in re.findall(r"Knowledge Snapshots\.\.\. > (.{0,40})", text):
                assert not named.startswith(wrong), f"{path.name} names a button its language's GUI does not show: " \
                                                    f"{named}"
                named_somewhere.update(k for k in buttons if named.startswith(TEXTS[lang][k]))
        assert named_somewhere == set(buttons), "the guides name both storage buttons"

    def test_the_cli_shows_the_storage_and_deletes_only_after_the_persons_yes(self, tmp_path, monkeypatch):
        from test_doctor_fixes import run_cli
        state = converted_state(tmp_path)
        audit = Audit()
        fm = SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d),
                             tool_registry=SimpleNamespace(audit=audit, policy_store=None))
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage")
        assert "Qdrant Edge" in out and "Converted from the old storage" in out and "delete-old" in out
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage", "delete-old")
        assert "Nothing was deleted" in out and str(state / "qdrant") in out
        assert (state / "qdrant").is_dir() and audit.entries == []
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage", "delete-old", terminal=True, answer="trash\nn\n")
        assert (state / "qdrant").is_dir() and audit.entries == []  # "No" is the default answer
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage", "delete-old", terminal=True,
                         answer="permanent\ny\n")
        assert "Deleted: the old knowledge base storage" in out and not (state / "qdrant").exists()
        assert audit.entries[0]["outcome"] == "old_store_deleted"
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage", "delete-old")
        assert "no old storage folder to delete" in out


def test_the_snapshots_window_offers_the_old_folder_only_after_the_conversion(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.Core.trash import Trash
    from orchestrator.gui import knowledge_snapshots_window as window
    from test_deletion_dialog import answer_dialog, boxes
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    audit = Audit()
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d),
                                tool_registry=SimpleNamespace(audit=audit, policy_store=None))
    told = boxes(monkeypatch)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang="en")
    try:
        assert dialog.btn_old.isHidden() and dialog.storage.isHidden()
    finally:
        dialog.close()
    from orchestrator.rag import migration
    migration.run(state)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang="en")
    try:
        assert not dialog.btn_old.isHidden() and "converted from the storage of earlier versions" in \
            dialog.storage.text()
        shown = answer_dialog(monkeypatch)
        dialog._delete_old_store()
        assert str(state / "qdrant") in shown[0]
        assert not (state / "qdrant").exists() and Trash(state).entries()[0].kind == "knowledge_store"
        assert "Moved to the trash: the old knowledge base storage" in told[-1]
        assert dialog.btn_old.isHidden()
    finally:
        dialog.close()


# --- review of the switch (w4-edge-v): the storage really in use, and the person's way to try again -----------------

def failed_state(tmp_path, code="verify_mismatch") -> Path:
    """An old store whose conversion failed (with `code`): the old storage is still the knowledge base."""
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir(parents=True)
    build_old_store(state, extra_model=False, legacy=False)
    migration.write_record(state, {"state": "failed", "failed_at": "2026-10-16T10:00:00", "code": code,
                                   "reason": "a reason", "engine": migration.engine_version(),
                                   "retry": ("on the next start" if code in migration.TRANSIENT else
                                             "llm-cli knowledge storage convert")})
    return state


class TestTheStorageInUse:

    def test_a_converted_knowledge_base_is_never_swapped_for_the_stale_old_store(self, make_rag, tmp_path,  # noqa: F811
                                                                                 monkeypatch):
        """After the conversion the knowledge base lives in Qdrant Edge. When the engine cannot be loaded later (a
        missing or broken package), the old folder must not quietly become the knowledge base again: it lacks what was
        added since the conversion, and what would be written into it never reaches Qdrant Edge. The knowledge base is
        refused with the reason, the old folder stays as it was, and Mojo Oracle reports it as an error."""
        import sys

        from orchestrator.Core.mojo.checks.knowledge import check_storage
        from orchestrator.Core.mojo.findings import Severity
        from orchestrator.rag import migration
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        rag = make_rag()
        ingest_all(rag, {"https://docs.example/added": "# Added later\n\nThe error E7777 appears after an update."})
        assert rag.get_status()["store_format"] == "qdrant-edge"
        rag.close()
        old = state / "qdrant"

        def files():
            return sorted((str(p.relative_to(old)), p.stat().st_size, p.stat().st_mtime_ns)
                          for p in old.rglob("*") if p.is_file())

        before = files()
        monkeypatch.setitem(sys.modules, "qdrant_edge", None)  # import qdrant_edge raises ImportError
        again = make_rag()
        status = again.get_status()
        assert status.get("store_format") != "old-local", status
        assert "cannot be loaded" in status.get("error", "")
        assert again.ingest_document("https://docs.example/x", "# X\n\nText that must go nowhere.", {}) is False
        with pytest.raises(KnowledgeBaseError, match="cannot be loaded"):
            migration.current(state)
        assert files() == before, "nothing was opened or written in the old folder"
        (finding,) = check_storage(state)
        assert finding.severity == Severity.ERROR and finding.cannot == "person"
        assert "cannot be loaded" in finding.message

    def test_the_cli_names_the_storage_that_is_really_in_use(self, tmp_path, monkeypatch):
        """`llm-cli knowledge storage` while the conversion failed: the old storage is the knowledge base, so the
        first line must not say Qdrant Edge; and the retry sentence names what really happens."""
        from test_doctor_fixes import run_cli
        state = failed_state(tmp_path)
        fm = SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d))
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage")
        assert "Storage: Qdrant Edge" not in out
        assert "Storage: the storage of earlier versions" in out and str(state / "qdrant") in out
        assert "It is tried again llm-cli" not in out
        assert "not tried again by itself" in out and "Try the conversion again" in out
        state2 = failed_state(tmp_path / "b", code="disk_space")
        fm2 = SimpleNamespace(config=SimpleNamespace(user_state_dir=state2, get=lambda k, d=None: d))
        _, out = run_cli(fm2, monkeypatch, "knowledge", "storage")
        assert "tried again on the next start" in out

    def test_the_cli_names_the_old_storage_without_the_engine(self, tmp_path, monkeypatch):
        import sys

        from test_doctor_fixes import run_cli
        state = tmp_path / "state"
        state.mkdir()
        build_old_store(state, extra_model=False, legacy=False)
        monkeypatch.setitem(sys.modules, "qdrant_edge", None)
        fm = SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d))
        _, out = run_cli(fm, monkeypatch, "knowledge", "storage")
        assert "Storage: Qdrant Edge" not in out and "not available on this computer" in out

    def test_an_old_store_that_cannot_be_read_is_a_recorded_failure_not_a_crash(self, tmp_path):
        """run() never raises for a failed conversion: an old store qdrant-client cannot open (here a broken
        meta.json) is recorded with its reason, so the person sees why (status, Mojo Oracle), and it is not tried
        again on every start."""
        from orchestrator.Core.mojo.checks.knowledge import check_storage
        from orchestrator.rag import migration
        state = tmp_path / "state"
        (state / "qdrant").mkdir(parents=True)
        (state / "qdrant" / "meta.json").write_text("{not json", encoding="utf-8")
        use, reason = migration.run(state)
        assert use == "old" and "cannot be read" in reason
        record = migration.read_record(state)
        assert record["state"] == "failed" and record["code"] == "unreadable"
        assert migration.should_try(state)[0] is False
        assert not (state / "qdrant_edge").exists() and not (state / "qdrant_edge.converting").exists()
        assert (state / "qdrant" / "meta.json").read_text(encoding="utf-8") == "{not json"
        (warn,) = check_storage(state)
        assert "cannot be read" in warn.message

    def test_mojo_names_the_ways_to_try_again_in_plain_words(self, tmp_path):
        from orchestrator.Core.mojo.checks.knowledge import check_storage
        (warn,) = check_storage(failed_state(tmp_path))
        assert "tried again llm-cli" not in warn.fix_hint
        assert "Try the conversion again" in warn.fix_hint and "llm-cli knowledge storage convert" in warn.fix_hint


def test_the_snapshots_window_tries_a_failed_conversion_again(qapp, tmp_path, monkeypatch):
    """W1': every action the person can start in the CLI can be started in the GUI by mouse. A failed conversion is
    tried again with [Try the conversion again]; it runs off the GUI thread and says what happened. The German window
    never shows the English retry text of the record."""
    pytest.importorskip("PySide6")
    import threading
    import time as _time

    from orchestrator.gui import knowledge_snapshots_window as window
    from test_deletion_dialog import boxes
    state = failed_state(tmp_path)
    calls = []

    class Rag:
        def convert_storage(self):
            from orchestrator.rag import migration
            calls.append(threading.current_thread().name)
            use = migration.run(state, force=True)[0]
            return {**migration.summary(state), "used": "qdrant-edge" if use == "edge" else "old-local", "error": ""}

    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d),
                                tool_registry=SimpleNamespace(audit=Audit(), policy_store=None),
                                ensure_rag_manager=lambda: Rag())
    told = boxes(monkeypatch)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang="de")
    try:
        assert not dialog.btn_convert.isHidden() and dialog.btn_convert.isEnabled()
        text = dialog.storage.text()
        assert "on the next start" not in text and "llm-cli knowledge storage convert" in text
        assert "Umwandlung erneut versuchen" in text
        dialog.btn_convert.click()
        deadline = _time.monotonic() + 60
        while not told and _time.monotonic() < deadline:
            qapp.processEvents()
            _time.sleep(0.02)
        assert calls and calls[0] != threading.current_thread().name, "the conversion runs off the GUI thread"
        assert told and "Qdrant Edge" in told[-1]
        assert dialog.btn_convert.isHidden() and "umgewandelt" in dialog.storage.text()
    finally:
        dialog.close()
