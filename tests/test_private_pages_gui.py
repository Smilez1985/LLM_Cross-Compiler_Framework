"""
Tools > Knowledge Sources...: private pages by mouse (GUI parity with `llm-cli knowledge add-private` and `remove-private`).
Offscreen, on a real embedded Qdrant with the fake embedders: a saved file is added with the button or by dragging it onto
the window, the list marks it private, removing asks first (No by default) and never touches the file, and every text exists
in English and German. Documentation addresses only.
"""

import re
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from test_private_pages import ADDRESS, PAGE  # noqa: E402
from test_rag import make_rag  # noqa: E402,F401 - the fixture


def wait_until(qapp, condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def dialog(qapp, make_rag, tmp_path):
    from orchestrator.gui.knowledge_sources_window import KnowledgeSourcesDialog
    rag = make_rag()
    framework = SimpleNamespace(ensure_rag_manager=lambda: rag)
    window = KnowledgeSourcesDialog(framework, None, lang="en")
    wait_until(qapp, lambda: window._token is None)
    saved = tmp_path / "saved.html"
    saved.write_text(PAGE, encoding="utf-8")
    yield SimpleNamespace(window=window, rag=rag, saved=saved)
    window.close()
    window.deleteLater()


def settle(qapp, window):
    wait_until(qapp, lambda: not window._jobs and window._token is None)


def kinds(window):
    return {e["source"]: e["kind"] for e in window.entries}


class TestAdding:

    def test_the_button_adds_the_chosen_file_with_the_pages_address(self, qapp, dialog, monkeypatch):
        from orchestrator.gui import knowledge_sources_window as module
        window = dialog.window
        monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(dialog.saved), ""))
        window.address.setText(ADDRESS)
        window.btn_add.click()
        settle(qapp, window)
        assert kinds(window) == {"private:" + ADDRESS: "private"}
        assert "Added as a private page" in window.status.text()
        row = [window.table.item(0, c).text() for c in range(window.table.columnCount())]
        assert row[0] == "private:" + ADDRESS and row[1] == "Private pages" and row[6] == "not exported"
        assert "never shared" in row[7]

    def test_a_file_dragged_onto_the_window_is_added_too(self, qapp, dialog):
        from PySide6.QtCore import QMimeData, QUrl
        window = dialog.window
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(dialog.saved))])
        assert window.acceptDrops()
        from PySide6.QtGui import QDragEnterEvent, QDropEvent
        from PySide6.QtCore import QPoint, Qt
        enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
        window.dragEnterEvent(enter)
        assert enter.isAccepted()
        window.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier))
        settle(qapp, window)
        assert kinds(window) == {"private:saved.html": "private"}       # no address: the file's name, never its folder

    def test_a_drag_without_a_local_file_is_not_accepted(self, qapp, dialog):
        from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
        from PySide6.QtGui import QDragEnterEvent
        data = QMimeData()
        data.setUrls([QUrl("https://docs.example.com/a")])
        enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
        enter.ignore()
        dialog.window.dragEnterEvent(enter)
        assert not enter.isAccepted()

    def test_a_file_that_cannot_be_added_is_told_in_plain_words(self, qapp, dialog, monkeypatch, tmp_path):
        from orchestrator.gui import knowledge_sources_window as module
        told = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **kw: told.append((kind, text)))
        notes = tmp_path / "notes.txt"
        notes.write_text("text", encoding="utf-8")
        dialog.window.add_file(str(notes))
        wait_until(qapp, lambda: bool(told))
        assert told[0][0] == "warning" and "Only a PDF or an HTML page" in told[0][1]
        assert dialog.rag.list_sources() == []

    def test_the_kind_filter_lists_only_private_pages(self, qapp, dialog):
        window = dialog.window
        dialog.rag.ingest_document("https://docs.example.com/public", "# Public\n\nA public page about the NPU.",
                                   {"content_type": "html"}, outside=True)
        window.add_file(str(dialog.saved))
        settle(qapp, window)
        assert set(kinds(window).values()) == {"web", "private"}
        window.kind.setCurrentIndex(window.kind.findData("private"))
        settle(qapp, window)
        assert list(kinds(window).values()) == ["private"]


class TestRemoving:

    def test_removing_asks_first_and_no_is_the_default(self, qapp, dialog, monkeypatch):
        from orchestrator.gui import knowledge_sources_window as module
        window = dialog.window
        window.add_file(str(dialog.saved))
        settle(qapp, window)
        window.table.selectRow(0)
        asked = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: asked.append(text) or False)
        window.btn_remove.click()
        qapp.processEvents()
        assert len(asked) == 1 and "The file that was saved is not touched" in asked[0]
        assert dialog.rag.list_sources() != []                              # "No" changed nothing
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        window.btn_remove.click()
        settle(qapp, window)
        assert dialog.rag.list_sources() == [] and dialog.saved.exists()    # the saved file is never touched

    def test_only_a_private_page_can_be_removed_this_way(self, qapp, dialog, monkeypatch):
        from orchestrator.gui import knowledge_sources_window as module
        window = dialog.window
        dialog.rag.ingest_document("https://docs.example.com/public", "# Public\n\nA public page about the NPU.",
                                   {"content_type": "html"}, outside=True)
        window.reload()
        settle(qapp, window)
        told = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **kw: told.append(text))
        window.table.selectRow(0)
        window.btn_remove.click()
        assert told == ["Select a private page first."] and dialog.rag.list_sources() != []


class TestTexts:

    def test_every_new_text_exists_in_both_languages_with_the_same_placeholders(self):
        from orchestrator.utils import l10n_knowledge
        english, german = l10n_knowledge.TEXTS["en"], l10n_knowledge.TEXTS["de"]
        keys = [k for k in english if k.startswith("sources.private") or k == "sources.filter.private"]
        assert len(keys) >= 12
        for key in keys:
            assert key in german, key
            assert re.findall(r"\{(\w+)\}", english[key]) == re.findall(r"\{(\w+)\}", german[key]), key

    def test_the_window_comes_in_german_too(self, qapp, make_rag):
        from orchestrator.gui.knowledge_sources_window import KnowledgeSourcesDialog
        rag = make_rag()
        window = KnowledgeSourcesDialog(SimpleNamespace(ensure_rag_manager=lambda: rag), None, lang="de")
        wait_until(qapp, lambda: window._token is None)
        assert window.btn_add.text() == "Gespeicherte Seite hinzufügen ..."
        assert window.btn_remove.text() == "Private Seite entfernen ..."
        window.close()
        window.deleteLater()
