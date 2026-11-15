"""
The host guard of the test suite (decided 2026-11-15, DESIGN round 100): a test must not change the computer it runs on.

A test of the installer once installed 7-Zip as root on the host that ran it. Stubbing a program on a test's own PATH
stays the rule; this guard checks it. It looks at every program a test starts through `subprocess.Popen` (so also
`subprocess.run`, `check_call` and `check_output`) and warns when it is a real package manager that installs, removes or
upgrades something, or a real service command that starts, stops or enables a service of the host. The warning names the
test and the command. It never fails the test and never stops the command (the maintainer's decision: warn); what the
warning shows is the test to fix.

A program that lies in a temporary folder of the test run (a stub the test made) is never a real one. A program that is
not found at all cannot change anything. What this does not see: a program started by `os.system`, `os.exec*` or a
library that loads its own code; a file written into a system folder by Python itself.

The logic lives here, without pytest, so that tests/test_host_guard.py can check it; tests/conftest.py installs it for
the whole session.
"""

import os
import shlex
import shutil
import tempfile
import warnings
from typing import Any, Callable, Iterable, List, Optional

# The programs that change what a computer has installed, and the words that make them do it.
PACKAGE_MANAGERS = {
    "apt": ("install", "remove", "purge", "upgrade", "full-upgrade", "dist-upgrade", "autoremove", "reinstall"),
    "apt-get": ("install", "remove", "purge", "upgrade", "dist-upgrade", "autoremove", "reinstall"),
    "dpkg": ("-i", "--install", "-r", "--remove", "-P", "--purge", "--configure"),
    "dnf": ("install", "remove", "erase", "upgrade", "update", "reinstall", "autoremove"),
    "yum": ("install", "remove", "erase", "upgrade", "update", "reinstall"),
    "zypper": ("install", "in", "remove", "rm", "update", "up", "dist-upgrade", "dup"),
    "pacman": ("-S", "-Sy", "-Syu", "-R", "-Rs", "-U", "--sync", "--remove", "--upgrade"),
    "apk": ("add", "del", "upgrade"),
    "brew": ("install", "uninstall", "remove", "upgrade", "reinstall"),
    "snap": ("install", "remove", "refresh"),
    "flatpak": ("install", "uninstall", "update"),
    "winget": ("install", "uninstall", "upgrade"),
    "choco": ("install", "uninstall", "upgrade"),
    "scoop": ("install", "uninstall", "update"),
}
# The programs that start or stop a service of the host, and the words that make them do it.
SERVICE_COMMANDS = {
    "systemctl": ("start", "stop", "restart", "reload", "enable", "disable", "mask", "unmask", "daemon-reload",
                  "try-restart", "reload-or-restart"),
    "service": ("start", "stop", "restart", "reload", "force-reload"),
    "rc-service": ("start", "stop", "restart"),
    "launchctl": ("load", "unload", "bootstrap", "bootout", "start", "stop", "kickstart"),
    "sc": ("start", "stop", "create", "delete", "config"),
    "net": ("start", "stop"),
}
# Programs that are themselves a service of the host when a test starts them for real.
DAEMONS = ("sshd", "dockerd")
# Programs that only hand over to the one after them.
WRAPPERS = ("sudo", "doas", "pkexec", "env", "nice", "ionice", "timeout", "command", "nohup")


class HostChangeWarning(UserWarning):
    """A test started a real program that changes the computer it runs on."""


def _name(program: str) -> str:
    base = os.path.basename(str(program)).lower()
    return base[:-4] if base.endswith(".exe") else base


def _words(args: Any) -> List[str]:
    """The words of a command as `Popen` gets it: a list, a path, or (with a shell) one string."""
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        try:
            return shlex.split(text, posix=(os.name != "nt"))
        except ValueError:
            return text.split()
    return [os.fsdecode(a) for a in args]


def _unwrapped(words: List[str]) -> List[str]:
    """The command behind `sudo`, `env VAR=1` and the like (their options are skipped, not understood)."""
    words = list(words)
    while words and _name(words[0]) in WRAPPERS:
        words.pop(0)
        while words and (words[0].startswith("-") or ("=" in words[0] and not words[0].startswith("/"))):
            words.pop(0)
    return words


def stub_roots(extra: Iterable[Any] = ()) -> List[str]:
    """The folders whose programs are a test's own stubs: the temporary folder of this run and what the caller adds."""
    roots = [tempfile.gettempdir(), *[str(e) for e in extra]]
    return [os.path.realpath(r) for r in roots if r]


def is_stub(program: str, roots: Iterable[str], which: Callable[[str], Optional[str]] = shutil.which) -> Optional[bool]:
    """True for a program inside one of `roots`, False for another one found, None when it is not found at all."""
    found = which(program)
    if not found:
        return None
    real = os.path.realpath(found)
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def changes_host(args: Any, roots: Iterable[str], which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """Why this command would change the host ("" when it would not, or when its program is a stub or not there)."""
    words = _unwrapped(_words(args))
    if not words:
        return ""
    program = _name(words[0])
    rest = words[1:]
    reason = ""
    if program in PACKAGE_MANAGERS and any(w in PACKAGE_MANAGERS[program] for w in rest):
        reason = f"{program} changes the installed packages"
    elif program in SERVICE_COMMANDS and any(w in SERVICE_COMMANDS[program] for w in rest):
        reason = f"{program} changes a service of the host"
    elif program in DAEMONS:
        reason = f"{program} is a service of the host"
    if not reason or is_stub(words[0], roots, which) is not False:
        return ""
    return reason


def command_text(args: Any, limit: int = 160) -> str:
    text = " ".join(_words(args))
    return text if len(text) <= limit else text[:limit] + " ..."


def guarded_init(original: Callable, roots: Iterable[str], which: Callable[[str], Optional[str]] = shutil.which,
                 warn: Callable[..., None] = warnings.warn) -> Callable:
    """`Popen.__init__` with the check in front of it: the command always runs, a real host-changing one warns first."""
    roots = list(roots)

    def init(self: Any, args: Any, *a: Any, **k: Any) -> None:
        try:
            reason = changes_host(args, roots, which)
            if reason:
                test = os.environ.get("PYTEST_CURRENT_TEST", "a test").split(" (")[0]
                warn(HostChangeWarning(f"{test} starts a real program that changes this computer: {reason}: "
                                       f"{command_text(args)}. Put a stub of it on the test's PATH instead."),
                     stacklevel=3)
        except Exception:  # noqa: BLE001 - the guard never breaks a test
            pass
        return original(self, args, *a, **k)
    init.__wrapped__ = original   # type: ignore[attr-defined]
    return init


def install(extra_roots: Iterable[Any] = ()) -> Callable[[], None]:
    """Puts the check in front of `subprocess.Popen`; returns the function that takes it out again."""
    import subprocess
    original = subprocess.Popen.__init__
    subprocess.Popen.__init__ = guarded_init(original, stub_roots(extra_roots))   # type: ignore[method-assign]

    def remove() -> None:
        subprocess.Popen.__init__ = original   # type: ignore[method-assign]
    return remove


__all__ = ["DAEMONS", "HostChangeWarning", "PACKAGE_MANAGERS", "SERVICE_COMMANDS", "changes_host", "command_text",
           "guarded_init", "install", "is_stub", "stub_roots"]
