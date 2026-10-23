"""Offline mode and Docker images (DECISIONS round 24 L-NET, round 25; the cs-c4-v review's open item).

In offline mode a package with Docker images (DeploymentManager._export_docker_images: the CLI's --docker, the
Deployment tab, the AI's create_deploy_package with include_docker_images) and the target image of a build use only
images already on this computer, or a pull from a registry the one locality rule counts as local
(utils/image_ref.offline_pull_refusal over utils/locality.py). Otherwise a plain stop names the image and the step to
take while online: no pull and no build-time download in offline mode."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.builder import (BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, INPUTS_LABEL,
                                       IMAGE_LABEL, ModelFormat, OfflineImageMissing)
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.deployment_manager import DeployError, DeploymentManager

DIGEST = "sha256:" + "a" * 64
HUB_IMAGE = f"ghcr.io/acme/runtime@{DIGEST}"
LOCAL_IMAGE = f"registry.lan:5000/acme/runtime@{DIGEST}"


@pytest.fixture
def names(monkeypatch):
    """The system resolver's answers (no lookup reaches the network)."""
    from orchestrator.utils import locality
    answers = {"ghcr.io": ["192.0.2.30"], "registry-1.docker.io": ["192.0.2.31"], "registry.lan": ["192.168.1.20"]}
    locality.clear_cache()
    monkeypatch.setattr(locality, "_system_lookup", lambda host: answers.get(host) or (_ for _ in ()).throw(
        OSError(f"unknown host {host}")))
    yield answers
    locality.clear_cache()


# --- the rule -----------------------------------------------------------------------------------------------------

class TestThePullRule:

    def test_online_every_pull_goes_as_before(self, names):
        from orchestrator.utils.image_ref import offline_pull_refusal
        assert offline_pull_refusal(HUB_IMAGE, {"offline_mode": False}) == ""

    def test_offline_a_registry_outside_the_own_network_is_refused_in_plain_words(self, names):
        from orchestrator.utils.image_ref import offline_pull_refusal
        text = offline_pull_refusal(HUB_IMAGE, {"offline_mode": True})
        assert text.startswith(f"Offline mode is on, and the image {HUB_IMAGE} is not on this computer")
        assert "ghcr.io" in text and "while offline mode is off" in text
        hub = offline_pull_refusal(f"debian@{DIGEST}", {"offline_mode": True})
        assert "registry-1.docker.io" in hub, "an image without a registry is pulled from Docker Hub"

    def test_offline_a_registry_in_the_own_network_or_on_this_computer_may_serve_it(self, names):
        from orchestrator.utils.image_ref import offline_pull_refusal
        assert offline_pull_refusal(LOCAL_IMAGE, {"offline_mode": True}) == ""
        assert offline_pull_refusal(f"localhost:5000/acme/runtime@{DIGEST}", {"offline_mode": True}) == ""

    def test_the_persons_opt_out_counts(self, names):
        from orchestrator.utils.image_ref import offline_pull_refusal
        assert offline_pull_refusal(HUB_IMAGE, {"offline_mode": True, "offline_local_hosts": ["ghcr.io"]}) == ""


# --- a package with Docker images -----------------------------------------------------------------------------

class Images:
    """docker-py's images API: `here` are the images on this computer; every pull is recorded."""

    def __init__(self, here=()):
        self.here, self.pulled = set(here), []

    def get(self, name):
        if name not in self.here:
            raise LookupError(f"No such image: {name}")
        return SimpleNamespace(tag=lambda *a, **k: None, reload=lambda: None, save=lambda named=None: [b"layer"])

    def pull(self, name):
        self.pulled.append(name)
        self.here.add(name)


def manager(offline):
    framework = SimpleNamespace(config={"offline_mode": offline}, get_component=lambda name: None)
    return DeploymentManager(framework)


class TestAPackageWithDockerImages:
    """Regression (review of cs-c4-v): in offline mode a digest-pinned image that was not on this computer was still
    pulled from its registry through the Docker daemon (CLI --docker, the Deployment tab, the AI's
    create_deploy_package with include_docker_images)."""

    def test_offline_an_image_that_is_not_here_is_not_pulled(self, names, tmp_path):
        client = SimpleNamespace(images=Images())
        with pytest.raises(DeployError) as refused:
            manager(True)._export_docker_images([HUB_IMAGE], tmp_path / "images", client=client)
        assert client.images.pulled == []
        assert refused.value.code == "needs_person" and "not on this computer" in str(refused.value)
        assert "could not be packaged" not in str(refused.value), "the plain refusal, not a packaging failure"

    def test_offline_an_image_already_here_is_packaged(self, names, tmp_path):
        client = SimpleNamespace(images=Images({HUB_IMAGE}))
        names_out = manager(True)._export_docker_images([HUB_IMAGE], tmp_path / "images", client=client)
        assert list(names_out) == [HUB_IMAGE] and client.images.pulled == []
        assert list((tmp_path / "images").glob("*.tar"))

    def test_offline_a_registry_in_the_own_network_still_serves_it(self, names, tmp_path):
        client = SimpleNamespace(images=Images())
        manager(True)._export_docker_images([LOCAL_IMAGE], tmp_path / "images", client=client)
        assert client.images.pulled == [LOCAL_IMAGE]

    def test_online_the_pinned_image_is_pulled_as_before(self, names, tmp_path):
        client = SimpleNamespace(images=Images())
        manager(False)._export_docker_images([HUB_IMAGE], tmp_path / "images", client=client)
        assert client.images.pulled == [HUB_IMAGE]


# --- the target image of a build -------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    return BuildEngine(ConfigManager(tmp_path / "configs"))


def config(**overrides):
    values = dict(build_id="b1", timestamp="t", model_source="acme/tiny-model", target_arch="Rockchip",
                  target_format=ModelFormat.GGUF, output_dir="out", quantization="Q4_K_M", target_board="rk3566",
                  target_pack_sha256="f" * 64)
    values.update(overrides)
    return BuildConfiguration(**values)


class Docker:
    """docker-py's client: images built earlier carry their labels; building is never expected offline."""

    def __init__(self, built=()):
        self.built, self.tags = list(built), []
        docker = self

        class ImagesApi:
            def list(self, filters=None):
                wanted = dict(f.split("=", 1) for f in (filters or {}).get("label", []))
                return [i for i in docker.built if all(i.labels.get(k) == v for k, v in wanted.items())]

            def get(self, tag):
                return SimpleNamespace(id="sha256:" + "c" * 64, tags=[tag])
        self.images = ImagesApi()
        self.api = SimpleNamespace(build=lambda **kw: pytest.fail("no image is built in offline mode"))

    def image(self, labels, created="2026-10-01T00:00:00Z"):
        image = SimpleNamespace(labels=labels, attrs={"Created": created}, short_id="sha256:cccc",
                                tag=lambda repo, name: self.tags.append(f"{repo}:{name}"))
        self.built.append(image)
        return image


def dockerfile(tmp_path, text="FROM debian:bookworm-slim AS builder\nRUN apt-get update\nFROM scratch\n"):
    path = tmp_path / "ctx" / "Dockerfile"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def online_labels(engine, monkeypatch, tmp_path, cfg):
    """The labels an online build gives the target image (BuildKit faked)."""
    seen = {}
    monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
    monkeypatch.setattr(engine, "_build_with_buildkit",
                        lambda tag, path, args, progress, labels=None: seen.update(labels or {}))
    monkeypatch.setattr(engine, "docker_client", SimpleNamespace(images=SimpleNamespace(get=lambda t: t)))
    engine._build_docker_image(cfg, BuildProgress("b0", BuildStatus.QUEUED, ""), dockerfile(tmp_path))
    return seen


class TestTheTargetImage:

    def test_offline_the_image_built_earlier_from_the_same_settings_is_used_without_any_build(self, engine, tmp_path,
                                                                                                monkeypatch):
        from test_hf_online import guard_sockets
        labels = online_labels(engine, monkeypatch, tmp_path, config(build_id="b0"))
        assert labels[IMAGE_LABEL] == "Rockchip" and len(labels[INPUTS_LABEL]) == 64
        docker = Docker()
        docker.image({IMAGE_LABEL: "Rockchip", INPUTS_LABEL: "0" * 64})          # other settings
        docker.image(dict(labels))
        engine.config.set("offline_mode", True)
        monkeypatch.setattr(engine, "docker_client", docker)
        monkeypatch.setattr(engine, "_buildkit_available", lambda: pytest.fail("no BuildKit build offline"))
        tried = guard_sockets(monkeypatch)
        progress = BuildProgress("b1", BuildStatus.QUEUED, "")
        image = engine._build_docker_image(config(), progress, dockerfile(tmp_path))
        assert docker.tags == ["llm-framework/rockchip:b1"], "named for this build like a built image"
        assert image.tags == ["llm-framework/rockchip:b1"]
        assert any("Offline mode: using the image of Rockchip" in line for line in progress.logs)
        assert tried == []

    def test_offline_without_such_an_image_the_build_stops_naming_the_images(self, engine, tmp_path, monkeypatch):
        engine.config.set("offline_mode", True)
        docker = Docker()
        docker.image({IMAGE_LABEL: "Rockchip", INPUTS_LABEL: "0" * 64})
        monkeypatch.setattr(engine, "docker_client", docker)
        with pytest.raises(OfflineImageMissing) as stopped:
            engine._build_docker_image(config(), BuildProgress("b1", BuildStatus.QUEUED, ""), dockerfile(tmp_path))
        text = str(stopped.value)
        assert "Offline mode is on, and the image of the target Rockchip" in text and "debian:bookworm-slim" in text
        assert "Start this build once with offline mode off" in text and "Image build failed" not in text

    def test_other_settings_are_another_image(self, engine, monkeypatch, tmp_path):
        first = online_labels(engine, monkeypatch, tmp_path, config(build_id="b0"))[INPUTS_LABEL]
        again = online_labels(engine, monkeypatch, tmp_path, config(build_id="b9"))[INPUTS_LABEL]
        other = online_labels(engine, monkeypatch, tmp_path, config(build_id="b8", target_pack_sha256="e" * 64))
        assert first == again, "the build id is no input of the image"
        assert other[INPUTS_LABEL] != first, "another target folder: another image"
