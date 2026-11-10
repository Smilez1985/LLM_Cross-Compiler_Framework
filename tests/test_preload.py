"""Preloads for a computer without any network: the installer's side (docs/DESIGN.md, Installer, platforms and
dependencies).

preload.sh (and preload.ps1) on a computer with a network pack the lock's packages for a target platform and Python,
uv, the system package list and SHA256SUMS into one .tar.gz through scripts/preload_lock.py; offline without a preload
the installers write both scripts for exactly this computer and wait for the archive's path; install.sh --offline
--preload FILE checks the archive and installs from it without any package index, calls no package manager and names
what it cannot install. Here pip fetches from a tiny fake wheelhouse (PIP_NO_INDEX, PIP_FIND_LINKS): nothing reaches
the network.
"""

import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import preload_lock  # noqa: E402

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_BAT = REPO_ROOT / "install.bat"
needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists() or not shutil.which("sha256sum"),
                                reason="needs bash and coreutils")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_wheel(folder: Path, name: str, version: str, tag: str = "py3-none-any") -> Path:
    """A minimal, installable wheel with one module of the package's name."""
    module = name.replace("-", "_")
    dist = f"{module}-{version}"
    files = {f"{module}.py": f"VERSION = {version!r}\n",
             f"{dist}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
             f"{dist}.dist-info/WHEEL": f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n"}
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    record.append(f"{dist}.dist-info/RECORD,,")
    files[f"{dist}.dist-info/RECORD"] = "\n".join(record) + "\n"
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / f"{dist}-{tag}.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


def lock_entry(wheel: Path, requirement: str, marker: str = "") -> str:
    return f"{requirement}{' ; ' + marker if marker else ''} \\\n    --hash=sha256:{sha256(wheel)}\n"


@pytest.fixture
def repo(tmp_path):
    """A checkout with the real installer files and a tiny lock whose packages are in a local wheelhouse."""
    root, house = tmp_path / "repo", tmp_path / "wheelhouse"
    (root / "scripts").mkdir(parents=True)
    (root / "orchestrator").mkdir()
    for name in ("install.sh", "preload.sh", "scripts/preload_lock.py"):
        shutil.copy2(REPO_ROOT / name, root / name)
    (root / "orchestrator" / "__init__.py").write_text('__version__ = "9.8.7"\n')
    alpha, beta = make_wheel(house, "alpha", "1.0"), make_wheel(house, "beta", "2.0")
    win = make_wheel(house, "winonly", "3.0")
    uv = make_wheel(house, "uv", "0.13.7")
    for tool in ("setuptools", "wheel"):
        make_wheel(house, tool, "1.0")
    lock = ("# exported\n" + lock_entry(alpha, "alpha==1.0") + "    # via llmcf\n" + lock_entry(beta, "beta==2.0")
            + lock_entry(win, "winonly==3.0", "sys_platform == 'win32'"))
    (root / "requirements-gui.txt").write_text(lock)
    (root / "requirements.txt").write_text(lock)
    (root / "requirements-resolve.txt").write_text(lock_entry(alpha, "alpha==1.0") + lock_entry(uv, "uv==0.13.7"))
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Debian GNU/Linux"\nID=debian\nVERSION_CODENAME=bookworm\n')
    return {"root": root, "house": house, "tmp": tmp_path, "os_release": os_release}


def pip_env(repo):
    return {"PATH": os.environ["PATH"], "HOME": str(repo["tmp"]), "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(repo["house"]), "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PRELOAD_PYTHON": sys.executable,
            "PIP_NO_CACHE_DIR": "1"}


def make_preload(repo, *args) -> subprocess.CompletedProcess:
    out = repo["tmp"] / "out"
    return subprocess.run(["/bin/bash", str(repo["root"] / "preload.sh"), "--target", "linux-x86_64", "--python",
                           "3.12", "--glibc", "2.31", "--os-release", str(repo["os_release"]), "--out", str(out),
                           *args], env=pip_env(repo), capture_output=True, text=True, timeout=300)


def archive_of(repo) -> Path:
    return next((repo["tmp"] / "out").glob("llmcf-preload-*.tar.gz"))


# --- the lock for another computer --------------------------------------------------------------------------------

def test_the_markers_are_evaluated_for_the_target_like_packaging_does():
    """Every marker of the shipped exports gives the same answer as packaging's own evaluator, for every target and
    Python the preload offers (the installers' Python may have no packaging, so preload_lock has its own)."""
    markers = pytest.importorskip("packaging.markers")
    checked = 0
    for name in ("requirements.txt", "requirements-gui.txt", "requirements-resolve.txt"):
        for _, marker, _ in preload_lock.entries((REPO_ROOT / name).read_text(encoding="utf-8")):
            if not marker:
                continue
            for target in preload_lock.TARGETS:
                for python in ("3.11", "3.12.3", "3.14"):
                    for env in preload_lock.environments(target, python):
                        assert preload_lock.marker_holds(marker, env) == markers.Marker(marker).evaluate(env), marker
                        checked += 1
    assert checked > 100


def test_windows_gets_its_own_packages_and_linux_does_not():
    text = (REPO_ROOT / "requirements-gui.txt").read_text(encoding="utf-8")
    names = {target: {preload_lock.name_of(e) for e in preload_lock.filter_lock(text, target, "3.12")}
             for target in ("linux-x86_64", "windows-amd64")}
    assert "pywin32" in names["windows-amd64"] and "pywin32" not in names["linux-x86_64"]
    assert "secretstorage" in names["linux-x86_64"] and "secretstorage" not in names["windows-amd64"]


def test_without_a_patch_level_both_sides_of_a_patch_marker_are_kept():
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    soupsieve = [e for e in preload_lock.filter_lock(text, "linux-x86_64", "3.11") if e.startswith("soupsieve==")]
    exact = [e for e in preload_lock.filter_lock(text, "linux-x86_64", "3.11.9") if e.startswith("soupsieve==")]
    assert len(soupsieve) == 2 and len(exact) == 1


def test_the_platform_tags_reach_back_to_manylinux2014():
    args = preload_lock.pip_args("linux-aarch64", "3.13", "2.20")
    assert args[0] == "--platform=manylinux_2_20_aarch64" and "--platform=manylinux2014_aarch64" in args
    assert "--abi=cp313" in args and "--python-version=3.13" in args
    assert preload_lock.pip_args("windows-amd64", "3.12", "")[0] == "--platform=win_amd64"
    with pytest.raises(preload_lock.PreloadError):
        preload_lock.check_target("linux-x86_64", "3.10", "2.28")


# --- preload.sh ----------------------------------------------------------------------------------------------------

@needs_bash
def test_preload_sh_writes_one_checked_archive_for_the_target(repo):
    result = make_preload(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    archive = archive_of(repo)
    assert archive.name == "llmcf-preload-9.8.7-linux-x86_64-py3.12.tar.gz"
    assert f"sha256: {sha256(archive)}" in result.stdout and "--preload-sha256" in result.stdout
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
        assert all(n == "llmcf-preload" or n.startswith("llmcf-preload/") for n in names)
        assert all(m.isfile() or m.isdir() for m in tar.getmembers())
        assert all(m.uid == 0 and m.gid == 0 for m in tar.getmembers())
        tar.extractall(repo["tmp"] / "x", filter="data")
    top = repo["tmp"] / "x" / "llmcf-preload"
    wheels = sorted(p.name for p in (top / "wheels").iterdir())
    assert wheels == ["alpha-1.0-py3-none-any.whl", "beta-2.0-py3-none-any.whl"], "winonly is for Windows only"
    assert [p.name for p in (top / "tools").iterdir()] == ["uv-0.13.7-py3-none-any.whl"]
    manifest = preload_lock.read_manifest((top / "manifest.txt").read_text())
    assert manifest["target"] == "linux-x86_64" and manifest["python"] == "3.12" and manifest["glibc"] == "2.31"
    assert manifest["lock"] == "requirements-gui.txt"
    assert manifest["lock_sha256"] == sha256(repo["root"] / "requirements-gui.txt")
    assert "manylinux_2_31_x86_64" in manifest["platforms"] and manifest["uv"] == "uv==0.13.7"
    packages = (top / "system-packages.txt").read_text()
    assert "apt" in packages and "python3-venv" in packages and "podman" in packages and "rsync" in packages
    sums = dict(reversed(line.split("  ", 1)) for line in (top / "SHA256SUMS").read_text().splitlines())
    files = {p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    assert set(sums) == files and all(sums[f] == sha256(top / f) for f in files)


@needs_bash
def test_a_hash_that_is_not_the_locks_stops_the_preload(repo):
    make_wheel(repo["house"], "beta", "2.0", tag="py3-none-any")  # the same file again: same hash
    (repo["house"] / "beta-2.0-py3-none-any.whl").write_bytes(b"something else")
    result = make_preload(repo)
    assert result.returncode != 0 and not list((repo["tmp"] / "out").glob("*.tar.gz"))


@needs_bash
def test_the_written_scripts_stand_alone(repo):
    """What the offline installer writes runs on a computer with a network without the checkout: the recipe and the
    program are inside the script."""
    spec = repo["tmp"] / "spec"
    packages = repo["tmp"] / "packages.txt"
    packages.write_text("rsync: rsync\n")
    tool = [sys.executable, "-I", str(REPO_ROOT / "scripts" / "preload_lock.py")]
    made = subprocess.run([*tool, "spec", "--requirements", str(repo["root"] / "requirements.txt"), "--resolve",
                           str(repo["root"] / "requirements-resolve.txt"), "--target", "linux-x86_64", "--python",
                           "3.12.1", "--glibc", "2.31", "--version", "9.8.7", "--system-packages", str(packages),
                           "--out", str(spec)], capture_output=True, text=True)
    assert made.returncode == 0 and made.stdout.strip() == "2", made.stderr
    written = subprocess.run([*tool, "write-scripts", "--spec", str(spec), "--out", str(repo["tmp"] / "scripts")],
                             capture_output=True, text=True)
    sh, ps1, what = written.stdout.splitlines()
    assert "2 Python packages" in what and "uv==0.13.7" in what
    ps_text = Path(ps1).read_bytes().decode("utf-8-sig")
    assert ps_text.count("\n") == ps_text.count("\r\n") and "pack --spec $Spec --out $Out" in ps_text
    away = repo["tmp"] / "elsewhere"
    away.mkdir()
    shutil.copy2(sh, away / "preload.sh")
    run = subprocess.run(["sh", "preload.sh"], cwd=away, env=pip_env(repo), capture_output=True, text=True,
                         timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    archive = next(away.glob("llmcf-preload-9.8.7-linux-x86_64-py3.12.tar.gz"))
    opened = preload_lock.open_archive(archive, repo["tmp"] / "opened")
    assert opened["lock"] == "requirements.txt" and opened["archive_sha256"] == sha256(archive)


# --- install.sh: checking the archive and installing from it ------------------------------------------------------

def bash(script: str, *args, env=None, timeout=120):
    return subprocess.run(["/bin/bash", "-c", f'source "$1"; {script}', "test", str(INSTALL_SH), *map(str, args)],
                          env=env or {"PATH": os.environ["PATH"], "HOME": "/nonexistent"}, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)


def open_preload(repo, archive: Path, extra: str = "", machine="x86_64", glibc="glibc 2.36"):
    fakes = repo["tmp"] / "fakes"
    fakes.mkdir(exist_ok=True)
    (fakes / "uname").write_text(f"#!/bin/sh\necho {machine}\n")
    (fakes / "getconf").write_text(f"#!/bin/sh\necho {glibc}\n")
    for f in fakes.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{fakes}:{os.environ['PATH']}", "HOME": str(repo["tmp"])}
    return bash(f'REPO_ROOT="$2"; PRELOAD_FILE="$3"; {extra} preload_open && echo "OPENED $PRELOAD_DIR"',
                repo["root"], archive, env=env)


def rebuild(repo, change) -> Path:
    """The good archive, unpacked, changed by change(top) and packed again (SHA256SUMS left as it was)."""
    good = archive_of(repo)
    work = repo["tmp"] / "rebuild"
    shutil.rmtree(work, ignore_errors=True)
    with tarfile.open(good) as tar:
        tar.extractall(work, filter="data")
    change(work / "llmcf-preload")
    bad = repo["tmp"] / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(work / "llmcf-preload", arcname="llmcf-preload")
    return bad


@needs_bash
class TestInstallShWithAPreload:

    @pytest.fixture(autouse=True)
    def preload(self, repo):
        assert make_preload(repo).returncode == 0
        self.repo = repo

    def test_a_good_archive_is_opened_and_its_sha256_printed(self):
        archive = archive_of(self.repo)
        result = open_preload(self.repo, archive, f'PRELOAD_SHA256="{sha256(archive).upper()}";')
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OPENED" in result.stdout and sha256(archive) in result.stdout and "Preload geprüft: 2 Pakete" in result.stdout

    def test_a_wrong_sha256_given_stops(self):
        result = open_preload(self.repo, archive_of(self.repo), 'PRELOAD_SHA256="' + "0" * 64 + '";')
        assert result.returncode == 1 and "nicht das Archiv" in result.stdout

    @pytest.mark.parametrize("change, said", [
        (lambda top: (top / "wheels" / "alpha-1.0-py3-none-any.whl").write_bytes(b"damaged"), "beschädigt"),
        (lambda top: (top / "wheels" / "extra.whl").write_bytes(b"x"), "nicht die, die SHA256SUMS nennt"),
        (lambda top: (top / "wheels" / "beta-2.0-py3-none-any.whl").unlink(), "nicht die, die SHA256SUMS nennt"),
        (lambda top: (top / "wheels" / "link.whl").symlink_to("/etc/passwd"), "weder Datei noch Ordner"),
    ])
    def test_a_changed_archive_is_refused(self, change, said):
        result = open_preload(self.repo, rebuild(self.repo, change))
        assert result.returncode == 1 and said in result.stdout, result.stdout
        assert "OPENED" not in result.stdout

    def test_a_path_outside_the_folder_is_refused_before_anything_is_unpacked(self):
        bad = self.repo["tmp"] / "escape.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            data = b"x"
            info = tarfile.TarInfo("llmcf-preload/../../escaped")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        result = open_preload(self.repo, bad)
        assert result.returncode == 1 and "außerhalb" in result.stdout
        assert not (self.repo["tmp"] / "escaped").exists()

    def test_a_builds_preload_is_not_the_installers(self):
        def as_build(top):
            manifest = top / "manifest.txt"
            manifest.write_text(manifest.read_text() + "kind=build\n")
            sums = [line for line in (top / "SHA256SUMS").read_text().splitlines() if not line.endswith(" manifest.txt")]
            (top / "SHA256SUMS").write_text("\n".join(sums + [f"{sha256(manifest)}  manifest.txt"]) + "\n")
        result = open_preload(self.repo, rebuild(self.repo, as_build))
        assert result.returncode == 1 and "Preload eines Builds" in result.stdout and "OPENED" not in result.stdout

    def test_another_platform_glibc_or_lock_is_named_with_the_command_that_fits(self):
        archive = archive_of(self.repo)
        arm = open_preload(self.repo, archive, machine="aarch64")
        assert arm.returncode == 1 and "--target linux-aarch64" in arm.stdout
        old = open_preload(self.repo, archive, glibc="glibc 2.28")
        assert old.returncode == 1 and "glibc 2.31" in old.stdout and "--glibc 2.28" in old.stdout
        headless = open_preload(self.repo, archive, "HEADLESS=1;")
        assert headless.returncode == 1 and "requirements.txt" in headless.stdout
        (self.repo["root"] / "requirements-gui.txt").write_text("changed\n")
        changed = open_preload(self.repo, archive)
        assert changed.returncode == 1 and "anderen Stand" in changed.stdout

    def test_the_venv_is_built_from_the_archive_without_any_index(self):
        """venv_from with the preload: pip --no-index --find-links, checked against the lock's hashes; a proxy that
        cannot be reached proves that nothing goes to the network."""
        install = self.repo["tmp"] / "opt"
        (install / "scripts").mkdir(parents=True)
        shutil.copy2(self.repo["root"] / "requirements-gui.txt", install)
        (install / "scripts" / "fresh_deps.py").write_text(
            "import alpha, beta, sys\nprint('selfcheck', alpha.VERSION, beta.VERSION)\n")
        fakes = self.repo["tmp"] / "fakes"
        fakes.mkdir(exist_ok=True)
        (fakes / "uname").write_text("#!/bin/sh\necho x86_64\n")
        (fakes / "getconf").write_text("#!/bin/sh\necho glibc 2.36\n")
        for f in fakes.iterdir():
            f.chmod(0o755)
        env = {"PATH": f"{fakes}:{os.environ['PATH']}", "HOME": str(self.repo["tmp"]),
               "https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "PIP_NO_CACHE_DIR": "1"}
        script = ('REPO_ROOT="$2"; INSTALL_DIR="$3"; PRELOAD_FILE="$4"; VENV_PYTHON="$5"; VENV_AS_USER=(env); '
                  'REQUIREMENTS=requirements-gui.txt; HEADLESS=0; preload_open > /dev/null; '
                  'VENV_PYTHON_VERSION=1; venv_from "$INSTALL_DIR/requirements-gui.txt" && echo BUILT')
        python = shutil.which(f"python{sys.version_info[0]}.{sys.version_info[1]}") or sys.executable
        result = bash(script, self.repo["root"], install, archive_of(self.repo), python, env=env, timeout=300)
        assert result.returncode == 0 and "BUILT" in result.stdout, result.stdout + result.stderr
        assert "selfcheck 1.0 2.0" in result.stdout

    def test_the_python_of_the_venv_must_be_the_preloads(self):
        script = ('REPO_ROOT="$2"; PRELOAD_FILE="$3"; preload_open > /dev/null; VENV_PYTHON="$4"; preload_fits; '
                  'echo FITS')
        result = open_preload(self.repo, archive_of(self.repo))
        assert result.returncode == 0
        fakes = self.repo["tmp"] / "fakes"
        py = fakes / "python-3.11"
        py.write_text('#!/bin/sh\necho 3.11\n')
        py.chmod(0o755)
        env = {"PATH": f"{fakes}:{os.environ['PATH']}", "HOME": str(self.repo["tmp"])}
        result = bash(script, self.repo["root"], archive_of(self.repo), py, env=env)
        assert result.returncode == 1 and "Python 3.12" in result.stdout and "--python 3.11" in result.stdout


# --- offline without a preload: the scripts are written and the installer waits ------------------------------------

@needs_bash
class TestTheInstallerWritesThePreloadAndWaits:

    def run(self, repo, answer=None, extra=""):
        tty = repo["tmp"] / "tty"
        if answer is not None:
            tty.write_text(answer + "\n")
        fakes = repo["tmp"] / "fakes"
        fakes.mkdir(exist_ok=True)
        (fakes / "getconf").write_text("#!/bin/sh\necho glibc 2.36\n")
        (fakes / "uname").write_text("#!/bin/sh\necho x86_64\n")
        for f in fakes.iterdir():
            f.chmod(0o755)
        env = {"PATH": f"{fakes}:{os.environ['PATH']}", "HOME": str(repo["tmp"]),
               "OS_RELEASE": str(repo["os_release"])}
        script = (f'REPO_ROOT="$2"; ASK_TTY="{tty}"; OFFLINE=1; OS_RELEASE_FILE="$3"; {extra} detect_system; '
                  'preload_request && echo "CONTINUES $PRELOAD_FILE"')
        return bash(script, repo["root"], repo["os_release"], env=env)

    def test_without_a_terminal_it_writes_the_scripts_and_stops_with_the_steps(self, repo):
        result = self.run(repo)
        assert result.returncode == 1, result.stdout + result.stderr
        folder = repo["root"] / "llmcf-preload-scripts"
        assert (folder / "preload.sh").is_file() and (folder / "preload.ps1").is_file()
        out = result.stdout
        assert str(folder / "preload.sh") in out and "Es holt:" in out and "2 Pakete" in out
        assert "--offline --preload DATEI" in out and "Kein Terminal" in out
        text = (folder / "preload.sh").read_text()
        assert "linux-x86_64" in text and "requirements-gui.txt" in text and "alpha==1.0" in text

    def test_it_waits_for_the_path_and_goes_on_with_the_checked_archive(self, repo):
        assert make_preload(repo, "--glibc", "2.31").returncode == 0
        archive = archive_of(repo)
        # the preload was made for Python 3.12; the request is for this computer's Python, so give the archive as is
        result = self.run(repo, answer=str(archive))
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"CONTINUES {archive}" in result.stdout and "Preload geprüft" in result.stdout

    def test_an_empty_answer_stops_and_changes_nothing(self, repo):
        result = self.run(repo, answer="")
        assert result.returncode == 1 and "Abgebrochen, nichts verändert" in result.stdout

    def test_an_own_mirror_for_pip_needs_no_preload(self, repo):
        result = self.run(repo, extra="PIP_INDEX_URL=https://mirror.example/simple/;")
        assert result.returncode == 0 and "CONTINUES" in result.stdout
        assert not (repo["root"] / "llmcf-preload-scripts").exists()


# --- offline: no package manager, named what is missing ------------------------------------------------------------

@needs_bash
def test_offline_no_package_manager_runs_and_what_is_missing_is_named(tmp_path):
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "apt-get").write_text(f'#!/bin/sh\necho "apt-get $*" >> "{tmp_path}/calls"\n')
    (fakes / "apt-get").chmod(0o755)
    env = {"PATH": f"{fakes}:/usr/bin:/bin", "HOME": str(tmp_path)}
    script = ('OFFLINE=1; PKG=apt; SYSTEM_DETECTED=1; pkg_install 7zip; echo "rc=$?"; '
              'podman() { :; }; command() { if [ "$2" = podman ] || [ "$2" = docker ]; then return 1; fi; builtin command "$@"; }; '
              'SUDO_USER=alice; id() { echo 1000; }; rootless_docker_there() { return 1; }; '
              'setup_engine() { echo ENGINE-SETUP; }; setup_engine_or_name; print_offline_missing; '
              'DITTO_CHOICE=auto; setup_ditto')
    result = bash(script, env=env)
    assert "rc=1" in result.stdout and not (tmp_path / "calls").exists(), "no package manager offline"
    assert "ENGINE-SETUP" not in result.stdout
    assert "Container-Engine (podman uidmap" in result.stdout and "- 7zip" in result.stdout
    assert "Dittos Laufzeit wird nicht eingerichtet" in result.stdout and "runtime import" in result.stdout


def test_main_opens_or_requests_the_preload_before_anything_changes():
    main = INSTALL_SH.read_text(encoding="utf-8").split("\nmain() {", 1)[1]
    assert main.index("preload_open") < main.index("preload_request") < main.index("setup_system")
    assert main.index("preload_request") < main.index("deploy_files")


def test_install_bat_takes_a_preload_and_writes_one_when_it_has_none():
    text = INSTALL_BAT.read_bytes().decode("utf-8")
    assert text.count("\n") == text.count("\r\n"), "cmd needs CRLF line endings"
    for needed in ('"--preload=*"', '"--preload-sha256=*"', "LCF_PRELOAD", "preload_lock.py", " open ",
                   "write-scripts", "Read-Host \"Pfad des Preload-Archivs", '"--no-index"', "llmcf-preload-scripts",
                   "Keine Konsole zum Warten"):
        assert needed in text.replace("$PreloadTool open", " open "), needed
    # offline: no winget for Python, 7-Zip or Podman, and Ditto later
    assert "Offline: Python fehlt" in text and "Offline: 7-Zip fehlt" in text
    assert "Offline: Es gibt noch keine Container-Engine" in text and "Offline: Dittos Laufzeit" in text
    assert text.index("Offline: Python fehlt") < text.index("winget install -e --id Python.Python.3.13 --scope")


@pytest.mark.skipif(not shutil.which("pwsh"), reason="needs PowerShell")
def test_preload_ps1_parses():  # pragma: no cover - only where PowerShell is installed
    for path in (REPO_ROOT / "preload.ps1",):
        result = subprocess.run(["pwsh", "-NoProfile", "-Command",
                                 f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{path}', "
                                 "[ref]$null, [ref]$e); if ($e) { $e; exit 1 }"], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
