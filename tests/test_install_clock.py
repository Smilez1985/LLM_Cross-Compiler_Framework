"""The installer says why HTTPS failed: a clock that is wrong is named, not a proxy (docs/DESIGN.md, Installer,
platforms and dependencies).

A certificate or TLS failure (curl's exit codes 35, 60 and the like; wget's 5) is checked against the time: the clock is
compared with the server's Date header, fetched once more without the certificate check and read for its date only.
Off by more than five minutes: both times and the fix (timedatectl set-ntp true; the hardware clock of a computer that
also starts Windows) before any proxy hint, and with the person's yes the installer has the clock set and tries again.
Offline nothing is checked. Every run here is install.sh sourced, with fake curl, wget and timedatectl on the PATH;
nothing reaches the network.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_BAT = REPO_ROOT / "install.bat"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists(), reason="needs bash")
REAL = ("bash", "sh", "date", "awk", "tr", "sed", "cat", "grep", "basename", "dirname", "realpath", "mktemp", "rm",
        "printf", "sleep", "head", "touch")
OLD_DATE = "Mon, 01 Jan 2024 10:00:00 GMT"


def fake_bin(tmp_path: Path, curl_rc: int = 60, date_header=OLD_DATE, curl_ok_after_sync=False, wget=False):
    """A PATH of fakes: curl (or wget) fails with curl_rc and answers -I with the Date header; timedatectl logs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in REAL:
        if shutil.which(tool):
            (bin_dir / tool).symlink_to(shutil.which(tool))
    log = tmp_path / "calls.log"
    header = f'printf "HTTP/1.1 200 OK\\r\\nDate: {date_header}\\r\\n\\r\\n"' if date_header else 'printf "HTTP/1.1 200 OK\\r\\n\\r\\n"'
    ok = f'[ -f "{tmp_path}/synced" ] && exit 0' if curl_ok_after_sync else ":"
    if wget:
        (bin_dir / "wget").write_text(
            f'#!/bin/sh\necho "wget $*" >> "{log}"\ncat > /dev/null\n'
            f'case " $* " in *" -S "*) {header.replace("HTTP/1.1", "  HTTP/1.1").replace("Date:", "  Date:")} >&2; exit 0 ;; esac\n'
            f'{ok}\nexit {curl_rc}\n')
        (bin_dir / "wget").chmod(0o755)
    else:
        (bin_dir / "curl").write_text(
            f'#!/bin/sh\necho "curl $*" >> "{log}"\ncat > /dev/null\n'
            f'case " $* " in *" -I "*) {header}; exit 0 ;; esac\n{ok}\nexit {curl_rc}\n')
        (bin_dir / "curl").chmod(0o755)
    (bin_dir / "timedatectl").write_text(
        f'#!/bin/sh\necho "timedatectl $*" >> "{log}"\n'
        f'case "$*" in "set-ntp true") touch "{tmp_path}/synced" ;; *NTPSynchronized*) echo yes ;; esac\nexit 0\n')
    (bin_dir / "timedatectl").chmod(0o755)
    return bin_dir, log


def run(tmp_path: Path, script: str, bin_dir: Path, answer=None, **env):
    tty = tmp_path / "tty"
    if answer is not None:
        tty.write_text(answer + "\n")
    code = f'source "$1"; ASK_TTY="{tty}"; CLOCK_SYNC_WAIT=0; {script}'
    return subprocess.run(["/bin/bash", "-c", code, "test", str(INSTALL_SH)],
                          env={"PATH": str(bin_dir), "HOME": str(tmp_path), **env}, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)


@needs_bash
class TestTheIndexCheck:

    def test_a_wrong_clock_is_named_with_both_times_and_the_fix_not_a_proxy(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=60)
        result = run(tmp_path, "wait_for_internet", bin_dir)
        assert result.returncode == 1
        out = result.stdout
        assert "Uhr dieses Rechners geht falsch" in out and "2024-01-01 10:00:00 UTC" in out
        assert "timedatectl set-ntp true" in out and "timedatectl set-local-rtc 1" in out and "Windows" in out
        assert "Proxy?" not in out and "ein Proxy ist hier nicht die Ursache" in out
        calls = log.read_text()
        assert "-k" in calls and "-I" in calls, "the Date header is fetched without the certificate check"
        assert "set-ntp" not in calls, "without a yes the clock is never set"

    @pytest.mark.parametrize("code", [35, 51, 58, 60, 77, 83, 90])
    def test_every_tls_exit_code_leads_to_the_clock(self, tmp_path, code):
        bin_dir, _ = fake_bin(tmp_path, curl_rc=code)
        result = run(tmp_path, "wait_for_internet", bin_dir)
        assert result.returncode == 1 and "Uhr dieses Rechners geht falsch" in result.stdout

    def test_a_right_clock_names_the_proxy_or_mirror(self, tmp_path):
        now = subprocess.run(["date", "-u", "+%a, %d %b %Y %H:%M:%S GMT"], capture_output=True, text=True,
                             env={"LC_ALL": "C"}).stdout.strip()
        bin_dir, log = fake_bin(tmp_path, curl_rc=60, date_header=now)
        result = run(tmp_path, "wait_for_internet", bin_dir)
        assert result.returncode == 1
        assert "die Uhr dieses Rechners stimmt" in result.stdout and "Proxy oder Spiegel" in result.stdout
        assert "geht falsch" not in result.stdout

    def test_without_a_date_header_the_clock_is_named_first(self, tmp_path):
        bin_dir, _ = fake_bin(tmp_path, curl_rc=60, date_header="")
        result = run(tmp_path, "wait_for_internet", bin_dir)
        out = result.stdout
        assert result.returncode == 1 and "ließ sich nicht lesen" in out
        assert out.index("Prüfe zuerst die Uhr") < out.index("Proxy oder Spiegel")

    def test_a_failure_that_is_not_tls_gives_the_proxy_hint_and_no_date_request(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=7)
        result = run(tmp_path, "wait_for_internet", bin_dir)
        assert result.returncode == 1 and "Proxy?" in result.stdout and "sudo -E" in result.stdout
        assert " -I " not in log.read_text()

    def test_wget_reports_tls_with_5_and_the_clock_is_checked_too(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=5, wget=True)
        result = run(tmp_path, "wait_for_internet", bin_dir)
        assert result.returncode == 1 and "Uhr dieses Rechners geht falsch" in result.stdout
        assert "--no-check-certificate" in log.read_text()

    def test_offline_nothing_is_checked(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=60)
        result = run(tmp_path, "OFFLINE=1; wait_for_internet", bin_dir)
        assert result.returncode == 0 and "Offline" in result.stdout
        assert not log.exists() or "curl" not in log.read_text()

    def test_with_a_yes_the_clock_is_set_and_the_check_runs_again(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=60, curl_ok_after_sync=True)
        result = run(tmp_path, "wait_for_internet && echo CONTINUED", bin_dir, answer="j")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "CONTINUED" in result.stdout and "Uhr dieses Rechners geht falsch" in result.stdout
        calls = log.read_text()
        assert "timedatectl set-ntp true" in calls
        assert calls.count("-fsS") == 2, "one more try after the clock was set"

    def test_with_a_no_nothing_is_changed_and_the_installer_stops(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=60, curl_ok_after_sync=True)
        result = run(tmp_path, "wait_for_internet", bin_dir, answer="n")
        assert result.returncode == 1 and "set-ntp" not in log.read_text()

    def test_still_failing_after_the_clock_was_set_stops_once(self, tmp_path):
        bin_dir, log = fake_bin(tmp_path, curl_rc=60)
        result = run(tmp_path, "wait_for_internet", bin_dir, answer="j")
        assert result.returncode == 1
        assert log.read_text().count("set-ntp true") == 1


@needs_bash
def test_the_docker_key_download_names_the_clock_too(tmp_path):
    bin_dir, _ = fake_bin(tmp_path, curl_rc=60)
    result = run(tmp_path, 'DOCKER_KEY_CURL_RC=60; docker_key_problem debian 2 X /k', bin_dir)
    assert "Signaturschlüssel ließ sich nicht" in result.stdout and "Uhr dieses Rechners geht falsch" in result.stdout


def test_install_bat_checks_the_index_and_the_clock_before_it_installs():
    text = INSTALL_BAT.read_bytes().decode("utf-8")
    assert "\r\n" in text and text.count("\n") == text.count("\r\n"), "cmd needs CRLF line endings"
    check, python = text.index("Test-Https $IndexProbe"), text.index('[1/5] Pruefe Python Umgebung')
    assert check < python, "the HTTPS check runs before anything is installed"
    block = text[text.index("if (-not $Offline) {\r\n    Write-Host \"Pruefe Internetverbindung"):python]
    for needed in ("Test-TlsFailure", "Get-ServerDate", "TrustFailure", "SecureChannelFailure", "$ClockTolerance",
                   "w32tm /resync", "Read-Host", "RealTimeIsUniversal", "timedatectl set-local-rtc 1"):
        assert needed in block or needed in text, needed
    # the offer only at an interactive console, and only after a yes
    assert block.index("UserInteractive") < block.index("Read-Host") < block.index("Start-Service")
    # only the one request for the date accepts any certificate
    assert text.count("ServerCertificateValidationCallback") == 1
    assert "ServicePointManager]::ServerCertificateValidationCallback" not in text
