import gc
import logging
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Exported by a git command that runs the tests (a hook, `git rebase --exec`, `git bisect run`): with them, the
# tests' own git commands (`git init` and `git commit` in a temporary folder) would act on this repository instead.
GIT_REPOSITORY_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")


def scrub_git_environment(environ) -> None:
    for name in GIT_REPOSITORY_VARIABLES:
        environ.pop(name, None)


scrub_git_environment(os.environ)


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
    # Spoken announcements stay off; audio tests switch them on explicitly.
    monkeypatch.setenv("LLM_FRAMEWORK_NO_AUDIO", "1")
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
    yield QApplication.instance() or QApplication([])
    # Qt objects a GUI test leaves unreachable die now, on the GUI thread. Left to a later garbage collection, they
    # died at a random point of another test (seen while it started a thread), which crashed the run with SIGSEGV.
    gc.collect()


def frameworks_left_running(framework_cls):
    """FrameworkManager objects whose background event loop was never stopped. type() instead of isinstance():
    isinstance() reads __class__, which raises ReferenceError for a weakref.proxy whose object is gone."""
    return [obj for obj in gc.get_objects()
            if issubclass(type(obj), framework_cls) and getattr(obj, "_loop", None) is not None]


def pytest_unconfigure(config):
    """Ends what the tests left running while Python is still fully alive.

    Background event loops of frameworks that were never shut down (CLI tests boot their own) are stopped. The
    widgets left by GUI tests and the QApplication are deleted here: otherwise PySide deletes them in its exit
    handler during interpreter finalization, which now and then crashed the process with SIGSEGV
    (QObject::~QObject called from PySide::destroyQCoreApplication) after every test had passed."""
    framework = sys.modules.get("orchestrator.Core.framework")
    if framework is not None:
        for obj in frameworks_left_running(framework.FrameworkManager):
            try:
                obj._stop_loop(timeout=5)
            except Exception as e:  # e.g. a half-built stand-in: the widgets below must still be deleted
                print(f"conftest: event loop of {obj!r} not stopped: {e!r}", file=sys.stderr)
    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets is not None else None
    if app is None:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    app.shutdown()
