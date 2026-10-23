"""install.sh installs Docker only with a checksum (DECISIONS round 23, item 3).

Before, a missing Docker made install.sh download get.docker.com and run it as root, unchecked. Now Docker comes only
from Docker's official apt repository (Debian, Ubuntu, Raspberry Pi OS): Docker's signing key is trusted only when the
downloaded file holds exactly one key and its primary fingerprint is the one Docker's install docs publish (pinned in
the script); apt then checks every package against the signed package lists (SHA256). No downloaded script runs. Any
other system, ARMv6, a key that does not match, a key that cannot be downloaded or a failing apt stops in plain words
with Docker's manual install link, and nothing unchecked is written.

Every run here is in a sandbox: install.sh is sourced, its system paths (os-release, keyring, sources) point into the
test folder, and curl, apt-get, dpkg and uname are fakes that write down their calls. gpg is the real one, with a key
made for the test; nothing reaches the network.
"""

import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT

INSTALL_SH = REPO_ROOT / "install.sh"
DOCKER_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"  # docs.docker.com/engine/install (debian, ubuntu)
MANUAL = "https://docs.docker.com/engine/install/"
# Real tools the sandbox may use; everything else on its PATH is a fake.
REAL_TOOLS = ("bash", "gpg", "awk", "grep", "install", "mktemp", "rm", "dirname", "basename", "cat", "date", "sed",
              "cp", "mkdir", "chmod", "sort", "comm", "realpath", "id")

needs_bash_and_gpg = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists() or not shutil.which("gpg"),
                                        reason="install.sh needs bash; the key check needs gpg")

OS_RELEASE = {
    "debian": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_CODENAME=bookworm\n',
    "ubuntu": 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n',
    "raspbian": 'PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"\nID=raspbian\nID_LIKE=debian\n'
                'VERSION_CODENAME=bookworm\n',
    "fedora": 'NAME="Fedora Linux"\nID=fedora\nVERSION_ID=42\n',
    "debian-sid": 'PRETTY_NAME="Debian GNU/Linux trixie/sid"\nID=debian\n',
    # Systems built on Ubuntu or Debian (get.docker.com installed Docker there too, from the upstream repository)
    "linuxmint": 'NAME="Linux Mint"\nID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_CODENAME=wilma\n'
                 'UBUNTU_CODENAME=noble\n',
    "pop": 'NAME="Pop!_OS"\nID=pop\nID_LIKE="ubuntu debian"\nVERSION_CODENAME=jammy\nUBUNTU_CODENAME=jammy\n',
    "lmde": 'PRETTY_NAME="LMDE 6 (faye)"\nVERSION_CODENAME=faye\nID=linuxmint\nID_LIKE=debian\n'
            'DEBIAN_CODENAME=bookworm\n',
    "debian-like": 'PRETTY_NAME="Some Debian-based system"\nID=somedeb\nID_LIKE=debian\nVERSION_CODENAME=own\n',
    "kali": 'PRETTY_NAME="Kali GNU/Linux Rolling"\nID=kali\nID_LIKE=debian\nVERSION_CODENAME=kali-rolling\n',
    "ubuntu-like-no-codename": 'NAME="Something"\nID=something\nID_LIKE=ubuntu\nVERSION_CODENAME=own\n',
}


def make_key(home: Path, name: str) -> str:
    """A new signing key in the keyring `home`; returns its fingerprint."""
    home.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ, GNUPGHOME=str(home))
    subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", f"{name} <{name}@example.com>",
                    "ed25519", "sign", "never"], env=env, check=True, capture_output=True)
    listing = subprocess.run(["gpg", "--batch", "--with-colons", "--fingerprint", f"{name}@example.com"], env=env,
                             check=True, capture_output=True, text=True).stdout
    return next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))


def export(home: Path, fingerprints, target: Path) -> Path:
    env = dict(os.environ, GNUPGHOME=str(home))
    target.write_bytes(subprocess.run(["gpg", "--batch", "--armor", "--export", *fingerprints], env=env, check=True,
                                      capture_output=True).stdout)
    return target


def keys_in(keyring: Path, tmp_path: Path):
    """The primary fingerprints in a keyring file (read with a throwaway GNUPGHOME)."""
    home = tmp_path / "inspect"
    home.mkdir(mode=0o700, exist_ok=True)
    listing = subprocess.run(["gpg", "--batch", "--with-colons", "--show-keys", str(keyring)],
                             env=dict(os.environ, GNUPGHOME=str(home)), check=True, capture_output=True,
                             text=True).stdout.splitlines()
    return [listing[i + 1].split(":")[9] for i, line in enumerate(listing) if line.startswith("pub:")]


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """Two signing keys: 'docker' (pinned in the tests) and 'other'; their armored exports."""
    folder = tmp_path_factory.mktemp("keys")
    home = folder / "gnupg"
    pinned, other = make_key(home, "docker"), make_key(home, "other")
    files = {"pinned": pinned, "other": other,
             "docker.asc": export(home, [pinned], folder / "docker.asc"),
             "other.asc": export(home, [other], folder / "other.asc")}
    # Two keys in one download, in both orders (the pinned one first, then last).
    for name, parts in (("pinned-first.asc", ("docker.asc", "other.asc")),
                        ("pinned-last.asc", ("other.asc", "docker.asc"))):
        files[name] = folder / name
        files[name].write_bytes(b"".join(files[part].read_bytes() for part in parts))
    return files


class Sandbox:
    def __init__(self, tmp_path: Path, system: str, key_file, machine="x86_64", arch="amd64", apt_fails="",
                 pinned=DOCKER_FINGERPRINT, debian_version=None):
        self.root = tmp_path / "sandbox"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        self.calls = self.root / "calls.log"
        self.os_release = self.root / "etc" / "os-release"
        self.debian_version = self.root / "etc" / "debian_version"
        self.keyring = self.root / "etc" / "apt" / "keyrings" / "docker.asc"
        self.sources = self.root / "etc" / "apt" / "sources.list.d" / "docker.sources"
        self.os_release.parent.mkdir(parents=True)
        self.os_release.write_text(OS_RELEASE[system])
        if debian_version is not None:
            self.debian_version.write_text(debian_version)
        self.pinned = pinned
        for tool in REAL_TOOLS:
            if shutil.which(tool):
                (self.bin / tool).symlink_to(shutil.which(tool))
        log = f'echo "$(basename "$0") $*" >> "{self.calls}"'
        source = f'cp "{key_file}" "$out"' if key_file else "exit 22"
        self.fake("curl", f'{log}\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                          f'{source}\n')
        # apt-get fails when its arguments contain apt_fails (the n-th "update" with "update#n"); installing
        # docker-ce puts a docker command on the PATH.
        self.fake("apt-get", f'{log}\ncount_file="{self.root}/updates"\n'
                             'if [ "$1" = update ]; then n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 )); '
                             'echo "$n" > "$count_file"; fi\n'
                             f'case "$1#${{n:-}} $*" in *"{apt_fails}"*) [ -n "{apt_fails}" ] && exit 100 ;; esac\n'
                             f'case " $* " in *" docker-ce "*) printf "#!/bin/sh\\nexit 0\\n" > "{self.bin}/docker"; '
                             f'chmod +x "{self.bin}/docker" ;; esac\nexit 0\n')
        self.fake("dpkg", f'{log}\necho {arch}\n')
        self.fake("uname", f'echo {machine}\n')

    def fake(self, name: str, body: str):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def run(self, function="install_docker", tmp=None):
        """`tmp`: mktemp's folder, where the key check's throwaway keyring lands (default: one in the sandbox; its
        long path is also a test that a key check whose gpg cannot reach an agent still decides by the listing)."""
        script = ('source "$1"; OS_RELEASE_FILE="$2"; DOCKER_KEYRING="$3"; DOCKER_SOURCES="$4"; '
                  f'DOCKER_KEY_FINGERPRINT="$5"; DEBIAN_VERSION_FILE="$6"; {function}')
        self.tmp = tmp or self.root / "tmp"
        self.tmp.mkdir(exist_ok=True)
        run = subprocess.run([str(self.bin / "bash"), "-c", script, "test", str(INSTALL_SH), str(self.os_release),
                              str(self.keyring), str(self.sources), self.pinned, str(self.debian_version)],
                             env={"PATH": str(self.bin), "HOME": str(self.root), "TMPDIR": str(self.tmp)},
                             capture_output=True, text=True, timeout=60, cwd=self.root)
        return run, self.calls.read_text() if self.calls.exists() else ""


def processes_naming(folder: Path):
    """(pid, command line) of the processes whose command line names `folder` (a gpg-agent, for example)."""
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            text = cmdline.read_bytes()
        except OSError:
            continue
        if str(folder).encode() in text:
            found.append((int(cmdline.parent.name), text.replace(b"\0", b" ").decode(errors="replace")))
    return found


@pytest.fixture
def short_tmp():
    """A short folder under /tmp: there gpg could start an agent (its socket path fits), so a check that leaves one
    behind is seen. Any process left naming it is ended afterwards."""
    folder = Path(tempfile.mkdtemp(prefix="lcf-", dir="/tmp"))
    yield folder
    for pid, _ in processes_naming(folder):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    shutil.rmtree(folder, ignore_errors=True)


# --- the script itself ----------------------------------------------------------------------------------------------

def test_the_fingerprint_is_dockers_published_one_with_its_source_named():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert re.search(r'^DOCKER_KEY_FINGERPRINT="([0-9A-F]{40})"$', text, re.M).group(1) == DOCKER_FINGERPRINT
    head = text[:text.index("DOCKER_KEY_FINGERPRINT=")]
    assert "docs.docker.com/engine/install" in head and "9DC8 5822 9FC7 DD38 854A  E2D8 8D81 803C 0EBF CD88" in head
    assert f'DOCKER_MANUAL_URL="{MANUAL}"' in text


def test_no_downloaded_script_is_run():
    """The convenience script is gone; nothing is downloaded and piped into a shell."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "get.docker.com" not in text
    assert "get-docker" not in text
    assert not re.search(r"curl[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b", text)


def test_shellcheck_finds_nothing():
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck is not installed")
    run = subprocess.run([shellcheck, str(INSTALL_SH)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout


# --- the installation in the sandbox --------------------------------------------------------------------------------

@needs_bash_and_gpg
class TestDockerFromTheAptRepository:

    def test_debian_gets_the_checked_key_and_dockers_repository(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", keys["docker.asc"], pinned=keys["pinned"])
        run, calls = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert keys_in(box.keyring, tmp_path) == [keys["pinned"]]
        assert box.sources.read_text() == ("Types: deb\nURIs: https://download.docker.com/linux/debian\n"
                                           "Suites: bookworm\nComponents: stable\nArchitectures: amd64\n"
                                           f"Signed-By: {box.keyring}\n")
        lines = [line for line in calls.splitlines() if not line.startswith("dpkg")]
        assert lines == ["apt-get update",
                         "apt-get install -y --no-install-recommends ca-certificates curl gnupg",
                         "curl -fsSL --proto =https --tlsv1.2 https://download.docker.com/linux/debian/gpg -o "
                         + lines[2].rsplit(" -o ", 1)[1],
                         "apt-get update",
                         "apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin "
                         "docker-compose-plugin"]
        assert "Docker ist installiert" in run.stdout

    @pytest.mark.parametrize("download", ["docker.asc", "other.asc"], ids=["accepted", "refused"])
    def test_the_key_check_leaves_no_keyring_and_no_gpg_agent_behind(self, tmp_path, keys, download, short_tmp):
        """gpg runs with --no-autostart: a key check as root on the person's computer starts no agent that outlives
        it, and its throwaway keyring is removed either way."""
        box = Sandbox(tmp_path, "debian", keys[download], pinned=keys["pinned"])
        run, _ = box.run(tmp=short_tmp)
        assert run.returncode == (0 if download == "docker.asc" else 1), run.stdout + run.stderr
        assert list(short_tmp.iterdir()) == []
        if Path("/proc").is_dir():
            assert processes_naming(short_tmp) == []

    def test_ubuntu_uses_its_own_repository_and_codename(self, tmp_path, keys):
        box = Sandbox(tmp_path, "ubuntu", keys["docker.asc"], arch="arm64", pinned=keys["pinned"])
        run, calls = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert "URIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n" in box.sources.read_text()
        assert "Architectures: arm64\n" in box.sources.read_text()
        assert "https://download.docker.com/linux/ubuntu/gpg" in calls

    def test_raspberry_pi_os_32_bit_gets_debians_armhf_packages(self, tmp_path, keys):
        """Docker's Raspberry Pi OS page: v28 is the last for raspbian; ARMv7 uses Debian's armhf packages."""
        box = Sandbox(tmp_path, "raspbian", keys["docker.asc"], machine="armv7l", arch="armhf",
                      pinned=keys["pinned"])
        run, calls = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        text = box.sources.read_text()
        assert "URIs: https://download.docker.com/linux/debian\n" in text and "Architectures: armhf\n" in text
        assert "https://download.docker.com/linux/debian/gpg" in calls

    def test_an_existing_docker_source_is_kept(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", keys["docker.asc"], pinned=keys["pinned"])
        earlier = box.sources.parent / "docker.list"
        earlier.parent.mkdir(parents=True)
        earlier.write_text(f"deb [signed-by={box.keyring}] https://download.docker.com/linux/debian bookworm stable\n")
        run, calls = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert not box.sources.exists() and "deb [signed-by=" in earlier.read_text()
        assert "gibt es schon" in run.stdout and "docker-ce " in calls

    def test_an_existing_deb822_docker_source_is_kept(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", keys["docker.asc"], pinned=keys["pinned"])
        earlier = box.sources.parent / "docker-ce.sources"
        earlier.parent.mkdir(parents=True)
        earlier.write_text("Types: deb\nURIs: https://download.docker.com/linux/debian\nSuites: bookworm\n"
                           "Components: stable\nEnabled: yes\n")
        run, _ = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert not box.sources.exists() and "gibt es schon" in run.stdout

    @pytest.mark.parametrize("file, text", [
        # Ubuntu's release upgrade turns a third-party source off like this (and may remove its packages)
        ("docker.list", ("# deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] "
                         "https://download.docker.com/linux/debian bookworm stable # disabled on upgrade to noble\n")),
        ("docker-ce.sources", ("Types: deb\nURIs: https://download.docker.com/linux/debian\nSuites: bookworm\n"
                               "Components: stable\nEnabled: no\n")),
        ("old.sources", "# Types: deb\n# URIs: https://download.docker.com/linux/debian\n# Suites: bookworm\n"),
    ], ids=["commented-line", "deb822-enabled-no", "deb822-commented"])
    def test_a_switched_off_docker_source_does_not_count(self, tmp_path, keys, file, text):
        """apt ignores it, so Docker's packages would not be found: the installer writes its own source."""
        box = Sandbox(tmp_path, "debian", keys["docker.asc"], pinned=keys["pinned"])
        earlier = box.sources.parent / file
        earlier.parent.mkdir(parents=True)
        earlier.write_text(text)
        run, _ = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert "URIs: https://download.docker.com/linux/debian\nSuites: bookworm\n" in box.sources.read_text()
        assert earlier.read_text() == text and "gibt es schon" not in run.stdout

    @pytest.mark.parametrize("system, repo, suite", [("linuxmint", "ubuntu", "noble"), ("pop", "ubuntu", "jammy")])
    def test_a_system_built_on_ubuntu_gets_ubuntus_packages_for_its_ubuntu_release(self, tmp_path, keys, system,
                                                                                    repo, suite):
        """As get.docker.com did (and Docker's Ubuntu guide says for Linux Mint): UBUNTU_CODENAME, not the system's
        own codename."""
        box = Sandbox(tmp_path, system, keys["docker.asc"], pinned=keys["pinned"])
        run, calls = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert f"URIs: https://download.docker.com/linux/{repo}\nSuites: {suite}\n" in box.sources.read_text()
        assert f"https://download.docker.com/linux/{repo}/gpg" in calls
        assert keys_in(box.keyring, tmp_path) == [keys["pinned"]]
        assert "baut auf" in run.stdout

    @pytest.mark.parametrize("system, debian_version", [("lmde", None), ("debian-like", "12.7\n")])
    def test_a_system_built_on_debian_gets_debians_packages_for_its_debian_release(self, tmp_path, keys, system,
                                                                                    debian_version):
        """DEBIAN_CODENAME, else the release in /etc/debian_version (as get.docker.com read it); never the system's
        own codename."""
        box = Sandbox(tmp_path, system, keys["docker.asc"], pinned=keys["pinned"], debian_version=debian_version)
        run, calls = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert "URIs: https://download.docker.com/linux/debian\nSuites: bookworm\n" in box.sources.read_text()
        assert "https://download.docker.com/linux/debian/gpg" in calls

    def test_debian_testing_without_a_codename_takes_it_from_debian_version(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian-sid", keys["docker.asc"], pinned=keys["pinned"], debian_version="trixie/sid\n")
        run, _ = box.run()
        assert run.returncode == 0, run.stdout + run.stderr
        assert "Suites: trixie\n" in box.sources.read_text()


@needs_bash_and_gpg
class TestItStopsInPlainWords:

    def assert_stopped(self, run, box, calls, *said):
        assert run.returncode == 1, run.stdout + run.stderr
        text = run.stdout + run.stderr
        assert "Docker wurde nicht installiert" in text and MANUAL in text, text
        for part in said:
            assert part in text, text
        assert "docker-ce" not in calls
        assert not box.sources.exists()

    @pytest.mark.parametrize("system, debian_version, said", [
        ("fedora", None, "dieses System laut"),
        ("debian-sid", None, "VERSION_CODENAME"),
        ("kali", "kali-rolling\n", "DEBIAN_CODENAME"),
        ("ubuntu-like-no-codename", None, "UBUNTU_CODENAME")])
    def test_another_system_or_one_without_a_codename(self, tmp_path, keys, system, debian_version, said):
        box = Sandbox(tmp_path, system, keys["docker.asc"], pinned=keys["pinned"], debian_version=debian_version)
        run, calls = box.run()
        self.assert_stopped(run, box, calls, said)
        assert calls == "" and not box.keyring.exists()

    def test_raspberry_pi_os_on_armv6(self, tmp_path, keys):
        box = Sandbox(tmp_path, "raspbian", keys["docker.asc"], machine="armv6l", arch="armhf",
                      pinned=keys["pinned"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, "ARMv6")
        assert calls == ""

    def test_a_key_with_another_fingerprint_is_not_trusted(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", keys["other.asc"], pinned=keys["pinned"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, "nicht den Fingerabdruck", keys["pinned"])
        assert not box.keyring.exists()

    def test_dockers_real_fingerprint_does_not_accept_another_key(self, tmp_path, keys):
        """The pinned value of the script itself (no test override): a key made here never passes."""
        box = Sandbox(tmp_path, "debian", keys["docker.asc"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, DOCKER_FINGERPRINT)
        assert not box.keyring.exists()

    @pytest.mark.parametrize("download", ["pinned-first.asc", "pinned-last.asc"])
    def test_a_file_with_a_second_key_is_not_trusted(self, tmp_path, keys, download):
        """apt trusts every key in a Signed-By file, so a download holding more than the pinned key is refused."""
        box = Sandbox(tmp_path, "debian", keys[download], pinned=keys["pinned"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, "mehr als diesen einen Schlüssel")
        assert not box.keyring.exists()

    def test_a_key_that_cannot_be_downloaded(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", None, pinned=keys["pinned"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, "ließ sich nicht von https://download.docker.com/linux/debian/gpg laden")
        assert not box.keyring.exists()

    def test_the_tools_for_the_check_cannot_be_installed(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", keys["docker.asc"], apt_fails="install -y --no-install-recommends",
                      pinned=keys["pinned"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, "gnupg")
        assert "curl" not in calls.replace("ca-certificates curl gnupg", "")

    def test_a_failing_apt_update_takes_the_new_source_back(self, tmp_path, keys):
        box = Sandbox(tmp_path, "debian", keys["docker.asc"], apt_fails="update#2", pinned=keys["pinned"])
        run, calls = box.run()
        self.assert_stopped(run, box, calls, "apt-get update mit Dockers Repository", "wieder entfernt")
        assert calls.count("apt-get update") == 2


@needs_bash_and_gpg
def test_setup_system_installs_a_missing_docker_this_way(tmp_path, keys):
    """The wiring: setup_system without docker on the PATH goes through install_docker, never get.docker.com."""
    box = Sandbox(tmp_path, "debian", keys["docker.asc"], pinned=keys["pinned"])
    for name in ("usermod", "groups"):
        box.fake(name, 'echo "$(basename "$0") $*" >> "' + str(box.calls) + '"\n')
    (box.bin / "python3").symlink_to(shutil.which("python3"))
    run, calls = box.run('wait_for_internet() { :; }; SUDO_USER=lcf; setup_system')
    assert run.returncode == 0, run.stdout + run.stderr
    assert "https://download.docker.com/linux/debian/gpg" in calls and "get.docker.com" not in calls
    assert "apt-get install -y docker-ce" in calls and "usermod -aG docker lcf" in calls
