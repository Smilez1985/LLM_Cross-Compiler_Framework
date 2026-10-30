"""Knowledge base follow-ups: shared excerpts of several packs (holders), old excerpts without a topic, the recipe
binding of a target pack on Windows, a re-crawl of a site, topics of own crawls, and uninstalling a pack.

Every address and name here is a documentation one (example.com, example.org, *.test, *.invalid)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import community_pack as cp
from orchestrator.Core.community_manager import installed_digest, read_provenance, same_content
from orchestrator.Core.community_pack import build_manifest
from test_knowledge_order import RecordingRag
from test_zoo import World, make_target, pack_bytes


@pytest.fixture
def world(tmp_path):
    """test_knowledge_order's world: an installation, a signed index, and a knowledge base that records."""
    world = World(tmp_path)
    world.rag = RecordingRag()
    world.framework.get_component = lambda name: world.rag if name == "rag_manager" else None
    return world


def windows_view(monkeypatch):
    """os.stat as Windows reports a pack's files: no execute bit (a 0o755 script reads as 0o644)."""
    real = cp._scan

    def view(folder):
        return [(rel, path, SimpleNamespace(st_mode=st.st_mode & ~0o111, st_size=st.st_size))
                for rel, path, st in real(folder)]
    monkeypatch.setattr(cp, "_scan", view)


def linux_target(world, name="demo-board"):
    """A target pack made on Linux by the module wizard (its script 0o755), and its content digest there."""
    root = make_target(world.tmp / "src" / name)
    (root / "modules" / "build.sh").chmod(0o755)
    digest = build_manifest(root, kind="target", id=name, version="1.2.0", author="alice",
                            license="MIT").content_sha256()
    return pack_bytes(root, pack_id=name, version="1.2.0"), digest


def recipe_file(tmp_path: Path, pack_sha256: str, name="demo-recipe") -> Path:
    from test_recipes import recipe_data
    data = recipe_data()
    data.update(id=name, target={"id": "demo-board", "pack_sha256": pack_sha256}, format="gguf", board="demo",
                quantization="Q4_K_M")
    root = tmp_path / name
    root.mkdir()
    (root / "recipe.yml").write_text(json.dumps(data), encoding="utf-8")
    path = tmp_path / f"{name}.zip"
    path.write_bytes(pack_bytes(root, kind="recipe", pack_id=name, version="1.0.0"))
    return path


# --- (3) a recipe bound to a target pack made on Linux verifies on Windows ----------------------------------------

class TestRecipeBindingOnWindows:

    def test_the_recipe_check_finds_the_same_target(self, world, monkeypatch, tmp_path):
        data, digest = linux_target(world)
        world.add(data)
        world.publish()
        windows_view(monkeypatch)
        dest = world.cm.finish_install(world.cm.prepare_install("target", "demo-board"))
        assert cp.tree_sha256(dest) != digest, "the simulated file system dropped the execute bit"
        prepared = world.cm.prepare_install_file(recipe_file(tmp_path, digest))
        assert not any("differs from the one the recipe was built with" in n for n in prepared.notes), prepared.notes
        assert same_content(dest, digest) and installed_digest(dest) == digest

    def test_the_builder_records_the_packs_own_digest(self, world, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        data, digest = linux_target(world)
        world.add(data)
        world.publish()
        windows_view(monkeypatch)
        dest = world.cm.finish_install(world.cm.prepare_install("target", "demo-board"))
        engine = SimpleNamespace(config=SimpleNamespace(user_state_dir=world.tmp / "state"))
        config = SimpleNamespace()
        BuildEngine._record_target(engine, config, SimpleNamespace(add_warning=lambda w: None), dest)
        assert config.target_pack_sha256 == digest, "a recipe made on Windows names the target as Linux does"

    def test_an_edited_target_is_still_another_target(self, world, monkeypatch, tmp_path):
        data, digest = linux_target(world)
        world.add(data)
        world.publish()
        windows_view(monkeypatch)
        dest = world.cm.finish_install(world.cm.prepare_install("target", "demo-board"))
        (dest / "modules" / "build.sh").write_text("#!/bin/bash\necho changed\n", encoding="utf-8")
        assert not same_content(dest, digest) and installed_digest(dest) == cp.tree_sha256(dest)
        prepared = world.cm.prepare_install_file(recipe_file(tmp_path, digest))
        assert any("differs from the one the recipe was built with" in n for n in prepared.notes)

    def test_an_own_target_keeps_its_folder_digest(self, world):
        own = make_target(world.root / "targets" / "mine")
        assert read_provenance(own) is None
        assert installed_digest(own) == cp.tree_sha256(own) and same_content(own, cp.tree_sha256(own))
        assert not same_content(own, "") and not same_content(own, None)


# --- (4) a topic folder taken by an older pack: name it and the way out --------------------------------------------

PAGES = [{"source": "https://docs.example.com/npu", "content": "Run the model on the NPU.", "metadata": {}}]


def knowledge_pack(world, name, version="1.0.0", **scope):
    root = world.tmp / "src" / f"{name}-{version}"
    root.mkdir(parents=True)
    (root / "knowledge.json").write_text(json.dumps(PAGES), encoding="utf-8")
    return pack_bytes(root, kind="knowledge", pack_id=name, version=version, **scope)


def legacy_pack(world, name="npu"):
    """A knowledge pack installed before topic folders: community/knowledge/<pack>/."""
    world.add(knowledge_pack(world, name, topic="t"), kind="knowledge", pack_id=name, version="1.0.0")
    world.publish()
    dest = world.cm.finish_install(world.cm.prepare_install("knowledge", name))
    legacy = world.cm.knowledge_dir / name
    dest.rename(legacy)
    (world.cm.knowledge_dir / "t").rmdir()
    return legacy


def test_an_index_pack_whose_topic_is_an_older_packs_folder_names_it_and_the_way_out(world):
    legacy_pack(world)
    world.add(knowledge_pack(world, "npu-guide", topic="npu"), kind="knowledge", pack_id="npu-guide",
              version="1.0.0")
    world.publish(version=2)
    with pytest.raises(cp.PackError) as info:
        world.cm.prepare_install("knowledge", "npu-guide")
    text = str(info.value)
    assert "knowledge pack npu 1.0.0" in text and "Uninstall that pack, or install it again" in text
    assert "Choose another topic name" not in text and "choose another" not in text, "the person cannot choose it"
    assert "pack.remove" in [getattr(s, "key", s) for s in getattr(info.value, "steps", ())] or info.value.steps


def test_sorting_a_loose_file_may_also_choose_another_topic(world):
    legacy_pack(world)
    path = world.cm.knowledge_dir / "notes.json"
    path.write_text(json.dumps(PAGES), encoding="utf-8")
    with pytest.raises(cp.PackError, match="or choose another topic name"):
        world.cm.prepare_sort("notes.json", topic="npu")


# --- (5) a linked knowledge folder is ignored, and said so ---------------------------------------------------------

@pytest.mark.skipif(not hasattr(__import__("os"), "symlink") or __import__("os").name == "nt",
                    reason="symbolic links need privileges on Windows")
def test_a_linked_knowledge_folder_is_ignored_and_the_listing_and_mojo_say_why(world, tmp_path):
    from orchestrator.Core.mojo.checks.community import check_community
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "npu" / "pack").mkdir(parents=True)
    world.cm.knowledge_dir.parent.mkdir(parents=True, exist_ok=True)
    world.cm.knowledge_dir.symlink_to(elsewhere, target_is_directory=True)
    assert world.cm.ignored_links() == ["knowledge"]
    world.publish()
    _, note = world.cm.listing()
    assert "community/knowledge is a link: ignored" in note and "never follows a link" in note
    framework = SimpleNamespace(community_manager=world.cm)
    (finding,) = [f for f in check_community(framework) if f.code == "community.knowledge.links"]
    assert "never follows a link" in finding.message
    world.cm.knowledge_dir.unlink()
    world.cm.knowledge_dir.mkdir()
    (world.cm.knowledge_dir / "topic").mkdir()
    (world.cm.knowledge_dir / "topic" / "linked").symlink_to(elsewhere / "npu", target_is_directory=True)
    assert world.cm.ignored_links() == ["knowledge/topic/linked"]
    assert "1 link(s) in community/knowledge" in world.cm.links_note()


# --- (10) an edited target pack: why it is not imported and how to go on ---------------------------------------------

def test_an_edited_target_pack_says_why_and_how(world):
    from orchestrator.Core.mojo.checks.community import check_community
    root = make_target(world.tmp / "src" / "demo-board")
    (root / "knowledge").mkdir()
    (root / "knowledge" / "initial_knowledge.json").write_text(json.dumps(PAGES), encoding="utf-8")
    world.add(pack_bytes(root, pack_id="demo-board", version="1.2.0"))
    world.publish()
    dest = world.cm.finish_install(world.cm.prepare_install("target", "demo-board"))
    (dest / "modules" / "build.sh").write_text("#!/bin/bash\necho edited\n", encoding="utf-8")
    (world.cm.knowledge_dir / ".targets" / "demo-board.imported").unlink()
    world.cm.sync_knowledge_base()
    framework = SimpleNamespace(community_manager=world.cm)
    (finding,) = [f for f in check_community(framework) if f.code == "community.demo-board.changed"]
    for text in (world.cm.last_sync_problem, finding.fix_hint):
        assert "not imported" in text and "install it again" in text and "export that as a new pack" in text
    assert "security gate" in finding.why and "llm-cli" not in finding.fix_hint
    assert [p for f, _, _, p in world.cm.verify_installed() if f == dest] == [["changed since it was installed"]]
