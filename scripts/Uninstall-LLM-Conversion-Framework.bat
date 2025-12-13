@echo off
TITLE LLM Cross-Compiler Framework - Uninstaller
CLS
SETLOCAL ENABLEDELAYEDEXPANSION

:: --- KONFIGURATION ---
SET "MARKER_FILE=.install_complete"
SET "SHORTCUT_NAME=LLM-Conversion-Framework.lnk"
SET "BACKUP_DIR_NAME=backup_user_data_%DATE:~6,4%%DATE:~3,2%%DATE:~0,2%_%TIME:~0,2%%TIME:~3,2%"
SET "BACKUP_DIR_NAME=!BACKUP_DIR_NAME: =0!"

echo ========================================================
echo      LLM Framework - Enterprise Uninstaller
echo ========================================================
echo.

:: --- 1. PFAD-ERKENNUNG ---
cd /d "%~dp0"
SET "SOURCE_REPO=%CD%"

IF NOT EXIST "%MARKER_FILE%" (
    echo [INFO] Keine Installation gefunden (Marker fehlt).
    echo Es scheint nichts installiert zu sein.
    PAUSE
    EXIT /B 0
)

:: Lese Installationspfad
set /p INSTALL_PATH=<"%MARKER_FILE%"
set "INSTALL_PATH=!INSTALL_PATH:"=!"

echo [INFO] Installation gefunden in:
echo        "!INSTALL_PATH!"
echo.

IF NOT EXIST "!INSTALL_PATH!" (
    echo [WARNUNG] Der Zielordner existiert bereits nicht mehr.
    echo Raeume nur Metadaten auf...
    GOTO :CLEANUP_METADATA
)

:: Sicherheits-Check: Nicht das eigene Repo löschen!
IF /I "!INSTALL_PATH!"=="!SOURCE_REPO!" (
    echo [CRITICAL] Installationspfad ist identisch mit Quell-Repo!
    echo Abbruch zum Schutz Ihrer Daten.
    PAUSE
    EXIT /B 1
)

:: --- 2. USER DATA CHECK ---
set "HAS_USER_DATA=0"
if exist "!INSTALL_PATH!\output\*" set "HAS_USER_DATA=1"
if exist "!INSTALL_PATH!\models\*" set "HAS_USER_DATA=1"
if exist "!INSTALL_PATH!\targets\*" set "HAS_USER_DATA=1"

IF "!HAS_USER_DATA!"=="1" (
    echo [ACHTUNG] Benutzerdaten gefunden (Modelle, Artefakte, Targets).
    echo.
    echo Wie sollen wir verfahren?
    echo.
    echo   [B] Backup & Loeschen (Kopiert Daten in dieses Repo zurueck, dann Loeschen)
    echo   [K] Behalten (Loescht nur App & VENV, behaelt Daten-Ordner)
    echo   [D] Alles Loeschen (Unwiderruflich!)
    echo   [A] Abbrechen
    echo.
    set /p "CHOICE=Ihre Wahl (B/K/D/A): "
    
    IF /I "!CHOICE!"=="A" GOTO :EOF
    IF /I "!CHOICE!"=="B" GOTO :DO_BACKUP
    IF /I "!CHOICE!"=="K" GOTO :DO_PARTIAL_UNINSTALL
    IF /I "!CHOICE!"=="D" GOTO :DO_FULL_UNINSTALL
    
    echo Ungueltige Eingabe. Abbruch.
    GOTO :EOF
) ELSE (
    echo Keine Benutzerdaten gefunden. Fuehre vollstaendige Deinstallation durch...
    GOTO :DO_FULL_UNINSTALL
)

:DO_BACKUP
echo.
echo [BACKUP] Sicherung in Quell-Repo...
SET "TARGET_BACKUP=!SOURCE_REPO!\recovered_data\!BACKUP_DIR_NAME!"
mkdir "!TARGET_BACKUP!"

echo   - Sichere Targets...
xcopy "!INSTALL_PATH!\targets" "!TARGET_BACKUP!\targets" /E /I /H /Y >nul 2>&1
echo   - Sichere Output/Artefakte...
xcopy "!INSTALL_PATH!\output" "!TARGET_BACKUP!\output" /E /I /H /Y >nul 2>&1
echo   - Sichere Configs...
xcopy "!INSTALL_PATH!\configs\config.yml" "!TARGET_BACKUP!\configs\" /Y >nul 2>&1

echo [INFO] Daten gesichert nach: recovered_data\!BACKUP_DIR_NAME!
echo        (Sie koennen diese nun committen und pushen)
GOTO :DO_FULL_UNINSTALL

:DO_PARTIAL_UNINSTALL
echo.
echo [UNINSTALL] Entferne System-Komponenten (behalte Daten)...
:: Lösche VENV (Dependencies)
if exist "!INSTALL_PATH!\.venv" (
    echo   - Entferne Dependencies (.venv)...
    rmdir /s /q "!INSTALL_PATH!\.venv"
)
:: Lösche Core Code
echo   - Entferne Orchestrator...
rmdir /s /q "!INSTALL_PATH!\orchestrator" 2>nul
echo   - Entferne Scripts...
rmdir /s /q "!INSTALL_PATH!\scripts" 2>nul
GOTO :CLEANUP_SHORTCUTS

:DO_FULL_UNINSTALL
echo.
echo [UNINSTALL] Entferne vollstaendigen Installationsordner...
echo   - Loesche "!INSTALL_PATH!"...
rmdir /s /q "!INSTALL_PATH!"
IF EXIST "!INSTALL_PATH!" (
    echo [WARNUNG] Konnte Ordner nicht vollstaendig loeschen (Dateien in Benutzung?).
    echo Bitte manuell pruefen: "!INSTALL_PATH!"
) ELSE (
    echo   - Ordner entfernt.
)
GOTO :CLEANUP_SHORTCUTS

:CLEANUP_SHORTCUTS
echo.
echo [CLEANUP] Entferne Verknuepfungen...
set "DESKTOP_LNK=%USERPROFILE%\Desktop\%SHORTCUT_NAME%"
set "PUBLIC_LNK=%PUBLIC%\Desktop\%SHORTCUT_NAME%"

if exist "%DESKTOP_LNK%" (
    del "%DESKTOP_LNK%"
    echo   - Desktop Shortcut entfernt.
)
if exist "%PUBLIC_LNK%" (
    del "%PUBLIC_LNK%"
    echo   - Public Desktop Shortcut entfernt.
)

:CLEANUP_METADATA
echo.
echo [CLEANUP] Entferne Installations-Marker...
del "%MARKER_FILE%"

echo.
echo ========================================================
echo      Deinstallation erfolgreich abgeschlossen.
echo ========================================================
echo.
PAUSE
