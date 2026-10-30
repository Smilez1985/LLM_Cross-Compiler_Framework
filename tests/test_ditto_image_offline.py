"""Ditto's Docker image follows the offline image rule.

- The runtime-base stage pulls its base image and installs runtime libraries from the distribution's mirrors, so in
  offline mode it is never built: a runtime base that is there is used, otherwise the plain early stop names the
  image, before anything is built (no stage, no llama.cpp build in the builder container).
- Ditto's own stage copies files only: offline it is made on a runtime base that is there.
- A tag of an AI-proposed base image is pinned offline only in a registry the locality rule counts as local.

No real Docker: the fake docker-py client of tests/test_runtime_mode.py.
"""

from types import SimpleNamespace

import pytest

from orchestrator.runtime import container_server as cs

from test_runtime_mode import BASES, FakeClient, _stored_native, runtime  # noqa: F401
from test_runtime_mode import dri, verify_in_container  # noqa: F401,E402 - fixtures
from test_runtime_selfbuilt import no_source  # noqa: F401 - fixture


def offline_runtime(tmp_path, client):
    return runtime(tmp_path, client, settings={"offline_mode": True})


class TestTheRuntimeBaseOffline:

    def test_no_runtime_base_here_stops_early_naming_the_image(self, tmp_path):
        client = FakeClient()
        rt = offline_runtime(tmp_path, client)
        build = _stored_native(rt)
        base = BASES["debian-13"]
        with pytest.raises(cs.ContainerError) as stopped:
            cs.build_runtime_image(rt.docker(), build, base, "repackaged", tmp_path / "log", offline=True)
        text = str(stopped.value)
        assert "Offline mode is on" in text and base.image in text and cs.BASE_REPO in text
        assert client.builds == [], "nothing was built"

    def test_a_runtime_base_that_is_here_is_used_offline(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        build = _stored_native(rt)
        base = BASES["debian-13"]
        cs.build_runtime_base(rt.docker(), base, build.backend, tmp_path / "log")   # online, once
        image = cs.build_runtime_image(rt.docker(), build, base, "repackaged", tmp_path / "log", offline=True)
        assert [b["target"] for b in client.builds] == ["runtime-base", "ditto"]
        assert image.role == "image"

    def test_online_nothing_changes(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        cs.build_runtime_image(rt.docker(), _stored_native(rt), BASES["debian-13"], "repackaged", tmp_path / "log")
        assert [b["target"] for b in client.builds] == ["runtime-base", "ditto"]

    def test_docker_mode_in_offline_mode_stops_before_the_builder_container(self, tmp_path, no_source, dri,
                                                                           verify_in_container):
        from orchestrator.runtime.runtime_mode import RuntimeModeError, ensure
        from test_runtime_mode import Z1, with_libc
        client = FakeClient()
        rt = runtime(tmp_path, client, profile=with_libc(Z1, "2.17"), native=False, settings={"offline_mode": True})
        with pytest.raises((cs.ContainerError, RuntimeModeError), match="Offline mode is on"):
            ensure(rt, mode="docker")
        assert client.builds == [], "no stage was built"
        assert rt.store.references().get("docker") is None and verify_in_container == []


class TestPinningOffline:

    def test_a_tag_in_a_local_registry_is_pinned_and_any_other_is_refused(self, monkeypatch):
        from orchestrator.Core import ditto_manager
        from orchestrator.utils import image_ref, image_registry
        asked = []

        def inspect(reference, offline=False, client=None):
            asked.append((reference, offline))
            if offline:
                raise image_registry.ImageLookupError("offline mode: the tag cannot be resolved")
            return SimpleNamespace(pinned=f"{reference}@sha256:{'a' * 64}")
        monkeypatch.setattr(image_registry, "inspect_image", inspect)
        verdicts = {"registry.test:5000": "", "docker.io": "Offline mode is on, and the image ..."}
        monkeypatch.setattr(image_ref, "offline_pull_refusal",
                            lambda reference, config: verdicts[image_ref.split_image(reference)["registry"]])
        config = SimpleNamespace(offline_mode=True)
        pinned = ditto_manager.pin_reference("registry.test:5000/base/debian:13", offline=True, config=config)
        assert pinned.endswith("@sha256:" + "a" * 64) and asked[-1] == ("registry.test:5000/base/debian:13", False)
        with pytest.raises(image_registry.ImageLookupError):
            ditto_manager.pin_reference("debian:13", offline=True, config=config)
        assert asked[-1] == ("debian:13", True)
        with pytest.raises(image_registry.ImageLookupError):
            ditto_manager.pin_reference("registry.test:5000/base/debian:13", offline=True)   # no settings: refused
