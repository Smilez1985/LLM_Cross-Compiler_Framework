"""SearXNG in the person's own Python folder (orchestrator/Core/searxng_native.py): the pinned install, the process with its
settings and secret on this computer's address only, the start that waits for /healthz, and Running, which starts it for a
search and stops only what it started. The system (git, pip, the process) is played by fakes; the real run is
scripts/searxng_native_check.py, which CI runs on Linux, Windows and macOS."""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orchestrator.Core import searxng_native as sn
from orchestrator.Core.searxng_native import NativeError, Running


class FakeRun:
    """subprocess.run: records every command; `fail` is a word that makes the command containing it fail."""

    def __init__(self, fail="", head=sn.COMMIT):
        self.calls, self.fail, self.head = [], fail, head

    def __call__(self, command, cwd=None, env=None, capture_output=True, text=True, timeout=0):
        self.calls.append(list(command))
        if "venv" in command and "-m" in command and "venv" == command[command.index("-m") + 1]:
            python = sn.venv_python(Path(command[-1]).parent)     # command[-1] is <folder>/venv
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")
        if "init" in command and command[0] == "git":
            Path(command[-1]).mkdir(parents=True, exist_ok=True)
        if self.fail and self.fail in " ".join(command):
            return SimpleNamespace(returncode=1, stdout="", stderr="boom\nthe real reason")
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout=self.head + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def have_git(name):
    return "/usr/bin/git" if name == "git" else None


class TestThePin:

    def test_the_commit_is_a_full_hash_and_the_requirements_are_python_310_or_newer(self):
        assert re.fullmatch(r"[0-9a-f]{40}", sn.COMMIT) and sn.MIN_PYTHON >= (3, 10)
        assert sn.REPOSITORY.startswith("https://github.com/searxng/searxng")


class TestInstalling:

    def test_it_fetches_exactly_the_pinned_commit_and_installs_the_way_the_documentation_does(self, tmp_path):
        run = FakeRun()
        sn.install(tmp_path, run=run, which=have_git)
        text = [" ".join(c) for c in run.calls]
        assert any(f"fetch -q --depth 1 {sn.REPOSITORY} {sn.COMMIT}" in t for t in text)
        assert any("checkout -q FETCH_HEAD" in t for t in text)
        assert any("pip install -q --use-pep517 --no-build-isolation -e ." in t for t in text)
        helpers = next(t for t in text if "--upgrade" in t)
        assert all(h in helpers for h in ("pyyaml", "msgspec", "typing-extensions", "pybind11"))
        assert sn.installed(tmp_path)

    def test_a_second_install_does_nothing(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), which=have_git)
        again = FakeRun()
        sn.install(tmp_path, run=again, which=have_git)
        assert again.calls == []

    def test_files_that_are_not_the_pinned_commit_are_never_installed(self, tmp_path):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(head="0" * 40), which=have_git)
        assert "not the pinned stand" in str(e.value) and not sn.installed(tmp_path)

    def test_a_missing_git_is_told_and_nothing_is_changed(self, tmp_path):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(), which=lambda name: None)
        assert "git is not installed" in str(e.value) and not (tmp_path / "src").exists()

    def test_a_failing_step_names_the_step_and_the_last_line_of_its_error(self, tmp_path):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(fail="--no-build-isolation"), which=have_git)
        assert "installing SearXNG did not work: the real reason" in str(e.value) and not sn.installed(tmp_path)

    def test_an_older_stand_is_replaced_not_mixed(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "old.txt").write_text("old")
        (tmp_path / "installed.json").write_text('{"commit": "' + "1" * 40 + '"}')
        assert not sn.installed(tmp_path)
        sn.install(tmp_path, run=FakeRun(), which=have_git)
        assert not (tmp_path / "src" / "old.txt").exists() and sn.installed(tmp_path)

    @pytest.mark.skipif(os.name == "nt", reason="the real interpreter on Windows is the CI job's to prove")
    def test_the_stand_in_for_pwd_is_put_only_where_windows_needs_it(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), which=have_git)
        assert not (tmp_path / "shim").exists()


class TestTheStandIn:

    def load(self):
        spec = importlib.util.spec_from_file_location("pwd_standin", sn.SHIM)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_it_answers_what_searxng_asks_of_pwd(self):
        entry = self.load().getpwuid(1000)
        assert entry.pw_uid == 1000 and isinstance(entry.pw_name, str) and entry.pw_name

    def test_it_is_the_only_posix_only_thing_searxng_needs_and_says_so(self):
        text = sn.SHIM.read_text(encoding="utf-8")
        assert "valkeydb" in text and "Windows" in text and "Linux and macOS" in text


class TestStarting:

    def ready(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), which=have_git)
        settings = tmp_path / "settings.yml"
        settings.write_text("use_default_settings: true\n")
        return settings

    def test_the_process_gets_the_settings_the_secret_and_this_computers_address_only(self, tmp_path):
        settings = self.ready(tmp_path)
        seen = {}

        def popen(command, **kwargs):
            seen.update(command=command, **kwargs)
            return SimpleNamespace(poll=lambda: None)
        sn.start(tmp_path, settings, "SECRET", 8891, popen=popen, check=lambda port: True)
        env = seen["env"]
        assert seen["command"][1:] == ["-m", "searx.webapp"] and seen["cwd"] == str(tmp_path / "src")
        assert env["SEARXNG_SETTINGS_PATH"] == str(settings) and env["SEARXNG_SECRET"] == "SECRET"
        assert env["SEARXNG_BIND_ADDRESS"] == "127.0.0.1" and env["SEARXNG_PORT"] == "8891"

    def test_it_waits_until_the_engine_answers(self, tmp_path):
        settings = self.ready(tmp_path)
        answers = iter([False, False, True])
        process = SimpleNamespace(poll=lambda: None)
        got = sn.start(tmp_path, settings, "s", 8891, popen=lambda c, **k: process, check=lambda p: next(answers),
                       pause=lambda s: None)
        assert got is process

    def test_an_engine_that_ends_at_once_is_told_with_the_place_of_its_log(self, tmp_path):
        settings = self.ready(tmp_path)
        with pytest.raises(NativeError) as e:
            sn.start(tmp_path, settings, "s", 8891, popen=lambda c, **k: SimpleNamespace(poll=lambda: 1),
                     check=lambda p: False)
        assert "server.log" in str(e.value)

    def test_one_that_never_answers_is_stopped_and_told(self, tmp_path):
        settings = self.ready(tmp_path)
        stopped = []
        process = SimpleNamespace(poll=lambda: None, terminate=lambda: stopped.append("t"),
                                  wait=lambda timeout=0: None, kill=lambda: stopped.append("k"))
        ticks = iter(range(0, 1000, 20))
        with pytest.raises(NativeError) as e:
            sn.start(tmp_path, settings, "s", 8891, popen=lambda c, **k: process, check=lambda p: False,
                     timeout=45, clock=lambda: next(ticks), pause=lambda s: None)
        assert "did not answer within 45 seconds" in str(e.value) and stopped == ["t"]

    def test_not_installed_is_told(self, tmp_path):
        with pytest.raises(NativeError):
            sn.start(tmp_path, tmp_path / "s.yml", "s", 8891)

    def test_stop_asks_then_insists_and_never_raises(self):
        calls = []

        class Stubborn:
            def poll(self):
                return None

            def terminate(self):
                calls.append("terminate")

            def wait(self, timeout=0):
                calls.append("wait")
                if calls.count("wait") == 1:
                    raise subprocess.TimeoutExpired("x", 1)

            def kill(self):
                calls.append("kill")
        sn.stop(Stubborn())
        assert calls == ["terminate", "wait", "kill", "wait"]
        sn.stop(SimpleNamespace(poll=lambda: (_ for _ in ()).throw(RuntimeError("gone"))))


class TestRunning:

    def make(self, tmp_path, alive=False):
        events = []
        state = {"up": alive}

        def start(folder, settings, secret, port):
            events.append("start")
            state["up"] = True
            return "process"

        def stop(process):
            events.append(f"stop {process}")
            state["up"] = False
        return Running(tmp_path, tmp_path / "s.yml", "secret", 8891, start_fn=start, stop_fn=stop,
                       check=lambda port: state["up"]), events

    def test_it_starts_for_a_search_and_stops_right_after(self, tmp_path):
        running, events = self.make(tmp_path)
        with running:
            assert events == ["start"]
        assert events == ["start", "stop process"]

    def test_searches_at_the_same_time_share_one_start_and_the_last_one_stops_it(self, tmp_path):
        running, events = self.make(tmp_path)
        with running:
            with running:
                assert events == ["start"]
            assert events == ["start"]                       # one search is still going
        assert events == ["start", "stop process"]

    def test_an_engine_the_person_started_is_used_and_never_stopped(self, tmp_path):
        running, events = self.make(tmp_path, alive=True)
        with running:
            pass
        assert events == []

    def test_it_stops_even_when_the_search_fails(self, tmp_path):
        running, events = self.make(tmp_path)
        with pytest.raises(RuntimeError):
            with running:
                raise RuntimeError("search failed")
        assert events == ["start", "stop process"]

    def test_the_settings_file_of_the_repository_is_what_it_is_given(self):
        data = yaml.safe_load((Path(sn.__file__).resolve().parents[2] / "configs" / "searxng" / "settings.yml")
                              .read_text(encoding="utf-8"))
        assert data["search"]["formats"] == ["html", "json"]


def test_the_check_script_exists_and_names_the_module_it_proves():
    script = (Path(sn.__file__).resolve().parents[2] / "scripts" / "searxng_native_check.py").read_text(encoding="utf-8")
    assert "searxng_native" in script and "format=csv" in script and sys.version_info >= (3, 10)
