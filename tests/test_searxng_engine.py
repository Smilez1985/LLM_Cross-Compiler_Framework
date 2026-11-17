"""The search engine of the web search, both ways in one place (orchestrator/Core/searxng_engine.py): what the person is told
first, the setup of the own Python folder and of the container, the removal, and what is written to the settings. The install
and the engine are played by fakes (test_searxng_native, test_searxng_setup)."""

from pathlib import Path

import pytest
from test_searxng_setup import FakeClient, FakeContainer

from orchestrator.Core import searxng_engine as eng
from orchestrator.Core import searxng_setup
from orchestrator.Core.searxng_native import NativeError


class Config(dict):
    def set(self, key, value):
        self[key] = value

    def save_user_config(self):
        return True


class FakeRunning:
    """searxng_native.Running: records that SearXNG was started and stopped around the check."""
    events = []

    def __init__(self, folder, settings, secret, port):
        self.args = (folder, settings, secret, port)

    def __enter__(self):
        FakeRunning.events.append(("up", self.args[3]))
        return self

    def __exit__(self, *exc):
        FakeRunning.events.append(("down",))


@pytest.fixture(autouse=True)
def reset():
    FakeRunning.events = []


def installed(folder, progress=None):
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / "installed.json").write_text("{}")
    (Path(folder) / "src").mkdir(exist_ok=True)


class TestWhatThePersonIsToldFirst:

    def test_the_own_folder_says_what_it_installs_how_big_it_is_what_it_runs_with_and_how_to_remove_it(self, tmp_path):
        text = " ".join(eng.explain("own", tmp_path))
        assert "about 200 MB" in text and "your own rights" in text and "no Docker" in text
        assert "only while a search needs it" in text and str(tmp_path / "searxng" / "native") in text

    def test_the_container_says_it_needs_a_container_engine_and_the_image(self, tmp_path):
        text = " ".join(eng.explain("container", tmp_path))
        assert "Docker or Podman" in text and searxng_setup.IMAGE in text and "127.0.0.1" in text

    def test_an_unknown_way_is_refused(self, tmp_path):
        with pytest.raises(eng.EngineError):
            eng.explain("docker", tmp_path)
        assert eng.DEFAULT_WAY == "own"                         # no Docker unless the person wants it


class TestSettingUpTheOwnFolder:

    def test_it_installs_checks_that_it_answers_and_writes_the_setting(self, tmp_path):
        config = Config(web_search_ways=["api"], web_search_url="http://127.0.0.1:8888")
        result = eng.setup("own", tmp_path, config, install_fn=installed, running_cls=FakeRunning)
        assert (tmp_path / "searxng" / "native" / "installed.json").is_file()
        assert (tmp_path / "searxng" / "config" / "settings.yml").is_file() and (tmp_path / "searxng" / "secret").is_file()
        assert FakeRunning.events == [("up", 8888), ("down",)]                # started once, stopped again
        assert config["web_search_engine"] == "own" and config["web_search_ways"] == ["searxng", "api"]
        assert any("starts for a search" in n for n in result.notes)

    def test_the_port_comes_from_the_persons_address(self, tmp_path):
        eng.setup("own", tmp_path, Config(web_search_url="http://127.0.0.1:9123"), install_fn=installed,
                  running_cls=FakeRunning)
        assert FakeRunning.events[0] == ("up", 9123)

    def test_without_run_it_does_not_start_anything(self, tmp_path):
        eng.setup("own", tmp_path, Config(), run=False, install_fn=installed, running_cls=FakeRunning)
        assert FakeRunning.events == []

    def test_a_failed_install_changes_no_setting(self, tmp_path):
        def failing(folder, progress=None):
            raise NativeError("git is not installed")
        config = Config(web_search_ways=["api"])
        with pytest.raises(NativeError):
            eng.setup("own", tmp_path, config, install_fn=failing, running_cls=FakeRunning)
        assert "web_search_engine" not in config and config["web_search_ways"] == ["api"]

    def test_an_engine_that_does_not_answer_is_told_and_changes_no_setting(self, tmp_path):
        class Dead(FakeRunning):
            def __enter__(self):
                raise NativeError("The search engine did not answer within 45 seconds; see server.log in its folder.")
        config = Config()
        with pytest.raises(NativeError) as e:
            eng.setup("own", tmp_path, config, install_fn=installed, running_cls=Dead)
        assert "did not answer" in str(e.value) and "web_search_engine" not in config

    def test_a_second_setup_keeps_the_settings_file_the_person_may_have_changed(self, tmp_path):
        eng.setup("own", tmp_path, Config(), install_fn=installed, running_cls=FakeRunning)
        settings = tmp_path / "searxng" / "config" / "settings.yml"
        settings.write_text("use_default_settings: true\n# mine\n", encoding="utf-8")
        eng.setup("own", tmp_path, Config(), install_fn=installed, running_cls=FakeRunning)
        assert "# mine" in settings.read_text(encoding="utf-8")

    def test_no_state_folder_is_told(self):
        with pytest.raises(eng.EngineError):
            eng.setup("own", None, Config())
        with pytest.raises(eng.EngineError):
            eng.setup("docker", "x", Config())


class TestSettingUpTheContainer:

    def test_it_sets_up_the_container_and_writes_the_setting(self, tmp_path):
        client = FakeClient()
        config = Config()
        eng.setup("container", tmp_path, config, client_factory=lambda: client)
        assert client.created and client.container.status == "exited"           # made, started once, stopped
        assert config["web_search_engine"] == "container" and config["web_search_ways"] == ["searxng"]

    def test_no_engine_is_told_and_changes_no_setting(self, tmp_path):
        def none():
            raise RuntimeError("no daemon")
        config = Config()
        with pytest.raises(searxng_setup.SetupError) as e:
            eng.setup("container", tmp_path, config, client_factory=none)
        assert "No container engine" in str(e.value) and "web_search_engine" not in config


class TestRemoving:

    def test_it_removes_the_python_folder_and_the_container_and_keeps_settings_and_secret(self, tmp_path):
        client = FakeClient(existing=FakeContainer("exited"))
        config = Config(web_search_engine="own", web_search_ways=["searxng"])
        eng.setup("own", tmp_path, config, install_fn=installed, running_cls=FakeRunning)
        notes = eng.remove(tmp_path, config, client_factory=lambda: client)
        assert not (tmp_path / "searxng" / "native" / "src").exists() and client.container.removed
        assert (tmp_path / "searxng" / "secret").is_file() and (tmp_path / "searxng" / "config" / "settings.yml").is_file()
        assert config["web_search_engine"] == "" and any("Python folder" in n for n in notes)

    def test_nothing_to_remove_is_said(self, tmp_path):
        def none():
            raise RuntimeError("no daemon")
        notes = eng.remove(tmp_path, Config(), client_factory=none)
        assert notes == ["There was nothing to remove."]

    def test_an_unreachable_container_is_told_when_the_container_way_was_set_up(self, tmp_path):
        def none():
            raise RuntimeError("no daemon")
        notes = eng.remove(tmp_path, Config(web_search_engine="container"), client_factory=none)
        assert any("could not be reached" in n for n in notes)

    def test_current_reads_the_setting(self):
        assert eng.current(Config(web_search_engine="own")) == "own" and eng.current(Config()) == ""
        assert eng.current(Config(web_search_engine="bogus")) == ""
