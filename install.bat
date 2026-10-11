<# :
@echo off
setlocal
cd /d "%~dp0"
title LLM Framework - Enterprise Installer

:: --- BOOTSTRAPPER (Batch Teil) ---
echo.
echo [INIT] Starte Installations-Routine...
echo.

:: Prüfe auf Admin-Rechte
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo [FEHLER] Bitte Rechtsklick -> "Als Administrator ausfuehren".
    pause
    exit /b
)

:: Rufe den PowerShell-Teil dieses Skripts auf
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ($(Get-Content '%~f0' -Raw))"
if %errorlevel% NEQ 0 (
    echo.
    echo [FEHLER] Die Installation ist fehlgeschlagen.
    pause
) else (
    echo.
    echo [ERFOLG] Installation abgeschlossen. Fenster schliesst sich.
    timeout /t 5
)
goto :EOF
: #>

# --- INSTALLER LOGIC (PowerShell Teil) ---

$ErrorActionPreference = "Stop"
$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. KONFIGURATION
$AppName = "LLM-Conversion-Framework"
$ProgramFiles = [Environment]::GetFolderPath("ProgramFiles")
$InstallDir = Join-Path $ProgramFiles $AppName
# Program Files is read-only for standard users: the framework keeps builds, cache and models in each user's
# own folder (%USERPROFILE%\.llm-framework, or LLM_FRAMEWORK_HOME) and creates them when they are needed.
$UserDataHint = "%USERPROFILE%\.llm-framework"

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "      LLM Framework - Enterprise Installer"
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Code Ziel: $InstallDir"
Write-Host "Daten (je Benutzer, beim ersten Gebrauch angelegt): $UserDataHint"
Write-Host ""

# 2. PYTHON PRÜFUNG & INSTALLATION
Write-Host "[1/5] Pruefe Python Umgebung..." -ForegroundColor Yellow
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "Python nicht gefunden. Versuche Installation via Winget..."
    try {
        # Python 3.13: supported by every pinned dependency (3.11 reaches end of life in 2027-10)
        winget install -e --id Python.Python.3.13 --scope machine --accept-source-agreements --accept-package-agreements
        # Refresh Env Vars
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    } catch {
        Write-Error "Python Installation fehlgeschlagen. Bitte Python 3 manuell installieren."
    }
}
# Versions-Check
python --version
if ($LASTEXITCODE -ne 0) { Write-Error "Python ist nicht korrekt im PATH." }
# requirements.txt only matches Python 3.11-3.14; outside that range pip would silently install nothing.
& python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Error "Python 3.11 bis 3.14 wird benoetigt. Bitte z. B. 'winget install Python.Python.3.13' ausfuehren." }

# 3. DATEIEN KOPIEREN
Write-Host "[2/5] Installiere Dateien (Robocopy)..." -ForegroundColor Yellow

# Verzeichnisse erstellen
New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null

# Robocopy Wrapper Funktion
function Copy-Robust ($Src, $Dst, $Exclude) {
    if (Test-Path $Src) {
        # /E = Rekursiv, /NFL = No File List (Silent), /NDL = No Dir List, /NJH = No Job Header
        # Wir nutzen robocopy.exe direkt, da Copy-Item oft langsam ist
        $args = @($Src, $Dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS")
        if ($Exclude) { $args += "/XD"; $args += $Exclude }
        & robocopy.exe $args
        # Robocopy Exit Codes: 0-7 sind Success/Partial Success
        if ($LASTEXITCODE -gt 7) { Write-Error "Kopierfehler von $Src nach $Dst" }
    }
}

# Code kopieren (ohne git, venv, cache und ohne Build-Ergebnisse oder Modelle einer Entwicklungskopie)
Copy-Robust $ScriptPath $InstallDir -Exclude ".git", ".venv", "__pycache__", "build", "dist", ".installer_venv", "output", "cache", "models", "logs"

# 4. VENV SETUP
Write-Host "[3/5] Erstelle Python Environment..." -ForegroundColor Yellow
Set-Location $InstallDir

# Venv erstellen
& python -m venv .venv
if ($LASTEXITCODE -ne 0) { Write-Error "Konnte VENV nicht erstellen." }

$Pip = ".venv\Scripts\pip.exe"
$PythonVenv = ".venv\Scripts\python.exe"

# Pip Upgrade & Dependencies
& $Pip install --upgrade pip | Out-Null
Write-Host "Installiere Requirements..."
if (Test-Path "requirements.txt") {
    & $Pip install -r requirements.txt | Out-Null
} else {
    & $Pip install pyyaml requests psutil pywin32 winshell | Out-Null
}

# 5. LAUNCHER
Write-Host "[4/5] Erstelle Launcher..." -ForegroundColor Yellow
# Launcher Skript im InstallDir erstellen (damit die Shortcuts funktionieren)
$LauncherBat = Join-Path $InstallDir "start_framework.bat"
$BatContent = @"
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python orchestrator\main.py
pause
"@
Set-Content $LauncherBat $BatContent

# 6. SHORTCUT
Write-Host "[5/5] Erstelle Desktop Verknuepfung..." -ForegroundColor Yellow
$WshShell = New-Object -comObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")

# App Shortcut
$Shortcut = $WshShell.CreateShortcut((Join-Path $Desktop "$AppName.lnk"))
$Shortcut.TargetPath = $LauncherBat
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Description = "Startet das Framework"
$IconPath = Join-Path $InstallDir "assets\LLM-Builder.ico"
if (Test-Path $IconPath) { $Shortcut.IconLocation = $IconPath }
$Shortcut.Save()

# Marker for scripts\Uninstall-LLM-Conversion-Framework.bat: the installation folder, kept next to this script
try {
    Set-Content -Path (Join-Path $ScriptPath ".install_complete") -Value $InstallDir -Encoding ASCII
} catch {
    Write-Warning "Marker fuer den Uninstaller nicht geschrieben ($_); er findet die Installation dann nicht."
}

Write-Host "Installation Erfolgreich!" -ForegroundColor Green
Write-Host "Builds, Cache und Modelle: $UserDataHint (je Benutzer)"
