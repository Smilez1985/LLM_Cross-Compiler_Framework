"""Spoken announcements: the OS speech engine through subprocess (always faked here), best effort and silent
wherever nobody sits at the speaker."""

import os
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from orchestrator.utils import announce as an

ENGINES = {"spd-say", "espeak-ng", "espeak", "say", "powershell.exe"}


class Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.fixture
def audible(monkeypatch):
    """Returns a function that makes announcements audible (a Linux desktop by default). It is called in the
    test body, because pytest sets PYTEST_CURRENT_TEST again right before the test runs."""

    def enable(platform="linux", which=None):
        for var in ("LLM_FRAMEWORK_NO_AUDIO", "CI", "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "WAYLAND_DISPLAY",
                    "PYTEST_CURRENT_TEST"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr(an, "_platform", lambda: platform)
        tools = {"spd-say": "/usr/bin/spd-say", "espeak-ng": "/usr/bin/espeak-ng"} if which is None else which
        monkeypatch.setattr(an.shutil, "which", lambda name: tools.get(name))
        an._mac_voice.cache_clear()

    yield enable
    an._mac_voice.cache_clear()


@pytest.fixture
def runs(monkeypatch):
    """Fake subprocess.run: records (argv, kwargs); answers come from `results` (returncode, or an exception)."""
    calls, results = [], {}

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        result = results.get(os.path.basename(argv[0]), 0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(argv, kwargs)
        return SimpleNamespace(returncode=result, stdout="")

    monkeypatch.setattr(an.subprocess, "run", run)
    return SimpleNamespace(calls=calls, results=results)


def say(text, **kwargs):
    thread = an.announce(text, **kwargs)
    if thread is not None:
        thread.join(5)
        assert not thread.is_alive()
    return thread


class TestEngines:

    def test_linux_uses_spd_say_in_the_ui_language(self, audible, runs):
        audible()
        assert say("Deep Ingest abgeschlossen.", config=Config(language="de"))
        assert runs.calls[0][0] == ["/usr/bin/spd-say", "-w", "-l", "de", "--", "Deep Ingest abgeschlossen."]
        assert runs.calls[0][1]["stdin"] is subprocess.DEVNULL and runs.calls[0][1]["timeout"] == an.TIMEOUT
        assert len(runs.calls) == 1

    def test_a_failing_engine_falls_back_to_the_next(self, audible, runs):
        audible()
        runs.results["spd-say"] = 1
        say("-v x; rm -rf / $(id)", lang="en")
        argv, kwargs = runs.calls[1]
        assert argv == ["/usr/bin/espeak-ng", "-v", "en", "--stdin"]
        assert kwargs["input"] == b"-v x; rm -rf / $(id)"   # text on stdin, never parsed as options

    def test_macos_say_reads_stdin_with_a_voice_of_the_language(self, audible, runs):
        audible("darwin", {"say": "/usr/bin/say"})
        runs.results["say"] = lambda argv, kw: SimpleNamespace(returncode=0, stdout=(
            "Albert              de_DE    # Hallo\n"
            "Anna                de_DE    # Hallo, ich heiße Anna.\n"
            "Samantha            en_US    # Hello, my name is Samantha.\n") if argv[1:] == ["-v", "?"] else "")
        say("Bitte lesen Sie den Hinweis.", lang="de")
        assert runs.calls[-1][0] == ["/usr/bin/say", "-v", "Anna"]
        assert runs.calls[-1][1]["input"] == "Bitte lesen Sie den Hinweis.".encode()

    def test_macos_without_a_voice_for_the_language_keeps_the_system_voice(self, audible, runs):
        audible("darwin", {"say": "/usr/bin/say"})
        say("Hello", lang="fr")
        assert runs.calls[-1][0] == ["/usr/bin/say"]

    def test_windows_passes_text_and_language_only_through_the_environment(self, audible, runs, monkeypatch):
        audible("win32", {})
        exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        monkeypatch.setattr(an, "_windows_powershell", lambda: exe)
        text = "Page 1'; Remove-Item C:\\ -Recurse; \"$(whoami)\""

        say(text, lang="de-DE")

        argv, kwargs = runs.calls[0]
        assert argv[:5] == [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
        assert argv[5] == an._WINDOWS_SCRIPT and all(text not in a and "whoami" not in a for a in argv)
        assert kwargs["env"]["LCF_TTS_TEXT"] == text and kwargs["env"]["LCF_TTS_LANG"] == "de"
        assert kwargs["creationflags"] and "shell" not in kwargs

    def test_powershell_comes_from_the_system_folder_only(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SystemRoot", str(tmp_path))
        assert an._windows_powershell() is None   # not looked up on PATH or in the working directory
        exe = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        assert an._windows_powershell() == str(exe)

    @pytest.mark.parametrize("platform,which", [
        ("linux", {"spd-say": "/x/spd-say", "espeak-ng": "/x/espeak-ng", "espeak": "/x/espeak"}),
        ("darwin", {"say": "/x/say"}), ("win32", {}),
    ])
    def test_only_speech_engines_are_ever_started(self, audible, runs, monkeypatch, platform, which):
        """Never a mixer or volume tool (rag4ll's player set the master volume to 100 %)."""
        audible(platform, which)
        monkeypatch.setattr(an, "_windows_powershell", lambda: r"C:\W\powershell.exe")
        for name in which:
            runs.results[name] = 1   # every engine fails, so all of them are tried
        runs.results["powershell.exe"] = 1
        say("Hello")
        names = {os.path.basename(argv[0].replace("\\", "/")) for argv, _ in runs.calls}
        assert names and names <= ENGINES

    def test_no_engine_is_silent_and_harmless(self, audible, runs):
        audible("linux", {})
        assert say("Hello")
        assert runs.calls == []

    def test_language_and_text_are_sanitized(self, audible, runs):
        audible()
        say("Line one\n\x1b[31mline two\u202e" + "x" * 500, lang="../../etc")
        argv = runs.calls[0][0]
        assert argv[3] == "en" and argv[-1].startswith("Line one [31mline two x") and len(argv[-1]) == an.MAX_CHARS


class TestBestEffort:

    @pytest.mark.parametrize("error", [FileNotFoundError("spd-say"), subprocess.TimeoutExpired("spd-say", 30),
                                       PermissionError("denied"), RuntimeError("boom")])
    def test_errors_are_swallowed_and_the_next_cue_still_plays(self, audible, runs, error):
        audible()
        runs.results["spd-say"] = error
        runs.results["espeak-ng"] = error
        say("first")
        runs.results.clear()
        say("second")
        assert runs.calls[-1][0][-1] == "second"

    def test_announce_returns_before_the_engine_finishes(self, audible, runs):
        audible()
        release = threading.Event()
        runs.results["spd-say"] = lambda argv, kw: SimpleNamespace(returncode=0 if release.wait(5) else 1)

        start = time.monotonic()
        thread = an.announce("slow")
        assert time.monotonic() - start < 0.5 and thread.is_alive()
        release.set()
        thread.join(5)
        assert not thread.is_alive()

    def test_one_announcement_at_a_time(self, audible, runs):
        audible()
        active, peak, lock = [0], [0], threading.Lock()

        def engine(argv, kw):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return SimpleNamespace(returncode=0)

        runs.results["spd-say"] = engine
        threads = [an.announce(f"cue {i}") for i in range(3)]
        for thread in threads:
            thread.join(5)
        assert peak[0] == 1 and len(runs.calls) == 3

    def test_a_cue_waiting_too_long_is_dropped(self, audible, runs, monkeypatch):
        audible()
        monkeypatch.setattr(an, "TIMEOUT", 0.05)
        assert an._lock.acquire()
        try:
            say("dropped")
        finally:
            an._lock.release()
        assert runs.calls == []


class TestSilence:

    @pytest.mark.parametrize("env,platform,config", [
        ({"LLM_FRAMEWORK_NO_AUDIO": "1"}, "linux", None),
        ({"CI": "true"}, "linux", None),
        ({"SSH_CONNECTION": "10.0.0.2 50000 10.0.0.1 22", "DISPLAY": ""}, "darwin", None),
        ({"SSH_TTY": "/dev/pts/1", "DISPLAY": ""}, "win32", None),
        ({"DISPLAY": "", "WAYLAND_DISPLAY": ""}, "linux", None),     # headless, e.g. Docker
        ({}, "linux", {an.SETTING: False}),
    ])
    def test_silent_cases_never_start_a_process(self, audible, runs, monkeypatch, env, platform, config):
        audible(platform, {"spd-say": "/x/spd-say", "say": "/x/say"})
        monkeypatch.setattr(an, "_windows_powershell", lambda: r"C:\W\powershell.exe")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert an.announce("Hello", config=Config(config or {})) is None
        assert runs.calls == []

    def test_silent_under_pytest(self, runs, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_NO_AUDIO")
        monkeypatch.setenv("DISPLAY", ":0")
        assert "PYTEST_CURRENT_TEST" in os.environ
        assert an.silent_reason() == "running under tests" and an.announce("Hello") is None

    def test_no_audio_switch_values(self, monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_NO_AUDIO", "0")
        assert an.silent_reason() != "LLM_FRAMEWORK_NO_AUDIO is set"
        monkeypatch.setenv("LLM_FRAMEWORK_NO_AUDIO", "yes")
        assert an.silent_reason() == "LLM_FRAMEWORK_NO_AUDIO is set"

    def test_ssh_with_a_display_may_speak(self, audible, runs, monkeypatch):
        audible()
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 50000 10.0.0.1 22")
        assert an.silent_reason() is None

    def test_an_unreadable_setting_means_silence(self, audible, runs):
        audible()

        class Broken:
            def get(self, *args):
                raise OSError("config gone")

        assert an.announce("Hello", config=Broken()) is None and runs.calls == []

    def test_the_text_is_logged_even_when_silent(self, caplog):
        with caplog.at_level("INFO", logger=an.__name__):
            assert an.announce("Deep Ingest failed: nothing was stored.") is None
        assert "Announcement: Deep Ingest failed: nothing was stored." in caplog.text

    def test_the_real_config_default_is_on_and_can_be_switched_off(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager

        cfg = ConfigManager(tmp_path)
        assert cfg.get(an.SETTING) is True
        assert cfg.set(an.SETTING, False) and an.silent_reason(cfg) == f"the setting {an.SETTING} is off"
