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
