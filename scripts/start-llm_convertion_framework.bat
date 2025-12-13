@echo off
setlocal

:: Titel setzen
title LLM Conversion Framework Orchestrator

:: 1. Update Check
:: Wir prüfen, ob ein Updater existiert und führen ihn aus.
:: HINWEIS: Da du 'updater.sh' nanntest, gehen wir davon aus, dass Git Bash installiert ist
:: oder du eigentlich eine .bat meinst. Hier eine robuste Lösung:

if exist "updater.sh" (
    echo [INFO] Suche nach Updates...
    :: Versucht, das sh-Skript via Git Bash auszuführen, falls vorhanden
    if exist "%ProgramFiles%\Git\bin\bash.exe" (
        "%ProgramFiles%\Git\bin\bash.exe" updater.sh
    ) else (
        echo [WARNUNG] updater.sh gefunden, aber keine Git Bash. Überspringe Update.
    )
)

if exist "updater.bat" (
    echo [INFO] Suche nach Updates...
    call updater.bat
)

:: 2. GUI Starten
echo [INFO] Starte Orchestrator GUI...

:: Hier wird angenommen, dass 'python_embed' und 'orchestrator' im gleichen Ordner liegen
:: wie diese .bat Datei (da sie ja ins Root kopiert wurde).
if exist "python_embed\python.exe" (
    .\python_embed\python.exe orchestrator\main.py
) else (
    echo [FEHLER] Python Umgebung nicht gefunden (python_embed\python.exe fehlt).
    echo Bitte sicherstellen, dass das Framework korrekt installiert ist.
    pause
)

:: Fenster schließt sich automatisch, wenn die GUI beendet wird, 
:: es sei denn, es gab einen Crash (dann bleibt es kurz offen).
if %ERRORLEVEL% NEQ 0 pause
