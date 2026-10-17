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

:: Die Argumente (z. B. --ditto=skip) erreichen den PowerShell-Teil als Variable.
set "LCF_INSTALL_ARGS=%*"
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
# Run through Invoke-Expression (see above), this part has no script path of its own; the batch part has
# changed into the folder of install.bat before, so that folder is the current one.
$ScriptPath = if ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }

# 1. KONFIGURATION
$AppName = "LLM-Cross-Compiler-Framework"
$ProgramFiles = [Environment]::GetFolderPath("ProgramFiles")
$InstallDir = Join-Path $ProgramFiles $AppName
# The folder of the installation before the repository was renamed (LLM Conversion Framework). Only read: this installer
# never changes or deletes it; it says that it is there and how to remove it with the uninstaller.
$OldAppName = "LLM-Conversion-Framework"
$OldInstallDir = Join-Path $ProgramFiles $OldAppName
# Program Files is read-only for standard users: the framework keeps builds, cache and models in each user's
# own folder (%USERPROFILE%\.llm-framework, or LLM_FRAMEWORK_HOME) and creates them when they are needed.
$UserDataHint = "%USERPROFILE%\.llm-framework"
# Ditto's local runtime: --ditto=auto|bare-metal|docker|skip, for unattended runs also LCF_DITTO (no questions; the
# model's size and licence are still shown). An unknown value stops here, before anything is installed.
$DittoChoice = if ($env:LCF_DITTO) { $env:LCF_DITTO } else { "auto" }
foreach ($InstallArg in (("" + $env:LCF_INSTALL_ARGS) -split '\s+')) {
    if ($InstallArg -like "--ditto=*") { $DittoChoice = $InstallArg.Substring(8) }
}
if ($DittoChoice -notin @("auto", "bare-metal", "docker", "skip")) {
    Write-Error "Unbekannter Wert fuer --ditto: '$DittoChoice' (auto, bare-metal, docker oder skip)."
}

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
# Exactly the versions in requirements.txt of this checkout (uv export from uv.lock); pip checks every file against
# its hash. Without the file nothing is installed unchecked.
if (-not (Test-Path "requirements.txt")) { Write-Error "requirements.txt fehlt; ohne die festgelegten Versionen wird nichts installiert." }
& $Pip install --require-hashes -r requirements.txt | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "Die Pakete aus requirements.txt liessen sich nicht installieren (Proxy oder eigener Spiegel: PIP_INDEX_URL)." }

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

# Marker for scripts\Uninstall-LLM-Cross-Compiler-Framework.bat: the installation folder, kept next to this script. The
# first line is this installation; the lines after it name other installations that still exist (the one under the
# old name, or one an earlier marker named), so the uninstaller finds them too.
# Reading the earlier marker never stops the installation: without it, only the old folder is added.
$MarkerFile = Join-Path $ScriptPath ".install_complete"
$OtherInstalls = @()
try {
    if (Test-Path -LiteralPath $MarkerFile) {
        $OtherInstalls += @(Get-Content -LiteralPath $MarkerFile | ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ })
    }
    if (Test-Path $OldInstallDir) { $OtherInstalls += $OldInstallDir }
    $OtherInstalls = @($OtherInstalls | Where-Object {
        ($_ -ne $InstallDir) -and (Test-Path -LiteralPath $_ -ErrorAction SilentlyContinue) } | Sort-Object -Unique)
} catch {
    $OtherInstalls = @()
    if (Test-Path $OldInstallDir) { $OtherInstalls = @($OldInstallDir) }
}
try {
    Set-Content -Path (Join-Path $ScriptPath ".install_complete") -Value $InstallDir -Encoding ASCII
    if ($OtherInstalls.Count -gt 0) { Add-Content -Path $MarkerFile -Value $OtherInstalls -Encoding ASCII }
} catch {
    Write-Warning "Marker fuer den Uninstaller nicht geschrieben ($_); er findet die Installation dann nicht."
}

# DITTO: lokale Laufzeit (llm-cli ditto runtime ensure). Idempotent, und sie laesst die Installation nie scheitern:
# ein Fehler wird gemeldet, die Installation geht weiter. Sie laeuft nur fuer die angemeldete Person selbst (ihr
# Benutzerordner); hat ein anderes Administratorkonto die Rechte gegeben, wird sie der Person ueberlassen.
Write-Host "[Ditto] Lokale Laufzeit fuer Ditto ($DittoChoice)..." -ForegroundColor Yellow
try {
    $SignedIn = (Get-CimInstance Win32_ComputerSystem).UserName
    $Elevated = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ($DittoChoice -eq "skip") {
        Write-Host "Uebersprungen (--ditto=skip). Spaeter: llm-cli ditto runtime ensure"
    } elseif ($SignedIn -and ($SignedIn -ne $Elevated)) {
        Write-Warning "Ditto wird fuer $SignedIn eingerichtet, nicht fuer das Administratorkonto. Spaeter als du selbst (normales Terminal, im Ordner $InstallDir): .venv\Scripts\python.exe -m orchestrator.cli ditto runtime ensure"
    } else {
        $DittoArgs = @("-m", "orchestrator.cli", "ditto", "runtime", "ensure", "--mode", $DittoChoice)
        if ($env:LCF_DITTO) { $DittoArgs += "--yes" }
        & $PythonVenv @DittoArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Ditto liess sich nicht einrichten (Meldungen oben); die Installation ist trotzdem fertig. Spaeter erneut: llm-cli ditto runtime ensure"
        }
    }
} catch {
    Write-Warning "Ditto: $_ - die Installation ist trotzdem fertig. Spaeter: llm-cli ditto runtime ensure"
}

Write-Host "Installation Erfolgreich!" -ForegroundColor Green
Write-Host "Builds, Cache und Modelle: $UserDataHint (je Benutzer)"

# The installation under the old name: said in plain words, never removed here.
if (Test-Path $OldInstallDir) {
    Write-Host ""
    Write-Warning "Es gibt noch eine Installation unter dem frueheren Namen: $OldInstallDir"
    Write-Host "Dieser Installer hat sie nicht veraendert und loescht sie nicht. Die neue Installation liegt in $InstallDir,"
    Write-Host "ihre Verknuepfung auf dem Desktop heisst '$AppName'. Die alte Verknuepfung '$OldAppName' startet weiter die alte."
    Write-Host "Deine Einstellungen, Zugangsdaten und Builds in $UserDataHint gelten fuer beide und bleiben, wie sie sind."
    Write-Host "KI-Apps, die du ueber MCP verbunden hast (Claude Desktop, VS Code, Claude Code), starten noch die alte Installation."
    Write-Host "Vor dem Entfernen aus der neuen einrichten (llm-cli mcp config, siehe README), im Ordner $InstallDir:"
    Write-Host "  .venv\Scripts\python.exe -m orchestrator.cli mcp config"
    Write-Host "Entfernen: scripts\Uninstall-LLM-Cross-Compiler-Framework.bat ausfuehren. Es findet beide Installationen,"
    Write-Host "zeigt fuer jede, was es entfernt, und fragt vorher (ohne Antwort: Nein)."
}
