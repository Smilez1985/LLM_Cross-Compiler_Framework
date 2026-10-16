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
