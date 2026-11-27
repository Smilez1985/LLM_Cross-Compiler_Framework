"""
`knowledge clear` (DESIGN, No dead code: the second recon's decision B of 2026-11-27; the rule of Deletion and the
trash): RAGManager.clear_knowledge_base empties the knowledge base only on the person's yes to the exact list of its
collections, saves the active one as a snapshot first (no room: nothing is emptied), is audited first and never an
AI's; Ditto's archive is no collection of the knowledge base and stays.
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept
from test_rag import DOCS, ingest_all, make_rag  # noqa: F401 - the fixture
from tests.knowledge_clear import Audit


def test_the_list_names_the_active_collection_with_its_chunks_and_where_it_is(make_rag):
    rag = make_rag()
    assert rag.clear_plan() is None   # nothing stored yet
    ingest_all(rag)
    plan = rag.clear_plan()
    [item] = plan.items
    count = rag.get_status()["vector_count"]
    assert item.kind == "knowledge_collection" and item.full_path == ""
    assert f"active collection, {count} chunks" in item.what and rag._storage_label() in item.what
    assert plan.mode_offered == ("permanent",)


def test_the_active_collection_is_saved_first_and_comes_back(make_rag):
    rag = make_rag()
    ingest_all(rag)
    count = rag.get_status()["vector_count"]
    audit = Audit()
    result = rag.clear_knowledge_base(accept(rag.clear_plan(), "cli", "permanent"), audit=audit)
    assert result and result.snapshot and result.snapshot in rag.list_snapshots()
    assert rag.get_status()["vector_count"] == 0
    assert audit.entries[0]["tool"] == "knowledge_cleared"
    assert audit.entries[0]["arguments"]["snapshot"] == result.snapshot
    assert rag.restore_snapshot(result.snapshot) and rag.get_status()["vector_count"] == count


def test_an_ai_a_changed_list_or_no_audit_empty_nothing(make_rag):
    rag = make_rag()
    ingest_all(rag)
    plan = rag.clear_plan()
    count = rag.get_status()["vector_count"]
    with pytest.raises(PermissionError):
        rag.clear_knowledge_base(AcceptedDeletion(plan, "permanent", "mcp"), audit=Audit())
    with pytest.raises(RuntimeError):
        rag.clear_knowledge_base(accept(plan, "gui", "permanent"), audit=None)
    assert rag.ingest_document("https://docs.example/more", "# More\n\nAnother page about the NPU.", {},
                               replace=True)
    with pytest.raises(PlanChanged):
        rag.clear_knowledge_base(accept(plan, "gui", "permanent"), audit=Audit())
    assert rag.get_status()["vector_count"] > count


def test_without_room_for_the_snapshot_nothing_is_emptied(make_rag, monkeypatch):
    from orchestrator.Core.knowledge_snapshots import SnapshotNoRoom
    rag = make_rag()
    ingest_all(rag)
    count = rag.get_status()["vector_count"]

    def no_room(name=None, before="the change"):
        error = SnapshotNoRoom.__new__(SnapshotNoRoom)
        Exception.__init__(error, f"No room for the snapshot before {before}.")
        raise error
    monkeypatch.setattr(rag, "create_snapshot", no_room)
    with pytest.raises(SnapshotNoRoom):
        rag.clear_knowledge_base(accept(rag.clear_plan(), "cli", "permanent"), audit=Audit())
    assert rag.get_status()["vector_count"] == count


def test_the_command_lists_asks_and_empties(make_rag, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    rag = make_rag()
    ingest_all(rag)
    audit = Audit()
    monkeypatch.setattr(cli, "_private_rag", lambda ctx: rag)
    monkeypatch.setattr(cli, "_cli_audit", lambda ctx: audit)
    asked = []
    monkeypatch.setattr(cli, "confirm_deletion",
                        lambda plan, **kw: asked.append(kw.get("verb")) or accept(plan, "cli", "permanent"))
    out = CliRunner().invoke(cli.cli, ["knowledge", "clear"])
    assert out.exit_code == 0, out.output
    assert asked == ["Empty"] and "Snapshot taken first: pre_clear_" in out.output
    assert rag.get_status()["vector_count"] == 0
    assert "nothing to empty" in CliRunner().invoke(cli.cli, ["knowledge", "clear"]).output


def test_the_snapshots_window_empties_after_the_dialog(qapp, make_rag, monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from orchestrator.gui import deletion_dialog, knowledge_snapshots_window
    rag = make_rag()
    ingest_all(rag)
    audit = Audit()
    framework = SimpleNamespace(ensure_rag_manager=lambda: rag, tool_registry=SimpleNamespace(audit=audit))
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accept(plan, "gui", "permanent"))
    shown = []
    window = knowledge_snapshots_window.KnowledgeSnapshotsDialog(tmp_path / "state", framework=framework, lang="en")
    monkeypatch.setattr(window, "_box", lambda kind, text: shown.append((kind, text)))
    try:
        assert window.btn_clear.objectName() == "btn_knowledge_clear"
        result = window._clear()
        assert result and rag.get_status()["vector_count"] == 0
        assert shown[-1][0] == "information" and result.snapshot in shown[-1][1]
    finally:
        window.close()
