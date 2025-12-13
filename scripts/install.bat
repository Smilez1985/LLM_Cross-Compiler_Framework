@echo off
setlocal EnableDelayedExpansion

:: ===================================================
:: LLM Conversion Framework - Installer Launcher (Final)
:: Startet die GUI-Installation (setup_windows.py)
:: ===================================================

:: 1. Admin-Rechte prüfen (Notwendig fuer den Python Installer)
openfiles >nul 2>&1
if %errorlevel% NEQ 0 (
    echo.
    echo [ACHTUNG] Bitte Rechtsklick auf die Datei und "Als Administrator ausfuehren".
    echo Das ist notwendig, damit der Installer Schreibrechte fuer C:\Program Files hat.
    echo.
    pause
    exit /b
)

:: 2. Pfade setzen (Root Verzeichnis)
pushd "%~dp0.."
set "REPO_DIR=%CD%"
popd
set "INSTALLER_VENV=%REPO_DIR%\.installer_venv"
set "SETUP_SCRIPT=%REPO_DIR%\scripts\setup_windows.py"

:: Globaler Pfad fuer das Checkfile (Der Pointer)
set "GLOBAL_DATA_DIR=C:\Users\Public\Documents\llm_conversion_framework"

cls
echo ===================================================
echo      LLM Conversion Framework - Setup
echo ===================================================
echo.
echo [INFO] Framework Root: "%REPO_DIR%"
echo [INFO] Global Data:    "%GLOBAL_DATA_DIR%"
echo.

:: 3. Python Check & Bootstrapping
echo [1/3] Pruefe Python Umgebung...
python --version >nul 2>&1
if !errorlevel! NEQ 0 (
    echo [WARNUNG] Python fehlt. Versuche Installation via Winget...
    winget install -e --id Python.Python.3.11 --scope machine --accept-source-agreements --accept-package-agreements
    if !errorlevel! NEQ 0 (
        echo [FEHLER] Python Installation fehlgeschlagen.
        pause
        exit /b
    )
    echo [INFO] Python installiert. Bitte Skript neu starten!
    pause
    exit /b
)

:: 4. Installer-Umgebung vorbereiten (Mini-VENV für Setup)
echo.
echo [2/3] Bereite Installer-Umgebung vor...

if not exist "%INSTALLER_VENV%" (
    echo       Erstelle isolierte Umgebung...
    python -m venv "%INSTALLER_VENV%"
)

:: WICHTIG: Installiere PyYAML (yaml) ZUSÄTZLICH
echo       Lade Hilfs-Pakete fuer den Installer (psutil, pyyaml, etc.)...
"%INSTALLER_VENV%\Scripts\python.exe" -m pip install psutil requests pywin32 winshell pyyaml >nul 2>&1

:: 5. GUI Installer starten
echo.
echo [3/3] Starte grafischen Installer...
echo.

if exist "%SETUP_SCRIPT%" (
    "%INSTALLER_VENV%\Scripts\python.exe" "%SETUP_SCRIPT%"
) else (
    echo [FEHLER] Konnte 'scripts\setup_windows.py' nicht finden!
    echo Pfad: %SETUP_SCRIPT%
    pause
)
