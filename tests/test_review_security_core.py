"""Regression tests for the Phase 1+2 review fixes (commit 1a714ea): configuration location, secrets
store, audit key, audit log, tool policy, tool registry errors and the matching doctor findings.

The scenarios follow handover/2026-10-10/review-phase1-2-findings.json. Tests marked xfail(strict=True)
show places where the fixed code is still wrong; each reason starts with "BUG:".
"""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import BaseModel

from conftest import REPO_ROOT
from orchestrator.Core import secrets_manager as secrets_module
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.doctor import Doctor, Severity, check_secrets, check_tool_policy
from orchestrator.Core.secrets_manager import SecretsManager
from orchestrator.tools import audit as audit_module
from orchestrator.tools import framework_tools
from orchestrator.tools.audit import GENESIS, AuditLog
from orchestrator.tools.framework_tools import AUDIT_KEY_ID
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolDenied, ToolError, ToolRegistry, ToolSpec

HF = "hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456"
SK = "sk-proj-AbCdEf0123456789_xyz-ABCDEFGHIJ"

# What a hostile project folder would like to set for an MCP server started inside it.
PLANTED = {
    "source_repositories": {"llama_cpp": {"url": "https://evil.example/llama.cpp.git"}},
    "image_base_debian": "evil.example/debian:backdoored",
    "docker_registry": "evil.example",
    "llama_cpp_version": "evil-tag",
}
PLANTED_FILES = ("configs/framework.yml", "configs/config.yml", "config/framework.yml", "config/config.yml",
                 "framework.yml", "config.yml")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network, no Docker: LiteLLM uses its bundled price list and the Docker client finds no daemon."""
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/llm-framework-test/docker.sock")


def snapshot(root: Path):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


def plant_project(root: Path, extra=None) -> Path:
    data = dict(PLANTED, **(extra or {}))
    for rel in PLANTED_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data))
    return root


@pytest.fixture
def install_configs(tmp_path, monkeypatch):
    """Stands in for <installation>/configs, so no test reads or writes the repository's configs/."""
    path = tmp_path / "install" / "configs"
    path.mkdir(parents=True)
    monkeypatch.setattr(ConfigManager, "installation_config_dir", staticmethod(lambda: path))
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A foreign project folder as working directory (Claude Code starts stdio MCP servers there)."""
    root = plant_project(tmp_path / "project")
    monkeypatch.chdir(root)
    return root


def boot_framework(tmp_path, config=None):
    """Like conftest.make_framework, but lets FrameworkManager find its configuration itself (config=None),
    as the CLI and the MCP server do."""
    from orchestrator.Core.framework import FrameworkManager
    fm = FrameworkManager(config)
    fm.info.installation_path = tmp_path
    fm.config.set("targets_dir", str(REPO_ROOT / "targets"))
    assert fm.initialize(), "FrameworkManager.initialize() failed"
    return fm


def invoke_cli(framework, *args):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx())


def flat(text: str) -> str:
    """Rich wraps long lines; compare on single-spaced text."""
    return " ".join(text.split())


# ======================================================================================================
# Configuration: never from the working directory
# ======================================================================================================

class TestConfigLocation:

    def test_planted_files_would_take_effect_from_a_real_config_dir(self, tmp_path):
        """Positive control: the planted YAML is valid, so the 'ignored' tests below are not vacuous."""
        cfg = ConfigManager(plant_project(tmp_path / "trusted") / "configs")
        assert cfg.get("source_repositories") == PLANTED["source_repositories"]
        assert cfg.get("docker_registry") == "evil.example"

    def test_cwd_configs_config_and_framework_yml_are_never_read(self, project, install_configs, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        before = snapshot(project)

        cfg = ConfigManager()

        assert cfg.config_dir == install_configs
        assert cfg.get("source_repositories") == {}
        assert cfg.get("image_base_debian") == "debian:bookworm-slim"
        assert cfg.get("docker_registry") == "ghcr.io"
        assert cfg.get("llama_cpp_version") != "evil-tag"
        assert snapshot(project) == before, "nothing may be written into the working directory"
        assert {"environments", "templates", "backups"} <= {p.name for p in install_configs.iterdir()}

    def test_without_an_installation_folder_the_per_user_folder_is_used(self, project, tmp_path, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setattr(ConfigManager, "installation_config_dir",
                            staticmethod(lambda: tmp_path / "install-without-configs" / "configs"))
        before = snapshot(project)

        cfg = ConfigManager()

        assert cfg.config_dir == tmp_path / "home" / ".llm-framework"
        assert cfg.get("source_repositories") == {}
        assert snapshot(project) == before

        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        assert ConfigManager().config_dir == tmp_path / "state"
        assert snapshot(project) == before

    def test_read_only_installation_folder_falls_back_to_the_per_user_folder(self, project, install_configs,
                                                                             tmp_path, monkeypatch):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        real_access = os.access
        monkeypatch.setattr(os, "access", lambda p, mode, *a, **kw: False if Path(p) == install_configs
                            else real_access(p, mode, *a, **kw))
        before = snapshot(project)

        cfg = ConfigManager()

        assert cfg.config_dir == tmp_path / "state"
        assert snapshot(project) == before

    def test_llm_framework_config_dir_wins(self, project, install_configs, tmp_path, monkeypatch):
        override = tmp_path / "override"
        override.mkdir()
        (override / "framework.yml").write_text("docker_registry: override.example\n")
        (install_configs / "framework.yml").write_text("docker_registry: install.example\n")
        monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", str(override))

        cfg = ConfigManager()

        assert cfg.config_dir == override
        assert cfg.get("docker_registry") == "override.example"
        assert cfg.get("source_repositories") == {}

    def test_llm_framework_config_dir_expands_the_home_directory(self, project, install_configs, tmp_path,
                                                                 monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", "~/my-llm-config")
        cfg = ConfigManager()
        assert cfg.config_dir == tmp_path / "home" / "my-llm-config"
        assert (tmp_path / "home" / "my-llm-config" / "environments").is_dir()

    def test_framework_booted_inside_a_project_ignores_its_config(self, project, install_configs, tmp_path,
                                                                  monkeypatch, memory_keyring):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        fm = boot_framework(tmp_path)
        try:
            assert fm.config.config_dir == install_configs
            assert fm.config.get("source_repositories") == {}
            assert fm.config.get("image_base_debian") == "debian:bookworm-slim"
            assert fm.secrets_manager.secrets_file == tmp_path / "state" / "secrets.store"
            assert fm.tool_registry.audit.path == tmp_path / "state" / "audit.db"
            for leftover in ("secrets.store", "environments", "templates", "backups", "audit.db",
                             "tool_policy.json"):
                for folder in (project, project / "config", project / "configs"):
                    assert not (folder / leftover).exists(), f"{leftover} written into {folder}"
        finally:
            fm.shutdown()

    def test_framework_boot_writes_nothing_into_the_working_directory(self, project, install_configs,
                                                                     tmp_path, monkeypatch, memory_keyring):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        before = snapshot(project)
        fm = boot_framework(tmp_path)
        fm.shutdown()
        assert snapshot(project) == before

    def test_models_dir_resolves_against_the_installation(self, project, install_configs, tmp_path,
                                                          monkeypatch, memory_keyring):
        monkeypatch.delenv("LLM_FRAMEWORK_CONFIG_DIR")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        fm = boot_framework(tmp_path)
        try:
            assert fm.model_manager.models_dir == tmp_path / "models"
            # Made by the download or build that needs them, not at every start (empty folders in the install).
            assert not any((tmp_path / name).exists() for name in ("models", "output", "cache"))
        finally:
            fm.shutdown()


class TestCliFromForeignProject:
    """The real CLI started from a project folder that contains config/framework.yml."""

    @staticmethod
    def run_cli(cwd: Path, tmp_path: Path, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "FRAMEWORK_"))}
        env.update(PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
                   LLM_SECRET_STORAGE="ephemeral",
                   LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_CONFIG_DIR=str(tmp_path / "cfg"))
        return subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", *args], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=180, check=False)

    @pytest.fixture
    def setup(self, tmp_path):
        safe = tmp_path / "safe"
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        # The trusted configuration keeps every data folder out of the repository and out of the project.
        (cfg / "framework.yml").write_text(yaml.safe_dump({
            "output_dir": str(safe / "output"), "cache_dir": str(safe / "cache"),
            "models_dir": str(safe / "models"), "logs_dir": str(safe / "logs")}))
        project = tmp_path / "project"
        evil = {"output_dir": str(project / "evil_output"), "cache_dir": str(project / "evil_cache"),
                "models_dir": str(project / "evil_models"), "logs_dir": str(project / "evil_logs")}
        plant_project(project, evil)
        return SimpleNamespace(project=project, safe=safe, evil=evil, before=snapshot(project))

    def test_tools_list_ignores_the_project_config(self, setup, tmp_path):
        result = self.run_cli(setup.project, tmp_path, "tools", "list")

        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert "start_build" in result.stdout
        listed = self.run_cli(setup.project, tmp_path, "deploy", "list-artifacts")
        assert listed.returncode == 0, listed.stderr
        assert str(setup.safe / "output") in "".join(listed.stdout.split()), "the trusted configuration was used"
        for folder in setup.evil.values():
            assert not Path(folder).exists(), f"project setting used: {folder}"
        assert snapshot(setup.project) == setup.before, "nothing may be written into the project"

    def test_source_repositories_from_the_project_are_not_used(self, setup, tmp_path):
        result = self.run_cli(setup.project, tmp_path, "config", "repos")

        assert result.returncode == 0, result.stderr
        assert "evil.example" not in result.stdout
        assert "No repository overrides defined" in result.stdout
        assert snapshot(setup.project) == setup.before


# ======================================================================================================
# Secrets store
# ======================================================================================================

def secrets_for(tmp_path: Path, config_dir: Path | None = None) -> SecretsManager:
    """A SecretsManager wired like FrameworkManager does it (config folder, logs folder, per-user folder)."""
    config = SimpleNamespace(config_dir=config_dir or tmp_path / "configs", logs_dir=str(tmp_path / "logs"),
                             user_state_dir=tmp_path / "state")
    sm = SecretsManager(SimpleNamespace(config=config))
    assert sm.initialize()
    return sm


def leftovers(folder: Path):
    return sorted(p.name for p in folder.glob(".secrets.*"))


class TestSecretsStore:

    def test_store_lives_in_the_per_user_folder(self, memory_keyring, tmp_path):
        sm = secrets_for(tmp_path)
        assert sm.secrets_file == tmp_path / "state" / "secrets.store"
        assert sm.set_secret("hf_token", HF)
        assert (tmp_path / "state" / "secrets.store").is_file()
        assert not (tmp_path / "configs" / "secrets.store").exists()
        assert HF not in (tmp_path / "state" / "secrets.store").read_text(), "values are stored encrypted"

    def test_booted_framework_uses_one_store_whatever_config_dir_and_cwd(self, make_framework, tmp_path,
                                                                         monkeypatch):
        first = make_framework()
        assert first.secrets_manager.secrets_file == first.config.user_state_dir / "secrets.store"
        assert first.secrets_manager.set_secret("hf_token", HF)
        assert not (tmp_path / "configs" / "secrets.store").exists()

        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "cfg").mkdir(parents=True)
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", str(elsewhere / "cfg"))
        second = boot_framework(tmp_path)
        try:
            assert second.config.config_dir == elsewhere / "cfg"
            assert second.secrets_manager.get_secret("hf_token") == HF
        finally:
            second.shutdown()

    def test_legacy_store_is_copied_over_once(self, memory_keyring, tmp_path):
        old = SecretsManager(tmp_path / "configs")  # the pre-3.0 layout: <config_dir>/secrets.store
        assert old.initialize() and old.set_secret("hf_token", HF) and old.set_secret("openai_api_key", SK)
        legacy = tmp_path / "configs" / "secrets.store"
        legacy_bytes = legacy.read_bytes()

        sm = secrets_for(tmp_path)

        assert sm.get_secret("hf_token") == HF and sm.get_secret("openai_api_key") == SK
        assert sm.secrets_file.is_file()
        if os.name == "posix":
            assert sm.secrets_file.stat().st_mode & 0o777 == 0o600
        assert legacy.read_bytes() == legacy_bytes, "the old copy stays untouched"

        # Once copied, the per-user store is authoritative: a deleted secret stays deleted.
        assert sm.delete_secret("hf_token")
        again = secrets_for(tmp_path)
        assert again.get_secret("hf_token") is None
        assert again.get_secret("openai_api_key") == SK

    def test_secrets_deleted_after_the_migration_never_come_back(self, memory_keyring, tmp_path):
        old = SecretsManager(tmp_path / "configs")
        assert old.initialize() and old.set_secret("hf_token", HF)
        sm = secrets_for(tmp_path)
        assert sm.get_secret("hf_token") == HF
        assert sm.delete_secret("hf_token")  # e.g. the token leaked and was revoked

        # Later the store gets damaged; doctor says: move it aside and set the secrets again.
        sm.secrets_file.rename(sm.secrets_file.with_name("secrets.store.broken"))
        fresh = secrets_for(tmp_path)

        assert fresh.get_secret("hf_token") is None

    def test_damaged_legacy_store_is_reported_not_silently_skipped(self, memory_keyring, tmp_path):
        legacy = tmp_path / "configs" / "secrets.store"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'{"hf_token": "gAAAAABk')  # half written by a pre-3.0 process (truncate, then write)

        sm = secrets_for(tmp_path)

        assert legacy.read_bytes() == b'{"hf_token": "gAAAAABk'
        finding = check_secrets(SimpleNamespace(secrets_manager=sm))[0]
        assert finding.severity == Severity.ERROR

    @pytest.mark.parametrize("content", [b'{"hf_token": "gAAAAABk', b"[1, 2, 3]", b"\x00\xff" * 16, b"",
                                         b'"just a string"'])
    def test_damaged_store_is_never_overwritten(self, memory_keyring, tmp_path, content):
        store = tmp_path / "state" / "secrets.store"
        store.parent.mkdir(parents=True)
        store.write_bytes(content)

        sm = secrets_for(tmp_path)

        assert sm._load_failed
        assert sm.set_secret("hf_token", HF) is False
        assert sm.delete_secret("hf_token") is False
        assert store.read_bytes() == content
        assert leftovers(store.parent) == []
        finding = check_secrets(SimpleNamespace(secrets_manager=sm))[0]
        assert finding.severity == Severity.ERROR and str(store) in finding.message

    def test_store_damaged_after_loading_is_not_overwritten(self, memory_keyring, tmp_path):
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF)
        damaged = b'{"hf_token": "gAAAAABk'
        sm.secrets_file.write_bytes(damaged)  # another (older) process is half-way through writing it

        assert sm.set_secret("openai_api_key", SK) is False
        assert sm.delete_secret("hf_token") is False
        assert sm.secrets_file.read_bytes() == damaged

        # Repaired (or moved aside), writing works again.
        sm.secrets_file.unlink()
        assert sm.set_secret("openai_api_key", SK)
        assert secrets_for(tmp_path).get_secret("openai_api_key") == SK

    def test_boot_with_a_damaged_store_leaves_every_byte_in_place(self, make_framework, tmp_path):
        """The original high finding: creating the audit key at boot rewrote a store that failed to load."""
        store = tmp_path / "state" / "secrets.store"
        store.parent.mkdir(parents=True)
        damaged = b'{"hf_token": "gAAAAABkZ1", "openai_api_key": "gAAAA'
        store.write_bytes(damaged)

        fm = make_framework()

        assert store.read_bytes() == damaged
        assert fm.tool_registry is not None and fm.tool_registry.audit.keyed
        report = Doctor(fm, [check_secrets]).run()
        assert report.findings[0].severity == Severity.ERROR

    def test_save_is_atomic_with_mode_0600(self, memory_keyring, tmp_path, monkeypatch):
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF)
        store = sm.secrets_file
        if os.name == "posix":
            assert store.stat().st_mode & 0o777 == 0o600
            os.chmod(store, 0o644)
            assert sm.set_secret("openai_api_key", SK)
            assert store.stat().st_mode & 0o777 == 0o600, "a rewrite makes a readable store private again"
        assert leftovers(store.parent) == []
        before = store.read_bytes()

        def failing_replace(src, dst):
            raise OSError(28, "No space left on device")

        with monkeypatch.context() as m:
            m.setattr(secrets_module.os, "replace", failing_replace)
            assert sm.set_secret("gemini_api_key", "AIzaSyA1234567890abcdefghijklmnopqrstuv") is False
        assert store.read_bytes() == before, "the old store is untouched when the new one cannot be put in place"
        assert leftovers(store.parent) == []

        def dump_half_then_fail(obj, f, **kwargs):
            f.write('{"hf_token": "gAAAA')
            raise OSError(5, "Input/output error")

        with monkeypatch.context() as m:
            m.setattr(secrets_module.json, "dump", dump_half_then_fail)
            assert sm.set_secret("gemini_api_key", "x") is False
        assert store.read_bytes() == before, "a reader never sees a half-written store"
        assert json.loads(store.read_text())  # still valid JSON
        assert leftovers(store.parent) == []

    def test_reload_before_write_keeps_a_secret_written_by_another_instance(self, memory_keyring, tmp_path):
        gui = secrets_for(tmp_path)
        mcp = secrets_for(tmp_path)  # loaded before the GUI writes

        assert gui.set_secret("hf_token", HF)
        assert mcp.set_secret("openai_api_key", SK)

        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("hf_token") == HF
        assert fresh.get_secret("openai_api_key") == SK

        assert gui.delete_secret("openai_api_key")  # the GUI never saw this secret in its own cache
        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("openai_api_key") is None
        assert fresh.get_secret("hf_token") == HF

    def test_reading_secrets_writes_nothing_into_the_working_directory(self, make_framework, tmp_path,
                                                                       monkeypatch):
        first = make_framework()
        assert first.secrets_manager.set_secret("openai_api_key", SK)
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)

        fm = make_framework()  # Ditto (provider OpenAI) reads the stored key while booting
        assert fm.secrets_manager.get_secret("openai_api_key") == SK

        assert not (project / "logs").exists()


class TestSecretsFollowUp:
    """Follow-up checks for the secrets store fixes: audit log, legacy takeover, links, threads, doctor."""

    def test_secrets_audit_log_is_private_and_in_the_per_user_folder(self, memory_keyring, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF) and sm.get_secret("hf_token") == HF

        log = tmp_path / "state" / "secrets-audit.log"
        assert sm.audit_log_file == log and "ACTION=READ KEY=hf_token" in log.read_text()
        assert HF not in log.read_text()
        if os.name == "posix":
            assert log.stat().st_mode & 0o777 == 0o600
        assert not (tmp_path / "logs").exists() and list(project.iterdir()) == []

    def test_secrets_audit_log_lines_end_with_one_line_break(self, memory_keyring, tmp_path):
        """os.open() gives a text-mode descriptor on Windows unless O_BINARY is set; with the text layer on
        top, every entry would end in \\r\\r\\n."""
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF) and sm.get_secret("hf_token") == HF

        raw = sm.audit_log_file.read_bytes()
        assert b"\r\r" not in raw
        assert raw.count(os.linesep.encode()) == 2 and raw.endswith(os.linesep.encode())

    def test_legacy_merge_keeps_per_user_values_and_runs_once(self, memory_keyring, tmp_path):
        gui_store = SecretsManager(tmp_path / "configs")
        assert gui_store.initialize()
        assert gui_store.set_secret("hf_token", "hf_oldTokenFromTheGuiStore0123456789")
        assert gui_store.set_secret("gemini_api_key", "AIzaSyA1234567890abcdefghijklmnopqrstuv")
        cli_store = SecretsManager(tmp_path / "state")
        assert cli_store.initialize() and cli_store.set_secret("hf_token", HF)

        sm = secrets_for(tmp_path)

        assert sm.get_secret("hf_token") == HF, "a secret already in the per-user store is never overwritten"
        assert sm.get_secret("gemini_api_key") == "AIzaSyA1234567890abcdefghijklmnopqrstuv"
        assert sm.delete_secret("gemini_api_key")
        assert secrets_for(tmp_path).get_secret("gemini_api_key") is None, "the takeover ran a second time"

    def test_moved_installation_does_not_bring_deleted_secrets_back(self, memory_keyring, tmp_path):
        """The old store is recognised by its content too: after the installation folder is moved (or seen
        under another mount point), its configs/secrets.store is not taken over a second time."""
        old_configs = tmp_path / "install-a" / "configs"
        old = SecretsManager(old_configs)
        assert old.initialize() and old.set_secret("hf_token", HF) and old.set_secret("openai_api_key", SK)
        sm = secrets_for(tmp_path, old_configs)
        assert sm.get_secret("hf_token") == HF
        assert sm.delete_secret("hf_token")  # leaked and revoked

        (tmp_path / "install-a").rename(tmp_path / "install-b")
        moved = secrets_for(tmp_path, tmp_path / "install-b" / "configs")

        assert moved.get_secret("hf_token") is None, "a secret deleted after the takeover came back"
        assert moved.get_secret("openai_api_key") == SK

        # Another old store with other content is still taken over once.
        gemini = "AIzaSyA1234567890abcdefghijklmnopqrstuv"
        other = SecretsManager(tmp_path / "other" / "configs")
        assert other.initialize() and other.set_secret("gemini_api_key", gemini)
        assert secrets_for(tmp_path, tmp_path / "other" / "configs").get_secret("gemini_api_key") == gemini

    @pytest.mark.parametrize("record", [b"garbage", b"[]", b'{"stores": "/opt/llm/configs/secrets.store"}'])
    def test_unreadable_takeover_record_never_takes_the_old_store_over_again(self, memory_keyring, tmp_path,
                                                                            record):
        old = SecretsManager(tmp_path / "configs")
        assert old.initialize() and old.set_secret("hf_token", HF)
        sm = secrets_for(tmp_path)
        assert sm.get_secret("hf_token") == HF
        assert sm.delete_secret("hf_token")
        marker = tmp_path / "state" / "secrets.store.migrated"
        assert marker.is_file()
        marker.write_bytes(record)  # damaged or edited by hand

        fresh = secrets_for(tmp_path)

        assert fresh.get_secret("hf_token") is None, "a secret deleted after the takeover came back"
        assert fresh.set_secret("openai_api_key", SK)
        assert secrets_for(tmp_path).list_secrets() == ["cred/openai/api_key"]  # the v2 name of the old key

    def test_damaged_legacy_store_is_taken_over_once_repaired(self, memory_keyring, tmp_path):
        legacy = tmp_path / "configs" / "secrets.store"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'{"hf_token": "gAAAAABk')

        sm = secrets_for(tmp_path)

        finding = check_secrets(SimpleNamespace(secrets_manager=sm))[0]
        assert finding.severity == Severity.ERROR and str(legacy) in finding.message
        assert sm.set_secret("openai_api_key", SK) is False, "writing now would make the old secrets unreachable"
        assert not sm.secrets_file.exists()

        # Repaired (e.g. restored from a backup) while the GUI keeps running.
        intact = SecretsManager(tmp_path / "backup")
        assert intact.initialize() and intact.set_secret("hf_token", HF)
        legacy.write_bytes((tmp_path / "backup" / "secrets.store").read_bytes())

        assert sm.get_secret("hf_token") == HF
        assert check_secrets(SimpleNamespace(secrets_manager=sm))[0].severity == Severity.OK
        assert sm.set_secret("openai_api_key", SK)
        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("hf_token") == HF and fresh.get_secret("openai_api_key") == SK

    @pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
    def test_dangling_store_link_is_not_replaced_by_a_new_store(self, memory_keyring, tmp_path):
        """secrets.store linked by a dotfile manager onto a drive that is not mounted: writing would replace
        the link with a store holding only the new secret, and the real one would never be used again."""
        target = tmp_path / "unmounted-drive" / "secrets.store"
        link = tmp_path / "state" / "secrets.store"
        link.parent.mkdir(parents=True)
        os.symlink(target, link)

        sm = secrets_for(tmp_path)

        assert sm.set_secret("openai_api_key", SK) is False
        assert link.is_symlink() and not target.exists()
        finding = check_secrets(SimpleNamespace(secrets_manager=sm))[0]
        assert finding.severity == Severity.ERROR and str(link) in finding.message

        # The drive is mounted again.
        intact = SecretsManager(tmp_path / "backup")
        assert intact.initialize() and intact.set_secret("hf_token", HF)
        target.parent.mkdir()
        target.write_bytes((tmp_path / "backup" / "secrets.store").read_bytes())
        assert sm.get_secret("hf_token") == HF
        assert sm.set_secret("openai_api_key", SK)
        assert link.is_symlink(), "a write goes through the link instead of replacing it"
        assert leftovers(link.parent) == [] and leftovers(target.parent) == []
        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("hf_token") == HF and fresh.get_secret("openai_api_key") == SK

    def test_concurrent_writes_in_one_process_are_not_lost(self, memory_keyring, tmp_path, monkeypatch):
        """GUI workers and MCP tool calls share one SecretsManager; a write must not drop another thread's."""
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF)
        real_dump = secrets_module.json.dump
        inside, release = threading.Event(), threading.Event()

        def slow_first_dump(obj, f, **kwargs):
            if threading.current_thread().name == "first" and not inside.is_set():
                inside.set()
                release.wait(10)
            return real_dump(obj, f, **kwargs)

        monkeypatch.setattr(secrets_module.json, "dump", slow_first_dump)
        results = {}
        first = threading.Thread(name="first", target=lambda: results.update(a=sm.set_secret("openai_api_key", SK)))
        second = threading.Thread(name="second", target=lambda: results.update(b=sm.set_secret("gemini_api_key", "g")))
        first.start()
        assert inside.wait(10)
        second.start()
        second.join(0.5)
        release.set()
        first.join(10)
        second.join(10)

        assert results == {"a": True, "b": True}
        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("openai_api_key") == SK and fresh.get_secret("gemini_api_key") == "g"
        assert fresh.get_secret("hf_token") == HF

    def test_doctor_in_a_long_running_process_reports_the_current_store(self, memory_keyring, tmp_path):
        gui = secrets_for(tmp_path)
        assert gui.set_secret("hf_token", HF)
        good = gui.secrets_file.read_bytes()
        assert check_secrets(SimpleNamespace(secrets_manager=gui))[0].severity == Severity.OK

        gui.secrets_file.write_bytes(b'{"hf_token": "gAAAA')  # damaged by another (older) process
        assert check_secrets(SimpleNamespace(secrets_manager=gui))[0].severity == Severity.ERROR
        assert gui.get_secret("hf_token") == HF, "the last good copy keeps serving reads"

        gui.secrets_file.write_bytes(good)  # repaired
        assert check_secrets(SimpleNamespace(secrets_manager=gui))[0].severity == Severity.OK
        assert gui.list_secrets() == ["hf_token"]


# ======================================================================================================
# Audit key
# ======================================================================================================

def keyring_key(memory_keyring):
    return memory_keyring.store.get((SecretsManager.SERVICE_ID, AUDIT_KEY_ID))


class TestAuditKey:

    def test_key_has_its_own_keyring_entry_not_the_secrets_store(self, make_framework, memory_keyring):
        fm = make_framework()
        key = keyring_key(memory_keyring)
        assert key and len(key) >= 32
        assert fm.tool_registry.audit.keyed and fm.tool_registry.audit._key == key.encode()
        assert "audit_hmac_key" not in fm.secrets_manager.list_secrets()
        store = fm.secrets_manager.secrets_file
        assert not store.exists() or "audit_hmac_key" not in store.read_text()

    def test_key_is_reused_across_boots_and_working_directories(self, make_framework, memory_keyring, tmp_path,
                                                                monkeypatch):
        first = make_framework()
        first.tool_registry.call("list_targets", {}, ToolContext("gui"))
        key = keyring_key(memory_keyring)

        other = tmp_path / "some" / "project"
        other.mkdir(parents=True)
        monkeypatch.chdir(other)
        second = make_framework()
        second.tool_registry.call("list_targets", {}, ToolContext("mcp"))

        assert keyring_key(memory_keyring) == key
        assert second.tool_registry.audit._key == key.encode()
        result = second.tool_registry.audit.check()
        assert result.intact and result.keyed and result.entries == 2, result.message

    def test_legacy_key_from_the_secrets_store_is_taken_over(self, make_framework, memory_keyring, tmp_path):
        # 3.0.0 development builds kept the key in the secrets store; their chain must stay verifiable.
        legacy_log = AuditLog(tmp_path / "state" / "audit.db", key=b"legacy-audit-key-0123456789abcdef")
        legacy_log.record("mcp", "start_build", "local_write", "ask", "ok", approved=True)
        sm = secrets_for(tmp_path)
        assert sm.set_secret("audit_hmac_key", "legacy-audit-key-0123456789abcdef")

        fm = make_framework()

        assert keyring_key(memory_keyring) == "legacy-audit-key-0123456789abcdef"
        fm.tool_registry.call("list_targets", {}, ToolContext("cli"))
        result = fm.tool_registry.audit.check()
        assert result.intact and result.entries == 2, result.message

    def test_without_a_keyring_the_chain_is_unkeyed_and_doctor_warns(self, make_framework, memory_keyring,
                                                                     monkeypatch):
        real_set = memory_keyring.set_password

        def null_for_audit_key(service, username, password):  # like keyring.backends.null for this entry
            if username != AUDIT_KEY_ID:
                real_set(service, username, password)

        monkeypatch.setattr(memory_keyring, "set_password", null_for_audit_key)
        fm = make_framework()

        assert fm.tool_registry.audit.keyed is False
        fm.tool_registry.call("list_targets", {}, ToolContext("cli"))
        result = fm.tool_registry.audit.check()
        assert result.intact and not result.keyed and "unkeyed" in result.message
        finding = next(f for f in Doctor(fm, [check_tool_policy]).run().findings if f.code == "tools.audit")
        assert finding.severity == Severity.WARN and "unkeyed" in finding.message

    def test_a_failing_keyring_gives_no_key_instead_of_an_error(self, memory_keyring, monkeypatch):
        def broken(service, username):
            raise RuntimeError("keyring locked")

        monkeypatch.setattr(memory_keyring, "get_password", broken)
        assert framework_tools._audit_key(SimpleNamespace(secrets_manager=None)) is None

    def test_two_first_starts_end_up_with_the_same_key(self, memory_keyring, monkeypatch):
        """Two processes start for the first time together (e.g. the GUI and an MCP server). The scripted
        keyring lets both read 'no key yet'; the second store lands after the first process re-read its key.
        An implementation that serializes the get-or-create (a lock file, BEGIN IMMEDIATE on audit.db) passes:
        the barrier then times out and the second process finds the first one's key."""
        real_get, real_set = memory_keyring.get_password, memory_keyring.set_password
        both_read = threading.Barrier(2, timeout=2)
        first_reread = threading.Event()
        guard = threading.Lock()
        state = {"first_setter": None}

        def get_password(service, username):
            value = real_get(service, username)
            if username == AUDIT_KEY_ID:
                if value is None:
                    try:
                        both_read.wait()
                    except threading.BrokenBarrierError:
                        pass
                elif state["first_setter"] == threading.get_ident():
                    first_reread.set()
            return value

        def set_password(service, username, password):
            if username == AUDIT_KEY_ID:
                with guard:
                    first = state["first_setter"] is None
                    if first:
                        state["first_setter"] = threading.get_ident()
                if not first:
                    first_reread.wait(2)
            real_set(service, username, password)

        monkeypatch.setattr(memory_keyring, "get_password", get_password)
        monkeypatch.setattr(memory_keyring, "set_password", set_password)
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            framework_tools._audit_key(SimpleNamespace(secrets_manager=None)))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert len(results) == 2 and None not in results
        assert results[0] == results[1] == keyring_key(memory_keyring).encode()


# ======================================================================================================
# Audit log
# ======================================================================================================

WRITER = textwrap.dedent("""
    import sys
    from pathlib import Path
    from orchestrator.tools.audit import AuditLog
    path, key, count, name = sys.argv[1:]
    log = AuditLog(Path(path), key=key.encode())
    sys.stdin.read(1)  # start together
    for i in range(int(count)):
        log.record(name, "start_build", "local_write", "ask", "ok", arguments={"writer": name, "i": i},
                   approved=True)
""")


def filled_log(path: Path, n=3, key=b"audit-key") -> AuditLog:
    log = AuditLog(path, key=key)
    for i in range(n):
        log.record("mcp", "start_build", "local_write", "ask", "ok", arguments={"i": i}, approved=True)
    return log


def run_sql(path: Path, statement: str):
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute(statement)
    finally:
        db.close()


class TestAuditLog:

    def test_three_processes_keep_one_valid_chain(self, tmp_path):
        script = tmp_path / "writer.py"
        script.write_text(WRITER)
        db = tmp_path / "state" / "audit.db"
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        procs = [subprocess.Popen([sys.executable, str(script), str(db), "shared-key", "100", f"w{n}"],
                                  stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
                 for n in range(3)]
        for p in procs:
            p.stdin.write("g")
            p.stdin.flush()
        for p in procs:
            p.stdin.close()
        for p in procs:
            assert p.wait(timeout=120) == 0, p.stderr.read()
            p.stderr.close()

        log = AuditLog(db, key=b"shared-key")
        result = log.check()
        assert result.intact and result.entries == 300, result.message

        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT id, actor, prev_hash, hash FROM events ORDER BY id").fetchall()
        finally:
            con.close()
        assert [r[0] for r in rows] == list(range(1, 301))
        prev = GENESIS
        for _id, _actor, prev_hash, digest in rows:
            assert prev_hash == prev, f"chain forked at entry {_id}"
            prev = digest
        assert len({r[3] for r in rows}) == 300
        assert sorted({r[1] for r in rows}) == ["w0", "w1", "w2"]
        assert all(sum(1 for r in rows if r[1] == w) == 100 for w in ("w0", "w1", "w2"))
        anchor = json.loads(log.anchor_path.read_text())
        assert anchor["id"] == 300 and anchor["hash"] == rows[-1][3]

    def test_a_writer_right_after_the_first_entry_sees_its_anchor(self, tmp_path):
        """The race behind the flaky test above: another process appends as soon as the first entry of a new
        log is committed. The anchor must already exist then, or that writer records 'anchor_missing'."""
        db = tmp_path / "state" / "audit.db"
        first, second = AuditLog(db, key=b"shared-key"), AuditLog(db, key=b"shared-key")
        interleaved = []

        class CommitHook:
            """first's connection; the second writer runs right after the first COMMIT, before anything else."""
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                cursor = self._conn.execute(sql, *args)
                if sql == "COMMIT" and not interleaved:
                    interleaved.append(True)
                    second.record("w1", "start_build", "local_write", "ask", "ok")
                return cursor

            def __getattr__(self, name):
                return getattr(self._conn, name)

        real_connect = first._connect
        first._connect = lambda: CommitHook(real_connect())
        first.record("w0", "start_build", "local_write", "ask", "ok")

        assert interleaved, "the second writer did not run"
        result = AuditLog(db, key=b"shared-key").check()
        assert result.intact and result.entries == 2, result.message
        assert json.loads(first.anchor_path.read_text())["id"] == 2

    def test_first_anchor_is_removed_when_the_first_entry_is_rolled_back(self, tmp_path, monkeypatch):
        db = tmp_path / "state" / "audit.db"
        log = AuditLog(db, key=b"k")
        real_connect = log._connect

        class FailingCommit:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("disk I/O error")
                return self._conn.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        log._connect = lambda: FailingCommit(real_connect())
        with pytest.raises(sqlite3.OperationalError):
            log.record("cli", "t", "read", "allow", "ok")
        assert not log.anchor_path.exists(), "an anchor naming an entry that was never committed"
        log._connect = real_connect
        log.record("cli", "t", "read", "allow", "ok")
        assert log.check().intact and json.loads(log.anchor_path.read_text())["id"] == 1

    def test_opening_is_lazy(self, tmp_path):
        log = AuditLog(tmp_path / "not-yet" / "audit.db", key=b"k")
        assert not (tmp_path / "not-yet").exists()
        log.record("cli", "t", "read", "allow", "ok")
        assert (tmp_path / "not-yet" / "audit.db").is_file()
        if os.name == "posix":
            assert log.anchor_path.stat().st_mode & 0o777 == 0o600

    def test_intact_log_and_empty_log(self, tmp_path):
        assert AuditLog(tmp_path / "empty.db", key=b"k").check().intact
        log = filled_log(tmp_path / "a.db")
        result = log.check()
        assert result.intact and result.entries == 3 and result.keyed and log.verify() is None

    def test_deleting_the_newest_entry_is_reported_as_removed(self, tmp_path):
        log = filled_log(tmp_path / "a.db")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")

        result = log.check()

        assert not result.intact and result.problem == "removed" and result.entry == 3
        assert result.entries == 2
        assert log.verify() == 3
        assert "removed" in result.message

    def test_deleting_every_entry_is_reported_as_removed(self, tmp_path):
        log = filled_log(tmp_path / "a.db")
        run_sql(log.path, "DELETE FROM events")

        result = log.check()

        assert not result.intact and result.problem == "removed" and result.entry == 3 and result.entries == 0
        assert log.verify() == 3

    def test_deleting_the_anchor_is_reported(self, tmp_path):
        log = filled_log(tmp_path / "a.db")
        log.anchor_path.unlink()

        result = log.check()

        assert not result.intact and result.problem == "anchor_missing" and result.entries == 3
        assert log.verify() == 0
        assert "anchor" in result.message

    @pytest.mark.parametrize("content", ["", "not json", "[]", '{"id": "x", "hash": 1}', '{"hash": "h"}'])
    def test_unreadable_anchor_counts_as_missing(self, tmp_path, content):
        log = filled_log(tmp_path / "a.db")
        log.anchor_path.write_text(content)
        assert log.check().problem == "anchor_missing"

    def test_forged_anchor_is_reported_as_modified(self, tmp_path):
        log = filled_log(tmp_path / "a.db")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")
        con = sqlite3.connect(log.path)
        head = con.execute("SELECT hash FROM events WHERE id = 2").fetchone()[0]
        con.close()
        # Pointing the anchor back at entry 2 needs the key for its MAC; a plain hash does not do.
        mac = hashlib.sha256(f"anchor:2:{head}".encode()).hexdigest()
        log.anchor_path.write_text(json.dumps({"id": 2, "hash": head, "mac": mac}))

        result = log.check()

        assert not result.intact and result.problem == "modified"

    def test_changed_entry_is_reported_as_modified(self, tmp_path):
        log = filled_log(tmp_path / "a.db")
        run_sql(log.path, "UPDATE events SET approved = 0 WHERE id = 2")
        result = log.check()
        assert result.problem == "modified" and result.entry == 2

    def test_another_key_is_a_key_mismatch_not_tampering(self, tmp_path):
        filled_log(tmp_path / "a.db", key=b"key-of-this-user")

        other = AuditLog(tmp_path / "a.db", key=b"key-after-keyring-reset").check()
        unkeyed = AuditLog(tmp_path / "a.db").check()

        assert other.problem == "key_mismatch" and not other.intact
        assert unkeyed.problem == "key_mismatch"
        assert "different key" in other.message

    @pytest.mark.parametrize("anchor_too", [False, True], ids=["newest-row", "newest-row-and-anchor"])
    def test_removed_entries_stay_detectable_after_the_next_call(self, tmp_path, anchor_too):
        log = filled_log(tmp_path / "a.db")
        run_sql(log.path, "DELETE FROM events WHERE id = 3")  # hide the last start_build
        if anchor_too:
            log.anchor_path.unlink()
        assert not log.check().intact

        log.record("mcp", "list_targets", "read", "allow", "ok")  # the next normal call

        assert not log.check().intact

    @pytest.mark.parametrize("writer_key", [None, b"another-key"], ids=["no-key", "other-key"])
    def test_a_writer_with_another_key_does_not_cause_a_tamper_alarm(self, tmp_path, writer_key):
        log = filled_log(tmp_path / "a.db", key=b"key-of-this-user")
        # e.g. a process whose keyring lookup failed transiently (_audit_key returned None); refusing is fine
        with contextlib.suppress(Exception):
            AuditLog(log.path, key=writer_key).record("cli", "list_targets", "read", "allow", "ok")

        assert log.check().problem != "modified"

    def test_corrupt_database_raises_on_use_only(self, tmp_path):
        db = tmp_path / "audit.db"
        db.write_bytes(b"this is not a database" * 40)
        log = AuditLog(db, key=b"k")  # no error yet
        with pytest.raises(sqlite3.DatabaseError):
            log.check()
        with pytest.raises(sqlite3.DatabaseError):
            log.record("cli", "t", "read", "allow", "ok")
        assert db.read_bytes() == b"this is not a database" * 40


class TestCorruptAuditDatabase:

    @pytest.fixture
    def fm(self, make_framework, tmp_path):
        db = tmp_path / "state" / "audit.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"garbage, not SQLite" * 64)
        return make_framework()  # asserts initialize()

    def test_boot_and_tool_calls_still_work(self, fm, tmp_path):
        registry = fm.tool_registry
        assert registry is not None and registry.audit is not None
        result = registry.call("get_target_capabilities", {"target": "Rockchip"}, ToolContext("cli"))
        assert "gguf" in result.formats
        assert (tmp_path / "state" / "audit.db").read_bytes() == b"garbage, not SQLite" * 64

    def test_doctor_reports_it(self, fm):
        finding = next(f for f in Doctor(fm, [check_tool_policy]).run().findings if f.code == "tools.audit")
        assert finding.severity == Severity.ERROR and "cannot be opened" in finding.message

    @pytest.mark.parametrize("args", [["tools", "audit", "--verify"], ["tools", "audit"]])
    def test_cli_reports_it_cleanly(self, fm, args):
        result = invoke_cli(fm, *args)
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Traceback" not in result.output
        text = flat(result.output)
        assert "cannot be read" in text and "not a database" in text

    def test_a_failing_registry_does_not_stop_the_framework(self, make_framework, monkeypatch):
        from orchestrator.Core import framework as framework_module

        def broken(_framework):
            raise PermissionError(13, "Permission denied", "/home/user/.llm-framework")

        monkeypatch.setattr(framework_module, "build_tool_registry", broken)
        fm = make_framework()  # asserts initialize()
        assert fm.tool_registry is None and fm.orchestrator is not None and fm.build_service is not None
        finding = Doctor(fm, [check_tool_policy]).run().findings[0]
        assert finding.code == "tools.registry" and finding.severity == Severity.WARN
        result = invoke_cli(fm, "tools", "list")
        assert result.exit_code == 1 and "Tool registry not available" in result.output


class TestCliVerify:

    @pytest.fixture
    def fm(self, make_framework):
        fm = make_framework()
        for _ in range(3):
            fm.tool_registry.call("list_targets", {}, ToolContext("mcp"))
        return fm

    def test_intact(self, fm):
        result = invoke_cli(fm, "tools", "audit", "--verify")
        assert result.exit_code == 0 and "Audit log intact, 3 entries" in flat(result.output)

    def test_removed_newest_entry(self, fm):
        run_sql(fm.tool_registry.audit.path, "DELETE FROM events WHERE id = 3")
        result = invoke_cli(fm, "tools", "audit", "--verify")
        assert result.exit_code == 1 and "removed" in flat(result.output)

    def test_missing_anchor(self, fm):
        fm.tool_registry.audit.anchor_path.unlink()
        result = invoke_cli(fm, "tools", "audit", "--verify")
        assert result.exit_code == 1 and "anchor file is missing" in flat(result.output)


class TestDoctorAuditFindings:

    @staticmethod
    def audit_finding(registry):
        return next(f for f in Doctor(SimpleNamespace(tool_registry=registry), [check_tool_policy]).run().findings
                    if f.code == "tools.audit")

    @pytest.fixture
    def registry(self, tmp_path):
        return ToolRegistry(PolicyStore(tmp_path), filled_log(tmp_path / "audit.db", key=b"key"))

    def test_intact_keyed_log_is_ok(self, registry):
        assert self.audit_finding(registry).severity == Severity.OK

    @pytest.mark.parametrize("damage, severity, words", [
        ("DELETE FROM events WHERE id = 3", Severity.ERROR, "removed"),
        ("UPDATE events SET outcome = 'denied' WHERE id = 1", Severity.ERROR, "modified"),
    ])
    def test_tampering_is_an_error(self, registry, damage, severity, words):
        run_sql(registry.audit.path, damage)
        finding = self.audit_finding(registry)
        assert finding.severity == severity and words in finding.message

    def test_missing_anchor_is_a_warning_and_other_key_an_error(self, registry, tmp_path):
        registry.audit.anchor_path.unlink()
        assert self.audit_finding(registry).severity == Severity.WARN
        # A log this key cannot verify may also have been rewritten with a key of the attacker's choice.
        other = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db", key=b"other"))
        finding = self.audit_finding(other)
        assert finding.severity == Severity.ERROR and "different key" in finding.message

    def test_unkeyed_log_is_a_warning(self, tmp_path):
        registry = ToolRegistry(PolicyStore(tmp_path), filled_log(tmp_path / "plain.db", key=None))
        finding = self.audit_finding(registry)
        assert finding.severity == Severity.WARN and "unkeyed" in finding.message


# ======================================================================================================
# Tool policy
# ======================================================================================================

STRICT = {"version": 1, "configured": True, "tiers": {"read": "deny", "local_write": "deny", "external": "deny"},
          "overrides": {"read_build_log": "deny"}}


def assert_denies_everything(policy: ToolPolicy):
    assert policy.configured is False
    for tier in ToolTier:
        assert policy.decide("list_targets", tier) == Decision.DENY, tier
    assert policy.decide("read_build_log", ToolTier.READ) == Decision.DENY


class TestPolicyStore:

    @pytest.mark.parametrize("content", [
        b"{not json",
        json.dumps(dict(STRICT, future_field=True)).encode(),           # a newer version's field
        json.dumps(dict(STRICT, tiers={"read": "sometimes"})).encode(),  # invalid value
        json.dumps({**STRICT, "version": 2}).encode(),
        b"[]", b"null", b"\xff\xfe\x00garbage",
    ], ids=["syntax", "unknown-key", "bad-value", "version", "list", "null", "not-utf8"])
    def test_damaged_file_denies_every_tier_and_is_kept(self, tmp_path, content):
        store = PolicyStore(tmp_path)
        store.path.write_bytes(content)

        policy = store.load()

        assert_denies_everything(policy)
        assert store.damaged and str(store.path) in store.problem and "denied" in store.problem
        assert "tool_policy.json.bad" in store.problem
        assert (tmp_path / "tool_policy.json.bad").read_bytes() == content
        assert store.path.read_bytes() == content, "the damaged file itself is not replaced"

    def test_a_stricter_stored_choice_never_becomes_the_looser_defaults(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.path.write_text(json.dumps(dict(STRICT, added_by_newer_version={"x": 1})))
        policy = store.load()
        assert policy.decide("list_targets", ToolTier.READ) == Decision.DENY
        assert policy.decide("start_build", ToolTier.LOCAL_WRITE) == Decision.DENY

    def test_bad_copy_is_refreshed_only_when_the_content_changes(self, tmp_path):
        store = PolicyStore(tmp_path)
        bad = tmp_path / "tool_policy.json.bad"
        store.path.write_text("{first")
        store.load()
        first_inode = bad.stat().st_ino
        store.load()
        assert bad.stat().st_ino == first_inode, "unchanged content is not copied again"
        store.path.write_text("{second")
        store.load()
        assert bad.read_text() == "{second"

    def test_missing_file_gives_the_recommended_defaults(self, tmp_path):
        store = PolicyStore(tmp_path)
        policy = store.load()
        assert store.problem is None and not store.damaged
        assert policy.decide("x", ToolTier.READ) == Decision.ALLOW
        assert policy.decide("x", ToolTier.LOCAL_WRITE) == Decision.ASK
        assert policy.decide("x", ToolTier.EXTERNAL) == Decision.ASK
        assert not (tmp_path / "tool_policy.json.bad").exists()

    def test_unreadable_file_denies_everything(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.path.mkdir()  # reading it raises IsADirectoryError (an OSError)

        policy = store.load()

        assert_denies_everything(policy)
        assert store.damaged and "cannot be read" in store.problem
        assert not (tmp_path / "tool_policy.json.bad").exists()

    def test_transient_oserror_denies_and_recovers(self, tmp_path, monkeypatch):
        store = PolicyStore(tmp_path)
        store.save(ToolPolicy(configured=True, tiers={ToolTier.READ: Decision.ALLOW,
                                                      ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        real_read_text = Path.read_text

        def emfile(self, *args, **kwargs):
            if self == store.path:
                raise OSError(24, "Too many open files")
            return real_read_text(self, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(Path, "read_text", emfile)
            assert_denies_everything(store.load())
            assert "Too many open files" in store.problem

        policy = store.load()
        assert store.problem is None
        assert policy.configured and policy.decide("start_build", ToolTier.LOCAL_WRITE) == Decision.ALLOW

    def test_saving_again_clears_the_problem(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.path.write_text("{broken")
        store.load()
        assert store.damaged
        store.save(ToolPolicy(configured=True))
        assert store.load().configured and store.problem is None
        assert (tmp_path / "tool_policy.json.bad").read_text() == "{broken"

    def test_registry_denies_read_tools_with_a_damaged_policy(self, tmp_path):
        class In(BaseModel):
            text: str

        calls = []
        registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db", key=b"k"))
        registry.register(ToolSpec("echo", "Echo", "", ToolTier.READ, In, In, lambda i: calls.append(i) or i))
        registry.policy_store.path.write_text("{broken")

        with pytest.raises(ToolDenied):
            registry.call("echo", {"text": "hi"}, ToolContext("mcp", confirm=lambda s, i: True))

        assert calls == []
        entry = registry.audit.entries(1)[0]
        assert (entry["decision"], entry["outcome"], entry["approved"]) == ("deny", "denied", None)

    def test_doctor_and_tools_list_show_the_problem(self, make_framework):
        fm = make_framework()
        store = fm.tool_registry.policy_store
        store.path.write_text(json.dumps(dict(STRICT, unknown_setting=1)))

        finding = next(f for f in Doctor(fm, [check_tool_policy]).run().findings if f.code == "tools.permissions")
        assert finding.severity == Severity.ERROR and "damaged" in finding.message

        result = invoke_cli(fm, "tools", "list")
        assert result.exit_code == 0, result.output
        assert "damaged" in flat(result.stderr)
        permissions = re.findall(r"[│|]\s*(allow|ask|deny)\s*[│|]", result.stdout)
        assert len(permissions) == len(fm.tool_registry.list())
        assert set(permissions) == {"deny"}


# ======================================================================================================
# Tool registry: internal errors, redaction, approvals in the audit log
# ======================================================================================================

class In(BaseModel):
    text: str


class Out(BaseModel):
    text: str


class RecordingLogger:
    def __init__(self):
        self.records = []

    def _add(self, level, msg, *args, **kwargs):
        self.records.append((level, str(msg), kwargs))

    def error(self, msg, *args, **kwargs):
        self._add("error", msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._add("warning", msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._add("info", msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._add("debug", msg, *args, **kwargs)


def make_registry(tmp_path, handler, tier=ToolTier.READ):
    registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db", key=b"k"))
    registry.logger = RecordingLogger()
    registry.register(ToolSpec("probe", "Probe", "", tier, In, Out, handler))
    return registry


def last_entry(registry):
    return registry.audit.entries(1)[0]


class TestRegistryErrors:

    def test_invalid_handler_output_is_a_generic_internal_error(self, tmp_path):
        registry = make_registry(tmp_path, lambda i: {"text": {"nested": "not a string"}})

        with pytest.raises(ToolError) as exc:
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))

        message = str(exc.value)
        assert message == "Probe failed (internal error; details are in the framework log)."
        assert "validation error" not in message.lower() and "pydantic" not in message
        errors = [r for r in registry.logger.records if r[0] == "error"]
        assert errors and errors[0][2].get("exc_info") is True and "probe" in errors[0][1]
        entry = last_entry(registry)
        assert entry["outcome"] == "error" and "validation error for Out" in entry["detail"]

    def test_model_built_inside_the_handler_does_not_leak_its_input(self, tmp_path):
        def handler(_inp):
            return Out.model_validate({"text": {"token": HF}})  # e.g. a manifest field of the wrong type

        registry = make_registry(tmp_path, handler)

        with pytest.raises(ToolError) as exc:
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))

        assert HF not in str(exc.value) and "input_value" not in str(exc.value)
        assert "internal error" in str(exc.value)
        assert HF not in last_entry(registry)["detail"]
        assert any(r[0] == "error" for r in registry.logger.records)

    def test_user_facing_value_errors_are_redacted(self, tmp_path):
        def handler(_inp):
            raise ValueError(f"Model https://user:{HF}@huggingface.co/x is not reachable")

        registry = make_registry(tmp_path, handler)

        with pytest.raises(ToolError) as exc:
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))

        assert HF not in str(exc.value) and "REDACTED" in str(exc.value)
        assert "not reachable" in str(exc.value)
        assert HF not in last_entry(registry)["detail"]

    def test_unexpected_errors_are_logged_and_redacted(self, tmp_path):
        def handler(_inp):
            raise RuntimeError(f"upstream rejected key {SK}")

        registry = make_registry(tmp_path, handler)

        with pytest.raises(ToolError) as exc:
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))

        assert str(exc.value).startswith("Probe failed: ")
        assert SK not in str(exc.value) and "REDACTED" in str(exc.value)
        assert any(r[0] == "error" and r[2].get("exc_info") for r in registry.logger.records)
        assert SK not in last_entry(registry)["detail"]

    def test_wrong_handler_return_type_is_an_error(self, tmp_path):
        registry = make_registry(tmp_path, lambda i: "plain string")
        with pytest.raises(ToolError, match="Probe failed"):
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))
        assert last_entry(registry)["outcome"] == "error"

    def test_tool_errors_from_handlers_are_redacted_too(self, tmp_path):
        def handler(_inp):
            raise ToolError(f"Download refused for token {HF}")

        registry = make_registry(tmp_path, handler)

        with pytest.raises(ToolError) as exc:
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))

        assert HF not in str(exc.value)

    def test_redacted_tool_errors_keep_their_kind(self, tmp_path):
        def handler(_inp):
            raise ToolDenied(f"Upload refused for key {SK}")

        registry = make_registry(tmp_path, handler)

        with pytest.raises(ToolDenied) as exc:
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))

        assert SK not in str(exc.value) and "Upload refused" in str(exc.value)
        assert exc.value.__cause__ is None and exc.value.__suppress_context__
        entry = last_entry(registry)
        assert entry["outcome"] == "error" and SK not in entry["detail"]

    def test_audit_failure_never_breaks_the_call(self, tmp_path):
        registry = make_registry(tmp_path, lambda i: Out(text=i.text))
        (tmp_path / "audit.db").write_bytes(b"not a database" * 50)

        assert registry.call("probe", {"text": "x"}, ToolContext("mcp")).text == "x"
        assert any(r[0] == "warning" and "Audit write failed" in r[1] for r in registry.logger.records)


class TestApprovalAuditing:

    @pytest.fixture
    def registry(self, tmp_path):
        return make_registry(tmp_path, lambda i: Out(text="done"), tier=ToolTier.LOCAL_WRITE)

    @staticmethod
    def approved_column(registry):
        con = sqlite3.connect(registry.audit.path)
        try:
            return con.execute("SELECT approved, approved IS NULL FROM events ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()

    def test_ask_without_anyone_asked_is_audited_as_null(self, registry):
        with pytest.raises(ToolDenied):
            registry.call("probe", {"text": "x"}, ToolContext("mcp"))
        entry = last_entry(registry)
        assert (entry["decision"], entry["outcome"]) == ("ask", "not_confirmed")
        assert self.approved_column(registry) == (None, 1)

    def test_a_persons_no_is_audited_as_false(self, registry):
        with pytest.raises(ToolDenied):
            registry.call("probe", {"text": "x"}, ToolContext("ditto", confirm=lambda s, i: False))
        assert self.approved_column(registry) == (0, 0)
        with pytest.raises(ToolDenied):
            registry.call("probe", {"text": "x"}, ToolContext("mcp"), approved=False)
        assert self.approved_column(registry) == (0, 0)

    def test_a_persons_yes_is_audited_as_true(self, registry):
        assert registry.call("probe", {"text": "x"}, ToolContext("mcp"), approved=True).text == "done"
        entry = last_entry(registry)
        assert (entry["outcome"], entry["approved"]) == ("ok", 1)

    def test_allow_and_deny_are_not_recorded_as_approvals(self, registry):
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"probe": Decision.ALLOW}))
        registry.call("probe", {"text": "x"}, ToolContext("mcp"))
        assert (last_entry(registry)["decision"], self.approved_column(registry)) == ("allow", (None, 1))

        registry.policy_store.save(ToolPolicy(configured=True, overrides={"probe": Decision.DENY}))
        with pytest.raises(ToolDenied):
            registry.call("probe", {"text": "x"}, ToolContext("mcp"), approved=True)
        entry = last_entry(registry)
        assert (entry["decision"], entry["outcome"]) == ("deny", "denied")
        assert self.approved_column(registry) == (None, 1)


# ======================================================================================================
# Adversarial review of the fixes: edge cases, races and other callers
# ======================================================================================================

class TestAdversarialConfigAndLaunch:

    def test_empty_config_dir_variable_falls_back_to_the_installation_not_the_cwd(self, project, install_configs,
                                                                                   monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", "")
        before = snapshot(project)
        cfg = ConfigManager()
        assert cfg.config_dir == install_configs
        assert cfg.get("source_repositories") == {}
        assert snapshot(project) == before

    def test_unicode_config_dir_works(self, project, install_configs, tmp_path, monkeypatch):
        override = tmp_path / "Konfigüration 設定 ✓"
        override.mkdir()
        (override / "framework.yml").write_text("docker_registry: unicode.example\n", encoding="utf-8")
        monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", str(override))
        cfg = ConfigManager()
        assert cfg.config_dir == override and cfg.get("docker_registry") == "unicode.example"

    def test_path_variables_never_become_config_values(self, project, install_configs, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        cfg = ConfigManager()
        assert "config_dir" not in cfg.config_values and "home" not in cfg.config_values

    def test_mcp_launch_command_does_not_import_code_from_the_working_directory(self, tmp_path):
        from orchestrator import mcp_server
        project = tmp_path / "cloned-repo"
        planted = project / "orchestrator"
        planted.mkdir(parents=True)
        marker = project / "PLANTED_CODE_RAN"
        (planted / "__init__.py").write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(marker)!r}).write_text('imported from the working directory')\n"
            "sys.exit(0)\n")
        launch = mcp_server.launch_command(REPO_ROOT)
        args = list(launch["args"])
        if args[-2:] == ["mcp", "serve"]:
            args = args[:-2]  # same interpreter flags and module, but only print the version
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "FRAMEWORK_", "PYTHONSAFEPATH"))}
        env.update(launch.get("env") or {})
        env.update(PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring", LLM_SECRET_STORAGE="ephemeral",
                   LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_CONFIG_DIR=str(tmp_path / "cfg"))

        # Claude Code cannot set a working directory: the server starts in the project (client_snippets).
        result = subprocess.run([launch["command"], *args, "--version"], cwd=project, env=env,
                                capture_output=True, text=True, timeout=180, check=False)

        assert not marker.exists(), "code from the project folder ran inside the MCP server"
        assert result.returncode == 0 and "version" in result.stdout, result.stderr


class TestAdversarialSecrets:

    @pytest.mark.parametrize("change", ["set", "deleted"])
    def test_a_long_running_process_sees_secret_changes_of_other_processes(self, memory_keyring, tmp_path,
                                                                           change):
        cli = secrets_for(tmp_path)
        if change == "deleted":
            assert cli.set_secret("hf_token", HF)
        mcp = secrets_for(tmp_path)  # e.g. the MCP server Claude Desktop keeps running for days

        if change == "set":
            assert cli.set_secret("hf_token", HF)  # llm-cli secrets set hf_token
            assert mcp.get_secret("hf_token") == HF  # the next gated build started over MCP
        else:
            assert cli.delete_secret("hf_token")  # the token leaked and was revoked
            assert mcp.get_secret("hf_token") is None

    def test_removing_the_store_file_does_not_bring_its_secrets_back(self, memory_keyring, tmp_path):
        gui = secrets_for(tmp_path)
        assert gui.set_secret("hf_token", HF)
        gui.secrets_file.rename(gui.secrets_file.with_name("secrets.store.moved-aside"))

        assert gui.set_secret("openai_api_key", SK)  # "set the secrets again"

        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("openai_api_key") == SK
        assert fresh.get_secret("hf_token") is None, "a secret from the removed store came back"

    def test_legacy_store_is_taken_over_even_if_a_per_user_store_exists(self, memory_keyring, tmp_path):
        gui_store = SecretsManager(tmp_path / "configs")  # pre-3.0 GUI, started from the installation
        assert gui_store.initialize() and gui_store.set_secret("hf_token", HF)
        cli_store = SecretsManager(tmp_path / "state")  # pre-3.0 llm-cli from $HOME: config dir ~/.llm-framework
        assert cli_store.initialize() and cli_store.set_secret("openai_api_key", SK)

        sm = secrets_for(tmp_path)

        assert sm.get_secret("openai_api_key") == SK
        assert sm.get_secret("hf_token") == HF

    @pytest.mark.parametrize("value", ["pässwörd-✓-設定-‮evil", "x" * 1_000_000], ids=["unicode", "1MB"])
    def test_unusual_values_round_trip_and_keep_the_store_valid(self, memory_keyring, tmp_path, value):
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF) and sm.set_secret("odd", value)
        fresh = secrets_for(tmp_path)
        assert fresh.get_secret("odd") == value and fresh.get_secret("hf_token") == HF
        assert isinstance(json.loads(sm.secrets_file.read_text()), dict)
        assert leftovers(sm.secrets_file.parent) == []

    @pytest.mark.parametrize("value", [None, 12345, b"bytes", "lone \ud800 surrogate"])
    def test_unstorable_values_are_refused_without_touching_the_store(self, memory_keyring, tmp_path, value):
        sm = secrets_for(tmp_path)
        assert sm.set_secret("hf_token", HF)
        before = sm.secrets_file.read_bytes()
        assert sm.set_secret("odd", value) is False
        assert sm.secrets_file.read_bytes() == before
        assert "odd" not in secrets_for(tmp_path).list_secrets()


class TestAdversarialAuditLog:

    @pytest.mark.parametrize("damage, expected", [
        ("DELETE FROM events WHERE id = 3", "removed"),
        ("UPDATE events SET tool = 'list_targets', approved = 1 WHERE id = 2", "modified"),
    ], ids=["newest-row-deleted", "middle-row-changed"])
    def test_changing_the_key_id_does_not_hide_tampering(self, tmp_path, damage, expected):
        registry = ToolRegistry(PolicyStore(tmp_path), filled_log(tmp_path / "audit.db", key=b"key"))
        run_sql(registry.audit.path, damage)
        run_sql(registry.audit.path, "UPDATE meta SET v = 'deadbeefdeadbeef' WHERE k = 'key_id'")

        result = registry.audit.check()

        assert result.problem in ("modified", "removed"), result.message
        assert TestDoctorAuditFindings.audit_finding(registry).severity == Severity.ERROR

    def test_a_real_key_change_is_still_a_key_mismatch(self, tmp_path):
        """Counterpart of the test above: a genuinely different key (nothing verifies) stays key_mismatch."""
        filled_log(tmp_path / "a.db", key=b"old-key")
        result = AuditLog(tmp_path / "a.db", key=b"new-key").check()
        assert result.problem == "key_mismatch"

    def test_an_append_during_check_is_not_reported_as_removed(self, tmp_path, monkeypatch):
        log = filled_log(tmp_path / "a.db")
        other_process = AuditLog(log.path, key=b"audit-key")
        real_read_anchor = log._read_anchor
        writer = threading.Thread(target=other_process.record, args=("mcp", "list_targets", "read", "allow", "ok"))

        def append_then_read():
            if writer.ident is None:  # only during the first check()
                writer.start()
                writer.join(1.5)  # finishes at once unless check() keeps writers out while it reads
            return real_read_anchor()

        monkeypatch.setattr(log, "_read_anchor", append_then_read)
        result = log.check()
        writer.join(20)

        assert result.intact, result.message
        assert log.check().intact and log.check().entries == 4

    def test_a_failed_commit_does_not_cause_a_tamper_alarm(self, tmp_path, monkeypatch):
        log = filled_log(tmp_path / "a.db", n=2)
        real_connect = sqlite3.connect

        class CommitFails:
            def __init__(self, con):
                self._con = con

            def execute(self, sql, *args):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("disk I/O error")
                return self._con.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._con, name)

        with monkeypatch.context() as m:
            m.setattr(sqlite3, "connect", lambda *a, **kw: CommitFails(real_connect(*a, **kw)))
            with pytest.raises(sqlite3.OperationalError):
                log.record("mcp", "start_build", "local_write", "ask", "ok", approved=True)

        result = log.check()
        assert result.intact and result.entries == 2, result.message

    def test_an_anchor_that_cannot_be_replaced_does_not_drop_the_entry(self, tmp_path, monkeypatch):
        log = filled_log(tmp_path / "a.db", n=1)
        real_replace = os.replace

        def sharing_violation(src, dst):
            if str(dst).endswith(".anchor"):
                raise PermissionError(13, "The process cannot access the file because it is being used by "
                                          "another process")
            return real_replace(src, dst)

        with monkeypatch.context() as m:
            m.setattr(audit_module.os, "replace", sharing_violation)
            with contextlib.suppress(Exception):
                log.record("mcp", "start_build", "local_write", "ask", "ok", approved=True)

        assert [e["tool"] for e in log.entries()] == ["start_build", "start_build"]
        assert log.check().problem not in ("modified", "removed")

    def test_a_running_process_keeps_auditing_after_the_log_was_moved_aside(self, tmp_path):
        log = filled_log(tmp_path / "a.db", n=2)  # a long-running process that has written before
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(log.path) + suffix)
            if path.exists():
                path.rename(path.with_name(path.name + ".inspect"))

        log.record("mcp", "start_build", "local_write", "ask", "ok", approved=True)

        fresh = AuditLog(log.path, key=b"audit-key")
        assert [e["tool"] for e in fresh.entries()] == ["start_build"]
        assert fresh.check().intact

    def test_installing_a_keyring_leads_to_a_verifiable_keyed_log(self, tmp_path):
        unkeyed = filled_log(tmp_path / "a.db", n=2, key=None)  # no keyring yet: doctor warns 'unkeyed'
        assert unkeyed.check().intact

        keyed = AuditLog(unkeyed.path, key=b"key-from-the-new-keyring")
        keyed.record("gui", "start_build", "local_write", "ask", "ok", approved=True)

        assert any(e["tool"] == "start_build" and e["actor"] == "gui" for e in keyed.entries())
        result = keyed.check()
        assert result.problem != "key_mismatch", result.message
        assert result.problem != "modified", result.message

    @pytest.mark.parametrize("arguments", [
        {"model": "Ünïcödé/模型-‮evil", "note": "emoji 🦙"},
        {"model": "x" * 300_000, "token": HF},
    ], ids=["unicode-bidi", "huge-with-secret"])
    def test_unusual_arguments_keep_the_chain_intact(self, tmp_path, arguments):
        log = AuditLog(tmp_path / "a.db", key=b"k")
        log.record("mcp", "start_build", "local_write", "ask", "ok", arguments=arguments, approved=True)
        log.record("mcp", "list_targets", "read", "allow", "ok")
        entry = log.entries(2)[1]
        full = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        assert entry["args_sha256"] == hashlib.sha256(full.encode("utf-8")).hexdigest()
        assert len(entry["args_preview"]) <= 500 and HF not in entry["args_preview"]
        assert log.check().intact


class TestAdversarialPolicy:

    def test_deeply_nested_file_denies_instead_of_crashing(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.path.write_text("[" * 100_000 + "]" * 100_000)
        assert_denies_everything(store.load())
        assert store.damaged

    @pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
    def test_dangling_symlink_is_not_treated_as_a_missing_file(self, tmp_path):
        store = PolicyStore(tmp_path)
        os.symlink(tmp_path / "unmounted-drive" / "tool_policy.json", store.path)
        assert_denies_everything(store.load())
        assert store.damaged

    def test_file_with_a_byte_order_mark_fails_closed(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.path.write_bytes(b"\xef\xbb\xbf" + json.dumps(STRICT).encode())  # e.g. saved by an old Notepad
        assert_denies_everything(store.load())
        assert store.damaged and (tmp_path / "tool_policy.json.bad").exists()


class TestAdversarialRegistry:

    @pytest.mark.parametrize("input_model, outcome", [
        (framework_tools.TargetIn, "invalid_input"),  # like every real tool: pydantic rejects the surrogate
        (In, "ok"),                                   # an unconstrained str field accepts it and the tool runs
    ], ids=["rejected-like-real-tools", "accepted-plain-str"])
    def test_arguments_with_a_lone_surrogate_are_still_audited(self, tmp_path, input_model, outcome):
        registry = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db", key=b"k"))
        registry.logger = RecordingLogger()
        field = next(iter(input_model.model_fields))
        registry.register(ToolSpec("probe", "Probe", "", ToolTier.READ, input_model, Out,
                                   lambda i: Out(text="done")))

        with contextlib.suppress(ToolError):
            registry.call("probe", {field: "Rock\ud800chip"}, ToolContext("ditto"))

        assert not any("Audit write failed" in r[1] for r in registry.logger.records)
        assert last_entry(registry)["outcome"] == outcome

    def test_input_errors_do_not_echo_argument_values(self, tmp_path):
        registry = make_registry(tmp_path, lambda i: Out(text=i.text))
        with pytest.raises(ToolError) as exc:
            registry.call("probe", {"text": {"token": HF}, "extra": SK}, ToolContext("mcp"))
        assert HF not in str(exc.value) and SK not in str(exc.value)
        entry = last_entry(registry)
        assert entry["outcome"] == "invalid_input" and HF not in entry["args_preview"]
        assert SK not in entry["args_preview"]
