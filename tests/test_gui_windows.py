"""GUI windows that were not reachable: the Hugging Face browser now opens from Tools; the benchmark window,
which could not work (no such DockerManager attribute, an image that does not exist, a placeholder model path),
was removed."""

import threading
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")


class FakeModels:
    def __init__(self):
        self.downloads = []

    def search_huggingface_models(self, query, limit=20):
        return [{"id": "Qwen/Qwen3.5-0.8B", "downloads": 10, "likes": 2, "gated": False, "license": "apache-2.0"},
                {"id": "meta-llama/Llama-3.2-1B", "downloads": 20, "likes": 3, "gated": True, "license": "llama3.2"}]

    def list_repo_files(self, repo_id, token=None):
        return ["config.json", "model-Q4_K_M.gguf"]

    def download_file(self, repo_id, filename, token=None):
        self.downloads.append((repo_id, filename, token))
        return f"/models/{filename}"

    def check_license(self, model_id):
        return {"license": "apache-2.0", "is_restrictive": False, "message": "ok"}


@pytest.mark.usefixtures("qapp")
class TestHuggingFaceBrowser:

    def test_search_filter_and_download_with_the_current_model_manager(self, monkeypatch):
        from orchestrator.gui import huggingface_window as hf
        models = FakeModels()
        win = hf.HuggingFaceWindow(SimpleNamespace(model_manager=models, secrets_manager=None))
        worker = hf.SearchWorker(models, "qwen")
        worker.finished.connect(win.on_search_finished)
        worker.run()
        assert win.table.rowCount() == 2
        win.filter_combo.setCurrentIndex(2)  # gated only
        assert [win.table.item(r, 0).text() for r in range(win.table.rowCount())] == ["meta-llama/Llama-3.2-1B"]

        files = hf.FileSelectionDialog("Qwen/Qwen3.5-0.8B", models.list_repo_files("x"), win)
        assert files.list_widget.item(0).text() == "model-Q4_K_M.gguf", "model files first"
        download = hf.DownloadWorker(models, "Qwen/Qwen3.5-0.8B", "model-Q4_K_M.gguf")
        done = []
        download.finished.connect(done.append)
        download.run()
        assert done == ["/models/model-Q4_K_M.gguf"] and models.downloads[0][:2] == ("Qwen/Qwen3.5-0.8B",
                                                                                    "model-Q4_K_M.gguf")

    def test_the_tools_menu_opens_it(self, qapp, tmp_path, monkeypatch, memory_keyring):
        from orchestrator.Core.framework import FrameworkManager
        from orchestrator.gui import main_window
        from orchestrator.gui.huggingface_window import HuggingFaceWindow

        (tmp_path / "configs").mkdir()

        class TmpFramework(FrameworkManager):
            def __init__(self, *args, **kwargs):
                super().__init__(str(tmp_path / "configs"))
                self.info.installation_path = tmp_path

        monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
        monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions",
                            lambda self, first_run=False: None)
        win = main_window.MainOrchestrator(REPO_ROOT)
        qapp.processEvents()  # the first-run permissions call is queued; it must not fire in a later test
        try:
            # wave 6: the entry is found by its object name (the titles are translated, with mnemonics)
            tools = next(m.menu() for m in win.menuBar().actions() if m.menu().objectName() == "menu_tools")
            action = next(a for a in tools.actions() if a.text() == "Hugging Face Model Browser...")
            assert action.objectName() == "act_tools_hf_browser"
            action.trigger()
            assert isinstance(win.hf_browser, HuggingFaceWindow) and win.hf_browser.isVisible()
            assert win.hf_browser.model_manager is win.framework.model_manager
            action.trigger()  # a second click shows the same window
            assert len(win.findChildren(HuggingFaceWindow)) == 1
        finally:
            win.close()


class BlockingSearch(FakeModels):
    """Network calls that wait until the test releases them, and remember the thread they ran on."""

    def __init__(self, licence=None):
        super().__init__()
        self.release = threading.Event()
        self.licence = licence or {"license": "apache-2.0", "is_restrictive": False, "message": "ok"}
        self.licence_threads = []

    def search_huggingface_models(self, query, limit=20):
        self.release.wait(10)
        return super().search_huggingface_models(query, limit)

    def check_license(self, model_id):
        self.licence_threads.append(threading.current_thread() is threading.main_thread())
        return self.licence


def wait_for(*workers):
    for worker in workers:
        if worker is not None:
            assert worker.wait(10000), "worker did not finish"


@pytest.mark.usefixtures("qapp")
class TestHuggingFaceBrowserThreads:
    """The browser's network calls run in worker threads, and a running thread is never replaced: PySide6 aborts
    the whole GUI when a QThread object is destroyed while its thread still runs."""

    def window(self, models):
        from orchestrator.gui import huggingface_window as hf
        win = hf.HuggingFaceWindow(SimpleNamespace(model_manager=models, secrets_manager=None))
        win.populate_table(FakeModels().search_huggingface_models("x"))
        return win

    def test_the_licence_check_does_not_block_the_gui_thread(self, qapp, monkeypatch):
        models = BlockingSearch()
        win = self.window(models)
        opened = []
        monkeypatch.setattr(win, "show_file_selection", lambda *args: opened.append(args))
        win.on_download_request(0, 0)
        wait_for(getattr(win, "list_worker", None))
        qapp.processEvents()
        assert models.licence_threads == [False], "check_license ran on the GUI thread"
        assert opened and opened[0][:2] == ("Qwen/Qwen3.5-0.8B", ["config.json", "model-Q4_K_M.gguf"])

    def test_enter_during_a_search_keeps_the_running_thread(self, qapp):
        models = BlockingSearch()
        win = self.window(models)
        win.search_edit.setText("qwen")
        win.start_search()
        first = win.worker
        win.start_search()  # Enter in the search field: the button is disabled, the field is not
        second = win.worker
        models.release.set()
        wait_for(first, second)
        qapp.processEvents()
        assert second is first

    def test_a_second_download_request_waits_for_the_running_one(self, qapp, monkeypatch):
        from PySide6.QtCore import QThread
        models = BlockingSearch()
        win = self.window(models)
        release = threading.Event()

        class Running(QThread):
            def run(self):
                release.wait(10)

        monkeypatch.setattr(win, "show_file_selection", lambda *args: None)
        busy = Running()
        win.dl_worker = busy
        busy.start()
        try:
            win.on_download_request(0, 0)
            assert getattr(win, "list_worker", None) is None and models.licence_threads == []
            assert win.dl_worker is busy
        finally:
            release.set()
            wait_for(busy, getattr(win, "list_worker", None))
            qapp.processEvents()

    def test_the_licence_warning_is_plain_text(self, qapp, monkeypatch):
        from PySide6.QtCore import Qt
        from orchestrator.gui import huggingface_window as hf
        models = BlockingSearch({"license": "cc-by-nc", "is_restrictive": True,
                                 "message": "<a href='https://example.invalid'>cc-by-nc</a> is restrictive"})
        win = self.window(models)
        shown, dialogs = [], []
        monkeypatch.setattr(hf.QMessageBox, "exec", lambda box: shown.append(box.textFormat()) or hf.QMessageBox.No)
        monkeypatch.setattr(hf.QMessageBox, "warning", staticmethod(
            lambda *args, **kwargs: shown.append("rich text allowed") or hf.QMessageBox.No))
        monkeypatch.setattr(hf, "FileSelectionDialog", lambda *args, **kwargs: dialogs.append(args))
        win.on_download_request(0, 0)
        wait_for(getattr(win, "list_worker", None))
        qapp.processEvents()
        assert shown == [Qt.PlainText], "the licence name comes from the model card"
        assert dialogs == [], "declined: no file selection"


@pytest.mark.usefixtures("qapp")
class TestVaultWording:
    """Secrets go to the encrypted credential vault (its key may be in the keyring); the GUI said 'Keyring'."""

    @pytest.mark.parametrize("language, expected", [
        ("en", "This value is stored in the encrypted credential vault."),
        ("de", "Dieser Wert wird im verschlüsselten Tresor für Zugangsdaten gespeichert."),
    ])
    def test_secret_dialog_names_the_vault(self, monkeypatch, language, expected):
        from PySide6.QtWidgets import QLabel
        from orchestrator.gui import dialogs
        from orchestrator.utils import localization
        monkeypatch.setattr(dialogs, "get_i18n", lambda: localization.LocalizationManager(language))
        sm = SimpleNamespace(get_secret=lambda key: None, set_secret=lambda key, value: True)
        dlg = dialogs.SecretInputDialog(SimpleNamespace(secrets_manager=sm), "openai_api_key", "OpenAI API Key")
        texts = [label.text() for label in dlg.findChildren(QLabel)]
        assert expected in texts and not any("Keyring" in t for t in texts)

    def test_no_gui_message_says_the_value_is_in_the_keyring(self):
        sources = [REPO_ROOT / "orchestrator" / "gui" / name for name in ("dialogs.py", "huggingface_window.py")]
        sources.append(REPO_ROOT / "orchestrator" / "utils" / "localization.py")
        for path in sources:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or '"' not in stripped:
                    continue
                assert not ("eyring" in stripped and any(w in stripped.lower() for w in ("stored", "store ", "save"))), \
                    f"{path.name}: {stripped}"
