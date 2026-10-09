import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def clean_framework_env(monkeypatch):
    """ConfigManager reads LLM_*/FRAMEWORK_* env vars; keep tests independent of the host shell."""
    for var in list(os.environ):
        if var.startswith(("LLM_", "FRAMEWORK_")):
            monkeypatch.delenv(var)


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
def booted_framework(tmp_path, monkeypatch, memory_keyring):
    """A fully initialized FrameworkManager whose relative paths live in tmp_path."""
    monkeypatch.chdir(tmp_path)
    from orchestrator.Core.framework import FrameworkManager

    fm = FrameworkManager()
    fm.info.installation_path = tmp_path
    assert fm.initialize(), "FrameworkManager.initialize() failed"
    yield fm
    fm.shutdown()


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
