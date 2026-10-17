"""One-time notices (consolidation chain c1-imports: orchestrator/utils/notice_store.py).

Characterization first: the Deep Ingest notice (utils/crawl_notice.py) and the notice on Ditto (utils/ditto_notice.py)
are the same mechanism with their own file, texts and messages. Both keep their files byte for byte and their hashes,
so nobody is asked again because of the move.
"""

import hashlib
import json
import logging
import os
import re

import pytest

from orchestrator.utils import crawl_notice, ditto_notice
from orchestrator.utils.localization import texts_in_every_form

NOTICES = {
    "crawl": (crawl_notice, crawl_notice.CrawlNotice, "crawl_notice.json",
              ("crawl.notice.title", "crawl.notice.text"), "Showing the Deep Ingest notice again: ",
              "The Deep Ingest notice is acknowledged in the GUI or the CLI, not via 'mcp'."),
    "ditto": (ditto_notice, ditto_notice.DittoNotice, "ditto_notice.json",
              ("chat.ditto.notice.title", "chat.ditto.notice.text"), "Showing the notice on Ditto again: ",
              "The notice on Ditto is shown in the GUI or the CLI, not via 'mcp'."),
}


@pytest.fixture(params=sorted(NOTICES))
def notice(request):
    return NOTICES[request.param]


def test_the_names_files_and_texts(notice):
    module, cls, file_name, keys, _, _ = notice
    assert cls.FILE_NAME == file_name and module.TEXT_KEYS == keys and module.VIA == ("gui", "cli")
    from orchestrator.utils.localization import ui_text
    for lang in ("en", "de"):
        assert module.notice_text(lang) == (ui_text(keys[0], lang), ui_text(keys[1], lang))


def test_the_hash_is_made_from_every_form_of_the_texts(notice):
    module, _, _, keys, _, _ = notice
    expected = hashlib.sha256(json.dumps(texts_in_every_form(keys), ensure_ascii=False, sort_keys=True)
                              .encode("utf-8")).hexdigest()
    assert module.notice_sha256() == expected


def test_the_state_file_byte_for_byte(notice, tmp_path):
    module, cls, file_name, _, _, _ = notice
    state = cls(tmp_path / "state")
    assert state.path == tmp_path / "state" / file_name
    assert state.needed() and state.acknowledged_via() is None
    state.acknowledge("cli")
    raw = state.path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert list(data) == ["notice_sha256", "via", "acknowledged_at"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", data["acknowledged_at"])
    assert raw == json.dumps({"notice_sha256": module.notice_sha256(), "via": "cli",
                              "acknowledged_at": data["acknowledged_at"]}, indent=2)
    assert not state.needed() and state.acknowledged_via() == "cli"
    if os.name == "posix":
        assert state.path.stat().st_mode & 0o777 == 0o600


def test_only_a_person_acknowledges_it(notice, tmp_path):
    _, cls, _, _, _, refusal = notice
    with pytest.raises(ValueError) as e:
        cls(tmp_path).acknowledge("mcp")
    assert str(e.value) == refusal
    assert not (tmp_path / cls.FILE_NAME).exists()


@pytest.mark.parametrize("content", ["{broken", "[]", '"x"', "{}", '{"notice_sha256": "x"}'])
def test_a_damaged_or_old_state_file_shows_it_again(notice, tmp_path, content, caplog):
    module, cls, _, _, again, _ = notice
    (tmp_path / cls.FILE_NAME).write_text(content, encoding="utf-8")
    with caplog.at_level(logging.INFO, logger=module.__name__):
        assert cls(tmp_path).needed()
    assert cls(tmp_path).acknowledged_via() is None
    if content != '{"notice_sha256": "x"}':   # a different hash is no damage: shown again without a log line
        assert any(r.getMessage().startswith(again + str(tmp_path / cls.FILE_NAME)) for r in caplog.records)


def test_a_current_hash_with_a_foreign_via_counts_but_names_no_window(notice, tmp_path):
    module, cls, _, _, _, _ = notice
    (tmp_path / cls.FILE_NAME).write_text(json.dumps({"notice_sha256": module.notice_sha256(), "via": "mcp"}),
                                          encoding="utf-8")
    assert not cls(tmp_path).needed() and cls(tmp_path).acknowledged_via() is None


class TestAcknowledgeAndAudit:

    class Audit:
        def __init__(self, fail=None):
            self.rows, self.fail = [], fail

        def record(self, *args, **kwargs):
            if self.fail:
                raise self.fail
            self.rows.append((args, kwargs))

    def test_records_the_person_in_the_audit_log(self, tmp_path):
        audit = self.Audit()
        assert crawl_notice.acknowledge_and_audit(crawl_notice.CrawlNotice(tmp_path), "gui", audit) is None
        assert audit.rows == [(("gui", "crawl_notice_acknowledged", "framework", "person", "ok"),
                               {"arguments": {"via": "gui"}, "approved": True})]
        assert crawl_notice.CrawlNotice(tmp_path).acknowledged_via() == "gui"

    def test_without_an_audit_log_or_with_a_failing_one(self, tmp_path):
        assert crawl_notice.acknowledge_and_audit(crawl_notice.CrawlNotice(tmp_path), "cli") is None
        why = crawl_notice.acknowledge_and_audit(crawl_notice.CrawlNotice(tmp_path), "cli",
                                                 self.Audit(fail=OSError("disk full")))
        assert why == "disk full" and not crawl_notice.CrawlNotice(tmp_path).needed()
        why = crawl_notice.acknowledge_and_audit(crawl_notice.CrawlNotice(tmp_path), "cli",
                                                 self.Audit(fail=RuntimeError("")))
        assert why == "RuntimeError"

    def test_an_unknown_window_records_nothing(self, tmp_path):
        audit = self.Audit()
        with pytest.raises(ValueError):
            crawl_notice.acknowledge_and_audit(crawl_notice.CrawlNotice(tmp_path), "mcp", audit)
        assert audit.rows == [] and crawl_notice.CrawlNotice(tmp_path).needed()
