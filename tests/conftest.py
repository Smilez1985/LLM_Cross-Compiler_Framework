import contextlib
import functools
import gc
import logging
import os
import shutil
import subprocess
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
    if os.environ.get("HOMEDRIVE") and os.environ.get("HOMEPATH"):  # Windows' second way to name the profile
        homes.add(os.environ["HOMEDRIVE"] + os.environ["HOMEPATH"])
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



def home_variables(home, pathmod=os.path):
    """Every variable that names a home or per-user folder, pointed into `home` (the layout the defaults have).

    Windows names the profile three ways: USERPROFILE (what os.path.expanduser and Path.home() read), HOMEDRIVE plus
    HOMEPATH (their fallback, and what older programs read) and HOME (Git for Windows, OpenSSH); APPDATA and
    LOCALAPPDATA are the per-user folders below it. HOMEDRIVE is empty where paths have no drive (POSIX): the variable
    is then removed, and HOMEDRIVE + HOMEPATH is still the home. `pathmod` (ntpath) shows the Windows values on Linux."""
    home = str(home)
    join = pathmod.join
    cache = join(home, ".cache")
    hf = join(cache, "huggingface")
    drive, rest = pathmod.splitdrive(home)
    return {
        "HOME": home, "USERPROFILE": home, "HOMEDRIVE": drive, "HOMEPATH": rest,
        "XDG_CONFIG_HOME": join(home, ".config"), "XDG_CACHE_HOME": cache,
        "XDG_DATA_HOME": join(home, ".local", "share"), "XDG_STATE_HOME": join(home, ".local", "state"),
        "APPDATA": join(home, "AppData", "Roaming"), "LOCALAPPDATA": join(home, "AppData", "Local"),
        "HF_HOME": hf, "HF_TOKEN_PATH": join(hf, "token"), "HF_HUB_CACHE": join(hf, "hub"),
        "HUGGINGFACE_HUB_CACHE": join(hf, "hub"), "HF_XET_CACHE": join(hf, "xet"), "HF_ASSETS_CACHE": join(hf, "assets"),
        "TRANSFORMERS_CACHE": join(hf, "hub"), "FASTEMBED_CACHE_PATH": join(cache, "fastembed"),
        "GNUPGHOME": join(home, ".gnupg"), "DOCKER_CONFIG": join(home, ".docker"),
        "LLM_FRAMEWORK_HOME": join(home, ".llm-framework"),
    }


def redirect_home(environ, home: Path) -> None:
    for name, value in home_variables(home).items():
        if value:
            environ[name] = value
        else:
            environ.pop(name, None)  # HOMEDRIVE without a drive
    for name in CREDENTIAL_VARIABLES:
        environ.pop(name, None)
    environ["PYTHON_KEYRING_BACKEND"] = session_keyring_backend()


# What the guard compares, relative to the real home: SSH and GnuPG keys, the framework's per-user folder, the Hugging
# Face home (token and model cache), Claude Desktop's configuration, keyring files and other credential folders (on
# Windows also Claude Desktop's and keyrings.alt's folders below AppData).
HOME_GUARD_ROOTS = (".ssh", ".gnupg", ".llm-framework", ".cache/huggingface", ".cache/fastembed", ".config/Claude",
                    ".config/llm-framework", ".local/share/python_keyring", ".local/share/keyrings", ".netrc",
                    ".docker", ".aws", ".kube", ".password-store", ".huggingface", "AppData/Roaming/Claude",
                    "AppData/Local/Python Keyring")
# Content caches inside those roots that other programs change all the time (no credential lives there).
HOME_GUARD_SKIP = (".cache/huggingface/xet", ".cache/huggingface/hub/.locks")
HOME_GUARD_MAX = 50_000  # entries per root


def _below(home: Path, relative: str) -> str:
    return os.path.join(str(home), *relative.split("/"))


def home_snapshot(home: Path, roots=HOME_GUARD_ROOTS, skip=HOME_GUARD_SKIP):
    """{relative path: (kind, size, mtime_ns)} for everything below `roots` in `home`, from lstat alone: no file is
    opened and no link followed. A missing root is recorded as missing, so creating it counts as a change. Relative
    paths are written with '/' on every platform (the roots, the skip list and the report use the same form)."""
    found = {}
    skipped = set(skip)
    for root in roots:
        top = _below(home, root)
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
                with os.scandir(_below(home, relative)) as entries:
                    listing = list(entries)
            except OSError as e:
                found[relative] = (f"unreadable {type(e).__name__}", 0, 0)
                continue
            for entry in listing:
                rel = f"{relative}/{entry.name}"
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
# On Windows the temporary folder lies inside the profile (C:\Users\<name>\AppData\Local\Temp): the session's and every
# test's home are below it, which is not the real home's sensitive folders.
TEMP_DIR = Path(tempfile.gettempdir())
SESSION_HOME = Path(tempfile.mkdtemp(prefix="lcf-test-home-"))
redirect_home(os.environ, SESSION_HOME)


def is_inside(path, folder) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except OSError:
        return False


def in_real_home(path, homes=None, temp=None) -> bool:
    """True when `path` lies in a real home folder outside the temporary folder (where every test home lives, also
    when the temporary folder is inside the profile, as on Windows)."""
    homes = REAL_HOMES if homes is None else homes
    return any(is_inside(path, home) for home in homes) and not is_inside(path, TEMP_DIR if temp is None else temp)


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
    # HOME, USERPROFILE, HOMEDRIVE/HOMEPATH, APPDATA, the XDG folders, the Hugging Face home and token file, ... all
    # point into this test's own home.
    for name, value in home_variables(home).items():
        if name == "LLM_FRAMEWORK_HOME":  # the per-user folder stays unset unless a test sets it (as before)
            continue
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)  # HOMEDRIVE without a drive
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
def no_network_build_plan(monkeypatch):
    """start_build and check_build_request look up the model on Hugging Face, probe this computer and read the free
    disk space before the question (MCP design T1). Tests never reach huggingface.co: the look-up returns a small
    ungated model, the disk is roomy and the hardware unknown, unless a test injects its own (tests/test_build_tools)."""
    from orchestrator.Core import hf_lookup
    from orchestrator.Core.build_service import BuildService

    def canned(repo, revision, token, api=None):
        files = [hf_lookup.HubFile("config.json", 700), hf_lookup.HubFile("model.safetensors", 988_000_000, "0" * 64)]
        return hf_lookup.ModelFacts(repo=repo, commit=revision or "c" * 40, files=files, staged=files,
                                    weights="safetensors", gated="false", license="apache-2.0",
                                    license_class="permissive", token_access="not_needed")

    monkeypatch.setattr(hf_lookup, "look_up", canned)
    monkeypatch.setattr(BuildService, "host_hardware", lambda self: None)
    monkeypatch.setattr(BuildService, "disk_usage",
                        staticmethod(lambda path: shutil._ntuple_diskusage(1000 * 10 ** 9, 400 * 10 ** 9,
                                                                           600 * 10 ** 9)))


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


# --- Windows hosts --------------------------------------------------------------------------------------------------

# POSIX file modes (0600 files, 0700 folders) do not exist on Windows: os.chmod only sets or clears the read-only flag
# and st_mode shows 0o666 or 0o444. A test of such a mode keeps running on POSIX and names how Windows covers it.
WINDOWS_FILE_MODES = ("POSIX file modes: Windows keeps only a read-only flag (st_mode shows 0o666); there the file is "
                      "private through the ACL it inherits from the profile (the default state folder "
                      "%USERPROFILE%\\.llm-framework: the account, SYSTEM and Administrators), and explicit ACLs by "
                      "SID come with the host-account work (H12/M7)")
POSIX_MODES_ONLY = pytest.mark.skipif(os.name == "nt", reason=WINDOWS_FILE_MODES)

# pytest puts every test's id into the environment variable PYTEST_CURRENT_TEST; Windows refuses a variable longer
# than this ("the environment variable is longer than 32767 characters"), so a longer id errors in setup and teardown.
WINDOWS_ENVIRONMENT_LIMIT = 32767


@functools.lru_cache(maxsize=None)
def posix_shell(name: str = "bash"):
    """The path of a working POSIX shell called `name` (bash, sh, dash), or None. Every match on PATH is run once:
    on Windows the first `bash` on PATH is System32's WSL launcher, which runs nothing without an installed Linux
    distribution, while Git for Windows brings a real bash and dash (also tried next to git.exe)."""
    folders = os.environ.get("PATH", "").split(os.pathsep)
    git = shutil.which("git")
    if os.name == "nt" and git:
        root = Path(git).resolve().parent.parent  # <Git>\cmd\git.exe or <Git>\bin\git.exe
        folders += [str(root / "bin"), str(root / "usr" / "bin")]
    system = os.path.normcase(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    seen = set()
    for folder in folders:
        found = shutil.which(name, path=folder) if folder else None
        if not found or os.path.normcase(found) in seen:
            continue
        seen.add(os.path.normcase(found))
        if os.name == "nt" and os.path.normcase(os.path.dirname(found)) == system:
            continue  # the WSL launcher (bash.exe) is no shell for files of this host
        try:
            probe = subprocess.run([found, "-c", "printf ok"], capture_output=True, timeout=30,
                                   stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and probe.stdout == b"ok":
            return found
    return None


NO_POSIX_BASH = ("needs a POSIX bash: on Windows `bash` on PATH is the WSL launcher (no distribution on the runner) "
                 "and no Git for Windows bash was found")


@contextlib.contextmanager
def windows_newlines():
    """Inside it, text files opened for writing without newline= get CRLF, as on Windows (where the default translates
    "\\n" to os.linesep): shows on any host which files a Windows host would write with CRLF. Covers open(),
    Path.write_text/open and os.fdopen (all of them go through io.open)."""
    import builtins
    import io
    real = io.open

    def windows_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True,
                     opener=None):
        if "b" not in mode and newline is None and any(c in mode for c in "wax+"):
            newline = "\r\n"
        return real(file, mode, buffering, encoding, errors, newline, closefd, opener)

    io.open = builtins.open = windows_open
    try:
        yield
    finally:
        io.open = builtins.open = real


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
