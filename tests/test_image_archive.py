"""A vendor image published only as a file as a source image (docs/DESIGN.md, Installer, platforms and dependencies:
a vendor image as a file).

A `docker save` archive (the legacy layout with manifest.json and repositories, and the OCI layout Docker 25 and later
write) or an OCI image layout archive, pinned by the file's sha256 (and optionally the image's config digest), is read
by the same image reader (scripts/image_files.py) as a registry image: the file's sha256 first, the image of the
platform, every document and layer against its digest and the config's diff_ids, the layers from the top down with
whiteouts, only the named regular files taken, nothing unpacked, loaded or run. The archives here are made with
tarfile and real sha256 digests; no container engine.
"""

import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import preload_lock  # noqa: E402

from orchestrator.Core.build_preload import reader  # noqa: E402

image_files = reader()   # the one loaded copy the framework uses

LIB = b"the vendor's runtime library\n"
TOOL = b"#!/bin/sh\necho converter\n"
AMD64 = "linux/amd64"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def layer(entries, gz: bool = False) -> bytes:
    """A layer tar from (name, kind, payload): kind file, dir, symlink, hardlink, chr; gzip-compressed with `gz`."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
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
            elif kind == "chr":
                info.type, info.devmajor, info.devminor = tarfile.CHRTYPE, 1, 3
                tar.addfile(info)
            else:
                info.size, info.mode = len(payload), 0o755
                tar.addfile(info, io.BytesIO(payload))
    raw = out.getvalue()
    return gzip.compress(raw, mtime=0) if gz else raw


def diff_id(data: bytes) -> str:
    return "sha256:" + sha(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)


def config_of(layers, platform: str = AMD64) -> bytes:
    os_, arch = platform.split("/")[:2]
    return json.dumps({"os": os_, "architecture": arch, "config": {},
                       "rootfs": {"type": "layers", "diff_ids": [diff_id(d) for d in layers]}}).encode()


def write_tar(path: Path, members, gz: bool = False) -> Path:
    """An archive of (name, bytes) or (name, ("symlink", target))."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if isinstance(data, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, data[1]
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    raw = buffer.getvalue()
    path.write_bytes(gzip.compress(raw, mtime=0) if gz else raw)
    return path


def docker_save(path: Path, images, gz: bool = False, extra=()) -> Path:
    """A legacy docker save archive: [(layers, platform)] -> manifest.json, repositories, ID.json, ID/layer.tar."""
    members, listing = [], []
    for number, (layers, platform) in enumerate(images):
        config = config_of(layers, platform)
        names = []
        for index, data in enumerate(layers):
            folder = sha(f"{number}-{index}".encode())
            names.append(f"{folder}/layer.tar")
            members += [(f"{folder}/VERSION", b"1.0"), (f"{folder}/layer.tar", data)]
        members.append((f"{sha(config)}.json", config))
        listing.append({"Config": f"{sha(config)}.json", "RepoTags": [f"vendor/sdk:{number}"], "Layers": names})
    members.append(("manifest.json", json.dumps(listing).encode()))
    members.append(("repositories", b"{}"))
    return write_tar(path, members + list(extra), gz)


def oci_layout(path: Path, images, nested: bool = True, with_platform: bool = True, gz: bool = False,
               drop=()) -> Path:
    """An OCI image layout archive: [(layers, platform)] -> blobs, an index (nested under index.json with `nested`)."""
    blobs, descriptors = {}, []
    for layers, platform in images:
        config = config_of(layers, platform)
        blobs[sha(config)] = config
        layer_descriptors = []
        for data in layers:
            blobs[sha(data)] = data
            media = "application/vnd.oci.image.layer.v1.tar" + ("+gzip" if data[:2] == b"\x1f\x8b" else "")
            layer_descriptors.append({"mediaType": media, "digest": "sha256:" + sha(data), "size": len(data)})
        manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                               "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                          "digest": "sha256:" + sha(config), "size": len(config)},
                               "layers": layer_descriptors}).encode()
        blobs[sha(manifest)] = manifest
        descriptor = {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": "sha256:" + sha(manifest),
                      "size": len(manifest)}
        if with_platform:
            os_, arch = platform.split("/")[:2]
            descriptor["platform"] = {"os": os_, "architecture": arch}
        descriptors.append(descriptor)
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                        "manifests": descriptors}).encode()
    if nested:
        blobs[sha(index)] = index
        index = json.dumps({"schemaVersion": 2, "manifests": [{
            "mediaType": "application/vnd.oci.image.index.v1+json", "digest": "sha256:" + sha(index),
            "size": len(index)}]}).encode()
    members = [("oci-layout", b'{"imageLayoutVersion": "1.0.0"}'), ("index.json", index)]
    members += [(f"blobs/sha256/{digest}", data) for digest, data in blobs.items() if digest not in drop]
    return write_tar(path, members, gz)


BASE = layer([("opt/", "dir", None), ("opt/vendor/", "dir", None), ("opt/vendor/lib/", "dir", None),
              ("opt/vendor/lib/librknnrt.so", "file", b"old library\n"),
              ("opt/vendor/bin/convert", "file", TOOL), ("opt/old/data", "file", b"gone\n")], gz=True)
TOP = layer([("opt/vendor/lib/librknnrt.so", "file", LIB), ("opt/.wh.old", "file", b"")])
WANTED = {"/opt/vendor/lib/librknnrt.so": sha(LIB), "/opt/vendor/bin/convert": sha(TOOL)}


def take(path: Path, files=None, platform: str = AMD64, config: str = "", pin: str = ""):
    return image_files.take_archive_files(path, pin or sha(path.read_bytes()), files or WANTED, path.parent / "out",
                                          platform, config)


# --- the reader --------------------------------------------------------------------------------------------------------

class TestReadingAnImageFile:

    @pytest.mark.parametrize("make", ["docker-save", "docker-save-gz", "oci", "oci-flat", "oci-gz"])
    def test_the_named_files_are_taken_from_the_top_layer_of_a_gzip_and_an_uncompressed_layer(self, tmp_path, make):
        images = [([BASE, TOP], AMD64)]
        path = tmp_path / "vendor.tar"
        if make.startswith("docker-save"):
            docker_save(path, images, gz=make.endswith("gz"))
        else:
            oci_layout(path, images, nested=make != "oci-flat", with_platform=make != "oci-flat",
                       gz=make.endswith("gz"))
        found = take(path)
        assert (tmp_path / "out" / sha(LIB)).read_bytes() == LIB, "the top layer's library, not the base's"
        assert (tmp_path / "out" / sha(TOOL)).read_bytes() == TOOL, "a file of the gzip-compressed base layer"
        assert found["opt/vendor/lib/librknnrt.so"]["size"] == len(LIB)
        assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted([sha(LIB), sha(TOOL)]), \
            "nothing else of the image is kept"

    def test_a_file_that_is_not_the_pinned_one_is_refused_before_anything_is_read(self, tmp_path, monkeypatch):
        path = docker_save(tmp_path / "vendor.tar", [([BASE, TOP], AMD64)])
        opened = []
        monkeypatch.setattr(image_files.tarfile, "open", lambda *a, **k: opened.append(a) or None)
        with pytest.raises(image_files.ImageFilesError, match="not the pinned .*nothing in it was read"):
            take(path, pin="0" * 64)
        assert opened == []

    @pytest.mark.parametrize("make", ["docker-save", "oci"])
    def test_a_changed_layer_is_refused_naming_it(self, tmp_path, make):
        tampered = layer([("opt/vendor/lib/librknnrt.so", "file", b"a changed library\n")])
        path = tmp_path / "vendor.tar"
        if make == "docker-save":
            docker_save(path, [([BASE, TOP], AMD64)])
            data = path.read_bytes().replace(TOP, tampered.ljust(len(TOP), b"\0")[:len(TOP)])
            assert data != path.read_bytes()
            path.write_bytes(data)
        else:
            oci_layout(path, [([BASE, TOP], AMD64)])
            members = []
            with tarfile.open(path) as tar:
                for member in tar.getmembers():
                    body = tar.extractfile(member).read()
                    members.append((member.name, tampered if body == TOP else body))
            write_tar(path, members)
        with pytest.raises(image_files.ImageFilesError, match="layer 2 .*(digest|diff_id)"):
            take(path)
        assert not (tmp_path / "out" / sha(LIB)).exists()

    def test_a_layer_swapped_with_its_config_is_refused_by_the_diff_ids(self, tmp_path):
        other = layer([("opt/vendor/lib/librknnrt.so", "file", b"another library\n")])
        path = docker_save(tmp_path / "vendor.tar", [([BASE, TOP], AMD64)])
        with tarfile.open(path) as tar:
            members = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        members = [(n, other if body == TOP else body) for n, body in members]
        write_tar(path, members)
        with pytest.raises(image_files.ImageFilesError, match="not the layer its config names"):
            take(path)

    @pytest.mark.parametrize("make", ["docker-save", "oci"])
    def test_a_file_without_an_image_for_the_platform_is_refused_naming_what_it_has(self, tmp_path, make):
        path = tmp_path / "vendor.tar"
        (docker_save if make == "docker-save" else oci_layout)(path, [([BASE, TOP], "linux/arm64")])
        with pytest.raises(image_files.ImageFilesError, match="no image for linux/amd64 .*linux/arm64"):
            take(path)

    def test_the_image_of_the_platform_is_chosen_among_several(self, tmp_path):
        arm = layer([("opt/vendor/lib/librknnrt.so", "file", b"arm library\n")])
        path = oci_layout(tmp_path / "vendor.tar", [([arm], "linux/arm64"), ([BASE, TOP], AMD64)])
        take(path)
        assert (tmp_path / "out" / sha(LIB)).is_file()
        only_arm = oci_layout(tmp_path / "multi.tar", [([arm], "linux/arm64"), ([BASE, TOP], AMD64)],
                              drop={sha(BASE)})
        take(only_arm, {"/opt/vendor/lib/librknnrt.so": sha(b"arm library\n")}, "linux/arm64")

    def test_two_images_for_the_platform_need_the_config_digest(self, tmp_path):
        other = layer([("opt/vendor/lib/librknnrt.so", "file", b"older release\n")])
        path = docker_save(tmp_path / "vendor.tar", [([other], AMD64), ([BASE, TOP], AMD64)])
        with pytest.raises(image_files.ImageFilesError, match="2 images for linux/amd64.*config"):
            take(path)
        take(path, config="sha256:" + sha(config_of([BASE, TOP])))
        assert (tmp_path / "out" / sha(LIB)).is_file()
        with pytest.raises(image_files.ImageFilesError, match="no image with the config"):
            take(path, config="sha256:" + "c" * 64)

    def test_whiteouts_and_an_opaque_folder_hide_what_is_below(self, tmp_path):
        upper = layer([("opt/vendor/bin/.wh.convert", "file", b""), ("opt/vendor/lib/.wh..wh..opq", "file", b"")])
        path = oci_layout(tmp_path / "vendor.tar", [([BASE, upper], AMD64)])
        with pytest.raises(image_files.ImageFilesError) as refused:
            take(path)
        assert "/opt/vendor/bin/convert is deleted" in str(refused.value)
        assert "/opt/vendor/lib/librknnrt.so lies under opt/vendor/lib, which a layer of the image empties" \
            in str(refused.value)
        with pytest.raises(image_files.ImageFilesError, match="/opt/old/data is deleted"):
            take(docker_save(tmp_path / "save.tar", [([BASE, TOP], AMD64)]), {"/opt/old/data": sha(b"gone\n")})

    @pytest.mark.parametrize("entries,said", [
        ([("../../etc/evil", "file", b"x")], "no '..'"),
        ([("opt/vendor/lib/librknnrt.so", "symlink", "/etc/shadow")], "is a link to /etc/shadow"),
        ([("opt/vendor/lib/librknnrt.so", "symlink", "../../../../etc/shadow")], "is a link"),
        ([("opt/vendor/lib/librknnrt.so", "chr", None)], "is not a regular file"),
    ])
    def test_a_path_out_of_the_image_a_link_and_a_device_are_refused(self, tmp_path, entries, said):
        path = oci_layout(tmp_path / "vendor.tar", [([BASE, layer(entries)], AMD64)])
        with pytest.raises(image_files.ImageFilesError, match=said):
            take(path, {"/opt/vendor/lib/librknnrt.so": sha(LIB)})
        assert not list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True
        assert not (tmp_path / "etc").exists()

    def test_an_absolute_path_is_read_inside_the_image(self, tmp_path):
        absolute = layer([("/opt/vendor/lib/librknnrt.so", "file", LIB)])
        path = docker_save(tmp_path / "vendor.tar", [([BASE, absolute], AMD64)])
        take(path, {"/opt/vendor/lib/librknnrt.so": sha(LIB)})
        assert (tmp_path / "out" / sha(LIB)).read_bytes() == LIB

    def test_a_link_in_the_archive_is_followed_only_inside_it(self, tmp_path):
        # an old docker save links a layer that occurs twice to its first copy
        path = docker_save(tmp_path / "vendor.tar", [([BASE, TOP], AMD64)])
        with tarfile.open(path) as tar:
            members = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        top_name = next(n for n, body in members if body == TOP)
        linked = [(n, body) for n, body in members if n != top_name] + [("copy/layer.tar", TOP),
                                                                     (top_name, ("symlink", "../copy/layer.tar"))]
        write_tar(path, linked)
        take(path)
        assert (tmp_path / "out" / sha(LIB)).is_file()
        escaping = [(n, body) for n, body in members if n != top_name] + [(top_name, ("symlink", "../../outside"))]
        write_tar(path, escaping)
        with pytest.raises(image_files.ImageFilesError, match="links outside the archive"):
            take(path)

    def test_a_path_with_dots_in_the_archive_itself_is_refused(self, tmp_path):
        path = docker_save(tmp_path / "vendor.tar", [([BASE, TOP], AMD64)], extra=[("../escape", b"x")])
        with pytest.raises(image_files.ImageFilesError, match=r"\.\."):
            take(path)

    def test_a_file_that_is_no_image_archive_and_a_zstd_layer_are_refused(self, tmp_path):
        plain = write_tar(tmp_path / "plain.tar", [("readme.txt", b"hello")])
        with pytest.raises(image_files.ImageFilesError, match="neither a docker save archive"):
            take(plain)
        zstd = b"\x28\xb5\x2f\xfd" + b"\0" * 32
        path = oci_layout(tmp_path / "zstd.tar", [([BASE], AMD64)])
        with tarfile.open(path) as tar:
            members = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        index = json.loads(dict(members)["index.json"])
        assert index["manifests"]
        # a manifest naming a zstd layer, checked by its digest
        config = json.dumps({"os": "linux", "architecture": "amd64",
                             "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "d" * 64]}}).encode()
        manifest = json.dumps({"schemaVersion": 2, "config": {"digest": "sha256:" + sha(config)}, "layers": [
            {"mediaType": "application/vnd.oci.image.layer.v1.tar+zstd", "digest": "sha256:" + sha(zstd)}]}).encode()
        top = json.dumps({"schemaVersion": 2, "manifests": [{
            "mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": "sha256:" + sha(manifest),
            "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
        write_tar(path, [("oci-layout", b'{"imageLayoutVersion": "1.0.0"}'), ("index.json", top)]
                  + [(f"blobs/sha256/{sha(b)}", b) for b in (config, manifest, zstd)])
        with pytest.raises(image_files.ImageFilesError, match="zstd"):
            take(path, {"/x": "a" * 64})

    def test_the_limits_hold(self, tmp_path, monkeypatch):
        path = docker_save(tmp_path / "vendor.tar", [([BASE, TOP], AMD64)])
        monkeypatch.setattr(image_files, "MAX_ARCHIVE_ENTRIES", 3)
        with pytest.raises(image_files.ImageFilesError, match="more than 3 entries"):
            take(path)
        monkeypatch.setattr(image_files, "MAX_ARCHIVE_ENTRIES", 10000)
        monkeypatch.setattr(image_files, "MAX_LAYERS", 1)
        with pytest.raises(image_files.ImageFilesError, match="more than 1 layers"):
            take(path)
        monkeypatch.setattr(image_files, "MAX_LAYERS", 512)
        monkeypatch.setattr(image_files, "MAX_MANIFEST", 10)
        with pytest.raises(image_files.ImageFilesError, match="larger than"):
            take(path)

    def test_reading_stops_at_the_layer_that_decides_and_a_lower_layer_is_not_checked(self, tmp_path):
        broken = layer([("opt/vendor/bin/convert", "file", TOOL)])
        path = docker_save(tmp_path / "vendor.tar", [([broken, TOP], AMD64)])
        with tarfile.open(path) as tar:
            members = [(m.name, tar.extractfile(m).read()) for m in tar.getmembers()]
        write_tar(path, [(n, b"x" * len(body) if body == broken else body) for n, body in members])
        take(path, {"/opt/vendor/lib/librknnrt.so": sha(LIB)})
        assert (tmp_path / "out" / sha(LIB)).is_file()
        with pytest.raises(image_files.ImageFilesError, match="layer 1 "):
            take(path)


# --- the target names the file, the person gives it --------------------------------------------------------------------

def archive_image(path: Path, name: str = "rknn-sdk-docker.tar", config: str = "") -> dict:
    entry = {"archive": {"sha256": sha(path.read_bytes()), "name": name}, "platform": AMD64,
             "files": [{"path": "/opt/vendor/lib/librknnrt.so", "sha256": sha(LIB),
                        "dest": "/usr/lib/librknnrt.so"},
                       {"path": "/opt/vendor/bin/convert", "sha256": sha(TOOL), "dest": "/usr/bin/rknn-convert",
                        "executable": True, "redistributable": True}]}
    if config:
        entry["archive"]["config"] = config
    return entry


def vendor_target(tmp_path, images) -> Path:
    folder = tmp_path / "targets" / "Vendor"
    folder.mkdir(parents=True)
    (folder / "target.yml").write_text(yaml.safe_dump({"metadata": {"name": "Vendor"}, "template_variables": {
        "schema_version": 1, "formats": ["gguf"], "source_images": list(images)}}), encoding="utf-8")
    return folder


@pytest.fixture
def vendor(tmp_path):
    path = oci_layout(tmp_path / "files" / "rknn-sdk-docker.tar", [([BASE, TOP], AMD64)]) \
        if (tmp_path / "files").mkdir() is None else None
    folder = vendor_target(tmp_path, [archive_image(path)])
    return SimpleNamespace(file=path, folder=folder, state=tmp_path / "state", key=f"file:sha256:{sha(path.read_bytes())}")


class TestTheTemplateVariable:

    def test_a_source_image_is_named_by_its_registry_digest_or_by_the_files_sha256(self, tmp_path):
        from pydantic import ValidationError
        from orchestrator.Core.module_generator import source_image_problems
        from orchestrator.schemas.target_template import SourceImage
        path = docker_save(tmp_path / "vendor.tar", [([BASE, TOP], AMD64)])
        image = SourceImage.model_validate(archive_image(path, config="sha256:" + "c" * 64))
        assert image.key == f"file:sha256:{sha(path.read_bytes())}" and image.image is None
        assert image.label.startswith("the image file rknn-sdk-docker.tar (sha256 ")
        files = archive_image(path)["files"]
        for bad in ({"files": files},                                                        # neither
                    {"image": "vendor/sdk@sha256:" + "a" * 64, "archive": {"sha256": "b" * 64}, "files": files},
                    {"archive": {"sha256": "B" * 64}, "files": files},                      # not lower-case hex
                    {"archive": {"sha256": "b" * 64, "path": "/home/someone/x.tar"}, "files": files},   # no path
                    {"archive": {"sha256": "b" * 64, "name": "../x.tar"}, "files": files},
                    {"archive": {"sha256": "b" * 64, "config": "c" * 64}, "files": files}):
            with pytest.raises(ValidationError):
                SourceImage.model_validate(bad)
        values, problems = source_image_problems([archive_image(path)])
        assert problems == [] and values[0]["archive"]["sha256"] == sha(path.read_bytes())
        assert "archive" in source_image_problems("not a list")[1][0]


class TestTakingTheFilesOfAnImageFile:

    def test_the_file_is_checked_and_only_the_named_files_go_into_the_cache(self, vendor):
        from orchestrator.Core import build_preload
        said = []
        taken = build_preload.take_image_file(vendor.state, vendor.folder, vendor.file, log=said.append)
        cache = vendor.state / "build-preload" / "image-files"
        assert sorted(p.name for p in cache.iterdir()) == sorted([sha(LIB), sha(TOOL)])
        assert taken.files == 2 and taken.personal and taken.sha256 == sha(vendor.file.read_bytes())
        assert "the file itself is not kept" in taken.lines()[0] and vendor.file.is_file()
        rows = build_preload.source_image_rows(vendor.state, vendor.folder)
        assert rows[0]["kind"] == "file" and rows[0]["cached"] == rows[0]["files"] == 2

    def test_a_file_the_target_does_not_name_is_refused_before_it_is_read(self, vendor, tmp_path):
        from orchestrator.Core import build_preload
        other = docker_save(tmp_path / "other.tar", [([BASE, TOP], AMD64)])
        with pytest.raises(build_preload.PreloadProblem, match="none of the image files the target names") as refused:
            build_preload.take_image_file(vendor.state, vendor.folder, other)
        assert refused.value.code == "mismatch"
        assert not (vendor.state / "build-preload").exists()

    def test_a_file_named_by_the_target_but_tampered_inside_is_refused(self, tmp_path):
        from orchestrator.Core import build_preload
        (tmp_path / "files").mkdir()
        path = docker_save(tmp_path / "files" / "v.tar", [([BASE, TOP], "linux/arm64")])
        folder = vendor_target(tmp_path, [archive_image(path)])
        with pytest.raises(build_preload.PreloadProblem, match="no image for linux/amd64"):
            build_preload.take_image_file(tmp_path / "state", folder, path)

    def test_offline_the_plan_names_the_file_and_online_the_build_stops_with_the_step(self, vendor, tmp_path):
        from orchestrator.Core import build_preload
        from orchestrator.Core.build_image import offline_plan
        context = tmp_path / "ctx"
        assert build_preload.image_files(vendor.state, vendor.folder, context, online=False) == [vendor.key]
        with pytest.raises(build_preload.PreloadProblem, match="published only as a file") as stopped:
            build_preload.image_files(vendor.state, vendor.folder, context, online=True)
        assert stopped.value.code == "needs_person"
        assert [s.id for s in stopped.value.steps] == ["build.source_image_take"]
        found = build_preload.preload_sources(vendor.state, "Vendor", vendor.folder, "FROM scratch\n")
        assert found.images_missing == [vendor.key]
        plan = offline_plan("FROM scratch\n", None, None, preload=found)
        missing = [s for s in plan.sources if s.kind == "image_files"]
        assert missing and not missing[0].local and missing[0].ref == vendor.key
        assert "the image file rknn-sdk-docker.tar" in missing[0].label and "--image-file" in missing[0].how
        assert str(vendor.file) not in json.dumps([s.ai() for s in plan.sources]), "no path reaches an AI"
        build_preload.take_image_file(vendor.state, vendor.folder, vendor.file)
        assert build_preload.image_files(vendor.state, vendor.folder, context, online=False) == []
        staged = context / "sources" / "image-files"
        assert (staged / "usr" / "lib" / "librknnrt.so").read_bytes() == LIB
        assert (staged / "usr" / "bin" / "rknn-convert").read_bytes() == TOOL
        assert build_preload.image_files(vendor.state, vendor.folder, tmp_path / "ctx2", online=True) == []


# --- the build's preload brings the files of an image file -----------------------------------------------------------

class TestThePreloadOfAnImageFile:

    def recipe(self, vendor):
        from orchestrator.Core import build_preload
        from orchestrator.Core.build_image import ImagePlan, ImageSource
        plan = ImagePlan(sources=[ImageSource("image_files", "files taken from an image file", False, ref=vendor.key)])
        return build_preload.recipe(plan, "Vendor", vendor.folder, "FROM scratch\n", platform=AMD64)

    def pack(self, vendor, tmp_path, given):
        from orchestrator.Core import build_preload
        data = self.recipe(vendor)
        written = build_preload.write_scripts(vendor.state, data, "9.9.9")
        spec = tmp_path / "spec"
        spec.mkdir(exist_ok=True)
        (spec / "sources.json").write_bytes(build_preload.recipe_bytes(data))
        (spec / "manifest.base").write_text(f"format=1\nkind=build\nversion=9.9.9\ntarget=Vendor\n"
                                            f"platform={AMD64}\nrecipe_sha256={written.recipe_sha256}\n")
        return written, preload_lock.pack_build(spec, tmp_path / "out", given)

    def test_the_recipe_names_the_file_by_its_sha256_and_never_by_a_path(self, vendor):
        from orchestrator.Core import build_preload
        data = self.recipe(vendor)
        entry = data["source_images"][0]
        assert entry["archive"] == {"sha256": sha(vendor.file.read_bytes()), "name": "rknn-sdk-docker.tar"}
        assert "image" not in entry and str(vendor.file.parent) not in json.dumps(data)
        assert "--image-file" in build_preload.describe(data)

    @pytest.mark.skipif(image_files.host_platform() != AMD64, reason="the preload is taken in on its own platform")
    def test_the_script_takes_the_given_file_and_the_take_in_checks_the_files(self, vendor, tmp_path):
        from orchestrator.Core import build_preload
        with pytest.raises(preload_lock.PreloadError, match="--image-file FILE"):
            self.pack(vendor, tmp_path, [])
        with pytest.raises(preload_lock.PreloadError, match="other sha256"):
            self.pack(vendor, tmp_path, [docker_save(tmp_path / "other.tar", [([TOP], AMD64)])])
        _written, archive = self.pack(vendor, tmp_path, [vendor.file])
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        assert f"llmcf-preload/image-files/{sha(LIB)}" in names and not any(n.endswith(".tar") for n in names)
        taken = build_preload.take(vendor.state, "Vendor", archive, client=None,
                                   sha256=preload_lock.sha256_of(archive))
        assert taken.image_files == 2 and taken.personal
        assert build_preload.image_files(vendor.state, vendor.folder, tmp_path / "ctx", online=False) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="runs the POSIX shell script")
    def test_the_shell_script_hands_each_image_file_on(self, vendor, tmp_path):
        import os
        import shutil
        import subprocess
        if not shutil.which("sh"):
            pytest.skip("needs sh")
        from orchestrator.Core import build_preload
        written = build_preload.write_scripts(vendor.state, self.recipe(vendor), "9.9.9")
        capture = tmp_path / "args"
        fake = tmp_path / "fake-python"
        fake.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > {capture}\n')
        fake.chmod(0o755)
        result = subprocess.run(["sh", str(written.scripts[0]), "--image-file", "/data/a file.tar", "--out",
                                 str(tmp_path / "o"), "--image-file", "b.tar"], capture_output=True, text=True,
                                env=dict(os.environ, PRELOAD_PYTHON=str(fake)))
        assert result.returncode == 0, result.stderr
        args = capture.read_text().splitlines()
        assert args[-6:] == ["--out", str(tmp_path / "o"), "--image-file", "/data/a file.tar", "--image-file",
                             "b.tar"]
        refused = subprocess.run(["sh", str(written.scripts[0]), "--bogus", "x"], capture_output=True, text=True,
                                 env=dict(os.environ, PRELOAD_PYTHON=str(fake)))
        assert refused.returncode == 2 and "unknown option" in refused.stderr

    def test_the_powershell_script_hands_each_image_file_on(self, vendor):
        from orchestrator.Core import build_preload
        written = build_preload.write_scripts(vendor.state, self.recipe(vendor), "9.9.9")
        text = written.scripts[1].read_text(encoding="utf-8-sig")
        assert "[string[]]$ImageFile = @()" in text and "--out $Out @Extra" in text
        assert '$Extra += "--image-file"; $Extra += $F' in text


# --- the person's ways in: CLI and GUI ----------------------------------------------------------------------------------

def framework_for(vendor):
    engine = SimpleNamespace(resolve_target_dir=lambda target: vendor.folder if target == "Vendor" else None,
                             config=SimpleNamespace(user_state_dir=vendor.state))
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=vendor.state),
                           orchestrator=SimpleNamespace(build_engine=engine))


class TestTheWaysIn:

    def test_the_cli_lists_the_source_images_and_takes_the_file(self, vendor, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli
        from orchestrator.Core import build_image
        monkeypatch.setattr(build_image, "_engine", lambda fm: fm.orchestrator.build_engine)
        monkeypatch.setattr(cli, "_image_framework", lambda ctx: framework_for(vendor))
        runner = CliRunner()
        listed = runner.invoke(cli.cli, ["build", "source-image", "list", "--target", "Vendor"])
        assert listed.exit_code == 0, listed.output
        assert "published as a file" in listed.output and "0 of 2 file(s)" in listed.output
        assert "build source-image take FILE --target Vendor" in listed.output
        taken = runner.invoke(cli.cli, ["build", "source-image", "take", str(vendor.file), "--target", "Vendor"])
        assert taken.exit_code == 0, taken.output
        assert "2 file(s) taken" in taken.output
        listed = runner.invoke(cli.cli, ["build", "source-image", "list", "--target", "Vendor"])
        assert "2 of 2 file(s)" in listed.output
        wrong = runner.invoke(cli.cli, ["build", "source-image", "take", str(vendor.folder / "target.yml"),
                                        "--target", "Vendor"])
        assert wrong.exit_code == 1 and "nothing in it was read" in wrong.output

    def test_the_window_lists_and_takes_the_file_through_its_buttons(self, qapp, vendor, monkeypatch):
        import time
        from orchestrator.Core import build_image
        from orchestrator.gui import build_image_window
        monkeypatch.setattr(build_image, "_engine", lambda fm: fm.orchestrator.build_engine)
        monkeypatch.setattr(build_image, "target_images", lambda client, target: [])
        monkeypatch.setattr(build_image_window.BuildImageDialog, "docker_client", lambda self: None)
        monkeypatch.setattr(build_image_window.BuildImageDialog, "_targets", lambda self: ["Vendor"])
        monkeypatch.setattr(build_image_window.QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(vendor.file), "")))
        dialog = build_image_window.BuildImageDialog(framework_for(vendor), lang="en", select="Vendor")

        def wait_for(text):
            for _ in range(500):
                qapp.processEvents()
                if text in dialog.txt_result.toPlainText():
                    return
                time.sleep(0.01)
            raise AssertionError(dialog.txt_result.toPlainText())
        dialog.btn_source_list.click()
        wait_for("0 of 2 file(s)")
        assert "published as a file" in dialog.txt_result.toPlainText()
        dialog.btn_image_file.click()
        wait_for("2 file(s) taken")
        dialog.btn_source_list.click()
        wait_for("2 of 2 file(s)")
        dialog.close()

    def test_the_windows_file_buttons_open_their_dialog_when_clicked(self, qapp, vendor, monkeypatch):
        """A button's clicked signal passes `checked`; the window's slots with an optional path took it for the path
        and did nothing (Take in a preload..., Import..., Export...)."""
        from orchestrator.Core import build_image
        from orchestrator.gui import build_image_window
        monkeypatch.setattr(build_image, "target_images", lambda client, target: [])
        monkeypatch.setattr(build_image_window.BuildImageDialog, "docker_client", lambda self: None)
        monkeypatch.setattr(build_image_window.BuildImageDialog, "_targets", lambda self: ["Vendor"])
        asked = []
        monkeypatch.setattr(build_image_window.QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: asked.append(a[1]) or ("", "")))
        monkeypatch.setattr(build_image_window.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: asked.append(a[1]) or ("", "")))
        dialog = build_image_window.BuildImageDialog(framework_for(vendor), lang="en", select="Vendor")
        for button in (dialog.btn_preload_take, dialog.btn_import, dialog.btn_export, dialog.btn_image_file):
            button.click()
        assert len(asked) == 4
        dialog.close()
