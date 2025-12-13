@echo off
setlocal EnableDelayedExpansion

:: ============================================================
:: LLM Cross-Compiler Framework - Windows Launcher
:: DIREKTIVE: Goldstandard, Robustheit, Pfad-Unabhängigkeit.
:: ============================================================

:: Sicherstellen, dass wir im Verzeichnis des Skripts arbeiten
:: (Wichtig bei "Als Administrator ausführen" oder Verknüpfungen)
cd /d "%~dp0"

title LLM Conversion Framework Orchestrator

:: --- BANNER ---
cls
echo ========================================================
echo   LLM Cross-Compiler Framework - Orchestrator
echo ========================================================
echo.

:: --- 1. UPDATE CHECK ---
:: Prüft auf Updates vor dem Start
if exist "updater.bat" (
    echo [INFO] Suche nach Updates...
    call updater.bat
) else if exist "updater.sh" (
    :: Fallback für Git-Umgebungen auf Windows
    if exist "%ProgramFiles%\Git\bin\bash.exe" (
        echo [INFO] Führe Updater via Git Bash aus...
        "%ProgramFiles%\Git\bin\bash.exe" updater.sh
    )
)

:: --- 2. ENVIRONMENT SETUP ---
echo [INFO] Initialisiere Umgebung...

set "PYTHON_EXE="

:: Priorität 1: Embedded Python (Portable)
if exist "python_embed\python.exe" (
    set "PYTHON_EXE=python_embed\python.exe"
    echo [INFO] Nutze Embedded Python.
) else (
    :: Priorität 2: Virtual Environment (Dev Mode)
    if exist ".venv\Scripts\python.exe" (
        set "PYTHON_EXE=.venv\Scripts\python.exe"
        echo [INFO] Nutze Virtual Environment (.venv).
    ) else (
        :: Priorität 3: System Python (Fallback)
        python --version >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            set "PYTHON_EXE=python"
            echo [WARNUNG] Kein lokales Python gefunden. Nutze System-Python.
        )
    )
)

if not defined PYTHON_EXE (
    echo.
    echo [FEHLER] Keine Python-Laufzeitumgebung gefunden!
    echo Bitte sicherstellen, dass der Ordner 'python_embed' existiert
    echo oder ein '.venv' eingerichtet ist.
    echo.
    pause
    exit /b 1
)

:: Setze PYTHONPATH, damit Imports wie 'from orchestrator...' funktionieren
set "PYTHONPATH=%~dp0"

:: --- 3. GUI START ---
echo [INFO] Starte Orchestrator GUI...
echo.

"%PYTHON_EXE%" orchestrator\main.py

:: --- 4. EXIT HANDLING ---
:: Falls Python mit Fehler abstürzt, Fenster offen lassen
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [FEHLER] Der Orchestrator wurde unerwartet beendet (Code: %ERRORLEVEL%).
    pause
)

endlocal
