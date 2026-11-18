"""The installers keep a container engine that already answers instead of installing Podman beside it (DESIGN, Installer,
platforms and dependencies; the maintainer's rule: where Docker Desktop is there, a second engine has no use).

- install.sh (`engine_kept_answers`, `setup_rootless`, `offline_engine_there`, `plan_engine`): a `docker` that answers
  the question the framework itself asks (`docker --context default version`), as the person and within a deadline, stays;
  Podman keeps its own path (sub-ids, the person's service); a docker that does not answer, none, or an engine the person
  turned away with --engine= leave everything as it was. Run in bash with fakes for docker, timeout and the user switch.
- install.bat (`Get-ContainerEngine`): three states, answers, not-running and none, in a real pwsh with a fake docker; the
  step's order and what it never does (install, write the manifest) are checked by the text, because cmd.exe is not here.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_BAT = (REPO_ROOT / "install.bat").read_text(encoding="utf-8")
PWSH = shutil.which("pwsh")
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="install.sh needs bash")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="needs pwsh")
SYSTEM_PATH = "/usr/bin:/bin"


def fake_docker(folder: Path, answer: str = "Docker Engine - Community", exit_code: int = 0) -> Path:
    """A `docker` that writes down how it was called and answers the platform question with `answer` (or fails)."""
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "docker.log"
    script = folder / "docker"
    script.write_text(f'#!/bin/sh\necho "docker $*" >> "{log}"\n'
                      f'if [ {exit_code} -ne 0 ]; then echo "Cannot connect to the Docker daemon" >&2; exit {exit_code}; fi\n'
                      f'echo "{answer}"\n')
    script.chmod(0o755)
    return log


def fake_timeout(folder: Path) -> Path:
    log = folder / "timeout.log"
    script = folder / "timeout"
    script.write_text(f'#!/bin/sh\necho "timeout $*" >> "{log}"\nshift\nexec "$@"\n')
    script.chmod(0o755)
    return log


def isolated_bin(folder: Path) -> Path:
    """A PATH folder with the few real tools install.sh needs and no docker: the system's own PATH may hold a real docker
    (the CI runners have one), so 'no docker at all' needs a PATH of its own."""
    box = folder / "isolated"
    box.mkdir(exist_ok=True)
    for tool in ("bash", "head", "id", "dirname", "realpath", "tr", "sed", "cut", "grep", "cat", "date", "awk", "uname",
                 "basename"):
        found = shutil.which(tool)
        if found and not (box / tool).exists():
            (box / tool).symlink_to(found)
    return box


def bash(script: str, folder: Path, extra_path: bool = True, path_only: "Path | None" = None):
    """install.sh sourced, the user switch replaced by a plain call (the fakes need no sudo), then `script`."""
    path = f"{folder}:{SYSTEM_PATH}" if extra_path else SYSTEM_PATH
    if path_only is not None:
        path = str(path_only)
    prelude = 'SUDO_USER=alice; id() { echo 1000; }; as_engine_user() { "$@"; }; '
    return subprocess.run(["/bin/bash", "-c", f'source "$1"; {prelude}{script}', "test", str(INSTALL_SH)],
                          env={"PATH": path, "HOME": str(folder)}, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=60)


@needs_bash
class TestTheEngineThatAnswersStays:

    def test_a_docker_that_answers_stays_and_is_asked_the_way_the_framework_asks(self, tmp_path):
        log = fake_docker(tmp_path)
        result = bash('engine_kept_answers; echo "rc=$? kept=$ENGINE_KEPT"', tmp_path)
        assert "rc=0 kept=Docker Engine - Community" in result.stdout, result.stdout + result.stderr
        assert "docker --context default version --format {{.Server.Platform.Name}}" in log.read_text()

    def test_docker_desktop_counts(self, tmp_path):
        fake_docker(tmp_path, answer="Docker Desktop 4.41.2 (191736)")
        result = bash('engine_kept_answers; echo "rc=$? kept=$ENGINE_KEPT"', tmp_path)
        assert "rc=0 kept=Docker Desktop 4.41.2" in result.stdout

    def test_podman_keeps_its_own_path(self, tmp_path):
        fake_docker(tmp_path, answer="Podman Engine")
        result = bash('engine_kept_answers; echo "rc=$? kept=[$ENGINE_KEPT]"', tmp_path)
        assert "rc=1 kept=[]" in result.stdout

    def test_a_docker_that_does_not_answer_is_no_engine(self, tmp_path):
        fake_docker(tmp_path, exit_code=1)
        result = bash('engine_kept_answers; echo "rc=$? kept=[$ENGINE_KEPT]"', tmp_path)
        assert "rc=1 kept=[]" in result.stdout

    def test_no_docker_at_all_is_no_engine(self, tmp_path):
        result = bash('command -v docker > /dev/null && echo DOCKER-FOUND; engine_kept_answers; echo "rc=$?"', tmp_path,
                      path_only=isolated_bin(tmp_path))
        assert "DOCKER-FOUND" not in result.stdout, "the test's own PATH must have no docker"
        assert "rc=1" in result.stdout

    def test_an_engine_the_person_turned_away_with_engine_is_not_kept_and_not_even_asked(self, tmp_path):
        log = fake_docker(tmp_path)
        result = bash('ENGINE_EXPLICIT=1; engine_kept_answers; echo "rc=$?"', tmp_path)
        assert "rc=1" in result.stdout and not log.exists()

    def test_the_question_has_a_deadline(self, tmp_path):
        fake_docker(tmp_path)
        waited = fake_timeout(tmp_path)
        result = bash('engine_kept_answers; echo "rc=$?"', tmp_path)
        assert "rc=0" in result.stdout and "timeout 20 docker --context default version" in waited.read_text()

    def test_the_option_engine_marks_the_choice_as_the_persons(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert 'ENGINE_CHOICE="${arg#--engine=}"; ENGINE_EXPLICIT=1' in text and "ENGINE_EXPLICIT=0" in text


STUBS = ('engine_user() { REAL_USER=alice; REAL_UID=1000; }; rootless_docker_there() { return 1; }; '
         'ensure_subids() { :; }; setup_rootless_podman() { echo PODMAN-SETUP; ROOTLESS_ENGINE=podman; }; '
         'setup_rootless_docker() { echo ROOTLESS-DOCKER-SETUP; ROOTLESS_ENGINE=docker; }; '
         'root_group_note() { echo ROOT-GROUP-NOTE; }; ')


@needs_bash
class TestTheSetupFollowsIt:

    def test_an_engine_that_answers_means_no_podman_and_no_second_engine(self, tmp_path):
        fake_docker(tmp_path)
        result = bash(STUBS + 'in_docker_group() { return 1; }; setup_rootless; echo "ENGINE=$ROOTLESS_ENGINE"', tmp_path)
        out = result.stdout
        assert "antwortet schon (Docker Engine - Community)" in out and "keine zweite daneben" in out
        assert "ENGINE=kept" in out and "PODMAN-SETUP" not in out and "ROOTLESS-DOCKER-SETUP" not in out
        assert "ROOT-GROUP-NOTE" not in out

    def test_without_an_answer_it_is_the_old_way_rootless_podman(self, tmp_path):
        fake_docker(tmp_path, exit_code=1)
        result = bash(STUBS + 'in_docker_group() { return 1; }; setup_rootless; echo "ENGINE=$ROOTLESS_ENGINE"', tmp_path)
        assert "PODMAN-SETUP" in result.stdout and "ENGINE=podman" in result.stdout and "ROOT-GROUP-NOTE" in result.stdout
        assert "antwortet schon" not in result.stdout

    def test_the_persons_own_rootless_docker_is_still_found_first(self, tmp_path):
        log = fake_docker(tmp_path)
        script = STUBS.replace("rootless_docker_there() { return 1; }", "rootless_docker_there() { return 0; }")
        result = bash(script + 'setup_rootless; echo "ENGINE=$ROOTLESS_ENGINE"', tmp_path)
        assert "Rootless Docker von 'alice' ist schon da" in result.stdout and "ENGINE=docker" in result.stdout
        assert not log.exists()                      # nothing was asked: the earlier rule decided

    def test_a_kept_engine_that_runs_as_root_names_the_group_and_changes_nothing(self, tmp_path):
        fake_docker(tmp_path)
        result = bash(STUBS + 'in_docker_group() { return 0; }; setup_rootless', tmp_path)
        out = result.stdout
        assert "Die Engine, die schon da ist, braucht diese Gruppe" in out and "ändert der Installer nichts" in out
        assert "gpasswd -d alice docker" in out and "--engine=podman" in out
        assert "usermod" not in out and "ROOT-GROUP-NOTE" not in out

    def test_a_run_without_a_signed_in_person_asks_nothing(self, tmp_path):
        log = fake_docker(tmp_path)
        result = bash('engine_user() { REAL_USER=root; }; setup_rootless; echo done', tmp_path)
        assert "done" in result.stdout and not log.exists()


@needs_bash
class TestTheDryRunAndTheOfflineRun:

    def test_the_dry_run_says_the_engine_stays(self, tmp_path):
        fake_docker(tmp_path)
        result = bash(STUBS + 'in_docker_group() { return 1; }; ROOTFUL=0; plan_engine', tmp_path)
        assert "Docker Engine - Community antwortet schon; sie bleibt" in result.stdout
        assert "Podman" not in result.stdout

    def test_offline_an_engine_that_answers_is_an_engine_there(self, tmp_path):
        fake_docker(tmp_path)
        script = ('engine_user() { REAL_USER=alice; REAL_UID=1000; }; rootless_docker_there() { return 1; }; ROOTFUL=0; '
                  'command() { if [ "$2" = podman ]; then return 1; fi; builtin command "$@"; }; '
                  'offline_engine_there && echo THERE || echo MISSING')
        assert "THERE" in bash(script, tmp_path).stdout

    def test_offline_without_any_engine_it_is_named_as_missing(self, tmp_path):
        fake_docker(tmp_path, exit_code=1)
        script = ('engine_user() { REAL_USER=alice; REAL_UID=1000; }; rootless_docker_there() { return 1; }; ROOTFUL=0; '
                  'command() { if [ "$2" = podman ]; then return 1; fi; builtin command "$@"; }; '
                  'offline_engine_there && echo THERE || echo MISSING')
        assert "MISSING" in bash(script, tmp_path).stdout


def test_the_final_summary_names_a_kept_engine():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'kept) echo "🐳 Engine: ${ENGINE_KEPT} (war schon da und antwortet;' in text


# ---------------------------------------------------------------------------------------------------- install.bat

def engine_block() -> str:
    return INSTALL_BAT[INSTALL_BAT.index("# ENGINE-BEGIN"):INSTALL_BAT.index("# ENGINE-END")]


def engine_step() -> str:
    return INSTALL_BAT[INSTALL_BAT.index("# CONTAINER ENGINE"):INSTALL_BAT.index("# DITTO: lokale Laufzeit")]


def ask_pwsh(tmp_path: Path, with_docker: "str | None" = None, exit_code: int = 0):
    """Runs Get-ContainerEngine in a real pwsh; a fake docker on the PATH (or none) answers."""
    path = "/usr/bin:/bin:/usr/local/bin"
    log = None
    if with_docker is not None:
        log = fake_docker(tmp_path / "bin", answer=with_docker, exit_code=exit_code)
        path = f"{tmp_path / 'bin'}:{path}"
    # "No docker program" needs a PATH without one; a computer that runs the tests may have a real docker, so the PATH is
    # emptied inside pwsh, where Get-Command reads it at the moment it is called.
    empty = "$env:PATH = '/nonexistent'; " if with_docker is None else ""
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command",
                             engine_block() + '\n' + empty + '$E = Get-ContainerEngine; "$($E.State)|$($E.Name)"'],
                            capture_output=True, text=True, timeout=120, cwd=tmp_path,
                            env={"PATH": path, "HOME": str(tmp_path), "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"})
    return result, log


# The fake docker is a shell script on a ':' PATH, so these run where there is a POSIX pwsh (the Linux jobs; the real Windows
# behaviour is what install.bat does with the real docker.exe, which no runner has).
needs_posix_pwsh = pytest.mark.skipif(PWSH is None or os.name == "nt", reason="needs a POSIX pwsh")


@needs_posix_pwsh
class TestGetContainerEngine:

    def test_an_engine_that_answers_is_named(self, tmp_path):
        result, log = ask_pwsh(tmp_path, "Docker Desktop 4.41.2 (191736)")
        assert result.stdout.strip() == "answers|Docker Desktop 4.41.2 (191736)", result.stdout + result.stderr
        assert "docker --context default version --format {{.Server.Platform.Name}}" in log.read_text()

    def test_a_docker_that_does_not_answer_is_not_running(self, tmp_path):
        result, _ = ask_pwsh(tmp_path, "x", exit_code=1)
        assert result.stdout.strip() == "not-running|"

    def test_no_docker_program_is_none(self, tmp_path):
        result, _ = ask_pwsh(tmp_path, None)
        assert result.stdout.strip() == "none|"


def test_the_engine_step_keeps_an_engine_that_answers_and_installs_and_records_nothing_then():
    step = engine_step()
    answers = step[step.index('elseif ($Engine.State -eq "answers")'):step.index('elseif (($Engine.State -eq "not-running")')]
    assert "winget" not in answers and "Add-InstalledByUs" not in answers and "podman machine" not in answers
    assert "keine zweite daneben" in answers


def test_docker_that_is_installed_but_not_running_gets_no_podman_beside_it_unless_podman_is_there():
    step = engine_step()
    not_running = step[step.index('elseif (($Engine.State -eq "not-running")'):step.index('elseif ($Offline')]
    assert '-not (Get-Command "podman"' in step[step.index('"not-running"'):step.index('"not-running"') + 120]
    assert "winget" not in not_running and "Starte Docker Desktop" in not_running


def test_the_question_comes_before_anything_is_installed_and_rootful_keeps_its_meaning():
    step = engine_step()
    assert step.index("Get-ContainerEngine") < step.index("if ($Rootful)") < step.index('elseif ($Engine.State -eq "answers")')
    assert step.index('elseif ($Engine.State -eq "answers")') < step.index("winget install -e --id RedHat.Podman")
    assert "docker-users" in step                      # the group is still named, never changed
    assert re.search(r"Add-LocalGroupMember|localgroup docker-users /add", step) is None


def test_the_dry_run_says_podman_falls_away_when_an_engine_answers():
    assert "entfaellt, wenn schon eine Container-Engine antwortet" in INSTALL_BAT
