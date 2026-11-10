# preload.ps1 - The installer's preload, on Windows: run on a computer WITH a network, it puts everything install.bat
# (or install.sh) would fetch from the network for a computer WITHOUT one into one archive (.tar.gz) that the person
# carries over (a USB stick). There: install.bat --offline --preload=FILE (from the same checkout). Without --preload,
# install.bat --offline writes a preload.ps1 and preload.sh for exactly that computer itself (they run without a
# checkout) and waits for the archive's path; this script makes the same archive beforehand, from the checkout.
#
#   powershell -ExecutionPolicy Bypass -File preload.ps1 [-Target windows-amd64|windows-arm64|linux-x86_64|linux-aarch64]
#                                                        [-Python X.Y[.Z]] [-Glibc 2.NN] [-Headless] [-Out FOLDER]
#
# Without options the target is this computer (its platform and Python). For another computer: -Target, -Python (the
# Python the installer builds the .venv with there) and for Linux -Glibc (getconf GNU_LIBC_VERSION there); for a Linux
# target the list of system packages is made by preload.sh (it knows the package managers), here it says so.
# The archive llmcf-preload-VERSION-TARGET-pyX.Y.tar.gz holds llmcf-preload\ with manifest.txt, wheels\ (every package
# of requirements-gui.txt, with -Headless of requirements.txt: a wheel for the target where there is one, else the
# source archive, each checked against the lock's hashes), build\ (setuptools and wheel to build such an archive
# offline), tools\ (uv, pinned in requirements-resolve.txt), system-packages.txt and SHA256SUMS. Writing and fetching
# is done by scripts\preload_lock.py (spec, pack) with this computer's Python and pip, so no tar or zip tool is needed.
# No signature: the computer without a network checks every package against the lock's hashes in its checkout (pip
# --require-hashes), the same anchor as with a network; the archive's sha256 is printed to compare (the installer
# prints it too and checks it with --preload-sha256=HEX).
param(
    [string]$Target = "",
    [string]$Python = "",
    [string]$Glibc = "",
    [switch]$Headless,
    [string]$Out = (Get-Location).Path
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tool = Join-Path $Root "scripts\preload_lock.py"

# The Python that fetches (any Python 3 with pip): PRELOAD_PYTHON, else py -3, else python.
$Py = $null
$PyArgs = @()
if ($env:PRELOAD_PYTHON) { $Py = $env:PRELOAD_PYTHON }
elseif (Get-Command "py" -ErrorAction SilentlyContinue) { $Py = "py"; $PyArgs = @("-3") }
elseif (Get-Command "python" -ErrorAction SilentlyContinue) { $Py = "python" }
if (-not $Py) { Write-Error "preload: Python 3 with pip is needed (winget install -e --id Python.Python.3.13; or PRELOAD_PYTHON)." }

$Here = @(& $Py @PyArgs -I $Tool this-computer)
if (-not $Target) { $Target = $Here[0] }
if (-not $Python) {
    if ($Target -eq $Here[0]) { $Python = $Here[1] } else { Write-Error "preload: for another target than this computer, name the target's Python: -Python X.Y" }
}
if ($Target -like "linux-*" -and -not $Glibc) { $Glibc = "2.28" }

$Lock = if ($Headless) { "requirements.txt" } else { "requirements-gui.txt" }
$Version = ""
$Match = Select-String -Path (Join-Path $Root "orchestrator\__init__.py") -Pattern '^__version__\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
if ($Match) { $Version = $Match.Matches[0].Groups[1].Value }

$Work = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-preload-" + [Guid]::NewGuid().ToString("N"))
$Spec = Join-Path $Work "spec"
New-Item -ItemType Directory -Path $Work -Force | Out-Null
try {
    $Packages = Join-Path $Work "system-packages.txt"
    if ($Target -like "windows-*") {
        $Text = @(
            "# Systempakete, die install.bat auf dem Zielrechner installieren wuerde, wenn sie fehlen (mit Netz ueber winget).",
            "# Offline installiert er keine: Bring die Installer von einem Rechner mit Netz mit.",
            "python: winget install -e --id Python.Python.3.13",
            "7zip: winget install -e --id 7zip.7zip",
            "container-engine: winget install -e --id RedHat.Podman (mit --rootful: Docker.DockerDesktop)") -join "`n"
    } else {
        $Text = "# Linux target: the system packages depend on its package manager; preload.sh --os-release FILE lists them, and install.sh names what is missing."
    }
    [IO.File]::WriteAllText($Packages, $Text + "`n", (New-Object System.Text.UTF8Encoding $false))
    & $Py @PyArgs -I $Tool spec --requirements (Join-Path $Root $Lock) --resolve (Join-Path $Root "requirements-resolve.txt") --target $Target --python $Python --glibc $Glibc --version $Version --system-packages $Packages --out $Spec | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "preload: target $Target, Python $Python is not possible like this (above)." }
    & $Py @PyArgs -I $Tool pack --spec $Spec --out $Out
    $Code = $LASTEXITCODE
} finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
exit $Code
