"""GUI dialog tests (offscreen Qt) against a real ConfigManager."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from orchestrator.Core.config_manager import ConfigManager
from orchestrator.gui import dialogs
from orchestrator.gui.dialogs import AIConfigurationDialog, URLInputDialog


class FakeSecrets:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get_secret(self, key):
        return self.store.get(key)

    def set_secret(self, key, value):
        self.store[key] = value
        return True


class FakeDitto:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1


class FakeFramework:
    def __init__(self, config, secrets=None):
        self.config = config
        self.secrets_manager = secrets or FakeSecrets()
        self.ditto_manager = FakeDitto()
        self.rag_requests = 0

    def ensure_rag_manager(self):
        self.rag_requests += 1


@pytest.fixture
def message_boxes(monkeypatch):
    shown = []

    class RecordingMessageBox:
        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", title))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", title))

        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", title))

    monkeypatch.setattr(dialogs, "QMessageBox", RecordingMessageBox)
    return shown


@pytest.fixture(autouse=True)
def model_lists(monkeypatch):
    """No network: the live model list comes from here (default: unavailable)."""
    calls, answers = [], {}

    def lister(config, secrets, provider):
        calls.append(SimpleNamespace(provider=provider, model=config.get("ai_model"),
                                     base_url=config.get("ai_base_url"), offline=config.get("offline_mode"),
                                     key=secrets.get_secret("anthropic_api_key")))
        result = answers.get(provider, RuntimeError("no network in tests"))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(AIConfigurationDialog, "model_lister", staticmethod(lister))
    return SimpleNamespace(calls=calls, answers=answers)


@pytest.fixture
def framework(tmp_path):
    return FakeFramework(ConfigManager(tmp_path))


def select_provider(dlg, provider_id):
    dlg.cb_provider.setCurrentIndex(dlg.cb_provider.findData(provider_id))


def wait_for(qapp, condition, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


class TestAIConfigurationDialog:

    def test_providers_come_from_the_catalog(self, qapp, framework):
        from orchestrator.llm import load_catalog
        dlg = AIConfigurationDialog(framework)
        catalog = load_catalog()
        items = {dlg.cb_provider.itemData(i): dlg.cb_provider.itemText(i) for i in range(dlg.cb_provider.count())}
        assert items == {pid: preset.label for pid, preset in catalog.providers.items()}

    def test_loads_saved_selection_including_old_provider_names(self, qapp, framework):
        framework.config.set("ai_provider", "Anthropic")
        framework.config.set("ai_model", "claude-sonnet-5-5")

        dlg = AIConfigurationDialog(framework)

        assert dlg.cb_provider.currentData() == "anthropic"
        assert dlg.cb_model.currentText() == "claude-sonnet-5-5"
        assert dlg.txt_api_key.isEnabled()
        assert not dlg.txt_base_url.isEnabled()

    def test_default_model_of_another_provider_is_not_shown(self, qapp, framework):
        framework.config.set("ai_provider", "anthropic")  # ai_model keeps the schema default (an OpenAI model)
        dlg = AIConfigurationDialog(framework)
        assert dlg.cb_model.currentText() == "claude-opus-5-5"
        assert dlg.cb_model.findText("claude-haiku-5-5") >= 0

    def test_cloud_provider_requires_api_key(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openai")

        dlg._save_config()

        assert message_boxes == [("warning", "Missing API Key")]
        assert dlg.result() == 0
        assert dlg.get_config() == {}
        assert framework.ditto_manager.reloads == 0

    def test_save_writes_keyring_config_and_reloads_ditto(self, qapp, framework, tmp_path, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.cb_model.setCurrentText("claude-opus-5-5")
        dlg.txt_api_key.setText("sk-ant-test-key-0123456789abcdef")
        dlg.cb_effort.setCurrentIndex(dlg.cb_effort.findData("high"))
        dlg.cb_security.setCurrentIndex(dlg.cb_security.findData("BALANCED"))
        dlg.txt_fallback.setText("ollama:qwen3.5:4b, local")
        dlg.chk_enable_rag.setChecked(True)

        dlg._save_config()

        assert message_boxes == []
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.secrets_manager.store["anthropic_api_key"] == "sk-ant-test-key-0123456789abcdef"
        assert framework.rag_requests == 1
        assert framework.ditto_manager.reloads == 1

        reloaded = ConfigManager(tmp_path)
        assert reloaded.get("ai_provider") == "anthropic"
        assert reloaded.get("ai_model") == "claude-opus-5-5"
        assert reloaded.get("ai_effort") == "high"
        assert reloaded.get("ai_security_level") == "BALANCED"
        assert reloaded.get("ai_fallback_chain") == ["ollama:qwen3.5:4b", "local"]
        assert reloaded.get("enable_rag_knowledge") is True
        assert "sk-ant-test-key" not in (tmp_path / "config.yml").read_text()

        result = dlg.get_config()
        assert result["provider"] == "anthropic" and "api_key" not in result
        assert "sk-ant-test-key" not in repr(result)

    def test_saved_settings_are_shown_again(self, qapp, framework):
        framework.config.set("ai_provider", "ollama")
        framework.config.set("ai_effort", "low")
        framework.config.set("ai_security_level", "OFF")
        framework.config.set("ai_fallback_chain", ["local"])
        dlg = AIConfigurationDialog(framework)
        assert dlg.cb_effort.currentData() == "low"
        assert dlg.cb_security.currentData() == "OFF"
        assert dlg.txt_fallback.text() == "local"

    def test_stored_key_is_reused(self, qapp, tmp_path, message_boxes):
        fw = FakeFramework(ConfigManager(tmp_path), FakeSecrets({"openai_api_key": "sk-stored-0123456789abcdefghij"}))
        dlg = AIConfigurationDialog(fw)
        select_provider(dlg, "openai")

        assert "Stored" in dlg.txt_api_key.placeholderText()
        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert fw.secrets_manager.store == {"openai_api_key": "sk-stored-0123456789abcdefghij"}

    def test_ollama_needs_no_key(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")

        assert not dlg.txt_api_key.isEnabled()
        assert dlg.txt_base_url.isEnabled()

        dlg.txt_base_url.setText("http://192.168.1.20:11434/v1")
        dlg._save_config()

        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.config.get("ai_base_url") == "http://192.168.1.20:11434/v1"

    def test_custom_endpoint_requires_base_url(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "custom")
        dlg.cb_model.setCurrentText("my-model")

        dlg._save_config()

        assert message_boxes == [("warning", "Missing Base URL")]
        assert dlg.result() == 0

    def test_offline_mode_accepts_only_local_providers(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openai")
        dlg.txt_api_key.setText("sk-proj-0123456789abcdefghijkl")
        dlg.chk_offline.setChecked(True)

        dlg._save_config()
        assert message_boxes == [("warning", "Offline Mode")] and dlg.result() == 0

        select_provider(dlg, "ollama")
        dlg._save_config()
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.config.get("offline_mode") is True and framework.config.get("ai_provider") == "ollama"

    def test_switching_to_cloud_provider_drops_old_base_url(self, qapp, framework, tmp_path, message_boxes):
        custom = AIConfigurationDialog(framework)
        select_provider(custom, "custom")
        custom.cb_model.setCurrentText("my-model")
        custom.txt_base_url.setText("https://third-party-proxy.example.com/v1")
        custom._save_config()
        assert framework.config.get("ai_base_url") == "https://third-party-proxy.example.com/v1"

        dlg = AIConfigurationDialog(framework)
        assert dlg.txt_base_url.text() == "https://third-party-proxy.example.com/v1"
        select_provider(dlg, "openai")
        dlg.cb_model.setCurrentText("gpt-6.1-sol")
        dlg.txt_api_key.setText("sk-proj-0123456789abcdefghijkl")
        dlg._save_config()

        assert message_boxes == []
        assert dlg.get_config()["base_url"] is None
        assert ConfigManager(tmp_path).get("ai_base_url") == ""

    def test_custom_endpoint_key_is_optional_and_stored_separately(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "custom")
        dlg.cb_model.setCurrentText("my-model")
        dlg.txt_base_url.setText("http://192.168.1.20:8000/v1")
        assert dlg.txt_api_key.isEnabled()

        dlg._save_config()
        assert dlg.result() == dialogs.QDialog.Accepted
        assert framework.secrets_manager.store == {}

        dlg2 = AIConfigurationDialog(framework)
        assert dlg2.cb_provider.currentData() == "custom"
        dlg2.txt_api_key.setText("custom-secret")
        dlg2._save_config()
        assert framework.secrets_manager.store == {"custom_api_key": "custom-secret"}

    def test_invalid_base_url_is_rejected(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        dlg.txt_base_url.setText("localhost:11434")

        dlg._save_config()

        assert message_boxes == [("warning", "Invalid Base URL")]

    def test_unknown_fallback_provider_is_rejected(self, qapp, framework, message_boxes):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        dlg.txt_fallback.setText("local, skynet:model")

        dlg._save_config()

        assert message_boxes == [("warning", "Invalid Fallback Chain")]

    def test_live_model_list_fills_the_combo(self, qapp, framework, model_lists):
        model_lists.answers["anthropic"] = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-new-model"]
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        dlg.txt_api_key.setText("sk-ant-typed-0123456789abcdef")
        dlg.cb_model.setCurrentText("my-pinned-model")
        dlg._refresh_models()

        assert wait_for(qapp, lambda: dlg.cb_model.findText("claude-new-model") >= 0)
        assert dlg.cb_model.currentText() == "my-pinned-model", "a typed model id stays"
        last = model_lists.calls[-1]
        assert (last.provider, last.model, last.key) == ("anthropic", "my-pinned-model",
                                                          "sk-ant-typed-0123456789abcdef")
        assert "3 models" in dlg.lbl_models.text()
        assert framework.secrets_manager.store == {}, "listing models stores nothing"

    def test_failed_model_list_keeps_free_text(self, qapp, framework, model_lists):
        from orchestrator.llm import AuthFailed
        model_lists.answers["openrouter"] = AuthFailed("OpenRouter rejected the credentials.")
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "openrouter")
        dlg.cb_model.setCurrentText("vendor/some-model")

        assert wait_for(qapp, lambda: "unavailable" in dlg.lbl_models.text())
        assert "rejected the credentials" in dlg.lbl_models.text()
        assert dlg.cb_model.currentText() == "vendor/some-model"

    def test_unsaved_choices_are_used_for_the_model_list(self, qapp, framework, model_lists):
        model_lists.answers["ollama"] = ["qwen3.5:9b"]
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "ollama")
        dlg.txt_base_url.setText("http://192.168.1.20:11434/v1")
        dlg.chk_offline.setChecked(True)
        dlg._refresh_models()
        assert wait_for(qapp, lambda: dlg.cb_model.findText("qwen3.5:9b") >= 0)
        assert (model_lists.calls[-1].base_url, model_lists.calls[-1].offline) == ("http://192.168.1.20:11434/v1",
                                                                                     True)
        assert framework.config.get("ai_base_url") == "", "nothing is saved before Save"

    def test_sign_in_placeholder(self, qapp, framework):
        dlg = AIConfigurationDialog(framework)
        select_provider(dlg, "anthropic")
        assert dlg.btn_sign_in.text() == "Sign in..." and not dlg.btn_sign_in.isEnabled()
        assert dlg._sign_in() is None

    def test_works_without_framework(self, qapp, message_boxes):
        dlg = AIConfigurationDialog(None)
        select_provider(dlg, "ollama")

        dlg._save_config()

        assert dlg.get_config()["provider"] == "ollama"


class TestURLInputDialog:

    def test_get_urls_ignores_blank_lines(self, qapp):
        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("  https://docs.radxa.com/ \n\n\nhttps://wiki.example.org/\n")
        assert dlg.get_urls() == ["https://docs.radxa.com/", "https://wiki.example.org/"]

    @pytest.mark.parametrize("bad_url", [
        "not-a-url", "javascript:alert(1)", "ftp://files.example.com/", "file:///etc/passwd", "../../../etc/passwd",
    ])
    def test_rejects_non_http_urls(self, qapp, message_boxes, bad_url):
        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText(f"https://docs.radxa.com/\n{bad_url}")

        dlg.validate_and_accept()

        assert message_boxes == [("warning", "Invalid URLs")]
        assert dlg.result() == 0

    def test_accepts_http_urls(self, qapp, message_boxes):
        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("https://docs.radxa.com/\nhttp://example.com/docs")

        dlg.validate_and_accept()

        assert message_boxes == []
        assert dlg.result() == dialogs.QDialog.Accepted

    def test_empty_input_is_rejected(self, qapp, message_boxes):
        dlg = URLInputDialog()
        dlg.validate_and_accept()
        assert message_boxes == [("warning", "No URLs")]

    def test_default_options(self, qapp):
        assert URLInputDialog().get_options() == {
            "max_depth": 2, "respect_robots": True, "download_pdfs": True, "request_delay": 1,
        }


class TestCrawlOptionWiring:
    """Options chosen in URLInputDialog apply to that crawl only."""

    def test_options_reach_the_crawler_without_changing_global_config(self, qapp, tmp_path):
        from orchestrator.Core.crawler_manager import CrawlerManager
        from orchestrator.gui.wizards import CrawlWorker

        cfg = ConfigManager(tmp_path)
        seen = []

        class FakeRag:
            framework = SimpleNamespace(config=cfg)

            def ingest_url(self, url, crawler_options=None, **kwargs):
                crawler = CrawlerManager(self.framework, crawler_options)
                seen.append((url, crawler.max_depth, crawler.respect_robots, crawler.download_pdfs, crawler.rate_limit))
                return {"success": True, "outcome": "success", "count": 3, "documents": 1}

        dlg = URLInputDialog()
        dlg.txt_urls.setPlainText("https://docs.radxa.com/")
        dlg.spin_depth.setValue(4)
        dlg.chk_robots.setChecked(False)
        dlg.chk_pdfs.setChecked(False)
        dlg.spin_delay.setValue(3)

        finished = []
        worker = CrawlWorker(FakeRag(), dlg.get_urls(), dlg.get_options(), lang="en")
        worker.done.connect(lambda outcome, message: finished.append((outcome, message)))
        worker.run()

        assert seen == [("https://docs.radxa.com/", 4, False, False, 3.0)]
        assert finished == [("success", "Deep Ingest finished: 1 pages (3 chunks) are now searchable.")]
        assert cfg.get("crawler_respect_robots") is True
        assert cfg.get("crawler_max_depth") == 2
        assert CrawlerManager(SimpleNamespace(config=cfg)).respect_robots is True

    def test_skipped_and_failed_pages_are_named_in_the_crawl_log(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        class FakeRag:
            def ingest_url(self, url, crawler_options=None, **kwargs):
                return {"success": True, "outcome": "partial", "count": 1, "documents": 1, "skipped": [
                    {"url": "https://docs.example/private", "reason": "X-Robots-Tag: noai", "opt_out": True}],
                    "failed": [{"url": "https://docs.example/gone", "reason": "HTTP status 404", "opt_out": False}]}

        lines = []
        worker = CrawlWorker(FakeRag(), ["https://docs.example/"], {})
        worker.progress.connect(lines.append)
        worker.run()

        assert "  -> Skipped https://docs.example/private: X-Robots-Tag: noai" in lines
        assert "  -> Failed https://docs.example/gone: HTTP status 404" in lines


class ScriptedRag:
    """ingest_url answers from a script per URL and records what happened, in order."""

    def __init__(self, results, events=None):
        self.results = results
        self.events = [] if events is None else events
        self.calls = []

    def ingest_url(self, url, crawler_options=None, stop=None, progress=None):
        self.calls.append(url)
        result = self.results[url]
        if callable(result):
            result = result(stop, progress)
        if isinstance(result, Exception):
            raise result
        self.events.append(("stored", url))
        return result


def page_result(outcome, pages=0, chunks=0, stopped=False):
    return {"success": outcome != "failure", "outcome": outcome, "documents": pages, "count": chunks,
            "message": f"{outcome} message", "stopped": stopped}


class TestDeepIngestOutcome:
    """The eject rule: the outcome is reported and spoken once it is true (after the last write), and success,
    partial and failure are told apart; "complete" is never said when nothing was stored."""

    def run_worker(self, rag, urls, lang="en", stop_after_first=False):
        from orchestrator.gui.wizards import CrawlWorker

        worker = CrawlWorker(rag, urls, {}, announcer=lambda text: rag.events.append(("spoken", text)), lang=lang)
        done, pages = [], []
        worker.done.connect(lambda outcome, message: done.append((outcome, message)))
        worker.page.connect(lambda n, m, url: pages.append((n, m, url)))
        worker.run()
        return worker, done, pages

    def test_success_is_spoken_after_the_writes(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("success", 2, 5),
                           "https://b.example/": page_result("success", 1, 2)})
        _, done, _ = self.run_worker(rag, list(rag.results))
        message = "Deep Ingest finished: 3 pages (7 chunks) are now searchable."
        assert done == [("success", message)]
        assert rag.events == [("stored", "https://a.example/"), ("stored", "https://b.example/"), ("spoken", message)]

    def test_one_failed_site_makes_the_crawl_partial(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("success", 2, 5),
                           "https://b.example/": page_result("failure")})
        _, done, _ = self.run_worker(rag, list(rag.results))
        assert done[0][0] == "partial" and "2 pages (5 chunks) are searchable" in done[0][1]
        assert rag.events[-1] == ("spoken", done[0][1])

    def test_a_partial_site_makes_the_crawl_partial(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("partial", 2, 5)})
        assert self.run_worker(rag, list(rag.results))[1][0][0] == "partial"

    def test_nothing_stored_is_a_failure_never_complete(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("failure"), "https://b.example/": page_result("failure")})
        _, done, _ = self.run_worker(rag, list(rag.results))
        assert done == [("failure", "Deep Ingest failed: nothing was stored. See the log.")]
        assert all("complete" not in text for kind, text in rag.events if kind == "spoken")

    def test_outcome_in_the_ui_language(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("success", 1, 3)})
        _, done, _ = self.run_worker(rag, list(rag.results), lang="de")
        assert done == [("success", "Deep Ingest abgeschlossen: 1 Seiten (3 Abschnitte) sind jetzt durchsuchbar.")]

    def test_stop_keeps_what_was_stored_and_skips_the_remaining_sites(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        holder = {}

        def first(stop, progress):
            progress(1, 50, "https://a.example/")
            holder["worker"].stop()           # the user presses Stop during the first site
            assert stop.is_set()
            return page_result("partial", 2, 4, stopped=True)

        rag = ScriptedRag({"https://a.example/": first, "https://b.example/": page_result("success", 9, 9)})
        worker = CrawlWorker(rag, list(rag.results), {}, announcer=lambda t: rag.events.append(("spoken", t)),
                             lang="en")
        holder["worker"] = worker
        done, pages = [], []
        worker.done.connect(lambda outcome, message: done.append((outcome, message)))
        worker.page.connect(lambda n, m, url: pages.append((n, m, url)))
        worker.run()

        assert rag.calls == ["https://a.example/"]
        assert pages == [(1, 50, "https://a.example/")]
        assert done == [("partial", "Deep Ingest stopped: 2 pages (4 chunks) are kept and searchable.")]

    def test_stop_before_anything_was_stored(self, qapp):
        rag = ScriptedRag({"https://a.example/": page_result("failure", stopped=True)})
        _, done, _ = self.run_worker(rag, list(rag.results))
        assert done == [("failure", "Deep Ingest stopped: nothing was stored.")]

    def test_an_unexpected_error_is_reported_and_spoken_as_failure(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        rag = ScriptedRag({"https://a.example/": RuntimeError("disk full")})
        worker = CrawlWorker(rag, list(rag.results), {}, announcer=lambda t: rag.events.append(("spoken", t)),
                             lang="en")
        errors, done = [], []
        worker.error.connect(errors.append)
        worker.done.connect(lambda *a: done.append(a))
        worker.run()
        assert errors == ["disk full"] and done == []
        assert rag.events == [("spoken", "Deep Ingest failed: nothing was stored. See the log.")]

    def test_a_broken_announcer_never_breaks_the_report(self, qapp):
        from orchestrator.gui.wizards import CrawlWorker

        def broken(text):
            raise OSError("no audio device")

        worker = CrawlWorker(ScriptedRag({"https://a.example/": page_result("success", 1, 1)}),
                             ["https://a.example/"], {}, announcer=broken, lang="en")
        done = []
        worker.done.connect(lambda outcome, message: done.append(outcome))
        worker.run()
        assert done == ["success"]


@pytest.fixture
def wizard_boxes(monkeypatch):
    from orchestrator.gui import wizards

    shown = []

    class RecordingMessageBox:
        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", title, text))

        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", title, text))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", title, text))

    monkeypatch.setattr(wizards, "QMessageBox", RecordingMessageBox)
    return shown


class TestDeepIngestControls:
    """Progress "page N of M: <url>" instead of an endless spinner, a Stop button, the notice before the first
    crawl."""

    @pytest.fixture
    def wizard(self, qapp, tmp_path, wizard_boxes):
        from PySide6.QtCore import QThread
        from orchestrator.gui.wizards import ModuleCreationWizard

        wiz = ModuleCreationWizard(tmp_path / "targets")
        yield wiz
        # A crawl still running after a failed assertion must not open a real (modal) box in a later test.
        if isinstance(wiz.crawl_worker, QThread):
            wiz.crawl_worker.stop()
            wiz.crawl_worker.wait(5000)
            qapp.processEvents()
        wiz.close()

    def test_progress_names_page_and_url(self, wizard):
        wizard.on_crawl_page(3, 50, "https://docs.example/guide")
        assert wizard.status_label.text() == "Page 3 of 50: https://docs.example/guide"
        assert (wizard.ai_progress.minimum(), wizard.ai_progress.maximum(), wizard.ai_progress.value()) == (0, 50, 3)

    def test_progress_shows_urls_as_plain_escaped_text(self, wizard):
        wizard.on_crawl_page(1, 5, "https://docs.example/<b>x</b>‮" + "a" * 300)
        text = wizard.status_label.text()
        assert wizard.status_label.textFormat() == dialogs.Qt.PlainText
        assert "\\u202e" in text and "‮" not in text and text.endswith("...") and len(text) < 160

    def test_outcomes_get_their_own_box_and_reset_the_controls(self, wizard, wizard_boxes):
        wizard.btn_stop_crawl.setVisible(True)
        wizard.btn_deep_ingest.setEnabled(False)
        wizard.on_crawl_done("success", "ok")
        wizard.on_crawl_done("partial", "some")
        wizard.on_crawl_done("failure", "none")
        assert wizard_boxes == [("information", "Deep Ingest Complete", "ok"),
                                ("warning", "Deep Ingest Partly Complete", "some"),
                                ("critical", "Deep Ingest Failed", "none")]
        assert wizard.btn_stop_crawl.isHidden() and wizard.btn_deep_ingest.isEnabled()

    def test_stop_button_stops_the_running_worker(self, wizard):
        class Worker:
            stopped = False

            def isRunning(self):
                return True

            def stop(self):
                self.stopped = True

        assert wizard.btn_stop_crawl.isHidden()
        wizard.crawl_worker = Worker()
        wizard.btn_stop_crawl.setEnabled(True)
        wizard.stop_deep_ingest()
        assert wizard.crawl_worker.stopped and not wizard.btn_stop_crawl.isEnabled()
        assert wizard.status_label.text() == "Stopping after the current page..."

    def test_deep_ingest_shows_the_notice_once_then_crawls_in_the_background(self, qapp, wizard, tmp_path,
                                                                           monkeypatch, wizard_boxes):
        from orchestrator.gui import wizards

        cfg = ConfigManager(tmp_path / "configs")
        rag = ScriptedRag({"https://docs.example/": lambda stop, progress: (
            progress(1, 50, "https://docs.example/"), page_result("success", 1, 2))[1]})
        wizard.framework_manager = SimpleNamespace(config=cfg, get_component=lambda name: rag)
        notices, spoken = [], []
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: notices.append(self.windowTitle()))
        monkeypatch.setattr(wizards, "announce", lambda text, lang=None, config=None: spoken.append((text, config)))

        class FakeURLDialog:
            def __init__(self, parent=None):
                pass

            def exec(self):
                return wizards.QDialog.Accepted

            def get_urls(self):
                return ["https://docs.example/"]

            def get_options(self):
                return {"max_depth": 1}

        monkeypatch.setattr(wizards, "URLInputDialog", FakeURLDialog)

        for _ in range(2):
            wizard.run_deep_ingest()
            assert not wizard.btn_stop_crawl.isHidden() and wizard.ai_progress.maximum() != 0
            assert wait_for(qapp, lambda: len(wizard_boxes) and not wizard.crawl_worker.isRunning())
            wizard_boxes.clear()

        assert notices == ["Before your first Deep Ingest"]   # once per user
        assert (cfg.user_state_dir / "crawl_notice.json").exists()
        assert spoken == [("Deep Ingest finished: 1 pages (2 chunks) are now searchable.", cfg)] * 2
        assert wizard.ai_progress.maximum() == 50   # the "page 1 of 50" progress reached the GUI
        assert wizard.status_label.text() == "Deep Ingest finished: 1 pages (2 chunks) are now searchable."


class TestCrawlNotice:
    """Information before the first crawl, not a legal confirmation: one button, no checkbox, shown again when
    the text changes, spoken only once it is on screen."""

    def test_one_understood_button_and_no_checkbox(self, qapp):
        dlg = dialogs.CrawlNoticeDialog(lang="en")
        assert [b.text() for b in dlg.buttons()] == ["&Understood"]
        assert dlg.defaultButton() is dlg.understood and dlg.escapeButton() is dlg.understood
        assert dlg.checkBox() is None and dlg.textFormat() == dialogs.Qt.PlainText
        text = dlg.text()
        for needle in ("publicly reachable", "robots.txt", "X-Robots-Tag", "noai", "TDMRep", "stays on this computer",
                       "licence", "release it explicitly", "not a legal confirmation"):
            assert needle in text, needle
        assert dlg.accessibleName() == "Before your first Deep Ingest" and dlg.accessibleDescription() == text

    def test_german_text(self, qapp):
        dlg = dialogs.CrawlNoticeDialog(lang="de")
        assert dlg.windowTitle() == "Vor Ihrem ersten Deep Ingest"
        assert [b.text() for b in dlg.buttons()] == ["&Verstanden"]
        assert "TDMRep" in dlg.text() and "§ 44b UrhG" in dlg.text()

    def test_escape_counts_as_understood(self, qapp):
        from PySide6.QtTest import QTest

        dlg = dialogs.CrawlNoticeDialog(lang="en")
        dlg.show()
        QTest.keyClick(dlg, dialogs.Qt.Key_Escape)
        assert dlg.clickedButton() is dlg.understood and not dlg.isVisible()

    def test_spoken_only_once_the_notice_is_visible(self, qapp):
        spoken = []
        dlg = dialogs.CrawlNoticeDialog(lang="de", announcer=lambda text: spoken.append((text, dlg.isVisible())))
        qapp.processEvents()
        assert spoken == []
        dlg.show()
        assert wait_for(qapp, lambda: spoken)
        dlg.hide()
        dlg.show()
        qapp.processEvents()
        dlg.close()
        assert spoken == [("Bitte lesen Sie den Hinweis vor Ihrem ersten Deep Ingest.", True)]

    def test_shown_once_and_again_when_the_text_changes(self, qapp, tmp_path, monkeypatch):
        import os
        from orchestrator.utils import localization
        from orchestrator.utils.crawl_notice import CrawlNotice

        cfg = SimpleNamespace(user_state_dir=tmp_path / "state")
        shown = []
        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: shown.append(self.windowTitle()))

        dialogs.show_crawl_notice_once(None, cfg)
        dialogs.show_crawl_notice_once(None, cfg)
        assert len(shown) == 1
        state = cfg.user_state_dir / CrawlNotice.FILE_NAME
        if os.name == "posix":
            assert state.stat().st_mode & 0o777 == 0o600
        stored = json.loads(state.read_text())
        assert stored["via"] == "gui" and stored["notice_version"] == 1 and len(stored["notice_sha256"]) == 64

        de = dict(localization.LocalizationManager.TRANSLATIONS["de"])
        de["crawl.notice.text"] += " Neu."
        monkeypatch.setitem(localization.LocalizationManager.TRANSLATIONS, "de", de)
        dialogs.show_crawl_notice_once(None, cfg)
        assert len(shown) == 2   # a changed text in any language is shown again

    @pytest.mark.parametrize("content", ["{broken", '{"version": 1}', '{"version": 1, "notice_version": 1, '
                                         '"notice_sha256": "x", "acknowledged_at": "", "via": "mcp"}'])
    def test_a_damaged_state_file_shows_the_notice_again(self, tmp_path, content):
        from orchestrator.utils.crawl_notice import CrawlNotice

        (tmp_path / CrawlNotice.FILE_NAME).write_text(content)
        notice = CrawlNotice(tmp_path)
        assert notice.needed()
        notice.acknowledge("cli")
        assert not notice.needed()

    def test_only_the_gui_or_cli_can_acknowledge(self, tmp_path):
        from orchestrator.utils.crawl_notice import CrawlNotice

        with pytest.raises(ValueError):
            CrawlNotice(tmp_path).acknowledge("mcp")
        assert not (tmp_path / CrawlNotice.FILE_NAME).exists()

    def test_a_failed_save_still_lets_the_crawl_start(self, qapp, tmp_path, monkeypatch):
        from orchestrator.utils.crawl_notice import CrawlNotice

        monkeypatch.setattr(dialogs.CrawlNoticeDialog, "exec", lambda self: 0)
        monkeypatch.setattr(CrawlNotice, "acknowledge", lambda self, via: (_ for _ in ()).throw(OSError("read-only")))
        dialogs.show_crawl_notice_once(None, SimpleNamespace(user_state_dir=tmp_path))   # no exception


class TestMainWindowWiring:

    def test_main_window_exposes_ai_config_and_wizard(self, qapp, tmp_path, monkeypatch, memory_keyring):
        import shutil
        from PySide6.QtWidgets import QMenu
        from conftest import REPO_ROOT
        from orchestrator.Core.framework import FrameworkManager
        from orchestrator.gui import main_window
        from orchestrator.gui.wizards import ModuleCreationWizard

        monkeypatch.chdir(tmp_path)
        (tmp_path / "configs").mkdir()

        class TmpFramework(FrameworkManager):
            def __init__(self, *args, **kwargs):
                super().__init__(str(tmp_path / "configs"))
                self.info.installation_path = tmp_path

        monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
        monkeypatch.setattr(main_window.ModuleCreationWizard, "exec", lambda self: 0)
        # The first-run permissions dialog is modal; a later processEvents() in another test would block on it.
        opened = []
        monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions",
                            lambda self, first_run=False: opened.append(first_run))

        win = main_window.MainOrchestrator(REPO_ROOT)
        try:
            qapp.processEvents()
            assert opened == [True], "the permissions are asked once at the first start"
            menus = {m.title(): [a.text() for a in m.actions()] for m in win.findChildren(QMenu) if m.title()}
            assert menus["Tools"] == ["Configure AI Agent (Ditto)...", "Create Hardware Module...",
                                      "Tool Permissions (Ditto / MCP)...", "Spoken Announcements"]
            # The audio switch is a persisted setting, on by default.
            assert win.act_audio.isCheckable() and win.act_audio.isChecked()
            win.act_audio.setChecked(False)
            assert win.framework.config.get("audio_announcements") is False
            assert "audio_announcements: false" in (tmp_path / "configs" / "config.yml").read_text()

            wizard = ModuleCreationWizard(tmp_path / "targets", win)
            assert wizard.framework_manager is win.framework
            assert len(wizard.pageIds()) == 5

            # A module created by the wizard shows up without restarting the app.
            assert win.builder_tab.target_combo.count() == 0
            shutil.copytree(REPO_ROOT / "targets" / "Rockchip", tmp_path / "targets" / "Rockchip",
                            ignore=shutil.ignore_patterns("scripts", "modules"))
            win._open_module_wizard()
            assert win.builder_tab.target_combo.count() == 1
            assert win.deployment_window.cb_profiles.itemText(0) == "Rockchip"

            win._update_system_state()
        finally:
            win.update_timer.stop()
            win.close()
