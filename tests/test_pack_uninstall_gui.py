"""The Community Hub's Uninstall...: the deletion dialog with the exact list, then the same core call as `llm-cli zoo
uninstall`; a pack that is not installed is not offered."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.deletion import accept  # noqa: E402
from test_pack_uninstall import two_packs  # noqa: E402,F401 - the fixture
from test_rag import make_rag  # noqa: E402,F401 - the fixture
from test_zoo import wait  # noqa: E402


def test_uninstall_from_the_hub(qapp, two_packs, monkeypatch):  # noqa: F811
    from orchestrator.gui import community_hub, deletion_dialog
    world = two_packs
    shown = []

    def ask(parent, plan, **kwargs):
        shown.append(plan)
        return accept(plan, "gui", "permanent")
    monkeypatch.setattr(deletion_dialog, "ask_deletion", ask)
    boxes = []
    monkeypatch.setattr(community_hub, "plain_box", lambda parent, kind, title, text, **kw: boxes.append(text))
    framework = SimpleNamespace(community_manager=world.cm, target_manager=None, secrets_manager=world.secrets,
                                tool_registry=SimpleNamespace(audit=SimpleNamespace(record=lambda *a, **k: 1)))
    hub = community_hub.CommunityHubWindow(framework)
    try:
        wait(hub, qapp)
        row = next(r for r, item in enumerate(hub.shown_items) if item.id == "pack-b")
        hub.table.selectRow(row)
        hub.findChild(community_hub.QPushButton, "btn_zoo_uninstall").click()
        wait(hub, qapp)
        assert shown and shown[0].items[0].file_name == "pack-b"
        assert not any(prov.id == "pack-b" for _, prov in world.cm.installed())
        assert boxes and "Deleted for good" in boxes[0]
    finally:
        hub.close()
