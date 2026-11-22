"""Deleting a module of your own (orchestrator/Core/module_delete.py; docs/DESIGN.md, Targets, hardware and
templates): the module's folder and the file of its private values in one plan, only for a module of the own targets
folder, only the person's accepted plan, audited first, to the trash or for good, never while a build uses it.

Every private value is made at run time from random characters, so this file holds no secret.
"""

import json
import random
import string
from pathlib import Path

import pytest

from orchestrator.Core import modules_env
from orchestrator.Core.community_manager import SHIPPED_TARGETS_DIR
from orchestrator.Core.community_pack import PROVENANCE_NAME
from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept
from orchestrator.Core.module_delete import ModuleDeleteError, delete_module, module_folder, module_plan
from orchestrator.Core.trash import Trash
from test_trash import Audit

ALNUM = string.ascii_letters + string.digits


def secret(seed, n=16):
    return "".join(random.Random(seed).choices(ALNUM, k=n))


class World:
    def __init__(self, tmp_path):
        self.own = tmp_path / "own_targets"
        self.state = tmp_path / "state"
        self.own.mkdir()
        self.state.mkdir()
        self.roots = [SHIPPED_TARGETS_DIR, self.own]
        self.values = {"LOCAL_SDK_DIR": "/mnt/nas/" + secret(1), "LOCAL_PASSWORD_1": secret(2)}

    def module(self, name="my-board", values=True, provenance=False):
        folder = self.own / name
        (folder / "modules").mkdir(parents=True)
        (folder / "target.yml").write_text(f"metadata:\n  id: {name}\n  name: {name}\n", encoding="utf-8")
        (folder / "modules" / "config_module.sh").write_text("echo hi\n", encoding="utf-8")
        if provenance:
            (folder / PROVENANCE_NAME).write_text("{}", encoding="utf-8")
        if values:
            modules_env.write(self.state, name, self.values)
        return folder

    def plan(self, name="my-board", busy=()):
        return module_plan(self.roots, self.own, name, self.state, busy)

    def delete(self, accepted, name="my-board", busy=(), trash="default", audit="default"):
        audit = Audit() if audit == "default" else audit
        trash = Trash(self.state, audit=audit or Audit()) if trash == "default" else trash
        return delete_module(self.roots, self.own, name, accepted, self.state, busy, trash=trash, audit=audit), audit


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# --- the plan ------------------------------------------------------------------------------------------------

def test_the_plan_lists_the_folder_and_the_values_file_counted_never_shown(world):
    folder = world.module()
    plan = world.plan()
    module, values = plan.items
    assert module.full_path == str(folder) and module.file_name == "my-board"
    assert values.full_path == str(modules_env.path_for(world.state, "my-board"))
    assert "2 value(s)" in values.what
    text = json.dumps([i.model_dump() for i in plan.items]) + json.dumps(plan.ai_view())
    for name, value in world.values.items():
        assert value not in text and name not in text, "a value or its name is never in the list"
    assert plan.mode_offered == ("trash", "permanent") and plan.total_bytes == sum(i.size_bytes for i in plan.items)


def test_a_module_without_values_lists_its_folder_only(world):
    world.module(values=False)
    assert [i.file_name for i in world.plan().items] == ["my-board"]


def test_the_module_is_found_by_its_folder_name_in_any_letter_case(world):
    world.module()
    assert world.plan("MY-BOARD").items[0].file_name == "my-board"


# --- what is refused -----------------------------------------------------------------------------------------

def test_a_target_the_framework_ships_is_refused(world):
    with pytest.raises(ModuleDeleteError, match="framework ships"):
        world.plan("Rockchip")
    shipped_is_own = World.__new__(World)
    shipped_is_own.__dict__.update(world.__dict__)
    with pytest.raises(ModuleDeleteError, match="framework ships"):   # a developer checkout: own folder is the shipped one
        module_folder([SHIPPED_TARGETS_DIR], SHIPPED_TARGETS_DIR, "Rockchip")


def test_a_module_installed_from_a_pack_is_refused_with_the_way_to_remove_it(world):
    world.module(provenance=True)
    with pytest.raises(ModuleDeleteError, match="uninstalling the pack"):
        world.plan()


def test_an_unknown_name_a_path_and_an_empty_name_are_refused(world):
    world.module()
    for bad in ("nothing", "", "../own_targets/my-board", "my-board/modules"):
        with pytest.raises(ModuleDeleteError):
            world.plan(bad)


def test_a_module_outside_the_own_targets_folder_is_refused(world, tmp_path):
    other = tmp_path / "other_targets"
    (other / "elsewhere").mkdir(parents=True)
    (other / "elsewhere" / "target.yml").write_text("metadata:\n  id: elsewhere\n", encoding="utf-8")
    with pytest.raises(ModuleDeleteError, match="not in your own targets folder"):
        module_plan([other, world.own], world.own, "elsewhere", world.state)


def test_a_module_that_a_running_build_uses_is_refused(world):
    world.module()
    for busy in ({"my-board"}, {"MY-BOARD"}):
        with pytest.raises(ModuleDeleteError, match="build is using"):
            world.plan(busy=busy)
    assert world.plan(busy={"another"}).items


# --- the deletion --------------------------------------------------------------------------------------------

def test_to_the_trash_moves_the_folder_and_the_values_and_audits_first(world):
    folder = world.module()
    values_file = modules_env.path_for(world.state, "my-board")
    audit = Audit()
    result, _ = world.delete(accept(world.plan(), "cli", "trash"), audit=audit)
    assert not result.failed and len(result.done) == 2 and len(result.trash_ids) == 2
    assert not folder.exists() and not values_file.exists()
    assert [e["outcome"] for e in audit.entries][0] == "module_deleted"
    assert "my-board" in json.dumps(audit.entries[0]["arguments"])
    for value in world.values.values():
        assert value not in json.dumps(audit.entries, default=str), "the audit holds no value"


def test_the_trash_brings_the_module_and_its_values_back(world):
    folder = world.module()
    values_file = modules_env.path_for(world.state, "my-board")
    audit = Audit()
    trash = Trash(world.state, audit=audit)
    result, _ = world.delete(accept(world.plan(), "gui", "trash"), trash=trash, audit=audit)
    assert not folder.exists() and not values_file.exists() and len(trash.entries()) == 2
    for entry_id in result.trash_ids:
        trash.restore(entry_id, "gui")
    assert (folder / "target.yml").is_file() and modules_env.read(world.state, "my-board") == world.values


def test_for_good_frees_the_space_and_removes_both(world):
    folder = world.module()
    values_file = modules_env.path_for(world.state, "my-board")
    plan = world.plan()
    result, _ = world.delete(accept(plan, "gui", "permanent"))
    assert not result.failed and result.freed_bytes == plan.total_bytes and not result.trash_ids
    assert not folder.exists() and not values_file.exists()


def test_only_the_person_can_accept(world):
    folder = world.module()
    plan = world.plan()
    for actor in ("mcp", "ditto", "grant:g_0123456789abcdef", "delegation:d_0123456789abcdef", ""):
        with pytest.raises(PermissionError):
            accept(plan, actor)
    with pytest.raises(PermissionError):
        AcceptedDeletion(plan, "trash", "framework", "elicitation")
    with pytest.raises(PermissionError):
        world.delete(plan)   # a plan nobody accepted
    with pytest.raises(PermissionError):   # the tool registry's own window is not the way for a module
        world.delete(AcceptedDeletion(plan, "trash", "framework"))
    assert folder.is_dir()


def test_a_changed_list_stops_it(world):
    folder = world.module()
    accepted = accept(world.plan(), "cli", "trash")
    modules_env.set_value(world.state, "my-board", "LOCAL_NEW", "x")   # the values file changed (its size)
    (folder / "modules" / "more.sh").write_text("echo more\n", encoding="utf-8")
    with pytest.raises(PlanChanged):
        world.delete(accepted)
    assert folder.is_dir() and modules_env.read(world.state, "my-board")


def test_a_module_that_became_busy_stops_it(world):
    folder = world.module()
    accepted = accept(world.plan(), "cli", "trash")
    with pytest.raises(PlanChanged):
        world.delete(accepted, busy={"my-board"})
    assert folder.is_dir()


def test_nothing_is_deleted_without_the_audit_or_the_trash(world):
    folder = world.module()
    with pytest.raises(RuntimeError, match="audit log is not available"):
        world.delete(accept(world.plan(), "cli", "trash"), audit=None)

    class Failing(Audit):
        def record(self, *args, **kwargs):
            raise OSError("/secret/path")

    with pytest.raises(RuntimeError) as failure:
        world.delete(accept(world.plan(), "cli", "trash"), audit=Failing())
    assert "/secret/path" not in str(failure.value)
    with pytest.raises(RuntimeError, match="trash is not available"):
        world.delete(accept(world.plan(), "cli", "trash"), trash=None)
    assert folder.is_dir() and modules_env.read(world.state, "my-board")


def test_a_second_module_and_its_values_stay(world):
    world.module("my-board")
    other = world.module("other-board")
    result, _ = world.delete(accept(world.plan(), "cli", "permanent"))
    assert not result.failed
    assert other.is_dir() and modules_env.read(world.state, "other-board") == world.values


def test_the_values_stay_when_the_folder_cannot_go(world, monkeypatch):
    folder = world.module()
    from orchestrator.Core import module_delete
    monkeypatch.setattr(module_delete, "remove_tree", lambda path: [folder] if path == folder else [])
    result, _ = world.delete(accept(world.plan(), "cli", "permanent"))
    assert [name for name, _ in result.failed] == ["module my-board", "private values of module my-board"]
    assert folder.is_dir() and modules_env.read(world.state, "my-board") == world.values


# --- the command `module delete` ------------------------------------------------------------------------------

def _cli(monkeypatch, tmp_path, *args):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    home = tmp_path / "home"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(home))
    monkeypatch.setattr(cli_module, "_target_folders", lambda ctx: (tmp_path / "own_targets",
                                                                    [SHIPPED_TARGETS_DIR, tmp_path / "own_targets"]))
    monkeypatch.setattr(cli_module, "_local_state", lambda: home)
    return CliRunner().invoke(cli_module.cli, ["-q", "module", "delete", *args])


def test_the_command_lists_first_and_deletes_nothing_without_a_terminal(monkeypatch, tmp_path, world):
    folder = world.module()
    world.state = tmp_path / "home"
    modules_env.write(world.state, "my-board", world.values)
    result = _cli(monkeypatch, tmp_path, "my-board")
    assert result.exit_code == 1 and "Nothing was deleted" in result.output
    assert "private values of module my-board" in result.output
    for value in world.values.values():
        assert value not in result.output
    assert folder.is_dir() and modules_env.read(world.state, "my-board") == world.values


def test_the_command_refuses_a_shipped_target_an_unknown_name_and_a_pack(monkeypatch, tmp_path, world):
    world.module("from-pack", provenance=True)
    for name, words in (("Rockchip", "framework ships"), ("nothing", "no module here"), ("from-pack", "uninstalling the pack")):
        result = _cli(monkeypatch, tmp_path, name)
        assert result.exit_code == 1 and words in " ".join(result.output.split()), (name, result.output)
    assert (world.own / "from-pack").is_dir()


def test_the_command_deletes_after_the_persons_yes(monkeypatch, tmp_path, world):
    from orchestrator import cli as cli_module
    folder = world.module()
    world.state = tmp_path / "home"
    modules_env.write(world.state, "my-board", world.values)
    audit = Audit()
    accepted_modes = []

    def person_says_yes(plan, **kwargs):
        accepted_modes.append(plan.mode_offered)
        return accept(plan, "cli", "permanent")

    monkeypatch.setattr(cli_module, "confirm_deletion", person_says_yes)
    monkeypatch.setattr(cli_module, "_person_trash", lambda ctx, state: (Trash(state, audit=audit), audit))
    result = _cli(monkeypatch, tmp_path, "my-board")
    assert result.exit_code == 0, result.output
    assert accepted_modes == [("trash", "permanent")]
    assert not folder.exists() and not modules_env.path_for(world.state, "my-board").exists()
    assert audit.entries[0]["outcome"] == "module_deleted"


def test_an_ai_has_no_tool_that_deletes_a_module():
    import orchestrator.tools as tools_package
    text = "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in Path(tools_package.__file__).parent.rglob("*.py"))
    assert "module_delete" not in text and "delete_module" not in text


# --- the button -----------------------------------------------------------------------------------------------

def _dialog(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.target_manager import TargetManager
    from orchestrator.gui import targets_window
    own = tmp_path / "own_targets"
    own.mkdir()
    for name in ("MyBoard", "FromPack"):
        (own / name).mkdir()
        (own / name / "target.yml").write_text(f"metadata:\n  id: {name}\n  name: {name}\n", encoding="utf-8")
    (own / "FromPack" / PROVENANCE_NAME).write_text("{}", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(own)), resolve_path=Path))
    audit = Audit()
    framework = SimpleNamespace(target_manager=tm, get_component=lambda n: tm if n == "target_manager" else None,
                                config=SimpleNamespace(user_state_dir=state), tool_registry=SimpleNamespace(audit=audit))
    framework.build_service = BuildService(framework)
    monkeypatch.setattr(Trash, "for_framework", staticmethod(lambda fw: Trash(state, audit=audit)))
    said = []
    monkeypatch.setattr(targets_window, "plain_box", lambda parent, icon, title, text: said.append((icon, text)))
    return targets_window.TargetsDialog(framework, lang="en"), own, state, audit, said


def test_the_button_is_enabled_for_a_module_of_your_own_only(qapp, tmp_path, monkeypatch):
    dialog, own, state, audit, said = _dialog(tmp_path, monkeypatch)
    assert dialog.btn_delete.objectName() == "btn_targets_delete"
    dialog.table.selectRow([dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())].index("MyBoard"))
    assert dialog.btn_delete.isEnabled()
    dialog.table.selectRow([dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())].index("FromPack"))
    assert not dialog.btn_delete.isEnabled()


def test_the_button_asks_first_and_no_deletes_nothing(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import deletion_dialog
    dialog, own, state, audit, said = _dialog(tmp_path, monkeypatch)
    dialog.refresh("MyBoard")
    asked = []
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: asked.append(plan) or None)
    dialog._delete()
    assert len(asked) == 1 and asked[0].items[0].file_name == "MyBoard"
    assert (own / "MyBoard").is_dir() and not audit.entries


def test_the_button_deletes_the_accepted_list_and_reads_the_registry_again(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import deletion_dialog
    dialog, own, state, audit, said = _dialog(tmp_path, monkeypatch)
    modules_env.write(state, "MyBoard", {"LOCAL_X": secret(7)})
    dialog.refresh("MyBoard")
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accept(plan, "gui", "trash"))
    dialog._delete()
    assert not (own / "MyBoard").exists() and not modules_env.path_for(state, "MyBoard").exists()
    assert audit.entries[0]["outcome"] == "module_deleted"
    assert [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())] == ["FromPack"]
    assert said and said[-1][0] == "information" and "MyBoard" in said[-1][1]
