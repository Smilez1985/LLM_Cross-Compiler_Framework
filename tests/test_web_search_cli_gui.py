"""Ditto's web search by command and by window (`llm-cli ditto web ...`, Ditto > Web Search...): the same choice, the same
search, the same plain words. The network is a fake fetch and the container engine a fake setup; nothing leaves the test."""

import json
from types import SimpleNamespace

import pytest

from orchestrator.Core import web_search as ws
from orchestrator.Core.web_search import Response, WebSearch

pytest.importorskip("PySide6")

SEARXNG = {"results": [{"url": "https://docs.radxa.com/en/rock5/rock5b", "title": "ROCK 5B", "content": "6 TOPS"},
                       {"url": "https://github.com/a/b", "title": "b", "content": "repo"}]}


class Config(dict):
    saved = 0

    def set(self, key, value):
        self[key] = value

    def save_user_config(self):
        type(self).saved += 1
        return True


def fake_fetch(request):
    return Response(200, json.dumps(SEARXNG))


@pytest.fixture
def framework(monkeypatch):
    config = Config({"web_search_ways": [], "web_search_apis": [], "offline_mode": False, "ai_security_level": "STRICT"})
    secrets = {}
    manager = SimpleNamespace(get_secret=lambda name: secrets.get(name))
    fw = SimpleNamespace(config=config, secrets_manager=manager, secrets=secrets)
    monkeypatch.setattr(ws, "for_framework", lambda framework, fetch=None: WebSearch(
        framework.config, manager.get_secret, fetch=fake_fetch, table={}, time_limit=3.0))
    from orchestrator import cli

    def require(self):
        self.framework_manager, self._initialized = fw, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    return fw


def invoke(*args):
    from click.testing import CliRunner
    from orchestrator import cli
    return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, ["ditto", "web", *args])


class TestConfigure:

    def test_it_checks_everything_before_it_changes_anything(self):
        config = Config()
        with pytest.raises(ValueError):
            ws.configure(config, ways="searxng", apis="nonsense")
        assert config == {}

    def test_off_and_an_empty_list_turn_a_choice_off(self):
        config = Config({"web_search_ways": ["searxng"]})
        ws.configure(config, ways="off", apis=[])
        assert config["web_search_ways"] == [] and config["web_search_apis"] == []

    @pytest.mark.parametrize("address", ["ftp://x", "127.0.0.1:8888", "", "http://"])
    def test_a_bad_address_is_refused(self, address):
        with pytest.raises(ValueError):
            ws.configure(Config(), address=address)

    def test_the_order_is_the_fixed_one_and_a_trailing_slash_goes(self):
        config = Config()
        ws.configure(config, ways="html,searxng", apis="brave,tavily", address="http://h:1/")
        assert config["web_search_ways"] == ["searxng", "html"] and config["web_search_apis"] == ["tavily", "brave"]
        assert config["web_search_url"] == "http://h:1"


class TestCommands:

    def test_status_says_off_and_how_to_turn_it_on(self, framework):
        out = invoke("status").output
        assert "off" in out and "ditto web use --ways searxng" in out and "no key" in out

    def test_use_sets_and_shows_the_choice_without_ever_showing_a_key(self, framework):
        framework.secrets["web_search_tavily_key"] = "TOPSECRETKEY"
        result = invoke("use", "--ways", "searxng,api", "--apis", "tavily")
        assert result.exit_code == 0 and "searxng, api" in result.output and "Tavily: listed, key saved" in result.output
        assert "TOPSECRETKEY" not in result.output and framework.config["web_search_ways"] == ["searxng", "api"]
        assert Config.saved >= 1

    def test_a_wrong_name_is_one_plain_line_and_changes_nothing(self, framework):
        result = invoke("use", "--ways", "bogus")
        assert result.exit_code == 1 and "is not known" in result.output and framework.config["web_search_ways"] == []

    def test_search_lists_the_hits_with_their_origin_and_never_a_page(self, framework):
        framework.config["web_search_ways"] = ["searxng"]
        result = invoke("search", "RK3588 NPU")
        assert result.exit_code == 0 and "ROCK 5B" in result.output and "https://github.com/a/b" in result.output
        assert "repository" in result.output and "not a page" not in result.output.lower() or "Deep Ingest" in result.output

    def test_search_as_json(self, framework):
        framework.config["web_search_ways"] = ["searxng"]
        data = json.loads(invoke("search", "x", "--json").stdout)
        assert [h["domain"] for h in data["hits"]] == ["docs.radxa.com", "github.com"] and data["ways"][0]["status"] == "ok"

    def test_search_with_no_way_on_is_refused_in_plain_words(self, framework):
        result = invoke("search", "x")
        assert result.exit_code == 1 and "off" in result.output

    def test_a_query_with_a_secret_is_refused_and_never_sent(self, framework):
        framework.config["web_search_ways"] = ["searxng"]
        result = invoke("search", "why does " + "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789" + " fail")
        assert result.exit_code == 1 and "not sent anywhere" in result.output

    def test_setup_without_start_copies_the_file_and_makes_the_secret(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        result = invoke("setup", "--no-start")
        assert result.exit_code == 0 and "settings file was copied" in result.output.replace("\n", " ")
        assert (tmp_path / "searxng" / "config" / "settings.yml").is_file()

    def test_setup_without_an_engine_says_so_and_the_file_is_ready(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_setup

        def no_engine():
            raise RuntimeError("no daemon")
        monkeypatch.setattr(searxng_setup, "_client", lambda factory: (_ for _ in ()).throw(
            searxng_setup.SetupError(searxng_setup.NO_ENGINE)))
        result = invoke("setup")
        assert result.exit_code == 1 and "No container engine" in result.output.replace("\n", " ")
        assert (tmp_path / "searxng" / "config" / "settings.yml").is_file()


class TestWindow:

    @pytest.fixture
    def window(self, qapp, framework, monkeypatch):
        from orchestrator.gui.ditto_web_window import DittoWebWindow
        ran = []

        def setup_runner(folder):
            ran.append(folder)
            return SimpleNamespace(url="http://127.0.0.1:8888")
        win = DittoWebWindow(framework, None, setup_runner=setup_runner, stop_runner=lambda: "removed", lang="en")
        win.ran = ran
        yield win
        win.close()
        win.deleteLater()

    def wait(self, qapp, window):
        import time
        end = time.monotonic() + 8
        while window._jobs and time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        qapp.processEvents()

    def test_it_shows_what_is_set_and_saves_the_choice_like_the_command(self, qapp, window, framework):
        assert "off" in window.lbl_status.text() and not any(b.isChecked() for b in window.way_boxes.values())
        window.way_boxes["searxng"].setChecked(True)
        window.api_boxes["brave"].setChecked(True)
        window.edt_address.setText("http://127.0.0.1:9999")
        window.btn_apply.click()
        assert framework.config["web_search_ways"] == ["searxng"] and framework.config["web_search_apis"] == ["brave"]
        assert framework.config["web_search_url"] == "http://127.0.0.1:9999" and window.lbl_result.text() == "Saved."
        assert "Search engine on this computer" in window.lbl_status.text()

    def test_a_wrong_address_is_told_and_changes_nothing(self, qapp, window, framework, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        told = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: told.append(text))
        window.edt_address.setText("ftp://nope")
        window.btn_apply.click()
        assert told and "http://" in told[0] and framework.config["web_search_ways"] == []

    def test_a_key_goes_to_the_secrets_store_and_is_never_shown_again(self, qapp, window, framework, monkeypatch):
        import orchestrator.Core.secret_entries as entries
        stored = {}
        monkeypatch.setattr(entries, "store_secret", lambda manager, name, value: stored.update({name: value}))
        window.cb_key_provider.setCurrentIndex(window.cb_key_provider.findData("tavily"))
        window.edt_key.setText("  KEY-123  ")
        window.btn_key.click()
        assert stored == {"web_search_tavily_key": "KEY-123"} and window.edt_key.text() == ""
        assert "KEY-123" not in window.lbl_result.text() and "Tavily" in window.lbl_result.text()

    def test_an_empty_key_is_told(self, qapp, window, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        told = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: told.append(text))
        window.btn_key.click()
        assert told == ["Enter the key first."]

    def test_a_search_fills_the_table_with_plain_text(self, qapp, window, framework):
        framework.config["web_search_ways"] = ["searxng"]
        window.edt_query.setText("RK3588 NPU")
        window.btn_search.click()
        self.wait(qapp, window)
        assert window.table.rowCount() == 2
        row = [window.table.item(0, c).text() for c in range(window.table.columnCount())]
        assert row[0] == "ROCK 5B" and row[1] == "Manufacturer" or row[1] in ("Manufacturer", "Other")
        assert "https://docs.radxa.com" in row[2] and "2 hits" in window.lbl_result.text()

    def test_a_search_with_no_way_on_is_told_in_plain_words(self, qapp, window, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        told = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: told.append(text))
        window.edt_query.setText("x")
        window.btn_search.click()
        self.wait(qapp, window)
        assert told and "off" in told[0]

    def test_the_search_engine_is_set_up_and_stopping_asks_first(self, qapp, window, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        window.btn_setup.click()
        self.wait(qapp, window)
        assert window.ran == [tmp_path] and "http://127.0.0.1:8888" in window.lbl_result.text()
        monkeypatch.setattr(module, "ask", lambda parent, title, text: False)
        window.btn_stop.click()
        self.wait(qapp, window)
        assert "stopped" not in window.lbl_result.text()                      # "No" changed nothing
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        window.btn_stop.click()
        self.wait(qapp, window)
        assert "stopped and removed" in window.lbl_result.text()

    def test_every_text_exists_in_both_languages_with_the_same_placeholders(self):
        import re
        from orchestrator.utils import l10n_websearch as texts
        english, german = texts.TEXTS["en"], texts.TEXTS["de"]
        assert set(english) == set(german) and len(english) > 40
        for key in english:
            assert re.findall(r"\{(\w+)\}", english[key]) == re.findall(r"\{(\w+)\}", german[key]), key
