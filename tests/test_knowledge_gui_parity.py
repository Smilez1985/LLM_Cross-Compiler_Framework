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
        text = dialog.storage.text()
        assert "a reason" in text and ui_text("storage.convert.offer", "en") not in text
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


# --- Tools > Community Hub -------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    from test_zoo import World
    return World(tmp_path)


def hub_for(world, lang="en", monkeypatch=None):
    from orchestrator.gui import community_hub
    if monkeypatch is not None:
        monkeypatch.setattr(community_hub, "current_language", lambda: lang)
    framework = SimpleNamespace(community_manager=world.cm, target_manager=None, secrets_manager=world.secrets,
                                config=world.config, resolve_path=world.framework.resolve_path)
    return community_hub.CommunityHubWindow(framework)


def settle(hub, qapp):
    from test_zoo import wait
    wait(hub, qapp)
    qapp.processEvents()


def spy(monkeypatch, owner, name):
    """Wraps owner.name: each call's arguments and thread are remembered, the real function runs."""
    calls, real = [], getattr(owner, name)

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs, threading.current_thread().name))
        return real(*args, **kwargs)
    monkeypatch.setattr(owner, name, wrapper)
    return calls


def errors(monkeypatch):
    """The hub's error boxes (on_error: plain text made into a rich-text container), as the person reads them."""
    from PySide6.QtGui import QTextDocument

    from orchestrator.gui import community_hub
    shown = []

    def read(parent, title, text):
        document = QTextDocument()
        document.setHtml(text)
        shown.append(document.toPlainText().replace("\xa0", " "))
    monkeypatch.setattr(community_hub.QMessageBox, "warning", read)
    return shown


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_hub_has_every_new_button_in_both_languages(qapp, world, monkeypatch, lang):
    from orchestrator.gui.menus import knowledge as menu_module
    from orchestrator.utils.localization import ui_text
    hub = hub_for(world, lang, monkeypatch)
    try:
        settle(hub, qapp)
        found = window_objects(hub, menu_module, menu_module.ACT_HUB)
        assert found and all(found.values()), found
        for button, key in ((hub.details_btn, "zoo.details"), (hub.verify_btn, "zoo.verify_installed"),
                            (hub.verify_file_btn, "zoo.verify_file"), (hub.export_btn, "zoo.export"),
                            (hub.keys_btn, "zoo.keys")):
            assert button.text() == ui_text(key, lang).replace("&", "&&")
            assert ui_text(f"{key}.tip", lang) in button.toolTip()
    finally:
        hub.close()


def install_recipe(world, tmp_path):
    import json

    from test_recipes import recipe_data
    from test_zoo import pack_bytes
    root = tmp_path / "r"
    root.mkdir()
    data = recipe_data()
    data.update(id="demo-recipe", target={"id": "demo-board", "pack_sha256": "a" * 64}, format="gguf", board="demo",
                quantization="Q8_0")
    (root / "recipe.yml").write_text(json.dumps(data))
    (tmp_path / "r.zip").write_bytes(pack_bytes(root, kind="recipe", pack_id="demo-recipe", version="1.0.0"))
    return world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "r.zip"))


def test_details_show_what_zoo_show_shows(qapp, world, tmp_path, monkeypatch):
    from orchestrator.gui import community_dialogs
    from orchestrator.utils.localization import ui_text
    world.add(world.target_pack())
    world.publish()
    folder = install_recipe(world, tmp_path)
    calls = spy(monkeypatch, world.cm, "details")
    shown = []
    monkeypatch.setattr(community_dialogs.PackDetailsDialog, "exec",
                        lambda self: shown.append((self.fields, [r.toPlainText() for r in self.recipes])) or 0)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.table.clearSelection()
        hub.table.setCurrentCell(-1, -1)
        hub.details_btn.click()
        assert hub.status_label.text() == ui_text("zoo.choose_first", "en") and calls == []
        rows = {hub.table.item(r, 0).text(): r for r in range(hub.table.rowCount())}
        hub.table.setCurrentCell(rows["demo-recipe"], 0)
        hub.details_btn.click()
        settle(hub, qapp)
        args, _, thread = calls[0]
        assert args == ("demo-recipe", "recipe") and thread != threading.current_thread().name
        fields, recipes = shown[0]
        values = dict(fields[0])
        assert values[ui_text("zoo.field.folder", "en")] == str(folder)
        assert recipes == [(folder / "recipe.yml").read_text(encoding="utf-8")]
        hub.table.setCurrentCell(rows["Demo-Board"], 0)
        hub.table.cellDoubleClicked.emit(rows["Demo-Board"], 0)   # a double click shows the same
        settle(hub, qapp)
        entry = world.cm.details("demo-board")[0].item.entry
        values = dict(shown[1][0][0])
        assert values[ui_text("zoo.field.download", "en")] == entry.url
        assert values[ui_text("zoo.field.sha256", "en")] == entry.sha256
        assert values[ui_text("zoo.field.size", "en")] == ui_text("zoo.value.bytes", "en", size=entry.size)
        assert values[ui_text("zoo.field.licence", "en")] == "MIT"
    finally:
        hub.close()


def import_dialog_answers(monkeypatch, *answers):
    """ImportPackDialog answered like a person, once per entry of `answers`: (file, passphrase, key files)."""
    from orchestrator.gui import community_dialogs
    seen = []

    def run(dialog):
        path, passphrase, key_files = answers[len(seen)]
        dialog.file_edit.setText(str(path))
        dialog.passphrase.setText(passphrase or "")
        dialog.key_files = [str(k) for k in key_files]
        dialog._check()
        seen.append(dialog.problem.text())
        return dialog.result()
    monkeypatch.setattr(community_dialogs.ImportPackDialog, "exec", run)
    return seen


def test_import_a_passphrase_pack_by_mouse(qapp, world, tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.Core import community_pack as cp
    from orchestrator.gui import community_hub
    from orchestrator.utils.localization import ui_text
    secret = "correct horse battery staple"
    (tmp_path / "p.lcfpack").write_bytes(cp.encrypt_pack(world.target_pack(), sender=Ed25519PrivateKey.generate(),
                                                         passphrase=secret))
    calls = spy(monkeypatch, world.cm, "prepare_install_file")
    asked = []
    monkeypatch.setattr(community_hub.QMessageBox, "exec",
                        lambda self: asked.append((self.text(), self.defaultButton() is self.button(QMessageBox.No)))
                        or QMessageBox.No)
    seen = import_dialog_answers(monkeypatch, (tmp_path / "p.lcfpack", "", []), (tmp_path / "p.lcfpack", secret, []))
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.import_btn.click()
        assert seen == [ui_text("zoo.import.needs_passphrase", "en")] and calls == [], "refused before reading"
        hub.import_btn.click()
        settle(hub, qapp)
        args, kwargs, thread = calls[0]
        assert args == (tmp_path / "p.lcfpack",) and kwargs == {"passphrase": secret, "trusted_senders": None}
        assert thread != threading.current_thread().name
        assert asked and "demo-board" in asked[0][0] and asked[0][1], "the summary asks with No as the default"
        assert not (world.root / "targets" / "demo-board").exists(), "No installs nothing"
        assert secret not in repr(vars(hub)), "the passphrase is not kept by the window"
    finally:
        hub.close()


def test_import_only_from_the_senders_in_key_files(qapp, world, tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.Core import community_pack as cp
    from orchestrator.gui import community_hub
    from orchestrator.utils.localization import ui_text
    me = cp.UserKeys(world.secrets).age_identity()
    sender, stranger = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    data = world.target_pack()
    (tmp_path / "p.lcfpack").write_bytes(cp.encrypt_pack(data, sender=sender, recipients=[me.to_public()]))
    (tmp_path / "plain.zip").write_bytes(data)
    (tmp_path / "sender.keys").write_text(cp.public_key_text(sender.public_key()) + "\n", encoding="utf-8")
    (tmp_path / "stranger.keys").write_text(cp.public_key_text(stranger.public_key()) + "\n", encoding="utf-8")
    (tmp_path / "bin.keys").write_bytes(b"\xff\xfe\x00\x81")
    calls = spy(monkeypatch, world.cm, "prepare_install_file")
    monkeypatch.setattr(community_hub.QMessageBox, "exec", lambda self: QMessageBox.Yes)
    shown = errors(monkeypatch)
    seen = import_dialog_answers(monkeypatch, (tmp_path / "plain.zip", "", [tmp_path / "sender.keys"]),
                                 (tmp_path / "p.lcfpack", "", [tmp_path / "bin.keys"]),
                                 (tmp_path / "p.lcfpack", "", [tmp_path / "stranger.keys"]),
                                 (tmp_path / "p.lcfpack", "", [tmp_path / "sender.keys"]))
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.import_btn.click()
        assert seen[0] == ui_text("zoo.import.not_encrypted", "en") and calls == []
        hub.import_btn.click()
        assert ui_text("zoo.keyfile.not_text", "en", file=str(tmp_path / "bin.keys")) in shown[-1] and calls == []
        hub.import_btn.click()
        settle(hub, qapp)
        assert "not one of the trusted senders" in shown[-1]
        hub.import_btn.click()
        settle(hub, qapp)
        (senders,) = calls[-1][1]["trusted_senders"]
        assert isinstance(senders, Ed25519PublicKey) and calls[-1][1]["passphrase"] is None
        assert (world.root / "targets" / "demo-board").is_dir()
    finally:
        hub.close()


def test_check_installed_packs_finds_a_changed_pack(qapp, world, monkeypatch):
    from orchestrator.gui import community_dialogs
    from orchestrator.utils.localization import ui_text
    world.add(world.target_pack())
    world.publish()
    folder = world.install()
    (folder / "modules" / "build.sh").write_text("#!/bin/bash\necho changed\n", encoding="utf-8")
    calls = spy(monkeypatch, world.cm, "verify_installed")
    shown = []
    monkeypatch.setattr(community_dialogs.VerifyResultsDialog, "exec", lambda self: shown.append(
        (self.summary.text(), [[self.table.item(r, c).text() for c in range(5)]
                               for r in range(self.table.rowCount())])) or 0)
    hub = hub_for(world, "de", monkeypatch)
    try:
        settle(hub, qapp)
        hub.verify_btn.click()
        settle(hub, qapp)
        assert calls and calls[0][2] != threading.current_thread().name
        summary, rows = shown[0]
        assert summary == ui_text("zoo.verify.summary", "de", n=1, bad=1)
        assert rows[0][:4] == ["target", "demo-board", "1.2.0", str(folder)]
        assert "changed since it was installed" in rows[0][4]
    finally:
        hub.close()


def test_check_a_pack_file_before_installing_it(qapp, world, tmp_path, monkeypatch):
    from orchestrator.gui import community_hub, plain
    from orchestrator.utils.localization import ui_text
    (tmp_path / "good.zip").write_bytes(world.target_pack())
    (tmp_path / "bad.zip").write_bytes(world.target_pack(name="bad", script="wget https://x.invalid/a\n"))
    calls = spy(monkeypatch, world.cm, "verify_file")
    told = []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        for name in ("good.zip", "bad.zip"):
            monkeypatch.setattr(community_hub.QFileDialog, "getOpenFileName",
                                lambda *a, n=name, **k: (str(tmp_path / n), ""))
            hub.verify_file_btn.click()
            settle(hub, qapp)
        assert [c[0] for c in calls] == [(tmp_path / "good.zip",), (tmp_path / "bad.zip",)]
        assert all(c[2] != threading.current_thread().name for c in calls)
        assert told[0] == ui_text("zoo.verify_file.ok", "en", kind="target", id="demo-board", version="1.2.0")
        assert "downloads" in told[1] and "would be rejected" in told[1]
        assert not (world.root / "targets" / "demo-board").exists(), "checking installs nothing"
    finally:
        hub.close()


def export_through(monkeypatch, qapp, kind, source, fill):
    """Export...: ExportSourceDialog answered with `kind` and `source`, then CommunityExportDialog filled by `fill`
    and Create pack clicked; returns the export dialogs as they ended."""
    from orchestrator.gui import community_dialogs, community_hub
    ended = []

    def choose(dialog):
        dialog.kind.setCurrentIndex(dialog.kind.findData(kind))
        if kind == "target":
            dialog.target.setCurrentText(source)
        elif kind == "recipe":
            dialog.build_info = source
        dialog._check()
        return dialog.result()

    def export(dialog):
        fill(dialog)
        dialog.create_btn.click()
        if dialog._export_task is not None:
            dialog._export_task.wait(60000)
        qapp.processEvents()
        ended.append(dialog)
        return 0
    monkeypatch.setattr(community_dialogs.ExportSourceDialog, "exec", choose)
    monkeypatch.setattr(community_hub.CommunityExportDialog, "exec", export)
    return ended


def test_export_an_existing_target_with_a_passphrase_into_a_chosen_folder(qapp, world, tmp_path, monkeypatch):
    from test_zoo import make_target

    from orchestrator.Core import community_pack as cp
    make_target(world.root / "targets" / "my-board")
    calls = spy(monkeypatch, world.cm, "export_pack")
    out = tmp_path / "usb"
    out.mkdir()
    secret = "a long enough passphrase"

    def fill(dialog):
        dialog.author_edit.setText("pat")
        dialog.encrypt_combo.setCurrentIndex(dialog.encrypt_combo.findData("passphrase"))
        dialog.passphrase_edit.setText(secret)
        dialog.passphrase2_edit.setText(secret)
        dialog.out_dir = out
    ended = export_through(monkeypatch, qapp, "target", "my-board", fill)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.export_btn.click()
        args, kwargs, thread = calls[0]
        assert args == ("target", "my-board") and thread != threading.current_thread().name
        assert kwargs == {"author": "pat", "license": "MIT", "pack_id": None, "model_license": "unknown",
                          "recipients": [], "out_dir": out, "passphrase": secret}
        dialog = ended[0]
        assert dialog.result.encrypted and dialog.result.path.parent == out and dialog.result.path.suffix == ".lcfpack"
        assert not dialog.pr_btn.isEnabled(), "an encrypted pack is never submitted"
        assert dialog.passphrase_edit.text() == "" and dialog.passphrase2_edit.text() == ""
        data, _ = cp.decrypt_pack(dialog.result.path.read_bytes(), passphrase=secret)
        assert cp.read_manifest(data).id == "my-board"
    finally:
        hub.close()


def test_export_refuses_passphrases_that_differ_and_missing_key_files(qapp, world, monkeypatch):
    from test_zoo import make_target

    from orchestrator.gui import plain
    from orchestrator.utils.localization import ui_text
    make_target(world.root / "targets" / "my-board")
    calls = spy(monkeypatch, world.cm, "export_pack")
    told = []
    monkeypatch.setattr(plain, "_exec", lambda box: told.append(box.text()) or 0)

    def differs(dialog):
        dialog.author_edit.setText("pat")
        dialog.encrypt_combo.setCurrentIndex(dialog.encrypt_combo.findData("passphrase"))
        dialog.passphrase_edit.setText("a long enough passphrase")
        dialog.passphrase2_edit.setText("another long passphrase")
    export_through(monkeypatch, qapp, "target", "my-board", differs)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.export_btn.click()
        assert told[-1] == ui_text("zoo.export.passphrase_differs", "en") and calls == []

        def no_keys(dialog):
            dialog.author_edit.setText("pat")
            dialog.encrypt_combo.setCurrentIndex(dialog.encrypt_combo.findData("keys"))
        export_through(monkeypatch, qapp, "target", "my-board", no_keys)
        hub.export_btn.click()
        assert told[-1] == ui_text("zoo.export.no_recipients", "en") and calls == []
    finally:
        hub.close()


def test_export_the_knowledge_base_with_its_id_to_the_keys_in_a_key_file(qapp, world, tmp_path, monkeypatch):
    import zipfile

    import pyrage

    from orchestrator.Core import community_pack as cp
    world.rag.records = [{"source": "https://docs.invalid/a", "content": "a page",
                          "metadata": {"licence": "MIT", "copyright": "Copyright (c) 2026 Example"}}]
    friend = pyrage.x25519.Identity.generate()
    (tmp_path / "friend.keys").write_text(str(friend.to_public()) + "\n", encoding="utf-8")
    calls = spy(monkeypatch, world.cm, "export_pack")

    def fill(dialog):
        assert dialog.license_combo.currentText() == "NOASSERTION", "the default of zoo export knowledge"
        dialog.author_edit.setText("pat")
        dialog.id_edit.setText("board-notes")
        dialog.encrypt_combo.setCurrentIndex(dialog.encrypt_combo.findData("keys"))
        dialog.recipient_files = [str(tmp_path / "friend.keys")]
    ended = export_through(monkeypatch, qapp, "knowledge", None, fill)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.export_btn.click()
        args, kwargs, _ = calls[0]
        assert args == ("knowledge", None) and kwargs["pack_id"] == "board-notes"
        assert kwargs["license"] == "NOASSERTION" and len(kwargs["recipients"]) == 1
        result = ended[0].result
        assert result.encrypted and result.manifest.id == "board-notes"
        data, _ = cp.decrypt_pack(result.path.read_bytes(), identity=friend)
        (tmp_path / "plain.zip").write_bytes(data)
        with zipfile.ZipFile(tmp_path / "plain.zip") as zf:
            names = set(zf.namelist())
        assert "knowledge.json" in names and "NOTICE" in names and any(n.startswith("LICENSES/") for n in names)
    finally:
        hub.close()


def test_export_a_finished_build_chooses_its_build_info(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import community_dialogs
    (tmp_path / "build_info.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(community_dialogs.QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(tmp_path / "build_info.json"), ""))
    dialog = community_dialogs.ExportSourceDialog(["my-board"], str(tmp_path), lang="de")
    try:
        assert dialog.values() == ("target", "my-board")
        dialog.kind.setCurrentIndex(dialog.kind.findData("recipe"))
        dialog._check()
        assert dialog.problem.text() and dialog.result() == 0
        dialog.findChild(type(dialog.btn_next), "btn_zoo_export_build").click()
        dialog._check()
        assert dialog.values() == ("recipe", str(tmp_path / "build_info.json")) and dialog.result() == 1
        dialog.kind.setCurrentIndex(dialog.kind.findData("knowledge"))
        assert dialog.values() == ("knowledge", None)
    finally:
        dialog.close()


def test_my_public_keys_with_copy_and_save(qapp, world, tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from orchestrator.Core import community_pack as cp
    from orchestrator.gui import community_dialogs
    calls = spy(monkeypatch, cp.UserKeys, "export_public")
    target = tmp_path / "mine.keys"
    monkeypatch.setattr(community_dialogs.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    shown = []

    def run(dialog):
        dialog.btn_copy.click()
        dialog.btn_save.click()
        shown.append((dialog.text, dialog.status.text()))
        return 0
    monkeypatch.setattr(community_dialogs.PublicKeysDialog, "exec", run)
    hub = hub_for(world)
    try:
        settle(hub, qapp)
        hub.keys_btn.click()
        settle(hub, qapp)
        assert calls and calls[0][2] != threading.current_thread().name
        text, status = shown[0]
        assert text.count("ed25519:") == 1 and "\nage1" in text and "AGE-SECRET-KEY" not in text.upper()
        assert QGuiApplication.clipboard().text() == text
        assert target.read_text(encoding="utf-8") == text and str(target) in status
        assert [type(k).__name__ for k in cp.parse_public_keys(text)] == ["Recipient", "Ed25519PublicKey"]
    finally:
        hub.close()


def test_the_hub_and_the_snapshots_window_open_from_the_menu_bar(qapp, main_window, monkeypatch):
    from PySide6.QtGui import QAction

    from orchestrator.gui import knowledge_snapshots_window
    from orchestrator.gui.menus import knowledge as menu_module
    win = main_window()
    qapp.processEvents()
    win.findChild(QAction, menu_module.ACT_HUB).trigger()
    hub = win.community_hub
    settle(hub, qapp)
    found = window_objects(win, menu_module, menu_module.ACT_HUB)
    assert found and all(found.values()), found
    hub.close()
    opened = []
    monkeypatch.setattr(knowledge_snapshots_window.KnowledgeSnapshotsDialog, "exec",
                        lambda self: opened.append(window_objects(self, menu_module, menu_module.ACT_SNAPSHOTS)) or 0)
    win.findChild(QAction, menu_module.ACT_SNAPSHOTS).trigger()
    assert opened and all(opened[0].values()), opened


# --- CLI_PATHS and texts ---------------------------------------------------------------------------------------------

def test_cli_paths_name_every_knowledge_and_zoo_command_and_option():
    from orchestrator import cli as cli_module
    from orchestrator.gui.menus import knowledge as menu_module

    def walk(group, path):
        for name, command in group.commands.items():
            full = f"{path} {name}".strip()
            yield full, command
            if hasattr(command, "commands"):
                yield from walk(command, full)

    area = {p: c for p, c in walk(cli_module.cli, "") if p.split()[0] in ("knowledge", "zoo")}
    assert {"knowledge sources", "knowledge storage convert", "zoo show", "zoo verify", "zoo keys"} <= set(area)
    for path, command in area.items():
        assert path in menu_module.CLI_PATHS, path
        for param in command.params:
            flag = next((o for o in param.opts if o.startswith("--")), None)
            if flag is None or flag == "--help":
                continue
            key = f"{path} {flag}"
            assert key in menu_module.CLI_PATHS or key in menu_module.MACHINE_ONLY, key
    for name in list(menu_module.CLI_PATHS.values()) + list(menu_module.ARGUMENT_PATHS.values()):
        assert name in menu_module.ACTIONS or menu_module.WINDOW_OBJECTS[name] in menu_module.ACTIONS, name
    assert set(menu_module.CLI_PATHS) & set(menu_module.MACHINE_ONLY) == set()


def test_every_text_key_of_the_knowledge_windows_exists_in_english_and_german():
    import re

    from conftest import REPO_ROOT
    from orchestrator.utils.localization import LocalizationManager
    table = LocalizationManager.TRANSLATIONS
    used = set()
    for name in ("knowledge_snapshots_window.py", "knowledge_sources_window.py", "community_dialogs.py",
                 "community_hub.py", "menus/knowledge.py"):
        source = (REPO_ROOT / "orchestrator" / "gui" / name).read_text(encoding="utf-8")
        used |= set(re.findall(r'(?:ui_text|_t)\(\s*f?"([a-z_.]+)"', source))
    used |= {f"{k}.tip" for k in ("zoo.details", "zoo.verify_installed", "zoo.verify_file", "zoo.export", "zoo.keys")}
    used |= {f"sources.filter.{k}" for k in ("any", "web", "pack", "docs", "other")}
    used |= {f"sources.carried.{k}" for k in ("full_text", "link_and_facts", "not_exported")}
    used |= {f"storage.reason.{k}" for k in ("engine_missing", "not_converted")}
    used |= {f"zoo.export.kind.{k}" for k in ("target", "recipe", "knowledge")}
    used |= {f"zoo.export.encrypt.{k}" for k in ("none", "keys", "passphrase")}
    used = {k for k in used if not k.endswith(".")}
    missing = {lang: sorted(k for k in used if k not in table[lang]) for lang in ("en", "de")}
    assert missing == {"en": [], "de": []}
    assert len(used) > 80
