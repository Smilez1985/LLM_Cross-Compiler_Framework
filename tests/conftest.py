import gc
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- The real home folder is never touched --------------------------------------------------------------------------
# Libraries read the home folder when they are imported (huggingface_hub's token and cache paths, the keyring file,
# platform folders), and some tests run code that writes SSH, Hugging Face or framework files below it. So before
# anything imports the framework, every variable that names a home or a per-user folder points into a temporary folder
# for the whole session, and each test gets its own (isolated_environment). At the end, the session fails if the real
# home folder's sensitive folders changed (their listings are compared; no file is ever opened).

def _real_homes():
    homes = {os.path.expanduser("~")}
    try:
        import pwd
        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    homes.update(os.environ.get(var, "") for var in ("USERPROFILE",))
    return sorted(Path(h) for h in homes if h and os.path.isabs(h))


# Credentials a developer's shell may hold; a test must never use them. Without the session bus no test reaches the
# desktop's keyring (Secret Service, KWallet) either.
CREDENTIAL_VARIABLES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "GH_TOKEN",
                        "GITHUB_TOKEN", "DOCKER_AUTH_CONFIG", "DBUS_SESSION_BUS_ADDRESS")


def session_keyring_backend() -> str:
    """The keyring every test uses unless it installs its own (memory_keyring): never the operating system's store
    (Secret Service, macOS Keychain, Windows Credential Manager), which outlives the temporary home. A file keyring
    below the test's home (its XDG data folder) when keyrings.alt is installed, else one that keeps nothing."""
    import importlib.util
    try:
        alt = importlib.util.find_spec("keyrings.alt.file") is not None
    except (ImportError, ValueError):
        alt = False
    return "keyrings.alt.file.PlaintextKeyring" if alt else "keyring.backends.null.Keyring"



def home_variables(home: Path):
    """Every variable that names a home or per-user folder, pointed into `home` (the layout the defaults have)."""
    home = Path(home)
    cache, hf = home / ".cache", home / ".cache" / "huggingface"
    return {
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(cache),
        "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_STATE_HOME": str(home / ".local" / "state"),
        "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "HF_HOME": str(hf), "HF_TOKEN_PATH": str(hf / "token"), "HF_HUB_CACHE": str(hf / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(hf / "hub"), "HF_XET_CACHE": str(hf / "xet"), "HF_ASSETS_CACHE": str(hf / "assets"),
        "TRANSFORMERS_CACHE": str(hf / "hub"), "FASTEMBED_CACHE_PATH": str(cache / "fastembed"),
        "GNUPGHOME": str(home / ".gnupg"), "DOCKER_CONFIG": str(home / ".docker"),
        "LLM_FRAMEWORK_HOME": str(home / ".llm-framework"),
    }


def redirect_home(environ, home: Path) -> None:
    for name, value in home_variables(home).items():
        environ[name] = value
    for name in CREDENTIAL_VARIABLES:
        environ.pop(name, None)
    environ["PYTHON_KEYRING_BACKEND"] = session_keyring_backend()


# What the guard compares, relative to the real home: SSH and GnuPG keys, the framework's per-user folder, the Hugging
# Face home (token and model cache), Claude Desktop's configuration, keyring files and other credential folders.
HOME_GUARD_ROOTS = (".ssh", ".gnupg", ".llm-framework", ".cache/huggingface", ".cache/fastembed", ".config/Claude",
                    ".config/llm-framework", ".local/share/python_keyring", ".local/share/keyrings", ".netrc",
                    ".docker", ".aws", ".kube", ".password-store", ".huggingface")
# Content caches inside those roots that other programs change all the time (no credential lives there).
HOME_GUARD_SKIP = (".cache/huggingface/xet", ".cache/huggingface/hub/.locks")
HOME_GUARD_MAX = 50_000  # entries per root


def home_snapshot(home: Path, roots=HOME_GUARD_ROOTS, skip=HOME_GUARD_SKIP):
    """{relative path: (kind, size, mtime_ns)} for everything below `roots` in `home`, from lstat alone: no file is
    opened and no link followed. A missing root is recorded as missing, so creating it counts as a change."""
    found = {}
    skipped = {os.path.normpath(s) for s in skip}
    for root in roots:
        top = os.path.join(str(home), root)
        try:
            st = os.lstat(top)
        except FileNotFoundError:
            found[root] = ("missing", 0, 0)
            continue
        except OSError as e:
            found[root] = (f"unreadable {type(e).__name__}", 0, 0)
            continue
        if not os.path.isdir(top) or os.path.islink(top):
            found[root] = ("link" if os.path.islink(top) else "file", st.st_size, st.st_mtime_ns)
            continue
        found[root] = ("dir", 0, 0)
        pending, count = [root], 0
        while pending and count < HOME_GUARD_MAX:
            relative = pending.pop()
            try:
                with os.scandir(os.path.join(str(home), relative)) as entries:
                    listing = list(entries)
            except OSError as e:
                found[relative] = (f"unreadable {type(e).__name__}", 0, 0)
                continue
            for entry in listing:
                rel = os.path.normpath(os.path.join(relative, entry.name))
                if rel in skipped:
                    continue
                count += 1
                try:
                    est = entry.stat(follow_symlinks=False)
                except OSError:
                    found[rel] = ("gone", 0, 0)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    found[rel] = ("dir", 0, 0)  # a folder's own time changes with its entries, which are listed
                    pending.append(rel)
                else:
                    found[rel] = ("link" if entry.is_symlink() else "file", est.st_size, est.st_mtime_ns)
    return found


def snapshot_changes(before, after):
    """What changed between two home_snapshot() results, one line each."""
    changes = [f"added {p}" for p in sorted(set(after) - set(before)) if after[p][0] != "missing"]
    changes += [f"removed {p}" for p in sorted(set(before) - set(after)) if before[p][0] != "missing"]
    changes += [f"changed {p}" for p in sorted(set(before) & set(after)) if before[p] != after[p]]
    return changes


REAL_HOMES = _real_homes()
REAL_HOME_SNAPSHOTS = {home: home_snapshot(home) for home in REAL_HOMES}
SESSION_HOME = Path(tempfile.mkdtemp(prefix="lcf-test-home-"))
redirect_home(os.environ, SESSION_HOME)

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
    for var in list(os.environ):
        if var.startswith(("LLM_", "FRAMEWORK_")):
            monkeypatch.delenv(var)
    # HOME, the XDG folders, the Hugging Face home and token file, ... all point into this test's own home.
    for name, value in home_variables(home).items():
        if name != "LLM_FRAMEWORK_HOME":  # the per-user folder stays unset unless a test sets it (as before)
            monkeypatch.setenv(name, value)
    for name in CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
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
def no_network_llama_source(monkeypatch):
    """Tests never fetch the llama.cpp source from the network: a real fetch is about 215 MB, and a test that reaches
    it would go on to a real build. Local mirrors and bundles (the source tests) still work."""
    from orchestrator.runtime.build import source
    real = source.fetch_source

    def guarded(pin, state_dir, *, url=None, **kwargs):
        if str(url or pin.repo).lower().startswith(("https://", "http://", "ssh://", "git://")):
            raise RuntimeError("a test tried to fetch the llama.cpp source from the network; inject a fake")
        return real(pin, state_dir, url=url, **kwargs)

    monkeypatch.setattr(source, "fetch_source", guarded)


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
    shutil.rmtree(SESSION_HOME, ignore_errors=True)
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


def real_home_changes():
    """Changes below the real home folder's guarded folders since the session started, one line each."""
    lines = []
    for home, before in REAL_HOME_SNAPSHOTS.items():
        lines += [f"{home}: {change}" for change in snapshot_changes(before, home_snapshot(home))]
    return lines


def pytest_sessionfinish(session, exitstatus):
    """Fails the session when the real home folder changed while the tests ran (by a test or, rarely, by another
    program at the same time: the list says what changed, never what a file holds)."""
    changes = real_home_changes()
    if not changes:
        return
    shown = changes[:40] + ([f"... and {len(changes) - 40} more"] if len(changes) > 40 else [])
    message = ("The real home folder changed during this test run; tests must use tmp paths and the isolation "
               "fixture:\n  " + "\n  ".join(shown))
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "real home folder changed", red=True)
        reporter.write_line(message)
    else:
        print(message, file=sys.stderr)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
