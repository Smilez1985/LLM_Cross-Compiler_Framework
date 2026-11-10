"""The build's preload for a computer without any network and the files taken from vendor and community images
(docs/DESIGN.md, Installer, platforms and dependencies).

An offline build whose build container image is not here and whose sources are not all local writes preload scripts
for exactly those sources (Core/build_preload.py), in the installer's preload format of kind build
(scripts/preload_lock.py pack-build); the archive is checked and taken in as local sources. Images are read without a
container engine and without running them (scripts/image_files.py): here a tiny registry in memory and
synthetic layer tars, never a real pull or docker build.
"""

import email.message
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import preload_lock  # noqa: E402

from orchestrator.Core.build_preload import reader  # noqa: E402

image_files = reader()   # the one loaded copy the framework uses


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def layer(entries) -> bytes:
    """A gzipped layer tar from (name, kind, payload): kind file, dir, symlink, hardlink."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tar.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, payload
                tar.addfile(info)
            else:
                info.size, info.mode = len(payload), 0o755
                tar.addfile(info, io.BytesIO(payload))
    return out.getvalue()


def layer_files(tmp_path, layers):
    paths = []
    for number, data in enumerate(layers):
        path = tmp_path / f"layer{number}.tar.gz"
        path.write_bytes(data)
        paths.append(str(path))
    return lambda: paths


PIN = "vendor.example/sdk/rknn:2.3@sha256:" + "a" * 64
LIB = b"the runtime library\n"


# --- reading an image without running it ------------------------------------------------------------------------------

class TestTakingFiles:

    def test_only_the_named_file_is_taken_from_the_top_layer_and_checked(self, tmp_path):
        new = b"new version\n"
        layers = [layer([("opt/rknn/lib/librknnrt.so", "file", b"old"), ("opt/other", "file", b"x")]),
                  layer([("./opt/rknn/lib/librknnrt.so", "file", new)])]
        out = tmp_path / "out"
        found = image_files.take_files(PIN, {"/opt/rknn/lib/librknnrt.so": sha(new)}, out,
                                       layer_source=layer_files(tmp_path, layers))
        assert found["opt/rknn/lib/librknnrt.so"]["sha256"] == sha(new)
        assert sorted(p.name for p in out.iterdir()) == [sha(new)], "only the named file; the rest is discarded"

    @pytest.mark.parametrize("upper, said", [
        ([("opt/rknn/lib/.wh.librknnrt.so", "file", b"")], "is deleted"),
        ([("opt/rknn/.wh.lib", "file", b"")], "is deleted"),
        ([("opt/rknn/.wh..wh..opq", "file", b"")], "opaque"),
        ([("opt/rknn", "file", b"not a folder")], "non-folder"),
    ])
    def test_whiteouts_hide_what_is_below(self, tmp_path, upper, said):
        layers = [layer([("opt/rknn/lib/librknnrt.so", "file", LIB)]), layer(upper)]
        with pytest.raises(image_files.ImageFilesError, match=said):
            image_files.take_files(PIN, {"/opt/rknn/lib/librknnrt.so": sha(LIB)}, tmp_path / "out",
                                   layer_source=layer_files(tmp_path, layers))

    def test_an_opaque_folder_keeps_what_its_own_layer_adds(self, tmp_path):
        layers = [layer([("opt/rknn/lib/librknnrt.so", "file", b"old")]),
                  layer([("opt/rknn/.wh..wh..opq", "file", b""), ("opt/rknn/lib/librknnrt.so", "file", LIB)])]
        found = image_files.take_files(PIN, {"/opt/rknn/lib/librknnrt.so": sha(LIB)}, tmp_path / "out",
                                       layer_source=layer_files(tmp_path, layers))
        assert found["opt/rknn/lib/librknnrt.so"]["size"] == len(LIB)

    def test_a_hard_link_in_its_layer_is_a_file(self, tmp_path):
        layers = [layer([("opt/a.so", "file", LIB), ("opt/b.so", "hardlink", "opt/a.so")])]
        found = image_files.take_files(PIN, {"/opt/b.so": sha(LIB)}, tmp_path / "out",
                                       layer_source=layer_files(tmp_path, layers))
        assert (tmp_path / "out" / found["opt/b.so"]["sha256"]).read_bytes() == LIB

    @pytest.mark.parametrize("entries, wanted, said", [
        ([("opt/lib.so", "symlink", "lib.so.2")], {"/opt/lib.so": "b" * 64}, "is a link to lib.so.2"),
        ([("opt/lib", "dir", None)], {"/opt/lib": "b" * 64}, "is a folder"),
        ([("opt/x", "file", b"x")], {"/opt/missing.so": "b" * 64}, "is not in the image"),
        ([("opt/lib.so", "file", LIB)], {"/opt/lib.so": "b" * 64}, "not the pinned"),
    ])
    def test_what_cannot_be_taken_is_named(self, tmp_path, entries, wanted, said):
        with pytest.raises(image_files.ImageFilesError, match=said):
            image_files.take_files(PIN, wanted, tmp_path / "out", layer_source=layer_files(tmp_path, [layer(entries)]))
        assert not list((tmp_path / "out").glob("[0-9a-f]*")), "nothing of a refused file stays"

    def test_a_source_image_is_pinned_by_digest(self, tmp_path):
        with pytest.raises(image_files.ImageFilesError, match="pinned by its digest"):
            image_files.take_files("vendor.example/sdk:latest", {"/x": "b" * 64}, tmp_path,
                                   layer_source=lambda: [])

    @pytest.mark.parametrize("ref, parts", [
        ("debian:bookworm-slim", ("docker.io", "library/debian", "bookworm-slim", "")),
        ("ghcr.io/owner/img@sha256:" + "c" * 64, ("ghcr.io", "owner/img", "", "sha256:" + "c" * 64)),
        ("localhost:5000/a/b:1", ("localhost:5000", "a/b", "1", "")),
        ("owner/name", ("docker.io", "owner/name", "", "")),
    ])
    def test_references_are_read_as_docker_reads_them(self, ref, parts):
        assert image_files.parse_reference(ref) == parts

    @pytest.mark.parametrize("ref", ["", "a b", "x@sha256:short", "$BASE", "UPPER/case"])
    def test_a_bad_reference_is_refused(self, ref):
        with pytest.raises(image_files.ImageFilesError):
            image_files.parse_reference(ref)


class FakeRegistry:
    """A registry API in memory (an opener for image_files.Registry): an index for linux/amd64 and linux/arm64, the
    amd64 manifest, its config and layers; the first request without a token is answered 401 with a bearer challenge."""

    def __init__(self, layers):
        self.blobs, self.calls = {}, []
        digests = []
        diff_ids = []
        for data in layers:
            digest = "sha256:" + sha(data)
            self.blobs[digest] = data
            digests.append(digest)
            diff_ids.append("sha256:" + sha(__import__("gzip").decompress(data)))
        config = json.dumps({"rootfs": {"type": "layers", "diff_ids": diff_ids}}).encode()
        self.config = "sha256:" + sha(config)
        self.blobs[self.config] = config
        manifest = json.dumps({"schemaVersion": 2, "mediaType": image_files.MANIFEST_TYPES[0],
                               "config": {"digest": self.config},
                               "layers": [{"digest": d} for d in digests]}).encode()
        self.manifest = "sha256:" + sha(manifest)
        index = json.dumps({"schemaVersion": 2, "manifests": [
            {"digest": "sha256:" + "f" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": self.manifest, "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
        self.index = "sha256:" + sha(index)
        self.documents = {self.manifest: (manifest, image_files.MANIFEST_TYPES[0]),
                          self.index: (index, image_files.INDEX_TYPES[0]), "bookworm-slim":
                          (index, image_files.INDEX_TYPES[0])}

    def open(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        self.calls.append(url)
        if url.startswith("https://auth.example/token"):
            return self._answer(json.dumps({"token": "t0ken"}).encode())
        if not (request.unredirected_hdrs.get("Authorization") == "Bearer t0ken"):
            headers = email.message.Message()
            headers["WWW-Authenticate"] = 'Bearer realm="https://auth.example/token",service="registry.example"'
            raise urllib.error.HTTPError(url, 401, "Unauthorized", headers, None)
        kind, reference = url.split("/")[-2:]
        if kind == "manifests":
            data, media = self.documents[reference]
            return self._answer(data, media)
        return self._answer(self.blobs[reference])

    @staticmethod
    def _answer(data, media=""):
        answer = io.BytesIO(data)
        answer.headers = {"Content-Type": media}   # io.BytesIO is a context manager already
        return answer


class TestTheRegistry:

    def test_a_base_image_is_saved_as_a_docker_save_archive_named_only_by_its_reference(self, tmp_path):
        from orchestrator.Core.build_image import _archive
        registry = FakeRegistry([layer([("etc/os-release", "file", b"ID=debian\n")])])
        out = tmp_path / "base.tar"
        saved = image_files.save_image("registry.example/library/debian:bookworm-slim", out, "linux/amd64",
                                       opener=registry)
        assert saved["digest"] == registry.manifest and saved["config"] == registry.config
        assert _archive(out)[1] == ["registry.example/library/debian:bookworm-slim"]
        assert any("auth.example/token" in c for c in registry.calls), "an anonymous token was asked for"

    def test_files_are_taken_through_the_registry_pinned_by_the_index_digest(self, tmp_path):
        lower = layer([("opt/big.bin", "file", b"x" * 1000)])
        upper = layer([("opt/rknn/librknnrt.so", "file", LIB)])
        registry = FakeRegistry([lower, upper])
        pinned = f"registry.example/vendor/sdk@{registry.index}"
        image_files.take_files(pinned, {"/opt/rknn/librknnrt.so": sha(LIB)}, tmp_path / "out", "linux/amd64",
                               opener=registry)
        fetched = [c for c in registry.calls if "/blobs/" in c]
        assert fetched == [c for c in fetched if "sha256:" + sha(upper) in c], "reading stopped at the top layer"

    def test_a_manifest_that_is_not_the_pinned_one_is_refused(self, tmp_path):
        registry = FakeRegistry([layer([("x", "file", b"x")])])
        registry.documents["sha256:" + "e" * 64] = registry.documents[registry.index]
        with pytest.raises(image_files.ImageFilesError, match="not the pinned"):
            image_files.take_files("registry.example/vendor/sdk@sha256:" + "e" * 64, {"/x": sha(b"x")},
                                   tmp_path / "out", "linux/amd64", opener=registry)

    def test_a_blob_that_is_not_its_digest_is_refused(self, tmp_path):
        data = layer([("x", "file", b"x")])
        registry = FakeRegistry([data])
        registry.blobs["sha256:" + sha(data)] = data + b"tampered"
        with pytest.raises(image_files.ImageFilesError, match="does not have its digest"):
            image_files.take_files(f"registry.example/vendor/sdk@{registry.index}", {"/x": sha(b"x")},
                                   tmp_path / "out", "linux/amd64", opener=registry)

    def test_a_base_image_pinned_by_digest_is_not_one_a_preload_loads(self, tmp_path):
        with pytest.raises(image_files.ImageFilesError, match="pinned by digest"):
            image_files.load_name("ubuntu:22.04@sha256:" + "0" * 64)


# --- the recipe, the scripts and the archive -------------------------------------------------------------------------

def plan_with(*sources):
    from orchestrator.Core.build_image import ImagePlan
    return ImagePlan(sources=list(sources))


def source(kind, label, local=False, how="", ref=""):
    from orchestrator.Core.build_image import ImageSource
    return ImageSource(kind, label, local, how, ref=ref)


def target_folder(tmp_path, *, pip=True, images=()):
    folder = tmp_path / "targets" / "Vendor"
    folder.mkdir(parents=True)
    meta = {"metadata": {"name": "Vendor"}}
    if pip:
        meta["build_preload"] = {"pip": [{"requirements": ["vendor-sdk==1.0"]},
                                         {"requirements": ["x86-only==2"], "platforms": ["linux/amd64"]}]}
    if images:
        meta["template_variables"] = {"schema_version": 1, "formats": ["gguf"], "source_images": list(images)}
    (folder / "target.yml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    return folder


DOCKERFILE = """FROM debian:bookworm-slim AS builder
ARG PIP_INDEX_URL
ARG PIP_FIND_LINKS
ARG PIP_NO_INDEX
COPY sources/ /tmp/llmcf-sources/
RUN set -eu; \\
    mkdir -p /opt/vendor; \\
    if [ -f '/tmp/llmcf-sources/files/sdk.tgz' ]; then cp '/tmp/llmcf-sources/files/sdk.tgz' '/opt/vendor/sdk.tgz.part'; else \\
    curl -fsSL --proto '=https' --tlsv1.2 -o '/opt/vendor/sdk.tgz.part' 'https://dl.vendor.example/sdk.tgz'; fi; \\
    test "$(wc -c < '/opt/vendor/sdk.tgz.part')" -eq 3 \\
        || { echo 'sdk.tgz: not the size the module declares' >&2; exit 1; }; \\
    echo '""" + sha(b"sdk") + """  /opt/vendor/sdk.tgz.part' | sha256sum -c -; \\
    mv '/opt/vendor/sdk.tgz.part' '/opt/vendor/sdk.tgz'
RUN apt-get update && apt-get install -y python3-pip && pip install vendor-sdk==1.0
"""

SOURCE_IMAGE = {"image": PIN, "files": [{"path": "/opt/rknn/lib/librknnrt.so", "sha256": sha(LIB),
                                         "dest": "/opt/vendor/lib/librknnrt.so"}]}


class TestTheRecipe:

    def test_the_generator_writes_downloads_the_preload_reads_back(self):
        from orchestrator.Core.build_preload import pinned_downloads
        from orchestrator.Core.module_generator import vendor_blocks
        block = vendor_blocks({"pinned_downloads": [{"url": "https://dl.vendor.example/sdk.tgz", "name": "sdk.tgz",
                                                     "sha256": "c" * 64, "size_bytes": 10}]})["# [PINNED_DOWNLOADS]"]
        assert "/tmp/llmcf-sources/files/sdk.tgz" in block, "the preload's copy in the context comes first"
        assert pinned_downloads(block) == [{"name": "sdk.tgz", "url": "https://dl.vendor.example/sdk.tgz",
                                            "size": 10, "sha256": "c" * 64}]

    def test_the_recipe_carries_what_a_preload_can_and_names_the_rest(self, tmp_path, monkeypatch):
        from orchestrator.Core import build_preload
        monkeypatch.setattr("orchestrator.runtime.build.source.catalog_pin",
                            lambda: SimpleNamespace(repo="https://github.com/ggml-org/llama.cpp.git",
                                                    commit="d" * 40, tag="b1"))
        folder = target_folder(tmp_path, images=[SOURCE_IMAGE])
        plan = plan_with(source("base_image", "base image debian:bookworm-slim", ref="debian:bookworm-slim"),
                         source("base_image", "base image ubuntu@sha256", ref="ubuntu:22.04@sha256:" + "0" * 64),
                         source("llama_cpp", "llama.cpp source (the pinned commit)"),
                         source("apt", "apt packages", how="set a mirror"),
                         source("pip", "pip packages"),
                         source("download", "downloads from dl.vendor.example"),
                         source("download", "downloads from other.example"),
                         source("image_files", "files taken from the source image", ref=PIN),
                         source("base_image", "base image here", local=True, ref="alpine:3"))
        data = build_preload.recipe(plan, "Vendor", folder, DOCKERFILE, platform="linux/amd64")
        assert data["images"] == ["debian:bookworm-slim"]
        assert data["llama_cpp_bundle"] and data["llama_cpp"]["commit"] == "d" * 40
        assert [g["requirements"] for g in data["pip"]["groups"]] == [["vendor-sdk==1.0"], ["x86-only==2"]]
        assert data["downloads"] == [{"name": "sdk.tgz", "url": "https://dl.vendor.example/sdk.tgz", "size": 3,
                                      "sha256": sha(b"sdk")}]
        assert data["source_images"][0]["files"][0]["redistributable"] is False
        named = " ".join(data["not_covered"])
        assert "apt packages" in named and "other.example" in named and "pinned by digest" in named
        assert "alpine" not in json.dumps(data), "a local source is not fetched"
        text = build_preload.describe(data)
        assert "debian:bookworm-slim" in text and "sdk.tgz" in text and "vendor.example/sdk/rknn:2.3" in text

    def test_pip_without_a_declaration_is_named_not_guessed(self, tmp_path):
        from orchestrator.Core import build_preload
        data = build_preload.recipe(plan_with(source("pip", "pip packages")), "Vendor",
                                    target_folder(tmp_path, pip=False), "", platform="linux/amd64")
        assert data["pip"] is None and "declares no build_preload" in data["not_covered"][0]
        with pytest.raises(build_preload.PreloadProblem, match="Nothing a preload can bring"):
            build_preload.write_scripts(tmp_path / "state", data, "1.0")

    def test_the_pip_groups_are_fetched_for_the_image_and_a_group_of_another_computer_is_left_out(self, tmp_path,
                                                                                                    monkeypatch):
        calls = []
        monkeypatch.setattr(preload_lock.subprocess, "run",
                            lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=0))
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        pip = {"python": "3.11", "glibc": "2.36", "tools": ["pip", "setuptools<81"],
               "groups": [{"requirements": ["a==1"], "index_url": "https://download.pytorch.org/whl/cpu"},
                          {"requirements": ["x86==1"], "platforms": ["linux/amd64"]}]}
        preload_lock._gather_pip(pip, "linux/arm64", None, wheels, tmp_path)
        group = calls[0]
        assert "--platform=manylinux_2_36_aarch64" in group and "--platform=linux_aarch64" in group
        assert "--python-version=3.11" in group and "--only-binary=:all:" in group
        assert group[group.index("--index-url") + 1] == "https://download.pytorch.org/whl/cpu"
        assert (tmp_path / "group-0.txt").read_text() == "a==1\n"
        assert not (tmp_path / "group-1.txt").exists(), "the x86_64 group is not fetched for arm64"
        assert [c[c.index("--dest") + 2] for c in calls[1:]] == ["pip", "setuptools<81"], "one call per tool"

    def test_llama_cpp_requirement_files_are_read_at_the_pinned_commit_with_their_includes(self, tmp_path,
                                                                                            monkeypatch):
        fetched = {}
        files = {"requirements/requirements-convert_hf_to_gguf.txt": "-r ./requirements-convert_legacy_llama.txt\n"
                                                                     "torch~=2.6\n",
                 "requirements/requirements-convert_legacy_llama.txt": "numpy~=1.26\n"}

        def fetch(url, dest, sha256="", size=0):
            name = url.split("/" + "d" * 40 + "/", 1)[1]
            fetched[name] = url
            Path(dest).write_text(files[name])
        monkeypatch.setattr(preload_lock, "_fetch", fetch)
        base = preload_lock.raw_base("https://github.com/ggml-org/llama.cpp.git", "d" * 40)
        assert base == "https://raw.githubusercontent.com/ggml-org/llama.cpp/" + "d" * 40 + "/"
        paths = preload_lock._llama_requirements(base, ["requirements/requirements-convert_hf_to_gguf.txt"], tmp_path)
        assert sorted(fetched) == sorted(files) and paths[0].read_text().startswith("-r ./")
        with pytest.raises(preload_lock.PreloadError):
            preload_lock.raw_base("https://gitlab.example/llama.cpp.git", "d" * 40)


def write_recipe(tmp_path, state, data, monkeypatch=None):
    from orchestrator.Core import build_preload
    return build_preload.write_scripts(state, data, "9.9.9")


def basic_recipe(**changes):
    data = {"format": "1", "target": "Vendor", "platform": image_files.host_platform(), "images": [],
            "llama_cpp": None, "llama_cpp_bundle": False, "pip": None,
            "downloads": [{"name": "sdk.tgz", "url": "https://dl.vendor.example/sdk.tgz", "size": 3,
                           "sha256": sha(b"sdk")}],
            "source_images": [{"image": PIN, "platform": image_files.host_platform(),
                               "files": [{"path": "/opt/rknn/lib/librknnrt.so", "sha256": sha(LIB),
                                          "redistributable": False}]}],
            "not_covered": ["apt packages: set a mirror"]}
    data.update(changes)
    return data


def fake_fetch(url, dest, sha256="", size=0):
    Path(dest).write_bytes(b"sdk")


class FakeReader:
    """image_files as the packed scripts load it, without a registry."""
    ImageFilesError = image_files.ImageFilesError
    host_platform = staticmethod(image_files.host_platform)
    load_name = staticmethod(image_files.load_name)

    @staticmethod
    def take_files(ref, files, out, platform=""):
        Path(out).mkdir(parents=True, exist_ok=True)
        for _path, digest in files.items():
            (Path(out) / digest).write_bytes(LIB)

    @staticmethod
    def save_image(ref, out, platform=""):
        manifest = json.dumps([{"Config": "c.json", "RepoTags": [image_files.load_name(ref)], "Layers": []}]).encode()
        with tarfile.open(out, "w") as tar:
            for name, data in (("manifest.json", manifest), ("c.json", b"{}")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return {"name": image_files.load_name(ref), "digest": "sha256:" + "1" * 64, "config": "sha256:" + "2" * 64}


@pytest.fixture
def packed(tmp_path, monkeypatch):
    """A build's preload packed from a recipe written here (no network: fetch and the reader are fakes)."""
    monkeypatch.setattr(preload_lock, "_fetch", fake_fetch)
    monkeypatch.setattr(preload_lock, "_image_files", lambda: FakeReader)
    state = tmp_path / "state"

    def make(**changes):
        written = write_recipe(tmp_path, state, basic_recipe(**changes))
        spec = tmp_path / "spec"
        shutil.rmtree(spec, ignore_errors=True)
        spec.mkdir()
        recipe = json.dumps(basic_recipe(**changes), indent=1, sort_keys=True) + "\n"
        (spec / "sources.json").write_bytes(recipe.encode("utf-8"))   # the exact bytes: no CRLF on Windows
        (spec / "manifest.base").write_text(f"format=1\nkind=build\nversion=9.9.9\ntarget=Vendor\n"
                                            f"platform={image_files.host_platform()}\n"
                                            f"recipe_sha256={written.recipe_sha256}\n")
        archive = preload_lock.pack_build(spec, tmp_path / "out")
        return SimpleNamespace(state=state, archive=archive, written=written)
    return make


class FakeImages:
    def __init__(self):
        self.loaded = []

    def load(self, handle):
        self.loaded.append(handle.read())
        return []


class TestThePreload:

    def test_the_scripts_carry_the_recipe_and_both_programs(self, tmp_path):
        written = write_recipe(tmp_path, tmp_path / "state", basic_recipe())
        sh, ps = written.scripts
        assert written.folder == tmp_path / "state" / "llmcf-preload-scripts" / "build-Vendor"
        text = sh.read_text()
        assert "pack-build" in text and "def take_files" in text and "def pack_build" in text
        assert '"sdk.tgz"' in text and "(for linux/" in text.split("\n")[4]
        assert ps.read_bytes().startswith(b"\xef\xbb\xbf") and b"\r\n" in ps.read_bytes()
        assert (tmp_path / "state" / "build-preload" / "recipes.txt").read_text().startswith(written.recipe_sha256)

    @pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="needs sh")
    def test_the_shell_script_unpacks_exactly_the_recipe_and_the_programs(self, tmp_path):
        written = write_recipe(tmp_path, tmp_path / "state", basic_recipe())
        capture = tmp_path / "capture"
        fake = tmp_path / "fake-python"
        fake.write_text(f'#!/bin/sh\nmkdir -p {capture}\ncp -r "$(dirname "$2")"/* {capture}/\necho "$@" > '
                        f'{capture}/args\n')
        fake.chmod(0o755)
        result = subprocess.run(["sh", str(written.scripts[0]), "--out", str(tmp_path / "o")], capture_output=True,
                                text=True, env=dict(os.environ, PRELOAD_PYTHON=str(fake)))
        assert result.returncode == 0, result.stderr
        assert (capture / "preload_lock.py").read_bytes() == (REPO_ROOT / "scripts" / "preload_lock.py").read_bytes()
        assert (capture / "image_files.py").read_bytes() == \
            (REPO_ROOT / "scripts" / "image_files.py").read_bytes()
        assert json.loads((capture / "spec" / "sources.json").read_text())["downloads"][0]["name"] == "sdk.tgz"
        assert "pack-build" in (capture / "args").read_text()

    def test_a_packed_preload_is_checked_and_taken_in(self, tmp_path, packed):
        from orchestrator.Core import build_preload
        made = packed(images=["debian:bookworm-slim"])
        assert made.archive.name.startswith("llmcf-preload-build-vendor-")
        client = SimpleNamespace(images=FakeImages())
        taken = build_preload.take(made.state, "vendor", made.archive, client=client,
                                   sha256=preload_lock.sha256_of(made.archive))
        assert taken.images == ["debian:bookworm-slim"] and len(client.images.loaded) == 1
        assert taken.personal and taken.downloads == ["sdk.tgz"] and taken.image_files == 1
        assert (made.state / "build-preload" / "vendor" / "files" / "sdk.tgz").read_bytes() == b"sdk"
        assert (made.state / "build-preload" / "image-files" / sha(LIB)).read_bytes() == LIB
        assert any("apt packages" in line for line in taken.lines())

    def test_the_installer_refuses_a_builds_preload_and_the_other_way_round(self, tmp_path, packed):
        made = packed()
        with pytest.raises(preload_lock.PreloadError, match="build's preload"):
            preload_lock.open_archive(made.archive, tmp_path / "x")
        assert preload_lock.open_archive(made.archive, tmp_path / "y", kind="build")["kind"] == "build"

    def test_a_preload_of_a_recipe_not_written_here_is_refused(self, tmp_path, packed):
        from orchestrator.Core import build_preload
        made = packed()
        (made.state / "build-preload" / "recipes.txt").write_text("")
        with pytest.raises(build_preload.PreloadProblem, match="not made from a recipe") as refused:
            build_preload.take(made.state, "Vendor", made.archive, client=None, actor="ai")
        assert refused.value.code == "needs_person"
        assert not (made.state / "build-preload" / "vendor").exists(), "nothing was taken in"

    @pytest.mark.parametrize("target, said", [("Other", "not Other"), ])
    def test_a_preload_of_another_target_is_refused(self, packed, target, said):
        from orchestrator.Core import build_preload
        made = packed()
        with pytest.raises(build_preload.PreloadProblem, match=said):
            build_preload.take(made.state, target, made.archive, client=None)

    def test_an_image_named_otherwise_is_never_loaded(self, tmp_path, packed, monkeypatch):
        from orchestrator.Core import build_preload
        real = FakeReader.save_image

        def renamed(ref, out, platform=""):
            saved = real("evil/base:1", out, platform)
            return dict(saved, name=image_files.load_name(ref))
        monkeypatch.setattr(FakeReader, "save_image", staticmethod(renamed))
        made = packed(images=["debian:bookworm-slim"])
        client = SimpleNamespace(images=FakeImages())
        with pytest.raises(build_preload.PreloadProblem, match="would be named evil/base:1"):
            build_preload.take(made.state, "Vendor", made.archive, client=client)
        assert client.images.loaded == []

    def test_a_download_whose_pin_is_wrong_is_refused_although_its_sums_match(self, tmp_path, packed, monkeypatch):
        from orchestrator.Core import build_preload
        made = packed()
        # the archive rebuilt with another file of the same name and SHA256SUMS made to match it
        work = tmp_path / "re"
        with tarfile.open(made.archive) as tar:
            tar.extractall(work, filter="data")
        top = work / "llmcf-preload"
        (top / "files" / "sdk.tgz").write_bytes(b"SDK")
        files = sorted(p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
        (top / "SHA256SUMS").write_text("".join(f"{preload_lock.sha256_of(top / f)}  {f}\n" for f in files))
        bad = tmp_path / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            tar.add(top, arcname="llmcf-preload")
        with pytest.raises(build_preload.PreloadProblem, match="not the pinned file"):
            build_preload.take(made.state, "Vendor", bad, client=None)


# --- the offline plan and the build context --------------------------------------------------------------------------

class TestThePlan:

    def config(self):
        return {"offline_mode": True}

    def test_pip_is_local_from_the_preload_when_the_dockerfile_takes_it(self, tmp_path, monkeypatch):
        from orchestrator.Core import build_image
        from orchestrator.Core.build_preload import PreloadSources
        monkeypatch.setattr("orchestrator.utils.locality.check_reach",
                            lambda host, config: SimpleNamespace(local=False, why=lambda: "internet"))
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        sdk = tmp_path / "sdk.tgz"
        preload = PreloadSources(wheels=wheels, downloads={"https://dl.vendor.example/sdk.tgz": sdk},
                                 images_missing=[PIN])
        plan = build_image.offline_plan(DOCKERFILE, self.config(), None, preload=preload)
        pip = next(s for s in plan.sources if s.kind == "pip")
        assert pip.local and "preload" in pip.how
        assert plan.build_args["PIP_FIND_LINKS"] == "/tmp/llmcf-sources/wheels" and plan.build_args["PIP_NO_INDEX"]
        download = next(s for s in plan.sources if s.label == "downloads from dl.vendor.example")
        assert download.local and ("files", sdk) in plan.preload_stage and ("wheels", wheels) in plan.preload_stage
        files = next(s for s in plan.sources if s.kind == "image_files")
        assert not files.local and files.ref == PIN and "sha256" not in files.label
        assert "image_files" in json.dumps(plan.ai()) and str(tmp_path) not in json.dumps(plan.ai())

    def test_without_the_arguments_the_preloads_wheels_are_named_not_used(self, tmp_path, monkeypatch):
        from orchestrator.Core import build_image
        from orchestrator.Core.build_preload import PreloadSources
        monkeypatch.setattr("orchestrator.utils.locality.check_reach",
                            lambda host, config: SimpleNamespace(local=False, why=lambda: "internet"))
        text = DOCKERFILE.replace("ARG PIP_FIND_LINKS\n", "")
        plan = build_image.offline_plan(text, self.config(), None, preload=PreloadSources(wheels=tmp_path))
        pip = next(s for s in plan.sources if s.kind == "pip")
        assert not pip.local and "ARG PIP_FIND_LINKS" in pip.how and "PIP_FIND_LINKS" not in plan.build_args

    def test_the_source_arguments_do_not_change_the_images_inputs(self):
        from orchestrator.Core.build_image import SOURCE_ARGS
        from orchestrator.Core.builder import SOURCE_ONLY_ARGS
        assert {"PIP_FIND_LINKS", "PIP_NO_INDEX"} <= set(SOURCE_ARGS) <= SOURCE_ONLY_ARGS

    def test_the_target_dockerfiles_take_the_preloads_wheels(self):
        for name in ("targets/Rockchip/dockerfile", "targets/ARM64/Dockerfile", "targets/_template/Dockerfile",
                     "targets/_template/Dockerfile.gpu"):
            text = (REPO_ROOT / name).read_text()
            assert "ARG PIP_FIND_LINKS" in text and "ARG PIP_NO_INDEX" in text, name
            assert text.index("ARG PIP_FIND_LINKS") < text.index("COPY sources/ /tmp/llmcf-sources/"), name
        rockchip = (REPO_ROOT / "targets/Rockchip/dockerfile").read_text()
        assert '"${PIP_INDEX_URL:-}${PIP_FIND_LINKS:-}"' in rockchip
        for name in ("targets/_template/Dockerfile", "targets/_template/Dockerfile.gpu"):
            assert "COPY sources/image-files/ /" in (REPO_ROOT / name).read_text()

    def test_the_shipped_declarations_are_valid_and_match_the_rockchip_pins(self):
        from orchestrator.Core.build_preload import declared_pip
        rockchip = declared_pip(REPO_ROOT / "targets" / "Rockchip")
        text = (REPO_ROOT / "targets/Rockchip/dockerfile").read_text()
        lines = [r for g in rockchip["groups"] for r in g["requirements"]]
        assert "torch==2.6.0" in lines and "torch==2.4.0" in lines and "rknn-toolkit2==2.3.2" in lines
        wheel_sha = text.split("ARG RKLLM_WHEEL_SHA256=", 1)[1].split()[0]
        assert any(wheel_sha in line for line in lines), "the RKLLM wheel by the Dockerfile's pin"
        assert declared_pip(REPO_ROOT / "targets" / "ARM64")["groups"][0]["llama_cpp_requirements"]

    def test_the_build_context_has_the_folder_of_the_image_files(self, tmp_path):
        from orchestrator.utils.build_context import stage_build_context
        dockerfile = stage_build_context(REPO_ROOT / "targets" / "ARM64", REPO_ROOT, tmp_path / "ctx")
        assert (dockerfile.parent / "sources" / "image-files").is_dir()
        assert not list((dockerfile.parent / "sources" / "image-files").iterdir())


class TestImageFilesOfABuild:

    def test_online_the_files_are_taken_into_the_cache_and_the_context(self, tmp_path, monkeypatch):
        from orchestrator.Core import build_preload
        taken = []

        def take(ref, files, out, platform=""):
            taken.append((ref, dict(files)))
            Path(out).mkdir(parents=True, exist_ok=True)
            (Path(out) / sha(LIB)).write_bytes(LIB)
        monkeypatch.setattr(image_files, "take_files", take)
        folder = target_folder(tmp_path, images=[SOURCE_IMAGE])
        context = tmp_path / "ctx"
        assert build_preload.image_files(tmp_path / "state", folder, context, online=False) == [PIN]
        assert taken == [] and not (context / "sources").exists(), "offline: nothing fetched"
        assert build_preload.image_files(tmp_path / "state", folder, context, online=True) == []
        staged = context / "sources" / "image-files" / "opt" / "vendor" / "lib" / "librknnrt.so"
        assert staged.read_bytes() == LIB and taken[0][0] == PIN
        assert build_preload.image_files(tmp_path / "state", folder, tmp_path / "ctx2", online=False) == []
        assert len(taken) == 1, "the cache serves the next build, also offline"


# --- the template variable and the generated module -------------------------------------------------------------------

class TestTheTemplateVariable:

    def test_a_source_image_is_pinned_and_its_files_have_sha256(self):
        from pydantic import ValidationError
        from orchestrator.schemas.target_template import SourceImage, TemplateVariables
        assert SourceImage.model_validate(SOURCE_IMAGE).files[0].redistributable is False
        for bad in ({"image": "vendor/sdk:latest", "files": SOURCE_IMAGE["files"]},
                    {"image": PIN, "files": [dict(SOURCE_IMAGE["files"][0], sha256="x")]},
                    {"image": PIN, "files": [dict(SOURCE_IMAGE["files"][0], dest="/opt/../etc/x")]}):
            with pytest.raises(ValidationError):
                SourceImage.model_validate(bad)
        twice = dict(SOURCE_IMAGE, files=SOURCE_IMAGE["files"] * 1)
        with pytest.raises(ValidationError, match="each dest once"):
            TemplateVariables(formats=[{"id": "gguf", "label": "GGUF", "converter": {"command": "llama.cpp"},
                                        "input": ["hf"], "output_extension": "gguf", "quantizations": ["Q4_0"],
                                        "default_quantization": "Q4_0"}], source_images=[twice, twice])

    def test_the_module_edit_step_keeps_and_changes_the_source_images(self, tmp_path):
        from orchestrator.Core.module_generator import source_image_problems, template_variable_problems
        values, problems = source_image_problems([SOURCE_IMAGE])
        assert problems == [] and values[0]["image"] == PIN
        assert source_image_problems([{"image": "x"}])[1]
        folder = tmp_path / "m"
        folder.mkdir()
        (folder / "target.yml").write_text(yaml.safe_dump({"template_variables": {
            "schema_version": 1, "formats": ["gguf"], "source_images": [SOURCE_IMAGE], "other": 1}}))
        assert "optionally source_images" in template_variable_problems(folder)[0]

    def test_a_generated_module_declares_its_build_images_packages(self):
        from orchestrator.Core.module_generator import TEMPLATE_PIP, generated_build_preload
        text = (REPO_ROOT / "targets/_template/Dockerfile").read_text()
        block = text.split("# Install Framework Dependencies", 1)[1].split("\n\n", 1)[0]
        assert block.split("--no-cache-dir", 1)[1].replace("\\", " ").split() == TEMPLATE_PIP
        made = generated_build_preload({"pip_packages": ["vendor==1.0"], "pip_index_url": "https://pypi.example/s"},
                                       False)
        assert made["python"] == "3.11" and made["pip"][-1] == {"requirements": ["vendor==1.0"],
                                                                "index_url": "https://pypi.example/s"}
        from orchestrator.schemas.target_template import BuildPreload
        assert BuildPreload.model_validate(made).section()["groups"][0]["llama_cpp_requirements"]
        gpu = generated_build_preload({}, True)
        assert gpu["python"] == "3.10" and "onnxruntime-gpu" in gpu["pip"][0]["requirements"]


# --- the person's ways in: CLI, GUI, the AI's field -------------------------------------------------------------------

class TestTheWaysIn:

    def test_without_a_terminal_the_scripts_are_written_and_the_run_stops_with_the_steps(self, tmp_path,
                                                                                          monkeypatch, capsys):
        from orchestrator import cli
        from orchestrator.Core import build_preload
        written = build_preload.Written(tmp_path / "build-Vendor", (tmp_path / "preload.sh", tmp_path / "preload.ps1"),
                                        "base images debian:bookworm-slim", "e" * 64, ["apt packages: a mirror"])
        monkeypatch.setattr(build_preload, "needed", lambda fm, target, gpu=False: object())
        monkeypatch.setattr(build_preload, "write", lambda fm, target, gpu=False, plan=None: written)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        ctx = SimpleNamespace(framework_manager=object())
        with pytest.raises(SystemExit) as stopped:
            cli._offline_preload(ctx, "Vendor", False, None)
        assert stopped.value.code == 2
        out = capsys.readouterr().out
        assert "preload.sh" in out and "apt packages" in out and "--preload FILE" in out
        assert "Nothing was started" in out

    def test_an_ai_names_a_preload_only_as_a_local_file_reference(self):
        from orchestrator.Core.build_service import BuildService, BuildServiceError
        service = BuildService.__new__(BuildService)
        service.framework = SimpleNamespace(config=None)
        with pytest.raises(BuildServiceError) as refused:
            service.take_preload("Vendor", "/home/someone/preload.tar.gz")
        assert refused.value.field == "preload" and refused.value.code == "invalid_value"

    def test_the_start_tools_field_is_left_out_of_the_build_request(self):
        from orchestrator.schemas.build import StartBuildIn
        from orchestrator.tools.tools_builds import start_request
        inp = StartBuildIn(model="a/b", target="Rockchip", format="gguf", preload="downloads:p.tar.gz")
        assert "preload" not in start_request(inp, {}).model_dump()

    def test_the_dialog_goes_on_by_itself_when_nothing_is_missing(self, qapp, monkeypatch):
        from orchestrator.Core import build_preload
        from orchestrator.gui.build_image_window import PreloadDialog
        monkeypatch.setattr(build_preload, "needed", lambda fm, target, gpu=False: None)
        dialog = PreloadDialog(SimpleNamespace(config=None), "Vendor", lang="en")
        for _ in range(200):
            qapp.processEvents()
            if dialog.needed is not None:
                break
            __import__("time").sleep(0.01)
        assert dialog.needed is False and dialog.result() == accepted_code()

    def test_the_dialog_shows_the_scripts_and_takes_the_archive_in(self, qapp, monkeypatch, tmp_path):
        from orchestrator.Core import build_preload
        from orchestrator.gui import build_image_window
        monkeypatch.setattr(build_preload, "needed", lambda fm, target, gpu=False: object())
        monkeypatch.setattr(build_image_window, "written_text", lambda fm, t, g, lang: "Wrote preload.sh into X.")
        taken = build_preload.Taken("Vendor", "f" * 64, ["debian:bookworm-slim"], False, 0, [], 0, False, [])
        monkeypatch.setattr(build_preload, "take_in", lambda fm, target, path, actor, sha256="": taken)
        dialog = build_image_window.PreloadDialog(SimpleNamespace(config=None), "Vendor", lang="en")

        def wait(condition):
            for _ in range(300):
                qapp.processEvents()
                if condition():
                    return
                __import__("time").sleep(0.01)
        wait(lambda: dialog.needed is not None)
        assert dialog.needed is True and "preload.sh" in dialog.txt_info.toPlainText() and dialog.btn_go.isEnabled()
        dialog.txt_path.setText(str(tmp_path / "p.tar.gz"))
        dialog.take()
        wait(lambda: dialog.result() == accepted_code())
        assert dialog.result() == accepted_code() and "debian:bookworm-slim" in dialog.txt_info.toPlainText()


def accepted_code():
    from PySide6.QtWidgets import QDialog
    return QDialog.DialogCode.Accepted


def test_the_powershell_script_writes_every_part_back_byte_for_byte(tmp_path):
    """preload.ps1 carries each file in a here-string, which leaves out the last line break: Write-Part writes it
    back, so sources.json has the bytes whose sha256 the manifest names (without it a build's preload made on
    Windows was refused every time)."""
    import re
    spec = tmp_path / "spec"
    spec.mkdir()
    body = (json.dumps({"a": 1}, indent=1, sort_keys=True) + "\n").encode("utf-8")
    (spec / "sources.json").write_bytes(body)
    (spec / "manifest.base").write_bytes(b"format=1\nkind=build\n")
    sh, ps = preload_lock.write_build_scripts(spec, tmp_path / "out", "a test")
    text = Path(ps).read_text(encoding="utf-8")
    assert '+ "`n", $Utf8' in text
    part = re.search(r'Write-Part \(Join-Path \$Spec "sources.json"\) @\'\r?\n(.*?)\r?\n\'@', text, re.S).group(1)
    assert (part.replace("\r\n", "\n") + "\n").encode("utf-8") == body
