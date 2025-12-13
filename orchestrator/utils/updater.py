#!/usr/bin/env python3
"""Update Manager - Secure Process Launch"""
import sys
import subprocess
import logging
from pathlib import Path
import tempfile

class UpdateManager:
    def __init__(self, app_root: Path):
        self.app_root = app_root
        self.logger = logging.getLogger(__name__)
        self.CRITICAL_FILES = ["scripts/setup_windows.py", "pyproject.toml"]

    def check_for_updates(self) -> bool:
        try:
            git_dir = self.app_root / ".git"
            if not git_dir.exists(): return False
            subprocess.run(["git", "fetch"], cwd=self.app_root, check=True, capture_output=True)
            res = subprocess.run(["git", "status", "-uno"], cwd=self.app_root, capture_output=True, text=True)
            return "Your branch is behind" in res.stdout
        except: return False

    def _is_major_update(self):
        try:
            res = subprocess.run(["git", "diff", "--name-only", "HEAD", "@{u}"], cwd=self.app_root, capture_output=True, text=True)
            return any(f in res.stdout for f in self.CRITICAL_FILES)
        except: return True

    def perform_update_and_restart(self):
        try:
            major = self._is_major_update()
            exe_name = "LLM-Builder.exe"
            setup_name = "Setup_LLM-Framework.exe"
            is_frozen = getattr(sys, 'frozen', False)
            batch_file = Path(tempfile.gettempdir()) / "llm_updater.bat"
            
            cmd_next = f'start "" "{setup_name}" --update' if major else (f'start "" "{exe_name}"' if is_frozen else f'python "{sys.argv[0]}"')
            
            # --- HIER IST DIE ÄNDERUNG ---
            # Wir fügen 'copy /Y scripts\start-llm_convertion_framework.bat .' ein.
            # Das aktualisiert den Launcher im Root-Verzeichnis direkt nach dem git pull.
            batch_content = (
                f'@echo off\n'
                f'cd /d "{self.app_root}"\n'
                f'git pull\n'
                f'copy /Y "scripts\\start-llm_convertion_framework.bat" .\n'
                f'{cmd_next}\n'
                f'del "%~f0"\n'
                f'exit\n'
            )

            with open(batch_file, "w") as f:
                f.write(batch_content)
            
            if sys.platform == "win32":
                subprocess.Popen(["cmd.exe", "/c", str(batch_file)], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                # Für Linux/Mac müsste man theoretisch auch kopieren, falls dort ein Shell-Skript genutzt wird
                subprocess.Popen(["/bin/bash", "-c", f"cd {self.app_root} && git pull && ./llm-builder"])
            
            sys.exit(0)
        except Exception as e:
            self.logger.error(f"Update failed: {e}")
            raise e
