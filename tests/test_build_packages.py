"""The build image's distribution packages in the build's preload (docs/DESIGN.md, Installer, platforms and
dependencies): the package set read from each target's Dockerfile per stage and build architecture
(Core/image_packages.py), what a base image is and has read from its layers without running it
(scripts/image_files.read_saved), the board preload's packages/ folder inside the build's preload
(scripts/preload_lock.py pack-build with scripts/deb_preload.py), its checks at take-in (the pinned keys, gpgv where it
is), the offline plan, the file repository in the build context and the Dockerfiles' step that makes it apt's one
source (scripts/build_apt_preload.sh), the CLI and the GUI's preload dialog. A fake signed archive stands in for the
distribution; a real offline image build is not part of the suite (it needs Docker and the network)."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from dockerfile_steps import find_run, run_steps
from orchestrator.Core import build_preload, image_packages
from tests.test_board_packages import PgpKey, _packet, fake_archive

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import preload_lock  # noqa: E402

dp = build_preload.deb_preload()   # the one loaded copy the framework's take-in uses
images = build_preload.reader()
POSIX_SH = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="needs a POSIX sh")
ARM64 = REPO_ROOT / "targets" / "ARM64" / "Dockerfile"
ROCKCHIP = REPO_ROOT / "targets" / "Rockchip" / "dockerfile"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- the package set, read from the Dockerfile ------------------------------------------------------------------------

BUILDER = ["build-essential", "cmake", "git", "ca-certificates", "python3", "python3-venv", "python3-dev",
           "pkg-config", "libgomp1"]
FINAL = ["cmake", "make", "gcc", "g++", "libc6-dev", "pkg-config", "git", "ca-certificates", "gosu", "python3",
         "libgomp1"]
CROSS = ["gcc-aarch64-linux-gnu", "g++-aarch64-linux-gnu"]
OPENCV = ["libgl1", "libglib2.0-0"]
EXPECTED = {
    ("ARM64", "amd64"): [BUILDER, FINAL + CROSS],
    ("ARM64", "arm64"): [BUILDER, FINAL],
    ("Rockchip", "amd64"): [BUILDER + OPENCV, FINAL + OPENCV + CROSS],
    ("Rockchip", "arm64"): [BUILDER + OPENCV, FINAL + OPENCV],
}


def shipped_targets():
    return sorted(p for p in (REPO_ROOT / "targets").iterdir() if p.is_dir() and not p.name.startswith("_"))


@pytest.mark.parametrize("target, arch", sorted(EXPECTED))
def test_the_shipped_targets_package_sets_are_read_from_their_dockerfiles(target, arch):
    from orchestrator.utils.build_context import find_dockerfile
    found, problems = image_packages.need(text(find_dockerfile(REPO_ROOT / "targets" / target)), arch)
    assert problems == []
    assert [s["packages"] for s in found] == EXPECTED[(target, arch)]
    assert [s["image"] for s in found] == ["debian:bookworm-slim", "debian:bookworm-slim"]


def test_every_shipped_target_is_read_without_a_problem_on_both_build_computers():
    from orchestrator.utils.build_context import find_dockerfile
    targets = shipped_targets()
    assert {t.name for t in targets} == {target for target, _ in EXPECTED}, "a new target gets its expected set here"
    for folder in targets:
        for arch in ("amd64", "arm64"):
            found, problems = image_packages.need(text(find_dockerfile(folder)), arch)
            assert found and problems == [], (folder.name, arch, problems)


def test_the_raw_templates_placeholders_are_named_never_guessed():
    for name in ("Dockerfile", "Dockerfile.gpu"):
        _found, problems = image_packages.need(text(REPO_ROOT / "targets" / "_template" / name), "amd64")
        named = " ".join(problems)
        assert "{packages_str}" in named and "gcc-[GNU_TRIPLET]" in named, name


TEMPLATE_BUILDER = ["build-essential", "cmake", "make", "git", "curl", "wget", "ca-certificates", "python3",
                    "python3-pip", "python3-dev", "python3-venv", "pkg-config", "libffi-dev", "libssl-dev", "jq", "bc",
                    "file", "time", "ccache", "libgomp1"]
TEMPLATE_RUNTIME = ["python3", "cmake", "make", "git", "bc", "jq", "file", "curl", "gosu", "libgomp1", "gcc", "g++",
                    "libc6-dev"]
# the board's architecture -> its cross compilers on an amd64 and an arm64 build computer ([] where they are its own)
TEMPLATE_CROSS = {
    "aarch64": {"amd64": ["gcc-aarch64-linux-gnu", "g++-aarch64-linux-gnu"], "arm64": []},
    "x86_64": {"amd64": [], "arm64": ["gcc-x86-64-linux-gnu", "g++-x86-64-linux-gnu"]},
    "armv7l": {"amd64": ["gcc-arm-linux-gnueabihf", "g++-arm-linux-gnueabihf"],
               "arm64": ["gcc-arm-linux-gnueabihf", "g++-arm-linux-gnueabihf"]},
    "riscv64": {"amd64": ["gcc-riscv64-linux-gnu", "g++-riscv64-linux-gnu"],
                "arm64": ["gcc-riscv64-linux-gnu", "g++-riscv64-linux-gnu"]},
}


@pytest.mark.parametrize("gpu", [False, True], ids=["cpu", "gpu"])
@pytest.mark.parametrize("board", sorted(TEMPLATE_CROSS))
def test_a_target_the_module_generator_makes_is_read_without_a_problem(tmp_path, board, gpu):
    """The wizard's generator fills the template; the build's preload reads every package of the result."""
    from orchestrator.Core.module_generator import ModuleGenerator, validate_target_folder
    data = dict(module_name=f"Board {board}", architecture=board, packages=["libusb-1.0-0-dev"])
    if gpu:
        data["sdk"] = "cuda"
    folder = Path(ModuleGenerator(tmp_path).generate_module(data))
    assert validate_target_folder(folder) == []
    dockerfile = text(folder / "Dockerfile")
    assert "ARG APT_PRELOAD" in dockerfile and ("xz-utils" in dockerfile) is gpu
    for arch in ("amd64", "arm64"):
        found, problems = image_packages.need(dockerfile, arch)
        assert problems == [], (arch, problems)
        builder = TEMPLATE_BUILDER[:7] + (["xz-utils"] if gpu else []) + TEMPLATE_BUILDER[7:] + ["libusb-1.0-0-dev"]
        assert [s["packages"] for s in found] == [builder, TEMPLATE_RUNTIME + TEMPLATE_CROSS[board][arch]], arch
        assert len({s["image"] for s in found}) == 1 and ("ubuntu" in found[0]["image"]) is gpu


# The template's former choice of the cross compilers, kept here to show the plain tests choose the same
OLD_CROSS = """case "$(dpkg --print-architecture):${GNU_TRIPLET}" in
    amd64:x86_64-linux-gnu|arm64:aarch64-linux-gnu|riscv64:riscv64-linux-gnu|armhf:arm-linux-gnueabihf) cross="" ;;
    *) package="$(printf '%s' "${GNU_TRIPLET}" | tr _ -)"; cross="gcc-${package} g++-${package}" ;;
esac
echo "${cross}"
"""


@POSIX_SH
@pytest.mark.parametrize("name", ["Dockerfile", "Dockerfile.gpu"])
def test_the_templates_plain_tests_choose_the_cross_compilers_the_case_chose(tmp_path, name):
    from orchestrator.Core.module_generator import GNU_TRIPLETS
    step = find_run(text(REPO_ROOT / "targets" / "_template" / name), "cross=")
    choice = step[:step.index("apt-get update")] + 'echo "${cross}"'
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for arch in ("amd64", "arm64", "armhf", "riscv64"):
        (bin_dir / "dpkg").write_text(f"#!/bin/sh\necho {arch}\n")
        (bin_dir / "dpkg").chmod(0o755)
        for triplet in GNU_TRIPLETS.values():
            env = {"PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]), "GNU_TRIPLET": triplet}
            new = subprocess.run(["sh", "-c", choice], env=env, capture_output=True, text=True)
            old = subprocess.run(["sh", "-c", OLD_CROSS], env=env, capture_output=True, text=True)
            assert new.returncode == old.returncode == 0, new.stderr
            assert new.stdout == old.stdout, (arch, triplet)


@pytest.mark.parametrize("run, packages, said", [
    ("apt-get update && apt-get install -y --no-install-recommends curl=7.88.1-10+deb12u5 jq",
     ["curl=7.88.1-10+deb12u5", "jq"], ""),
    ("DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::=--force-confold install -qq tool", ["tool"], ""),
    ("set -eu; extra=\"a1 b2\"; if [ \"$(dpkg --print-architecture)\" != amd64 ]; then extra=\"c3\"; fi; "
     "apt-get install -y ${extra}", ["a1", "b2"], ""),
    ("apt-get install -y -t bookworm-backports tool", [], "the option -t"),
    ("apt-get install -y ${UNSET}", [], "whose value is not known"),
    ("apt-get install -y \"$(cat list)\"", [], "whose value is not known"),
    ("if [ -f /x ]; then apt-get install -y tool; fi", [], "cannot evaluate"),
    ("for p in a b; do apt-get install -y \"$p\"; done", [], "cannot evaluate"),
    ("add-apt-repository ppa:someone/tools && apt-get install -y tool", ["tool"], "a package source of its own"),
    ("echo 'deb [signed-by=/k.gpg] https://x.example/ stable main' > /etc/apt/sources.list.d/x.list", [],
     "a package source of its own"),
    ("apt-get upgrade -y", [], "changes packages"),
    ("apt-get install -y Tool", [], "not a package name"),
    ("apt-get install -y lib*", [], "not a package name"),
])
def test_an_install_line_is_read_or_named(run, packages, said):
    found, problems = image_packages.need(f"FROM debian:bookworm-slim\nRUN {run}\n", "amd64")
    assert (found[0]["packages"] if found else []) == packages
    assert (said in " ".join(problems)) if said else problems == []


def test_args_env_and_stages_built_on_stages():
    dockerfile = ("ARG BASE=debian:bookworm-slim\nFROM ${BASE} AS one\nARG TOOL=jq\nENV MORE=bc\n"
                  "RUN apt-get install -y ${TOOL} $MORE\nFROM one AS two\nRUN apt-get install -y file\n"
                  "FROM ubuntu:24.04\nRUN echo no apt\n")
    stages = image_packages.stages(dockerfile, "arm64")
    assert [(s.name, s.image, s.packages) for s in stages] == [
        ("one", "debian:bookworm-slim", ["jq", "bc"]), ("two", "debian:bookworm-slim", ["file"]),
        ("2", "ubuntu:24.04", [])]
    found, _ = image_packages.need(dockerfile, "arm64")
    assert image_packages.need_key(found, "arm64") != image_packages.need_key(found, "amd64")
    assert image_packages.dpkg_arch("linux/arm64") == "arm64" and image_packages.dpkg_arch("linux/riscv64") == ""


# --- a base image's facts, read from its layers ---------------------------------------------------------------------

def layer_tar(entries) -> bytes:
    """A layer: (name, bytes) files, (name, "->target") links."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if isinstance(data, str) and data.startswith("->"):
                info.type, info.linkname = tarfile.SYMTYPE, data[2:]
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


OS_RELEASE = b'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_CODENAME=bookworm\n'
STATUS = (b"Package: libc6\nStatus: install ok installed\nVersion: 2.36-9\n\n"
          b"Package: base-files\nStatus: install ok installed\nVersion: 12.4\n\n"
          b"Package: gone\nStatus: deinstall ok config-files\nVersion: 1.0\n")


def saved_image(path: Path, layers) -> Path:
    """A docker save archive of `layers` (from the bottom up)."""
    with tarfile.open(path, "w") as tar:
        names = []
        for n, data in enumerate(layers):
            name = f"blobs/sha256/{hashlib.sha256(data).hexdigest()}"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            names.append(name)
        listing = json.dumps([{"Config": "c.json", "RepoTags": ["debian:bookworm-slim"], "Layers": names}]).encode()
        for name, data in (("manifest.json", listing), ("c.json", b"{}")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def base_layers():
    return [layer_tar([("usr/lib/os-release", OS_RELEASE), ("etc/os-release", "->../usr/lib/os-release"),
                       ("var/lib/dpkg/status", b"old")]),
            layer_tar([("var/lib/dpkg/status", STATUS), ("var/lib/.wh.secret", b"")])]


def test_the_os_release_through_its_link_and_the_newest_status_are_read(tmp_path):
    archive = saved_image(tmp_path / "image.tar", base_layers())
    read = images.read_saved(archive, ["etc/os-release", "var/lib/dpkg/status", "var/lib/secret", "nothing"])
    assert read["etc/os-release"] == OS_RELEASE and read["var/lib/dpkg/status"] == STATUS
    assert read["var/lib/secret"] is None and read["nothing"] is None
    assert dp.parse_os_release(OS_RELEASE.decode())["VERSION_CODENAME"] == "bookworm"
    assert dp.installed_from_status(STATUS.decode()) == {"libc6": "2.36-9", "base-files": "12.4"}


class FakeImage:
    def __init__(self, archive: Path, arch="arm64"):
        self.archive, self.attrs = archive, {"Architecture": arch}

    def save(self, named=False):
        data = self.archive.read_bytes()
        return [data[:1000], data[1000:]]


class FakeClient:
    def __init__(self, found=None):
        self.found = found or {}
        self.images = SimpleNamespace(get=self.get)

    def get(self, ref):
        if ref not in self.found:
            raise LookupError(ref)
        return self.found[ref]


def test_a_local_base_image_is_saved_read_and_never_run(tmp_path):
    archive = saved_image(tmp_path / "image.tar", base_layers())
    client = FakeClient({"debian:bookworm-slim": FakeImage(archive)})
    facts = image_packages.base_facts(client, "debian:bookworm-slim", "arm64")
    assert facts == {"distribution": "debian", "release": "bookworm",
                     "installed": {"libc6": "2.36-9", "base-files": "12.4"}}
    assert image_packages.base_facts(client, "debian:bookworm-slim", "amd64") is None, "another architecture"
    assert image_packages.base_facts(client, "other:1", "arm64") is None
    assert image_packages.base_facts(None, "debian:bookworm-slim", "arm64") is None


# --- the recipe ----------------------------------------------------------------------------------------------------

DOCKERFILE = """FROM debian:bookworm-slim AS builder
ARG APT_PRELOAD
COPY sources/apt/ /tmp/llmcf-apt/
RUN apt-get update && apt-get install -y --no-install-recommends fake-hwclock
FROM debian:bookworm-slim
ARG APT_PRELOAD
COPY sources/apt/ /tmp/llmcf-apt/
RUN apt-get update && apt-get install -y --no-install-recommends tool
"""


def plan_with_apt():
    from orchestrator.Core.build_image import ImagePlan, ImageSource
    return ImagePlan(sources=[ImageSource("apt", "apt packages", False, "set a mirror")])


def test_the_recipe_carries_each_stages_packages_and_its_base_and_names_what_it_cannot(tmp_path):
    archive = saved_image(tmp_path / "image.tar", base_layers())
    client = FakeClient({"debian:bookworm-slim": FakeImage(archive)})
    data = build_preload.recipe(plan_with_apt(), "Vendor", tmp_path, DOCKERFILE, platform="linux/arm64",
                                client=client)
    apt = data["apt"]
    assert (apt["distribution"], apt["release"], apt["arch"]) == ("debian", "bookworm", "arm64")
    assert [s["packages"] for s in apt["stages"]] == [["fake-hwclock"], ["tool"]]
    assert apt["stages"][0]["installed"] == {"libc6": "2.36-9", "base-files": "12.4"}
    assert build_preload.fetches(data) and data["not_covered"] == []
    assert "fake-hwclock, tool; for debian bookworm on arm64" in build_preload.describe(data)
    # no ARG APT_PRELOAD, a base image not on this computer, a line that cannot be read: named, never fetched
    for dockerfile, client_, said in (
            (DOCKERFILE.replace("ARG APT_PRELOAD\n", ""), client, "takes no ARG APT_PRELOAD"),
            (DOCKERFILE, FakeClient(), "could not be read on this computer"),
            (DOCKERFILE + "RUN apt-get install -y ${X}\n", client, "cannot be read for certain")):
        data = build_preload.recipe(plan_with_apt(), "Vendor", tmp_path, dockerfile, platform="linux/arm64",
                                    client=client_)
        assert data["apt"] is None and said in data["not_covered"][0] and not build_preload.fetches(data)


# --- packing, the checks at take-in and the file repository -----------------------------------------------------------

def certified(primary: PgpKey) -> bytes:
    """The primary key with a self-certified user id (gpgv takes no key without one)."""
    uid = b"test archive key"
    cert = primary.signature(primary.framed() + b"\xb4" + len(uid).to_bytes(4, "big") + uid, 0x13)
    return _packet(6, primary.body) + _packet(13, uid) + cert


@pytest.fixture(scope="module")
def archive_keys():
    return PgpKey(), PgpKey()


@pytest.fixture
def distribution(monkeypatch, archive_keys):
    """The fake archive as the only source of a debian release, signed by the primary key (gpgv takes it)."""
    primary = archive_keys[0]
    stranger = PgpKey()

    def tamper(files):
        files["https://keys.example.org/test.gpg"] = certified(primary) + stranger.public()
        release = files["https://deb.example.org/debian/dists/bookworm/Release"]
        files["https://deb.example.org/debian/dists/bookworm/Release.gpg"] += stranger.signature(release)
    files = fake_archive(archive_keys, sign_with_subkey=False, tamper=tamper)
    monkeypatch.setitem(dp.PINNED_KEYS, "test", (primary.fingerprint, "https://keys.example.org/test.gpg"))
    monkeypatch.setattr(dp, "sources_for", lambda d, r, a, x=(): [dp.Source("https://deb.example.org/debian", r,
                                                                            ["main"], ("test",))])

    def fetch(url):
        if url not in files:
            raise dp.PreloadError(f"{url}: 404")
        return files[url]
    monkeypatch.setattr(dp, "_fetch", fetch)
    monkeypatch.setattr(preload_lock, "_deb_preload", lambda: dp)
    return SimpleNamespace(files=files, stranger=stranger)


def apt_section(installed=True, distribution="debian"):
    found = [{"name": "builder", "image": "debian:bookworm-slim", "packages": ["fake-hwclock"]},
             {"name": "1", "image": "debian:bookworm-slim", "packages": ["tool"]}]
    have = {"libc6": "2.36-9", "base-files": "12.4"} if installed else None
    return {"distribution": distribution, "release": "bookworm" if distribution else None, "arch": "arm64",
            "key": image_packages.need_key(found, "arm64"),
            "stages": [dict(s, installed=have) for s in found]}


def recipe_with(apt, **changes):
    data = {"format": "1", "target": "Vendor", "platform": images.host_platform(), "images": [], "llama_cpp": None,
            "llama_cpp_bundle": False, "pip": None, "downloads": [], "source_images": [], "apt": apt,
            "not_covered": []}
    data.update(changes)
    return data


@pytest.fixture
def pack(tmp_path, distribution):
    """A build's preload packed from a recipe written here (its fetches answered by the fake archive)."""
    state = tmp_path / "state"

    def make(data):
        written = build_preload.write_scripts(state, data, "9.9.9")
        spec = tmp_path / "spec"
        shutil.rmtree(spec, ignore_errors=True)
        spec.mkdir()
        (spec / "sources.json").write_bytes(build_preload.recipe_bytes(data))
        (spec / "manifest.base").write_text(f"format=1\nkind=build\nversion=9.9.9\ntarget=Vendor\n"
                                            f"platform={images.host_platform()}\n"
                                            f"recipe_sha256={written.recipe_sha256}\n")
        archive = preload_lock.pack_build(spec, tmp_path / "out")
        return SimpleNamespace(state=state, archive=archive, written=written)
    return make


def manifest_of(archive: Path) -> dict:
    with tarfile.open(archive) as tar:
        return preload_lock.read_manifest(tar.extractfile("llmcf-preload/manifest.txt").read().decode())


def test_the_packages_are_the_board_preloads_folder_resolved_per_stage_and_taken_in(tmp_path, pack):
    made = pack(recipe_with(apt_section()))
    manifest = manifest_of(made.archive)
    assert manifest["packages_release"] == "bookworm" and manifest["packages_arch"] == "arm64"
    assert sorted(manifest["packages_resolved"].split(",")) == ["fake-hwclock=0.13", "libdep=2.1", "provider=3.0",
                                                                 "tool=1.0"]
    assert "the build image's distribution packages" in made.written.what
    taken = build_preload.take(made.state, "Vendor", made.archive, client=None)
    assert sorted(taken.packages) == ["fake-hwclock=0.13", "libdep=2.1", "provider=3.0", "tool=1.0"]
    assert taken.packages_for == "debian bookworm arm64" and taken.packages_gpgv == bool(shutil.which("gpgv"))
    assert any("4 distribution packages for the build image (debian bookworm arm64)" in line for line in taken.lines())
    kept = build_preload._newest_apt(build_preload.target_store(made.state, "Vendor")
                                     / f"apt-{apt_section()['key']}")
    assert kept is not None and (kept / "1" / "Release").is_file()
    # an offline build of the same Dockerfile on an arm64 computer finds them; a changed Dockerfile does not
    assert build_preload._newest_apt(build_preload.apt_store(made.state, "Vendor", DOCKERFILE, "linux/arm64")) == kept
    changed = DOCKERFILE.replace("install -y --no-install-recommends tool", "install -y --no-install-recommends tool jq")
    assert build_preload._newest_apt(build_preload.apt_store(made.state, "Vendor", changed, "linux/arm64")) is None


def test_without_the_base_images_status_the_whole_closure_is_fetched(pack):
    made = pack(recipe_with(apt_section(installed=False)))
    resolved = manifest_of(made.archive)["packages_resolved"].split(",")
    assert "libc6=2.36-9" in resolved and "base-files=12.4" in resolved


def test_a_base_image_the_preload_brings_is_read_for_its_release_and_status(tmp_path, pack, monkeypatch):
    def save_image(ref, out, platform=""):
        saved_image(Path(out), base_layers())
        return {"name": images.load_name(ref), "digest": "sha256:" + "1" * 64, "config": "sha256:" + "2" * 64}
    reader = SimpleNamespace(**{name: getattr(images, name) for name in ("host_platform", "load_name", "read_saved",
                                                                          "ImageFilesError")}, save_image=save_image)
    monkeypatch.setattr(preload_lock, "_image_files", lambda: reader)
    made = pack(recipe_with(apt_section(installed=False, distribution=None), images=["debian:bookworm-slim"]))
    manifest = manifest_of(made.archive)
    assert manifest["packages_distribution"] == "debian" and manifest["packages_release"] == "bookworm"
    assert "libc6" not in manifest["packages_resolved"], "the fetched image's status counts"


def test_another_release_in_the_fetched_image_stops_the_preload(tmp_path, pack, monkeypatch):
    def save_image(ref, out, platform=""):
        saved_image(Path(out), [layer_tar([("etc/os-release", b"ID=debian\nVERSION_CODENAME=trixie\n")])])
        return {"name": images.load_name(ref), "digest": "sha256:" + "1" * 64, "config": "sha256:" + "2" * 64}
    reader = SimpleNamespace(**{name: getattr(images, name) for name in ("host_platform", "load_name", "read_saved",
                                                                          "ImageFilesError")}, save_image=save_image)
    monkeypatch.setattr(preload_lock, "_image_files", lambda: reader)
    with pytest.raises(preload_lock.PreloadError, match="trixie"):
        pack(recipe_with(apt_section(), images=["debian:bookworm-slim"]))


def repacked(archive: Path, out: Path, change) -> Path:
    """The archive with `change` made and SHA256SUMS made to match it again."""
    work = out / "repack"
    shutil.rmtree(work, ignore_errors=True)
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(work)
    top = work / "llmcf-preload"
    change(top)
    files = sorted(p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (top / "SHA256SUMS").write_bytes("".join(f"{preload_lock.sha256_of(top / f)}  {f}\n" for f in files).encode())
    bad = out / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(top, arcname="llmcf-preload")
    return bad


def _flip(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))


@pytest.mark.parametrize("change, said", [
    (lambda top: _flip(top / "packages" / "1" / "main" / "binary-arm64" / "Packages"),
     "does not match its signed Release"),
    (lambda top: _flip(next((top / "packages" / "1" / "pool").rglob("tool_*.deb"))),
     "not in its signed lists or does not match"),
    (lambda top: (top / "packages" / "1" / "pool" / "extra.deb").write_bytes(b"x"), "not in its signed lists"),
    (lambda top: shutil.rmtree(next((top / "packages" / "1" / "pool").rglob("tool_*.deb")).parent),
     "lack tool"),
])
def test_a_changed_package_list_or_package_is_refused_and_nothing_is_kept(tmp_path, pack, change, said):
    made = pack(recipe_with(apt_section()))
    bad = repacked(made.archive, tmp_path, change)
    with pytest.raises(build_preload.PreloadProblem, match=said):
        build_preload.take(made.state, "Vendor", bad, client=None)
    assert not (build_preload.target_store(made.state, "Vendor")).exists()


def test_packages_the_recipe_does_not_name_are_refused(tmp_path, pack, monkeypatch):
    with_packages = pack(recipe_with(apt_section()))
    shutil.copytree(tmp_path / "out", tmp_path / "kept")
    monkeypatch.setattr(preload_lock, "_fetch", lambda url, dest, sha256="", size=0: Path(dest).write_bytes(b"sdk"))
    plain = pack(recipe_with(None, downloads=[{"name": "sdk.tgz", "url": "https://dl.vendor.example/sdk.tgz",
                                               "size": 3, "sha256": hashlib.sha256(b"sdk").hexdigest()}]))
    source = tmp_path / "kept" / with_packages.archive.name
    with tarfile.open(source) as tar:
        tar.extractall(tmp_path / "x", filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(
            tmp_path / "x")
    bad = repacked(plain.archive, tmp_path, lambda top: shutil.copytree(tmp_path / "x" / "llmcf-preload" / "packages",
                                                                        top / "packages"))
    with pytest.raises(build_preload.PreloadProblem, match="does not name"):
        build_preload.take(plain.state, "Vendor", bad, client=None)


@pytest.mark.skipif(not shutil.which("gpgv"), reason="needs gpgv")
def test_gpgv_takes_only_the_pinned_keys_signatures(tmp_path, pack, distribution):
    made = pack(recipe_with(apt_section()))
    with tarfile.open(made.archive) as tar:
        tar.extractall(tmp_path / "x", filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(
            tmp_path / "x")
    folder = tmp_path / "x" / "llmcf-preload" / "packages"
    assert dp.gpgv_check(folder) is True
    # the stranger's key is in the key file but never in the pinned keyring, and its signature is left out
    ring = dp.pinned_keyring(p.read_bytes() for p in (folder / "keys").glob("*"))
    assert distribution.stranger.body not in ring
    release = (folder / "1" / "Release").read_bytes()
    only_stranger = distribution.stranger.signature(release)
    (folder / "1" / "Release.gpg").write_bytes(only_stranger)
    with pytest.raises(dp.PreloadError, match="does not verify with gpgv"):
        dp.gpgv_check(folder)


def test_the_file_repository_is_signed_by_the_pinned_keys_and_never_trusted(tmp_path, pack, distribution):
    made = pack(recipe_with(apt_section()))
    build_preload.take(made.state, "Vendor", made.archive, client=None)
    kept = build_preload._newest_apt(build_preload.target_store(made.state, "Vendor") / f"apt-{apt_section()['key']}")
    context = tmp_path / "context"
    build_preload.stage(context, SimpleNamespace(preload_stage=[("apt", kept)]))
    repo = context / "sources" / "apt"
    files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file())
    assert "1/dists/bookworm/Release" in files and "1/dists/bookworm/main/binary-arm64/Packages" in files
    assert any(f.startswith("1/pool/") and f.endswith(".deb") for f in files)
    sources = (repo / "llmcf-preload.sources").read_text()
    assert "URIs: file:/tmp/llmcf-apt/1\nSuites: bookworm\nComponents: main\n" in sources
    assert "Signed-By: /tmp/llmcf-apt/keyring.gpg" in sources and "trusted" not in sources.lower()
    assert (repo / "llmcf-apt.sh").read_bytes() == (REPO_ROOT / "scripts" / "build_apt_preload.sh").read_bytes()
    # the Release unchanged (its hashes still hold), its signature only the pinned key's, armored
    assert (repo / "1" / "dists" / "bookworm" / "Release").read_bytes() == (kept / "1" / "Release").read_bytes()
    signature = (repo / "1" / "dists" / "bookworm" / "Release.gpg").read_bytes()
    assert signature.startswith(b"-----BEGIN PGP SIGNATURE-----")
    keys = dp.folder_keys(kept)
    release = (kept / "1" / "Release").read_bytes()
    assert dp.signed_by(release, signature, keys) and dp.signed_by(release, distribution.stranger.signature(release),
                                                                    keys) == ""
    assert len(list(dp.packets(dp.dearmor(signature)))) == 1
    ring = (repo / "keyring.gpg").read_bytes()
    assert dp.signing_keys(ring, dp.PINNED_KEYS["test"][0]) and distribution.stranger.body not in ring


def test_the_armor_checksum_is_openpgps_crc24():
    assert dp._crc24(b"") == 0xB704CE
    block = dp.armor(b"\x01\x02\x03")
    assert dp.dearmor(block) == b"\x01\x02\x03" and b"\n=" in block


# --- the offline plan and the build context ---------------------------------------------------------------------------

def test_apt_is_local_from_the_preload_only_when_the_dockerfile_takes_it(tmp_path, monkeypatch):
    from orchestrator.Core import build_image
    from orchestrator.Core.build_preload import PreloadSources
    from orchestrator.Core.builder import SOURCE_ONLY_ARGS
    monkeypatch.setattr("orchestrator.utils.locality.check_reach",
                        lambda host, config: SimpleNamespace(local=False, why=lambda: "internet"))
    packages = tmp_path / "apt"
    plan = build_image.offline_plan(DOCKERFILE, {"offline_mode": True}, None, preload=PreloadSources(apt=packages))
    apt = next(s for s in plan.sources if s.kind == "apt")
    assert apt.local and "preload" in apt.how and plan.build_args["APT_PRELOAD"] == "1"
    assert ("apt", packages) in plan.preload_stage and str(tmp_path) not in json.dumps(plan.ai())
    plan = build_image.offline_plan(DOCKERFILE.replace("ARG APT_PRELOAD\n", ""), {"offline_mode": True}, None,
                                    preload=PreloadSources(apt=packages))
    apt = next(s for s in plan.sources if s.kind == "apt")
    assert not apt.local and "ARG APT_PRELOAD" in apt.how and "APT_PRELOAD" not in plan.build_args
    plan = build_image.offline_plan(DOCKERFILE, {"offline_mode": True}, None, preload=PreloadSources())
    assert not next(s for s in plan.sources if s.kind == "apt").local
    assert "APT_PRELOAD" in build_image.SOURCE_ARGS and "APT_PRELOAD" in SOURCE_ONLY_ARGS


def test_the_build_context_has_the_empty_repository_folder(tmp_path):
    from orchestrator.utils.build_context import stage_build_context
    stage_build_context(REPO_ROOT / "targets" / "ARM64", REPO_ROOT, tmp_path / "ctx")
    assert (tmp_path / "ctx" / "sources" / "apt").is_dir() and not any((tmp_path / "ctx" / "sources" / "apt").iterdir())


@pytest.mark.parametrize("dockerfile", [ARM64, ROCKCHIP, REPO_ROOT / "targets" / "_template" / "Dockerfile",
                                        REPO_ROOT / "targets" / "_template" / "Dockerfile.gpu"],
                         ids=lambda p: p.parent.name + "/" + p.name)
def test_every_stage_that_runs_apt_uses_the_preloads_repository_and_gives_the_sources_back(dockerfile):
    body = text(dockerfile)
    stages = body.split("\nFROM ")[1:]
    assert len(stages) == 2
    for stage in stages:
        assert "ARG APT_PRELOAD\nCOPY sources/apt/ /tmp/llmcf-apt/\n" in stage
        copy, use = stage.index("COPY sources/apt/"), stage.index("llmcf-apt.sh use")
        install = stage.rindex("apt-get install -y")
        restore = stage.index("llmcf-apt.sh restore")
        assert copy < use < install < restore, "the restore after the stage's last install"
    steps = [s for s in run_steps(body) if "01llmcf-proxy" in s]
    assert all('if [ -n "${APT_PRELOAD:-}" ]; then sh /tmp/llmcf-apt/llmcf-apt.sh use; fi' in s for s in steps)


@POSIX_SH
def test_the_helper_sets_the_repository_as_the_one_source_and_gives_the_images_own_back(tmp_path):
    root, repo = tmp_path / "root", tmp_path / "repo"
    apt = root / "etc" / "apt"
    (apt / "sources.list.d").mkdir(parents=True)
    (apt / "apt.conf.d").mkdir()
    (apt / "sources.list.d" / "debian.sources").write_text("Types: deb\nURIs: http://deb.debian.org/debian\n")
    (apt / "sources.list").write_text("# empty\n")
    repo.mkdir()
    (repo / "llmcf-preload.sources").write_text("Types: deb\nURIs: file:/tmp/llmcf-apt/1\n")
    (repo / "keyring.gpg").write_bytes(b"k")
    env = {"PATH": os.environ.get("PATH", ""), "LLMCF_APT_ROOT": str(root), "LLMCF_APT_REPO": str(repo)}
    helper = str(REPO_ROOT / "scripts" / "build_apt_preload.sh")
    step = find_run(text(ARM64).split("\nFROM ")[1], "01llmcf-proxy").replace("/tmp/llmcf-apt/llmcf-apt.sh", helper)
    step = step.replace("/etc/apt", str(apt))
    result = subprocess.run(["sh", "-c", step], env=dict(env, APT_PRELOAD="1"), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (apt / "sources.list.d").iterdir()) == ["llmcf-preload.sources"]
    assert not (apt / "sources.list").exists()
    conf = (apt / "apt.conf.d" / "01llmcf-preload").read_text()
    assert 'Acquire::Languages "none";' in conf and '"uncompressed"' in conf
    result = subprocess.run(["sh", helper, "restore"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (apt / "sources.list.d").iterdir()) == ["debian.sources"]
    assert (apt / "sources.list").read_text() == "# empty\n" and not (apt / "apt.conf.d" / "01llmcf-preload").exists()
    assert not (apt / "llmcf-saved-sources").exists()
    # without the build argument nothing changes; without a repository the build stops
    result = subprocess.run(["sh", "-c", step], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and sorted(p.name for p in (apt / "sources.list.d").iterdir()) == ["debian.sources"]
    shutil.rmtree(repo)
    result = subprocess.run(["sh", helper, "use"], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and "no file repository" in result.stderr


# --- the ways in: the CLI and the GUI ------------------------------------------------------------------------------

def test_the_cli_says_what_the_preload_fetches_and_what_was_taken_in(tmp_path, pack, monkeypatch, capsys):
    from orchestrator import cli
    made = pack(recipe_with(apt_section()))
    cli._print_written(made.written)
    monkeypatch.setattr(build_preload, "take_in", lambda fm, target, path, actor, sha256="", log=None:
                        build_preload.take(made.state, target, path, client=None, actor=actor, sha256=sha256,
                                           log=log))
    cli._take_preload(object(), "Vendor", str(made.archive), preload_lock.sha256_of(made.archive))
    out = capsys.readouterr().out
    assert "distribution packages with their dependencies" in out.replace("\n", " ")
    assert "4 distribution packages for the build image" in out.replace("\n", " ")


def test_the_preload_dialog_shows_the_packages_and_takes_the_archive_in(qapp, tmp_path, pack, monkeypatch):
    from orchestrator.gui import build_image_window
    made = pack(recipe_with(apt_section()))
    monkeypatch.setattr(build_preload, "needed", lambda fm, target, gpu=False: object())
    monkeypatch.setattr(build_preload, "write", lambda fm, target, gpu=False, plan=None: made.written)
    monkeypatch.setattr(build_preload, "take_in", lambda fm, target, path, actor, sha256="", log=None:
                        build_preload.take(made.state, target, path, client=None, actor=actor))
    dialog = build_image_window.PreloadDialog(SimpleNamespace(config=None), "Vendor", lang="en")

    def wait(condition):
        for _ in range(500):
            qapp.processEvents()
            if condition():
                return
            __import__("time").sleep(0.01)
    wait(lambda: dialog.needed is not None)
    assert dialog.needed is True and "distribution packages" in dialog.txt_info.toPlainText()
    dialog.txt_path.setText(str(made.archive))
    dialog.take()
    wait(lambda: "distribution packages for the build image" in dialog.txt_info.toPlainText())
    assert "4 distribution packages for the build image (debian bookworm arm64)" in dialog.txt_info.toPlainText()
    dialog.close()
