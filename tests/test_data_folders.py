"""Where the framework keeps its settings and data folders, and what the installers do about them:
no folders at startup, a read-only installation (C:\\Program Files) falls back to the per-user folder, the GUI's
first-run language goes to the effective config folder, deployment bundles never read the working directory, and
install.sh / install.bat drop their unused data folder and check the network over HTTPS."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager

DATA_KEYS = ("models_dir", "output_dir", "cache_dir")


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
            assert cfg.get(key) == str(tmp_path / "state" / key[:-4]), key
            assert not (tmp_path / "state" / key[:-4]).exists(), "created only when it is used"
        assert not any((installation / name).exists() for name in ("models", "output", "cache"))

    def test_a_writable_installation_keeps_relative_defaults(self, installation, tmp_path, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        cfg = ConfigManager()
        assert cfg.config_dir == installation / "configs"
        assert [cfg.get(k) for k in DATA_KEYS] == ["models", "output", "cache"]

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
        assert fm.orchestrator.build_engine.output_dir == tmp_path / "output"
        assert not any((tmp_path / name).exists() for name in ("models", "output", "cache", "logs"))

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
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "$0 $*" >> "{folder}/calls.log"\nexit {exit_code}\n')
    path.chmod(0o755)


def wait_for_internet(stubs: Path, **env):
    return subprocess.run(["/bin/bash", "-c", 'source "$1"; wait_for_internet', "test", str(INSTALL_SH)],
                          env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(stubs), **env},
                          capture_output=True, text=True, timeout=60)


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

    def test_no_unused_data_folder_and_user_data_survives_a_reinstall(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert "DATA_DIR" not in text and "user_config.yml" not in text and "8.8.8.8" not in text
        for path in ("/output", "/cache", "/models", "/logs", "/configs/config.yml"):
            assert f"--exclude '{path}'" in text, path   # rsync --delete keeps excluded paths in /opt


class TestInstallBat:

    def test_no_unused_data_folder_and_no_data_in_program_files(self):
        text = INSTALL_BAT.read_text(encoding="utf-8")
        for gone in ("CommonDocuments", "$DataDir", "user_config.yml", "Data.lnk"):
            assert gone not in text, gone
        copy = next(line for line in text.splitlines() if line.startswith("Copy-Robust $ScriptPath $InstallDir"))
        for folder in ("output", "cache", "models", "logs"):
            assert f'"{folder}"' in copy, folder
        assert "\r\n" in INSTALL_BAT.read_bytes().decode("utf-8"), "cmd needs CRLF line endings"
