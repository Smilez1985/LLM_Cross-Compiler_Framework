"""Base images of a target pack (consolidation chain c1-imports: pack_base_images, base_image_line and dockerfile_bases
move from tools/tools_community.py to Core/community_pack.py, so the build plan no longer imports a tool module).

Characterization: which files of a pack count as Dockerfiles (never through a link), which images each names, and the
plain line the person reads per image.
"""

from pathlib import Path

import pytest

from orchestrator.tools.tools_community import base_image_line, dockerfile_bases, pack_base_images

DIGEST = "sha256:" + "c" * 64


def _write(folder: Path, rel: str, text: str) -> None:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_pack_base_images_reads_every_dockerfile_of_a_pack(tmp_path):
    pack = tmp_path / "pack"
    _write(pack, "Dockerfile", "FROM debian:bookworm-slim AS build\nFROM build\nCOPY --from=ghcr.io/a/b:1 /x /x\n")
    _write(pack, "docker/arm.dockerfile", "ARG BASE=alpine:3.20\nFROM ${BASE}\n")
    _write(pack, "Dockerfile.cuda", "FROM nvcr.io/nvidia/cuda:12@" + DIGEST + "\n")
    _write(pack, "sub/Containerfile", "FROM scratch\nFROM ubuntu:24.04\n")
    _write(pack, "README.md", "FROM not-an-image\n")
    _write(pack, "dockerfiles/notes.txt", "FROM also-not\n")
    outside = tmp_path / "outside.dockerfile"
    outside.write_text("FROM evil/image:1\n", encoding="utf-8")
    try:
        (pack / "linked.dockerfile").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass   # without symbolic links the rest still counts
    assert pack_base_images(pack) == [
        ("Dockerfile", "debian:bookworm-slim"), ("Dockerfile", "ghcr.io/a/b:1"),
        ("Dockerfile.cuda", "nvcr.io/nvidia/cuda:12@" + DIGEST),
        ("docker/arm.dockerfile", "alpine:3.20"),
        ("sub/Containerfile", "ubuntu:24.04"),
    ]


def test_the_order_is_the_same_on_windows(tmp_path, monkeypatch):
    # A WindowsPath compares without case and with backslashes ("docker\\arm.dockerfile" < "dockerfile"): sorting the
    # Path objects put docker/ before Dockerfile on the Windows CI job only. Here the POSIX paths compare that way.
    _write(tmp_path, "Dockerfile", "FROM debian:bookworm-slim\n")
    _write(tmp_path, "docker/arm.dockerfile", "FROM alpine:3.20\n")

    def like_windows(a, b):
        return str(a).lower().replace("/", "\\") < str(b).lower().replace("/", "\\")
    monkeypatch.setattr(type(tmp_path), "__lt__", like_windows)
    assert sorted([tmp_path / "Dockerfile", tmp_path / "docker" / "arm.dockerfile"])[0].name == "arm.dockerfile"
    assert pack_base_images(tmp_path) == [("Dockerfile", "debian:bookworm-slim"),
                                          ("docker/arm.dockerfile", "alpine:3.20")]


def test_an_empty_or_missing_pack_has_no_images(tmp_path):
    assert pack_base_images(tmp_path) == [] and pack_base_images(tmp_path / "missing") == []


@pytest.mark.parametrize("image, expected", [
    ("debian:bookworm-slim",
     "debian:bookworm-slim: registry docker.io, image debian, tag bookworm-slim; not pinned by digest: its publisher "
     "can later point the tag at another image; Docker Official Image; the image build runs with network access on "
     "this computer, outside the build sandbox."),
    ("nvcr.io/nvidia/cuda:12@" + DIGEST,
     f"nvcr.io/nvidia/cuda:12@{DIGEST}: registry nvcr.io, image nvidia/cuda, tag 12; digest {DIGEST}; other "
     "registry: nvcr.io; prebuilt image made by its publisher, not checked by the framework; the image build runs "
     "with network access on this computer, outside the build sandbox."),
    ("ghcr.io/a/b",
     "ghcr.io/a/b: registry ghcr.io, image a/b; not pinned by digest: its publisher can later point the tag at "
     "another image; other registry: ghcr.io; prebuilt image made by its publisher, not checked by the framework; "
     "the image build runs with network access on this computer, outside the build sandbox."),
    ("$BASE",
     "$BASE: names a build argument; the image is decided when the target is built. The image build runs with "
     "network access on this computer, outside the build sandbox."),
])
def test_base_image_line(image, expected):
    assert base_image_line(image) == expected


def test_dockerfile_bases_with_continued_lines_and_mount_sources():
    text = ("# comment\nFROM --platform=linux/arm64 \\\n    debian:12 AS base\n"
            "RUN --mount=type=cache,from=ghcr.io/c/cache:1,target=/c true\nCOPY --from=0 /a /a\n"
            "COPY --from=base /b /b\nADD --from=quay.io/x/y:2 /d /d\n")
    assert dockerfile_bases(text) == ["debian:12", "ghcr.io/c/cache:1", "quay.io/x/y:2"]


def test_the_old_names_are_the_one_implementation_loaded_on_first_use():
    """tools_community keeps the names as lazy attributes (the pack code loads on first use, RG1); each is the
    function in Core/community_pack.py, which the build plan imports."""
    from orchestrator.Core import community_pack
    from orchestrator.tools import tools_community
    for name in ("pack_base_images", "base_image_line", "dockerfile_bases"):
        assert getattr(tools_community, name) is getattr(community_pack, name), name
