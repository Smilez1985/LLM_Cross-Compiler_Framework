"""
LLM Cross-Compiler Framework - Update Manager

An update is a fresh run of the installer: it is idempotent and resolves the newest compatible dependencies on
every run (DECISIONS round 65), so the updater only brings the checkout forward (git fast-forward) and starts the
installer of this platform with the person's flags (for example --offline).
"""

import platform
import subprocess
from pathlib import Path

try:
    from orchestrator.utils.logging import get_logger
except ImportError:
    import logging

    def get_logger(name):
        return logging.getLogger(name)


class UpdateManager:
    def __init__(self, app_root: Path):
        self.app_root = Path(app_root)
        self.logger = get_logger("UpdateManager")

    def installer_command(self, *flags: str) -> list[str]:
        """The installer of this platform, started from the checkout with the given flags."""
        if platform.system() == "Windows":
            return ["cmd.exe", "/c", str(self.app_root / "install.bat"), *flags]
        return ["sudo", "bash", str(self.app_root / "install.sh"), *flags]

    def check_for_updates(self) -> bool:
        """True when the checkout's upstream has commits this checkout lacks."""
        if not (self.app_root / ".git").exists():
            return False
        try:
            subprocess.run(["git", "fetch", "--quiet"], cwd=self.app_root, check=True, capture_output=True)
            res = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=self.app_root,
                                 check=True, capture_output=True, text=True)
            return int(res.stdout.strip() or 0) > 0
        except (OSError, subprocess.CalledProcessError, ValueError) as e:
            self.logger.error(f"Update check failed: {e.__class__.__name__}")
            return False

    def run(self, *flags: str) -> int:
        """Fast-forward the checkout (when it is one), then run the installer; returns the installer's exit code."""
        if (self.app_root / ".git").exists():
            subprocess.run(["git", "pull", "--ff-only", "--quiet"], cwd=self.app_root, check=True)
        return subprocess.run(self.installer_command(*flags), cwd=self.app_root, check=False).returncode
