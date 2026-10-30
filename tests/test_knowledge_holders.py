"""Two installed packs (or a pack and your own crawl) that hold the same page share its excerpts: a point's id is its
source and chunk hash, never the pack. Every excerpt names its holders; a newer pack version, a re-crawl or an
uninstall releases only its own hold, and an excerpt goes only when nobody holds it any more.

Real knowledge base (Qdrant Edge, the fake embedders of test_rag); documentation addresses only."""

import json
import shutil

from orchestrator.Core.rag_manager import holder_info, holder_of
from test_edge_store import backend  # noqa: F401 - the fixture
from test_rag import make_rag  # noqa: F401 - the fixture
from test_zoo import World, pack_bytes

SHARED = {"source": "https://docs.example.com/shared", "content": "Both packs carry this page about the NPU.",
          "metadata": {}}
ONLY_A = {"source": "https://docs.example.com/a", "content": "Only pack a has this page.", "metadata": {}}
ROOT = "https://docs.example.com/"


def knowledge_pack(world, name, pages, version="1.0.0", topic="npu"):
    root = world.tmp / "src" / f"{name}-{version}"
    root.mkdir(parents=True)
    (root / "knowledge.json").write_text(json.dumps(pages), encoding="utf-8")
    return pack_bytes(root, kind="knowledge", pack_id=name, version=version, topic=topic)


def real_world(tmp_path, rag):
    world = World(tmp_path)
    world.framework.get_component = lambda name: rag if name == "rag_manager" else None
    return world


def points(rag, source):
    return rag._with_store(lambda store: list(store.source_records(source)))


def test_a_newer_version_that_drops_a_shared_page_keeps_it_for_the_other_pack(make_rag, tmp_path):  # noqa: F811
    rag = make_rag()
    world = real_world(tmp_path, rag)
    world.add(knowledge_pack(world, "pack-a", [SHARED, ONLY_A]), kind="knowledge", pack_id="pack-a", version="1.0.0")
    world.add(knowledge_pack(world, "pack-b", [SHARED], topic="boards"), kind="knowledge", pack_id="pack-b",
              version="1.0.0")
    world.publish()
    world.cm.finish_install(world.cm.prepare_install("knowledge", "pack-b"))
    # pack-a comes last: before holders, the last import owned the shared points
    first = world.cm.finish_install(world.cm.prepare_install("knowledge", "pack-a"))
    shared = points(rag, SHARED["source"])
    assert shared and all(set(p.payload["holders"]) == {"pack-a", "pack-b"} for p in shared)
    # version 2 of pack-a drops the shared page
    shutil.rmtree(first)
    world.add(knowledge_pack(world, "pack-a", [ONLY_A], version="2.0.0"), kind="knowledge", pack_id="pack-a",
              version="2.0.0")
    world.publish(version=2)
    world.cm.finish_install(world.cm.prepare_install("knowledge", "pack-a"))
    assert world.cm.last_sync_problem == ""
    assert SHARED["source"] in {s["source"] for s in rag.list_sources()}, "pack-b still holds the page"
    shared = points(rag, SHARED["source"])
    assert shared and all(p.payload["holders"] == ["pack-b"] and p.payload["origin_pack"] == "pack-b"
                          and p.payload["topic"] == "boards" for p in shared)
    assert points(rag, ONLY_A["source"]), "pack-a's own page of version 2 is in"


def test_a_pack_and_your_own_crawl_of_the_same_page_keep_each_others_excerpts(make_rag):  # noqa: F811
    rag = make_rag()
    text = "The same page, crawled by you and shipped in a pack."
    source = "https://docs.example.com/both"
    assert rag.ingest_document(source, text, {"root_url": ROOT}, replace=True)
    assert rag.ingest_document(source, text, {"origin_pack": "pack-a", "origin_sha256": "a" * 64, "topic": "npu"})
    assert all(set(p.payload["holders"]) == {"pack-a", f"crawl:{ROOT}"} for p in points(rag, source))
    # the pack's newer version no longer has the page: your crawl keeps it, and it is yours again
    assert rag.remove_pack_leftovers("pack-a", "b" * 64)
    kept = points(rag, source)
    assert kept and all(p.payload["holders"] == [f"crawl:{ROOT}"] and not p.payload.get("origin_pack")
                        for p in kept)
    # the pack again, then your re-crawl of the page with new text: only your own old excerpt is replaced
    assert rag.ingest_document(source, text, {"origin_pack": "pack-a", "origin_sha256": "b" * 64})
    assert rag.ingest_document(source, "New text of the page.", {"root_url": ROOT}, replace=True)
    contents = sorted(p.payload["content"] for p in points(rag, source))
    assert contents == ["New text of the page.", text], "the pack's excerpt stays, your old one is replaced"
    (old,) = [p for p in points(rag, source) if p.payload["content"] == text]
    assert old.payload["holders"] == ["pack-a"]


def test_an_excerpt_stored_before_holders_is_released_by_its_pack(make_rag):  # noqa: F811
    """Excerpts of earlier versions name only origin_pack; the release finds them by it."""
    rag = make_rag()
    assert rag.ingest_document("https://docs.example.com/old", "An excerpt of an earlier version.",
                               {"origin_pack": "pack-a", "origin_sha256": "a" * 64})

    def strip(store):
        from orchestrator.rag.store import DENSE, sparse_of
        records = list(store.records(with_vectors=True))
        store.upsert([store.point(r.id, list(r.vector[DENSE]), sparse_of(r.vector),
                                  {k: v for k, v in r.payload.items() if k not in ("holders", "holder_info")})
                      for r in records])
    rag._with_store(strip)
    assert "holders" not in points(rag, "https://docs.example.com/old")[0].payload
    assert rag.remove_pack_leftovers("pack-a", "b" * 64)
    assert points(rag, "https://docs.example.com/old") == []


def test_holders_never_come_from_a_pages_metadata(make_rag):  # noqa: F811
    rag = make_rag()
    assert rag.ingest_document("https://docs.example.com/x", "A page claiming holders.",
                               {"origin_pack": "pack-a", "origin_sha256": "a" * 64,
                                "holders": ["pack-z"], "holder_info": {"pack-z": {}}})
    (record,) = points(rag, "https://docs.example.com/x")
    assert record.payload["holders"] == ["pack-a"]
    assert all("holders" not in r["metadata"] and "holder_info" not in r["metadata"] for r in rag.export_records())


def test_holder_rules():
    assert holder_of({"origin_pack": "pack-a", "root_url": ROOT}) == "pack-a"
    assert holder_of({"root_url": ROOT}) == f"crawl:{ROOT}" and holder_of({}) == "own"
    assert holder_info({"origin_pack": "p", "origin_sha256": "x", "topic": "t"}) == {
        "p": {"origin_pack": "p", "origin_sha256": "x", "topic": "t"}}


def test_a_shared_excerpt_belongs_to_every_holders_topic():
    from orchestrator.Core.knowledge_sources import belongs_to
    meta = {"topic": "boards", "holder_info": {"pack-a": {"topic": "npu"}, "pack-b": {"topic": "boards"}}}
    assert belongs_to(meta, "npu") and belongs_to(meta, "Boards") and not belongs_to(meta, "other")


def test_both_backends_find_list_members_retrieve_and_delete_by_id(backend):  # noqa: F811
    """The store calls the holders rely on: a match on a list field, retrieve and delete by id (Qdrant Edge and the
    qdrant-client storage)."""
    import uuid
    from orchestrator.rag.embeddings import SparseVec
    store = backend.make()
    store.ensure()
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"h{i}")) for i in range(3)]
    store.upsert([store.point(pid, [1.0, 0.1 * i, 0.0, 0.0], SparseVec([100 + i], [1.0]),
                              {"source": "s", "holders": holders, "content": f"t{i}"})
                  for i, (pid, holders) in enumerate(zip(ids, (["a"], ["a", "b"], ["b"])))])
    assert sorted(r.payload["content"] for r in store.records_where("holders", "a")) == ["t0", "t1"]
    assert [r.payload["content"] for r in store.retrieve([ids[2]], with_vectors=False)] == ["t2"]
    assert all(r.vector for r in store.records_where("holders", "b", with_vectors=True))
    store.delete_ids([ids[0]])
    assert sorted(r.payload["content"] for r in store.records()) == ["t1", "t2"]
