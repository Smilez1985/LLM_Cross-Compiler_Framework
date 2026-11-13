"""The runtime layer's tools from the build's preload (docs/DESIGN.md, Build pipeline and target modules): a computer
that never had a network gets the sources of the way "build" through the build's preload (the builder's base image
pinned by digest, the pinned tarballs, the builder's packages), every byte checked against the pins of the installation
that takes it in, and its offline build makes the tools from them without a network."""

import gzip
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import image_files  # noqa: E402
import preload_lock  # noqa: E402

from orchestrator.Core import build_preload, runtime_layer, toolbox_preload  # noqa: E402

TARBALLS = {"util-linux": b"util-linux source", "busybox": b"busybox source", "libcap-ng": b"libcap-ng source"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def layer_blob(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


class Registry:
    """A registry API in memory (an opener for image_files.Registry): an index pinned by digest that names the same
    image manifest for linux/amd64 and linux/arm64, its config (debian trixie) and its two layers."""

    def __init__(self):
        layers = [layer_blob("usr/lib/os-release", b"ID=debian\nVERSION_CODENAME=trixie\n"),
                  layer_blob("var/lib/dpkg/status", b"Package: base-files\nStatus: install ok installed\n"
                                                    b"Version: 13.8\nArchitecture: amd64\n\n")]
        self.blobs = {"sha256:" + sha(d): d for d in layers}
        diff_ids = ["sha256:" + sha(gzip.decompress(d)) for d in layers]
        self.diff_ids = diff_ids
        config = json.dumps({"architecture": "amd64", "rootfs": {"type": "layers", "diff_ids": diff_ids}}).encode()
        self.config = "sha256:" + sha(config)
        self.blobs[self.config] = config
        manifest = json.dumps({"schemaVersion": 2, "mediaType": image_files.MANIFEST_TYPES[0],
                               "config": {"digest": self.config},
                               "layers": [{"digest": "sha256:" + sha(d)} for d in layers]}).encode()
        self.manifest = "sha256:" + sha(manifest)
        index = json.dumps({"schemaVersion": 2, "manifests": [
            {"digest": self.manifest, "platform": {"os": "linux", "architecture": arch}}
            for arch in ("amd64", "arm64")]}).encode()
        self.index = "sha256:" + sha(index)
        self.documents = {self.manifest: (manifest, image_files.MANIFEST_TYPES[0]),
                          self.index: (index, image_files.INDEX_TYPES[0])}

    def open(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        kind, reference = url.split("/")[-2:]
        if kind == "manifests":
            data, media = self.documents[reference]
            answer = io.BytesIO(data)
            answer.headers = {"Content-Type": media}
            return answer
        answer = io.BytesIO(self.blobs[reference])
        answer.headers = {}
        return answer


@pytest.fixture
def pinned(monkeypatch):
    """This installation's pins pointed at the registry in memory and at small tarballs."""
    registry = Registry()
    base = f"registry.example/library/debian:13-slim@{registry.index}"
    monkeypatch.setattr(runtime_layer, "BUILDER_BASE", base)
    monkeypatch.setattr(toolbox_preload, "BASE_NAME", "llm-framework/runtime-toolbox-base:" + registry.index[7:23])
    monkeypatch.setattr(runtime_layer, "BUILD_SOURCES", {
        name: {"url": f"https://src.example/{name}.tar", "sha256": sha(data), "file": f"{name}.tar"}
        for name, data in TARBALLS.items()})
    return registry


def save(registry, folder: Path) -> dict:
    return image_files.save_pinned(runtime_layer.BUILDER_BASE, folder, toolbox_preload.BASE_NAME,
                                   image_files.host_platform(), opener=registry)


def check(folder: Path) -> dict:
    return image_files.check_pinned(folder, runtime_layer.BUILDER_BASE, toolbox_preload.BASE_NAME,
                                    image_files.host_platform())


# --- the base image pinned by digest -----------------------------------------------------------------------------------

def test_the_pinned_base_is_saved_with_its_documents_and_checked_against_the_pin(tmp_path, pinned):
    saved = save(pinned, tmp_path / "base")
    assert saved["config"] == pinned.config
    assert sorted(p.name for p in (tmp_path / "base").iterdir()) == ["image.tar", "manifest.json", "pinned.json"]
    facts = check(tmp_path / "base")
    assert facts["config"] == pinned.config and facts["diff_ids"] == pinned.diff_ids
    with tarfile.open(tmp_path / "base" / "image.tar") as tar:
        listing = json.loads(tar.extractfile("manifest.json").read())
    assert listing[0]["RepoTags"] == [toolbox_preload.BASE_NAME], "named locally, never by the digest"


def _retar(path: Path, change) -> None:
    with tarfile.open(path) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    change(members)
    with tarfile.open(path, "w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("change, said", [
    (lambda folder: (folder / "pinned.json").write_bytes(b"{}"), "pinned digest"),
    (lambda folder: (folder / "manifest.json").write_bytes(b'{"layers": []}'), "not the one its index names"),
    (lambda folder: _retar(folder / "image.tar", lambda m: m.update({k: v + b"x" for k, v in m.items()
                                                                       if k.endswith("layer.tar")})),
     "does not have its digest"),
    (lambda folder: _retar(folder / "image.tar", lambda m: m.update({"extra": b"x"})), "exactly"),
    (lambda folder: _retar(folder / "image.tar", lambda m: m.update({"manifest.json": json.dumps(
        [dict(json.loads(m["manifest.json"])[0], RepoTags=["debian:13-slim"])]).encode()})), "listing"),
    (lambda folder: (folder / "image.tar").unlink(), "not readable"),
    (lambda folder: (folder / "pinned.json").unlink(), "missing"),
])
def test_a_changed_or_missing_part_of_the_base_is_refused(tmp_path, pinned, change, said):
    save(pinned, tmp_path / "base")
    change(tmp_path / "base")
    with pytest.raises(image_files.ImageFilesError, match=said):
        check(tmp_path / "base")


def test_a_base_of_another_pin_is_refused(tmp_path, pinned):
    save(pinned, tmp_path / "base")
    with pytest.raises(image_files.ImageFilesError, match="pinned digest"):
        image_files.check_pinned(tmp_path / "base", "registry.example/library/debian@sha256:" + "0" * 64,
                                 toolbox_preload.BASE_NAME, image_files.host_platform())
    with pytest.raises(image_files.ImageFilesError, match="not pinned by digest"):
        image_files.save_pinned("debian:13-slim", tmp_path / "other", toolbox_preload.BASE_NAME, opener=pinned)


# --- the recipe, the scripts, packing and taking in --------------------------------------------------------------------

def recipe_data(section=None, **changes):
    data = {"format": "1", "target": "Vendor", "platform": image_files.host_platform(), "images": [],
            "llama_cpp": None, "llama_cpp_bundle": False, "pip": None, "downloads": [], "source_images": [],
            "apt": None, "toolbox": section, "not_covered": []}
    data.update(changes)
    return data


def section_without_packages():
    return {"base": runtime_layer.BUILDER_BASE, "base_name": toolbox_preload.BASE_NAME,
            "sources": toolbox_preload.sources(), "apt": None}


@pytest.fixture
def packed(tmp_path, pinned, monkeypatch):
    """A build's preload of the tools' sources packed from a recipe written here (registry and downloads in memory)."""
    def fetch(url, dest, sha256="", size=0):
        data = TARBALLS[url.rsplit("/", 1)[-1][:-4]]
        if sha256 and sha(data) != sha256:
            raise preload_lock.PreloadError("not the pinned file")
        Path(dest).write_bytes(data)
    images = types.SimpleNamespace(**{k: v for k, v in vars(image_files).items() if not k.startswith("__")})
    images.save_pinned = lambda ref, folder, name, platform="": image_files.save_pinned(ref, folder, name, platform,
                                                                                         opener=pinned)
    monkeypatch.setattr(preload_lock, "_fetch", fetch)
    monkeypatch.setattr(preload_lock, "_image_files", lambda: images)
    state = tmp_path / "state"

    def make(data=None):
        data = data or recipe_data(section_without_packages())
        written = build_preload.write_scripts(state, data, "9.9.9")
        spec = tmp_path / "spec"
        shutil.rmtree(spec, ignore_errors=True)
        spec.mkdir()
        (spec / "sources.json").write_bytes(build_preload.recipe_bytes(data))
        (spec / "manifest.base").write_bytes((f"format=1\nkind=build\nversion=9.9.9\ntarget=Vendor\n"
                                              f"platform={image_files.host_platform()}\n"
                                              f"recipe_sha256={written.recipe_sha256}\n").encode())
        archive = preload_lock.pack_build(spec, tmp_path / "out")
        return SimpleNamespace(state=state, archive=archive, written=written)
    return make


def test_the_scripts_say_they_fetch_the_tools_sources_and_carry_the_programs(tmp_path, pinned):
    written = build_preload.write_scripts(tmp_path / "state", recipe_data(section_without_packages()), "9.9.9")
    assert "the runtime layer's tools' sources" in written.what and "busybox.tar" in written.what
    sh, ps = written.scripts
    text = sh.read_text(encoding="utf-8")
    assert "def save_pinned" in text and "def _gather_toolbox" in text and '"toolbox": {' in text
    body = Path(ps).read_text(encoding="utf-8")
    assert '+ "`n", $Utf8' in body, "the here-string's last line break is written back"
    part = re.search(r'Write-Part \(Join-Path \$Spec "sources.json"\) @\'\r?\n(.*?)\r?\n\'@', body, re.S).group(1)
    assert (part.replace("\r\n", "\n") + "\n").encode("utf-8") == \
        build_preload.recipe_bytes(recipe_data(section_without_packages()))


def test_a_packed_preload_of_the_tools_is_checked_and_kept(packed):
    made = packed()
    with tarfile.open(made.archive) as tar:
        names = tar.getnames()
    assert "llmcf-preload/toolbox/base/image.tar" in names and "llmcf-preload/toolbox/sources/busybox.tar" in names
    taken = build_preload.take(made.state, "Vendor", made.archive, client=None,
                               sha256=preload_lock.sha256_of(made.archive))
    assert taken.toolbox and any("runtime layer's tools' sources kept" in line for line in taken.lines())
    folder = toolbox_preload.kept(made.state)
    assert folder == toolbox_preload.store(made.state)
    assert (folder / "sources" / "busybox.tar").read_bytes() == TARBALLS["busybox"]
    assert json.loads((folder / "kept.json").read_text())["pins"] == toolbox_preload.pins_key()


def test_kept_sources_of_other_pins_are_not_used(packed, monkeypatch):
    made = packed()
    build_preload.take(made.state, "Vendor", made.archive, client=None)
    assert toolbox_preload.kept(made.state) is not None
    sources = dict(runtime_layer.BUILD_SOURCES)
    sources["busybox"] = dict(sources["busybox"], sha256="0" * 64)
    monkeypatch.setattr(runtime_layer, "BUILD_SOURCES", sources)
    assert toolbox_preload.kept(made.state) is None, "a framework update that changed a pin asks for them again"


def repacked(archive: Path, out: Path, change) -> Path:
    """The archive unpacked, changed, its SHA256SUMS made to match again and packed: only the pins can tell."""
    work = out.with_suffix(".work")
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    top = work / "llmcf-preload"
    change(top)
    files = sorted(p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (top / "SHA256SUMS").write_bytes("".join(f"{preload_lock.sha256_of(top / f)}  {f}\n" for f in files).encode())
    with tarfile.open(out, "w:gz") as tar:
        tar.add(top, arcname="llmcf-preload")
    return out


@pytest.mark.parametrize("change, said", [
    (lambda top: (top / "toolbox" / "sources" / "busybox.tar").write_bytes(b"tampered"), "not the pinned file"),
    (lambda top: (top / "toolbox" / "sources" / "busybox.tar").unlink(), "missing or not the pinned file"),
    (lambda top: (top / "toolbox" / "sources" / "extra.tar").write_bytes(b"x"), "does not name"),
    (lambda top: (top / "toolbox" / "base" / "pinned.json").write_bytes(b"{}"), "base image in the preload"),
    (lambda top: _retar(top / "toolbox" / "base" / "image.tar",
                        lambda m: m.update({k: b"x" for k in m if k.endswith(".json") and k != "manifest.json"})),
     "base image in the preload"),
    (lambda top: shutil.rmtree(top / "toolbox"), "lacks the runtime layer's tools' sources"),
])
def test_a_tampered_or_missing_part_is_refused_and_nothing_is_kept(tmp_path, packed, change, said):
    made = packed()
    bad = repacked(made.archive, tmp_path / "bad.tar.gz", change)
    with pytest.raises(build_preload.PreloadProblem, match=said):
        build_preload.take(made.state, "Vendor", bad, client=None)
    assert toolbox_preload.kept(made.state) is None
    assert not toolbox_preload.store(made.state).exists()


def test_a_recipe_naming_other_sources_than_this_installation_pins_is_refused(packed):
    section = dict(section_without_packages(), sources=[dict(s, url=s["url"].replace("src.example", "other.example"))
                                                        for s in toolbox_preload.sources()])
    made = packed(recipe_data(section))
    with pytest.raises(build_preload.PreloadProblem, match="other sources"):
        build_preload.take(made.state, "Vendor", made.archive, client=None)


def test_a_toolbox_folder_the_recipe_does_not_name_is_refused(tmp_path):
    top = tmp_path / "top"
    assert toolbox_preload.check(top, {}, None) is None
    (top / "toolbox").mkdir(parents=True)
    with pytest.raises(build_preload.PreloadProblem, match="does not name"):
        toolbox_preload.check(top, {}, None)


def test_the_builders_packages_are_checked_for_the_release_of_the_checked_base(tmp_path, pinned, monkeypatch):
    """The archive's manifest names a release; the packages are checked for the one the checked base image is."""
    folder = tmp_path / "top" / "toolbox"
    save(pinned, folder / "base")
    (folder / "sources").mkdir()
    for pin in toolbox_preload.sources():
        (folder / "sources" / pin["file"]).write_bytes(TARBALLS[pin["file"][:-4]])
    (folder / "packages").mkdir()
    seen = {}

    def check_packages(top, manifest, apt):
        seen.update(top=top, manifest=manifest, apt=apt)
        return ["gcc=14"], "debian trixie amd64", False
    monkeypatch.setattr(build_preload, "_check_packages", check_packages)
    apt = {"distribution": None, "release": None, "arch": "amd64", "key": "k", "stages": []}
    checked = toolbox_preload.check(tmp_path / "top", {"toolbox_packages_release": "bookworm", "packages_arch": "x"},
                                    dict(section_without_packages(), apt=apt))
    assert checked.packages == ["gcc=14"]
    assert seen["top"] == folder and seen["apt"]["release"] == "trixie" and seen["apt"]["distribution"] == "debian"
    assert seen["manifest"] == {"packages_release": "bookworm"}, "only the archive's lines of the builder's packages"


def test_the_recipe_carries_the_section_only_when_asked(pinned, monkeypatch, tmp_path):
    monkeypatch.setattr(build_preload, "_apt_section", lambda text, platform, client, fetched: (
        {"arch": "amd64", "stages": [{"image": fetched[0], "packages": ["gcc"]}]}, ""))
    plan = SimpleNamespace(sources=[])
    target = tmp_path / "target"
    target.mkdir()
    (target / "target.yml").write_text("name: Vendor\n")
    without = build_preload.recipe(plan, "Vendor", target, "FROM debian\n")
    assert without["toolbox"] is None and not build_preload.fetches(without)
    data = build_preload.recipe(plan, "Vendor", target, "FROM debian\n", toolbox=True)
    assert data["toolbox"]["base"] == runtime_layer.BUILDER_BASE and build_preload.fetches(data)
    assert [s["file"] for s in data["toolbox"]["sources"]] == ["busybox.tar", "libcap-ng.tar", "util-linux.tar"]
    monkeypatch.setattr(build_preload, "_apt_section", lambda *a: (None, "cannot be read"))
    refused = build_preload.recipe(plan, "Vendor", target, "FROM debian\n", toolbox=True)
    assert refused["toolbox"] is None and refused["not_covered"] == ["the runtime layer's tools: cannot be read"]


def test_tools_or_kept_sources_make_the_preload_unneeded(tmp_path, packed):
    cache = tmp_path / "cache"
    assert toolbox_preload.missing(cache, tmp_path / "nothing")
    made = packed()
    build_preload.take(made.state, "Vendor", made.archive, client=None)
    assert not toolbox_preload.missing(cache, made.state)
    assert not toolbox_preload.missing(None, None), "cannot be told: the build's own check says it"


# --- the builder's Dockerfile and the offline build ----------------------------------------------------------------

def test_the_builders_dockerfile_takes_the_pinned_base_and_the_preloads_packages():
    from orchestrator.Core.image_packages import need
    text = runtime_layer.BUILDER_DOCKERFILE.read_text(encoding="utf-8")
    assert f"ARG BUILDER_BASE={runtime_layer.BUILDER_BASE}\nFROM ${{BUILDER_BASE}}\n" in text
    found, problems = need(text, "amd64")
    assert not problems and found[0]["image"] == runtime_layer.BUILDER_BASE and "musl-tools" in found[0]["packages"]
    assert text.index("llmcf-apt.sh use") < text.index("apt-get update") < text.index("llmcf-apt.sh restore")
    assert "ARG APT_PRELOAD" in text and "COPY sources/apt/ /tmp/llmcf-apt/" in text


def test_the_online_builder_context_has_the_empty_repository_folder(tmp_path, pinned):
    context = runtime_layer.builder_context(tmp_path, lambda url: TARBALLS[url.rsplit("/", 1)[-1][:-4]])
    assert (context / "sources" / "apt").is_dir() and not list((context / "sources" / "apt").iterdir())


@pytest.mark.real_toolbox
def test_offline_the_tools_are_made_the_way_build_from_the_preload(tmp_path):
    from test_runtime_layer import _stub_build
    calls = []
    kept = tmp_path / "kept"
    kept.mkdir()

    def build(work, setpriv="util-linux", preload=None):
        calls.append(preload)
        return _stub_build([])(work)
    record = runtime_layer.ensure_toolbox(tmp_path / "cache", "build-rust", offline=True, build=build, preload=kept)
    assert calls == [kept] and record["way"] == "build" and record["setpriv"] == "util-linux"
    assert record["sources"]["busybox"]["from"] == "the build's preload"
    assert runtime_layer.ensure_toolbox(tmp_path / "cache", "build", offline=True) == record, "kept, made once"


@pytest.mark.real_toolbox
def test_offline_without_the_preloads_sources_the_refusal_names_the_preload(tmp_path):
    with pytest.raises(runtime_layer.ToolboxMissing) as refused:
        runtime_layer.ensure_toolbox(tmp_path, "build", offline=True, build=lambda work, **_kw: {})
    assert "build's preload" in str(refused.value) and "offered when an offline build starts" in str(refused.value)


class Images:
    """The container engine's images: the base after a load, and the build's arguments."""

    def __init__(self, archive_layers):
        self.layers, self.archive_layers, self.loaded, self.built = None, archive_layers, 0, []

    def get(self, name):
        if self.layers is None:
            raise LookupError(name)
        return SimpleNamespace(attrs={"RootFS": {"Layers": self.layers}})

    def load(self, handle):
        handle.read()
        self.loaded += 1
        self.layers = self.archive_layers

    def build(self, path, tag, **kwargs):
        self.built.append((sorted(p.name for p in Path(path).iterdir()), kwargs))
        return SimpleNamespace(id=tag), []


def test_the_offline_builder_loads_the_checked_base_and_builds_without_a_network(tmp_path, packed, monkeypatch):
    from test_runtime_layer import _FakeContainer
    from orchestrator.utils import docker_engine
    made = packed()
    build_preload.take(made.state, "Vendor", made.archive, client=None)
    folder = toolbox_preload.kept(made.state)
    images = Images(Registry().diff_ids)
    client = SimpleNamespace(images=images)
    monkeypatch.setattr(docker_engine, "create_container", lambda _c, image, **_kw: _FakeContainer(image, []))
    work = tmp_path / "work"
    work.mkdir()
    files = runtime_layer.engine_builder(client, fetch=lambda url: pytest.fail("nothing is fetched"),
                                         prepare=toolbox_preload.prepare)(work, preload=folder)
    assert images.loaded == 1 and set(files) == {"busybox", "setpriv"}
    names, kwargs = images.built[0]
    assert names == ["Dockerfile", "busybox.tar", "libcap-ng.tar", "sources", "util-linux.tar"]
    assert kwargs["network_mode"] == "none" and kwargs["pull"] is False
    assert kwargs["buildargs"] == {"BUILDER_BASE": toolbox_preload.BASE_NAME, "APT_PRELOAD": "1"}
    toolbox_preload.ready_base(client, folder)
    assert images.loaded == 1, "a base with exactly the checked layers is not loaded again"


def test_a_loaded_base_with_other_layers_and_a_changed_kept_tarball_are_refused(tmp_path, packed):
    made = packed()
    build_preload.take(made.state, "Vendor", made.archive, client=None)
    folder = toolbox_preload.kept(made.state)
    client = SimpleNamespace(images=Images(["sha256:" + "0" * 64]))
    with pytest.raises(ValueError, match="layers"):
        toolbox_preload.ready_base(client, folder)
    (folder / "sources" / "busybox.tar").write_bytes(b"changed")
    with pytest.raises(ValueError, match="not the pinned file"):
        toolbox_preload.context(tmp_path / "work", folder)


@pytest.mark.parametrize("image_here, ready, tools_missing, want", [
    (True, True, True, "empty"),      # the image is here, only the tools are missing: the preload is offered
    (True, True, False, None),
    (False, True, True, "plan"),
    (False, True, False, None),
    (False, False, False, "plan"),
])
def test_the_preload_is_offered_when_only_the_tools_are_missing(monkeypatch, image_here, ready, tools_missing, want):
    from orchestrator.Core import build_image
    from orchestrator.Core.builder import INPUTS_LABEL
    engine = SimpleNamespace(config={"offline_mode": True})
    plan = build_image.ImagePlan(sources=[build_image.ImageSource("apt", "apt packages", ready, "")])
    monkeypatch.setattr(build_image, "_engine", lambda fm: engine)
    monkeypatch.setattr(build_image, "docker_client", lambda fm, engine=None: None)
    monkeypatch.setattr(build_image, "expected_labels", lambda fm, target, gpu=False: {INPUTS_LABEL: "in"})
    monkeypatch.setattr(build_image, "target_images", lambda client, target=None: [
        SimpleNamespace(inputs="in")] if image_here else [])
    monkeypatch.setattr(build_image, "target_plan", lambda fm, target, gpu=False: plan)
    monkeypatch.setattr(toolbox_preload, "missing_for", lambda fm: tools_missing)
    found = build_preload.needed(SimpleNamespace(config={"offline_mode": True}), "Vendor")
    if want is None:
        assert found is None
    elif want == "empty":
        assert found is not None and found.sources == [] and found.ready
    else:
        assert found is plan
