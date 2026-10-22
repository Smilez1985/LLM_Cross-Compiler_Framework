@echo off
TITLE LLM Cross-Compiler Framework - Uninstaller
CLS
SETLOCAL ENABLEDELAYEDEXPANSION

:: --- KONFIGURATION ---
SET "MARKER_FILE=.install_complete"
:: install.bat legt den Ordner "%ProgramFiles%\<Name>" und die Verknuepfung "<Name>.lnk" an.
SET "APP_NAME=LLM-Cross-Compiler-Framework"
SET "SHORTCUT_NAME=LLM-Cross-Compiler-Framework.lnk"
:: Die Installation vor der Umbenennung des Repositorys (LLM Conversion Framework): eigener Ordner, eigene Verknuepfung.
SET "OLD_APP_NAME=LLM-Conversion-Framework"
SET "OLD_SHORTCUT_NAME=LLM-Conversion-Framework.lnk"
SET "BACKUP_DIR_NAME=backup_user_data_%DATE:~6,4%%DATE:~3,2%%DATE:~0,2%_%TIME:~0,2%%TIME:~3,2%"
SET "BACKUP_DIR_NAME=!BACKUP_DIR_NAME: =0!"

echo ========================================================
echo      LLM Cross-Compiler Framework - Uninstaller
echo ========================================================
echo.

:: --- 1. PFAD-ERKENNUNG ---
:: Der Quellordner mit install.bat: install.bat schreibt dort den Marker. Seine erste Zeile ist die Installation,
:: weitere Zeilen nennen Installationen, die es noch gibt (etwa die unter dem frueheren Namen).
:: Dieses Skript liegt in scripts\, also eine Ebene tiefer.
cd /d "%~dp0"
IF NOT EXIST "install.bat" IF EXIST "..\install.bat" cd ..
SET "SOURCE_REPO=%CD%"

:: Gefunden wird jede Installation, die der Marker nennt, und der Ordner unter jedem der beiden Namen in
:: "%ProgramFiles%". Jede wird einzeln gezeigt, mit allem, was entfernt wuerde, und nur nach einem Ja entfernt.
SET "SEEN=;"
SET "FOUND=0"
IF NOT EXIST "%MARKER_FILE%" GOTO :SCAN_DEFAULTS
FOR /F "usebackq delims=" %%P IN ("%MARKER_FILE%") DO CALL :CHECK_ONE "%%~P" marker
:SCAN_DEFAULTS
CALL :CHECK_ONE "%ProgramFiles%\%APP_NAME%" default
CALL :CHECK_ONE "%ProgramFiles%\%OLD_APP_NAME%" default

IF NOT "!FOUND!"=="0" GOTO :FINISH
echo [INFO] Keine Installation gefunden: kein Marker und kein Ordner unter "%ProgramFiles%".
echo Es scheint nichts installiert zu sein.
PAUSE
EXIT /B 0

:FINISH
CALL :REWRITE_MARKER
echo.
echo ========================================================
echo      Deinstallation abgeschlossen.
echo ========================================================
echo.
PAUSE
EXIT /B 0


:: ------------------------------------------------------------------------------------------------------------------
:: Eine Installation pruefen: jeden Ordner nur einmal; ein Standardordner zaehlt nur, wenn es ihn gibt.
:CHECK_ONE
SET "INSTALL_PATH=%~1"
IF NOT DEFINED INSTALL_PATH GOTO :EOF
IF NOT "!SEEN:;%~1;=!"=="!SEEN!" GOTO :EOF
IF "%~2"=="default" IF NOT EXIST "!INSTALL_PATH!\" GOTO :EOF
SET "SEEN=!SEEN!%~1;"
SET /A FOUND+=1
CALL :UNINSTALL_ONE
GOTO :EOF


:: ------------------------------------------------------------------------------------------------------------------
:: Eine Installation zeigen und nach Rueckfrage entfernen. Ohne Eingabe wird nichts entfernt.
:UNINSTALL_ONE
echo.
echo --------------------------------------------------------
echo [INFO] Installation gefunden in:
echo        "!INSTALL_PATH!"
:: Ihre Verknuepfung heisst wie ihr Ordner; nur die beiden bekannten Namen werden je entfernt.
SET "LNK_NAME="
FOR %%I IN ("!INSTALL_PATH!") DO SET "LNK_NAME=%%~nxI.lnk"
IF /I "!LNK_NAME!"=="%OLD_SHORTCUT_NAME%" echo        Das ist die Installation unter dem frueheren Namen LLM Conversion Framework.
IF /I NOT "!LNK_NAME!"=="%SHORTCUT_NAME%" IF /I NOT "!LNK_NAME!"=="%OLD_SHORTCUT_NAME%" SET "LNK_NAME="

IF NOT EXIST "!INSTALL_PATH!\" GOTO :GONE
:: Sicherheits-Check: Nicht das eigene Repo loeschen!
IF /I "!INSTALL_PATH!"=="!SOURCE_REPO!" GOTO :IS_SOURCE

:: --- 2. WAS ENTFERNT WIRD ---
echo.
echo Entfernt wird der Ordner "!INSTALL_PATH!" mit allem darin:
FOR /F "delims=" %%E IN ('dir /b /a "!INSTALL_PATH!" 2^>nul') DO echo     %%E
IF DEFINED LNK_NAME IF EXIST "%USERPROFILE%\Desktop\!LNK_NAME!" echo und die Verknuepfung "%USERPROFILE%\Desktop\!LNK_NAME!"
IF DEFINED LNK_NAME IF EXIST "%PUBLIC%\Desktop\!LNK_NAME!" echo und die Verknuepfung "%PUBLIC%\Desktop\!LNK_NAME!"
echo Es bleibt dein persoenlicher Ordner "%USERPROFILE%\.llm-framework": Einstellungen, Zugangsdaten, Builds.
echo.

:: --- 3. USER DATA CHECK ---
SET "HAS_USER_DATA=0"
IF EXIST "!INSTALL_PATH!\output\*" SET "HAS_USER_DATA=1"
IF EXIST "!INSTALL_PATH!\models\*" SET "HAS_USER_DATA=1"
IF EXIST "!INSTALL_PATH!\targets\*" SET "HAS_USER_DATA=1"
IF "!HAS_USER_DATA!"=="1" GOTO :ASK_USER_DATA

SET "CHOICE="
set /p "CHOICE=Diese Installation entfernen? [j/N]: "
IF /I "!CHOICE!"=="J" GOTO :DO_FULL_UNINSTALL
echo Nicht entfernt.
GOTO :EOF

:ASK_USER_DATA
echo [ACHTUNG] Darin liegen Benutzerdaten: Modelle, Artefakte oder Targets.
echo.
echo Wie sollen wir verfahren?
echo.
echo   [B] Sichern, dann entfernen: targets, output und configs\config.yml nach recovered_data im Quellordner
echo   [K] Behalten: entfernt nur .venv, orchestrator und scripts, die Daten-Ordner bleiben
echo   [D] Alles entfernen, unwiderruflich
echo   [A] Abbrechen, nichts entfernen - so auch ohne Eingabe
echo.
SET "CHOICE="
set /p "CHOICE=Ihre Wahl [B/K/D/A]: "
IF /I "!CHOICE!"=="B" GOTO :DO_BACKUP
IF /I "!CHOICE!"=="K" GOTO :DO_PARTIAL_UNINSTALL
IF /I "!CHOICE!"=="D" GOTO :DO_FULL_UNINSTALL
echo Nicht entfernt.
GOTO :EOF

:DO_BACKUP
echo.
echo [BACKUP] Sicherung in den Quellordner...
SET "TARGET_BACKUP=!SOURCE_REPO!\recovered_data\!BACKUP_DIR_NAME!"
IF EXIST "!TARGET_BACKUP!\" SET "TARGET_BACKUP=!TARGET_BACKUP!_!FOUND!"
mkdir "!TARGET_BACKUP!"
IF NOT EXIST "!TARGET_BACKUP!\" GOTO :BACKUP_FAILED
SET "BACKUP_OK=1"
echo   - Sichere Targets...
CALL :BACKUP_ONE "!INSTALL_PATH!\targets" "!TARGET_BACKUP!\targets" "/E /I /H /Y"
echo   - Sichere Output/Artefakte...
CALL :BACKUP_ONE "!INSTALL_PATH!\output" "!TARGET_BACKUP!\output" "/E /I /H /Y"
echo   - Sichere Configs...
:: Ziel ist ein vorhandener Ordner ohne Schraegstrich am Ende: xcopy liest \" sonst als Anfuehrungszeichen.
IF EXIST "!INSTALL_PATH!\configs\config.yml" mkdir "!TARGET_BACKUP!\configs"
CALL :BACKUP_ONE "!INSTALL_PATH!\configs\config.yml" "!TARGET_BACKUP!\configs" "/Y"
IF "!BACKUP_OK!"=="0" GOTO :BACKUP_FAILED

echo [INFO] Daten gesichert nach: "!TARGET_BACKUP!"
echo        (Sie koennen diese nun committen und pushen)
GOTO :DO_FULL_UNINSTALL

:BACKUP_FAILED
echo [FEHLER] Die Sicherung nach "!TARGET_BACKUP!" ist unvollstaendig; nichts wurde entfernt.
GOTO :EOF

:: Sichert %1 nach %2 mit den xcopy-Schaltern %3; fehlt die Quelle, gibt es nichts zu sichern.
:BACKUP_ONE
IF NOT EXIST "%~1" GOTO :EOF
xcopy "%~1" "%~2" %~3 >nul 2>&1
IF ERRORLEVEL 2 SET "BACKUP_OK=0"
GOTO :EOF

:DO_PARTIAL_UNINSTALL
echo.
echo [UNINSTALL] Entferne System-Komponenten (behalte Daten)...
:: Lösche VENV (Dependencies)
IF EXIST "!INSTALL_PATH!\.venv" echo   - Entferne Dependencies: .venv
IF EXIST "!INSTALL_PATH!\.venv" rmdir /s /q "!INSTALL_PATH!\.venv"
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
IF EXIST "!INSTALL_PATH!" echo [WARNUNG] Konnte Ordner nicht vollstaendig loeschen, Dateien in Benutzung? Bitte manuell pruefen: "!INSTALL_PATH!"
IF NOT EXIST "!INSTALL_PATH!" echo   - Ordner entfernt.
GOTO :CLEANUP_SHORTCUTS

:CLEANUP_SHORTCUTS
IF NOT DEFINED LNK_NAME GOTO :EOF
echo [CLEANUP] Entferne Verknuepfungen...
IF EXIST "%USERPROFILE%\Desktop\!LNK_NAME!" del "%USERPROFILE%\Desktop\!LNK_NAME!"
IF EXIST "%USERPROFILE%\Desktop\!LNK_NAME!" echo [WARNUNG] Konnte "%USERPROFILE%\Desktop\!LNK_NAME!" nicht entfernen.
IF EXIST "%PUBLIC%\Desktop\!LNK_NAME!" del "%PUBLIC%\Desktop\!LNK_NAME!"
IF EXIST "%PUBLIC%\Desktop\!LNK_NAME!" echo [WARNUNG] Konnte "%PUBLIC%\Desktop\!LNK_NAME!" nicht entfernen.
GOTO :EOF

:GONE
echo [WARNUNG] Der Ordner existiert bereits nicht mehr; nur sein Eintrag im Marker wird entfernt.
GOTO :EOF

:IS_SOURCE
echo [CRITICAL] Installationspfad ist identisch mit Quell-Repo! Nichts entfernt, zum Schutz Ihrer Daten.
GOTO :EOF


:: ------------------------------------------------------------------------------------------------------------------
:: Der Marker nennt danach nur noch Installationen, die es noch gibt; ohne eine wird er entfernt.
:REWRITE_MARKER
IF NOT EXIST "%MARKER_FILE%" GOTO :EOF
echo.
echo [CLEANUP] Aktualisiere den Installations-Marker...
IF EXIST "%MARKER_FILE%.new" del "%MARKER_FILE%.new"
FOR /F "usebackq delims=" %%P IN ("%MARKER_FILE%") DO IF EXIST "%%~P\" >>"%MARKER_FILE%.new" echo %%~P
del "%MARKER_FILE%"
IF EXIST "%MARKER_FILE%.new" ren "%MARKER_FILE%.new" "%MARKER_FILE%"
GOTO :EOF
