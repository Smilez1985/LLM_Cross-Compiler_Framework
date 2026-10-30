"""Review of the uninstall and the holders: the cases the first tests left open.

- An excerpt stored before holders (3.1.0) names only the pack that wrote it last: uninstalling that pack (or a newer
  version of it that dropped the page) must not take away the page another installed pack still carries; that pack's
  copy of the page is imported again from its own pages.
- An uninstalled pack restored from the trash comes back with its import notes: its pages are imported again at the
  next knowledge import, since its excerpts left the knowledge base with the uninstall.

Real knowledge base (Qdrant Edge, the fake embedders of test_rag); documentation addresses only."""

import shutil

from orchestrator.Core.deletion import accept
from orchestrator.Core.trash import Trash
from test_knowledge_holders import ONLY_A, SHARED, knowledge_pack, points
from test_pack_uninstall import two_packs  # noqa: F401 - the fixture
from test_rag import make_rag  # noqa: F401 - the fixture
from test_trash import Audit


def _strip_holders(rag):
    """The knowledge base as 3.1.0 left it: no holders, each point names only the pack that wrote it last."""
    def strip(store):
        from orchestrator.rag.store import DENSE, sparse_of
        records = list(store.records(with_vectors=True))
        store.upsert([store.point(r.id, list(r.vector[DENSE]), sparse_of(r.vector),
                                  {k: v for k, v in r.payload.items() if k not in ("holders", "holder_info")})
                      for r in records])
    rag._with_store(strip)


def _last_writer(rag):
    (writer,) = {p.payload["origin_pack"] for p in points(rag, SHARED["source"])}
    return writer, ("pack-a" if writer == "pack-b" else "pack-b")


def test_uninstalling_the_last_writer_of_an_old_shared_page_keeps_it_for_the_other_pack(two_packs):  # noqa: F811
    cm, rag = two_packs.cm, two_packs.rag_real
    _strip_holders(rag)
    writer, other = _last_writer(rag)
    plan = cm.uninstall_plan("knowledge", writer)
    result = cm.uninstall("knowledge", writer, accept(plan, "cli", "permanent"), audit=Audit())
    assert not result.failed, result.failed
    kept = points(rag, SHARED["source"])
    assert kept, f"{other} still carries the shared page"
    assert all(p.payload["holders"] == [other] and p.payload["origin_pack"] == other for p in kept)


def test_a_newer_version_that_drops_an_old_shared_page_keeps_it_for_the_other_pack(two_packs):  # noqa: F811
    world, cm, rag = two_packs, two_packs.cm, two_packs.rag_real
    _strip_holders(rag)
    writer, other = _last_writer(rag)
    folder = next(f for f, prov in cm.installed() if prov.id == writer)
    shutil.rmtree(folder)
    topic = "boards" if writer == "pack-b" else "npu"   # its own topic (two_packs)
    world.add(knowledge_pack(world, writer, [ONLY_A], version="2.0.0", topic=topic), kind="knowledge",
              pack_id=writer, version="2.0.0")
    world.publish(version=2)
    world.cm.finish_install(world.cm.prepare_install("knowledge", writer))
    assert cm.last_sync_problem == ""
    kept = points(rag, SHARED["source"])
    assert kept and all(other in p.payload["holders"] for p in kept), f"{other} still carries the shared page"


def test_a_pack_restored_from_the_trash_is_imported_again(two_packs, tmp_path):  # noqa: F811
    cm, rag = two_packs.cm, two_packs.rag_real
    trash = Trash(tmp_path / "state", audit=Audit())
    plan = cm.uninstall_plan("knowledge", "pack-a")
    result = cm.uninstall("knowledge", "pack-a", accept(plan, "cli", "trash"), trash=trash, audit=Audit())
    assert not result.failed and points(rag, ONLY_A["source"]) == []
    for entry_id in result.trash_ids:   # the person undoes the uninstall: the folder and its import notes come back
        trash.restore(entry_id, "cli")
    assert any(prov.id == "pack-a" for _, prov in cm.installed())
    cm.sync_knowledge_base(rag)
    assert cm.last_sync_problem == ""
    assert points(rag, ONLY_A["source"]), "its pages are in the knowledge base again"
    assert set(points(rag, SHARED["source"])[0].payload["holders"]) == {"pack-a", "pack-b"}
    assert not list(cm.knowledge_dir.glob("*.released")), "the note goes once the pack is imported again"
    cm.sync_knowledge_base(rag)
    assert cm.last_sync_snapshot is None, "imported once: the next start imports nothing again"
