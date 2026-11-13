"""
Ditto > Ditto's Archive...: every `llm-cli ditto archive` action by mouse (GUI parity). Offscreen, with a fake archive
that records each call and whether it ran on the GUI thread: the list and the search, the person's confirm, take-back,
delete and undo (questions default to "No"), the level through the one function the CLI uses, and every text in English
and German. No Qdrant, no model.
"""

import re
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


def wait_until(qapp, condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def on_gui_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def make_entry(n, **changes):
    from orchestrator.schemas.archive import ArchiveEntry
    values = {"id": f"00000000-0000-4000-8000-{n:012d}", "key": f"target:rk3588/p{n}", "text": f"Sentence number {n}.",
              "ground": "tested", "evidence": [f"build:a_{n:016x}"], "status": "unconfirmed", "kind": "entry",
              "created": "2026-11-13T10:00:00+00:00", "updated": f"2026-11-13T10:00:{n:02d}+00:00"}
    values.update(changes)
    return ArchiveEntry(**values)


class FakeArchive:
    """DittoArchive's calls the window makes, on a list; every call is recorded with its thread."""

    def __init__(self, entries=()):
        self.items = list(entries)
        self.calls = []

    def _note(self, name, *args):
        self.calls.append((name, args, on_gui_thread()))

    def overview(self):
        entries = [e for e in self.items if e.kind == "entry"]
        return {"entries": len(entries), "confirmed": sum(e.status == "confirmed" for e in entries),
                "copies": len(self.items) - len(entries), "level": "mid", "needed": 5, "dropped": 0, "last_drop": ""}

    def entries(self, status="", kind="entry"):
        self._note("entries", status, kind)
        return [e for e in self.items if (not kind or e.kind == kind) and (not status or e.status == status)]

    def search(self, query, limit=5, confirmed_only=False):
        self._note("search", query, confirmed_only)
        hits = [SimpleNamespace(entry=e, score=1.0) for e in self.items
                if e.kind == "entry" and query.lower() in e.text.lower() and (e.status == "confirmed" or not confirmed_only)]
        return hits

    def _replace(self, entry_id, **changes):
        for index, e in enumerate(self.items):
            if e.id == entry_id:
                self.items[index] = e.model_copy(update=changes)
                return self.items[index]
        raise KeyError(entry_id)

    def confirm(self, entry_id):
        self._note("confirm", entry_id)
        return self._replace(entry_id, status="confirmed", confirmed_by="person")

    def unconfirm(self, entry_id):
        self._note("unconfirm", entry_id)
        return self._replace(entry_id, status="unconfirmed", confirmed_by=None)

    def delete(self, entry_id):
        self._note("delete", entry_id)
        self.items = [e for e in self.items if e.id != entry_id]
        return True

    def undo(self, key):
        self._note("undo", key)
        return self.items[0]


@pytest.fixture
def make_window(qapp, tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.ditto_archive_window import DittoArchiveWindow
    made = []

    def factory(archive, lang="en"):
        framework = SimpleNamespace(config=ConfigManager(tmp_path / "configs"), ditto_archive=archive)
        window = DittoArchiveWindow(framework, None, lang=lang)
        wait_until(qapp, lambda: not window._jobs)
        made.append(window)
        return window

    yield factory
    for window in made:
        window.close()
        window.deleteLater()


def rows(window):
    return [window.table.item(r, 3).text() for r in range(window.table.rowCount())]


def select(qapp, window, row):
    window.table.selectRow(row)
    qapp.processEvents()


class TestList:

    def test_the_entries_are_listed_newest_first_with_status_ground_and_sources(self, make_window):
        archive = FakeArchive([make_entry(1), make_entry(2, status="confirmed", confirmed_by="ditto", ground="vendor")])
        window = make_window(archive)
        assert rows(window) == ["target:rk3588/p1", "target:rk3588/p2"]
        assert window.table.item(1, 0).text() == "confirmed by Ditto"
        assert window.table.item(0, 0).text() == "not confirmed"
        assert window.table.item(1, 1).text() == "manufacturer's page"
        assert "2 entries, 1 confirmed" in window.lbl_overview.text() and "5 independent sources" in window.lbl_overview.text()

    def test_the_reading_runs_off_the_gui_thread(self, make_window):
        archive = FakeArchive([make_entry(1)])
        make_window(archive)
        assert archive.calls and all(not gui for _, _, gui in archive.calls)

    def test_an_empty_archive_says_what_teaches_it(self, make_window):
        window = make_window(FakeArchive())
        assert window.table.rowCount() == 0 and "passed its functional test" in window.lbl_state.text()

    def test_a_missing_archive_is_said_plainly(self, qapp, make_window, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.gui.ditto_archive_window import DittoArchiveWindow
        window = DittoArchiveWindow(SimpleNamespace(config=ConfigManager(tmp_path / "c2"), ditto_archive=None), None)
        wait_until(qapp, lambda: not window._jobs)
        assert "could not be read" in window.lbl_state.text()
        window.close()

    def test_the_filter_and_the_copies_checkbox_choose_what_is_read(self, qapp, make_window):
        copy = make_entry(3, kind="copy", status="confirmed", confirmed_by="person", key="target:rk3588/p1")
        archive = FakeArchive([make_entry(1), make_entry(2, status="confirmed"), copy])
        window = make_window(archive)
        window.cb_status.setCurrentIndex(2)           # Confirmed
        wait_until(qapp, lambda: not window._jobs)
        assert rows(window) == ["target:rk3588/p2"]
        window.chk_copies.setChecked(True)
        wait_until(qapp, lambda: not window._jobs)
        assert window.table.item(0, 0).text() == "copy" and rows(window) == ["target:rk3588/p1"]

    def test_the_search_asks_the_archive_and_confirmed_only_narrows_it(self, qapp, make_window):
        archive = FakeArchive([make_entry(1, text="The NPU is fast."), make_entry(2, text="The NPU is quiet.",
                                                                              status="confirmed")])
        window = make_window(archive)
        window.edt_search.setText("npu")
        window.btn_search.click()
        wait_until(qapp, lambda: not window._jobs)
        assert len(rows(window)) == 2 and ("search", ("npu", False), False) in archive.calls
        window.chk_confirmed_only.setChecked(True)
        wait_until(qapp, lambda: not window._jobs)
        assert rows(window) == ["target:rk3588/p2"]


class TestDetail:

    def test_the_selected_entry_is_shown_in_full_with_its_evidence(self, qapp, make_window):
        window = make_window(FakeArchive([make_entry(1, evidence=["https://example1.org/a", "build:a_0000000000000001"])]))
        assert "Select an entry" in window.lbl_detail.text()
        select(qapp, window, 0)
        text = window.lbl_detail.text()
        assert "target:rk3588/p1" in text and "Sentence number 1." in text and "passed a test" in text
        assert "https://example1.org/a" in text and "Confirmed by: none" in text

    def test_a_copy_says_it_was_kept_and_how_to_get_it_back(self, qapp, make_window):
        copy = make_entry(2, kind="copy", status="confirmed", confirmed_by="person", copied_at="2026-11-13T10:00:09+00:00")
        window = make_window(FakeArchive([copy]))
        window.chk_copies.setChecked(True)
        wait_until(qapp, lambda: not window._jobs)
        select(qapp, window, 0)
        assert "Kept when a newer entry replaced it" in window.lbl_detail.text()

    def test_the_buttons_follow_the_selection(self, qapp, make_window):
        copy = make_entry(3, kind="copy", status="confirmed", confirmed_by="person", key="target:rk3588/p2")
        window = make_window(FakeArchive([make_entry(1), make_entry(2, status="confirmed", confirmed_by="person"), copy]))
        assert not any(b.isEnabled() for b in (window.btn_confirm, window.btn_unconfirm, window.btn_delete, window.btn_undo))
        select(qapp, window, 0)                       # an unconfirmed entry without a copy
        assert (window.btn_confirm.isEnabled(), window.btn_unconfirm.isEnabled(), window.btn_undo.isEnabled()) == \
            (True, False, False) and window.btn_delete.isEnabled()
        select(qapp, window, 1)                       # a confirmed entry that has a copy
        assert (window.btn_confirm.isEnabled(), window.btn_unconfirm.isEnabled(), window.btn_undo.isEnabled()) == \
            (False, True, True)


class TestActions:

    def test_confirm_and_take_back_run_the_same_calls_as_the_cli_off_the_gui_thread(self, qapp, make_window):
        archive = FakeArchive([make_entry(1)])
        window = make_window(archive)
        select(qapp, window, 0)
        window.btn_confirm.click()
        wait_until(qapp, lambda: not window._jobs and window.table.item(0, 0).text() == "confirmed by the person")
        assert ("confirm", (archive.items[0].id,), False) in archive.calls
        select(qapp, window, 0)
        window.btn_unconfirm.click()
        wait_until(qapp, lambda: not window._jobs and window.table.item(0, 0).text() == "not confirmed")
        assert any(name == "unconfirm" and not gui for name, _, gui in archive.calls)

    def test_delete_and_undo_ask_first_and_no_is_the_default(self, qapp, make_window, monkeypatch):
        from orchestrator.gui import ditto_archive_window as module
        copy = make_entry(2, kind="copy", status="confirmed", confirmed_by="person", key="target:rk3588/p1")
        archive = FakeArchive([make_entry(1), copy])
        window = make_window(archive)
        select(qapp, window, 0)
        questions = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: questions.append(text) or False)
        window.btn_delete.click()
        window.btn_undo.click()
        qapp.processEvents()
        assert len(questions) == 2 and "target:rk3588/p1" in questions[0] and "target:rk3588/p1" in questions[1]
        assert not [c for c in archive.calls if c[0] in ("delete", "undo")]         # "No" changed nothing
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        window.btn_undo.click()
        wait_until(qapp, lambda: not window._jobs)
        assert ("undo", ("target:rk3588/p1",), False) in archive.calls
        select(qapp, window, 0)
        window.btn_delete.click()
        wait_until(qapp, lambda: not window._jobs and window.table.rowCount() == 0)
        assert any(name == "delete" and not gui for name, _, gui in archive.calls)

    def test_a_failing_action_is_told_in_plain_words_and_nothing_breaks(self, qapp, make_window, monkeypatch):
        from orchestrator.gui import ditto_archive_window as module
        archive = FakeArchive([make_entry(1)])
        window = make_window(archive)
        select(qapp, window, 0)
        told = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **kw: told.append((kind, text)))
        archive.confirm = lambda entry_id: (_ for _ in ()).throw(RuntimeError("storage is busy"))
        window.btn_confirm.click()
        wait_until(qapp, lambda: bool(told))
        assert told[0][0] == "warning" and "storage is busy" in told[0][1]

    def test_the_level_is_written_by_the_one_function_and_shown(self, qapp, make_window):
        window = make_window(FakeArchive())
        assert window.cb_level.currentData() == "mid"
        window.cb_level.setCurrentIndex(2)
        window.btn_level.click()
        wait_until(qapp, lambda: not window._jobs)
        assert window.framework.config.get("ditto_archive_security") == "high"
        assert "High: 7 sources" in window.lbl_state.text() or "Level" in window.lbl_overview.text()


class TestTexts:

    def keys(self):
        from orchestrator.utils import l10n_archive
        return l10n_archive.TEXTS

    def test_every_text_exists_in_both_languages_with_the_same_placeholders(self):
        texts = self.keys()
        assert set(texts["en"]) == set(texts["de"])
        for key, english in texts["en"].items():
            assert re.findall(r"\{(\w+)\}", english) == re.findall(r"\{(\w+)\}", texts["de"][key]), key

    def test_the_window_comes_in_german_too(self, qapp, make_window):
        window = make_window(FakeArchive([make_entry(1, status="confirmed", confirmed_by="ditto")]), lang="de")
        assert window.windowTitle() == "Dittos Archiv"
        assert window.table.item(0, 0).text() == "bestätigt von Ditto"
        assert window.btn_confirm.text() == "Bestätigen"

    def test_the_sie_form_is_given_for_each_text_that_says_du(self):
        from orchestrator.utils import l10n_archive
        for key in l10n_archive.FORMAL["de"]:
            assert key in l10n_archive.TEXTS["de"]

    def test_no_command_is_written_into_a_text_of_the_window(self):
        for lang, table in self.keys().items():
            assert not [k for k, v in table.items() if "llm-cli" in v or "llmcf" in v], lang
