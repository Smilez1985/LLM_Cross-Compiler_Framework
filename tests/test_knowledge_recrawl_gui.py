"""The Deep Ingest window asks for the topic of the crawl's pages (empty: the site's name) and the crawl carries it."""

import pytest

pytest.importorskip("PySide6")

from orchestrator.gui import dialogs, wizards  # noqa: E402


def test_the_topic_field_is_checked_and_passed_to_the_crawl(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda parent, title, text: warnings.append(title))
    dlg = dialogs.URLInputDialog()
    dlg.txt_urls.setPlainText("https://docs.example.com/")
    assert dlg.get_topic() == "", "empty: the site's name"
    dlg.txt_topic.setText("not a topic/name")
    dlg.validate_and_accept()
    assert warnings == ["Topic"] and dlg.result() == 0
    dlg.txt_topic.setText(" npu-guide ")
    dlg.validate_and_accept()
    assert dlg.result() == dialogs.QDialog.Accepted and dlg.get_topic() == "npu-guide"

    calls = []

    class Rag:
        def ingest_url(self, url, options, **kwargs):
            calls.append((url, kwargs.get("topic"), kwargs.get("actor")))
            return {"outcome": "success", "documents": 1, "count": 1, "message": "ok"}

    worker = wizards.CrawlWorker(Rag(), ["https://docs.example.com/"], {}, topic="npu-guide")
    worker.run()
    assert calls == [("https://docs.example.com/", "npu-guide", "gui")]
    calls.clear()
    wizards.CrawlWorker(Rag(), ["https://docs.example.com/"], {}).run()
    assert calls == [("https://docs.example.com/", None, "gui")], "no topic named: the site's name decides"
