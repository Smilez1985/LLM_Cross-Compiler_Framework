"""The mouse and command-line paths of DECISIONS round 23 items 1 and 2 (offscreen; the core is in
test_knowledge_order.py):

- the Community Hub's Sort loose knowledge... lists the loose knowledge files, takes the topic or target, checks the
  chosen file through CommunityManager.prepare_sort off the GUI thread and moves it only after the person's Yes;
- the knowledge export names its topic or target (Belongs to:), refused without one;
- Tools > Knowledge Sources... filters by topic or target and shows it;
- `llmcf knowledge loose`, `llmcf knowledge sort`, `llmcf zoo export knowledge --topic` and the Mojo line.
"""

import json
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from test_knowledge_gui_parity import ListingRag, hub_for, settle, spy, wait_until  # noqa: E402
from test_knowledge_order import PAGES, RecordingRag, loose  # noqa: E402
from test_zoo import World, make_target  # noqa: E402

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def world(tmp_path):
    world = World(tmp_path)
    world.rag = RecordingRag()
    world.framework.get_component = lambda name: world.rag if name == "rag_manager" else None
    return world


def test_sort_loose_knowledge_checks_off_the_gui_thread_and_moves_only_after_yes(qapp, world, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import community_dialogs, community_hub
    path = loose(world, "notes.json")
    make_target(world.root / "targets" / "Rockchip")
    calls = spy(monkeypatch, world.cm, "prepare_sort")
    seen = {}

    def choose(dialog):
        seen["files"] = [dialog.files.item(i).data(256) for i in range(dialog.files.count())]
        seen["targets"] = []
        dialog.chooser.scope.setCurrentIndex(dialog.chooser.scope.findData("target"))
        seen["targets"] = [dialog.chooser.name.itemText(i) for i in range(dialog.chooser.name.count())]
        dialog._check()
        return dialog.result()
    monkeypatch.setattr(community_dialogs.SortKnowledgeDialog, "exec", choose)
    answers = [QMessageBox.No, QMessageBox.Yes]
    asked = []

    def answer(box):
        asked.append((box.windowTitle(), box.text(), box.defaultButton() == box.button(QMessageBox.No)))
        return answers.pop(0)
    monkeypatch.setattr(community_hub.QMessageBox, "exec", answer)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        assert hub.sort_btn.objectName() == "btn_zoo_sort"
        hub.sort_btn.click()
        settle(hub, qapp)
        assert seen == {"files": ["notes.json"], "targets": ["Rockchip"]}
        args, kwargs, thread = calls[0]
        assert args == ("notes.json",) and kwargs == {"topic": None, "target": "Rockchip"}
        assert thread != threading.current_thread().name
        title, text, no_default = asked[0]
        assert "Target: Rockchip" in text and "Moves the file notes.json" in text and no_default
        assert path.is_file() and world.rag.ingested == [], "No: nothing moves, nothing is imported"
        hub.sort_btn.click()
        settle(hub, qapp)
        assert not path.exists() and (world.cm.knowledge_dir / "Rockchip" / "notes" / "knowledge.json").is_file()
        assert [s for s, _, _ in world.rag.ingested] == [p["source"] for p in PAGES]
        assert "notes.json" in hub.status_label.text() and "Rockchip" in hub.status_label.text()
    finally:
        hub.close()


@pytest.mark.parametrize("no_room", [True, False])
def test_only_a_snapshot_that_did_not_fit_offers_the_snapshot_list(qapp, tmp_path, monkeypatch, no_room):
    """After an install the hub says why the pack is not in the knowledge base yet. Only the snapshot that did not fit
    opens "Not enough disk space" with the list of snapshots to delete; another reason (pages refused, e.g. offline
    without the embedding model) is said in the hub's status, never as a question of disk space."""
    from PySide6.QtWidgets import QLabel

    from orchestrator.gui import community_hub, knowledge_snapshots_window
    from orchestrator.utils.localization import ui_text
    offered = []
    monkeypatch.setattr(knowledge_snapshots_window, "offer_snapshot_list",
                        lambda parent, framework, text, lang=None: offered.append(text) or False)
    why = ("Not enough disk space for the snapshot" if no_room
           else "knowledge pack rk-docs: 2 page(s) not imported yet; tried again when the knowledge base starts")
    cm = SimpleNamespace(last_sync_problem=why, last_sync_no_room=no_room, last_sync_snapshot=None)
    hub = SimpleNamespace(comm_manager=cm, framework_manager=SimpleNamespace(target_manager=None),
                          status_label=QLabel(), installed=SimpleNamespace(emit=lambda kind: None),
                          refresh_list=lambda: None, lang="en")
    prepared = SimpleNamespace(manifest=SimpleNamespace(kind="knowledge", id="rk-docs"), imports_knowledge=True,
                               loose_file=None)
    community_hub.CommunityHubWindow._installed(hub, (prepared, tmp_path / "rk-docs"))
    assert offered == ([why] if no_room else [])
    if not no_room:
        assert ui_text("zoo.install.waiting", "en", why=why) in hub.status_label.text()


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_sort_dialog_refuses_without_a_topic_and_says_when_nothing_is_loose(qapp, lang):
    from orchestrator.gui.community_dialogs import SortKnowledgeDialog
    from orchestrator.utils.localization import ui_text
    item = SimpleNamespace(label="a.json", size=12)
    dialog = SortKnowledgeDialog([item], ["llama.cpp"], [], lang=lang)
    try:
        dialog._check()
        assert dialog.problem.text() == ui_text("knowledge.scope.missing", lang) and not dialog.result()
        dialog.chooser.name.setCurrentText("rk-npu")
        dialog._check()
        assert dialog.values() == ("a.json", "rk-npu", None)
    finally:
        dialog.close()
    empty = SortKnowledgeDialog([], [], [], lang=lang)
    try:
        assert not empty.btn_check.isEnabled()
    finally:
        empty.close()


def test_the_knowledge_export_needs_its_topic_or_target(qapp, world, monkeypatch):
    from orchestrator.gui import community_hub, plain
    from orchestrator.utils.localization import ui_text
    told = []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)
    calls = spy(monkeypatch, world.cm, "export_pack")
    make_target(world.root / "targets" / "Rockchip")
    dialog = community_hub.CommunityExportDialog(SimpleNamespace(community_manager=world.cm), "knowledge", None)
    try:
        dialog.author_edit.setText("pat")
        dialog.create_pack()
        assert told[-1] == ui_text("knowledge.scope.missing", "en") and calls == []
        dialog.scope.scope.setCurrentIndex(dialog.scope.scope.findData("target"))
        assert dialog.scope.name.currentText() == "Rockchip" and not dialog.scope.name.isEditable()
    finally:
        dialog.close()
    target_dialog = community_hub.CommunityExportDialog(SimpleNamespace(community_manager=world.cm), "target", "x")
    try:
        assert target_dialog.scope is None, "only a knowledge pack names a topic or target"
    finally:
        target_dialog.close()


def test_the_sources_window_filters_by_topic_and_shows_it(qapp, monkeypatch):
    from orchestrator.gui import knowledge_sources_window as window
    from orchestrator.utils.localization import ui_text
    rag = ListingRag()
    real = rag.list_sources

    def with_topics():
        entries = real()
        entries[0]["target"] = "Rockchip"
        return entries
    rag.list_sources = with_topics
    dialog = window.KnowledgeSourcesDialog(SimpleNamespace(ensure_rag_manager=lambda: rag), lang="de")
    try:
        assert wait_until(qapp, lambda: dialog.table.rowCount() == 3)
        topic_column = window.COLUMNS.index("sources.col.topic")
        assert dialog.table.item(0, topic_column).text() == ui_text("sources.topic.target", "de", name="Rockchip")
        assert dialog.table.item(1, topic_column).text() == "-"
        dialog.topic.setText("rockchip")
        dialog.topic.editingFinished.emit()
        assert wait_until(qapp, lambda: dialog.table.rowCount() == 1)
    finally:
        dialog.close()


# --- the command line -----------------------------------------------------------------------------------------------

def invoke(world, *args):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    framework = SimpleNamespace(get_component=lambda n: world.cm if n == "community_manager" else None,
                                target_manager=None, secrets_manager=world.secrets, config=world.config)

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), terminal_width=200)


def test_cli_lists_loose_knowledge_and_sorts_only_with_a_confirmation(world):
    loose(world, "notes.json")
    listed = invoke(world, "knowledge", "loose")
    assert listed.exit_code == 0, listed.output
    # the table's title holds the folder, whose length depends on where the tests run: compared without the wrapping
    words = " ".join(listed.output.split())
    assert "notes.json" in words and "(not imported)" in words
    refused = invoke(world, "knowledge", "sort", "notes.json")
    assert refused.exit_code == 2 and "--topic or --target" in refused.output
    missing = invoke(world, "knowledge", "sort", "other.json", "--topic", "t")
    assert missing.exit_code == 1 and "no loose knowledge file" in missing.output
    assert "llmcf knowledge loose" in missing.output, "the next step names the command"
    # a loose file nobody reviewed is always confirmed: without a terminal it stays where it is
    blind = invoke(world, "knowledge", "sort", "notes.json", "--topic", "npu")
    assert blind.exit_code == 1 and "No terminal" in blind.output and "Topic: npu" in blind.output
    assert (world.cm.knowledge_dir / "notes.json").is_file() and world.rag.ingested == []


def test_cli_knowledge_export_needs_a_topic(world):
    result = invoke(world, "zoo", "export", "knowledge", "--author", "a")
    assert result.exit_code == 2 and "--topic or --target" in result.output
    result = invoke(world, "zoo", "export", "target", "x", "--author", "a", "--topic", "t")
    assert result.exit_code == 2 and "only to a knowledge pack" in result.output


def test_mojo_lists_the_loose_files(world):
    from orchestrator.Core.mojo.checks.community import check_community
    assert [f.code for f in check_community(SimpleNamespace(community_manager=world.cm))] == []
    loose(world, "notes.json")
    loose(world, "board/knowledge.json", json.dumps([]))
    findings = check_community(SimpleNamespace(community_manager=world.cm))
    assert [f.code for f in findings] == ["community.knowledge.loose"]
    finding = findings[0]
    assert finding.severity.value == "warn" and "board/knowledge.json, notes.json" in finding.message
    assert finding.cannot == "person" and [getattr(s, "id", s) for s in finding.steps] == ["knowledge.loose"]
