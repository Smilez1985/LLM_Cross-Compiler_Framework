"""Starting without a terminal (docs/DESIGN.md, GUI and the person's path): the Linux desktop entry, the Windows
Start-menu shortcut, the GUI without standard streams, the crash dialog and the crash file before Qt, and
`llm-cli launcher` with its mouse path.

The desktop entry is written into the test's own home and checked with desktop-file-validate where it is installed.
The Windows shortcut is read and written through PowerShell, which a fake runner stands in for here; install.bat and
the uninstaller are batch files that cannot run on Linux, so their text is checked the way the other installer tests
do. The crash dialog runs offscreen with its modal exec and its folder opener replaced.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from conftest import REPO_ROOT
from orchestrator.utils import crash_report, launcher

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_BAT = REPO_ROOT / "install.bat"
UNINSTALL_BAT = REPO_ROOT / "scripts" / "Uninstall-LLM-Cross-Compiler-Framework.bat"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="needs bash")
TOKEN = "hf_" + "Q7x" * 12   # the shape of a Hugging Face token, made up


def env_of(home: Path, **more) -> dict:
    return {"HOME": str(home), **more}


def unescape_string(value: str) -> str:
    """A desktop entry's string value as a reader sees it (\\\\ -> \\, \\s, \\n, \\t, \\r)."""
    out, i = [], 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            out.append({"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def exec_argv(value: str) -> list:
    """The Exec key split into its arguments by the specification's quoting rules (all arguments quoted here)."""
    text, args, i = unescape_string(value), [], 0
    while i < len(text):
        if text[i] == " ":
            i += 1
            continue
        assert text[i] == '"', text
        i += 1
        arg = []
        while text[i] != '"':
            if text[i] == "\\":
                i += 1
            arg.append(text[i])
            i += 1
        args.append("".join(arg).replace("%%", "%"))
        i += 1
    return args


def keys_of(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines()[1:] if "=" in line)


# --- the Linux desktop entry ----------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="the desktop entry is Linux's (POSIX paths)")
class TestDesktopEntry:

    def test_written_into_the_persons_application_folder_and_valid(self, tmp_path):
        root = tmp_path / "opt" / "llm-cross-compiler-framework"
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / ".venv" / "bin" / "python").write_text("")
        env = env_of(tmp_path / "home")
        assert launcher.linux_status(root, env).state == "missing"
        assert launcher.linux_install(root, env) == "written"
        path = tmp_path / "home" / ".local" / "share" / "applications" / "llm-cross-compiler-framework.desktop"
        text = path.read_text(encoding="utf-8")
        keys = keys_of(text)
        assert text.startswith("[Desktop Entry]\n") and keys["Type"] == "Application"
        assert keys["Terminal"] == "false" and keys["X-LLMCF-Launcher"] == "true"
        python = str(root / ".venv" / "bin" / "python")
        assert keys["TryExec"] == python, "a desktop hides the entry once the installation is gone"
        assert exec_argv(keys["Exec"]) == [python, str(root / "orchestrator" / "main.py")]
        assert keys["Path"] == str(root) and keys["Icon"] == str(root / "assets" / "logo.png")
        assert "activate" not in text and "bash" not in text, "no shell and no activated environment"
        assert launcher.linux_status(root, env).state == "current"
        assert launcher.linux_install(root, env) == "unchanged"
        validator = shutil.which("desktop-file-validate")
        if validator and os.name != "nt":
            run = subprocess.run([validator, str(path)], capture_output=True, text=True, timeout=60)
            assert run.returncode == 0 and not run.stdout.strip(), run.stdout + run.stderr

    def test_the_repository_has_the_icon_the_entry_names(self):
        assert (REPO_ROOT / "assets" / "logo.png").is_file()
        assert keys_of(launcher.desktop_entry(REPO_ROOT))["Icon"] == str(REPO_ROOT / "assets" / "logo.png")

    @pytest.mark.parametrize("folder", ['/opt/my llmcf', '/opt/a$b"c`d\\e%f'])
    def test_exec_quotes_every_path_as_the_specification_says(self, folder):
        python = folder + "/.venv/bin/python"
        keys = keys_of(launcher.desktop_entry(Path(folder), python))
        assert exec_argv(keys["Exec"]) == [python, folder + "/orchestrator/main.py"]
        assert unescape_string(keys["TryExec"]) == python and unescape_string(keys["Path"]) == folder

    def test_a_path_with_a_line_break_is_refused(self):
        with pytest.raises(ValueError):
            launcher.desktop_entry(Path("/opt/a\nb"), "/usr/bin/python3")

    def test_xdg_data_home_is_used_only_when_absolute(self, tmp_path):
        assert launcher.applications_dir(env_of(tmp_path, XDG_DATA_HOME=str(tmp_path / "d"))) == tmp_path / "d" / \
            "applications"
        assert launcher.applications_dir(env_of(tmp_path, XDG_DATA_HOME="rel")) == tmp_path / ".local" / "share" / \
            "applications"

    def test_another_installation_is_outdated_and_repaired(self, tmp_path):
        env = env_of(tmp_path / "home")
        launcher.linux_install(tmp_path / "old", env)
        assert launcher.linux_status(tmp_path / "new", env).state == "outdated"
        assert launcher.linux_install(tmp_path / "new", env) == "written"
        assert launcher.linux_status(tmp_path / "new", env).state == "current"

    def test_a_file_the_framework_did_not_write_is_never_changed(self, tmp_path):
        env = env_of(tmp_path / "home")
        path = launcher.desktop_path(env)
        path.parent.mkdir(parents=True)
        path.write_text("[Desktop Entry]\nType=Application\nName=Mine\nExec=mine\n")
        assert launcher.linux_status(tmp_path, env).state == "foreign"
        assert launcher.linux_install(tmp_path, env) == "foreign"
        assert launcher.linux_remove(tmp_path, env) == "foreign"
        assert path.read_text().startswith("[Desktop Entry]\nType=Application\nName=Mine")

    def test_remove_takes_only_the_own_entry(self, tmp_path):
        env = env_of(tmp_path / "home")
        assert launcher.linux_remove(tmp_path, env) == "missing"
        launcher.linux_install(tmp_path, env)
        assert launcher.linux_remove(tmp_path, env) == "removed"
        assert not launcher.desktop_path(env).exists()

    def test_the_gui_start_rewrites_an_outdated_entry_but_never_creates_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert launcher.refresh("Linux") is False and not launcher.desktop_path().exists()
        launcher.linux_install(tmp_path / "elsewhere")
        assert launcher.refresh("Linux") is True
        assert launcher.linux_status().state == "current"
        assert launcher.refresh("Linux") is False and launcher.refresh("Windows") is False


# --- the Windows shortcut through PowerShell (a fake runner) --------------------------------------------------------

class FakeShell:
    """Stands in for PowerShell's WScript.Shell: shortcuts in a dict, read and written by the scripts' variables."""

    def __init__(self, links=None):
        self.links = dict(links or {})
        self.calls = []

    def exists(self, path):
        return path in self.links

    def __call__(self, argv, env, **kwargs):
        assert argv[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
        self.calls.append((argv[4], {k: v for k, v in env.items() if k.startswith("LCF_")}))
        if argv[4] == launcher.READ_SCRIPT:
            target, args, description = self.links[env["LCF_LNK"]]
            return subprocess.CompletedProcess(argv, 0, f"{target}\n{args}\n{description}\n", "")
        assert argv[4] == launcher.WRITE_SCRIPT
        self.links[env["LCF_LNK"]] = (env["LCF_TARGET"], env["LCF_ARGS"], env["LCF_DESCRIPTION"])
        return subprocess.CompletedProcess(argv, 0, "", "")


ROOT_W = "C:\\Program Files\\LLM-Cross-Compiler-Framework"
ENV_W = {"ProgramData": "C:\\ProgramData", "APPDATA": "C:\\Users\\alice\\AppData\\Roaming"}
COMMON = "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\LLM-Cross-Compiler-Framework.lnk"
OWN = "C:\\Users\\alice\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\LLM-Cross-Compiler-Framework.lnk"
RIGHT = (ROOT_W + "\\.venv\\Scripts\\pythonw.exe", f'"{ROOT_W}\\orchestrator\\main.py"', "LLM Cross-Compiler Framework")


class TestWindowsShortcut:

    def run(self, action, shell):
        return getattr(launcher, f"windows_{action}")(ROOT_W, ENV_W, run=shell, exists=shell.exists)

    def test_the_shortcut_starts_pythonw_with_main_py(self):
        values = launcher.windows_shortcut(ROOT_W)
        assert values["LCF_TARGET"].endswith("\\.venv\\Scripts\\pythonw.exe")
        assert values["LCF_ARGS"] == f'"{ROOT_W}\\orchestrator\\main.py"' and values["LCF_WORKDIR"] == ROOT_W
        assert values["LCF_ICON"].endswith("\\assets\\LLM-Builder.ico")

    def test_the_installers_shortcut_for_all_accounts_covers_the_person(self):
        shell = FakeShell({COMMON: RIGHT})
        assert [(e.scope, e.state) for e in self.run("status", shell)] == [("everyone", "current"), ("person", "missing")]
        assert self.run("install", shell) == "covered" and OWN not in shell.links

    def test_an_old_batch_shortcut_is_outdated_and_the_person_gets_an_own_one(self):
        shell = FakeShell({COMMON: (ROOT_W + "\\start_framework.bat", "", "Startet das Framework")})
        assert self.run("status", shell)[0].state == "outdated"
        assert self.run("install", shell) == "written" and shell.links[OWN] == RIGHT
        script, values = shell.calls[-1]
        assert "$env:LCF_TARGET" in script and ROOT_W not in script, "values travel as variables, not in the script"
        assert self.run("install", shell) == "unchanged"

    def test_remove_takes_only_the_persons_own_shortcut_and_never_a_foreign_one(self, monkeypatch):
        removed = []
        monkeypatch.setattr(launcher, "remove_file", removed.append)
        shell = FakeShell({OWN: ("C:\\Tools\\other.exe", "", "Something else")})
        assert self.run("install", shell) == "foreign" and self.run("remove", shell) == "foreign" and removed == []
        shell = FakeShell({COMMON: RIGHT, OWN: RIGHT})
        assert self.run("remove", shell) == "removed" and removed == [OWN], "never the one for all accounts"


# --- no console: streams, console windows ---------------------------------------------------------------------------

class TestWithoutConsole:

    def test_missing_streams_are_replaced_and_everything_keeps_working(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        assert crash_report.guard_streams() == ["stdout", "stderr"]
        try:
            print("hello")
            print("to stderr", file=sys.stderr)
            sys.stderr.write("x")
            assert isinstance(sys.stderr.fileno(), int) and not sys.stdout.isatty()
            handler = logging.StreamHandler(sys.stderr)
            handler.emit(logging.LogRecord("t", logging.ERROR, __file__, 1, "message", None, None))
            from rich.console import Console
            Console().print("[bold]rich[/bold]")
            Console(stderr=True).print("rich on stderr")
            env = {**os.environ, "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, "-c", "print('child')"], env=env, timeout=60)
            assert result.returncode == 0
        finally:
            sys.stdout.close()
            sys.stderr.close()
        assert crash_report.guard_streams() == []

    def test_children_without_own_flags_get_no_console_window(self, monkeypatch):
        seen = []

        def init(self, *args, **kwargs):
            seen.append(kwargs.get("creationflags"))

        monkeypatch.setattr(subprocess.Popen, "__init__", init)
        monkeypatch.setattr(crash_report, "_PATCHED", False)
        assert crash_report.windowless_children(force=True) is True
        subprocess.Popen(["tool"])
        subprocess.Popen(["installer"], creationflags=crash_report.CREATE_NEW_CONSOLE)
        assert seen == [crash_report.CREATE_NO_WINDOW, crash_report.CREATE_NEW_CONSOLE]

    def test_nothing_changes_with_a_console_or_outside_windows(self, monkeypatch):
        monkeypatch.setattr(crash_report, "_PATCHED", False)
        if os.name != "nt":
            assert crash_report.windowless_children() is False
        assert crash_report.windowless_flags(0) == crash_report.CREATE_NO_WINDOW
        assert crash_report.windowless_flags(crash_report.CREATE_NEW_CONSOLE) == crash_report.CREATE_NEW_CONSOLE


# --- crash details, the log folder, the crash file ------------------------------------------------------------------

def raised(message: str) -> BaseException:
    try:
        raise RuntimeError(message)
    except RuntimeError as e:
        return e


class TestCrashReport:

    def test_details_name_version_python_and_system_without_secrets_or_home(self):
        home = str(Path.home())
        details = crash_report.crash_details(raised(f"failed with {TOKEN} in {home}/models/run.log"), "a test")
        assert TOKEN not in details and home not in details and "~/models/run.log" in details.replace("\\", "/")
        assert details.startswith(f"llmcf {crash_report.version()} - unexpected error in a test")
        assert "Python " in details and "System: " in details and "Traceback" in details
        assert "RuntimeError" in details

    def test_the_log_folder_is_the_setting(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert crash_report.log_folder({"logs_dir": "logs"}) == tmp_path / "logs"
        assert crash_report.log_folder({"logs_dir": str(tmp_path / "abs")}) == tmp_path / "abs"
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
        assert crash_report.log_folder({"logs_dir": ""}) == tmp_path / "state" / "logs"
        assert crash_report.log_folder(None) == tmp_path / "state" / "logs"

    def test_gui_log_is_written_through_the_redaction(self, tmp_path):
        handler = crash_report.add_gui_log(tmp_path / "logs")
        try:
            logging.getLogger("llmcf.test").warning("token %s", TOKEN)
            handler.flush()
            text = (tmp_path / "logs" / "gui.log").read_text(encoding="utf-8")
            assert "token" in text and TOKEN not in text
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_an_early_crash_goes_into_a_crash_file_and_a_native_box_on_windows(self, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(crash_report, "WINDOWS", True)
        monkeypatch.setattr(crash_report, "has_console", lambda: False)
        path = crash_report.report_early_crash(raised(f"broken {TOKEN}"), {"logs_dir": str(tmp_path / "logs")},
                                               message=lambda title, text: shown.append((title, text)) or True)
        assert path.parent == tmp_path / "logs" and path.name.startswith("crash-") and path.suffix == ".txt"
        assert "RuntimeError" in path.read_text(encoding="utf-8") and TOKEN not in path.read_text(encoding="utf-8")
        assert len(shown) == 1 and path.name in shown[0][1] and TOKEN not in shown[0][1]
        again = crash_report.report_early_crash(raised("again"), {"logs_dir": str(tmp_path / "logs")},
                                                message=lambda *a: True)
        assert again != path and again.exists(), "a second crash in the same second gets its own file"

    def test_with_a_console_no_box_is_shown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(crash_report, "WINDOWS", True)
        monkeypatch.setattr(crash_report, "has_console", lambda: True)
        shown = []
        crash_report.report_early_crash(raised("x"), {"logs_dir": str(tmp_path)}, message=lambda *a: shown.append(a))
        assert shown == []


def test_main_py_writes_a_crash_file_when_the_gui_cannot_even_be_imported(tmp_path):
    """Started as the shortcut starts it (orchestrator/main.py as a script) without any standard stream, a crash
    before Qt exists ends with exit code 1 and a crash file in the log folder."""
    logs = tmp_path / "crashlogs"
    code = ("import runpy, sys\n"
            "sys.stdout = None\nsys.stderr = None\n"
            "sys.modules['orchestrator.gui.app'] = None\n"
            "import orchestrator.utils.crash_report as report\n"
            "report.native_message = lambda title, text: True\n"   # never a real message box in a test
            f"sys.argv = [{str(REPO_ROOT / 'orchestrator' / 'main.py')!r}]\n"
            f"runpy.run_path({str(REPO_ROOT / 'orchestrator' / 'main.py')!r}, run_name='__main__')\n")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(REPO_ROOT), "LLM_FRAMEWORK_LOGS_DIR": str(logs)}
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert run.returncode == 1, run.stdout + run.stderr
    files = list(logs.glob("crash-*.txt"))
    assert len(files) == 1, (run.stdout, run.stderr)
    text = files[0].read_text(encoding="utf-8")
    assert "ModuleNotFoundError" in text and "orchestrator.gui.app" in text and "the start of the window" in text


# --- the crash dialog (offscreen) -----------------------------------------------------------------------------------

@pytest.fixture
def reporter(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import crash_dialog
    opened, dialogs = [], []
    monkeypatch.setattr(crash_dialog, "_exec", lambda dialog: dialogs.append(dialog) or 0)
    found = crash_dialog.CrashReporter(tmp_path / "logs", opener=lambda folder: opened.append(folder) or True)
    found.opened, found.dialogs = opened, dialogs
    yield found
    found.deleteLater()


class TestCrashDialog:

    def test_one_error_shows_the_dialog_with_its_buttons(self, reporter, qapp, caplog):
        from PySide6.QtWidgets import QApplication
        reporter_exec = reporter.dialogs

        with caplog.at_level(logging.CRITICAL, logger="llmcf.crash"):
            error = raised(f"bad value {TOKEN} in {Path.home()}/x")
            reporter.excepthook(type(error), error, error.__traceback__)
            qapp.processEvents()
        assert len(reporter_exec) == 1 and reporter.shown == 1
        dialog = reporter_exec[0]
        assert dialog.objectName() == "crash_dialog" and "unexpected error" in dialog.summary.text()
        assert dialog.btn_open_log.objectName() == "btn_crash_open_log" and dialog.btn_copy.text()
        dialog.copy_details()
        copied = QApplication.clipboard().text()
        assert "RuntimeError" in copied and TOKEN not in copied and str(Path.home()) not in copied
        assert "llmcf " in copied and "Python " in copied and "System: " in copied
        assert dialog.open_log_folder() and reporter.opened == [reporter.folder]
        logged = [r.getMessage() for r in caplog.records if r.name == "llmcf.crash"]
        assert logged and "RuntimeError" in logged[0] and TOKEN not in logged[0]

    def test_a_burst_of_errors_shows_one_dialog_that_counts_the_rest(self, reporter, qapp, monkeypatch):
        from orchestrator.gui import crash_dialog
        seen = []

        def exec_with_more_errors(dialog):
            for n in range(3):
                error = raised(f"more {n}")
                reporter.excepthook(type(error), error, error.__traceback__)
            qapp.processEvents()
            seen.append((dialog, dialog.lbl_more.text(), dialog.lbl_more.isVisibleTo(dialog)))
            return 0

        monkeypatch.setattr(crash_dialog, "_exec", exec_with_more_errors)
        error = raised("first")
        reporter.excepthook(type(error), error, error.__traceback__)
        qapp.processEvents()
        qapp.processEvents()
        assert len(seen) == 1 and reporter.shown == 1, "never a second dialog for the same burst"
        assert "3" in seen[0][1] and seen[0][2]
        assert reporter.busy is False and reporter.more == 0
        error = raised("later")
        reporter.excepthook(type(error), error, error.__traceback__)
        qapp.processEvents()
        assert reporter.shown == 2, "after Close the next error shows again"

    def test_an_error_in_another_thread_is_shown_on_the_gui_thread(self, reporter, qapp):
        threads = []

        def fail():
            try:
                raise ValueError("in a worker")
            except ValueError as e:
                threads.append(threading.current_thread())
                reporter.thread_excepthook(SimpleNamespace(exc_type=ValueError, exc_value=e,
                                                           exc_traceback=e.__traceback__, thread=threads[0]))

        worker = threading.Thread(target=fail, name="worker-1")
        worker.start()
        worker.join(30)
        assert reporter.dialogs == [], "not shown from the worker thread itself"
        qapp.processEvents()
        assert len(reporter.dialogs) == 1 and "thread worker-1" in reporter.dialogs[0].details_text
        assert reporter.dialogs[0].thread() is qapp.thread()

    def test_ctrl_c_keeps_pythons_own_handling(self, reporter):
        called = []
        reporter.previous_hook = lambda *a: called.append(a[0])
        reporter.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        assert called == [KeyboardInterrupt] and reporter.dialogs == []

    def test_install_sets_both_hooks_and_uninstall_puts_them_back(self, qapp, tmp_path):
        from orchestrator.gui import crash_dialog
        before = (sys.excepthook, threading.excepthook)
        found = crash_dialog.install(tmp_path)
        assert crash_dialog.install(tmp_path) is found
        assert sys.excepthook == found.excepthook and threading.excepthook == found.thread_excepthook
        crash_dialog.uninstall()
        assert (sys.excepthook, threading.excepthook) == before


# --- the command line and the mouse path ----------------------------------------------------------------------------

class TestCommandLine:

    def invoke(self, *args):
        from orchestrator.cli import cli
        return CliRunner().invoke(cli, ["launcher", *args], env={"PYTHONUTF8": "1"})

    @pytest.mark.skipif(os.name == "nt", reason="the Linux entry; Windows goes through PowerShell (fakes above)")
    def test_status_install_remove(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher, "system_name", lambda system=None: system or "Linux")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        result = self.invoke("status")
        assert result.exit_code == 0 and "not there" in result.output and "your own" in result.output
        result = self.invoke("install")
        assert result.exit_code == 0 and "is written" in result.output and launcher.desktop_path().exists()
        assert "up to date" in self.invoke("status").output
        assert "already up to date" in self.invoke("install").output
        result = self.invoke("remove")
        assert result.exit_code == 0 and "is removed" in result.output and not launcher.desktop_path().exists()

    def test_a_system_without_start_entries_says_so(self, monkeypatch):
        monkeypatch.setattr(launcher, "system_name", lambda system=None: "Darwin")
        result = self.invoke("status")
        assert result.exit_code == 1 and "no start entry" in result.output


@pytest.mark.skipif(os.name == "nt", reason="the Linux entry; Windows goes through PowerShell (fakes above)")
def test_the_update_page_shows_creates_and_removes_the_entry(qapp, monkeypatch, tmp_path):
    from orchestrator.gui.update_page import UpdatePage
    monkeypatch.setattr(launcher, "system_name", lambda system=None: system or "Linux")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    page = UpdatePage(None, lang="en")
    try:
        assert "Not there" in page.lbl_launcher_status.text()
        assert page.change_start_entry("install") == "written" and launcher.desktop_path().exists()
        assert "up to date" in page.lbl_launcher_status.text()
        assert page.change_start_entry("remove") == "removed" and not launcher.desktop_path().exists()
        assert "removed" in page.lbl_launcher_result.text()
    finally:
        page.deleteLater()


def test_the_crash_texts_exist_in_both_languages():
    from orchestrator.utils.localization import ui_text
    for lang in ("en", "de"):
        for key in ("crash.title", "crash.summary", "crash.why", "crash.open_log", "launcher.title"):
            assert ui_text(key, lang) != key
    assert ui_text("crash.open_log", "de") == "Log-Ordner öffnen" and ui_text("words.error.copy", "de") == \
        "Details kopieren"


# --- the installers -------------------------------------------------------------------------------------------------

@needs_bash
class TestInstallSh:

    def run(self, tmp_path, script, headless="0", user="alice"):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = tmp_path / "calls.log"
        (bin_dir / "sudo").write_text(f'#!/bin/sh\necho "sudo $*" >> "{log}"\n'
                                      'while [ $# -gt 0 ]; do case "$1" in -H) shift ;; -u) shift 2 ;; --*) shift ;; '
                                      '*) break ;; esac; done\nexec "$@"\n')
        (bin_dir / "sudo").chmod(0o755)
        install = tmp_path / "opt"
        (install / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        python = install / ".venv" / "bin" / "python"
        python.write_text(f'#!/bin/sh\necho "python $*" >> "{log}"\nexit "${{FAKE_EXIT:-0}}"\n')
        python.chmod(0o755)
        code = f'source "$1"; INSTALL_DIR="{install}"; HEADLESS={headless}; CONTAINER_BUILD=0; {script}'
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "SUDO_USER": user, "USER": "root",
               **({"FAKE_EXIT": "3"} if "FAIL" in script else {})}
        result = subprocess.run(["/bin/bash", "-c", code, "test", str(INSTALL_SH)], env=env, capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=120)
        return result, (log.read_text().splitlines() if log.exists() else [])

    def test_the_entry_is_written_as_the_person_with_the_installations_python(self, tmp_path):
        result, calls = self.run(tmp_path, "setup_start_entry")
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[0].startswith("sudo -H -u alice ") and calls[1] == "python -m orchestrator.cli launcher install"
        assert "Anwendungsmenü" in result.stdout

    def test_headless_and_root_write_none_and_a_failure_never_stops_the_installation(self, tmp_path):
        result, calls = self.run(tmp_path, "setup_start_entry", headless="1")
        assert result.returncode == 0 and calls == []
        result, calls = self.run(tmp_path, "setup_start_entry", user="root")
        assert result.returncode == 0 and calls == [] and "llm-cli launcher install" in result.stdout
        result, calls = self.run(tmp_path, "FAIL=1; setup_start_entry; echo DONE")
        assert result.returncode == 0 and "DONE" in result.stdout and "llm-cli launcher install" in result.stdout


def test_install_sh_writes_the_entry_in_every_kind_of_run():
    text = INSTALL_SH.read_text(encoding="utf-8")
    main = text.split("\nmain() {", 1)[1]
    assert main.index("record_installation") < main.index("setup_start_entry") < main.index("finish")
    person = text.split("\nperson_install() {", 1)[1].split("\n}\n", 1)[0]
    assert "setup_start_entry" in person, "a second person gets their own entry too"


def test_install_bat_starts_pythonw_from_the_start_menu_and_asks_for_the_desktop():
    text = INSTALL_BAT.read_bytes().decode("utf-8")
    assert text.count("\n") == text.count("\r\n"), "cmd needs CRLF line endings"
    block = text.split("# 6. SHORTCUTS:", 1)[1].split("# Marker for scripts", 1)[0]
    assert '[Environment]::GetFolderPath("CommonPrograms")' in block
    assert '$PythonWindowless = Join-Path $InstallDir ".venv\\Scripts\\pythonw.exe"' in block
    assert "$Shortcut.TargetPath = $PythonWindowless" in block and "$Shortcut.TargetPath = $LauncherBat" not in block
    assert '$Shortcut.Arguments = \'"\' + $MainScript + \'"\'' in block
    assert f'$ShortcutDescription = "{launcher.SHORTCUT_DESCRIPTION}"' in block
    assert "New-FrameworkShortcut (Join-Path $StartMenu \"$AppName.lnk\")" in block
    assert "if ($DesktopShortcut) {" in block and "elseif (Test-OwnShortcut $DesktopLink)" in block
    # the question: Enter means yes, a switch and a variable for a run without questions
    assert '"--no-desktop-shortcut"' in text and "$env:LCF_DESKTOP_SHORTCUT" in text and "[J/n]" in text
    assert text.index("$DesktopShortcut = ") < text.index("# 6. SHORTCUTS:")


def test_the_uninstaller_removes_the_start_menu_shortcuts():
    text = UNINSTALL_BAT.read_bytes().decode("utf-8")
    assert 'SET "COMMON_START=%ProgramData%\\Microsoft\\Windows\\Start Menu\\Programs"' in text
    assert 'SET "OWN_START=%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs"' in text
    cleanup = text.split(":CLEANUP_SHORTCUTS", 2)[2]
    for folder in ("%COMMON_START%", "%OWN_START%", "%PUBLIC%\\Desktop", "%USERPROFILE%\\Desktop"):
        assert f'del "{folder}\\!LNK_NAME!"' in cleanup, folder
    assert launcher.SHORTCUT_FILE == "LLM-Cross-Compiler-Framework.lnk" and \
        'SET "SHORTCUT_NAME=LLM-Cross-Compiler-Framework.lnk"' in text
