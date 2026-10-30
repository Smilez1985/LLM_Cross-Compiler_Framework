"""The Community Hub's Uninstall... runs in the hub's worker pattern (chain followups-3): CommunityManager.uninstall
removes excerpts from the knowledge base and moves folders, so it runs off the GUI thread; the hub's buttons and the
pack table are off while it runs, and the result (the box with what became of each entry) arrives on the GUI thread."""

import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.deletion import accept  # noqa: E402
from test_pack_uninstall import two_packs  # noqa: E402,F401 - the fixture
from test_rag import make_rag  # noqa: E402,F401 - the fixture
from test_zoo import wait  # noqa: E402


def test_uninstall_runs_off_the_gui_thread_with_the_buttons_off(qapp, two_packs, monkeypatch):  # noqa: F811
    from orchestrator.gui import community_hub, deletion_dialog
    world = two_packs
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accept(plan, "gui", "permanent"))
    boxes = []
    monkeypatch.setattr(community_hub, "plain_box",
                        lambda parent, kind, title, text, **kw: boxes.append((threading.current_thread(), text)))
    release, entered, ran_in = threading.Event(), threading.Event(), []
    real = world.cm.uninstall

    def slow_uninstall(*args, **kwargs):
        ran_in.append(threading.current_thread())
        entered.set()
        assert release.wait(10), "the test never let the uninstall finish"
        return real(*args, **kwargs)
    monkeypatch.setattr(world.cm, "uninstall", slow_uninstall)
    framework = SimpleNamespace(community_manager=world.cm, target_manager=None, secrets_manager=world.secrets,
                                tool_registry=SimpleNamespace(audit=SimpleNamespace(record=lambda *a, **k: 1)))
    hub = community_hub.CommunityHubWindow(framework)
    try:
        wait(hub, qapp)
        row = next(r for r, item in enumerate(hub.shown_items) if item.id == "pack-b")
        hub.table.selectRow(row)
        button = hub.findChild(community_hub.QPushButton, "btn_zoo_uninstall")
        button.click()   # returns at once: the uninstall waits in its worker
        assert entered.wait(10)
        assert ran_in[0] is not threading.main_thread()
        assert not button.isEnabled() and not hub.table.isEnabled() and not hub.refresh_btn.isEnabled()
        assert not hub.import_btn.isEnabled() and not hub.details_btn.isEnabled()
        assert boxes == []
        release.set()
        wait(hub, qapp)
        assert boxes and boxes[0][0] is threading.main_thread() and "Deleted for good" in boxes[0][1]
        assert button.isEnabled() and hub.table.isEnabled() and hub.refresh_btn.isEnabled()
        assert not any(prov.id == "pack-b" for _, prov in world.cm.installed())
    finally:
        release.set()
        hub.close()


def test_a_failed_uninstall_turns_the_buttons_on_again(qapp, two_packs, monkeypatch):  # noqa: F811
    from orchestrator.gui import community_hub, deletion_dialog
    world = two_packs
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accept(plan, "gui", "permanent"))
    errors = []
    monkeypatch.setattr(community_hub.QMessageBox, "warning", lambda parent, title, text: errors.append(text))

    def refused(*args, **kwargs):
        raise PermissionError("the folder is read-only")
    monkeypatch.setattr(world.cm, "uninstall", refused)
    framework = SimpleNamespace(community_manager=world.cm, target_manager=None, secrets_manager=world.secrets,
                                tool_registry=None)
    hub = community_hub.CommunityHubWindow(framework)
    try:
        wait(hub, qapp)
        hub.table.selectRow(next(r for r, item in enumerate(hub.shown_items) if item.id == "pack-b"))
        button = hub.findChild(community_hub.QPushButton, "btn_zoo_uninstall")
        button.click()
        wait(hub, qapp)
        assert errors and "read-only" in errors[0]
        assert button.isEnabled() and hub.table.isEnabled()
    finally:
        hub.close()
