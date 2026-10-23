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
import subprocess
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

# A package manager: writes its call down, fails when a line of a file in $LCF_FAILS is in its arguments, and
# "installs" what it is asked for (the package's command appears on the PATH).
PACKAGE_MANAGER = r'''#!/bin/sh
echo "$(basename "$0") $*" >> "$LCF_CALLS"
for f in "$LCF_FAILS"/*; do
    [ -f "$f" ] || continue
    case " $* " in *"$(cat "$f")"*) exit 100 ;; esac
done
for arg in "$@"; do
    case "$arg" in
        python3|python) "$LCF_MAKE" python3 "$LCF_PY_BASE" ;;
        python3.1[0-9]) "$LCF_MAKE" "$arg" "3.${arg#python3.}" ;;
        python31[0-9]) v="${arg#python3}"; "$LCF_MAKE" "python3.$v" "3.$v" ;;
        docker-ce|moby-engine|docker|podman-docker) "$LCF_MAKE" docker ;;
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


class Box:
    """A sandbox with the os-release of `system`. `present`: commands there from the start ("python3=3.9", "docker",
    "rsync", "curl"); `fails`: argument texts the package managers and tools fail on; `failing_until_suseconnect`:
    one that SUSEConnect lifts (the Containers Module makes docker installable)."""

    def __init__(self, tmp_path, system, key_file=None, present=("curl",), fails=(), failing_until_suseconnect=None,
                 py_base="3.13", pinned=None, machine="x86_64", tools=()):
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
        for tool in REAL_TOOLS:
            if shutil.which(tool):
                (self.bin / tool).symlink_to(shutil.which(tool))
        (self.bin / "cut").symlink_to(shutil.which("cut"))
        self.write(self.stash / "curl", CURL)
        self.write(self.root / "make", MAKE)
        for name in ("apt-get", "dnf", "zypper", "pacman", "apk"):
            if name in tools or not tools:
                self.write(self.bin / name, PACKAGE_MANAGER)
        for name in ("rpm", "systemctl", "rc-update", "rc-service", "usermod", "addgroup"):
            self.write(self.bin / name, RECORDER)
        self.write(self.bin / "SUSEConnect", RECORDER.replace("exit 0\n", f'rm -f "{self.fails}/until-suseconnect"\n'))
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
                  'APK_REPOSITORIES="$2/apk/repositories"; PACMAN_SYNC_DIR="$2/pacman-sync"; '
                  'wait_for_internet() { :; }; ')
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
         "Docker veröffentlicht für rocky kein Repository; das Paket der Distribution: dnf install -y podman-docker"),
        ("almalinux", "rhel", "dnf install -y python3 python3-pip", "dnf install -y podman-docker"),
        ("ol", "rhel", "dnf install -y python3 python3-pip", "dnf install -y podman-docker"),
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
        assert "apk add git tar" in text
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
    """Fedora, RHEL and CentOS Stream: Docker's own repository, its key checked against the pinned fingerprint."""

    @pytest.mark.parametrize("system", ["fedora", "rhel", "centos"])
    def test_python_docker_and_the_group_with_the_checked_key(self, tmp_path, keys, system):
        box = Box(tmp_path, system, keys["docker.asc"], pinned=keys["pinned"])
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["dnf install -y python3 python3-pip",
                         f"curl -fsSL --proto =https --tlsv1.2 https://download.docker.com/linux/{system}/gpg -o "
                         + calls[1].rsplit(" -o ", 1)[1],
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
        box = Box(tmp_path, "fedora", keys[download] if download else None, present=("python3=3.13",),
                  pinned=keys["pinned"])
        run, calls = box.run("install_docker")
        assert run.returncode == 1, said(run)
        assert "Docker wurde nicht installiert" in said(run) and DOCKER_MANUAL in said(run)
        assert not box.keyring.exists() and not box.repo.exists()
        assert not any(call.startswith(("rpm", "dnf install -y docker")) for call in calls)

    def test_dockers_real_fingerprint_does_not_accept_another_key(self, tmp_path, keys):
        box = Box(tmp_path, "rhel", keys["docker.asc"], present=("python3=3.13",))
        run, _ = box.run("install_docker")
        assert run.returncode == 1 and RPM_FINGERPRINT in said(run), said(run)
        assert not box.keyring.exists() and not box.repo.exists()

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
        assert run.returncode == 1, said(run)
        assert "wieder entfernt" in said(run) and "podman, buildah oder runc" in said(run)
        assert not box.repo.exists() and "systemctl enable --now docker" not in calls

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
    def test_rhel_rebuilds_get_podman_docker_without_a_docker_group(self, tmp_path, system):
        """Docker's install docs name no repository for them; their own package is podman-docker."""
        box = Box(tmp_path, system, present=("python3=3.12",))
        run, calls = box.run("setup_system")
        assert run.returncode == 0, said(run)
        assert calls == ["dnf install -y podman-docker",
                         "systemctl --user --machine=lcf@ enable --now podman.socket"]
        assert "Podman ist installiert" in run.stdout and "/podman/podman.sock" in run.stdout
        assert "DOCKER_HOST=unix:///run/user/" in run.stdout and "Gruppe docker" in run.stdout

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

    def test_sles_switches_the_containers_module_on(self, tmp_path):
        box = Box(tmp_path, "sles", present=("python3=3.13",),
                  failing_until_suseconnect="--non-interactive install docker ")
        run, calls = box.run("install_docker")
        assert run.returncode == 0, said(run)
        assert calls[:3] == ["zypper --non-interactive install docker",
                             "SUSEConnect -p sle-module-containers/15.6/x86_64",
                             "zypper --non-interactive install docker"]

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
                         "rc-service docker start", "addgroup lcf docker"]
        text = said(run)
        assert "musl" in text and "onnxruntime" in text and "scripts/build_container_image.sh" in text
        assert "Docker ist eingerichtet" in text
        # The way it points to needs git and GNU tar, which Alpine does not bring: build_container_image.sh stages the
        # context with 'tar --null --files-from', and busybox' tar stops at once ("unrecognized option: null").
        assert "apk add git tar" in text and "GNU tar" in text

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
