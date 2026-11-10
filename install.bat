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
# The container engine (step [Engine] below): Podman without administrator rights by default, Docker Desktop only
# with --rootful. An unknown --engine value stops here too, before anything is installed.
$Rootful = $false
$EngineChoice = "podman"
foreach ($InstallArg in (("" + $env:LCF_INSTALL_ARGS) -split '\s+')) {
    if ($InstallArg -eq "--rootful") { $Rootful = $true }
    if ($InstallArg -like "--engine=*") { $EngineChoice = $InstallArg.Substring(9) }
}
if ($EngineChoice -notin @("podman")) {
    Write-Error "Unbekannter Wert fuer --engine: '$EngineChoice' (unter Windows: podman; Docker Desktop nur mit --rootful)."
}
# The framework mode: private (the default) or enterprise (only OpenSSH with sudo on a board, the root-owned key line).
# --mode=private|enterprise, for unattended runs also LCF_MODE; without either the installer asks in this window
# (Enter: the current mode on an update, private on a fresh install). A run without a console to ask, or with its input redirected (an unattended run: Read-Host would
# read an empty line there and pick private), keeps the mode as it is (new: private). An unknown value stops here.
$ModeChoice = if ($env:LCF_MODE) { $env:LCF_MODE } else { "" }
foreach ($InstallArg in (("" + $env:LCF_INSTALL_ARGS) -split '\s+')) {
    if ($InstallArg -like "--mode=*") { $ModeChoice = $InstallArg.Substring(7) }
}
if ($ModeChoice -notin @("", "private", "enterprise")) {
    Write-Error "Unbekannter Wert fuer --mode: '$ModeChoice' (private oder enterprise)."
}
# Dependencies (DESIGN round 65): every run resolves the newest compatible versions fresh (step [3/5]); --offline (for
# unattended runs also LCF_OFFLINE=1) installs the shipped tested lock without resolving.
$Offline = ($env:LCF_OFFLINE -eq "1")
# A preload (an archive written by preload.ps1 or preload.sh on a computer with a network) for a computer without one:
# --preload=FILE (a path without spaces; else LCF_PRELOAD), checked against --preload-sha256=HEX (LCF_PRELOAD_SHA256)
# when given. With a preload the installer never tries the network.
$PreloadFile = "" + $env:LCF_PRELOAD
$PreloadSha256 = "" + $env:LCF_PRELOAD_SHA256
foreach ($InstallArg in (("" + $env:LCF_INSTALL_ARGS) -split '\s+')) {
    if ($InstallArg -eq "--offline") { $Offline = $true }
    if ($InstallArg -like "--preload=*") { $PreloadFile = $InstallArg.Substring(10) }
    if ($InstallArg -like "--preload-sha256=*") { $PreloadSha256 = $InstallArg.Substring(17) }
}
if ($PreloadFile) { $Offline = $true }
if ($PreloadSha256 -and ($PreloadSha256 -notmatch '^[0-9A-Fa-f]{64}$')) {
    Write-Error "--preload-sha256 erwartet die 64 Hexziffern, die preload.ps1 ausgab."
}
# On an update the default is the current mode (read like every setting, with the earlier installation's Python and
# the mode's one reader, ConfigManager and framework_mode): Enter alone keeps it ([p/E] for enterprise); a fresh
# install has private as its default ([P/e]).
$CurrentMode = ""
$OldPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not $ModeChoice -and (Test-Path $OldPython)) {
    try {
        Push-Location $InstallDir
        $CurrentMode = ("" + (& $OldPython -c "from orchestrator.Core.config_manager import ConfigManager, framework_mode; print(framework_mode(ConfigManager()))" 2>$null | Select-Object -Last 1)).Trim()
    } catch {
        $CurrentMode = ""
    } finally {
        Pop-Location
    }
    if ($CurrentMode -notin @("private", "enterprise")) { $CurrentMode = "" }
}
if (-not $ModeChoice -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    try {
        $ModeDefault = if ($CurrentMode -eq "enterprise") { "enterprise" } else { "private" }
        $ModeHint = if ($ModeDefault -eq "enterprise") { "[p/E]" } else { "[P/e]" }
        $ModeNow = if ($CurrentMode -eq "enterprise") { "enterprise (jetzt eingestellt)" } elseif ($CurrentMode) { "privat (jetzt eingestellt)" } else { "privat (Standard)" }
        $ModeAnswer = Read-Host "Framework-Modus: $ModeNow; Enter behaelt ihn. privat oder enterprise? Enterprise nimmt auf Boards nur OpenSSH mit sudo (die Schluesselzeile in Root-Besitz) und lehnt die schwaechere Dropbear-Stufe und das Home-Tor ab. $ModeHint"
        $ModeWord = "$ModeAnswer".Trim().ToLower()
        $ModeChoice = if ($ModeWord -in @("e", "enterprise")) { "enterprise" } elseif ($ModeWord -in @("p", "privat", "private")) { "private" } else { $ModeDefault }
    } catch {
        $ModeChoice = ""
    }
}

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "      LLM Framework - Enterprise Installer"
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Code Ziel: $InstallDir"
Write-Host "Daten (je Benutzer, beim ersten Gebrauch angelegt): $UserDataHint"
Write-Host ""

# HTTPS CHECK: the package index over HTTPS before anything is installed (pip and winget need it). A failure of the
# certificate or the TLS handshake is checked against the time first: this computer's clock against the Date header
# of the server, fetched once more with a check that accepts the certificate of this one request only and read for its
# date only. A clock off by more than five minutes is named with both times and the fix; with the person's yes the
# installer starts Windows' time service and syncs it. Only when the clock is right does the message name a proxy.
# Offline (--offline, a preload) nothing is checked: the installer does not try the network.
$IndexUrl = if ($env:PIP_INDEX_URL) { $env:PIP_INDEX_URL } else { "https://pypi.org/simple/" }
$IndexShown = $IndexUrl -replace '://[^/@]*@', '://***@'
$IndexProbe = $IndexUrl.TrimEnd('/') + "/pip/"
$ClockTolerance = 300
# Windows PowerShell 5.1 may offer only old TLS versions by default; the package index needs TLS 1.2.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

# $null when the URL answers over HTTPS, else the exception.
function Test-Https ($Url) {
    try {
        $Request = [System.Net.HttpWebRequest]::Create($Url)
        $Request.Timeout = 10000
        $Response = $Request.GetResponse()
        $Response.Close()
        return $null
    } catch {
        return $_.Exception
    }
}

# Is the exception (or one inside it) a failure of the certificate or the TLS handshake?
function Test-TlsFailure ($Problem) {
    $Inner = $Problem
    while ($Inner) {
        if ($Inner -is [System.Net.WebException] -and ($Inner.Status -in @([System.Net.WebExceptionStatus]::TrustFailure, [System.Net.WebExceptionStatus]::SecureChannelFailure))) { return $true }
        if ($Inner -is [System.Security.Authentication.AuthenticationException]) { return $true }
        $Inner = $Inner.InnerException
    }
    return $false
}

# The server's Date header (UTC), or $null. Only this one request accepts any certificate, and only its date is read.
function Get-ServerDate ($Url) {
    try {
        $Request = [System.Net.HttpWebRequest]::Create($Url)
        $Request.Method = "HEAD"
        $Request.Timeout = 10000
        $Request.ServerCertificateValidationCallback = { $true }
        try { $Response = $Request.GetResponse() } catch [System.Net.WebException] { $Response = $_.Exception.Response }
        if (-not $Response) { return $null }
        $Header = $Response.Headers["Date"]
        $Response.Close()
        if (-not $Header) { return $null }
        return [DateTime]::Parse($Header, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal)
    } catch {
        return $null
    }
}

function Get-SpanText ($Seconds) {
    if ($Seconds -lt 7200) { return "$([Math]::Round($Seconds / 60)) Minuten" }
    if ($Seconds -lt 172800) { return "$([Math]::Round($Seconds / 3600)) Stunden" }
    return "$([Math]::Round($Seconds / 86400)) Tage"
}

if (-not $Offline) {
    Write-Host "Pruefe Internetverbindung (HTTPS zu $IndexShown)..."
    $Problem = Test-Https $IndexProbe
    if ($Problem) {
        $Why = "Proxy? Dann den Proxy fuer dieses Konto einrichten oder PIP_INDEX_URL auf einen erreichbaren Spiegel setzen."
        if (Test-TlsFailure $Problem) {
            $Now = [DateTime]::UtcNow
            $Server = Get-ServerDate $IndexProbe
            if (-not $Server) {
                $Why = "Das Zertifikat oder der TLS-Handshake scheiterte, und die Uhrzeit des Servers liess sich nicht lesen. Pruefe zuerst die Uhr dieses Rechners (jetzt: $($Now.ToString('yyyy-MM-dd HH:mm:ss')) UTC): Einstellungen > Zeit und Sprache > Datum und Uhrzeit, 'Uhrzeit automatisch festlegen' und 'Jetzt synchronisieren'. Stimmt sie: ein Proxy oder Spiegel mit eigenem Zertifikat?"
            } else {
                $Off = [Math]::Abs(($Now - $Server).TotalSeconds)
                if ($Off -gt $ClockTolerance) {
                    $Why = "Die Uhr dieses Rechners geht falsch: Sie zeigt $($Now.ToString('yyyy-MM-dd HH:mm:ss')) UTC, der Server sagt $($Server.ToString('yyyy-MM-dd HH:mm:ss')) UTC (etwa $(Get-SpanText $Off) Unterschied). Mit einer falschen Uhr scheitert jede Zertifikatspruefung; ein Proxy ist hier nicht die Ursache. Behebung: Einstellungen > Zeit und Sprache > Datum und Uhrzeit: 'Uhrzeit automatisch festlegen' einschalten und 'Jetzt synchronisieren' (oder in einem Terminal als Administrator: w32tm /resync). Startet dieser Rechner auch Linux, haelt Linux die Hardware-Uhr in UTC und Windows in Ortszeit: entweder Windows auf UTC stellen (HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation, RealTimeIsUniversal = 1 als DWORD) oder unter Linux: sudo timedatectl set-local-rtc 1."
                    # The offer: only with the person's yes, never silently (it changes a setting of the system).
                    $Fixed = $false
                    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
                        Write-Warning $Why
                        $ClockAnswer = Read-Host "Die Uhr jetzt ueber das Netz stellen lassen (startet den Windows-Zeitdienst und synchronisiert: w32tm /resync)? [j/N]"
                        if ("$ClockAnswer".Trim().ToLower() -in @("j", "ja", "y", "yes")) {
                            try {
                                Set-Service -Name w32time -StartupType Automatic
                                Start-Service -Name w32time
                                & w32tm /resync /force | Out-Host
                                Start-Sleep -Seconds 3
                                Write-Host "Die Uhr zeigt jetzt $([DateTime]::UtcNow.ToString('yyyy-MM-dd HH:mm:ss')) UTC."
                                $Fixed = -not (Test-Https $IndexProbe)
                            } catch {
                                Write-Warning "Der Zeitdienst liess sich nicht stellen: $_"
                            }
                        }
                    }
                    if ($Fixed) { $Problem = $null }
                } else {
                    $Why = "Das Zertifikat oder der TLS-Handshake scheiterte; die Uhr dieses Rechners stimmt ($($Now.ToString('yyyy-MM-dd HH:mm:ss')) UTC). Ein Proxy oder Spiegel mit eigenem Zertifikat? Dann PIP_INDEX_URL auf einen Spiegel setzen, dem dieser Rechner vertraut."
                }
            }
        }
        if ($Problem) { Write-Error "$IndexShown ist per HTTPS nicht erreichbar. $Why" }
    }
} else {
    Write-Host "Offline: keine Pruefung der Internetverbindung."
}

# 2. PYTHON PRÜFUNG & INSTALLATION
Write-Host "[1/5] Pruefe Python Umgebung..." -ForegroundColor Yellow
if ($Offline -and -not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Error "Offline: Python fehlt, und ohne Netz installiert der Installer keins (winget braucht das Netz). Installiere Python 3.11 bis 3.14 von einem lokalen Installer (python.org, fuer alle Benutzer) und starte install.bat erneut."
}
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

# SEVERAL PEOPLE, ONE INSTALLATION: the program folder keeps the rights Program Files gives (administrators full
# control, every account read and run), so every account may use it; there is no group of its own on Windows. The
# account that installed or last updated it is written in .lcf-installed-by, what it holds in .lcf-installed-state (the
# version and one sha256 over the copied files). Run by another account while both are the checkout's, the installer
# copies nothing and builds no .venv: only that person's part (the shortcut, the container engine, Ditto and the mode
# for the signed-in person). Another version or other files: an update of the shared part, as before.
$SignedInAccount = $null
try { $SignedInAccount = (Get-CimInstance Win32_ComputerSystem).UserName } catch { $SignedInAccount = $null }
if (-not $SignedInAccount) { $SignedInAccount = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name }
$CopyExcludes = @(".git", ".venv", "__pycache__", "build", "dist", ".installer_venv", "output", "cache", "models", "logs", "llmcf-preload-scripts")
function Get-CheckoutState {
    $Version = ""
    $Init = Join-Path $ScriptPath "orchestrator\__init__.py"
    if (Test-Path $Init) {
        $Match = Select-String -Path $Init -Pattern '^__version__\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
        if ($Match) { $Version = $Match.Matches[0].Groups[1].Value }
    }
    $Root = (Resolve-Path $ScriptPath).Path.TrimEnd('\')
    $Lines = Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $Rel = $_.FullName.Substring($Root.Length + 1)
        -not ($Rel.Split('\') | Where-Object { $CopyExcludes -contains $_ })
    } | ForEach-Object {
        $_.FullName.Substring($Root.Length + 1).Replace('\', '/') + " " + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()
    } | Sort-Object
    $Bytes = [Text.Encoding]::UTF8.GetBytes(($Lines -join "`n"))
    $Sha = [Security.Cryptography.SHA256]::Create()
    $Sum = ([BitConverter]::ToString($Sha.ComputeHash($Bytes))).Replace("-", "").ToLower()
    return "$Version $Sum"
}
$InstalledByFile = Join-Path $InstallDir ".lcf-installed-by"
$InstalledStateFile = Join-Path $InstallDir ".lcf-installed-state"
$CheckoutState = ""
try { $CheckoutState = Get-CheckoutState } catch { $CheckoutState = "" }
$PersonOnly = $false
$InstalledBy = ""
if ((Test-Path (Join-Path $InstallDir ".venv\Scripts\python.exe")) -and (Test-Path $InstalledByFile)) {
    $InstalledBy = ("" + (Get-Content -LiteralPath $InstalledByFile -TotalCount 1)).Trim()
    $RecordedState = if (Test-Path $InstalledStateFile) { ("" + (Get-Content -LiteralPath $InstalledStateFile -TotalCount 1)).Trim() } else { "" }
    if ($InstalledBy -and ($InstalledBy -ne $SignedInAccount)) {
        if ($CheckoutState -and ($CheckoutState -eq $RecordedState)) {
            $PersonOnly = $true
            Write-Host "llmcf ist auf diesem Rechner schon installiert (von $InstalledBy, dieselbe Version und dieselben Dateien wie dieser Checkout). Fuer $SignedInAccount wird nur der eigene Teil eingerichtet; der gemeinsame Teil in $InstallDir bleibt, wie er ist." -ForegroundColor Cyan
        } else {
            Write-Host "llmcf ist hier schon installiert (von $InstalledBy); dieser Checkout hat eine andere Version oder andere Dateien: Der gemeinsame Teil wird aktualisiert, die Ordner jeder Person bleiben, wie sie sind." -ForegroundColor Cyan
        }
    }
}

# THE PRELOAD (offline): the Python packages from an archive made on a computer with a network, without any package
# index. Without one (and without an own mirror for pip, PIP_INDEX_URL or PIP_FIND_LINKS), the installer writes
# preload.ps1 and preload.sh for exactly this computer into llmcf-preload-scripts next to install.bat, says what they
# fetch and waits for the archive's path; a run without a console stops with these steps instead of waiting. The
# archive is checked by scripts\preload_lock.py open (only folders and files under llmcf-preload\, every file with the
# sha256 that SHA256SUMS names); the lock's own hashes are checked by pip as with a network.
$PreloadDir = ""
$PreloadWork = ""
$PreloadTool = Join-Path $ScriptPath "scripts\preload_lock.py"
$PythonVersionFull = ("" + (& python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])")).Trim()
$PythonVersion = ($PythonVersionFull -split '\.')[0..1] -join '.'
$ThisTarget = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "windows-arm64" } else { "windows-amd64" }
if ((-not $PersonOnly) -and $Offline -and (-not $PreloadFile) -and (-not $env:PIP_INDEX_URL) -and (-not $env:PIP_FIND_LINKS)) {
    $ScriptsDir = Join-Path $ScriptPath "llmcf-preload-scripts"
    $SpecDir = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-spec-" + [Guid]::NewGuid().ToString("N"))
    $PackagesFile = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-packages-" + [Guid]::NewGuid().ToString("N") + ".txt")
    $PackagesText = @(
        "# Systempakete, die install.bat auf dem Zielrechner installieren wuerde, wenn sie fehlen (mit Netz ueber winget).",
        "# Offline installiert er keine: Bring die Installer von einem Rechner mit Netz mit.",
        "python: winget install -e --id Python.Python.3.13",
        "7zip: winget install -e --id 7zip.7zip",
        "container-engine: winget install -e --id RedHat.Podman (mit --rootful: Docker.DockerDesktop)") -join "`n"
    [IO.File]::WriteAllText($PackagesFile, $PackagesText + "`n", (New-Object System.Text.UTF8Encoding $false))
    $Version = if ($CheckoutState) { $CheckoutState.Split(' ')[0] } else { "" }
    & python -I $PreloadTool spec --requirements (Join-Path $ScriptPath "requirements-gui.txt") --resolve (Join-Path $ScriptPath "requirements-resolve.txt") --target $ThisTarget --python $PythonVersionFull --version $Version --system-packages $PackagesFile --out $SpecDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "Das Rezept des Preloads liess sich nicht schreiben (oben)." }
    $Written = @(& python -I $PreloadTool write-scripts --spec $SpecDir --out $ScriptsDir)
    if (($LASTEXITCODE -ne 0) -or ($Written.Count -lt 3)) { Write-Error "preload.ps1 und preload.sh liessen sich nicht schreiben (oben)." }
    Remove-Item -Recurse -Force $SpecDir -ErrorAction SilentlyContinue
    Remove-Item -Force $PackagesFile -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Warning "Offline: Die Python-Pakete von llmcf fehlen hier, und ohne Netz holt der Installer sie nicht."
    Write-Host "Er hat das Preload fuer genau diesen Rechner geschrieben:"
    Write-Host "  $($Written[1])   (Windows: powershell -ExecutionPolicy Bypass -File preload.ps1)"
    Write-Host "  $($Written[0])   (Linux, macOS: sh preload.sh)"
    Write-Host "Es holt: $($Written[2])."
    Write-Host "Fuehre eins davon auf einem Rechner mit Netz aus (Python 3 mit pip genuegt), trag das Archiv (llmcf-preload-...tar.gz)"
    Write-Host "hierher (etwa auf einem USB-Stick) und gib hier seinen Pfad an."
    if (-not ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected)) {
        Write-Error "Keine Konsole zum Warten (ein unbeaufsichtigter Lauf). Mach das Archiv mit dem Preload oben und starte dann: install.bat --offline --preload=DATEI (oder LCF_PRELOAD=DATEI)"
    }
    while (-not $PreloadFile) {
        $Answer = ("" + (Read-Host "Pfad des Preload-Archivs (leer: abbrechen)")).Trim().Trim('"')
        if (-not $Answer) { Write-Error "Abgebrochen, nichts veraendert. Spaeter: install.bat --offline --preload=DATEI" }
        if (Test-Path -LiteralPath $Answer -PathType Leaf) { $PreloadFile = $Answer } else { Write-Warning "$Answer gibt es nicht (oder es ist keine Datei); noch einmal." }
    }
}
if ((-not $PersonOnly) -and $PreloadFile) {
    if (-not (Test-Path -LiteralPath $PreloadFile -PathType Leaf)) { Write-Error "Das Preload $PreloadFile gibt es nicht (oder es ist keine Datei)." }
    $PreloadWork = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-preload-" + [Guid]::NewGuid().ToString("N"))
    $OpenArgs = @("-I", $PreloadTool, "open", "--archive", $PreloadFile, "--out", $PreloadWork)
    if ($PreloadSha256) { $OpenArgs += @("--sha256", $PreloadSha256) }
    $Manifest = @{}
    foreach ($Line in @(& python @OpenArgs)) { $Pair = "$Line" -split '=', 2; if ($Pair.Count -eq 2) { $Manifest[$Pair[0]] = $Pair[1] } }
    if ($LASTEXITCODE -ne 0) { Write-Error "Das Preload $PreloadFile wurde abgelehnt (der Grund steht oben); nichts veraendert." }
    Write-Host "Preload: $PreloadFile"
    Write-Host "sha256 des Archivs: $($Manifest['archive_sha256']) (vergleiche mit der Zahl, die preload.ps1 auf dem Rechner mit Netz ausgab)"
    if ($Manifest["target"] -ne $ThisTarget) { Write-Error "Das Preload ist fuer $($Manifest['target']), dieser Rechner ist $ThisTarget. Auf dem Rechner mit Netz: preload.ps1 -Target $ThisTarget" }
    $PreloadPython = (("" + $Manifest["python"]) -split '\.')[0..1] -join '.'
    if ($PreloadPython -ne $PythonVersion) { Write-Error "Das Preload ist fuer Python $($Manifest['python']), hier ist Python $PythonVersionFull. Auf dem Rechner mit Netz: preload.ps1 -Python $PythonVersion" }
    if ($Manifest["lock"] -ne "requirements-gui.txt") { Write-Error "Das Preload enthaelt die Pakete von $($Manifest['lock']); install.bat installiert requirements-gui.txt. Mach es mit preload.ps1 (ohne -Headless) neu." }
    $LockSha = (Get-FileHash -LiteralPath (Join-Path $ScriptPath "requirements-gui.txt") -Algorithm SHA256).Hash.ToLower()
    if ($Manifest["lock_sha256"] -ne $LockSha) { Write-Error "Das Preload wurde aus einem anderen Stand von requirements-gui.txt gemacht als diesem Checkout (Preload: llmcf $($Manifest['version'])). Mach es mit preload.ps1 aus genau diesem Checkout neu." }
    $PreloadDir = $Manifest["folder"]
    Write-Host "Preload geprueft: Pakete fuer $ThisTarget, Python $($Manifest['python']) (llmcf $($Manifest['version']))." -ForegroundColor Green
}

if (-not $PersonOnly) {
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
Copy-Robust $ScriptPath $InstallDir -Exclude ".git", ".venv", "__pycache__", "build", "dist", ".installer_venv", "output", "cache", "models", "logs", "llmcf-preload-scripts"
# Fuer den Updater (orchestrator/utils/updater.py, llm-cli update): der Checkout, aus dem installiert wurde
if (Test-Path (Join-Path $ScriptPath ".git")) { Set-Content -LiteralPath (Join-Path $InstallDir ".install_source") -Value $ScriptPath -Encoding UTF8 }

# 4. VENV SETUP
Write-Host "[3/5] Erstelle Python Environment..." -ForegroundColor Yellow
Set-Location $InstallDir

$Pip = ".venv\Scripts\pip.exe"
$PythonVenv = ".venv\Scripts\python.exe"

# Dependencies (DESIGN round 65). Every run, every update too, resolves the newest compatible versions fresh
# (scripts\fresh_deps.py resolve: uv lock --upgrade in a copy, inside the ranges of pyproject.toml and with the 5-day
# cooldown), audits them with pip-audit, installs them with hashes and checks the new environment itself
# (fresh_deps.py selfcheck: pip check, every dependency and the core modules import, llm-cli --help). When any of
# these fails, the shipped tested lock comes instead (uv.lock, exported as requirements-gui.txt: the base packages plus
# the optional extra 'gui', PySide6). Offline (--offline, LCF_OFFLINE=1) or without a reachable index: the tested lock
# at once. The earlier .venv waits aside (.venv.previous) until the new one passed; when the tested lock fails too, it
# comes back unchanged. The venv's pip is the one that comes with Python (ensurepip) and is not upgraded: that would
# fetch the newest pip without a checksum, and the bundled one is enough for --require-hashes.
$TestedFile = Join-Path $InstallDir "requirements-gui.txt"
$FreshDir = Join-Path $InstallDir ".deps-fresh"
$FreshFile = Join-Path $FreshDir "requirements-fresh.txt"
$PreviousVenv = Join-Path $InstallDir ".venv.previous"
# Without the tested lock nothing is installed: it is the way back from every failure.
if (-not (Test-Path $TestedFile)) { Write-Error "requirements-gui.txt fehlt; ohne die festgelegten Versionen wird nichts installiert." }

# Builds .venv from one hash-pinned export and checks it; $true when the self-check passed. Output goes to the
# console (Out-Host), so the function returns only its answer.
function Install-Venv ($ReqFile) {
    if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
    & python -m venv .venv | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "Konnte VENV nicht erstellen."; return $false }
    # --no-deps: the file is the complete export (every package, every hash); pip installs exactly that set.
    # From a preload: only its packages, without any package index; build\ has setuptools and wheel for a package that
    # is published only as a source archive.
    $PipSource = @()
    if ($PreloadDir) { $PipSource = @("--no-index", "--find-links", (Join-Path $PreloadDir "wheels"), "--find-links", (Join-Path $PreloadDir "build")) }
    & $Pip install @PipSource --require-hashes --no-deps -r $ReqFile | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ($PreloadDir) { Write-Host "Die Pakete aus $ReqFile liessen sich aus dem Preload nicht installieren (passt es zu diesem Rechner und Checkout?)." }
        else { Write-Host "Die Pakete aus $ReqFile liessen sich nicht installieren (Proxy oder eigener Spiegel: PIP_INDEX_URL)." }
        return $false
    }
    & $PythonVenv scripts\fresh_deps.py selfcheck --root . --gui | Out-Host
    return ($LASTEXITCODE -eq 0)
}

$UseFresh = $false
if (Test-Path $FreshDir) { Remove-Item -Recurse -Force $FreshDir }
if ($Offline) {
    Write-Warning "Offline (--offline): Es wird nichts neu aufgeloest; installiert wird der mitgelieferte, getestete Stand (uv.lock)."
} else {
    Write-Host "Suche die neuesten vertraeglichen Versionen (uv, 5 Tage Wartezeit) und pruefe sie (pip-audit)..."
    New-Item -Path $FreshDir -ItemType Directory -Force | Out-Null
    $SystemPython = (Get-Command "python").Source
    & python scripts\fresh_deps.py resolve --project $InstallDir --work $FreshDir --out $FreshFile --extra gui --python $SystemPython | Out-Host
    $ResolveCode = $LASTEXITCODE
    if (($ResolveCode -eq 0) -and (Test-Path $FreshFile)) {
        $UseFresh = $true
    } elseif ($ResolveCode -eq 4) {
        Write-Warning "pip-audit fand im neuesten Stand eine bekannte Schwachstelle (oben); installiert wird der mitgelieferte, getestete Stand (uv.lock)."
    } elseif ($ResolveCode -eq 5) {
        Write-Warning "pip-audit liess sich nicht ausfuehren (oben); ungeprueft wird nichts installiert, also der mitgelieferte, getestete Stand (uv.lock)."
    } else {
        Write-Warning "Der neueste Stand liess sich nicht ermitteln (offline, Paketindex nicht erreichbar oder keine Aufloesung; oben); installiert wird der mitgelieferte, getestete Stand (uv.lock)."
    }
}

# The earlier environment aside until the new one passed (a venv knows its own path, so the new one is built in its
# place and the earlier one waits next to it). A running framework holds its files: then nothing is changed.
if (Test-Path $PreviousVenv) { Remove-Item -Recurse -Force $PreviousVenv }
if (Test-Path ".venv") {
    try {
        Rename-Item -Path ".venv" -NewName ".venv.previous"
    } catch {
        Write-Error "Die bisherige Umgebung $InstallDir\.venv liess sich nicht beiseitelegen (laeuft das Framework noch?); nichts veraendert."
    }
}

$Installed = ""
if ($UseFresh) {
    Write-Host "Installiere die neuesten vertraeglichen Versionen (geprueft mit pip-audit)..."
    if (Install-Venv $FreshFile) {
        $Installed = "fresh"
    } else {
        Write-Warning "Der neueste Stand liess sich nicht installieren oder bestand die Selbstpruefung nicht (oben). Zurueck zum mitgelieferten, getesteten Stand (uv.lock)."
    }
}
if (-not $Installed) {
    Write-Host "Installiere Requirements (requirements-gui.txt, der getestete Stand)..."
    if (Install-Venv $TestedFile) {
        $Installed = "tested"
    } else {
        if (Test-Path ".venv") { Remove-Item -Recurse -Force ".venv" }
        if (Test-Path $FreshDir) { Remove-Item -Recurse -Force $FreshDir }
        if (Test-Path $PreviousVenv) {
            Rename-Item -Path $PreviousVenv -NewName ".venv"
            Write-Warning "Die bisherige Umgebung ist unveraendert wieder da ($InstallDir\.venv)."
        }
        Write-Error "Die Python-Umgebung in $InstallDir\.venv liess sich nicht einrichten (Meldungen oben); die Installation ist nicht fertig. Proxy oder eigener Spiegel: PIP_INDEX_URL."
    }
}
if (Test-Path $PreviousVenv) { Remove-Item -Recurse -Force $PreviousVenv }
if (Test-Path $FreshDir) { Remove-Item -Recurse -Force $FreshDir }
if ($PreloadWork) { Remove-Item -Recurse -Force $PreloadWork -ErrorAction SilentlyContinue }
if ($Installed -eq "fresh") {
    Write-Host "Die neuesten vertraeglichen Versionen sind installiert, mit pip-audit geprueft und selbstgeprueft." -ForegroundColor Green
} else {
    Write-Host "Der mitgelieferte, getestete Stand (uv.lock) ist installiert und selbstgeprueft." -ForegroundColor Green
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

# Who installed this and what it holds, for the next installer run of another account.
try {
    Set-Content -LiteralPath $InstalledByFile -Value $SignedInAccount -Encoding UTF8
    if ($CheckoutState) { Set-Content -LiteralPath $InstalledStateFile -Value $CheckoutState -Encoding UTF8 }
} catch {
    Write-Warning "Konnte nicht festhalten, wer installiert hat ($_); ein zweites Konto installiert dann den gemeinsamen Teil noch einmal."
}
}
$LauncherBat = Join-Path $InstallDir "start_framework.bat"
$PythonVenv = Join-Path $InstallDir ".venv\Scripts\python.exe"
Set-Location $InstallDir

# 6. SHORTCUT: on the public desktop, so every account on this computer has it (one shared installation).
Write-Host "[5/5] Erstelle Desktop Verknuepfung..." -ForegroundColor Yellow
$WshShell = New-Object -comObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
# An earlier installer put it on the desktop of the account that ran it; that one would now be there twice.
$OwnShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"
try {
    if ((Test-Path -LiteralPath $OwnShortcut) -and ($WshShell.CreateShortcut($OwnShortcut).TargetPath -eq $LauncherBat)) { Remove-Item -LiteralPath $OwnShortcut -Force }
} catch { }

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

# 7-ZIP: the harness take-in reads rar, 7z, iso, cpio, zstd and disk images with it (orchestrator/utils/archives.py:
# 7zz or 7z on PATH, or the usual install folder). Installed with winget only when none is there, like Python and
# Podman above; a failure never stops the installation (zip, tar and folders need no 7-Zip).
Write-Host "[7-Zip] Pruefe 7-Zip (fuer Harness-Archive wie rar, 7z, iso und Disk-Images)..." -ForegroundColor Yellow
try {
    $SevenZipExe = Join-Path $env:ProgramFiles "7-Zip\7z.exe"
    $SevenZipThere = (Get-Command "7z" -ErrorAction SilentlyContinue) -or (Get-Command "7zz" -ErrorAction SilentlyContinue) -or (Test-Path $SevenZipExe)
    if ((-not $SevenZipThere) -and $Offline) {
        Write-Warning "Offline: 7-Zip fehlt, und ohne Netz installiert der Installer es nicht (winget). Die Installation geht weiter; rar, 7z, iso und Disk-Images brauchen 7-Zip (spaeter: winget install -e --id 7zip.7zip, oder sein Installer von einem Rechner mit Netz)."
    } elseif (-not $SevenZipThere) {
        Write-Host "7-Zip fehlt. Installiere es ueber winget (7zip.7zip)..."
        winget install -e --id 7zip.7zip --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { Write-Warning "7-Zip liess sich nicht mit winget installieren; selbst: 7-Zip fuer Windows installieren (winget install -e --id 7zip.7zip). Ohne 7-Zip nimmt das Framework einen Harness als zip, tar oder Ordner an; rar, 7z, iso und Disk-Images brauchen 7-Zip." }
    }
} catch {
    Write-Warning "7-Zip: $_ - die Installation geht weiter."
}

# CONTAINER ENGINE: without administrator rights by default (docs/DESIGN.md, no standing rights that can do more than
# needed). Podman with its machine (a WSL 2 machine, rootless by default) for the signed-in person; Docker Desktop
# only with --rootful, after a warning: its group docker-users controls a service with administrator rights, so the
# account has administrator-equivalent rights on this computer, permanently. The installer never changes a group
# membership; it names docker-users when the account is in it. A step that fails never stops the installation.
Write-Host "[Engine] Container-Engine (ohne Administratorrechte, ausser mit --rootful)..." -ForegroundColor Yellow
$SignedIn = $null
$Elevated = $null
try {
    $SignedIn = (Get-CimInstance Win32_ComputerSystem).UserName
    $Elevated = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
} catch {
    $SignedIn = $null
}
try {
    if ($Rootful) {
        Write-Warning "--rootful: Docker Desktop laeuft mit einem Dienst mit Administratorrechten, und wer in der Gruppe docker-users ist, steuert ihn. Damit hat das Konto dauerhaft Rechte wie ein Administrator auf diesem Rechner, auch wenn das Framework nicht laeuft. Ohne --rootful richtet der Installer Podman ohne Administratorrechte ein, mit dem das Framework genauso baut."
        if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
            winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -ne 0) { Write-Warning "Docker Desktop liess sich nicht mit winget installieren; selbst: https://docs.docker.com/desktop/setup/install/windows-install/" }
        }
    } elseif ($Offline -and -not (Get-Command "podman" -ErrorAction SilentlyContinue)) {
        Write-Warning "Offline: Es gibt noch keine Container-Engine, und ohne Netz installiert der Installer keine (Podman: winget install -e --id RedHat.Podman, oder sein Installer von einem Rechner mit Netz). Die Installation geht weiter; Builds brauchen sie."
    } else {
        if (-not (Get-Command "podman" -ErrorAction SilentlyContinue)) {
            Write-Host "Podman fehlt. Installiere es ueber winget (RedHat.Podman)..."
            winget install -e --id RedHat.Podman --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        }
        if (-not (Get-Command "podman" -ErrorAction SilentlyContinue)) {
            Write-Warning "Podman liess sich nicht einrichten (Meldungen oben). Selbst: Podman Desktop installieren (https://podman-desktop.io), dort eine Podman-Maschine anlegen (ohne Administratorrechte, rootful aus) und starten."
        } elseif ($SignedIn -and ($SignedIn -ne $Elevated)) {
            Write-Warning "Die Podman-Maschine wird fuer $SignedIn angelegt, nicht fuer das Administratorkonto. Spaeter als du selbst (normales Terminal): podman machine init; podman machine start"
        } else {
            & podman machine inspect *> $null
            if ($LASTEXITCODE -ne 0) { & podman machine init }
            & podman machine start
            if ($LASTEXITCODE -ne 0) { Write-Warning "Die Podman-Maschine liess sich nicht starten (Meldungen oben). Spaeter: podman machine start" }
        }
    }
    $Groups = (& whoami /groups /fo csv /nh) -join "`n"
    if ($Groups -match '(^|\\|")docker-users"') {
        Write-Warning "Dein Konto ist in der Gruppe docker-users: Wer darin ist, steuert Docker Desktops Dienst mit Administratorrechten und hat damit dauerhaft Rechte wie ein Administrator auf diesem Rechner. Das Framework braucht die Gruppe nicht (es nimmt Podman ohne Administratorrechte). Herausnehmen, wenn nichts anderes sie braucht: in einem Terminal als Administrator 'net localgroup docker-users <dein Konto> /delete', danach neu anmelden."
    }
} catch {
    Write-Warning "Container-Engine: $_ - die Installation geht weiter. Mojo (llm-cli mojo) nennt spaeter, was fehlt."
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
    } elseif ($Offline) {
        Write-Host "Offline: Dittos Laufzeit wird nicht eingerichtet (sie laedt Quellcode und Modell). Spaeter mit Netz: llm-cli ditto runtime ensure; ohne Netz aus einer Laufzeit, die ein anderer Rechner exportiert hat: llm-cli ditto runtime import"
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

# MODUS: der gewaehlte Framework-Modus (llm-cli config set framework_mode), fuer die angemeldete Person selbst (ihre
# config.yml). Ohne Wahl tut der Schritt nichts; ein Fehler laesst die Installation nicht scheitern.
if ($ModeChoice) {
    Write-Host "[Modus] Framework-Modus: $ModeChoice..." -ForegroundColor Yellow
    try {
        $SignedIn = (Get-CimInstance Win32_ComputerSystem).UserName
        $Elevated = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        if ($SignedIn -and ($SignedIn -ne $Elevated)) {
            Write-Warning "Der Framework-Modus wird fuer $SignedIn gesetzt, nicht fuer das Administratorkonto. Spaeter als du selbst (normales Terminal, im Ordner $InstallDir): .venv\Scripts\python.exe -m orchestrator.cli config set framework_mode $ModeChoice"
        } else {
            & $PythonVenv -m orchestrator.cli config set framework_mode $ModeChoice
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Der Framework-Modus liess sich nicht setzen (Meldungen oben). Spaeter: llm-cli config set framework_mode $ModeChoice"
            }
        }
    } catch {
        Write-Warning "Framework-Modus: $_ - die Installation ist trotzdem fertig. Spaeter: llm-cli config set framework_mode $ModeChoice"
    }
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
    Write-Host "Vor dem Entfernen aus der neuen einrichten (llm-cli mcp config, siehe README), im Ordner ${InstallDir}:"
    Write-Host "  .venv\Scripts\python.exe -m orchestrator.cli mcp config"
    Write-Host "Entfernen: scripts\Uninstall-LLM-Cross-Compiler-Framework.bat ausfuehren. Es findet beide Installationen,"
    Write-Host "zeigt fuer jede, was es entfernt, und fragt vorher (ohne Antwort: Nein)."
}
