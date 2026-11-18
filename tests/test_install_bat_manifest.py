"""The Windows installer and uninstaller and the install manifest (install.bat, scripts/Uninstall-*.bat; the maintainer's rule of
2026-11-18): the installer writes down only the programs it installed itself, in the manifest shape rag4ll uses, and the
uninstaller removes exactly those. The PowerShell function runs in a real pwsh where there is one (CI's Linux runner has it;
Windows PowerShell 5.1 is not tested here); the batch files are checked by their text, because cmd.exe is not here."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from orchestrator.utils import install_manifest as im

INSTALL_BAT = (REPO_ROOT / "install.bat").read_text(encoding="utf-8")
UNINSTALL_BAT = (REPO_ROOT / "scripts" / "Uninstall-LLM-Cross-Compiler-Framework.bat").read_text(encoding="utf-8")
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="needs pwsh")


def install_line(ref: str) -> int:
    """Where the installer really runs `winget install` for `ref` (not the dry-run's list of what it would do)."""
    match = re.search(rf"^\s+winget install -e --id {re.escape(ref)}\b.*--accept-source-agreements", INSTALL_BAT, re.M)
    assert match, ref
    return match.start()


def function_block() -> str:
    return INSTALL_BAT[INSTALL_BAT.index("# MANIFEST-BEGIN"):INSTALL_BAT.index("# MANIFEST-END")]


def run_pwsh(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", function_block() + "\n" + script],
                          capture_output=True, text=True, timeout=120, cwd=tmp_path,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
                               "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"})


class TestTheInstallerWritesDownOnlyWhatItInstalled:

    def test_every_record_follows_the_winget_install_it_belongs_to_and_only_a_successful_one(self):
        for ref, name in (("Python.Python.3.13", "python"), ("7zip.7zip", "7zip"), ("Docker.DockerDesktop", "docker-desktop"),
                          ("RedHat.Podman", "podman")):
            install = install_line(ref)
            record = INSTALL_BAT.index(f'Add-InstalledByUs "{name}" "{ref}"')
            assert 0 < record - install < 700, ref                         # right behind its own install
            line = INSTALL_BAT[INSTALL_BAT.rfind("\n", 0, record) + 1:INSTALL_BAT.index("\n", record)]
            assert "LASTEXITCODE" in line or "else" in line, f"{ref}: recorded only when winget succeeded"

    def test_each_install_sits_in_an_if_not_exist_branch_so_only_a_missing_program_is_ever_recorded(self):
        for ref, probe in (("Python.Python.3.13", 'if (-not (Get-Command "python"'), ("7zip.7zip", "elseif (-not $SevenZipThere)"),
                           ("Docker.DockerDesktop", 'if (-not (Get-Command "docker"'),
                           ("RedHat.Podman", 'if (-not (Get-Command "podman"')):
            install = install_line(ref)
            assert INSTALL_BAT.rfind(probe, 0, install) != -1 and install - INSTALL_BAT.rfind(probe, 0, install) < 900, ref

    def test_the_manifest_is_written_after_the_installation_exists_and_a_failure_only_warns(self):
        save = INSTALL_BAT.index('Save-InstallManifest (Join-Path $InstallDir "install-manifest.json") $InstalledByUs')
        assert INSTALL_BAT.index("Set-Content -Path (Join-Path $ScriptPath \".install_complete\")") < save
        around = INSTALL_BAT[save - 20:save + 300]
        assert "catch" in around and "laesst es dann in Ruhe" in around and "Write-Error" not in around

    def test_the_file_is_named_like_the_one_of_the_framework_and_the_uninstaller(self):
        assert "install-manifest.json" in INSTALL_BAT and "install-manifest.json" in UNINSTALL_BAT and im.FILE_NAME == "install-manifest.json"

    @needs_pwsh
    def test_the_function_writes_the_shape_the_python_side_reads_and_marks_programs_shared(self, tmp_path):
        target = tmp_path / "install-manifest.json"
        run = run_pwsh(f'Add-InstalledByUs "7zip" "7zip.7zip" "7-Zip"; Add-InstalledByUs "podman" "RedHat.Podman" "Podman"; '
                       f'Save-InstallManifest "{target}" $InstalledByUs', tmp_path)
        assert run.returncode == 0, run.stdout + run.stderr
        raw = target.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 without a BOM"
        entries = {e.id: e for e in im.read(target)}
        assert set(entries) == {"7zip", "podman"} and all(e.scope == "shared" and e.kind == "winget" for e in entries.values())
        assert entries["7zip"].ref == "7zip.7zip" and "registered_at" in json.loads(raw)["7zip"]

    @needs_pwsh
    def test_what_was_recorded_before_and_what_another_program_wrote_stays(self, tmp_path):
        target = tmp_path / "install-manifest.json"
        im.record(target, im.Entry("python", "shared", "winget", "Python.Python.3.13", "Python 3.13"))
        data = json.loads(target.read_text(encoding="utf-8"))
        data["rag-host"] = {"install_dir": "C:/rag", "services": ["rag-api"]}
        target.write_text(json.dumps(data), encoding="utf-8")
        run = run_pwsh(f'Add-InstalledByUs "7zip" "7zip.7zip" "7-Zip"; Save-InstallManifest "{target}" $InstalledByUs', tmp_path)
        assert run.returncode == 0, run.stdout + run.stderr
        after = json.loads(target.read_text(encoding="utf-8"))
        assert set(after) == {"python", "rag-host", "7zip"} and after["rag-host"]["services"] == ["rag-api"]

    @needs_pwsh
    def test_a_manifest_that_cannot_be_read_is_never_overwritten(self, tmp_path):
        target = tmp_path / "install-manifest.json"
        target.write_text("{ this is not json", encoding="utf-8")
        run = run_pwsh(f'Add-InstalledByUs "7zip" "7zip.7zip" "7-Zip"; Save-InstallManifest "{target}" $InstalledByUs', tmp_path)
        assert run.returncode == 0 and target.read_text(encoding="utf-8") == "{ this is not json"
        assert "nicht lesbar" in run.stdout + run.stderr

    @needs_pwsh
    def test_nothing_installed_by_the_installer_writes_no_file(self, tmp_path):
        target = tmp_path / "install-manifest.json"
        run = run_pwsh(f'Save-InstallManifest "{target}" $InstalledByUs', tmp_path)
        assert run.returncode == 0 and not target.exists()           # Python and 7-Zip were there: nothing to write down


class TestTheUninstallerIsFedByTheManifest:

    def test_it_calls_the_module_of_the_installation_before_anything_is_removed(self):
        for label in (":DO_PARTIAL_UNINSTALL", ":DO_FULL_UNINSTALL"):
            body = UNINSTALL_BAT[UNINSTALL_BAT.index(label):]
            body = body[:body.index("GOTO :CLEANUP_SHORTCUTS")]
            assert "CALL :COMPONENTS" in body and body.index("CALL :COMPONENTS") < body.index("rmdir /s /q"), label
        routine = UNINSTALL_BAT[UNINSTALL_BAT.index(":COMPONENTS\r\nSET") if ":COMPONENTS\r\nSET" in UNINSTALL_BAT
                                else UNINSTALL_BAT.index(":COMPONENTS\nSET"):]
        routine = routine[:routine.index("GOTO :EOF", routine.index("popd"))]
        assert "orchestrator.Core.uninstall_components uninstall" in routine
        assert '--machine "!INSTALL_PATH!\\install-manifest.json"' in routine and "install-manifest.json" in routine.split("--person")[1]
        assert ".llm-framework" in routine and "LLM_FRAMEWORK_HOME" in routine
        assert routine.index("pushd") < routine.index("uninstall_components") < routine.index("popd")

    def test_without_the_python_environment_it_says_so_and_removes_nothing_extra(self):
        routine = UNINSTALL_BAT[UNINSTALL_BAT.index(":COMPONENTS"):]
        assert 'IF NOT EXIST "!VENV_PY!" GOTO :EOF' in routine and "wird nicht angeboten" in routine

    def test_the_uninstaller_never_names_a_program_by_itself(self):
        lowered = UNINSTALL_BAT.lower()
        assert "winget uninstall" not in lowered and "7zip" not in lowered and "podman" not in lowered   # only what the manifest says
        assert "python.python" not in lowered and "docker.dockerdesktop" not in lowered

    def test_both_batch_files_keep_their_windows_line_endings(self):
        for path in (REPO_ROOT / "install.bat", REPO_ROOT / "scripts" / "Uninstall-LLM-Cross-Compiler-Framework.bat"):
            data = path.read_bytes()
            assert data.count(b"\r\n") > 100 and data.count(b"\n") == data.count(b"\r\n"), path.name


class TestTheCheckOfThePowerShellPart:

    def test_the_check_script_exists_and_ci_runs_it(self):
        script = (REPO_ROOT / "scripts" / "Check-PowerShell.ps1").read_text(encoding="utf-8")
        assert "ParseFile" in script and "PSScriptAnalyzer" in script and "-Severity Error" in script
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "./scripts/Check-PowerShell.ps1" in ci and re.search(r"shell: pwsh\s+run: \./scripts/Check-PowerShell\.ps1", ci)

    @needs_pwsh
    def test_install_bat_parses_without_an_error_in_powershell(self):
        run = subprocess.run([PWSH, "-NoProfile", "-File", str(REPO_ROOT / "scripts" / "Check-PowerShell.ps1"), "-SkipAnalyzer"],
                             capture_output=True, text=True, timeout=120,
                             env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp",
                                  "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"})
        assert run.returncode == 0 and "no parse errors" in run.stdout, run.stdout + run.stderr

    @needs_pwsh
    def test_the_check_fails_on_a_syntax_error(self, tmp_path):
        broken = tmp_path / "broken.bat"
        broken.write_text("<# :\r\n@echo off\r\n#>\r\nif ($x -eq ) { Write-Host 'x' \r\n", encoding="utf-8")
        run = subprocess.run([PWSH, "-NoProfile", "-File", str(REPO_ROOT / "scripts" / "Check-PowerShell.ps1"), "-Path", str(broken),
                              "-SkipAnalyzer"], capture_output=True, text=True, timeout=120,
                             env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp",
                                  "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1"})
        assert run.returncode == 1 and "syntax: line" in run.stdout
