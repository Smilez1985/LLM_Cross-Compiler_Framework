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

    def test_setup_says_what_it_installs_and_asks_first_and_no_is_the_answer_without_a_yes(self, framework, tmp_path,
                                                                                         monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "setup", lambda *a, **k: pytest.fail("nothing is set up without a yes"))
        from click.testing import CliRunner
        from orchestrator import cli
        monkeypatch.setattr(cli, "_interactive", lambda: True)
        result = CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(
            cli.cli, ["ditto", "web", "setup"], input="\n")                # an empty answer: No
        text = result.output.replace("\n", " ")
        assert result.exit_code == 0 and "about 200 MB" in text and "no Docker" in text and "--remove" in text
        assert "Nothing was changed" in text and "search APIs" in text

    def test_setup_without_a_terminal_and_without_yes_does_nothing_and_says_so(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "setup", lambda *a, **k: pytest.fail("no yes, no setup"))
        result = invoke("setup")
        assert result.exit_code == 1 and "needs your yes" in result.output.replace("\n", " ")

    def test_setup_with_yes_uses_the_own_python_folder_by_default(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        seen = {}

        def fake(way, folder, config, **kwargs):
            seen.update(way=way, run=kwargs.get("run"))
            return searxng_engine.EngineResult(way, tmp_path, ["SearXNG is installed in your own folder."])
        monkeypatch.setattr(searxng_engine, "setup", fake)
        result = invoke("setup", "--yes")
        assert result.exit_code == 0 and seen == {"way": "own", "run": True} and "Done" in result.output
        invoke("setup", "--yes", "--way", "container", "--no-start")
        assert seen == {"way": "container", "run": False}

    def test_setup_that_fails_says_why_in_one_line(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine, searxng_native
        monkeypatch.setattr(searxng_engine, "setup", lambda *a, **k: (_ for _ in ()).throw(
            searxng_native.NativeError("Fetching SearXNG did not work (HTTP 403).")))
        result = invoke("setup", "--yes")
        assert result.exit_code == 1 and "HTTP 403" in result.output and "Traceback" not in result.output

    def test_remove_asks_first_and_removes_with_yes(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "remove", lambda *a, **k: ["The Python folder of SearXNG was removed."])
        assert invoke("setup", "--remove").exit_code == 1                   # no terminal, no yes
        done = invoke("setup", "--remove", "--yes")
        assert done.exit_code == 0 and "Python folder" in done.output

    def plan(self, needs=True, way="own"):
        from orchestrator.Core import searxng_engine
        lines = ["SearXNG in your own folder is at 111111111111; this version of the framework pins bbbbbbbbbbbb.",
                 "What changes: a new copy is fetched."] if needs else ["There is nothing to update."]
        return searxng_engine.UpdatePlan(way, needs, "111111111111", "bbbbbbbbbbbb",
                                         "https://github.com/searxng/searxng/compare/1...2", lines)

    def test_update_says_what_changes_asks_first_and_no_is_the_answer_without_a_yes(self, framework, tmp_path,
                                                                                   monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from click.testing import CliRunner

        from orchestrator import cli
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "update_plan", lambda *a, **k: self.plan())
        monkeypatch.setattr(searxng_engine, "update", lambda *a, **k: pytest.fail("nothing is updated without a yes"))
        monkeypatch.setattr(cli, "_interactive", lambda: True)
        result = CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(
            cli.cli, ["ditto", "web", "setup", "--update"], input="\n")          # an empty answer: No
        text = result.output.replace("\n", " ")
        assert result.exit_code == 0 and "111111111111" in text and "bbbbbbbbbbbb" in text and "Nothing was changed" in text

    def test_update_with_yes_updates_and_says_it_is_done(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        seen = {}

        def fake(folder, config, **kwargs):
            seen.update(folder=folder, progress=kwargs.get("progress"))
            return searxng_engine.EngineResult("own", tmp_path, ["SearXNG was updated to the stand that this version of the "
                                                                 "framework pins."])
        monkeypatch.setattr(searxng_engine, "update_plan", lambda *a, **k: self.plan())
        monkeypatch.setattr(searxng_engine, "update", fake)
        result = invoke("setup", "--update", "--yes")
        assert result.exit_code == 0 and "was updated" in result.output and "Done" in result.output
        assert seen["folder"] == tmp_path and callable(seen["progress"])

    def test_update_without_a_terminal_and_without_yes_does_nothing_and_says_so(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "update_plan", lambda *a, **k: self.plan())
        monkeypatch.setattr(searxng_engine, "update", lambda *a, **k: pytest.fail("no yes, no update"))
        result = invoke("setup", "--update")
        assert result.exit_code == 1 and "needs your yes" in result.output.replace("\n", " ")

    def test_update_at_the_pin_says_so_and_asks_nothing(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "update_plan", lambda *a, **k: self.plan(needs=False))
        monkeypatch.setattr(searxng_engine, "update", lambda *a, **k: pytest.fail("nothing to update"))
        result = invoke("setup", "--update")
        assert result.exit_code == 0 and "nothing to update" in result.output

    def test_update_that_cannot_start_says_why_in_one_line(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine
        monkeypatch.setattr(searxng_engine, "update_plan", lambda *a, **k: (_ for _ in ()).throw(
            searxng_engine.EngineError("No search engine is set up, so there is nothing to update.")))
        result = invoke("setup", "--update", "--yes")
        assert result.exit_code == 1 and "nothing to update" in result.output and "Traceback" not in result.output

    def test_a_failed_update_says_why_in_one_line(self, framework, tmp_path, monkeypatch):
        framework.config.user_state_dir = str(tmp_path)
        from orchestrator.Core import searxng_engine, searxng_native
        monkeypatch.setattr(searxng_engine, "update_plan", lambda *a, **k: self.plan())
        monkeypatch.setattr(searxng_engine, "update", lambda *a, **k: (_ for _ in ()).throw(
            searxng_native.NativeError("Fetching SearXNG did not work (HTTP 403). The earlier stand is kept and still "
                                       "works.")))
        result = invoke("setup", "--update", "--yes")
        assert result.exit_code == 1 and "earlier stand is kept" in result.output and "Traceback" not in result.output

    def test_update_and_remove_together_are_refused(self, framework, tmp_path):
        framework.config.user_state_dir = str(tmp_path)
        assert invoke("setup", "--update", "--remove", "--yes").exit_code == 2


class TestWindow:

    @pytest.fixture
    def window(self, qapp, framework, monkeypatch):
        from orchestrator.gui.ditto_web_window import DittoWebWindow
        ran = []

        def setup_runner(way, folder, config, **kwargs):
            ran.append((way, folder))
            return SimpleNamespace(notes=[])
        removed = []
        win = DittoWebWindow(framework, None, setup_runner=setup_runner,
                             remove_runner=lambda folder, config: removed.append(folder) or ["removed"], lang="en")
        win.ran, win.removed = ran, removed
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

    def test_the_own_python_folder_is_the_default_way_and_docker_is_the_persons_choice(self, qapp, window):
        assert window.cb_engine_way.currentData() == "own" and window.cb_engine_way.count() == 2
        assert "no Docker" in window.cb_engine_way.itemText(0) and "Docker or Podman" in window.cb_engine_way.itemText(1)

    def test_setting_up_says_what_it_installs_and_nothing_happens_without_a_yes(self, qapp, window, framework, tmp_path,
                                                                                monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        asked = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: asked.append(text) or False)
        window.btn_setup.click()
        self.wait(qapp, window)
        assert len(asked) == 1 and "about 200 MB" in asked[0] and "no Docker" in asked[0] and "Set it up?" in asked[0]
        assert str(tmp_path / "searxng") in asked[0] and window.ran == []          # "No" changed nothing

    def test_a_yes_sets_it_up_the_chosen_way(self, qapp, window, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        window.btn_setup.click()
        self.wait(qapp, window)
        assert window.ran == [("own", tmp_path)] and "starts only for a search" in window.lbl_result.text()
        window.cb_engine_way.setCurrentIndex(window.cb_engine_way.findData("container"))
        window.btn_setup.click()
        self.wait(qapp, window)
        assert window.ran[-1] == ("container", tmp_path)

    def test_the_container_way_explains_the_image_and_the_engine(self, qapp, window, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        asked = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: asked.append(text) or False)
        window.cb_engine_way.setCurrentIndex(window.cb_engine_way.findData("container"))
        window.btn_setup.click()
        assert "Docker or Podman" in asked[0] and "docker.io/searxng/searxng" in asked[0] and "127.0.0.1:8888" in asked[0]

    def test_a_failed_setup_is_told_in_plain_words(self, qapp, framework, tmp_path, monkeypatch):
        from orchestrator.Core import searxng_native
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        told = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: told.append(text))

        def failing(way, folder, config, **kwargs):
            raise searxng_native.NativeError("Fetching SearXNG did not work (HTTP 403).")
        win = module.DittoWebWindow(framework, None, setup_runner=failing, lang="en")
        win.btn_setup.click()
        self.wait(qapp, win)
        assert told == ["Fetching SearXNG did not work (HTTP 403)."]
        win.close()

    def test_removing_asks_first_and_no_is_the_default(self, qapp, window, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        monkeypatch.setattr(module, "ask", lambda parent, title, text: False)
        window.btn_remove.click()
        self.wait(qapp, window)
        assert window.removed == []                                              # "No" changed nothing
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        window.btn_remove.click()
        self.wait(qapp, window)
        assert window.removed == [tmp_path] and "removed" in window.lbl_result.text()

    def plan(self, needs=True, way="own"):
        from orchestrator.Core import searxng_engine
        return searxng_engine.UpdatePlan(way, needs, "111111111111", "bbbbbbbbbbbb",
                                         "https://github.com/searxng/searxng/compare/1...2", [])

    def test_updating_says_what_changes_and_nothing_happens_without_a_yes(self, qapp, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        asked, ran = [], []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: asked.append(text) or False)
        win = module.DittoWebWindow(framework, None, plan_runner=lambda folder, config: self.plan(),
                                    update_runner=lambda folder, config: ran.append(folder), lang="en")
        win.btn_update.click()
        self.wait(qapp, win)
        assert len(asked) == 1 and "111111111111" in asked[0] and "bbbbbbbbbbbb" in asked[0]
        assert "compare/1...2" in asked[0] and "Update it?" in asked[0] and ran == []        # "No" changed nothing
        win.close()

    def test_a_yes_updates_and_the_result_is_told(self, qapp, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        ran = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        win = module.DittoWebWindow(framework, None, plan_runner=lambda folder, config: self.plan(),
                                    update_runner=lambda folder, config: ran.append(folder) or [], lang="en")
        win.btn_update.click()
        self.wait(qapp, win)
        assert ran == [tmp_path] and "was updated" in win.lbl_result.text()
        win.close()

    def test_at_the_pin_it_says_so_and_asks_nothing(self, qapp, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        monkeypatch.setattr(module, "ask", lambda parent, title, text: pytest.fail("nothing to update"))
        win = module.DittoWebWindow(framework, None, plan_runner=lambda folder, config: self.plan(needs=False),
                                    update_runner=lambda folder, config: pytest.fail("nothing to update"), lang="en")
        win.btn_update.click()
        assert "nothing to update" in win.lbl_result.text() and "bbbbbbbbbbbb" in win.lbl_result.text()
        win.close()

    def test_the_container_way_says_the_image_is_pulled_again(self, qapp, framework, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        asked = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: asked.append(text) or False)
        win = module.DittoWebWindow(framework, None, plan_runner=lambda folder, config: self.plan(way="container"),
                                    update_runner=lambda folder, config: pytest.fail("no yes"), lang="en")
        win.btn_update.click()
        assert "docker.io/searxng/searxng" in asked[0] and "pulled again" in asked[0]
        win.close()

    def test_a_plan_that_cannot_be_made_and_a_failed_update_are_told_in_plain_words(self, qapp, framework, tmp_path,
                                                                                   monkeypatch):
        from orchestrator.Core import searxng_engine, searxng_native
        from orchestrator.gui import ditto_web_window as module
        framework.config.user_state_dir = str(tmp_path)
        told = []
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: told.append(text))

        def no_plan(folder, config):
            raise searxng_engine.EngineError("No search engine is set up, so there is nothing to update.")
        win = module.DittoWebWindow(framework, None, plan_runner=no_plan, lang="en")
        win.btn_update.click()
        assert told == ["No search engine is set up, so there is nothing to update."]

        def failing(folder, config):
            raise searxng_native.NativeError("Fetching SearXNG did not work (HTTP 403).")
        win2 = module.DittoWebWindow(framework, None, plan_runner=lambda folder, config: self.plan(),
                                     update_runner=failing, lang="en")
        win2.btn_update.click()
        self.wait(qapp, win2)
        assert told[-1] == "Fetching SearXNG did not work (HTTP 403)."
        win.close()
        win2.close()

    def test_every_text_exists_in_both_languages_with_the_same_placeholders(self):
        import re
        from orchestrator.utils import l10n_websearch as texts
        english, german = texts.TEXTS["en"], texts.TEXTS["de"]
        assert set(english) == set(german) and len(english) > 40
        for key in english:
            assert re.findall(r"\{(\w+)\}", english[key]) == re.findall(r"\{(\w+)\}", german[key]), key
