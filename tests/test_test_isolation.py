"""The tests never touch the real home folder: every home and per-user folder variable points into a temporary folder
before anything imports the framework, each test gets its own home, and the session fails when the real home's
sensitive folders changed (compared by their listings; no file is ever opened)."""

import builtins
import ntpath
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest
from conftest import (CREDENTIAL_VARIABLES, SESSION_HOME, WINDOWS_ENVIRONMENT_LIMIT, home_snapshot, home_variables,
                      in_real_home, snapshot_changes)
from conftest import is_inside as _inside

TESTS = Path(__file__).resolve().parent


class TestRedirect:

    def test_every_home_variable_points_into_this_tests_home(self, tmp_path):
        for name in home_variables(tmp_path / "home"):
            if name == "LLM_FRAMEWORK_HOME":
                assert name not in os.environ, "the per-user folder stays unset unless a test sets it"
                continue
            if name == "HOMEDRIVE":
                continue  # checked with HOMEPATH: together they name the home
            value = os.environ.get(name)
            if name == "HOMEPATH":
                value = os.environ.get("HOMEDRIVE", "") + (value or "")
            assert value and _inside(value, tmp_path / "home"), f"{name}={value!r} is not this test's home"
            assert not in_real_home(value), f"{name} points into the real home"
        assert Path.home() == tmp_path / "home" and os.path.expanduser("~") == str(tmp_path / "home")

    def test_the_windows_profile_variables_name_the_test_home(self, monkeypatch):
        """On Windows, os.path.expanduser and Path.home() read USERPROFILE, then HOMEDRIVE + HOMEPATH; Git for Windows
        and OpenSSH read HOME; programs keep their files in APPDATA and LOCALAPPDATA. All of them point into the test
        home (shown with ntpath, so this runs on every host)."""
        home = r"C:\Users\someone\AppData\Local\Temp\pytest-of-someone\pytest-1\test0\home"
        variables = home_variables(home, pathmod=ntpath)
        assert variables["HOMEDRIVE"] == "C:" and variables["HOMEDRIVE"] + variables["HOMEPATH"] == home
        assert variables["APPDATA"] == home + r"\AppData\Roaming"
        assert variables["LOCALAPPDATA"] == home + r"\AppData\Local"
        assert all(v.startswith(home) for k, v in variables.items() if k not in ("HOMEDRIVE", "HOMEPATH"))
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        assert ntpath.expanduser("~") == home
        monkeypatch.delenv("USERPROFILE")
        assert ntpath.expanduser("~") == home, "HOMEDRIVE + HOMEPATH name the same home"
        unc = home_variables(r"\\server\share\t\home", pathmod=ntpath)
        assert (unc["HOMEDRIVE"], unc["HOMEPATH"]) == (r"\\server\share", r"\t\home")

    @pytest.mark.skipif(os.name == "nt", reason="paths without a drive exist only on POSIX")
    def test_without_a_drive_homedrive_is_removed_and_homepath_is_the_home(self, tmp_path):
        variables = home_variables(tmp_path / "home")
        assert (variables["HOMEDRIVE"], variables["HOMEPATH"]) == ("", str(tmp_path / "home"))
        assert "HOMEDRIVE" not in os.environ, "a variable without a value is removed, not set to an empty string"
        assert os.environ["HOMEPATH"] == str(tmp_path / "home")

    def test_credentials_of_the_developers_shell_are_removed(self):
        for name in CREDENTIAL_VARIABLES:
            assert name not in os.environ, name

    def test_libraries_imported_during_the_session_do_not_see_the_real_home(self):
        constants = pytest.importorskip("huggingface_hub.constants")
        for path in (constants.HF_HOME, constants.HF_TOKEN_PATH, constants.HF_STORED_TOKENS_PATH,
                     constants.HF_HUB_CACHE):
            assert not in_real_home(path), path

    def test_the_session_redirect_comes_before_any_import(self, tmp_path):
        """A fresh interpreter that imports conftest and then huggingface_hub: the library's token and cache paths are
        inside the session's temporary home, never in the home folder the process started with."""
        pytest.importorskip("huggingface_hub")
        started_with = tmp_path / "started-home"
        started_with.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in home_variables(tmp_path)}
        env.update(HOME=str(started_with), TMPDIR=str(tmp_path), PYTHONPATH=str(TESTS))
        code = ("import conftest, huggingface_hub.constants as c; "
                "print(c.HF_TOKEN_PATH); print(c.HF_HUB_CACHE); print(conftest.SESSION_HOME)")
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True,
                             timeout=120, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, out.stderr[-2000:]
        token_path, hub, session_home = out.stdout.strip().splitlines()[-3:]
        for path in (token_path, hub):
            assert not _inside(path, started_with), path
            assert _inside(path, Path(session_home)), path

    def test_the_session_home_is_a_temporary_folder(self):
        assert _inside(SESSION_HOME, tempfile.gettempdir()) and not in_real_home(SESSION_HOME)

    def test_the_temporary_folder_inside_the_profile_is_not_the_real_home(self, tmp_path):
        """Windows keeps the temporary folder in the profile (AppData\\Local\\Temp): a test home there is not the real
        home, while the profile's own folders (.ssh, AppData\\Roaming) still are."""
        profile = tmp_path / "Users" / "someone"
        temp = profile / "AppData" / "Local" / "Temp"
        for folder in (temp / "pytest-1" / "home", profile / ".ssh", profile / "AppData" / "Roaming"):
            folder.mkdir(parents=True)
        assert not in_real_home(temp / "pytest-1" / "home", homes=[profile], temp=temp)
        assert in_real_home(profile / ".ssh", homes=[profile], temp=temp)
        assert in_real_home(profile / "AppData" / "Roaming", homes=[profile], temp=temp)
        assert not in_real_home(tmp_path / "elsewhere", homes=[profile], temp=temp)

    def test_every_test_id_fits_a_windows_environment_variable(self, request):
        """pytest puts the running test's id into PYTEST_CURRENT_TEST; Windows refuses a variable over 32767
        characters, and such a test errors in setup and teardown there (a long parameter needs a short id)."""
        too_long = [item.nodeid[:80] + "..." for item in request.session.items
                    if len(f"PYTEST_CURRENT_TEST={item.nodeid} (teardown)") > WINDOWS_ENVIRONMENT_LIMIT]
        assert not too_long

    def test_no_test_reaches_the_operating_systems_keyring(self, tmp_path):
        """Secret Service, the macOS Keychain and the Windows Credential Manager live outside the temporary home: a
        test that stored a secret there would change the developer's real keyring. Every test gets a file keyring in
        its own home (or one that keeps nothing), and the desktop's session bus is gone."""
        assert os.environ.get("PYTHON_KEYRING_BACKEND") in ("keyrings.alt.file.PlaintextKeyring",
                                                            "keyring.backends.null.Keyring")
        assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ
        keyring = pytest.importorskip("keyring")
        backend = keyring.get_keyring()
        assert f"{type(backend).__module__}.{type(backend).__qualname__}" == os.environ["PYTHON_KEYRING_BACKEND"]
        path = getattr(backend, "file_path", None)
        if path is not None:
            assert _inside(path, tmp_path / "home"), path

    def test_a_fresh_interpreter_gets_the_test_keyring_too(self, tmp_path):
        pytest.importorskip("keyring")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHON_KEYRING_BACKEND",)}
        env.update(HOME=str(tmp_path), TMPDIR=str(tmp_path), PYTHONPATH=str(TESTS),
                   DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/bus")
        code = ("import os, conftest, keyring; b = keyring.get_keyring(); "
                "print(type(b).__module__ + '.' + type(b).__qualname__); "
                "print('DBUS_SESSION_BUS_ADDRESS' in os.environ)")
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True,
                             timeout=120, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, out.stderr[-2000:]
        backend, bus = out.stdout.strip().splitlines()[-2:]
        assert backend in ("keyrings.alt.file.PlaintextKeyring", "keyring.backends.null.Keyring"), backend
        assert bus == "False"


class TestGuard:

    def test_added_changed_removed_and_created_entries_are_found(self, tmp_path):
        home = tmp_path / "real"
        (home / ".ssh").mkdir(parents=True)
        key = home / ".ssh" / "known_hosts"
        key.write_text("a")
        before = home_snapshot(home)
        (home / ".ssh" / "id_ed25519").write_text("x")      # added
        key.write_text("ab")                                 # changed (size)
        (home / ".llm-framework").mkdir()                    # a root that did not exist
        (home / ".llm-framework" / "audit.db").write_text("")
        changes = snapshot_changes(before, home_snapshot(home))
        assert "added .ssh/id_ed25519" in changes
        assert "changed .ssh/known_hosts" in changes
        assert "changed .llm-framework" in changes and "added .llm-framework/audit.db" in changes
        key.unlink()
        assert "removed .ssh/known_hosts" in snapshot_changes(before, home_snapshot(home))

    def test_a_deep_change_in_the_hugging_face_cache_is_found(self, tmp_path):
        home = tmp_path / "real"
        snapshot = home / ".cache" / "huggingface" / "hub" / "models--org--m" / "snapshots" / "abc"
        snapshot.mkdir(parents=True)
        before = home_snapshot(home)
        (snapshot / "config.json").write_text("{}")
        (home / ".cache" / "huggingface" / "token").write_text("t")
        changes = snapshot_changes(before, home_snapshot(home))
        assert "added .cache/huggingface/hub/models--org--m/snapshots/abc/config.json" in changes
        assert "added .cache/huggingface/token" in changes

    def test_content_caches_other_programs_change_are_skipped(self, tmp_path):
        home = tmp_path / "real"
        (home / ".cache" / "huggingface" / "xet").mkdir(parents=True)
        before = home_snapshot(home)
        (home / ".cache" / "huggingface" / "xet" / "chunk").write_text("x")
        assert snapshot_changes(before, home_snapshot(home)) == []

    def test_no_file_is_opened_and_no_link_followed(self, tmp_path, monkeypatch):
        home = tmp_path / "real"
        (home / ".ssh").mkdir(parents=True)
        (home / ".ssh" / "id_ed25519").write_text("private")
        outside = tmp_path / "outside"
        (outside / "deep").mkdir(parents=True)
        (home / ".ssh" / "link").symlink_to(outside)

        def refuse(*args, **kwargs):
            raise AssertionError("the guard opened a file")
        monkeypatch.setattr(builtins, "open", refuse)
        monkeypatch.setattr(os, "open", refuse)
        found = home_snapshot(home)
        assert found[".ssh/id_ed25519"][0] == "file" and found[".ssh/link"][0] == "link"
        assert not any("deep" in p for p in found)

    def test_a_changed_real_home_fails_the_session(self, tmp_path, monkeypatch):
        home = tmp_path / "real"
        (home / ".ssh").mkdir(parents=True)
        monkeypatch.setattr(conftest, "REAL_HOME_SNAPSHOTS", {home: home_snapshot(home)})
        lines = []
        reporter = SimpleNamespace(write_sep=lambda *a, **k: None, write_line=lines.append)
        session = SimpleNamespace(exitstatus=pytest.ExitCode.OK, config=SimpleNamespace(
            pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter)))
        conftest.pytest_sessionfinish(session, 0)
        assert session.exitstatus == pytest.ExitCode.OK and not lines
        (home / ".ssh" / "id_ed25519").write_text("x")
        conftest.pytest_sessionfinish(session, 0)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        assert "added .ssh/id_ed25519" in lines[0] and "x" not in lines[0].split("id_ed25519", 1)[1]
        assert "\\" not in lines[0], "paths are reported with '/' on every platform"
