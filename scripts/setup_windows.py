#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Windows GUI Installer (v2.20 FINAL ROBUST)
DIREKTIVE: Goldstandard. FEHLERBEHANDLUNG IMPLEMENTIERT.
  1. FIX: Pip-Installation prüft nun returncode und gibt STDERR bei Fehler aus.
  2. FIX: PyYAML wird explizit in der App-Umgebung installiert/geprüft.
  3. FIX: Launcher wird generiert und zeigt korrekt auf .venv\Scripts\python.exe.
"""

import os
import sys
import shutil
import subprocess
import threading
import time
import ctypes
import winreg
import webbrowser
# pyyaml ist im Installer-VENV vorhanden (durch install.bat)
try:
    import yaml
except ImportError:
    pass
from pathlib import Path
# pythoncom fuer Shortcuts im Thread
try:
    import pythoncom
except ImportError:
    pass

# Dependencies (vom Launcher vorinstalliert oder im Host-Environment erwartet)
try:
    import psutil
    import requests
    import winshell
    from win32com.client import Dispatch
except ImportError:
    pass

# GUI Imports
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText


# ============================================================================
# 1. KONSTANTEN DEFINITION
# ============================================================================

APP_NAME = "LLM-Conversion-Framework"
APP_TITLE = "LLM Conversion Framework"
LAUNCHER_FILE_NAME = "start-llm_convertion_framework.bat"

SOURCE_DIR = Path(__file__).resolve().parent.parent

# Pfade
DEFAULT_APP_PATH = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / APP_NAME
DEFAULT_DATA_PATH = Path("C:/Users/Public/Documents") / APP_NAME 

# Config Pointer
CHECKFILE_DIR = DEFAULT_DATA_PATH 
CHECKFILE_PATH = CHECKFILE_DIR / "checkfile.txt"

# Ordner-Struktur
CODE_DIRS = ["orchestrator", "configs", "assets", "Docker Setup"] # App Path
DATA_TEMPLATES_DIRS = ["targets", "models"] # Data Path

INCLUDE_APP_FILES = ["pyproject.toml", "poetry.lock", "requirements.txt", ".gitignore"]
INCLUDE_LAUNCHER_DOCS = ["README.md", "LICENSE.txt"]
IGNORE_PATTERNS = ["__pycache__", "*.pyc", ".git", ".venv", "venv", "dist", "build", ".installer_venv"]

# ============================================================================
# 2. GUI KLASSE
# ============================================================================

class InstallerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} Installer v2.20")
        self.geometry("800x700")
        self.resizable(True, True)
        
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.docker_ok = False
        self._init_ui()
        self.after(500, self._check_docker_background)

    def _init_ui(self):
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        header = ttk.Frame(main_frame)
        header.pack(fill=tk.X, pady=(0, 15))
        lbl_title = ttk.Label(header, text=f"Install {APP_TITLE}", font=("Segoe UI", 18, "bold"))
        lbl_title.pack(anchor=tk.W)
        
        self.lbl_status = ttk.Label(main_frame, text="Checking System Requirements...", foreground="blue")
        self.lbl_status.pack(fill=tk.X, pady=(0, 10))

        # App Path
        grp_app = ttk.LabelFrame(main_frame, text="Application Path (Code & VENV)", padding="10")
        grp_app.pack(fill=tk.X, pady=5)
        self.var_app_path = tk.StringVar(value=str(DEFAULT_APP_PATH))
        e_app = ttk.Entry(grp_app, textvariable=self.var_app_path)
        e_app.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(grp_app, text="Browse...", command=lambda: self._browse(self.var_app_path)).pack(side=tk.RIGHT)
        
        # Data Path
        grp_data = ttk.LabelFrame(main_frame, text="Data & Output Path (Artefakte)", padding="10")
        grp_data.pack(fill=tk.X, pady=5)
        self.var_data_path = tk.StringVar(value=str(DEFAULT_DATA_PATH))
        e_data = ttk.Entry(grp_data, textvariable=self.var_data_path)
        e_data.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(grp_data, text="Browse...", command=lambda: self._browse(self.var_data_path)).pack(side=tk.RIGHT)
        
        # Options
        opt_frame = ttk.Frame(main_frame)
        opt_frame.pack(fill=tk.X, pady=10)
        self.var_desktop = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="Create Desktop Shortcuts", variable=self.var_desktop).pack(anchor=tk.W)
        
        # Log
        log_frame = ttk.LabelFrame(main_frame, text="Installation Log", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.log_text = ScrolledText(log_frame, height=12, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Progress
        self.progress = ttk.Progressbar(main_frame, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.btn_install = ttk.Button(btn_frame, text="Install Now", command=self._start_install, state='disabled')
        self.btn_install.pack(side=tk.RIGHT)
        self.btn_cancel = ttk.Button(btn_frame, text="Cancel", command=self.destroy)
        self.btn_cancel.pack(side=tk.RIGHT, padx=10)

    def _browse(self, var):
        path_str = var.get()
        p = Path(path_str) 
        initial_dir = p.parent 
        if not initial_dir.exists():
            initial_dir = Path.home()
        
        d = filedialog.askdirectory(initialdir=str(initial_dir))
        if d:
            final_path = Path(d) / p.name
            var.set(str(final_path))

    def log(self, msg):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, f"{msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
        self.update_idletasks()

    def _check_docker_background(self):
        threading.Thread(target=self._check_docker, daemon=True).start()

    def _check_docker(self):
        self.log("Checking Docker environment...")
        if 'psutil' not in sys.modules: return

        docker_exe = shutil.which("docker")
        if not docker_exe:
            self.lbl_status.config(text="Docker not found (Warning)", foreground="orange")
            self.log("WARNUNG: Docker nicht im PATH gefunden.")
            return

        try:
            subprocess.run(["docker", "--version"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.docker_ok = True
            self.lbl_status.config(text="System Ready.", foreground="green")
            self.log("Docker check passed.")
        except:
            pass

    def _create_launcher_content(self) -> str:
        """Erstellt den Inhalt fuer eine funktionierende Batch-Datei."""
        return f"""@echo off
setlocal
title LLM Conversion Framework

:: Wechsel in das Installationsverzeichnis
cd /d "%~dp0"

:: Pfad zum Python Interpreter im VENV (KORRIGIERT: .venv statt python_embed)
set "PYTHON_EXE=.venv\\Scripts\\python.exe"
set "MAIN_SCRIPT=orchestrator\\main.py"

if not exist "%PYTHON_EXE%" (
    echo [FEHLER] Python Environment nicht gefunden!
    echo Erwartet in: %CD%\\.venv
    pause
    exit /b 1
)

echo [INFO] Starte Framework...
"%PYTHON_EXE%" "%MAIN_SCRIPT%"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Das Programm wurde unerwartet beendet (Code %ERRORLEVEL%).
    pause
)
"""

    def _start_install(self):
        target_dir = Path(self.var_app_path.get())
        data_dir = Path(self.var_data_path.get())
        
        if not target_dir.is_absolute() or not data_dir.is_absolute():
             messagebox.showerror("Fehler", "Pfade muessen absolut sein!")
             return

        self.btn_install.config(state='disabled')
        self.btn_cancel.config(state='disabled')
        self.progress['value'] = 0
        threading.Thread(target=self._install_process, args=(target_dir, data_dir), daemon=True).start()

    def _run_pip(self, pip_exe, args, cwd):
        """Hilfsfunktion fuer robustes Pip-Install mit Fehlererkennung."""
        cmd = [str(pip_exe), "install"] + args
        self.log(f"  > pip install {' '.join(args)}")
        
        # FIX: capture_output=True und text=True, um Fehlermeldungen zu lesen
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        
        if res.returncode != 0:
            self.log(f"  [PIP FEHLER] Code {res.returncode}")
            self.log(f"  STDERR: {res.stderr}")
            raise Exception(f"Abhaengigkeit konnte nicht installiert werden: {' '.join(args)}\n\nDetails: {res.stderr}")
        
        return True

    def _install_process(self, app_dir: Path, data_dir: Path):
        if 'pythoncom' in sys.modules:
            pythoncom.CoInitialize()

        try:
            self.log(f"--- Starte Installation ---")
            self.log(f"App:  {app_dir}")
            self.log(f"Data: {data_dir}")
            
            # 1. Verzeichnisse
            if not app_dir.exists(): app_dir.mkdir(parents=True, exist_ok=True)
            if not data_dir.exists(): data_dir.mkdir(parents=True, exist_ok=True)
            
            # 2. Code kopieren
            self.log("Kopiere Programmdateien...")
            total_items = len(CODE_DIRS) + len(DATA_TEMPLATES_DIRS) + len(INCLUDE_APP_FILES) + 5
            current = 0
            
            for item in CODE_DIRS:
                src = SOURCE_DIR / item
                dst = app_dir / item
                if src.exists():
                    if dst.exists(): shutil.rmtree(dst)
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*IGNORE_PATTERNS))
                current += 1
                self.progress['value'] = (current / total_items) * 30
            
            for item in INCLUDE_APP_FILES:
                src = SOURCE_DIR / item
                dst = app_dir / item
                if src.exists(): shutil.copy2(src, dst)
                current += 1

            for item in INCLUDE_LAUNCHER_DOCS:
                src = SOURCE_DIR / item
                dst = app_dir / item
                if src.exists(): shutil.copy2(src, dst)
            
            # 3. LAUNCHER ERSTELLEN (Generieren statt Kopieren!)
            self.log(f"Erstelle Start-Skript: {LAUNCHER_FILE_NAME}")
            launcher_path = app_dir / LAUNCHER_FILE_NAME
            with open(launcher_path, "w") as f:
                f.write(self._create_launcher_content())
            current += 1

            # Uninstaller kopieren
            src_uninst = SOURCE_DIR / "scripts" / "Uninstall-LLM-Conversion-Framework.bat"
            if src_uninst.exists():
                shutil.copy2(src_uninst, app_dir / "Uninstall-LLM-Conversion-Framework.bat")
            
            self.progress['value'] = 40
            
            # 4. Daten-Templates kopieren
            self.log("Kopiere Daten-Templates...")
            for item in DATA_TEMPLATES_DIRS:
                src = SOURCE_DIR / item
                dst = data_dir / item
                if src.exists():
                    if dst.exists(): shutil.rmtree(dst)
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*IGNORE_PATTERNS))
                current += 1
                self.progress['value'] = (current / total_items) * 50

            # 5. VENV & Dependencies (ROBUST)
            self.log("Erstelle Python Environment (kann dauern)...")
            venv_path = app_dir / ".venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv_path), "--clear"], 
                         check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            
            pip_exe = venv_path / "Scripts" / "pip.exe"
            cwd = str(app_dir)
            
            self.log("Installiere Requirements...")
            
            # FIX: Upgrade Pip first
            self._run_pip(pip_exe, ["--upgrade", "pip"], cwd)

            # FIX: Install PyYAML explizit (Sicherheitsnetz)
            self._run_pip(pip_exe, ["PyYAML"], cwd)

            req_file = app_dir / "requirements.txt"
            if req_file.exists():
                # FIX: Fehlerbehandlung integriert in _run_pip
                self._run_pip(pip_exe, ["-r", "requirements.txt"], cwd)
                # Install package itself
                self._run_pip(pip_exe, ["-e", "."], cwd)
            else:
                self.log("WARNUNG: requirements.txt nicht gefunden!")
            
            self.progress['value'] = 80

            # 6. Config
            self.log("Konfiguriere Pfade...")
            (data_dir / "output").mkdir(parents=True, exist_ok=True)
            (data_dir / "logs").mkdir(parents=True, exist_ok=True)
            (data_dir / "cache").mkdir(parents=True, exist_ok=True)
            
            config_file = app_dir / "configs" / "user_config.yml" 
            config_data = {}
            if config_file.exists():
                try:
                    with open(config_file, 'r') as f: config_data = yaml.safe_load(f) or {}
                except: pass
            
            config_data["output_dir"] = str(data_dir / "output")
            config_data["logs_dir"] = str(data_dir / "logs")
            config_data["cache_dir"] = str(data_dir / "cache")
            config_data["targets_dir"] = str(data_dir / "targets")
            config_data["models_dir"] = str(data_dir / "models")
            
            with open(config_file, "w") as f:
                yaml.dump(config_data, f, default_flow_style=False)

            # 7. Checkfile
            self.log("Registriere Installation...")
            if not CHECKFILE_DIR.exists():
                CHECKFILE_DIR.mkdir(parents=True, exist_ok=True)
            
            # Reset Attributes falls vorhanden
            if CHECKFILE_PATH.exists():
                try:
                    ctypes.windll.kernel32.SetFileAttributesW(str(CHECKFILE_PATH), 0x00)
                except: pass

            with open(CHECKFILE_PATH, "w") as f:
                f.write(f'Path="{app_dir}"') 
            
            try:
                ctypes.windll.kernel32.SetFileAttributesW(str(CHECKFILE_PATH), 0x02) # Hidden
            except: pass

            self.progress['value'] = 90

            # 8. Shortcuts
            if self.var_desktop.get():
                self.log("Erstelle Verknuepfungen...")
                self._create_shortcuts(app_dir, data_dir)

            self.progress['value'] = 100
            self.log("FERTIG!")
            
            messagebox.showinfo("Installation Erfolgreich", 
                                f"Installation abgeschlossen.\n\n"
                                f"App: {app_dir}\n"
                                f"Daten: {data_dir}\n\n"
                                f"Sie koennen das Programm nun ueber den Desktop starten.")
            self.destroy()
            
        except Exception as e:
            self.log(f"CRITICAL ERROR: {e}")
            messagebox.showerror("Fehler", str(e))
            self.btn_install.config(state='normal')
            self.btn_cancel.config(state='normal')
        finally:
            if 'pythoncom' in sys.modules:
                pythoncom.CoUninitialize()

    def _create_shortcuts(self, app_dir: Path, data_dir: Path):
        if 'winshell' not in sys.modules: return
        
        desktop = winshell.desktop()
        shell = Dispatch('WScript.Shell')
        
        # 1. App Shortcut
        lnk_app = os.path.join(desktop, f"{APP_TITLE}.lnk")
        target = str(app_dir / LAUNCHER_FILE_NAME)
        
        sc = shell.CreateShortCut(lnk_app)
        sc.Targetpath = target
        sc.WorkingDirectory = str(app_dir)
        sc.Description = "Startet das Framework"
        icon = app_dir / "assets" / "LLM-Builder.ico"
        if icon.exists(): sc.IconLocation = str(icon)
        sc.save()
        
        # 2. Data Shortcut
        lnk_data = os.path.join(desktop, f"{APP_TITLE} Data.lnk")
        sc = shell.CreateShortCut(lnk_data)
        sc.Targetpath = str(data_dir)
        sc.Description = "Hier liegen Modelle und Outputs"
        icon = app_dir / "assets" / "setup_LLM-Builder.ico"
        if icon.exists(): sc.IconLocation = str(icon)
        sc.save()

if __name__ == "__main__":
    app = InstallerGUI()
    app.mainloop()
