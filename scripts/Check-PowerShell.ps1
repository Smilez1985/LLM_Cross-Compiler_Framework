<#
.SYNOPSIS
    Checks the PowerShell part of install.bat: its syntax, and PSScriptAnalyzer's errors.

.DESCRIPTION
    install.bat is a batch file and a PowerShell script in one (its first lines are a PowerShell comment that holds the batch
    part). This copies it to a temporary .ps1, parses it with PowerShell's own parser and lists every parse error with its
    line. Then it runs PSScriptAnalyzer at the severity Error (style warnings, such as Write-Host in an installer that talks to
    a person, do not fail the check). Exit code 0 when both are clean, 1 otherwise.

    CI runs it in the Lint job (.github/workflows/ci.yml); run it yourself with:  pwsh scripts/Check-PowerShell.ps1

.PARAMETER Path
    The file to check (default: install.bat next to this script's folder).

.PARAMETER SkipAnalyzer
    Only the syntax check; for a computer that has no PSScriptAnalyzer and no network.
#>
param(
    [string]$Path = (Join-Path (Split-Path -Parent $PSScriptRoot) "install.bat"),
    [switch]$SkipAnalyzer
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Path)) { Write-Error "Not found: $Path" }
$Copy = Join-Path ([System.IO.Path]::GetTempPath()) ("check-" + [System.IO.Path]::GetRandomFileName() + ".ps1")
Copy-Item -LiteralPath $Path -Destination $Copy
$Failed = $false
try {
    $Tokens = $null
    $Errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Copy, [ref]$Tokens, [ref]$Errors)
    if ($Errors.Count -gt 0) {
        $Failed = $true
        foreach ($Problem in $Errors) { Write-Host ("syntax: line {0}: {1}" -f $Problem.Extent.StartLineNumber, $Problem.Message) }
    } else {
        Write-Host "syntax: no parse errors"
    }
    if (-not $SkipAnalyzer) {
        if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
            Install-Module -Name PSScriptAnalyzer -RequiredVersion 1.24.0 -Scope CurrentUser -Force -Repository PSGallery
        }
        Import-Module PSScriptAnalyzer
        $Findings = @(Invoke-ScriptAnalyzer -Path $Copy -Severity Error)
        foreach ($Finding in $Findings) {
            Write-Host ("analyzer: line {0} [{1}] {2}" -f $Finding.Line, $Finding.RuleName, $Finding.Message)
        }
        if ($Findings.Count -gt 0) { $Failed = $true } else { Write-Host "analyzer: no errors" }
    }
} finally {
    Remove-Item -LiteralPath $Copy -Force -ErrorAction SilentlyContinue
}
if ($Failed) { exit 1 }
exit 0
