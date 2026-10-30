"""Offline builds of the build container image and its air-gapped transfer (DECISIONS round 36).

The build container image is the Docker image with a target's toolchain (Core/builder.py, IMAGE_LABEL and
INPUTS_LABEL); the target artifact, package or bundle is what goes to the board. Offline mode means no internet, the
own network may be there: offline mode may build the image when every source its Dockerfile needs is local by the one
locality rule (Core/build_image.offline_plan over utils/locality.py), otherwise the build stops before anything slow
and names each source that is not local. A build container image moves between offline computers as a file with its
sha256 (export_image / import_image), person-only.

Every address here is a documentation address (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) or a
*.test / example.com name with a stand-in resolver; 192.0.2.0/24 counts as local only through the person's opt-out
(offline_local_ranges), which is how a test network becomes "the own network" here. No socket is opened.
"""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import build_image
from orchestrator.Core.builder import (IMAGE_LABEL, INPUTS_LABEL, BuildConfiguration, BuildEngine, BuildProgress,
                                       BuildStatus, ModelFormat, OfflineImageMissing)
from orchestrator.Core.config_manager import ConfigManager

LAN = "192.0.2.0/24"          # counted as the own network by the person's opt-out
APT = "http://192.0.2.10:3142"
PIP = "http://192.0.2.11:3141/simple"
PUBLIC = "http://198.51.100.7:3142"   # a documentation address on the internet: never local

DOCKERFILE = """\
FROM debian:bookworm-slim AS builder
ARG APT_MIRROR
ARG APT_PROXY
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG LLAMA_CPP_SOURCE=git
ARG LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git
ARG TORCH_PIP_URL=https://download.pytorch.org/whl/cpu
RUN apt-get update && apt-get install -y --no-install-recommends git
RUN python3 -m pip install --index-url "${PIP_INDEX_URL:-https://download.pytorch.org/whl/cpu}" torch
FROM debian:bookworm-slim
"""


@pytest.fixture
def names(monkeypatch):
    """The system resolver's answers (no lookup reaches the network)."""
    from orchestrator.utils import locality
    answers = {"mirror.test": ["192.0.2.20"], "registry.test": ["192.0.2.21"], "cdn.example.com": ["203.0.113.9"],
               "registry-1.docker.io": ["198.51.100.31"]}
    locality.clear_cache()
    monkeypatch.setattr(locality, "_system_lookup", lambda host: answers.get(host) or (_ for _ in ()).throw(
        OSError(f"unknown host {host}")))
    yield answers
    locality.clear_cache()


def settings(**values):
    base = {"offline_mode": True, "offline_local_ranges": [LAN]}
    base.update(values)
    return base


class Images:
    def __init__(self, here=(), built=()):
        self.here, self.built, self.loaded = set(here), list(built), []

    def get(self, reference):
        if reference in self.here or reference.startswith("llm-framework/") or reference.startswith("sha256:"):
            return SimpleNamespace(id="sha256:" + "c" * 64, tags=[reference], attrs={"Size": 1000},
                                   save=lambda named=True: [b"layer-1", b"layer-2"])
        raise LookupError(f"No such image: {reference}")

    def list(self, filters=None):
        wanted = [f.split("=", 1) for f in (filters or {}).get("label", [])]
        return [i for i in self.built if all((i.labels.get(k[0]) == k[1]) if len(k) == 2 else k[0] in i.labels
                                             for k in wanted)]

    def load(self, data):
        self.loaded.append(data.read())
        return [SimpleNamespace(tags=["llm-framework/rockchip:last-build"])]


class Docker:
    def __init__(self, here=(), mirrors=(), built=()):
        self.images = Images(here, built)
        self.mirrors = list(mirrors)
        self.api = SimpleNamespace(build=lambda **kw: pytest.fail("the legacy builder is not expected here"))

    def info(self):
        return {"RegistryConfig": {"Mirrors": self.mirrors}}

    def version(self):
        return {"Components": [{"Name": "Engine"}]}


def no_copy(_state):
    return None


def plan_of(text=DOCKERFILE, config=None, client=None, **kw):
    kw.setdefault("copy_finder", no_copy)
    return build_image.offline_plan(text, config if config is not None else settings(),
                                    client if client is not None else Docker(here={"debian:bookworm-slim"}), **kw)


def source(plan, kind):
    return next(s for s in plan.sources if s.kind == kind)


# --- the plan: one source at a time -----------------------------------------------------------------------------------

class TestThePlan:

    def test_without_mirrors_apt_pip_and_llama_cpp_are_named_as_not_local(self, names):
        plan = plan_of()
        assert not plan.ready
        assert {s.kind for s in plan.missing} == {"llama_cpp", "apt", "pip"}
        assert source(plan, "base_image").local, "debian:bookworm-slim is on this computer"
        assert "build_apt_mirror" in source(plan, "apt").how and "build_pip_index" in source(plan, "pip").how

    def test_every_source_in_the_own_network_makes_the_plan_ready(self, names):
        plan = plan_of(config=settings(build_apt_proxy=APT, build_pip_index=PIP),
                       llama_repo="https://mirror.test/llama.cpp.git")
        assert plan.ready, plan.lines()
        assert plan.build_args == {"APT_PROXY": APT, "PIP_INDEX_URL": PIP, "PIP_TRUSTED_HOST": "192.0.2.11:3141"}
        assert plan.hosts == ["mirror.test:192.0.2.20"], "the mirror's name is pinned to its checked address"
        assert not [s for s in plan.sources if s.kind == "download"], \
            "the pip index replaces the _PIP_URL default and the ${PIP_INDEX_URL:-...} fallback"

    def test_a_mirror_on_the_internet_is_not_local_and_its_address_never_reaches_the_ai(self, names):
        plan = plan_of(config=settings(build_apt_mirror=PUBLIC, build_pip_index=PIP),
                       llama_repo="https://mirror.test/llama.cpp.git")
        apt = source(plan, "apt")
        assert not apt.local and "is not local" in apt.how and "198.51.100.7" in apt.detail
        shown = json.dumps(plan.ai())
        assert "198.51.100.7" not in shown and "192.0.2." not in shown and "mirror.test" not in shown
        assert "198.51.100.7" not in build_image.stop_text("Rockchip", plan)

    def test_a_mirror_with_credentials_or_a_query_is_refused(self, names):
        for bad in ("http://user:pw@192.0.2.10:3142", "http://192.0.2.10/simple?token=x", "ftp://192.0.2.10/"):
            assert build_image.mirror_problem(bad), bad
            plan = plan_of(config=settings(build_apt_mirror=bad))
            assert not source(plan, "apt").local
        assert build_image.mirror_problem(APT) is None and build_image.mirror_problem("http://[2001:db8::5]:3142/") \
            is None

    def test_a_dockerfile_that_takes_no_mirror_argument_is_named(self, names):
        text = "FROM debian:bookworm-slim\nRUN apt-get update\n"
        apt = source(plan_of(text, config=settings(build_apt_mirror=APT)), "apt")
        assert not apt.local and "ARG APT_MIRROR" in apt.how

    def test_a_base_image_from_a_registry_in_the_own_network_or_a_local_daemon_mirror(self, names):
        text = "FROM registry.test:5000/acme/base:1\nFROM debian:bookworm-slim\n"
        plan = plan_of(text, client=Docker(mirrors=["http://192.0.2.30:5000"]))
        assert [s.local for s in plan.sources] == [True, True]
        assert "registry mirror" in plan.sources[1].how

    def test_a_missing_hub_image_without_a_local_mirror_and_a_daemon_mirror_on_the_internet(self, names):
        plan = plan_of("FROM debian:bookworm-slim\n", client=Docker(mirrors=["https://198.51.100.40"]))
        kinds = [(s.kind, s.local) for s in plan.sources]
        assert kinds == [("base_image", False), ("registry_mirror", False)]
        assert "198.51.100.40" not in json.dumps(plan.ai())

    def test_other_downloads_are_judged_by_their_host(self, names):
        text = ("FROM scratch\nRUN curl -fsSL https://cdn.example.com/tool.tar.gz -o /t\n"
                "RUN curl -fsSL http://192.0.2.40/vendor.deb -o /v\n")
        plan = plan_of(text)
        assert [(s.label, s.local) for s in plan.sources] == [("downloads from cdn.example.com", False),
                                                              ("downloads from 192.0.2.40", True)]

    def test_the_local_copy_of_llama_cpp_is_used_when_the_dockerfile_takes_it(self, names, tmp_path):
        copy = tmp_path / "llama.cpp"
        plan = plan_of(config=settings(build_apt_proxy=APT, build_pip_index=PIP), state_dir=tmp_path,
                       copy_finder=lambda state: copy)
        assert plan.ready and plan.llama_copy == copy and plan.build_args["LLAMA_CPP_SOURCE"] == "context"
        without = plan_of(DOCKERFILE.replace("ARG LLAMA_CPP_SOURCE=git\n", ""), state_dir=tmp_path,
                          copy_finder=lambda state: copy)
        assert not source(without, "llama_cpp").local and "LLAMA_CPP_SOURCE" in source(without, "llama_cpp").how

    def test_the_plan_opens_no_socket(self, monkeypatch):
        """Only documentation addresses that need no lookup: nothing is resolved and nothing is connected."""
        from test_hf_online import guard_sockets
        from orchestrator.utils import locality
        locality.clear_cache()
        tried = guard_sockets(monkeypatch)
        plan = plan_of(config=settings(build_apt_proxy=APT, build_pip_index=PIP),
                       llama_repo="https://192.0.2.20/llama.cpp.git")
        assert plan.ready and tried == []

    def test_a_shipped_target_dockerfile_takes_every_source_argument(self):
        """The shipped Dockerfiles carry the arguments the plan passes (and the local llama.cpp copy)."""
        root = Path(__file__).resolve().parents[1] / "targets"
        for name in ("Rockchip/dockerfile", "_template/Dockerfile", "_template/Dockerfile.gpu"):
            text = (root / name).read_text(encoding="utf-8")
            for arg in ("APT_MIRROR", "APT_PROXY", "PIP_INDEX_URL", "PIP_TRUSTED_HOST", "LLAMA_CPP_SOURCE"):
                assert f"ARG {arg}" in text, (name, arg)
            assert "COPY sources/" in text, name


# --- the image build in offline mode ----------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    engine = BuildEngine(ConfigManager(tmp_path / "configs"))
    monkeypatch.setattr(engine, "_build_host_profile", lambda progress: (None, "no probe in the test"))
    return engine


def config(**overrides):
    values = dict(build_id="b1", timestamp="t", model_source="acme/tiny-model", target_arch="Rockchip",
                  target_format=ModelFormat.GGUF, output_dir="out", quantization="Q4_K_M", target_board="rk3566",
                  target_pack_sha256="f" * 64)
    values.update(overrides)
    return BuildConfiguration(**values)


def dockerfile(tmp_path, text=DOCKERFILE):
    path = tmp_path / "ctx" / "Dockerfile"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def build(engine, monkeypatch, tmp_path, docker, driver=lambda: "docker", **settings_values):
    seen = {}
    monkeypatch.setattr(engine, "_buildx_driver", driver, raising=False)   # the buildx builder of the Docker daemon

    def buildkit(tag, path, args, progress, labels=None, hosts=None):
        seen.update(args=dict(args), labels=dict(labels or {}), hosts=hosts)
    monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
    monkeypatch.setattr(engine, "_build_with_buildkit", buildkit)
    monkeypatch.setattr(engine, "docker_client", docker)
    for key, value in settings_values.items():
        engine.config.set(key, value)
    progress = BuildProgress("b1", BuildStatus.QUEUED, "")
    engine._build_docker_image(config(), progress, dockerfile(tmp_path))
    return seen, progress


class TestTheOfflineImageBuild:

    def test_offline_mode_builds_the_image_when_every_source_is_local(self, engine, monkeypatch, tmp_path, names):
        """Regression (round 36): offline mode refused every image build (OfflineImageMissing) although every source
        was in the own network."""
        from test_hf_online import guard_sockets
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        repos = {"llama.cpp": {"url": "https://192.0.2.20/llama.cpp.git"}}
        engine.config.set("source_repositories", repos)
        tried = guard_sockets(monkeypatch)
        seen, progress = build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}),
                               offline_mode=True, offline_local_ranges=[LAN], build_apt_proxy=APT, build_pip_index=PIP)
        assert seen["args"]["APT_PROXY"] == APT and seen["args"]["PIP_INDEX_URL"] == PIP
        assert seen["args"]["HTTPS_PROXY"] == "" and seen["args"]["NO_PROXY"] == "*", "no proxy offline"
        assert seen["args"]["LLAMA_CPP_REPO"] == "https://192.0.2.20/llama.cpp.git"
        assert any("from local sources only" in line for line in progress.logs)
        assert tried == [], "no socket: the documentation addresses need no lookup"

    def test_the_source_arguments_are_no_inputs_of_the_image(self, engine, monkeypatch, tmp_path, names):
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        engine.config.set("source_repositories", {"llama.cpp": {"url": "https://192.0.2.20/llama.cpp.git"}})
        offline, _ = build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}),
                           offline_mode=True, offline_local_ranges=[LAN], build_apt_proxy=APT, build_pip_index=PIP)
        online, _ = build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}), offline_mode=False)
        assert offline["labels"][INPUTS_LABEL] == online["labels"][INPUTS_LABEL], \
            "an image built offline through the mirrors is the one an online build would make, and the other way round"
        assert "APT_PROXY" not in online["args"] and "HTTPS_PROXY" not in online["args"], "online: as before"

    def test_a_source_that_is_not_local_stops_before_anything_is_built(self, engine, monkeypatch, tmp_path, names):
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        with pytest.raises(OfflineImageMissing) as stopped:
            build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}), offline_mode=True,
                  offline_local_ranges=[LAN], build_apt_mirror=PUBLIC)
        text = str(stopped.value)
        assert "(1) llama.cpp source" in text and "apt packages: the address you set" in text \
            and "pip packages" in text
        assert "198.51.100.7" not in text, "labels only: the person's mirror address is not in the build's error"
        assert "Start this build once with offline mode off" in text and "debian:bookworm-slim" in text

    def test_the_local_llama_cpp_copy_is_staged_into_the_build_context(self, engine, monkeypatch, tmp_path, names):
        copy = tmp_path / "store" / "llama.cpp"
        (copy / ".git").mkdir(parents=True)
        (copy / "CMakeLists.txt").write_text("project(llama)\n")
        (copy / "CMakeLists.txt").chmod(0o444)
        monkeypatch.setattr(build_image, "local_llama_copy", lambda state: copy)
        seen, _ = build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}), offline_mode=True,
                        offline_local_ranges=[LAN], build_apt_proxy=APT, build_pip_index=PIP)
        staged = tmp_path / "ctx" / "sources" / "llama.cpp"
        assert (staged / "CMakeLists.txt").read_text() == "project(llama)\n" and (staged / ".git").is_dir()
        assert seen["args"]["LLAMA_CPP_SOURCE"] == "context"
        (staged / "CMakeLists.txt").unlink()   # writable: the build folder can be cleaned

    def test_the_image_built_earlier_is_still_used_first(self, engine, monkeypatch, tmp_path, names):
        online, _ = build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}), offline_mode=False)
        earlier = SimpleNamespace(labels=dict(online["labels"]), attrs={"Created": "2026"}, short_id="i1",
                                  tag=lambda *a: None)
        docker = Docker(built=[earlier])
        monkeypatch.setattr(engine, "_build_with_buildkit", lambda *a, **k: pytest.fail("no build"))
        monkeypatch.setattr(engine, "docker_client", docker)
        engine.config.set("offline_mode", True)
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._build_docker_image(config(), progress, dockerfile(tmp_path))
        assert any("built earlier from the same settings" in line for line in progress.logs)


def test_the_build_context_always_has_a_sources_folder(tmp_path):
    from orchestrator.utils.build_context import stage_build_context
    target = tmp_path / "t"
    target.mkdir()
    (target / "Dockerfile").write_text("FROM scratch\n")
    app = tmp_path / "app"
    app.mkdir()
    (app / "entrypoint.sh").write_text("#!/bin/sh\n")
    path = stage_build_context(target, app, tmp_path / "ctx")
    assert (path.parent / "sources").is_dir(), "the Dockerfiles COPY sources/ also when it holds no local copy"


# --- export and import ----------------------------------------------------------------------------------------------

def archive(path: Path, labels) -> Path:
    """A docker save archive of one image with `labels` (manifest.json and its config)."""
    config = json.dumps({"config": {"Labels": labels}}).encode()
    manifest = json.dumps([{"Config": "abc.json", "RepoTags": ["llm-framework/rockchip:last-build"],
                            "Layers": []}]).encode()
    with tarfile.open(path, "w") as tar:
        for name, data in (("manifest.json", manifest), ("abc.json", config)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


GOOD = {IMAGE_LABEL: "Rockchip", INPUTS_LABEL: "a" * 64}


class TestTransfer:

    def test_export_writes_the_file_and_its_sha256_sidecar(self, tmp_path):
        image = SimpleNamespace(labels=dict(GOOD), attrs={"Created": "2026", "Size": 10}, id="sha256:" + "c" * 64,
                                tags=["llm-framework/rockchip:last-build"])
        docker = Docker(built=[image])
        result = build_image.export_image(None, "Rockchip", tmp_path / "rk.tar", actor="cli", client=docker)
        data = (tmp_path / "rk.tar").read_bytes()
        assert data == b"layer-1layer-2" and result.sha256 == hashlib.sha256(data).hexdigest()
        assert (tmp_path / "rk.tar.sha256").read_text() == f"{result.sha256}  rk.tar\n"
        assert build_image.read_sidecar(tmp_path / "rk.tar") == result.sha256
        with pytest.raises(build_image.ImageTransferError) as again:
            build_image.export_image(None, "Rockchip", tmp_path / "rk.tar", actor="cli", client=docker)
        assert again.value.code == "invalid_value", "an existing file is never replaced"

    def test_export_and_import_are_the_persons(self, tmp_path):
        for call in (lambda: build_image.export_image(None, "Rockchip", tmp_path / "x.tar", actor="mcp"),
                     lambda: build_image.import_image(None, tmp_path / "x.tar", actor="ditto")):
            with pytest.raises(build_image.ImageTransferError) as refused:
                call()
            assert refused.value.code == "needs_person"

    def test_export_follows_the_disk_rule(self, tmp_path, monkeypatch):
        from orchestrator.Core import disk_plan
        image = SimpleNamespace(labels=dict(GOOD), attrs={"Created": "2026", "Size": 50 * 1024 ** 3},
                                id="sha256:" + "c" * 64, tags=["t"])
        monkeypatch.setattr(disk_plan, "free_space",
                            lambda path, du=None: SimpleNamespace(free=10 * 1024 ** 3, total=100 * 1024 ** 3))
        with pytest.raises(build_image.ImageTransferError) as refused:
            build_image.export_image(None, "Rockchip", tmp_path / "rk.tar", actor="gui", client=Docker(built=[image]))
        assert refused.value.code == "insufficient_resources" and not (tmp_path / "rk.tar").exists()

    def test_import_checks_the_digest_before_loading(self, tmp_path):
        file = archive(tmp_path / "rk.tar", GOOD)
        docker = Docker()
        with pytest.raises(build_image.ImageTransferError) as refused:
            build_image.import_image(None, file, actor="cli", sha256="0" * 64, client=docker, expected=GOOD)
        assert refused.value.code == "mismatch" and docker.images.loaded == []
        with pytest.raises(build_image.ImageTransferError) as none:
            build_image.import_image(None, file, actor="cli", client=docker, expected=GOOD)
        assert none.value.code == "missing_information", "no sha256 and no sidecar"
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        (tmp_path / "rk.tar.sha256").write_text(f"{digest}  rk.tar\n")
        result = build_image.import_image(None, file, actor="gui", client=docker, expected=GOOD)
        assert docker.images.loaded == [file.read_bytes()] and result.target == "Rockchip"

    def test_import_refuses_an_image_a_build_here_would_not_use(self, tmp_path):
        docker = Docker()
        for labels, expected in (({}, GOOD), ({IMAGE_LABEL: "Rockchip", INPUTS_LABEL: "b" * 64}, GOOD)):
            file = archive(tmp_path / f"i{len(labels)}.tar", labels)
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            with pytest.raises(build_image.ImageTransferError) as refused:
                build_image.import_image(None, file, actor="cli", sha256=digest, client=docker, expected=expected)
            assert refused.value.code == "mismatch"
        file = archive(tmp_path / "other.tar", GOOD)
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        with pytest.raises(build_image.ImageTransferError, match="not Hailo"):
            build_image.import_image(None, file, actor="cli", sha256=digest, target="Hailo", client=docker,
                                     expected=GOOD)
        assert docker.images.loaded == []

    def test_the_package_export_and_the_scan_share_the_one_docker_save(self):
        """One helper, not copies: the deployment manager's package export, the security scan's archive and the image
        export all write docker save through utils/docker_engine.save_image."""
        import inspect
        from orchestrator.Core import builder, deployment_manager
        assert "save_image(" in inspect.getsource(deployment_manager.DeploymentManager._export_docker_images)
        assert "save_image(" in inspect.getsource(builder.BuildEngine._save_image_for_scan)
        assert "save_image(" in inspect.getsource(build_image.export_image)


# --- person-only settings ---------------------------------------------------------------------------------------------

def test_the_build_sources_are_person_only(tmp_path):
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, AISettingsChange
    for key in ("build_apt_mirror", "build_apt_proxy", "build_pip_index"):
        assert key in PERSON_ONLY_SETTINGS
        problems = AISettingsChange.problems_of_settings({}, None, {key: APT})
        assert [p.code for p in problems] == ["person_only"] and "build container image" in problems[0].message


def test_the_person_sets_a_build_source_and_a_bad_one_is_refused(tmp_path):
    from orchestrator.Core.local_network import LocalNetworkError, build_sources, set_build_source
    config = ConfigManager(tmp_path / "configs")
    assert set_build_source(config, "build_apt_proxy", APT) == APT
    assert build_sources(config)["build_apt_proxy"] == APT
    with pytest.raises(LocalNetworkError):
        set_build_source(config, "build_apt_proxy", "http://user:pw@192.0.2.10:3142")
    with pytest.raises(LocalNetworkError):
        set_build_source(config, "offline_mode", APT)
    assert set_build_source(config, "build_apt_proxy", "") == ""


# --- review (offline-builder-image-v): the attacks a first build let through ------------------------------------------

class TestReviewOfflineSources:

    def test_an_override_the_builder_cannot_use_is_not_a_local_llama_source(self, names):
        """Regression: an http (or credential-carrying) llama.cpp override in the own network counted as local, but the
        builder ignores such an override (only https without credentials) and the image fetched llama.cpp from GitHub
        in offline mode."""
        for bad in ("http://192.0.2.20/llama.cpp.git", "https://tok3n@192.0.2.20/llama.cpp.git",
                    "file:///srv/llama.cpp"):
            plan = plan_of(config=settings(build_apt_proxy=APT, build_pip_index=PIP), llama_repo=bad)
            llama = source(plan, "llama_cpp")
            assert not llama.local and "GitHub" in llama.how, bad
            assert not plan.ready

    def test_an_unusable_override_stops_the_offline_build(self, engine, monkeypatch, tmp_path, names):
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        engine.config.set("source_repositories", {"llama.cpp": {"url": "http://192.0.2.20/llama.cpp.git"}})
        with pytest.raises(OfflineImageMissing, match="llama.cpp source"):
            build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}), offline_mode=True,
                  offline_local_ranges=[LAN], build_apt_proxy=APT, build_pip_index=PIP)

    def test_fetches_of_every_scheme_and_other_package_managers_are_judged(self, names):
        """Regression: only http(s) addresses were judged; a git://, ssh:// or scp-like address and a package manager
        other than apt and pip fetched from the internet while the plan said every source was local."""
        text = ("FROM scratch\nRUN git clone git://cdn.example.com/tool.git /t\n"
                "ADD git@cdn.example.com:org/repo.git /src\nRUN git clone ssh://git@192.0.2.41/r.git /r\n"
                "RUN apk add --no-cache curl && npm install left-pad\n")
        plan = plan_of(text)
        labels = {s.label: s.local for s in plan.sources}
        assert labels["downloads from cdn.example.com"] is False
        assert labels["downloads from 192.0.2.41"] is True
        assert labels["packages through apk"] is False and labels["packages through npm"] is False
        assert not plan.ready

    def test_the_shipped_dockerfiles_name_no_other_package_manager(self):
        root = Path(__file__).resolve().parents[1] / "targets"
        for name in ("Rockchip/dockerfile", "_template/Dockerfile", "_template/Dockerfile.gpu"):
            text = (root / name).read_text(encoding="utf-8")
            body = "\n".join(build_image._logical_lines(text))
            assert not build_image._other_package_sources(body), name


class MirrorImages(Images):
    def __init__(self, here=(), fail=False):
        super().__init__(here)
        self.pulls, self.tags, self.fail = [], [], fail

    def pull(self, repository, tag=None):
        self.pulls.append((repository, tag))
        if self.fail:
            raise RuntimeError("manifest unknown at 192.0.2.30:5000")
        return SimpleNamespace(tag=lambda name, t: self.tags.append((name, t)))


class TestReviewDaemonMirror:

    def test_a_hub_base_image_is_pulled_from_the_mirror_address_never_through_the_daemon_fallback(
            self, engine, monkeypatch, tmp_path, names):
        """Regression: a Docker Hub base image through the daemon's registry mirror was left to the build's own pull,
        which falls back to Docker Hub on the internet when the mirror fails."""
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        engine.config.set("source_repositories", {"llama.cpp": {"url": "https://192.0.2.20/llama.cpp.git"}})
        docker = Docker(mirrors=["http://192.0.2.30:5000"])
        docker.images = MirrorImages()
        build(engine, monkeypatch, tmp_path, docker, offline_mode=True, offline_local_ranges=[LAN],
              build_apt_proxy=APT, build_pip_index=PIP)
        assert docker.images.pulls == [("192.0.2.30:5000/library/debian", "bookworm-slim")]
        assert docker.images.tags == [("debian", "bookworm-slim")]

    def test_a_mirror_that_does_not_provide_the_image_stops_the_build(self, engine, monkeypatch, tmp_path, names):
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        engine.config.set("source_repositories", {"llama.cpp": {"url": "https://192.0.2.20/llama.cpp.git"}})
        docker = Docker(mirrors=["http://192.0.2.30:5000"])
        docker.images = MirrorImages(fail=True)
        with pytest.raises(OfflineImageMissing) as stopped:
            build(engine, monkeypatch, tmp_path, docker, offline_mode=True, offline_local_ranges=[LAN],
                  build_apt_proxy=APT, build_pip_index=PIP)
        assert "debian:bookworm-slim" in str(stopped.value) and "192.0.2.30" not in str(stopped.value)

    def test_a_digest_pinned_image_is_pulled_by_digest_from_the_mirror_first(self):
        images = MirrorImages()
        ref = "ubuntu:22.04@sha256:" + "0" * 64
        build_image.pull_from_mirror(SimpleNamespace(images=images), ref, "https://mirror.test")
        assert images.pulls == [("mirror.test/library/ubuntu", "sha256:" + "0" * 64), (ref, None)]
        assert build_image.mirror_reference("acme/base:1", "http://192.0.2.30:5000") == \
            ("192.0.2.30:5000/acme/base", "1")


class TestReviewBuildxDriver:

    def test_an_offline_build_never_uses_a_buildx_builder_apart_from_the_daemon(self, engine, monkeypatch, tmp_path,
                                                                                names):
        """Regression: a docker-container (or remote) buildx builder does not see this computer's images or the
        daemon's mirrors and pulls every base image from its registry on the internet itself."""
        monkeypatch.setattr(build_image, "local_llama_copy", no_copy)
        engine.config.set("source_repositories", {"llama.cpp": {"url": "https://192.0.2.20/llama.cpp.git"}})
        monkeypatch.setattr(engine, "_buildx_driver", lambda: "docker-container", raising=False)
        legacy = []
        docker = Docker(here={"debian:bookworm-slim"})
        docker.api = SimpleNamespace(build=lambda **kw: legacy.append(kw) or iter(()))
        monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
        monkeypatch.setattr(engine, "_build_with_buildkit", lambda *a, **k: pytest.fail("not the buildx builder"))
        monkeypatch.setattr(engine, "docker_client", docker)
        for key, value in dict(offline_mode=True, offline_local_ranges=[LAN], build_apt_proxy=APT,
                               build_pip_index=PIP).items():
            engine.config.set(key, value)
        engine._build_docker_image(config(), BuildProgress("b1", BuildStatus.QUEUED, ""), dockerfile(tmp_path))
        assert len(legacy) == 1 and legacy[0]["buildargs"]["APT_PROXY"] == APT
        assert not legacy[0].get("pull"), "no pull of the base images"

    def test_online_the_buildx_builder_stays_whatever_its_driver(self, engine, monkeypatch, tmp_path, names):
        seen, _ = build(engine, monkeypatch, tmp_path, Docker(here={"debian:bookworm-slim"}), offline_mode=False,
                        driver=lambda: pytest.fail("online: not asked"))
        assert seen["labels"]


def oci_archive(path: Path, labels, *, repo_tags=("llm-framework/rockchip:last-build",), index_config=None,
                index_name=None) -> Path:
    """A docker save archive of Docker 25 and later: manifest.json and an OCI index.json naming the image."""
    config = json.dumps({"config": {"Labels": labels}}).encode()
    config_digest = hashlib.sha256(config).hexdigest()
    other = json.dumps({"config": {"Labels": {}}}).encode()
    other_digest = hashlib.sha256(other).hexdigest()
    wanted = other_digest if index_config == "other" else config_digest
    image_manifest = json.dumps({"config": {"digest": f"sha256:{wanted}"}, "layers": []}).encode()
    manifest_digest = hashlib.sha256(image_manifest).hexdigest()
    entry = {"digest": f"sha256:{manifest_digest}"}
    if index_name:
        entry["annotations"] = {"io.containerd.image.name": index_name}
    files = {"manifest.json": json.dumps([{"Config": f"blobs/sha256/{config_digest}", "RepoTags": list(repo_tags),
                                           "Layers": []}]).encode(),
             "index.json": json.dumps({"manifests": [entry]}).encode(),
             f"blobs/sha256/{config_digest}": config, f"blobs/sha256/{other_digest}": other,
             f"blobs/sha256/{manifest_digest}": image_manifest}
    with tarfile.open(path, "w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class TestReviewImport:

    def load(self, file, docker):
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        return build_image.import_image(None, file, actor="cli", sha256=digest, client=docker, expected=GOOD)

    def test_an_image_named_like_a_base_image_is_never_loaded(self, tmp_path):
        """Regression: the archive's names were not checked, so an import could take over debian:bookworm-slim (a
        base image of every target) or another target's image name."""
        for tags in (["debian:bookworm-slim"], ["llm-framework/rockchip:last-build", "llm-framework/hailo:last-build"]):
            docker = Docker()
            file = oci_archive(tmp_path / f"t{len(tags)}.tar", GOOD, repo_tags=tags)
            with pytest.raises(build_image.ImageTransferError) as refused:
                self.load(file, docker)
            assert refused.value.code == "mismatch" and docker.images.loaded == []
        docker = Docker()
        file = oci_archive(tmp_path / "named.tar", GOOD, index_name="docker.io/library/debian:bookworm-slim")
        with pytest.raises(build_image.ImageTransferError):
            self.load(file, docker)
        assert docker.images.loaded == []

    def test_an_oci_index_naming_another_image_is_never_loaded(self, tmp_path):
        """Regression: a load with the containerd image store reads index.json, which could name another image than
        the manifest.json whose labels were checked."""
        docker = Docker()
        file = oci_archive(tmp_path / "other.tar", GOOD, index_config="other")
        with pytest.raises(build_image.ImageTransferError) as refused:
            self.load(file, docker)
        assert refused.value.code == "invalid_value" and docker.images.loaded == []
        good = oci_archive(tmp_path / "good.tar", GOOD, index_name="docker.io/llm-framework/rockchip:last-build")
        assert self.load(good, docker).target == "Rockchip" and docker.images.loaded == [good.read_bytes()]

    def test_the_loaded_file_is_the_checked_file(self, tmp_path, monkeypatch):
        """Regression: the file was hashed by its name and opened again for the load; another file put under the
        same name in between was loaded unchecked."""
        file = archive(tmp_path / "rk.tar", GOOD)
        checked = file.read_bytes()
        other = archive(tmp_path / "evil.tar", {IMAGE_LABEL: "Rockchip", INPUTS_LABEL: "a" * 64, "x": "y"})
        swapped = []

        def swap(*a, **k):
            if not swapped:
                swapped.append(True)
                try:
                    other.replace(file)   # another file under the checked name, after the check
                except PermissionError:
                    # Windows refuses to replace a file that is open: the import holds the checked file open
                    if os.name != "nt":
                        raise
            return GOOD
        monkeypatch.setattr(build_image, "expected_labels", swap)
        docker = Docker()
        build_image.import_image(None, file, actor="cli", sha256=hashlib.sha256(checked).hexdigest(), client=docker)
        assert swapped and docker.images.loaded == [checked]
