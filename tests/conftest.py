import logging
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _catalog_key_variables():
    """Every environment variable the provider catalog reads as a key source (method `env` lists)."""
    import yaml
    data = yaml.safe_load((REPO_ROOT / "configs" / "llm_providers.yml").read_text(encoding="utf-8")) or {}
    names = set()
    for preset in (data.get("providers") or {}).values():
        for method in (preset or {}).get("auth") or []:
            if isinstance(method, dict):
                names.update(method.get("env") or [])
    return sorted(names)


CATALOG_KEY_VARIABLES = _catalog_key_variables()


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Keeps tests away from the developer's real ~/.llm-framework, API keys and LLM_* settings."""
    home = tmp_path / "home"
    home.mkdir()
    # Components that fall back to relative paths (output/, cache/, models/) must not write into the repo.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in list(os.environ):
        if var.startswith(("LLM_", "FRAMEWORK_")):
            monkeypatch.delenv(var)
    # Without it the installation's configs/ folder (the repository) would be used and written to.
    monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", str(tmp_path / "configs"))
    # Real provider keys from the developer's shell must not reach the tests, nor SDK switches (headers, endpoints,
    # profiles, debug logs) that change what the SDKs send; the CLI's startup guard would also edit them for good.
    # The catalog's key variables are a credential source of the provider layer itself.
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "OPENAI_CUSTOM_HEADERS", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL",
                "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR", "ANTHROPIC_LOG",
                "OPENAI_LOG", *CATALOG_KEY_VARIABLES):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def sdk_debug_logging_off():
    """A CLI or GUI test with --debug-http must not leave the SDK loggers at DEBUG, with a stderr handler, for
    the tests after it (the choice is process-wide and kept by later setup_logging() calls)."""
    yield
    from orchestrator.security import redaction
    if getattr(redaction, "_debug_http", False):
        redaction.configure_sdk_loggers(logging.INFO, debug_http=False)


@pytest.fixture
def memory_keyring():
    keyring = pytest.importorskip("keyring")
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.store = {}

        def get_password(self, service, username):
            return self.store.get((service, username))

        def set_password(self, service, username, password):
            self.store[(service, username)] = password

        def delete_password(self, service, username):
            self.store.pop((service, username), None)

    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def make_framework(tmp_path, monkeypatch, memory_keyring):
    """Builds FrameworkManagers whose config, data and install paths all live in tmp_path."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "configs"
    config_dir.mkdir(exist_ok=True)
    # Per-user state (tool policy, audit log, chat history) goes to its own directory, not $HOME.
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    from orchestrator.Core.framework import FrameworkManager

    created = []

    def factory(config_file=None, initialize=True):
        fm = FrameworkManager(str(config_file or config_dir))
        fm.info.installation_path = tmp_path  # output, cache and models go to tmp_path
        fm.config.set("targets_dir", str(REPO_ROOT / "targets"))  # the real targets, read-only use
        if initialize:
            assert fm.initialize(), "FrameworkManager.initialize() failed"
        created.append(fm)
        return fm

    yield factory
    for fm in created:
        fm.shutdown()


@pytest.fixture
def booted_framework(make_framework):
    return make_framework()


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
