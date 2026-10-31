"""install.sh on every Linux family through the family's own package manager (DECISIONS round 24 PKG, round 23 item 3).

The family comes from ID and ID_LIKE in /etc/os-release (the table of the hardware probe, tables.os_families):
apt (Debian, Ubuntu, Raspberry Pi OS and the systems built on them), dnf (Fedora, RHEL, CentOS Stream, Rocky Linux,
AlmaLinux, Oracle Linux, Amazon Linux), zypper (openSUSE Leap and Tumbleweed, SLES), pacman (Arch, Manjaro,
EndeavourOS) and apk (Alpine). Everything install.sh installs (Python 3.11 to 3.14, rsync, Docker) goes through it,
with the distribution's real package names; nothing is downloaded and run. A third-party repository (Docker's, for
Fedora, RHEL and CentOS Stream as for apt) is trusted only after its key matches the pinned fingerprint; every other
family gets the distribution's own package, checked by its package manager. Any other family stops in plain words with
the family and the manual install page.

Every run here is in a sandbox: install.sh is sourced, its system paths point into the test folder, and the package
managers (apt-get, dnf, zypper, pacman, apk), rpm, systemctl, OpenRC, SUSEConnect, curl, uname, groups, usermod and
addgroup are fakes that write down their calls; installing a package puts its command (python3, python3.12, docker,
rsync, curl) on the sandbox PATH. gpg is the real one, with keys made for the test. The os-release files are those of
the real distributions. `install.sh --dry-run` shows the plan from the same decisions and calls nothing.
"""

import getpass
import os
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT
from test_install_docker import export, keys_in, make_key

INSTALL_SH = REPO_ROOT / "install.sh"
SCHEMA = REPO_ROOT / "configs" / "hardware_probe_schema.yml"
DEB_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
RPM_FINGERPRINT = "060A61C51B558A7F742B77AAC52FEB6B621E9F35"  # docs.docker.com/engine/install (fedora, rhel, centos)
DOCKER_MANUAL = "https://docs.docker.com/engine/install/"
MANUAL = "https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/blob/main/README_DE.md#voraussetzungen"
DOCKER_CE = "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
REAL_TOOLS = ("bash", "gpg", "awk", "grep", "install", "mktemp", "rm", "dirname", "basename", "cat", "date", "sed",
              "cp", "mkdir", "chmod", "sort", "comm", "realpath", "id", "find", "chown", "ls")

needs_bash_and_gpg = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists() or not shutil.which("gpg"),
                                        reason="install.sh needs bash; the key check needs gpg")

# /etc/os-release of the real distributions (the fields install.sh reads, as they ship them).
OS_RELEASE = {
    "debian": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\nVERSION_ID="12"\n'
              'VERSION_CODENAME=bookworm\nID=debian\n',
    "ubuntu": 'PRETTY_NAME="Ubuntu 24.04.5 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n'
              'ID=ubuntu\nID_LIKE=debian\nUBUNTU_CODENAME=noble\n',
    "linuxmint": 'NAME="Linux Mint"\nVERSION="22 (Wilma)"\nID=linuxmint\nID_LIKE="ubuntu debian"\n'
                 'PRETTY_NAME="Linux Mint 22"\nVERSION_ID="22"\nVERSION_CODENAME=wilma\nUBUNTU_CODENAME=noble\n',
    "fedora": 'NAME="Fedora Linux"\nVERSION="42 (Workstation Edition)"\nID=fedora\nVERSION_ID=42\n'
              'VERSION_CODENAME=""\nPLATFORM_ID="platform:f42"\nPRETTY_NAME="Fedora Linux 42 (Workstation Edition)"\n'
              'VARIANT_ID=workstation\n',
    "rhel": 'NAME="Red Hat Enterprise Linux"\nVERSION="9.4 (Plow)"\nID="rhel"\nID_LIKE="fedora"\nVERSION_ID="9.4"\n'
            'PLATFORM_ID="platform:el9"\nPRETTY_NAME="Red Hat Enterprise Linux 9.4 (Plow)"\n',
    "centos": 'NAME="CentOS Stream"\nVERSION="9"\nID="centos"\nID_LIKE="rhel fedora"\nVERSION_ID="9"\n'
              'PLATFORM_ID="platform:el9"\nPRETTY_NAME="CentOS Stream 9"\n',
    "rocky": 'NAME="Rocky Linux"\nVERSION="9.4 (Blue Onyx)"\nID="rocky"\nID_LIKE="rhel centos fedora"\n'
             'VERSION_ID="9.4"\nPLATFORM_ID="platform:el9"\nPRETTY_NAME="Rocky Linux 9.4 (Blue Onyx)"\n',
    "almalinux": 'NAME="AlmaLinux"\nVERSION="9.4 (Seafoam Ocelot)"\nID="almalinux"\nID_LIKE="rhel centos fedora"\n'
                 'VERSION_ID="9.4"\nPLATFORM_ID="platform:el9"\nPRETTY_NAME="AlmaLinux 9.4 (Seafoam Ocelot)"\n',
    "ol": 'NAME="Oracle Linux Server"\nVERSION="9.4"\nID="ol"\nID_LIKE="fedora"\nVERSION_ID="9.4"\n'
          'PRETTY_NAME="Oracle Linux Server 9.4"\n',
    "amzn": 'NAME="Amazon Linux"\nVERSION="2023"\nID="amzn"\nID_LIKE="fedora"\nVERSION_ID="2023"\n'
            'PLATFORM_ID="platform:al2023"\nPRETTY_NAME="Amazon Linux 2023.6.20241010"\n',
    # Built on Fedora, not in the family table itself (ID_LIKE decides)
    "ultramarine": 'NAME="Ultramarine Linux"\nVERSION="41 (Flagship Edition)"\nID=ultramarine\nID_LIKE="fedora"\n'
                   'VERSION_ID=41\nPRETTY_NAME="Ultramarine Linux 41 (Flagship Edition)"\n',
    "opensuse-leap": 'NAME="openSUSE Leap"\nVERSION="15.6"\nID="opensuse-leap"\nID_LIKE="suse opensuse"\n'
                     'VERSION_ID="15.6"\nPRETTY_NAME="openSUSE Leap 15.6"\n',
    "opensuse-tumbleweed": 'NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n'
                           'VERSION_ID="20251020"\nPRETTY_NAME="openSUSE Tumbleweed"\n',
    "sles": 'NAME="SLES"\nVERSION="15-SP6"\nVERSION_ID="15.6"\nPRETTY_NAME="SUSE Linux Enterprise Server 15 SP6"\n'
            'ID="sles"\nID_LIKE="suse"\n',
    "arch": 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\nBUILD_ID=rolling\n',
    "manjaro": 'NAME="Manjaro Linux"\nPRETTY_NAME="Manjaro Linux"\nID=manjaro\nID_LIKE=arch\nBUILD_ID=rolling\n',
    "endeavouros": 'NAME="EndeavourOS"\nPRETTY_NAME="EndeavourOS"\nID="endeavouros"\nID_LIKE="arch"\n'
                   'BUILD_ID=rolling\n',
    "alpine": 'NAME="Alpine Linux"\nID=alpine\nVERSION_ID=3.22.2\nPRETTY_NAME="Alpine Linux v3.22"\n',
    "gentoo": 'NAME=Gentoo\nID=gentoo\nPRETTY_NAME="Gentoo Linux"\n',
    "void": 'NAME="Void"\nID="void"\nPRETTY_NAME="Void Linux"\n',
    "nixos": 'NAME=NixOS\nID=nixos\nVERSION_ID="25.05"\nPRETTY_NAME="NixOS 25.05 (Warbler)"\n',
}

# A package manager: writes its call down, fails when a line of a file in $LCF_FAILS is in its arguments (printing
# $LCF_STASH/fail-output, when a test put dnf's real error there), answers dnf's --assumeno with the transaction in
# $LCF_STASH/dnf-plan (dnf's real output; it changes nothing and ends with "Operation aborted.", exit 1), and
# "installs" what it is asked for (the package's command appears on the PATH).
PACKAGE_MANAGER = r'''#!/bin/sh
echo "$(basename "$0") $*" >> "$LCF_CALLS"
for f in "$LCF_FAILS"/*; do
    [ -f "$f" ] || continue
    case " $* " in *"$(cat "$f")"*) [ -f "$LCF_STASH/fail-output" ] && cat "$LCF_STASH/fail-output"; exit 100 ;; esac
done
case " $* " in *" --assumeno "*) cat "$LCF_STASH/dnf-plan"; exit 1 ;; esac
for arg in "$@"; do
    case "$arg" in
        python3|python) "$LCF_MAKE" python3 "$LCF_PY_BASE" ;;
        python3.1[0-9]) "$LCF_MAKE" "$arg" "3.${arg#python3.}" ;;
        python31[0-9]) v="${arg#python3}"; "$LCF_MAKE" "python3.$v" "3.$v" ;;
        docker-ce|moby-engine|docker|podman-docker) "$LCF_MAKE" docker ;;
        podman) "$LCF_MAKE" podman ;;
        rsync|curl) "$LCF_MAKE" "$arg" ;;
    esac
done
exit 0
'''
# Any other tool: writes its call down and fails like a package manager.
RECORDER = r'''#!/bin/sh
echo "$(basename "$0") $*" >> "$LCF_CALLS"
for f in "$LCF_FAILS"/*; do
    [ -f "$f" ] || continue
    case " $(basename "$0") $* " in *"$(cat "$f")"*) exit 100 ;; esac
done
exit 0
'''
# Puts a command on the sandbox PATH: a python3 of a given version, curl (delivers $LCF_KEY) or a no-op.
MAKE = r'''#!/bin/sh
name="$1"; version="$2"; path="$LCF_BIN/$name"
case "$name" in
    python3*)
        minor="$(echo "$version" | cut -d. -f2)"
        printf '#!/bin/sh\ncase "$1" in\n    --version) echo "Python %s.0" ;;\n    -c) [ %s -ge 11 ] && [ %s -le 14 ] ;;\nesac\n' \
            "$version" "$minor" "$minor" > "$path" ;;
    curl) cp "$LCF_STASH/curl" "$path" ;;
    *) printf '#!/bin/sh\nexit 0\n' > "$path" ;;
esac
chmod +x "$path"
'''
CURL = r'''#!/bin/sh
echo "$(basename "$0") $*" >> "$LCF_CALLS"
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
[ -n "$LCF_KEY" ] || exit 22
cp "$LCF_KEY" "$out"
'''
# SUSEConnect: --status prints $LCF_STASH/suse-status (the JSON of the real tool), anything else is an activation, which
# makes what failed until then installable.
SUSECONNECT = r'''#!/bin/sh
echo "$(basename "$0") $*" >> "$LCF_CALLS"
case "$1" in --status|-s) [ -f "$LCF_STASH/suse-status" ] && cat "$LCF_STASH/suse-status"; exit 0 ;; esac
for f in "$LCF_FAILS"/*; do
    [ -f "$f" ] || continue
    case " $(basename "$0") $* " in *"$(cat "$f")"*) exit 100 ;; esac
done
rm -f "$LCF_FAILS/until-suseconnect"
exit 0
'''

# What dnf 4 printed for 'LC_ALL=C dnf install --assumeno --allowerasing <Docker CE>' with Docker's RHEL repository on
# Rocky Linux 9.8 (real runs, 2026-10-23; the dependency lists shortened): nothing to remove; podman-docker installed
# (its docker conflicts with docker-ce's); a package name Docker does not publish; the repository unreachable.
DNF_PLAN_CLEAN = """Dependencies resolved.
================================================================================
 Package                   Arch   Version                Repository        Size
================================================================================
Installing:
 containerd.io             x86_64 2.4.2-3.el9            docker-ce-stable  35 M
 docker-buildx-plugin      x86_64 0.38.1-1.el9           docker-ce-stable  18 M
 docker-ce                 x86_64 3:29.9.2-1.el9         docker-ce-stable  26 M
 docker-ce-cli             x86_64 1:29.9.2-1.el9         docker-ce-stable 8.9 M
 docker-compose-plugin     x86_64 5.6.1-1.el9            docker-ce-stable 8.4 M
Installing dependencies:
 container-selinux         noarch 4:2.245.0-1.el9        appstream         57 k
 iptables-nft              x86_64 1.8.10-11.el9_5        baseos           187 k
Installing weak dependencies:
 docker-ce-rootless-extras x86_64 29.9.2-1.el9           docker-ce-stable 5.6 M

Transaction Summary
================================================================================
Install  31 Packages

Total download size: 116 M
Installed size: 426 M
Operation aborted.
"""
DNF_PLAN_PODMAN_DOCKER = """Last metadata expiration check: 0:00:40 ago on Fri Oct 23 21:36:26 2026.
Dependencies resolved.
========================================================================================
 Package                     Arch    Version                     Repository         Size
========================================================================================
Installing:
 containerd.io               x86_64  2.4.2-3.el9                 docker-ce-stable   35 M
 docker-buildx-plugin        x86_64  0.38.1-1.el9                docker-ce-stable   18 M
 docker-ce                   x86_64  3:29.9.2-1.el9              docker-ce-stable   26 M
 docker-ce-cli               x86_64  1:29.9.2-1.el9              docker-ce-stable  8.9 M
 docker-compose-plugin       x86_64  5.6.1-1.el9                 docker-ce-stable  8.4 M
Installing dependencies:
 container-selinux           noarch  4:2.245.0-1.el9             appstream          57 k
 passt-selinux               noarch  0^20251210.gd04c480-7.el9_8 appstream          25 k
Installing weak dependencies:
 docker-ce-rootless-extras   x86_64  29.9.2-1.el9                docker-ce-stable  5.6 M
Removing dependent packages:
 podman-docker               noarch  6:5.8.2-7.el9_8             @appstream         12 k

Transaction Summary
========================================================================================
Install  23 Packages
Remove    1 Package

Total download size: 114 M
Operation aborted.
"""
DNF_NO_MATCH = """Last metadata expiration check: 0:00:40 ago on Fri Oct 23 21:36:26 2026.
No match for argument: docker-ce
Error: Unable to find a match: docker-ce
"""
DNF_UNREACHABLE = """Errors during downloading metadata for repository 'docker-ce-stable':
  - Curl error (56): Failure when receiving data from the peer for https://download.docker.com/linux/rhel/9/x86_64/stable/repodata/repomd.xml [Received HTTP code 502 from proxy after CONNECT]
Error: Failed to download metadata for repo 'docker-ce-stable': Cannot download repomd.xml: Cannot download repodata/repomd.xml: All mirrors were tried
"""
# Rocky Linux 8.10 with podman, buildah, runc and podman-docker (real run): containerd.io replaces runc, and podman,
# buildah, containers-common, podman-catatonit and podman-docker would be removed.
DNF_PLAN_EL8_PODMAN = """Dependencies resolved.
=======================================================================================================
 Package                     Arch    Version                                    Repository         Size
=======================================================================================================
Installing:
 containerd.io               x86_64  2.4.2-3.el8                                docker-ce-stable   35 M
     replacing  runc.x86_64 4:1.2.9-6.module+el8.10.0+40310+c25440c1
 docker-buildx-plugin        x86_64  0.38.1-1.el8                               docker-ce-stable   19 M
 docker-ce                   x86_64  3:29.9.2-1.el8                             docker-ce-stable   26 M
 docker-ce-cli               x86_64  1:29.9.2-1.el8                             docker-ce-stable  9.0 M
 docker-compose-plugin       x86_64  5.6.1-1.el8                                docker-ce-stable  8.6 M
Installing dependencies:
 libcgroup                   x86_64  0.41-19.el8                                baseos             69 k
 xz                          x86_64  5.2.4-4.el8_6                              baseos            152 k
Installing weak dependencies:
 docker-ce-rootless-extras   x86_64  29.9.2-1.el8                               docker-ce-stable  5.6 M
Removing dependent packages:
 buildah                     x86_64  2:1.33.14-6.module+el8.10.0+40310+c25440c1 @appstream         34 M
 containers-common           x86_64  2:1-82.module+el8.10.0+40133+32c21f88      @appstream        581 k
 podman                      x86_64  4:4.9.4-37.module+el8.10.0+40310+c25440c1  @appstream         56 M
 podman-catatonit            x86_64  4:4.9.4-37.module+el8.10.0+40310+c25440c1  @appstream        799 k
 podman-docker               noarch  4:4.9.4-37.module+el8.10.0+40310+c25440c1  @appstream         10 k
Enabling module streams:
 container-tools                     rhel8

Transaction Summary
=======================================================================================================
Install  8 Packages
Remove   5 Packages

Total download size: 104 M
Operation aborted.
"""
# Fedora 42 (dnf5 5.2.18) with podman-docker installed, Docker's Fedora repository (real run): the removals come first,
# and the summary lists "Removing:" indented, which is no package.
DNF5_PLAN_PODMAN_DOCKER = """Updating and loading repositories:
Repositories loaded.
Package                    Arch   Version                    Repository            Size
Removing dependent packages:
 podman-docker             noarch 5:5.8.2-1.fc42             updates           11.7 KiB
Installing:
 containerd.io             x86_64 2.2.4-1.fc42               docker-ce-stable 120.4 MiB
 docker-ce                 x86_64 3:29.5.3-1.fc42            docker-ce-stable 102.7 MiB
Installing dependencies:
 container-selinux         noarch 4:2.247.0-1.fc42           updates           74.3 KiB

Transaction Summary:
 Installing:        17 packages
 Removing:           1 package

Total size of inbound packages is 108 MiB. Need to download 108 MiB.
Operation aborted by the user.
"""
# CentOS Stream 9 (dnf 4), where docker-ce's obsoletes replace a distribution package: dnf prints it as "replacing"
# under the package that takes its place (no "Removing" section), the silent replacement the trial run must catch.
DNF_PLAN_OBSOLETES = """Dependencies resolved.
================================================================================
 Package                   Arch   Version                Repository        Size
================================================================================
Installing:
 containerd.io             x86_64 2.4.2-3.el9            docker-ce-stable  35 M
     replacing  runc.x86_64 4:1.2.4-1.el9
 docker-ce                 x86_64 3:29.9.2-1.el9         docker-ce-stable  26 M

Transaction Summary
================================================================================
Install  5 Packages

Operation aborted.
"""
DNF5_NO_MATCH = """Updating and loading repositories:
Repositories loaded.
Failed to resolve the transaction:
No match for argument: docker-ce
You can try to add to command line:
  --skip-unavailable to skip unavailable packages
"""
# Rocky Linux 8.10 with a RHEL version Docker publishes nothing for (rhel/11): the repository answers 404.
DNF_404 = """Docker CE Stable - x86_64                       797  B/s | 382  B     00:00
Errors during downloading metadata for repository 'docker-ce-stable':
  - Status code: 404 for https://download.docker.com/linux/rhel/11/x86_64/stable/repodata/repomd.xml (IP: 192.0.2.10)
Error: Failed to download metadata for repo 'docker-ce-stable': Cannot download repomd.xml: Cannot download repodata/repomd.xml: All mirrors were tried
"""


class Box:
    """A sandbox with the os-release of `system`. `present`: commands there from the start ("python3=3.9", "docker",
    "rsync", "curl"); `fails`: argument texts the package managers and tools fail on; `failing_until_suseconnect`:
    one that SUSEConnect lifts (the Containers Module makes docker installable)."""

    def __init__(self, tmp_path, system, key_file=None, present=("curl",), fails=(), failing_until_suseconnect=None,
                 py_base="3.13", pinned=None, machine="x86_64", tools=(), dnf_plan=DNF_PLAN_CLEAN, answer=None,
                 fail_output=None, suse_status=None, rootful=True):
        self.root = tmp_path / "sandbox"
        self.bin = self.root / "bin"
        self.etc = self.root / "etc"
        self.stash = self.root / "stash"
        self.fails = self.root / "fails"
        for folder in (self.bin, self.etc, self.stash, self.fails, self.root / "tmp"):
            folder.mkdir(parents=True, exist_ok=True)
        self.calls = self.root / "calls.log"
        self.os_release = self.etc / "os-release"
        self.os_release.write_text(OS_RELEASE[system])
        # pacman's package database, as on an installed Arch system (a fresh container has none)
        (self.etc / "pacman-sync").mkdir()
        (self.etc / "pacman-sync" / "core.db").write_text("")
        self.pinned = pinned
        # True: the tests of Docker's own routes run as 'install.sh --rootful' (Docker as root with the group docker,
        # the person's explicit choice); False: the default, a container engine without root rights (rootless)
        self.rootful = rootful
        for tool in REAL_TOOLS:
            if shutil.which(tool):
                (self.bin / tool).symlink_to(shutil.which(tool))
        (self.bin / "cut").symlink_to(shutil.which("cut"))
        self.write(self.stash / "curl", CURL)
        (self.stash / "dnf-plan").write_text(dnf_plan)
        if fail_output is not None:
            (self.stash / "fail-output").write_text(fail_output)
        if suse_status is not None:
            (self.stash / "suse-status").write_text(suse_status)
        if answer is not None:   # the person's answer on the terminal (ASK_TTY); without it there is no terminal
            (self.etc / "answer").write_text(answer + "\n")
        self.write(self.root / "make", MAKE)
        for name in ("apt-get", "dnf", "zypper", "pacman", "apk"):
            if name in tools or not tools:
                self.write(self.bin / name, PACKAGE_MANAGER)
        for name in ("rpm", "systemctl", "rc-update", "rc-service", "usermod", "addgroup", "sudo"):
            self.write(self.bin / name, RECORDER)
        self.write(self.bin / "SUSEConnect", SUSECONNECT)
        self.write(self.bin / "groups", "#!/bin/sh\nexit 0\n")
        self.write(self.bin / "dpkg", "#!/bin/sh\necho amd64\n")
        self.write(self.bin / "uname", f"#!/bin/sh\necho {machine}\n")
        for i, text in enumerate(fails):
            (self.fails / str(i)).write_text(text)
        if failing_until_suseconnect:
            (self.fails / "until-suseconnect").write_text(failing_until_suseconnect)
        self.env = {"PATH": str(self.bin), "HOME": str(self.root), "TMPDIR": str(self.root / "tmp"),
                    "SUDO_USER": "lcf", "USER": "lcf", "LCF_CALLS": str(self.calls), "LCF_FAILS": str(self.fails),
                    "LCF_MAKE": str(self.root / "make"), "LCF_BIN": str(self.bin), "LCF_STASH": str(self.stash),
                    "LCF_PY_BASE": py_base, "LCF_KEY": str(key_file) if key_file else ""}
        for item in present:
            name, _, version = item.partition("=")
            self.make(name, version)

    @staticmethod
    def write(path: Path, text: str):
        path.write_text(text)
        path.chmod(0o755)

    def make(self, name, version=""):
        subprocess.run([str(self.root / "make"), name, version], env=self.env, check=True)

    @property
    def keyring(self):
        return self.etc / "pki" / "rpm-gpg" / "docker-ce.asc"

    @property
    def repo(self):
        return self.etc / "yum.repos.d" / "docker-ce.repo"

    @property
    def apk_repositories(self):
        return self.etc / "apk" / "repositories"

    def run(self, body):
        script = ('source "$1"; OS_RELEASE_FILE="$2/os-release"; DEBIAN_VERSION_FILE="$2/debian_version"; '
                  'DOCKER_KEYRING="$2/apt/keyrings/docker.asc"; DOCKER_SOURCES="$2/apt/sources.list.d/docker.sources"; '
                  'DOCKER_RPM_KEYRING="$2/pki/rpm-gpg/docker-ce.asc"; DOCKER_RPM_REPO="$2/yum.repos.d/docker-ce.repo"; '
                  'APK_REPOSITORIES="$2/apk/repositories"; PACMAN_SYNC_DIR="$2/pacman-sync"; ASK_TTY="$2/answer"; '
                  'SUBUID_FILE="$2/subuid"; SUBGID_FILE="$2/subgid"; wait_for_internet() { :; }; ')
        if self.rootful:
            script += "ROOTFUL=1; "
        if self.pinned:
            script += 'DOCKER_KEY_FINGERPRINT="$3"; DOCKER_RPM_KEY_FINGERPRINT="$3"; '
        run = subprocess.run([str(self.bin / "bash"), "-c", script + body, "test", str(INSTALL_SH), str(self.etc),
                              self.pinned or ""], env=self.env, capture_output=True, text=True, timeout=60,
                             cwd=self.root)
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return run, calls


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """The signing key pinned in these tests and another one, as armored files."""
    folder = tmp_path_factory.mktemp("keys")
    home = folder / "gnupg"
    pinned, other = make_key(home, "docker"), make_key(home, "other")
    return {"pinned": pinned, "docker.asc": export(home, [pinned], folder / "docker.asc"),
            "other.asc": export(home, [other], folder / "other.asc")}


def said(run):
    return run.stdout + run.stderr


# --- the script itself ----------------------------------------------------------------------------------------------

def test_the_family_table_is_the_hardware_probes():
    """One table: install.sh's os_family_of is tables.os_families of the probe schema (as scripts/hardware_probe.sh)."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("\nos_family_of() {", 1)[1].split("\n}", 1)[0]
    table = dict(re.findall(r"^\s+([^)\s*]+)\) echo (\S+) ;;$", body, re.M))
    families = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))["tables"]["os_families"]
    assert table == {i: family for family, ids in families.items() for i in ids}


def test_the_rpm_fingerprint_is_dockers_published_one_with_its_source_named():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert re.search(r'^DOCKER_RPM_KEY_FINGERPRINT="([0-9A-F]{40})"$', text, re.M).group(1) == RPM_FINGERPRINT
    head = text[:text.index("DOCKER_RPM_KEY_FINGERPRINT=")]
    assert "060A 61C5\n# 1B55 8A7F 742B 77AA C52F EB6B 621E 9F35" in head
    assert "docs.docker.com/engine/install/fedora/" in head


def test_no_option_turns_a_signature_check_off():
    """No package manager is told to skip signatures or to take a key on its own (the code, not the comments)."""
    text = "\n".join(line for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    for option in ("--nogpgcheck", "gpgcheck=0", "--gpg-auto-import-keys", "--no-gpg-checks", "--allow-untrusted",
                   "--allow-unauthenticated", "SigLevel", "trusted=yes", "pacman -Sy ", "-Syu --noconfirm"):
        assert option not in text, option


# --- the plan (--dry-run) -------------------------------------------------------------------------------------------

@needs_bash_and_gpg
class TestTheDryRun:
    """Nothing missing is installed and no package manager is called: the plan names what would happen."""

    def plan(self, tmp_path, system, present=("curl",)):
        box = Box(tmp_path, system, present=present)
        run, calls = box.run('main --dry-run')
        assert calls == [], "the dry run calls no package manager or tool"
        assert not (box.etc / "yum.repos.d").exists() and not (box.etc / "apt").exists()
        return run, said(run)

    @pytest.mark.parametrize("system, family, python, docker", [
        ("debian", "debian", "apt-get install -y python3 python3-venv python3-pip",
         f"Dockers apt-Repository https://download.docker.com/linux/debian (bookworm), sein Schlüssel nur mit dem "
         f"Fingerabdruck {DEB_FINGERPRINT}; apt-get install -y {DOCKER_CE}"),
        ("linuxmint", "debian", "apt-get install -y python3 python3-venv python3-pip",
         "Dockers apt-Repository https://download.docker.com/linux/ubuntu (noble)"),
        ("fedora", "fedora", "dnf install -y python3 python3-pip",
         f"Dockers dnf-Repository https://download.docker.com/linux/fedora, sein Schlüssel nur mit dem Fingerabdruck "
         f"{RPM_FINGERPRINT}; dnf install -y {DOCKER_CE}; systemctl enable --now docker"),
        ("rhel", "rhel", "dnf install -y python3 python3-pip",
         "Dockers dnf-Repository https://download.docker.com/linux/rhel,"),
        ("centos", "rhel", "dnf install -y python3 python3-pip",
         "Dockers dnf-Repository https://download.docker.com/linux/centos,"),
        ("rocky", "rhel", "dnf install -y python3 python3-pip",
         f"Dockers dnf-Repository https://download.docker.com/linux/rhel (9, x86_64), sein Schlüssel nur mit dem "
         f"Fingerabdruck {RPM_FINGERPRINT}; dnf install -y {DOCKER_CE}; systemctl enable --now docker"),
        ("almalinux", "rhel", "dnf install -y python3 python3-pip",
         "Dockers dnf-Repository https://download.docker.com/linux/rhel (9, x86_64)"),
        ("ol", "rhel", "dnf install -y python3 python3-pip",
         "Dockers dnf-Repository https://download.docker.com/linux/rhel (9, x86_64)"),
        ("amzn", "rhel", "dnf install -y python3 python3-pip",
         "aus den Paketquellen der Distribution: dnf install -y docker; systemctl enable --now docker"),
        ("ultramarine", "fedora", "dnf install -y python3 python3-pip", "dnf install -y moby-engine"),
        ("opensuse-tumbleweed", "suse", "zypper --non-interactive install python3 python3-pip",
         "zypper --non-interactive install docker (dazu, wenn es das gibt: docker-buildx); "
         "systemctl enable --now docker"),
        ("sles", "suse", "zypper --non-interactive install python3 python3-pip",
         "zypper --non-interactive install docker"),
        ("arch", "arch", "pacman -S --needed --noconfirm python",
         "pacman -S --needed --noconfirm docker docker-buildx; systemctl enable --now docker"),
        ("manjaro", "arch", "pacman -S --needed --noconfirm python", "pacman -S --needed --noconfirm docker"),
        ("endeavouros", "arch", "pacman -S --needed --noconfirm python", "pacman -S --needed --noconfirm docker"),
    ])
    def test_each_family_plans_with_its_package_manager(self, tmp_path, system, family, python, docker):
        run, text = self.plan(tmp_path, system)
        assert run.returncode == 0, text
        install = python.split(" python")[0]
        assert f"(ID {system}, Familie {family}); Paketverwaltung: {install.split()[0].replace('-get', '')}" in text
        assert f"Python: fehlt -> {python}" in text and "Docker: fehlt -> " in text and docker in text, text
        assert f"rsync: fehlt -> {install} rsync" in text
        assert "Probelauf fertig: Die Installation liefe hier durch." in text

    @pytest.mark.parametrize("system, old, first, rest", [
        ("rhel", "3.9", "dnf install -y python3.13 python3.13-pip",
         "python3.12 python3.12-pip; python3.11 python3.11-pip; python3.14 python3.14-pip"),
        ("opensuse-leap", "3.6", "zypper --non-interactive install python313 python313-pip",
         "python312 python312-pip; python311 python311-pip; python314 python314-pip")])
    def test_an_old_python3_gets_a_numbered_python_of_the_distribution(self, tmp_path, system, old, first, rest):
        run, text = self.plan(tmp_path, system, present=("curl", f"python3={old}", "docker", "rsync"))
        assert run.returncode == 0, text
        assert f"Python: Python {old}.0 passt nicht (3.11 bis 3.14) -> {first} (sonst der Reihe nach: {rest})" in text

    def test_what_is_there_is_named(self, tmp_path):
        run, text = self.plan(tmp_path, "fedora", present=("curl", "python3.12=3.12", "python3=3.9", "docker",
                                                            "rsync"))
        assert run.returncode == 0, text
        assert "Python: " in text and "python3.12 (Python 3.12.0)" in text
        assert "Docker: vorhanden" in text and "rsync: vorhanden" in text

    def test_an_old_python3_on_apt_still_stops(self, tmp_path):
        """apt brings no numbered Python of its own; as before, the installer asks for a fitting system."""
        run, text = self.plan(tmp_path, "ubuntu", present=("curl", "python3=3.10", "docker", "rsync"))
        assert run.returncode == 1 and "Probelauf: Hier hielte die Installation an" in text
        assert "Python 3.11 bis 3.14 wird benötigt, gefunden: Python 3.10.0" in text

    def test_alpine_plans_docker_from_community_and_stops_with_the_way_that_works(self, tmp_path):
        run, text = self.plan(tmp_path, "alpine")
        assert run.returncode == 1, text
        assert ("apk add docker docker-cli-buildx; rc-update add docker default; rc-service docker start" in text
                and "Repository community" in text)
        assert "musl" in text and "onnxruntime" in text and "scripts/build_container_image.sh" in text
        assert "git, GNU tar (für scripts/build_container_image.sh): fehlen -> apk add git tar" in text
        assert "richtet der Installer vorher ein (apk add git tar)" in text
        assert "Python:" not in text

    def test_the_way_alpine_is_pointed_to_takes_busybox_tar_into_account(self):
        """scripts/build_container_image.sh stages the build context with GNU tar's --null --files-from (busybox' tar
        on Alpine has no --null): the Alpine stop names what to add, and the script still uses those options."""
        script = (REPO_ROOT / "scripts" / "build_container_image.sh").read_text(encoding="utf-8")
        assert "--null --files-from=" in script
        text = INSTALL_SH.read_text(encoding="utf-8")
        stop = text[text.index("alpine_stop() {"):]
        stop = stop[:stop.index("\n}")]
        assert "apk add git tar" in stop and "GNU tar" in stop

    @pytest.mark.parametrize("system, family", [("gentoo", "gentoo"), ("void", "void"), ("nixos", "nixos")])
    def test_another_family_stops_naming_the_family_and_the_manual(self, tmp_path, system, family):
        run, text = self.plan(tmp_path, system)
        assert run.returncode == 1, text
        assert f"(ID {system}, Familie {family})" in text and "kennt der Installer keine Paketverwaltung" in text
        assert MANUAL in text and DOCKER_MANUAL in text

    def test_another_family_with_everything_there_needs_no_package_manager(self, tmp_path):
        run, text = self.plan(tmp_path, "gentoo", present=("curl", "python3=3.13", "docker", "rsync"))
        assert run.returncode == 0, text
        assert "Paketverwaltung: keine, die der Installer kennt" in text

    def test_another_family_without_docker_stops_with_dockers_manual(self, tmp_path):
        run, text = self.plan(tmp_path, "void", present=("curl", "python3=3.13", "rsync"))
        assert run.returncode == 1, text
        assert "Docker wurde nicht installiert" in text and "dieses System laut" in text and DOCKER_MANUAL in text

    @pytest.mark.parametrize("system, tool", [("centos", "dnf"), ("amzn", "dnf"), ("arch", "pacman"),
                                              ("debian", "apt-get")])
    def test_without_the_package_manager_the_dry_run_stops_like_the_installation(self, tmp_path, system, tool):
        """CentOS 7 and Amazon Linux 2 have yum, not dnf: with Python and rsync there, the installation still stops
        at Docker ('dnf fehlt auf diesem System'), so the dry run must not say it would go through."""
        box = Box(tmp_path, system, present=("curl", "python3=3.12", "rsync"), tools=("zypper",))
        run, calls = box.run("main --dry-run")
        assert calls == []
        assert run.returncode == 1, said(run)
        assert f"Docker wurde nicht installiert: {tool} fehlt auf diesem System." in said(run)
        real = Box(tmp_path / "real", system, present=("curl", "python3=3.12", "rsync"), tools=("zypper",))
        run, _ = real.run("install_docker")
        assert run.returncode == 1 and f"{tool} fehlt auf diesem System." in said(run), said(run)

    def test_the_dry_run_changes_nothing_and_needs_no_root(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        main = text[text.index("main() {"):]
        assert main.index('ditto_option "$@"') < main.index("plan_installation") < main.index("check_root")


# --- the installation in the sandbox --------------------------------------------------------------------------------

@needs_bash_and_gpg
class TestDockersRpmRepository:
    """Fedora, RHEL and CentOS Stream: Docker's own repository, its key checked against the pinned fingerprint, through
    the same trial run as the RHEL rebuilds (one install rule for the whole RHEL family, TestRhelFamilyRule)."""

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    def test_python_docker_and_the_group_with_the_checked_key(self, tmp_path, keys, system):
        box = Box(tmp_path, system, keys["docker.asc"], pinned=keys["pinned"])
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["dnf install -y python3 python3-pip",
                         f"curl -fsSL --proto =https --tlsv1.2 https://download.docker.com/linux/{system}/gpg -o "
                         + calls[1].rsplit(" -o ", 1)[1],
                         f"dnf install --assumeno --allowerasing {DOCKER_CE}",
                         f"rpm --import {box.keyring}",
                         f"dnf install -y {DOCKER_CE}",
                         "systemctl enable --now docker",
                         "usermod -aG docker lcf"]
        assert keys_in(box.keyring, tmp_path) == [keys["pinned"]]
        assert box.repo.read_text() == ("[docker-ce-stable]\nname=Docker CE Stable - $basearch\n"
                                        f"baseurl=https://download.docker.com/linux/{system}/$releasever/$basearch/"
                                        f"stable\nenabled=1\ngpgcheck=1\ngpgkey=file://{box.keyring}\n")
        assert "Docker ist installiert" in run.stdout and "get.docker.com" not in said(run)

    def test_only_the_missing_key_tools_are_installed(self, tmp_path, keys):
        """Fedora and RHEL often have curl-minimal, which conflicts with the package curl: it is left alone."""
        box = Box(tmp_path, "fedora", keys["docker.asc"], present=("python3=3.13",), pinned=keys["pinned"])
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert calls[0] == "dnf install -y curl" and calls[1].startswith("curl ")

    @pytest.mark.parametrize("download", ["other.asc", None], ids=["another-key", "no-download"])
    def test_a_key_that_is_not_dockers_is_not_trusted(self, tmp_path, keys, download):
        """Not trusted, and (the RHEL family's rule) Podman comes instead of a stop."""
        box = Box(tmp_path, "fedora", keys[download] if download else None, present=("python3=3.13",),
                  pinned=keys["pinned"])
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert "Docker CE wird hier nicht installiert" in said(run) and "Podman ist installiert" in said(run)
        assert not box.keyring.exists() and not box.repo.exists()
        assert not any(call.startswith(("rpm", "dnf install -y docker")) for call in calls)

    def test_dockers_real_fingerprint_does_not_accept_another_key(self, tmp_path, keys):
        box = Box(tmp_path, "rhel", keys["docker.asc"], present=("python3=3.13",))
        run, calls = box.run("install_docker")
        assert run.returncode == 0 and RPM_FINGERPRINT in said(run), said(run)
        assert not box.keyring.exists() and not box.repo.exists()
        assert calls[-1] == "dnf install -y podman-docker"

    def test_an_existing_docker_repository_is_kept(self, tmp_path, keys):
        box = Box(tmp_path, "fedora", keys["docker.asc"], present=("python3=3.13",), pinned=keys["pinned"])
        earlier = box.etc / "yum.repos.d" / "docker.repo"
        earlier.parent.mkdir(parents=True)
        earlier.write_text("[docker-ce-stable]\nbaseurl=https://download.docker.com/linux/fedora/$releasever/"
                           "$basearch/stable\nenabled=1\ngpgcheck=1\n")
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert not box.repo.exists() and "gibt es schon" in run.stdout
        assert f"rpm --import {box.keyring}" in calls, "the checked key is trusted before dnf loads a package"

    @pytest.mark.parametrize("text", [
        "[docker-ce-stable]\nbaseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable\n"
        "enabled=0\n",
        "[docker-ce-stable]\n#baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable\n",
        "[docker-ce-test]\nbaseurl=https://download.docker.com/linux/fedora/test\nenabled=0\n[fedora]\n"
        "metalink=https://mirrors.fedoraproject.org/metalink\n"], ids=["enabled-0", "commented", "only-disabled"])
    def test_a_switched_off_docker_repository_does_not_count(self, tmp_path, keys, text):
        box = Box(tmp_path, "fedora", keys["docker.asc"], present=("python3=3.13",), pinned=keys["pinned"])
        earlier = box.etc / "yum.repos.d" / "other.repo"
        earlier.parent.mkdir(parents=True)
        earlier.write_text(text)
        run, _ = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert box.repo.exists() and earlier.read_text() == text

    def test_a_failing_install_takes_the_new_repository_back(self, tmp_path, keys):
        box = Box(tmp_path, "rhel", keys["docker.asc"], present=("python3=3.13",), fails=("install -y docker-ce",),
                  pinned=keys["pinned"])
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert "wieder entfernt" in said(run) and "podman, buildah oder runc" in said(run)
        assert not box.repo.exists() and "systemctl enable --now docker" not in calls
        assert calls[-1] == "dnf install -y podman-docker"

    @pytest.mark.parametrize("body", ["install_docker", "main --dry-run"])
    @pytest.mark.parametrize("text", [
        "[docker-ce-stable]\nname=Docker CE Stable - $basearch\nbaseurl=https://download.docker.com/linux/fedora/"
        "$releasever/$basearch/stable\nenabled=0\ngpgcheck=1\n[docker-ce-test]\nbaseurl=https://download.docker.com/"
        "linux/fedora/$releasever/$basearch/test\nenabled=0\ngpgcheck=1\n",
        "#[docker-ce-stable]\n#baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable\n"],
        ids=["enabled-0", "commented"])
    def test_a_switched_off_repository_file_of_the_same_name_is_never_replaced_or_removed(self, tmp_path, keys,
                                                                                         text, body):
        """The person's own docker-ce.repo (Docker's guide writes that name) with Docker's repository switched off:
        the installer neither overwrites it nor, when the install fails, deletes it. It stops before it changes
        anything (no key import, no dnf call) and says what to do; the dry run stops the same way."""
        box = Box(tmp_path, "fedora", keys["docker.asc"], present=("python3=3.13", "rsync"),
                  fails=("install -y docker-ce",), pinned=keys["pinned"])
        box.repo.parent.mkdir(parents=True)
        box.repo.write_text(text)
        run, calls = box.run(body)
        assert run.returncode == 1, said(run)
        assert box.repo.read_text() == text
        assert "Docker wurde nicht installiert" in said(run) and str(box.repo) in said(run)
        assert "enabled=1" in said(run) and "wieder entfernt" not in said(run)
        assert calls == [] and not box.keyring.exists()

    def test_without_dnf_it_stops(self, tmp_path, keys):
        """CentOS 7 has yum only."""
        box = Box(tmp_path, "centos", keys["docker.asc"], present=("python3=3.13",), tools=("apt-get",),
                  pinned=keys["pinned"])
        run, calls = box.run("install_docker")
        assert run.returncode == 1 and "dnf fehlt auf diesem System" in said(run), said(run)
        assert calls == []

    def test_a_service_that_does_not_start_does_not_stop_the_installation(self, tmp_path, keys):
        box = Box(tmp_path, "fedora", keys["docker.asc"], present=("python3=3.13",), fails=("enable --now docker",),
                  pinned=keys["pinned"])
        run, _ = box.run("install_docker; echo AFTER")
        assert run.returncode == 0 and "AFTER" in run.stdout, said(run)
        assert "ließ sich aber nicht starten" in run.stdout and "systemctl enable --now docker" in run.stdout


@needs_bash_and_gpg
class TestTheDistributionsOwnPackages:

    @pytest.mark.parametrize("system, package", [("amzn", "docker"), ("ultramarine", "moby-engine")])
    def test_dnf_systems_without_dockers_repository(self, tmp_path, system, package):
        box = Box(tmp_path, system, present=("python3=3.13",))
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == [f"dnf install -y {package}", "systemctl enable --now docker", "usermod -aG docker lcf"]
        assert not (box.etc / "yum.repos.d").exists() and not (box.etc / "pki").exists()

    @pytest.mark.parametrize("system", ["rocky", "almalinux", "ol"])
    def test_the_podman_fallback_has_no_docker_group(self, tmp_path, system):
        """Podman, the fallback of the RHEL rebuilds (here: Docker publishes nothing for this architecture), needs no
        group docker; the person's Podman socket is switched on and named."""
        box = Box(tmp_path, system, present=("python3=3.12",), machine="ppc64le")
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["dnf install -y podman-docker",
                         "systemctl --user --machine=lcf@ enable --now podman.socket"]
        assert "Podman ist installiert" in run.stdout and "/podman/podman.sock" in run.stdout
        assert "DOCKER_HOST=unix:///run/user/" in run.stdout and "Gruppe docker" in run.stdout
        assert "ppc64le" in said(run) and "Docker CE wird hier nicht installiert" in said(run)

    @pytest.mark.parametrize("system", ["rocky", "fedora"])
    def test_a_docker_command_that_is_podman_gets_the_podman_way_on_a_second_run(self, tmp_path, system):
        """install.sh run again after podman-docker came in (or with podman-docker the person installed): there is
        no group docker to join and no re-login to ask for; Podman's socket and DOCKER_HOST are named again."""
        box = Box(tmp_path, system, present=("python3=3.12", "rsync"))
        box.write(box.bin / "docker", '#!/bin/sh\n[ "$1" = --version ] && echo "podman version 5.4.0"\n'
                                      'echo "Emulate Docker CLI using podman." >&2\nexit 0\n')
        run, calls = box.run("setup_system; finish")
        assert run.returncode == 0, said(run)
        assert calls == ["systemctl --user --machine=lcf@ enable --now podman.socket"]
        assert "DOCKER_HOST=unix:///run/user/" in run.stdout
        assert "zur Gruppe docker" not in said(run) and "Docker-Gruppe hinzugefügt" not in said(run)

    def test_a_real_docker_still_gets_the_group(self, tmp_path):
        box = Box(tmp_path, "rocky", present=("python3=3.12", "rsync"))
        box.write(box.bin / "docker", '#!/bin/sh\n[ "$1" = --version ] && echo "Docker version 28.5.1, build e180ab8"\n'
                                      'exit 0\n')
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["usermod -aG docker lcf"]

    def test_opensuse_gets_docker_and_buildx_from_its_repositories(self, tmp_path):
        box = Box(tmp_path, "opensuse-tumbleweed")
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["zypper --non-interactive install python3 python3-pip",
                         "zypper --non-interactive install docker", "zypper --non-interactive install docker-buildx",
                         "systemctl enable --now docker", "usermod -aG docker lcf"]

    def test_without_buildx_the_installation_goes_on(self, tmp_path):
        box = Box(tmp_path, "opensuse-leap", present=("python3=3.13",), fails=("install docker-buildx",))
        run, _ = box.run("install_docker; echo AFTER")
        assert run.returncode == 0 and "AFTER" in run.stdout, said(run)
        assert "docker-buildx ließ sich nicht installieren" in run.stdout and "älterem Builder" in run.stdout

    def test_sles_switches_the_containers_module_on_after_a_yes(self, tmp_path):
        """Asked first, like the Python 3 Module (the registration status is read, never shown)."""
        box = Box(tmp_path, "sles", present=("python3=3.13",), suse_status=SUSE_REGISTERED, answer="j",
                  failing_until_suseconnect="--non-interactive install docker ")
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert calls[:4] == ["zypper --non-interactive install docker", "SUSEConnect --status",
                             f"SUSEConnect -p {CONTAINERS_MODULE}", "zypper --non-interactive install docker"]
        assert f"SUSEs Containers Module einschalten (SUSEConnect -p {CONTAINERS_MODULE})? [j/N]" in run.stdout
        assert "EXAMPLE-NOT-A-CODE" not in said(run)

    @pytest.mark.parametrize("answer", ["n", None], ids=["no", "no-terminal"])
    def test_sles_without_a_yes_switches_nothing_on_and_stops_with_the_step(self, tmp_path, answer):
        box = Box(tmp_path, "sles", present=("python3=3.13",), suse_status=SUSE_REGISTERED, answer=answer,
                  failing_until_suseconnect="--non-interactive install docker ")
        run, calls = box.run("install_docker")
        assert run.returncode == 1, said(run)
        assert calls == ["zypper --non-interactive install docker", "SUSEConnect --status"]
        assert f"Selbst einschalten: sudo SUSEConnect -p {CONTAINERS_MODULE}" in said(run)

    def test_an_unregistered_sles_stops_naming_the_registration(self, tmp_path):
        box = Box(tmp_path, "sles", present=("python3=3.13",), suse_status=SUSE_NOT_REGISTERED, answer="j",
                  failing_until_suseconnect="--non-interactive install docker ")
        run, calls = box.run("install_docker")
        assert run.returncode == 1, said(run)
        assert "dieses SLES ist nicht registriert" in said(run) and "[j/N]" not in run.stdout
        assert calls == ["zypper --non-interactive install docker", "SUSEConnect --status"]

    def test_the_dry_run_names_the_containers_module_step(self, tmp_path):
        box = Box(tmp_path, "sles", present=("curl", "python3=3.13", "rsync"))
        run, calls = box.run("main --dry-run")
        assert run.returncode == 0 and calls == [], said(run)
        assert ("unter SLES liegt Docker in SUSEs Containers Module: Gibt es das Paket nicht, fragt der Installer, ob "
                f"er das Modul einschaltet (SUSEConnect -p {CONTAINERS_MODULE}; nur auf einem registrierten System)"
                ) in said(run)

    def test_opensuse_without_a_docker_package_stops(self, tmp_path):
        box = Box(tmp_path, "opensuse-leap", present=("python3=3.13",), fails=("install docker ",))
        run, calls = box.run("install_docker")
        assert run.returncode == 1 and "Docker wurde nicht installiert" in said(run), said(run)
        assert not any(call.startswith("SUSEConnect") for call in calls)

    @pytest.mark.parametrize("system", ["arch", "manjaro", "endeavouros"])
    def test_arch_and_its_derivatives_never_upgrade_partially(self, tmp_path, system):
        box = Box(tmp_path, system)
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["pacman -S --needed --noconfirm python",
                         "pacman -S --needed --noconfirm docker docker-buildx", "systemctl enable --now docker",
                         "usermod -aG docker lcf"]

    @pytest.mark.parametrize("body", ["main --dry-run", "setup_system"])
    def test_without_a_package_database_pacman_is_not_run(self, tmp_path, body):
        """A fresh Arch system or container has no sync database: pacman finds nothing ("target not found"). The
        installer says to update the system first and never reads the database itself (-Sy) or upgrades (-Syu)."""
        box = Box(tmp_path, "arch")
        (box.etc / "pacman-sync" / "core.db").unlink()
        run, calls = box.run(body)
        assert run.returncode == 1, said(run)
        assert "pacman hat noch keine Paketdatenbank" in said(run) and "sudo pacman -Syu" in said(run)
        assert calls == []

    def test_a_failing_pacman_says_how_to_update_first(self, tmp_path):
        box = Box(tmp_path, "arch", present=("python3=3.13",), fails=("docker-buildx",))
        run, calls = box.run("install_docker")
        assert run.returncode == 1, said(run)
        assert "sudo pacman -Syu" in said(run) and not any("-Sy " in call for call in calls)


@needs_bash_and_gpg
class TestPython:

    @pytest.mark.parametrize("system, old, failing, chosen, tried", [
        ("rhel", "3.9", "python3.13", "python3.12",
         ["dnf install -y python3.13 python3.13-pip", "dnf install -y python3.12 python3.12-pip"]),
        ("opensuse-leap", "3.6", "python313", "python3.12",
         ["zypper --non-interactive install python313 python313-pip",
          "zypper --non-interactive install python312 python312-pip"])])
    def test_a_numbered_python_when_python3_is_too_old(self, tmp_path, system, old, failing, chosen, tried):
        """RHEL 9 brings 3.9 as python3 and offers python3.12 (no 3.13); Leap 15.6 brings 3.6 and offers python312."""
        box = Box(tmp_path, system, present=(f"python3={old}", "docker", "rsync"), fails=(failing,))
        run, calls = box.run('setup_system; echo "PYTHON=$PYTHON"')
        assert run.returncode == 0, said(run)
        assert calls[:2] == tried
        assert f"PYTHON={box.bin / chosen}" in run.stdout

    def test_the_virtual_environment_is_made_with_that_python(self, tmp_path):
        text = INSTALL_SH.read_text(encoding="utf-8")
        venv = text[text.index("setup_venv() {"):text.index("# --- SCHRITT 4")]
        assert 'local python="${PYTHON:-python3}"' in venv and '"$python" -m venv .venv' in venv
        assert "python3 -m venv" not in venv

    def test_no_fitting_python_stops(self, tmp_path):
        box = Box(tmp_path, "rhel", present=("python3=3.9", "docker", "rsync"),
                  fails=("python3.13", "python3.12", "python3.11", "python3.14"))
        run, calls = box.run("setup_system")
        assert run.returncode == 1 and "Python 3.11 bis 3.14 wird benötigt" in said(run), said(run)
        assert len(calls) == 4

    # Characterization of the apt path as it was before the families came in (these pass against install.sh of
    # cs-c3-v as well): the Python step on Debian and Ubuntu is unchanged.
    @pytest.mark.parametrize("system", ["debian", "ubuntu", "linuxmint"])
    def test_apt_installs_a_missing_python3_as_before(self, tmp_path, system):
        box = Box(tmp_path, system, present=("docker", "rsync"))
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["apt-get update", "apt-get install -y python3 python3-venv python3-pip",
                         "usermod -aG docker lcf"]
        assert "Python3 fehlt. Installiere..." in run.stdout

    def test_apt_with_a_too_old_python3_stops_as_before(self, tmp_path):
        box = Box(tmp_path, "ubuntu", present=("python3=3.10", "docker", "rsync"))
        run, calls = box.run("setup_system")
        assert run.returncode == 1, said(run)
        assert ("Python 3.11 bis 3.14 wird benötigt, gefunden: Python 3.10.0. Bitte eine passende Version "
                "installieren (z. B. Debian 12, Ubuntu 24.04 oder 'uv python install 3.13')." in said(run))
        assert calls == []

    def test_apt_that_cannot_install_python_stops_as_before(self, tmp_path):
        box = Box(tmp_path, "debian", present=("docker", "rsync"), fails=("install -y python3 ",))
        run, calls = box.run("setup_system")
        assert run.returncode == 1 and "Konnte Python nicht installieren." in said(run), said(run)
        assert calls == ["apt-get update", "apt-get install -y python3 python3-venv python3-pip"]


# --- Rocky Linux, AlmaLinux, Oracle Linux: Docker CE, Podman only as the fallback -----------------------------------

@needs_bash_and_gpg
class TestRhelRebuilds:
    """DECISIONS round 26 Q1: on the RHEL rebuilds Docker CE from Docker's RHEL repository is the standard (its key
    checked against the pinned fingerprint, as for Fedora, RHEL and CentOS); Podman (podman-docker) is the fallback
    when Docker CE cannot be installed or when the person says no to the packages dnf would have to remove."""

    def box(self, tmp_path, keys, system="rocky", **kw):
        kw.setdefault("present", ("curl", "python3=3.12"))
        return Box(tmp_path, system, kw.pop("key_file", keys["docker.asc"]), pinned=keys["pinned"], **kw)

    def assert_fallback(self, box, run, calls, reason):
        assert run.returncode == 0, said(run)
        assert "Docker CE wird hier nicht installiert" in said(run) and reason in said(run), said(run)
        assert calls[-2:] == ["dnf install -y podman-docker",
                              "systemctl --user --machine=lcf@ enable --now podman.socket"], calls
        assert not any(c.startswith(("rpm --import", f"dnf install -y {DOCKER_CE}")) for c in calls)
        assert not any("--allowerasing" in c and "--assumeno" not in c for c in calls), "never --allowerasing unasked"
        assert not box.repo.exists() and not box.keyring.exists(), "what install.sh created is taken back"
        assert "Podman ist installiert" in run.stdout and "sudo dnf remove podman-docker" in run.stdout

    @pytest.mark.parametrize("system", ["rocky", "almalinux", "ol"])
    def test_docker_ce_from_dockers_rhel_repository_with_the_checked_key(self, tmp_path, keys, system):
        box = self.box(tmp_path, keys, system)
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == [f"curl -fsSL --proto =https --tlsv1.2 https://download.docker.com/linux/rhel/gpg -o "
                         + calls[0].rsplit(" -o ", 1)[1],
                         f"dnf install --assumeno --allowerasing {DOCKER_CE}",
                         f"rpm --import {box.keyring}",
                         f"dnf install -y {DOCKER_CE}",
                         "systemctl enable --now docker",
                         "usermod -aG docker lcf"]
        assert keys_in(box.keyring, tmp_path) == [keys["pinned"]]
        # The RHEL version in the address, from PLATFORM_ID (el9) or VERSION_ID (9.4): Docker's RHEL folders are 8, 9
        # and 10, whatever a rebuild calls its own release.
        assert box.repo.read_text() == ("[docker-ce-stable]\nname=Docker CE Stable - $basearch\n"
                                        "baseurl=https://download.docker.com/linux/rhel/9/$basearch/stable\n"
                                        f"enabled=1\ngpgcheck=1\ngpgkey=file://{box.keyring}\n")
        assert "Docker ist installiert" in run.stdout and "BuildKit" in run.stdout and "podman" not in calls[-1]

    def test_the_rhel_version_comes_from_platform_id(self, tmp_path, keys):
        box = self.box(tmp_path, keys)
        box.os_release.write_text(OS_RELEASE["rocky"].replace('VERSION_ID="9.4"', 'VERSION_ID="8.10"')
                                  .replace("platform:el9", "platform:el8"))
        run, _ = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert "baseurl=https://download.docker.com/linux/rhel/8/$basearch/stable" in box.repo.read_text()

    @pytest.mark.parametrize("plan, removals", [
        (DNF_PLAN_PODMAN_DOCKER, "podman-docker"),
        (DNF_PLAN_EL8_PODMAN, "buildah containers-common podman podman-catatonit podman-docker runc")],
        ids=["podman-docker", "el8-podman-and-runc"])
    def test_what_dnf_would_remove_is_named_and_a_yes_installs_docker_ce(self, tmp_path, keys, plan, removals):
        box = self.box(tmp_path, keys, dnf_plan=plan, answer="j")
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert f"Für Docker CE müsste dnf diese Pakete entfernen oder ersetzen: {removals}" in run.stdout
        assert "Diese Pakete entfernen und Docker CE installieren? [j/N]" in run.stdout
        assert calls[1:] == [f"dnf install --assumeno --allowerasing {DOCKER_CE}", f"rpm --import {box.keyring}",
                             f"dnf install -y --allowerasing {DOCKER_CE}", "systemctl enable --now docker"]
        assert box.repo.exists() and box.keyring.exists()

    @pytest.mark.parametrize("answer", ["n", "", "irgendwas", None], ids=["no", "enter", "other", "no-terminal"])
    def test_without_a_yes_nothing_is_removed_and_podman_comes(self, tmp_path, keys, answer):
        """No (also: Enter, any other answer, or no terminal at all, as in an unattended run) never removes anything:
        Docker CE is not installed, what install.sh created is taken back, and the Podman fallback comes."""
        box = self.box(tmp_path, keys, dnf_plan=DNF_PLAN_EL8_PODMAN, answer=answer)
        run, calls = box.run("install_docker; docker_group")
        self.assert_fallback(box, run, calls, "du hast Nein gesagt")
        assert "podman podman-catatonit podman-docker runc" in run.stdout
        if answer is None:
            assert "Kein Terminal für die Antwort" in run.stdout

    @pytest.mark.parametrize("plan, why", [
        (DNF_NO_MATCH, "Docker veröffentlicht in https://download.docker.com/linux/rhel keine Pakete für dieses System "
                       "(Rocky Linux 9.4 (Blue Onyx), RHEL 9, x86_64)"),
        (DNF_404, "keine Pakete für dieses System"),
        (DNF_UNREACHABLE, "Dockers Repository https://download.docker.com/linux/rhel ist nicht erreichbar")],
        ids=["no-package", "404", "unreachable"])
    def test_when_dnf_cannot_install_docker_ce_podman_comes(self, tmp_path, keys, plan, why):
        box = self.box(tmp_path, keys, fails=("--assumeno",), fail_output=plan)
        run, calls = box.run("install_docker; docker_group")
        self.assert_fallback(box, run, calls, why)
        assert "dnf: " in run.stdout, "dnf's own words are shown"

    @pytest.mark.parametrize("download, why", [
        ("other.asc", "hat nicht den Fingerabdruck aus Dockers Anleitung"),
        (None, "ließ sich nicht von https://download.docker.com/linux/rhel/gpg laden")],
        ids=["key-check-fails", "repository-unreachable"])
    def test_when_the_key_is_not_dockers_or_not_there_podman_comes(self, tmp_path, keys, download, why):
        box = self.box(tmp_path, keys, key_file=keys[download] if download else None)
        run, calls = box.run("install_docker; docker_group")
        self.assert_fallback(box, run, calls, why)
        assert not any("--assumeno" in c for c in calls), "an untrusted repository is never added"

    @pytest.mark.parametrize("machine", ["ppc64le", "riscv64"])
    def test_an_architecture_docker_publishes_nothing_for_gets_podman_at_once(self, tmp_path, keys, machine):
        box = self.box(tmp_path, keys, machine=machine)
        run, calls = box.run("install_docker; docker_group")
        assert calls == ["dnf install -y podman-docker", "systemctl --user --machine=lcf@ enable --now podman.socket"]
        assert f"keine Pakete für die Architektur {machine} (nur für x86_64, aarch64, s390x)" in said(run)

    def test_a_failing_docker_ce_install_falls_back_too(self, tmp_path, keys):
        box = self.box(tmp_path, keys, fails=(f"install -y {DOCKER_CE}",))
        run, calls = box.run("install_docker; docker_group")
        assert run.returncode == 0, said(run)
        assert f"rpm --import {box.keyring}" in calls and calls[-2] == "dnf install -y podman-docker"
        assert not box.repo.exists() and "Die Docker-Pakete ließen sich nicht installieren" in said(run)

    def test_a_switched_off_docker_repository_of_the_person_still_stops(self, tmp_path, keys):
        """The person's own docker-ce.repo with Docker switched off is a choice: no Podman instead, a plain stop."""
        box = self.box(tmp_path, keys)
        box.repo.parent.mkdir(parents=True)
        box.repo.write_text("[docker-ce-stable]\nbaseurl=https://download.docker.com/linux/rhel/9/x86_64/stable\n"
                            "enabled=0\n")
        run, calls = box.run("install_docker")
        assert run.returncode == 1 and "enabled=1" in said(run) and calls == []

    def test_the_dry_run_names_both_ways(self, tmp_path, keys):
        box = self.box(tmp_path, keys, present=("curl",))
        run, calls = box.run("main --dry-run")
        assert run.returncode == 0 and calls == [], said(run)
        text = said(run)
        assert "Dockers dnf-Repository https://download.docker.com/linux/rhel (9, x86_64)" in text
        assert "Docker CE ist hier der Standard: Das Framework braucht BuildKit (etwa für Hailo)." in text
        assert ("nennt der Installer sie genau und fragt dich. Bei Nein, ohne Paket für dieses System, ohne Verbindung "
                "zum Repository oder mit einem falschen Schlüssel kommt der Ersatzweg: dnf install -y podman-docker") in text

    def test_the_dry_run_on_an_architecture_without_packages_names_podman_and_why(self, tmp_path, keys):
        box = self.box(tmp_path, keys, present=("curl",), machine="ppc64le")
        run, calls = box.run("main --dry-run")
        assert run.returncode == 0 and calls == [], said(run)
        assert ("Docker: fehlt -> Podman (der Ersatzweg), denn Docker CE geht hier nicht: Docker veröffentlicht in "
                "seinem RHEL-Repository keine Pakete für die Architektur ppc64le") in said(run)
        assert "Stattdessen: dnf install -y podman-docker" in said(run)

    def test_a_second_run_with_podman_says_how_to_switch(self, tmp_path):
        box = Box(tmp_path, "rocky", present=("python3=3.12", "rsync"))
        box.write(box.bin / "docker", '#!/bin/sh\n[ "$1" = --version ] && echo "podman version 5.8.2"\nexit 0\n')
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["systemctl --user --machine=lcf@ enable --now podman.socket"]
        assert "Standard ist Docker CE" in run.stdout and "'sudo dnf remove podman-docker'" in run.stdout


@needs_bash_and_gpg
class TestRhelFamilyRule:
    """One install rule for the whole RHEL family: Fedora, RHEL and CentOS Stream get the rule of the RHEL rebuilds.
    Docker CE from Docker's repository for the system (its key checked), through the same dnf trial run: no conflict
    means no question; when dnf would remove or replace a distribution package (also through obsoletes) the exact list
    comes first and only a yes installs; a no, an unreachable repository, a key mismatch or no package for the release
    means the Podman fallback with the reason. This replaces, on purpose, the earlier tests that pinned the old way of
    these three systems (dnf install -y without a trial run, a conflict stopped the installer)."""

    def box(self, tmp_path, keys, system, **kw):
        kw.setdefault("present", ("curl", "python3=3.13"))
        return Box(tmp_path, system, kw.pop("key_file", keys["docker.asc"]), pinned=keys["pinned"], **kw)

    def assert_podman(self, box, run, calls, reason):
        assert run.returncode == 0, said(run)
        assert "Docker CE wird hier nicht installiert" in said(run) and reason in said(run), said(run)
        assert calls[-2:] == ["dnf install -y podman-docker",
                              "systemctl --user --machine=lcf@ enable --now podman.socket"], calls
        assert not any(c.startswith(("rpm --import", f"dnf install -y {DOCKER_CE}")) for c in calls)
        assert not any("--allowerasing" in c and "--assumeno" not in c for c in calls), "never --allowerasing unasked"
        assert not box.repo.exists() and not box.keyring.exists(), "what install.sh created is taken back"
        assert "Podman ist installiert" in run.stdout and "sudo dnf remove podman-docker" in run.stdout

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    def test_no_conflict_means_no_question(self, tmp_path, keys, system):
        box = self.box(tmp_path, keys, system, answer="n")
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert calls[1:] == [f"dnf install --assumeno --allowerasing {DOCKER_CE}", f"rpm --import {box.keyring}",
                             f"dnf install -y {DOCKER_CE}", "systemctl enable --now docker"], calls
        assert "[j/N]" not in run.stdout and "entfernen oder ersetzen" not in said(run)
        assert f"https://download.docker.com/linux/{system}/$releasever/" in box.repo.read_text()

    @pytest.mark.parametrize("system, plan, removals", [
        ("fedora", DNF5_PLAN_PODMAN_DOCKER, "podman-docker"),
        ("rhel", DNF_PLAN_EL8_PODMAN, "buildah containers-common podman podman-catatonit podman-docker runc"),
        ("centos", DNF_PLAN_OBSOLETES, "runc")], ids=["fedora-dnf5", "rhel-el8", "centos-obsoletes"])
    def test_a_conflict_shows_the_exact_list_and_a_yes_installs(self, tmp_path, keys, system, plan, removals):
        box = self.box(tmp_path, keys, system, dnf_plan=plan, answer="j")
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert f"Für Docker CE müsste dnf diese Pakete entfernen oder ersetzen: {removals}" in run.stdout
        assert "Diese Pakete entfernen und Docker CE installieren? [j/N]" in run.stdout
        assert calls[1:] == [f"dnf install --assumeno --allowerasing {DOCKER_CE}", f"rpm --import {box.keyring}",
                             f"dnf install -y --allowerasing {DOCKER_CE}", "systemctl enable --now docker"]

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    @pytest.mark.parametrize("answer", ["n", None], ids=["no", "no-terminal"])
    def test_a_no_means_podman_and_nothing_removed(self, tmp_path, keys, system, answer):
        box = self.box(tmp_path, keys, system, dnf_plan=DNF5_PLAN_PODMAN_DOCKER, answer=answer)
        run, calls = box.run("install_docker; docker_group")
        self.assert_podman(box, run, calls, "du hast Nein gesagt")

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    @pytest.mark.parametrize("plan, why", [
        (DNF_NO_MATCH, "keine Pakete für dieses System"),
        (DNF_UNREACHABLE, "ist nicht erreichbar")], ids=["no-package", "unreachable"])
    def test_no_package_or_an_unreachable_repository_means_podman_with_the_reason(self, tmp_path, keys, system,
                                                                                   plan, why):
        box = self.box(tmp_path, keys, system, fails=("--assumeno",), fail_output=plan)
        run, calls = box.run("install_docker; docker_group")
        self.assert_podman(box, run, calls, why)
        assert f"https://download.docker.com/linux/{system}" in said(run)

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    @pytest.mark.parametrize("download, why", [
        ("other.asc", "hat nicht den Fingerabdruck aus Dockers Anleitung"),
        (None, "ließ sich nicht von https://download.docker.com/linux/")], ids=["key-mismatch", "key-unreachable"])
    def test_a_key_failure_means_podman_with_the_reason(self, tmp_path, keys, system, download, why):
        box = self.box(tmp_path, keys, system, key_file=keys[download] if download else None)
        run, calls = box.run("install_docker; docker_group")
        self.assert_podman(box, run, calls, why)
        assert not any("--assumeno" in c for c in calls), "an untrusted repository is never added"

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    def test_the_dry_run_says_which_way_and_why(self, tmp_path, keys, system):
        box = self.box(tmp_path, keys, system, present=("curl", "python3=3.13", "rsync"))
        run, calls = box.run("main --dry-run")
        assert run.returncode == 0 and calls == [], said(run)
        text = said(run)
        assert f"Dockers dnf-Repository https://download.docker.com/linux/{system}, sein Schlüssel" in text
        assert "Docker CE ist hier der Standard: Das Framework braucht BuildKit (etwa für Hailo)." in text
        assert ("nennt der Installer sie genau und fragt dich. Bei Nein, ohne Paket für dieses System, ohne Verbindung "
                "zum Repository oder mit einem falschen Schlüssel kommt der Ersatzweg: dnf install -y podman-docker") in text

    def test_a_second_run_on_fedora_with_podman_says_how_to_switch(self, tmp_path):
        box = Box(tmp_path, "fedora", present=("python3=3.13", "rsync"))
        box.write(box.bin / "docker", '#!/bin/sh\n[ "$1" = --version ] && echo "podman version 5.8.2"\nexit 0\n')
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["systemctl --user --machine=lcf@ enable --now podman.socket"]
        assert "Standard ist Docker CE" in run.stdout and "'sudo dnf remove podman-docker'" in run.stdout

    @pytest.mark.parametrize("system, package", [("amzn", "docker"), ("ultramarine", "moby-engine")])
    def test_amazon_linux_and_the_fedora_derivatives_keep_their_way(self, tmp_path, system, package):
        box = Box(tmp_path, system, present=("python3=3.13", "rsync"))
        run, calls = box.run("install_docker; docker_group")
        assert run.returncode == 0, said(run)
        assert calls[0] == f"dnf install -y {package}" and not any("--assumeno" in c for c in calls)
        assert "Standard ist Docker CE" not in said(run)


@needs_bash_and_gpg
@pytest.mark.parametrize("plan, removals", [
    (DNF_PLAN_CLEAN, ""), (DNF_PLAN_PODMAN_DOCKER, "podman-docker"),
    (DNF_PLAN_EL8_PODMAN, "buildah containers-common podman podman-catatonit podman-docker runc"),
    (DNF5_PLAN_PODMAN_DOCKER, "podman-docker")], ids=["clean", "dnf4", "el8", "dnf5"])
def test_what_dnf_would_remove_is_read_from_its_own_output(tmp_path, plan, removals):
    box = Box(tmp_path, "rocky")
    (box.root / "plan.txt").write_text(plan)
    run, _ = box.run(f'printf "<%s>\\n" "$(dnf_removals < "{box.root / "plan.txt"}")"')
    assert run.stdout.splitlines() == [f"<{removals}>"], said(run)


# --- SLES: Python from SUSE's Python 3 Module --------------------------------------------------------------------------

SUSE_REGISTERED = ('[{"identifier":"SLES","version":"15.6","arch":"x86_64","status":"Registered",'
                   '"regcode":"EXAMPLE-NOT-A-CODE","type":"full"},{"identifier":"sle-module-basesystem",'
                   '"version":"15.6","arch":"x86_64","status":"Registered"}]\n')
SUSE_WITH_PYTHON3 = SUSE_REGISTERED.replace(']\n', ',{"identifier":"sle-module-python3","version":"15.6",'
                                                   '"arch":"x86_64","status":"Registered"}]\n')
SUSE_NOT_REGISTERED = '[{"identifier":"SLES","version":"15.6","arch":"x86_64","status":"Not Registered"}]\n'
PYTHON_MODULE = "sle-module-python3/15.6/x86_64"
CONTAINERS_MODULE = "sle-module-containers/15.6/x86_64"   # SUSE Customer Center: python311 is there for SLES 15.6 x86_64


@needs_bash_and_gpg
class TestSlesPythonModule:
    """SLES 15 has python3 3.6; python311/312/313 come from SUSE's Python 3 Module, which is off by default."""

    TRIED = ["zypper --non-interactive install python313 python313-pip",
             "zypper --non-interactive install python312 python312-pip",
             "zypper --non-interactive install python311 python311-pip",
             "zypper --non-interactive install python314 python314-pip"]

    def box(self, tmp_path, **kw):
        return Box(tmp_path, "sles", present=("python3=3.6", "docker", "rsync"),
                   failing_until_suseconnect="install python31", **kw)

    def test_with_a_yes_the_module_is_switched_on_and_python_comes(self, tmp_path):
        box = self.box(tmp_path, suse_status=SUSE_REGISTERED, answer="j")
        run, calls = box.run('ensure_python; echo "PYTHON=$PYTHON"')
        assert run.returncode == 0, said(run)
        assert calls == self.TRIED + ["SUSEConnect --status", f"SUSEConnect -p {PYTHON_MODULE}", self.TRIED[0]]
        assert f"PYTHON={box.bin / 'python3.13'}" in run.stdout
        assert f"SUSEs Python 3 Module einschalten (SUSEConnect -p {PYTHON_MODULE})? [j/N]" in run.stdout
        assert "EXAMPLE-NOT-A-CODE" not in said(run), "the registration status is read, never shown"

    @pytest.mark.parametrize("answer", ["n", None], ids=["no", "no-terminal"])
    def test_without_a_yes_it_stops_with_the_step(self, tmp_path, answer):
        box = self.box(tmp_path, suse_status=SUSE_REGISTERED, answer=answer)
        run, calls = box.run("ensure_python")
        assert run.returncode == 1, said(run)
        assert calls == self.TRIED + ["SUSEConnect --status"]
        assert f"Selbst einschalten: sudo SUSEConnect -p {PYTHON_MODULE}" in said(run)

    def test_an_unregistered_system_stops_naming_the_registration(self, tmp_path):
        box = self.box(tmp_path, suse_status=SUSE_NOT_REGISTERED, answer="j")
        run, calls = box.run("ensure_python")
        assert run.returncode == 1, said(run)
        assert "dieses SLES ist nicht registriert" in said(run) and "sudo SUSEConnect -r" in said(run)
        assert f"sudo SUSEConnect -p {PYTHON_MODULE}" in said(run) and "[j/N]" not in run.stdout
        assert calls == self.TRIED + ["SUSEConnect --status"]

    def test_with_the_module_on_and_still_no_python_the_hint_names_the_module(self, tmp_path):
        box = Box(tmp_path, "sles", present=("python3=3.6", "docker", "rsync"), fails=("install python31",),
                  suse_status=SUSE_WITH_PYTHON3)
        run, calls = box.run("ensure_python")
        assert run.returncode == 1, said(run)
        assert calls == self.TRIED + ["SUSEConnect --status"]
        assert "Python 3.11 bis 3.14 wird benötigt, gefunden: Python 3.6.0" in said(run)
        assert f"unter SLES aus SUSEs Python 3 Module: sudo SUSEConnect -p {PYTHON_MODULE}" in said(run)

    def test_opensuse_is_not_asked_about_a_module(self, tmp_path):
        box = Box(tmp_path, "opensuse-leap", present=("python3=3.6", "docker", "rsync"), fails=("install python31",),
                  answer="j")
        run, calls = box.run("ensure_python")
        assert run.returncode == 1 and calls == self.TRIED, said(run)
        assert "Python 3 Module" not in said(run) and "uv python install 3.13" in said(run)

    def test_the_dry_run_names_the_module_step(self, tmp_path):
        box = Box(tmp_path, "sles", present=("curl", "python3=3.6", "docker", "rsync"))
        run, calls = box.run("main --dry-run")
        assert run.returncode == 0 and calls == [], said(run)
        assert ("unter SLES liegen diese Pakete in SUSEs Python 3 Module: Gibt es keins davon, fragt der Installer, "
                f"ob er das Modul einschaltet (SUSEConnect -p {PYTHON_MODULE}; nur auf einem registrierten System)"
                ) in said(run)


# --- Alpine: git and GNU tar for the framework image ------------------------------------------------------------------

GNU_TAR = '#!/bin/sh\n[ "$1" = --version ] && echo "tar (GNU tar) 1.35"\nexit 0\n'
BUSYBOX_TAR = '#!/bin/sh\necho "tar: unrecognized option: version" >&2\nexit 1\n'


@needs_bash_and_gpg
class TestAlpineImageTools:

    def box(self, tmp_path, **kw):
        box = Box(tmp_path, "alpine", present=("curl", "docker"), **kw)
        box.apk_repositories.parent.mkdir(parents=True)
        box.apk_repositories.write_text(f"{TestAlpine.MAIN}\n{TestAlpine.COMMUNITY}\n")
        (box.bin / "usermod").unlink()
        return box

    def test_busybox_tar_and_no_git_get_both(self, tmp_path):
        box = self.box(tmp_path)
        box.write(box.bin / "tar", BUSYBOX_TAR)
        run, calls = box.run("setup_system")
        assert run.returncode == 1, said(run)
        assert calls == ["addgroup lcf docker", "apk update", "apk add git tar"]
        assert "fehlen: git tar" in run.stdout and "sind installiert" in said(run)

    def test_with_git_and_gnu_tar_nothing_is_installed(self, tmp_path):
        box = self.box(tmp_path)
        box.write(box.bin / "tar", GNU_TAR)
        box.make("git")
        run, calls = box.run("setup_system")
        assert run.returncode == 1, said(run)
        assert calls == ["addgroup lcf docker"] and "sind installiert" in said(run)

    def test_only_what_is_missing_is_installed(self, tmp_path):
        box = self.box(tmp_path)
        box.make("git")
        box.write(box.bin / "tar", BUSYBOX_TAR)
        run, calls = box.run("setup_system")
        assert calls[-1] == "apk add tar", calls

    def test_when_apk_cannot_install_them_the_stop_says_how(self, tmp_path):
        box = self.box(tmp_path, fails=("add git tar",))
        run, calls = box.run("setup_system")
        assert run.returncode == 1, said(run)
        assert "Das Skript braucht dafür auch git und GNU tar (busybox' tar genügt nicht): sudo apk add git tar." in said(run)

    def test_the_dry_run_with_both_there(self, tmp_path):
        box = self.box(tmp_path)
        box.write(box.bin / "tar", GNU_TAR)
        box.make("git")
        run, calls = box.run("main --dry-run")
        assert calls == [] and "git, GNU tar (für scripts/build_container_image.sh): vorhanden" in said(run)


@needs_bash_and_gpg
class TestAlpine:

    MAIN = "http://dl-cdn.alpinelinux.org/alpine/v3.22/main"
    COMMUNITY = "http://dl-cdn.alpinelinux.org/alpine/v3.22/community"
    EDGE = "#http://dl-cdn.alpinelinux.org/alpine/edge/community"

    def box(self, tmp_path, repositories):
        box = Box(tmp_path, "alpine")
        box.apk_repositories.parent.mkdir(parents=True)
        box.apk_repositories.write_text(repositories)
        (box.bin / "usermod").unlink()  # busybox has addgroup, no usermod
        return box

    @pytest.mark.parametrize("before, after", [
        (f"#/media/cdrom/apks\n{MAIN}\n#{COMMUNITY}\n{EDGE}\n", f"#/media/cdrom/apks\n{MAIN}\n{COMMUNITY}\n{EDGE}\n"),
        (f"{MAIN}\n", f"{MAIN}\n{COMMUNITY}\n"),
        (f"{MAIN}\n{COMMUNITY}\n", f"{MAIN}\n{COMMUNITY}\n")], ids=["commented", "missing", "enabled"])
    def test_docker_from_community_then_the_container_way(self, tmp_path, before, after):
        box = self.box(tmp_path, before)
        run, calls = box.run("setup_system")
        assert run.returncode == 1, said(run)
        assert box.apk_repositories.read_text() == after
        assert calls == ["apk update", "apk add docker docker-cli-buildx", "rc-update add docker default",
                         "rc-service docker start", "addgroup lcf docker", "apk add git tar"]
        text = said(run)
        assert "musl" in text and "onnxruntime" in text and "scripts/build_container_image.sh" in text
        assert "Docker ist eingerichtet" in text
        # The way it points to needs git and GNU tar, which Alpine does not bring: build_container_image.sh stages the
        # context with 'tar --null --files-from', and busybox' tar stops at once ("unrecognized option: null").
        # install.sh installs both (apk add git tar) and says so.
        assert "git und GNU tar" in text and "sind installiert" in text

    def test_without_a_main_line_it_stops(self, tmp_path):
        box = self.box(tmp_path, "#nothing here\n")
        run, calls = box.run("install_docker")
        assert run.returncode == 1 and "community ließ sich" in said(run), said(run)
        assert calls == []


@needs_bash_and_gpg
@pytest.mark.parametrize("system, expected", [
    ("debian", ["apt-get update", "apt-get install -y rsync"]),
    ("fedora", ["dnf install -y rsync"]),
    ("opensuse-leap", ["zypper --non-interactive install rsync"]),
    ("arch", ["pacman -S --needed --noconfirm rsync"]),
    ("gentoo", [])])
def test_rsync_comes_from_the_family_too(tmp_path, system, expected):
    """deploy_files installed a missing rsync with apt-get only (and went on when that failed)."""
    box = Box(tmp_path, system, present=("python3=3.13",))
    repo, install = tmp_path / "repo", tmp_path / "opt"
    (repo / "orchestrator").mkdir(parents=True)
    (repo / "orchestrator" / "main.py").write_text("x")
    box.env["SUDO_USER"] = getpass.getuser()
    run, calls = box.run(f'REPO_ROOT="{repo}"; INSTALL_DIR="{install}"; deploy_files')
    assert calls == expected
    if expected:
        assert run.returncode == 0, said(run)
        assert (install / ".lcf-installed-files").exists()
    else:
        assert run.returncode == 1 and "rsync fehlt" in said(run) and MANUAL in said(run)


# --- rootless by default (DESIGN: no standing rights that can do more than needed) ---------------------------------

PODMAN_PLAN = {
    "debian": "apt-get install -y podman uidmap slirp4netns dbus-user-session (dazu, wenn es das gibt: podman-docker)",
    "ubuntu": "apt-get install -y podman uidmap slirp4netns dbus-user-session (dazu, wenn es das gibt: podman-docker)",
    "fedora": "dnf install -y podman (dazu, wenn es das gibt: podman-docker)",
    "rocky": "dnf install -y podman (dazu, wenn es das gibt: podman-docker)",
    "amzn": "dnf install -y podman (dazu, wenn es das gibt: podman-docker)",
    "opensuse-tumbleweed": "zypper --non-interactive install podman (dazu, wenn es das gibt: podman-docker)",
    "sles": "zypper --non-interactive install podman (dazu, wenn es das gibt: podman-docker)",
    "arch": "pacman -S --needed --noconfirm podman (dazu, wenn es das gibt: podman-docker)",
    "manjaro": "pacman -S --needed --noconfirm podman (dazu, wenn es das gibt: podman-docker)",
}
SOCKET_STEP = "systemctl --user --machine=lcf@ enable --now podman.socket"


@needs_bash_and_gpg
class TestRootlessByDefault:
    """The installer sets up a container engine that runs with the person's own rights: rootless Podman with sub-id
    ranges and the person's podman.socket; rootless Docker with --engine=docker or where Podman cannot be installed;
    Docker as root with the group docker only with --rootful, after a warning. The dry run shows the plan per
    distribution and calls nothing."""

    @pytest.mark.parametrize("system", sorted(PODMAN_PLAN))
    def test_the_dry_run_shows_the_rootless_plan_of_each_distribution(self, tmp_path, system):
        box = Box(tmp_path, system, rootful=False)
        run, calls = box.run("main --dry-run")
        text = said(run)
        assert run.returncode == 0 and calls == [], text
        assert f"Container-Engine: rootless Podman (Standard) -> {PODMAN_PLAN[system]}" in text
        assert f"Sub-IDs für 'lcf', wo noch keine sind: 65536 IDs ab 100000 in {box.etc}/subuid und " in text
        assert f"Dienst des Benutzers: {SOCKET_STEP}" in text
        assert "Gruppe docker" not in text and "usermod" not in text and "Docker: fehlt" not in text
        assert not (box.etc / "subuid").exists(), "the dry run writes nothing"

    def test_alpine_plans_rootless_podman_and_stops_with_the_way_that_works(self, tmp_path):
        box = Box(tmp_path, "alpine", rootful=False)
        run, calls = box.run("main --dry-run")
        text = said(run)
        assert run.returncode == 1 and calls == [], text
        assert ("rootless Podman (Standard) -> apk add podman shadow-uidmap (dazu, wenn es das gibt: podman-docker)"
                in text)
        assert "ohne systemd kein Dienst podman.socket" in text
        assert "Container-Engine ohne Root-Rechte ist eingerichtet" in text and "Gruppe docker" not in text

    def test_the_installation_sets_up_podman_sub_ids_and_the_service(self, tmp_path):
        box = Box(tmp_path, "debian", present=("python3=3.13", "rsync"), rootful=False)
        run, calls = box.run("setup_system; finish")
        text = said(run)
        assert run.returncode == 0, text
        assert calls[:4] == ["apt-get update", "apt-get install -y podman uidmap slirp4netns dbus-user-session",
                             "apt-get install -y podman-docker", SOCKET_STEP], calls
        assert not any(c.startswith(("usermod", "addgroup")) for c in calls), "no group docker"
        assert any("podman system migrate" in c for c in calls), "Podman takes the new sub-ids"
        for name in ("subuid", "subgid"):
            assert (box.etc / name).read_text() == "lcf:100000:65536\n"
        assert "Rootless Podman ist eingerichtet" in text and "Engine: rootless Podman" in text
        assert "Docker-Gruppe hinzugefügt" not in text

    def test_existing_sub_ids_stay_and_new_ones_follow_the_others(self, tmp_path):
        box = Box(tmp_path, "fedora", present=("python3=3.13", "rsync", "podman"), rootful=False)
        (box.etc / "subuid").write_text("lcf:300000:65536\n")
        (box.etc / "subgid").write_text("other:100000:65536\nsmall:165536:1000\n")
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls[0] == SOCKET_STEP, "podman is there: nothing is installed"
        assert (box.etc / "subuid").read_text() == "lcf:300000:65536\n"
        assert (box.etc / "subgid").read_text() == "other:100000:65536\nsmall:165536:1000\nlcf:365536:65536\n"

    def test_without_podman_rootless_docker_comes_from_dockers_repository(self, tmp_path, keys):
        """Podman cannot be installed (here: apt fails on its packages): rootless Docker from Docker's repository
        with the checked key, docker-ce-rootless-extras, the service with root rights switched off and the person's
        own service through dockerd-rootless-setuptool.sh; no group docker."""
        box = Box(tmp_path, "debian", keys["docker.asc"], present=("python3=3.13", "rsync", "curl"),
                  pinned=keys["pinned"], fails=("install -y podman uidmap",), rootful=False)
        run, calls = box.run("setup_system")
        text = said(run)
        assert run.returncode == 0, text
        assert "Podman ließ sich nicht einrichten" in text and "Stattdessen: rootless Docker" in text
        assert f"apt-get install -y {DOCKER_CE} docker-ce-rootless-extras uidmap" in calls
        assert "systemctl disable --now docker.service docker.socket" in calls
        assert any(c.startswith("sudo ") and "dockerd-rootless-setuptool.sh install" in c for c in calls), calls
        assert not any(c.startswith(("usermod", "addgroup")) for c in calls)
        assert (box.etc / "subuid").read_text() == "lcf:100000:65536\n"
        assert keys_in(box.etc / "apt" / "keyrings" / "docker.asc", tmp_path) == [keys["pinned"]]

    def test_engine_docker_plans_rootless_docker(self, tmp_path):
        box = Box(tmp_path, "ubuntu", rootful=False)
        run, calls = box.run("main --dry-run --engine=docker")
        text = said(run)
        assert run.returncode == 0 and calls == [], text
        assert ("Container-Engine: rootless Docker (--engine=docker) -> Dockers apt-Repository "
                "https://download.docker.com/linux/ubuntu (noble)") in text
        assert f"apt-get install -y {DOCKER_CE} docker-ce-rootless-extras uidmap" in text
        assert "dockerd-rootless-setuptool.sh install" in text and "docker.service docker.socket" in text

    def test_where_docker_publishes_nothing_engine_docker_falls_back_to_podman(self, tmp_path):
        box = Box(tmp_path, "rocky", machine="ppc64le", rootful=False)
        run, calls = box.run("main --dry-run --engine=docker")
        text = said(run)
        assert run.returncode == 0 and calls == [], text
        assert "rootless Docker geht hier nicht" in text and "ppc64le" in text
        assert "rootless Podman (Standard) -> dnf install -y podman" in text

    def test_rootful_only_on_request_and_with_the_warning(self, tmp_path):
        box = Box(tmp_path, "debian", rootful=False)
        run, calls = box.run("main --dry-run --rootful")
        text = said(run)
        assert run.returncode == 0 and calls == [], text
        assert "--rootful: Docker läuft als root" in text and "dauerhaft Rechte wie root" in text
        assert "Docker: fehlt -> Dockers apt-Repository" in text and "rootless Podman" in text

    def test_a_membership_of_the_group_docker_is_named_and_left_to_the_person(self, tmp_path):
        box = Box(tmp_path, "fedora", present=("python3=3.13", "rsync", "podman"), rootful=False)
        box.write(box.bin / "groups", "#!/bin/sh\necho 'lcf : lcf wheel docker'\n")
        run, calls = box.run("setup_system")
        text = said(run)
        assert run.returncode == 0, text
        assert "in der Gruppe docker" in text and "dauerhaft Rechte wie root" in text
        assert "sudo gpasswd -d lcf docker" in text
        assert not any("gpasswd" in c for c in calls), "the installer never takes the person out by itself"

    def test_an_unknown_engine_stops_before_anything(self, tmp_path):
        box = Box(tmp_path, "debian", rootful=False)
        run, calls = box.run("main --dry-run --engine=lxc")
        assert run.returncode == 1 and calls == []
        assert "Unbekannter Wert für --engine: 'lxc'" in said(run)

    def test_root_without_sudo_gets_no_engine_of_its_own(self, tmp_path):
        box = Box(tmp_path, "debian", present=("python3=3.13", "rsync"), rootful=False)
        box.env["SUDO_USER"] = "root"
        run, calls = box.run("setup_system")
        assert run.returncode == 0 and calls == [], said(run)
        assert "nicht für root" in said(run)


@needs_bash_and_gpg
class TestRootlessReview:
    """Review of the rootless default: what counts as the person's rootless Docker and as the group docker."""

    @staticmethod
    def _runtime(box):
        """A short runtime root (/run/user) for uid 1000 (Unix socket paths have a 108-byte limit)."""
        root = Path(tempfile.mkdtemp(prefix="rl-run-", dir="/tmp"))
        (root / "1000" / "podman").mkdir(parents=True)
        return root

    @staticmethod
    def _body(root, body):
        return f'USER_RUNTIME_ROOT="{root}"; id() {{ echo 1000; }}; {body}'

    def test_podman_dockers_link_is_not_the_persons_rootless_docker(self, tmp_path):
        """podman-docker links $XDG_RUNTIME_DIR/docker.sock to the person's Podman socket: that is Podman, so the
        installer sets up Podman's service and does not say 'rootless Docker is already there'."""
        box = Box(tmp_path, "fedora", present=("python3=3.13", "rsync", "podman"), rootful=False)
        root = self._runtime(box)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(root / "1000" / "podman" / "podman.sock"))
            (root / "1000" / "docker.sock").symlink_to(root / "1000" / "podman" / "podman.sock")
            run, calls = box.run(self._body(root, "setup_system; finish"))
            text = said(run)
            assert run.returncode == 0, text
            assert "Rootless Docker von 'lcf' ist schon da" not in text
            assert SOCKET_STEP in calls and "Engine: rootless Podman" in text
            plan, _ = box.run(self._body(root, "main --dry-run"))
            assert "rootless Docker von 'lcf', vorhanden" not in said(plan), "the dry run decides the same"
        finally:
            server.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_a_real_rootless_docker_socket_is_taken(self, tmp_path):
        box = Box(tmp_path, "fedora", present=("python3=3.13", "rsync", "podman"), rootful=False)
        root = self._runtime(box)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(root / "1000" / "docker.sock"))
            run, calls = box.run(self._body(root, "setup_system"))
            assert run.returncode == 0 and "Rootless Docker von 'lcf' ist schon da" in said(run), said(run)
            plan, _ = box.run(self._body(root, "main --dry-run"))
            assert "rootless Docker von 'lcf', vorhanden" in said(plan)
        finally:
            server.close()
            shutil.rmtree(root, ignore_errors=True)

    @pytest.mark.parametrize("groups, named", [
        ("lcf : lcf wheel docker", True),
        ("lcf : lcf docker-users", False),
        ("docker-dev : docker-dev wheel", False),
    ])
    def test_only_the_group_docker_itself_is_named(self, tmp_path, groups, named):
        box = Box(tmp_path, "fedora", present=("python3=3.13", "rsync", "podman"), rootful=False)
        box.env["SUDO_USER"] = box.env["USER"] = groups.split(" ", 1)[0]
        box.write(box.bin / "groups", f"#!/bin/sh\necho '{groups}'\n")
        run, _ = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert ("in der Gruppe docker:" in said(run)) is named, said(run)
        plan, _ = box.run("main --dry-run")
        assert ("ist in der Gruppe docker (dauerhaft" in said(plan)) is named, said(plan)
