"""Docker image references (consolidation chain c1-imports: orchestrator/utils/image_ref.py, moved from
llm/task_prompts.py so the registry client in utils no longer imports the LLM prompts).

Characterization: split_image's whole answer (registry, repository, tag, digest, official) and is_official_image for
the forms a module, a recipe or an AI proposal names. Nothing is looked up.
"""

import pytest

from orchestrator.llm.task_prompts import is_official_image, split_image

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("reference, expected", [
    ("debian:bookworm-slim", ("docker.io", "debian", "bookworm-slim", "", "yes")),
    ("debian", ("docker.io", "debian", "", "", "yes")),
    ("library/debian:12", ("docker.io", "debian", "12", "", "yes")),
    ("docker.io/library/ubuntu:24.04", ("docker.io", "ubuntu", "24.04", "", "yes")),
    ("ubuntu:24.04@" + DIGEST, ("docker.io", "ubuntu", "24.04", DIGEST, "yes")),
    ("ubuntu@" + DIGEST, ("docker.io", "ubuntu", "", DIGEST, "yes")),
    ("nvidia/cuda:12.6.0-devel-ubuntu22.04", ("docker.io", "nvidia/cuda", "12.6.0-devel-ubuntu22.04", "", "")),
    ("nvcr.io/nvidia/l4t-jetpack:r36.4.0", ("nvcr.io", "nvidia/l4t-jetpack", "r36.4.0", "", "")),
    ("ghcr.io/ggml-org/llama.cpp:server", ("ghcr.io", "ggml-org/llama.cpp", "server", "", "")),
    ("registry.example:5000/team/tool@" + DIGEST, ("registry.example:5000", "team/tool", "", DIGEST, "")),
    ("localhost/mine:1", ("localhost", "mine", "1", "", "")),
    ("localhost:5000/mine", ("localhost:5000", "mine", "", "", "")),
    ("  debian:12  ", ("docker.io", "debian", "12", "", "yes")),
    ("", ("docker.io", "", "", "", "")),
])
def test_split_image_and_official(reference, expected):
    info = split_image(reference)
    assert (info["registry"], info["repository"], info["tag"], info["digest"], info["official"]) == expected
    assert set(info) == {"registry", "repository", "tag", "digest", "official"}
    assert is_official_image(reference) is (expected[4] == "yes")


def test_none_is_an_empty_reference():
    assert split_image(None)["repository"] == "" and is_official_image(None) is False


def test_the_old_names_are_the_one_implementation():
    from orchestrator.llm import task_prompts
    from orchestrator.utils import image_ref
    assert task_prompts.split_image is image_ref.split_image
    assert task_prompts.is_official_image is image_ref.is_official_image
