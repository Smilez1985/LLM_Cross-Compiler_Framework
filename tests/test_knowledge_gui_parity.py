"""The knowledge and Community Hub mouse paths (DECISIONS W1', wave 5 chain "knowledge"; offscreen).

Every action of `llmcf knowledge` and `llmcf zoo` can be started in the GUI by mouse, without typing a command:

- Tools > Knowledge Snapshots...: whether the next snapshot fits, the storage in every state, [Convert now] also
  before any attempt;
- Tools > Knowledge Sources...: every source with kind, licence, copyright notice and what a pack carries, by kind;
- Tools > Community Hub: Details..., Import file... with a passphrase or sender key files, Check installed packs,
  Check a pack file..., Export... (an existing target, a build, the knowledge base with its id, encrypted to key files
  or with a passphrase, into a chosen folder) and My public keys... (copy, save).

Each handler calls the core function the CLI calls, with the right arguments; long work runs off the GUI thread;
questions default to "No"; every text exists in English and German.
"""

import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import knowledge_snapshots as ks
from test_deletion_dialog import boxes
from test_edge_store import Audit, build_old_store, failed_state

pytestmark = pytest.mark.usefixtures("qapp")
GB = 1000 ** 3


def usage(free, total=100 * GB):
    return lambda path: shutil._ntuple_diskusage(total, total - free, free)


def wait_until(qapp, condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()
    return condition()


def state_framework(state: Path, **extra):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state, get=lambda k, d=None: d),
                           tool_registry=SimpleNamespace(audit=Audit(), policy_store=None), **extra)


# --- Tools > Knowledge Snapshots...: the next snapshot and the storage -----------------------------------------------

@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_snapshots_window_says_beforehand_whether_the_next_snapshot_fits(tmp_path, monkeypatch, lang):
    from orchestrator.gui import knowledge_snapshots_window as window
    from orchestrator.utils.localization import ui_text
    folder = tmp_path / "knowledge_snapshots"
    folder.mkdir()
    (folder / "pre_ingest_1.jsonl.gz").write_bytes(b"x" * 300_000_000)
    calls = []
    real = ks.snapshot_disk
    monkeypatch.setattr(ks, "snapshot_disk", lambda *a, **k: calls.append(a) or real(*a, **k))
    monkeypatch.setattr(ks, "_disk_usage", usage(int(2.3 * GB)))
    dialog = window.KnowledgeSnapshotsDialog(tmp_path, lang=lang)
    try:
        assert calls and calls[0][0] == Path(tmp_path), "the core function of `llm-cli knowledge snapshots`"
        text = dialog.disk.text()
        assert dialog.snapshot_disk.verdict == "does_not_fit"
        assert ui_text("snapshots.next.no_room", lang, size="300.0 MB") in text
        assert ui_text("snapshots.next.step", lang) in text
        if lang == "en":   # the same warning words as the CLI
            assert dialog.snapshot_disk.warning() in text
    finally:
        dialog.close()
    monkeypatch.setattr(ks, "_disk_usage", usage(int(3.5 * GB)))
    dialog = window.KnowledgeSnapshotsDialog(tmp_path, lang=lang)
    try:
        assert ui_text("snapshots.next.tight", lang, size="300.0 MB") in dialog.disk.text()
        if lang == "en":
            assert ks.NEXT_TIGHT.format(size="300.0 MB") in dialog.disk.text()
    finally:
        dialog.close()
    monkeypatch.setattr(ks, "_disk_usage", usage(500 * GB))
    dialog = window.KnowledgeSnapshotsDialog(tmp_path, lang=lang)
    try:
        assert "\n" not in dialog.disk.text() and dialog.disk.text()
    finally:
        dialog.close()
    assert [p.name for p in folder.iterdir()] == ["pre_ingest_1.jsonl.gz"], "nothing is deleted by itself"


class ConvertingRag:
    """convert_storage as RAGManager does it (migration.run with force), remembering the thread it ran on."""

    def __init__(self, state):
        self.state, self.threads = state, []

    def convert_storage(self):
        from orchestrator.rag import migration
        self.threads.append(threading.current_thread().name)
        use = migration.run(self.state, force=True)[0]
        return {**migration.summary(self.state), "used": "qdrant-edge" if use == "edge" else "old-local", "error": ""}


@pytest.mark.parametrize("lang", ["en", "de"])
def test_convert_now_is_offered_before_any_attempt_and_runs_off_the_gui_thread(qapp, tmp_path, monkeypatch, lang):
    from orchestrator.gui import knowledge_snapshots_window as window
    from orchestrator.utils.localization import ui_text
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    rag = ConvertingRag(state)
    framework = state_framework(state, ensure_rag_manager=lambda: rag)
    told = boxes(monkeypatch)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang=lang)
    try:
        assert dialog.storage_status.state == "" and dialog.storage_status.reason_code == "not_converted"
        assert not dialog.btn_convert.isHidden() and dialog.btn_convert.isEnabled()
        assert dialog.btn_convert.text() == ui_text("storage.convert.now", lang)
        text = dialog.storage.text()
        assert ui_text("storage.used.old", lang, folder=str(state / "qdrant")) in text
        assert ui_text("storage.reason.not_converted", lang) in text and ui_text("storage.convert.offer", lang) in text
        dialog.btn_convert.click()
        assert ui_text("storage.converting", lang) in dialog.storage.text()
        assert wait_until(qapp, lambda: bool(told))
        assert rag.threads and rag.threads[0] != threading.current_thread().name
        assert ui_text("storage.convert.done", lang, points=dialog.storage_status.points) in told[-1]
        assert dialog.btn_convert.isHidden() and dialog.storage_status.state == "converted"
        assert ui_text("storage.used.edge", lang, folder=str(state / "qdrant_edge")) in dialog.storage.text()
        assert ui_text("storage.old.folder.kept", lang, folder=str(state / "qdrant"),
                       size=ks.size_text(dialog.storage_status.old_folder_bytes)) in dialog.storage.text()
        assert not dialog.btn_old.isHidden(), "the old folder is offered for deletion only now"
    finally:
        dialog.close()
    assert (state / "qdrant").is_dir(), "the conversion keeps the old folder"


def test_after_a_failure_the_button_says_try_again(tmp_path, monkeypatch):
    from orchestrator.gui import knowledge_snapshots_window as window
    from orchestrator.utils.localization import ui_text
    state = failed_state(tmp_path)
    framework = state_framework(state, ensure_rag_manager=lambda: None)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang="en")
    try:
        assert dialog.btn_convert.text() == ui_text("storage.convert", "en") and dialog.btn_convert.isEnabled()
        assert "a reason" in dialog.storage.text() and ui_text("storage.convert.offer", "en") not in dialog.storage.text()
    finally:
        dialog.close()


def test_the_storage_line_is_shown_in_every_other_state(tmp_path, monkeypatch):
    from orchestrator.gui import knowledge_snapshots_window as window
    from orchestrator.rag import migration
    from orchestrator.utils.localization import ui_text
    # A fresh knowledge base: Qdrant Edge, nothing to convert.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    dialog = window.KnowledgeSnapshotsDialog(fresh, framework=state_framework(fresh, ensure_rag_manager=lambda: None),
                                             lang="de")
    try:
        assert dialog.storage.text() == ui_text("storage.used.edge", "de", folder=str(fresh / "qdrant_edge"))
        assert dialog.btn_convert.isHidden() and dialog.btn_old.isHidden()
    finally:
        dialog.close()
    # A Qdrant server named by the open knowledge base.
    rag = SimpleNamespace(storage_format="server", storage_reason="")
    dialog = window.KnowledgeSnapshotsDialog(fresh, framework=state_framework(fresh, rag_manager=rag), lang="en")
    try:
        assert dialog.storage.text() == ui_text("storage.used.server", "en")
    finally:
        dialog.close()
    old = tmp_path / "old"
    old.mkdir()
    build_old_store(old, extra_model=False, legacy=False)
    converted = tmp_path / "converted"
    converted.mkdir()
    build_old_store(converted, extra_model=False, legacy=False)
    migration.run(converted)
    monkeypatch.setitem(sys.modules, "qdrant_edge", None)   # from here on Qdrant Edge cannot be loaded
    # The old storage without Qdrant Edge for this computer: named, and no conversion offered.
    dialog = window.KnowledgeSnapshotsDialog(old, framework=state_framework(old, ensure_rag_manager=lambda: None),
                                             lang="en")
    try:
        assert ui_text("storage.reason.engine_missing", "en") in dialog.storage.text()
        assert dialog.btn_convert.isHidden() and dialog.storage_status.reason_code == "engine_missing"
    finally:
        dialog.close()
    # Qdrant Edge in use but not loadable: refused with the reason, the old folder never stands in.
    dialog = window.KnowledgeSnapshotsDialog(converted, lang="de")
    try:
        text = dialog.storage.text()
        assert ui_text("storage.used.none", "de", reason=ui_text("storage.reason.edge_unavailable", "de")) in text
        assert ui_text("storage.converted", "de", when=dialog.storage_status.converted_at[:16].replace("T", " "),
                       points=dialog.storage_status.points) in text
    finally:
        dialog.close()


def test_the_snapshot_window_buttons_have_object_names(tmp_path):
    from orchestrator.gui import knowledge_snapshots_window as window
    from PySide6.QtWidgets import QPushButton
    dialog = window.KnowledgeSnapshotsDialog(tmp_path, lang="en")
    try:
        names = {b.objectName() for b in dialog.findChildren(QPushButton)}
        assert {"btn_knowledge_snapshots_delete", "btn_knowledge_snapshots_export", "btn_knowledge_snapshots_open",
                "btn_knowledge_storage_delete_old", "btn_knowledge_storage_convert"} <= names
    finally:
        dialog.close()


# --- Tools > Knowledge Sources... ------------------------------------------------------------------------------------

SOURCES = [
    {"source": "https://docs.example/a", "title": "<b>A</b>", "kind": "web", "licence": "MIT",
     "copyright": "(c) 2026 A <a href=x>", "pack_share": "full_text", "share_reason": "MIT allows it"},
    {"source": "https://docs.example/b", "kind": "web", "licence": "", "copyright": "", "pack_share": "link_and_facts",
     "share_reason": "no licence that allows it"},
    {"source": "pack:notes/x", "kind": "pack", "licence": "", "copyright": "", "pack_share": "not_exported",
     "share_reason": "its author shares it"},
]


class ListingRag:
    def __init__(self, entries=SOURCES):
        self.entries = entries

    def list_sources(self):
        return [dict(e) for e in self.entries]


def recording_sources(monkeypatch):
    """knowledge_sources.sources as it is, remembering each call's kind and thread."""
    from orchestrator.Core import knowledge_sources
    calls, real = [], knowledge_sources.sources

    def spy(rag, kind="any"):
        calls.append((kind, threading.current_thread().name))
        return real(rag, kind)
    monkeypatch.setattr(knowledge_sources, "sources", spy)
    return calls


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_sources_window_lists_by_kind_through_the_core_off_the_gui_thread(qapp, monkeypatch, lang):
    from orchestrator.Core import knowledge_sources
    from orchestrator.gui import knowledge_sources_window as window
    from orchestrator.utils.localization import ui_text
    calls = recording_sources(monkeypatch)
    rag = ListingRag()
    dialog = window.KnowledgeSourcesDialog(SimpleNamespace(ensure_rag_manager=lambda: rag), lang=lang)
    try:
        assert wait_until(qapp, lambda: dialog.table.rowCount() == 3)
        assert calls[0][0] == "any" and calls[0][1] != threading.current_thread().name
        headers = [dialog.table.horizontalHeaderItem(c).text() for c in range(dialog.table.columnCount())]
        assert headers == [ui_text(k, lang) for k in window.COLUMNS]
        row = [dialog.table.item(0, c).text() for c in range(6)]
        assert row == ["https://docs.example/a", ui_text("sources.filter.web", lang), "MIT", "(c) 2026 A <a href=x>",
                       ui_text("sources.carried.full_text", lang), "MIT allows it"]
        assert "&lt;b&gt;A&lt;/b&gt;" in dialog.table.item(0, 0).toolTip(), "the title is plain text"
        assert dialog.summary.text() == ui_text("sources.totals", lang, n=3, full=1, facts=1)
        dialog.kind.setCurrentIndex(dialog.kind.findData("web"))
        assert wait_until(qapp, lambda: dialog.shown_kind == "web")
        assert calls[-1][0] == "web" and dialog.table.rowCount() == 2
        dialog.kind.setCurrentIndex(dialog.kind.findData("docs"))
        assert wait_until(qapp, lambda: dialog.shown_kind == "docs")
        assert dialog.table.rowCount() == 0
        assert dialog.summary.text() == ui_text("sources.empty.kind", lang, kind=ui_text("sources.filter.docs", lang))
        assert [dialog.kind.itemData(i) for i in range(dialog.kind.count())] == ["any", *knowledge_sources.KINDS]
    finally:
        dialog.close()
    if lang == "en":   # the same "In a pack" words as `llm-cli knowledge sources`
        assert {k: ui_text(f"sources.carried.{k}", "en") for k in knowledge_sources.CARRIED} == \
            knowledge_sources.CARRIED


def test_the_sources_window_says_when_the_knowledge_base_is_off_or_unreadable(qapp):
    from orchestrator.gui import knowledge_sources_window as window
    from orchestrator.rag.errors import KnowledgeBaseError
    from orchestrator.utils.localization import ui_text
    dialog = window.KnowledgeSourcesDialog(SimpleNamespace(ensure_rag_manager=lambda: None), lang="de")
    try:
        assert wait_until(qapp, lambda: dialog.status.text() == ui_text("sources.off", "de"))
        assert dialog.table.rowCount() == 0 and dialog.btn_refresh.isEnabled()
    finally:
        dialog.close()

    class Broken:
        def list_sources(self):
            raise KnowledgeBaseError("in use by another process")
    dialog = window.KnowledgeSourcesDialog(SimpleNamespace(ensure_rag_manager=lambda: Broken()), lang="en")
    try:
        assert wait_until(qapp, lambda: "in use by another process" in dialog.status.text())
        assert dialog.status.text() == ui_text("sources.unreadable", "en", reason="in use by another process")
    finally:
        dialog.close()


@pytest.fixture
def main_window(tmp_path, monkeypatch, memory_keyring):
    from conftest import REPO_ROOT
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window as module
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(module, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(module.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    windows = []

    def factory():
        win = module.MainOrchestrator(REPO_ROOT)
        windows.append(win)
        return win
    yield factory
    for win in windows:
        win.update_timer.stop()
        win.close()


def window_objects(window, menu_module, action):
    """The objects of `window` that WINDOW_OBJECTS says live in the window `action` opens."""
    from PySide6.QtCore import QObject
    names = [name for name, opener in menu_module.WINDOW_OBJECTS.items() if opener == action]
    return {name: window.findChild(QObject, name) for name in names}


def test_the_tools_menu_has_knowledge_sources_below_the_snapshots(qapp, main_window):
    from PySide6.QtGui import QAction

    from orchestrator.gui.knowledge_sources_window import KnowledgeSourcesDialog
    from orchestrator.gui.menus import knowledge as menu_module
    win = main_window()
    qapp.processEvents()
    win.framework.ensure_rag_manager = lambda: ListingRag()
    tools = next(a.menu() for a in win.menuBar().actions() if a.text() == "Tools")
    names = [a.objectName() for a in tools.actions()]
    assert names.index(menu_module.ACT_SOURCES) == names.index(menu_module.ACT_SNAPSHOTS) + 1, names
    for name in menu_module.ACTIONS:
        assert win.findChild(QAction, name) is not None, name
    action = win.findChild(QAction, menu_module.ACT_SOURCES)
    assert action is win.act_knowledge_sources and action.text() == "Knowledge Sources..."
    action.trigger()
    dialog = win.knowledge_sources_window
    assert isinstance(dialog, KnowledgeSourcesDialog) and dialog.isVisible()
    assert wait_until(qapp, lambda: dialog.table.rowCount() == 3)
    found = window_objects(dialog, menu_module, menu_module.ACT_SOURCES)
    assert found and all(found.values()), found
    action.trigger()
    assert len([d for d in win.findChildren(KnowledgeSourcesDialog) if d.isVisible()]) == 1
    win.knowledge_sources_window.close()
