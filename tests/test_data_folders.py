"""Where the framework keeps its settings and data folders, and what the installers do about them:
no folders at startup, a read-only installation (C:\\Program Files) falls back to the per-user folder, the GUI's
first-run language goes to the effective config folder, deployment bundles never read the working directory, and
install.sh / install.bat drop their unused data folder and check the network over HTTPS; running install.sh again
keeps what the user and the framework created in the installation."""

import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager

DATA_KEYS = ("models_dir", "build_dir", "cache_dir")
FOLDER_OF = {"models_dir": "models", "build_dir": "output", "cache_dir": "cache"}


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """Stands in for the installation folder (<install>/configs is where the config folder detection looks)."""
    root = tmp_path / "install"
    (root / "configs").mkdir(parents=True)
    monkeypatch.setattr(ConfigManager, "installation_config_dir", staticmethod(lambda: root / "configs"))
    return root


def read_only_like_program_files(monkeypatch, *folders):
    """os.access says yes (Windows ignores ACLs there), but creating a file fails, as in C:\\Program Files."""
    real = tempfile.TemporaryFile
    blocked = {Path(f).resolve() for f in folders}

    def temporary_file(*args, **kwargs):
        if kwargs.get("dir") is not None and Path(kwargs["dir"]).resolve() in blocked:
            raise PermissionError(13, "Access is denied", str(kwargs["dir"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryFile", temporary_file)


class TestReadOnlyInstallation:

    def test_settings_and_data_go_to_the_per_user_folder(self, installation, tmp_path, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        read_only_like_program_files(monkeypatch, installation, installation / "configs")

        cfg = ConfigManager()

        assert cfg.config_dir == tmp_path / "state"
        for key in DATA_KEYS:
            assert cfg.get(key) == str(tmp_path / "state" / FOLDER_OF[key]), key
            assert not (tmp_path / "state" / FOLDER_OF[key]).exists(), "created only when it is used"
        assert not any((installation / name).exists() for name in ("models", "output", "cache"))
        # the person's output folder for golden artifacts is in the home folder (eighth round, answer 3)
        assert cfg.get("output_dir") == "~/llmcf/output"

    def test_a_writable_installation_keeps_relative_defaults(self, installation, tmp_path, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        cfg = ConfigManager()
        assert cfg.config_dir == installation / "configs"
        assert [cfg.get(k) for k in DATA_KEYS] == ["models", "output", "cache"]
        assert cfg.get("output_dir") == "~/llmcf/output"

    def test_configured_folders_win_over_the_default(self, installation, tmp_path, monkeypatch):
        read_only_like_program_files(monkeypatch, installation, installation / "configs")
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "config.yml").write_text(f"output_dir: {tmp_path / 'builds'}\n")
        monkeypatch.setenv("LLM_FRAMEWORK_CACHE_DIR", str(tmp_path / "fast-disk"))
        cfg = ConfigManager()
        assert cfg.get("output_dir") == str(tmp_path / "builds")
        assert cfg.get("cache_dir") == str(tmp_path / "fast-disk")

    def test_the_cli_runtime_uses_the_effective_models_folder(self, installation, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        read_only_like_program_files(monkeypatch, installation, installation / "configs")
        rt = cli_module._local_runtime(cli_module.FrameworkContext())
        assert rt.models_dir == tmp_path / "state" / "models" / "ditto"


class TestNoFoldersAtStartup:

    def test_framework_boot_creates_no_data_folders(self, make_framework, tmp_path):
        fm = make_framework()
        assert fm.orchestrator.build_engine.build_dir == tmp_path / "output"
        assert fm.orchestrator.build_engine.output_dir == Path.home() / "llmcf" / "output"
        assert not any((tmp_path / name).exists() for name in ("models", "output", "cache", "logs"))
        assert not (Path.home() / "llmcf").exists(), "the output folder is made by the first build that uses it"

    def test_a_build_creates_the_folders_it_mounts(self, tmp_path):
        from orchestrator.Core.builder import BuildEngine
        engine = BuildEngine(ConfigManager(tmp_path / "configs"), base_dir=tmp_path / "install")
        assert not (tmp_path / "install").exists()
        progress = SimpleNamespace(current_stage="", progress_percent=0, add_log=lambda *_: None)
        config = SimpleNamespace(build_id="b1", target_arch="rockchip")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("orchestrator.Core.builder.asdict", lambda c: {"build_id": c.build_id})
            engine._prepare_build_environment(config, progress, REPO_ROOT / "targets" / "Rockchip")
        assert (tmp_path / "install" / "cache" / "models").is_dir()
        assert (tmp_path / "install" / "cache" / "builds" / "b1" / "output").is_dir()


class TestFirstRunLanguage:

    def test_language_is_saved_in_the_effective_config_folder(self, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        import orchestrator.main as gui_main
        install = tmp_path / "install"
        (install / "configs").mkdir(parents=True)
        monkeypatch.setattr(gui_main, "root_dir", install)
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "config.yml").write_text("gui_note: kept\n")  # LLM_FRAMEWORK_CONFIG_DIR

        class Dialog:
            selected_lang = "de"

            def exec(self):
                return gui_main.QDialog.Accepted

        class App:
            def __init__(self, argv):
                pass

            def setApplicationName(self, name):
                pass

            def exec(self):
                return 0

        class Window:
            def __init__(self, app_root):
                pass

            def show(self):
                pass

        monkeypatch.setenv("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
        monkeypatch.setattr(sys, "argv", ["llm-gui"])
        monkeypatch.setattr(gui_main, "QApplication", App)
        monkeypatch.setattr(gui_main, "PassphrasePrompt", lambda app: None)
        monkeypatch.setattr(gui_main, "set_passphrase_prompt", lambda prompt: None)
        monkeypatch.setattr(gui_main, "LanguageSelectionDialog", Dialog)
        monkeypatch.setattr(gui_main, "MainOrchestrator", Window)
        monkeypatch.setattr(gui_main, "get_i18n", lambda lang=None: None)
        with pytest.raises(SystemExit):
            gui_main.main()

        saved = yaml.safe_load((tmp_path / "configs" / "config.yml").read_text())
        assert saved["language"] == "de" and saved["gui_note"] == "kept"
        assert not (install / "configs" / "config.yml").exists(), "never the installation's configs/"

    @pytest.mark.parametrize("content", ["ai_provider: openai\nai_model: [unclosed\n", "- a list\n- not settings\n"])
    def test_a_config_file_that_cannot_be_read_is_not_overwritten(self, tmp_path, content):
        # Its settings were not loaded, so the language would be asked again; saving it must not replace the file
        # with nothing but the language (the old first-run code refused to write such a file, too).
        pytest.importorskip("PySide6")
        from orchestrator.main import save_language
        config_file = tmp_path / "configs" / "config.yml"
        config_file.parent.mkdir()
        config_file.write_text(content)
        assert save_language(ConfigManager(), "de") is False
        assert config_file.read_text() == content


class TestDeploymentBundle:

    def test_user_configs_never_come_from_the_working_directory(self, tmp_path, monkeypatch):
        from orchestrator.Core.deployment_manager import DeploymentManager
        project = tmp_path / "project"
        (project / "config" / "prompts").mkdir(parents=True)
        (project / "config" / "prompts" / "planted.txt").write_text("from the working directory")
        monkeypatch.chdir(project)
        dest = tmp_path / "bundle"

        for config in (SimpleNamespace(get=lambda k, d=None: d),                       # no config_dir at all
                       SimpleNamespace(config_dir=tmp_path / "missing")):              # not created yet
            DeploymentManager(SimpleNamespace(config=config))._bundle_user_configs(dest)
            assert not (dest / "prompts").exists()

        configs = tmp_path / "configs"
        (configs / "prompts").mkdir(parents=True)
        (configs / "prompts" / "system.txt").write_text("mine")
        DeploymentManager(SimpleNamespace(config=SimpleNamespace(config_dir=configs)))._bundle_user_configs(dest)
        assert (dest / "prompts" / "system.txt").read_text() == "mine"
        assert not (dest / "prompts" / "planted.txt").exists()


def test_the_repository_ignores_the_data_folders():
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    paths = ["output/build_1/model.gguf", "cache/builds/x/log.txt", "models/ditto/m.gguf", "logs/a.log"]
    ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", *paths], capture_output=True, text=True)
    assert ignored.stdout.split() == paths
    tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "output", "cache", "models", "logs"],
                             capture_output=True, text=True, check=True)
    assert tracked.stdout == ""


# --- installers ----------------------------------------------------------------------------------------------

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_BAT = REPO_ROOT / "install.bat"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="needs bash")


def stub(folder: Path, name: str, exit_code: int):
    """A fake command that logs its arguments ("ARGV: ...") and what it read on stdin ("STDIN: ...")."""
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "ARGV: $0 $*" >> "{folder}/calls.log"\n'
                    f'sed "s/^/STDIN: /" >> "{folder}/calls.log"\nexit {exit_code}\n')
    path.chmod(0o755)


def wait_for_internet(stubs: Path, path: Optional[str] = None, **env):
    return subprocess.run(["/bin/bash", "-c", 'source "$1"; wait_for_internet', "test", str(INSTALL_SH)],
                          env={"PATH": path or f"{stubs}:/usr/bin:/bin", "HOME": str(stubs), **env},
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)


@needs_bash
class TestInstallSh:

    def test_syntax_and_sourcing_does_not_install(self, tmp_path):
        assert subprocess.run(["/bin/bash", "-n", str(INSTALL_SH)]).returncode == 0
        run = subprocess.run(["/bin/bash", "-c", 'source "$1"; echo sourced', "test", str(INSTALL_SH)],
                             env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, capture_output=True, text=True)
        assert run.returncode == 0 and run.stdout.strip() == "sourced" and "Root-Rechte" not in run.stdout

    def test_blocked_icmp_does_not_matter_https_does(self, tmp_path):
        stubs = tmp_path / "bin"
        stub(stubs, "ping", 1)   # ICMP blocked
        stub(stubs, "curl", 0)   # HTTPS to the package index works
        run = wait_for_internet(stubs)
        assert run.returncode == 0, run.stdout + run.stderr
        calls = (stubs / "calls.log").read_text()
        assert "ping" not in calls and "https://pypi.org/simple/" in calls and "--max-time" in calls

    def test_unreachable_package_index_stops_with_a_hint(self, tmp_path):
        stubs = tmp_path / "bin"
        stub(stubs, "curl", 7)
        run = wait_for_internet(stubs, PIP_INDEX_URL="https://mirror.example/simple/")
        assert run.returncode == 1
        assert "https://mirror.example/simple/" in run.stdout and "sudo -E" in run.stdout

    @pytest.mark.parametrize("index, probe", [
        (None, "https://pypi.org/simple/pip/"),
        ("https://mirror.example/simple", "https://mirror.example/simple/pip/"),
    ])
    def test_the_check_loads_one_small_project_page_not_the_whole_index(self, tmp_path, index, probe):
        # https://pypi.org/simple/ is over 40 MB: with a 10 s limit a connection below ~40 Mbit/s counted as offline.
        stubs = tmp_path / "bin"
        stub(stubs, "curl", 0)
        run = wait_for_internet(stubs, **({"PIP_INDEX_URL": index} if index else {}))
        assert run.returncode == 0, run.stdout + run.stderr
        assert f'STDIN: url = "{probe}"' in (stubs / "calls.log").read_text()

    def test_credentials_in_the_index_url_are_never_shown_or_put_on_a_command_line(self, tmp_path):
        stubs = tmp_path / "bin"
        stub(stubs, "curl", 22)
        run = wait_for_internet(stubs, PIP_INDEX_URL="https://alice:s3cr3t@mirror.example/simple/")
        assert run.returncode == 1
        assert "s3cr3t" not in run.stdout + run.stderr and "mirror.example/simple/" in run.stdout
        calls = (stubs / "calls.log").read_text().splitlines()
        assert not any("s3cr3t" in line for line in calls if line.startswith("ARGV:")), "visible in ps"
        assert 'STDIN: url = "https://alice:s3cr3t@mirror.example/simple/pip/"' in calls, "curl still gets them"

    def test_wget_when_there_is_no_curl(self, tmp_path):
        stubs = tmp_path / "bin"
        stub(stubs, "wget", 0)
        for tool in ("sed", "cat"):  # nothing else from the system, so no real curl is found
            (stubs / tool).symlink_to(next(Path(d) / tool for d in ("/usr/bin", "/bin") if (Path(d) / tool).exists()))
        run = wait_for_internet(stubs, path=str(stubs))
        assert run.returncode == 0, run.stdout + run.stderr
        calls = (stubs / "calls.log").read_text()
        assert "STDIN: https://pypi.org/simple/pip/" in calls and "--timeout=10" in calls

    @pytest.mark.skipif(not Path("/usr/bin/dash").exists() and not Path("/bin/dash").exists(), reason="needs dash")
    def test_sh_or_dash_stops_with_a_clear_message(self, tmp_path):
        dash = next(p for p in ("/usr/bin/dash", "/bin/dash") if Path(p).exists())
        run = subprocess.run([dash, str(INSTALL_SH)], env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        assert run.returncode == 1
        assert "bash" in run.stderr and "Bad substitution" not in run.stderr and "parameter not set" not in run.stderr

    def test_no_unused_data_folder(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert "DATA_DIR" not in text and "user_config.yml" not in text and "8.8.8.8" not in text

    @pytest.mark.skipif(not shutil.which("rsync") or not shutil.which("git"), reason="needs rsync and git")
    def test_a_reinstall_keeps_what_the_user_and_the_framework_created(self, tmp_path):
        """install.sh used rsync --delete with a few excludes: running it again deleted the targets made with the
        module wizard or `module create`, community packs installed into targets/ and community/, and anything
        else in /opt that the repository does not ship. Now only files an earlier install.sh copied are removed."""
        repo, outside = tmp_path / "repo", tmp_path / "outside"
        install = tmp_path / "opt" / "llm-cross-compiler-framework"
        shipped = {"README.md": "v1", "orchestrator/main.py": "v1", "orchestrator/old.py": "old",
                   "targets/Rockchip/target.yml": "v1", "targets/Rockchip/modules/build.sh": "v1",
                   "community/knowledge/.gitkeep": "", "configs/llm_providers.yml": "v1"}
        for rel, text in {**shipped, "scratch.txt": "not tracked", "configs/config.yml": "a developer's"}.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        git = ["git", "-C", str(repo), "-c", "safe.directory=*"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", *shipped], check=True)

        def deploy():
            script = ('source "$1"; REPO_ROOT="$2"; INSTALL_DIR="$3"; '
                      'apt-get() { echo "apt-get must not run" >&2; return 1; }; deploy_files')
            return subprocess.run(["/bin/bash", "-c", script, "test", str(INSTALL_SH), str(repo), str(install)],
                                  env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
                                       "USER": os.environ.get("USER") or getpass.getuser()},
                                  capture_output=True, text=True, timeout=120)

        run = deploy()
        assert run.returncode == 0, run.stdout + run.stderr
        assert {p.relative_to(install).as_posix() for p in install.rglob("*") if p.is_file()} == \
            set(shipped) | {".lcf-installed-files"}, "only the files the repository ships"

        created = {"targets/my-board/target.yml": "module wizard", "targets/zoo-board/.lcf-provenance.json": "pack",
                   "targets/Rockchip/modules/my_step.sh": "added by the user",
                   "community/recipes/my-recipe/recipe.yml": "pack", "community/knowledge/notes.imported": "marker",
                   "community/contributions/contribution-target-x-1.0.0.zip": "export",
                   "configs/config.yml": "settings", "output/build_1/model.gguf": "build", "cache/x": "cache",
                   "models/m.gguf": "model", ".venv/bin/python": "venv"}
        for rel, text in created.items():
            (install / rel).parent.mkdir(parents=True, exist_ok=True)
            (install / rel).write_text(text)
        # Entries no install.sh writes must never delete anything outside the installation.
        outside.mkdir()
        (outside / "victim.txt").write_text("keep")
        (install / "linked").symlink_to(outside, target_is_directory=True)
        with open(install / ".lcf-installed-files", "ab") as manifest:
            manifest.write(b"../../outside/victim.txt\0" + str(outside / "victim.txt").encode() + b"\0"
                           b"linked/victim.txt\0")

        (repo / "orchestrator" / "main.py").write_text("v2")
        later = (install / "orchestrator" / "main.py").stat().st_mtime + 10  # as after a git pull, not the same second
        os.utime(repo / "orchestrator" / "main.py", (later, later))
        (repo / "orchestrator" / "new.py").write_text("new")
        subprocess.run(git + ["rm", "-q", "-f", "orchestrator/old.py"], check=True)  # staged only, never committed
        subprocess.run(git + ["add", "orchestrator/new.py"], check=True)
        run = deploy()

        assert run.returncode == 0, run.stdout + run.stderr
        assert (install / "orchestrator" / "main.py").read_text() == "v2"
        assert (install / "orchestrator" / "new.py").exists()
        assert not (install / "orchestrator" / "old.py").exists(), "a file the repository no longer ships is removed"
        assert {rel: (install / rel).read_text() for rel in created} == created, "everything else stays"
        assert (outside / "victim.txt").read_text() == "keep"
        listed = (install / ".lcf-installed-files").read_bytes().split(b"\0")
        assert b"orchestrator/new.py" in listed and b"orchestrator/old.py" not in listed

    @pytest.mark.skipif(not shutil.which("rsync") or not shutil.which("git"), reason="needs rsync and git")
    def test_the_installed_copy_a_failed_listing_and_a_stale_link_delete_nothing_else(self, tmp_path):
        """Three ways the reinstall could still lose files: the copy of install.sh in /opt listed every file there
        as shipped (own targets and packs included), so the next update from the repository deleted them; a listing
        that failed or came back empty removed the whole installed program; and a stale entry that is a link
        deleted the file it points to instead of the link."""
        repo, bin_dir = tmp_path / "repo", tmp_path / "bin"
        install = tmp_path / "opt" / "llm-cross-compiler-framework"
        shipped = {"README.md": "v1", "install.sh": INSTALL_SH.read_text(encoding="utf-8"),
                   "orchestrator/main.py": "v1", "orchestrator/old.md": "old", "targets/Rockchip/target.yml": "v1"}
        for rel, text in shipped.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        git = ["git", "-C", str(repo), "-c", "safe.directory=*"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", *shipped], check=True)
        # A git whose ls-files fails halfway (a damaged index, no read access) after rev-parse found the work tree.
        bin_dir.mkdir()
        (bin_dir / "git").write_text(f'#!/bin/sh\ncase "$*" in *ls-files*) printf "README.md\\0"; exit 128 ;; esac\n'
                                     f'exec "{shutil.which("git")}" "$@"\n')
        (bin_dir / "git").chmod(0o755)

        def deploy(script, root, path=os.environ["PATH"]):
            code = ('source "$1"; REPO_ROOT="$2"; INSTALL_DIR="$3"; '
                    'apt-get() { echo "apt-get must not run" >&2; return 1; }; deploy_files')
            return subprocess.run(["/bin/bash", "-c", code, "test", str(script), str(root), str(install)],
                                  env={"PATH": path, "HOME": str(tmp_path),
                                       "USER": os.environ.get("USER") or getpass.getuser()},
                                  capture_output=True, text=True, timeout=120)

        def files():
            return {p.relative_to(install).as_posix(): p.read_text() for p in install.rglob("*")
                    if p.is_file() and p.name != ".lcf-installed-files"}

        assert deploy(INSTALL_SH, repo).returncode == 0
        own = {"targets/my-board/target.yml": "module wizard", "community/recipes/r/recipe.yml": "pack"}
        for rel, text in own.items():
            (install / rel).parent.mkdir(parents=True, exist_ok=True)
            (install / rel).write_text(text)
        manifest, before = (install / ".lcf-installed-files").read_bytes(), files()

        run = deploy(install / "install.sh", install)  # sudo /opt/llm-cross-compiler-framework/install.sh
        assert run.returncode == 0, run.stdout + run.stderr
        assert (install / ".lcf-installed-files").read_bytes() == manifest and files() == before

        empty = tmp_path / "empty"
        empty.mkdir()
        for script, root, path in ((INSTALL_SH, empty, os.environ["PATH"]),
                                   (INSTALL_SH, repo, f"{bin_dir}{os.pathsep}{os.environ['PATH']}")):
            run = deploy(script, root, path)
            assert run.returncode != 0 and "nichts kopiert oder entfernt" in run.stdout, run.stdout + run.stderr
            assert (install / ".lcf-installed-files").read_bytes() == manifest and files() == before

        (install / "orchestrator" / "old.md").unlink()
        (install / "orchestrator" / "old.md").symlink_to("../README.md")  # e.g. a link the user put there
        subprocess.run(git + ["rm", "-q", "--cached", "orchestrator/old.md"], check=True)
        run = deploy(INSTALL_SH, repo)
        assert run.returncode == 0, run.stdout + run.stderr
        assert not (install / "orchestrator" / "old.md").is_symlink(), "the stale link itself is removed"
        assert (install / "README.md").read_text() == "v1", "never the file it points to"
        assert {rel: (install / rel).read_text() for rel in own} == own


    # --- the installation before the rename (/opt/llm-conversion-framework) ----------------------------------

    @staticmethod
    def old_and_new(tmp_path: Path):
        old, new = tmp_path / "opt" / "llm-conversion-framework", tmp_path / "opt" / "llm-cross-compiler-framework"
        for rel, text in {"orchestrator/main.py": "old code", "configs/config.yml": "language: de\n",
                          "output/build_1/model.gguf": "build", "targets/my-board/target.yml": "module wizard",
                          ".lcf-installed-files": "orchestrator/main.py\0"}.items():
            (old / rel).parent.mkdir(parents=True, exist_ok=True)
            (old / rel).write_text(text)
        new.mkdir(parents=True)
        return old, new

    @staticmethod
    def tree(folder: Path) -> dict:
        return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
                for p in sorted(folder.rglob("*"))}

    @staticmethod
    def run_old_steps(old: Path, new: Path, home: Path):
        # sudo -u USER CMD runs CMD as that user; here the test user is that user already.
        script = ('source "$1"; INSTALL_DIR="$2"; OLD_INSTALL_DIR="$3"; '
                  'sudo() { shift 2; "$@"; }; adopt_old_settings; old_installation_note')
        user = os.environ.get("USER") or getpass.getuser()
        return subprocess.run(["/bin/bash", "-c", script, "test", str(INSTALL_SH), str(new), str(old)],
                              env={"PATH": os.environ["PATH"], "HOME": str(home), "USER": user, "SUDO_USER": user},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_the_old_installation_is_named_its_settings_copied_and_nothing_of_it_removed(self, tmp_path):
        old, new = self.old_and_new(tmp_path)
        before = self.tree(old)

        run = self.run_old_steps(old, new, tmp_path)

        assert run.returncode == 0, run.stdout + run.stderr
        assert self.tree(old) == before, "the installer never changes or deletes the old installation"
        assert (new / "configs" / "config.yml").read_text() == "language: de\n", "the settings are copied"
        assert not (new / "output").exists() and not (new / "targets").exists(), "builds and targets stay"
        out = run.stdout
        assert str(old) in out and "löscht sie nicht" in out and str(new) in out
        assert "configs/config.yml" in out and f"{old}/output" in out and "targets/" in out
        assert f"sudo rm -rf {old}" in out and f"ls -la {old}" in out, "how to remove it, after looking inside"

    def test_existing_settings_of_the_new_installation_are_never_overwritten(self, tmp_path):
        old, new = self.old_and_new(tmp_path)
        (new / "configs").mkdir()
        (new / "configs" / "config.yml").write_text("language: en\n")

        run = self.run_old_steps(old, new, tmp_path)

        assert run.returncode == 0, run.stdout + run.stderr
        assert (new / "configs" / "config.yml").read_text() == "language: en\n"
        assert "wurden in die neue Installation kopiert" not in run.stdout

    def test_a_linked_settings_file_is_not_copied(self, tmp_path):
        old, new = self.old_and_new(tmp_path)
        secret = tmp_path / "elsewhere.txt"
        secret.write_text("not a settings file")
        (old / "configs" / "config.yml").unlink()
        (old / "configs" / "config.yml").symlink_to(secret)

        run = self.run_old_steps(old, new, tmp_path)

        assert run.returncode == 0, run.stdout + run.stderr
        assert not (new / "configs" / "config.yml").exists() and not (new / "configs" / "config.yml").is_symlink()

    def test_without_an_old_installation_nothing_is_said_or_copied(self, tmp_path):
        old, new = self.old_and_new(tmp_path)
        shutil.rmtree(old)

        run = self.run_old_steps(old, new, tmp_path)

        assert run.returncode == 0 and run.stdout == "", run.stdout + run.stderr
        assert not (new / "configs").exists()

    def test_main_runs_both_steps_and_no_command_removes_the_old_folder(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        main = text[text.index("main() {"):]
        assert main.index("deploy_files") < main.index("adopt_old_settings") < main.index("setup_venv")
        finish = text[text.index("finish() {"):text.index("main() {")]
        assert "old_installation_note" in finish
        assert 'OLD_INSTALL_DIR="/opt/llm-conversion-framework"' in text
        assert 'APP_NAME="llm-cross-compiler-framework"' in text
        for line in text.splitlines():
            if "OLD_INSTALL_DIR" in line and re.search(r"\brm\b", line):
                assert line.strip().startswith("echo "), f"only said, never run: {line}"


class TestInstallBat:

    def test_no_unused_data_folder_and_no_data_in_program_files(self):
        text = INSTALL_BAT.read_text(encoding="utf-8")
        for gone in ("CommonDocuments", "$DataDir", "user_config.yml", "Data.lnk"):
            assert gone not in text, gone
        copy = next(line for line in text.splitlines() if line.startswith("Copy-Robust $ScriptPath $InstallDir"))
        for folder in ("output", "cache", "models", "logs"):
            assert f'"{folder}"' in copy, folder
        assert "\r\n" in INSTALL_BAT.read_bytes().decode("utf-8"), "cmd needs CRLF line endings"
