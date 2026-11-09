"""
LLM Cross-Compiler Framework - Update Manager

An update is a fresh run of the installer: it is idempotent and resolves the newest compatible dependencies on
every run (DECISIONS round 65), so the updater only brings the source checkout forward (git fast-forward) and starts
the installer of this platform from there with the person's flags (for example --offline).

The installed copy (/opt/llm-cross-compiler-framework, or Program Files on Windows) has no .git and cannot update
itself (install.sh refuses to copy onto itself); the installer writes the checkout it ran from into
SOURCE_FILE there, and the updater starts from that checkout. Started from a checkout, the checkout is the source.

`llm-cli update` and Tools > Maintenance > Update Framework... use this module; an AI never starts an update.
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from orchestrator.utils.logging import get_logger
except ImportError:
    import logging

    def get_logger(name):
        return logging.getLogger(name)

# Written by install.sh / install.bat into the installed copy: one line, the checkout the installer ran from.
SOURCE_FILE = ".install_source"

# Linux terminals that can run a command the person watches and answers (the installer asks at its terminal and
# sudo asks for the password), each with the switch before the command.
TERMINALS = (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"), ("xfce4-terminal", "-x"),
             ("mate-terminal", "--"), ("lxterminal", "-e"), ("xterm", "-e"))


class UpdateError(RuntimeError):
    """Why an update cannot start, in words for the person."""


class UpdateManager:
    def __init__(self, app_root: Path):
        self.app_root = Path(app_root)
        self.logger = get_logger("UpdateManager")

    def source_root(self) -> Path | None:
        """The checkout to update from: this folder when it is a checkout, else the one the installer recorded."""
        if (self.app_root / ".git").exists():
            return self.app_root
        try:
            recorded = (self.app_root / SOURCE_FILE).read_text(encoding="utf-8-sig").strip()
        except OSError:
            return None
        source = Path(recorded) if recorded else None
        if source is not None and source.is_absolute() and (source / ".git").exists():
            return source
        return None

    def _source(self) -> Path:
        source = self.source_root()
        if source is None:
            raise UpdateError(f"No git checkout to update from: {self.app_root} is none and names none in "
                              f"{SOURCE_FILE}. Update from the folder you installed from: git pull, then run its "
                              "installer again.")
        return source

    def installer_command(self, *flags: str) -> list[str]:
        """The installer of this platform in the source checkout, with the given flags."""
        source = self._source()
        if platform.system() == "Windows":
            return ["cmd.exe", "/c", str(source / "install.bat"), *flags]
        return ["sudo", "bash", str(source / "install.sh"), *flags]

    def check_for_updates(self) -> int:
        """How many commits the checkout's upstream has that the checkout lacks (0 when none or not knowable)."""
        source = self.source_root()
        if source is None:
            return 0
        try:
            subprocess.run(["git", "fetch", "--quiet"], cwd=source, check=True, capture_output=True)
            res = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=source,
                                 check=True, capture_output=True, text=True)
            return int(res.stdout.strip() or 0)
        except (OSError, subprocess.CalledProcessError, ValueError) as e:
            self.logger.error(f"Update check failed: {e.__class__.__name__}")
            return 0

    def pull(self) -> None:
        """Fast-forwards the source checkout; a checkout with its own commits stays as it is (UpdateError)."""
        source = self._source()
        res = subprocess.run(["git", "pull", "--ff-only", "--quiet"], cwd=source, capture_output=True, text=True,
                             check=False)
        if res.returncode != 0:
            raise UpdateError(f"git pull --ff-only in {source} failed: {(res.stderr or res.stdout).strip()[:300]}")

    def run(self, *flags: str) -> int:
        """Fast-forwards the checkout, then runs the installer. On Linux it runs here and its exit code is returned.
        On Windows it starts in a console of its own and 0 is returned at once: the installer replaces the .venv
        this process runs from, which Windows refuses while the process lives, so the caller exits next."""
        self.pull()
        cmd = self.installer_command(*flags)
        if platform.system() == "Windows":
            subprocess.Popen(cmd, cwd=self._source(),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            return 0
        return subprocess.run(cmd, cwd=self._source(), check=False).returncode

    def terminal_command(self, *flags: str) -> list[str] | None:
        """Linux: `llm-cli update --yes` in a terminal the person sees (the installer asks there, sudo asks for the
        password), or None when no known terminal is installed."""
        cli = [sys.executable, "-m", "orchestrator.cli", "update", "--yes", *flags]
        for name, switch in TERMINALS:
            if shutil.which(name):
                return [name, switch, *cli]
        return None
