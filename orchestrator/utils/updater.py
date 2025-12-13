#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Update Manager (v2.3.0)
DIREKTIVE: Goldstandard, Secure Process Launch.

Zweck:
Verwaltet Self-Updates via Git.
Erkennt "Major Updates" (die ein Setup benötigen).
Startet die Anwendung nach dem Update neu und aktualisiert den Launcher.

Updates v2.3.0:
- Integrated centralized logging.
- Added automatic launcher sync (copy start-script).
- Robust path handling.
"""

import sys
import subprocess
import tempfile
import platform
from pathlib import Path
from typing import List

# Central Logging
try:
    from orchestrator.utils.logging import get_logger
except ImportError:
    import logging
    def get_logger(name): return logging.getLogger(name)

class UpdateManager:
    def __init__(self, app_root: Path):
        self.app_root = app_root
        self.logger = get_logger("UpdateManager")
        # Dateien, die Änderungen an der Umgebung/Dependencies signalisieren
        self.CRITICAL_FILES = ["scripts/setup_windows.py", "pyproject.toml", "requirements.txt"]

    def check_for_updates(self) -> bool:
        """Prüft via Git Fetch, ob Updates vorliegen."""
        try:
            git_dir = self.app_root / ".git"
            if not git_dir.exists():
                self.logger.warning("Kein Git-Repository gefunden. Updates deaktiviert.")
                return False
                
            # Fetch ohne Merge (sicher)
            subprocess.run(["git", "fetch"], cwd=self.app_root, check=True, capture_output=True)
            
            # Status prüfen
            res = subprocess.run(["git", "status", "-uno"], cwd=self.app_root, capture_output=True, text=True)
            
            if "Your branch is behind" in res.stdout:
                self.logger.info("Update verfügbar.")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Fehler beim Update-Check: {e}")
            return False

    def _is_major_update(self) -> bool:
        """Prüft, ob kritische Systemdateien geändert wurden."""
        try:
            # Diff zwischen HEAD und Upstream
            res = subprocess.run(["git", "diff", "--name-only", "HEAD", "@{u}"], cwd=self.app_root, capture_output=True, text=True)
            changed_files = res.stdout.splitlines()
            
            for cf in changed_files:
                if any(crit in cf for crit in self.CRITICAL_FILES):
                    self.logger.info(f"Major Update erkannt (geändert: {cf})")
                    return True
            return False
        except:
            # Im Zweifel lieber Full Setup
            return True

    def perform_update_and_restart(self):
        """
        Führt git pull aus, kopiert den Launcher und startet neu.
        Nutzt ein temporäres Batch-File, um Files im laufenden Betrieb zu tauschen (Windows).
        """
        try:
            self.logger.info("Starte Update-Prozess...")
            major = self._is_major_update()
            
            # Bestimme das nächste Executable
            exe_name = "LLM-Builder.exe"
            setup_name = "Setup_LLM-Framework.exe"
            is_frozen = getattr(sys, 'frozen', False)
            
            # Startbefehl für NACH dem Update
            if major:
                # Bei Major Updates: Setup ausführen
                cmd_next = f'start "" "{setup_name}" --update'
            else:
                # Normaler Neustart
                if is_frozen:
                    cmd_next = f'start "" "{exe_name}"'
                else:
                    # Dev Mode: Main Script neu starten
                    cmd_next = f'python "{sys.argv[0]}"'
            
            # Pfad zur Batch-Datei
            batch_file = Path(tempfile.gettempdir()) / "llm_updater.bat"
            
            # WICHTIG: Launcher Sync
            # Wir kopieren das Start-Skript aus scripts/ ins Root, damit der Entrypoint aktuell bleibt.
            launcher_sync_cmd = 'copy /Y "scripts\\start-llm_convertion_framework.bat" .'
            
            # Batch Inhalt erstellen
            # 1. Wait (optional, falls File-Locks existieren)
            # 2. Git Pull
            # 3. Launcher kopieren
            # 4. App neu starten
            # 5. Self-Delete
            batch_content = (
                f'@echo off\n'
                f'timeout /t 1 /nobreak >nul\n'
                f'cd /d "{self.app_root}"\n'
                f'echo Updating Repository...\n'
                f'git pull\n'
                f'echo Syncing Launcher...\n'
                f'{launcher_sync_cmd}\n'
                f'echo Restarting Application...\n'
                f'{cmd_next}\n'
                f'(goto) 2>nul & del "%~f0"\n'
                f'exit\n'
            )

            with open(batch_file, "w") as f:
                f.write(batch_content)
            
            self.logger.info(f"Update-Skript erstellt: {batch_file}")
            
            if platform.system() == "Windows":
                # Starte die Batch detached
                subprocess.Popen(
                    ["cmd.exe", "/c", str(batch_file)], 
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                # Linux/Mac Fallback (einfacher Shell-Chain)
                # Hier kopieren wir das .sh Skript falls vorhanden
                cmd_linux = (
                    f"cd {self.app_root} && "
                    f"git pull && "
                    f"cp -f scripts/start.sh . 2>/dev/null || true && "
                    f"./llm-builder"
                )
                subprocess.Popen(["/bin/bash", "-c", cmd_linux])
            
            # Sofort beenden, damit Files freigegeben werden
            sys.exit(0)
            
        except Exception as e:
            self.logger.error(f"Update fehlgeschlagen: {e}")
            raise e
