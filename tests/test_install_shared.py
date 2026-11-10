"""Several people share one installation on a computer: the installer's side (docs/DESIGN.md, Installer, platforms and
dependencies).

Once the checkout's framework says it writes nothing into the install folder (INSTALL_READ_ONLY = True in
orchestrator/__init__.py), install.sh hands the shared part to root and the group llmcf (readable and runnable for the
group, writable by none of its members, nothing for other accounts; an earlier version's personal files keep their
owner). A second account with the same version and files gets only its own part (the group, ~/.llm-framework, the
engine, mode and Ditto for that person), never a new copy or .venv; another version is an update. Before that line
exists, the folder goes to the installing account as before, so installer and framework can land in either order.
Every run is install.sh sourced on a fake root: id, getent, groupadd, usermod, chown, chmod, stat and sudo are fakes
that write down their calls.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_BAT = REPO_ROOT / "install.bat"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="needs bash")
READY = 'INSTALL_READ_ONLY = True\n'


class FakeRoot:
    """A checkout and an install folder in tmp_path, with fake account and ownership tools on the PATH."""

    def __init__(self, tmp_path: Path, ready=True, groups="alice", owner="alice"):
        self.tmp = tmp_path
        self.repo = tmp_path / "repo"
        self.install = tmp_path / "opt" / "llm-cross-compiler-framework"
        self.home = tmp_path / "personhome"
        self.bin = tmp_path / "bin"
        self.log = tmp_path / "calls.log"
        for folder in (self.repo / "orchestrator", self.bin, self.home):
            folder.mkdir(parents=True)
        (self.repo / "orchestrator" / "__init__.py").write_text('__version__ = "3.4.0"\n' + (READY if ready else ""))
        (self.repo / "README.md").write_text("shipped\n")
        log = f'echo "$(basename "$0") $*" >> "{self.log}"'
        self.fake("id", f'{log}\ncase "$1" in -nG) echo "{groups}" ;; -gn) echo "$2" ;; -u) echo 1000 ;; *) echo 0 ;; esac')
        self.fake("getent", f'{log}\n[ -f "{tmp_path}/group-made" ]')
        self.fake("groupadd", f'{log}\ntouch "{tmp_path}/group-made"')
        self.fake("usermod", log)
        self.fake("chown", log)
        self.fake("chmod", log)
        self.fake("stat", f'{log}\necho {owner}')
        # sudo -H -u USER CMD...: runs CMD with HOME in the fake home
        self.fake("sudo", f'{log}\nwhile [ $# -gt 0 ]; do case "$1" in -H) shift ;; -u) shift 2 ;; --*) shift ;; *) break ;; esac; done\n'
                          f'HOME="{self.home}" exec "$@"')

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def installed(self, by="alice", state=None):
        (self.install / "orchestrator").mkdir(parents=True)
        (self.install / "orchestrator" / "__init__.py").write_text('__version__ = "3.4.0"\n')
        (self.install / ".venv" / "bin").mkdir(parents=True)
        python = self.install / ".venv" / "bin" / "python"
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        if by:
            (self.install / ".lcf-installed-by").write_text(by + "\n")
        if state:
            (self.install / ".lcf-installed-state").write_text(state + "\n")

    def run(self, script, user="bob"):
        code = (f'source "$1"; REPO_ROOT="{self.repo}"; INSTALL_DIR="{self.install}"; ASK_TTY="{self.tmp}/no-tty"; '
                f'{script}')
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.tmp), "SUDO_USER": user, "USER": "root"}
        result = subprocess.run(["/bin/bash", "-c", code, "test", str(INSTALL_SH)], env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, calls

    def state(self):
        result, _ = self.run("shipped_files() { printf 'README.md\\0orchestrator/__init__.py\\0'; }; checkout_state")
        return result.stdout.strip()


@needs_bash
class TestWhatTheRunDoes:

    def role(self, box, user="bob"):
        result, _ = box.run('shipped_files() { printf "README.md\\0orchestrator/__init__.py\\0"; }; '
                            'install_role; echo "ROLE=$INSTALL_ROLE OWNER=$INSTALL_OWNER"', user)
        return result.stdout.strip().splitlines()[-1]

    def test_before_the_framework_writes_nothing_there_every_run_is_as_before(self, tmp_path):
        box = FakeRoot(tmp_path, ready=False)
        box.installed(by="alice")
        assert self.role(box) == "ROLE=full OWNER="

    def test_without_an_installation_it_is_the_first_person(self, tmp_path):
        assert self.role(FakeRoot(tmp_path)) == "ROLE=full OWNER="

    def test_a_second_account_with_the_same_version_and_files_gets_only_its_own_part(self, tmp_path):
        box = FakeRoot(tmp_path)
        box.installed(by="alice", state=box.state())
        assert self.role(box) == "ROLE=person OWNER=alice"

    def test_the_same_account_runs_as_before(self, tmp_path):
        box = FakeRoot(tmp_path)
        box.installed(by="alice", state=box.state())
        assert self.role(box, user="alice") == "ROLE=full OWNER=alice"

    def test_another_version_or_other_files_is_an_update_of_the_shared_part(self, tmp_path):
        box = FakeRoot(tmp_path)
        box.installed(by="alice", state="3.3.0 " + "0" * 64)
        result, _ = box.run('shipped_files() { printf "README.md\\0"; }; install_role; echo "ROLE=$INSTALL_ROLE"')
        assert "ROLE=update" in result.stdout and "aktualisiert" in result.stdout

    def test_an_installation_from_before_the_marker_belongs_to_the_folders_owner(self, tmp_path):
        box = FakeRoot(tmp_path, owner="carol")
        box.installed(by=None)
        assert self.role(box) == "ROLE=update OWNER=carol"


@needs_bash
class TestTheSharedPart:

    def test_the_shared_part_goes_to_root_and_the_group_personal_files_keep_their_owner(self, tmp_path):
        box = FakeRoot(tmp_path, groups="bob")
        box.installed(by="alice")
        for personal in ("configs/secrets.store", "configs/config.yml", "output/b/model.gguf", "models/m.gguf"):
            (box.install / personal).parent.mkdir(parents=True, exist_ok=True)
            (box.install / personal).write_text("personal")
        (box.install / "configs" / "llm_providers.yml").write_text("shipped")
        result, calls = box.run("share_installation", user="alice")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "groupadd --system llmcf" in calls and "usermod -aG llmcf alice" in calls
        owned = " ".join(c for c in calls if c.startswith("chown -h root:llmcf"))
        modes = " ".join(c for c in calls if c.startswith("chmod u+rwX,g+rX,g-w,o-rwx"))
        for shared in ("orchestrator/__init__.py", ".venv/bin/python", "configs/llm_providers.yml"):
            assert str(box.install / shared) in owned and str(box.install / shared) in modes, shared
        assert f"{box.install} " in owned + " ", "the folder itself"
        for personal in ("secrets.store", "config.yml", "model.gguf", "models"):
            assert personal not in owned and personal not in modes, personal
        assert "abmelden" not in result.stdout and "nächsten Anmeldung" in result.stdout

    def test_before_the_framework_is_ready_nothing_is_handed_to_root(self, tmp_path):
        box = FakeRoot(tmp_path, ready=False)
        box.installed(by="alice")
        result, calls = box.run("share_installation", user="alice")
        assert result.returncode == 0 and not any("root:llmcf" in c or "groupadd" in c for c in calls)

    @pytest.mark.skipif(not shutil.which("rsync") or not shutil.which("git"), reason="needs rsync and git")
    @pytest.mark.parametrize("ready, recursive", [(True, False), (False, True)])
    def test_deploy_files_gives_the_installing_account_only_the_folder_once_ready(self, tmp_path, ready, recursive):
        box = FakeRoot(tmp_path, ready=ready)
        subprocess.run(["git", "-C", str(box.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(box.repo), "-c", "safe.directory=*", "add", "."], check=True)
        result, calls = box.run("apt-get() { return 1; }; deploy_files", user="alice")
        assert result.returncode == 0, result.stdout + result.stderr
        chowns = [c for c in calls if c.startswith("chown")]
        assert chowns == [f"chown {'-R ' if recursive else ''}alice:alice {box.install}"]

    def test_the_installation_records_who_installed_it_and_what_it_holds(self, tmp_path):
        box = FakeRoot(tmp_path)
        box.install.mkdir(parents=True)
        result, calls = box.run('shipped_files() { printf "README.md\\0"; }; record_installation', user="alice")
        assert (box.install / ".lcf-installed-by").read_text() == "alice\n"
        assert (box.install / ".lcf-installed-state").read_text().startswith("3.4.0 ")
        assert any(c.startswith("chown root:llmcf") and ".lcf-installed-by" in c for c in calls)


@needs_bash
class TestASecondPerson:

    def test_only_the_persons_part_is_installed(self, tmp_path):
        box = FakeRoot(tmp_path, groups="bob")
        box.installed(by="alice", state=box.state())
        script = ('shipped_files() { printf "README.md\\0orchestrator/__init__.py\\0"; }; '
                  'setup_engine_or_name() { echo ENGINE-FOR-PERSON; }; DITTO_CHOICE=skip; '
                  'for f in setup_system deploy_files setup_venv resolve_fresh configure share_installation; do '
                  'eval "$f() { echo SHARED-PART-TOUCHED-$f; }"; done; '
                  'check_root() { :; }; main --ditto=skip')
        result, calls = box.run(script)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SHARED-PART-TOUCHED" not in result.stdout, "no copy, no .venv, no new ownership"
        assert "nur der eigene Teil" in result.stdout and "von 'alice'" in result.stdout
        assert "ENGINE-FOR-PERSON" in result.stdout
        assert "groupadd --system llmcf" in calls and "usermod -aG llmcf bob" in calls
        folder = box.home / ".llm-framework"
        assert folder.is_dir() and f"chmod 0700 {folder}" in calls, "made as the person, readable only by them"
        assert "Melde dich einmal ab und wieder an" in result.stdout
        assert (box.install / ".lcf-installed-by").read_text() == "alice\n", "the shared part is not taken over"

    def test_a_person_already_in_the_group_is_not_added_again(self, tmp_path):
        box = FakeRoot(tmp_path, groups="bob llmcf")
        (tmp_path / "group-made").write_text("")
        result, calls = box.run("ensure_shared_group")
        assert result.returncode == 0 and not any(c.startswith(("usermod", "groupadd")) for c in calls)


def test_main_decides_the_role_before_anything_is_installed():
    main = INSTALL_SH.read_text(encoding="utf-8").split("\nmain() {", 1)[1]
    assert main.index("install_role") < main.index("person_install") < main.index("setup_system")
    assert main.index("setup_venv") < main.index("share_installation") < main.index("record_installation")
    assert main.index("record_installation") < main.index("setup_mode")


def test_install_bat_copies_nothing_for_a_second_account_and_puts_the_shortcut_on_the_public_desktop():
    text = INSTALL_BAT.read_bytes().decode("utf-8")
    assert text.count("\n") == text.count("\r\n"), "cmd needs CRLF line endings"
    start = text.index("if (-not $PersonOnly) {\r\n# 3. DATEIEN KOPIEREN")
    end = text.index("$LauncherBat = Join-Path $InstallDir \"start_framework.bat\"\r\n$PythonVenv")
    block = text[start:end]
    for inside in ("Copy-Robust $ScriptPath", "Install-Venv $TestedFile", "Set-Content $LauncherBat",
                   "Set-Content -LiteralPath $InstalledByFile", "$InstalledStateFile"):
        assert inside in block, inside
    assert '[Environment]::GetFolderPath("CommonDesktopDirectory")' in text
    assert "$InstalledBy -ne $SignedInAccount" in text and "$CheckoutState -eq $RecordedState" in text
