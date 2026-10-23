"""Knowledge always belongs to a topic or a target (DECISIONS round 23 items 1, 2 and 8).

- A loose knowledge file (a .json file in community/knowledge/ or a topic folder, a knowledge.json without the pack's
  provenance) is never imported: it is listed, and the person sorts it into a topic or target folder through the
  checks of an installed knowledge pack (quarantine, validator, licence files), then it is imported.
- Knowledge packs name their topic or target; a pack made before that still verifies and installs, with the topic
  taken from its id. Installed packs live in community/knowledge/<topic or target>/<pack>/.
- A newer version of a pack replaces the excerpts of the older one, per pack and page source (no stale chunks).
- The knowledge snapshot of a target pack is imported at the recipient under that target, with the same checks and
  the same snapshot first.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from orchestrator.Core import community_pack as cp
from orchestrator.Core.community_manager import (ORIGIN_FIELDS, TARGET_MARKERS, TARGET_ORIGIN, TrustLevel,
                                                 derived_topic, read_provenance)
from orchestrator.Core.community_pack import PackError, PackManifest, build_manifest
from test_rag import make_rag  # noqa: F401 - the fixture
from test_zoo import World, make_target, pack_bytes

PAGES = [{"source": "https://docs.example/npu", "content": "Run the model on the NPU with rknn.", "metadata": {}},
         {"source": "https://docs.example/e2041", "content": "E2041 means the NPU memory ran out.", "metadata": {}}]


class RecordingRag:
    """A knowledge base that records what reaches it, with the snapshot taken first."""

    def __init__(self):
        self.ingested, self.snapshots, self.removed = [], [], []

    def create_snapshot(self, name, before="the change"):
        assert not self.ingested or self.snapshots, "the snapshot comes before the first page"
        self.snapshots.append(name)
        return name

    def ingest_document(self, source, content, meta):
        self.ingested.append((source, content, dict(meta)))
        return True

    def remove_pack_leftovers(self, origin, digest):
        self.removed.append((origin, digest))
        return True


@pytest.fixture
def world(tmp_path):
    world = World(tmp_path)
    world.rag = RecordingRag()
    world.framework.get_component = lambda name: world.rag if name == "rag_manager" else None
    return world


def loose(world, name, pages=PAGES):
    path = world.cm.knowledge_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pages) if not isinstance(pages, str) else pages, encoding="utf-8")
    return path


def knowledge_pack(world, name="rk-docs", pages=PAGES, version="1.0.0", **scope):
    root = world.tmp / "src" / f"{name}-{version}"
    root.mkdir(parents=True)
    (root / "knowledge.json").write_text(json.dumps(pages), encoding="utf-8")
    return pack_bytes(root, kind="knowledge", pack_id=name, version=version, **scope)


# --- the manifest: topic or target, and older packs still verify ---------------------------------------------------

class TestManifest:

    def test_a_pack_made_before_topics_still_verifies_and_keeps_its_bytes(self, tmp_path):
        """The signature of an older pack covers a manifest without topic/target: it still verifies, and the manifest
        written for it holds no new field."""
        key = cp.Ed25519PrivateKey.generate()
        (tmp_path / "knowledge.json").write_text(json.dumps(PAGES))
        manifest = build_manifest(tmp_path, kind="knowledge", id="old-docs", version="1.0.0", author="a",
                                  license="MIT")
        old_dump = {k: v for k, v in manifest.model_dump(mode="json").items() if k not in ("topic", "target")}
        old_bytes = json.dumps(old_dump, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert manifest.canonical_bytes() == old_bytes, "the bytes an older signature covers stay the same"
        signature = cp.sign_manifest(manifest, key)
        reread = PackManifest.model_validate_json(json.dumps(old_dump))
        cp.verify_manifest(reread, signature, [key.public_key()])   # raises when it does not verify
        data = cp.write_pack(tmp_path, manifest, signature)
        written = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("pack.json"))
        assert "topic" not in written and "target" not in written

    def test_a_new_pack_signs_its_topic(self, tmp_path):
        key = cp.Ed25519PrivateKey.generate()
        (tmp_path / "knowledge.json").write_text(json.dumps(PAGES))
        manifest = build_manifest(tmp_path, kind="knowledge", id="npu", version="1.0.0", author="a", license="MIT",
                                  target="Rockchip")
        signature = cp.sign_manifest(manifest, key)
        assert manifest.as_json()["target"] == "Rockchip" and "topic" not in manifest.as_json()
        changed = manifest.model_copy(update={"target": "Other"})
        with pytest.raises(PackError):
            cp.verify_manifest(changed, signature, [key.public_key()])

    @pytest.mark.parametrize("fields, problem", [
        ({"kind": "target", "topic": "x"}, "only a knowledge pack"),
        ({"kind": "knowledge", "topic": "x", "target": "y"}, "not both"),
        ({"kind": "knowledge", "topic": "trailing."}, "not a topic"),
        ({"kind": "knowledge", "topic": "CON"}, "not a topic"),
        ({"kind": "knowledge", "topic": "a/b"}, "not a topic"),
    ])
    def test_topic_rules(self, fields, problem):
        base = {"id": "p", "version": "1.0.0", "author": "a", "license": "MIT", "created": "2026-10-23T00:00:00Z"}
        with pytest.raises(ValueError, match=problem):
            PackManifest(**base, **fields)

    def test_the_tool_layer_uses_the_same_topic_rule(self):
        from orchestrator.tools import tools_community
        assert tools_community.TOPIC_PATTERN == cp.TOPIC_PATTERN

    def test_the_topic_of_an_older_pack_is_its_id(self):
        assert derived_topic("rk3588-docs") == "rk3588-docs" and derived_topic("x.") == "x"


# --- loose files: listed, never imported unsorted, sorted through the pack checks -----------------------------------

class TestLooseFiles:

    def test_loose_files_are_listed_and_never_imported(self, world):
        loose(world, "top.json")
        loose(world, "rockchip/dropped.json")
        loose(world, "rockchip/no-provenance/knowledge.json")
        loose(world, "folder/knowledge.json")
        (world.cm.knowledge_dir / "notes.txt").write_text("not knowledge")
        assert [item.label for item in world.cm.loose_knowledge()] == [
            "folder/knowledge.json", "rockchip/dropped.json", "rockchip/no-provenance/knowledge.json", "top.json"]
        assert world.cm.sync_knowledge_base() == 0 and world.rag.ingested == [] and world.rag.snapshots == []
        assert world.cm.installed() == []

    def test_sorting_checks_moves_and_imports_under_the_topic(self, world):
        path = loose(world, "board notes.json",
                     PAGES[:1] + [{**PAGES[1], "metadata": {"topic": "spoofed", "target": "Other",
                                                             "origin_pack": "someone", "origin_sha256": "x"}}])
        prepared = world.cm.prepare_sort("board notes.json", topic="rk-npu")
        summary = prepared.summary()
        assert "Topic: rk-npu" in summary and "Moves the file board notes.json to" in summary
        assert "a loose file of the knowledge folder" in summary and prepared.trust == TrustLevel.LOCAL
        assert path.is_file(), "nothing moves before the person's Yes"
        dest = world.cm.finish_install(prepared)
        assert dest == world.cm.knowledge_dir / "rk-npu" / "board-notes"
        assert not path.exists() and world.cm.loose_knowledge() == []
        prov = read_provenance(dest)
        assert (prov.trust, prov.topic, prov.target, prov.source) == ("local", "rk-npu", None,
                                                                      "loose file board notes.json")
        assert len(world.rag.snapshots) == 1 and [s for s, _, _ in world.rag.ingested] == [p["source"] for p in PAGES]
        for _, _, meta in world.rag.ingested:   # the import sets these, never a page
            assert (meta["origin_pack"], meta["topic"], meta["origin_sha256"]) == ("board-notes", "rk-npu",
                                                                                  prov.content_sha256)
            assert "target" not in meta
        assert world.rag.removed == [("board-notes", prov.content_sha256)]
        assert [folder for folder, _ in world.cm.installed()] == [dest]

    def test_a_target_must_exist_and_names_the_folder(self, world):
        make_target(world.root / "targets" / "Rockchip")
        loose(world, "rk.json")
        with pytest.raises(PackError, match="no target 'Nope'"):
            world.cm.prepare_sort("rk.json", target="Nope")
        with pytest.raises(PackError, match="topic or a target"):
            world.cm.prepare_sort("rk.json")
        with pytest.raises(PackError, match="topic or a target"):
            world.cm.prepare_sort("rk.json", topic="a", target="Rockchip")
        dest = world.cm.finish_install(world.cm.prepare_sort("rk.json", target="rockchip"))
        assert dest == world.cm.knowledge_dir / "Rockchip" / "rk" and read_provenance(dest).target == "Rockchip"
        assert all(meta["target"] == "Rockchip" and "topic" not in meta for _, _, meta in world.rag.ingested)

    @pytest.mark.parametrize("content, problem", [
        ('{"swarm_encrypted": true}', "old 'swarm' format"),
        ("[{\"content\": \"no source\"}]", "each with its source"),
        ("not json", "not valid JSON"),
    ])
    def test_a_file_that_fails_the_checks_stays_loose(self, world, content, problem):
        path = loose(world, "bad.json", content)
        with pytest.raises(PackError, match=problem):
            world.cm.prepare_sort("bad.json", topic="t")
        quarantine = world.cm.knowledge_dir / ".quarantine"
        assert path.read_text() == content and not (quarantine.is_dir() and list(quarantine.iterdir()))
        assert world.rag.ingested == []

    def test_only_a_listed_loose_file_can_be_sorted(self, world, tmp_path):
        outside = tmp_path / "elsewhere.json"
        outside.write_text(json.dumps(PAGES))
        for name in (str(outside), "../elsewhere.json", "missing.json"):
            with pytest.raises(PackError, match="no loose knowledge file"):
                world.cm.prepare_sort(name, topic="t")

    def test_discard_keeps_the_loose_file_and_a_changed_file_is_not_removed(self, world):
        path = loose(world, "keep.json")
        prepared = world.cm.prepare_sort("keep.json", topic="t")
        world.cm.discard(prepared)
        assert path.is_file() and not prepared.quarantine.exists()
        prepared = world.cm.prepare_sort("keep.json", topic="t")
        path.write_text(json.dumps(PAGES[:1]))   # changed after the check: the checked copy is installed
        world.cm.finish_install(prepared)
        assert path.is_file(), "a file changed after it was checked stays where it is"

    def test_a_sorted_name_is_unique_across_topics(self, world):
        loose(world, "docs.json")
        loose(world, "b/docs.json")
        first = world.cm.finish_install(world.cm.prepare_sort("docs.json", topic="one"))
        second = world.cm.finish_install(world.cm.prepare_sort("b/docs.json", topic="two"))
        assert (first.name, second.name) == ("docs", "docs-2")


# --- installed packs: in their topic folder, never imported once changed -------------------------------------------

class TestInstalledPacks:

    def test_a_pack_with_a_topic_installs_into_its_folder(self, world):
        world.add(knowledge_pack(world, topic="llama.cpp"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
        world.publish()
        prepared = world.cm.prepare_install("knowledge", "rk-docs")
        assert prepared.topic == "llama.cpp" and not prepared.topic_from_id and "Topic: llama.cpp" in prepared.summary()
        dest = world.cm.finish_install(prepared)
        assert dest == world.cm.knowledge_dir / "llama.cpp" / "rk-docs" and read_provenance(dest).topic == "llama.cpp"
        assert all(meta["topic"] == "llama.cpp" for _, _, meta in world.rag.ingested)

    def test_an_older_pack_takes_its_topic_from_its_id_with_the_persons_yes(self, world):
        world.add(knowledge_pack(world), kind="knowledge", pack_id="rk-docs", version="1.0.0")
        world.publish()
        prepared = world.cm.prepare_install("knowledge", "rk-docs")
        assert prepared.topic == "rk-docs" and prepared.topic_from_id
        assert "taken from the pack id" in prepared.summary()
        dest = world.cm.finish_install(prepared)
        assert dest == world.cm.knowledge_dir / "rk-docs" / "rk-docs" and read_provenance(dest).topic == "rk-docs"

    def test_a_pack_installed_before_topic_folders_is_still_read(self, world):
        world.add(knowledge_pack(world, topic="t"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
        world.publish()
        dest = world.cm.finish_install(world.cm.prepare_install("knowledge", "rk-docs"))
        legacy = world.cm.knowledge_dir / "rk-docs"
        dest.rename(legacy)   # where earlier versions installed it
        (world.cm.knowledge_dir / "t").rmdir()
        (world.cm.knowledge_dir / "rk-docs.imported").unlink()
        world.rag.ingested.clear()
        assert [folder for folder, _ in world.cm.installed()] == [legacy]
        assert world.cm.sync_knowledge_base() == 2

    def test_a_changed_pack_is_not_imported_and_says_why(self, world):
        world.add(knowledge_pack(world, topic="t"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
        world.publish()
        world.rag = None   # installed while the knowledge base is off
        dest = world.cm.finish_install(world.cm.prepare_install("knowledge", "rk-docs"))
        (dest / "knowledge.json").write_text(json.dumps(PAGES + [{"source": "s", "content": "planted"}]))
        rag = RecordingRag()
        assert world.cm.sync_knowledge_base(rag) == 0 and rag.ingested == [] and rag.snapshots == []
        assert "changed since it was installed" in world.cm.last_sync_problem

    def test_a_folder_dropped_in_without_provenance_is_loose_not_installed(self, world):
        loose(world, "planted/knowledge.json")
        assert world.cm.sync_knowledge_base() == 0 and world.rag.ingested == []
        assert [item.label for item in world.cm.loose_knowledge()] == ["planted/knowledge.json"]


# --- the target pack's knowledge snapshot reaches the recipient's knowledge base ------------------------------------

def target_with_knowledge(world, name="demo-board", pages=PAGES, version="1.2.0"):
    root = make_target(world.tmp / "src" / f"{name}-{version}")
    (root / "knowledge").mkdir()
    (root / "knowledge" / "initial_knowledge.json").write_text(json.dumps(pages), encoding="utf-8")
    return pack_bytes(root, pack_id=name, version=version)


class TestTargetKnowledge:

    def test_the_snapshot_is_imported_under_the_target_after_a_snapshot(self, world):
        world.add(target_with_knowledge(world))
        world.publish()
        prepared = world.cm.prepare_install("target", "demo-board")
        assert prepared.knowledge_pages == 2 and prepared.imports_knowledge
        assert any("knowledge snapshot (2 page(s)) is imported" in n for n in prepared.notes)
        dest = world.cm.finish_install(prepared)
        assert len(world.rag.snapshots) == 1 and len(world.rag.ingested) == 2
        digest = read_provenance(dest).content_sha256
        for _, _, meta in world.rag.ingested:
            assert (meta["origin_pack"], meta["target"], meta["origin_sha256"]) == (
                f"{TARGET_ORIGIN}demo-board", "demo-board", digest)
        assert (world.cm.knowledge_dir / TARGET_MARKERS / "demo-board.imported").is_file()
        assert world.cm.sync_knowledge_base() == 0, "imported once"

    def test_a_target_pack_without_a_snapshot_imports_nothing(self, world):
        world.add(world.target_pack())
        world.publish()
        prepared = world.cm.prepare_install("target", "demo-board")
        assert not prepared.imports_knowledge
        world.cm.finish_install(prepared)
        assert world.rag.snapshots == [] and world.rag.ingested == []

    def test_a_broken_snapshot_is_refused_like_a_knowledge_pack(self, world):
        world.add(target_with_knowledge(world, pages=[{"content": "no source"}]))
        world.publish()
        with pytest.raises(PackError, match="initial_knowledge.json: expected a list"):
            world.cm.prepare_install("target", "demo-board")

    def test_own_and_shipped_targets_are_not_imported(self, world):
        own = make_target(world.root / "targets" / "mine")
        (own / "knowledge").mkdir()
        (own / "knowledge" / "initial_knowledge.json").write_text(json.dumps(PAGES))
        assert world.cm.sync_knowledge_base() == 0 and world.rag.ingested == []


# --- a newer version replaces the old one's excerpts (real knowledge base) ------------------------------------------

def test_a_newer_pack_version_replaces_the_old_excerpts(make_rag, tmp_path):  # noqa: F811
    rag = make_rag()
    world = World(tmp_path)
    world.framework.get_component = lambda name: rag if name == "rag_manager" else None
    own = "https://docs.example/own"
    assert rag.ingest_document(own, "My own notes about the NPU.", {"title": "own"}, replace=True)
    v1 = PAGES + [{"source": "https://docs.example/gone", "content": "This page is dropped in v2.", "metadata": {}}]
    world.add(knowledge_pack(world, pages=v1, topic="npu"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    world.add(knowledge_pack(world, "other", pages=[{"source": "https://docs.example/other", "content": "Other pack.",
                                                     "metadata": {}}], topic="npu"),
              kind="knowledge", pack_id="other", version="1.0.0")
    world.publish()
    first = world.cm.finish_install(world.cm.prepare_install("knowledge", "rk-docs"))
    world.cm.finish_install(world.cm.prepare_install("knowledge", "other"))
    sources = {s["source"]: s for s in rag.list_sources()}
    assert set(sources) == {own, "https://docs.example/other", *(p["source"] for p in v1)}
    assert sources["https://docs.example/npu"]["topic"] == "npu"

    # version 2: one page changed, one page gone (the person removed the old folder to install it, as the hub says)
    import shutil
    shutil.rmtree(first)
    v2 = [{**PAGES[0], "content": "Run the model on the NPU with rknn-toolkit2 now."}, PAGES[1]]
    world.add(knowledge_pack(world, pages=v2, version="2.0.0", topic="npu"), kind="knowledge", pack_id="rk-docs",
              version="2.0.0")
    world.publish(version=2)
    world.cm.finish_install(world.cm.prepare_install("knowledge", "rk-docs"))
    sources = {s["source"]: s for s in rag.list_sources()}
    assert "https://docs.example/gone" not in sources, "no stale excerpt of the old version"
    assert {own, "https://docs.example/other"} <= set(sources), "your own pages and other packs stay"
    chunks, _ = rag.read_source("https://docs.example/npu", 0, 20)
    assert [c["content"] for c in chunks] == ["Run the model on the NPU with rknn-toolkit2 now."]
    assert world.cm.last_sync_problem == ""


# --- exports name their topic or target; nothing writes loose files -------------------------------------------------

class TestExport:

    def test_a_knowledge_export_names_its_topic_or_target(self, world):
        from orchestrator.Core.community_pack import UserKeys
        UserKeys(world.secrets).signing_key()
        world.rag.export_records = lambda: iter([{"source": "https://docs.example/a", "content": "text",
                                                  "metadata": {"licence": "CC0-1.0"}}])
        with pytest.raises(PackError, match="topic or a target"):
            world.cm.export_pack("knowledge", author="a")
        with pytest.raises(PackError, match="no target"):
            world.cm.export_pack("knowledge", author="a", target="Missing")
        with pytest.raises(PackError, match="Only a knowledge pack"):
            world.cm.export_pack("target", "x", author="a", topic="t")
        make_target(world.root / "targets" / "Rockchip")
        result = world.cm.export_pack("knowledge", author="a", target="rockchip")
        assert result.manifest.target == "Rockchip" and result.manifest.topic is None
        assert any("belong to the target Rockchip" in n for n in result.notes)
        assert cp.read_manifest(result.path.read_bytes()).target == "Rockchip"

    def test_the_knowledge_export_never_writes_into_the_knowledge_folder(self, world):
        world.rag.export_records = lambda: iter([{"source": "https://docs.example/a", "content": "text",
                                                  "metadata": {}}])
        path = Path(world.cm.export_knowledge_base("knowledge_board"))
        assert world.cm.knowledge_dir not in path.parents and path.is_file()
        assert world.cm.loose_knowledge() == []

    def test_the_module_generator_leaves_no_loose_file(self, world):
        from orchestrator.Core.module_generator import KNOWLEDGE_SNAPSHOT, ModuleGenerator
        world.rag.export_records = lambda: iter([{"source": "https://b.invalid/a", "content": "board doc",
                                                  "metadata": {"root_url": "https://b.invalid"}}])
        generator = ModuleGenerator(Path(__file__).resolve().parents[1] / "targets")
        generator.targets_dir = world.root / "targets"
        target = world.root / "targets" / "kb-board"
        target.mkdir()
        generator._create_knowledge_snapshot(target, world.framework, "kb_board", ["https://b.invalid"])
        assert (target / KNOWLEDGE_SNAPSHOT).is_file()
        assert world.cm.loose_knowledge() == [] and not world.cm.knowledge_dir.exists()


def test_the_trusted_excerpt_fields_are_named_once():
    assert set(ORIGIN_FIELDS) == {"origin_pack", "origin_sha256", "topic", "target"}


def test_a_knowledge_pack_in_a_fake_framework_without_rag_installs(tmp_path):
    """Installing while the knowledge base is off: the pack waits, nothing fails."""
    world = World(tmp_path)
    world.framework.get_component = lambda name: None
    world.add(knowledge_pack(world, topic="t"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    world.publish()
    dest = world.cm.finish_install(world.cm.prepare_install("knowledge", "rk-docs"))
    assert dest.is_dir() and world.cm.last_sync_problem == ""
