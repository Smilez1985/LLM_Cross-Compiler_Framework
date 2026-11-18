"""SearXNG in the person's own Python folder (orchestrator/Core/searxng_native.py): the pinned install, the process with its
settings and secret on this computer's address only, the start that waits for /healthz, and Running, which starts it for a
search and stops only what it started. The system (the download, pip, the process) is played by fakes; the real run is
scripts/searxng_native_check.py, which CI runs on Linux, Windows and macOS."""

import importlib.util
import io
import os
import re
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orchestrator.Core import searxng_native as sn
from orchestrator.Core.searxng_native import NativeError, Running

ROOT = f"searxng-{sn.COMMIT}/"
FILES = {"setup.py": "setup", "requirements.txt": "flask==3.1.3\n", "searx/__init__.py": "",
         "searx/webapp.py": "app", "utils/templates/etc/uwsgi/apps-available/searxng.ini:socket": "x",
         "utils/templates/etc/nginx/default.apps-available/searxng.conf:socket": "x", "utils/searxng.sh": "script"}


def make_zip(comment=sn.COMMIT, files=None, root=ROOT):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.comment = comment.encode("ascii")
        bundle.writestr(root, "")
        for name, text in (FILES if files is None else files).items():
            bundle.writestr(root + name, text)
    return buffer.getvalue()


class FakeOpen:
    """urllib.request.urlopen: answers with the bytes it was given (or raises), and records the address asked."""

    def __init__(self, data=None, error=None):
        self.data, self.error, self.urls = data if data is not None else make_zip(), error, []

    def __call__(self, url, timeout=0):
        self.urls.append(url)
        if self.error:
            raise self.error
        return _Answer(io.BytesIO(self.data))


class _Answer:
    def __init__(self, stream):
        self.stream = stream

    def read(self, size=-1):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRun:
    """subprocess.run: records every command; `fail` is a word that makes the command containing it fail."""

    def __init__(self, fail=""):
        self.calls, self.fail = [], fail

    def __call__(self, command, cwd=None, env=None, capture_output=True, text=True, timeout=0):
        self.calls.append(list(command))
        if "-m" in command and command[command.index("-m") + 1] == "venv":
            python = sn.venv_python(Path(command[-1]).parent)     # command[-1] is <folder>/venv
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")
        if self.fail and self.fail in " ".join(command):
            return SimpleNamespace(returncode=1, stdout="", stderr="boom\nthe real reason")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class TestThePin:

    def test_the_commit_is_a_full_hash_and_the_requirements_are_python_310_or_newer(self):
        assert re.fullmatch(r"[0-9a-f]{40}", sn.COMMIT) and sn.MIN_PYTHON >= (3, 10)
        assert sn.ARCHIVE == f"https://github.com/searxng/searxng/archive/{sn.COMMIT}.zip"


class TestInstalling:

    def test_it_downloads_exactly_the_pinned_commit_and_installs_the_way_the_documentation_does(self, tmp_path):
        run, opener = FakeRun(), FakeOpen()
        sn.install(tmp_path, run=run, urlopen=opener)
        assert opener.urls == [sn.ARCHIVE]
        assert (sn.source_dir(tmp_path) / "searx" / "webapp.py").read_text() == "app"
        text = [" ".join(c) for c in run.calls]
        assert any("pip install -q --use-pep517 --no-build-isolation -e ." in t for t in text)
        helpers = next(t for t in text if "--upgrade" in t)
        assert all(h in helpers for h in ("pyyaml", "msgspec", "typing-extensions", "pybind11"))
        assert sn.installed(tmp_path)
        assert not (tmp_path / "searxng.zip").exists()             # the download is removed

    def test_the_server_templates_with_a_colon_in_their_name_are_left_out(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        assert not (sn.source_dir(tmp_path) / "utils" / "templates").exists()
        assert (sn.source_dir(tmp_path) / "utils" / "searxng.sh").is_file()

    def test_a_second_install_does_nothing(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        again, opener = FakeRun(), FakeOpen()
        sn.install(tmp_path, run=again, urlopen=opener)
        assert again.calls == [] and opener.urls == []

    def test_an_archive_that_is_not_the_pinned_commit_is_never_installed(self, tmp_path):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen(make_zip(comment="0" * 40)))
        assert "not the pinned stand" in str(e.value) and not sn.installed(tmp_path)

    def test_a_name_that_would_land_outside_the_folder_stops_the_install(self, tmp_path):
        evil = make_zip(files={"../../evil.txt": "x"})
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen(evil))
        assert "outside its folder" in str(e.value) and not (tmp_path.parent / "evil.txt").exists()

    def test_a_damaged_download_is_told(self, tmp_path):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen(b"not a zip at all"))
        assert "damaged" in str(e.value) and not sn.installed(tmp_path)

    @pytest.mark.parametrize("error, words", [
        (urllib.error.HTTPError(sn.ARCHIVE, 403, "no", {}, None), "HTTP 403"),
        (urllib.error.URLError("no route"), "URLError")])
    def test_a_failed_download_names_the_cause_without_a_traceback(self, tmp_path, error, words):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen(error=error))
        assert words in str(e.value) and not sn.installed(tmp_path)

    def test_a_failing_step_names_the_step_and_the_last_line_of_its_error(self, tmp_path):
        with pytest.raises(NativeError) as e:
            sn.install(tmp_path, run=FakeRun(fail="--no-build-isolation"), urlopen=FakeOpen())
        assert "installing SearXNG did not work: the real reason" in str(e.value) and not sn.installed(tmp_path)

    def test_an_older_stand_is_replaced_not_mixed(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "old.txt").write_text("old")
        (tmp_path / "installed.json").write_text('{"commit": "' + "1" * 40 + '"}')
        assert not sn.installed(tmp_path)
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        assert not (tmp_path / "src" / "old.txt").exists() and sn.installed(tmp_path)

    @pytest.mark.skipif(os.name == "nt", reason="the real interpreter on Windows is the CI job's to prove")
    def test_the_stand_in_for_pwd_is_put_only_where_windows_needs_it(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        assert not (tmp_path / "shim").exists()


def legacy(folder, commit=sn.COMMIT, source_file=None):
    """An installation made before the stands: `src` and `venv` directly in the folder, a record without `stand`."""
    folder = Path(folder)
    (folder / "installed.json").write_text('{"commit": "' + commit + '"}')
    python = sn.venv_python(folder)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("")
    (folder / "src").mkdir(exist_ok=True)
    if source_file:
        (folder / "src" / source_file).write_text("old")


def answering(events=None):
    """A process that is up (poll() None), and what it takes to stop it."""
    events = [] if events is None else events
    return SimpleNamespace(poll=lambda: None, terminate=lambda: events.append("terminate"), wait=lambda timeout=0: None,
                           kill=lambda: events.append("kill"))


class TestTheStands:

    def test_a_fresh_install_lives_in_a_stand_named_by_its_commit_and_the_record_names_it(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        stand = tmp_path / "stands" / sn.COMMIT[:12]
        assert sn.stand_of(tmp_path) == stand and (stand / "src" / "searx" / "webapp.py").is_file()
        assert sn.recorded(tmp_path)["stand"] == stand.name and not (tmp_path / "src").exists()
        assert not (tmp_path / "venv").exists()

    def test_an_installation_from_before_the_stands_works_as_it_is(self, tmp_path):
        legacy(tmp_path)
        settings = tmp_path / "settings.yml"
        settings.write_text("use_default_settings: true\n")
        seen = {}

        def popen(command, **kwargs):
            seen.update(command=command, **kwargs)
            return answering()
        assert sn.installed(tmp_path) and sn.stand_of(tmp_path) == tmp_path
        sn.start(tmp_path, settings, "s", 8891, popen=popen, check=lambda port: True)
        assert seen["cwd"] == str(tmp_path / "src") and seen["command"][0] == str(sn.venv_python(tmp_path))

    def test_a_record_that_names_a_stand_with_a_bad_name_is_not_followed(self, tmp_path):
        (tmp_path / "installed.json").write_text('{"commit": "' + sn.COMMIT + '", "stand": "../../elsewhere"}')
        assert sn.stand_of(tmp_path) == tmp_path

    def test_the_settings_file_and_the_secret_are_never_in_the_folder_of_a_stand(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        names = {p.name for p in sn.stand_of(tmp_path).iterdir()}
        assert names <= {"src", "venv", "shim"}

    def test_remove_takes_every_stand_and_the_parts_of_an_older_installation(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        (tmp_path / "src").mkdir()                       # a part of an installation made before the stands
        (tmp_path / "server.log").write_text("log")
        assert sn.remove(tmp_path) is True
        assert not (tmp_path / "stands").exists() and not (tmp_path / "src").exists()
        assert not (tmp_path / "installed.json").exists() and sn.remove(tmp_path) is False

    def test_a_free_port_is_one_on_this_computer_that_can_be_bound(self):
        import socket
        port = sn.free_port()
        assert isinstance(port, int) and 1024 <= port <= 65535
        with socket.socket() as probe:
            probe.bind((sn.HOST, port))


class TestUpdateInfo:

    def test_nothing_installed_is_nothing_to_update(self, tmp_path):
        info = sn.update_info(tmp_path)
        assert not info.present and not info.needs_update and info.current == "" and info.pinned == sn.COMMIT

    def test_an_install_at_the_pin_needs_nothing(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        info = sn.update_info(tmp_path)
        assert info.present and not info.needs_update and info.current == sn.COMMIT and info.link == sn.REPOSITORY

    def test_an_older_install_needs_the_update_and_the_link_shows_what_changed(self, tmp_path):
        legacy(tmp_path, commit="1" * 40)
        info = sn.update_info(tmp_path)
        assert info.present and info.needs_update and info.current == "1" * 40
        assert info.link == f"{sn.REPOSITORY}/compare/{'1' * 40}...{sn.COMMIT}"

    def test_a_damaged_stand_at_the_pin_is_repaired_by_the_update(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        sn.venv_python(tmp_path).unlink()
        info = sn.update_info(tmp_path)
        assert info.present and info.needs_update and not sn.installed(tmp_path)

    def test_a_record_without_a_commit_is_no_install(self, tmp_path):
        (tmp_path / "installed.json").write_text('{"stand": "aaaaaaaaaaaa"}')
        assert not sn.update_info(tmp_path).present


class TestUpdating:

    def run_update(self, folder, run=None, opener=None, popen=None, check=lambda port: True, **kwargs):
        settings = Path(folder) / "settings.yml"
        settings.write_text("use_default_settings: true\n")
        return sn.update(folder, settings, "SECRET", run=run or FakeRun(), urlopen=opener or FakeOpen(),
                         popen=popen or (lambda command, **k: answering()), check=check, port_fn=lambda: 8999, **kwargs)

    def test_the_new_stand_is_made_beside_the_old_one_checked_and_only_then_switched(self, tmp_path):
        legacy(tmp_path, commit="1" * 40, source_file="old.txt")
        seen = {}

        def popen(command, **kwargs):
            seen.update(command=command, env=kwargs["env"], cwd=kwargs["cwd"],
                        record_then=sn.recorded(tmp_path).get("stand"), old_still_there=(tmp_path / "src" / "old.txt").exists())
            return answering(seen.setdefault("stopped", []))
        assert self.run_update(tmp_path, popen=popen) == "updated"
        stand = tmp_path / "stands" / sn.COMMIT[:12]
        assert seen["record_then"] is None and seen["old_still_there"]          # nothing was switched or removed yet
        assert seen["cwd"] == str(stand / "src") and seen["env"]["SEARXNG_PORT"] == "8999"
        assert seen["env"]["SEARXNG_SECRET"] == "SECRET" and seen["stopped"] == ["terminate"]    # started once, stopped
        assert sn.stand_of(tmp_path) == stand and sn.installed(tmp_path) and not sn.update_info(tmp_path).needs_update
        assert not (tmp_path / "src").exists() and not (tmp_path / "venv").exists()       # the old parts went

    def test_an_older_named_stand_is_removed_after_the_switch(self, tmp_path):
        old = tmp_path / "stands" / "aaaaaaaaaaaa"
        python = sn._venv_python_of(old)
        python.parent.mkdir(parents=True)
        python.write_text("")
        (old / "src").mkdir()
        (tmp_path / "installed.json").write_text('{"commit": "' + "2" * 40 + '", "stand": "aaaaaaaaaaaa"}')
        assert self.run_update(tmp_path) == "updated"
        assert not old.exists() and sn.stand_of(tmp_path).name == sn.COMMIT[:12]

    def test_at_the_pin_there_is_nothing_to_do_and_nothing_is_fetched(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        run, opener = FakeRun(), FakeOpen()
        assert self.run_update(tmp_path, run=run, opener=opener) == "current"
        assert run.calls == [] and opener.urls == []

    def test_nothing_installed_is_told(self, tmp_path):
        with pytest.raises(NativeError) as e:
            self.run_update(tmp_path)
        assert "nothing to update" in str(e.value)

    def test_a_failing_step_leaves_the_old_stand_as_it_was(self, tmp_path):
        legacy(tmp_path, commit="1" * 40, source_file="old.txt")
        before = (tmp_path / "installed.json").read_text()
        with pytest.raises(NativeError) as e:
            self.run_update(tmp_path, run=FakeRun(fail="--no-build-isolation"))
        assert "installing SearXNG did not work" in str(e.value)
        assert (tmp_path / "src" / "old.txt").read_text() == "old" and (tmp_path / "installed.json").read_text() == before
        assert not (tmp_path / "stands" / sn.COMMIT[:12]).exists()                   # the new stand was removed again

    def test_a_download_that_is_not_the_pinned_commit_changes_nothing(self, tmp_path):
        legacy(tmp_path, commit="1" * 40, source_file="old.txt")
        with pytest.raises(NativeError) as e:
            self.run_update(tmp_path, opener=FakeOpen(make_zip(comment="0" * 40)))
        assert "not the pinned stand" in str(e.value) and (tmp_path / "src" / "old.txt").is_file()
        assert sn.recorded(tmp_path)["commit"] == "1" * 40

    def test_a_new_stand_that_does_not_answer_is_removed_and_the_old_one_keeps_working(self, tmp_path):
        legacy(tmp_path, commit="1" * 40, source_file="old.txt")
        with pytest.raises(NativeError) as e:
            self.run_update(tmp_path, popen=lambda command, **k: SimpleNamespace(poll=lambda: 1))
        assert "server.log" in str(e.value) and "earlier stand is kept" in str(e.value)
        assert not (tmp_path / "stands" / sn.COMMIT[:12]).exists()
        assert sn.recorded(tmp_path)["commit"] == "1" * 40 and (tmp_path / "src" / "old.txt").is_file()
        assert sn.stand_of(tmp_path) == tmp_path                                         # the old install is still the one in use

    def test_a_damaged_stand_at_the_pin_is_made_again(self, tmp_path):
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
        sn.venv_python(tmp_path).unlink()
        assert self.run_update(tmp_path) == "updated" and sn.installed(tmp_path)


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
        sn.install(tmp_path, run=FakeRun(), urlopen=FakeOpen())
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
        assert seen["command"][1:] == ["-m", "searx.webapp"] and seen["cwd"] == str(sn.source_dir(tmp_path))
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
