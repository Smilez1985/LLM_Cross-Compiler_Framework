"""Uninstalling an installed pack (knowledge, target, recipe): only through Core/deletion.py with the exact list (the
folder with its provenance, its import notes, and the excerpts only it holds), "No" as the default, files to the trash
or for good; excerpts another pack still holds stay. Only the person decides (an AI's request needs the person)."""

import json
import shutil

import pytest

from orchestrator.Core.community_pack import PackError
from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept
from orchestrator.Core.trash import Trash
from test_knowledge_holders import SHARED, ONLY_A, knowledge_pack, points, real_world
from test_rag import make_rag  # noqa: F401 - the fixture
from test_trash import Audit


@pytest.fixture
def two_packs(make_rag, tmp_path):  # noqa: F811
    rag = make_rag()
    world = real_world(tmp_path, rag)
    world.add(knowledge_pack(world, "pack-a", [SHARED, ONLY_A]), kind="knowledge", pack_id="pack-a", version="1.0.0")
    world.add(knowledge_pack(world, "pack-b", [SHARED], topic="boards"), kind="knowledge", pack_id="pack-b",
              version="1.0.0")
    world.publish()
    world.cm.finish_install(world.cm.prepare_install("knowledge", "pack-a"))
    world.cm.finish_install(world.cm.prepare_install("knowledge", "pack-b"))
    world.rag_real = rag
    return world


def test_the_plan_lists_the_folder_its_notes_and_what_leaves_the_knowledge_base(two_packs):
    plan = two_packs.cm.uninstall_plan("knowledge", "pack-a")
    folder, *notes = plan.items
    assert folder.file_name == "pack-a" and folder.full_path.endswith("pack-a") and "1 page(s) that only it holds" \
        in folder.what and "also holds stay" in folder.what
    assert {n.file_name for n in notes} == {"pack-a.imported", "pack-a.snapshotted"}
    assert plan.mode_offered == ("trash", "permanent") and plan.total_bytes == sum(i.size_bytes for i in plan.items)
    assert all("/" not in item["label"] for item in plan.ai_view()), "an AI sees labels, never paths"


def test_uninstall_moves_it_to_the_trash_and_keeps_what_another_pack_holds(two_packs, tmp_path):
    cm, rag = two_packs.cm, two_packs.rag_real
    plan = cm.uninstall_plan("knowledge", "pack-a")
    audit = Audit()
    result = cm.uninstall("knowledge", "pack-a", accept(plan, "cli", "trash"), trash=Trash(tmp_path / "state",
                                                                                            audit=audit),
                          audit=audit)
    assert not result.failed and len(result.trash_ids) == len(plan.items)
    assert not any(prov.id == "pack-a" for _, prov in cm.installed())
    assert points(rag, ONLY_A["source"]) == [], "what only pack-a held went with it"
    shared = points(rag, SHARED["source"])
    assert shared and all(p.payload["holders"] == ["pack-b"] for p in shared), "pack-b still holds the shared page"
    assert [e["outcome"] for e in audit.entries][:1] == ["pack_uninstalled"]
    assert not (cm.knowledge_dir / "pack-a.imported").exists()


def test_uninstall_for_good_frees_the_space(two_packs):
    cm = two_packs.cm
    plan = cm.uninstall_plan("knowledge", "pack-b")
    result = cm.uninstall("knowledge", "pack-b", accept(plan, "gui", "permanent"), audit=Audit())
    assert result.freed_bytes == plan.total_bytes and not result.failed


def test_a_changed_list_deletes_nothing(two_packs):
    cm = two_packs.cm
    plan = cm.uninstall_plan("knowledge", "pack-a")
    (cm.knowledge_dir / "pack-a.snapshotted").unlink()   # the list changed while the person read it
    with pytest.raises(PlanChanged):
        cm.uninstall("knowledge", "pack-a", accept(plan, "cli", "permanent"), audit=Audit())
    assert any(prov.id == "pack-a" for _, prov in cm.installed())


def test_only_the_person_uninstalls(two_packs):
    plan = two_packs.cm.uninstall_plan("knowledge", "pack-a")
    with pytest.raises(PermissionError):
        AcceptedDeletion(plan, "trash", "mcp")
    with pytest.raises(PermissionError):
        two_packs.cm.uninstall("knowledge", "pack-a", plan, audit=Audit())


def test_a_recipe_and_an_unknown_pack(make_rag, tmp_path):  # noqa: F811
    from test_zoo import pack_bytes
    from test_recipes import recipe_data
    world = real_world(tmp_path, make_rag())
    root = tmp_path / "r"
    root.mkdir()
    data = recipe_data()
    data.update(id="demo-recipe", target={"id": "demo-board", "pack_sha256": "a" * 64}, format="gguf",
                board="demo", quantization="Q4_K_M")
    (root / "recipe.yml").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "r.zip").write_bytes(pack_bytes(root, kind="recipe", pack_id="demo-recipe", version="1.0.0"))
    world.publish()
    folder = world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "r.zip"))
    plan = world.cm.uninstall_plan("recipe", "demo-recipe")
    assert [i.full_path for i in plan.items] == [str(folder)]
    world.cm.uninstall("recipe", "demo-recipe", accept(plan, "cli", "permanent"), audit=Audit())
    assert not folder.exists()
    with pytest.raises(PackError, match="no installed recipe pack"):
        world.cm.uninstall_plan("recipe", "demo-recipe")


def test_cli_zoo_uninstall_asks_with_no_as_the_default(two_packs, monkeypatch):
    from orchestrator import cli as cli_module
    from test_zoo import invoke
    world = two_packs
    audit = Audit()
    monkeypatch.setattr(cli_module, "_cli_audit", lambda ctx: audit)
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    result = invoke(world, "uninstall", "pack-a", "--kind", "knowledge", input="permanent\n\n")
    assert result.exit_code == 1 and "Nothing was deleted" in result.output, result.output
    assert any(prov.id == "pack-a" for _, prov in world.cm.installed()), "the default is No"
    result = invoke(world, "uninstall", "pack-a", "--kind", "knowledge", input="permanent\ny\n")
    assert result.exit_code == 0, result.output
    assert "Uninstalled knowledge pack-a" in " ".join(result.output.split())
    assert not any(prov.id == "pack-a" for _, prov in world.cm.installed())
    assert invoke(world, "uninstall", "nothing", "--kind", "recipe").exit_code == 1


def test_an_ai_only_asks_and_the_person_decides_in_the_framework_window(two_packs):
    """remove_pack is optional (hidden until the person enables it), capped at ask, never granted or delegated; the
    person's window shows the exact list; an AI's request goes to the trash only and the answer holds no host path."""
    pytest.importorskip("mcp")
    from test_optional_tools import ask, enable
    from orchestrator.tools.delegation import NEVER_DELEGATE
    from orchestrator.tools.grants import NEVER_GRANT
    from orchestrator.tools.policy import Decision
    env = _tool_env(two_packs)
    spec = env.registry.get("remove_pack")
    assert spec.destructive and "remove_pack" in NEVER_GRANT and "remove_pack" in NEVER_DELEGATE
    assert "remove_pack" not in {s.name for s in env.registry.visible("mcp")}, "hidden until enabled"
    enable(env, "remove_pack")
    assert env.registry.decide(spec, caller="ditto") == Decision.ASK
    out, shown = ask(env, "remove_pack", {"kind": "knowledge", "pack_id": "pack-a"})
    assert out.uninstalled and out.trash_ids and "pack-a" in shown[0]
    assert str(two_packs.tmp) not in out.model_dump_json(), "no host path in what an AI reads"
    assert not any(prov.id == "pack-a" for _, prov in two_packs.cm.installed())


def _tool_env(world):
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    from orchestrator.tools.audit import AuditLog
    from orchestrator.tools.framework_tools import register_framework_tools
    from orchestrator.tools.policy import PolicyStore, ToolPolicy
    from orchestrator.tools.registry import ToolRegistry
    from test_build_tools import Cfg, Orchestrator, Service
    from orchestrator.Core.target_manager import TargetManager
    tmp = world.tmp / "tools"
    tmp.mkdir()
    framework = SimpleNamespace(config=Cfg(tmp), resolve_path=lambda p: Path(p), orchestrator=Orchestrator())
    framework.run_coroutine = lambda coro, timeout=None: asyncio.run(coro)
    framework.target_manager = TargetManager(framework)
    framework.build_service = Service(framework)
    framework.community_manager = world.cm
    registry = ToolRegistry(PolicyStore(tmp / "policy"), AuditLog(tmp / "audit.db"))
    framework.tool_registry = registry
    register_framework_tools(registry, framework)
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return SimpleNamespace(framework=framework, registry=registry, tmp=tmp)


def test_a_knowledge_pack_is_not_uninstalled_while_the_knowledge_base_is_off(two_packs):
    two_packs.framework.get_component = lambda name: None
    with pytest.raises(PackError, match="knowledge base is off"):
        two_packs.cm.uninstall_plan("knowledge", "pack-a")
    shutil.rmtree(two_packs.tmp / "src", ignore_errors=True)


def test_cli_zoo_uninstall_keeps_its_not_removed_line_and_names_what_stayed(two_packs, monkeypatch, tmp_path):
    """An item the uninstall kept is said as before ("Not removed: NAME (REASON)"), and every path that stayed follows
    for the person (Core/deletion.kept_lines' deletion.remaining line)."""
    from orchestrator import cli as cli_module
    from orchestrator.Core.deletion import DeletionResult, note_kept
    from test_zoo import invoke
    world = two_packs
    monkeypatch.setattr(cli_module, "_cli_audit", lambda ctx: Audit())
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    folder = tmp_path / "pack-a-folder"
    stayed = folder / "open.bin"

    def uninstall(kind, pack_id, accepted, trash=None, audit=None):
        result = DeletionResult()
        note_kept(result, "the pack folder", folder, [stayed])
        return result

    monkeypatch.setattr(world.cm, "uninstall", uninstall)
    result = invoke(world, "uninstall", "pack-a", "--kind", "knowledge", input="permanent\ny\n")
    out = " ".join(result.output.split())
    assert result.exit_code == 1, result.output
    assert "Not removed: the pack folder (kept: could not remove 1 item (open.bin))" in out, result.output
    assert "Kept: the pack folder" not in out
    assert "Still there (the pack folder), delete it by hand:" in out and "open.bin" in out
