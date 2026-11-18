"""The install manifest on Linux (install.sh `pkg_install`, `pkg_present`, `write_install_manifest`; scripts/uninstall.sh):
install.sh writes down only the packages it installed because they were NOT there, one entry per component, and the
uninstaller script shows them and asks. Everything runs in bash with fakes for the package tools, so nothing here installs
or removes a package of the computer that runs the tests."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"
UNINSTALL_SH = REPO_ROOT / "scripts" / "uninstall.sh"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="the scripts need bash")
SYSTEM_PATH = "/usr/bin:/bin"


def fake_apt(folder: Path, installed=(), fail=False) -> Path:
    """apt-get and dpkg-query over one state file (the packages that are installed); returns the state file."""
    folder.mkdir(parents=True, exist_ok=True)
    state = folder / "state"
    state.write_text("".join(f"{name}\n" for name in installed))
    (folder / "apt-get").write_text(
        '#!/bin/sh\n'
        f'if [ {1 if fail else 0} -ne 0 ]; then echo "E: could not install" >&2; exit 100; fi\n'
        'if [ "$1" = install ]; then shift; fi\n'
        f'for p in "$@"; do case "$p" in -*) ;; *) echo "$p" >> "{state}" ;; esac; done\n')
    (folder / "dpkg-query").write_text(
        '#!/bin/sh\nfor last; do :; done\n'
        f'if grep -qx "$last" "{state}"; then printf "install ok installed"; exit 0; fi\nexit 1\n')
    for name in ("apt-get", "dpkg-query"):
        (folder / name).chmod(0o755)
    return state


def bash(script: str, folder: Path, path_extra: bool = True, env_extra=None):
    path = f"{folder}:{SYSTEM_PATH}" if path_extra else SYSTEM_PATH
    env = {"PATH": path, "HOME": str(folder), **(env_extra or {})}
    return subprocess.run(["/bin/bash", "-c", f'source "$1"; {script}', "test", str(INSTALL_SH)], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)


def tracked(result):
    """The lines 'Title<TAB>package' that the script printed after the markers."""
    return [line for line in result.stdout.splitlines() if "\t" in line]


PRELUDE = 'PKG=apt; OFFLINE=0; PKG_LISTS_READ=1; '
SHOW = 'for l in ${INSTALLED_BY_US[@]+"${INSTALLED_BY_US[@]}"}; do :; done; printf "%s\\n" ${INSTALLED_BY_US[@]+"${INSTALLED_BY_US[@]}"}'


def show_script(call: str) -> str:
    # the entries hold a tab, so they are printed one per line with IFS cleared
    return PRELUDE + f'{call}; echo "rc=$?"; for e in ${{INSTALLED_BY_US[@]+"${{INSTALLED_BY_US[@]}}"}}; do printf "%s\\n" "$e"; done'


@needs_bash
class TestOnlyWhatWasMissingIsWrittenDown:

    def test_a_package_that_was_missing_and_is_installed_now_is_recorded_with_its_title(self, tmp_path):
        fake_apt(tmp_path)
        result = bash(show_script('pkg_install_as "rsync" rsync'), tmp_path)
        assert "rc=0" in result.stdout and tracked(result) == ["rsync\trsync"], result.stdout + result.stderr

    def test_a_package_that_was_there_before_is_never_recorded_not_even_beside_a_missing_one(self, tmp_path):
        fake_apt(tmp_path, installed=["uidmap"])
        result = bash(show_script('pkg_install_as "Podman" podman uidmap slirp4netns'), tmp_path)
        assert tracked(result) == ["Podman\tpodman", "Podman\tslirp4netns"], result.stdout

    def test_when_everything_was_there_nothing_is_recorded(self, tmp_path):
        fake_apt(tmp_path, installed=["python3"])
        result = bash(show_script('pkg_install_as "Python" python3'), tmp_path)
        assert "rc=0" in result.stdout and tracked(result) == []

    def test_a_failed_install_records_nothing(self, tmp_path):
        fake_apt(tmp_path, fail=True)
        result = bash(show_script('pkg_install_as "rsync" rsync'), tmp_path)
        assert "rc=100" in result.stdout and tracked(result) == []

    def test_a_package_that_is_not_there_after_the_install_is_not_recorded(self, tmp_path):
        state = fake_apt(tmp_path)
        (tmp_path / "apt-get").write_text("#!/bin/sh\nexit 0\n")                       # claims success, installs nothing
        result = bash(show_script('pkg_install_as "rsync" rsync'), tmp_path)
        assert "rc=0" in result.stdout and tracked(result) == [] and state.read_text() == ""

    def test_the_title_is_for_one_call_only_and_a_plain_call_uses_the_package_name(self, tmp_path):
        fake_apt(tmp_path)
        result = bash(show_script('pkg_install_as "Python" python3; pkg_install p7zip-full; echo "title=[$PKG_TITLE]"'), tmp_path)
        assert tracked(result) == ["Python\tpython3", "p7zip-full\tp7zip-full"] and "title=[]" in result.stdout

    def test_offline_nothing_is_run_and_nothing_is_recorded(self, tmp_path):
        fake_apt(tmp_path)
        result = bash(show_script('OFFLINE=1; pkg_install_as "rsync" rsync'), tmp_path)
        assert "rc=1" in result.stdout and tracked(result) == []

    def test_every_manager_has_its_own_question_for_a_package_that_is_installed(self, tmp_path):
        for manager, tool in (("dnf", "rpm"), ("zypper", "rpm"), ("pacman", "pacman"), ("apk", "apk")):
            (tmp_path / tool).write_text('#!/bin/sh\necho "$@" >> "' + str(tmp_path / "asked") + '"\nexit 0\n')
            (tmp_path / tool).chmod(0o755)
            result = bash(f'PKG={manager}; pkg_present rsync; echo "rc=$?"', tmp_path)
            assert "rc=0" in result.stdout, (manager, result.stdout + result.stderr)
        asked = (tmp_path / "asked").read_text().splitlines()
        assert asked == ["-q rsync", "-q rsync", "-Q rsync", "info -e rsync"]

    def test_an_unknown_manager_has_nothing_installed(self, tmp_path):
        assert "rc=1" in bash('PKG=""; pkg_present rsync; echo "rc=$?"', tmp_path).stdout


@needs_bash
class TestWritingTheManifest:

    def write(self, tmp_path, entries, extra=""):
        install = tmp_path / "opt"
        install.mkdir(exist_ok=True)
        lines = "".join(f'INSTALLED_BY_US+=("{title}"$\'\\t\'"{pkg}"); ' for title, pkg in entries)
        script = (f'PKG=apt; INSTALL_DIR="{install}"; PYTHON="{sys.executable}"; {lines}{extra} write_install_manifest; '
                  'echo "left=${#INSTALLED_BY_US[@]}"')
        return install / "install-manifest.json", bash(script, tmp_path)

    def read(self, path):
        return {name: (d["kind"], d["ref"], d["scope"], d["title"]) for name, d in json.loads(path.read_text()).items()}

    def test_one_entry_per_component_with_its_packages_shared_and_the_manager_as_kind(self, tmp_path):
        path, result = self.write(tmp_path, [("Podman", "podman"), ("Podman", "slirp4netns"), ("rsync", "rsync"),
                                             ("7-Zip", "7zip")])
        assert "left=0" in result.stdout, result.stdout + result.stderr
        assert self.read(path) == {"podman": ("apt", "podman slirp4netns", "shared", "Podman"),
                                   "rsync": ("apt", "rsync", "shared", "rsync"),
                                   "7-zip": ("apt", "7zip", "shared", "7-Zip")}

    def test_a_second_write_adds_to_the_component_and_keeps_the_rest(self, tmp_path):
        path, _ = self.write(tmp_path, [("Podman", "podman")])
        self.write(tmp_path, [("Podman", "uidmap")])
        assert self.read(path)["podman"][1] == "podman uidmap"

    def test_what_is_there_already_from_another_program_stays(self, tmp_path):
        install = tmp_path / "opt"
        install.mkdir()
        (install / "install-manifest.json").write_text(json.dumps(
            {"other": {"scope": "shared", "kind": "winget", "ref": "7zip.7zip", "title": "7-Zip"}}))
        path, _ = self.write(tmp_path, [("rsync", "rsync")])
        assert set(self.read(path)) == {"other", "rsync"}

    def test_nothing_pending_writes_no_file(self, tmp_path):
        path, result = self.write(tmp_path, [])
        assert "left=0" in result.stdout and not path.exists()

    def test_without_the_installation_folder_nothing_is_written_and_the_entries_wait(self, tmp_path):
        script = (f'PKG=apt; INSTALL_DIR="{tmp_path}/missing"; PYTHON="{sys.executable}"; '
                  'INSTALLED_BY_US+=("rsync"$\'\\t\'"rsync"); write_install_manifest; echo "left=${#INSTALLED_BY_US[@]}"')
        result = bash(script, tmp_path)
        assert "left=1" in result.stdout and not (tmp_path / "missing").exists()

    def test_a_write_that_fails_keeps_the_entry_for_the_next_try_and_never_stops_the_installer(self, tmp_path):
        install = tmp_path / "opt"
        install.mkdir()
        script = (f'PKG=apt; INSTALL_DIR="{install}"; PYTHON=/bin/false; INSTALLED_BY_US+=("rsync"$\'\\t\'"rsync"); '
                  'write_install_manifest; echo "rc=$? left=${#INSTALLED_BY_US[@]}"')
        result = bash(script, tmp_path)
        assert "rc=0 left=1" in result.stdout and not (install / "install-manifest.json").exists()

    def test_the_id_is_made_from_the_title(self, tmp_path):
        path, _ = self.write(tmp_path, [("git und GNU tar", "git")])
        assert list(self.read(path)) == ["git-und-gnu-tar"]

    def test_a_package_name_the_manifest_refuses_is_not_written_and_not_fatal(self, tmp_path):
        path, result = self.write(tmp_path, [("Evil", "rsync;reboot")])
        assert "left=1" in result.stdout and not path.exists()


def test_the_manifest_is_written_after_the_install_and_also_when_the_run_stops():
    text = INSTALL_SH.read_text(encoding="utf-8")
    main = text.split("\nmain() {", 1)[1]
    assert main.index("check_root") < main.index("trap 'write_install_manifest || true' EXIT") < main.index("setup_system")
    assert main.index("record_installation") < main.index("write_install_manifest\n") < main.index("setup_mode")


def test_every_package_the_installer_installs_has_a_title_of_its_own():
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("\npkg_install() {", 1)[1].split("\n}\n", 1)[1]
    plain = [line.strip() for line in body.splitlines() if "pkg_install " in line and "pkg_install_as" not in line
             and not line.strip().startswith("#")]
    assert plain == [], plain                       # a bare pkg_install would be recorded under the package's own name


@needs_bash
def test_the_dry_run_names_the_manifest():
    result = subprocess.run(["/bin/bash", str(INSTALL_SH), "--dry-run"], capture_output=True, text=True, timeout=120,
                            env={"PATH": SYSTEM_PATH, "HOME": "/nonexistent"}, stdin=subprocess.DEVNULL)
    assert "install-manifest.json: schreibt auf, was der Installer selbst installiert" in result.stdout, result.stdout[-600:]


# ----------------------------------------------------------------------------------------------- scripts/uninstall.sh

def uninstall(script: str, folder: Path, extra_path: str = "", stdin: str = ""):
    env = {"PATH": f"{extra_path}{SYSTEM_PATH}", "HOME": str(folder)}
    return subprocess.run(["/bin/bash", "-c", f'source "$1"; {script}', "test", str(UNINSTALL_SH)], env=env,
                          input=stdin, capture_output=True, text=True, timeout=120)


def machine_manifest(folder: Path) -> Path:
    install = folder / "opt"
    install.mkdir(parents=True, exist_ok=True)
    if not (install / "orchestrator").exists():
        (install / "orchestrator").symlink_to(REPO_ROOT / "orchestrator", target_is_directory=True)   # the code of an installation
    path = install / "install-manifest.json"
    path.write_text(json.dumps({"python": {"scope": "shared", "kind": "apt", "ref": "python3", "title": "Python"}}))
    return path


@needs_bash
class TestTheUninstallScript:

    def test_it_is_executable_and_shellcheck_clean(self):
        assert os.access(UNINSTALL_SH, os.X_OK) and UNINSTALL_SH.read_text().startswith("#!/usr/bin/env bash")
        shellcheck = shutil.which("shellcheck")
        if shellcheck:
            run = subprocess.run([shellcheck, str(UNINSTALL_SH)], capture_output=True, text=True)
            assert run.returncode == 0, run.stdout

    def test_without_root_rights_it_asks_for_sudo_and_changes_nothing(self, tmp_path):
        machine = machine_manifest(tmp_path)
        result = uninstall(f'INSTALL_DIR="{tmp_path}/opt"; is_root() {{ return 1; }}; main; echo "rc=$?"', tmp_path)
        assert "Starte es mit sudo" in result.stdout and "rc=1" in result.stdout and machine.exists()

    def test_the_dry_run_needs_no_root_shows_the_packages_and_removes_nothing(self, tmp_path):
        machine = machine_manifest(tmp_path)
        result = uninstall(f'INSTALL_DIR="{tmp_path}/opt"; is_root() {{ return 1; }}; main --dry-run; echo "rc=$?"', tmp_path)
        assert "Python: ein Paket (apt: python3)" in result.stdout and "rc=0" in result.stdout, result.stdout + result.stderr
        assert "wird vielleicht von anderen Programmen benutzt" in result.stdout and machine.exists()

    def test_after_a_failure_the_closing_note_is_not_printed(self, tmp_path):
        (tmp_path / "opt").mkdir()
        (tmp_path / "opt" / "orchestrator").mkdir()          # code that cannot run: the module is not there
        script = (f'INSTALL_DIR="{tmp_path}/opt"; REPO_ROOT="{tmp_path}/norepo"; is_root() {{ return 0; }}; '
                  'find_tools() { PYTHON_BIN=/usr/bin/python3; CODE_DIR="$INSTALL_DIR"; }; main --dry-run; echo "rc=$?"')
        result = uninstall(script, tmp_path)
        assert "rc=1" in result.stdout and "Nicht entfernt, weil das nicht Teil" not in result.stdout

    def test_it_says_what_it_does_not_remove(self, tmp_path):
        machine_manifest(tmp_path)
        result = uninstall(f'INSTALL_DIR="{tmp_path}/opt"; main --dry-run', tmp_path)
        for named in ("Installationsordner", "/usr/local/bin/llm-framework", "Gruppe llmcf", "~/.llm-framework", "apt autoremove"):
            assert named in result.stdout, named

    def test_a_removal_with_the_real_module_asks_first_and_no_is_the_default(self, tmp_path):
        machine = machine_manifest(tmp_path)
        result = uninstall(f'INSTALL_DIR="{tmp_path}/opt"; is_root() {{ return 0; }}; main', tmp_path, stdin="\n")
        assert "entfernen? [j/N]" in result.stdout and "bleibt." in result.stdout
        assert "Python" in machine.read_text()                                  # still written down: nothing was removed

    def test_without_an_installation_and_without_code_it_says_so(self, tmp_path):
        script = (f'INSTALL_DIR="{tmp_path}/none"; REPO_ROOT="{tmp_path}/norepo"; is_root() {{ return 0; }}; main; '
                  'echo "rc=$?"')
        result = uninstall(script, tmp_path)
        assert "scheint nichts installiert zu sein" in result.stdout and "rc=1" in result.stdout

    def test_the_code_of_the_installation_is_used_before_the_checkout(self, tmp_path):
        machine_manifest(tmp_path)
        result = uninstall(f'INSTALL_DIR="{tmp_path}/opt"; find_tools; echo "code=$CODE_DIR"', tmp_path)
        assert f"code={tmp_path}/opt" in result.stdout
        result = uninstall(f'INSTALL_DIR="{tmp_path}/none"; find_tools; echo "code=$CODE_DIR"', tmp_path)
        assert f"code={REPO_ROOT}" in result.stdout

    def test_the_persons_manifest_is_in_the_home_of_whoever_called_sudo_never_in_home_of_root(self, tmp_path):
        getent = tmp_path / "getent"
        getent.write_text('#!/bin/sh\necho "alice:x:1000:1000::/home/alice:/bin/bash"\n')
        getent.chmod(0o755)
        result = uninstall('SUDO_USER=alice; person_manifest; echo "rc=$?"', tmp_path, extra_path=f"{tmp_path}:")
        assert "/home/alice/.llm-framework/install-manifest.json" in result.stdout and "rc=0" in result.stdout
        assert "rc=1" in uninstall('SUDO_USER=root; person_manifest; echo "rc=$?"', tmp_path).stdout
        assert "rc=1" in uninstall('SUDO_USER=; person_manifest; echo "rc=$?"', tmp_path).stdout

    def test_an_unknown_option_is_refused(self, tmp_path):
        result = uninstall('main --everything; echo "rc=$?"', tmp_path)
        assert "Unbekannte Option" in result.stdout and "rc=2" in result.stdout

    def test_help_prints_the_header(self, tmp_path):
        result = uninstall('main --help', tmp_path)
        assert "entfernt, was der Installer selbst installiert hat" in result.stdout
