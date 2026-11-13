"""`llm-cli ditto archive`: list, search, show, confirm, unconfirm, delete, undo and level, on a real archive (Qdrant Edge in
tmp_path, the fake embedders of test_rag). The commands are the person's own actions: Ditto's tools have none of them."""

import json
from types import SimpleNamespace

import pytest

from orchestrator.Core.ditto_archive import Candidate, DittoArchive
from test_archive_recorder import TABLE  # noqa: F401 - the vendor table of the archive tests
from test_ditto_archive import PAGES, Clock
from test_rag import make_rag  # noqa: F401 - the fixture

KEY = "target:rk3588/npu"


def invoke(*args, stdin=None):
    from click.testing import CliRunner
    from orchestrator import cli
    return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args), input=stdin)


@pytest.fixture
def archive(make_rag, monkeypatch):
    from orchestrator import cli
    rag = make_rag()
    archive = DittoArchive(lambda: rag, rag.config, table=TABLE, clock=Clock())
    framework = SimpleNamespace(ditto_archive=archive, config=rag.config, resolve_path=lambda p: p)

    def require(self):
        self.framework_manager, self._initialized = framework, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    return archive


def learn(archive, text="The NPU of the RK3588 has 6 TOPS.", key=KEY, evidence=None, ground="sources"):
    return archive.record(Candidate(key, text, ground, evidence or PAGES[:2])).entry


def test_the_group_alone_lists_and_says_what_teaches_an_empty_archive(archive):
    empty = invoke("ditto", "archive")
    assert empty.exit_code == 0 and "0 entries, 0 confirmed" in empty.output and "functional test" in empty.output
    learn(archive)
    result = invoke("ditto", "archive")
    assert result.exit_code == 0 and KEY in result.output and "6 TOPS" in result.output
    assert "Level mid: Ditto confirms an entry by itself with 5 independent sources" in result.output


def test_list_filters_and_prints_machine_output(archive):
    first = learn(archive)
    learn(archive, key="target:rk3588/other", text="The model runs fast on the NPU.")
    archive.confirm(first.id)
    assert "other" not in invoke("ditto", "archive", "list", "--status", "confirmed").output
    data = json.loads(invoke("ditto", "archive", "list", "--json").stdout)
    assert {e["key"] for e in data} == {KEY, "target:rk3588/other"}
    assert [e["status"] for e in data if e["key"] == KEY] == ["confirmed"]


def test_search_finds_by_meaning_and_confirmed_only_narrows(archive):
    learn(archive, text="The NPU speeds up model inference.")
    other = learn(archive, key="target:rk3588/other", text="The NPU model runs fast.")
    archive.confirm(other.id)
    both = invoke("ditto", "archive", "search", "neural accelerator inference")
    assert both.exit_code == 0 and KEY in both.output
    narrow = json.loads(invoke("ditto", "archive", "search", "npu model", "--confirmed-only", "--json").stdout)
    assert [h["key"] for h in narrow] == ["target:rk3588/other"]
    archive.unconfirm(other.id)
    assert "No entry matches." in invoke("ditto", "archive", "search", "npu model", "--confirmed-only").output


def test_show_takes_the_first_characters_of_an_id(archive):
    entry = learn(archive)
    shown = invoke("ditto", "archive", "show", entry.id[:8])
    assert shown.exit_code == 0 and "Ground: sources; 2 independent source(s)" in shown.output
    assert PAGES[0] in shown.output
    assert invoke("ditto", "archive", "show", "abc").exit_code == 1               # too short
    assert invoke("ditto", "archive", "show", "ffffffff").exit_code == 1          # nothing begins with it


def test_confirm_and_unconfirm_are_the_persons(archive):
    entry = learn(archive)
    assert invoke("ditto", "archive", "confirm", entry.id[:8]).exit_code == 0
    assert archive.get(entry.id).confirmed_by == "person"
    assert invoke("ditto", "archive", "unconfirm", entry.id[:8]).exit_code == 0
    assert archive.get(entry.id).status == "unconfirmed"
    assert invoke("ditto", "archive", "confirm", "ffffffff").exit_code == 1


def test_delete_asks_and_no_is_the_default(archive):
    entry = learn(archive)
    declined = invoke("ditto", "archive", "delete", entry.id[:8], stdin="\n")
    assert "Nothing was changed." in declined.output and archive.get(entry.id) is not None
    assert invoke("ditto", "archive", "delete", entry.id[:8], "--yes").exit_code == 0
    assert archive.get(entry.id) is None


def test_undo_swaps_the_text_back_after_a_question(archive):
    old = learn(archive, text="The NPU of the RK3588 has 6 TOPS.")
    archive.confirm(old.id)
    learn(archive, text="The NPU of the RK3588 has 8 TOPS.", evidence=PAGES[2:4])
    shown = invoke("ditto", "archive", "list", "--copies")
    assert "6 TOPS" in shown.output and "copy" in shown.output
    declined = invoke("ditto", "archive", "undo", KEY, stdin="n\n")
    assert "Nothing was changed." in declined.output and archive.entries()[0].text.endswith("8 TOPS.")
    done = invoke("ditto", "archive", "undo", old.id[:8], "--yes")                 # an id works as well as the key
    assert done.exit_code == 0 and "Undone" in done.output
    assert archive.entries()[0].text.endswith("6 TOPS.") and archive.entries()[0].status == "confirmed"
    assert invoke("ditto", "archive", "undo", "target:none/x", "--yes").exit_code == 1


def test_level_shows_and_sets_the_setting_that_only_the_person_changes(archive):
    shown = invoke("ditto", "archive", "level")
    assert shown.exit_code == 0 and "ditto_archive_security: mid" in shown.output and "5 independent" in shown.output
    assert invoke("ditto", "archive", "level", "high").exit_code == 0          # written to config.yml of this installation
    data = json.loads(invoke("ditto", "archive", "level", "--json").stdout)
    assert data["level"] == "high" and data["sources_needed"] == 7 and data["levels"] == {"high": 7, "low": 3, "mid": 5}
    assert invoke("ditto", "archive", "level", "extreme").exit_code == 2             # not a choice


def test_an_ai_has_no_way_to_change_the_level():
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, person_only_text
    assert "ditto_archive_security" in PERSON_ONLY_SETTINGS
    assert "Only the person sets it" in person_only_text("ditto_archive_security")


def test_without_the_archive_the_commands_say_so(monkeypatch):
    from orchestrator import cli

    def require(self):
        self.framework_manager, self._initialized = SimpleNamespace(ditto_archive=None), True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    result = invoke("ditto", "archive", "list")
    assert result.exit_code == 1 and "not available" in result.output


def test_an_archive_that_cannot_be_opened_is_a_plain_line(monkeypatch):
    from orchestrator import cli
    archive = DittoArchive(lambda: None, None, table=TABLE)           # the knowledge base is switched off

    def require(self):
        self.framework_manager, self._initialized = SimpleNamespace(ditto_archive=archive), True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    result = invoke("ditto", "archive", "list")
    assert result.exit_code == 1 and "switched off" in result.output
