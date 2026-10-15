"""The builder never deletes an image (round 7 item 12; round 6 7.7; next-fixes item 103).

Until now every build ended by removing its image's per-build name and pruning the framework's dangling images. Now
the build's image gets the stable name `llm-framework/<target>:last-build`, then only its per-build name is dropped;
the image that had the stable name before keeps its layers and is listed for the person, who alone deletes it. Nothing
in the framework prunes Docker objects.
"""

import ast
from pathlib import Path

import pytest

from orchestrator.Core.builder import LAST_BUILD_TAG, BuildEngine

REPO = Path(__file__).resolve().parent.parent


class Image:
    def __init__(self, store, image_id):
        self.store, self.id = store, image_id

    @property
    def tags(self):
        return sorted(n for n, i in self.store.names.items() if i == self.id)

    def tag(self, repository, tag=None):
        self.store.names[f"{repository}:{tag}"] = self.id
        return True


class Images:
    """Docker's names of images. Removing an image's last name deletes the image: the fake fails then."""

    def __init__(self):
        self.names = {}
        self.deleted = []
        self.calls = []

    def get(self, ref):
        if ref.startswith("sha256:"):
            return Image(self, ref)
        if ref not in self.names:
            raise LookupError(ref)
        return Image(self, self.names[ref])

    def remove(self, image=None, force=False, noprune=False, **kwargs):
        self.calls.append(("remove", image, force, noprune))
        image_id = self.names.pop(image, image)
        if image_id not in self.names.values():
            self.deleted.append(image_id)
            raise AssertionError(f"the builder deleted the image {image_id}")

    def prune(self, *args, **kwargs):
        self.calls.append(("prune",))
        raise AssertionError("the builder never prunes images")


def engine_with(images, cache_dir):
    engine = BuildEngine.__new__(BuildEngine)   # only what cleanup and naming use
    engine.docker_client = type("Client", (), {"images": images})()
    engine.cache_dir = cache_dir
    return engine


def test_builder_never_deletes_an_image(tmp_path):
    images = Images()
    engine = engine_with(images, tmp_path)
    images.names["llm-framework/rk3588:b1"] = "sha256:" + "1" * 64
    engine._keep_as_last_build("llm-framework/rk3588:b1")
    assert images.names == {f"llm-framework/rk3588:{LAST_BUILD_TAG}": "sha256:" + "1" * 64}
    assert images.calls == [("remove", "llm-framework/rk3588:b1", False, True)] and images.deleted == []
    # The next build of the target moves the stable name; the earlier image keeps its layers without a name.
    images.names["llm-framework/rk3588:b2"] = "sha256:" + "2" * 64
    engine._keep_as_last_build("llm-framework/rk3588:b2")
    assert images.names == {f"llm-framework/rk3588:{LAST_BUILD_TAG}": "sha256:" + "2" * 64}
    assert images.deleted == [] and ("prune",) not in images.calls


def test_the_per_build_name_stays_when_the_stable_name_did_not_land(tmp_path):
    images = Images()
    engine = engine_with(images, tmp_path)
    images.names["llm-framework/rk3588:b1"] = "sha256:" + "1" * 64
    Image.tag, real = (lambda self, repository, tag=None: True), Image.tag   # Docker ignored the new name
    try:
        with pytest.raises(LookupError):
            engine._keep_as_last_build("llm-framework/rk3588:b1")
    finally:
        Image.tag = real
    assert images.names == {"llm-framework/rk3588:b1": "sha256:" + "1" * 64} and images.calls == []


def test_no_docker_prune_in_the_framework():
    """No prune of Docker images, containers, volumes, networks or the build cache anywhere in orchestrator/: a
    prune cannot be limited to what the person saw in a list."""
    hits = []
    for path in (REPO / "orchestrator").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name, base = node.func.attr, node.func.value
            docker_base = isinstance(base, ast.Attribute) and base.attr in ("images", "containers", "volumes",
                                                                           "networks", "api")
            if name in ("prune_builds", "prune_containers", "prune_volumes", "prune_networks", "prune_images") or \
                    (name == "prune" and docker_base):
                hits.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert hits == []
    text = (REPO / "orchestrator" / "Core" / "builder.py").read_text(encoding="utf-8")
    assert "images.prune" not in text
