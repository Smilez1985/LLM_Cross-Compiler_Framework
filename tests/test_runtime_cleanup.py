"""Old llama.cpp runtime builds and device packages are deleted only from the person's list (round 5 f; WP7 item 4).

Until now every `ditto runtime install` (also through the AI tool build_local_runtime) kept only the two newest older
self-built builds and removed installations of other llama.cpp versions, every device build and deployment (also
through the AI tool deploy_device_runtime) removed the device's other runtime packages, and `llm-cli ditto runtime
prune` deleted by a count, all without a list. Now nothing of them is deleted by itself: the install and the
deployment say what they kept, and `llm-cli ditto runtime prune` and `llm-cli device runtime prune DEVICE` list each
item (full path, size, what it is), nothing ticked, and delete exactly what the person ticked and confirmed at a
terminal, into the trash by default.
"""

from dataclasses import replace

import pytest
from test_device_runtime import RK3566, source, stored_build  # noqa: F401 - the source fixture
from test_runtime_selfbuilt import GCC, machine, no_source, runtime, toolchain  # noqa: F401 - the no_source fixture

from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept, choose
from orchestrator.Core.trash import Trash
from orchestrator.runtime.build.store import BuildStore
from orchestrator.runtime.device.package import DeviceRuntimeStore, package_runtime
from orchestrator.runtime.llama_server import LocalLLMRuntime


class Audit:
    def __init__(self, fails=False):
        self.entries, self.fails = [], fails

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fails:
            raise OSError("disk full")
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


def two_installs(tmp_path):
    """Two installs of Ditto's runtime with different toolchains (another build ID each time)."""
    rt = runtime(tmp_path, hw=machine([]))
    first = rt.install(backend="cpu")
    other = runtime(tmp_path, hw=machine([]))
    other._toolchain = toolchain(info=replace(GCC, cmake="3.31.0"))
    second = other.install(backend="cpu")
    return other, first.active, second


def test_installing_dittos_runtime_deletes_no_older_build(tmp_path, no_source):
    rt, first, second = two_installs(tmp_path)
    assert second.active.build_id != first.build_id
    assert first.path.is_dir() and rt.store.get(first.build_id) is not None, "the older build stays"
    assert second.older == [first.build_id], "the install says which older builds it kept"
    for cls, name in ((LocalLLMRuntime, "prune"), (LocalLLMRuntime, "_prune_old_builds"), (BuildStore, "prune"),
                      (DeviceRuntimeStore, "prune")):
        assert not hasattr(cls, name), f"{cls.__name__}.{name} deleted by a count, without the person's list"


def test_an_installation_of_another_llama_cpp_version_is_kept_and_listed(tmp_path, no_source):
    rt = runtime(tmp_path, hw=machine([]))
    old = rt.root / "b0001-cpu"
    old.mkdir(parents=True)
    (old / "llama-server").write_bytes(b"x" * 10)
    rt.install(backend="cpu")
    assert old.is_dir(), "an install never deletes another installation"
    plan = rt.cleanup_plan()
    item = next(i for i in plan.items if i.full_path == str(old))
    assert item.kind == "runtime_build" and item.size_bytes == 10 and "another version" in item.what
    assert plan.mode_offered == ("trash", "permanent")


def test_old_runtime_builds_are_deleted_only_as_the_person_ticked(tmp_path, no_source):
    rt, first, _second = two_installs(tmp_path)
    old = rt.root / "b0001-cpu"
    old.mkdir(parents=True)
    (old / "llama-server").write_bytes(b"x")
    plan = rt.cleanup_plan()
    assert sorted(i.full_path for i in plan.items) == sorted([str(first.path), str(old)])
    assert all(i.full_path and i.size_bytes >= 0 and i.what for i in plan.items)
    assert str(tmp_path) not in str(plan.ai_view()), "an AI would see names and sizes only"
    chosen = choose(plan, [i for i, item in enumerate(plan.items) if item.full_path == str(old)])
    with pytest.raises(PermissionError):   # never through a tool: only the person's own window or terminal
        rt.delete_old_builds(AcceptedDeletion(chosen, "permanent", "framework"), audit=Audit())
    with pytest.raises(RuntimeError, match="nothing was deleted"):
        rt.delete_old_builds(accept(chosen, "cli", "permanent"), audit=Audit(fails=True))
    (old / "llama-server").write_bytes(b"xy")
    with pytest.raises(PlanChanged):
        rt.delete_old_builds(accept(chosen, "cli", "permanent"), audit=Audit())
    assert old.is_dir() and first.path.is_dir()
    chosen = choose(rt.cleanup_plan(), [i for i, item in enumerate(rt.cleanup_plan().items)
                                        if item.full_path == str(old)])
    audit = Audit()
    result = rt.delete_old_builds(accept(chosen, "cli", "permanent"), audit=audit)
    assert result.done == [chosen.items[0].speaking_name] and result.freed_bytes == 2 and result.failed == []
    assert not old.exists() and first.path.is_dir(), "exactly the ticked item"
    assert audit.entries[0]["outcome"] == "started" and audit.entries[0]["extra"]["deletion_sha256"] == \
        chosen.sha256()
    # The trash (the default): the build moves into the per-user folder's trash and can be restored.
    trash = Trash(rt.data_dir, audit=Audit())
    plan = rt.cleanup_plan()
    result = rt.delete_old_builds(accept(plan, "cli"), trash=trash, audit=Audit())
    assert result.done and not first.path.exists() and rt.store.get(first.build_id) is None
    (entry,) = trash.entries()
    assert entry.kind == "runtime_build" and entry.stored().is_dir()
    trash.restore(entry.id, "cli")
    assert rt.store.get(first.build_id) is not None, "restored, the store has it again"


def test_a_build_that_became_active_after_the_list_was_shown_is_kept(tmp_path, no_source):
    rt, first, _second = two_installs(tmp_path)
    plan = rt.cleanup_plan()
    rt.store.activate(first.build_id)   # the person or another process switched back meanwhile
    with pytest.raises(PlanChanged):
        rt.delete_old_builds(accept(plan, "cli", "permanent"), audit=Audit())
    assert first.path.is_dir()
    # Under the store's lock the same check keeps it, whatever the list said.
    done, kept = rt.store.remove([first.build_id], purpose="host-runtime")
    assert done == [] and kept[0][0] == first.build_id and "in use" in kept[0][1]


def test_device_packages_are_kept_and_deleted_only_as_the_person_ticked(tmp_path, source):  # noqa: F811
    state = tmp_path / "state"
    store, build = stored_build(state, source)
    devices = DeviceRuntimeStore(state, "0123456789abcdef")
    packaged = package_runtime(store, build, licence=source / "LICENSE", out_dir=devices.root)
    old = devices.root / "llama-runtime-arm64-ffffffffffffffff.tar.gz"
    old.write_bytes(b"old")
    old.with_name(old.name + ".sha256").write_text(f"{'0' * 64}  {old.name}\n")
    devices.save_record({"build_id": build.build_id})
    plan = devices.cleanup_plan()
    assert [i.file_name for i in plan.items] == [old.name, old.name + ".sha256"], "never the deployed one's"
    assert all(i.kind == "deploy_package" and i.full_path.startswith(str(devices.root)) for i in plan.items)
    with pytest.raises(PermissionError):
        devices.delete_packages(AcceptedDeletion(plan, "permanent", "framework"), state, audit=Audit())
    with pytest.raises(RuntimeError):
        devices.delete_packages(accept(plan, "cli", "permanent"), state, audit=None)
    result = devices.delete_packages(accept(choose(plan, [0]), "cli", "permanent"), state, audit=Audit())
    assert result.done == [old.name] and not old.exists() and packaged.path.is_file()
    plan = devices.cleanup_plan()
    assert [i.file_name for i in plan.items] == [old.name + ".sha256"], "a checksum file left alone stays listed"
    trash = Trash(state, audit=Audit())
    devices.delete_packages(accept(plan, "cli"), state, trash=trash, audit=Audit())
    assert [e.kind for e in trash.entries()] == ["deploy_package"] and devices.cleanup_plan() is None
    assert packaged.path.is_file(), "the deployed build's package stays"


def test_cli_runtime_prune_lists_and_deletes_only_what_the_person_picked(tmp_path, monkeypatch, no_source):
    import io

    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module
    rt, first, _second = two_installs(tmp_path)
    monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)

    def run(terminal, answer=None):
        printed = io.StringIO()
        monkeypatch.setattr(cli_module, "console", Console(file=printed, width=400, color_system=None))
        monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
        result = CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", "prune"], input=answer)
        return result, printed.getvalue() + result.output

    _, out = run(False)
    assert str(first.path) in out and "Nothing was deleted: deleting needs your answer at a terminal" in out
    _, out = run(True, "\n")
    assert "Nothing was deleted." in out and first.path.is_dir(), "nothing is ticked by default"
    _, out = run(True, "1\npermanent\n\n")
    assert "Delete exactly 1 items" in out and "Nothing was deleted." in out and first.path.is_dir(), "default no"
    result, out = run(True, "1\npermanent\ny\n")
    assert result.exit_code == 0, out
    assert "Deleted: llama-server build" in out and not first.path.exists()
    assert rt.store.get(rt.installed().build_id) is not None, "the active build is never listed"
