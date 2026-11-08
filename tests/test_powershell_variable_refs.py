"""PowerShell reads `$Name:` as a drive-qualified variable (like `$env:PATH`), so a plain variable followed by a
colon inside a string, e.g. "im Ordner $InstallDir:", stops the whole script at parse time ("Ungueltiger
Variablenverweis"), before anything runs. install.bat runs its PowerShell part through Invoke-Expression, so one such
line breaks the Windows installation with and without administrator rights. Such a reference must be written
`${Name}:`."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "install.bat", *sorted((ROOT / "scripts").glob("*.ps1")), *sorted(ROOT.glob("*.ps1"))]
# Scope modifiers and the variable/function/alias drives are the only names PowerShell allows before the colon.
ALLOWED = {"env", "script", "global", "local", "private", "using", "variable", "function", "alias", "workflow"}
DRIVE_REF = re.compile(r"(?<![`\w{])\$([A-Za-z_][A-Za-z0-9_]*):(?!:)")


def drive_refs(text: str):
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("#", "REM ", "rem ", "::")):
            continue
        for match in DRIVE_REF.finditer(line):
            if match.group(1).lower() not in ALLOWED:
                yield number, line.strip()


@pytest.mark.parametrize("path", [p for p in FILES if p.is_file()], ids=lambda p: p.name)
def test_no_plain_variable_is_followed_by_a_colon(path):
    found = list(drive_refs(path.read_text(encoding="utf-8", errors="replace")))
    assert not found, f"write ${{Name}}: instead of $Name: in {path.name}: {found}"


def test_the_check_finds_the_broken_form_and_accepts_the_right_ones():
    assert list(drive_refs('Write-Host "im Ordner $InstallDir:"'))
    assert not list(drive_refs('Write-Host "im Ordner ${InstallDir}:"'))
    assert not list(drive_refs('$x = $env:LOCALAPPDATA; $script:Count = 1; [IO.Path]::Combine($a, $b)'))
    assert not list(drive_refs('Write-Host "Preis: `$Teuer: nein"'))
