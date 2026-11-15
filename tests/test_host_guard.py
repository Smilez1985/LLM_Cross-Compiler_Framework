"""The host guard of the test suite (tests/host_guard.py; DESIGN round 100): a real package manager or service command
warns and still runs, a stub in a test's own folder never warns, and the guard never breaks a test."""

import os
import stat
import subprocess
import sys

import pytest

import host_guard
from host_guard import HostChangeWarning, changes_host, guarded_init

# The folders are resolved the way the guard resolves them (`stub_roots`): on Windows "/tmp/run" is "D:\\tmp\\run".
RUN = os.path.realpath(os.path.join(os.sep, "tmp", "run"))
ROOTS = [RUN]


def real(program):
    """`which` that finds every program outside the run's folder (a real one)."""
    return os.path.join(os.sep, "usr", "bin", os.path.basename(program))


def stub(program):
    return os.path.join(RUN, "bin", os.path.basename(program))


def nowhere(program):
    return None


class TestWhatChangesTheHost:

    @pytest.mark.parametrize("command", [
        ["apt-get", "install", "-y", "p7zip-full"], ["sudo", "apt", "remove", "git"], ["dpkg", "-i", "x.deb"],
        ["dnf", "install", "git"], ["pacman", "-S", "git"], ["apk", "add", "git"], ["brew", "install", "git"],
        ["systemctl", "start", "ssh"], ["sudo", "-n", "systemctl", "enable", "docker"], ["service", "ssh", "restart"],
        ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "curl"], ["sshd", "-D"],
        "apt-get install -y p7zip-full", "sudo systemctl restart nginx", ["/usr/bin/apt-get", "upgrade"],
        ["winget.exe", "install", "7zip.7zip"],
    ])
    def test_a_real_installing_or_service_command_is_flagged(self, command):
        assert changes_host(command, ROOTS, real) != ""

    @pytest.mark.parametrize("command", [
        ["apt-get", "--version"], ["apt-cache", "policy", "git"], ["dpkg", "-l"], ["dpkg", "--version"],
        ["systemctl", "status", "ssh"], ["systemctl", "is-active", "ssh"], ["service", "ssh", "status"],
        ["git", "status"], [sys.executable, "-c", "print(1)"], ["pip", "install", "x"], ["ls", "-l"], [], "",
    ])
    def test_a_command_that_only_reads_or_is_something_else_is_not(self, command):
        assert changes_host(command, ROOTS, real) == ""

    def test_a_stub_in_the_runs_own_folder_is_not_a_real_program(self):
        assert changes_host(["apt-get", "install", "git"], ROOTS, stub) == ""
        assert changes_host(["sudo", "systemctl", "start", "ssh"], ROOTS, stub) == ""

    def test_a_program_that_is_not_there_changes_nothing(self):
        assert changes_host(["apt-get", "install", "git"], ROOTS, nowhere) == ""

    def test_the_reason_names_the_program(self):
        assert changes_host(["apt-get", "install", "git"], ROOTS, real) == "apt-get changes the installed packages"
        assert changes_host(["systemctl", "start", "x"], ROOTS, real) == "systemctl changes a service of the host"
        assert changes_host(["sshd"], ROOTS, real) == "sshd is a service of the host"


class TestTheWrapper:

    def run(self, args, which=real, ran=None, monkeypatch=None):
        seen = []
        original = lambda self, args, *a, **k: (ran if ran is not None else []).append(args)  # noqa: E731
        init = guarded_init(original, ROOTS, which, warn=lambda w, stacklevel=0: seen.append(w))
        init(object(), args)
        return seen

    def test_it_warns_with_the_test_and_the_command_and_still_runs_it(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
        ran = []
        seen = self.run(["apt-get", "install", "-y", "p7zip-full"], ran=ran)
        assert ran == [["apt-get", "install", "-y", "p7zip-full"]]            # warn, never stop
        assert len(seen) == 1 and isinstance(seen[0], HostChangeWarning)
        text = str(seen[0])
        assert "tests/test_x.py::test_y" in text and "apt-get install -y p7zip-full" in text and "stub" in text

    def test_it_is_silent_for_a_stub_and_for_a_harmless_command(self):
        assert self.run(["apt-get", "install", "git"], which=stub) == []
        assert self.run(["git", "status"]) == []

    def test_a_check_that_fails_never_breaks_the_command(self):
        ran = []
        original = lambda self, args, *a, **k: ran.append(args)  # noqa: E731

        def broken(program):
            raise RuntimeError("which failed")
        guarded_init(original, ROOTS, broken)(object(), ["apt-get", "install", "git"])
        assert ran == [["apt-get", "install", "git"]]

    def test_a_long_command_is_cut_in_the_warning(self, monkeypatch):
        seen = self.run(["apt-get", "install"] + ["package"] * 100)
        assert "..." in str(seen[0]) and len(str(seen[0])) < 450


class TestInTheSuite:

    def test_the_guard_is_installed_for_the_session(self):
        assert hasattr(subprocess.Popen.__init__, "__wrapped__")

    def test_a_stub_on_the_tests_own_path_runs_without_a_warning(self, tmp_path, monkeypatch, recwarn):
        if os.name == "nt":
            pytest.skip("a POSIX shell script stub")
        stub_file = tmp_path / "apt-get"
        stub_file.write_text("#!/bin/sh\nexit 0\n")
        stub_file.chmod(stub_file.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
        assert subprocess.run(["apt-get", "install", "-y", "git"], check=False).returncode == 0
        assert not [w for w in recwarn.list if issubclass(w.category, HostChangeWarning)]

    def test_an_ordinary_command_runs_without_a_warning(self, recwarn):
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        assert not [w for w in recwarn.list if issubclass(w.category, HostChangeWarning)]
