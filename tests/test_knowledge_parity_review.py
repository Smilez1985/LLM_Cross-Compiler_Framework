"""Review of the knowledge and Community Hub mouse paths (wave 5 chain "knowledge"; offscreen).

What the review found and these tests pin:

- Installing a pack from the Community Hub (an index entry or Import file...) moved it into place on the GUI thread:
  for a knowledge pack that is a snapshot of the knowledge base and the import of every page (minutes). The install
  now runs in the background like the download and the checks before it.
- Open pull request handed the pack to a public pull request at once; `llm-cli zoo export --pr` asks first ("Submit
  this pack to the public community repository?", "No" is the default). The export window asks the same now.
- With a Qdrant server set (setting qdrant_url) the knowledge base uses the server and never the local storage, but
  `llm-cli knowledge storage` and Tools > Knowledge Snapshots... named the local storage (and offered to convert it,
  which opens the server and converts nothing). Both name the server now, and the conversion is refused while it is
  set.
- A passphrase typed into Import file... or the export window stayed in the hidden window after Cancel or Close (the
  windows live as long as the Community Hub). It is cleared when the window closes.
"""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_edge_store import build_old_store
from test_knowledge_gui_parity import hub_for, import_dialog_answers, settle, spy, state_framework

pytestmark = pytest.mark.usefixtures("qapp")
SERVER = "https://qdrant.invalid:6333"


@pytest.fixture
def world(tmp_path):
    from test_zoo import World
    return World(tmp_path)


def answers(monkeypatch, *replies):
    """plain_box answered like a person, one reply per box; each box's text and whether "No" is its default."""
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import plain
    asked, replies = [], list(replies)

    def run(box):
        asked.append((box.text(), box.defaultButton() is box.button(QMessageBox.No)))
        reply = replies.pop(0) if replies else QMessageBox.No
        return int(reply.value) if hasattr(reply, "value") else int(reply)
    monkeypatch.setattr(plain, "_exec", run)
    return asked


# --- installing a pack runs in the background ------------------------------------------------------------------------

def test_installing_a_pack_file_moves_it_into_place_off_the_gui_thread(qapp, world, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import community_hub
    (tmp_path / "p.zip").write_bytes(world.target_pack())
    finished = spy(monkeypatch, world.cm, "finish_install")
    monkeypatch.setattr(community_hub.QMessageBox, "exec", lambda self: QMessageBox.Yes)
    import_dialog_answers(monkeypatch, (tmp_path / "p.zip", "", []))
    initialized, installed = [], []
    hub = hub_for(world)
    hub.framework_manager.target_manager = SimpleNamespace(
        initialize=lambda: initialized.append(threading.current_thread().name))
    hub.installed.connect(installed.append)
    try:
        settle(hub, qapp)
        hub.import_btn.click()
        settle(hub, qapp)
        assert finished, "the pack was installed"
        assert finished[0][2] != threading.current_thread().name, "the install runs off the GUI thread"
        assert initialized == [threading.current_thread().name], "the targets are read again on the GUI thread"
        assert installed == ["target"] and (world.root / "targets" / "demo-board").is_dir()
    finally:
        hub.close()


def test_a_failed_install_is_shown_and_nothing_is_left(qapp, world, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.gui import community_hub
    from test_knowledge_gui_parity import errors
    from orchestrator.Core.community_pack import PackError
    (tmp_path / "p.zip").write_bytes(world.target_pack())
    threads = []

    def refused(prepared):   # as finish_install refuses a folder that appeared meanwhile; its quarantine goes
        threads.append(threading.current_thread().name)
        world.cm.discard(prepared)
        raise PackError(f"{prepared.destination} already exists.")
    monkeypatch.setattr(world.cm, "finish_install", refused)
    monkeypatch.setattr(community_hub.QMessageBox, "exec", lambda self: QMessageBox.Yes)
    shown = errors(monkeypatch)
    import_dialog_answers(monkeypatch, (tmp_path / "p.zip", "", []))
    installed = []
    hub = hub_for(world)
    hub.installed.connect(installed.append)
    try:
        settle(hub, qapp)
        hub.import_btn.click()
        settle(hub, qapp)
        assert threads and threads[0] != threading.current_thread().name
        assert shown and "already exists" in shown[-1] and installed == []
        assert "Installed" not in hub.status_label.text()
        assert not (world.root / "targets" / "demo-board").exists()
    finally:
        hub.close()


# --- Open pull request asks first, as `zoo export --pr` does ---------------------------------------------------------

def test_open_pull_request_asks_first_with_no_as_the_default(qapp, world, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from orchestrator.Core import community_manager
    from orchestrator.gui import community_hub
    from orchestrator.utils.localization import ui_text
    from test_zoo import make_target
    make_target(world.root / "targets" / "my-board")
    submitted = []
    monkeypatch.setattr(community_manager, "submit_contribution",
                        lambda *a: submitted.append((a, threading.current_thread().name)) or "Pull request opened")
    asked = answers(monkeypatch, QMessageBox.No, QMessageBox.Yes)
    monkeypatch.setattr(community_hub, "current_language", lambda: "en")
    framework = SimpleNamespace(community_manager=world.cm, config=world.config)
    dialog = community_hub.CommunityExportDialog(framework, "target", "my-board")
    try:
        dialog.author_edit.setText("pat")
        dialog.create_btn.click()
        dialog._export_task.wait(60000)
        qapp.processEvents()
        assert dialog.result is not None and dialog.pr_btn.isEnabled()
        dialog.pr_btn.click()
        assert asked == [(ui_text("zoo.export.submit.ask", "en"), True)], "the CLI's question, No by default"
        assert asked[0][0] == "Submit this pack to the public community repository?"
        assert submitted == [], "No submits nothing"
        assert ui_text("zoo.export.submit.no", "en", path=str(dialog.result.path)) in dialog.output.toPlainText()
        assert dialog.pr_btn.isEnabled()
        dialog.pr_btn.click()
        dialog._task.wait(60000)
        qapp.processEvents()
        assert len(asked) == 2 and submitted and submitted[0][0][0] == dialog.result.path
        assert submitted[0][1] != threading.current_thread().name
        assert "Pull request opened" in dialog.output.toPlainText()
    finally:
        dialog.close()


# --- a Qdrant server: named, and no conversion offered ---------------------------------------------------------------

def server_config(state: Path, url: str = SERVER):
    return SimpleNamespace(user_state_dir=state, get=lambda k, d=None: url if k == "qdrant_url" else d)


def test_the_storage_status_names_a_configured_qdrant_server(tmp_path):
    from orchestrator.rag import migration
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    assert migration.server_configured(server_config(state))
    assert not migration.server_configured(server_config(state, "  "))
    assert not migration.server_configured(SimpleNamespace(get=lambda k, d=None: d))
    assert not migration.server_configured(None)
    status = migration.storage_status(state, server=True)
    assert status.used == "server" and status.folder == "" and not status.can_convert
    assert migration.convert_refusal(state, server=True) == "server"
    assert migration.convert_refusal(state) == "", "without a server the old storage is converted"
    lines = [line for _, line in migration.storage_lines(status)]
    assert lines[0] == "Storage: a Qdrant server (setting qdrant_url); the local storage is not used."
    assert f"Old storage folder: {state / 'qdrant'}" in lines[-1]
    # An open knowledge base still decides: what it names wins over the setting.
    assert migration.storage_status(state, used="old-local", server=True).used == "old-local"


def test_the_cli_names_the_server_and_does_not_convert_while_it_is_set(tmp_path, monkeypatch):
    from test_doctor_fixes import run_cli
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    opened = []
    fm = SimpleNamespace(config=server_config(state), ensure_rag_manager=lambda: opened.append(1))
    _, out = run_cli(fm, monkeypatch, "knowledge", "storage")
    assert "Storage: a Qdrant server (setting qdrant_url)" in out and "Storage: the storage of earlier" not in out
    result, out = run_cli(fm, monkeypatch, "knowledge", "storage", "convert")
    assert result.exit_code == 1 and opened == [], "nothing is opened or converted"
    assert "Qdrant server" in out and "qdrant_url" in out
    assert (state / "qdrant").is_dir() and not (state / "qdrant_edge").exists()


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_snapshots_window_names_the_server_and_offers_no_conversion(tmp_path, lang):
    from orchestrator.gui import knowledge_snapshots_window as window
    from orchestrator.utils.localization import ui_text
    state = tmp_path / "state"
    state.mkdir()
    build_old_store(state, extra_model=False, legacy=False)
    framework = state_framework(state, ensure_rag_manager=lambda: None)
    framework.config = server_config(state)
    dialog = window.KnowledgeSnapshotsDialog(state, framework=framework, lang=lang)
    try:
        assert dialog.storage_status.used == "server"
        assert dialog.storage.text().splitlines()[0] == ui_text("storage.used.server", lang)
        assert dialog.btn_convert.isHidden(), "converting would open the server and convert nothing"
        assert ui_text("storage.convert.offer", lang) not in dialog.storage.text()
    finally:
        dialog.close()


# --- a typed passphrase does not stay in a hidden window -------------------------------------------------------------

def test_the_import_window_forgets_the_passphrase_on_cancel(qapp):
    from orchestrator.gui.community_dialogs import ImportPackDialog
    dialog = ImportPackDialog(lang="en")
    dialog.passphrase.setText("correct horse battery staple")
    dialog.reject()
    assert dialog.passphrase.text() == "", "Cancel keeps no passphrase"
    dialog.show()   # the window's close button
    dialog.passphrase.setText("correct horse battery staple")
    dialog.close()
    assert dialog.passphrase.text() == "" and not dialog.isVisible()
    dialog.show()   # Import: take() hands the passphrase over once and empties the field
    dialog.passphrase.setText("correct horse battery staple")
    dialog.accept()
    assert dialog.passphrase.text() == "correct horse battery staple", "kept until take() reads it"
    assert dialog.take()[1] == "correct horse battery staple" and dialog.passphrase.text() == ""


def test_the_export_window_forgets_the_passphrase_when_it_closes(qapp, world):
    from orchestrator.gui import community_hub
    framework = SimpleNamespace(community_manager=world.cm, config=world.config)
    dialog = community_hub.CommunityExportDialog(framework, "target", "my-board")
    dialog.encrypt_combo.setCurrentIndex(dialog.encrypt_combo.findData("passphrase"))
    dialog.passphrase_edit.setText("a long enough passphrase")
    dialog.passphrase2_edit.setText("a long enough passphrase")
    dialog.reject()
    assert dialog.passphrase_edit.text() == "" and dialog.passphrase2_edit.text() == ""
