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
$PublicDocs = [Environment]::GetFolderPath("CommonDocuments")
$DataDir = Join-Path $PublicDocs $AppName

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "      LLM Framework - Enterprise Installer"
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Code Ziel: $InstallDir"
Write-Host "Daten Ziel: $DataDir"
Write-Host ""

# 2. PYTHON PRÜFUNG & INSTALLATION
Write-Host "[1/5] Pruefe Python Umgebung..." -ForegroundColor Yellow
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "Python nicht gefunden. Versuche Installation via Winget..."
    try {
        # Python 3.11 festnageln für Stabilität
        winget install -e --id Python.Python.3.11 --scope machine --accept-source-agreements --accept-package-agreements
        # Refresh Env Vars
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    } catch {
        Write-Error "Python Installation fehlgeschlagen. Bitte Python 3 manuell installieren."
    }
}
# Versions-Check
python --version
if ($LASTEXITCODE -ne 0) { Write-Error "Python ist nicht korrekt im PATH." }

# 3. DATEIEN KOPIEREN
Write-Host "[2/5] Installiere Dateien (Robocopy)..." -ForegroundColor Yellow

# Verzeichnisse erstellen
New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
New-Item -Path $DataDir -ItemType Directory -Force | Out-Null

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

# Code kopieren (ohne git, venv, cache)
Copy-Robust $ScriptPath $InstallDir -Exclude ".git", ".venv", "__pycache__", "build", "dist", ".installer_venv"

# Daten Templates kopieren (targets, models, configs)
foreach ($folder in @("targets", "models", "configs", "assets")) {
    Copy-Robust (Join-Path $ScriptPath $folder) (Join-Path $DataDir $folder)
}
# Output Ordner anlegen
foreach ($folder in @("output", "logs", "cache")) {
    New-Item -Path (Join-Path $DataDir $folder) -ItemType Directory -Force | Out-Null
}

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

# 5. CONFIG UPDATE
Write-Host "[4/5] Konfiguriere Pfade..." -ForegroundColor Yellow
$ConfigFile = Join-Path $DataDir "configs\user_config.yml"
if (Test-Path $ConfigFile) {
    $YamlContent = Get-Content $ConfigFile -Raw
    # Simpler String Replace, da wir kein PyYAML im Host-PowerShell haben
    $YamlContent = $YamlContent -replace "output_dir:.*", "output_dir: $(Join-Path $DataDir 'output')"
    $YamlContent = $YamlContent -replace "logs_dir:.*", "logs_dir: $(Join-Path $DataDir 'logs')"
    $YamlContent = $YamlContent -replace "cache_dir:.*", "cache_dir: $(Join-Path $DataDir 'cache')"
    $YamlContent = $YamlContent -replace "\\", "/"  # YAML mag Forward Slashes lieber
    Set-Content -Path $ConfigFile -Value $YamlContent
}

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

# 6. SHORTCUTS
Write-Host "[5/5] Erstelle Desktop Verknuepfungen..." -ForegroundColor Yellow
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

# Data Shortcut
$ShortcutData = $WshShell.CreateShortcut((Join-Path $Desktop "$AppName Data.lnk"))
$ShortcutData.TargetPath = $DataDir
$ShortcutData.Description = "Hier liegen Modelle und Outputs"
$IconPathData = Join-Path $InstallDir "assets\setup_LLM-Builder.ico"
if (Test-Path $IconPathData) { $ShortcutData.IconLocation = $IconPathData }
$ShortcutData.Save()

Write-Host "Installation Erfolgreich!" -ForegroundColor Green
